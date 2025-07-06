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


class ChatOrchestrator:
    def __init__(self, user_id: UUID, session_id: UUID, db: Database, system_instructions: list[str]):
        self.user_id = user_id
        self.session_id = session_id
        self.db = db
        self.system_instructions = system_instructions
        self.ai_provider: AbstractAIProvider = get_ai_provider(
            db=self.db, user_id=self.user_id, session_id=self.session_id
        )
        self.full_ai_response_text = ""
        self.history: list[ChatMessage] = []

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
        if not self.history: await self._load_history()
        
        if user_message_text and user_message_text.strip():
            user_message = ChatMessage(
                id=uuid4(), timestamp=datetime.now(timezone.utc),
                user_id=self.user_id, session_id=self.session_id,
                sender="user", message_text=user_message_text
            )
            await self._add_and_save_message(user_message)
        
        while True:
            ai_history = self.ai_provider.format_history(self.system_instructions, self.history, user_message=None)
            tool_calls_this_turn: List[dict] = []
            self.full_ai_response_text = ""
            
            try:
                response_stream = self.ai_provider.generate_stream(ai_history)

                async for part in response_stream:
                    # Always yield the SSE formatted string
                    yield part.to_sse()
                    
                    # Accumulate content based on type
                    if part.type == "tool_call":
                        tool_calls_this_turn.append(part.content)
                    elif part.type == "text":
                        self.full_ai_response_text += part.content

                if tool_calls_this_turn:
                    debug_print(f"[Orchestrator] Turn ended with {len(tool_calls_this_turn)} tool calls. Executing.")
                    
                    model_request_message = ChatMessage(
                        id=uuid4(), timestamp=datetime.now(timezone.utc), 
                        user_id=self.user_id, session_id=self.session_id, 
                        sender="model", 
                        message_text=None,
                        tool_calls=tool_calls_this_turn
                    )
                    await self._add_and_save_message(model_request_message)

                    for tool_call_item in tool_calls_this_turn:
                        tool_name = tool_call_item.get("name")
                        tool_args = tool_call_item.get("args", {})
                        
                        tool_func = available_tools.get(tool_name)
                        if not tool_func: continue

                        result = await tool_func(**tool_args)
                        tool_output_content = {"name": tool_name, "content": to_json_primitive(result)}
                        
                        # Yield the tool output to the client
                        yield StreamedPart(type="tool_output", content=tool_output_content).to_sse()
                        
                        tool_output_message = ChatMessage(
                            id=uuid4(), timestamp=datetime.now(timezone.utc), 
                            user_id=self.user_id, session_id=self.session_id, 
                            sender="tool", 
                            message_text=f"Tool output for {tool_name}",
                            tool_outputs=[tool_output_content]
                        )
                        await self._add_and_save_message(tool_output_message)
                    
                    continue 
                else:
                    debug_print("[Orchestrator] Turn ended with final text response. Breaking loop.")
                    break
            except Exception as e:
                debug_print(f"Exception in orchestrator stream: {e}")
                # --- THIS IS THE FIX FOR BUG #2 ---
                # Always yield the SSE formatted string, even for errors
                yield StreamedPart(type="error", content=str(e)).to_sse()
                # --- END OF FIX ---
                break

        if self.full_ai_response_text:
            ai_response_message = ChatMessage(
                id=uuid4(), timestamp=datetime.now(timezone.utc),
                user_id=self.user_id, session_id=self.session_id,
                sender="ai", message_text=self.full_ai_response_text,
                ai_response=self.full_ai_response_text
            )
            await self._add_and_save_message(ai_response_message)
            
        yield StreamedPart(type="end", content={"session_id": str(self.session_id)}).to_sse()