from typing import List, Optional
from datetime import datetime, date # Import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from service.db.models import ShoppingListItem, ShoppingListItemStatus, UserPersonalData # Import UserPersonalData
from service.db.psql import PostgresDatabase
from service.routers.auth import RequireAuth
from service.db.base import get_db_session # Import from base.py

router = APIRouter(tags=["Shopping List Items"])

class ShoppingListItemCreateRequest(BaseModel):
    g_product_id: int
    quantity: Decimal
    base_unit_type: str
    price_at_addition: Optional[Decimal] = None
    store_id_at_addition: Optional[int] = None
    notes: Optional[str] = None

class ShoppingListItemUpdateRequest(BaseModel):
    quantity: Optional[Decimal] = None
    status: Optional[ShoppingListItemStatus] = None
    notes: Optional[str] = None

class ShoppingListItemResponse(BaseModel):
    id: int
    shopping_list_id: int
    g_product_id: int
    product_name: Optional[str] = None
    ean: Optional[str] = None
    brand: Optional[str] = None
    category: Optional[str] = None
    variants: Optional[List[dict]] = None # Changed to List[dict]
    is_generic_product: Optional[bool] = None
    seasonal_start_month: Optional[int] = None
    seasonal_end_month: Optional[int] = None
    chain_code: Optional[str] = None
    quantity: Decimal
    base_unit_type: str
    price_at_addition: Optional[Decimal] = None
    store_id_at_addition: Optional[int] = None
    status: ShoppingListItemStatus
    notes: Optional[str] = None
    added_at: datetime
    bought_at: Optional[datetime] = None
    updated_at: datetime
    deleted_at: Optional[datetime] = None
    
    # Current Price Information (from g_prices)
    current_price_date: Optional[date] = None
    current_regular_price: Optional[Decimal] = None
    current_special_price: Optional[Decimal] = None
    current_price_per_kg: Optional[Decimal] = None
    current_price_per_l: Optional[Decimal] = None
    current_price_per_piece: Optional[Decimal] = None
    current_is_on_special_offer: Optional[bool] = None

    # Best Offer Information (from g_product_best_offers)
    best_unit_price_per_kg: Optional[Decimal] = None
    best_unit_price_per_l: Optional[Decimal] = None
    best_unit_price_per_piece: Optional[Decimal] = None
    lowest_price_in_season: Optional[Decimal] = None
    best_price_store_id: Optional[int] = None
    best_price_found_at: Optional[datetime] = None

    # Store Information (from stores and chains)
    store_address: Optional[str] = None
    store_city: Optional[str] = None
    store_lat: Optional[Decimal] = None
    store_lon: Optional[Decimal] = None
    store_phone: Optional[str] = None
    chain_code: Optional[str] = None

    # Store Information (from stores and chains)
    store_address: Optional[str] = None
    store_city: Optional[str] = None
    store_lat: Optional[Decimal] = None
    store_lon: Optional[Decimal] = None
    store_phone: Optional[str] = None
    chain_code: Optional[str] = None

@router.post("/shopping_lists/{list_id}/items", response_model=ShoppingListItemResponse, status_code=status.HTTP_201_CREATED)
async def add_item_to_shopping_list(
    list_id: int,
    request: ShoppingListItemCreateRequest,
    auth: UserPersonalData = RequireAuth,
    db: PostgresDatabase = Depends(get_db_session),
):
    """
    Add an item to a specific shopping list for the authenticated user.
    """
    user_id = auth.user_id
    # Verify shopping list exists and belongs to the user
    shopping_list = await db.shopping_lists.get_shopping_list_by_id(list_id=list_id, user_id=user_id)
    if not shopping_list:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shopping list not found")

    shopping_list_item = await db.shopping_list_items.add_shopping_list_item(
        shopping_list_id=list_id,
        g_product_id=request.g_product_id,
        quantity=request.quantity,
        base_unit_type=request.base_unit_type,
        price_at_addition=request.price_at_addition,
        store_id_at_addition=request.store_id_at_addition,
        notes=request.notes,
    )
    return shopping_list_item

