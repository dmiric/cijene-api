from service.config import settings
import sys
import json
from typing import Optional, List, Any
from decimal import Decimal
from datetime import date
from service.utils.timing import timing_decorator # Import the decorator
from service.db.field_configs import (
    USER_LOCATION_AI_FIELDS, # Import AI fields for user locations
    PRODUCT_AI_SEARCH_FIELDS, # Import AI fields for product search
    PRODUCT_AI_DETAILS_FIELDS, # Import AI fields for product details
    STORE_AI_FIELDS, # Import AI fields for stores
    PRODUCT_PRICE_AI_FIELDS # Import AI fields for product prices
)

from .ai_helpers import pydantic_to_dict # A new helper file

db_v2 = settings.get_db_v2()
db = settings.get_db() # Still needed for get_user_locations_tool

def debug_print(*args, **kwargs):
    print("[DEBUG AI_TOOLS]", *args, file=sys.stderr, **kwargs)

# --- Tool Functions ---
@timing_decorator
async def search_products_tool_v2(
    q: str,
    limit: int = 20,
    offset: int = 0,
    sort_by: Optional[str] = None,
    category: Optional[str] = None,
    brand: Optional[str] = None,
    store_ids: Optional[str] = None, # Added store_ids
):
    """
    Search for products by name using hybrid search (vector + keyword) and optionally filter by stores.
    Args:
        q (str): The user's natural language query.
        limit (int): Maximum number of results to return.
        offset (int): Number of results to skip.
        sort_by (Optional[str]): How to sort the results (e.g., 'best_value_kg', 'relevance').
        category (Optional[str]): Filter by product category.
        brand (Optional[str]): Filter by product brand.
        store_ids (Optional[str]): Comma-separated list of store IDs to filter products by availability.
    """
    debug_print(f"Tool Call: search_products_tool_v2(q={q}, limit={limit}, offset={offset}, sort_by={sort_by}, category={category}, brand={brand}, store_ids={store_ids})")
    try:
        # Step 1: Perform hybrid search using db_v2.get_g_products_hybrid_search
        products = await db_v2.get_g_products_hybrid_search(
            query=q,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            category=category,
            brand=brand,
            fields=PRODUCT_AI_SEARCH_FIELDS # Pass AI-specific fields
        )

        if not products:
            return {"products": []}

        # Step 2: If store_ids are provided, filter products by their availability in those stores
        if store_ids:
            parsed_store_ids = [int(s.strip()) for s in store_ids.split(',') if s.strip()]
            filtered_products_with_prices = []
            
            for product_dict in products: # products are now dicts
                product_id = product_dict.get("id")
                if product_id is None:
                    continue

                # Fetch prices for this product in the specified stores
                prices_in_stores = await db.get_product_prices(
                    product_ids=[product_id],
                    date=date.today(), # Use today's date for price lookup
                    store_ids=parsed_store_ids,
                    fields=PRODUCT_PRICE_AI_FIELDS # Pass AI-specific fields for prices
                )
                
                if prices_in_stores:
                    product_dict["prices_in_stores"] = prices_in_stores
                    filtered_products_with_prices.append(product_dict)
            
            # No need to pop embedding here, as it's already excluded by PRODUCT_AI_SEARCH_FIELDS
            return pydantic_to_dict({"products": filtered_products_with_prices})
        
        # If no store_ids, return products from hybrid search directly
        # No need to pop embedding here, as it's already excluded by PRODUCT_AI_SEARCH_FIELDS
        return pydantic_to_dict({"products": products})

    except Exception as e:
        debug_print(f"Error in search_products_tool_v2: {e}")
        return {"error": str(e)}


async def get_product_prices_by_location_tool_v2(product_id: int, store_ids: str):
    """
    Finds the prices for a single product at a list of specific stores.
    Args:
        product_id (int): The ID of the product.
        store_ids (str): A comma-separated list of store IDs, e.g., 101,105,230.
    """
    debug_print(f"Tool Call: get_product_prices_by_location_tool_v2(product_id={product_id}, store_ids={store_ids})")
    try:
        parsed_store_ids = [int(s.strip()) for s in store_ids.split(',') if s.strip()]
        
        # Use db.get_product_prices (from psql.py)
        prices_data = await db.get_product_prices(
            product_ids=[product_id],
            date=date.today(), # Use today's date for price lookup
            store_ids=parsed_store_ids,
            fields=PRODUCT_PRICE_AI_FIELDS # Pass AI-specific fields
        )
        return pydantic_to_dict({"prices": prices_data})
    except Exception as e:
        return {"error": str(e)}


@timing_decorator
async def get_product_details_tool_v2(product_id: int):
    """
    Retrieves the full details for a single product.
    Args:
        product_id (int): The ID of the product.
    """
    debug_print(f"Tool Call: get_product_details_tool_v2(product_id={product_id})")
    try:
        details = await db_v2.get_g_product_details(
            product_id,
            fields=PRODUCT_AI_DETAILS_FIELDS # Pass AI-specific fields
        )
        if details:
            # No need to pop embedding here, as it's already excluded by PRODUCT_AI_DETAILS_FIELDS
            return pydantic_to_dict(details)
        else:
            return {"message": f"Product with ID {product_id} not found."}
    except Exception as e:
        return {"error": str(e)}


@timing_decorator
async def find_nearby_stores_tool_v2(
    lat: float,
    lon: float,
    radius_meters: int = 5000,
    chain_code: Optional[str] = None,
):
    """
    Finds stores within a specified radius of a geographic point using the 'stores' table.
    Args:
        lat (float): Latitude of the center point.
        lon (float): Longitude of the center point.
        radius_meters (int): Radius in meters to search within.
        chain_code (Optional[str]): Optional: Filter by a specific chain.
    """
    debug_print(f"Tool Call: find_nearby_stores_tool_v2(lat={lat}, lon={lon}, radius_meters={radius_meters}, chain_code={chain_code})")
    try:
        # Use db (psql.py) and get_stores_within_radius for 'stores' table
        response = await db.get_stores_within_radius(
            lat=Decimal(str(lat)), # Convert float to Decimal for db method
            lon=Decimal(str(lon)), # Convert float to Decimal for db method
            radius_meters=radius_meters,
            chain_code=chain_code,
            fields=STORE_AI_FIELDS # Pass AI-specific fields
        )
        return pydantic_to_dict({"stores": response})
    except Exception as e:
        error_message = f"Database error in find_nearby_stores_tool_v2: {e}"
        debug_print(error_message)
        return {"error": error_message}


@timing_decorator
async def get_user_locations_tool(user_id: int):
    """
    Retrieves a user's saved locations.
    Args:
        user_id (int): The ID of the user.
    """
    debug_print(f"Tool Call: get_user_locations_tool(user_id={user_id})")
    try:
        # This still uses the original db as user locations are not g_tables
        locations = await db.users.get_user_locations_by_user_id(
            user_id,
            fields=USER_LOCATION_AI_FIELDS # Pass the specific fields for AI
        )
        return pydantic_to_dict({"locations": locations})
    except Exception as e:
        return {"error": str(e)}


# Map tool names to their Python functions
available_tools = {
    "search_products_v2": search_products_tool_v2,
    "get_product_prices_by_location_v2": get_product_prices_by_location_tool_v2,
    "get_product_details_v2": get_product_details_tool_v2,
    "find_nearby_stores_v2": find_nearby_stores_tool_v2,
    "get_user_locations": get_user_locations_tool,
}
