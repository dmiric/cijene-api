from service.config import settings
import sys
import json
from typing import Optional
from decimal import Decimal
from datetime import date

from .ai_helpers import pydantic_to_dict # A new helper file

db_v2 = settings.get_db_v2()
db = settings.get_db()

def debug_print(*args, **kwargs):
    print("[DEBUG AI_TOOLS]", *args, file=sys.stderr, **kwargs)

# --- Tool Functions ---
async def search_products_tool_v2(
    q: str,
    limit: int = 20,
    offset: int = 0,
    sort_by: Optional[str] = None, # Note: original search_products in psql.py doesn't support sort_by, category, brand
    category: Optional[str] = None,
    brand: Optional[str] = None,
):
    """
    Search for products by name using keyword search.
    Args:
        q (str): The user's natural language query.
        limit (int): Maximum number of results to return.
        offset (int): Number of results to skip.
        sort_by (Optional[str]): Not directly supported by current db.search_products.
        category (Optional[str]): Not directly supported by current db.search_products.
        brand (Optional[str]): Not directly supported by current db.search_products.
    """
    debug_print(f"Tool Call: search_products_tool_v2(q={q}, limit={limit}, offset={offset})")
    try:
        # db.search_products uses search_keywords table and returns ProductWithId
        # It doesn't directly support offset, sort_by, category, brand.
        # We will pass the query and then slice/filter in Python if needed,
        # or note the limitation. For now, just pass q.
        products = await db.search_products(query=q)
        
        # Apply limit and offset in Python for now, as db.search_products doesn't support it
        products_to_return = products[offset:offset+limit]

        return pydantic_to_dict({"products": products_to_return})
    except Exception as e:
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
        
        # db.get_product_prices takes product_ids (list), date, and optional store_ids (list)
        # We need to provide a date. Let's use today's date for now.
        today = date.today()
        prices_data = await db.get_product_prices(
            product_ids=[product_id],
            date=today,
            store_ids=parsed_store_ids,
        )
        return pydantic_to_dict({"prices": prices_data})
    except Exception as e:
        return {"error": str(e)}


async def get_product_details_tool_v2(product_id: int):
    """
    Retrieves the full details for a single product.
    Args:
        product_id (int): The ID of the product.
    """
    debug_print(f"Tool Call: get_product_details_tool_v2(product_id={product_id})")
    try:
        # psql.py does not have a direct get_product_details by ID.
        # We need to fetch by EAN. This is a placeholder.
        # For a proper implementation, we'd need to get EAN from product_id first,
        # or add a get_product_by_id method to psql.py.
        # For now, returning a dummy response or an error.
        # Assuming product_id can be used to get EAN for testing purposes.
        # This part needs proper implementation in psql.py if product_id is the only input.
        # For now, let's return a generic message or an empty dict.
        # If we assume product_id maps directly to EAN for simplicity in testing:
        # product_ean = str(product_id) # This is a hack for testing
        # products = await db.get_products_by_ean([product_ean])
        # if products:
        #     return pydantic_to_dict(products[0])
        # else:
        #     return {"error": f"Product with ID {product_id} not found."}
        return {"message": f"Product details for ID {product_id} (details retrieval not fully implemented yet)."}
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
        # Use db.get_stores_within_radius which queries the 'stores' table
        response = await db.get_stores_within_radius(
            lat=Decimal(str(lat)), # Convert float to Decimal for db method
            lon=Decimal(str(lon)), # Convert float to Decimal for db method
            radius_meters=radius_meters,
            chain_code=chain_code,
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
        locations = await db.get_user_locations_by_user_id(user_id)
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
