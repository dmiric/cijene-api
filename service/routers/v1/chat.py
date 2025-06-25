print(">>> Importing chat.py")
from decimal import Decimal
from fastapi import APIRouter, HTTPException, Query, status, Request
from pydantic import BaseModel, Field
import datetime
import sys
from dataclasses import asdict
from typing import AsyncGenerator, Optional
import json
from uuid import UUID, uuid4
import os

# AI imports
import google.generativeai as genai
from openai import OpenAI

from service.config import settings
from service.routers.v1.initial_context import INITIAL_SYSTEM_INSTRUCTIONS
from service.db.models import ChainStats, ProductWithId, StorePrice, User, UserLocation, ChatMessage, UserPreference
from service.routers.auth import verify_authentication
from fastapi.responses import StreamingResponse
from fastapi import Depends

router = APIRouter(tags=["AI Chat"], dependencies=[Depends(verify_authentication)])
db = settings.get_db()

# Load environment variables for AI API keys
from dotenv import load_dotenv
load_dotenv()

# Using print for debugging as logging is not appearing reliably
def debug_print(*args, **kwargs):
    print("[DEBUG chat]", *args, file=sys.stderr, **kwargs)

# Initialize AI clients
# Google Gemini
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
    gemini_client = genai.GenerativeModel('gemini-2.5-flash-preview-05-20')
else:
    debug_print("GOOGLE_API_KEY nije pronađen. Gemini klijent nije inicijaliziran.")
    gemini_client = None

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if OPENAI_API_KEY:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
else:
    debug_print("OPENAI_API_KEY nije pronađen. OpenAI klijent nije inicijaliziran.")
    openai_client = None


class ChatRequest(BaseModel):
    user_id: int = Field(..., description="ID of the user initiating the chat.")
    session_id: Optional[UUID] = Field(None, description="Optional: UUID of the chat session to continue. If not provided, a new session will be started.")
    message_text: str = Field(..., description="The user's message.")


class ChatMessageResponse(BaseModel):
    id: UUID
    user_id: int
    session_id: UUID
    sender: str
    message_text: str
    timestamp: datetime.datetime
    tool_calls: Optional[dict] = None
    tool_outputs: Optional[dict] = None


# Helper function to convert Pydantic models to dict for tool output
def pydantic_to_dict(obj):
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode='json')
    elif isinstance(obj, list):
        return [pydantic_to_dict(item) for item in obj]
    elif isinstance(obj, dict):
        return {k: pydantic_to_dict(v) for k, v in obj.items()}
    return obj


# Define tools for AI models
async def search_products_tool(q: str, date: Optional[str] = None, chains: Optional[str] = None, store_ids: Optional[str] = None):
    """
    Search for products by name.
    Args:
        q (str): Search query for product names.
        date (Optional[str]): Date in YYYY-MM-DD format, defaults to today.
        chains (Optional[str]): Comma-separated list of chain codes to include.
        store_ids (Optional[str]): Comma-separated list of store IDs to filter by.
    """
    debug_print(f"Tool Call: search_products_tool(q={q}, date={date}, chains={chains}, store_ids={store_ids})")
    try:
        # Convert date string to datetime.date object if provided
        parsed_date = datetime.datetime.strptime(date, "%Y-%m-%d").date() if date else None
        
        # Call the existing FastAPI endpoint logic directly
        # NOTE: This will require importing search_products from the new products.py
        from .products import search_products as original_search_products
        response = await original_search_products(q=q, date=parsed_date, chains=chains, store_ids=store_ids)
        return pydantic_to_dict(response)
    except HTTPException as e:
        return {"error": e.detail, "status_code": e.status_code}
    except Exception as e:
        return {"error": str(e)}


async def list_nearby_stores_tool(lat: float, lon: float, radius_meters: int, chain_code: Optional[str] = None):
    """
    Finds and lists stores within a specified radius of a given lat/lon.
    Results are ordered by distance from the center point.
    Args:
        lat (float): Latitude of the center point.
        lon (float): Longitude of the center point.
        radius_meters (int): Radius in meters to search within.
        chain_code (Optional[str]): Optional: Filter by chain code.
    """
    debug_print(f"Tool Call: list_nearby_stores_tool(lat={lat}, lon={lon}, radius_meters={radius_meters}, chain_code={chain_code})")
    try:
        # Call the existing FastAPI endpoint logic directly
        # NOTE: This will require importing list_nearby_stores from the new stores.py
        from .stores import list_nearby_stores as original_list_nearby_stores
        response = await original_list_nearby_stores(lat=Decimal(str(lat)), lon=Decimal(str(lon)), radius_meters=radius_meters, chain_code=chain_code)
        return pydantic_to_dict(response)
    except HTTPException as e:
        return {"error": e.detail, "status_code": e.status_code}
    except Exception as e:
        return {"error": str(e)}


