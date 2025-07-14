from contextlib import asynccontextmanager
import asyncpg
from typing import List, Any, Optional, AsyncGenerator, AsyncIterator
import sys
from datetime import date, datetime
from decimal import Decimal
import json
from service.utils.timing import timing_decorator # Import the decorator

from service.db.base import BaseRepository
from service.db.models import (
    GProduct, GProductWithId, GPrice, GStore, GStoreWithId, GProductBestOffer,
    GProductBestOfferWithId, ProductSearchItemV2,
)
from service.db.field_configs import PRODUCT_FULL_FIELDS, PRODUCT_AI_SEARCH_FIELDS, PRODUCT_AI_DETAILS_FIELDS, PRODUCT_DB_SEARCH_FIELDS

class GoldenProductRepository(BaseRepository):
    """
    Contains all logic for interacting with the 'golden record' tables (g_products,
    g_prices, g_product_best_offers) and the new g_stores table.
    """

    def __init__(self):
        self.pool = None
        def debug_print_db(*args, **kwargs):
            print("[DEBUG golden_product_repo]", *args, file=sys.stderr, **kwargs)
        self.debug_print = debug_print_db

    async def connect(self, pool: asyncpg.Pool) -> None:
        """
        Initializes the repository with an existing connection pool.
        This repository does not create its own pool.
        """
        self.pool = pool

    @asynccontextmanager
    async def _get_conn(self) -> AsyncGenerator[asyncpg.Connection, None]:
        if not self.pool:
            raise RuntimeError("Database pool is not initialized for GoldenProductRepository")
        async with self.pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def _atomic(self) -> AsyncIterator[asyncpg.Connection]:
        async with self._get_conn() as conn:
            async with conn.transaction():
                yield conn

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    async def _fetchval(self, query: str, *args: Any) -> Any:
        async with self._get_conn() as conn:
            return await conn.fetchval(query, *args)
    # used by search_products_tool_v2 to search products w/o prices
    async def get_g_products_hybrid_search_with_prices(
        self,
        query: str,
        store_ids: List[int],
        limit: int = 20,
        offset: int = 0,
        sort_by: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """
        Performs a hybrid search and fetches prices.
        FINAL VERSION 2.0:
        1. When sorting by price, ONLY returns products that both have a price
        AND match the corresponding base_unit_type (e.g., 'WEIGHT' for 'best_value_kg').
        2. Correctly populates the 'unit_price' field.
        """
        if not store_ids:
            raise ValueError("store_ids cannot be empty for this function.")

        self.debug_print(f"get_g_products_hybrid_search_with_prices: query={query}, store_ids={store_ids}, sort_by={sort_by}")

        params = [query]
        param_counter = 2

        # --- Intelligent Sorting and Filtering Logic ---
        sort_by_column = "relevance_score"
        sort_by_direction = "DESC"
        filter_condition = "TRUE" # Default: no additional filtering

        if sort_by and sort_by.startswith("best_value_"):
            sort_by_column = "best_price_metric"
            sort_by_direction = "ASC NULLS LAST"
            if sort_by == 'best_value_kg':
                filter_condition = "base_unit_type = 'WEIGHT'"
            elif sort_by == 'best_value_l':
                filter_condition = "base_unit_type = 'VOLUME'"
            elif sort_by == 'best_value_piece':
                filter_condition = "base_unit_type = 'COUNT'"

        final_query = f"""
            WITH products_with_metrics AS (
                -- Step 1: Find all products matching the text search and calculate their
                -- metrics. We must include base_unit_type here for the next step.
                SELECT
                    gp.id,
                    gp.base_unit_type,
                    ts_rank_cd(to_tsvector('hr', gp.canonical_name || ' ' || array_to_string(gp.keywords, ' ')), websearch_to_tsquery('hr', $1)) AS relevance_score,
                    MIN(
                        CASE
                            WHEN gp.base_unit_type = 'WEIGHT' THEN gpr.price_per_kg
                            WHEN gp.base_unit_type = 'VOLUME' THEN gpr.price_per_l
                            WHEN gp.base_unit_type = 'COUNT' THEN gpr.price_per_piece
                            ELSE NULL
                        END
                    ) AS best_price_metric
                FROM g_products gp
                INNER JOIN g_prices gpr ON gp.id = gpr.product_id AND gpr.store_id = ANY(${param_counter + 2})
                WHERE to_tsvector('hr', gp.canonical_name || ' ' || array_to_string(gp.keywords, ' ')) @@ websearch_to_tsquery('hr', $1)
                GROUP BY gp.id, gp.base_unit_type
            ),
            sorted_product_ids AS (
                -- Step 2: Apply the new, intelligent filter.
                SELECT id
                FROM products_with_metrics
                WHERE {filter_condition}
                ORDER BY {sort_by_column} {sort_by_direction}, relevance_score DESC
                LIMIT ${param_counter} OFFSET ${param_counter + 1}
            )
            -- Step 3: Fetch the full data for only the final, filtered, sorted, and limited set of product IDs.
            SELECT
                gp.*,
                (
                    SELECT jsonb_agg(prices.price_data)
                    FROM (
                        SELECT
                            jsonb_build_object(
                                'product_id', gpr.product_id,
                                'store_id', gpr.store_id,
                                'price_date', gpr.price_date,
                                'regular_price', gpr.regular_price,
                                'special_price', gpr.special_price,
                                'unit_price',
                                    CASE (SELECT p.base_unit_type FROM g_products p WHERE p.id = gpr.product_id)
                                        WHEN 'WEIGHT' THEN gpr.price_per_kg
                                        WHEN 'VOLUME' THEN gpr.price_per_l
                                        WHEN 'COUNT' THEN gpr.price_per_piece
                                        ELSE NULL
                                    END,
                                'best_price_30', NULL, 'anchor_price', NULL, 'is_on_special_offer', gpr.is_on_special_offer
                            ) as price_data
                        FROM g_prices gpr
                        WHERE gpr.product_id = gp.id AND gpr.store_id = ANY(${param_counter + 2})
                    ) AS prices
                ) AS prices_in_stores
            FROM g_products gp
            WHERE gp.id IN (SELECT id FROM sorted_product_ids)
            ORDER BY (
                SELECT {sort_by_column} FROM products_with_metrics WHERE products_with_metrics.id = gp.id
            ) {sort_by_direction},
            (
                SELECT relevance_score FROM products_with_metrics WHERE products_with_metrics.id = gp.id
            ) DESC
        """
        params.extend([limit, offset, store_ids])
        
        async with self._get_conn() as conn:
            rows = await conn.fetch(final_query, *params, timeout=45.0)
            results = [dict(row) for row in rows]
            for r in results:
                if r.get('prices_in_stores') is None:
                    r['prices_in_stores'] = []
            return results
    
    #this is being used to search products without stores
    async def get_g_product_prices_by_location(
        self, product_id: int, store_ids: Optional[List[int]] = None
    ) -> list[dict[str, Any]]:
        """
        Queries g_prices for a specific product across a list of stores, ordered by price.
        If store_ids is None or empty, it queries for all stores.
        """
        self.debug_print(f"get_g_product_prices_by_location: product_id={product_id}, store_ids={store_ids}")
        async with self._get_conn() as conn:
            query = """
                SELECT
                    gp.id AS product_id,
                    gp.canonical_name AS product_name,
                    gp.brand AS product_brand,
                    gs.id AS store_id,
                    gs.code AS store_code,
                    gs.address AS store_address,
                    gs.city AS store_city,
                    c.code AS chain_code, -- Added chain_code
                    gpr.price_date,
                    gpr.regular_price,
                    gpr.special_price,
                    gpr.price_per_kg,
                    gpr.price_per_l,
                    gpr.price_per_piece,
                    gpr.is_on_special_offer
                FROM g_prices gpr
                JOIN g_products gp ON gpr.product_id = gp.id
                JOIN stores gs ON gpr.store_id = gs.id
                JOIN chains c ON gs.chain_id = c.id -- Joined chains table
                WHERE gpr.product_id = $1
            """
            params = [product_id]
            param_counter = 2

            if store_ids: # Only add store_id filter if store_ids is not empty or None
                query += f" AND gpr.store_id = ANY(${param_counter})"
                params.append(store_ids)
                param_counter += 1

            query += " ORDER BY COALESCE(gpr.special_price, gpr.regular_price) ASC"

            self.debug_print(f"get_g_product_prices_by_location: Executing Query!!")
            self.debug_print(f"get_g_product_prices_by_location: With Params: {params}")
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]
        
    async def get_g_product_by_ean(self, ean: str) -> Optional[GProductWithId]:
        """
        Retrieves a single golden product by its EAN.
        """
        self.debug_print(f"get_g_product_by_ean: ean={ean}")
        query = """
            SELECT id, ean, canonical_name, brand, category, base_unit_type,
                   variants, text_for_embedding, keywords, is_generic_product,
                   seasonal_start_month, seasonal_end_month, embedding,
                   created_at, updated_at
            FROM g_products
            WHERE ean = $1
        """
        async with self._get_conn() as conn:
            row = await conn.fetchrow(query, ean)
            if row:
                row_dict = dict(row)
                if "embedding" in row_dict and isinstance(row_dict["embedding"], str):
                    try:
                        row_dict["embedding"] = json.loads(row_dict["embedding"])
                    except json.JSONDecodeError:
                        self.debug_print(f"Warning: Could not decode embedding string: {row_dict['embedding']}")
                        row_dict["embedding"] = None
                return GProductWithId(**row_dict)
            return None

    async def get_g_product_details(
        self,
        product_id: int,
    ) -> dict[str, Any] | None: # Return dict for flexibility
        """
        Retrieves a single product's details from g_products, potentially joining with g_product_best_offers,
        with selectable fields.
        """
        self.debug_print(f"get_g_product_details: product_id={product_id}")

        fields_to_select = list(PRODUCT_FULL_FIELDS) # Default to full fields

        # Basic validation for fields
        valid_fields = set(PRODUCT_FULL_FIELDS + ["best_unit_price_per_kg", "best_unit_price_per_l", "best_unit_price_per_piece"])
        if not all(f in valid_fields for f in fields_to_select):
            raise ValueError("Invalid field requested for product details.")

        # Construct SELECT clause dynamically
        select_parts = []
        for field in fields_to_select:
            if field == "name":
                select_parts.append("gp.canonical_name AS name")
            elif field == "description":
                select_parts.append("gp.text_for_embedding AS description")
            elif field == "image_url":
                select_parts.append("NULL AS image_url") # Placeholder
            elif field == "product_url":
                select_parts.append("NULL AS product_url") # Placeholder
            elif field == "unit_of_measure":
                select_parts.append("gp.base_unit_type AS unit_of_measure")
            elif field == "quantity_value":
                select_parts.append("NULL AS quantity_value") # Placeholder
            elif field.startswith("best_unit_price_"):
                select_parts.append(f"gpbo.{field}")
            # Remove regular_price and special_price from here, as they belong to g_prices
            elif field in ["regular_price", "special_price"]:
                continue # Skip these fields
            else:
                select_parts.append(f"gp.{field}") # Direct mapping for other fields

        select_clause = ", ".join(select_parts)

        join_clause = ""
        # Add join for best offers if selecting best offer fields
        if any(f.startswith('best_unit_price_') for f in fields_to_select):
            join_clause = "LEFT JOIN g_product_best_offers gpbo ON gp.id = gpbo.product_id"

        query = f"""
            SELECT {select_clause}
            FROM g_products gp
            {join_clause}
            WHERE gp.id = $1
        """
        async with self._get_conn() as conn:
            self.debug_print(f"get_g_product_details: Final Query: {query}")
            row = await conn.fetchrow(query, product_id)
            
            if row:
                row_dict = dict(row)
                # Manually convert embedding string to list of floats if it's a string
                if "embedding" in row_dict and isinstance(row_dict["embedding"], str):
                    try:
                        row_dict["embedding"] = json.loads(row_dict["embedding"])
                    except json.JSONDecodeError:
                        self.debug_print(f"Warning: Could not decode embedding string: {row_dict['embedding']}")
                        row_dict["embedding"] = None
                return row_dict
            return None

 
    async def add_many_g_products(self, g_products: List[GProduct]) -> int:
        """
        Adds multiple golden products to the g_products table.
        """
        self.debug_print(f"add_many_g_products: Adding {len(g_products)} products.")
        if not g_products:
            return 0

        records = [
            (
                p.ean, # Added ean
                p.canonical_name,
                p.brand,
                p.category,
                p.text_for_embedding,
                p.base_unit_type,
                json.dumps(p.variants) if p.variants is not None else None, # Convert to JSON string
                p.keywords,
                p.is_generic_product,
                p.seasonal_start_month, # Added seasonal_start_month
                p.seasonal_end_month,   # Added seasonal_end_month
                p.embedding,
            )
            for p in g_products
        ]
        async with self._get_conn() as conn:
            # Use copy_records_to_table for efficient bulk insert
            # Ensure the order of columns matches the order in records
            result = await conn.copy_records_to_table(
                'g_products',
                records=records,
                columns=[
                    'ean',
                    'canonical_name', 'brand', 'category', 'text_for_embedding',
                    'base_unit_type', 'variants', 'keywords', 'is_generic_product',
                    'seasonal_start_month', 'seasonal_end_month', 'embedding'
                ]
            )
            self.debug_print(f"add_many_g_products: Inserted {result} rows.")
            return result
    
    async def add_many_g_prices(self, g_prices: List[GPrice]) -> int:
        """
        Adds multiple golden prices to the g_prices table.
        """
        self.debug_print(f"add_many_g_prices: Adding {len(g_prices)} prices.")
        if not g_prices:
            return 0

        records = [
            (
                p.product_id,
                p.store_id,
                p.price_date,
                p.regular_price,
                p.special_price,
                p.price_per_kg,
                p.price_per_l,
                p.price_per_piece,
            )
            for p in g_prices
        ]
        async with self._get_conn() as conn:
            result = await conn.copy_records_to_table(
                'g_prices',
                records=records,
                columns=[
                    'product_id', 'store_id', 'price_date', 'regular_price',
                    'special_price', 'price_per_kg', 'price_per_l', 'price_per_piece'
                ]
            )
            self.debug_print(f"add_many_g_prices: Inserted {result} rows.")
            return result
    
    async def add_many_g_product_best_offers(self, g_offers: List[GProductBestOffer]) -> int:
        """
        Adds multiple golden product best offers to the g_product_best_offers table.
        """
        self.debug_print(f"add_many_g_product_best_offers: Adding {len(g_offers)} offers.")
        if not g_offers:
            return 0

        records = [
            (
                o.product_id,
                o.best_unit_price_per_kg,
                o.best_unit_price_per_l,
                o.best_unit_price_per_piece,
                o.lowest_price_in_season, # Added lowest_price_in_season
                o.best_price_store_id,
                o.best_price_found_at,
            )
            for o in g_offers
        ]
        async with self._get_conn() as conn:
            result = await conn.copy_records_to_table(
                'g_product_best_offers',
                records=records,
                columns=[
                    'product_id', 'best_unit_price_per_kg', 'best_unit_price_per_l',
                    'best_unit_price_per_piece', 'lowest_price_in_season', 'best_price_store_id', 'best_price_found_at'
                ]
            )
            self.debug_print(f"add_many_g_product_best_offers: Inserted {result} rows.")
            return result

    async def get_overall_seasonal_best_price_for_generic_product(
        self,
        canonical_name: str,
        category: str,
        current_month: int,
        limit: int = 10,
        offset: int = 0
    ) -> list[dict[str, Any]]:
        """
        Finds the lowest seasonal price for generic products across all chains
        that match the canonical name and category and are currently in season.
        """
        self.debug_print(f"get_overall_seasonal_best_price_for_generic_product: canonical_name={canonical_name}, category={category}, current_month={current_month}")
        
        async with self._get_conn() as conn:
            query = """
                SELECT
                    gp.id AS product_id,
                    gp.canonical_name,
                    gp.category,
                    gp.brand,
                    gp.base_unit_type,
                    gp.variants,
                    gp.text_for_embedding,
                    gp.keywords,
                    gp.is_generic_product,
                    gp.seasonal_start_month,
                    gp.seasonal_end_month,
                    gpbo.lowest_price_in_season,
                    gpbo.best_price_store_id,
                    gpbo.best_price_found_at
                FROM g_products gp
                JOIN g_product_best_offers gpbo ON gp.id = gpbo.product_id
                WHERE
                    gp.is_generic_product = TRUE
                    AND gp.canonical_name ILIKE $1
                    AND gp.category ILIKE $2
                    AND gp.seasonal_start_month IS NOT NULL
                    AND gp.seasonal_end_month IS NOT NULL
                    AND (
                        ($3 >= gp.seasonal_start_month AND $3 <= gp.seasonal_end_month) OR
                        (gp.seasonal_start_month > gp.seasonal_end_month AND ($3 >= gp.seasonal_start_month OR $3 <= gp.seasonal_end_month))
                    )
                ORDER BY gpbo.lowest_price_in_season ASC NULLS LAST
                LIMIT $4 OFFSET $5;
            """
            params = [f"%{canonical_name}%", f"%{category}%", current_month, limit, offset]
            
            self.debug_print(f"get_overall_seasonal_best_price_for_generic_product: Executing Query!!!")
            self.debug_print(f"get_overall_seasonal_best_price_for_generic_product: With Params: {params}")
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]
