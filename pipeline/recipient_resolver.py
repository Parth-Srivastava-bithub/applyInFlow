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
    "founder", "executive", "official"
}


def is_obvious_garbage(name: str) -> bool:
    """Returns True if the name is clearly a phrase, headline, company, or placeholder."""
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
    Calls Groq to determine the appropriate recipient greeting:
    - Real human first name (e.g. 'Bharat', 'Priya', 'Sarah')
    - 'Sir' or 'Ma'am' if gender is evident from pronouns/text
    - None if unknown (so the email opens with 'Hi,')

    STRICT RULE: Never extracts or guesses names from the email address/username.
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

    prompt = f"""You are an expert at analyzing LinkedIn recruiter posts and contact information.
Determine how a professional cold job application email should be addressed.

CRITICAL RULES:
1. Output the recipient's real human FIRST NAME ONLY if clearly present in the Author Name or mentioned in the Post Text (e.g., 'Bharat', 'Priya', 'Sarah', 'Alex').
   - NEVER treat 'HR', 'Team', 'Recruiter', 'Admin', 'Talent' as a person's name.
2. STRICTLY FORBIDDEN: NEVER invent, derive, or split a name from the email address or username (e.g., 'bharatkp@gmail.com' -> DO NOT output 'Bharatkp', 'softwared250@gmail.com' -> DO NOT output 'Softwared'). An email address username could be anything and is NOT a human name.
3. If the author name is a company, sentence, action, slogan, or headline (e.g. 'Showcase Your Creativity', 'Final Interview', 'Save Mutual Time', 'Tech Solutions Pvt Ltd', 'Hiring Manager'), it is NOT a person's name - ignore it.
4. If a genuine human name is NOT found, but gender/honorific is clearly discernible (e.g., from 'she/her' pronouns or 'Ms./Mrs.' -> 'Ma\'am', 'he/him' or 'Mr.' -> 'Sir'), return 'Sir' or 'Ma\'am'.
5. If neither a real human name nor gender is known, return null.

Context:
- Author Name: {(author_name or '').strip()}
- Headline/Title: {(title or '').strip()}
- Target Email: {clean_email}
- Post Text: {(post_text or '')[:600].strip()}

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
                else:
                    # Validate that it's not a garbage token or derived from email
                    local_email = clean_email.split("@")[0] if "@" in clean_email else ""
                    if local_email and sal_clean.lower() == local_email.lower():
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

