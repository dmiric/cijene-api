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
import psycopg2
from psycopg2.extras import RealDictCursor

from .db_utils import get_db_connection
from service.main import configure_logging # Import configure_logging

def delete_old_prices_and_chain_products() -> None:
    """
    Deletes prices, chain_prices, and g_prices older than 3 days,
    and then deletes chain_products that no longer have associated prices.
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                log.info("Deleting prices older than 3 days...")
                cur.execute("DELETE FROM prices WHERE price_date < CURRENT_DATE - INTERVAL '3 days';")
                log.info("Deleted old price entries.", row_count=cur.rowcount)

                log.info("Deleting chain_prices older than 3 days...")
                cur.execute("DELETE FROM chain_prices WHERE price_date < CURRENT_DATE - INTERVAL '3 days';")
                log.info("Deleted old chain_price entries.", row_count=cur.rowcount)

                log.info("Deleting g_prices older than 3 days...")
                cur.execute("DELETE FROM g_prices WHERE price_date < CURRENT_DATE - INTERVAL '3 days';")
                log.info("Deleted old g_price entries.", row_count=cur.rowcount)

                log.info("Deleting chain_products with no associated prices...")
                cur.execute("""
                    DELETE FROM chain_products cp
                    WHERE NOT EXISTS (SELECT 1 FROM prices p WHERE p.chain_product_id = cp.id)
                      AND NOT EXISTS (SELECT 1 FROM chain_prices c_p WHERE c_p.chain_product_id = cp.id);
                """)
                log.info("Deleted old chain_product entries.", row_count=cur.rowcount)

                conn.commit()
                log.info("Old price and chain_product data cleanup complete.")

    except Exception as e:
        log.error("Error during old price and chain_product cleanup", error=str(e))

def get_min_max_chain_product_ids() -> Optional[tuple[int, int]]:
    """
    Retrieves the minimum and maximum chain_product_id from the prices table for unprocessed entries.
    """
    conn: Optional[PgConnection] = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT MIN(chain_product_id), MAX(chain_product_id)
            FROM prices
            WHERE processed = FALSE;
        """)
        min_id, max_id = cur.fetchone()
        if min_id is None or max_id is None:
            return None
        return min_id, max_id
    except Exception as e:
        log.error("Error getting min/max chain_product IDs", error=str(e))
        return None
    finally:
        if conn:
            conn.close()

def run_worker(start_id: int, limit: int):
    """
    Runs a single price calculator worker process.
    """
    command = [
        sys.executable, # Use the current Python executable
        "-m",
        "service.normaliser.price_calculator",
        "--start-id", str(start_id),
        "--limit", str(limit)
    ]
    log.info("Launching worker", command=' '.join(command))
    process = subprocess.Popen(command, stdout=sys.stdout, stderr=sys.stderr)
    return process

def orchestrate_prices(num_workers: int, batch_size: int):
    """
    Orchestrates the price calculation phase by distributing chain_product_id ranges to multiple workers.
    """
    id_range = get_min_max_chain_product_ids()
    if not id_range:
        log.info("No unprocessed prices found. Exiting price calculation orchestration.")
        return

    min_chain_product_id, max_chain_product_id = id_range
    
    log.info("Total chain_product ID range for unprocessed prices", min_id=min_chain_product_id, max_id=max_chain_product_id)
    log.info("Orchestrating price calculation", num_workers=num_workers, batch_size=batch_size)

    processes = []
    current_start_id = min_chain_product_id
    while current_start_id <= max_chain_product_id:
        actual_limit = batch_size
        process = run_worker(current_start_id, actual_limit)
        processes.append(process)
        current_start_id += batch_size

        if len(processes) >= num_workers:
            for p in processes:
                p.wait()
            processes = []
    for p in processes: # Wait for any remaining processes
        p.wait()
    log.info("Price Calculation orchestration finished.")

if __name__ == "__main__":
    configure_logging() # Configure logging at the start of the script
    log = structlog.get_logger() # Initialize structlog logger AFTER configuration
    parser = argparse.ArgumentParser(description="Orchestrate price calculation across multiple workers.")
    parser.add_argument("--num-workers", type=int, default=os.cpu_count() or 1,
                        help="Number of parallel workers to run (defaults to CPU count).")
    parser.add_argument("--batch-size", type=int, default=1000,
                        help="Number of chain_product IDs to cover per worker batch.")
    args = parser.parse_args()

    delete_old_prices_and_chain_products() # Call the cleanup function before orchestration
    orchestrate_prices(args.num_workers, args.batch_size)
