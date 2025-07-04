from typing import List, Optional
from datetime import datetime
from decimal import Decimal
import json # Import json

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
                sli.quantity,
                sli.base_unit_type,
                sli.price_at_addition,
                sli.store_id_at_addition,
                sli.status,
                sli.notes,
                sli.added_at,
                sli.bought_at,
                sli.updated_at,
                sli.deleted_at,
                
                -- From g_products (gp)
                gp.canonical_name AS product_name,
                gp.ean,
                gp.brand,
                gp.category,
                gp.variants::jsonb, -- Explicitly cast to jsonb
                gp.is_generic_product,
                gp.seasonal_start_month,
                gp.seasonal_end_month,

                -- From chains (c)
                c.code AS chain_code,

                -- From current_price (lateral join)
                current_price.price_date AS current_price_date,
                current_price.regular_price AS current_regular_price,
                current_price.special_price AS current_special_price,
                current_price.price_per_kg AS current_price_per_kg,
                current_price.price_per_l AS current_price_per_l,
                current_price.price_per_piece AS current_price_per_piece,
                current_price.is_on_special_offer AS current_is_on_special_offer,

                -- From g_product_best_offers (gpbo)
                gpbo.best_unit_price_per_kg,
                gpbo.best_unit_price_per_l,
                gpbo.best_unit_price_per_piece,
                gpbo.lowest_price_in_season,
                gpbo.best_price_store_id,
                gpbo.best_price_found_at
            FROM shopping_list_items sli
            LEFT JOIN g_products gp ON sli.g_product_id = gp.id
            LEFT JOIN stores s ON sli.store_id_at_addition = s.id
            LEFT JOIN chains c ON s.chain_id = c.id
            LEFT JOIN g_product_best_offers gpbo ON sli.g_product_id = gpbo.product_id
            LEFT JOIN LATERAL (
                SELECT
                    gp_current_price.price_date,
                    gp_current_price.regular_price,
                    gp_current_price.special_price,
                    gp_current_price.price_per_kg,
                    gp_current_price.price_per_l,
                    gp_current_price.price_per_piece,
                    gp_current_price.is_on_special_offer
                FROM g_prices AS gp_current_price
                WHERE gp_current_price.product_id = sli.g_product_id
                  AND (sli.store_id_at_addition IS NULL OR gp_current_price.store_id = sli.store_id_at_addition)
                ORDER BY gp_current_price.price_date DESC
                LIMIT 1
            ) AS current_price ON TRUE
            WHERE sli.shopping_list_id = $1 AND sli.deleted_at IS NULL
            ORDER BY sli.added_at DESC
        """
        records = await self.pool.fetch(query, shopping_list_id)
        
        # Manually deserialize variants if it's a string
        processed_records = []
        for record in records:
            record_dict = dict(record)
            if "variants" in record_dict and isinstance(record_dict["variants"], str):
                try:
                    record_dict["variants"] = json.loads(record_dict["variants"])
                except json.JSONDecodeError:
                    record_dict["variants"] = None # Handle malformed JSON
            processed_records.append(record_dict)
        
        return [ShoppingListItem(**record) for record in processed_records]

    async def get_shopping_list_item_by_id(self, item_id: int, shopping_list_id: int) -> Optional[ShoppingListItem]:
        query = """
            SELECT
                sli.id,
                sli.shopping_list_id,
                sli.g_product_id,
                sli.quantity,
                sli.base_unit_type,
                sli.price_at_addition,
                sli.store_id_at_addition,
                sli.status,
                sli.notes,
                sli.added_at,
                sli.bought_at,
                sli.updated_at,
                sli.deleted_at,
                
                -- From g_products (gp)
                gp.canonical_name AS product_name,
                gp.ean,
                gp.brand,
                gp.category,
                gp.variants,
                gp.is_generic_product,
                gp.seasonal_start_month,
                gp.seasonal_end_month,

                -- From chains (c)
                c.code AS chain_code,

                -- From current_price (lateral join)
                current_price.price_date AS current_price_date,
                current_price.regular_price AS current_regular_price,
                current_price.special_price AS current_special_price,
                current_price.price_per_kg AS current_price_per_kg,
                current_price.price_per_l AS current_price_per_l,
                current_price.price_per_piece AS current_price_per_piece,
                current_price.is_on_special_offer AS current_is_on_special_offer,

                -- From g_product_best_offers (gpbo)
                gpbo.best_unit_price_per_kg,
                gpbo.best_unit_price_per_l,
                gpbo.best_unit_price_per_piece,
                gpbo.lowest_price_in_season,
                gpbo.best_price_store_id,
                gpbo.best_price_found_at
            FROM shopping_list_items sli
            LEFT JOIN g_products gp ON sli.g_product_id = gp.id
            LEFT JOIN stores s ON sli.store_id_at_addition = s.id
            LEFT JOIN chains c ON s.chain_id = c.id
            LEFT JOIN g_product_best_offers gpbo ON sli.g_product_id = gpbo.product_id
            LEFT JOIN LATERAL (
                SELECT
                    gp_current_price.price_date,
                    gp_current_price.regular_price,
                    gp_current_price.special_price,
                    gp_current_price.price_per_kg,
                    gp_current_price.price_per_l,
                    gp_current_price.price_per_piece,
                    gp_current_price.is_on_special_offer
                FROM g_prices AS gp_current_price
                WHERE gp_current_price.product_id = sli.g_product_id
                  AND (sli.store_id_at_addition IS NULL OR gp_current_price.store_id = sli.store_id_at_addition)
                ORDER BY gp_current_price.price_date DESC
                LIMIT 1
            ) AS current_price ON TRUE
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
