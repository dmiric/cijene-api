import os
import json
import logging
import structlog
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

# Initialize structlog logger
log = structlog.get_logger()

# Global client variable, initialized lazily
_gemini_client = None
_gemini_text_model = None

def _initialize_gemini_client():
    """Initializes the Google Gemini client and model lazily."""
    global _gemini_client, _gemini_text_model
    if _gemini_client is None:
        load_dotenv() # Load environment variables here, after logging is configured
        _gemini_client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
        # The model for text generation with specific config
        generation_config = {"response_mime_type": "application/json"}
        _gemini_text_model = _gemini_client.generative_models.GenerativeModel(
            os.getenv("GEMINI_TEXT_MODEL", "gemini-2.5-flash"),
            generation_config=generation_config
        )
        log.info("Google Gemini client initialized.")

def normalize_product_with_ai(
    name_variations: list[str],
    brands: list[Optional[str]],
    categories: list[Optional[str]],
    units: list[Optional[str]]
) -> Optional[Dict[str, Any]]:
    """Sends product name variations and other aggregated data to the AI and gets a structured JSON response."""
    _initialize_gemini_client() # Ensure client is initialized
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
        response = _gemini_text_model.generate_content(full_prompt) # Use the global model
        normalized_data = json.loads(response.text)
        log.debug("Received normalized data from Gemini", normalized_data=normalized_data)
        if response.usage_metadata:
            input_tokens = response.usage_metadata.prompt_token_count
            output_tokens = response.usage_metadata.candidates_token_count
            log.info("Gemini Text Model Usage", input_tokens=input_tokens, output_tokens=output_tokens)
        return normalized_data
    except Exception as e:
        log.error("Error calling Gemini API for normalization", error=str(e))
        if hasattr(e, 'response') and hasattr(e.response, 'text'):
            log.error("Gemini API error response text", response_text=e.response.text)
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
            log.info("No unprocessed products found for product_id range. Exiting.", start_id=start_id, limit=limit)
            return

        log.info("Processing EANs for product_id range",
                 num_eans=len(unprocessed_eans), start_id=start_id, limit=limit)

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
                            log.info("Skipping EAN: AI normalization failed.", ean=ean)
                            continue

                        embedding = get_embedding(normalized_data['text_for_embedding'])
                        if not embedding:
                            log.info("Skipping EAN: Embedding generation failed.", ean=ean)
                            continue

                        # Load (Golden Record)
                        g_product_id = create_golden_record(product_cur, ean, normalized_data, embedding)
                        if not g_product_id:
                            raise Exception(f"Failed to create golden record for EAN {ean}")
                        log.info("Created golden record", ean=ean, g_product_id=g_product_id)
                    else:
                        g_product_id = g_product_id['id']
                        log.info("Golden record already exists. Skipping normalization.", ean=ean, g_product_id=g_product_id)

                    # Mark as Processed (This now happens after the single best offer update)
                    mark_chain_products_as_processed(product_cur, chain_product_ids)
                    conn.commit()
                    log.info("Successfully processed EAN and marked chain_products as processed.", ean=ean, num_chain_products=len(chain_product_ids))
                except Exception as e:
                    conn.rollback()
                    log.error("Error processing EAN. Transaction rolled back.", ean=ean, error=str(e))
                    continue # Continue to the next EAN even if one fails

    except Exception as e:
        log.error("An error occurred during the main processing loop", error=str(e))
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process a batch of products for normalization.")
    parser.add_argument("--start-id", type=int, required=True, help="Starting ID for the batch of products.")
    parser.add_argument("--limit", type=int, required=True, help="Number of products to process in this batch.")
    args = parser.parse_args()

    log.info("Starting Gemini Normalizer Service for batch", start_id=args.start_id, limit=args.limit)
    process_products_by_id_range(args.start_id, args.limit)
    log.info("Gemini Normalizer Service finished.")
