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

def get_product_ids_needing_golden_records() -> List[int]:
    """
    Retrieves a list of product IDs from the products table
    for products that do not yet have a golden record.
    """
    conn: Optional[PgConnection] = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT p.id
            FROM products p
            LEFT JOIN g_products gp ON p.ean = gp.ean
            WHERE gp.id IS NULL
            ORDER BY p.id;
        """)
        product_ids = [row[0] for row in cur.fetchall()]
        return product_ids
    except Exception as e:
        log.error("Error getting product IDs for golden record creation", error=str(e))
        return []
    finally:
        if conn:
            conn.close()

def run_worker(normalizer_type: str, embedder_type: str, product_ids_batch: List[int], pushgateway_url: str):
    """
    Runs a single golden record creator worker process with a specific list of product IDs.
    """
    product_ids_str = ",".join(map(str, product_ids_batch))
    command = [
        sys.executable, # Use the current Python executable
        "-m",
        "service.normaliser.golden_record.golden_record_creator",
        "--normalizer-type", normalizer_type.replace('-', '_'),
        "--embedder-type", embedder_type,
        "--product-ids", product_ids_str,
        "--pushgateway-url", pushgateway_url
    ]
    log.info("Launching worker", command=' '.join(command), num_ids=len(product_ids_batch))
    process = subprocess.Popen(command, stdout=sys.stdout, stderr=sys.stderr)
    return process

def orchestrate_golden_records(normalizer_type: str, embedder_type: str, num_workers: int, batch_size: int, max_products_to_do: Optional[int] = None, pushgateway_url: str = "http://pushgateway:9091"):
    """
    Orchestrates the golden record creation phase by distributing specific product IDs to multiple workers.
    """
    all_product_ids = get_product_ids_needing_golden_records()
    if not all_product_ids:
        log.info("No products found needing golden records. Exiting golden record orchestration.")
        return

    if max_products_to_do is not None:
        all_product_ids = all_product_ids[:max_products_to_do]
        log.info("Limiting total products to process", max_products_to_do=max_products_to_do, actual_count=len(all_product_ids))

    log.info("Total products needing golden records", count=len(all_product_ids))
    log.info("Orchestrating golden record creation", num_workers=num_workers, batch_size=batch_size)

    processes = []
    
    for i in range(0, len(all_product_ids), batch_size):
        product_ids_batch = all_product_ids[i:i + batch_size]
        
        process = run_worker(normalizer_type, embedder_type, product_ids_batch, pushgateway_url)
        processes.append(process)

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
                        help="Maximum number of products to process. Defaults to None (process all).")
    parser.add_argument("--pushgateway-url", type=str, default=os.getenv("PROMETHEUS_PUSHGATEWAY_URL", "http://pushgateway:9091"),
                        help="URL of the Prometheus Pushgateway.")
    args = parser.parse_args()

    orchestrate_golden_records(args.normalizer_type, args.embedder_type, args.num_workers, args.batch_size, args.max_products_to_do, args.pushgateway_url)
