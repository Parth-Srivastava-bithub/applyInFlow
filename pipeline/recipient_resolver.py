"""
pipeline/recipient_resolver.py

Extracts recipient salutation for cold job application emails.
Hierarchy:
1. Genuine human first name (from author or post context, NEVER from email username).
2. Gender honorific ('Sir' or 'Ma'am') if discernible from pronouns / text.
3. None -> email salutation defaults cleanly to "Hi,".
"""

import os
import re
import json
import logging
from typing import Optional
import requests

logger = logging.getLogger(__name__)

# In-memory cache to avoid duplicate Groq calls for the same email/contact
_SALUTATION_CACHE = {}

GARBAGE_PATTERNS = [
    r'\bdeveloper\b', r'\bengineer\b', r'\bsoftware\b', r'\bcloud\b', r'\bcyber\b', r'\bdefense\b',
    r'\bresume\b', r'\bwhitepaper\b', r'\breport\b', r'\bshare\b', r'\bconnect\b', r'\bdiscuss\b',
    r'\bhello\b', r'\bweekly\b', r'\binsights\b', r'\bclient\b', r'\bagentic\b', r'\btalent acquisition\b',
    r'\bcollege\b', r'\bschool\b', r'\buniversity\b', r'\bpvt ltd\b', r'\blimited\b', r'\bsolutions\b',
    r'\breach out\b', r'\bhelp someone\b', r'\bshape\b', r'\bmore about\b', r'\binnovations\b',
    r'\bshowcase\b', r'\bcreativity\b', r'\bpractical\b', r'\bexperience\b', r'\binterview\b',
    r'\bpost with\b', r'\bmutual time\b', r'\beagerness\b', r'\bwork directly\b', r'\bhiring team\b',
    r'\bhuman resources\b', r'\bhiring manager\b', r'\blinkedin member\b', r'\bunknown\b'
]


NON_HUMAN_NAMES = {
    "hr", "hiring", "team", "recruiter", "talent", "admin", "careers", "career",
    "info", "contact", "support", "jobs", "job", "help", "staffing", "services",
    "solutions", "agency", "tech", "technologies", "global", "india", "llc", "corp",
    "company", "member", "unknown", "lead", "leads", "there", "manager", "engineer",
    "founder", "executive", "official", "apply", "easy apply", "applied", "click",
    "view", "feed", "post", "connect", "follow", "message", "share", "send", "like",
    "comment", "repost", "join", "save", "showcase", "creativity", "interview"
}


def is_obvious_garbage(name: str) -> bool:
    """Returns True if the name is clearly a phrase, headline, button, company, or placeholder."""
    if not name:
        return True
    s = name.strip()
    if len(s) < 2 or len(s) > 35:
        return True
    words = s.split()
    if len(words) > 4:
        return True
    if len(words) == 1 and words[0].lower() in NON_HUMAN_NAMES:
        return True
    lower = s.lower()
    if lower in NON_HUMAN_NAMES:
        return True
    for pat in GARBAGE_PATTERNS:
        if re.search(pat, lower):
            return True
    return False


