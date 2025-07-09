from pydantic import BaseModel, Field
from typing import Optional
from uuid import UUID
import datetime
from decimal import Decimal
from datetime import date
from dataclasses import asdict, fields, is_dataclass
from google import genai
from .ai_models import gemini_client
from .ai_tools import available_tools

# --- Pydantic Models ---
class ChatRequest(BaseModel):
    session_id: Optional[UUID] = Field(None, description="Optional: UUID of the chat session to continue. If not provided, a new session will be started.")
    message_text: str = Field(..., description="The user's message.")

class ChatMessageResponse(BaseModel):
    id: UUID
    user_id: UUID
    session_id: UUID
    sender: str
    message_text: str
    timestamp: datetime.datetime
    tool_calls: Optional[dict] = None
    tool_outputs: Optional[dict] = None

class ToolCall(BaseModel):
    name: str = Field(..., description="The name of the tool to call.")
    arguments: dict = Field(..., description="The arguments to pass to the tool.")

class MultiSearchTool(BaseModel):
    queries: list[ToolCall] = Field(..., description="A list of tool calls to execute.")

class ChatResponse(BaseModel):
    session_id: UUID = Field(..., description="The UUID of the chat session.")
    message: str = Field(..., description="A message indicating the status or initial response.")

# --- AI Tool Schemas ---
# Manually define gemini_tools as FunctionDeclaration dictionaries
gemini_tools = [
    genai.types.Tool(function_declarations=[
        {
            "name": "search_products_v2",
            "description": "Search for products by name using hybrid search (vector + keyword) and advanced sorting.",
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {"type": "string", "description": "The user's natural language query."},
                    "limit": {"type": "integer", "description": "Maximum number of results to return. Should ALWAYS be 3"},
                    "offset": {"type": "integer", "description": "Number of results to skip."},
                    "sort_by": {"type": "string", "description": "How to sort the results (e.g., 'best_value_kg', 'best_value_l', 'best_value_piece', 'relevance')."},
                    "store_ids": {"type": "string", "description": "Comma-separated list of store IDs to filter products by availability."},
                    "caption": {"type": "string", "description": "Caption for this search in Croatian. (e.g., 'Najjeftiniji limun', 'Najpopularniji proizvodi od limuna', 'Sviježi limun')"}
                },
                "required": ["q","store_ids","caption","limit"],
            },
        }
    ]),
    genai.types.Tool(function_declarations=[
        {
            "name": "get_product_prices_by_location_v2",
            "description": "Finds the prices for a single product at a list of specific stores.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer", "description": "The ID of the product."},
                    "store_ids": {"type": "string", "description": "A comma-separated list of store IDs, e.g., 101,105,230."},
                },
                "required": ["product_id", "store_ids"],
            },
        }
    ]),
    genai.types.Tool(function_declarations=[
        {
            "name": "get_product_details_v2",
            "description": "Retrieves the full details for a single product.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer", "description": "The ID of the product."},
                },
                "required": ["product_id"],
            },
        }
    ]),
    genai.types.Tool(function_declarations=[
        {
            "name": "find_nearby_stores_v2",
            "description": "Finds stores within a specified radius of a geographic point using the 'stores' table.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number", "format": "float", "description": "Latitude of the center point."},
                    "lon": {"type": "number", "format": "float", "description": "Longitude of the center point."},
                    "radius_meters": {"type": "integer", "description": "Radius in meters to search within."},
                    "chain_code": {"type": "string", "description": "Optional: Filter by a specific chain."},
                },
                "required": ["lat", "lon"],
            },
        }
    ]),
    genai.types.Tool(function_declarations=[
        {
            "name": "multi_search_tool",
            "description": "Executes multiple product search queries concurrently and returns all results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "The name of the tool function to call."},
                                "arguments": {
                                    "type": "object",
                                    "description": "The arguments to pass to the tool. Must match the schema of the target tool.",
                                    "properties": {
                                        "q": {"type": "string", "description": "The user's natural language query."},
                                        "limit": {"type": "integer", "description": "Maximum number of results to return."},
                                        "offset": {"type": "integer", "description": "Number of results to skip."},
                                        "sort_by": {"type": "string", "description": "How to sort the results (e.g., 'best_value_kg', 'relevance')."},
                                        "store_ids": {"type": "string", "description": "Comma-separated list of store IDs to filter products by availability."},
                                    },
                                    "required": ["q"],
                                },
                            },
                            "required": ["name", "arguments"],
                        },
                        "description": "A list of tool calls to execute. Each item should be a dictionary with 'name' (the tool function name) and 'arguments' (a dictionary of arguments for that tool).",
                    }
                },
                "required": ["queries"],
            },
        }
    ]),
    genai.types.Tool(function_declarations=[
        {
            "name": "get_seasonal_product_deals_v2",
            "description": "Finds the lowest seasonal price for generic products across all chains that match the canonical name and category and are currently in season.",
            "parameters": {
                "type": "object",
                "properties": {
                    "canonical_name": {"type": "string", "description": "The canonical name of the generic product (e.g., 'Crvene Naranče')."},
                    "category": {"type": "string", "description": "The category of the product (e.g., 'Voće i povrće')."},
                    "current_month": {"type": "integer", "description": "The current month (1-12)."},
                    "limit": {"type": "integer", "description": "Maximum number of results to return."},
                    "offset": {"type": "integer", "description": "Number of results to skip."},
                },
                "required": ["canonical_name", "category"],
            },
        }
    ]),
    genai.types.Tool(function_declarations=[
        {
            "name": "find_nearby_stores_for_user",
            "description": "Finds nearby stores for a given user by first retrieving their primary saved location and then searching around that location.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "The ID of the user."},
                    "radius_meters": {"type": "integer", "description": "Radius in meters to search within."},
                },
                "required": ["user_id"],
            },
        }
    ]),
]