@router.get("/shopping_lists/{list_id}/items", response_model=List[ShoppingListItemResponse])
async def get_shopping_list_items(
    list_id: int,
    auth: UserPersonalData = RequireAuth,
    db: PostgresDatabase = Depends(get_db_session),
):
    """
    Retrieve all active items for a specific shopping list for the authenticated user.
    """
    user_id = auth.user_id
    # Verify shopping list exists and belongs to the user
    shopping_list = await db.shopping_lists.get_shopping_list_by_id(list_id=list_id, user_id=user_id)
    if not shopping_list:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shopping list not found")

    shopping_list_items = await db.shopping_list_items.get_shopping_list_items(shopping_list_id=list_id)
    return shopping_list_items

@router.get("/shopping_lists/{list_id}/items/{item_id}", response_model=ShoppingListItemResponse)
async def get_shopping_list_item_by_id(
    list_id: int,
    item_id: int,
    auth: UserPersonalData = RequireAuth,
    db: PostgresDatabase = Depends(get_db_session),
):
    """
    Retrieve a specific active item from a shopping list by its ID.
    """
    user_id = auth.user_id
    # Verify shopping list exists and belongs to the user
    shopping_list = await db.shopping_lists.get_shopping_list_by_id(list_id=list_id, user_id=user_id)
    if not shopping_list:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shopping list not found")

    shopping_list_item = await db.shopping_list_items.get_shopping_list_item_by_id(item_id=item_id, shopping_list_id=list_id)
    if not shopping_list_item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shopping list item not found")
    return shopping_list_item

@router.put("/shopping_lists/{list_id}/items/{item_id}", response_model=bool)
async def update_shopping_list_item(
    list_id: int,
    item_id: int,
    request: ShoppingListItemUpdateRequest,
    auth: UserPersonalData = RequireAuth,
    db: PostgresDatabase = Depends(get_db_session),
):
    """
    Update an item's quantity, status, or notes within a shopping list.
    """
    user_id = auth.user_id
    # Verify shopping list exists and belongs to the user
    shopping_list = await db.shopping_lists.get_shopping_list_by_id(list_id=list_id, user_id=user_id)
    if not shopping_list:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shopping list not found")

    updated = await db.shopping_list_items.update_shopping_list_item(
        item_id=item_id,
        shopping_list_id=list_id,
        quantity=request.quantity,
        status=request.status,
        notes=request.notes,
    )
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shopping list item not found or no changes applied")
    return updated

@router.delete("/shopping_lists/{list_id}/items/{item_id}", response_model=bool)
async def soft_delete_shopping_list_item(
    list_id: int,
    item_id: int,
    auth: UserPersonalData = RequireAuth,
    db: PostgresDatabase = Depends(get_db_session),
):
    """
    Soft-delete a shopping list item by setting its deleted_at timestamp.
    """
    user_id = auth.user_id
    # Verify shopping list exists and belongs to the user
    shopping_list = await db.shopping_lists.get_shopping_list_by_id(list_id=list_id, user_id=user_id)
    if not shopping_list:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shopping list not found")

    deleted = await db.shopping_list_items.soft_delete_shopping_list_item(item_id=item_id, shopping_list_id=list_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shopping list item not found or already deleted")
    return deleted

@router.post("/shopping_lists/{list_id}/items/{item_id}/mark-bought", response_model=bool)
async def mark_item_bought(
    list_id: int,
    item_id: int,
    auth: UserPersonalData = RequireAuth,
    db: PostgresDatabase = Depends(get_db_session),
):
    """
    Mark a shopping list item as bought.
    """
    user_id = auth.user_id
    # Verify shopping list exists and belongs to the user
    shopping_list = await db.shopping_lists.get_shopping_list_by_id(list_id=list_id, user_id=user_id)
    if not shopping_list:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shopping list not found")

    marked = await db.shopping_list_items.mark_item_bought(item_id=item_id, shopping_list_id=list_id)
    if not marked:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shopping list item not found or already marked bought")
    return marked