def resolve_salutation_with_groq(
    post_text: str = "",
    author_name: str = "",
    email: str = "",
    title: str = "",
    api_key: str = None,
    model: str = None,
) -> Optional[str]:
    """
    Calls Groq to extract the recruiter's real human first name from the email address
    or context, or determine gender (Sir/Ma'am), or return None (defaults to 'Hi,').
    """
    clean_email = (email or "").strip().lower()
    cache_key = (clean_email, (author_name or "").strip().lower()[:40])
    if cache_key in _SALUTATION_CACHE:
        return _SALUTATION_CACHE[cache_key]

    key = (api_key or os.getenv("GROQ_API_KEY") or "").strip()
    if not key:
        # Fallback without LLM: check if author_name is valid person name
        if author_name and not is_obvious_garbage(author_name):
            first = author_name.strip().split()[0].capitalize()
            if first.lower() not in NON_HUMAN_NAMES:
                _SALUTATION_CACHE[cache_key] = first
                return first
        _SALUTATION_CACHE[cache_key] = None
        return None

    # Determine model
    use_model = model or os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")
    is_openai = any(use_model.startswith(p) for p in ("gpt-", "o1", "o3", "chatgpt"))
    api_url = "https://api.openai.com/v1/chat/completions" if is_openai else "https://api.groq.com/openai/v1/chat/completions"

    prompt = f"""You are an expert at extracting recipient contact names from Gmail addresses and LinkedIn context.
Determine the appropriate recipient greeting for a professional cold job application email.

CRITICAL RULES:
1. Extract the recipient's real human FIRST NAME from the email address or context:
   - 'bharatkp24@gmail.com' -> 'Bharat'
   - 'derinmicheal28@gmail.com' -> 'Derin'
   - 'upasanamodi2015@gmail.com' -> 'Upasana'
   - 'priya.sharma99@gmail.com' -> 'Priya'
   - 'john.smith@gmail.com' -> 'John'
   - Strip out any numbers (24, 28, 2015), initials, or suffixes (e.g. 'kp' in bharatkp -> 'Bharat').
2. STRICTLY FORBIDDEN:
   - NEVER use UI action words, buttons, or slogans as a name (e.g. 'Apply', 'Easy Apply', 'Connect', 'Follow', 'Showcase', 'Final Interview', 'Hiring Manager', 'Careers', 'Info', 'Software').
3. If the email address does NOT contain a human name (e.g., 'softwared250@gmail.com', 'careers@company.com', 'info@techcorp.com', 'hiring@...'):
   - Check if context indicates gender ('She/Her' -> 'Ma'am', 'He/Him' -> 'Sir').
   - Otherwise, return null.
4. If neither a real human first name nor gender can be found, return null.

Context / Author: {(author_name or '').strip()}
Headline / Title: {(title or '').strip()}
Target Email: {clean_email}
Post Snippet: {(post_text or '')[:300].strip()}

Respond with ONLY valid JSON:
{{"salutation": "<First name or Sir or Ma'am or null>"}}
"""

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": use_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 50,
        "response_format": {"type": "json_object"},
    }

    try:
        resp = requests.post(api_url, headers=headers, json=payload, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            parsed = json.loads(content)
            raw_sal = parsed.get("salutation")
            if raw_sal and isinstance(raw_sal, str):
                sal_clean = raw_sal.strip()
                # Check for null-like string
                if sal_clean.lower() in ("null", "none", "unknown", "n/a", "hiring manager", "there"):
                    sal_clean = None
                elif sal_clean.lower() in ("sir", "mr", "mr."):
                    sal_clean = "Sir"
                elif sal_clean.lower() in ("ma'am", "maam", "madam", "ms", "ms.", "mrs", "mrs."):
                    sal_clean = "Ma'am"
                elif sal_clean.lower() in NON_HUMAN_NAMES:
                    # Check if pronouns indicate gender
                    combined_text = f"{author_name} {title} {post_text}".lower()
                    if re.search(r'\b(she/her|her/she|ms\.|mrs\.)\b', combined_text):
                        sal_clean = "Ma'am"
                    elif re.search(r'\b(he/him|him/he|mr\.)\b', combined_text):
                        sal_clean = "Sir"
                    else:
                        sal_clean = None
                elif is_obvious_garbage(sal_clean):
                    sal_clean = None
                else:
                    sal_clean = sal_clean.split()[0].capitalize()
                    if sal_clean.lower() in NON_HUMAN_NAMES:
                        sal_clean = None
                _SALUTATION_CACHE[cache_key] = sal_clean
                return sal_clean
    except Exception as exc:
        logger.warning(f"Salutation resolution LLM call failed: {exc}")

    # Fallback if LLM failed
    if author_name and not is_obvious_garbage(author_name):
        res = author_name.strip().split()[0].capitalize()
        if res.lower() not in NON_HUMAN_NAMES:
            _SALUTATION_CACHE[cache_key] = res
            return res

    # Final check for gender if no name found
    combined_text = f"{author_name} {title} {post_text}".lower()
    if re.search(r'\b(she/her|her/she|ms\.|mrs\.)\b', combined_text):
        _SALUTATION_CACHE[cache_key] = "Ma'am"
        return "Ma'am"
    if re.search(r'\b(he/him|him/he|mr\.)\b', combined_text):
        _SALUTATION_CACHE[cache_key] = "Sir"
        return "Sir"

    _SALUTATION_CACHE[cache_key] = None
    return None