gemini_tools_old = [
    genai.types.Tool(function_declarations=[
        {
            "name": "search_products_v2",
            "description": "Search for products by name using hybrid search (vector + keyword) and advanced sorting.",
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {"type": "string", "description": "The user's natural language query."},
                    "limit": {"type": "integer", "description": "Maximum number of results to return."},
                    "offset": {"type": "integer", "description": "Number of results to skip."},
                    "sort_by": {"type": "string", "description": "How to sort the results (e.g., 'best_value_kg', 'relevance')."},
                    "store_ids": {"type": "string", "description": "Comma-separated list of store IDs to filter products by availability."},
                },
                "required": ["q"], # Only 'q' is strictly required
            },
        }
    ]),
    genai.types.Tool(function_declarations=[
        {
            "name": "get_product_prices_by_location_v2",
            "description": "Finds the prices for a single product at a list of specific stores.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer", "description": "The ID of the product."},
                    "store_ids": {"type": "string", "description": "A comma-separated list of store IDs, e.g., 101,105,230."},
                },
                "required": ["product_id", "store_ids"],
            },
        }
    ]),
    genai.types.Tool(function_declarations=[
        {
            "name": "get_product_details_v2",
            "description": "Retrieves the full details for a single product.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer", "description": "The ID of the product."},
                },
                "required": ["product_id"],
            },
        }
    ]),
    genai.types.Tool(function_declarations=[
        {
            "name": "find_nearby_stores_v2",
            "description": "Finds stores within a specified radius of a geographic point using the 'stores' table.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number", "format": "float", "description": "Latitude of the center point."},
                    "lon": {"type": "number", "format": "float", "description": "Longitude of the center point."},
                    "radius_meters": {"type": "integer", "description": "Radius in meters to search within."},
                    "chain_code": {"type": "string", "description": "Optional: Filter by a specific chain."},
                },
                "required": ["lat", "lon"],
            },
        }
    ]),
    genai.types.Tool(function_declarations=[
        {
            "name": "multi_search_tool",
            "description": "Executes multiple product search queries concurrently and returns all results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "The name of the tool function to call."},
                                "arguments": {
                                    "type": "object",
                                    "description": "The arguments to pass to the tool. Must match the schema of the target tool.",
                                    "properties": {
                                        "q": {"type": "string", "description": "The user's natural language query."},
                                        "limit": {"type": "integer", "description": "Maximum number of results to return."},
                                        "offset": {"type": "integer", "description": "Number of results to skip."},
                                        "sort_by": {"type": "string", "description": "How to sort the results (e.g., 'best_value_kg', 'relevance')."},
                                        "store_ids": {"type": "string", "description": "Comma-separated list of store IDs to filter products by availability."},
                                    },
                                    "required": ["q"],
                                },
                            },
                            "required": ["name", "arguments"],
                        },
                        "description": "A list of tool calls to execute. Each item should be a dictionary with 'name' (the tool function name) and 'arguments' (a dictionary of arguments for that tool).",
                    }
                },
                "required": ["queries"],
            },
        }
    ]),
    genai.types.Tool(function_declarations=[
        {
            "name": "get_seasonal_product_deals_v2",
            "description": "Finds the lowest seasonal price for generic products across all chains that match the canonical name and category and are currently in season.",
            "parameters": {
                "type": "object",
                "properties": {
                    "canonical_name": {"type": "string", "description": "The canonical name of the generic product (e.g., 'Crvene Naranče')."},
                    "category": {"type": "string", "description": "The category of the product (e.g., 'Voće i povrće')."},
                    "current_month": {"type": "integer", "description": "The current month (1-12)."},
                    "limit": {"type": "integer", "description": "Maximum number of results to return."},
                    "offset": {"type": "integer", "description": "Number of results to skip."},
                },
                "required": ["canonical_name", "category"],
            },
        }
    ]),
    genai.types.Tool(function_declarations=[
        {
            "name": "find_nearby_stores_for_user",
            "description": "Finds nearby stores for a given user by first retrieving their primary saved location and then searching around that location.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "The ID of the user."},
                    "radius_meters": {"type": "integer", "description": "Radius in meters to search within."},
                },
                "required": ["user_id"],
            },
        }
    ]),
]

openai_tools = []
