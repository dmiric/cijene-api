# service/routers/v2/chat_components/chat_orchestrator.py

import json
from uuid import UUID, uuid4
from typing import AsyncGenerator, Optional, List
from datetime import datetime, timezone

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
        self.history = await self.db.chat.get_chat_messages(self.user_id, self.session_id)

    async def stream_response(self, user_message_text: Optional[str]) -> AsyncGenerator[str, None]:
        if not self.history: await self._load_history()
        if user_message_text and user_message_text.strip():
            self.history.append(ChatMessage(
                id=uuid4(), timestamp=datetime.now(timezone.utc),
                user_id=self.user_id, session_id=self.session_id,
                sender="user", message_text=user_message_text,
            ))
        
        while True:
            ai_history = self.ai_provider.format_history(self.system_instructions, self.history, user_message=None)
            
            tool_calls_this_turn: List[dict] = []
            self.full_ai_response_text = ""
            
            try:
                response_stream = self.ai_provider.generate_stream(ai_history)

                async for part in response_stream:
                    if part.type == "tool_call":
                        tool_calls_this_turn.append(part.content)
                        yield part.to_sse()
                    elif part.type == "text":
                        self.full_ai_response_text += part.content
                        yield part.to_sse()
                    else:
                        yield part.to_sse()

                if tool_calls_this_turn:
                    debug_print(f"[Orchestrator] Turn ended with {len(tool_calls_this_turn)} tool calls. Executing.")
                    
                    self.history.append(ChatMessage(id=uuid4(), timestamp=datetime.now(timezone.utc), user_id=self.user_id, session_id=self.session_id, sender="model", message_text="Tool call request", tool_calls=tool_calls_this_turn))

                    for tool_call_item in tool_calls_this_turn:
                        tool_name = tool_call_item.get("name")
                        tool_args = tool_call_item.get("args", {})
                        
                        tool_func = available_tools.get(tool_name)
                        if not tool_func: continue

                        result = await tool_func(**tool_args)
                        tool_output_content = {"name": tool_name, "content": to_json_primitive(result)}
                        yield StreamedPart(type="tool_output", content=tool_output_content).to_sse()
                        self.history.append(ChatMessage(id=uuid4(), timestamp=datetime.now(timezone.utc), user_id=self.user_id, session_id=self.session_id, sender="tool", message_text=f"Tool output for {tool_name}", tool_outputs=[tool_output_content]))
                    
                    continue 
                else:
                    debug_print("[Orchestrator] Turn ended with final text response. Breaking loop.")
                    break
            except Exception as e:
                debug_print(f"Exception in orchestrator stream: {e}")
                yield StreamedPart(type="error", content=str(e)).to_sse()
                break

        if self.full_ai_response_text:
            await self.db.chat.save_chat_message(user_id=self.user_id, session_id=self.session_id, message_text=self.full_ai_response_text, is_user_message=False, ai_response=self.full_ai_response_text)
        yield StreamedPart(type="end", content={"session_id": str(self.session_id)}).to_sse()