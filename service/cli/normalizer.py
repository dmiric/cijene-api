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

9.  **Determine `is_generic_product` (boolean)**:
    *   Set to `true` if the product is a common, unbranded item (e.g., fresh fruits, vegetables, bulk nuts, etc.) where the primary identifier is its type rather than a specific brand.
    *   Set to `false` for all branded products or products with distinct packaging/variants that are not typically considered "generic" produce.

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

def calculate_unit_prices(
    price: Decimal,
    base_unit_type: str,
    variants: List[Dict[str, Any]]
) -> Dict[str, Optional[Decimal]]:
    """
    Calculates price_per_kg, price_per_l, and price_per_piece based on the product's
    base unit type and variants.
    """
    price_per_kg = None
    price_per_l = None
    price_per_piece = None

    if not variants:
        return {
            "price_per_kg": price_per_kg,
            "price_per_l": price_per_l,
            "price_per_piece": price_per_piece,
        }

    # Assuming a single variant for simplicity in initial implementation
    # For assortments, more complex logic would be needed to pick a representative variant
    main_variant = variants[0]
    unit = main_variant.get("unit")
    value = main_variant.get("value")
    piece_count = main_variant.get("piece_count")

    if unit and value and price is not None:
        if base_unit_type == 'WEIGHT' and unit.lower() == 'g':
            # Convert price per gram to price per kg
            price_per_kg = (price / Decimal(str(value))) * Decimal('1000')
        elif base_unit_type == 'VOLUME' and unit.lower() == 'ml':
            # Convert price per ml to price per liter
            price_per_l = (price / Decimal(str(value))) * Decimal('1000')
        elif base_unit_type == 'VOLUME' and unit.lower() == 'l':
            price_per_l = price / Decimal(str(value))
        elif base_unit_type == 'COUNT' and piece_count is not None:
            price_per_piece = price / Decimal(str(piece_count))
        elif base_unit_type == 'COUNT' and unit.lower() == 'kom':
            price_per_piece = price / Decimal(str(value)) # Assuming 'value' is piece count for 'kom'

    return {
        "price_per_kg": price_per_kg,
        "price_per_l": price_per_l,
        "price_per_piece": price_per_piece,
    }


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
                variants, text_for_embedding, keywords, is_generic_product,
                seasonal_start_month, seasonal_end_month, embedding
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            normalized_data['is_generic_product'],
            normalized_data.get('seasonal_start_month'), # Use .get() for optional fields
            normalized_data.get('seasonal_end_month'),   # Use .get() for optional fields
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
    current_unit_price: Decimal,
    seasonal_start_month: Optional[int],
    seasonal_end_month: Optional[int]
) -> None:
    """
    Updates g_product_best_offers if the new unit price is better,
    and updates lowest_price_in_season if applicable.
    """
    update_column = None
    if base_unit_type == 'WEIGHT':
        update_column = 'best_unit_price_per_kg'
    elif base_unit_type == 'VOLUME':
        update_column = 'best_unit_price_per_l'
    elif base_unit_type == 'COUNT':
        update_column = 'best_unit_price_per_piece'

    current_month = datetime.now().month
    is_in_season = False
    if seasonal_start_month and seasonal_end_month:
        if seasonal_start_month <= seasonal_end_month:
            is_in_season = seasonal_start_month <= current_month <= seasonal_end_month
        else: # Season spans across year end (e.g., Nov-Feb)
            is_in_season = current_month >= seasonal_start_month or current_month <= seasonal_end_month

    lowest_price_in_season_update = ""
    lowest_price_in_season_param = None

    if is_in_season and current_unit_price is not None:
        # Fetch current lowest_price_in_season from DB to compare
        cur.execute("""
            SELECT lowest_price_in_season FROM g_product_best_offers WHERE product_id = %s
        """, (g_product_id,))
        existing_lowest_in_season = cur.fetchone()
        
        if existing_lowest_in_season and existing_lowest_in_season['lowest_price_in_season'] is not None:
            if current_unit_price < existing_lowest_in_season['lowest_price_in_season']:
                lowest_price_in_season_update = ", lowest_price_in_season = EXCLUDED.lowest_price_in_season"
                lowest_price_in_season_param = current_unit_price
        else:
            # If no existing lowest_price_in_season or it's NULL, set it to current price
            lowest_price_in_season_update = ", lowest_price_in_season = EXCLUDED.lowest_price_in_season"
            lowest_price_in_season_param = current_unit_price

    try:
        # Construct the INSERT/UPDATE query dynamically
        query_columns = ["product_id", update_column, "best_price_store_id", "best_price_found_at"]
        query_values = ["%s", "%s", "%s", "NOW()"]
        query_excluded_sets = [
            f"{update_column} = EXCLUDED.{update_column}",
            "best_price_store_id = EXCLUDED.best_price_store_id",
            "best_price_found_at = NOW()"
        ]
        query_where_clause = f"EXCLUDED.{update_column} < COALESCE(g_product_best_offers.{update_column}, 'Infinity')"
        
        params = [g_product_id, current_unit_price, price_entry['store_id']]

        if lowest_price_in_season_param is not None:
            query_columns.append("lowest_price_in_season")
            query_values.append("%s")
            query_excluded_sets.append("lowest_price_in_season = EXCLUDED.lowest_price_in_season")
            params.append(lowest_price_in_season_param)
            # Add condition for lowest_price_in_season update
            query_where_clause += f" OR (EXCLUDED.lowest_price_in_season < COALESCE(g_product_best_offers.lowest_price_in_season, 'Infinity'))"


        final_query = f"""
            INSERT INTO g_product_best_offers ({', '.join(query_columns)})
            VALUES ({', '.join(query_values)})
            ON CONFLICT (product_id) DO UPDATE SET
                {', '.join(query_excluded_sets)}
            WHERE
                {query_where_clause};
        """
        
        cur.execute(final_query, tuple(params))
        print(f"Updated best offer for product {g_product_id} ({update_column}: {current_unit_price}, lowest_in_season: {lowest_price_in_season_param})")
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
        # 3850104259616 frutty products begin here
        ean_filter_list = [
            "3850104227530",
            "3850104244827",
            "3850104073656",
            "3850104210259",
            "3850104075759",
            "3850104230066",
            "3850103000820",
            "3850104020087",
            "3850104075513",
            "3850104075537",
            "3850102230044",
            "3850104053221",
            "3850104008597",
            "3850104004964",
            "3850102311439",
            "3850102320011",
            "3850102521449",
            "3850104008054",
            "3850104003189",
            "3850102326150",
            "3850104048999",
            "3850102507979",
            "3850102127252",
            "3850102011254",
            "3850103002312",
            "3850104019784",
            "3830001714715",
            "3850102125821",
            "3838471013109",
            "3838471032476",
            "3838977014051",
            "3838600253345",
            "3830001712667",
            "3850102314126",
            "3830001716016",
            "3850102117970",
            "3850102111152",
            "3850104003837",
            "3850104003134",
            "3850102320073",
            "3838600024945",
            "3850102326143",
            "38500022",
            "3850104023484",
            "38500039",
            "3850102001606",
            "3838471021272",
            "3830001714692",
            "3838471032537",
            "3850104259616", 
            "3859894042248",
"3859888163362",
"20489212",
"spar:377365",
"4337185381690",
"4062300278530",
"3859892735715",
"20539399",
"8019033020017",
"4335619104570",
"spar:34490",
"3858881580343",
"8602300204687",
"3850108036978",
"3609200006972",
"8001090621764",
"4337185613722",
"8002734100171",
"8076809580670",
"spar:10729",
"4337185382536",
"3870128039322",
"8002734100300",
"8057013880060",
"5310005005098",
"3856021219788",
"3859893697067",
"3858889331091",
"spar:40605",
"3856021219498",
"spar:207316",
"8436588101099",
"3858893252313",
"3850151244870",
"3838606881313",
"4740098090496",
"9062300132387",
"9100000905884",
"4335619167803",
"3856020262617",
"3859894055453",
"9100000633893",
"lidl:0080220",
"4011800584511",
"8001300501206",
"3858881735330",
"3608580705161",
"4062300269842",
"spar:370087",
"3859889267847",
"20449568",
"2832750000000",
"4063367108518",
"3858890973006",
"3856028500674",
"5310005001489",
"4062300278585",
"9062300140672",
"9100000810577",
"3856024805964",
"4056489877332",
"9100000734811",
"3859889287043",
"3858893252412",
"5997536134314",
"5310005001496",
"4056489676669",
"9006900013981",
"4056489006947",
"3859892735708",
"4335619260832",
"3859893822865",
"3858890265538",
"5310005005104",
"3859889287128",
"9000100656832",
"3858889897733",
"4015400795193",
"20538224",
"9062300132455",
"3856021206184",
"9100000764986",
"3871059000504",
"3830069179846",
"spar:149154",
"3609200010474",
"2831948000000",
"3870128003019",
"3858881249257",
"3858888530211",
"8004030271418",
"4335619058750",
"3858893252443",
"4062300278547",
"3830036310906",
"5999571051878",
"4063367379093",
"20287559",
"3800048200038",
"9008700215862",
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

                    # --- NEW LOGIC: FIND THE BEST OFFER AND CALCULATE UNIT PRICES ---
                    best_offer_entry = None
                    best_unit_price_overall = None # This will be the single best unit price for best_offer

                    for price_entry in raw_prices:
                        current_price = price_entry['special_price'] if price_entry['special_price'] is not None else price_entry['regular_price']
                        
                        # Calculate unit prices for the current price entry
                        calculated_unit_prices = calculate_unit_prices(
                            price=current_price,
                            base_unit_type=base_unit_type,
                            variants=variants
                        )
                        price_per_kg = calculated_unit_prices['price_per_kg']
                        price_per_l = calculated_unit_prices['price_per_l']
                        price_per_piece = calculated_unit_prices['price_per_piece']

                        # Determine the relevant unit price for this product based on its base_unit_type
                        current_unit_price_for_best_offer = None
                        if base_unit_type == 'WEIGHT':
                            current_unit_price_for_best_offer = price_per_kg
                        elif base_unit_type == 'VOLUME':
                            current_unit_price_for_best_offer = price_per_l
                        elif base_unit_type == 'COUNT':
                            current_unit_price_for_best_offer = price_per_piece

                        # Insert into g_prices with new unit price fields
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
                                is_on_special_offer = EXCLUDED.is_on_special_offer
                            RETURNING id
                        """, (
                            g_product_id,
                            price_entry['store_id'],
                            price_entry['price_date'],
                            price_entry['regular_price'],
                            price_entry['special_price'],
                            price_per_kg,
                            price_per_l,
                            price_per_piece,
                            price_entry['special_price'] is not None # Simplified logic
                        ))
                        g_price_id = product_cur.fetchone()['id']

                        # Update best_offer_entry if this is a better overall unit price
                        if current_unit_price_for_best_offer is not None:
                            if best_offer_entry is None or current_unit_price_for_best_offer < best_unit_price_overall:
                                best_unit_price_overall = current_unit_price_for_best_offer
                                best_offer_entry = price_entry # Keep the original price_entry for store_id

                    # --- END OF NEW LOGIC ---

                    # After the loop, if we found a best offer, update the database ONCE.
                    if best_offer_entry and best_unit_price_overall is not None:
                        # Call the update function just one time with the best price found
                        update_best_offer(
                            product_cur,
                            g_product_id,
                            base_unit_type,
                            best_offer_entry,
                            best_unit_price_overall,
                            seasonal_start_month, # Pass seasonal info
                            seasonal_end_month    # Pass seasonal info
                        )

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
