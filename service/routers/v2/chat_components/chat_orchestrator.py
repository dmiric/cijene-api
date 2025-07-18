# service/routers/v2/chat_components/chat_orchestrator.py

import json
from uuid import UUID, uuid4
from typing import AsyncGenerator, Optional, List
from datetime import datetime, timezone

# --- DEBUGGING IMPORT ---
import sys

from .ai_providers import get_ai_provider, StreamedPart, AbstractAIProvider, to_json_primitive
from .ai_tools import available_tools
from service.db.base import Database
from service.db.models import ChatMessage
from service.utils.timing import debug_print


from .ai_schemas import LocationInfo # Add this import

class ChatOrchestrator:
    def __init__(self, user_id: UUID, session_id: UUID, db: Database, system_instructions: list[str], location_info: Optional[LocationInfo] = None):
        self.user_id = user_id
        self.session_id = session_id
        self.db = db
        self.system_instructions = system_instructions
        self.location_info = location_info # Store location_info
        self.ai_provider: AbstractAIProvider = get_ai_provider(
            db=self.db, user_id=self.user_id, session_id=self.session_id
        )
        self.full_ai_response_text = ""
        self.history: list[ChatMessage] = []

    async def _get_location_context_with_nearby_stores(self) -> List[dict]:
        """
        Retrieves location context (either from chat request or user's saved locations)
        and appends nearby stores to each location.
        """
        locations_to_process = []

        if self.location_info:
            # Prioritize location_info from the current chat request
            loc_dict = {
                "latitude": self.location_info.latitude,
                "longitude": self.location_info.longitude,
                "id": self.location_info.locationId if self.location_info.locationId else None, # Changed to int
                "is_current_location": True # Mark this as the current location from the request
            }
            locations_to_process.append(loc_dict)
            print(f"!!! ORCHESTRATOR: Using location from chat request: {loc_dict}", file=sys.stderr, flush=True)
        else:
            # Fallback to user's saved locations
            user_locations = await self.db.users.get_user_locations_by_user_id(self.user_id)
            print(f"!!! ORCHESTRATOR: User Locations: {user_locations}", file=sys.stderr, flush=True)
            for loc in user_locations:
                loc_dict = loc.copy()
                if "user_id" in loc_dict and isinstance(loc_dict["user_id"], UUID):
                    loc_dict["user_id"] = str(loc_dict["user_id"])
                if "id" in loc_dict and isinstance(loc_dict["id"], UUID):
                    loc_dict["id"] = str(loc_dict["id"])
                locations_to_process.append(loc_dict)

        enriched_locations = []
        for loc_dict in locations_to_process:
            latitude = loc_dict.get("latitude")
            longitude = loc_dict.get("longitude")

            if latitude is not None and longitude is not None:
                nearby_stores = await self.db.stores.get_stores_within_radius(
                    lat=latitude,
                    lon=longitude,
                    radius_meters=1500 # Default to 1.5 km radius
                )
                loc_dict["nearby_stores"] = nearby_stores
            else:
                loc_dict["nearby_stores"] = []

            enriched_locations.append(loc_dict)
        return enriched_locations
    
    async def _load_history(self):
        print("!!! ORCHESTRATOR: Loading history...", file=sys.stderr, flush=True)
        self.history = await self.db.chat.get_chat_messages(self.user_id, self.session_id)
        print(f"!!! ORCHESTRATOR: Loaded {len(self.history)} messages from DB.", file=sys.stderr, flush=True)


    async def _add_and_save_message(self, message: ChatMessage):
        """Appends a message to the in-memory history and saves it to the database."""
        print(f"!!! ORCHESTRATOR: Adding and saving message from sender '{message.sender}'. ID: {message.id}", file=sys.stderr, flush=True)
        self.history.append(message)
        await self.db.save_chat_message_from_object(message)
        print(f"!!! ORCHESTRATOR: Save complete for message ID: {message.id}", file=sys.stderr, flush=True)

    async def stream_response(self, user_message_text: Optional[str]) -> AsyncGenerator[str, None]:
        # --- 1. Initial Setup ---
        if not self.history: await self._load_history()
        if user_message_text and user_message_text.strip():
            user_message = ChatMessage(
                id=uuid4(), 
                timestamp=datetime.now(timezone.utc), 
                user_id=self.user_id, 
                session_id=self.session_id, 
                sender="user", 
                message_text=user_message_text
            )
            await self._add_and_save_message(user_message)
        
        yield StreamedPart(type="status", content="processing").to_sse()

        # Add location context (from chat request or user's saved locations) with nearby stores to system instructions
        location_context = await self._get_location_context_with_nearby_stores()
        if location_context:
            self.system_instructions.append(
                "Dostupne lokacije i obližnje trgovine: " + json.dumps(location_context) + 
                "Koristi popis trgovina za multi_search_tool."
            )
        
        # The location_info is already handled by _get_location_context_with_nearby_stores,
        # so we remove the separate handling here to avoid duplication.
        # if self.location_info:
        #     location_data = {
        #         "latitude": self.location_info.latitude,
        #         "longitude": self.location_info.longitude,
        #         "locationId": str(self.location_info.locationId) if self.location_info.locationId else None
        #     }
        #     self.system_instructions.append(
        #         "Trenutna lokacija korisnika: " + json.dumps(location_data) +
        #         "Koristi ovu lokaciju za pretragu obližnjih trgovina ako je potrebno."
        #     )

        # --- 2. Make a Single API Call with Tools Enabled ---
        ai_history = self.ai_provider.format_history(self.system_instructions, self.history)
        
        debug_print("[Orchestrator] Making a single API call to determine action (text or tool)...")
        response_stream = self.ai_provider.generate_stream(ai_history, use_tools=True)

        tool_calls_this_turn = []
        try:
            async for part in response_stream:
                if part.type == "text":
                    self.full_ai_response_text += part.content
                    yield part.to_sse()
                elif part.type == "tool_call":
                    tool_calls_this_turn.append(part.content)
        except Exception as e:
            debug_print(f"Exception during initial stream: {e}")
            yield StreamedPart(type="error", content=str(e)).to_sse()

        # --- 3. Process the Result of the Single API Call ---
        if tool_calls_this_turn:
            # PATH A: TOOL USE - Execute the tool and we are DONE.
            debug_print("[Orchestrator] Tool call detected. Executing tool and ending request.")
            # We don't send tool_calls to chat.
            # yield StreamedPart(type="tool_call", content=tool_calls_this_turn).to_sse()

            model_request_message = ChatMessage(
                id=uuid4(), 
                timestamp=datetime.now(timezone.utc), 
                user_id=self.user_id, 
                session_id=self.session_id, 
                sender="model", 
                message_text=None,
                tool_calls=tool_calls_this_turn
            )
            await self._add_and_save_message(model_request_message)

            all_queries = [q for call in tool_calls_this_turn for q in call.get("args", {}).get("queries", [])]
            
            tool_func = available_tools.get("multi_search_tool")
            raw_result_list = await tool_func(queries=all_queries)
            
            # Filter out groups that have no products
            filtered_result_list = [
                item for item in raw_result_list 
                if item.get('products') and item['products'].get('products') and len(item['products']['products']) > 0
            ]

            tool_output_content = {"name": "multi_search_tool", "content": {"results": to_json_primitive(filtered_result_list)}}
            yield StreamedPart(type="tool_output", content=tool_output_content).to_sse()
            
            tool_output_message = ChatMessage(
                id=uuid4(), 
                timestamp=datetime.now(timezone.utc), 
                user_id=self.user_id, 
                session_id=self.session_id, 
                sender="tool",
                message_text=f"Tool output for multi_search_tool",
                tool_outputs=[tool_output_content]
            )
            await self._add_and_save_message(tool_output_message)

        elif self.full_ai_response_text:
            # PATH B: GENERAL QUESTION - The AI already gave us the answer.
            debug_print("[Orchestrator] Text response detected. Saving and ending request.")
            
            ai_response_message = ChatMessage(
                id=uuid4(), 
                timestamp=datetime.now(timezone.utc), 
                user_id=self.user_id, 
                session_id=self.session_id, 
                sender="ai", 
                message_text=self.full_ai_response_text,
                ai_response=self.full_ai_response_text
            )
            await self._add_and_save_message(ai_response_message)
        
        else:
            # PATH C: AI FREEZE - The AI gave neither text nor tool call.
            debug_print("[Orchestrator] AI returned an empty stream. Ending request.")
            yield StreamedPart(type="error", content="Asistent trenutno nije dostupan. Molimo pokušajte kasnije.").to_sse()

        # --- 4. End the Stream ---
        yield StreamedPart(type="end", content={"session_id": str(self.session_id)}).to_sse()
