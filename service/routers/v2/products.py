from decimal import Decimal
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from typing import Any, Optional, List
import sys
import datetime # Import datetime

from service.config import settings
from service.routers.auth import RequireAuth
from fastapi import Depends

router = APIRouter(tags=["Products V2"], dependencies=[RequireAuth])
db = settings.get_db()

# Pydantic Models for Responses

class ProductSearchItemV2(BaseModel):
    id: int = Field(..., description="Unique ID of the product.")
    name: str = Field(..., description="Name of the product.")
    description: Optional[str] = Field(None, description="Description of the product.")
    brand: Optional[str] = Field(None, description="Brand of the product.")
    category: Optional[str] = Field(None, description="Category of the product.")
    image_url: Optional[str] = Field(None, description="URL to the product image.")
    product_url: Optional[str] = Field(None, description="URL to the product page.")
    unit_of_measure: Optional[str] = Field(None, description="Unit of measure (e.g., 'kg', 'L', 'kom').")
    quantity_value: Optional[Decimal] = Field(None, description="Quantity value.")
    # Add best offer fields if joined
    best_unit_price_per_kg: Optional[Decimal] = Field(None, description="Best unit price per kg.")
    best_unit_price_per_l: Optional[Decimal] = Field(None, description="Best unit price per liter.")
    best_unit_price_per_piece: Optional[Decimal] = Field(None, description="Best unit price per piece.")

    class Config:
        json_encoders = {
            Decimal: float
        }

class ProductSearchResponseV2(BaseModel):
    products: List[ProductSearchItemV2] = Field(..., description="List of products matching the search query.")

class ProductPriceItemV2(BaseModel):
    product_id: int = Field(..., description="ID of the product.")
    product_name: str = Field(..., description="Name of the product.")
    product_brand: Optional[str] = Field(None, description="Brand of the product.")
    store_id: int = Field(..., description="ID of the store.")
    store_name: str = Field(..., description="Name of the store.")
    store_address: Optional[str] = Field(None, description="Address of the store.")
    store_city: Optional[str] = Field(None, description="City of the store.")
    price_date: datetime.date = Field(..., description="Date of the price.")
    regular_price: Decimal = Field(..., description="Regular price.")
    special_price: Optional[Decimal] = Field(None, description="Special price.")
    unit_price: Optional[Decimal] = Field(None, description="Unit price.")
    best_price_30: Optional[Decimal] = Field(None, description="Best price in last 30 days.")
    anchor_price: Optional[Decimal] = Field(None, description="Anchor price.")

    class Config:
        json_encoders = {
            Decimal: float
        }

class ProductPricesByLocationResponseV2(BaseModel):
    prices: List[ProductPriceItemV2] = Field(..., description="List of product prices by location.")

class ProductDetailsResponseV2(BaseModel):
    id: int = Field(..., description="Unique ID of the product.")
    name: str = Field(..., description="Name of the product.")
    description: Optional[str] = Field(None, description="Description of the product.")
    brand: Optional[str] = Field(None, description="Brand of the product.")
    category: Optional[str] = Field(None, description="Category of the product.")
    image_url: Optional[str] = Field(None, description="URL to the product image.")
    product_url: Optional[str] = Field(None, description="URL to the product page.")
    unit_of_measure: Optional[str] = Field(None, description="Unit of measure (e.g., 'kg', 'L', 'kom').")
    quantity_value: Optional[Decimal] = Field(None, description="Quantity value.")
    embedding: Optional[List[float]] = Field(None, description="Vector embedding of the product.")
    keywords: Optional[str] = Field(None, description="Keywords associated with the product.")
    keywords_tsv: Optional[str] = Field(None, description="TSV representation of keywords.")
    best_unit_price_per_kg: Optional[Decimal] = Field(None, description="Best unit price per kg.")
    best_unit_price_per_l: Optional[Decimal] = Field(None, description="Best unit price per liter.")
    best_unit_price_per_piece: Optional[Decimal] = Field(None, description="Best unit price per piece.")

    class Config:
        json_encoders = {
            Decimal: float
        }

# API Endpoints

@router.get("/products/search", summary="Hybrid Product Search (v2)")
async def search_products_v2(
    q: str = Query(..., description="The user's natural language query"),
    limit: int = Query(20, ge=1, description="Maximum number of results to return."),
    offset: int = Query(0, ge=0, description="Number of results to skip."),
    sort_by: Optional[str] = Query(
        None,
        description="Optional. Values: 'relevance', 'best_value_kg', 'best_value_l', 'best_value_piece'",
        regex="^(relevance|best_value_kg|best_value_l|best_value_piece)$"
    ),
    category: Optional[str] = Query(None, description="To filter by category, e.g., 'Slatkiši i grickalice'"),
    brand: Optional[str] = Query(None, description="To filter by brand"),
) -> ProductSearchResponseV2:
    """
    The main entry point for finding products using hybrid search (vector + keyword)
    and supporting advanced sorting.
    """
    products_data = await db.get_g_products_hybrid_search(
        query=q,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        category=category,
        brand=brand,
    )
    
    return ProductSearchResponseV2(products=[ProductSearchItemV2(**p) for p in products_data])


@router.get("/products/{product_id}/prices-by-location", summary="Get Product Prices by Location (v2)")
async def get_product_prices_by_location_v2(
    product_id: int,
    store_ids: str = Query(..., description="A comma-separated list of store IDs, e.g., 101,105,230"),
) -> ProductPricesByLocationResponseV2:
    """
    This is the core endpoint for answering the "best price near me" question.
    It finds the prices for a single product at a list of specific stores.
    """
    try:
        parsed_store_ids = [int(s.strip()) for s in store_ids.split(',') if s.strip()]
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid store_ids format. Must be a comma-separated list of integers."
        )
    
    prices_data = await db.get_g_product_prices_by_location(
        product_id=product_id,
        store_ids=parsed_store_ids,
    )
    
    return ProductPricesByLocationResponseV2(prices=[ProductPriceItemV2(**p) for p in prices_data])


@router.get("/products/{product_id}", summary="Get Product Details (v2)")
async def get_product_details_v2(
    product_id: int,
) -> ProductDetailsResponseV2:
    """
    A simple endpoint to retrieve the full "golden record" for a single product.
    It can also be expanded to join and include the absolute best offer from g_product_best_offers.
    """
    product_data = await db.get_g_product_details(product_id=product_id)
    
    if not product_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Product with ID {product_id} not found."
        )
    
    return ProductDetailsResponseV2(**product_data)
