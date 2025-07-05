print(">>> Importing chat_v2.py")
from fastapi import APIRouter, HTTPException, Query, status, Request
import datetime
import sys
from typing import AsyncGenerator, Optional
import json
from uuid import UUID, uuid4
import google.generativeai as genai # Added import for Gemini FunctionCall
from service.utils.timing import timing_decorator, debug_print # Import the decorator and debug_print

from service.config import settings
from service.routers.v2.chat_components.initial_context import INITIAL_SYSTEM_INSTRUCTIONS
from service.db.models import ChatMessage, UserLocation, UserPersonalData # UserLocation's user_id is now UUID, add UserPersonalData
from service.routers.auth import verify_authentication, RequireAuth # Import RequireAuth
from fastapi.responses import StreamingResponse
from fastapi import Depends
from service.routers.v2.chat_components.ai_schemas import ChatResponse # Import ChatResponse

# --- Import from our new, split-out files ---
from .chat_components.ai_models import gemini_client, openai_client
from .chat_components.ai_schemas import ChatRequest, ChatMessageResponse, gemini_tools, openai_tools
from .chat_components.ai_tools import available_tools, find_nearby_stores_tool_v2 # find_nearby_stores_tool_v2 is needed for orchestration
from .chat_components.ai_helpers import convert_protobuf_to_dict # Import the new helper

router = APIRouter(tags=["AI Chat V2"], dependencies=[Depends(verify_authentication)])
db = settings.get_db()

