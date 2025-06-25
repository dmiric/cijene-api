import os
import json
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List, Dict, Any
from psycopg2.extensions import connection as PgConnection, cursor as PgCursor

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import google.generativeai as genai

# Load environment variables
load_dotenv()

# Configure Google Gemini API
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

# The model for text generation with specific config
generation_config = {"response_mime_type": "application/json"}
gemini_text_model = genai.GenerativeModel(
    os.getenv("GEMINI_TEXT_MODEL", "gemini-2.5-flash"),
    generation_config=generation_config
)

def get_db_connection() -> PgConnection:
    """Establishes and returns a database connection."""
    conn = psycopg2.connect(os.getenv("DB_DSN"))
    return conn

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
  "keywords": ["string"]
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

def get_embedding(text: str) -> Optional[List[float]]:
    """Generates a vector embedding for the given text."""
    try:
        print(f"Attempting to generate embedding for text: '{text}'") # New debug print
        response = genai.embed_content(
            model=os.getenv("GEMINI_EMBEDDING_MODEL", "models/embedding-001"),
            content=text,
            task_type="RETRIEVAL_DOCUMENT",
            output_dimensionality=768 # Keeping this as per our database schema
        )
        # Safely access usage_metadata if it exists
        if 'usage_metadata' in response and response['usage_metadata']:
            total_tokens = response['usage_metadata']['total_token_count']
            print(f"Gemini Embedding Model Usage: Total Tokens={total_tokens}")
        print(f"Successfully generated embedding for text: '{text[:50]}...'") # New debug print
        return response['embedding'] # Access embedding directly from the dictionary
    except Exception as e:
        print(f"Error generating embedding for text '{text}': {e}") # Enhanced error print
        # Optionally, print more details if available from the API response
        if hasattr(e, 'response') and hasattr(e.response, 'text'):
            print(f"Embedding API error response text: {e.response.text}")
        return None

