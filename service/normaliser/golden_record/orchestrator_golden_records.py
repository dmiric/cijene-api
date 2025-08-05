from dotenv import load_dotenv
load_dotenv() # Load environment variables at the very top

import argparse
import os
import sys
import subprocess
import logging
import structlog
from typing import Optional, List
from psycopg2.extensions import connection as PgConnection, cursor as PgCursor

from service.normaliser.db_utils import get_db_connection
from service.main import configure_logging # Import configure_logging

# Configure logging right after imports
configure_logging()
log = structlog.get_logger()

def get_min_max_product_ids() -> Optional[tuple[int, int]]:
    """
    Retrieves the minimum and maximum IDs from the products table
    for products that do not yet have a golden record.
    """
    conn: Optional[PgConnection] = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT MIN(p.id), MAX(p.id)
            FROM products p
            LEFT JOIN g_products gp ON p.ean = gp.ean
            WHERE gp.id IS NULL;
        """)
        min_id, max_id = cur.fetchone()
        if min_id is None or max_id is None:
            return None
        return min_id, max_id
    except Exception as e:
        log.error("Error getting min/max product IDs for golden record creation", error=str(e))
        return None
    finally:
        if conn:
            conn.close()

def run_worker(normalizer_type: str, embedder_type: str, start_id: int, limit: int, pushgateway_url: str):
    """
    Runs a single golden record creator worker process.
    """
    command = [
        sys.executable, # Use the current Python executable
        "-m",
        "service.normaliser.golden_record.golden_record_creator",
        "--normalizer-type", normalizer_type.replace('-', '_'),
        "--embedder-type", embedder_type,
        "--start-id", str(start_id),
        "--limit", str(limit),
        "--pushgateway-url", pushgateway_url
    ]
    log.info("Launching worker", command=' '.join(command))
    process = subprocess.Popen(command, stdout=sys.stdout, stderr=sys.stderr)
    return process

def orchestrate_golden_records(normalizer_type: str, embedder_type: str, num_workers: int, batch_size: int, max_products_to_do: Optional[int] = None, pushgateway_url: str = "http://pushgateway:9091"):
    """
    Orchestrates the golden record creation phase by distributing product ID ranges to multiple workers.
    """
    id_range = get_min_max_product_ids()
    if not id_range:
        log.info("No products found needing golden records. Exiting golden record orchestration.")
        return

    min_product_id, max_product_id = id_range
    
    log.info("Total product ID range needing golden records", min_id=min_product_id, max_id=max_product_id)
    log.info("Orchestrating golden record creation", num_workers=num_workers, batch_size=batch_size)
    if max_products_to_do is not None:
        log.info("Limiting total products to process", max_products_to_do=max_products_to_do)

    processes = []
    current_start_id = min_product_id
    products_processed_count = 0

    while current_start_id <= max_product_id:
        if max_products_to_do is not None and products_processed_count >= max_products_to_do:
            log.info("Reached maximum products to process. Stopping orchestration.", max_products_to_do=max_products_to_do)
            break

        actual_limit = batch_size
        # Adjust limit if it would exceed max_products_to_do
        if max_products_to_do is not None:
            remaining_to_process = max_products_to_do - products_processed_count
            actual_limit = min(batch_size, remaining_to_process)
            if actual_limit <= 0:
                log.info("No more products to process within the limit. Stopping orchestration.", max_products_to_do=max_products_to_do)
                break

        process = run_worker(normalizer_type, embedder_type, current_start_id, actual_limit, pushgateway_url)
        processes.append(process)
        products_processed_count += actual_limit # Increment by the actual limit of the batch
        current_start_id += batch_size

        if len(processes) >= num_workers:
            for p in processes:
                p.wait()
            processes = []
    for p in processes: # Wait for any remaining processes
        p.wait()
    log.info("Golden Record Creation orchestration finished.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Orchestrate golden record creation across multiple workers.")
    parser.add_argument("--normalizer-type", type=str, choices=["gemini", "grok"], required=True,
                        help="Type of normalizer to use (gemini or grok).")
    parser.add_argument("--embedder-type", type=str, choices=["gemini"], default="gemini",
                        help="Type of embedder to use (e.g., gemini).")
    parser.add_argument("--num-workers", type=int, default=os.cpu_count() or 1,
                        help="Number of parallel workers to run (defaults to CPU count).")
    parser.add_argument("--batch-size", type=int, default=1000,
                        help="Number of product IDs to cover per worker batch.")
    parser.add_argument("--max-products-to-do", type=int,
                        help="Maximum number of products to process. Defaults to 100.")
    parser.add_argument("--pushgateway-url", type=str, default=os.getenv("PROMETHEUS_PUSHGATEWAY_URL", "http://pushgateway:9091"),
                        help="URL of the Prometheus Pushgateway.")
    args = parser.parse_args()

    # Calculate default max_products_to_do if not provided
    if args.max_products_to_do is None:
        args.max_products_to_do = 100

    orchestrate_golden_records(args.normalizer_type, args.embedder_type, args.num_workers, args.batch_size, args.max_products_to_do, args.pushgateway_url)
