"""Search execution and profile URL extraction with pagination support."""

import asyncio
import logging
from urllib.parse import quote_plus
from typing import List, Set
from playwright.async_api import Page
from config import SELECTORS, SEARCH_PAGE_DELAY
from parser import normalize_linkedin_url

logger = logging.getLogger("linkedin_scraper.search")


async def search_linkedin_people(
    page: Page,
    query: str,
    max_pages: int = 3,
    max_profiles: int = 50,
    seen_urls: Set[str] = None
) -> List[str]:
    """
    Performs a LinkedIn search for people matching the query and collects profile URLs.
    Handles pagination across multiple search result pages.
    """
    if seen_urls is None:
        seen_urls = set()

    collected_urls: List[str] = []
    encoded_query = quote_plus(query)
    base_search_url = f"https://www.linkedin.com/search/results/people/?keywords={encoded_query}&origin=GLOBAL_SEARCH_HEADER"

    logger.info(f"Starting search for query '{query}' (Max pages: {max_pages}, Max profiles: {max_profiles})")

    for current_page_num in range(1, max_pages + 1):
        if len(collected_urls) >= max_profiles:
            logger.info(f"Reached target limit of {max_profiles} profiles.")
            break

        page_url = f"{base_search_url}&page={current_page_num}" if current_page_num > 1 else base_search_url
        logger.info(f"Navigating to search page {current_page_num}: {page_url}")

        try:
            await page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(SEARCH_PAGE_DELAY)

            # Scroll down slowly to trigger LinkedIn's lazy-loaded result items
            await page.evaluate("window.scrollBy(0, 800);")
            await asyncio.sleep(1.0)
            await page.evaluate("window.scrollBy(0, 1000);")
            await asyncio.sleep(1.5)

            # Extract all anchor tags with /in/ links in the page
            anchors = await page.query_selector_all("a[href*='/in/']")
            page_urls_found = 0

            for anchor in anchors:
                raw_href = await anchor.get_attribute("href")
                if not raw_href:
                    continue

                # Exclude overlay links, edit links, or non-profile anchors
                if any(x in raw_href for x in ["/edit/", "/detail/", "miniprofileurn", "/overlay/"]):
                    # Note: miniprofileurn is often in query string of valid profiles, handled by normalizer
                    pass

                normalized = normalize_linkedin_url(raw_href)

                # Check if it's a valid individual profile link (/in/username)
                if "/in/" in normalized and not normalized.endswith("/in") and not normalized.endswith("/in/"):
                    if normalized not in seen_urls and normalized not in collected_urls:
                        collected_urls.append(normalized)
                        page_urls_found += 1
                        if len(collected_urls) >= max_profiles:
                            break

            logger.info(f"Found {page_urls_found} new profile URLs on page {current_page_num}. Total collected so far: {len(collected_urls)}")

            # If no results found or reached last page, stop pagination
            if page_urls_found == 0 and current_page_num > 1:
                logger.info("No further profile links found. Ending pagination for this query.")
                break

        except Exception as e:
            logger.warning(f"Failed loading search page {current_page_num} for query '{query}': {e}")
            break

    return collected_urls
