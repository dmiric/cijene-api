import logging # Import logging
import dataclasses # Import dataclasses
from fastapi import APIRouter, HTTPException, status, Depends
from typing import List, Optional
from uuid import UUID
from datetime import datetime
from decimal import Decimal

from service.config import get_settings
from service.db.models import UserLocation, UserPersonalData
from service.routers.auth import RequireAuth
from pydantic import BaseModel

logger = logging.getLogger(__name__) # Initialize logger

router = APIRouter(tags=["User Locations V2"])
db = get_settings().get_db()

# Pydantic Models for V2 User Location Endpoints
class UserLocationCreateRequest(BaseModel):
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    country: Optional[str] = None
    latitude: Optional[Decimal] = None
    longitude: Optional[Decimal] = None
    location_name: Optional[str] = None

class UserLocationResponse(BaseModel):
    id: int
    user_id: UUID
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    country: Optional[str] = None
    latitude: Optional[Decimal] = None
    longitude: Optional[Decimal] = None
    location_name: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None

class UserLocationUpdateRequest(BaseModel):
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    country: Optional[str] = None
    latitude: Optional[Decimal] = None
    longitude: Optional[Decimal] = None
    location_name: Optional[str] = None

@router.post("/user_locations", response_model=UserLocationResponse, status_code=status.HTTP_201_CREATED)
async def add_user_location(
    location_data: UserLocationCreateRequest,
    current_user_personal_data: UserPersonalData = RequireAuth
):
    """
    Add a new location for the authenticated user.
    """
    logger.debug(f"Attempting to add user location for user_id: {current_user_personal_data.user_id}")
    logger.debug(f"Incoming location_data: {location_data.dict(exclude_unset=True)}")
    try:
        new_location = await db.users.add_user_location(
            user_id=current_user_personal_data.user_id,
            location_data=location_data.dict(exclude_unset=True)
        )
        logger.debug(f"Successfully added new location: {new_location}")
        return UserLocationResponse(**dataclasses.asdict(new_location)) # Corrected: Use dataclasses.asdict()
    except Exception as e:
        logger.error(f"Error adding user location for user_id {current_user_personal_data.user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Error adding user location: {e}")

@router.get("/user_locations", response_model=List[UserLocationResponse])
async def get_user_locations(current_user_personal_data: UserPersonalData = RequireAuth): # Removed Depends()
    """
    Get all active locations for the authenticated user.
    """
    try:
        locations = await db.users.get_user_locations_by_user_id(current_user_personal_data.user_id)
        return [UserLocationResponse(**loc) for loc in locations]
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error retrieving user locations: {e}")

@router.get("/user_locations/{location_id}", response_model=UserLocationResponse)
async def get_user_location_by_id(
    location_id: int,
    current_user_personal_data: UserPersonalData = RequireAuth # Removed Depends()
):
    """
    Get a specific active user location by its ID and the authenticated user's ID.
    """
    try:
        location = await db.users.get_user_location_by_id(current_user_personal_data.user_id, location_id)
        if not location:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User location not found.")
        return UserLocationResponse(**dataclasses.asdict(location)) # Corrected: Use dataclasses.asdict()
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error retrieving user location: {e}")

@router.put("/user_locations/{location_id}", response_model=UserLocationResponse)
async def update_user_location(
    location_id: int,
    location_update: UserLocationUpdateRequest,
    current_user_personal_data: UserPersonalData = RequireAuth # Removed Depends()
):
    """
    Update a specific active user location.
    """
    try:
        success = await db.users.update_user_location(
            location_id=location_id,
            user_id=current_user_personal_data.user_id,
            **location_update.dict(exclude_unset=True)
        )
        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User location not found or not updated.")
        
        # Retrieve the updated location to return in the response
        updated_location = await db.users.get_user_location_by_id(current_user_personal_data.user_id, location_id)
        if not updated_location:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Updated location could not be retrieved.")
        return UserLocationResponse(**dataclasses.asdict(updated_location)) # Corrected: Use dataclasses.asdict()
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error updating user location: {e}")

@router.delete("/user_locations/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
async def soft_delete_user_location(
    location_id: int,
    current_user_personal_data: UserPersonalData = RequireAuth # Removed Depends()
):
    """
    Soft-delete a specific user location (sets deleted_at timestamp).
    """
    try:
        success = await db.users.delete_user_location(current_user_personal_data.user_id, location_id)
        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User location not found or already deleted.")
        return
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error soft-deleting user location: {e}")
