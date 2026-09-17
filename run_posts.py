"""CLI runner for LinkedIn Posts -> HR Gmail scraper."""

import argparse
import asyncio
import logging
import sys
from posts_scraper import main, DEFAULT_QUERIES
from config import CDP_URL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)]
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Scrape LinkedIn POSTS for HR Gmail addresses (e.g. 'hr with mail')"
    )
    parser.add_argument(
        "-q", "--query", nargs="+",
        default=["hr with mail"],
        help="Search queries (e.g. -q 'hr with mail' 'mail cv gmail india')"
    )
    parser.add_argument(
        "-m", "--max-posts", type=int, default=30,
        help="Max posts with emails to collect per query (default: 30)"
    )
    parser.add_argument(
        "--cdp-url", default=CDP_URL,
        help="Chrome CDP endpoint"
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    print("\n" + "=" * 65)
    print("[*] LinkedIn Posts -> HR Gmail Scraper")
    print("=" * 65)
    print(f"Queries    : {args.query}")
    print(f"Max Posts  : {args.max_posts}")
    print(f"CDP        : {args.cdp_url}")
    print("=" * 65 + "\n")

    try:
        asyncio.run(main(
            queries=args.query,
            max_posts=args.max_posts,
            cdp_url=args.cdp_url,
        ))
    except ConnectionError as e:
        print(f"\nBrowser connection failed: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted. Partial results saved.")
