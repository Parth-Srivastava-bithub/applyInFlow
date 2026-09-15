"""LinkedIn Job Scraper engine for extracting AI/ML job listings."""

import asyncio
import json
import logging
import random
from pathlib import Path
from urllib.parse import quote_plus
from typing import List, Dict, Any, Optional
from playwright.async_api import Page

from browser import BrowserManager
from jobs_config import (
    DEFAULT_JOBS_JSON,
    DEFAULT_JOBS_CSV,
    SEEN_JOBS_FILE,
    DEFAULT_AIML_QUERIES,
    JOB_SELECTORS
)
from jobs_parser import extract_job_card_data, extract_job_details, normalize_job_url
from storage import StorageManager

logger = logging.getLogger("linkedin_scraper.jobs_engine")


class LinkedInJobScraper:
    """Orchestrates searching and scraping LinkedIn job postings."""

    def __init__(
        self,
        cdp_url: str = "http://localhost:9222",
        output_json: Path = DEFAULT_JOBS_JSON,
        output_csv: Path = DEFAULT_JOBS_CSV,
        min_delay: float = 2.0,
        max_delay: float = 4.0,
    ):
        self.browser_manager = BrowserManager(cdp_url=cdp_url)
        self.storage = StorageManager(
            json_path=output_json,
            csv_path=output_csv,
            seen_urls_path=SEEN_JOBS_FILE
        )
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.page: Optional[Page] = None

    async def _polite_delay(self):
        delay = random.uniform(self.min_delay, self.max_delay)
        await asyncio.sleep(delay)

    async def search_jobs(
        self,
        query: str,
        location: str = "",
        max_jobs: int = 25
    ) -> List[Dict[str, Any]]:
        """Searches for jobs matching query and extracts job card metadata."""
        encoded_query = quote_plus(query)
        encoded_loc = quote_plus(location) if location else ""
        
        search_url = f"https://www.linkedin.com/jobs/search/?keywords={encoded_query}"
        if location:
            search_url += f"&location={encoded_loc}"

        logger.info(f"Navigating to Job Search: {search_url}")
        await self.page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3.0)

        collected_jobs: List[Dict[str, Any]] = []

        # Find job list items
        for scroll_step in range(1, 6):
            if len(collected_jobs) >= max_jobs:
                break

            # Scroll list container to load more jobs
            await self.page.evaluate("window.scrollBy(0, 1000);")
            await asyncio.sleep(1.5)

            # Look for job card elements
            card_elements = []
            for sel in JOB_SELECTORS["job_card"]:
                card_elements = await self.page.query_selector_all(sel)
                if card_elements:
                    break

            logger.info(f"Found {len(card_elements)} job cards visible on search page...")

            for card in card_elements:
                if len(collected_jobs) >= max_jobs:
                    break

                job_data = await extract_job_card_data(card)
                if not job_data:
                    continue

                url = job_data["job_url"]
                if self.storage.is_seen(url) or any(j["job_url"] == url for j in collected_jobs):
                    continue

                collected_jobs.append(job_data)

        return collected_jobs

    async def run(
        self,
        queries: List[str] = DEFAULT_AIML_QUERIES,
        location: str = "",
        max_jobs_per_query: int = 15,
        fetch_full_description: bool = True
    ) -> List[Dict[str, Any]]:
        """Runs the complete Job Scraping pipeline."""
        try:
            self.page = await self.browser_manager.connect()
            total_added = 0

            for query in queries:
                logger.info(f"\n{'='*55}\nSearching Jobs for: '{query}' (Location: '{location or 'Any'}')\n{'='*55}")
                
                jobs = await self.search_jobs(
                    query=query,
                    location=location,
                    max_jobs=max_jobs_per_query
                )

                logger.info(f"Found {len(jobs)} new job postings for '{query}'")

                for idx, job in enumerate(jobs, 1):
                    url = job["job_url"]
                    if self.storage.is_seen(url):
                        continue

                    # If requested, fetch full description by clicking or visiting
                    if fetch_full_description:
                        await self._polite_delay()
                        try:
                            logger.info(f"Fetching job details ({idx}/{len(jobs)}): {job['title']} @ {job['company']}")
                            await self.page.goto(url, wait_until="domcontentloaded", timeout=25000)
                            await asyncio.sleep(2.0)  # Let JS fully render
                            details = await extract_job_details(self.page, url)
                            job["description"] = details.get("description", "")
                            job["gmail"]    = details.get("gmail")
                            job["hr_email"] = details.get("hr_email")
                        except Exception as e:
                            logger.warning(f"Could not load details for {url}: {e}")
                            job["description"] = ""
                            job["gmail"]    = None
                            job["hr_email"] = None
                    else:
                        job["description"] = ""
                        job["gmail"]    = None
                        job["hr_email"] = None

                    self.storage.add_record(job)
                    total_added += 1
                    logger.info(f"[SAVED] Job: {job['title']} | {job['company']} | {job['location']}")

            logger.info(
                f"\n{'='*55}\nJob Scraping Completed!\n"
                f" - Total New Jobs Saved: {total_added}\n"
                f" - Output JSON: {self.storage.json_path}\n"
                f" - Output CSV: {self.storage.csv_path}\n"
                f"{'='*55}"
            )

            return self.storage.records

        finally:
            await self.browser_manager.close()
