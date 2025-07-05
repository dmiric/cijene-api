from pydantic import BaseModel, Field
from typing import Optional
from uuid import UUID
import datetime
from decimal import Decimal
from datetime import date
from dataclasses import asdict, fields, is_dataclass

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
gemini_tools = [
    {
        # <<< UPDATED TOOL >>>
        # This tool is updated to require a 'caption' for each sub-query.
        "function_declarations": [
            {
                "name": "multi_search_tool",
                "description": "Omogućuje izvršavanje više upita za pretraživanje proizvoda odjednom. Svaki upit mora imati naslov (caption) koji će se prikazati korisniku.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "queries": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "caption": {
                                        "type": "string",
                                        "description": "Kratki, korisniku vidljiv naslov za ovu grupu pretrage (npr. 'Najbolja Vrijednost', 'Bio Izbor'). Mora biti na hrvatskom jeziku."
                                    },
                                    "name": {
                                        "type": "string",
                                        "description": "Naziv alata za pozivanje (uvijek 'search_products_v2')."
                                    },
                                    "arguments": {
                                        "type": "object",
                                        "description": "Argumenti za prosljeđivanje alatu search_products_v2."
                                    }
                                },
                                "required": ["caption", "name", "arguments"]
                            },
                            "description": "Popis poziva alata za izvršavanje. Svaki poziv mora imati 'caption', 'name' i 'arguments'."
                        }
                    },
                    "required": ["queries"],
                },
            }
        ]
    },
    {
        # <<< SIMPLIFIED TOOL >>>
        # This tool reflects your change, removing 'brand' and 'category'.
        "function_declarations": [
            {
                "name": "search_products_v2",
                "description": "Pretraživanje proizvoda pomoću hibridne pretrage (vektor + ključna riječ) i naprednog sortiranja.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "q": {"type": "string", "description": "Korisnikov upit prirodnim jezikom. Može sadržavati marku, kategoriju i druge atribute."},
                        "limit": {"type": "integer", "description": "Maksimalan broj rezultata za povratak."},
                        "offset": {"type": "integer", "description": "Broj rezultata za preskakanje."},
                        "sort_by": {"type": "string", "description": "Neobavezno. Vrijednosti: 'relevance', 'best_value_kg', 'best_value_l', 'best_value_piece'."},
                    },
                    "required": ["q"],
                },
            }
        ]
    },
    {
        # --- Unchanged Tools Below ---
        "function_declarations": [
            {
                "name": "get_product_prices_by_location_v2",
                "description": "Pronalazi cijene za jedan proizvod na popisu određenih trgovina.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "product_id": {"type": "integer", "description": "ID proizvoda."},
                        "store_ids": {"type": "string", "description": "Popis ID-ova trgovina odvojenih zarezima, npr. 101,105,230."},
                    },
                    "required": ["product_id", "store_ids"],
                },
            }
        ]
    },
    {
        "function_declarations": [
            {
                "name": "get_product_details_v2",
                "description": "Dohvaća potpuni 'zlatni zapis' za jedan proizvod.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "product_id": {"type": "integer", "description": "ID proizvoda."},
                    },
                    "required": ["product_id"],
                },
            }
        ]
    },
    {
        "function_declarations": [
            {
                "name": "find_nearby_stores_v2",
                "description": "Pronalazi trgovine unutar određenog radijusa od zemljopisne točke.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "lat": {"type": "number", "format": "float", "description": "Zemljopisna širina središnje točke."},
                        "lon": {"type": "number", "format": "float", "description": "Zemljopisna dužina središnje točke."},
                        "radius_meters": {"type": "integer", "description": "Radijus u metrima za pretraživanje."},
                        "chain_code": {"type": "string", "description": "Neobavezno: Filtriranje po kodu lanca."},
                    },
                    "required": ["lat", "lon"],
                },
            }
        ]
    },
    {
        "function_declarations": [
            {
                "name": "get_user_locations",
                "description": "Dohvaća spremljene lokacije korisnika.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "integer", "description": "ID korisnika."},
                    },
                    "required": ["user_id"],
                },
            }
        ]
    },
]

openai_tools = [
    {
        "type": "function",
        "function": {
            "name": "multi_search_tool",
            "description": "Omogućuje izvršavanje više upita za pretraživanje proizvoda odjednom i vraćanje svih rezultata. Koristite ovo kada korisnik traži više vrsta informacija o proizvodima koje se mogu dohvatiti različitim upitima (npr. najjeftinije po pakiranju i najjeftinije po komadu).",
            "parameters": {
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Naziv alata za pozivanje (npr. 'search_products_v2', 'get_product_prices_by_location_v2')."},
                                "arguments": {"type": "object", "description": "Argumenti za prosljeđivanje alatu. Moraju odgovarati shemi argumenata ciljanog alata."},
                            },
                            "required": ["name", "arguments"],
                        },
                        "description": "Popis poziva alata za izvršavanje. Svaki poziv alata mora imati 'name' i 'arguments'.",
                    }
                },
                "required": ["queries"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_products_v2",
            "description": "Pretraživanje proizvoda pomoću hibridne pretrage (vektor + ključna riječ) i naprednog sortiranja.",
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {"type": "string", "description": "Korisnikov upit prirodnim jezikom."},
                    "limit": {"type": "integer", "description": "Maksimalan broj rezultata za povratak."},
                    "offset": {"type": "integer", "description": "Broj rezultata za preskakanje."},
                    "sort_by": {"type": "string", "description": "Neobavezno. Vrijednosti: 'relevance', 'best_value_kg', 'best_value_l', 'best_value_piece'."},
                },
                "required": ["q"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_prices_by_location_v2",
            "description": "Pronalazi cijene za jedan proizvod na popisu određenih trgovina.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer", "description": "ID proizvoda."},
                    "store_ids": {"type": "string", "description": "Popis ID-ova trgovina odvojenih zarezima, npr. 101,105,230."},
                },
                "required": ["product_id", "store_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_details_v2",
            "description": "Dohvaća potpuni 'zlatni zapis' za jedan proizvod.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer", "description": "ID proizvoda."},
                },
                "required": ["product_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_nearby_stores_v2",
            "description": "Pronalazi trgovine unutar određenog radijusa od zemljopisne točke.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number", "format": "float", "description": "Zemljopisna širina središnje točke."},
                    "lon": {"type": "number", "format": "float", "description": "Zemljopisna dužina središnje točke."},
                    "radius_meters": {"type": "integer", "description": "Radijus u metrima za pretraživanje."},
                    "chain_code": {"type": "string", "description": "Neobavezno: Filtriranje po kodu lanca."},
                },
                "required": ["lat", "lon"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_user_locations",
            "description": "Dohvaća spremljene lokacije korisnika.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "integer", "description": "ID korisnika."},
                },
                "required": ["user_id"],
            },
        },
    },
]
