from decimal import Decimal
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
import datetime
import sys # Import sys for direct print to stderr
from dataclasses import asdict # Import asdict

from service.config import settings
from service.db.models import ProductWithId, User, UserLocation # noqa: F401 # Import User and UserLocation
from service.routers.auth import RequireAuth
from fastapi import Depends # noqa: F401 # Import Depends for user injection

router = APIRouter(tags=["Products, Chains and Stores"], dependencies=[RequireAuth])
db = settings.get_db()

# Using print for debugging as logging is not appearing reliably
def debug_print(*args, **kwargs):
    print("[DEBUG v1]", *args, file=sys.stderr, **kwargs)


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
            )
            for store in stores
        ]
    )


@router.post("/users/{user_id}/locations/", summary="Create a new user location", status_code=status.HTTP_201_CREATED)
async def create_user_location(
    user_id: int,
    location_data: UserLocationCreate,
    current_user: User = RequireAuth,
) -> UserLocationResponse:
    """
    Create a new location for a specific user.
    Access is restricted to the user who owns the location.
    """
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to create locations for this user.",
        )

    new_location = await db.add_user_location(user_id, location_data.model_dump())
    return UserLocationResponse(**asdict(new_location))


@router.get("/users/{user_id}/locations/", summary="List user locations")
async def list_user_locations(
    user_id: int,
    current_user: User = RequireAuth,
) -> ListUserLocationsResponse:
    """
    List all locations for a specific user.
    Access is restricted to the user who owns the locations.
    """
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to view locations for this user.",
        )

    locations = await db.get_user_locations_by_user_id(user_id)
    return ListUserLocationsResponse(locations=[UserLocationResponse(**asdict(loc)) for loc in locations])


@router.get("/users/{user_id}/locations/{location_id}", summary="Get a specific user location")
async def get_user_location(
    user_id: int,
    location_id: int,
    current_user: User = RequireAuth,
) -> UserLocationResponse:
    """
    Get a specific location for a user by its ID.
    Access is restricted to the user who owns the location.
    """
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to view this location.",
        )

    location = await db.get_user_location_by_id(user_id, location_id)
    if not location:
        raise HTTPException(status_code=404, detail="User location not found.")
    return UserLocationResponse(**asdict(location))


@router.put("/users/{user_id}/locations/{location_id}", summary="Update a user location")
async def update_user_location(
    user_id: int,
    location_id: int,
    location_data: UserLocationCreate,
    current_user: User = RequireAuth,
) -> UserLocationResponse:
    """
    Update an existing location for a specific user.
    Access is restricted to the user who owns the location.
    """
    debug_print(f"Received update request for user {user_id}, location {location_id} with data: {location_data.model_dump()}")
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to update this location.",
        )

    updated_location = await db.update_user_location(user_id, location_id, location_data.model_dump())
    if not updated_location:
        raise HTTPException(status_code=404, detail="User location not found or not authorized.")
    return UserLocationResponse(**asdict(updated_location))


@router.delete("/users/{user_id}/locations/{location_id}", summary="Delete a user location", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_location(
    user_id: int,
    location_id: int,
    current_user: User = RequireAuth,
):
    """
    Delete a specific location for a user.
    Access is restricted to the user who owns the location.
    """
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to delete this location.",
        )

    deleted = await db.delete_user_location(user_id, location_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="User location not found or not authorized.")
    return {"message": "Location deleted successfully."}


class StorePriceResponse(BaseModel):
    """Response schema for a single store's price."""
    price_date: datetime.date = Field(..., description="Date of the price.")
    regular_price: Decimal = Field(..., description="Regular price at this store.")
    special_offer: bool | None = Field(None, description="True if there is a special price/offer.") # Made optional
    unit_price: Decimal | None = Field(None, description="Unit price at this store.")
    anchor_price: Decimal | None = Field(None, description="Anchor price at this store.")


class ChainProductResponse(BaseModel):
    """Chain product with individual store price information response schema."""

    chain: str = Field(..., description="Chain code.")
    quantity: str | None = Field(..., description="Product quantity within the chain.")
    store_prices: list[StorePriceResponse] = Field(..., description="List of individual store prices.")


class ProductResponse(BaseModel):
    """Basic product information response schema."""

    ean: str = Field(..., description="EAN barcode of the product.")
    brand: str | None = Field(None, description="Brand of the product.") # Removed or ""
    name: str | None = Field(None, description="Name of the product.") # Removed or ""
    chains: list[ChainProductResponse] = Field(
        ..., description="List of chain-specific product information."
    )