def create_golden_record(
    cur: PgCursor,
    ean: str,
    normalized_data: Dict[str, Any],
    embedding: List[float]
) -> Optional[int]:
    """Inserts a new golden record into g_products and returns its ID."""
    try:
        cur.execute("""
            INSERT INTO g_products (
                ean, canonical_name, brand, category, base_unit_type,
                variants, text_for_embedding, keywords, embedding
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            ean,
            normalized_data['canonical_name'],
            normalized_data['brand'],
            normalized_data['category'],
            normalized_data['base_unit_type'],
            json.dumps(normalized_data['variants']), # Store JSONB
            normalized_data['text_for_embedding'],
            normalized_data['keywords'],
            embedding
        ))
        return cur.fetchone()['id']
    except Exception as e:
        print(f"Error creating golden record for EAN {ean}: {e}")
        return None

def update_best_offer(
    cur: PgCursor,
    g_product_id: int,
    base_unit_type: str,
    price_entry: Dict[str, Any],
    current_unit_price: Decimal
) -> None:
    """Updates g_product_best_offers if the new unit price is better."""
    update_column = None
    if base_unit_type == 'WEIGHT':
        update_column = 'best_unit_price_per_kg'
    elif base_unit_type == 'VOLUME':
        update_column = 'best_unit_price_per_l'
    elif base_unit_type == 'COUNT':
        update_column = 'best_unit_price_per_piece'

    if update_column:
        try:
            cur.execute(f"""
                INSERT INTO g_product_best_offers (
                    product_id, {update_column}, best_price_store_id, best_price_found_at
                ) VALUES (%s, %s, %s, NOW())
                ON CONFLICT (product_id) DO UPDATE SET
                    {update_column} = EXCLUDED.{update_column},
                    best_price_store_id = EXCLUDED.best_price_store_id,
                    best_price_found_at = NOW()
                WHERE
                    EXCLUDED.{update_column} < COALESCE(g_product_best_offers.{update_column}, 'Infinity');
            """, (
                g_product_id,
                current_unit_price,
                price_entry['store_id']
            ))
            print(f"Updated best offer for product {g_product_id} ({update_column}: {current_unit_price})")
        except Exception as e:
            print(f"Error updating best offer for product {g_product_id}: {e}")

def mark_chain_products_as_processed(cur: PgCursor, chain_product_ids: List[int]) -> None:
    """Marks a list of chain_products as processed."""
    try:
        cur.execute("""
            UPDATE chain_products
            SET is_processed = TRUE
            WHERE id = ANY(%s)
        """, (chain_product_ids,))
    except Exception as e:
        print(f"Error marking chain_products as processed: {e}")

def process_unprocessed_data() -> None:
    """
    Processes unprocessed product data from chain_products, normalizes it with AI,
    generates embeddings, and loads it into golden record tables.
    """
    conn: Optional[PgConnection] = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 1. Extract: Query unprocessed data grouped by EAN
        # Define the list of EANs to filter by
        ean_filter_list = [
            "0011210000018", "0011210000155", "0011210006508", "0011210007253",
            "0011210009530", "0011210607040", "0011210697003", "0013051665654",
            "0022796976116", "0022796976123", "0022796976710", "0022796977519",
            "0022796977526", "0039047003880", "0054881000017", "0054881000024",
            "0054881000031", "0054881000048", "0054881000055", "0054881000062",
            "0054881000208", "0054881005517", "0054881005555", "0054881005593",
            "0054881005630", "0054881005654", "0054881005845", "0054881005890",
            "0054881005906", "0054881006163", "0054881007115", "0054881007511",
            "0054881007535", "0054881008020", "0054881008594"
        ]
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
                AND p.ean = ANY(%s) -- Add the EAN filter here
            GROUP BY
                p.ean
            LIMIT %s;
        """, (ean_filter_list, int(os.getenv("NORMALIZER_BATCH_LIMIT")),))
        unprocessed_eans = cur.fetchall()

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

                    # Load (Prices & Best Offers)
                    # Fetch all raw price data for the EAN from legacy tables
                    product_cur.execute("""
                        SELECT
                            p.store_id,
                            p.price_date,
                            p.regular_price,
                            p.special_price,
                            p.unit_price
                        FROM
                            prices p
                        WHERE
                            p.chain_product_id = ANY(%s)
                    """, (chain_product_ids,))
                    raw_prices = product_cur.fetchall()

                    # --- NEW LOGIC: FIND THE BEST OFFER IN PYTHON FIRST ---
                    best_offer_entry = None
                    best_unit_price = None

                    for price_entry in raw_prices:
                        # We still need to process and insert every price into g_prices
                        product_cur.execute("""
                            INSERT INTO g_prices (
                                product_id, store_id, price_date, regular_price,
                                special_price, is_on_special_offer
                            ) VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (product_id, store_id, price_date) DO UPDATE SET
                                regular_price = EXCLUDED.regular_price,
                                special_price = EXCLUDED.special_price,
                                is_on_special_offer = EXCLUDED.is_on_special_offer
                            RETURNING id
                        """, (
                            g_product_id,
                            price_entry['store_id'],
                            price_entry['price_date'],
                            price_entry['regular_price'],
                            price_entry['special_price'],
                            price_entry['special_price'] is not None and price_entry['special_price'] < price_entry['regular_price']
                        ))
                        g_price_id = product_cur.fetchone()['id']

                        # Now, check if this price is the best one we've seen so far in this batch
                        current_unit_price = price_entry.get('unit_price')
                        if current_unit_price is not None:
                            if best_unit_price is None or current_unit_price < best_unit_price:
                                best_unit_price = current_unit_price
                                best_offer_entry = price_entry

                    # --- END OF NEW LOGIC ---

                    # After the loop, if we found a best offer, update the database ONCE.
                    if best_offer_entry and best_unit_price is not None:
                        # Fetch g_product's base_unit_type to update the correct best offer column
                        product_cur.execute("SELECT base_unit_type FROM g_products WHERE id = %s", (g_product_id,))
                        base_unit_type = product_cur.fetchone()['base_unit_type']

                        # Call the update function just one time with the best price found
                        update_best_offer(product_cur, g_product_id, base_unit_type, best_offer_entry, best_unit_price)

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
    print("Starting AI Normalizer Service...")
    process_unprocessed_data()
    print("AI Normalizer Service finished.")