async def save_shopping_preference_tool(user_id: int, preference_key: str, preference_value: str):
    """
    Saves a user's shopping preference.
    Use this when the user explicitly states a preference like "I only want full fat milk" or "I only buy dark chocolate".
    Args:
        user_id (int): The ID of the user for whom to save the preference.
        preference_key (str): A concise key for the preference (e.g., "milk_type", "chocolate_type", "brand").
        preference_value (str): The specific value of the preference (e.g., "full_fat", "dark", "Nestle").
    """
    debug_print(f"Tool Call: save_shopping_preference_tool(user_id={user_id}, preference_key={preference_key}, preference_value={preference_value})")
    try:
        # Call the database layer directly
        await db.save_user_preference(user_id, preference_key, preference_value)
        return {"status": "success", "message": f"Preference '{preference_key}: {preference_value}' saved."}
    except Exception as e:
        return {"error": str(e)}


async def get_user_locations_tool(user_id: int):
    """
    Retrieves a user's saved locations.
    Args:
        user_id (int): The ID of the user.
    """
    debug_print(f"Tool Call: get_user_locations_tool(user_id={user_id})")
    try:
        # Call the existing FastAPI endpoint logic directly
        # NOTE: This will require importing list_user_locations from the new users.py
        from .users import list_user_locations as original_list_user_locations
        # We need to mock the current_user dependency for this internal call
        # For now, let's directly call the db method as it's simpler and avoids circular deps
        locations = await db.get_user_locations_by_user_id(user_id)
        return pydantic_to_dict({"locations": locations}) # Wrap in a dict to match ListUserLocationsResponse structure
    except Exception as e:
        return {"error": str(e)}


# Map tool names to their Python functions
available_tools = {
    "search_products": search_products_tool,
    "list_nearby_stores": list_nearby_stores_tool,
    "save_shopping_preference": save_shopping_preference_tool,
    "get_user_locations": get_user_locations_tool,
}

