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
    Processes a batch of products from g_products based on product_id range,
    calculates unit prices, and loads them into g_prices.
    """
    conn: Optional[PgConnection] = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 1. Extract: Query g_products for products within the ID range
        # and then fetch their associated chain_product prices.
        # We need to ensure we only process products that have a golden record
        # and whose prices haven't been processed into g_prices yet for the latest date.
        # This is a simplified check; a more robust solution might involve a flag
        # on g_products or checking the latest price date in g_prices.
        # For now, we'll assume we process all products in the range that have a golden record.
        cur.execute("""
            SELECT
                gp.id AS g_product_id,
                gp.base_unit_type,
                gp.variants,
                ARRAY_AGG(cp.id) AS chain_product_ids
            FROM
                g_products gp
            JOIN
                products p ON gp.ean = p.ean
            JOIN
                chain_products cp ON p.id = cp.product_id
            WHERE
                gp.id >= %s AND gp.id < %s + %s
            GROUP BY
                gp.id, gp.base_unit_type, gp.variants
            ORDER BY
                gp.id;
        """, (start_id, start_id, limit))
        products_to_process = cur.fetchall()

        if not products_to_process:
            log.info("No golden products found for product_id range for price calculation. Exiting.", start_id=start_id, limit=limit)
            return

        log.info("Processing prices for golden products in product_id range",
                 num_products=len(products_to_process), start_id=start_id, limit=limit)

        for record in products_to_process:
            g_product_id = record['g_product_id']
            base_unit_type = record['base_unit_type']
            variants = record['variants'] if record['variants'] else []
            chain_product_ids = record['chain_product_ids']

            with conn.cursor(cursor_factory=RealDictCursor) as product_cur:
                try:
                    # Fetch all raw price data for the EAN from legacy tables
                    product_cur.execute("""
                        SELECT
                            p.store_id,
                            p.price_date,
                            p.regular_price,
                            p.special_price
                        FROM
                            prices p
                        WHERE
                            p.chain_product_id = ANY(%s)
                    """, (chain_product_ids,))
                    raw_prices = product_cur.fetchall()

                    for price_entry in raw_prices:
                        # Use special_price if available, otherwise regular_price
                        current_price = price_entry['special_price'] if price_entry['special_price'] is not None else price_entry['regular_price']
                        
                        if current_price is None:
                            continue # Skip entries with no price at all

                        # Calculate unit prices for the current price entry using the new, robust function
                        calculated_unit_prices = calculate_unit_prices(
                            price=current_price,
                            base_unit_type=base_unit_type,
                            variants=variants
                        )
                        price_per_kg = calculated_unit_prices['price_per_kg']
                        price_per_l = calculated_unit_prices['price_per_l']
                        price_per_piece = calculated_unit_prices['price_per_piece']

                        # Insert the full price data, including the CORRECTLY calculated unit prices, into g_prices
                        product_cur.execute("""
                            INSERT INTO g_prices (
                                product_id, store_id, price_date, regular_price,
                                special_price, price_per_kg, price_per_l, price_per_piece,
                                is_on_special_offer
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (product_id, store_id, price_date) DO UPDATE SET
                                regular_price = EXCLUDED.regular_price,
                                special_price = EXCLUDED.special_price,
                                price_per_kg = EXCLUDED.price_per_kg,
                                price_per_l = EXCLUDED.price_per_l,
                                price_per_piece = EXCLUDED.price_per_piece,
                                is_on_special_offer = EXCLUDED.is_on_special_offer;
                        """, (
                            g_product_id,
                            price_entry['store_id'],
                            price_entry['price_date'],
                            price_entry['regular_price'],
                            price_entry['special_price'],
                            price_per_kg,
                            price_per_l,
                            price_per_piece,
                            price_entry['special_price'] is not None
                        ))
                    conn.commit()
                    log.info("Successfully processed prices for g_product_id.", g_product_id=g_product_id)
                except Exception as e:
                    conn.rollback()
                    log.error("Error processing prices for g_product_id. Transaction rolled back.", g_product_id=g_product_id, error=str(e))
                    continue # Continue to the next product even if one fails

    except Exception as e:
        log.error("An error occurred during the main price calculation loop", error=str(e))
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
