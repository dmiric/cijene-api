from abc import ABC, abstractmethod
from typing import AsyncGenerator, Any, Dict, List # Removed Tuple from import
import json
import asyncio # Added for retry mechanism
from google import genai
from google.api_core.exceptions import ClientError # Import ClientError

from .ai_models import gemini_client, openai_client
from .ai_helpers import convert_protobuf_to_dict
from .ai_tools import available_tools # Import available_tools
from .ai_schemas import gemini_tools # Import gemini_tools
from service.db.models import ChatMessage
from service.utils.timing import debug_print # Corrected import for debug_print
from service.config import get_settings # Import settings

# A simple data class for standardized stream parts
class StreamedPart:
    def __init__(self, type: str, content: Any):
        self.type = type
        self.content = content
    def to_sse(self) -> str:
        return f"data: {json.dumps({'type': self.type, 'content': self.content})}\n\n"

# Helper function to ensure data is JSON-serializable
def to_json_primitive(value):
    if isinstance(value, (dict, list)):
        return json.loads(json.dumps(value, default=str))
    return value

class AbstractAIProvider(ABC):
    """Abstract base class for an AI Provider."""
    @abstractmethod
    def format_history(self, system_instructions: list[str], history: list[ChatMessage], user_message: str) -> list:
        pass

    @abstractmethod
    async def generate_stream(self, history: list) -> AsyncGenerator[Any, None]: # Changed return type hint
        pass

    @abstractmethod
    def parse_chunk(self, chunk: Any) -> list[StreamedPart]:
        pass

# Factory to get the configured provider
def get_ai_provider() -> AbstractAIProvider:
    if gemini_client:
        return GeminiProvider()
    if openai_client:
        return OpenAIProvider() # You would implement this class
    raise ValueError("No AI client is configured.")