async def chat_stream_generator(
    user_id: UUID,
    session_id: UUID,
    user_message_text: str,
    history: list[ChatMessage],
    system_instructions: list[str]
) -> AsyncGenerator[str, None]:
    """
    This is an async generator function that handles the core chat logic,
    including multi-turn tool use and streaming responses. It yields
    Server-Sent Events (SSE) formatted strings.
    """
    full_ai_response_text = ""
    
    # Format history for AI model
    ai_history = []
    for instruction in system_instructions:
        if openai_client:
            ai_history.append({"role": "system", "content": instruction})
        elif gemini_client:
            ai_history.append({"role": "user", "parts": [instruction]})
            ai_history.append({"role": "model", "parts": ["Razumijem. Kako vam mogu pomoći?"]})

    for msg in history:
        if msg.sender == "user":
            ai_history.append({"role": "user", "parts": [msg.message_text]})
        elif msg.sender == "ai":
            ai_history.append({"role": "model", "parts": [msg.message_text]})
        elif msg.sender == "tool_call" and msg.tool_calls:
            tool_calls_data = msg.tool_calls
            if isinstance(tool_calls_data, str):
                try:
                    tool_calls_data = json.loads(tool_calls_data)
                except json.JSONDecodeError:
                    debug_print(f"Error decoding tool_calls string: {tool_calls_data}")
                    continue

            if gemini_client:
                # Corrected structure for Gemini tool calls
                ai_history.append({"role": "model", "parts": [genai.protos.FunctionCall(name=tool_calls_data["name"], args=tool_calls_data["args"])]})
            elif openai_client:
                ai_history.append({"role": "assistant", "tool_calls": [
                    {"id": "call_id_placeholder", "function": {"name": tool_calls_data["name"], "arguments": json.dumps(tool_calls_data["args"])}}
                ]})
        elif msg.sender == "tool_output" and msg.tool_outputs:
            tool_output_data = msg.tool_outputs
            if isinstance(tool_output_data, str):
                try:
                    tool_output_data = json.loads(tool_output_data)
                except json.JSONDecodeError:
                    debug_print(f"Error decoding tool_outputs string: {tool_output_data}")
                    continue

            if gemini_client:
                ai_history.append({
                    "role": "user",
                    "parts": [{
                        "function_response": {
                            "name": tool_output_data["name"],
                            "response": tool_output_data["content"]
                        }
                    }]
                })
            elif openai_client:
                ai_history.append({
                    "role": "tool", 
                    "tool_call_id": "call_id_placeholder", 
                    "content": json.dumps(tool_output_data["content"])
                })
    
    ai_history.append({"role": "user", "parts": [user_message_text]})

    # Loop to handle multi-turn tool orchestration within a single user request
    while True:
        tool_call_occurred_in_turn = False
        current_tool_call_info = None
        tool_outputs_for_history = [] # Collect tool outputs for this turn

        try:
            # Make AI call
            if gemini_client:
                response_stream = gemini_client.generate_content(
                    ai_history,
                    tools=gemini_tools,
                    stream=True
                )
            elif openai_client:
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
                    elif msg["role"] == "system":
                        openai_messages.append({"role": "system", "content": msg["content"]})

                response_stream = openai_client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=openai_messages,
                    tools=openai_tools,
                    tool_choice="auto",
                    stream=True
                )
            else:
                yield f"data: {json.dumps({'type': 'error', 'content': 'Nijedan AI klijent nije inicijaliziran.'})}\n\n"
                break

            # Process AI response chunks
            for chunk in response_stream:
                if gemini_client:
                    if chunk.candidates and chunk.candidates[0].content.parts:
                        for part in chunk.candidates[0].content.parts:
                            if part.function_call:
                                tool_call_occurred_in_turn = True
                                converted_args = convert_protobuf_to_dict(part.function_call.args)
                                current_tool_call_info = {
                                    "name": part.function_call.name,
                                    "args": converted_args
                                }
                                current_tool_call_info = convert_protobuf_to_dict(current_tool_call_info)
                                debug_print(f"Gemini Tool Call: {current_tool_call_info}")
                                yield f"data: {json.dumps({'type': 'tool_call', 'content': current_tool_call_info})}\n\n"
                            elif part.text:
                                full_ai_response_text += part.text
                                debug_print(f"Gemini Text: {part.text}")
                                yield f"data: {json.dumps({'type': 'text', 'content': part.text})}\n\n"
                elif openai_client:
                    if chunk.choices:
                        delta = chunk.choices[0].delta
                        if delta.content:
                            full_ai_response_text += delta.content
                            debug_print(f"OpenAI Text: {delta.content}")
                            yield f"data: {json.dumps({'type': 'text', 'content': delta.content})}\n\n"
                        if delta.tool_calls:
                            tool_call_occurred_in_turn = True
                            for tc in delta.tool_calls:
                                if tc.function:
                                    current_tool_call_info = {
                                        "name": tc.function.name,
                                        "args": json.loads(tc.function.arguments) if tc.function.arguments else {}
                                    }
                                    debug_print(f"OpenAI Tool Call: {current_tool_call_info}")
                                    yield f"data: {json.dumps({'type': 'tool_call', 'content': current_tool_call_info})}\n\n"
            
            if not tool_call_occurred_in_turn:
                debug_print(f"AI turn finished without a tool call. Final response: {full_ai_response_text}. Exiting loop.")
                break
            
            tool_name = current_tool_call_info["name"]
            tool_args = current_tool_call_info["args"]
            debug_print(f"Executing tool: {tool_name} with args: {tool_args}")

            if tool_name == "get_user_locations":
                tool_args["user_id"] = user_id
            
            await db.chat.save_chat_message(
                user_id=user_id,
                session_id=session_id,
                message_text=f"Tool call: {tool_name}({tool_args})",
                is_user_message=False,
                tool_calls={"name": tool_name, "args": tool_args},
                tool_outputs=None,
                ai_response=None,
            )

            if tool_name not in available_tools:
                error_message = f"Alat '{tool_name}' nije pronađen."
                debug_print(f"Tool not found: {tool_name}")
                yield f"data: {json.dumps({'type': 'error', 'content': error_message})}\n\n"
                full_ai_response_text = error_message
                break
            else:
                if tool_name == "multi_search_tool":
                    debug_print(f"Calling multi_search_tool with args: {tool_args}")
                tool_output = await available_tools[tool_name](**tool_args)
                tool_output_info = {"name": tool_name, "content": tool_output}
                debug_print(f"Tool '{tool_name}' executed. Output: {tool_output_info}")
                
                await db.chat.save_chat_message(
                    user_id=user_id,
                    session_id=session_id,
                    message_text=f"Tool output for {tool_name}: {tool_output}",
                    is_user_message=False,
                    tool_calls=None,
                    tool_outputs=tool_output_info,
                    ai_response=None,
                )
                yield f"data: {json.dumps({'type': 'tool_output', 'content': tool_output_info})}\n\n"
                tool_outputs_for_history.append(tool_output_info)

                # If multi_search_tool was called, we break the loop here to prevent further AI turns
                # and ensure only one set of results is processed per user message.
                if tool_name == "multi_search_tool":
                    debug_print("multi_search_tool executed. Breaking chat stream loop.")
                    break

                if tool_name == "get_user_locations" and "error" not in tool_output:
                    locations = tool_output.get("locations", [])
                    if locations:
                        first_location = locations[0]
                        lat = first_location.get("latitude")
                        lon = first_location.get("longitude")

                        if lat is not None and lon is not None:
                            debug_print(f"Chained call: find_nearby_stores_tool_v2(lat={lat}, lon={lon})")
                            nearby_stores_output = await find_nearby_stores_tool_v2(lat=float(lat), lon=float(lon), radius_meters=1500)
                            nearby_stores_info = {"name": "find_nearby_stores_v2", "content": nearby_stores_output}
                            debug_print(f"Chained tool 'find_nearby_stores_v2' executed. Output: {nearby_stores_info}")
                            
                            await db.chat.save_chat_message(
                                user_id=user_id,
                                session_id=session_id,
                                message_text=f"Tool output for find_nearby_stores_v2: {nearby_stores_output}",
                                is_user_message=False,
                                tool_calls=None,
                                tool_outputs=nearby_stores_info,
                                ai_response=None,
                            )
                            yield f"data: {json.dumps({'type': 'tool_output', 'content': nearby_stores_info})}\n\n"
                            tool_outputs_for_history.append(nearby_stores_info)
                        else:
                            full_ai_response_text = "Pronašao/pronašla sam vašu spremljenu lokaciju, ali nedostaju zemljopisna širina i dužina. Ažurirajte detalje lokacije kako biste omogućili pretraživanje temeljeno na lokaciji."
                            debug_print(f"Location missing coordinates: {full_ai_response_text}")
                    else:
                        full_ai_response_text = "Nisam pronašao/pronašla spremljene lokacije za vas. Dodajte lokaciju kako biste omogućili pretraživanje temeljeno na lokaciji."
                        debug_print(f"No locations found: {full_ai_response_text}")
                
                for tool_out in tool_outputs_for_history:
                    if gemini_client:
                        ai_history.append({
                            "role": "user",
                            "parts": [{
                                "function_response": {
                                    "name": tool_out["name"],
                                    "response": tool_out["content"]
                                }
                            }]
                        })
                    elif openai_client:
                        ai_history.append({"role": "tool", "tool_call_id": "call_id_placeholder", "content": json.dumps(tool_out["content"])})
        
        except Exception as e:
            debug_print(f"Exception in event_stream: {e}") # Removed file=sys.stderr
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
            break

    # After the loop finishes (either by breaking or completing), save the final text response.
    if full_ai_response_text:
        debug_print(f"Saving final AI response: {full_ai_response_text}")
        await db.chat.save_chat_message(
            user_id=user_id,
            session_id=session_id,
            message_text=full_ai_response_text,
            is_user_message=False,
            tool_calls=None,
            tool_outputs=None,
            ai_response=full_ai_response_text,
        )
    
    debug_print(f"Ending stream for session: {session_id}")
    yield f"data: {json.dumps({'type': 'end', 'session_id': str(session_id)})}\n\n"

