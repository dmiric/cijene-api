from typing import List, Optional
from datetime import datetime
from decimal import Decimal

import asyncpg

from service.db.models import ShoppingListItem, ShoppingListItemStatus
from service.db.base import BaseRepository


class ShoppingListItemRepository(BaseRepository):
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None

    async def add_shopping_list_item(
        self,
        shopping_list_id: int,
        g_product_id: int,
        quantity: Decimal,
        base_unit_type: str,
        price_at_addition: Optional[Decimal] = None,
        store_id_at_addition: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> ShoppingListItem:
        query = """
            INSERT INTO shopping_list_items (
                shopping_list_id, g_product_id, quantity, base_unit_type,
                price_at_addition, store_id_at_addition, notes
            )
            VALUES ($1, $2, $3, $4::unit_type_enum, $5, $6, $7)
            RETURNING id, shopping_list_id, g_product_id, quantity, base_unit_type,
                      price_at_addition, store_id_at_addition, status, notes,
                      added_at, bought_at, updated_at, deleted_at
        """
        record = await self.pool.fetchrow(
            query,
            shopping_list_id,
            g_product_id,
            quantity,
            base_unit_type,
            price_at_addition,
            store_id_at_addition,
            notes,
        )
        return ShoppingListItem(**record)

    async def get_shopping_list_items(self, shopping_list_id: int) -> List[ShoppingListItem]:
        query = """
            SELECT
                sli.id,
                sli.shopping_list_id,
                sli.g_product_id,
                gp.canonical_name AS product_name,
                gp.is_generic_product,
                c.code AS chain_code,
                sli.quantity,
                sli.base_unit_type,
                sli.price_at_addition,
                sli.store_id_at_addition,
                sli.status,
                sli.notes,
                sli.added_at,
                sli.bought_at,
                sli.updated_at,
                sli.deleted_at
            FROM shopping_list_items sli
            LEFT JOIN g_products gp ON sli.g_product_id = gp.id
            LEFT JOIN stores s ON sli.store_id_at_addition = s.id
            LEFT JOIN chains c ON s.chain_id = c.id
            WHERE sli.shopping_list_id = $1 AND sli.deleted_at IS NULL
            ORDER BY sli.added_at DESC
        """
        records = await self.pool.fetch(query, shopping_list_id)
        return [ShoppingListItem(**record) for record in records]

    async def get_shopping_list_item_by_id(self, item_id: int, shopping_list_id: int) -> Optional[ShoppingListItem]:
        query = """
            SELECT
                sli.id,
                sli.shopping_list_id,
                sli.g_product_id,
                gp.canonical_name AS product_name,
                gp.is_generic_product,
                c.code AS chain_code,
                sli.quantity,
                sli.base_unit_type,
                sli.price_at_addition,
                sli.store_id_at_addition,
                sli.status,
                sli.notes,
                sli.added_at,
                sli.bought_at,
                sli.updated_at,
                sli.deleted_at
            FROM shopping_list_items sli
            LEFT JOIN g_products gp ON sli.g_product_id = gp.id
            LEFT JOIN stores s ON sli.store_id_at_addition = s.id
            LEFT JOIN chains c ON s.chain_id = c.id
            WHERE sli.id = $1 AND sli.shopping_list_id = $2 AND sli.deleted_at IS NULL
        """
        record = await self.pool.fetchrow(query, item_id, shopping_list_id)
        return ShoppingListItem(**record) if record else None

    async def update_shopping_list_item(
        self,
        item_id: int,
        shopping_list_id: int,
        quantity: Optional[Decimal] = None,
        status: Optional[ShoppingListItemStatus] = None,
        notes: Optional[str] = None,
    ) -> bool:
        updates = []
        args = [item_id, shopping_list_id]
        param_idx = 3

        if quantity is not None:
            updates.append(f"quantity = ${param_idx}")
            args.append(quantity)
            param_idx += 1
        if status is not None:
            updates.append(f"status = ${param_idx}::shopping_list_item_status_enum")
            args.append(status.value)
            param_idx += 1
        if notes is not None:
            updates.append(f"notes = ${param_idx}")
            args.append(notes)
            param_idx += 1

        if not updates:
            return False

        query = f"""
            UPDATE shopping_list_items
            SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP
            WHERE id = $1 AND shopping_list_id = $2 AND deleted_at IS NULL
        """
        status = await self.pool.execute(query, *args)
        return status == "UPDATE 1"

    async def soft_delete_shopping_list_item(self, item_id: int, shopping_list_id: int) -> bool:
        query = """
            UPDATE shopping_list_items
            SET deleted_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = $1 AND shopping_list_id = $2 AND deleted_at IS NULL
        """
        status = await self.pool.execute(query, item_id, shopping_list_id)
        return status == "UPDATE 1"

    async def mark_item_bought(self, item_id: int, shopping_list_id: int) -> bool:
        query = """
            UPDATE shopping_list_items
            SET status = 'bought'::shopping_list_item_status_enum,
                bought_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = $1 AND shopping_list_id = $2 AND deleted_at IS NULL
        """
        status = await self.pool.execute(query, item_id, shopping_list_id)
        return status == "UPDATE 1"