class ProductSearchResponse(BaseModel):
    products: list[ProductResponse] = Field(
        ..., description="List of products matching the search query."
    )


class SearchKeywordsGenerationItem(BaseModel):
    ean: str = Field(..., description="EAN barcode of the product.")
    product_name: str = Field(..., description="Name of the product.")
    brand_name: str | None = Field(None, description="Brand name of the product.")


class SearchKeywordsGenerationResponse(BaseModel):
    items: list[SearchKeywordsGenerationItem] = Field(
        ..., description="List of products suitable for keyword generation."
    )


@router.get("/search-keywords/", summary="Get products for keyword generation")
async def get_products_for_keyword_generation(
    current_user: User = RequireAuth, # Corrected: Removed Depends() wrapper
    limit: int = Query(100, description="Maximum number of items to return."),
    product_name_filter: str | None = Query(
        None, description="Optional: Filter products by name using a LIKE condition."
    ),
) -> SearchKeywordsGenerationResponse:
    """
    Returns a list of products (EAN, product name, and brand name) that are not yet in the
    search_keywords table, suitable for generating new keywords.
    Access is restricted to user 'dmiric'.
    """
    if current_user.name != "dmiric":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only user 'dmiric' can access this endpoint.",
        )

    products_data = await db.get_products_for_keyword_generation(
        limit=limit, product_name_filter=product_name_filter
    )
    return SearchKeywordsGenerationResponse(
        items=[SearchKeywordsGenerationItem(**item) for item in products_data]
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
    latitude: Decimal | None = Field(None, description="Latitude of the store.")
    longitude: Decimal | None = Field(None, description="Longitude of the store.")
    distance_meters: Decimal | None = Field(None, description="Distance from the query point in meters.")


class ListNearbyStoresResponse(BaseModel):
    """List nearby stores response schema."""
    stores: list[NearbyStoreResponse] = Field(
        ..., description="List of stores within the specified radius, ordered by distance."
    )


@router.get("/stores/nearby/", summary="Find stores within a given radius")
async def list_nearby_stores(
    latitude: Decimal = Query(..., description="Latitude of the center point."),
    longitude: Decimal = Query(..., description="Longitude of the center point."),
    radius_meters: int = Query(..., description="Radius in meters to search within."),
    chain_code: str | None = Query(None, description="Optional: Filter by chain code."),
) -> ListNearbyStoresResponse:
    """
    Finds and lists stores within a specified radius of a given latitude/longitude.
    Results are ordered by distance from the center point.
    """
    stores_data = await db.get_stores_within_radius(
        latitude=latitude,
        longitude=longitude,
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
                latitude=store_data["latitude"],
                longitude=store_data["longitude"],
                distance_meters=store_data["distance_meters"],
            )
        )

    return ListNearbyStoresResponse(stores=response_stores)


async def prepare_product_response(
    products: list[ProductWithId],
    date: datetime.date | None,
    filtered_chains: list[str] | None,
    filtered_store_ids: list[int] | None = None,
) -> list[ProductResponse]:
    debug_print(f"prepare_product_response: products_count={len(products)}, date={date}, filtered_chains={filtered_chains}, filtered_store_ids={filtered_store_ids}")

    chains = await db.list_chains()
    if filtered_chains:
        chains = [c for c in chains if c.code in filtered_chains]
    chain_id_to_code = {chain.id: chain.code for chain in chains}

    if not date:
        date = datetime.date.today()

    product_ids = [product.id for product in products]

    chain_products = await db.get_chain_products_for_product(
        product_ids,
        [chain.id for chain in chains],
    )
    debug_print(f"prepare_product_response: Found {len(chain_products)} chain products.")


    product_response_map = {
        product.id: ProductResponse(
            ean=product.ean,
            brand=product.brand, # Pass None if product.brand is None
            name=product.name,   # Pass None if product.name is None
            chains=[], # Removed quantity and unit
        )
        for product in products
    }

    # Group prices by chain_product_id
    prices_by_chain_product = {}
    prices = await db.get_product_prices(product_ids, date, filtered_store_ids)
    debug_print(f"prepare_product_response: Fetched {len(prices)} price entries from db.get_product_prices.")

    for p in prices:
        chain_product_id = p["chain_product_id"]
        if chain_product_id not in prices_by_chain_product:
            prices_by_chain_product[chain_product_id] = []
        prices_by_chain_product[chain_product_id].append(p)

    for cp in chain_products:
        product_id = cp.product_id
        chain_code = chain_id_to_code[cp.chain_id]

        cpr_data = {
            "chain": chain_code,
            "quantity": cp.quantity, # Keep quantity at chain level
            "store_prices": [] # Initialize store_prices list
        }
        
        store_prices_list = []
        for price_entry in prices_by_chain_product.get(cp.id, []):
            store_prices_data = {
                "price_date": price_entry["price_date"],
                "regular_price": price_entry["regular_price"],
                "unit_price": price_entry["unit_price"],
                "anchor_price": price_entry["anchor_price"],
                "special_offer": price_entry["special_price"] is not None, # Always set special_offer
            }
            
            store_prices_list.append(StorePriceResponse(**store_prices_data))
        
        cpr_data["store_prices"] = store_prices_list
        
        if store_prices_list: # Only append if there are actual store prices
            product_response_map[product_id].chains.append(ChainProductResponse(**cpr_data))

    # Fixup global product brand and name using original chain product data
    # This loop iterates over the original products and tries to fill in missing brand/name
    # from their associated chain products.
    for product_id, product_response in product_response_map.items():
        if not product_response.brand or not product_response.name:
            # Find the original ProductWithId object
            original_product = next((p for p in products if p.id == product_id), None)
            if original_product:
                # Iterate through its associated chain products to find brand/name
                for cp in chain_products:
                    if cp.product_id == product_id:
                        if not product_response.brand and cp.brand:
                            product_response.brand = cp.brand.capitalize()
                        if not product_response.name and cp.name:
                            product_response.name = cp.name.capitalize()
                        if product_response.brand and product_response.name:
                            break # Found both, no need to check further chain products

    return [p for p in product_response_map.values() if p.chains]


