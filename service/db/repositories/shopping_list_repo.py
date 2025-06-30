from typing import List, Optional
from datetime import datetime
from uuid import UUID
from decimal import Decimal

import asyncpg

from service.db.models import ShoppingList, ShoppingListStatus
from service.db.base import BaseRepository


class ShoppingListRepository(BaseRepository):
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None

    async def add_shopping_list(self, user_id: UUID, name: str) -> ShoppingList:
        query = """
            INSERT INTO shopping_lists (user_id, name)
            VALUES ($1, $2)
            RETURNING id, user_id, name, status, created_at, updated_at, deleted_at
        """
        record = await self.pool.fetchrow(query, user_id, name)
        return ShoppingList(**record)

    async def get_shopping_list_by_id(self, list_id: int, user_id: UUID) -> Optional[ShoppingList]:
        query = """
            SELECT id, user_id, name, status, created_at, updated_at, deleted_at
            FROM shopping_lists
            WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL
        """
        record = await self.pool.fetchrow(query, list_id, user_id)
        return ShoppingList(**record) if record else None

    async def get_user_shopping_lists(self, user_id: UUID) -> List[ShoppingList]:
        query = """
            SELECT id, user_id, name, status, created_at, updated_at, deleted_at
            FROM shopping_lists
            WHERE user_id = $1 AND deleted_at IS NULL
            ORDER BY created_at DESC
        """
        records = await self.pool.fetch(query, user_id)
        return [ShoppingList(**record) for record in records]

    async def update_shopping_list(
        self, list_id: int, user_id: UUID, name: Optional[str] = None, status: Optional[ShoppingListStatus] = None
    ) -> bool:
        updates = []
        args = [list_id, user_id]
        param_idx = 3

        if name is not None:
            updates.append(f"name = ${param_idx}")
            args.append(name)
            param_idx += 1
        if status is not None:
            updates.append(f"status = ${param_idx}::shopping_list_status_enum")
            args.append(status.value)
            param_idx += 1

        if not updates:
            return False

        query = f"""
            UPDATE shopping_lists
            SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP
            WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL
        """
        status = await self.pool.execute(query, *args)
        return status == "UPDATE 1"

    async def soft_delete_shopping_list(self, list_id: int, user_id: UUID) -> bool:
        query = """
            UPDATE shopping_lists
            SET deleted_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL
        """
        status = await self.pool.execute(query, list_id, user_id)
        return status == "UPDATE 1"
