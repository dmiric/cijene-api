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
from .db_utils import (
    get_db_connection,
    calculate_unit_prices,
    create_golden_record,
    update_best_offer,
    mark_chain_products_as_processed
)

# Import EAN filter list
from .ean_filters import EAN_FILTER_LIST

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

def get_ai_normalization_prompt() -> str:
    """
    Returns the master system prompt for the AI. This prompt instructs the AI
    to act as a data normalization engine, handling complex cases like assortments
    and generating all necessary fields for the golden record.
    """
    return """
You are an expert data enrichment and normalization AI for a Croatian e-commerce platform. Your primary task is to analyze a list of different name variations for a single product (identified by a common EAN) and create a single, canonical "golden record" in a structured JSON format. This record will be used to power both semantic vector search and keyword-based hybrid search.

You will be given an array of raw product names, along with aggregated brands, categories, and units from the source data. Use all provided information to create the golden record.

**Instructions:**

1.  **Analyze all provided name variations** to understand the product's core identity, ignoring retailer-specific formatting like ALL CAPS, extra punctuation, or different word orders.
2.  **Identify and extract the `brand`**. If no brand is explicitly mentioned, or if the provided brands are inconsistent, return `null`. Prioritize brands from the `brands` input array if consistent.
3.  **Create a single, user-friendly `canonical_name`** that is clean and suitable for display to customers. For assortments, use a general name like "Product Asortiman".
4.  **Assign a standardized `category`** from a relevant e-commerce taxonomy. Use your knowledge to pick the most appropriate one (e.g., "Mesni naresci i paštete", "Kućanske potrepštine", "Slatkiši i grickalice"). Prioritize categories from the `categories` input array if consistent.
5.  **Create a `variants` array.** This is a critical step.
    *   If the product is a single item (e.g., "150g" or "1.5l"), the array should contain one object.
    *   If it is a multi-pack (e.g., "4x100g"), the array should contain one object representing the total (e.g., `{"unit": "g", "value": 400, "piece_count": 4}`).
    *   If it is an **assortment of different sizes** (e.g., "270g, 276g, 300g"), create multiple objects in the array, one for each variant.
    *   Each object in the array must contain `unit` ('g', 'ml', 'kom') and `value` (an integer). Use the `units` input array to help determine the unit if not clear from name variations.
6.  **Based on the variants, determine the product's `base_unit_type`**. This must be one of 'WEIGHT', 'VOLUME', or 'COUNT'.
7.  **Construct a clean, descriptive sentence for `text_for_embedding`**. This sentence should be optimized for semantic search and combine the core product type, brand, category, and key attributes in natural Croatian language. It should describe the product generally, not a specific variant.
8.  **Generate a list of exactly 8 relevant `keywords`** in Croatian for keyword search. Follow these keyword guidelines:
    *   Include common synonyms.
    *   Include potential use cases.
    *   Include key attributes.
    *   All keywords must be lowercase.
    *   Do not include generic marketing words like "akcija" or "jeftino".

9.  **Determine `is_generic_product` (boolean)**:
    *   Set to `true` if the product is a common, unbranded item (e.g., fresh fruits, vegetables, bulk nuts, etc.) where the primary identifier is its type rather than a specific brand.
    *   Set to `false` for all branded products or products with distinct packaging/variants that are not typically considered "generic" produce.
    *   **CRITICAL RULE**: If the product's `variants` array contains any object where `unit` is 'g' or 'ml' and `value` is NOT 1000, OR if `unit` is 'kg' or 'l' and `value` is NOT 1, then `is_generic_product` MUST be `false`. This specifically targets prepackaged items that are not sold in standard 1kg/1L bulk units.

10. **Determine `seasonal_start_month` and `seasonal_end_month` (integer | null)**:
    *   If the product is seasonal (e.g., fresh fruits, vegetables), identify its typical start and end months (1-12) *based on typical seasonality and availability in Croatia*.
    *   If not seasonal, return `null` for both.

**Provide the final output as a single, clean JSON object with the following structure. Do not add any text or explanation outside of the JSON object.**

```json
{
  "canonical_name": "string",
  "brand": "string | null",
  "category": "string",
  "base_unit_type": "string",
  "variants": [
    {
      "unit": "string",
      "value": "integer",
      "piece_count": "integer | null"
    }
  ],
  "text_for_embedding": "string",
  "keywords": ["string"],
  "is_generic_product": "boolean",
  "seasonal_start_month": "integer | null",
  "seasonal_end_month": "integer | null"
}
"""

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

                    # Fetch g_product's base_unit_type, variants, and seasonal info
                    product_cur.execute("SELECT base_unit_type, variants, seasonal_start_month, seasonal_end_month FROM g_products WHERE id = %s", (g_product_id,))
                    g_product_info = product_cur.fetchone()
                    base_unit_type = g_product_info['base_unit_type']
                    variants = g_product_info['variants'] if g_product_info['variants'] else []
                    seasonal_start_month = g_product_info['seasonal_start_month']
                    seasonal_end_month = g_product_info['seasonal_end_month']

                    # Load (Prices & Best Offers)
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

                    best_offer_entry = None
                    best_unit_price_overall = None 

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

                        # Now, check if this entry is the best one we've seen so far for this product
                        current_unit_price_for_comparison = None
                        if base_unit_type == 'WEIGHT':
                            current_unit_price_for_comparison = price_per_kg
                        elif base_unit_type == 'VOLUME':
                            current_unit_price_for_comparison = price_per_l
                        elif base_unit_type == 'COUNT':
                            current_unit_price_for_comparison = price_per_piece
                        
                        if current_unit_price_for_comparison is not None:
                            if best_unit_price_overall is None or current_unit_price_for_comparison < best_unit_price_overall:
                                best_unit_price_overall = current_unit_price_for_comparison
                                best_offer_entry = price_entry # Save the price_entry that had the best price

                    # --- END OF CORRECTED LOGIC ---

                    # After the loop has finished, if we found a best offer, update the database ONCE.
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
    parser.add_argument("--eans-file", type=str, required=True, help="Path to a JSON file containing a list of EANs to process.")
    args = parser.parse_args()

    with open(args.eans_file, 'r') as f:
        eans_to_process = json.load(f)

    print(f"Starting Grok-3-mini Normalizer Service for {len(eans_to_process)} EANs from {args.eans_file}...")
    process_eans_batch(eans_to_process)
    print("Grok-3-mini Normalizer Service finished.")
