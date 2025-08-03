import argparse
import os
from typing import Optional, List, Dict, Any
from psycopg2.extensions import connection as PgConnection, cursor as PgCursor

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Import database utility functions
from .db_utils import (
    get_db_connection,
    calculate_unit_prices,
)

# Load environment variables
load_dotenv()

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
            print(f"No golden products found for product_id range {start_id} to {start_id + limit - 1} for price calculation. Exiting.")
            return

        print(f"Processing prices for {len(products_to_process)} golden products in product_id range {start_id} to {start_id + limit - 1}...")

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
                    print(f"Successfully processed prices for g_product_id {g_product_id}.")
                except Exception as e:
                    conn.rollback()
                    print(f"Error processing prices for g_product_id {g_product_id}: {e}. Transaction rolled back.")
                    continue # Continue to the next product even if one fails

    except Exception as e:
        print(f"An error occurred during the main price calculation loop: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process a batch of products for price calculation.")
    parser.add_argument("--start-id", type=int, required=True, help="Starting product_id for the batch.")
    parser.add_argument("--limit", type=int, required=True, help="Number of product_ids to cover in this batch.")
    args = parser.parse_args()

    print(f"Starting Price Calculator Service for batch (start_id={args.start_id}, limit={args.limit})...")
    process_prices_batch(args.start_id, args.limit)
    print("Price Calculator Service finished.")
