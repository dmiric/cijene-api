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

    async def get_g_products_hybrid_search(
        self,
        query: str,
        limit: int = 20,
        offset: int = 0,
        sort_by: Optional[str] = None,
        category: Optional[str] = None,
        brand: Optional[str] = None,
    ) -> list[dict[str, Any]]: # Return list of dicts for flexibility
        """
        Performs hybrid search (vector + keyword) on g_products, fuses results with RRF,
        and applies sorting based on g_product_best_offers.
        """
        self.debug_print(f"get_g_products_hybrid_search: query={query}, sort_by={sort_by}, category={category}, brand={brand}")

        fields_to_select = list(PRODUCT_DB_SEARCH_FIELDS) # Start with all DB search fields

        # Ensure 'rank' is selected if sorting by relevance (default or explicit)
        if (sort_by is None or sort_by == 'relevance') and 'rank' not in fields_to_select:
            fields_to_select.append('rank')

        # Basic validation for fields (can be more robust)
        # Valid fields are actual columns from g_products plus 'rank' and best offer fields
        valid_fields = set(PRODUCT_DB_SEARCH_FIELDS + ["embedding"]) # Add embedding as it's a DB field
        if not all(f in valid_fields for f in fields_to_select):
            raise ValueError("Invalid field requested for product search.")

        # Construct SELECT clause dynamically
        select_parts = []
        for field in fields_to_select:
            if field == "rank":
                select_parts.append("ts_rank_cd(to_tsvector('hr', array_to_string(gp.keywords, ' ')), websearch_to_tsquery('hr', $1)) AS rank")
            elif field.startswith("best_unit_price_"):
                select_parts.append(f"gpbo.{field}")
            else:
                select_parts.append(f"gp.{field}") # Direct mapping for other fields

        select_clause = ", ".join(select_parts)

        where_conditions = []
        params = [query]
        param_counter = 2

        # Initial FTS condition
        fts_condition = "to_tsvector('hr', array_to_string(gp.keywords, ' ')) @@ websearch_to_tsquery('hr', $1)"
        where_conditions.append(fts_condition)

        if category:
            where_conditions.append(f"category ILIKE ${param_counter}")
            params.append(f"%{category}%")
            param_counter += 1
        if brand:
            where_conditions.append(f"brand ILIKE ${param_counter}")
            params.append(f"%{brand}%")
            param_counter += 1

        from_clause = "FROM g_products gp"
        join_clause = ""
        
        # Add join for best offers if sorting by value or selecting best offer fields
        if sort_by and sort_by.startswith('best_value_') or any(f.startswith('best_unit_price_') for f in fields_to_select):
            join_clause = "LEFT JOIN g_product_best_offers gpbo ON gp.id = gpbo.product_id"

        order_by_clause = " ORDER BY rank DESC" # Default for relevance
        if sort_by:
            if sort_by == 'best_value_kg':
                order_by_clause = " ORDER BY gpbo.best_unit_price_per_kg ASC NULLS LAST"
            elif sort_by == 'best_value_l':
                order_by_clause = " ORDER BY gpbo.best_unit_price_per_l ASC NULLS LAST"
            elif sort_by == 'best_value_piece':
                order_by_clause = " ORDER BY gpbo.best_unit_price_per_piece ASC NULLS LAST"
            elif sort_by == 'relevance':
                order_by_clause = " ORDER BY rank DESC"

        final_query = f"""
            SELECT {select_clause}
            {from_clause}
            {join_clause}
            WHERE {' AND '.join(where_conditions)}
            {order_by_clause}
            LIMIT ${param_counter} OFFSET ${param_counter + 1}
        """
        params.extend([limit, offset])

        async with self._get_conn() as conn:
            self.debug_print(f"get_g_products_hybrid_search: Executing Query: {final_query}")
            self.debug_print(f"get_g_products_hybrid_search: With Params: {params}")
            rows = await conn.fetch(final_query, *params)
            self.debug_print(f"get_g_products_hybrid_search: Rows fetched: {len(rows)}")
            
            # Return as list of dictionaries for flexibility
            results = []
            for row in rows:
                row_dict = dict(row)
                # Manually convert embedding string to list of floats if it's a string
                if "embedding" in row_dict and isinstance(row_dict["embedding"], str):
                    try:
                        row_dict["embedding"] = json.loads(row_dict["embedding"])
                    except json.JSONDecodeError:
                        self.debug_print(f"Warning: Could not decode embedding string: {row_dict['embedding']}")
                        row_dict["embedding"] = None
                results.append(row_dict)
            return results

    
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
                    gs.code AS store_code, -- Changed from gs.name to gs.code
                    gs.address AS store_address,
                    gs.city AS store_city,
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
                WHERE gpr.product_id = $1 AND gpr.store_id = ANY($2)
                ORDER BY COALESCE(gpr.special_price, gpr.regular_price) ASC
            """
            self.debug_print(f"get_g_product_prices_by_location: Executing Query: {query}")
            self.debug_print(f"get_g_product_prices_by_location: With Params: {[product_id, store_ids]}")
            rows = await conn.fetch(query, product_id, store_ids)
            return [dict(row) for row in rows]

    
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
                json.dumps(p.variants) if p.variants is not None else None, # Convert to JSON string
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
                    'ean',
                    'canonical_name', 'brand', 'category', 'text_for_embedding',
                    'base_unit_type', 'variants', 'embedding', 'keywords'
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
                    'best_unit_price_per_piece', 'best_price_store_id', 'best_price_found_at'
                ]
            )
            self.debug_print(f"add_many_g_product_best_offers: Inserted {result} rows.")
            return result
