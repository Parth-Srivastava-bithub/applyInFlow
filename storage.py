"""Storage and deduplication management for scraped profile records."""

import csv
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Set
from config import DEFAULT_OUTPUT_JSON, DEFAULT_OUTPUT_CSV, SEEN_URLS_FILE

logger = logging.getLogger("linkedin_scraper.storage")


class StorageManager:
    """Handles deduplication and persistence to JSON and CSV formats."""

    def __init__(
        self,
        json_path: Path = DEFAULT_OUTPUT_JSON,
        csv_path: Path = DEFAULT_OUTPUT_CSV,
        seen_urls_path: Path = SEEN_URLS_FILE,
    ):
        self.json_path = json_path
        self.csv_path = csv_path
        self.seen_urls_path = seen_urls_path
        self.seen_urls: Set[str] = self._load_seen_urls()
        self.records: List[Dict[str, Any]] = self._load_existing_records()

    def _load_seen_urls(self) -> Set[str]:
        """Loads previously seen URLs from disk."""
        if self.seen_urls_path.exists():
            try:
                with open(self.seen_urls_path, "r", encoding="utf-8") as f:
                    urls = json.load(f)
                    return set(urls)
            except Exception as e:
                logger.warning(f"Could not load seen URLs from {self.seen_urls_path}: {e}")
        return set()

    def _save_seen_urls(self):
        """Persists seen URLs to disk."""
        try:
            with open(self.seen_urls_path, "w", encoding="utf-8") as f:
                json.dump(list(self.seen_urls), f, indent=2)
        except Exception as e:
            logger.error(f"Error saving seen URLs: {e}")

    def _load_existing_records(self) -> List[Dict[str, Any]]:
        """Loads existing scraped records from the output JSON file if present."""
        if self.json_path.exists():
            try:
                with open(self.json_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load existing records: {e}")
        return []

    def is_seen(self, url: str) -> bool:
        """Checks if a normalized profile URL was already scraped or queued."""
        return url in self.seen_urls

    def add_record(self, record: Dict[str, Any]):
        """Adds a new valid profile record and marks URL as seen."""
        url = record.get("linkedin_url")
        if url:
            self.seen_urls.add(url)
        self.records.append(record)
        self._save_seen_urls()
        self.export_json()
        self.export_csv()

    def mark_seen(self, url: str):
        """Marks a URL as seen even if skipped/filtered."""
        if url:
            self.seen_urls.add(url)
            self._save_seen_urls()

    def export_json(self, path: Path = None):
        """Exports all records to formatted JSON."""
        target_path = path or self.json_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(self.records, f, indent=2, ensure_ascii=False)
        logger.info(f"Saved {len(self.records)} records to {target_path}")

    def export_csv(self, path: Path = None):
        """Exports all records to CSV."""
        target_path = path or self.csv_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = ["name", "title", "company", "location", "linkedin_url", "gmail"]
        with open(target_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for record in self.records:
                # Ensure all keys are present
                row = {k: record.get(k, "") or "" for k in fieldnames}
                writer.writerow(row)
