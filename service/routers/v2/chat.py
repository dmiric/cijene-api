print(">>> Importing chat_v2.py")
from fastapi import APIRouter, HTTPException, Query, status, Request
import datetime
import sys
from typing import AsyncGenerator, Optional
import json
from uuid import UUID, uuid4

from service.config import settings
from service.routers.v2.chat_components.initial_context import INITIAL_SYSTEM_INSTRUCTIONS
from service.db.models import ChatMessage, UserLocation
from service.routers.auth import verify_authentication
from fastapi.responses import StreamingResponse
from fastapi import Depends

# --- Import from our new, split-out files ---
from .chat_components.ai_models import gemini_client, openai_client
from .chat_components.ai_schemas import ChatRequest, ChatMessageResponse, gemini_tools, openai_tools
from .chat_components.ai_tools import available_tools, find_nearby_stores_tool_v2 # find_nearby_stores_tool_v2 is needed for orchestration

router = APIRouter(tags=["AI Chat V2"], dependencies=[Depends(verify_authentication)])
db = settings.get_db()
db_v2 = settings.get_db_v2()

# Using print for debugging as logging is not appearing reliably
def debug_print(*args, **kwargs):
    print("[DEBUG chat_v2]", *args, file=sys.stderr, **kwargs)


@router.post("/chat", summary="Handle AI chat interactions with streaming responses (v2)")
async def chat_endpoint_v2(chat_request: ChatRequest) -> StreamingResponse:
    """
    Handles incoming chat messages, orchestrates AI interactions, and streams responses using v2 tools.
    """
    user_id = chat_request.user_id
    session_id = chat_request.session_id if chat_request.session_id else uuid4()
    user_message_text = chat_request.message_text

    debug_print(f"Chat V2 request received: user_id={user_id}, session_id={session_id}, message='{user_message_text}'")

    # Save user message to DB
    await db.save_chat_message(
        user_id=user_id,
        session_id=str(session_id),
        message_text=user_message_text,
        is_user_message=True,
        tool_calls=None,
        tool_outputs=None,
        ai_response=None,
    )

    # Retrieve chat history
    history = await db.chat.get_chat_messages(user_id, session_id, limit=20)
    debug_print(f"Retrieved {len(history)} messages for session {session_id}")

    # Start with the static instructions from the new file
    system_instructions = INITIAL_SYSTEM_INSTRUCTIONS.copy() 

    # Add the dynamic instruction with the current user's ID
    system_instructions.append(
        f"VAŽNO: ID trenutnog korisnika je {user_id}. Uvijek koristi ovaj ID kada pozivaš alate koji zahtijevaju user_id."
    )

    # Define system message (for debugging/logging, not directly used by AI models)
    system_message_content = "\n".join(system_instructions)

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
            if gemini_client:
                ai_history.append({"role": "model", "parts": [{"functionCall": msg.tool_calls}]})
            elif openai_client:
                ai_history.append({"role": "assistant", "tool_calls": [
                    {"id": "call_id_placeholder", "function": {"name": msg.tool_calls["name"], "arguments": json.dumps(msg.tool_calls["args"])}}
                ]})
        elif msg.sender == "tool_output" and msg.tool_outputs:
            # Ensure tool_outputs is a dictionary, not a string
            tool_output_data = msg.tool_outputs
            if isinstance(tool_output_data, str):
                try:
                    tool_output_data = json.loads(tool_output_data)
                except json.JSONDecodeError:
                    debug_print(f"Error decoding tool_outputs string: {tool_output_data}")
                    continue # Skip this message if it's malformed JSON

            if gemini_client:
                # The content of the response should be a dictionary, not a JSON string.
                # The pydantic_to_dict helper should have already serialized it correctly.
                response_content = tool_output_data.get("content")
                
                # CORRECT STRUCTURE for Gemini:
                # The "role" is "user" for the message containing the tool result,
                # and the "part" is a dictionary with the key "function_response".
                ai_history.append({
                    "role": "user",  # The role for the turn that PROVIDES the tool output is 'user'
                    "parts": [{
                        "function_response": {
                            "name": tool_output_data.get("name"),
                            "response": response_content # Pass the dictionary directly, not nested under 'content'
                        }
                    }]
                })
            elif openai_client:
                # Your OpenAI logic is correct
                ai_history.append({
                    "role": "tool", 
                    "tool_call_id": "call_id_placeholder", 
                    "content": json.dumps(tool_output_data.get("content"))
                })
    
    ai_history.append({"role": "user", "parts": [user_message_text]})

    async def event_stream():
        full_ai_response_text = ""
        
        # Loop to handle multi-turn tool orchestration within a single user request
        while True:
            tool_call_occurred_in_turn = False
            current_tool_call_info = None
            tool_outputs_for_history = [] # Collect tool outputs for this turn

            try:
                # Make AI call
                if gemini_client:
                    debug_print("Calling Gemini API (v2)...")
                    response_stream = gemini_client.generate_content(
                        ai_history,
                        tools=gemini_tools,
                        stream=True
                    )
                elif openai_client:
                    debug_print("Calling OpenAI API (v2)...")
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
                    break # Exit loop on error

                # Process AI response chunks
                for chunk in response_stream:
                    if gemini_client:
                        if chunk.candidates and chunk.candidates[0].content.parts:
                            for part in chunk.candidates[0].content.parts:
                                if part.function_call:
                                    tool_call_occurred_in_turn = True
                                    current_tool_call_info = {
                                        "name": part.function_call.name,
                                        "args": {k: v for k, v in part.function_call.args.items()}
                                    }
                                    debug_print(f"AI requested tool call: {current_tool_call_info}")
                                    yield f"data: {json.dumps({'type': 'tool_call', 'content': current_tool_call_info})}\n\n"
                                elif part.text:
                                    full_ai_response_text += part.text
                                    yield f"data: {json.dumps({'type': 'text', 'content': part.text})}\n\n"
                    elif openai_client:
                        if chunk.choices:
                            delta = chunk.choices[0].delta
                            if delta.content:
                                full_ai_response_text += delta.content
                                yield f"data: {json.dumps({'type': 'text', 'content': delta.content})}\n\n"
                            if delta.tool_calls:
                                tool_call_occurred_in_turn = True
                                for tc in delta.tool_calls:
                                    if tc.function:
                                        current_tool_call_info = {
                                            "name": tc.function.name,
                                            "args": json.loads(tc.function.arguments) if tc.function.arguments else {}
                                        }
                                        debug_print(f"AI requested tool call: {current_tool_call_info}")
                                        yield f"data: {json.dumps({'type': 'tool_call', 'content': current_tool_call_info})}\n\n"
                
                # Check if AI requested a tool call in this turn
                if not tool_call_occurred_in_turn:
                    # If no tool call occurred, the AI is done with its thought process for this turn.
                    # Break the loop to save the final text response and end the stream.
                    debug_print("AI turn finished without a tool call. Exiting loop.")
                    break
                
                # --- If we are here, a tool call occurred, so execute it and continue the loop ---
                tool_name = current_tool_call_info["name"]
                tool_args = current_tool_call_info["args"]

                # Override user_id for get_user_locations tool with the actual user_id from the request
                if tool_name == "get_user_locations":
                    debug_print(f"Overriding user_id for get_user_locations from {tool_args.get('user_id')} to {user_id}")
                    tool_args["user_id"] = user_id
                
                await db.save_chat_message(
                    user_id=user_id,
                    session_id=str(session_id),
                    message_text=f"Tool call: {tool_name}({tool_args})",
                    is_user_message=False, # Tool calls are not user messages
                    tool_calls={"name": tool_name, "args": tool_args},
                    tool_outputs=None,
                    ai_response=None,
                )

                if tool_name not in available_tools:
                    error_message = f"Alat '{tool_name}' nije pronađen."
                    debug_print(error_message)
                    yield f"data: {json.dumps({'type': 'error', 'content': error_message})}\n\n"
                    full_ai_response_text = error_message # Set error to break loop
                    break
                else:
                    # Execute the primary tool
                    tool_output = await available_tools[tool_name](**tool_args)
                    tool_output_info = {"name": tool_name, "content": tool_output}
                    debug_print(f"Tool output: {tool_output}")
                    
                    # Save this first tool's output to the database and yield to the client
                    await db.save_chat_message(
                        user_id=user_id,
                        session_id=str(session_id),
                        message_text=f"Tool output for {tool_name}: {tool_output}",
                        is_user_message=False,
                        tool_calls=None,
                        tool_outputs=tool_output_info,
                        ai_response=None,
                    )
                    yield f"data: {json.dumps({'type': 'tool_output', 'content': tool_output_info})}\n\n"
                    tool_outputs_for_history.append(tool_output_info)

                    # --- Programmatic Orchestration for Location Search (chained call) ---
                    # If the AI called get_user_locations, immediately call find_nearby_stores_v2
                    if tool_name == "get_user_locations" and "error" not in tool_output:
                        locations = tool_output.get("locations", [])
                        if locations:
                            first_location = locations[0]
                            lat = first_location.get("latitude")
                            lon = first_location.get("longitude")

                            if lat is not None and lon is not None:
                                debug_print(f"Found user location: lat={lat}, lon={lon}. Programmatically calling find_nearby_stores_tool_v2...")
                                nearby_stores_output = await find_nearby_stores_tool_v2(lat=float(lat), lon=float(lon), radius_meters=1500)
                                nearby_stores_info = {"name": "find_nearby_stores_v2", "content": nearby_stores_output}
                                debug_print(f"Nearby stores output: {nearby_stores_output}")
                                
                                # Save the second tool's output message to the DB
                                await db.save_chat_message(
                                    user_id=user_id,
                                    session_id=str(session_id),
                                    message_text=f"Tool output for find_nearby_stores_v2: {nearby_stores_output}",
                                    is_user_message=False,
                                    tool_calls=None,
                                    tool_outputs=nearby_stores_info,
                                    ai_response=None,
                                )
                                yield f"data: {json.dumps({'type': 'tool_output', 'content': nearby_stores_info})}\n\n"
                                
                                tool_outputs_for_history.append(nearby_stores_info)
                            else:
                                debug_print("User location found but missing lat/lon. Informing user.")
                                full_ai_response_text = "Pronašao/pronašla sam vašu spremljenu lokaciju, ali nedostaju zemljopisna širina i dužina. Ažurirajte detalje lokacije kako biste omogućili pretraživanje temeljeno na lokaciji."
                        else:
                            debug_print("No user locations found. Informing user.")
                            full_ai_response_text = "Nisam pronašao/pronašla spremljene lokacije za vas. Dodajte lokaciju kako biste omogućili pretraživanje temeljeno na lokaciji."
                
                    # Append all collected tool outputs to the history for the next AI turn
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
                    
                    # The loop will continue, effectively re-prompting the AI with the updated history.
                    # No explicit 'continue' needed here as it's the end of the 'if' block.

            except Exception as e:
                debug_print(f"Error in event_stream: {e}")
                yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
                break # Break loop on exception

        # Save AI's final text response to DB (after loop breaks)
        if full_ai_response_text:
            await db.save_chat_message(
                user_id=user_id,
                session_id=str(session_id),
                message_text=full_ai_response_text,
                is_user_message=False,
                tool_calls=None,
                tool_outputs=None,
                ai_response=full_ai_response_text,
            )
        
        yield f"data: {json.dumps({'type': 'end', 'session_id': str(session_id)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
print("<<< Finished importing in chat_v2.py")
