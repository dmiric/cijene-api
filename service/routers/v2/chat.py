# service/routers/v2/chat.py

from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse
from uuid import UUID, uuid4

from service.config import get_settings
from service.routers.auth import verify_authentication, RequireAuth
from service.routers.v2.chat_components.initial_context import INITIAL_SYSTEM_INSTRUCTIONS

# --- We no longer need to import get_ai_provider here ---
from .chat_components.chat_orchestrator import ChatOrchestrator
from .chat_components.ai_schemas import ChatRequest
from service.utils.timing import debug_print

router = APIRouter(tags=["AI Chat V2"], dependencies=[Depends(verify_authentication)])
db = get_settings().get_db()

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
    
    orchestrator = ChatOrchestrator(
        user_id=user_id,
        session_id=session_id,
        db=db,
        system_instructions=context["system_instructions"],
        location_info=chat_request.location_info
    )
    
    generator = orchestrator.stream_response(chat_request.message_text)
    return StreamingResponse(generator, media_type="text/event-stream")
