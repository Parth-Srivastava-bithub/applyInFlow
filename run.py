"""CLI runner for LinkedIn Playwright Scraper."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from config import DEFAULT_QUERIES, CDP_URL, MIN_DELAY, MAX_DELAY, DEFAULT_OUTPUT_JSON, DEFAULT_OUTPUT_CSV
from scraper import LinkedInScraper
from storage import StorageManager

# Configure logging format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Playwright-based LinkedIn Recruiter & HR Profile Scraper (Reuses existing authenticated browser session)"
    )
    parser.add_argument(
        "-q", "--query",
        type=str,
        nargs="+",
        default=DEFAULT_QUERIES,
        help="Search query or list of queries (e.g. -q 'HR recruiter' 'Talent Acquisition')"
    )
    parser.add_argument(
        "-p", "--pages",
        type=int,
        default=2,
        help="Maximum search result pages to scan per query (default: 2)"
    )
    parser.add_argument(
        "-m", "--max-profiles",
        type=int,
        default=15,
        help="Maximum profiles to collect per query (default: 15)"
    )
    parser.add_argument(
        "--cdp-url",
        type=str,
        default=CDP_URL,
        help=f"Chrome DevTools Protocol endpoint (default: {CDP_URL})"
    )
    parser.add_argument(
        "--min-delay",
        type=float,
        default=MIN_DELAY,
        help=f"Minimum pacing delay between profile visits in seconds (default: {MIN_DELAY})"
    )
    parser.add_argument(
        "--max-delay",
        type=float,
        default=MAX_DELAY,
        help=f"Maximum pacing delay between profile visits in seconds (default: {MAX_DELAY})"
    )
    parser.add_argument(
        "--output-json",
        type=str,
        default=str(DEFAULT_OUTPUT_JSON),
        help="Custom path to save JSON results"
    )
    parser.add_argument(
        "--output-csv",
        type=str,
        default=str(DEFAULT_OUTPUT_CSV),
        help="Custom path to save CSV results"
    )
    return parser.parse_args()


async def main():
    args = parse_arguments()

    print("\n" + "=" * 65)
    print("[*] LinkedIn Recruiter Profile Scraper (Playwright CDP)")
    print("=" * 65)
    print(f"Target Queries : {args.query}")
    print(f"Pages / Query  : {args.pages}")
    print(f"Max Profiles   : {args.max_profiles}")
    print(f"CDP Endpoint   : {args.cdp_url}")
    print(f"Output JSON    : {args.output_json}")
    print(f"Output CSV     : {args.output_csv}")
    print("=" * 65 + "\n")

    storage = StorageManager(
        json_path=Path(args.output_json),
        csv_path=Path(args.output_csv)
    )

    scraper = LinkedInScraper(
        cdp_url=args.cdp_url,
        min_delay=args.min_delay,
        max_delay=args.max_delay,
        storage=storage
    )

    try:
        await scraper.run(
            queries=args.query,
            max_pages_per_query=args.pages,
            max_profiles_per_query=args.max_profiles
        )
    except ConnectionError as ce:
        print(f"\n❌ Browser Connection Error:\n{ce}\n")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n⚠️ Process interrupted by user. Saved partial results.")
    except Exception as e:
        logging.exception(f"Unexpected error occurred: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
