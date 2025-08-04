from service.config import get_settings
from typing import Optional, List, Any, Dict
from decimal import Decimal
from datetime import datetime # Import datetime
import asyncio # Import asyncio for concurrent execution
from uuid import UUID # Import UUID
import structlog # Import structlog
from service.db.field_configs import (
    USER_LOCATION_AI_FIELDS, # Import AI fields for user locations
    PRODUCT_AI_SEARCH_FIELDS, # Import AI fields for product search
    PRODUCT_AI_DETAILS_FIELDS, # Import AI fields for product details
    STORE_AI_FIELDS, # Import AI fields for stores
    PRODUCT_PRICE_AI_FIELDS, # Import AI fields for product prices
    PRODUCT_DB_SEARCH_FIELDS # Import DB search fields
)

from .ai_helpers import pydantic_to_dict, filter_product_fields # A new helper file

db = get_settings().get_db()
log = structlog.get_logger(__name__) # Initialize structlog logger

# --- Tool Functions ---
async def multi_search_tool(queries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Executes multiple product search queries concurrently. It includes all of your
    existing logic and adds a fix to prevent the 'caption' argument from being
    passed to the underlying search function.
    """
    log.debug("multi_search_tool received queries", queries=queries)
    tasks = []

    # 1. Prepare all the asynchronous tasks (This is your existing logic)
    for query_data in queries:
        tool_name = query_data.get("name")
        # Make a copy to avoid modifying the original query data needed later
        tool_args = query_data.get("arguments", {}).copy()

        # The 'caption' is metadata, not an argument for the search function.
        # We remove it from the arguments we will pass to the sub-tool.
        if 'caption' in tool_args:
            del tool_args['caption']

        if tool_name not in available_tools:
            future = asyncio.Future()
            future.set_exception(ValueError(f"Tool '{tool_name}' not found."))
            tasks.append(future)
            continue

        tool_func = available_tools[tool_name]
        
        if asyncio.iscoroutinefunction(tool_func):
            # The 'tool_args' dictionary no longer contains 'caption', so this call is safe.
            tasks.append(tool_func(**tool_args))
        else:
            future = asyncio.Future()
            future.set_exception(TypeError(f"Tool '{tool_name}' is not an async function."))
            tasks.append(future)

    if not tasks:
        return []

    # 2. Execute all tasks concurrently (Your existing logic)
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    # 3. Structure the final output (Your existing logic)
    final_structured_results = []
    for i, res in enumerate(raw_results):
        # Get the entire original query object using its index.
        # This still contains the caption, because we only deleted it from a copy.
        original_query = queries[i]

        if isinstance(res, Exception):
            structured_item = {
                "query": original_query,
                "error": f"An error occurred: {str(res)}"
            }
        else:
            structured_item = {
                "query": original_query,
                "products": res
            }
        final_structured_results.append(structured_item)
    
    log.debug("multi_search_tool returning structured results", results=final_structured_results)
    return final_structured_results

async def search_products_tool_v2(
    q: str,
    limit: Optional[int] = 20,
    offset: Optional[int] = 0,
    *,
    sort_by: Optional[str] = "relevance",
    store_ids: Optional[str] = None,
):
    """
    Search for products by name and optionally filter by stores.
    FIXED: Now returns products even if they have no price in the specified stores,
    providing a better user experience.
    """
    try:
        if store_ids:
            parsed_store_ids = [int(s.strip()) for s in store_ids.split(',') if s.strip()]
            if not parsed_store_ids:
                 return {"products": []}

            # This call is working and returning products.
            # Each product has a `prices_in_stores` key which might be an empty list [].
            products_with_prices = await db.get_g_products_hybrid_search_with_prices(
                query=q,
                store_ids=parsed_store_ids,
                limit=limit,
                offset=offset,
                sort_by=sort_by,
            )
            
            # This function filters for AI-relevant fields. This is correct.
            filtered_products = filter_product_fields(products_with_prices, PRODUCT_AI_SEARCH_FIELDS)

            log.debug("search_products_tool_v2 returning with store_ids", num_products=len(filtered_products))
            return pydantic_to_dict({"products": filtered_products})

    except Exception as e:
        # Add traceback for better debugging if it happens again
        import traceback
        traceback.print_exc()
        log.error("search_products_tool_v2 caught exception", error=str(e))
        return {"error": str(e)}
    
async def get_product_details_tool_v2(product_id: int):
    """
    Retrieves the full details for a single product.
    Args:
        product_id (int): The ID of the product.
    """
    try:
        details = await db.get_g_product_details(
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
    radius_meters: Optional[int],
    chain_code: Optional[str],
):
    """
    Finds stores within a specified radius of a geographic point using the 'stores' table.
    Args:
        lat (float): Latitude of the center point.
        lon (float): Longitude of the center point.
        radius_meters (Optional[int]): Radius in meters to search within.
        chain_code (Optional[str]): Optional: Filter by a specific chain.
    """
    # Reintroduce default value internally
    if radius_meters is None:
        radius_meters = 5000

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
        return {"error": error_message}

async def get_seasonal_product_deals_tool_v2(
    canonical_name: str,
    category: str,
    current_month: Optional[int],
    limit: Optional[int],
    offset: Optional[int]
):
    """
    Finds the lowest seasonal price for generic products across all chains
    that match the canonical name and category and are currently in season.
    Args:
        canonical_name (str): The canonical name of the generic product (e.g., "Crvene Naranče").
        category (str): The category of the product (e.g., "Voće i povrće").
        current_month (Optional[int]): The current month (1-12). Defaults to current system month if not provided.
        limit (Optional[int]): Maximum number of results to return.
        offset (Optional[int]): Number of results to skip.
    """
    # Reintroduce default values internally
    if current_month is None:
        current_month = datetime.now().month
    if limit is None:
        limit = 10
    if offset is None:
        offset = 0

    log.debug("Tool Call: get_seasonal_product_deals_tool_v2", canonical_name=canonical_name, category=category, current_month=current_month)
    try:
        results = await db.golden_products.get_overall_seasonal_best_price_for_generic_product(
            canonical_name=canonical_name,
            category=category,
            current_month=current_month,
            limit=limit,
            offset=offset
        )
        return pydantic_to_dict({"seasonal_products": results})
    except Exception as e:
        log.error("Error in get_seasonal_product_deals_tool_v2", error=str(e))
        return {"error": str(e)}

from .internal_tools import get_user_locations_tool # Assuming you move the original tool logic here

# NEW COMPOSITE TOOL
async def find_nearby_stores_for_user_tool(user_id: str, radius_meters: Optional[int]) -> dict:
    """
    Finds nearby stores for a given user by first retrieving their primary
    saved location and then searching around that location.
    """
    log.debug("Composite tool 'find_nearby_stores_for_user' called", user_id=user_id)
    
    # Convert user_id from str to UUID for internal use
    user_id_uuid = UUID(user_id)

    # Step 1: Get user location
    location_data = await get_user_locations_tool(user_id=user_id_uuid)
    if "error" in location_data:
        return location_data # Pass through the error
        
    locations = location_data.get("locations", [])
    if not locations:
        return {"error": "No saved locations found for the user."}

    # Step 2: Use the first location to find stores
    first_location = locations[0]
    lat = first_location.get("latitude")
    lon = first_location.get("longitude")

    if lat is None or lon is None:
        return {"error": "Saved location is missing coordinate data."}

    # Reintroduce default value internally
    if radius_meters is None:
        radius_meters = 1500

    # Step 3: Find nearby stores
    return await find_nearby_stores_tool_v2(lat=float(lat), lon=float(lon), radius_meters=radius_meters)

# Map tool names to their Python functions
available_tools = {
    "multi_search_tool": multi_search_tool,
    "search_products_v2": search_products_tool_v2,
    "get_product_details_v2": get_product_details_tool_v2,
    "find_nearby_stores_v2": find_nearby_stores_tool_v2,
    "get_seasonal_product_deals_v2": get_seasonal_product_deals_tool_v2, # Add the new seasonal deals tool
    "find_nearby_stores_for_user": find_nearby_stores_for_user_tool, # Add the new composite tool
}
