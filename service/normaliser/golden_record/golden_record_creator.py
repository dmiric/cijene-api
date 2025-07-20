import argparse
import os
import json
import importlib
import sys # Added import for sys
from typing import Optional, List, Dict, Any
from psycopg2.extensions import connection as PgConnection, cursor as PgCursor

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Import database utility functions
from service.normaliser.db_utils import (
    get_db_connection,
    create_golden_record,
    mark_chain_products_as_processed
)

# Import EAN filter list
from service.normaliser.ean_filters import EAN_FILTER_LIST

# Import embedding service
from .embedding_service import get_embedding

# Load environment variables
load_dotenv()

def process_golden_records_batch(normalizer_type: str, embedder_type: str, start_id: int, limit: int) -> None:
    """
    Processes a batch of product data from chain_products based on product_id range,
    sends it to the AI for golden record creation, generates embeddings,
    and loads it into golden record tables.
    """
    conn: Optional[PgConnection] = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Dynamically import the correct normalizer's AI function
        try:
            # Construct the module name dynamically
            module_name = f".normaliser_{normalizer_type.replace('-', '_')}"
            # Import the module from the golden_record package
            normalizer_module = importlib.import_module(module_name, package="service.normaliser.golden_record")
        except ImportError:
            raise ValueError(f"Could not import normalizer module for type: {normalizer_type}")
        
        normalize_product_with_ai = normalizer_module.normalize_product_with_ai

        # 1. Extract: Query product_ids that need golden records within the given range
        # and then join with chain_products to get the necessary data.
        query = """
            WITH products_to_process AS (
                SELECT p.id AS product_id, p.ean
                FROM products p
                LEFT JOIN g_products gp ON p.ean = gp.ean
                WHERE gp.id IS NULL
                AND p.id >= %s AND p.id < %s + %s
            )
            SELECT
                ptp.ean,
                ARRAY_AGG(cp.name) AS name_variations,
                ARRAY_AGG(cp.id) AS chain_product_ids,
                ARRAY_AGG(cp.brand) AS brands,
                ARRAY_AGG(cp.category) AS categories,
                ARRAY_AGG(cp.unit) AS units
            FROM
                chain_products cp
            JOIN
                products_to_process ptp ON cp.product_id = ptp.product_id
            WHERE
                cp.is_processed = FALSE
        """
        params = [start_id, start_id, limit]
        
        query += " GROUP BY ptp.ean ORDER BY ptp.ean;"

        cur.execute(query, params)
        unprocessed_eans = cur.fetchall()

        if not unprocessed_eans:
            print(f"No unprocessed products found for product_id range {start_id} to {start_id + limit - 1}. Exiting.")
            return

        print(f"Processing {len(unprocessed_eans)} EANs for golden record creation in product_id range {start_id} to {start_id + limit - 1} using {normalizer_type} AI and {embedder_type} embedder...")

        for record in unprocessed_eans:
            ean = record['ean']
            name_variations = record['name_variations']
            chain_product_ids = record['chain_product_ids']
            brands = record['brands']
            categories = record['categories']
            units = record['units']

            with conn.cursor(cursor_factory=RealDictCursor) as product_cur:
                try:
                    # Check for Golden Record
                    product_cur.execute("SELECT id FROM g_products WHERE ean = %s", (ean,))
                    g_product_id = product_cur.fetchone()

                    if not g_product_id:
                        # Transform (AI Call)
                        normalized_data = normalize_product_with_ai(name_variations, brands, categories, units)
                        if not normalized_data:
                            print(f"Skipping EAN {ean}: AI normalization failed.")
                            continue

                        embedding = get_embedding(normalized_data['text_for_embedding'], embedder_type)
                        if not embedding:
                            print(f"Skipping EAN {ean}: Embedding generation failed.")
                            continue

                        # Load (Golden Record)
                        g_product_id = create_golden_record(product_cur, ean, normalized_data, embedding)
                        if not g_product_id:
                            # If create_golden_record returned None, it means the record already exists (due to ON CONFLICT DO NOTHING)
                            # or some other issue. Skip this EAN and continue.
                            print(f"Skipping EAN {ean}: Failed to create golden record or retrieve existing ID. See db_utils.py for details.", file=sys.stderr)
                            continue
                        else:
                            print(f"Created golden record for EAN {ean} with ID {g_product_id}")
                    else:
                        g_product_id = g_product_id['id']
                        print(f"Golden record already exists for EAN {ean} with ID {g_product_id}. Skipping AI normalization.")

                    # Mark as Processed (This now happens after golden record creation)
                    mark_chain_products_as_processed(product_cur, chain_product_ids)
                    conn.commit()
                    print(f"Successfully processed EAN {ean} and marked {len(chain_product_ids)} chain_products as processed.")
                except Exception as e:
                    conn.rollback()
                    print(f"Error processing EAN {ean}: {e}. Transaction rolled back.", file=sys.stderr) # Changed to sys.stderr
                    continue # Continue to the next EAN even if one fails

    except Exception as e:
        print(f"An error occurred during the main golden record creation loop: {e}", file=sys.stderr) # Changed to sys.stderr
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process a batch of products for golden record creation.")
    parser.add_argument("--normalizer-type", type=str, choices=["gemini", "grok"], required=True,
                        help="Type of normalizer to use (gemini or grok).")
    parser.add_argument("--embedder-type", type=str, choices=["gemini"], default="gemini",
                        help="Type of embedder to use (e.g., gemini).")
    parser.add_argument("--start-id", type=int, required=True, help="Starting product_id for the batch.")
    parser.add_argument("--limit", type=int, required=True, help="Number of product_ids to cover in this batch.")
    args = parser.parse_args()

    print(f"Starting Golden Record Creator Service for batch (normalizer={args.normalizer_type}, embedder={args.embedder_type}, start_id={args.start_id}, limit={args.limit})...")
    process_golden_records_batch(args.normalizer_type, args.embedder_type, args.start_id, args.limit)
    print("Golden Record Creator Service finished.")
