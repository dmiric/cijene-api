from service.config import settings
import sys
from typing import Optional, List, Any
from decimal import Decimal
from datetime import date
import asyncio # Import asyncio for concurrent execution
from service.utils.timing import timing_decorator # Import the decorator
from service.db.field_configs import (
    USER_LOCATION_AI_FIELDS, # Import AI fields for user locations
    PRODUCT_AI_SEARCH_FIELDS, # Import AI fields for product search
    PRODUCT_AI_DETAILS_FIELDS, # Import AI fields for product details
    STORE_AI_FIELDS, # Import AI fields for stores
    PRODUCT_PRICE_AI_FIELDS, # Import AI fields for product prices
    PRODUCT_DB_SEARCH_FIELDS # Import DB search fields
)

from .ai_helpers import pydantic_to_dict, filter_product_fields # A new helper file

db_v2 = settings.get_db_v2()
db = settings.get_db() # Still needed for get_user_locations_tool

def debug_print(*args, **kwargs):
    print("[DEBUG AI_TOOLS]", *args, file=sys.stderr, **kwargs)

# --- Tool Functions ---
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
        )

        if not products:
            return {"products": []}

        # Filter products to include only AI-relevant fields
        filtered_products = filter_product_fields(products, PRODUCT_AI_SEARCH_FIELDS)

        # Step 2: If store_ids are provided, filter products by their availability in those stores
        if store_ids:
            parsed_store_ids = [int(s.strip()) for s in store_ids.split(',') if s.strip()]
            filtered_products_with_prices = []
            
            for product_dict in filtered_products: # Use filtered_products here
                product_id = product_dict.get("id")
                base_unit_type = product_dict.get("base_unit_type") # Get base_unit_type
                if product_id is None or base_unit_type is None:
                    continue

                # Fetch prices for this product in the specified stores from g_prices
                prices_in_stores_raw = await db_v2.get_g_product_prices_by_location(
                    product_id=product_id,
                    store_ids=parsed_store_ids,
                )
                debug_print(f"Raw prices from g_prices: {prices_in_stores_raw}")

                prices_in_stores_formatted = []
                for price_entry in prices_in_stores_raw:
                    # Determine the correct unit_price based on base_unit_type
                    unit_price_value = None
                    if base_unit_type == 'WEIGHT':
                        unit_price_value = price_entry.get('price_per_kg')
                    elif base_unit_type == 'VOLUME':
                        unit_price_value = price_entry.get('price_per_l')
                    elif base_unit_type == 'COUNT':
                        unit_price_value = price_entry.get('price_per_piece')
                    
                    prices_in_stores_formatted.append({
                        "chain_code": price_entry.get("chain_code"),
                        "product_id": price_entry.get("product_id"),
                        "store_id": price_entry.get("store_id"),
                        "store_name": price_entry.get("store_code"), # Use store_code as store_name
                        "store_address": price_entry.get("store_address"),
                        "store_city": price_entry.get("store_city"),
                        "price_date": price_entry.get("price_date").isoformat() if price_entry.get("price_date") else None,
                        "regular_price": price_entry.get("regular_price"),
                        "special_price": price_entry.get("special_price"),
                        "unit_price": unit_price_value,
                        "best_price_30": None, # Not available in g_prices directly
                        "anchor_price": None, # Not available in g_prices directly
                        "is_on_special_offer": price_entry.get("is_on_special_offer")
                    })
                
                if prices_in_stores_formatted:
                    product_dict["prices_in_stores"] = prices_in_stores_formatted
                    filtered_products_with_prices.append(product_dict)
            
            return pydantic_to_dict({"products": filtered_products_with_prices})
        
        # If no store_ids, return filtered products directly
        return pydantic_to_dict({"products": filtered_products})

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
        
        # Fetch g_product's base_unit_type to determine which unit price to return
        # This requires fetching product details first
        product_details = await db_v2.get_g_product_details(product_id)
        if not product_details:
            return {"prices": []} # Product not found

        base_unit_type = product_details.get("base_unit_type")
        if base_unit_type is None:
            return {"prices": []} # Cannot determine unit type

        # Use db_v2.get_g_product_prices_by_location (from psql_v2.py)
        prices_in_stores_raw = await db_v2.get_g_product_prices_by_location(
            product_id=product_id,
            store_ids=parsed_store_ids,
        )
        debug_print(f"Raw prices from g_prices for get_product_prices_by_location_tool_v2: {prices_in_stores_raw}")

        prices_in_stores_formatted = []
        for price_entry in prices_in_stores_raw:
            unit_price_value = None
            if base_unit_type == 'WEIGHT':
                unit_price_value = price_entry.get('price_per_kg')
            elif base_unit_type == 'VOLUME':
                unit_price_value = price_entry.get('price_per_l')
            elif base_unit_type == 'COUNT':
                unit_price_value = price_entry.get('price_per_piece')
            
            prices_in_stores_formatted.append({
                "chain_code": price_entry.get("chain_code"),
                "product_id": price_entry.get("product_id"),
                "store_id": price_entry.get("store_id"),
                "price_date": price_entry.get("price_date").isoformat() if price_entry.get("price_date") else None,
                "regular_price": price_entry.get("regular_price"),
                "special_price": price_entry.get("special_price"),
                "unit_price": unit_price_value,
                "best_price_30": None, # Not available in g_prices directly
                "anchor_price": None, # Not available in g_prices directly
                "is_on_special_offer": price_entry.get("is_on_special_offer")
            })
        
        return pydantic_to_dict({"prices": prices_in_stores_formatted})
    except Exception as e:
        debug_print(f"Error in get_product_prices_by_location_tool_v2: {e}")
        return {"error": str(e)}



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
        )
        if details:
            # Filter details to include only AI-relevant fields
            # Note: filter_product_fields expects a list, so wrap details in a list
            filtered_details = filter_product_fields([details], PRODUCT_AI_DETAILS_FIELDS)
            return pydantic_to_dict(filtered_details[0]) if filtered_details else {"message": f"Product with ID {product_id} not found."}
        else:
            return {"message": f"Product with ID {product_id} not found."}
    except Exception as e:
        return {"error": str(e)}



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



async def multi_search_tool(queries: List[dict]):
    """
    Executes multiple product search queries concurrently and returns all results.
    Args:
        queries (List[dict]): A list of tool calls to execute. Each item should be a dictionary
                              with 'name' (the tool function name) and 'arguments' (a dictionary
                              of arguments for that tool).
    """
    debug_print(f"Tool Call: multi_search_tool(queries={queries})")
    results = []
    tasks = []

    for i, query_data in enumerate(queries):
        tool_name = query_data.get("name")
        tool_args = query_data.get("arguments", {})

        if tool_name not in available_tools:
            results.append({f"query_{i}_error": f"Tool '{tool_name}' not found."})
            continue

        tool_func = available_tools[tool_name]
        tasks.append(tool_func(**tool_args))

    if not tasks:
        return {"results": []}

    # Execute all tasks concurrently
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, res in enumerate(raw_results):
        if isinstance(res, Exception):
            results.append({f"query_{i}_error": str(res)})
        else:
            results.append({f"query_{i}_result": res})
    
    return {"results": results}


# Map tool names to their Python functions
available_tools = {
    "search_products_v2": search_products_tool_v2,
    "get_product_prices_by_location_v2": get_product_prices_by_location_tool_v2,
    "get_product_details_v2": get_product_details_tool_v2,
    "find_nearby_stores_v2": find_nearby_stores_tool_v2,
    "get_user_locations": get_user_locations_tool,
    "multi_search_tool": multi_search_tool, # Add the new multi-search tool
}
