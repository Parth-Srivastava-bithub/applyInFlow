"""
AutoApply Dashboard — Flask Backend Server
Handles:
- LinkedIn posts scraping via Playwright CDP
- Groq AI query generation & Pydantic resume parsing
- Cold email drafting via Groq (openai/gpt-oss-120b)
- Gmail SMTP sending with resume attachment
"""

import asyncio
import json
import logging
import os
import re
import smtplib
import socket
import ssl
from collections import deque
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
import threading
from typing import Optional, List, Dict, Any, Set

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from playwright.async_api import async_playwright
from werkzeug.utils import secure_filename

from posts_scraper import (
    POSTS_JSON,
    DEFAULT_QUERIES,
    SEEN_POSTS,
    load_seen,
    save_records,
    save_seen,
    scrape_posts,
)
from resume_parser import (
    StructuredResumeProfile,
    extract_text_from_file,
    format_candidate_context_for_prompt,
    structure_resume_with_ai,
)
from pipeline.name_cleaner import clean_name

load_dotenv()
import db
from axiom_logger import get_logger

axiom_logger = get_logger(service="backend_server")

app = Flask(__name__, static_folder="dashboard", static_url_path="")
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

logging.getLogger("werkzeug").setLevel(logging.ERROR)

@app.errorhandler(Exception)
def handle_server_exception(e):
    from werkzeug.exceptions import HTTPException
    run_id = request.headers.get("X-Run-ID") or "web_request"
    if isinstance(e, HTTPException):
        if e.code >= 500:
            axiom_logger.error(
                "SERVER_HTTP_500",
                f"HTTP {e.code} error on {request.method} {request.path}: {e}",
                error=str(e),
                path=request.path,
                method=request.method,
                run_id=run_id,
            )
        return jsonify(error=str(e)), e.code

    axiom_logger.error(
        "SERVER_UNHANDLED_EXCEPTION",
        f"Unhandled exception on {request.method} {request.path}: {e}",
        error=str(e),
        path=request.path,
        method=request.method,
        run_id=run_id,
    )
    return jsonify({"error": "Internal server error", "detail": str(e)}), 500

GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "")
GROQ_URL        = "https://api.groq.com/openai/v1/chat/completions"
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY", "")
OPENAI_URL      = "https://api.openai.com/v1/chat/completions"
MODEL           = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")

OUTPUT_DIR             = Path("output")
CONTACTS_FILE          = OUTPUT_DIR / "hr_gmail_posts.json"
EMAILS_FILE            = OUTPUT_DIR / "cold_emails.json"
PROFILE_FILE           = OUTPUT_DIR / "candidate_profile.json"
SENT_LOG               = OUTPUT_DIR / "sent_log.json"
RESUME_DIR             = OUTPUT_DIR / "resumes"
STRUCTURED_RESUME_FILE = OUTPUT_DIR / "structured_resume.json"
LATEX_DIR              = Path("latex_resume")
LATEX_SOURCE_FILE      = LATEX_DIR / "resume.tex"
TAILORED_RESUMES_DIR   = OUTPUT_DIR / "tailored_resumes"
LATEX_COMPILER_URL     = os.getenv("LATEX_COMPILER_URL", "http://localhost:8001/compile")

SEND_LOCK              = threading.Lock()
IN_FLIGHT_SENDS        = set()

OUTPUT_DIR.mkdir(exist_ok=True)
RESUME_DIR.mkdir(exist_ok=True)
TAILORED_RESUMES_DIR.mkdir(exist_ok=True)

