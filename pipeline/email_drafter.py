"""
pipeline/email_drafter.py — Isolated email-drafting stage.

Single public function: draft_email()

Input/Output contract:
    Input:  all context needed to draft one cold email (strings, no shared state)
    Output: {"subject": str, "body": str, "raw": str}

The system prompt is loaded from prompts/email_draft.txt so it can be edited
without touching this file.  Uses string.Template for variable substitution.

No Flask, no globals, no mutable state — independently testable.
"""

import json
import logging
import os
import re
from pathlib import Path
from string import Template
from typing import Any, Dict

import requests

logger = logging.getLogger("autoapply.email_drafter")

# Path to the prompt file (relative to project root, resolved at import time)
_PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / "email_draft.txt"

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
_OPENAI_URL = "https://api.openai.com/v1/chat/completions"


def _load_prompt() -> str:
    """Load the email drafting system prompt from the prompts/ directory."""
    if _PROMPT_FILE.exists():
        return _PROMPT_FILE.read_text(encoding="utf-8")
    # Fallback: minimal inline prompt if file is missing (should not happen)
    logger.warning("prompts/email_draft.txt not found — using inline fallback prompt.")
    return (
        "You are an expert job application email writer helping $cand_name.\n"
        "$candidate_context\n\n"
        "Write a concise, direct cold email to the recruiter.\n"
        "Output format ONLY:\nSubject: <subject>\n\n<body>"
    )


def _clean_post(text: str) -> str:
    """Strip UI noise from scraped LinkedIn post text."""
    if not text:
        return ""
    skip = {"Feed post", "Follow", "Like", "Comment", "Repost", "Send", "3rd+", "•"}
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    filtered = [
        l for l in lines
        if l not in skip and not re.match(r"^\d+\s*(reaction|comment|repost)", l, re.I)
    ]
    result = "\n".join(filtered[:25])
    return result if result.strip() else text[:600]


def _call_api(messages: list, model: str, max_tokens: int = 4096, api_key: str | None = None) -> str:
    """
    Calls Groq or OpenAI API (auto-detected from model name) with streaming.
    Returns the full generated text.
    """
    is_openai = any(model.startswith(p) for p in ("gpt-", "o1", "o3", "chatgpt"))

    if is_openai:
        key = (api_key or os.getenv("OPENAI_API_KEY", "")).strip()
        if not key:
            # Fall back to Groq if no OpenAI key
            is_openai = False
            model = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")

    if is_openai:
        url = _OPENAI_URL
        key = (api_key or os.getenv("OPENAI_API_KEY", "")).strip()
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.65,
            "max_tokens": max_tokens,
            "stream": True,
        }
    else:
        url = _GROQ_URL
        key = (api_key or os.getenv("GROQ_API_KEY", "")).strip()
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.65,
            "max_completion_tokens": max_tokens,
            "top_p": 0.95,
            "stream": True,
            "stop": None,
        }
        if "oss" in model.lower():
            payload["reasoning_effort"] = "medium"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
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


def _parse_subject_body(raw: str) -> tuple[str, str]:
    """Extract subject and body from the raw LLM output."""
    # Strip reasoning blocks from thinking models
    clean = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    if not clean:
        clean = raw

    # Normalize fancy unicode punctuation to prevent email encoding glitches
    clean = clean.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    clean = clean.replace("\u2014", " -- ").replace("\u2013", " - ")

    subject, body = "", clean
    lines = clean.split("\n")

    if clean.lower().startswith("subject:"):
        subject = lines[0].replace("Subject:", "").replace("subject:", "").strip()
        body = "\n".join(lines[1:]).strip()
    else:
        # Search first 4 lines for a Subject: marker
        for i, line in enumerate(lines[:4]):
            if line.lower().startswith("subject:"):
                subject = line.replace("Subject:", "").replace("subject:", "").strip()
                body = "\n".join(lines[i + 1:]).strip()
                break

    return subject, body


