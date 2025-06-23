from decimal import Decimal
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
import sys
from typing import Any, Optional

from service.config import settings
from service.db.models import Store, StoreWithId, ChainWithId, User
from service.routers.auth import RequireAuth
from fastapi import Depends

router = APIRouter(tags=["Stores"], dependencies=[RequireAuth])
db = settings.get_db()

def debug_print(*args, **kwargs):
    print("[DEBUG stores]", *args, file=sys.stderr, **kwargs)


class ListChainsResponse(BaseModel):
    """List chains response schema."""

    chains: list[str] = Field(..., description="List of retail chain codes.")


@router.get("/chains/", summary="List retail chains")
async def list_chains() -> ListChainsResponse:
    """List all available chains."""
    chains = await db.list_chains()
    return ListChainsResponse(chains=[chain.code for chain in chains])


class StoreResponse(BaseModel):
    """Store response schema."""

    chain_code: str = Field(..., description="Code of the retail chain.")
    code: str = Field(..., description="Unique code of the store.")
    type: str | None = Field(
        ...,
        description="Type of the store (e.g., supermarket, hypermarket).",
    )
    address: str | None = Field(..., description="Physical address of the store.")
    city: str | None = Field(..., description="City where the store is located.")
    zipcode: str | None = Field(..., description="Postal code of the store location.")
    lat: Decimal | None = Field(..., description="Latitude coordinate of the store.")
    lon: Decimal | None = Field(..., description="Longitude coordinate of the store.")
    phone: str | None = Field(..., description="Phone number of the store.")


class ListStoresResponse(BaseModel):
    """List stores response schema."""

    stores: list[StoreResponse] = Field(
        ..., description="List stores for the specified chain."
    )


@router.get(
    "/{chain_code}/stores/",
    summary="List retail chain stores",
)
async def list_stores(chain_code: str) -> ListStoresResponse:
    """
    List all stores (locations) for a particular chain.

    Future plan: Allow filtering by store type and location.
    """
    stores = await db.list_stores(chain_code)

    if not stores:
        raise HTTPException(status_code=404, detail=f"No chain {chain_code}")

    return ListStoresResponse(
        stores=[
            StoreResponse(
                chain_code=chain_code,
                code=store.code,
                type=store.type,
                address=store.address,
                city=store.city,
                zipcode=store.zipcode,
                lat=store.lat,
                lon=store.lon,
                phone=store.phone,
            )
            for store in stores
        ]
    )


@router.get("/stores/", summary="Search stores")
async def search_stores(
    chains: str = Query(
        None,
        description="Comma-separated list of chain codes to include, or all",
    ),
    city: str = Query(
        None,
        description="City name for case-insensitive substring match",
    ),
    address: str = Query(
        None,
        description="Address for case-insensitive substring match",
    ),
    lat: Decimal = Query(
        None,
        description="Latitude coordinate for geolocation search",
    ),
    lon: Decimal = Query(
        None,
        description="Longitude coordinate for geolocation search",
    ),
    d: float = Query(
        10.0,
        description="Distance in kilometers for geolocation search (default: 10.0)",
    ),
) -> ListStoresResponse:
    """
    Search for stores by chain codes, city, address, and/or geolocation.

    For geolocation search, both lat and lon must be provided together.
    Note that the geolocation search will only return stores that have
    the geo information available in the database.
    """
    # Validate lat/lon parameters
    if (lat is None) != (lon is None):
        raise HTTPException(
            status_code=400,
            detail="Both lat and lon must be provided for geolocation search",
        )

    # Parse chain codes
    chain_codes = None
    if chains:
        chain_codes = [c.strip().lower() for c in chains.split(",") if c.strip()]

    try:
        stores = await db.filter_stores(
            chain_codes=chain_codes,
            city=city,
            address=address,
            lat=lat,
            lon=lon,
            d=d,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Get chain code mapping for response
    chains_map = {}
    if stores:
        all_chains = await db.list_chains()
        chains_map = {chain.id: chain.code for chain in all_chains}

    return ListStoresResponse(
        stores=[
            StoreResponse(
                chain_code=chains_map.get(store.chain_id, "unknown"),
                code=store.code,
                type=store.type,
                address=store.address,
                city=store.city,
                zipcode=store.zipcode,
                lat=store.lat,
                lon=store.lon,
                phone=store.phone,
            )
            for store in stores
        ]
    )


class NearbyStoreResponse(BaseModel):
    """Response schema for a single nearby store."""
    id: int = Field(..., description="Unique ID of the store.")
    chain_code: str = Field(..., description="Code of the retail chain.")
    code: str = Field(..., description="Unique code of the store.")
    type: str | None = Field(None, description="Type of the store.")
    address: str | None = Field(None, description="Physical address of the store.")
    city: str | None = Field(None, description="City where the store is located.")
    zipcode: str | None = Field(None, description="Postal code of the store location.")
    lat: Decimal | None = Field(None, description="Latitude of the store.")
    lon: Decimal | None = Field(None, description="Longitude of the store.")
    distance_meters: Decimal | None = Field(None, description="Distance from the query point in meters.")


class ListNearbyStoresResponse(BaseModel):
    """List nearby stores response schema."""
    stores: list[NearbyStoreResponse] = Field(
        ..., description="List of stores within the specified radius, ordered by distance."
    )


@router.get("/stores/nearby/", summary="Find stores within a given radius")
async def list_nearby_stores(
    lat: Decimal = Query(..., description="Latitude of the center point."),
    lon: Decimal = Query(..., description="Longitude of the center point."),
    radius_meters: int = Query(..., description="Radius in meters to search within."),
    chain_code: str | None = Query(None, description="Optional: Filter by chain code."),
) -> ListNearbyStoresResponse:
    """
    Finds and lists stores within a specified radius of a given lat/lon.
    Results are ordered by distance from the center point.
    """
    stores_data = await db.get_stores_within_radius(
        lat=lat,
        lon=lon,
        radius_meters=radius_meters,
        chain_code=chain_code,
    )

    response_stores = []
    for store_data in stores_data:
        response_stores.append(
            NearbyStoreResponse(
                id=store_data["id"],
                chain_code=store_data["chain_code"],
                code=store_data["code"],
                type=store_data["type"],
                address=store_data["address"],
                city=store_data["city"],
                zipcode=store_data["zipcode"],
                lat=store_data["lat"],
                lon=store_data["lon"],
                distance_meters=store_data["distance_meters"],
            )
        )

    return ListNearbyStoresResponse(stores=response_stores)
