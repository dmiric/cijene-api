#!/usr/bin/env python3
import argparse
from datetime import datetime
import logging
import sys
import asyncio
from pathlib import Path

from crawler.crawl import crawl, get_chains


def parse_date(date_str):
    """Parse a date string in YYYY-MM-DD format."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError("Date must be in YYYY-MM-DD format")


def setup_logging(log_level_str: str):
    """
    Configure logging.
    Sets a high-level default for all loggers, then sets a specific,
    more verbose level for the 'crawler' package logger.
    """
    log_level = getattr(logging, log_level_str.upper(), logging.INFO)
    logging.basicConfig(
        level=logging.WARNING,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        stream=sys.stdout,
    )
    crawler_logger = logging.getLogger("crawler")
    crawler_logger.setLevel(log_level)


async def main(): # Made async
    parser = argparse.ArgumentParser(
        description="Crawl retail chains for product pricing data",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "-d",
        "--date",
        type=parse_date,
        help="Date for which to crawl (format: YYYY-MM-DD, defaults to today)",
    )
    parser.add_argument(
        "-c",
        "--chain",
        help="Comma-separated list of retail chains to crawl (defaults to all)",
    )
    parser.add_argument(
        "-l",
        "--list",
        action="store_true",
        help="List supported retail chains and exit (output_path is not required)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        choices=["debug", "info", "warning", "error", "critical"],
        default="info",
        help="Set verbosity level (default: info)", # Changed default to info
    )
    # --- FIX 1: REMOVED THE UNUSED --workers ARGUMENT ---
    # parser.add_argument(
    #     "-w",
    #     "--workers",
    #     type=int,
    #     default=4,
    #     help="Number of parallel workers for crawling (default: 4)",
    # )

    args = parser.parse_args()

    # Set up logging
    setup_logging(args.verbose)

    if args.list:
        print("Supported retail chains:")
        for chain_name in get_chains():
            print(f"  - {chain_name}")
        return 0

    # Hardcode the output path
    output_root_path = Path("/app/output")

    chains_to_crawl = None
    if args.chain:
        chains_to_crawl = [chain.strip() for chain in args.chain.split(",")]
        available_chains = get_chains()
        for chain_name in chains_to_crawl:
            if chain_name not in available_chains:
                parser.error(
                    f"Unknown chain '{chain_name}'. Available chains: {', '.join(available_chains)}"
                )

    # Run the crawler
    try:
        # Ensure date is None if not provided, so crawl() uses its default
        crawl_date = args.date

        chains_txt = (
            ", ".join(chains_to_crawl) if chains_to_crawl else "all retail chains"
        )
        date_txt = args.date.strftime("%Y-%m-%d") if args.date else "today"
        print(f"Fetching price data from {chains_txt} for {date_txt} ...", flush=True)

        # --- FIX 2: REMOVED args.workers FROM THE FUNCTION CALL ---
        zip_paths = await crawl(output_root_path, crawl_date, chains_to_crawl)
        
        # Print the paths for potential downstream processing
        for path in zip_paths:
            print(str(path))
            
        return 0
    except Exception as e:
        print(f"Error during crawling: {e}", file=sys.stderr) # Print errors to stderr
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main())) # Run main as async