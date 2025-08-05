import os
import json
import logging
import structlog
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List, Dict, Any
from psycopg2.extensions import connection as PgConnection, cursor as PgCursor

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import sys
import traceback
from psycopg2.extensions import AsIs # Added AsIs for custom adapter

# Load environment variables
load_dotenv()

# Initialize structlog logger
log = structlog.get_logger()

def get_db_connection() -> PgConnection:
    """Establishes and returns a database connection."""
    conn = psycopg2.connect(os.getenv("DB_DSN"))
    return conn

def calculate_unit_prices(
    price: Decimal,
    base_unit_type: str,
    variants: List[Dict[str, Any]]
) -> Dict[str, Optional[Decimal]]:
    """
    Calculates price_per_kg, price_per_l, and price_per_piece based on the product's
    base unit type and variants. This version is corrected to be robust and foolproof.
    """
    price_per_kg = None
    price_per_l = None
    price_per_piece = None

    if not variants or price is None:
        return {"price_per_kg": None, "price_per_l": None, "price_per_piece": None}

    # Use the first variant as the basis for calculation
    main_variant = variants[0]
    unit = main_variant.get("unit", "").lower()
    value = main_variant.get("value")
    piece_count = main_variant.get("piece_count")

    # Safely convert variant values to Decimal, handling potential errors
    try:
        if value is not None:
            value = Decimal(str(value))
        if piece_count is not None:
            piece_count = Decimal(str(piece_count))
    except Exception as e:
        log.error("Error converting variant values to Decimal", error=str(e), variant=main_variant)
        return {"price_per_kg": None, "price_per_l": None, "price_per_piece": None}

    # Prevent division by zero errors
    if (value is None or value <= 0) and (piece_count is None or piece_count <= 0):
        log.warning("Cannot calculate unit prices: value or piece_count is zero or None.")
        return {"price_per_kg": None, "price_per_l": None, "price_per_piece": None}

    # --- Corrected and Explicit Logic ---
    if base_unit_type == 'WEIGHT':
        if value is not None and value > 0:
            if unit == 'g':
                price_per_kg = (price / value) * 1000
            elif unit == 'kg':
                price_per_kg = price / value
    
    elif base_unit_type == 'VOLUME':
        if value is not None and value > 0:
            if unit == 'ml':
                price_per_l = (price / value) * 1000
            elif unit == 'l':
                price_per_l = price / value
            
    elif base_unit_type == 'COUNT':
        # Prioritize piece_count if it exists (e.g., for 4x100g packs)
        if piece_count is not None and piece_count > 0:
            price_per_piece = price / piece_count
        # Fallback to value if unit is 'kom'
        elif unit == 'kom' and value is not None and value > 0:
            price_per_piece = price / value

    return {
        "price_per_kg": price_per_kg,
        "price_per_l": price_per_l,
        "price_per_piece": price_per_piece,
    }

def get_category_id_by_name(cur: PgCursor, category_name: str) -> Optional[int]:
    """Retrieves the ID of an existing category by its name."""
    cur.execute("SELECT id FROM g_categories WHERE name = %s", (category_name,))
    result = cur.fetchone()
    return result['id'] if result else None

def create_category_if_not_exists(cur: PgCursor, category_name: str) -> int:
    """
    Creates a new category if it doesn't exist and returns its ID.
    Handles concurrent inserts using ON CONFLICT.
    """
    cur.execute("""
        INSERT INTO g_categories (name) VALUES (%s)
        ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name -- No-op update to return id
        RETURNING id
    """, (category_name,))
    return cur.fetchone()['id']

def get_existing_categories(cur: PgCursor) -> List[str]:
    """Fetches all existing category names from the g_categories table."""
    cur.execute("SELECT name FROM g_categories ORDER BY name;")
    return [row['name'] for row in cur.fetchall()]

def create_golden_record(
    cur: PgCursor,
    ean: str,
    normalized_data: Dict[str, Any],
    embedding: List[float],
    category_id: int # Changed to category_id
) -> Optional[int]:
    """Inserts a new golden record into g_products and returns its ID."""
    try:
        insert_query = """
            INSERT INTO g_products (
                ean, canonical_name, brand, category_id, base_unit_type, variants,
                text_for_embedding, keywords, is_generic_product,
                seasonal_start_month, seasonal_end_month, embedding
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ean) DO NOTHING
            RETURNING id
        """
        params = (
            ean,
            normalized_data['canonical_name'],
            normalized_data['brand'],
            category_id, # Use category_id
            normalized_data['base_unit_type'],
            json.dumps(normalized_data['variants']), # Pass variants as a JSON string
            normalized_data['text_for_embedding'],
            normalized_data['keywords'],
            normalized_data['is_generic_product'],
            normalized_data.get('seasonal_start_month'),
            normalized_data.get('seasonal_end_month'),
            AsIs(f"'{json.dumps(embedding)}'") if embedding is not None else None # Explicitly format embedding
        )

        cur.execute(insert_query, params)
        result = cur.fetchone()
        if result:
            return result['id']
        else:
            log.warning("INSERT for EAN returned no ID. This might indicate a silent failure or a unique constraint violation.", ean=ean)
            return None
    except psycopg2.Error as e:
        log.error("Database error creating golden record", ean=ean, pgcode=e.pgcode, pgerror=e.pgerror, exc_info=True)
        return None
    except Exception as e:
        log.error("Unexpected error creating golden record", ean=ean, error=str(e), exc_info=True)
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
        log.info("Updated best offer for product", g_product_id=g_product_id, update_column=update_column,
                 current_unit_price=current_unit_price, lowest_in_season=lowest_price_in_season_param)
    except Exception as e:
        log.error("Error updating best offer for product", g_product_id=g_product_id, error=str(e))

def mark_chain_products_as_processed(cur: PgCursor, chain_product_ids: List[int]) -> None:
    """Marks a list of chain_products as processed."""
    try:
        cur.execute("""
            UPDATE chain_products
            SET is_processed = TRUE
            WHERE id = ANY(%s)
        """, (chain_product_ids,))
    except Exception as e:
        log.error("Error marking chain_products as processed", error=str(e))
