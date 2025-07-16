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

from openai import OpenAI

# Import database utility functions
from ..db_utils import (
    get_db_connection,
    create_golden_record,
    mark_chain_products_as_processed
)

# Import EAN filter list
from ..ean_filters import EAN_FILTER_LIST

# Import golden product prompt
from .golden_product_prompt import get_ai_normalization_prompt

# Load environment variables
load_dotenv()

# Configure Grok-3-mini API
client = OpenAI(
    api_key=os.getenv("XAI_API_KEY"),
    base_url="https://api.x.ai/v1",
)

# The model for text generation with specific config
# Grok-3-mini does not directly support response_mime_type="application/json" for chat completions
# We will parse the text response as JSON.
grok_text_model_name = os.getenv("GROK_TEXT_MODEL", "grok-3-mini")

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
        
        response = client.chat.completions.create(
            model=grok_text_model_name,
            messages=[
                {"role": "system", "content": get_ai_normalization_prompt()},
                {"role": "user", "content": json.dumps(input_data)}
            ],
            temperature=0.7, # Adjust as needed
            stream=False # Ensure non-streaming response
        )
        
        normalized_data = json.loads(response.choices[0].message.content)
        print(f"Received normalized data from Grok-3-mini: {normalized_data}")

        return normalized_data
    except Exception as e:
        print(f"Error calling Grok-3-mini API for normalization: {e}")
        if hasattr(e, 'response') and hasattr(e.response, 'text'):
            print(f"Grok-3-mini API error response text: {e.response.text}")
        return None

def process_eans_batch(eans_to_process: List[str]) -> None:
    """
    Processes a batch of product data from chain_products based on a list of EANs,
    normalizes it with AI, and loads it into golden record tables.
    """
    conn: Optional[PgConnection] = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 1. Extract: Query unprocessed data grouped by EAN from the provided list
        # Also ensure that we only process EANs that are in the EAN_FILTER_LIST if it's defined
        query = """
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
                AND p.ean = ANY(%s)
        """
        params = [eans_to_process]

        if EAN_FILTER_LIST:
            query += " AND p.ean = ANY(%s)"
            params.append(EAN_FILTER_LIST)
        
        query += " GROUP BY p.ean;"

        cur.execute(query, params)
        unprocessed_eans = cur.fetchall()

        if not unprocessed_eans:
            print(f"No unprocessed products found for the provided EANs. Exiting.")
            return

        print(f"Processing {len(unprocessed_eans)} EANs from the provided batch...")

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

                        # Load (Golden Record)
                        # Removed embedding from create_golden_record call
                        g_product_id = create_golden_record(product_cur, ean, normalized_data)
                        if not g_product_id:
                            raise Exception(f"Failed to create golden record for EAN {ean}")
                        print(f"Created golden record for EAN {ean} with ID {g_product_id}")
                    else:
                        g_product_id = g_product_id['id']
                        print(f"Golden record already exists for EAN {ean} with ID {g_product_id}. Skipping normalization.")

                    # Mark as Processed
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
    parser.add_argument("--eans-file", type=str, required=True, help="Path to a JSON file containing a list of EANs to process.")
    args = parser.parse_args()

    with open(args.eans_file, 'r') as f:
        eans_to_process = json.load(f)

    print(f"Starting Grok-3-mini Normalizer Service for {len(eans_to_process)} EANs from {args.eans_file}...")
    process_eans_batch(eans_to_process)
    print("Grok-3-mini Normalizer Service finished.")
