import time
import logging
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from service.config import settings
from service.db.models import User, UserPersonalData # Import UserPersonalData

logger = logging.getLogger(__name__)

# Security scheme for OpenAPI documentation
security_scheme = HTTPBearer(scheme_name="HTTPBearer")

# Simple in-memory cache for authentication results
# Structure: {api_key: (user_personal_data_or_none, timestamp)}
_auth_cache: dict[str, tuple[UserPersonalData | None, float]] = {} # Cache stores UserPersonalData

# Cache durations in seconds
CACHE_HIT_TTL = 3600  # 60 minutes for valid users
CACHE_MISS_TTL = 60  # 60 seconds for invalid tokens
CACHE_MAX_SIZE = 10000  # Maximum cache size before cleanup

db = settings.get_db()


async def _lookup_user_by_token(api_key: str) -> UserPersonalData | None: # Return UserPersonalData
    """
    Lookup user by API key with caching.

    Args:
        api_key: The API key to look up.

    Returns:
        UserPersonalData object if found and active, None otherwise.
    """
    now = time.time()

    # Check cache first
    if api_key in _auth_cache:
        cached_user_personal_data, timestamp = _auth_cache[api_key]
        age = now - timestamp

        if (cached_user_personal_data and age < CACHE_HIT_TTL) or (
            not cached_user_personal_data and age < CACHE_MISS_TTL
        ):
            # Cache hit and still valid
            _auth_cache[api_key] = (cached_user_personal_data, now)  # Update timestamp
            return cached_user_personal_data
        else:
            # Need to refresh
            del _auth_cache[api_key]

    user_personal_data = await db.users.get_user_by_api_key(api_key) # Call db.users.get_user_by_api_key
    _auth_cache[api_key] = (user_personal_data, now)

    if len(_auth_cache) > CACHE_MAX_SIZE:
        # Remove all miss entries from cache to prevent memory bloat
        _to_remove = [k for k, v in _auth_cache.items() if v[0] is not None]
        for k in _to_remove:
            del _auth_cache[k]
    return user_personal_data


async def verify_authentication(
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
) -> UserPersonalData: # Return UserPersonalData
    """
    Verify bearer token authentication.

    Args:
        credentials: The HTTP authorization credentials containing the bearer token.

    Returns:
        The authenticated UserPersonalData object.
    """
    api_key = credentials.credentials
    user_personal_data = await _lookup_user_by_token(api_key)

    if not user_personal_data:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unknown API key",
        )

    logger.debug(f"Authenticated access for user: {user_personal_data.name} (id={user_personal_data.user_id})") # Access user_id from user_personal_data
    return user_personal_data


# Dependency for protecting routes
RequireAuth = Depends(verify_authentication)
