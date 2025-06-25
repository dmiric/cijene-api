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
    history = await db.get_chat_messages(user_id, session_id, limit=20)
    debug_print(f"Retrieved {len(history)} messages for session {session_id}")

    # Define system message
    system_message_content = "\n".join(INITIAL_SYSTEM_INSTRUCTIONS)

    # Format history for AI model
    ai_history = []
    for instruction in INITIAL_SYSTEM_INSTRUCTIONS:
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
            if gemini_client:
                # The content of the response should be a dictionary, not a JSON string.
                # The pydantic_to_dict helper should have already serialized it correctly.
                response_content = msg.tool_outputs.get("content")
                
                # CORRECT STRUCTURE for Gemini:
                # The "role" is "user" for the message containing the tool result,
                # and the "part" is a dictionary with the key "function_response".
                ai_history.append({
                    "role": "user",  # The role for the turn that PROVIDES the tool output is 'user'
                    "parts": [{
                        "function_response": {
                            "name": msg.tool_outputs.get("name"),
                            "response": response_content # Pass the dictionary directly, not nested under 'content'
                        }
                    }]
                })
            elif openai_client:
                # Your OpenAI logic is correct
                ai_history.append({
                    "role": "tool", 
                    "tool_call_id": "call_id_placeholder", 
                    "content": json.dumps(msg.tool_outputs.get("content"))
                })
    
    ai_history.append({"role": "user", "parts": [user_message_text]})

    async def event_stream():
        full_ai_response_text = ""
        tool_call_occurred = False
        tool_call_info = None
        tool_output_info = None

        try:
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
                        openai_messages.append({"role": "system", "content": msg["content"]}) # Corrected for system message content

                response_stream = openai_client.chat.completions.create(
                    model="gpt-3.5-turbo",
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
                    if chunk.choices:
                        delta = chunk.choices[0].delta
                        if delta.content:
                            full_ai_response_text += delta.content
                            yield f"data: {json.dumps({'type': 'text', 'content': delta.content})}\n\n"
                        if delta.tool_calls:
                            tool_call_occurred = True
                            for tc in delta.tool_calls:
                                if tc.function:
                                    tool_call_info = {
                                        "name": tc.function.name,
                                        "args": json.loads(tc.function.arguments) if tc.function.arguments else {}
                                    }
                                    debug_print(f"AI requested tool call: {tool_call_info}")
                                    yield f"data: {json.dumps({'type': 'tool_call', 'content': tool_call_info})}\n\n"

            if tool_call_occurred and tool_call_info:
                tool_name = tool_call_info["name"]
                tool_args = tool_call_info["args"]

                # Override user_id for get_user_locations tool with the actual user_id from the request
                if tool_name == "get_user_locations":
                    debug_print(f"Overriding user_id for get_user_locations from {tool_args.get('user_id')} to {user_id}")
                    tool_args["user_id"] = user_id
                
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

                    # --- RESTRUCTURED ORCHESTRATION AND FOLLOW-UP LOGIC ---

                    # PATH 1: Handle the special multi-turn location-based search
                    if tool_name == "get_user_locations":
                        locations = tool_output_info["content"]
                        if locations and len(locations.get("locations", [])) > 0:
                            first_location = locations["locations"][0]
                            lat = first_location.get("latitude")
                            lon = first_location.get("longitude")

                            if lat is not None and lon is not None:
                                # This is the "happy path" for location search.
                                # Perform the multi-step orchestration and make the follow-up call.
                                debug_print(f"Found user location: lat={lat}, lon={lon}. Calling find_nearby_stores_tool_v2...")
                                nearby_stores_output = await find_nearby_stores_tool_v2(lat=float(lat), lon=float(lon), radius_meters=1500)
                                debug_print(f"Nearby stores output: {nearby_stores_output}")

                                # Append both tool outputs to history
                                if gemini_client:
                                    ai_history.append({
                                        "role": "user",
                                        "parts": [{
                                            "function_response": {
                                                "name": "get_user_locations",
                                                "response": tool_output_info["content"] # Pass directly
                                            }
                                        }]
                                    })
                                    ai_history.append({
                                        "role": "user",
                                        "parts": [{
                                            "function_response": {
                                                "name": "find_nearby_stores_v2",
                                                "response": nearby_stores_output # Pass directly
                                            }
                                        }]
                                    })
                                elif openai_client:
                                    ai_history.append({"role": "tool", "tool_call_id": "call_id_placeholder", "content": json.dumps(tool_output_info["content"])})
                                    ai_history.append({"role": "tool", "tool_call_id": "call_id_placeholder", "content": json.dumps(nearby_stores_output)})
                                
                                # Re-add user message
                                ai_history.append({"role": "user", "parts": [user_message_text]})

                                # Make a dedicated follow-up AI call
                                debug_print("Making dedicated follow-up AI call for location orchestration...")
                                follow_up_response_text = ""
                                if gemini_client:
                                    follow_up_stream = gemini_client.generate_content(
                                        ai_history,
                                        tools=gemini_tools,
                                        stream=True
                                    )
                                elif openai_client:
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
                                        elif msg["role"] == "system":
                                            openai_messages_follow_up.append({"role": "system", "content": msg["content"]})

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
                                full_ai_response_text = follow_up_response_text # Set the final response text
                            else:
                                debug_print("User location found but missing lat/lon. Informing user.")
                                full_ai_response_text = "Pronašao/pronašla sam vašu spremljenu lokaciju, ali nedostaju zemljopisna širina i dužina. Ažurirajte detalje lokacije kako biste omogućili pretraživanje temeljeno na lokaciji."
                        else:
                            debug_print("No user locations found. Informing user.")
                            full_ai_response_text = "Nisam pronašao/pronašla spremljene lokacije za vas. Dodajte lokaciju kako biste omogućili pretraživanje temeljeno na lokaciji."
                    
                    # PATH 2: Handle the general follow-up for ALL OTHER tools
                    else: # This 'else' block now correctly handles every tool *except* get_user_locations.
                        debug_print("Making general follow-up AI call with tool output...")
                        follow_up_response_text = ""
                        # Add the current tool output to history before making the follow-up call
                        if gemini_client:
                            ai_history.append({
                                "role": "user",
                                "parts": [{
                                    "function_response": {
                                        "name": tool_output_info["name"],
                                        "response": tool_output_info["content"] # Pass directly
                                    }
                                }]
                            })
                        elif openai_client:
                            ai_history.append({"role": "tool", "tool_call_id": "call_id_placeholder", "content": json.dumps(tool_output_info["content"])})

                        if gemini_client:
                            follow_up_stream = gemini_client.generate_content(
                                ai_history,
                                tools=gemini_tools,
                                stream=True
                            )
                        elif openai_client:
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
                                elif msg["role"] == "system":
                                    openai_messages_follow_up.append({"role": "system", "content": msg["content"]})

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
                        full_ai_response_text = follow_up_response_text
                else:
                    error_message = f"Alat '{tool_name}' nije pronađen."
                    debug_print(error_message)
                    yield f"data: {json.dumps({'type': 'error', 'content': error_message})}\n\n"
                    full_ai_response_text = error_message

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
            debug_print(f"Error in chat_endpoint_v2: {e}")
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
print("<<< Finished importing in chat_v2.py")
