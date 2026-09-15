"""Parser for extracting LinkedIn Job listings, descriptions, and HR emails."""

import asyncio
import logging
import re
from typing import Dict, Any, Optional, Tuple
from urllib.parse import urlparse, urlunparse
from playwright.async_api import Page, ElementHandle
from jobs_config import JOB_SELECTORS

logger = logging.getLogger("linkedin_scraper.jobs_parser")

# Email regexes
GMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@gmail\.com\b", re.IGNORECASE)
EMAIL_RE  = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,7}\b", re.IGNORECASE)

# Description container selectors — the ID prefix is stable across LinkedIn versions.
# Hashed CSS class names are NOT reliable and change with LinkedIn deploys.
DESCRIPTION_SELECTORS = [
    # Primary: LinkedIn's dynamic ID pattern (confirmed from live DOM inspection)
    "div[id^='JobDetails_AboutTheJob_']",
    "div[id*='AboutTheJob']",
    # Fallbacks for older/alternate LinkedIn layouts
    "#job-details",
    "div.jobs-description-content__text",
    "div.jobs-description__content",
    "div.jobs-box__html-content",
    "div.show-more-less-html__markup",
    "article.jobs-description__container",
    "div.description__text",
]


def normalize_job_url(raw_url: str) -> str:
    """Normalizes job URLs to clean canonical /jobs/view/123456 format."""
    if not raw_url:
        return ""
    parsed = urlparse(raw_url)
    path = parsed.path.rstrip("/")
    job_id_match = re.search(r"/jobs/view/(\d+)", path)
    if job_id_match:
        return f"https://www.linkedin.com/jobs/view/{job_id_match.group(1)}"
    query_match = re.search(r"currentJobId=(\d+)", parsed.query)
    if query_match:
        return f"https://www.linkedin.com/jobs/view/{query_match.group(1)}"
    netloc = parsed.netloc or "www.linkedin.com"
    return urlunparse(("https", netloc, path, "", "", ""))


def extract_emails_from_text(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (gmail, any_email) extracted from raw text.
    gmail  — first @gmail.com address found, or None
    email  — first email of any domain found, or None
    """
    if not text:
        return None, None
    gmail = GMAIL_RE.search(text)
    email = EMAIL_RE.search(text)
    return (
        gmail.group(0).lower() if gmail else None,
        email.group(0).lower() if email else None,
    )


async def _click_show_more(page: Page):
    """Clicks any 'See more' / 'Show more' button in the job description via JS."""
    try:
        clicked = await page.evaluate("""
        () => {
            const btns = [...document.querySelectorAll('button')];
            const more = btns.find(b => /see more|show more/i.test((b.innerText || '').trim()));
            if (more && more.offsetParent !== null) { more.click(); return true; }
            return false;
        }
        """)
        if clicked:
            logger.debug("Clicked 'Show more' on job description.")
            await asyncio.sleep(1.0)
    except Exception as e:
        logger.debug(f"show-more click failed: {e}")


async def _wait_for_description(page: Page, timeout_ms: int = 12000) -> Optional[str]:
    """
    Waits for the job description container to appear and returns its full text.
    Uses the stable div[id^='JobDetails_AboutTheJob_'] pattern found from live DOM inspection.
    """
    best_text = ""

    # Step 1: Wait for any known description container to appear in DOM
    for sel in DESCRIPTION_SELECTORS[:3]:
        try:
            await page.wait_for_selector(sel, timeout=timeout_ms)
            logger.debug(f"Description container found with selector: {sel}")
            break
        except Exception:
            continue

    # Step 2: Scroll down slowly to trigger lazy-loading
    for scroll_y in [300, 600, 900]:
        await page.evaluate(f"window.scrollBy(0, {scroll_y});")
        await asyncio.sleep(0.4)

    # Step 3: Click 'Show more' to expand truncated descriptions
    await _click_show_more(page)
    await asyncio.sleep(0.8)

    # Step 4: Extract text from all candidate selectors, keep longest
    for sel in DESCRIPTION_SELECTORS:
        try:
            elements = await page.query_selector_all(sel)
            for el in elements:
                text = await el.inner_text()
                if text and len(text.strip()) > len(best_text):
                    best_text = text.strip()
        except Exception:
            continue

    return best_text if best_text else None


async def extract_job_card_data(card_el: ElementHandle) -> Optional[Dict[str, Any]]:
    """Extracts job summary data directly from a search result card."""
    try:
        title = None
        job_url = None
        for sel in JOB_SELECTORS["job_title"]:
            title_el = await card_el.query_selector(sel)
            if title_el:
                title_text = await title_el.inner_text()
                if title_text and title_text.strip():
                    title = title_text.strip().split("\n")[0]
                    href = await title_el.get_attribute("href")
                    if href:
                        job_url = normalize_job_url(href)
                    break

        if not title:
            anchor = await card_el.query_selector("a[href*='/jobs/']")
            if anchor:
                title_text = await anchor.inner_text()
                title = title_text.strip().split("\n")[0] if title_text else "Unknown"
                href = await anchor.get_attribute("href")
                if href:
                    job_url = normalize_job_url(href)

        company = None
        for sel in JOB_SELECTORS["company_name"]:
            comp_el = await card_el.query_selector(sel)
            if comp_el:
                comp_text = await comp_el.inner_text()
                if comp_text and comp_text.strip():
                    company = comp_text.strip().split("\n")[0]
                    break

        location = None
        for sel in JOB_SELECTORS["location"]:
            loc_el = await card_el.query_selector(sel)
            if loc_el:
                loc_text = await loc_el.inner_text()
                if loc_text and loc_text.strip():
                    location = loc_text.strip().split("\n")[0]
                    break

        posted_time = None
        for sel in JOB_SELECTORS["posted_time"]:
            time_el = await card_el.query_selector(sel)
            if time_el:
                time_text = await time_el.inner_text()
                if time_text and time_text.strip():
                    posted_time = time_text.strip().split("\n")[0]
                    break

        if not title or not job_url:
            return None

        return {
            "title": title or "Unknown",
            "company": company or "Unknown",
            "location": location or "Unknown",
            "posted_time": posted_time or "Recently",
            "job_url": job_url,
        }

    except Exception as e:
        logger.debug(f"Error parsing job card: {e}")
        return None


async def extract_job_details(page: Page, job_url: str) -> Dict[str, Any]:
    """
    Properly extracts:
    - Full job description text (waits for JS render, clicks 'Show more')
    - gmail: first @gmail.com visible in the description
    - hr_email: first email of any domain visible in the description
    """
    description = await _wait_for_description(page)

    if not description:
        logger.warning(f"Empty description for: {job_url}")
        return {"description": "", "gmail": None, "hr_email": None}

    gmail, hr_email = extract_emails_from_text(description)

    if gmail:
        logger.info(f"  [EMAIL FOUND] Gmail in job description: {gmail}")
    elif hr_email:
        logger.info(f"  [EMAIL FOUND] HR email in job description: {hr_email}")

    return {
        "description": description[:2000],  # Cap at 2000 chars to keep JSON clean
        "gmail": gmail,
        "hr_email": hr_email,
    }
