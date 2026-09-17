"""CLI Runner for LinkedIn Job Scraper (AI/ML Engineers & Custom Roles)."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from jobs_config import (
    DEFAULT_AIML_QUERIES,
    DEFAULT_JOBS_JSON,
    DEFAULT_JOBS_CSV
)
from jobs_scraper import LinkedInJobScraper
from config import CDP_URL

# Configure logging format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)]
)


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="LinkedIn Job Scraper for AI/ML Engineer and Custom Roles (Reuses existing Chrome session)"
    )
    parser.add_argument(
        "-q", "--query",
        type=str,
        nargs="+",
        default=["AI ML Engineer"],
        help="Job search query or queries (e.g. -q 'AI ML Engineer' 'Machine Learning Engineer')"
    )
    parser.add_argument(
        "-l", "--location",
        type=str,
        default="",
        help="Job location filter (e.g. -l 'Remote' or -l 'United States' or -l 'India')"
    )
    parser.add_argument(
        "-m", "--max-jobs",
        type=int,
        default=10,
        help="Maximum jobs to collect per query (default: 10)"
    )
    parser.add_argument(
        "--no-description",
        action="store_true",
        help="Skip visiting each job detail page for full description (faster card-only extraction)"
    )
    parser.add_argument(
        "--output-json",
        type=str,
        default=str(DEFAULT_JOBS_JSON),
        help="Path to output JSON file"
    )
    parser.add_argument(
        "--output-csv",
        type=str,
        default=str(DEFAULT_JOBS_CSV),
        help="Path to output CSV file"
    )
    parser.add_argument(
        "--cdp-url",
        type=str,
        default=CDP_URL,
        help=f"CDP endpoint (default: {CDP_URL})"
    )
    return parser.parse_args()


async def main():
    args = parse_arguments()

    print("\n" + "=" * 65)
    print("[*] LinkedIn AI/ML Engineer Job Scraper")
    print("=" * 65)
    print(f"Target Query    : {args.query}")
    print(f"Location Filter : {args.location or 'Worldwide / Any'}")
    print(f"Max Jobs / Query: {args.max_jobs}")
    print(f"Full Description: {not args.no_description}")
    print(f"CDP Endpoint    : {args.cdp_url}")
    print(f"Output JSON     : {args.output_json}")
    print(f"Output CSV      : {args.output_csv}")
    print("=" * 65 + "\n")

    scraper = LinkedInJobScraper(
        cdp_url=args.cdp_url,
        output_json=Path(args.output_json),
        output_csv=Path(args.output_csv)
    )

    try:
        await scraper.run(
            queries=args.query,
            location=args.location,
            max_jobs_per_query=args.max_jobs,
            fetch_full_description=not args.no_description
        )
    except ConnectionError as ce:
        print(f"\n❌ Browser Connection Error:\n{ce}\n")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n⚠️ Process interrupted by user. Saved collected jobs.")
    except Exception as e:
        logging.exception(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
