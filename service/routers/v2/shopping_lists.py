from typing import List, Optional
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from service.db.models import ShoppingList, ShoppingListStatus, UserPersonalData # Import UserPersonalData
from service.db.psql import PostgresDatabase
from service.routers.auth import RequireAuth

router = APIRouter(tags=["Shopping Lists"])

class ShoppingListCreateRequest(BaseModel):
    name: str

class ShoppingListUpdateRequest(BaseModel):
    name: Optional[str] = None
    status: Optional[ShoppingListStatus] = None

class ShoppingListResponse(BaseModel):
    id: int
    user_id: UUID
    name: str
    status: ShoppingListStatus
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None

@router.post("/shopping_lists", response_model=ShoppingListResponse, status_code=status.HTTP_201_CREATED)
async def create_shopping_list(
    request: ShoppingListCreateRequest,
    auth: UserPersonalData = RequireAuth,
    db: PostgresDatabase = Depends(PostgresDatabase),
):
    """
    Create a new shopping list for the authenticated user.
    """
    user_id = auth.user_id
    shopping_list = await db.shopping_lists.add_shopping_list(user_id=user_id, name=request.name)
    return ShoppingListResponse(**shopping_list.__dict__)

@router.get("/shopping_lists", response_model=List[ShoppingListResponse])
async def get_user_shopping_lists(
    auth: UserPersonalData = RequireAuth,
    db: PostgresDatabase = Depends(PostgresDatabase),
):
    """
    Retrieve all active shopping lists for the authenticated user.
    """
    user_id = auth.user_id
    shopping_lists = await db.shopping_lists.get_user_shopping_lists(user_id=user_id)
    return [ShoppingListResponse(**sl.__dict__) for sl in shopping_lists]

@router.get("/shopping_lists/{list_id}", response_model=ShoppingListResponse)
async def get_shopping_list_by_id(
    list_id: int,
    auth: UserPersonalData = RequireAuth,
    db: PostgresDatabase = Depends(PostgresDatabase),
):
    """
    Retrieve a specific active shopping list by its ID.
    """
    user_id = auth.user_id
    shopping_list = await db.shopping_lists.get_shopping_list_by_id(list_id=list_id, user_id=user_id)
    if not shopping_list:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shopping list not found")
    return ShoppingListResponse(**shopping_list.__dict__)

@router.put("/shopping_lists/{list_id}", response_model=bool)
async def update_shopping_list(
    list_id: int,
    request: ShoppingListUpdateRequest,
    auth: UserPersonalData = RequireAuth,
    db: PostgresDatabase = Depends(PostgresDatabase),
):
    """
    Update a shopping list's name or status.
    """
    user_id = auth.user_id
    updated = await db.shopping_lists.update_shopping_list(
        list_id=list_id, user_id=user_id, name=request.name, status=request.status
    )
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shopping list not found or no changes applied")
    return updated

@router.delete("/shopping_lists/{list_id}", response_model=bool)
async def soft_delete_shopping_list(
    list_id: int,
    auth: UserPersonalData = RequireAuth,
    db: PostgresDatabase = Depends(PostgresDatabase),
):
    """
    Soft-delete a shopping list by setting its deleted_at timestamp.
    """
    user_id = auth.user_id
    deleted = await db.shopping_lists.soft_delete_shopping_list(list_id=list_id, user_id=user_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shopping list not found or already deleted")
    return deleted
