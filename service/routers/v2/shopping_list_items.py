from typing import List, Optional
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from service.db.models import ShoppingListItem, ShoppingListItemStatus, UserPersonalData # Import UserPersonalData
from service.db.psql import PostgresDatabase
from service.routers.auth import RequireAuth

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

@router.post("/shopping_lists/{list_id}/items", response_model=ShoppingListItemResponse, status_code=status.HTTP_201_CREATED)
async def add_item_to_shopping_list(
    list_id: int,
    request: ShoppingListItemCreateRequest,
    auth: UserPersonalData = RequireAuth,
    db: PostgresDatabase = Depends(PostgresDatabase),
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
    return ShoppingListItemResponse(**shopping_list_item.__dict__)

@router.get("/shopping_lists/{list_id}/items", response_model=List[ShoppingListItemResponse])
async def get_shopping_list_items(
    list_id: int,
    auth: UserPersonalData = RequireAuth,
    db: PostgresDatabase = Depends(PostgresDatabase),
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
    return [ShoppingListItemResponse(**item.__dict__) for item in shopping_list_items]

@router.get("/shopping_lists/{list_id}/items/{item_id}", response_model=ShoppingListItemResponse)
async def get_shopping_list_item_by_id(
    list_id: int,
    item_id: int,
    auth: UserPersonalData = RequireAuth,
    db: PostgresDatabase = Depends(PostgresDatabase),
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
    return ShoppingListItemResponse(**shopping_list_item.__dict__)

@router.put("/shopping_lists/{list_id}/items/{item_id}", response_model=bool)
async def update_shopping_list_item(
    list_id: int,
    item_id: int,
    request: ShoppingListItemUpdateRequest,
    auth: UserPersonalData = RequireAuth,
    db: PostgresDatabase = Depends(PostgresDatabase),
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
    db: PostgresDatabase = Depends(PostgresDatabase),
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
    db: PostgresDatabase = Depends(PostgresDatabase),
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
