from decimal import Decimal
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
import datetime
import sys
from dataclasses import asdict
from typing import Any, Optional

from service.config import settings
from service.db.models import User, UserLocation
from service.routers.auth import RequireAuth
from fastapi import Depends

router = APIRouter(tags=["Users"], dependencies=[RequireAuth])
db = settings.get_db()

def debug_print(*args, **kwargs):
    print("[DEBUG users]", *args, file=sys.stderr, **kwargs)


class UserLocationCreate(BaseModel):
    """Schema for creating a new user location."""
    address: str = Field(..., description="Street address of the location.")
    city: str = Field(..., description="City of the location.")
    state: str | None = Field(None, description="State or province of the location.")
    zip_code: str | None = Field(None, description="Postal code of the location.")
    country: str | None = Field(None, description="Country of the location.")
    latitude: Decimal | None = Field(None, description="Latitude of the location.")
    longitude: Decimal | None = Field(None, description="Longitude of the location.")
    location_name: str | None = Field(None, description="Name for this location (e.g., 'Home', 'Work').")


class UserLocationResponse(BaseModel):
    """Response schema for a user location."""
    id: int = Field(..., description="Unique ID of the user location.")
    user_id: int = Field(..., description="ID of the user this location belongs to.")
    address: str = Field(..., description="Street address of the location.")
    city: str = Field(..., description="City of the location.")
    state: str | None = Field(None, description="State or province of the location.")
    zip_code: str | None = Field(None, description="Postal code of the location.")
    country: str | None = Field(None, description="Country of the location.")
    latitude: Decimal | None = Field(None, description="Latitude of the location.")
    longitude: Decimal | None = Field(None, description="Longitude of the location.")
    location_name: str | None = Field(None, description="Name for this location (e.g., 'Home', 'Work').")
    created_at: datetime.datetime = Field(..., description="Timestamp when the location was created.")
    updated_at: datetime.datetime = Field(..., description="Timestamp when the location was last updated.")


class ListUserLocationsResponse(BaseModel):
    """List user locations response schema."""
    locations: list[UserLocationResponse] = Field(..., description="List of user locations.")


@router.post("/users/{user_id}/locations/", summary="Create a new user location", status_code=status.HTTP_201_CREATED)
async def create_user_location(
    user_id: int,
    location_data: UserLocationCreate,
) -> UserLocationResponse:
    """
    Create a new location for a specific user.
    Access is restricted to the user who owns the location.
    """
    new_location = await db.add_user_location(user_id, location_data.model_dump())
    return UserLocationResponse(**asdict(new_location))


@router.get("/users/{user_id}/locations/", summary="List user locations")
async def list_user_locations(
    user_id: int,
) -> ListUserLocationsResponse:
    """
    List all locations for a specific user.
    Access is restricted to the user who owns the locations.
    """
    locations = await db.get_user_locations_by_user_id(user_id)
    return ListUserLocationsResponse(locations=[UserLocationResponse(**asdict(loc)) for loc in locations])


@router.get("/users/{user_id}/locations/{location_id}", summary="Get a specific user location")
async def get_user_location(
    user_id: int,
    location_id: int,
) -> UserLocationResponse:
    """
    Get a specific location for a user by its ID.
    Access is restricted to the user who owns the location.
    """
    location = await db.get_user_location_by_id(user_id, location_id)
    if not location:
        raise HTTPException(status_code=404, detail="User location not found.")
    return UserLocationResponse(**asdict(location))


@router.put("/users/{user_id}/locations/{location_id}", summary="Update a user location")
async def update_user_location(
    user_id: int,
    location_id: int,
    location_data: UserLocationCreate,
) -> UserLocationResponse:
    """
    Update an existing location for a specific user.
    Access is restricted to the user who owns the location.
    """
    debug_print(f"Received update request for user {user_id}, location {location_id} with data: {location_data.model_dump()}")
    updated_location = await db.update_user_location(user_id, location_id, location_data.model_dump())
    if not updated_location:
        raise HTTPException(status_code=404, detail="User location not found or not authorized.")
    return UserLocationResponse(**asdict(updated_location))


@router.delete("/users/{user_id}/locations/{location_id}", summary="Delete a user location", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_location(
    user_id: int,
    location_id: int,
):
    """
    Delete a specific location for a user.
    Access is restricted to the user who owns the location.
    """
    deleted = await db.delete_user_location(user_id, location_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="User location not found or not authorized.")
    return {"message": "Location deleted successfully."}