class GeminiProvider(AbstractAIProvider):
    def format_history(self, system_instructions: list[str], history: list[ChatMessage], user_message: str) -> list:
        ai_history = []
        
        # 1. Add system instructions
        full_instructions = "\n".join(system_instructions)
        if full_instructions.strip():
            ai_history.append(genai.types.Content(role="user", parts=[genai.types.Part(text=full_instructions)]))
            ai_history.append(genai.types.Content(role="model", parts=[genai.types.Part(text="Razumijem. Spreman sam pomoći.")]))

        # 2. Process historical messages
        for msg in history:
            debug_print(f"DEBUG: Processing history message: sender={msg.sender}, message_text='{msg.message_text}', ai_response='{msg.ai_response}', tool_calls={msg.tool_calls}, tool_outputs={msg.tool_outputs}")
            
            parts = []
            role = "user"  # Default role

            # A message from the user
            if msg.sender == "user":
                role = "user"
                if msg.message_text and msg.message_text.strip():
                    parts.append(genai.types.Part(text=msg.message_text))

            # A message from the AI/Model, which could contain text and/or tool calls
            elif msg.sender in ("ai", "tool_call"):
                role = "model"
                
                # Check for a text part (from either ai_response or a descriptive message_text)
                # The Gemini API allows a text part to accompany a function_call part.
                text_content = msg.ai_response or msg.message_text
                if text_content and text_content.strip():
                    parts.append(genai.types.Part(text=text_content))

                # Check for a function_call part in the SAME message
                debug_print(f"DEBUG: msg.tool_calls (raw): {msg.tool_calls} (type: {type(msg.tool_calls)})")
                if msg.tool_calls:
                    try:
                        tool_calls_data = msg.tool_calls
                        if isinstance(tool_calls_data, str):
                            tool_calls_data = json.loads(tool_calls_data)
                        if isinstance(tool_calls_data, dict):
                            tool_calls_data = [tool_calls_data] # Normalize to list
                        debug_print(f"DEBUG: tool_calls_data after processing: {tool_calls_data} (type: {type(tool_calls_data)})")
                        
                        for call in tool_calls_data:
                            if isinstance(call, dict) and "name" in call and "args" in call:
                                final_args = to_json_primitive(call["args"])
                                parts.append(genai.types.Part(function_call=genai.types.FunctionCall(name=call["name"], args=final_args)))
                            else:
                                debug_print(f"Skipping malformed tool_call in history: {msg.id} - {call}")
                    except (json.JSONDecodeError, TypeError) as e:
                        debug_print(f"ERROR: Could not process tool_calls in history for msg {msg.id}: {e}")

            # A response from a tool
            elif msg.sender == "tool_output":
                role = "tool"
                debug_print(f"DEBUG: msg.tool_outputs (raw): {msg.tool_outputs} (type: {type(msg.tool_outputs)})")
                if msg.tool_outputs:
                    try:
                        tool_outputs_data = msg.tool_outputs
                        if isinstance(tool_outputs_data, str):
                           tool_outputs_data = json.loads(tool_outputs_data)
                        if isinstance(tool_outputs_data, dict):
                           tool_outputs_data = [tool_outputs_data] # Normalize to list
                        debug_print(f"DEBUG: tool_outputs_data after processing: {tool_outputs_data} (type: {type(tool_outputs_data)})")

                        for output in tool_outputs_data:
                            if isinstance(output, dict) and "name" in output and "content" in output:
                                final_content = {"result": to_json_primitive(output["content"])}
                                parts.append(genai.types.Part(function_response=genai.types.FunctionResponse(name=output["name"], response=final_content)))
                            else:
                                debug_print(f"Skipping malformed tool_output in history: {msg.id} - {output}")
                    except (json.JSONDecodeError, TypeError) as e:
                        debug_print(f"ERROR: Could not process tool_outputs in history for msg {msg.id}: {e}")

            # Only append if we successfully created parts for this message
            if parts:
                ai_history.append(genai.types.Content(parts=parts, role=role))
            else:
                debug_print(f"Skipping message with no valid parts: id={msg.id}, sender={msg.sender}")

        # 3. Add the new user message
        if user_message:
            ai_history.append(genai.types.Content(role="user", parts=[genai.types.Part(text=user_message)]))
            
        return ai_history

    async def generate_stream(self, history: list) -> AsyncGenerator[Any, None]:
        """
        Correctly awaits the API call once, then iterates over the resulting
        asynchronous iterable object.
        Yields StreamedPart objects, including "history_update" parts for new history entries.
        """
        # This loop handles multi-turn function calling
        for attempt in range(get_settings().max_tool_calls): # Limit tool calls to prevent infinite loops
            # Create a deep copy of the history for each attempt to prevent in-place modification issues
            # This ensures a clean history is sent to the API for each turn.
            # The history passed here is already formatted by format_history
            current_history_for_api = [
                genai.types.Content(role=item.role, parts=list(item.parts))
                for item in history
            ]
            try:
                # STEP 1: Send the current history and tool declarations to the model
                # The result, `streaming_response`, IS the async-iterable.
                debug_print(f"GeminiProvider: History sent to API: {current_history_for_api}")
                streaming_response = await gemini_client.aio.models.generate_content_stream(
                    model=get_settings().gemini_text_model, # Use model from settings
                    contents=current_history_for_api, # Use the deep copied history
                    config=genai.types.GenerateContentConfig(
                        tools=gemini_tools # Pass the generated tool declarations
                    )
                )

                # This print will execute if the await above succeeds.
                debug_print(f"GeminiProvider: Successfully awaited API. Type of streaming_response is {type(streaming_response)}. Starting iteration.")
                debug_print(f"GeminiProvider: dir(streaming_response): {dir(streaming_response)}")

                # STEP 2: Iterate over the streaming response
                full_response_content = []
                function_calls_in_this_turn = [] # Renamed for clarity
                new_history_entries_for_orchestrator = [] # Collect new history entries to send back to orchestrator

                async for raw_chunk in streaming_response: # Iterate over raw chunks
            
                    # Parse the raw chunk into StreamedPart objects
                    parsed_parts = self.parse_chunk(raw_chunk)

                    for part in parsed_parts:
                        if part.type == "text":
                            full_response_content.append(part.content)
                            yield part # Yield the text part
                        elif part.type == "tool_call":
                            function_calls_in_this_turn.append(part.content) # Collect tool calls
                            yield part # Yield the tool call part
                        elif part.type == "tool_output":
                            # This case should ideally not happen here, as tool_outputs are generated by us
                            # and appended to history, not directly from model chunks.
                            yield part # Yield error part (for completeness, though unlikely)
                        elif part.type == "error":
                            yield part # Yield error part
                
                # After receiving all chunks for this turn, process function calls
                if function_calls_in_this_turn:
                    debug_print(f"GeminiProvider: Executing {len(function_calls_in_this_turn)} function calls.")
                    
                    # Append the model's response (which contains the tool call) to new_history_entries_for_orchestrator
                    new_model_content = genai.types.Content(role="model", parts=[
                        genai.types.Part(function_call=genai.types.FunctionCall(name=fc["name"], args=fc["args"]))
                        for fc in function_calls_in_this_turn
                    ])
                    new_history_entries_for_orchestrator.append(new_model_content)
                    debug_print(f"GeminiProvider: Appended new model content to new_history_entries_for_orchestrator: {new_model_content}")

                    for func_call_dict in function_calls_in_this_turn:
                        tool_name = func_call_dict["name"]
                        tool_args = func_call_dict["args"]

                        if tool_name not in available_tools:
                            error_msg = f"Tool '{tool_name}' not found in available_tools."
                            debug_print(f"ERROR: {error_msg}")
                            yield StreamedPart(type="error", content=error_msg)
                            new_history_entries_for_orchestrator.append(genai.types.Content(role="user", parts=[genai.types.Part(function_response=genai.types.FunctionResponse(name=tool_name, response={"error": error_msg}))]))
                            yield StreamedPart(type="history_update", content=new_history_entries_for_orchestrator) # Yield history update on error
                            return # End stream on tool not found

                        tool_func = available_tools[tool_name]
                        debug_print(f"GeminiProvider: Calling tool '{tool_name}' with args: {tool_args}")
                        
                        try:
                            tool_result = await tool_func(**tool_args)
                            debug_print(f"GeminiProvider: Tool '{tool_name}' returned: {tool_result}")
                            
                            # Ensure tool_result is a dictionary for consistent handling
                            if not isinstance(tool_result, dict):
                                debug_print(f"WARNING: tool_result for {tool_name} is not a dict. Converting to dict.")
                                tool_result = {"output": tool_result} # Wrap in a dict

                            # Ensure content is a plain dict or simple type, using json.dumps/loads for deep conversion
                            final_content = to_json_primitive(tool_result)
                            debug_print(f"GeminiProvider: Final content for FunctionResponse: {final_content} (type: {type(final_content)})")

                            yield StreamedPart(type="tool_output", content={"name": tool_name, "content": tool_result})
                            new_tool_output_content = genai.types.Content(role="user", parts=[genai.types.Part(function_response=genai.types.FunctionResponse(name=tool_name, response=final_content))])
                            new_history_entries_for_orchestrator.append(new_tool_output_content)
                            debug_print(f"GeminiProvider: Appended new tool output content to new_history_entries_for_orchestrator: {new_tool_output_content}")
                        except Exception as tool_e:
                            error_msg = f"Error executing tool '{tool_name}': {tool_e}"
                            debug_print(f"ERROR: {error_msg}")
                            yield StreamedPart(type="error", content=error_msg)
                            new_history_entries_for_orchestrator.append(genai.types.Content(role="user", parts=[genai.types.Part(function_response=genai.types.FunctionResponse(name=tool_name, response={"error": error_msg}))]))
                            yield StreamedPart(type="history_update", content=new_history_entries_for_orchestrator) # Yield history update on error
                            return # End stream on tool execution error
                    
                    yield StreamedPart(type="history_update", content=new_history_entries_for_orchestrator) # Yield history update before continuing loop
                    continue # Continue the loop to send updated history to model
                else:
                    # If no function calls, and we have text content, it's the final response
                    if full_response_content:
                        debug_print("GeminiProvider: No function calls, yielding final text response.")
                        yield StreamedPart(type="history_update", content=new_history_entries_for_orchestrator) # Yield history update before ending
                        return # End stream after final text
                    else:
                        debug_print("GeminiProvider: No function calls and no text content. Ending stream.")
                        yield StreamedPart(type="history_update", content=new_history_entries_for_orchestrator) # Yield history update before ending
                        return # End stream if nothing to yield

            except ClientError as e: # Use the imported ClientError
                # Handle API-specific errors, e.g., rate limits (429) or other transient issues
                debug_print(f"GeminiProvider: ClientError in generate_stream (Attempt {attempt + 1}/{get_settings().max_tool_calls}): {e}")
                if e.status_code == 429 or (e.status_code >= 500 and e.status_code < 600):
                    retry_delay = min(2 ** attempt, 60) # Exponential backoff, max 60 seconds
                    debug_print(f"GeminiProvider: Retrying in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                    continue # Retry the current turn
                else:
                    # For other client errors, re-raise or yield error and break
                    debug_print(f"ERROR within generate_stream (ClientError): {type(e).__name__}: {e}")
                    yield StreamedPart(type="error", content=str(e))
                    yield StreamedPart(type="history_update", content=new_history_entries_for_orchestrator) # Yield history update on error
                    return # End stream on unrecoverable client error
            except Exception as e:
                # Catch any other unexpected errors
                debug_print(f"ERROR within generate_stream (Unexpected Exception): {type(e).__name__}: {e}")
                yield StreamedPart(type="error", content=str(e))
                yield StreamedPart(type="history_update", content=new_history_entries_for_orchestrator) # Yield history update on error
                return # End stream on unexpected error
        
        debug_print(f"GeminiProvider: Max tool calls ({get_settings().max_tool_calls}) or max retries reached. Ending stream.")
        yield StreamedPart(type="error", content="Max tool calls or retries reached. Please refine your query.")
        yield StreamedPart(type="history_update", content=new_history_entries_for_orchestrator) # Yield history update if max retries reached
        return # End stream

    async def _empty_async_generator(self):
        """An empty async generator for cases where we need to return one but have no parts to yield."""
        return # Changed from yield from ()

    def parse_chunk(self, chunk: Any) -> list[StreamedPart]:
        parts = []
        debug_print(f"GeminiProvider: Raw chunk received: {chunk}")
        
        if not chunk.candidates:
            debug_print("GeminiProvider: No candidates found in chunk.")
            return parts

        # Iterate through all candidates, though typically there's only one
        for candidate in chunk.candidates:
            if not candidate.content:
                debug_print("GeminiProvider: No content found in candidate.")
                continue # Skip this candidate if no content

            if not candidate.content.parts:
                debug_print("GeminiProvider: No content parts found in candidate's content.")
                continue # Skip if no parts

            for part in candidate.content.parts:
                if part.text:
                    parts.append(StreamedPart(type="text", content=part.text))
                elif part.function_call:
                    tool_call_info = convert_protobuf_to_dict({
                        "name": part.function_call.name,
                        "args": part.function_call.args
                    })
                    parts.append(StreamedPart(type="tool_call", content=tool_call_info))
                    debug_print(f"GeminiProvider: Parsed tool_call: {tool_call_info}")
        
        debug_print(f"GeminiProvider: Parsed parts: {parts}")
        return parts

class OpenAIProvider(AbstractAIProvider):
    # Implementation for OpenAI would go here, following the same pattern.
    # This is left as an exercise but would involve formatting messages like:
    # {"role": "system", "content": ...}
    # {"role": "user", "content": ...}
    # {"role": "assistant", "tool_calls": [...]}
    # And parsing the delta chunks from the OpenAI stream.
    pass