# Define tool schemas for AI models
gemini_tools = [
    {
        "function_declarations": [
            {
                "name": "search_products",
                "description": "Pretraživanje proizvoda po nazivu.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "q": {"type": "string", "description": "Upit za pretraživanje naziva proizvoda."},
                        "date": {"type": "string", "format": "date-time", "description": "Datum u formatu GGGG-MM-DD, zadano je današnji datum."},
                        "chains": {"type": "string", "description": "Popis kodova lanaca odvojenih zarezima za uključivanje."},
                        "store_ids": {"type": "string", "description": "Popis ID-ova trgovina odvojenih zarezima za filtriranje."},
                    },
                    "required": ["q"],
                },
            }
        ]
    },
    {
        "function_declarations": [
            {
                "name": "list_nearby_stores",
                "description": "Pronalazi i popisuje trgovine unutar određenog radijusa od zadane zemljopisne širine/dužine. Rezultati su poredani po udaljenosti od središnje točke.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "lat": {"type": "number", "format": "float", "description": "Zemljopisna širina središnje točke."},
                        "lon": {"type": "number", "format": "float", "description": "Zemljopisna dužina središnje točke."},
                        "radius_meters": {"type": "integer", "description": "Radijus u metrima za pretraživanje."},
                        "chain_code": {"type": "string", "description": "Neobavezno: Filtriranje po kodu lanca."},
                    },
                    "required": ["lat", "lon", "radius_meters"],
                },
            }
        ]
    },
    {
        "function_declarations": [
            {
                "name": "save_shopping_preference",
                "description": "Sprema korisničke preferencije kupnje. Koristite ovo kada korisnik izričito navede preferenciju poput 'Želim samo punomasno mlijeko' ili 'Kupujem samo tamnu čokoladu'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "integer", "description": "ID korisnika za kojeg se sprema preferencija."},
                        "preference_key": {"type": "string", "description": "Kratki ključ za preferenciju (npr. 'vrsta_mlijeka', 'vrsta_čokolade', 'marka')."},
                        "preference_value": {"type": "string", "description": "Specifična vrijednost preferencije (npr. 'punomasno', 'tamna', 'Nestle')."},
                    },
                    "required": ["user_id", "preference_key", "preference_value"],
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
            "name": "search_products",
            "description": "Pretraživanje proizvoda po nazivu.",
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {"type": "string", "description": "Upit za pretraživanje naziva proizvoda."},
                    "date": {"type": "string", "format": "date-time", "description": "Datum u formatu GGGG-MM-DD, zadano je današnji datum."},
                    "chains": {"type": "string", "description": "Popis kodova lanaca odvojenih zarezima za uključivanje."},
                    "store_ids": {"type": "string", "description": "Popis ID-ova trgovina odvojenih zarezima za filtriranje."},
                },
                "required": ["q"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_nearby_stores",
            "description": "Pronalazi i popisuje trgovine unutar određenog radijusa od zadane zemljopisne širine/dužine. Rezultati su poredani po udaljenosti od središnje točke.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number", "format": "float", "description": "Zemljopisna širina središnje točke."},
                    "lon": {"type": "number", "format": "float", "description": "Zemljopisna dužina središnje točke."},
                    "radius_meters": {"type": "integer", "description": "Radijus u metrima za pretraživanje."},
                    "chain_code": {"type": "string", "description": "Neobavezno: Filtriranje po kodu lanca."},
                },
                "required": ["lat", "lon", "radius_meters"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_shopping_preference",
            "description": "Sprema korisničke preferencije kupnje. Koristite ovo kada korisnik izričito navede preferenciju poput 'Želim samo punomasno mlijeko' ili 'Kupujem samo tamnu čokoladu'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "integer", "description": "ID korisnika za kojeg se sprema preferencija."},
                    "preference_key": {"type": "string", "description": "Kratki ključ za preferenciju (npr. 'vrsta_mlijeka', 'vrsta_čokolade', 'marka')."},
                    "preference_value": {"type": "string", "description": "Specifična vrijednost preferencije (npr. 'punomasno', 'tamna', 'Nestle')."},
                },
                "required": ["user_id", "preference_key", "preference_value"],
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


