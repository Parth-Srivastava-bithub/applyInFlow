"""Relevance filter for identifying HR, Talent Acquisition, and Recruiter profiles."""

import logging
from typing import Dict, Any, Tuple
from config import HR_KEYWORDS

logger = logging.getLogger("linkedin_scraper.filter")


def is_hr_recruiter_profile(profile_data: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Evaluates whether a profile is related to HR, recruiting, or talent acquisition.
    Returns:
      (is_match: bool, reason: str)
    """
    title = (profile_data.get("title") or "").lower()
    name = (profile_data.get("name") or "").lower()

    if not title or title == "unknown":
        return False, "Empty or missing title"

    # Match against configured HR keywords
    matched_keywords = [kw for kw in HR_KEYWORDS if kw in title]

    if matched_keywords:
        return True, f"Matched keyword(s): {', '.join(matched_keywords)}"

    return False, "Title does not contain HR/recruiting keywords"
