import os
import json
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List, Dict, Any
from psycopg2.extensions import connection as PgConnection, cursor as PgCursor
import argparse

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Import database utility functions
from ..db_utils import (
    get_db_connection,
    create_golden_record,
    mark_chain_products_as_processed
)

# Import EAN filter list
from ..ean_filters import EAN_FILTER_LIST

# Import embedding service
from .embedding_service import get_embedding

# Import golden product prompt
from .golden_product_prompt import get_ai_normalization_prompt

# Load environment variables
load_dotenv()

# Configure Google Gemini API
client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

# The model for text generation with specific config
generation_config = {"response_mime_type": "application/json"}
gemini_text_model = client.generative_models.GenerativeModel(
    os.getenv("GEMINI_TEXT_MODEL", "gemini-2.5-flash"),
    generation_config=generation_config
)

def normalize_product_with_ai(
    name_variations: list[str],
    brands: list[Optional[str]],
    categories: list[Optional[str]],
    units: list[Optional[str]]
) -> Optional[Dict[str, Any]]:
    """Sends product name variations and other aggregated data to the AI and gets a structured JSON response."""
    try:
        # Consolidate lists into a single input for the AI
        input_data = {
            "name_variations": name_variations,
            "brands": [b for b in brands if b is not None], # Filter out None values
            "categories": [c for c in categories if c is not None], # Filter out None values
            "units": [u for u in units if u is not None] # Filter out None values
        }

        full_prompt = [
            get_ai_normalization_prompt(),
            json.dumps(input_data)
        ]
        response = gemini_text_model.generate_content(full_prompt)
        normalized_data = json.loads(response.text)
        print(f"Received normalized data from Gemini: {normalized_data}") # Debug print
        if response.usage_metadata:
            input_tokens = response.usage_metadata.prompt_token_count
            output_tokens = response.usage_metadata.candidates_token_count
            print(f"Gemini Text Model Usage: Input Tokens={input_tokens}, Output Tokens={output_tokens}")
        return normalized_data
    except Exception as e:
        print(f"Error calling Gemini API for normalization: {e}")
        if hasattr(e, 'response') and hasattr(e.response, 'text'):
            print(f"Gemini API error response text: {e.response.text}")
        return None

def process_products_by_id_range(start_id: int, limit: int) -> None:
    """
    Processes a batch of product data from chain_products based on product_id range,
    normalizes it with AI, generates embeddings, and loads it into golden record tables.
    """
    conn: Optional[PgConnection] = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 1. Extract: Query unprocessed data grouped by EAN within the product_id range
        # This ensures that all chain_products for a given product.ean are processed together
        cur.execute("""
            SELECT
                p.ean,
                ARRAY_AGG(cp.name) AS name_variations,
                ARRAY_AGG(cp.id) AS chain_product_ids,
                ARRAY_AGG(cp.brand) AS brands,
                ARRAY_AGG(cp.category) AS categories,
                ARRAY_AGG(cp.unit) AS units
            FROM
                chain_products cp
            JOIN
                products p ON cp.product_id = p.id
            WHERE
                cp.is_processed = FALSE
                AND p.id >= %s AND p.id < %s + %s
            GROUP BY
                p.ean
            ORDER BY
                p.ean;
        """, (start_id, start_id, limit)) # Use start_id + limit for the upper bound
        unprocessed_eans = cur.fetchall()

        if not unprocessed_eans:
            print(f"No unprocessed products found for product_id range {start_id} to {start_id + limit - 1}. Exiting.")
            return

        print(f"Processing {len(unprocessed_eans)} EANs for product_id range {start_id} to {start_id + limit - 1}...")

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

                        embedding = get_embedding(normalized_data['text_for_embedding'])
                        if not embedding:
                            print(f"Skipping EAN {ean}: Embedding generation failed.")
                            continue

                        # Load (Golden Record)
                        g_product_id = create_golden_record(product_cur, ean, normalized_data, embedding)
                        if not g_product_id:
                            raise Exception(f"Failed to create golden record for EAN {ean}")
                        print(f"Created golden record for EAN {ean} with ID {g_product_id}")
                    else:
                        g_product_id = g_product_id['id']
                        print(f"Golden record already exists for EAN {ean} with ID {g_product_id}. Skipping normalization.")

                    # Mark as Processed (This now happens after the single best offer update)
                    mark_chain_products_as_processed(product_cur, chain_product_ids)
                    conn.commit()
                    print(f"Successfully processed EAN {ean} and marked {len(chain_product_ids)} chain_products as processed.")
                except Exception as e:
                    conn.rollback()
                    print(f"Error processing EAN {ean}: {e}. Transaction rolled back.")
                    continue # Continue to the next EAN even if one fails

    except Exception as e:
        print(f"An error occurred during the main processing loop: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process a batch of products for normalization.")
    parser.add_argument("--start-id", type=int, required=True, help="Starting ID for the batch of products.")
    parser.add_argument("--limit", type=int, required=True, help="Number of products to process in this batch.")
    args = parser.parse_args()

    print(f"Starting Gemini Normalizer Service for batch (start_id={args.start_id}, limit={args.limit})...")
    process_products_by_id_range(args.start_id, args.limit)
    print("Gemini Normalizer Service finished.")
