from service.config import get_settings
from typing import List, Any
from uuid import UUID
from service.db.field_configs import USER_LOCATION_AI_FIELDS

db = get_settings().get_db()

async def get_user_locations_tool(user_id: UUID):
    """
    Retrieves a user's saved locations.
    Args:
        user_id (UUID): The ID of the user.
    """
    try:
        locations = await db.users.get_user_locations_by_user_id(
            user_id=user_id,
            fields=USER_LOCATION_AI_FIELDS
        )
        return {"locations": locations}
    except Exception as e:
        return {"error": str(e)}
