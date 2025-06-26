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
import pgvector.asyncpg

from service.db.base import BaseRepository # Changed from Database as DBConnectionManager
from service.db.models import (
    Chain,
    ChainStats,
    ChainWithId,
    Product,
    ProductWithId,
    Store,
    ChainProduct,
    Price,
    StorePrice,
    StoreWithId,
    ChainProductWithId,
    User,
    UserLocation,
    ChatMessage,
    UserPreference,
    SearchKeyword,
    GProduct,
    GPrice,
    GProductBestOffer,
)


class ProductRepository(BaseRepository): # Changed inheritance
    """
    Contains all logic for interacting with the 'legacy' product-related tables
    (products, chain_products, prices, chain_prices, search_keywords).
    """

    def __init__(self, dsn: str, min_size: int = 10, max_size: int = 30):
        self.dsn = dsn
        self.min_size = min_size
        self.max_size = max_size
        self.pool = None
        def debug_print_db(*args, **kwargs):
            print("[DEBUG product_repo]", *args, file=sys.stderr, **kwargs)
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

    async def get_product_barcodes(self) -> dict[str, int]:
        async with self._get_conn() as conn:
            rows = await conn.fetch("SELECT id, ean FROM products")
            return {row["ean"]: row["id"] for row in rows}

    async def get_chain_product_map(self, chain_id: int) -> dict[str, int]:
        async with self._get_conn() as conn:
            rows = await conn.fetch(
                """
                SELECT code, id FROM chain_products WHERE chain_id = $1
                """,
                chain_id,
            )
            return {row["code"]: row["id"] for row in rows}

    async def add_chain(self, chain: Chain) -> int:
        async with self._atomic() as conn:
            chain_id = await conn.fetchval(
                "SELECT id FROM chains WHERE code = $1",
                chain.code,
            )
            if chain_id is not None:
                return chain_id
            chain_id = await conn.fetchval(
                "INSERT INTO chains (code) VALUES ($1) RETURNING id",
                chain.code,
            )
            if chain_id is None:
                raise RuntimeError(f"Failed to insert chain {chain.code}")
            return chain_id

    async def list_chains(self) -> list[ChainWithId]:
        async with self._get_conn() as conn:
            rows = await conn.fetch("SELECT id, code FROM chains")
            return [ChainWithId(**row) for row in rows]  # type: ignore

    async def add_ean(self, ean: str) -> int:
        """
        Add an empty product with only EAN barcode info.

        Args:
            ean: The EAN code to add.

        Returns:
            The database ID of the created product.
        """
        return await self._fetchval(
            "INSERT INTO products (ean) VALUES ($1) RETURNING id",
            ean,
        )

    async def get_products_by_ean(self, ean: list[str]) -> list[ProductWithId]:
        async with self._get_conn() as conn:
            rows = await conn.fetch(
                """
                SELECT id, ean, brand, name, quantity, unit
                FROM products WHERE ean = ANY($1)
                """,
                ean,
            )
            return [ProductWithId(**row) for row in rows]  # type: ignore

    async def get_product_store_prices(
        self, product_id: int, chain_ids: list[int] | None
    ) -> list[StorePrice]:
        async with self._get_conn() as conn:
            query = """
                WITH chains_dates AS (
                  -- Find the latest loaded data per chain
                    SELECT DISTINCT ON (chain_id) chain_id, price_date AS last_price_date
                    FROM chain_stats
                    ORDER BY chain_id, price_date DESC
                )
                SELECT
                    chains.id AS chain_id,
                    chains.code AS chain_code,
                    products.ean,
                    prices.price_date,
                    prices.regular_price,
                    prices.special_price,
                    prices.best_price_30,
                    prices.unit_price,
                    prices.anchor_price,
                    stores.code AS store_code,
                    stores.type,
                    stores.address,
                    stores.city,
                    stores.zipcode
                FROM chains_dates
                JOIN chains ON chains.id = chains_dates.chain_id
                JOIN chain_products ON chain_products.chain_id = chains.id
                JOIN products ON products.id = chain_products.product_id
                JOIN prices ON prices.chain_product_id = chain_products.id
                           AND prices.price_date = chains_dates.last_price_date
                JOIN stores ON stores.id = prices.store_id
                WHERE products.id = $1
            """

            if chain_ids:
                query += "AND chains.id = ANY($2)"
                rows = await conn.fetch(query, product_id, chain_ids)
            else:
                rows = await conn.fetch(query, product_id)

            return [
                StorePrice(
                    chain=row["chain_code"],
                    ean=row["ean"],
                    price_date=row["price_date"],
                    regular_price=row["regular_price"],
                    special_price=row["special_price"],
                    unit_price=row["unit_price"],
                    best_price_30=row["best_price_30"],
                    anchor_price=row["anchor_price"],
                    store=Store(
                        chain_id=row["chain_id"],
                        code=row["store_code"],
                        type=row["type"],
                        address=row["address"],
                        city=row["city"],
                        zipcode=row["zipcode"],
                        lat=row["lat"],
                        lon=row["lon"],
                        phone=row["phone"],
                    ),
                )
                for row in rows
            ]

    async def update_product(self, product: Product) -> bool:
        """
        Update product information by EAN code.

        Args:
            product: Product object containing the EAN and fields to update.
                    Only non-None fields will be updated in the database.

        Returns:
            True if the product was updated, False if not found.
        """
        async with self._get_conn() as conn:
            result = await conn.execute(
                """
                UPDATE products
                SET
                    brand = COALESCE($2, products.brand),
                    name = COALESCE($3, products.name),
                    quantity = COALESCE($4, products.quantity),
                    unit = COALESCE($5, products.unit)
                WHERE ean = $1
                """,
                product.ean,
                product.brand,
                product.name,
                product.quantity,
                product.unit,
            )
            _, rowcount = result.split(" ")
            return int(rowcount) == 1

    async def get_chain_products_for_product(
        self,
        product_ids: list[int],
        chain_ids: list[int] | None = None,
    ) -> list[ChainProductWithId]:
        async with self._get_conn() as conn:
            if chain_ids:
                # Use ANY for filtering by chain IDs
                query = """
                    SELECT
                        id, chain_id, product_id, code, name, brand,
                        category, unit, quantity
                    FROM chain_products
                    WHERE product_id = ANY($1) AND chain_id = ANY($2)
                """
                rows = await conn.fetch(query, product_ids, chain_ids)
            else:
                # Original query when no chain filtering
                query = """
                    SELECT
                        id, chain_id, product_id, code, name, brand,
                        category, unit, quantity
                    FROM chain_products
                    WHERE product_id = ANY($1)
                """
                rows = await conn.fetch(query, product_ids)
            return [ChainProductWithId(**row) for row in rows]  # type: ignore

    async def search_products(self, query: str) -> list[ProductWithId]:
        if not query.strip():
            return []

        words = [word.strip() for word in query.split(',') if word.strip()]
        if not words:
            return []

        where_conditions = []
        params = []
        SIMILARITY_THRESHOLD = 0.3

        for idx, word in enumerate(words, start=1):
            word = word.lower().replace("%", "")
            unaccented_word_param = f"${idx}"
            unaccented_word_ilike_param = f"${idx + len(words)}"

            fuzzy_condition = f"similarity(sk.keyword, {unaccented_word_param}) > {SIMILARITY_THRESHOLD}"
            params.append(word)

            ilike_condition = f"sk.keyword ILIKE '%' || {unaccented_word_ilike_param} || '%'"
            params.append(word)

            where_conditions.append(f"({fuzzy_condition} OR {ilike_condition})")

        where_clause = " OR ".join(where_conditions)
        query_sql = f"""
            SELECT
                p.ean,
                COUNT(sk) AS keyword_count
            FROM search_keywords sk
            JOIN products p ON sk.ean = p.ean
            WHERE {where_clause}
            GROUP BY p.ean
            ORDER BY keyword_count DESC
        """

        async with self._get_conn() as conn:
            self.debug_print(f"search_products: Query: {query_sql}")
            self.debug_print(f"search_products: Params: {params}")
            rows = await conn.fetch(query_sql, *params)
            eans = [row["ean"] for row in rows]

        return await self.get_products_by_ean(eans)

    async def get_product_prices(
        self, product_ids: list[int], date: date, store_ids: list[int] | None = None
    ) -> list[dict[str, Any]]:
        async with self._get_conn() as conn:
            query = """
                SELECT
                    c.code AS chain_code,
                    cpr.product_id,
                    cpr.id AS chain_product_id,
                    p.store_id,
                    s.code AS store_code,
                    p.price_date,
                    p.regular_price,
                    p.special_price,
                    p.unit_price,
                    p.best_price_30,
                    p.anchor_price
                FROM prices p
                JOIN chain_products cpr ON p.chain_product_id = cpr.id
                JOIN chains c ON cpr.chain_id = c.id
                JOIN stores s ON p.store_id = s.id
                WHERE cpr.product_id = ANY($1)
                AND p.price_date = (
                    SELECT MAX(p2.price_date)
                    FROM prices p2
                    WHERE p2.chain_product_id = p.chain_product_id
                    AND p2.store_id = p.store_id
                    AND p2.price_date <= $2
                )
            """
            params = [product_ids, date]

            if store_ids:
                query += " AND p.store_id = ANY($3)"
                params.append(store_ids)
            
            self.debug_print(f"get_product_prices: Query: {query}")
            self.debug_print(f"get_product_prices: Params: {params}")
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]

    async def add_many_prices(self, prices: list[Price]) -> int:
        async with self._atomic() as conn:
            await conn.execute(
                """
                CREATE TEMP TABLE temp_prices (
                    chain_product_id INTEGER,
                    store_id INTEGER,
                    price_date DATE,
                    regular_price DECIMAL(10, 2),
                    special_price DECIMAL(10, 2),
                    unit_price DECIMAL(10, 2),
                    best_price_30 DECIMAL(10, 2),
                    anchor_price DECIMAL(10, 2)
                )
                """
            )
            await conn.copy_records_to_table(
                "temp_prices",
                records=(
                    (
                        p.chain_product_id,
                        p.store_id,
                        p.price_date,
                        p.regular_price,
                        p.special_price,
                        p.unit_price,
                        p.best_price_30,
                        p.anchor_price,
                    )
                    for p in prices
                ),
            )
            result = await conn.execute(
                """
                INSERT INTO prices(
                    chain_product_id,
                    store_id,
                    price_date,
                    regular_price,
                    special_price,
                    unit_price,
                    best_price_30,
                    anchor_price
                )
                SELECT * from temp_prices
                ON CONFLICT DO NOTHING
                """
            )
            await conn.execute("DROP TABLE temp_prices")
            _, _, rowcount = result.split(" ")
            rowcount = int(rowcount)
            return rowcount

    async def add_many_chain_products(
        self,
        chain_products: List[ChainProduct],
    ) -> int:
        async with self._atomic() as conn:
            await conn.execute(
                """
                CREATE TEMP TABLE temp_chain_products (
                    chain_id INTEGER,
                    product_id INTEGER,
                    code VARCHAR(100),
                    name VARCHAR(255),
                    brand VARCHAR(255),
                    category VARCHAR(255),
                    unit VARCHAR(50),
                    quantity VARCHAR(50)
                )
                """
            )
            await conn.copy_records_to_table(
                "temp_chain_products",
                records=(
                    (
                        cp.chain_id,
                        cp.product_id,
                        cp.code,
                        cp.name,
                        cp.brand,
                        cp.category,
                        cp.unit,
                        cp.quantity,
                    )
                    for cp in chain_products
                ),
            )

            result = await conn.execute(
                """
                INSERT INTO chain_products(
                    chain_id,
                    product_id,
                    code,
                    name,
                    brand,
                    category,
                    unit,
                    quantity
                )
                SELECT * from temp_chain_products
                ON CONFLICT DO NOTHING
                """
            )
            await conn.execute("DROP TABLE temp_chain_products")

            _, _, rowcount = result.split(" ")
            rowcount = int(rowcount)
            return rowcount

    async def compute_chain_prices(self, date: date) -> None:
        async with self._get_conn() as conn:
            await conn.execute(
                """
                INSERT INTO chain_prices (
                    chain_product_id,
                    price_date,
                    min_price,
                    max_price,
                    avg_price
                )
                SELECT
                    chain_product_id,
                    price_date,
                    MIN(
                        LEAST(
                            COALESCE(regular_price, special_price),
                            COALESCE(special_price, regular_price)
                        )
                    ) AS min_price,
                    MAX(
                        LEAST(
                            COALESCE(regular_price, special_price),
                            COALESCE(special_price, regular_price)
                        )
                    ) AS max_price,
                    ROUND(
                        AVG(
                            LEAST(
                                COALESCE(regular_price, special_price),
                                COALESCE(special_price, regular_price)
                            )
                        ),
                        2
                    ) AS avg_price
                FROM prices
                WHERE price_date = $1
                GROUP BY chain_product_id, price_date
                ON CONFLICT (chain_product_id, price_date)
                DO UPDATE SET
                    min_price = EXCLUDED.min_price,
                    max_price = EXCLUDED.max_price,
                    avg_price = EXCLUDED.avg_price;

                """,
                date,
            )

    async def get_products_for_keyword_generation(
        self, limit: int = 100, product_name_filter: str | None = None
    ) -> list[dict[str, Any]]:
        async with self._get_conn() as conn:
            query = """
                SELECT
                    p.ean,
                    COALESCE(cp.name, p.name) AS product_name,
                    COALESCE(cp.brand, p.brand) AS brand_name
                FROM products p
                LEFT JOIN chain_products cp ON p.id = cp.product_id
                WHERE p.ean NOT IN (SELECT ean FROM search_keywords)
            """
            params = []
            param_index = 1

            if product_name_filter:
                query += f" AND COALESCE(cp.name, p.name) ILIKE '%' || ${param_index} || '%'"
                params.append(product_name_filter)
                param_index += 1

            query += f"""
                ORDER BY LENGTH(COALESCE(cp.name, p.name)) DESC, p.ean
                LIMIT ${param_index}
            """
            params.append(limit)
            rows = await conn.fetch(query, *params)
            return [
                {"ean": row["ean"], "product_name": row["product_name"], "brand_name": row["brand_name"]}
                for row in rows
            ]

    async def add_many_search_keywords(self, keywords: List[SearchKeyword]) -> int:
        """
        Bulk insert search keywords into the database.
        On conflict (ean, keyword), do nothing.
        """
        async with self._atomic() as conn:
            await conn.execute(
                """
                CREATE TEMP TABLE temp_search_keywords (
                    id INTEGER,
                    ean VARCHAR(255),
                    keyword TEXT,
                    created_at TIMESTAMP WITH TIME ZONE
                )
                """
            )
            await conn.copy_records_to_table(
                "temp_search_keywords",
                records=(
                    (
                        sk.id,
                        sk.ean,
                        sk.keyword,
                        sk.created_at,
                    )
                    for sk in keywords
                ),
            )
            result = await conn.execute(
                """
                INSERT INTO search_keywords(id, ean, keyword, created_at)
                SELECT * from temp_search_keywords
                ON CONFLICT (id) DO NOTHING
                """
            )
            await conn.execute("DROP TABLE temp_search_keywords")
            _, _, rowcount = result.split(" ")
            rowcount = int(rowcount)
            return rowcount
