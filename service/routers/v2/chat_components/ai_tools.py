from service.config import get_settings
import sys
from typing import Optional, List, Any
from decimal import Decimal
from datetime import date, datetime # Import datetime
import asyncio # Import asyncio for concurrent execution
from uuid import UUID # Import UUID
from service.utils.timing import timing_decorator, debug_print # Import the decorator and debug_print
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

# --- Tool Functions ---
async def search_products_tool_v2(
    q: str,
    limit: Optional[int] = 20, # Set default here
    offset: Optional[int] = 0, # Set default here
    *, # All subsequent arguments must be keyword-only
    sort_by: Optional[str] = "relevance", # Set default here
    store_ids: Optional[str] = None, # Set default here
):
    """
    Search for products by name using hybrid search (vector + keyword) and optionally filter by stores.
    Args:
        q (str): The user's natural language query.
        limit (Optional[int]): Maximum number of results to return.
        offset (Optional[int]): Number of results to skip.
        sort_by (Optional[str]): How to sort the results (e.g., 'best_value_kg', 'relevance').
        store_ids (Optional[str]): Comma-separated list of store IDs to filter products by availability.
    """
    # Remove internal default value assignments as they are now in the signature
    # if limit is None:
    #     limit = 20
    # if offset is None:
    #     offset = 0
    # if sort_by is None:
    #     sort_by = "relevance" # Default sort_by

    try:
        # Step 1: Perform hybrid search using db.get_g_products_hybrid_search
        products = await db.get_g_products_hybrid_search(
            query=q,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
        )

        if not products:
            debug_print("search_products_tool_v2 returning empty products list.")
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
                prices_in_stores_raw = await db.get_g_product_prices_by_location(
                    product_id=product_id,
                    store_ids=parsed_store_ids,
                )

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
            
            debug_print(f"search_products_tool_v2 returning with store_ids: {len(filtered_products_with_prices)} products.")
            return pydantic_to_dict({"products": filtered_products_with_prices})
        
        # If no store_ids, return filtered products directly
        debug_print(f"search_products_tool_v2 returning without store_ids: {len(filtered_products)} products.")
        return pydantic_to_dict({"products": filtered_products})

    except Exception as e:
        debug_print(f"search_products_tool_v2 caught exception: {e}")
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
        product_details = await db.get_g_product_details(product_id)
        if not product_details:
            return {"prices": []} # Product not found

        base_unit_type = product_details.get("base_unit_type")
        if base_unit_type is None:
            return {"prices": []} # Cannot determine unit type

        # Use db.get_g_product_prices_by_location
        prices_in_stores_raw = await db.get_g_product_prices_by_location(
            product_id=product_id,
            store_ids=parsed_store_ids,
        )

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






async def multi_search_tool(queries: List[dict]):
    """
    Executes multiple product search queries concurrently and returns all results.
    Args:
        queries (List[dict]): A list of tool calls to execute. Each item should be a dictionary
                              with 'name' (the tool function name) and 'arguments' (a dictionary
                              of arguments for that tool).
    """
    debug_print(f"multi_search_tool received queries: {queries}") # Added debug print
    results = []
    tasks = []

    for i, query_data in enumerate(queries):
        tool_name = query_data.get("name")
        tool_args = query_data.get("arguments", {})

        if tool_name not in available_tools:
            results.append({f"query_{i}_error": f"Tool '{tool_name}' not found."})
            continue

        # Ensure 'offset', 'store_ids', and 'sort_by' are always present for search_products_v2
        if tool_name == "search_products_v2":
            tool_args["offset"] = tool_args.get("offset")
            tool_args["store_ids"] = tool_args.get("store_ids")
            # Explicitly set sort_by to its default if not provided by the model
            if "sort_by" not in tool_args or tool_args["sort_by"] is None:
                tool_args["sort_by"] = "relevance"

        tool_func = available_tools[tool_name]
        debug_print(f"multi_search_tool: Preparing to call {tool_name}")
        debug_print(f"multi_search_tool: tool_func type: {type(tool_func)}")
        debug_print(f"multi_search_tool: tool_args for {tool_name}: {tool_args}")

        # Check if it's a coroutine function and append the coroutine object
        if asyncio.iscoroutinefunction(tool_func):
            tasks.append(tool_func(**tool_args))
            debug_print(f"multi_search_tool: Appended coroutine for {tool_name}")
        else:
            # If it's not a coroutine function, execute it directly (if it's synchronous)
            # or raise an error if it's expected to be async.
            debug_print(f"multi_search_tool: WARNING: {tool_name} is not a coroutine function. Executing directly.")
            try:
                result = tool_func(**tool_args)
                # If it returns a Future/Task, it's still async, so await it
                if asyncio.isfuture(result) or asyncio.iscoroutine(result):
                    tasks.append(result)
                else:
                    # Wrap synchronous result in a future to be gathered
                    tasks.append(asyncio.Future())
                    tasks[-1].set_result(result)
            except Exception as e:
                debug_print(f"multi_search_tool: Error executing sync tool {tool_name}: {e}")
                results.append({f"query_{i}_error": str(e)})

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

    debug_print(f"Tool Call: get_seasonal_product_deals_tool_v2(canonical_name={canonical_name}, category={category}, current_month={current_month})")
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
        debug_print(f"Error in get_seasonal_product_deals_tool_v2: {e}")
        return {"error": str(e)}


from .internal_tools import get_user_locations_tool # Assuming you move the original tool logic here

# NEW COMPOSITE TOOL
async def find_nearby_stores_for_user_tool(user_id: str, radius_meters: Optional[int]) -> dict:
    """
    Finds nearby stores for a given user by first retrieving their primary
    saved location and then searching around that location.
    """
    debug_print(f"Composite tool 'find_nearby_stores_for_user' called for user {user_id}")
    
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
    "search_products_v2": search_products_tool_v2,
    "get_product_prices_by_location_v2": get_product_prices_by_location_tool_v2,
    "get_product_details_v2": get_product_details_tool_v2,
    "find_nearby_stores_v2": find_nearby_stores_tool_v2,
    "multi_search_tool": multi_search_tool,
    "get_seasonal_product_deals_v2": get_seasonal_product_deals_tool_v2, # Add the new seasonal deals tool
    "find_nearby_stores_for_user": find_nearby_stores_for_user_tool, # Add the new composite tool
}