@router.get("/products/{ean}/", summary="Get product data/prices by barcode")
async def get_product(
    ean: str,
    date: datetime.date = Query(
        None,
        description="Date in YYYY-MM-DD format, defaults to today",
    ),
    chains: str = Query(
        None,
        description="Comma-separated list of chain codes to include",
    ),
) -> ProductResponse:
    """
    Get product information including chain products and prices by their
    barcode. For products that don't have official EAN codes and use
    chain-specific codes, use the "chain:<product_code>" format.

    The price information is for the last known date earlier than or
    equal to the specified date. If no date is provided, current date is used.
    """

    products = await db.get_products_by_ean([ean])
    if not products:
        raise HTTPException(
            status_code=404,
            detail=f"Product with EAN {ean} not found",
        )

    product_responses = await prepare_product_response(
        products=products,
        date=date,
        filtered_chains=(
            [c.lower().strip() for c in chains.split(",")] if chains else None
        ),
    )

    if not product_responses:
        with_chains = " with specified chains" if chains else ""
        raise HTTPException(
            status_code=404,
            detail=f"No product information found for EAN {ean}{with_chains}",
        )

    return product_responses[0]


@router.get("/products/", summary="Search for products by name")
async def search_products(
    q: str = Query(..., description="Search query for product names"),
    date: datetime.date = Query(
        None,
        description="Date in YYYY-MM-DD format, defaults to today",
    ),
    chains: str = Query(
        None,
        description="Comma-separated list of chain codes to include",
    ),
    store_ids: str | None = Query(
        None,
        description="Comma-separated list of store IDs to filter by",
    ),
) -> ProductSearchResponse:
    """
    Search for products by name.

    Returns a list of products that match the search query.
    """
    debug_print(f"search_products: q='{q}', date='{date}', chains='{chains}', store_ids='{store_ids}'")

    if not q.strip():
        debug_print("search_products: Empty query, returning empty products.")
        return ProductSearchResponse(products=[])

    products = await db.search_products(q)
    debug_print(f"search_products: Found {len(products)} products for query '{q}'.")

    # Parse store_ids if provided
    parsed_store_ids = None
    if store_ids:
        try:
            parsed_store_ids = [int(s.strip()) for s in store_ids.split(',') if s.strip()]
            debug_print(f"search_products: Parsed store_ids: {parsed_store_ids}")
        except ValueError:
            debug_print(f"search_products: Invalid store_ids format: '{store_ids}'")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid store_ids format. Must be a comma-separated list of integers."
            )

    product_responses = await prepare_product_response(
        products=products,
        date=date,
        filtered_chains=(
            [c.lower().strip() for c in chains.split(",")] if chains else None
        ),
        filtered_store_ids=parsed_store_ids,
    )
    debug_print(f"search_products: Prepared {len(product_responses)} product responses.")

    return ProductSearchResponse(products=product_responses)
