import argparse
import os
import json
import importlib
import sys
import time
from typing import Optional, List, Dict, Any
from psycopg2.extensions import connection as PgConnection, cursor as PgCursor

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from prometheus_client import CollectorRegistry, Gauge, Counter, Summary, push_to_gateway # Modified Prometheus imports

# Import database utility functions
from service.normaliser.db_utils import (
    get_db_connection,
    create_golden_record,
    mark_chain_products_as_processed,
    get_category_id_by_name,
    create_category_if_not_exists
)

# Import EAN filter list
from service.normaliser.ean_filters import EAN_FILTER_LIST

# Import embedding service
from .embedding_service import get_embedding

# Load environment variables
load_dotenv()

# Prometheus Metrics
# Create a registry for this specific job
registry = CollectorRegistry()

GOLDEN_RECORDS_PROCESSED = Counter(
    'golden_records_processed_total',
    'Total number of golden records processed',
    ['normalizer_type', 'embedder_type', 'status'],
    registry=registry
)
GOLDEN_RECORD_CREATION_ERRORS = Counter(
    'golden_record_creation_errors_total',
    'Total number of errors during golden record creation',
    ['normalizer_type', 'embedder_type', 'error_type'],
    registry=registry
)
GOLDEN_RECORD_PROCESSING_TIME = Summary(
    'golden_record_processing_time_seconds',
    'Time spent processing golden records',
    ['normalizer_type', 'embedder_type'],
    registry=registry
)
GOLDEN_RECORD_BATCH_SIZE = Gauge(
    'golden_record_batch_size',
    'Size of the current golden record processing batch',
    ['normalizer_type', 'embedder_type'],
    registry=registry
)

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
        normalizer_map = {
            "gemini": ".normaliser_gemini",
            "grok": ".normaliser_grok_3_mini"
        }
        module_name = normalizer_map.get(normalizer_type)
        if not module_name:
            raise ValueError(f"Unknown normalizer type: {normalizer_type}")

        try:
            normalizer_module = importlib.import_module(module_name, package="service.normaliser.golden_record")
        except ImportError:
            raise ValueError(f"Could not import normalizer module for type: {normalizer_type}")
        
        normalize_product_with_ai = normalizer_module.normalize_product_with_ai

        # 1. Extract: Query EANs that need golden records, ordered by product_id,
        # and then join with chain_products to get the necessary data.
        query = """
            WITH products_to_process AS (
                SELECT p.id AS product_id, p.ean
                FROM products p
                LEFT JOIN g_products gp ON p.ean = gp.ean
                WHERE gp.id IS NULL
                AND p.id >= %s -- Start looking from this product_id
                GROUP BY p.id, p.ean -- Group by both id and ean
                ORDER BY p.id -- Order by product_id to ensure consistent batches
                LIMIT %s -- Limit the number of EANs to process in this batch
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
            GROUP BY ptp.ean
            ORDER BY ptp.ean;
        """
        params = [start_id, limit]

        cur.execute(query, params)
        unprocessed_eans = cur.fetchall()

        batch_size_actual = len(unprocessed_eans)
        GOLDEN_RECORD_BATCH_SIZE.labels(normalizer_type=normalizer_type, embedder_type=embedder_type).set(batch_size_actual)

        if not unprocessed_eans:
            print(f"No unprocessed products found for product_id range {start_id} to {start_id + limit - 1}. Exiting.")
            return

        print(f"Processing {batch_size_actual} EANs for golden record creation in product_id range {start_id} to {start_id + limit - 1} using {normalizer_type} AI and {embedder_type} embedder...")

        for record in unprocessed_eans:
            ean = record['ean']
            name_variations = record['name_variations']
            chain_product_ids = record['chain_product_ids']
            brands = record['brands']
            categories = record['categories']
            units = record['units']

            start_time = time.time() # Start timing for each EAN
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
                            GOLDEN_RECORD_CREATION_ERRORS.labels(normalizer_type=normalizer_type, embedder_type=embedder_type, error_type='ai_normalization_failed').inc()
                            continue

                        # Get or create category ID
                        category_name = normalized_data.get('category')
                        if category_name:
                            category_id = get_category_id_by_name(product_cur, category_name)
                            if not category_id:
                                category_id = create_category_if_not_exists(product_cur, category_name)
                                conn.commit() # Commit category creation immediately
                                print(f"Created new category: {category_name} with ID {category_id}")
                        else:
                            category_id = None # Handle cases where category might be missing

                        embedding = get_embedding(normalized_data['text_for_embedding'], embedder_type)
                        if not embedding:
                            print(f"Skipping EAN {ean}: Embedding generation failed.")
                            GOLDEN_RECORD_CREATION_ERRORS.labels(normalizer_type=normalizer_type, embedder_type=embedder_type, error_type='embedding_failed').inc()
                            continue

                        # Load (Golden Record)
                        g_product_id = create_golden_record(product_cur, ean, normalized_data, embedding, category_id)
                        if not g_product_id:
                            # If create_golden_record returned None, it means the record already exists (due to ON CONFLICT DO NOTHING)
                            # or some other issue. Skip this EAN and continue.
                            print(f"Skipping EAN {ean}: Failed to create golden record or retrieve existing ID. See db_utils.py for details.", file=sys.stderr)
                            GOLDEN_RECORD_CREATION_ERRORS.labels(normalizer_type=normalizer_type, embedder_type=embedder_type, error_type='create_golden_record_failed').inc()
                            continue
                        else:
                            print(f"Created golden record for EAN {ean} with ID {g_product_id}")
                            GOLDEN_RECORDS_PROCESSED.labels(normalizer_type=normalizer_type, embedder_type=embedder_type, status='created').inc()
                    else:
                        g_product_id = g_product_id['id']
                        print(f"Golden record already exists for EAN {ean} with ID {g_product_id}. Skipping AI normalization.")
                        GOLDEN_RECORDS_PROCESSED.labels(normalizer_type=normalizer_type, embedder_type=embedder_type, status='skipped_exists').inc()

                    # Mark as Processed (This now happens after golden record creation)
                    mark_chain_products_as_processed(product_cur, chain_product_ids)
                    conn.commit()
                    print(f"Successfully processed EAN {ean} and marked {len(chain_product_ids)} chain_products as processed.")
                    GOLDEN_RECORDS_PROCESSED.labels(normalizer_type=normalizer_type, embedder_type=embedder_type, status='marked_processed').inc()

                    # Push metrics to Pushgateway after each product
                    try:
                        job_name = f"golden_record_creator_{os.getpid()}" # Unique job name for each worker
                        push_to_gateway(os.getenv("PROMETHEUS_PUSHGATEWAY_URL", "http://pushgateway:9091"), job=job_name, registry=registry)
                        print(f"Metrics pushed to Pushgateway for EAN {ean}.")
                    except Exception as e:
                        print(f"Error pushing metrics to Pushgateway for EAN {ean}: {e}", file=sys.stderr)

                except Exception as e:
                    conn.rollback()
                    print(f"Error processing EAN {ean}: {e}. Transaction rolled back.", file=sys.stderr)
                    GOLDEN_RECORD_CREATION_ERRORS.labels(normalizer_type=normalizer_type, embedder_type=embedder_type, error_type=type(e).__name__).inc()
                    continue
                finally:
                    end_time = time.time()
                    GOLDEN_RECORD_PROCESSING_TIME.labels(normalizer_type=normalizer_type, embedder_type=embedder_type).observe(end_time - start_time)

    except Exception as e:
        print(f"An error occurred during the main golden record creation loop: {e}", file=sys.stderr)
        GOLDEN_RECORD_CREATION_ERRORS.labels(normalizer_type=normalizer_type, embedder_type=embedder_type, error_type=type(e).__name__).inc()
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
    parser.add_argument("--pushgateway-url", type=str, default=os.getenv("PROMETHEUS_PUSHGATEWAY_URL", "http://pushgateway:9091"), # Added pushgateway URL argument
                        help="URL of the Prometheus Pushgateway.")
    args = parser.parse_args()

    print(f"Starting Golden Record Creator Service for batch (normalizer={args.normalizer_type}, embedder={args.embedder_type}, start_id={args.start_id}, limit={args.limit})...")
    
    # Process the batch
    process_golden_records_batch(args.normalizer_type, args.embedder_type, args.start_id, args.limit)
    
    print("Golden Record Creator Service finished.")
