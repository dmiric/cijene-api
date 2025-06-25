from decimal import Decimal
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from typing import Any, Optional, List
import sys

from service.config import settings
from service.routers.auth import RequireAuth
from fastapi import Depends

router = APIRouter(tags=["Stores V2"], dependencies=[RequireAuth])
db_v2 = settings.get_db_v2() # Assuming this will be added to settings.py

def debug_print(*args, **kwargs):
    print("[DEBUG stores_v2]", *args, file=sys.stderr, **kwargs)

# Pydantic Models for Responses

class NearbyStoreResponseV2(BaseModel):
    id: int = Field(..., description="Unique ID of the store.")
    name: str = Field(..., description="Name of the store.")
    address: Optional[str] = Field(None, description="Physical address of the store.")
    city: Optional[str] = Field(None, description="City where the store is located.")
    zipcode: Optional[str] = Field(None, description="Postal code of the store location.")
    latitude: Optional[Decimal] = Field(None, description="Latitude coordinate of the store.")
    longitude: Optional[Decimal] = Field(None, description="Longitude coordinate of the store.")
    chain_code: Optional[str] = Field(None, description="Code of the retail chain.")
    distance_meters: Optional[Decimal] = Field(None, description="Distance from the query point in meters.")

    class Config:
        json_encoders = {
            Decimal: float
        }

class ListNearbyStoresResponseV2(BaseModel):
    stores: List[NearbyStoreResponseV2] = Field(
        ..., description="List of stores within the specified radius, ordered by distance."
    )

# API Endpoints

@router.get("/stores/nearby", summary="Find Nearby Stores (v2)")
async def find_nearby_stores_v2(
    lat: float = Query(..., description="Latitude of the center point."),
    lon: float = Query(..., description="Longitude of the center point."),
    radius_meters: int = Query(5000, ge=0, description="Radius in meters to search within."),
    chain_code: Optional[str] = Query(None, description="Optional. To filter by a specific chain like 'konzum', 'lidl'"),
) -> ListNearbyStoresResponseV2:
    """
    Finds stores within a specified radius of a geographic point.
    Returns a list of store objects, ordered by distance from the user.
    """
    debug_print(f"find_nearby_stores_v2: lat={lat}, lon={lon}, radius_meters={radius_meters}, chain_code={chain_code}")
    
    stores_data = await db_v2.get_g_stores_nearby(
        lat=lat,
        lon=lon,
        radius_meters=radius_meters,
        chain_code=chain_code,
    )
    
    return ListNearbyStoresResponseV2(stores=[NearbyStoreResponseV2(**s) for s in stores_data])
