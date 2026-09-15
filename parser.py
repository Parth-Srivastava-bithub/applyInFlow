"""HTML/DOM parser for extracting publicly visible LinkedIn profile data."""

import logging
import re
from typing import Optional, Dict, Any
from urllib.parse import urlparse, urlunparse
from playwright.async_api import Page
from config import SELECTORS, GMAIL_REGEX, EMAIL_REGEX

logger = logging.getLogger("linkedin_scraper.parser")


def normalize_linkedin_url(raw_url: str) -> str:
    """
    Normalizes a LinkedIn profile URL by removing tracking query parameters,
    session tokens, and trailing slashes.
    Example:
      'https://www.linkedin.com/in/john-doe-123/?miniProfileUrn=urn%3Ali%3A...'
      -> 'https://www.linkedin.com/in/john-doe-123'
    """
    if not raw_url:
        return ""
    
    parsed = urlparse(raw_url)
    path = parsed.path.rstrip("/")
    
    # Ensure scheme and domain
    netloc = parsed.netloc or "www.linkedin.com"
    if "linkedin.com" not in netloc:
        netloc = "www.linkedin.com"
        
    return urlunparse(("https", netloc, path, "", "", ""))


def extract_emails_from_text(text: str) -> Dict[str, Optional[str]]:
    """
    Extracts publicly visible email addresses from text, separating Gmail vs other domains.
    Returns a dict with 'gmail' and 'email'.
    """
    if not text:
        return {"gmail": None, "email": None}

    gmail_match = GMAIL_REGEX.search(text)
    gmail = gmail_match.group(0) if gmail_match else None

    email_match = EMAIL_REGEX.search(text)
    email = email_match.group(0) if email_match else None

    return {
        "gmail": gmail,
        "email": email or gmail
    }


async def parse_profile_page(page: Page, profile_url: str) -> Dict[str, Any]:
    """
    Extracts publicly visible profile information from the rendered LinkedIn profile page:
    - Full Name
    - Job Title / Headline
    - Current Company
    - Location
    - LinkedIn Profile URL
    - Public Gmail / Email (from headline, about section, summary)
    """
    normalized_url = normalize_linkedin_url(profile_url)
    logger.debug(f"Parsing profile: {normalized_url}")

    # 1. Full Name
    name = None
    for sel in SELECTORS["name"]:
        el = await page.query_selector(sel)
        if el:
            raw_name = await el.inner_text()
            if raw_name and raw_name.strip():
                name = raw_name.strip().split("\n")[0]
                break

    # 2. Headline / Title
    headline = None
    for sel in SELECTORS["headline"]:
        el = await page.query_selector(sel)
        if el:
            raw_title = await el.inner_text()
            if raw_title and raw_title.strip():
                headline = raw_title.strip()
                break

    # 3. Location
    location = None
    for sel in SELECTORS["location"]:
        el = await page.query_selector(sel)
        if el:
            raw_loc = await el.inner_text()
            if raw_loc and raw_loc.strip():
                location = raw_loc.strip().split("\n")[0]
                # Filter out 'Contact info' or non-location strings
                if "contact info" not in location.lower():
                    break

    # 4. Current Company (from company badge or parsing headline)
    company = None
    for sel in SELECTORS["current_company"]:
        el = await page.query_selector(sel)
        if el:
            raw_company = await el.inner_text()
            if raw_company and raw_company.strip():
                company = raw_company.strip().split("\n")[0]
                break

    # Fallback company extraction from headline (e.g. "Recruiter at Google" -> "Google")
    if not company and headline:
        at_match = re.search(r"\b(?:at|@|with)\s+([^|,•·\n]+)", headline, re.IGNORECASE)
        if at_match:
            company = at_match.group(1).strip()

    # 5. About / Summary Section & Public Email Detection
    about_text = ""
    for sel in SELECTORS["about_section"]:
        el = await page.query_selector(sel)
        if el:
            raw_about = await el.inner_text()
            if raw_about:
                about_text += " " + raw_about.strip()

    # Check top card text as well for emails
    top_card_el = await page.query_selector("section.artdeco-card, div.ph5")
    top_card_text = await top_card_el.inner_text() if top_card_el else ""

    combined_text = f"{headline or ''} {about_text} {top_card_text}"
    email_data = extract_emails_from_text(combined_text)

    profile_data = {
        "name": name or "Unknown",
        "title": headline or "Unknown",
        "company": company or "Unknown",
        "location": location or "Unknown",
        "linkedin_url": normalized_url,
        "gmail": email_data["gmail"]  # Only populated if publicly visible @gmail.com is present
    }

    logger.debug(f"Extracted data: {profile_data}")
    return profile_data