def draft_email(
    hr_name: str,
    hr_title: str,
    hr_email: str,
    post_text: str,
    candidate_context: str,
    cand_name: str,
    model: str | None = None,
    api_key: str | None = None,
    portfolio_url: str = "https://parthml.in",
) -> Dict[str, str]:
    """
    Draft a cold email from the candidate to a specific HR contact.

    Args:
        hr_name:            Cleaned, display-ready name of the HR recruiter.
        hr_title:           Recruiter's headline/title.
        hr_email:           Recipient email address (used in user message).
        post_text:          Raw LinkedIn post text (will be cleaned internally).
        candidate_context:  Formatted candidate profile string (from resume_parser).
        cand_name:          Candidate's display name (e.g. "Parth Srivastava").
        model:              LLM model identifier. Defaults to GROQ_MODEL env var.
        api_key:            Optional API key (Groq or OpenAI) for the user.
        portfolio_url:      Candidate's portfolio link (e.g. "https://parthml.in").

    Returns:
        dict with keys: "subject" (str), "body" (str), "raw" (str)

    Raises:
        requests.HTTPError: if the API call fails.
    """
    use_model = model or os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")

    # Determine salutation first name
    first_name = "there"
    if hr_name and hr_name not in ("Unknown", "Hiring Manager", "LinkedIn Recruiter"):
        first_name = hr_name.split()[0].strip()

    # Clean portfolio URL display: ensure full https:// so email clients make it clickable
    raw_portfolio = (portfolio_url or "https://parthml.in").strip()
    if not raw_portfolio.startswith("http://") and not raw_portfolio.startswith("https://"):
        clean_portfolio_display = f"https://{raw_portfolio}".rstrip("/")
    else:
        clean_portfolio_display = raw_portfolio.rstrip("/")

    # Load and render system prompt from prompts/email_draft.txt
    template_src = _load_prompt()
    system_prompt = Template(template_src).safe_substitute(
        cand_name=cand_name,
        first_name=first_name,
        candidate_context=candidate_context,
        portfolio_url=clean_portfolio_display,
    )

    # Clean post text
    cleaned_post = _clean_post(post_text).strip()
    if not cleaned_post:
        cleaned_post = f"Hiring for {hr_title or 'AI/ML Engineer role'}."

    user_msg = (
        f"Recipient Email: {hr_email}\n"
        f"Recruiter Name: {hr_name}\n"
        f"Recruiter Headline / Company: {hr_title}\n\n"
        f"Relevant LinkedIn Post Content (about this specific recruiter):\n{cleaned_post}\n\n"
        f"IMPORTANT: The email must start with 'Hi {first_name},' where {first_name} = "
        "the recruiter's first name above. "
        "DO NOT mention any other recruiter's name. Write only about the job details in the post above.\n\n"
        "Write the human, contextual cold email now."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]

    raw = _call_api(messages, use_model, api_key=api_key)
    subject, body = _parse_subject_body(raw)

    # Strip markdown bolding from email body to keep it authentic
    body = re.sub(r"\*\*([^*]+)\*\*", r"\1", body)

    # Upgrade any naked domain like 'parthml.in' into clickable 'https://parthml.in'
    body = re.sub(r"(?<!https://)(?<!http://)\bparthml\.in\b", "https://parthml.in", body)

    # Guarantee portfolio link appears in sign-off if omitted by model
    if clean_portfolio_display and clean_portfolio_display.lower() not in body.lower():
        if cand_name in body:
            body = re.sub(re.escape(cand_name), f"{cand_name}\n{clean_portfolio_display}", body, count=1)
        else:
            body = body.rstrip() + f"\n\nBest,\n{cand_name}\n{clean_portfolio_display}"

    logger.debug(f"Drafted email for {hr_email}: subject='{subject[:60]}...'")

    return {
        "subject": subject,
        "body": body,
        "raw": raw,
    }
