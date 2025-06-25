from contextlib import asynccontextmanager
import asyncpg
from typing import (
    AsyncGenerator,
    AsyncIterator,
    List,
    Any,
    Optional,
)
import os
from datetime import date, datetime
from decimal import Decimal
import sys
import json

from .base import Database
from .models import (
    GProduct, GProductWithId, GPrice, GStore, GStoreWithId, GProductBestOffer,
    GProductBestOfferWithId,
)

class PostgresDatabaseV2(Database):
    """PostgreSQL implementation of the database interface for v2 using asyncpg."""

    def __init__(self, dsn: str, min_size: int = 10, max_size: int = 30):
        self.dsn = dsn
        self.min_size = min_size
        self.max_size = max_size
        self.pool = None
        def debug_print_db(*args, **kwargs):
            print("[DEBUG psql_v2]", *args, file=sys.stderr, **kwargs)
        self.debug_print = debug_print_db

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(
            dsn=self.dsn,
            min_size=self.min_size,
            max_size=self.max_size,
        )

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

    async def create_tables(self) -> None:
        self.debug_print("create_tables method in psql_v2.py is a placeholder.")
        pass

    async def _fetchval(self, query: str, *args: Any) -> Any:
        async with self._get_conn() as conn:
            return await conn.fetchval(query, *args)

    # Placeholder implementations for abstract methods from Database
    async def add_chain(self, chain: Any) -> int:
        raise NotImplementedError("add_chain is not implemented in PostgresDatabaseV2")

    async def list_chains(self) -> list[Any]:
        raise NotImplementedError("list_chains is not implemented in PostgresDatabaseV2")

    async def list_latest_chain_stats(self) -> list[Any]:
        raise NotImplementedError("list_latest_chain_stats is not implemented in PostgresDatabaseV2")

    async def add_store(self, store: Any) -> int:
        raise NotImplementedError("add_store is not implemented in PostgresDatabaseV2")

    async def update_store(
        self,
        chain_id: int,
        store_code: str,
        *,
        address: str | None = None,
        city: str | None = None,
        zipcode: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
        phone: str | None = None,
    ) -> bool:
        raise NotImplementedError("update_store is not implemented in PostgresDatabaseV2")

    async def list_stores(self, chain_code: str) -> list[Any]:
        raise NotImplementedError("list_stores is not implemented in PostgresDatabaseV2")

    async def filter_stores(
        self,
        chain_codes: list[str] | None = None,
        city: str | None = None,
        address: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
        d: float = 10.0,
    ) -> list[Any]:
        raise NotImplementedError("filter_stores is not implemented in PostgresDatabaseV2")

    async def get_product_barcodes(self) -> dict[str, int]:
        raise NotImplementedError("get_product_barcodes is not implemented in PostgresDatabaseV2")

    async def get_chain_product_map(self, chain_id: int) -> dict[str, int]:
        raise NotImplementedError("get_chain_product_map is not implemented in PostgresDatabaseV2")

    async def add_ean(self, ean: str) -> int:
        raise NotImplementedError("add_ean is not implemented in PostgresDatabaseV2")

    async def get_products_by_ean(self, ean: list[str]) -> list[Any]:
        raise NotImplementedError("get_products_by_ean is not implemented in PostgresDatabaseV2")

    async def update_product(self, product: Any) -> bool:
        raise NotImplementedError("update_product is not implemented in PostgresDatabaseV2")

    async def get_chain_products_for_product(
        self,
        product_ids: list[int],
        chain_ids: list[int] | None = None,
    ) -> list[Any]:
        raise NotImplementedError("get_chain_products_for_product is not implemented in PostgresDatabaseV2")

    async def search_products(self, query: str) -> list[Any]:
        # This is the v1 search_products, not the v2 hybrid search
        raise NotImplementedError("search_products (v1) is not implemented in PostgresDatabaseV2")

    async def add_many_prices(self, prices: list[Any]) -> int:
        raise NotImplementedError("add_many_prices is not implemented in PostgresDatabaseV2")

    async def add_many_chain_products(
        self,
        chain_products: list[Any],
    ) -> int:
        raise NotImplementedError("add_many_chain_products is not implemented in PostgresDatabaseV2")

    async def compute_chain_prices(self, date: date) -> None:
        raise NotImplementedError("compute_chain_prices is not implemented in PostgresDatabaseV2")

    async def compute_chain_stats(self, date: date) -> None:
        raise NotImplementedError("compute_chain_stats is not implemented in PostgresDatabaseV2")

    async def get_product_prices(
        self,
        product_ids: list[int],
        date: date,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError("get_product_prices (v1) is not implemented in PostgresDatabaseV2")

    async def get_product_store_prices(
        self,
        product_id: int,
        chain_ids: list[int] | None,
    ) -> list[Any]:
        raise NotImplementedError("get_product_store_prices (v1) is not implemented in PostgresDatabaseV2")

    async def get_user_by_api_key(self, api_key: str) -> Any | None:
        raise NotImplementedError("get_user_by_api_key is not implemented in PostgresDatabaseV2")

    # Existing v2 specific methods
    async def get_g_products_hybrid_search(
        self,
        query: str,
        limit: int = 20,
        offset: int = 0,
        sort_by: Optional[str] = None,
        category: Optional[str] = None,
        brand: Optional[str] = None,
    ) -> list[dict[str, Any]]:
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
            return [dict(row) for row in rows]

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
