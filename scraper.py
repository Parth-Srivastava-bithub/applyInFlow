"""Core scraping engine coordinating search, profile visits, parsing, filtering, and export."""

import asyncio
import logging
import random
from typing import List, Optional, Dict, Any
from playwright.async_api import Page

from config import MIN_DELAY, MAX_DELAY, MAX_RETRIES, DEFAULT_QUERIES
from browser import BrowserManager
from search import search_linkedin_people
from parser import parse_profile_page
from filter import is_hr_recruiter_profile
from storage import StorageManager

logger = logging.getLogger("linkedin_scraper.engine")


class LinkedInScraper:
    """Modular LinkedIn Recruiter Scraper for Playwright."""

    def __init__(
        self,
        cdp_url: Optional[str] = None,
        min_delay: float = MIN_DELAY,
        max_delay: float = MAX_DELAY,
        storage: Optional[StorageManager] = None
    ):
        self.browser_manager = BrowserManager(cdp_url=cdp_url) if cdp_url else BrowserManager()
        self.storage = storage or StorageManager()
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.page: Optional[Page] = None

    async def _polite_delay(self):
        """Pauses execution with random jitter to mimic human browsing and prevent rate-limiting."""
        delay = random.uniform(self.min_delay, self.max_delay)
        logger.debug(f"Pacing delay: waiting {delay:.2f}s...")
        await asyncio.sleep(delay)

    async def _visit_and_parse_with_retry(self, profile_url: str) -> Optional[Dict[str, Any]]:
        """Visits a single profile page with retry logic."""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                logger.info(f"Visiting [{attempt}/{MAX_RETRIES}]: {profile_url}")
                await self.page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
                
                # Small scroll to ensure top card and about sections are fully rendered
                await asyncio.sleep(1.2)
                await self.page.evaluate("window.scrollBy(0, 500);")
                await asyncio.sleep(0.8)

                # Parse data
                data = await parse_profile_page(self.page, profile_url)
                return data

            except Exception as e:
                logger.warning(f"Attempt {attempt} failed for {profile_url}: {e}")
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(2.0 * attempt)
                else:
                    logger.error(f"Permanently failed to load profile: {profile_url}")
                    return None

    async def run(
        self,
        queries: List[str] = DEFAULT_QUERIES,
        max_pages_per_query: int = 2,
        max_profiles_per_query: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Executes the end-to-end scraping workflow:
        1. Connects to existing authenticated browser session.
        2. Searches LinkedIn for each query.
        3. Collects and deduplicates profile URLs.
        4. Visits each profile, extracts data, filters for HR roles, and saves results.
        """
        try:
            self.page = await self.browser_manager.connect()
            
            # Verify login status
            is_authenticated = await self.browser_manager.verify_login_status()
            if not is_authenticated:
                logger.warning(
                    "Warning: Could not confirm active LinkedIn login. "
                    "Make sure you are logged into LinkedIn in the connected Chrome window."
                )

            total_extracted = 0
            total_filtered_out = 0
            total_failed = 0

            for query in queries:
                logger.info(f"\n{'='*50}\nProcessing Query: '{query}'\n{'='*50}")
                
                urls = await search_linkedin_people(
                    page=self.page,
                    query=query,
                    max_pages=max_pages_per_query,
                    max_profiles=max_profiles_per_query,
                    seen_urls=self.storage.seen_urls
                )

                logger.info(f"Discovered {len(urls)} profile candidate(s) for query: '{query}'")

                for idx, url in enumerate(urls, 1):
                    if self.storage.is_seen(url):
                        logger.info(f"Skipping already processed profile ({idx}/{len(urls)}): {url}")
                        continue

                    # Polite pacing delay
                    await self._polite_delay()

                    logger.info(f"Processing profile ({idx}/{len(urls)}): {url}")
                    data = await self._visit_and_parse_with_retry(url)

                    if not data or data.get("name") == "Unknown":
                        logger.warning(f"Skipping unparseable or blank profile: {url}")
                        self.storage.mark_seen(url)
                        total_failed += 1
                        continue

                    # Apply HR / Recruiter relevance filter
                    is_match, reason = is_hr_recruiter_profile(data)
                    if not is_match:
                        logger.info(f"Filtered out non-HR profile '{data['name']}' ({data['title']}): {reason}")
                        self.storage.mark_seen(url)
                        total_filtered_out += 1
                        continue

                    # Log email status
                    if data.get("gmail"):
                        logger.info(f"[GMAIL] Found publicly visible Gmail for {data['name']}: {data['gmail']}")
                    else:
                        logger.info(f"Extracted HR Profile: {data['name']} | {data['title']} | {data['company']}")

                    # Store record
                    self.storage.add_record(data)
                    total_extracted += 1

            logger.info(
                f"\n{'='*50}\nScraping Completed Summary:\n"
                f" - Total Valid HR Profiles Saved: {total_extracted}\n"
                f" - Filtered Out (Non-HR): {total_filtered_out}\n"
                f" - Failed / Skipped: {total_failed}\n"
                f" - Output JSON: {self.storage.json_path}\n"
                f" - Output CSV: {self.storage.csv_path}\n"
                f"{'='*50}"
            )

            return self.storage.records

        finally:
            await self.browser_manager.close()
