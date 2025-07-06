# service/routers/v2/chat_components/ai_providers.py

from abc import ABC, abstractmethod
from typing import AsyncGenerator, Any, Dict, List
import json
from uuid import UUID

from google import genai

from .ai_models import gemini_client
from .ai_helpers import convert_protobuf_to_dict
from .ai_schemas import gemini_tools
from service.db.models import ChatMessage
from service.db.base import Database
from service.utils.timing import debug_print
from service.config import get_settings

class StreamedPart:
    def __init__(self, type: str, content: Any):
        self.type = type
        self.content = content
    def to_sse(self) -> str:
        return f"data: {json.dumps({'type': self.type, 'content': self.content}, default=str)}\n\n"

def to_json_primitive(value):
    if isinstance(value, (dict, list)):
        return json.loads(json.dumps(value, default=str))
    return value

class AbstractAIProvider(ABC):
    @abstractmethod
    def format_history(self, system_instructions: list[str], history: list[ChatMessage], user_message: str | None) -> list:
        pass

    @abstractmethod
    async def generate_stream(self, history: list) -> AsyncGenerator[StreamedPart, None]:
        pass

def get_ai_provider(db: Database, user_id: UUID, session_id: UUID) -> AbstractAIProvider:
    if gemini_client:
        return GeminiProvider(db=db, user_id=user_id, session_id=session_id)
    raise ValueError("No AI client is configured.")

class GeminiProvider(AbstractAIProvider):
    def __init__(self, db: Database, user_id: UUID, session_id: UUID):
        self.db = db
        self.user_id = user_id
        self.session_id = session_id
        debug_print("GeminiProvider initialized.")

    def format_history(self, system_instructions: list[str], history: list[ChatMessage], user_message: str | None) -> list:
        # This version correctly formats the history roles
        ai_history = []
        full_instructions = "\n".join(system_instructions)
        if full_instructions.strip():
            ai_history.append(genai.types.Content(role="user", parts=[genai.types.Part(text=full_instructions)]))
            # This is a critical part of the prompt, ensuring the AI sees its own confirmation
            ai_history.append(genai.types.Content(role="model", parts=[genai.types.Part(text="Razumijem. Spreman sam pomoći.")]))

        for msg in history:
            parts, role = [], None
            sender_role = msg.sender

            if sender_role == "user":
                role = "user"
                if msg.message_text: parts.append(genai.types.Part(text=msg.message_text))
            
            elif sender_role in ("ai", "model"):
                role = "model"
                # --- THIS IS THE FIX FOR BUG #1 ---
                # A model turn can have BOTH text and tool calls. We must include both if they exist.
                # The prompt asks the AI to summarize AFTER getting results, so it needs to be able to send text.
                text_content = msg.message_text or msg.ai_response
                if text_content:
                    parts.append(genai.types.Part(text=text_content))
                
                if msg.tool_calls:
                    tool_calls_data = msg.tool_calls if isinstance(msg.tool_calls, list) else [msg.tool_calls]
                    for call in tool_calls_data:
                         if isinstance(call, dict) and "name" in call and "args" in call:
                            parts.append(genai.types.Part(function_call=genai.types.FunctionCall(name=call["name"], args=to_json_primitive(call["args"]))))
                # --- END OF FIX ---
            
            elif sender_role in ("tool", "tool_output"):
                role = "tool"
                if msg.tool_outputs:
                    tool_outputs_data = msg.tool_outputs if isinstance(msg.tool_outputs, list) else [msg.tool_outputs]
                    for output in tool_outputs_data:
                        if isinstance(output, dict) and "name" in output and "content" in output:
                            parts.append(genai.types.Part(function_response=genai.types.FunctionResponse(name=output["name"], response=to_json_primitive(output["content"]))))
            
            if parts and role:
                ai_history.append(genai.types.Content(parts=parts, role=role))
                
        if user_message: ai_history.append(genai.types.Content(role="user", parts=[genai.types.Part(text=user_message)]))
        return ai_history

    async def generate_stream(self, history: list) -> AsyncGenerator[StreamedPart, None]:
        try:
            config = genai.types.GenerateContentConfig(tools=gemini_tools)
            streaming_response = await gemini_client.aio.models.generate_content_stream(
                model=get_settings().gemini_text_model,
                contents=history,
                config=config
            )
            async for raw_chunk in streaming_response:
                for part in self._parse_chunk_and_convert(raw_chunk):
                    yield part
        except Exception as e:
            yield StreamedPart(type="error", content=str(e))

    def _parse_chunk_and_convert(self, chunk: Any) -> list[StreamedPart]:
        parts = []
        if not hasattr(chunk, 'candidates') or not chunk.candidates: return parts
        for candidate in chunk.candidates:
            if not candidate.content or not candidate.content.parts: continue
            for part in candidate.content.parts:
                if part.function_call:
                    raw_function_call_object = part.function_call
                    tool_call_dict = convert_protobuf_to_dict(raw_function_call_object)
                    streamed_part_to_yield = StreamedPart(type="tool_call", content=tool_call_dict)
              
                    parts.append(streamed_part_to_yield)
        return parts