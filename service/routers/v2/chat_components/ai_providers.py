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

import sys

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

    def format_history(self, system_instructions: list[str], history: list[ChatMessage]) -> list:
        debug_print("--- PROVIDER: Starting format_history ---")
        ai_history = []
        full_instructions = "\n".join(system_instructions)
        if full_instructions.strip():
            ai_history.append(genai.types.Content(role="user", parts=[genai.types.Part(text=full_instructions)]))
            ai_history.append(genai.types.Content(role="model", parts=[genai.types.Part(text="Razumijem. Spreman sam pomoći.")]))

        for msg in history:
            parts, role = [], None
            if msg.sender == "user":
                role = "user"
                if msg.message_text:
                    parts.append(genai.types.Part(text=msg.message_text))
            elif msg.sender in ("ai", "model"):
                role = "model"
                if msg.message_text:
                    parts.append(genai.types.Part(text=msg.message_text))
                if msg.tool_calls:
                    for call in msg.tool_calls:
                        parts.append(genai.types.Part(function_call=genai.types.FunctionCall(name=call["name"], args=call["args"])))
            elif msg.sender == "tool":
                role = "tool"
                # --- THIS IS THE FIX ---
                # A 'tool' role message MUST ONLY contain function_response parts.
                # We completely ignore the message.message_text for this role.
                if msg.tool_outputs:
                    for output in msg.tool_outputs:
                        parts.append(genai.types.Part(function_response=genai.types.FunctionResponse(name=output["name"], response=output["content"])))
                # --- END OF FIX ---
            
            if parts:
                ai_history.append(genai.types.Content(parts=parts, role=role))
        
        debug_print(f"--- PROVIDER: format_history finished. Final history for API:\n{ai_history}\n---")
        return ai_history

    async def generate_stream(self, history: list, use_tools: bool = True) -> AsyncGenerator[StreamedPart, None]:
        print("--- PROVIDER: Starting generate_stream ---", file=sys.stderr, flush=True)
        try:
            if use_tools:
                debug_print("[Provider] Calling API with tool configuration.")
                config_with_tools = genai.types.GenerateContentConfig(tools=gemini_tools)
                streaming_response = await gemini_client.aio.models.generate_content_stream(
                    model=get_settings().gemini_text_model,
                    contents=history,
                    config=config_with_tools
                )
            else:
                debug_print("[Provider] Calling API WITHOUT config for pure text generation.")
                streaming_response = await gemini_client.aio.models.generate_content_stream(
                    model=get_settings().gemini_text_model,
                    contents=history
            )
            
            chunk_count = 0
            async for raw_chunk in streaming_response:
                chunk_count += 1
                print(f"!!! PROVIDER: Received raw chunk #{chunk_count} from API.", file=sys.stderr, flush=True)
                # print(f"!!! PROVIDER: Raw chunk content: {raw_chunk}", file=sys.stderr, flush=True)
                parsed_parts = self._parse_chunk_and_convert(raw_chunk)
                if parsed_parts:
                    print(f"!!! PROVIDER: Parsed {len(parsed_parts)} part(s) from chunk.", file=sys.stderr, flush=True)
                    for part in parsed_parts:
                        yield part
                else:
                    print("!!! PROVIDER: Chunk was empty or contained no parsable parts.", file=sys.stderr, flush=True)

            if chunk_count == 0:
                print("!!! PROVIDER WARNING: The API returned 0 chunks. The stream was empty.", file=sys.stderr, flush=True)
                
        except Exception as e:
            print(f"!!! PROVIDER ERROR in generate_stream: {e}", file=sys.stderr, flush=True)
            debug_print(f"ERROR within GeminiProvider.generate_stream: {type(e).__name__}: {e}")
            yield StreamedPart(type="error", content=str(e))

    def _parse_chunk_and_convert(self, chunk: Any) -> list[StreamedPart]:
        """
        Parses a raw chunk from the API and converts its parts into StreamedPart objects.
        This version correctly handles BOTH text and function_call parts.
        """
        print(f"!!! PROVIDER: Parsing chunk: {chunk} from API.", file=sys.stderr, flush=True)
        parts = []
        if not hasattr(chunk, 'candidates') or not chunk.candidates:
            return parts
            
        for candidate in chunk.candidates:
            if not hasattr(candidate, 'content') or not candidate.content or not hasattr(candidate.content, 'parts'):
                continue
                
            for part in candidate.content.parts:
                # --- THIS IS THE FIX ---
                if part.text:
                    # If the part has text, create a 'text' StreamedPart.
                    print(f"!!! PROVIDER: Found text part: '{part.text}'", file=sys.stderr, flush=True)
                    parts.append(StreamedPart(type="text", content=part.text))
                
                elif part.function_call:
                    # If the part has a function call, create a 'tool_call' StreamedPart.
                    print(f"!!! PROVIDER: Found function_call part.", file=sys.stderr, flush=True)
                    tool_call_dict = convert_protobuf_to_dict(part.function_call)
                    parts.append(StreamedPart(type="tool_call", content=tool_call_dict))
                # --- END OF FIX ---

        print(f"!!! PROVIDER: Parsed {len(parts)} parts from chunk.", file=sys.stderr, flush=True)
        return parts