@router.post("/chat_v2", response_class=StreamingResponse, status_code=status.HTTP_200_OK)
async def event_stream_post(
    request: Request,
    chat_request: ChatRequest,
    auth: RequireAuth = Depends(verify_authentication)
):
    """
    Handles the initial streaming chat conversation with the AI model via POST.

    This endpoint receives a user's message, saves it, fetches chat history,
    and then uses the `chat_stream_generator` to stream the AI's response
    back to the client using Server-Sent Events (SSE).
    """
    user_id = auth.user_id
    user_message_text = chat_request.message_text

    # Use existing session_id or create a new one for a new conversation
    session_id = chat_request.session_id or uuid4()

    # Save the initial user message to the database
    await db.chat.save_chat_message(
        user_id=user_id,
        session_id=session_id,
        message_text=user_message_text,
        is_user_message=True
    )

    # Fetch chat history and prepare system instructions
    history = await db.chat.get_chat_messages(user_id=user_id, session_id=session_id, limit=10)
    _, user_personal_data = await db.users.get_user_by_id(user_id) # Get user and personal data
    
    system_instructions = list(INITIAL_SYSTEM_INSTRUCTIONS) # Create a mutable copy
    if user_personal_data and user_personal_data.name: # Use user_personal_data.name for first_name
        system_instructions.append(f"Korisnik se zove {user_personal_data.name}.")

    # Create the generator by calling the generator function
    generator = chat_stream_generator(
        user_id=user_id,
        session_id=session_id,
        user_message_text=user_message_text,
        history=history,
        system_instructions=system_instructions
    )

    # Return the generator wrapped in a StreamingResponse
    return StreamingResponse(generator, media_type="text/event-stream")

@router.get("/chat_v2/stream/{session_id}", response_class=StreamingResponse, status_code=status.HTTP_200_OK)
async def event_stream_get(
    session_id: UUID,
    auth: RequireAuth = Depends(verify_authentication)
):
    """
    Handles continuous streaming of chat conversation with the AI model
    for an existing session using Server-Sent Events (SSE) via GET.
    """
    user_id = auth.user_id

    # Fetch chat history and prepare system instructions
    history = await db.chat.get_chat_messages(user_id=user_id, session_id=session_id, limit=10)
    _, user_personal_data = await db.users.get_user_by_id(user_id) # Get user and personal data
    
    system_instructions = list(INITIAL_SYSTEM_INSTRUCTIONS) # Create a mutable copy
    if user_personal_data and user_personal_data.name: # Use user_personal_data.name for first_name
        system_instructions.append(f"Korisnik se zove {user_personal_data.name}.")

    # Create the generator by calling the generator function
    # For a GET stream, there's no new user message in this specific request,
    # the stream continues based on the session history.
    generator = chat_stream_generator(
        user_id=user_id,
        session_id=session_id,
        user_message_text="", # No new message for this GET request, just continue stream
        history=history,
        system_instructions=system_instructions
    )

    # Return the generator wrapped in a StreamingResponse
    return StreamingResponse(generator, media_type="text/event-stream")
