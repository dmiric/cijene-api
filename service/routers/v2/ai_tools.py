from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Dict, Any

router = APIRouter(tags=["AI Tools"])

@router.get("/ai-tools", summary="Get documentation for AI Tools")
async def get_ai_tools_documentation():
    """
    Provides documentation for the AI Tools available in the system.
    This endpoint describes the purpose and usage of various AI-powered functionalities.
    """
    ai_tools_info = [
        {
            "name": "search_products_v2",
            "description": "Searches for products based on a query, brand, or category, with optional sorting.",
            "parameters": {
                "q": "str (optional) - Search query for products.",
                "brand": "str (optional) - Filter products by brand.",
                "category": "str (optional) - Filter products by category.",
                "sort_by": "str (optional) - Sort order for results (e.g., 'best_value_kg', 'best_value_l', 'best_value_piece')."
            },
            "example_usage": "AI calls this tool when a user asks to find products, semantically or by keywords, or to sort them by value."
        },
        {
            "name": "find_nearby_stores_v2",
            "description": "Finds stores near a given latitude and longitude.",
            "parameters": {
                "lat": "float (required) - Latitude of the location.",
                "lon": "float (required) - Longitude of the location.",
                "radius_km": "float (optional) - Search radius in kilometers (default: 5)."
            },
            "example_usage": "AI calls this tool when a user asks to find stores near a specific location or their current location."
        },
        {
            "name": "get_product_prices_by_location_v2",
            "description": "Retrieves prices for a specific product at stores near a given location.",
            "parameters": {
                "product_id": "str (required) - The ID of the product.",
                "lat": "float (required) - Latitude of the location.",
                "lon": "float (required) - Longitude of the location."
            },
            "example_usage": "AI calls this tool when a user asks for the price of a specific product near a location."
        },
        {
            "name": "get_product_details_v2",
            "description": "Retrieves detailed information for a specific product.",
            "parameters": {
                "product_id": "str (required) - The ID of the product."
            },
            "example_usage": "AI calls this tool when a user asks for more details about a specific product."
        },
        {
            "name": "get_user_locations",
            "description": "Retrieves predefined locations for the current user (e.g., 'Kuca', 'Posao').",
            "parameters": {},
            "example_usage": "AI calls this tool when a user refers to a predefined location like 'home' or 'work'."
        }
    ]
    return ai_tools_info
