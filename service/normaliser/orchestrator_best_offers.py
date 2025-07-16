import argparse
import os
import sys
import subprocess
import math
from typing import Optional, List
from psycopg2.extensions import connection as PgConnection, cursor as PgCursor
from dotenv import load_dotenv

from .db_utils import get_db_connection

# Load environment variables
load_dotenv()

def get_min_max_product_ids() -> Optional[tuple[int, int]]:
    """
    Retrieves the minimum and maximum IDs from the products table.
    """
    conn: Optional[PgConnection] = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT MIN(id), MAX(id)
            FROM products;
        """)
        min_id, max_id = cur.fetchone()
        if min_id is None or max_id is None:
            return None
        return min_id, max_id
    except Exception as e:
        print(f"Error getting min/max product IDs: {e}", file=sys.stderr)
        return None
    finally:
        if conn:
            conn.close()

def run_worker(start_id: int, limit: int):
    """
    Runs a single best offer updater worker process.
    """
    command = [
        sys.executable, # Use the current Python executable
        "-m",
        "service.normaliser.best_offer_updater",
        "--start-id", str(start_id),
        "--limit", str(limit)
    ]
    print(f"Launching worker: {' '.join(command)}")
    process = subprocess.Popen(command, stdout=sys.stdout, stderr=sys.stderr)
    return process

def orchestrate_best_offers(num_workers: int, batch_size: int):
    """
    Orchestrates the best offer update phase by distributing product ID ranges to multiple workers.
    """
    id_range = get_min_max_product_ids()
    if not id_range:
        print("No products found in products table. Exiting best offer orchestration.", file=sys.stderr)
        return

    min_product_id, max_product_id = id_range
    
    print(f"Total product ID range: {min_product_id} to {max_product_id}")
    print(f"Orchestrating best offer update with {num_workers} workers, each processing a batch of {batch_size} product IDs.")

    processes = []
    current_start_id = min_product_id
    while current_start_id <= max_product_id:
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
    print("Best Offer Update orchestration finished.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Orchestrate best offer updates across multiple workers.")
    parser.add_argument("--num-workers", type=int, default=os.cpu_count() or 1,
                        help="Number of parallel workers to run (defaults to CPU count).")
    parser.add_argument("--batch-size", type=int, default=1000,
                        help="Number of product IDs to cover per worker batch.")
    args = parser.parse_args()

    orchestrate_best_offers(args.num_workers, args.batch_size)
