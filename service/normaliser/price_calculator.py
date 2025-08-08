from dotenv import load_dotenv
load_dotenv() # Load environment variables at the very top

import argparse
import os
import logging
import structlog
from typing import Optional, List, Dict, Any
from psycopg2.extensions import connection as PgConnection, cursor as PgCursor

import psycopg2
from psycopg2.extras import RealDictCursor

# Import database utility functions
from .db_utils import (
    get_db_connection,
    calculate_unit_prices,
)
from service.main import configure_logging # Import configure_logging

def process_prices_batch(start_id: int, limit: int) -> None:
    """
    Processes unprocessed rows from prices, calculates unit prices,
    inserts into g_prices, and marks them as processed.
    """
    conn: Optional[PgConnection] = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 1. Fetch unprocessed prices that have a linked g_product
            cur.execute("""
                SELECT
                    gp.id AS g_product_id,
                    gp.base_unit_type,
                    gp.variants,
                    cp.id AS chain_product_id,
                    pz.store_id,
                    pz.price_date,
                    pz.regular_price,
                    pz.special_price
                FROM
                    prices pz
                JOIN chain_products cp ON pz.chain_product_id = cp.id
                JOIN products pr ON cp.product_id = pr.id
                JOIN g_products gp ON pr.ean = gp.ean
                WHERE
                    pz.processed = FALSE
                    AND pz.chain_product_id >= %s
                    AND pz.chain_product_id < %s + %s
                ORDER BY
                    gp.id, pz.price_date;
            """, (start_id, start_id, limit))

            rows_to_process = cur.fetchall()

            if not rows_to_process:
                log.info("No unprocessed prices found in range.", start_id=start_id, limit=limit)
                return

            log.info("Processing unprocessed prices", count=len(rows_to_process), start_id=start_id, limit=limit)

            g_prices_data = []
            prices_to_mark_processed = []

            for record in rows_to_process:
                current_price = (
                    record['special_price']
                    if record['special_price'] is not None
                    else record['regular_price']
                )
                if current_price is None:
                    continue  # Skip if no price

                try:
                    # Calculate unit prices
                    calculated = calculate_unit_prices(
                        price=current_price,
                        base_unit_type=record['base_unit_type'],
                        variants=record['variants'] or []
                    )

                    g_prices_data.append((
                        record['g_product_id'],
                        record['store_id'],
                        record['price_date'],
                        record['regular_price'],
                        record['special_price'],
                        calculated['price_per_kg'],
                        calculated['price_per_l'],
                        calculated['price_per_piece'],
                        record['special_price'] is not None
                    ))

                    prices_to_mark_processed.append((
                        record['chain_product_id'],
                        record['store_id'],
                        record['price_date']
                    ))

                except Exception as e:
                    log.error("Error processing price row, skipping.",
                              chain_product_id=record['chain_product_id'],
                              store_id=record['store_id'],
                              price_date=str(record['price_date']),
                              error=str(e))
                    continue

            if g_prices_data:
                # Bulk insert or update into g_prices
                from psycopg2.extras import execute_values
                execute_values(
                    cur,
                    """
                    INSERT INTO g_prices (
                        product_id, store_id, price_date,
                        regular_price, special_price,
                        price_per_kg, price_per_l, price_per_piece,
                        is_on_special_offer
                    )
                    VALUES %s
                    ON CONFLICT (product_id, store_id, price_date) DO UPDATE SET
                        regular_price = EXCLUDED.regular_price,
                        special_price = EXCLUDED.special_price,
                        price_per_kg = EXCLUDED.price_per_kg,
                        price_per_l = EXCLUDED.price_per_l,
                        price_per_piece = EXCLUDED.price_per_piece,
                        is_on_special_offer = EXCLUDED.is_on_special_offer;
                    """,
                    g_prices_data
                )

            if prices_to_mark_processed:
                # Bulk update original price rows as processed
                execute_values(
                    cur,
                    """
                    UPDATE prices
                    SET processed = TRUE
                    WHERE (chain_product_id, store_id, price_date) IN %s;
                    """,
                    prices_to_mark_processed
                )

            conn.commit()
            log.info("Batch processing complete.", processed_count=len(rows_to_process))
    except Exception as e:
        log.error("Fatal error in price processing batch.", error=str(e))
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    configure_logging() # Configure logging at the start of the script
    log = structlog.get_logger() # Initialize structlog logger AFTER configuration
    parser = argparse.ArgumentParser(description="Process a batch of products for price calculation.")
    parser.add_argument("--start-id", type=int, required=True, help="Starting product_id for the batch.")
    parser.add_argument("--limit", type=int, required=True, help="Number of products to process in this batch.")
    args = parser.parse_args()

    log.info("Starting Price Calculator Service for batch", start_id=args.start_id, limit=args.limit)
    process_prices_batch(args.start_id, args.limit)
    log.info("Price Calculator Service finished.")