@router.post("/chat", summary="Handle AI chat interactions with streaming responses")
async def chat_endpoint(chat_request: ChatRequest) -> StreamingResponse:
    """
    Handles incoming chat messages, orchestrates AI interactions, and streams responses.
    """
    # Temporarily hardcode user_id for testing without authentication
    user_id = chat_request.user_id # Use user_id from request body
    session_id = chat_request.session_id if chat_request.session_id else uuid4()
    user_message_text = chat_request.message_text

    debug_print(f"Chat request received: user_id={user_id}, session_id={session_id}, message='{user_message_text}'")

    # Save user message to DB
    user_chat_message = ChatMessage(
        id=str(uuid4()),
        user_id=user_id,
        session_id=str(session_id),
        sender="user",
        message_text=user_message_text,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
        tool_calls=None,
        tool_outputs=None,
    )
    await db.save_chat_message(user_chat_message)

    # Retrieve chat history
    history = await db.get_chat_messages(user_id, session_id, limit=20) # Fetch last 20 messages
    debug_print(f"Retrieved {len(history)} messages for session {session_id}")

    # Define system message
    system_message_content = "\n".join(INITIAL_SYSTEM_INSTRUCTIONS)

    # Format history for AI model
    ai_history = []
    # Add system messages as the first entries
    for instruction in INITIAL_SYSTEM_INSTRUCTIONS:
        if openai_client: # For OpenAI, use the dedicated 'system' role
            ai_history.append({"role": "system", "content": instruction})
        elif gemini_client: # For Gemini, we can try adding it as a user message for initial context
            ai_history.append({"role": "user", "parts": [instruction]})
            # For Gemini, add a model response to acknowledge the system instruction
            ai_history.append({"role": "model", "parts": ["Razumijem. Kako vam mogu pomoći?"]})

    for msg in history:
        if msg.sender == "user":
            ai_history.append({"role": "user", "parts": [msg.message_text]})
        elif msg.sender == "ai":
            ai_history.append({"role": "model", "parts": [msg.message_text]})
        elif msg.sender == "tool_call" and msg.tool_calls:
            # Gemini expects tool_code for tool calls
            ai_history.append({"role": "model", "parts": [{"functionCall": msg.tool_calls}]})
        elif msg.sender == "tool_output" and msg.tool_outputs:
            # Gemini expects functionResponse for tool outputs
            ai_history.append({"role": "function", "name": msg.tool_outputs.get("name"), "content": msg.tool_outputs.get("content")})
    
    # Add current user message to history for the AI call
    ai_history.append({"role": "user", "parts": [user_message_text]})

    async def event_stream():
        full_ai_response_text = ""
        tool_call_occurred = False
        tool_call_info = None
        tool_output_info = None

        try:
            # Initial AI call
            if gemini_client:
                debug_print("Calling Gemini API...")
                response_stream = gemini_client.generate_content(
                    ai_history,
                    tools=gemini_tools,
                    stream=True
                )
            elif openai_client:
                debug_print("Calling OpenAI API...")
                # OpenAI expects messages in a different format
                openai_messages = []
                for msg in ai_history:
                    if msg["role"] == "user":
                        openai_messages.append({"role": "user", "content": msg["parts"][0]})
                    elif msg["role"] == "model":
                        if "functionCall" in msg["parts"][0]:
                            openai_messages.append({"role": "assistant", "tool_calls": [
                                {"id": "call_id_placeholder", "function": {"name": msg["parts"][0]["functionCall"]["name"], "arguments": json.dumps(msg["parts"][0]["functionCall"]["args"])}}
                            ]})
                        else:
                            openai_messages.append({"role": "assistant", "content": msg["parts"][0]})
                    elif msg["role"] == "function":
                        openai_messages.append({"role": "tool", "tool_call_id": "call_id_placeholder", "content": json.dumps(msg["content"])})
                    elif msg["role"] == "system": # Handle system messages for OpenAI
                        openai_messages.append({"role": "system", "content": msg["parts"][0]})


                response_stream = openai_client.chat.completions.create(
                    model="gpt-3.5-turbo", # Or gpt-4-turbo, etc.
                    messages=openai_messages,
                    tools=openai_tools,
                    tool_choice="auto",
                    stream=True
                )
            else:
                yield f"data: {json.dumps({'type': 'error', 'content': 'Nijedan AI klijent nije inicijaliziran.'})}\n\n"
                return

            for chunk in response_stream:
                if gemini_client:
                    # Handle Gemini response
                    if chunk.candidates and chunk.candidates[0].content.parts:
                        for part in chunk.candidates[0].content.parts:
                            if part.function_call:
                                tool_call_occurred = True
                                tool_call_info = {
                                    "name": part.function_call.name,
                                    "args": {k: v for k, v in part.function_call.args.items()}
                                }
                                debug_print(f"AI requested tool call: {tool_call_info}")
                                yield f"data: {json.dumps({'type': 'tool_call', 'content': tool_call_info})}\n\n"
                            elif part.text:
                                full_ai_response_text += part.text
                                yield f"data: {json.dumps({'type': 'text', 'content': part.text})}\n\n"
                elif openai_client:
                    # Handle OpenAI response
                    if chunk.choices:
                        delta = chunk.choices[0].delta
                        if delta.content:
                            full_ai_response_text += delta.content
                            yield f"data: {json.dumps({'type': 'text', 'content': delta.content})}\n\n"
                        if delta.tool_calls:
                            tool_call_occurred = True
                            # OpenAI can send tool_calls in chunks, need to reconstruct
                            for tc in delta.tool_calls:
                                if tc.function:
                                    tool_call_info = {
                                        "name": tc.function.name,
                                        "args": json.loads(tc.function.arguments) if tc.function.arguments else {}
                                    }
                                    debug_print(f"AI requested tool call: {tool_call_info}")
                                    yield f"data: {json.dumps({'type': 'tool_call', 'content': tool_call_info})}\n\n"

            # After streaming initial response, if a tool call occurred, execute it
            if tool_call_occurred and tool_call_info:
                tool_name = tool_call_info["name"]
                tool_args = tool_call_info["args"]
                
                # Save tool call message to DB
                tool_call_chat_message = ChatMessage(
                    id=str(uuid4()),
                    user_id=user_id,
                    session_id=str(session_id),
                    sender="tool_call",
                    message_text=f"Tool call: {tool_name}({tool_args})",
                    timestamp=datetime.datetime.now(datetime.timezone.utc),
                    tool_calls={"name": tool_name, "args": tool_args},
                    tool_outputs=None,
                )
                await db.save_chat_message(tool_call_chat_message)

                if tool_name in available_tools:
                    debug_print(f"Executing tool: {tool_name} with args: {tool_args}")
                    tool_output = await available_tools[tool_name](**tool_args)
                    debug_print(f"Tool output: {tool_output}")
                    tool_output_info = {"name": tool_name, "content": tool_output}
                    yield f"data: {json.dumps({'type': 'tool_output', 'content': tool_output_info})}\n\n"

                    # Save tool output message to DB
                    tool_output_chat_message = ChatMessage(
                        id=str(uuid4()),
                        user_id=user_id,
                        session_id=str(session_id),
                        sender="tool_output",
                        message_text=f"Tool output for {tool_name}: {tool_output}",
                        timestamp=datetime.datetime.now(datetime.timezone.utc),
                        tool_calls=None,
                        tool_outputs=tool_output_info,
                    )
                    await db.save_chat_message(tool_output_chat_message)

                    # --- Location-based search orchestration ---
                    # If the AI called get_user_locations, now call list_nearby_stores
                    if tool_name == "get_user_locations":
                        locations = tool_output_info["content"]
                        if locations and len(locations.get("locations", [])) > 0:
                            # Use the first location found
                            first_location = locations["locations"][0]
                            lat = first_location.get("latitude")
                            lon = first_location.get("longitude")

                            if lat is not None and lon is not None:
                                debug_print(f"Found user location: lat={lat}, lon={lon}. Calling list_nearby_stores_tool...")
                                nearby_stores_output = await list_nearby_stores_tool(lat=float(lat), lon=float(lon), radius_meters=1500)
                                debug_print(f"Nearby stores output: {nearby_stores_output}")

                                # Extract store IDs
                                store_ids_list = [store["id"] for store in nearby_stores_output.get("stores", [])]
                                if store_ids_list:
                                    store_ids_str = ",".join(map(str, store_ids_list))
                                    debug_print(f"Extracted store IDs: {store_ids_str}. Now calling search_products_tool...")

                                    # Send tool output (nearby stores) to AI
                                    ai_history.append({"role": "function", "name": "list_nearby_stores", "content": nearby_stores_output})
                                    
                                    # Make another AI call with original user message and store IDs
                                    # This is a critical step to guide the AI to use the store_ids
                                    # We need to tell the AI to search products with these store IDs
                                    # This might require a more complex prompt or a new tool for "search_products_in_stores"
                                    # For now, we'll try to force the AI to use it in the next turn.
                                    
                                    # Re-add the original user message and then the tool output
                                    # This is a simplified approach; a more robust solution might involve
                                    # a state machine or more explicit AI prompting.
                                    ai_history.append({"role": "user", "parts": [user_message_text]}) # Re-add original query
                                    ai_history.append({"role": "function", "name": "get_user_locations", "content": tool_output_info}) # Original location tool output
                                    ai_history.append({"role": "function", "name": "list_nearby_stores", "content": nearby_stores_output}) # Nearby stores tool output

                                    # Now, make a follow-up AI call, expecting it to use search_products with store_ids
                                    # This part is tricky as AI might not directly infer to use store_ids.
                                    # A better approach might be to have a dedicated tool for "search_products_in_stores"
                                    # or to explicitly tell the AI in the prompt to use store_ids if available.
                                    
                                    # For now, let's try to make a follow-up call with the updated history
                                    # and hope the AI uses the store_ids.
                                    
                                    # This is a recursive call to the AI, effectively a multi-turn tool use.
                                    # The AI should now have enough context to call search_products with store_ids.
                                    
                                    # To avoid infinite loops, we need to be careful.
                                    # For simplicity, I'll make a direct call to search_products_tool here
                                    # if the AI doesn't call it, or let the AI call it in the next turn.
                                    
                                    # Let's try to make the AI call search_products with store_ids in the next turn.
                                    # The current ai_history already contains the necessary info.
                                    
                                    # No direct call to search_products_tool here.
                                    # The AI will make the decision in the next turn.
                                    pass # Continue to the final AI call below
                                else:
                                    debug_print("No nearby stores found. Informing user.")
                                    full_ai_response_text = "Nisam pronašao/pronašla trgovine u blizini vaše lokacije. Pokušajte s drugom lokacijom ili proširite pretragu."
                            else:
                                debug_print("User location found but missing lat/lon. Informing user.")
                                full_ai_response_text = "Pronašao/pronašla sam vašu spremljenu lokaciju, ali nedostaju zemljopisna širina i dužina. Ažurirajte detalje lokacije kako biste omogućili pretraživanje temeljeno na lokaciji."
                        else:
                            debug_print("No user locations found. Informing user.")
                            full_ai_response_text = "Nisam pronašao/pronašla spremljene lokacije za vas. Dodajte lokaciju kako biste omogućili pretraživanje temeljeno na lokaciji."
                    
                    # Make another AI call with tool output to get a natural language response
                    # This is the general follow-up for any tool call.
                    # If get_user_locations was called, the logic above might have already set full_ai_response_text
                    # or prepared for a subsequent AI call.
                    
                    # If full_ai_response_text is already set (e.g., no locations found), skip this.
                    if not full_ai_response_text:
                        debug_print("Making follow-up AI call with tool output...")
                        follow_up_response_text = ""
                        if gemini_client:
                            follow_up_stream = gemini_client.generate_content(
                                ai_history,
                                tools=gemini_tools,
                                stream=True
                            )
                        elif openai_client:
                            # Reconstruct messages for OpenAI
                            openai_messages_follow_up = []
                            for msg in ai_history:
                                if msg["role"] == "user":
                                    openai_messages_follow_up.append({"role": "user", "content": msg["parts"][0]})
                                elif msg["role"] == "model":
                                    if "functionCall" in msg["parts"][0]:
                                        openai_messages_follow_up.append({"role": "assistant", "tool_calls": [
                                            {"id": "call_id_placeholder", "function": {"name": msg["parts"][0]["functionCall"]["name"], "arguments": json.dumps(msg["parts"][0]["functionCall"]["args"])}}
                                        ]})
                                    else:
                                        openai_messages_follow_up.append({"role": "assistant", "content": msg["parts"][0]})
                                elif msg["role"] == "function":
                                    openai_messages_follow_up.append({"role": "tool", "tool_call_id": "call_id_placeholder", "content": json.dumps(msg["content"])})
                                elif msg["role"] == "system": # Handle system messages for OpenAI
                                    openai_messages_follow_up.append({"role": "system", "content": msg["parts"][0]})

                            follow_up_stream = openai_client.chat.completions.create(
                                model="gpt-3.5-turbo",
                                messages=openai_messages_follow_up,
                                tools=openai_tools,
                                tool_choice="auto",
                                stream=True
                            )
                        else:
                            yield f"data: {json.dumps({'type': 'error', 'content': 'Nijedan AI klijent nije inicijaliziran za nastavak.'})}\n\n"
                            return

                        for follow_up_chunk in follow_up_stream:
                            if gemini_client:
                                if follow_up_chunk.candidates and follow_up_chunk.candidates[0].content.parts:
                                    for part in follow_up_chunk.candidates[0].content.parts:
                                        if part.text:
                                            follow_up_response_text += part.text
                                            yield f"data: {json.dumps({'type': 'text', 'content': part.text})}\n\n"
                            elif openai_client:
                                if follow_up_chunk.choices:
                                    delta = follow_up_chunk.choices[0].delta
                                    if delta.content:
                                        follow_up_response_text += delta.content
                                        yield f"data: {json.dumps({'type': 'text', 'content': delta.content})}\n\n"
                        full_ai_response_text = follow_up_response_text # Update full response for saving

                else:
                    error_message = f"Alat '{tool_name}' nije pronađen."
                    debug_print(error_message)
                    yield f"data: {json.dumps({'type': 'error', 'content': error_message})}\n\n"
                    full_ai_response_text = error_message

            # Save AI's final text response to DB
            if full_ai_response_text:
                ai_chat_message = ChatMessage(
                    id=str(uuid4()),
                    user_id=user_id,
                    session_id=str(session_id),
                    sender="ai",
                    message_text=full_ai_response_text,
                    timestamp=datetime.datetime.now(datetime.timezone.utc),
                    tool_calls=None,
                    tool_outputs=None,
                )
                await db.save_chat_message(ai_chat_message)
            
            yield f"data: {json.dumps({'type': 'end', 'session_id': str(session_id)})}\n\n"

        except Exception as e:
            debug_print(f"Error in chat_endpoint: {e}")
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
print("<<< Finished importing in chat.py")
