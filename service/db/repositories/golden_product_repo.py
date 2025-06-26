from contextlib import asynccontextmanager
import asyncpg
from typing import List, Any, Optional, AsyncGenerator, AsyncIterator
import sys
from datetime import date, datetime
from decimal import Decimal
import json

from service.db.base import BaseRepository # Changed from Database as DBConnectionManager
from service.db.models import (
    GProduct, GProductWithId, GPrice, GStore, GStoreWithId, GProductBestOffer,
    GProductBestOfferWithId, ProductSearchItemV2,
)

class GoldenProductRepository(BaseRepository): # Changed inheritance
    """
    Contains all logic for interacting with the 'golden record' tables (g_products,
    g_prices, g_product_best_offers) and the new g_stores table.
    """

    def __init__(self, dsn: str, min_size: int = 10, max_size: int = 30):
        self.dsn = dsn
        self.min_size = min_size
        self.max_size = max_size
        self.pool = None
        def debug_print_db(*args, **kwargs):
            print("[DEBUG golden_product_repo]", *args, file=sys.stderr, **kwargs)
        self.debug_print = debug_print_db

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(
            dsn=self.dsn,
            min_size=self.min_size,
            max_size=self.max_size,
            # Removed init=self._init_connection
        )

    # Removed async def _init_connection(self, conn):
    #     await pgvector.asyncpg.register_vector(conn)

    @asynccontextmanager
    async def _get_conn(self) -> AsyncGenerator[asyncpg.Connection, None]:
        if not self.pool:
            raise RuntimeError("Database pool is not initialized")
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

    async def get_g_products_hybrid_search(
        self,
        query: str,
        limit: int = 20,
        offset: int = 0,
        sort_by: Optional[str] = None,
        category: Optional[str] = None,
        brand: Optional[str] = None,
    ) -> list[ProductSearchItemV2]:
        """
        Performs hybrid search (vector + keyword) on g_products, fuses results with RRF,
        and applies sorting based on g_product_best_offers.
        """
        self.debug_print(f"get_g_products_hybrid_search: query={query}, sort_by={sort_by}, category={category}, brand={brand}")

        where_conditions = []
        params = [query]
        param_counter = 2

        if category:
            where_conditions.append(f"category ILIKE ${param_counter}")
            params.append(f"%{category}%")
            param_counter += 1
        if brand:
            where_conditions.append(f"brand ILIKE ${param_counter}")
            params.append(f"%{brand}%")
            param_counter += 1

        base_product_query = f"""
            SELECT
                gp.id,
                gp.canonical_name AS name,
                gp.brand,
                gp.category,
                gp.text_for_embedding AS description,
                NULL AS image_url,
                NULL AS product_url,
                gp.base_unit_type AS unit_of_measure,
                NULL AS quantity_value,
                gp.embedding,
                gp.keywords,
                ts_rank_cd(to_tsvector('hr', array_to_string(gp.keywords, ' ')), websearch_to_tsquery('hr', $1)) AS rank
            FROM g_products gp
            WHERE to_tsvector('hr', array_to_string(gp.keywords, ' ')) @@ websearch_to_tsquery('hr', $1)
        """
        if where_conditions:
            base_product_query += " AND " + " AND ".join(where_conditions)

        order_by_clause = " ORDER BY rank DESC"
        if sort_by:
            if sort_by == 'best_value_kg':
                base_product_query += """
                    LEFT JOIN g_product_best_offers gpbo ON gp.id = gpbo.product_id
                """
                order_by_clause = " ORDER BY gpbo.best_unit_price_per_kg ASC NULLS LAST"
            elif sort_by == 'best_value_l':
                base_product_query += """
                    LEFT JOIN g_product_best_offers gpbo ON gp.id = gpbo.product_id
                """
                order_by_clause = " ORDER BY gpbo.best_unit_price_per_l ASC NULLS LAST"
            elif sort_by == 'best_value_piece':
                base_product_query += """
                    LEFT JOIN g_product_best_offers gpbo ON gp.id = gpbo.product_id
                """
                order_by_clause = " ORDER BY gpbo.best_unit_price_per_piece ASC NULLS LAST"
            elif sort_by == 'relevance':
                order_by_clause = " ORDER BY rank DESC"

        final_query = f"""
            {base_product_query}
            {order_by_clause}
            LIMIT ${param_counter} OFFSET ${param_counter + 1}
        """
        params.extend([limit, offset])

        async with self._get_conn() as conn:
            self.debug_print(f"get_g_products_hybrid_search: Final Query: {final_query}")
            self.debug_print(f"get_g_products_hybrid_search: Params: {params}")
            rows = await conn.fetch(final_query, *params)
            return [ProductSearchItemV2(**dict(row)) for row in rows]

    async def get_g_product_prices_by_location(
        self, product_id: int, store_ids: list[int]
    ) -> list[dict[str, Any]]:
        """
        Queries g_prices for a specific product across a list of stores, ordered by price.
        """
        self.debug_print(f"get_g_product_prices_by_location: product_id={product_id}, store_ids={store_ids}")
        async with self._get_conn() as conn:
            query = """
                SELECT
                    gp.id AS product_id,
                    gp.canonical_name AS product_name,
                    gp.brand AS product_brand,
                    gs.id AS store_id,
                    gs.name AS store_name,
                    gs.address AS store_address,
                    gs.city AS store_city,
                    gpr.price_date,
                    gpr.regular_price,
                    gpr.special_price,
                    NULL AS unit_price, -- Not directly available in g_prices
                    NULL AS best_price_30, -- Not directly available in g_prices
                    NULL AS anchor_price -- Not directly available in g_prices
                FROM g_prices gpr
                JOIN g_products gp ON gpr.product_id = gp.id
                JOIN g_stores gs ON gpr.store_id = gs.id
                WHERE gpr.product_id = $1 AND gpr.store_id = ANY($2)
                ORDER BY COALESCE(gpr.special_price, gpr.regular_price) ASC
            """
            rows = await conn.fetch(query, product_id, store_ids)
            return [dict(row) for row in rows]

    async def get_g_product_details(self, product_id: int) -> dict[str, Any] | None:
        """
        Retrieves a single product's details from g_products, potentially joining with g_product_best_offers.
        """
        self.debug_print(f"get_g_product_details: product_id={product_id}")
        async with self._get_conn() as conn:
            query = """
                SELECT
                    gp.id,
                    gp.canonical_name AS name,
                    gp.text_for_embedding AS description,
                    gp.brand,
                    gp.category,
                    NULL AS image_url,
                    NULL AS product_url,
                    gp.base_unit_type AS unit_of_measure,
                    NULL AS quantity_value,
                    gp.embedding,
                    gp.keywords,
                    NULL AS keywords_tsv,
                    gpbo.best_unit_price_per_kg,
                    gpbo.best_unit_price_per_l,
                    gpbo.best_unit_price_per_piece
                FROM g_products gp
                LEFT JOIN g_product_best_offers gpbo ON gp.id = gpbo.product_id
                WHERE gp.id = $1
            """
            row = await conn.fetchrow(query, product_id)
            return dict(row) if row else None

    async def get_g_stores_nearby(
        self,
        lat: float,
        lon: float,
        radius_meters: int,
        chain_code: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """
        Performs PostGIS geospatial query on g_stores.location, ordered by distance.
        """
        self.debug_print(f"get_g_stores_nearby: lat={lat}, lon={lon}, radius_meters={radius_meters}, chain_code={chain_code}")
        async with self._get_conn() as conn:
            center_point = f"ST_SetSRID(ST_Point({lon}, {lat}), 4326)::geometry"
            query = f"""
                SELECT
                    gs.id,
                    gs.name,
                    gs.address,
                    gs.city,
                    gs.zipcode,
                    gs.latitude,
                    gs.longitude,
                    gs.chain_code,
                    ST_Distance(gs.location::geography, {center_point}::geography) AS distance_meters
                FROM g_stores gs
                WHERE ST_DWithin(gs.location::geography, {center_point}::geography, $1)
            """
            params = [radius_meters]

            if chain_code:
                query += " AND gs.chain_code ILIKE $2"
                params.append(f"%{chain_code}%")

            query += f" ORDER BY ST_Distance(gs.location, {center_point})"

            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]

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
                p.embedding,
                p.keywords,
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
                    'ean', # Added ean
                    'canonical_name', 'brand', 'category', 'text_for_embedding',
                    'base_unit_type', 'embedding', 'keywords'
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
            )
            for p in g_prices
        ]
        async with self._get_conn() as conn:
            result = await conn.copy_records_to_table(
                'g_prices',
                records=records,
                columns=[
                    'product_id', 'store_id', 'price_date', 'regular_price',
                    'special_price'
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
            )
            for o in g_offers
        ]
        async with self._get_conn() as conn:
            result = await conn.copy_records_to_table(
                'g_product_best_offers',
                records=records,
                columns=[
                    'product_id', 'best_unit_price_per_kg', 'best_unit_price_per_l',
                    'best_unit_price_per_piece'
                ]
            )
            self.debug_print(f"add_many_g_product_best_offers: Inserted {result} rows.")
            return result
