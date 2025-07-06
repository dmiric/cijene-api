# print(">>> Importing chat_v2.py") -> You can keep or remove this
from fastapi import APIRouter, Depends, status, Request
from fastapi.responses import StreamingResponse
from uuid import UUID, uuid4

from service.config import get_settings
from service.routers.auth import verify_authentication, RequireAuth
from service.routers.v2.chat_components.initial_context import INITIAL_SYSTEM_INSTRUCTIONS

# --- Import our new refactored components ---
from .chat_components.ai_providers import get_ai_provider
from .chat_components.chat_orchestrator import ChatOrchestrator
from .chat_components.ai_schemas import ChatRequest
from service.db.models import UserPersonalData # Import UserPersonalData
from service.utils.timing import debug_print # Import debug_print

router = APIRouter(tags=["AI Chat V2"], dependencies=[Depends(verify_authentication)])
db = get_settings().get_db()

# --- NEW: Use a dependency for shared setup logic ---
async def get_chat_context(auth: RequireAuth = Depends(verify_authentication)) -> dict:
    """Dependency to prepare common chat context."""
    user_id = auth.user_id
    _, user_personal_data = await db.users.get_user_by_id(user_id)
    
    system_instructions = list(INITIAL_SYSTEM_INSTRUCTIONS)
    if user_personal_data and user_personal_data.name:
        system_instructions.append(f"Korisnik se zove {user_personal_data.name}.")
        
    return {
        "user_id": user_id,
        "system_instructions": system_instructions
    }

@router.post("/chat_v2", response_class=StreamingResponse, status_code=status.HTTP_200_OK)
async def event_stream_post(
    chat_request: ChatRequest,
    context: dict = Depends(get_chat_context)
):
    """Handles a new or continuing chat conversation via POST."""
    session_id = chat_request.session_id or uuid4()
    user_id = context["user_id"]
    debug_print(f"[chat.py] Received chat request for user_id: {user_id}, session_id: {session_id}")
    
    # Save the user's message
    await db.chat.save_chat_message(
        user_id=user_id,
        session_id=session_id,
        message_text=chat_request.message_text,
        is_user_message=True
    )

    # Initialize and run the orchestrator
    ai_provider = get_ai_provider()
    orchestrator = ChatOrchestrator(
        user_id=user_id,
        session_id=session_id,
        db=db,
        ai_provider=ai_provider,
        system_instructions=context["system_instructions"]
    )
    
    generator = orchestrator.stream_response(chat_request.message_text)
    return StreamingResponse(generator, media_type="text/event-stream")

@router.get("/chat_v2/stream/{session_id}", response_class=StreamingResponse, status_code=status.HTTP_200_OK)
async def event_stream_get(
    session_id: UUID,
    context: dict = Depends(get_chat_context)
):
    """
    Continues a chat stream for an existing session. 
    Note: This will re-evaluate the conversation based on history.
    """
    user_id = context["user_id"]
    debug_print(f"[chat.py] Received GET request for user_id: {user_id}, session_id: {session_id}")
    ai_provider = get_ai_provider()
    orchestrator = ChatOrchestrator(
        user_id=context["user_id"],
        session_id=session_id,
        db=db,
        ai_provider=ai_provider,
        system_instructions=context["system_instructions"]
    )

    # The user_message_text is None, as this GET request does not add a new message.
    # The orchestrator will use the existing history to generate the next step.
    generator = orchestrator.stream_response(user_message_text=None)
    return StreamingResponse(generator, media_type="text/event-stream")
