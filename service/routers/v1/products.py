print(">>> Importing products.py")
from decimal import Decimal
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
import datetime
import sys
from typing import Any, Optional

from service.config import settings
from service.db.models import ChainStats, ProductWithId, StorePrice, Store, User
from service.routers.auth import RequireAuth
from fastapi import Depends

router = APIRouter(tags=["Products"], dependencies=[RequireAuth])
db = settings.get_db()

def debug_print(*args, **kwargs):
    print("[DEBUG products]", *args, file=sys.stderr, **kwargs)


class StorePriceResponse(BaseModel):
    """Response schema for a single store's price."""
    price_date: datetime.date = Field(..., description="Date of the price.")
    regular_price: Decimal = Field(..., description="Regular price at this store.")
    special_price: Decimal | None = Field(None, description="Special promotional price at this store.")
    special_offer: bool | None = Field(None, description="True if there is a special price/offer.")
    unit_price: Decimal | None = Field(None, description="Unit price at this store.")
    best_price_30: Decimal | None = Field(None, description="Best (lowest) price in the last 30 days at this store.")
    anchor_price: Decimal | None = Field(None, description="Anchor price at this store.")

    class Config:
        json_encoders = {
            Decimal: float
        }


class ChainProductResponse(BaseModel):
    """Chain product with individual store price information response schema."""

    chain: str = Field(..., description="Chain code.")
    store_prices: list[StorePriceResponse] = Field(..., description="List of individual store prices.")

    class Config:
        json_encoders = {
            Decimal: float
        }


class ProductResponse(BaseModel):
    """Basic product information response schema."""

    ean: str = Field(..., description="EAN barcode of the product.")
    brand: str | None = Field(None, description="Brand of the product.")
    name: str | None = Field(None, description="Name of the product.")
    quantity: Decimal | None = Field(None, description="Quantity of the product.")
    unit: str | None = Field(None, description="Unit of the product (e.g., 'L', 'kg').")
    chains: list[ChainProductResponse] = Field(
        ..., description="List of chain-specific product information."
    )

    class Config:
        json_encoders = {
            Decimal: float
        }


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
            brand=product.brand,
            name=product.name,
            quantity=product.quantity, # Add quantity from product
            unit=product.unit,         # Add unit from product
            chains=[],
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
            "store_prices": [] # Removed quantity from here
        }
        
        store_prices_list = []
        for price_entry in prices_by_chain_product.get(cp.id, []):
            store_prices_data = {
                "price_date": price_entry["price_date"],
                "regular_price": price_entry["regular_price"],
                "special_price": price_entry["special_price"], # Add special_price
                "unit_price": price_entry["unit_price"],
                "best_price_30": price_entry["best_price_30"], # Add best_price_30
                "anchor_price": price_entry["anchor_price"],
                "special_offer": price_entry["special_price"] is not None,
            }
            
            store_prices_list.append(StorePriceResponse(**store_prices_data))
        
        cpr_data["store_prices"] = store_prices_list
        
        if store_prices_list:
            product_response_map[product_id].chains.append(ChainProductResponse(**cpr_data))

    # Fixup global product brand and name using original product data (not chain product)
    for product_id, product_response in product_response_map.items():
        original_product = next((p for p in products if p.id == product_id), None)
        if original_product:
            if not product_response.brand and original_product.brand:
                product_response.brand = original_product.brand.capitalize()
            if not product_response.name and original_product.name:
                product_response.name = original_product.name.capitalize()
            # Quantity and unit are already set from original_product during map creation
            # No need to fixup here unless they were None and we want to try chain_product
            # But the request was to pull from products table, so this is fine.

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


class StorePricesResponse(BaseModel):
    store_prices: list[StorePrice] = Field(
        ..., description="For a given product return latest price data per store."
    )


@router.get("/products/{ean}/store-prices/", summary="Get product prices by store")
async def get_store_prices(
    ean: str,
    chains: str = Query(
        None,
        description="Comma-separated list of chain codes to include",
    ),
) -> StorePricesResponse:
    """
    For a single store return prices for each store where the product is
    available. Returns prices for the last available date. Optionally filtered
    by chain.
    """
    products = await db.get_products_by_ean([ean])
    if not products:
        raise HTTPException(
            status_code=404,
            detail=f"Product with EAN {ean} not not found",
        )

    [product] = products
    chain_ids = await _get_chain_ids(chains)
    store_prices = await db.get_product_store_prices(product.id, chain_ids)
    return StorePricesResponse(store_prices=store_prices)


async def _get_chain_ids(chains_query: str):
    if not chains_query:
        return None

    chains = await db.list_chains()
    chain_codes = [code.lower().strip() for code in chains_query.split(",")]
    return [c.id for c in chains if c.code in chain_codes]


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


class ChainStatsResponse(BaseModel):
    chain_stats: list[ChainStats] = Field(..., description="List chain stats.")


@router.get("/chain-stats/", summary="Return stats of currently loaded data per chain.")
async def chain_stats() -> ChainStatsResponse:
    """Return stats of currently loaded data per chain."""

    chain_stats = await db.list_latest_chain_stats()
    return ChainStatsResponse(chain_stats=chain_stats)
print("<<< Finished importing in products.py")
