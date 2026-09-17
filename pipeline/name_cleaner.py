"""
pipeline/name_cleaner.py — Dedicated name normalisation module.

Fixes the bug where scraped LinkedIn names are inserted into emails and
templates without cleaning (e.g. "Rahulsingh" instead of "Rahul Singh").

Public API:
    clean_name(raw: str) -> str

All scraped names MUST pass through clean_name() before reaching any
email template, resume tailoring prompt, or subject line.
"""

import re
from typing import Optional

# ---------------------------------------------------------------------------
# Word-boundary dictionary for splitting concatenated Indian / common names.
# Sorted longest-first so prefix matching is greedy (e.g. "priya" before "pri").
# This list is intentionally small — the camelCase splitter handles most cases.
# Add names here only if they are routinely run-together without case markers.
# ---------------------------------------------------------------------------
_NAME_ATOMS = sorted([
    # Common Indian given names (lower-case)
    "rahul", "priya", "neha", "ankit", "amit", "anil", "sunil", "sonia",
    "pooja", "rohit", "nikhil", "deepak", "sandeep", "manish", "ravi",
    "vijay", "arjun", "kiran", "rekha", "sunita", "kavita", "meena",
    "geeta", "seema", "reena", "shilpa", "nisha", "asha", "usha",
    "sanjay", "manoj", "rajesh", "ramesh", "suresh", "dinesh", "ganesh",
    "naresh", "mahesh", "ritesh", "hitesh", "nilesh", "alpesh",
    "divya", "shreya", "preeti", "swati", "shweta", "ankita",
    "shruti", "smita", "sangita", "archana", "namrata", "vandana",
    "harsha", "harshal", "bharat", "vishnu", "ganesh", "girish",
    "paresh", "mukesh", "yogesh", "lokesh", "umesh", "rakesh",
    "devesh", "rupesh", "bhavesh", "jignesh", "rajiv", "navin",
    # Common surnames used as given names
    "sharma", "verma", "singh", "patel", "shah", "jain", "gupta",
    "kumar", "mishra", "pandey", "dubey", "yadav", "tiwari", "shukla",
    "agarwal", "aggarwal", "kapoor", "khanna", "malhotra", "mehta",
    "saxena", "srivastava", "trivedi", "chaudhary", "chauhan", "reddy",
    "nair", "pillai", "menon", "iyer", "rao", "naidu", "murthy",
], key=len, reverse=True)

# Regex: split on camelCase boundary (lowercase→UPPERCASE transition)
_CAMEL_SPLIT_RE = re.compile(r'(?<=[a-z])(?=[A-Z])')

# Regex: strip non-name characters (digits, punctuation except hyphens in names)
_STRIP_RE = re.compile(r"[^A-Za-z\s\-']")

# Regex: collapse multiple whitespace
_WS_RE = re.compile(r'\s+')

# Fallback returned when cleaning produces an empty string
_FALLBACK = "Hiring Manager"


def _split_by_known_atoms(token: str) -> list[str]:
    """
    Given a fully-lowercase run-together token like "rahulsingh",
    greedily split it into known name atoms using the _NAME_ATOMS list.
    Returns a list of capitalised atoms, e.g. ["Rahul", "Singh"].
    Falls back to the original token (capitalised) if no split found.
    """
    token = token.lower()
    results: list[str] = []

    while token:
        matched = False
        for atom in _NAME_ATOMS:
            if token.startswith(atom):
                results.append(atom.capitalize())
                token = token[len(atom):]
                matched = True
                break
        if not matched:
            # No known atom matches — keep remainder as-is (capitalised)
            results.append(token.capitalize())
            break

    return results


def clean_name(raw: Optional[str]) -> str:
    """
    Normalises a raw scraped LinkedIn name string into a clean,
    properly title-cased human name.

    Handles:
      - CamelCase concatenation: "RahulSingh"   → "Rahul Singh"
      - All-lowercase concat:    "rahulsingh"   → "Rahul Singh"  (via atom dict)
      - ALL CAPS:                "PRIYA SHARMA" → "Priya Sharma"
      - Extra whitespace:        "  John  Doe " → "John Doe"
      - Already clean:           "John Doe"     → "John Doe"   (passthrough)
      - Empty / None:                           → "Hiring Manager"

    Returns:
        Cleaned, title-cased name string, or "Hiring Manager" as fallback.
    """
    if not raw:
        return _FALLBACK

    # 1. Strip non-name characters (keeps letters, spaces, hyphens, apostrophes)
    text = _STRIP_RE.sub(" ", raw.strip())

    # 2. Split on camelCase boundaries first (handles "RahulSingh" → "Rahul Singh")
    text = _CAMEL_SPLIT_RE.sub(" ", text)

    # 3. Normalise whitespace
    text = _WS_RE.sub(" ", text).strip()

    if not text:
        return _FALLBACK

    # 4. Process each whitespace-separated token
    tokens = text.split()
    final_parts: list[str] = []

    for token in tokens:
        token_lower = token.lower()

        if token.isupper() and len(token) > 1:
            # ALL-CAPS token: title-case it directly
            final_parts.append(token.capitalize())

        elif token_lower == token and len(token) > 4:
            # Fully-lowercase token longer than 4 chars: try atom splitting
            # (handles "rahulsingh" where camelCase splitter has no boundary)
            split = _split_by_known_atoms(token)
            final_parts.extend(split)

        else:
            # Mixed-case or short token: just title-case
            final_parts.append(token.capitalize())

    result = " ".join(final_parts).strip()

    # 5. Final safety check
    if not result or len(result) < 2:
        return _FALLBACK

    return result


def clean_first_name(raw: Optional[str]) -> str:
    """
    Returns just the first word of clean_name(raw).
    Used for email salutations: "Hi {first_name},".
    Falls back to "there" (so "Hi there,") if name is empty or fallback.
    """
    name = clean_name(raw)
    if name == _FALLBACK:
        return "there"
    return name.split()[0]