# ── Scraping Progress State ──────────────────────────────────────────────────
SCRAPE_PROGRESS = {
    "is_running": False,
    "stop_requested": False,
    "current_query_index": 0,
    "total_queries": 0,
    "current_query": "",
    "found_count": 0,
    "status_text": "Idle",
    "percent": 0,
    "new_contacts": [],
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def load_json(path: Path, default=None):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default if default is not None else []


def save_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


OWNER_ALIASES = ("legacy", "parth", "parthsrivastava", "parthsrivastava6112004_gmail_com", "parthsrivastava6112001_gmail_com", "yellowforesty")
_migrated_owners = set()

def check_and_migrate_legacy_for_owner(u: str):
    """
    If the authenticated user matches the owner/creator (Parth),
    automatically adopt existing legacy documents and alternate owner aliases
    in MongoDB into their active username.
    """
    if u not in OWNER_ALIASES or u in _migrated_owners:
        return

    mdb = db.get_mongo_db()
    if mdb is not None:
        try:
            from pymongo import UpdateOne
            other_aliases = [alias for alias in OWNER_ALIASES if alias != u]

            # 1. Applied collection
            app_ops = []
            for d in mdb[db.APPLIED_COLLECTION].find({"username": {"$in": other_aliases}}):
                em = d.get("email", "").lower().strip()
                if em:
                    app_ops.append(UpdateOne(
                        {"username": u, "email": em},
                        {"$setOnInsert": {"username": u, "email": em, "sent_at": d.get("sent_at")}},
                        upsert=True
                    ))
            if app_ops:
                mdb[db.APPLIED_COLLECTION].bulk_write(app_ops, ordered=False)
                mdb[db.APPLIED_COLLECTION].delete_many({"username": {"$in": other_aliases}})

            # 2. Emails collection
            email_ops = []
            for d in mdb[db.EMAILS_COLLECTION].find({"username": {"$in": other_aliases}}):
                em = d.get("email", "").lower().strip()
                if em:
                    insert_doc = {k: v for k, v in d.items() if k != "_id"}
                    insert_doc["username"] = u
                    email_ops.append(UpdateOne(
                        {"username": u, "email": em},
                        {"$setOnInsert": insert_doc},
                        upsert=True
                    ))
            if email_ops:
                mdb[db.EMAILS_COLLECTION].bulk_write(email_ops, ordered=False)
                mdb[db.EMAILS_COLLECTION].delete_many({"username": {"$in": other_aliases}})

            # 3. Local user pending file sync
            local_legacy_pending = Path("output/users/legacy/pending.json")
            local_u_pending = Path(f"output/users/{u}/pending.json")
            if local_legacy_pending.exists() and (not local_u_pending.exists() or local_u_pending.stat().st_size < 100):
                legacy_data = db._load_local_json(local_legacy_pending, [])
                u_pending_items = []
                for item in legacy_data:
                    item_copy = dict(item)
                    item_copy["username"] = u
                    u_pending_items.append(item_copy)
                db._save_local_json(local_u_pending, u_pending_items)

            _migrated_owners.add(u)
        except Exception as e:
            axiom_logger.warning(f"Error during owner migration for '{u}': {e}")


def get_current_username() -> Optional[str]:
    """
    Extracts active username from X-User-Name header, query parameters, or body.
    Returns None if unauthenticated to prevent data leakage.
    Strictly forbids 'legacy', 'guest', 'anonymous', 'null', 'undefined', 'none'
    to prevent unauthenticated visitors from accessing pre-migration legacy data.
    """
    forbidden = ("null", "undefined", "anonymous", "guest", "none", "legacy", "")
    header_u = request.headers.get("X-User-Name")
    if header_u and header_u.strip() and header_u.strip().lower() not in forbidden:
        u = db.sanitize_username(header_u)
        check_and_migrate_legacy_for_owner(u)
        return u
    param_u = request.args.get("username")
    if param_u and param_u.strip() and param_u.strip().lower() not in forbidden:
        u = db.sanitize_username(param_u)
        check_and_migrate_legacy_for_owner(u)
        return u
    if request.is_json:
        try:
            body_u = (request.json or {}).get("username")
            if body_u and str(body_u).strip() and str(body_u).strip().lower() not in forbidden:
                u = db.sanitize_username(body_u)
                check_and_migrate_legacy_for_owner(u)
                return u
        except Exception:
            pass
    return None


def get_user_profile_file(username: Optional[str] = "legacy") -> Path:
    u = db.sanitize_username(username or "legacy")
    if u == "legacy":
        return PROFILE_FILE
    user_dir = db._get_user_dir(u)
    return user_dir / "profile.json"


def get_user_drafts_file(username: Optional[str] = "legacy") -> Path:
    u = db.sanitize_username(username or "legacy")
    if u == "legacy":
        return EMAILS_FILE
    user_dir = db._get_user_dir(u)
    return user_dir / "cold_emails.json"


def load_profile(username: Optional[str] = None) -> dict:
    default = {
        "name": "",
        "role": "AI/ML Engineer",
        "experience": "2+ years building ML pipelines, LLM apps, RAG systems",
        "skills": "Python, PyTorch, TensorFlow, LangChain, FastAPI, Docker",
        "location": "India (open to remote / hybrid)",
        "linkedin": "",
        "phone": "",
        "portfolio_url": "",
        "gmail_sender": "",
        "gmail_app_password": "",
        "resume_filename": "",
        "groq_api_key": "",
        "openai_api_key": "",
        "resend_api_key": "",
        "resend_from_email": "",
    }
    if not username:
        return default

    u = db.sanitize_username(username)
    is_owner = u in OWNER_ALIASES

    # 1. Primary: load from DB (PostgreSQL / MongoDB)
    db_saved = db.load_user_profile_db(u)

    # 2. Secondary: load from local user file
    p_file = get_user_profile_file(u)
    local_saved = load_json(p_file, {})
    saved = db_saved or local_saved
    if not saved and is_owner:
        saved = load_json(PROFILE_FILE, {})

    res = {**default, **saved}
    if is_owner:
        if not res.get("gmail_sender"):
            res["gmail_sender"] = os.getenv("GMAIL_SENDER") or os.getenv("SENDER_EMAIL", "")
            if not res.get("gmail_sender"):
                resume_data = load_json(STRUCTURED_RESUME_FILE, {})
                res["gmail_sender"] = resume_data.get("email", "")
        if not res.get("gmail_app_password"):
            res["gmail_app_password"] = os.getenv("GMAIL_APP_PASSWORD", "")
        if not res.get("groq_api_key"):
            res["groq_api_key"] = os.getenv("GROQ_API_KEY", "")
        if not res.get("openai_api_key"):
            res["openai_api_key"] = os.getenv("OPENAI_API_KEY", "")
        if not res.get("resend_api_key"):
            res["resend_api_key"] = os.getenv("RESEND_API_KEY", "")
        if not res.get("resend_from_email"):
            res["resend_from_email"] = os.getenv("RESEND_FROM_EMAIL", "")
        if not res.get("portfolio_url"):
            res["portfolio_url"] = os.getenv("PORTFOLIO_URL", "https://parthml.in")
    return res


def save_user_profile(data: dict, username: str = "legacy") -> dict:
    u = db.sanitize_username(username)
    p_file = get_user_profile_file(u)
    profile = load_profile(u)
    profile.update(data)
    save_json(p_file, profile)
    is_owner = u in OWNER_ALIASES
    if is_owner:
        save_json(PROFILE_FILE, profile)
    # Persist in PostgreSQL / MongoDB so user credentials survive container restarts
    db.save_user_profile_db(u, profile)
    return profile


def sync_env_file(updates: dict):
    env_path = Path(".env")
    lines = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    updated_keys = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in updates:
                val = updates[k]
                new_lines.append(f'{k}="{val}"' if any(c in val for c in (' ', '@', ':', '?', '&', '=')) else f'{k}={val}')
                updated_keys.add(k)
                continue
        new_lines.append(line)

    for k, v in updates.items():
        if k not in updated_keys and v:
            new_lines.append(f'{k}="{v}"' if any(c in v for c in (' ', '@', ':', '?', '&', '=')) else f'{k}={v}')

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def clean_post(text: str) -> str:
    if not text:
        return ""
    skip = {'Feed post', 'Follow', 'Like', 'Comment', 'Repost', 'Send', '3rd+', '•'}
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    filtered = [l for l in lines if l not in skip and not re.match(r'^\d+\s*(reaction|comment|repost)', l, re.I)]
    res = '\n'.join(filtered[:30])
    return res if res.strip() else text[:600]


def extract_email_owner_name(post_text: str, email: str) -> str:
    """
    Given raw LinkedIn post_text and a target email, attempt to find the name
    of the person who *owns* that specific email in the post, rather than the
    LinkedIn profile author who made the post.

    Strategy:
    1. Look for lines like: "Contact: John Smith\n...john.smith@gmail.com"
       or "Send resume to John Smith at john.smith@gmail.com".
    2. Search a ±5-line window around the email occurrence for a likely human name.
    3. Fall back to None so caller can use stored hr_name.
    """
    if not post_text or not email:
        return ""

    email_lower = email.lower().strip()
    lines = [l.strip() for l in post_text.split('\n')]

    # Find line index containing the email
    email_line_idx = None
    for i, line in enumerate(lines):
        if email_lower in line.lower():
            email_line_idx = i
            break

    if email_line_idx is None:
        return ""

    # Patterns that suggest a name precedes the email on the same or adjacent line
    # e.g. "Send your Resume to: Violeta Zelaya, ...  E-Mail: sgcbrokers@gmail.com"
    # We look at lines within ±5 of the email occurrence
    window_start = max(0, email_line_idx - 6)
    window_end   = min(len(lines), email_line_idx + 3)
    window_text  = '\n'.join(lines[window_start:window_end])

    # Inline name patterns near email:  "to: Name" / "Name," / "Contact Name"
    name_patterns = [
        # "Send your Resume to: Firstname Lastname"
        r'(?:to|for|contact|recruiter|from|cc)[:\s]+([A-Z][a-z]+(?:\s[A-Z][a-z]+)+)',
        # Standalone "Firstname Lastname" (Capitalised words) on its own line close to email
        r'^([A-Z][a-z]{1,20}\s[A-Z][a-z]{1,25})$',
        # Name followed by comma (attribution line)
        r'([A-Z][a-z]{1,20}\s[A-Z][a-z]{1,25})\s*,',
    ]

    for pat in name_patterns:
        for m in re.finditer(pat, window_text, re.MULTILINE):
            candidate = m.group(1).strip()
            # Reject if looks like a company or generic noun
            bad_words = {'hiring', 'manager', 'engineer', 'developer', 'recruiter',
                         'services', 'solutions', 'consulting', 'technologies', 'india',
                         'limited', 'pvt', 'inc', 'llc', 'corp', 'urgent', 'required'}
            if candidate.lower().split()[0] in bad_words:
                continue
            if 2 <= len(candidate.split()) <= 4:
                return candidate

    return ""


def extract_post_for_email(post_text: str, email: str) -> str:
    """
    From the raw multi-person LinkedIn feed blob, extract only the portion
    that is contextually relevant to the given email address.
    Returns a clean snippet of at most ~400 chars around the email mention.
    """
    if not post_text or not email:
        return post_text or ""

    email_lower = email.lower().strip()
    text_lower  = post_text.lower()
    idx = text_lower.find(email_lower)
    if idx == -1:
        # email not found in raw text, just return cleaned full post
        return clean_post(post_text)

    # Grab a 600-char window centred on the email occurrence and expand to line boundaries
    start = max(0, idx - 350)
    end   = min(len(post_text), idx + 250)
    # Snap to line start/end
    start = post_text.rfind('\n', 0, start)
    start = 0 if start == -1 else start + 1
    end_nl = post_text.find('\n', end)
    end    = end_nl if end_nl != -1 else end

    snippet = post_text[start:end]
    return clean_post(snippet) or clean_post(post_text)


GARBAGE_PATTERNS = [
    r'developer', r'engineer', r'software', r'cloud', r'cyber', r'defense',
    r'resume', r'whitepaper', r'report', r'share', r'connect', r'discuss',
    r'hello', r'weekly', r'insights', r'client', r'agentic', r'talent acquisition',
    r'college', r'school', r'university', r'pvt ltd', r'limited', r'solutions',
    r'reach out', r'help someone', r'shape', r'more about', r'innovations',
    r'showcase', r'creativity', r'practical', r'experience', r'interview',
    r'post with', r'mutual time', r'eagerness', r'work directly', r'hiring team',
    r'human resources', r'hiring manager', r'linkedin member', r'unknown'
]

def is_garbage_name(name: str) -> bool:
    if not name or len(name) < 2 or len(name) > 35:
        return True
    words = name.strip().split()
    if len(words) > 4:
        return True
    n_lower = name.lower()
    for gp in GARBAGE_PATTERNS:
        if re.search(gp, n_lower):
            return True
    return False

def derive_name_from_email(email: str) -> str:
    """
    Deprecated: Never guess or invent a human name by splitting email at '@'.
    An email username could be anything and is not a name.
    """
    return ''

def resolve_contact_name(stored_name: str, email: str = "") -> str:
    """
    Returns the real contact/author name if valid, or empty string if junk/headline/generic.
    STRICT RULE: Never derives fake names from email address usernames.
    """
    stored = (stored_name or '').strip()
    if not stored or is_garbage_name(stored) or '@' in stored:
        return ''
    # If the stored name is just the email username (e.g. bharatkp, softwared250), reject it
    if email and '@' in email:
        local_part = email.split('@')[0].lower()
        if re.sub(r'[\d._-]+', '', stored.lower()) == re.sub(r'[\d._-]+', '', local_part):
            return ''
    from pipeline.recipient_resolver import NON_HUMAN_NAMES
    first_token = stored.split()[0].lower()
    if first_token in NON_HUMAN_NAMES:
        return ''
    return stored

def groq_stream(messages: list, model: str = None, max_tokens: int = 4096, user_profile: dict = None) -> str:
    use_model = model or MODEL
    is_openai = use_model.startswith("gpt-") or use_model.startswith("o1") or use_model.startswith("o3") or use_model.startswith("chatgpt")

    prof = user_profile or {}
    user_openai_key = (prof.get("openai_api_key") or os.getenv("OPENAI_API_KEY") or OPENAI_API_KEY or "").strip()
    user_groq_key = (prof.get("groq_api_key") or os.getenv("GROQ_API_KEY") or GROQ_API_KEY or "").strip()

    if is_openai:
        api_key = user_openai_key
        if not api_key:
            is_openai = False
            use_model = MODEL or "qwen/qwen3.8-27b"

    if is_openai:
        url = OPENAI_URL
        payload = {
            "model": use_model,
            "messages": messages,
            "temperature": 0.65,
            "max_tokens": max_tokens,
            "stream": True,
        }
    else:
        api_key = user_groq_key
        url = GROQ_URL
        payload = {
            "model": use_model,
            "messages": messages,
            "temperature": 0.65,
            "max_completion_tokens": max_tokens,
            "top_p": 0.95,
            "stream": True,
            "stop": None,
        }
        if "oss" in use_model.lower():
            payload["reasoning_effort"] = "medium"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    resp = requests.post(url, json=payload, headers=headers, stream=True, timeout=60)
    resp.raise_for_status()

    full = ""
    for line in resp.iter_lines():
        if not line:
            continue
        s = line.decode("utf-8")
        if s.startswith("data: "):
            d = s[6:].strip()
            if d == "[DONE]":
                break
            try:
                chunk = json.loads(d)
                content = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                full += content
            except Exception:
                pass
    return full.strip()


# ── Core API Routes ──────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("dashboard", "index.html")


@app.route("/api/profile", methods=["GET"])
def get_profile():
    u = get_current_username()
    if not u:
        return jsonify({
            "name": "",
            "role": "",
            "skills": "",
            "experience": "",
            "location": "",
            "linkedin": "",
            "phone": "",
            "gmail_sender": "",
            "gmail_app_password": "",
            "resume_filename": "",
            "groq_api_key": "",
            "openai_api_key": "",
            "authenticated": False
        })
    prof = load_profile(username=u)
    prof["authenticated"] = True
    return jsonify(prof)


@app.route("/api/profile", methods=["POST"])
def save_profile_route():
    u = get_current_username()
    if not u:
        return jsonify({"error": "Sign in required to update settings"}), 401
    data = request.json or {}
    profile = save_user_profile(data, username=u)

    env_updates = {}
    if "groq_api_key" in data and u in OWNER_ALIASES:
        groq_val = (data["groq_api_key"] or "").strip()
        os.environ["GROQ_API_KEY"] = groq_val
        global GROQ_API_KEY
        GROQ_API_KEY = groq_val
        env_updates["GROQ_API_KEY"] = groq_val

    if "openai_api_key" in data and u in OWNER_ALIASES:
        openai_val = (data["openai_api_key"] or "").strip()
        os.environ["OPENAI_API_KEY"] = openai_val
        global OPENAI_API_KEY
        OPENAI_API_KEY = openai_val
        env_updates["OPENAI_API_KEY"] = openai_val

    if "gmail_sender" in data and u in OWNER_ALIASES:
        env_updates["GMAIL_SENDER"] = (data["gmail_sender"] or "").strip()
    if "gmail_app_password" in data and u in OWNER_ALIASES:
        env_updates["GMAIL_APP_PASSWORD"] = (data["gmail_app_password"] or "").strip()
    if "resend_api_key" in data and u in OWNER_ALIASES:
        resend_val = (data["resend_api_key"] or "").strip()
        os.environ["RESEND_API_KEY"] = resend_val
        env_updates["RESEND_API_KEY"] = resend_val
    if "resend_from_email" in data and u in OWNER_ALIASES:
        from_val = (data["resend_from_email"] or "").strip()
        os.environ["RESEND_FROM_EMAIL"] = from_val
        env_updates["RESEND_FROM_EMAIL"] = from_val
    if "portfolio_url" in data and u in OWNER_ALIASES:
        port_val = (data["portfolio_url"] or "").strip()
        os.environ["PORTFOLIO_URL"] = port_val
        env_updates["PORTFOLIO_URL"] = port_val

    if env_updates:
        try:
            sync_env_file(env_updates)
        except Exception as e:
            print(f"Failed to update .env: {e}")

    return jsonify({
        "ok": True,
        "mongo_connected": db.is_db_connected(),
        "db_connected": db.is_db_connected(),
        "db_type": db.get_active_db_type(),
        "username": u
    })


@app.route("/api/resend/quota", methods=["GET"])
def get_resend_quota_route():
    u = get_current_username()
    if not u:
        return jsonify({"error": "Sign in required"}), 401
    profile = load_profile(username=u)
    resend_key = (profile.get("resend_api_key") or os.getenv("RESEND_API_KEY") or "").strip()
    quota = profile.get("resend_quota") or {}

    # If refresh requested or quota uninitialized, attempt probe or compute from today's activity
    if request.args.get("refresh") or not quota:
        if resend_key:
            try:
                # Try lightweight probe to see if Resend returns quota headers on full-access keys
                r = requests.get("https://api.resend.com/domains", headers={"Authorization": f"Bearer {resend_key}"}, timeout=4)
                if r.status_code == 200:
                    hdrs = {k.lower(): str(v) for k, v in r.headers.items()}
                    daily_str = hdrs.get("x-resend-daily-quota")
                    monthly_str = hdrs.get("x-resend-monthly-quota")
                    if daily_str and daily_str.isdigit():
                        quota["daily_used"] = int(daily_str)
                        quota["daily_remaining"] = max(0, 100 - int(daily_str))
                    if monthly_str and monthly_str.isdigit():
                        quota["monthly_used"] = int(monthly_str)
                        quota["monthly_remaining"] = max(0, 3000 - int(monthly_str))
            except Exception:
                pass

        if "daily_remaining" not in quota or quota.get("daily_remaining") is None:
            quota = {
                "daily_used": quota.get("daily_used", 3),
                "daily_limit": 100,
                "daily_remaining": max(0, 100 - quota.get("daily_used", 3)),
                "monthly_used": quota.get("monthly_used", 3),
                "monthly_limit": 3000,
                "monthly_remaining": max(0, 3000 - quota.get("monthly_used", 3)),
                "rate_remaining": quota.get("rate_remaining", "9"),
                "last_checked": datetime.now(timezone.utc).isoformat()
            }
        else:
            quota["last_checked"] = datetime.now(timezone.utc).isoformat()

        profile["resend_quota"] = quota
        try:
            save_user_profile({"resend_quota": quota}, username=u)
        except Exception:
            pass

    return jsonify({
        "has_resend": bool(resend_key),
        "quota": quota
    })


@app.route("/api/contacts", methods=["GET"])
def get_contacts():
    u = get_current_username()
    if not u:
        return jsonify([])
    filt = request.args.get("filter", "pending_gmail").lower().strip()
    if filt == "all":
        records = db.get_all_contacts(username=u)
    elif filt == "sent":
        records = db.get_sent_emails(username=u)
    else:
        records = db.get_pending_gmails(username=u)

    for r in records:
        e = (r.get("email") or r.get("hr_email") or r.get("gmail") or "").lower().strip()
        resolved = resolve_contact_name(clean_name(r.get("name")), e)
        r["name"] = resolved
        r["resolved_name"] = resolved
        r["is_applied"] = (r.get("status") == "sent")
        r["is_gmail"] = r.get("is_gmail") if "is_gmail" in r else e.endswith("@gmail.com")
        r["email"] = e

    return jsonify(records)


@app.route("/api/applied-emails", methods=["GET"])
def get_applied_emails():
    u = get_current_username()
    if not u:
        return jsonify([])
    return jsonify(list(db.get_applied_emails(username=u)))


@app.route("/api/db-status", methods=["GET"])
def get_db_status():
    u = get_current_username()
    if not u:
        return jsonify({
            "connected": db.is_db_connected(),
            "authenticated": False,
            "username": "",
            "total_contacts": 0,
            "pending_gmail_count": 0,
            "pending_count": 0,
            "sent_count": 0,
            "applied_count": 0,
            "unapplied_count": 0,
            "pending_limit": db.PENDING_LIMIT,
            "sent_limit": db.SENT_LIMIT
        })
    stats = db.get_user_stats(username=u)
    pending_count = stats["pending_count"]
    sent_count = stats["sent_count"]
    return jsonify({
        "connected": stats["connected"],
        "authenticated": True,
        "username": stats["username"],
        "total_contacts": pending_count + sent_count,
        "pending_gmail_count": pending_count,
        "pending_count": pending_count,
        "sent_count": sent_count,
        "applied_count": sent_count,
        "unapplied_count": pending_count,
        "pending_limit": stats["pending_limit"],
        "sent_limit": stats["sent_limit"]
    })


@app.route("/api/contacts/delete", methods=["POST"])
def delete_contact():
    u = get_current_username()
    if not u:
        return jsonify({"error": "Sign in required"}), 401
    data = request.json or {}
    target_email = (data.get("email") or "").lower().strip()
    if not target_email:
        return jsonify({"error": "No email provided"}), 400
    db.delete_email(target_email, username=u)
    return jsonify({"ok": True})


@app.route("/api/contacts/clear", methods=["POST"])
def clear_contacts():
    u = get_current_username()
    if not u:
        return jsonify({"error": "Sign in required"}), 401
    db.clear_pending_emails(username=u)
    return jsonify({"ok": True})


@app.route("/api/emails", methods=["GET"])
def get_emails():
    """
    Returns strictly pending, unsent drafts.
    Cross-verifies every email against actual DB records for this user.
    Any email that has already been sent is permanently excluded from this window.
    """
    u = get_current_username()
    if not u:
        return jsonify([])
    drafts_file = get_user_drafts_file(u)
    drafts = load_json(drafts_file, [])
    pending_drafts = []
    has_changes = False

    for d in drafts:
        em = (d.get("to_email") or "").lower().strip()
        if not em:
            continue
        # Verify with actual database & sent logs: sent emails must NEVER appear in drafts window
        if d.get("status") == "sent" or db.is_email_applied(em, username=u):
            if d.get("status") != "sent":
                d["status"] = "sent"
                has_changes = True
            continue
        d["to_name"] = resolve_contact_name(d.get("to_name"), em)
        pending_drafts.append(d)

    # Dynamically verify missing_tags against current resume skills so false-missing tags turn green
    try:
        from scorer import load_all_candidate_skills, is_skill_matched
        cand_skills, full_text = load_all_candidate_skills(load_profile(username=u))
        for d in pending_drafts:
            fit = d.get("fit")
            if isinstance(fit, dict) and "missing_tags" in fit and fit["missing_tags"]:
                still_missing = []
                matched = list(fit.get("matched_tags") or [])
                for tag in fit["missing_tags"]:
                    tag_clean = re.sub(r'^[✕✓~]\s*', '', str(tag)).strip()
                    if is_skill_matched(tag_clean, cand_skills, full_text, ""):
                        tag_match = f"✓ {tag_clean}"
                        if tag_match not in matched:
                            matched.append(tag_match)
                        has_changes = True
                    else:
                        still_missing.append(tag)
                fit["missing_tags"] = still_missing
                fit["matched_tags"] = matched
    except Exception as e:
        logger.warning(f"Error validating dynamic fit tags: {e}")

    if has_changes:
        save_json(drafts_file, drafts)

    sort_mode = request.args.get("sort", "score").lower().strip()
    if sort_mode == "date":
        # Newest first
        pass
    else:
        # Default: Sort by fit_score descending (highest score first!)
        def get_draft_score(doc):
            fit = doc.get("fit") or {}
            score = fit.get("fit_score") if isinstance(fit, dict) else None
            if score is None:
                score = doc.get("fit_score", 70)
            return int(score)
        pending_drafts.sort(key=get_draft_score, reverse=True)

    return jsonify(pending_drafts)


@app.route("/api/emails/delete", methods=["POST"])
def delete_draft():
    u = get_current_username()
    if not u:
        return jsonify({"error": "Sign in required"}), 401
    data = request.json or {}
    to_email = (data.get("to_email") or "").lower().strip()
    if not to_email:
        return jsonify({"error": "No email provided"}), 400
    drafts_file = get_user_drafts_file(u)
    emails = load_json(drafts_file, [])
    emails = [e for e in emails if (e.get("to_email") or "").lower().strip() != to_email]
    save_json(drafts_file, emails)
    return jsonify({"ok": True})


@app.route("/api/emails/clear", methods=["POST"])
def clear_all_drafts():
    """
    Clears all unsent drafts while strictly preserving sent email history.
    """
    u = get_current_username()
    if not u:
        return jsonify({"error": "Sign in required"}), 401
    drafts_file = get_user_drafts_file(u)
    emails = load_json(drafts_file, [])
    kept = [e for e in emails if e.get("status") == "sent" or db.is_email_applied(e.get("to_email", ""), username=u)]
    save_json(drafts_file, kept)
    return jsonify({"ok": True})


@app.route("/api/scrape-status", methods=["GET"])
def get_scrape_status():
    return jsonify(SCRAPE_PROGRESS)


@app.route("/api/suggest-queries", methods=["POST"])
def suggest_queries():
    u = get_current_username()
    if not u:
        return jsonify({"error": "Sign in required"}), 401
    data = request.json or {}
    topic = data.get("topic", "AI ML Engineer").strip()
    profile = load_profile(u)

    prompt = f"""You are a recruiter outreach & LinkedIn search expert.
Target role: "{topic}"

Generate 10 distinct, proven search queries to find recruiter posts on LinkedIn containing contact Gmails.
Examples: "hr with mail {topic}", "mail your cv {topic} gmail", "urgent hiring {topic} send resume gmail".

Return JSON with a single key "queries" containing an array of exactly 10 strings.
Example: {{"queries": ["query 1", "query 2", ...]}}"""

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You are a specialized query generator. Return only valid JSON."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.4,
        "max_completion_tokens": 1024,
        "response_format": {"type": "json_object"}
    }
    groq_key = (profile.get("groq_api_key") or os.getenv("GROQ_API_KEY", "") or GROQ_API_KEY).strip()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {groq_key}",
    }

    try:
        resp = requests.post(GROQ_URL, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        res_json = resp.json()
        raw_text = res_json["choices"][0]["message"]["content"].strip()
        parsed = json.loads(raw_text)
        queries = parsed.get("queries", [])
        if queries and isinstance(queries, list):
            return jsonify({"queries": queries[:10]})
    except Exception:
        pass

    fallback = [
        f"hr with mail {topic}",
        f"mail your cv {topic} gmail",
        f"send resume {topic} gmail",
        f"hiring {topic} gmail india",
        f"urgent requirement {topic} gmail",
        f"mail cv {topic} hiring",
        f"{topic} recruiter send resume gmail",
        f"immediate joiner {topic} gmail",
        f"{topic} contract remote gmail hiring",
        f"looking for {topic} mail cv"
    ]
    return jsonify({"queries": fallback})


@app.route("/api/scrape", methods=["POST"])
def trigger_scrape():
    global SCRAPE_PROGRESS
    u = get_current_username()
    if not u:
        return jsonify({"error": "Sign in required"}), 401
    data = request.json or {}
    queries = data.get("queries", [])
    if isinstance(queries, str):
        queries = [q.strip() for q in queries.split(",") if q.strip()]
    if not queries:
        queries = ["hr with mail"]
    
    max_posts = int(data.get("max_posts", 15))
    cdp_url   = data.get("cdp_url") or os.getenv("CDP_URL", "http://localhost:9222")

    SCRAPE_PROGRESS["is_running"] = True
    SCRAPE_PROGRESS["stop_requested"] = False
    SCRAPE_PROGRESS["total_queries"] = len(queries)
    SCRAPE_PROGRESS["current_query_index"] = 0
    SCRAPE_PROGRESS["found_count"] = 0
    SCRAPE_PROGRESS["percent"] = 5
    SCRAPE_PROGRESS["status_text"] = "Connecting to Chrome on port 9222..."
    SCRAPE_PROGRESS["new_contacts"] = []

    async def run_scraping():
        global SCRAPE_PROGRESS
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(cdp_url)
            ctx = browser.contexts[0]
            page = None
            for pg in ctx.pages:
                if "linkedin" in pg.url:
                    page = pg
                    break
            if not page:
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            page.set_default_timeout(20000)

            existing_before = {
                (e.get("email") or e.get("hr_email") or e.get("gmail") or "").lower().strip()
                for e in db.get_all_emails(username=u)
                if (e.get("email") or e.get("hr_email") or e.get("gmail"))
            }
            # Strictly include all sent and applied email history across MongoDB and local logs
            for app_em in db.get_applied_emails(username=u):
                clean_app = (app_em or "").lower().strip()
                if clean_app:
                    existing_before.add(clean_app)

            new_in_session = 0
            all_saved = []
            new_contacts_list = []

            try:
                for idx, query in enumerate(queries, 1):
                    # Check stop flag before each query
                    if SCRAPE_PROGRESS.get("stop_requested"):
                        SCRAPE_PROGRESS["status_text"] = f"Stopped by user after {idx - 1} queries. {new_in_session} new contacts collected."
                        break

                    SCRAPE_PROGRESS["current_query_index"] = idx
                    SCRAPE_PROGRESS["current_query"] = query
                    SCRAPE_PROGRESS["percent"] = int((idx / len(queries)) * 95)
                    SCRAPE_PROGRESS["status_text"] = f"Searching ({idx}/{len(queries)}): '{query}'..."

                    results = await scrape_posts(
                        page=page,
                        queries=[query],
                        max_posts_per_query=max_posts,
                    )
                    all_saved = db.save_contacts(results, username=u)

                    # Track newly discovered contacts in this query
                    for item in results:
                        raw_em = (item.get("email") or item.get("hr_email") or item.get("gmail") or "").lower().strip()
                        if raw_em and raw_em not in existing_before:
                            existing_before.add(raw_em)
                            resolved = resolve_contact_name(item.get("name"), raw_em)
                            new_contacts_list.append({
                                "name": resolved,
                                "email": raw_em,
                                "hr_email": raw_em,
                                "title": item.get("title") or "Talent Acquisition / HR",
                                "company": item.get("company") or "",
                                "post_text": item.get("post_text") or "",
                                "linkedin_url": item.get("post_url") or item.get("linkedin_url") or "",
                                "query": query,
                                "status": "pending",
                                "created_at": datetime.now().strftime("%I:%M %p")
                            })

                    new_in_session = len(new_contacts_list)
                    SCRAPE_PROGRESS["found_count"] = new_in_session
                    SCRAPE_PROGRESS["new_contacts"] = new_contacts_list

                if not SCRAPE_PROGRESS.get("stop_requested"):
                    SCRAPE_PROGRESS["percent"] = 100
                    SCRAPE_PROGRESS["status_text"] = f"Finished! Collected {new_in_session} new contacts ({len(db.get_pending_gmails(username=u))} total pending Gmails)."
                return all_saved, new_in_session, new_contacts_list
            finally:
                SCRAPE_PROGRESS["is_running"] = False
                SCRAPE_PROGRESS["stop_requested"] = False
                await browser.close()

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        records, new_count, new_contacts = loop.run_until_complete(run_scraping())
        return jsonify({
            "ok": True,
            "count": len(records),
            "new_count": new_count,
            "records": records,
            "new_contacts": new_contacts
        })
    except Exception as e:
        SCRAPE_PROGRESS["is_running"] = False
        SCRAPE_PROGRESS["status_text"] = f"Error: {e}"
        return jsonify({"error": str(e)}), 500


@app.route("/api/scrape/stop", methods=["POST"])
def stop_scrape():
    SCRAPE_PROGRESS["stop_requested"] = True
    SCRAPE_PROGRESS["status_text"] = "Stop requested — finishing current query..."
    return jsonify({"ok": True, "message": "Stop requested. Will stop after current query finishes."})


@app.route("/api/extension/status", methods=["GET"])
def extension_status():
    u = get_current_username()
    return jsonify({
        "ok": True,
        "status": "ready",
        "username": u or "unauthenticated",
        "pending_contacts": len(db.get_pending_gmails(username=u)) if u else 0
    })


@app.route("/api/extension/ingest", methods=["POST"])
def extension_ingest():
    data = request.json or {}
    u = get_current_username()
    if not u and data.get("username"):
        u = db.sanitize_username(data.get("username"))
        
    if not u:
        return jsonify({"error": "Authentication required. Please sign in to AutoApply."}), 401
        
    leads = data.get("leads", [])
    if not isinstance(leads, list):
        return jsonify({"error": "Invalid payload: 'leads' must be a list."}), 400
        
    if not leads:
        return jsonify({
            "ok": True,
            "inserted_count": 0,
            "total_pending": len(db.get_pending_gmails(username=u)),
            "message": "No leads received."
        })

    processed = []
    for item in leads:
        raw_em = (item.get("email") or item.get("hr_email") or item.get("gmail") or "").lower().strip()
        if not raw_em or "@" not in raw_em:
            continue
        resolved_name = resolve_contact_name(item.get("name"), raw_em)
        processed.append({
            "name": resolved_name,
            "email": raw_em,
            "hr_email": raw_em,
            "gmail": raw_em if raw_em.endswith("@gmail.com") else None,
            "title": item.get("title") or "Talent Acquisition / HR",
            "company": item.get("company") or "",
            "post_text": (item.get("post_text") or "")[:800],
            "linkedin_url": item.get("linkedin_url") or item.get("profile_url") or "",
            "query": item.get("query") or "extension_scrape",
            "status": "pending",
            "created_at": datetime.now().strftime("%I:%M %p")
        })

    saved = db.save_contacts(processed, username=u)
    total_pending = len(db.get_pending_gmails(username=u))
    axiom_logger.info(
        "EXTENSION_INGEST",
        f"Ingested {len(processed)} leads from extension for user '{u}' (saved: {saved})",
        username=u,
        received=len(leads),
        saved=saved,
        total_pending=total_pending,
    )
    app.logger.info(f"Extension ingested {len(saved)} contacts for user '{u}'. Total pending: {total_pending}")

    return jsonify({
        "ok": True,
        "inserted_count": len(saved),
        "total_pending": total_pending,
        "records": saved
    })



@app.route("/api/upload-resume", methods=["POST"])
def upload_resume():
    u = get_current_username()
    if not u:
        return jsonify({"error": "Sign in required"}), 401
    if 'resume' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files['resume']
    if file.filename == '':
        return jsonify({"error": "Empty filename"}), 400

    filename = secure_filename(file.filename)
    save_path = RESUME_DIR / filename
    file.save(str(save_path))

    structured_data = None
    try:
        raw_text = extract_text_from_file(save_path)
        parsed_profile = structure_resume_with_ai(raw_text)
        structured_data = parsed_profile.model_dump()
        save_json(STRUCTURED_RESUME_FILE, structured_data)

        # Sync profile settings
        profile = load_profile(username=u)
        profile['resume_filename'] = filename
        if parsed_profile.name:
            profile['name'] = parsed_profile.name
        if parsed_profile.headline_or_role:
            profile['role'] = parsed_profile.headline_or_role
        if parsed_profile.key_skills:
            profile['skills'] = ", ".join(parsed_profile.key_skills[:12])
        if parsed_profile.years_of_experience:
            profile['experience'] = f"{parsed_profile.years_of_experience} experience"
        if parsed_profile.location:
            profile['location'] = parsed_profile.location
        if parsed_profile.linkedin_url:
            profile['linkedin'] = parsed_profile.linkedin_url
        if parsed_profile.phone:
            profile['phone'] = parsed_profile.phone
            
        save_json(get_user_profile_file(u), profile)
        if u == "legacy":
            save_json(PROFILE_FILE, profile)
    except Exception:
        profile = load_profile(username=u)
        profile['resume_filename'] = filename
        save_json(get_user_profile_file(u), profile)
        if u == "legacy":
            save_json(PROFILE_FILE, profile)

    return jsonify({
        "ok": True,
        "filename": filename,
        "structured": structured_data
    })


@app.route("/api/generate", methods=["POST"])
def generate_email():
    u = get_current_username()
    if not u:
        return jsonify({"error": "Sign in required"}), 401
    data = request.json or {}
    hr_name_raw = clean_name(data.get("name", "Hiring Manager"))
    hr_title    = data.get("title", "")
    post_text   = data.get("post_text", "")
    hr_email    = data.get("hr_email", "")
    req_model   = data.get("model") or MODEL
    profile     = load_profile(username=u)

    # ── Correct name & post context for this specific email ──────────────────
    extracted_owner = extract_email_owner_name(post_text, hr_email)
    hr_name = resolve_contact_name(extracted_owner or hr_name_raw, hr_email)
    relevant_post = extract_post_for_email(post_text, hr_email)

    structured_resume = load_json(STRUCTURED_RESUME_FILE, None)
    if structured_resume:
        try:
            parsed_model = StructuredResumeProfile.model_validate(structured_resume)
            candidate_context = format_candidate_context_for_prompt(parsed_model)
            cand_name = parsed_model.name or profile.get("name") or "Parth Srivastava"
        except Exception:
            cand_name = profile.get("name") or "Parth Srivastava"
            candidate_context = f"""Candidate Profile:
- Name: {cand_name}
- Role: {profile['role']}
- Experience: {profile['experience']}
- Skills: {profile['skills']}
- Location: {profile['location']}"""
    else:
        cand_name = profile.get("name") or "Parth Srivastava"
        candidate_context = f"""Candidate Profile:
- Name: {cand_name}
- Role: {profile['role']}
- Experience: {profile['experience']}
- Skills: {profile['skills']}
- Location: {profile['location']}"""

    try:
        with axiom_logger.step("GENERATE_EMAIL", description=f"Generating email for {hr_email}", email=hr_email, model=req_model, username=u) as step_meta:
            # ── Delegate to isolated email-drafting module with user's scoped API key ──
            from pipeline.email_drafter import draft_email as _draft_email
            is_openai = any(req_model.startswith(p) for p in ("gpt-", "o1", "o3", "chatgpt"))
            user_api_key = (profile.get("openai_api_key") if is_openai else profile.get("groq_api_key")) or ""
            portfolio_url = (profile.get("portfolio_url") or profile.get("website") or "").strip()
            if is_owner and not portfolio_url:
                portfolio_url = "https://parthml.in"
            draft = _draft_email(
                hr_name=hr_name,
                hr_title=hr_title,
                hr_email=hr_email,
                post_text=relevant_post,
                candidate_context=candidate_context,
                cand_name=cand_name,
                model=req_model,
                api_key=user_api_key,
                portfolio_url=portfolio_url,
            )
            subject = draft["subject"]
            body    = draft["body"]
            result  = draft["raw"]

            # Compute match & fit score with dynamic proportional normalization
            fit_data = {}
            try:
                from scorer import score_match
                fit_data = score_match(
                    post_text=post_text or relevant_post,
                    candidate_profile=profile,
                    hr_title=hr_title
                )
                step_meta["fit_score"] = fit_data.get("score")
            except Exception as fit_err:
                print(f"Scoring error: {fit_err}")

            step_meta["subject"] = subject
            step_meta["body_len"] = len(body)

            return jsonify({
                "subject": subject,
                "body": body,
                "raw": result,
                "resolved_name": draft.get("salutation") or hr_name or "",
                "fit": fit_data
            })
    except Exception as e:
        axiom_logger.error("GENERATE_EMAIL_FAILED", f"Email generation failed for {hr_email}: {e}", email=hr_email, error=str(e), username=u)
        return jsonify({"error": str(e)}), 500



class IPv4SMTP_SSL(smtplib.SMTP_SSL):
    def _get_socket(self, host, port, timeout):
        addr_list = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        last_err = None
        for af, socktype, proto, canonname, sa in addr_list:
            s = None
            try:
                s = socket.socket(af, socktype, proto)
                s.settimeout(timeout)
                s.connect(sa)
                import ssl
                ctx = ssl.create_default_context()
                return ctx.wrap_socket(s, server_hostname=host)
            except Exception as e:
                last_err = e
                if s:
                    s.close()
        if last_err:
            raise last_err
        raise OSError(f"Could not connect via IPv4 to {host}:{port}")


class IPv4SMTP(smtplib.SMTP):
    def _get_socket(self, host, port, timeout):
        addr_list = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        last_err = None
        for af, socktype, proto, canonname, sa in addr_list:
            s = None
            try:
                s = socket.socket(af, socktype, proto)
                s.settimeout(timeout)
                s.connect(sa)
                return s
            except Exception as e:
                last_err = e
                if s:
                    s.close()
        if last_err:
            raise last_err
        raise OSError(f"Could not connect via IPv4 to {host}:{port}")


def send_smtp_email(sender: str, password: str, to_email: str, msg_str: str, timeout: int = 15) -> None:
    """
    Sends an email using Gmail SMTP with strict IPv4 socket binding, timeouts, and port fallback.
    Tries port 465 (SSL) first with timeout=15s. If it fails or times out, falls back to port 587 (STARTTLS).
    """
    last_err = None

    # Attempt 1: Port 465 (SSL via IPv4)
    try:
        axiom_logger.info(
            "SMTP_ATTEMPT",
            f"Attempting Gmail SMTP connection on port 465 via IPv4 (timeout={timeout}s)...",
            port=465,
            recipient=to_email
        )
        with IPv4SMTP_SSL("smtp.gmail.com", 465, timeout=timeout) as smtp:
            smtp.login(sender, password)
            smtp.sendmail(sender, to_email, msg_str)
            axiom_logger.info(
                "SMTP_SUCCESS",
                f"Email delivered successfully via port 465 to {to_email}",
                port=465,
                recipient=to_email
            )
            return
    except Exception as e:
        last_err = e
        axiom_logger.warning(
            "SMTP_PORT_465_FAILED",
            f"SMTP on port 465 failed: {e}. Falling back to port 587 (STARTTLS)...",
            port=465,
            recipient=to_email,
            error=str(e)
        )

    # Attempt 2: Port 587 (STARTTLS via IPv4)
    try:
        axiom_logger.info(
            "SMTP_ATTEMPT",
            f"Attempting Gmail SMTP connection on port 587 via IPv4 (STARTTLS, timeout={timeout}s)...",
            port=587,
            recipient=to_email
        )
        with IPv4SMTP("smtp.gmail.com", 587, timeout=timeout) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(sender, password)
            smtp.sendmail(sender, to_email, msg_str)
            axiom_logger.info(
                "SMTP_SUCCESS",
                f"Email delivered successfully via port 587 to {to_email}",
                port=587,
                recipient=to_email
            )
            return
    except Exception as e:
        last_err = e
        axiom_logger.error(
            "SMTP_PORT_587_FAILED",
            f"SMTP on port 587 also failed: {e}",
            port=587,
            recipient=to_email,
            error=str(e)
        )
        raise RuntimeError(f"Failed to send email via SMTP (tried ports 465 & 587): {last_err}") from last_err


def send_via_resend(api_key: str, sender: str, sender_name: str, to_email: str, subject: str, body: str, attachment_path: Optional[Path] = None, attachment_name: str = "Resume.pdf", from_email: Optional[str] = None) -> dict:
    """
    Sends email via official Resend Python SDK (Port 443 — 100% allowed on all cloud platforms including Railway).
    Sets reply_to to candidate's Gmail so all recruiter responses arrive in the candidate's personal inbox.
    """
    import resend

    resend.api_key = api_key.strip()

    # Determine 'from' address: if user specified a custom verified domain, use it; otherwise use onboarding@resend.dev
    if from_email and "@" in from_email:
        from_header = f"{sender_name} <{from_email}>" if sender_name else from_email
    elif sender and "@" in sender and not sender.lower().endswith("@gmail.com"):
        from_header = f"{sender_name} <{sender}>" if sender_name else sender
    else:
        from_header = f"{sender_name} <onboarding@resend.dev>" if sender_name else "onboarding@resend.dev"

    body_html = body.replace("\n", "<br>")
    params: dict = {
        "from": from_header,
        "to": [to_email] if isinstance(to_email, str) else to_email,
        "subject": subject,
        "html": f"<div style='font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif; font-size:14px; line-height:1.6; color:#1a1a1a;'>{body_html}</div>",
        "text": body,
    }
    if sender and "@" in sender:
        params["reply_to"] = sender

    if attachment_path and attachment_path.exists():
        with open(attachment_path, "rb") as f:
            params["attachments"] = [{
                "filename": attachment_name,
                "content": list(f.read())
            }]

    try:
        resp = resend.Emails.send(params)
        return resp
    except Exception as e:
        err_msg = str(e)
        if "resend.com/domains" in err_msg or "testing emails" in err_msg:
            err_msg = (
                "Resend Free Tier Notice: 'onboarding@resend.dev' can only send test emails to your registered email address. "
                "To send to recruiter emails, please add and verify a domain at resend.com/domains and set it in 'Resend From Address' in Settings."
            )
        raise RuntimeError(f"Resend send failed: {err_msg}") from e


def parse_resend_quota(resp: Any) -> dict:
    """
    Extracts Resend sending quota from API response headers:
    - x-resend-daily-quota: count of emails sent today on Free plan (max 100/day)
    - x-resend-monthly-quota: count of emails sent this month (max 3000/month)
    - ratelimit-remaining: remaining burst requests in current 1s window (max 10)
    """
    headers = {}
    if isinstance(resp, dict):
        headers = {str(k).lower(): str(v) for k, v in resp.get("http_headers", {}).items()}
    elif hasattr(resp, "http_headers") and resp.http_headers:
        headers = {str(k).lower(): str(v) for k, v in resp.http_headers.items()}
    elif hasattr(resp, "headers") and resp.headers:
        headers = {str(k).lower(): str(v) for k, v in resp.headers.items()}

    daily_str = headers.get("x-resend-daily-quota")
    monthly_str = headers.get("x-resend-monthly-quota")

    daily_used = int(daily_str) if daily_str and daily_str.isdigit() else None
    monthly_used = int(monthly_str) if monthly_str and monthly_str.isdigit() else None

    # Resend Free plan has 100 emails/day and 3,000 emails/month
    quota = {
        "daily_used": daily_used,
        "daily_limit": 100 if daily_used is not None else "Unlimited",
        "daily_remaining": max(0, 100 - daily_used) if daily_used is not None else "Unlimited",
        "monthly_used": monthly_used,
        "monthly_limit": 3000 if monthly_used is not None else None,
        "monthly_remaining": max(0, 3000 - monthly_used) if monthly_used is not None else None,
        "rate_remaining": headers.get("ratelimit-remaining"),
        "last_checked": datetime.now(timezone.utc).isoformat()
    }
    return quota



@app.route("/api/send", methods=["POST"])
def send_email():
    u = get_current_username()
    if not u:
        return jsonify({"error": "Sign in required"}), 401
    data     = request.json or {}
    to_email = data.get("to_email", "")
    subject  = data.get("subject", "")
    body     = data.get("body", "")
    profile  = load_profile(username=u)

    sender   = (profile.get("gmail_sender") or "").strip()
    password = (profile.get("gmail_app_password") or "").strip().replace(" ", "")
    resume_fn = profile.get("resume_filename", "")
    resend_key = (profile.get("resend_api_key") or os.getenv("RESEND_API_KEY") or "").strip()
    resend_from = (profile.get("resend_from_email") or os.getenv("RESEND_FROM_EMAIL") or "").strip()

    if not resend_key and (not sender or not password):
        return jsonify({"error": "Gmail credentials or Resend API key not set in Settings"}), 400
    if not to_email:
        return jsonify({"error": "No recipient email"}), 400

    clean_to = to_email.lower().strip()

    with SEND_LOCK:
        if clean_to in IN_FLIGHT_SENDS:
            return jsonify({"error": f"An email to {clean_to} is already in progress. Duplicate prevented."}), 400
        if db.is_email_applied(clean_to, username=u):
            return jsonify({"error": f"Already applied to {clean_to}. Duplicate permanently prevented."}), 400
        IN_FLIGHT_SENDS.add(clean_to)

    try:
        with axiom_logger.step("SEND_EMAIL", description=f"Sending email to {to_email}", recipient=to_email, username=u) as step_meta:
            msg = MIMEMultipart()
            msg["Subject"] = subject
            msg["From"]    = sender
            msg["To"]      = to_email
            msg.attach(MIMEText(body, "plain"))

            tailored_fn = data.get("tailored_resume_filename") or ""
            attached_resume = False
            resume_path_to_send = None
            resume_name_to_send = "Resume.pdf"

            if tailored_fn:
                tailored_path = TAILORED_RESUMES_DIR / secure_filename(tailored_fn)
                if tailored_path.exists():
                    resume_path_to_send = tailored_path
                    resume_name_to_send = "Parth_Srivastava_Resume.pdf"
                    with open(tailored_path, "rb") as f:
                        part = MIMEApplication(f.read(), Name="Parth_Srivastava_Resume.pdf")
                    part['Content-Disposition'] = 'attachment; filename="Parth_Srivastava_Resume.pdf"'
                    msg.attach(part)
                    attached_resume = True

            if not attached_resume and resume_fn:
                resume_path = RESUME_DIR / resume_fn
                if resume_path.exists():
                    resume_path_to_send = resume_path
                    resume_name_to_send = resume_fn
                    with open(resume_path, "rb") as f:
                        part = MIMEApplication(f.read(), Name=resume_fn)
                    part['Content-Disposition'] = f'attachment; filename="{resume_fn}"'
                    msg.attach(part)
                    attached_resume = True

            # Send via Resend HTTPS API if available, or direct Gmail IPv4 SMTP
            resend_quota = None
            sent_via_resend_ok = False
            if resend_key:
                try:
                    axiom_logger.info("RESEND_ATTEMPT", f"Sending cold email via Resend HTTPS API to {to_email}...", recipient=to_email)
                    resend_resp = send_via_resend(
                        api_key=resend_key,
                        sender=sender or "onboarding@resend.dev",
                        sender_name=profile.get("name") or "Applicant",
                        to_email=to_email,
                        subject=subject,
                        body=body,
                        attachment_path=resume_path_to_send,
                        attachment_name=resume_name_to_send,
                        from_email=resend_from
                    )
                    resend_quota = parse_resend_quota(resend_resp)
                    daily_rem = resend_quota.get("daily_remaining")
                    monthly_rem = resend_quota.get("monthly_remaining")
                    axiom_logger.info(
                        "RESEND_SUCCESS",
                        f"Email delivered via Resend API to {to_email}. Quota remaining: {daily_rem}/100 today, {monthly_rem}/3000 month",
                        recipient=to_email,
                        resend_quota=resend_quota
                    )
                    sent_via_resend_ok = True
                    try:
                        save_user_profile({"resend_quota": resend_quota}, username=u)
                    except Exception as save_q_err:
                        logging.warning(f"Could not persist resend quota for {u}: {save_q_err}")
                except Exception as resend_err:
                    if sender and password:
                        axiom_logger.warning(
                            "RESEND_FALLBACK",
                            f"Resend delivery failed ({resend_err}). Falling back to direct Gmail SMTP (IPv4)...",
                            recipient=to_email,
                            error=str(resend_err)
                        )
                        send_smtp_email(sender, password, to_email, msg.as_string(), timeout=15)
                    else:
                        raise resend_err

            if not resend_key and not sent_via_resend_ok:
                send_smtp_email(sender, password, to_email, msg.as_string(), timeout=15)

            step_meta["attached_resume"] = attached_resume
            step_meta["subject"] = subject

            # Permanently record applied email in user's sent collection (strictly NO CONTEXT, limit 200)
            to_name = resolve_contact_name(data.get("to_name"), clean_to)
            db.mark_email_applied(clean_to, name=to_name, subject=subject, body=body, username=u)

            drafts_file = get_user_drafts_file(u)
            emails = load_json(drafts_file, [])
            for e in emails:
                if (e.get("to_email") or "").lower().strip() == clean_to:
                    e["status"] = "sent"
            save_json(drafts_file, emails)

            # Append to sent_log.json if legacy user
            if u == "legacy":
                sent_log = load_json(SENT_LOG, [])
                if not any((r.get("email") or r.get("to") or "").lower().strip() == clean_to for r in sent_log):
                    sent_log.append({
                        "email": clean_to,
                        "name": to_name,
                        "subject": subject,
                        "sent_at": datetime.now().isoformat()
                    })
                    save_json(SENT_LOG, sent_log)

            resp_payload = {"ok": True, "message": f"Email sent to {to_email}"}
            if resend_quota:
                resp_payload["resend_quota"] = resend_quota
            return jsonify(resp_payload)
    except Exception as e:
        err_msg = str(e)
        if "101" in err_msg or "Network is unreachable" in err_msg or "Connection refused" in err_msg:
            err_msg = (
                "Railway cloud blocks outbound SMTP (ports 465/587). "
                "To send emails from Railway without port restrictions, add a free Resend API key (resend.com) in Settings or RESEND_API_KEY in Railway Variables, "
                "or enable Outbound IPv6 in Railway Settings > Networking."
            )
        axiom_logger.error("SEND_EMAIL_FAILED", f"Failed to send email to {to_email}: {err_msg}", recipient=to_email, error=err_msg, raw_error=str(e), username=u)
        return jsonify({"error": err_msg}), 500
    finally:
        with SEND_LOCK:
            IN_FLIGHT_SENDS.discard(clean_to)


# ── LaTeX Resume Customization & Preview Endpoints ────────────────────────────

def clean_latex_output(raw_tex: str) -> str:
    """Strips markdown code blocks, think blocks, or surrounding chatter."""
    clean = re.sub(r'<think>.*?</think>', '', raw_tex, flags=re.DOTALL).strip()
    if clean.startswith("```"):
        clean = re.sub(r'^```(?:latex|tex)?\n', '', clean)
        clean = re.sub(r'\n```$', '', clean)
    return clean.strip()


def compile_latex_via_service(tex_code: str) -> bytes:
    """Calls the XeLaTeX Docker compiler microservice."""
    resp = requests.post(
        LATEX_COMPILER_URL,
        json={"tex": tex_code, "engine": "xelatex", "runs": 1, "timeout_seconds": 35},
        timeout=40
    )
    if resp.status_code != 200:
        try:
            err_data = resp.json()
            err_msg = err_data.get("detail", {}).get("compiler_output") or str(err_data)
        except Exception:
            err_msg = resp.text
        raise ValueError(f"LaTeX compilation failed: {err_msg}")
    return resp.content


def get_tailored_pdf_filename(email: str) -> str:
    safe_em = re.sub(r'[^a-zA-Z0-9]', '_', email.lower().strip())
    return f"resume_{safe_em}.pdf"


@app.route("/api/resume/base-tex", methods=["GET"])
def get_base_tex():
    u = get_current_username()
    if not u:
        return jsonify({"tex": "", "filename": ""})
    if not LATEX_SOURCE_FILE.exists():
        return jsonify({"error": "Master resume.tex not found"}), 404
    return jsonify({
        "tex": LATEX_SOURCE_FILE.read_text(encoding="utf-8"),
        "filename": "resume.tex"
    })


@app.route("/api/resume/pdf/<path:filename>", methods=["GET"])
def serve_tailored_pdf(filename):
    clean_fn = secure_filename(filename)
    target = TAILORED_RESUMES_DIR / clean_fn
    if not target.exists():
        if LATEX_SOURCE_FILE.exists():
            try:
                pdf_bytes = compile_latex_via_service(LATEX_SOURCE_FILE.read_text(encoding="utf-8"))
                target.write_bytes(pdf_bytes)
            except Exception:
                pass
    if target.exists():
        return send_from_directory(TAILORED_RESUMES_DIR, clean_fn, mimetype="application/pdf")
    if (RESUME_DIR / "parth_resume.pdf").exists():
        return send_from_directory(RESUME_DIR, "parth_resume.pdf", mimetype="application/pdf")
    return jsonify({"error": "PDF not found"}), 404


@app.route("/api/resume/tailor", methods=["POST"])
def tailor_resume():
    u = get_current_username()
    if not u:
        return jsonify({"error": "Sign in required"}), 401
    data = request.json or {}
    to_email = (data.get("to_email") or "generic").lower().strip()
    post_text = data.get("post_text", "")
    hr_title = data.get("hr_title", "")
    feedback = data.get("feedback", "").strip()
    current_tex = data.get("current_tex", "").strip()
    req_model = data.get("model") or MODEL

    if not current_tex:
        if LATEX_SOURCE_FILE.exists():
            base_tex = LATEX_SOURCE_FILE.read_text(encoding="utf-8")
        else:
            return jsonify({"error": "Master resume.tex not found"}), 404
    else:
        base_tex = current_tex

    profile = load_profile(username=get_current_username())
    cand_name = profile.get("name") or "Parth Srivastava"

    try:
        # ── Delegate to isolated resume-tailoring module ──────────────────────
        from pipeline.resume_tailor import tailor_resume as _tailor_resume
        tailored_tex = _tailor_resume(
            base_tex=base_tex,
            post_text=post_text,
            hr_title=hr_title,
            to_email=to_email,
            feedback=feedback,
            cand_name=cand_name,
            model=req_model,
        )

        # Compile via Docker XeLaTeX service
        pdf_bytes = compile_latex_via_service(tailored_tex)

        out_pdf_name = get_tailored_pdf_filename(to_email)
        out_pdf_path = TAILORED_RESUMES_DIR / out_pdf_name
        out_pdf_path.write_bytes(pdf_bytes)

        # Save tailored .tex alongside for reference
        out_tex_name = out_pdf_name.replace(".pdf", ".tex")
        (TAILORED_RESUMES_DIR / out_tex_name).write_text(tailored_tex, encoding="utf-8")

        timestamp = int(datetime.now().timestamp())
        return jsonify({
            "ok": True,
            "tex": tailored_tex,
            "pdf_url": f"/api/resume/pdf/{out_pdf_name}?t={timestamp}",
            "filename": out_pdf_name
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500



@app.route("/api/resume/compile-raw", methods=["POST"])
def compile_raw_resume():
    u = get_current_username()
    if not u:
        return jsonify({"error": "Sign in required"}), 401
    data = request.json or {}
    to_email = (data.get("to_email") or "generic").lower().strip()
    tex_code = data.get("tex", "").strip()

    if not tex_code:
        return jsonify({"error": "No LaTeX code provided"}), 400

    clean_tex = clean_latex_output(tex_code)

    try:
        pdf_bytes = compile_latex_via_service(clean_tex)

        out_pdf_name = get_tailored_pdf_filename(to_email)
        out_pdf_path = TAILORED_RESUMES_DIR / out_pdf_name
        out_pdf_path.write_bytes(pdf_bytes)

        out_tex_name = out_pdf_name.replace(".pdf", ".tex")
        (TAILORED_RESUMES_DIR / out_tex_name).write_text(clean_tex, encoding="utf-8")

        timestamp = int(datetime.now().timestamp())
        return jsonify({
            "ok": True,
            "tex": clean_tex,
            "pdf_url": f"/api/resume/pdf/{out_pdf_name}?t={timestamp}",
            "filename": out_pdf_name
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


def inject_skills_into_resume(base_tex: str, skills_to_add: list[str]) -> str:
    """
    Deterministically and cleanly injects target skills into LaTeX \\section*{Skills} categories.
    Guarantees 100% preservation of all section dividers, \\section*{Experience}, and LaTeX structure.
    """
    if not skills_to_add:
        return base_tex

    # Define standard category keywords for routing
    cat_keywords = {
        "prog": ["python", "java", "c++", "c#", "c", "golang", "go", "rust", "typescript", "javascript", "scala", "ruby", "php", "sql", "kql", "r", "kotlin", "swift", "bash", "shell"],
        "ai": ["ai", "agents", "agentic", "rag", "llm", "fine-tuning", "peft", "qlora", "nlp", "synthetic data", "prompt engineering", "deep learning", "machine learning", "mlops", "generative ai", "genai", "computer vision", "diffusion", "embeddings", "vector search", "transformers"],
        "fw": ["fastapi", "flask", "django", "spring boot", "spring", "pytorch", "tensorflow", "keras", "hugging face", "langchain", "langgraph", "pydantic", "sqlalchemy", "next.js", "react", "angular", "vue", "scikit-learn", "node.js", "express", "pandas", "numpy"],
        "db": ["postgresql", "postgres", "mongodb", "mysql", "redis", "cassandra", "dynamodb", "elasticsearch", "pinecone", "chroma", "faiss", "weaviate", "qdrant", "milvus", "sqlite", "jwt", "clerk", "oauth", "security", "aes-256"],
        "cloud": ["aws", "gcp", "google cloud", "azure", "docker", "kubernetes", "k8s", "vllm", "runpod", "novita", "linux", "airflow", "prometheus", "locust", "git", "github", "ci/cd", "terraform", "helm", "devops"]
    }

    # Match each category line in base_tex
    lines = base_tex.split('\n')
    new_lines = []
    
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(r"\textbf{Programming:}"):
            for s in list(skills_to_add):
                s_clean = s.strip()
                s_low = s_clean.lower()
                if any(kw in s_low for kw in cat_keywords["prog"]) and s_low not in line.lower():
                    line = line.rstrip() + f", {s_clean}"
                    skills_to_add.remove(s)
        elif stripped.startswith(r"\textbf{AI \& LLM Systems:}") or stripped.startswith(r"\textbf{AI & LLM Systems:}"):
            for s in list(skills_to_add):
                s_clean = s.strip()
                s_low = s_clean.lower()
                if any(kw in s_low for kw in cat_keywords["ai"]) and s_low not in line.lower():
                    line = line.rstrip() + f", {s_clean}"
                    skills_to_add.remove(s)
        elif stripped.startswith(r"\textbf{Frameworks \& Backend:}") or stripped.startswith(r"\textbf{Frameworks & Backend:}"):
            for s in list(skills_to_add):
                s_clean = s.strip()
                s_low = s_clean.lower()
                if any(kw in s_low for kw in cat_keywords["fw"]) and s_low not in line.lower():
                    line = line.rstrip() + f", {s_clean}"
                    skills_to_add.remove(s)
        elif stripped.startswith(r"\textbf{Databases \& Security:}") or stripped.startswith(r"\textbf{Databases & Security:}"):
            for s in list(skills_to_add):
                s_clean = s.strip()
                s_low = s_clean.lower()
                if any(kw in s_low for kw in cat_keywords["db"]) and s_low not in line.lower():
                    line = line.rstrip() + f", {s_clean}"
                    skills_to_add.remove(s)
        elif stripped.startswith(r"\textbf{Cloud, DevOps \& Tools:}") or stripped.startswith(r"\textbf{Cloud, DevOps & Tools:}"):
            for s in list(skills_to_add):
                s_clean = s.strip()
                s_low = s_clean.lower()
                if any(kw in s_low for kw in cat_keywords["cloud"]) and s_low not in line.lower():
                    line = line.rstrip() + f", {s_clean}"
                    skills_to_add.remove(s)
        new_lines.append(line)

    # Any remaining unclassified skills go to Frameworks & Backend
    if skills_to_add:
        final_lines = []
        for line in new_lines:
            if (line.strip().startswith(r"\textbf{Frameworks \& Backend:}") or line.strip().startswith(r"\textbf{Frameworks & Backend:}")):
                for s in skills_to_add:
                    s_clean = s.strip()
                    if s_clean.lower() not in line.lower():
                        line = line.rstrip() + f", {s_clean}"
            final_lines.append(line)
        return '\n'.join(final_lines)

    return '\n'.join(new_lines)


@app.route("/api/resume/quick-tailor-skills", methods=["POST"])
def quick_tailor_skills():
    r"""
    Surgically tailors ONLY the skill lines inside \section*{Skills}.
    Never modifies section boundaries, \titlerule, \section*{Experience}, or document layout.
    """
    u = get_current_username()
    if not u:
        return jsonify({"error": "Sign in required"}), 401
    data = request.json or {}
    to_email = (data.get("to_email") or "generic").lower().strip()
    post_text = data.get("post_text", "")
    hr_title = data.get("hr_title", "")
    current_tex = data.get("current_tex", "").strip()

    # Always start from pristine master resume.tex if current_tex is corrupted or missing
    if not current_tex or r"\section*{Experience}" not in current_tex:
        if LATEX_SOURCE_FILE.exists():
            base_tex = LATEX_SOURCE_FILE.read_text(encoding="utf-8")
        else:
            return jsonify({"error": "Master resume.tex not found"}), 404
    else:
        base_tex = current_tex

    # Extract missing skills from post requirements
    from scorer import extract_post_requirements, is_skill_matched, load_all_candidate_skills, score_match
    profile = load_profile(username=get_current_username())
    reqs = extract_post_requirements(post_text, hr_title)
    cand_skills_set, full_text = load_all_candidate_skills(profile)

    missing_skills = [
        s.strip() for s in reqs.get("required_skills", [])
        if not is_skill_matched(s, cand_skills_set, full_text, "")
    ]
    if not missing_skills:
        missing_skills = [s.strip() for s in reqs.get("required_skills", [])]

    # Inject deterministically into the exact skill categories
    tailored_tex = inject_skills_into_resume(base_tex, list(missing_skills))

    try:
        # Safety check: Guarantee that section header for Experience exists and has title rule
        if r"\section*{Experience}" not in tailored_tex:
            if LATEX_SOURCE_FILE.exists():
                master_tex = LATEX_SOURCE_FILE.read_text(encoding="utf-8")
                exp_part = master_tex[master_tex.find(r"\section*{Experience}"):]
                tailored_tex = tailored_tex[:tailored_tex.find(r"\normalsize") + len(r"\normalsize")] + "\n\n%==================== EXPERIENCE ====================%\n" + exp_part

        # Compile via Docker XeLaTeX
        pdf_bytes = compile_latex_via_service(tailored_tex)

        out_pdf_name = get_tailored_pdf_filename(to_email)
        out_pdf_path = TAILORED_RESUMES_DIR / out_pdf_name
        out_pdf_path.write_bytes(pdf_bytes)

        out_tex_name = out_pdf_name.replace(".pdf", ".tex")
        (TAILORED_RESUMES_DIR / out_tex_name).write_text(tailored_tex, encoding="utf-8")

        # Re-score match with this custom tailored text so red pills turn to green!
        updated_fit = score_match(post_text, profile, hr_title=hr_title)
        new_skills_set, new_full_text = load_all_candidate_skills(profile)
        new_full_text += " " + tailored_tex.lower()
        matched_tags = []
        missing_tags = []
        for s in reqs.get("required_skills", []):
            if is_skill_matched(s, new_skills_set, new_full_text, ""):
                matched_tags.append(f"✓ {s.strip()}")
            else:
                missing_tags.append(f"✕ {s.strip()}")
        if reqs.get("required_skills"):
            updated_fit["matched_tags"] = matched_tags
            updated_fit["missing_tags"] = missing_tags
            if len(matched_tags) > 0 and len(missing_tags) == 0:
                updated_fit["fit_score"] = max(updated_fit.get("fit_score", 85), 95)
                updated_fit["fit_tier"] = "strong"

        timestamp = int(datetime.now().timestamp())
        return jsonify({
            "ok": True,
            "tex": tailored_tex,
            "pdf_url": f"/api/resume/pdf/{out_pdf_name}?t={timestamp}",
            "filename": out_pdf_name,
            "fit": updated_fit,
            "added_skills": missing_skills
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/save-email", methods=["POST"])
def save_email():
    u = get_current_username()
    if not u:
        return jsonify({"error": "Sign in required"}), 401
    data   = request.json or {}
    drafts_file = get_user_drafts_file(u)
    emails = load_json(drafts_file, [])
    to_email = data.get("to_email", "")
    clean_email = to_email.lower().strip()
    data["to_name"] = resolve_contact_name(data.get("to_name"), clean_email)

    idx = next((i for i, e in enumerate(emails) if (e.get("to_email") or "").lower().strip() == clean_email), None)
    if idx is not None:
        emails[idx].update(data)
        updated_item = emails.pop(idx)
        emails.insert(0, updated_item)
    else:
        # Prepend new drafts so newest drafts are always at the top!
        emails.insert(0, data)
    save_json(drafts_file, emails)
    return jsonify({"ok": True})


if __name__ == "__main__":
    print("\n" + "="*50)
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 5000))
    print(f"  Listening on http://{host}:{port}")
    print("="*50 + "\n")
    app.run(host=host, port=port, debug=False)
