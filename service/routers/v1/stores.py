from decimal import Decimal
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
import sys

from service.config import get_settings
from service.routers.auth import verify_authentication # Import verify_authentication directly
from fastapi import Depends

from service.routers.auth import RequireApiKey # Import RequireApiKey
router = APIRouter(tags=["Stores"], dependencies=[RequireApiKey]) # Use RequireApiKey
db = get_settings().get_db()

def debug_print(*args, **kwargs):
    print("[DEBUG stores]", *args, file=sys.stderr, **kwargs)

class ChainResponse(BaseModel):
    """Chain response schema."""
    id: int
    code: str = Field(..., description="Code of the retail chain.")
    active: bool = Field(..., description="Whether the chain is active.")

class ListChainsResponse(BaseModel):
    """List chains response schema."""

    chains: list[ChainResponse] = Field(..., description="List of retail chain codes.")

@router.get("/chains/", summary="List retail chains")
async def list_chains() -> ListChainsResponse:
    """List all available chains."""
    chains = await db.list_chains()
    return ListChainsResponse(chains=[ChainResponse(id=chain.id, code=chain.code, active=chain.active) for chain in chains])

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
