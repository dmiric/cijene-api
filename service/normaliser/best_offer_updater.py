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
    update_best_offer,
)

# Load environment variables
load_dotenv()

def process_best_offers_batch(start_id: int, limit: int) -> None:
    """
    Processes a batch of products from g_products based on product_id range,
    determines the best offer, and updates g_product_best_offers.
    """
    conn: Optional[PgConnection] = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 1. Extract: Query g_products for products within the ID range
        # and their associated base_unit_type, variants, and seasonal info
        cur.execute("""
            SELECT
                gp.id AS g_product_id,
                gp.base_unit_type,
                gp.variants,
                gp.seasonal_start_month,
                gp.seasonal_end_month
            FROM
                g_products gp
            WHERE
                gp.id >= %s AND gp.id < %s + %s
            ORDER BY
                gp.id;
        """, (start_id, start_id, limit))
        products_to_process = cur.fetchall()

        if not products_to_process:
            print(f"No golden products found for product_id range {start_id} to {start_id + limit - 1} for best offer update. Exiting.")
            return

        print(f"Processing best offers for {len(products_to_process)} golden products in product_id range {start_id} to {start_id + limit - 1}...")

        for record in products_to_process:
            g_product_id = record['g_product_id']
            base_unit_type = record['base_unit_type']
            variants = record['variants'] if record['variants'] else []
            seasonal_start_month = record['seasonal_start_month']
            seasonal_end_month = record['seasonal_end_month']

            with conn.cursor(cursor_factory=RealDictCursor) as product_cur:
                try:
                    # Fetch all prices for this g_product from g_prices
                    product_cur.execute("""
                        SELECT
                            store_id,
                            price_date,
                            regular_price,
                            special_price,
                            price_per_kg,
                            price_per_l,
                            price_per_piece
                        FROM
                            g_prices
                        WHERE
                            product_id = %s
                        ORDER BY
                            price_date DESC;
                    """, (g_product_id,))
                    g_prices_entries = product_cur.fetchall()

                    if not g_prices_entries:
                        print(f"No prices found for g_product_id {g_product_id}. Skipping best offer update.")
                        continue

                    best_offer_entry = None
                    best_unit_price_overall = None 

                    for price_entry in g_prices_entries:
                        current_unit_price_for_comparison = None
                        if base_unit_type == 'WEIGHT':
                            current_unit_price_for_comparison = price_entry['price_per_kg']
                        elif base_unit_type == 'VOLUME':
                            current_unit_price_for_comparison = price_entry['price_per_l']
                        elif base_unit_type == 'COUNT':
                            current_unit_price_for_comparison = price_entry['price_per_piece']
                        
                        if current_unit_price_for_comparison is not None:
                            if best_unit_price_overall is None or current_unit_price_for_comparison < best_unit_price_overall:
                                best_unit_price_overall = current_unit_price_for_comparison
                                best_offer_entry = price_entry # Save the price_entry that had the best price

                    if best_offer_entry and best_unit_price_overall is not None:
                        update_best_offer(
                            product_cur,
                            g_product_id,
                            base_unit_type,
                            best_offer_entry,
                            best_unit_price_overall,
                            seasonal_start_month,
                            seasonal_end_month
                        )
                        conn.commit()
                        print(f"Successfully updated best offer for g_product_id {g_product_id}.")
                    else:
                        print(f"Could not determine best offer for g_product_id {g_product_id}.")

                except Exception as e:
                    conn.rollback()
                    print(f"Error processing best offer for g_product_id {g_product_id}: {e}. Transaction rolled back.")
                    continue # Continue to the next product even if one fails

    except Exception as e:
        print(f"An error occurred during the main best offer update loop: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process a batch of products for best offer update.")
    parser.add_argument("--start-id", type=int, required=True, help="Starting product_id for the batch.")
    parser.add_argument("--limit", type=int, required=True, help="Number of product_ids to cover in this batch.")
    args = parser.parse_args()

    print(f"Starting Best Offer Updater Service for batch (start_id={args.start_id}, limit={args.limit})...")
    process_best_offers_batch(args.start_id, args.limit)
    print("Best Offer Updater Service finished.")
