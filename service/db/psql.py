from contextlib import asynccontextmanager
import asyncpg
from typing import (
    AsyncGenerator,
    AsyncIterator,
    List,
    Any,
)
import logging
import os
from datetime import date, datetime # Import datetime
from decimal import Decimal # Import Decimal
import uuid # Import uuid for API key generation
from .base import Database
from .models import (
    Chain,
    ChainWithId,
    Product,
    ProductWithId,
    Store,
    ChainProduct,
    Price,
    StoreWithId,
    ChainProductWithId,
    User,
)


class PostgresDatabase(Database):
    """PostgreSQL implementation of the database interface using asyncpg."""

    def __init__(self, dsn: str, min_size: int = 10, max_size: int = 30):
        """Initialize the PostgreSQL database connection pool.

        Args:
            dsn: Database connection string
            min_size: Minimum number of connections in the pool
            max_size: Maximum number of connections in the pool
        """
        self.dsn = dsn
        self.min_size = min_size
        self.max_size = max_size
        self.pool = None
        self.logger = logging.getLogger(__name__)

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(
            dsn=self.dsn,
            min_size=self.min_size,
            max_size=self.max_size,
        )

    @asynccontextmanager
    async def _get_conn(self) -> AsyncGenerator[Any, asyncpg.Connection]:
        """Context manager to acquire a connection from the pool."""
        if not self.pool:
            raise RuntimeError("Database pool is not initialized")
        async with self.pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def _atomic(self) -> AsyncIterator[asyncpg.Connection]:
        """Context manager for atomic transactions."""
        async with self._get_conn() as conn:
            async with conn.transaction():
                yield conn

    async def close(self) -> None:
        """Close all database connections."""
        if self.pool:
            await self.pool.close()

    async def create_tables(self) -> None:
        schema_path = os.path.join(os.path.dirname(__file__), "psql.sql")

        try:
            with open(schema_path, "r") as f:
                schema_sql = f.read()

            async with self._get_conn() as conn:
                await conn.execute(schema_sql)
                self.logger.info("Database tables created successfully")
        except Exception as e:
            self.logger.error(f"Error creating tables: {e}")
            raise

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

    async def add_store(self, store: Store) -> int:
        # Prepare location geometry if latitude and longitude are provided
        location_geom = None
        if store.latitude is not None and store.longitude is not None:
            location_geom = f"ST_SetSRID(ST_Point({store.longitude}, {store.latitude}), 4326)::geography"

        return await self._fetchval(
            f"""
            INSERT INTO stores (chain_id, code, type, address, city, zipcode, latitude, longitude, location)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, {location_geom if location_geom else 'NULL'})
            ON CONFLICT (chain_id, code) DO UPDATE SET
                type = COALESCE($3, stores.type),
                address = COALESCE($4, stores.address),
                city = COALESCE($5, stores.city),
                zipcode = COALESCE($6, stores.zipcode),
                latitude = COALESCE($7, stores.latitude),
                longitude = COALESCE($8, stores.longitude),
                location = COALESCE(EXCLUDED.location, stores.location)
            RETURNING id
            """,
            store.chain_id,
            store.code,
            store.type,
            store.address or None,
            store.city or None,
            store.zipcode or None,
            store.latitude,
            store.longitude,
        )

    async def list_stores(self, chain_code: str) -> list[StoreWithId]:
        async with self._get_conn() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    s.id, s.chain_id, s.code, s.type, s.address, s.city, s.zipcode, s.latitude, s.longitude
                FROM stores s
                JOIN chains c ON s.chain_id = c.id
                WHERE c.code = $1
                """,
                chain_code,
            )

            return [StoreWithId(**row) for row in rows]  # type: ignore

    async def get_ungeocoded_stores(self) -> list[StoreWithId]:
        """
        Fetches stores that have address information but are missing
        latitude or longitude.
        """
        async with self._get_conn() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    id, chain_id, code, type, address, city, zipcode, latitude, longitude
                FROM stores
                WHERE
                    (latitude IS NULL OR longitude IS NULL) AND
                    (address IS NOT NULL OR city IS NOT NULL OR zipcode IS NOT NULL)
                """
            )
            return [StoreWithId(**row) for row in rows] # type: ignore

    async def get_stores_within_radius(
        self,
        latitude: Decimal,
        longitude: Decimal,
        radius_meters: int,
        chain_code: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Finds and lists stores within a specified radius of a given latitude/longitude.
        Results include chain code and distance from the center point, ordered by distance.
        """
        async with self._get_conn() as conn:
            # Create a geography point for the center of the search
            center_point = f"ST_SetSRID(ST_Point({longitude}, {latitude}), 4326)::geography"

            query = f"""
                SELECT
                    s.id, s.chain_id, s.code, s.type, s.address, s.city, s.zipcode, s.latitude, s.longitude,
                    c.code AS chain_code,
                    ST_Distance(s.location, {center_point}) AS distance_meters
                FROM stores s
                JOIN chains c ON s.chain_id = c.id
                WHERE ST_DWithin(s.location, {center_point}, $1)
            """
            params = [radius_meters]

            if chain_code:
                query += " AND c.code = $2"
                params.append(chain_code)

            query += f" ORDER BY ST_Distance(s.location, {center_point})"

            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]

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

        # TODO: Implement full-text search using PostgreSQL's
        # text search capabilities
        words = [word.strip() for word in query.split(',') if word.strip()] # Split by comma
        if not words:
            return []

        # TODO: Implement full-text search using PostgreSQL's
        # text search capabilities
        # Using pg_trgm for fuzzy matching
        where_conditions = []
        params = []
        # Define a similarity threshold, e.g., 0.3 (can be adjusted)
        SIMILARITY_THRESHOLD = 0.3

        for idx, word in enumerate(words, start=1):
            word = word.lower().replace("%", "")
            unaccented_word_param = f"${idx}" # Parameter for unaccented word
            unaccented_word_ilike_param = f"${idx + len(words)}" # Parameter for ILIKE unaccented word

            # Condition for fuzzy matching using pg_trgm's similarity
            fuzzy_condition = f"similarity(unaccent(sk.keyword), unaccent({unaccented_word_param})) > {SIMILARITY_THRESHOLD}"
            params.append(word)

            # Condition for direct substring matching using ILIKE
            ilike_condition = f"unaccent(sk.keyword) ILIKE '%' || unaccent({unaccented_word_ilike_param}) || '%'"
            params.append(word) # Add the word again for the ILIKE parameter

            where_conditions.append(f"({fuzzy_condition} OR {ilike_condition})")

        where_clause = " OR ".join(where_conditions) # Changed to OR for multi-word search
        query_sql = f"""
            SELECT
                p.ean,
                COUNT(sk) AS keyword_count
            FROM search_keywords sk
            JOIN products p ON sk.ean = p.ean
            WHERE {where_clause}
            GROUP BY p.ean
            ORDER BY keyword_count DESC, MAX(similarity(unaccent(sk.keyword), unaccent(${len(words)}))) DESC
        """
        # The ORDER BY clause needs to refer to the last parameter for similarity,
        # which is now the original word parameter, not the duplicated one for ILIKE.
        # The parameter indices will be 1 to N for similarity, and N+1 to 2N for ILIKE.
        # So, the last similarity parameter is ${len(words)}.

        async with self._get_conn() as conn:
            rows = await conn.fetch(query_sql, *params)
            eans = [row["ean"] for row in rows]

        return await self.get_products_by_ean(eans)

    async def get_product_prices(
        self, product_ids: list[int], date: date
    ) -> list[dict[str, Any]]:
        async with self._get_conn() as conn:
            return await conn.fetch(
                """
                SELECT
                    c.code AS chain,
                    cpr.product_id,
                    cp.min_price,
                    cp.max_price,
                    cp.avg_price
                FROM chain_prices cp
                JOIN chain_products cpr ON cp.chain_product_id = cpr.id
                JOIN chains c ON cpr.chain_id = c.id
                WHERE cpr.product_id = ANY($1)
                AND cp.price_date = (
                    SELECT MAX(cp2.price_date)
                    FROM chain_prices cp2
                    WHERE cp2.chain_product_id = cp.chain_product_id
                    AND cp2.price_date <= $2
                )
                """,
                product_ids,
                date,
            )

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

    async def get_user_by_api_key(self, api_key: str) -> User | None:
        async with self._get_conn() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, name, api_key, is_active, created_at
                FROM users
                WHERE
                    api_key = $1 AND
                    is_active = TRUE
                """,
                api_key,
            )

            if row:
                return User(**row)  # type: ignore
            return None

    async def add_user(self, name: str) -> User:
        """
        Add a new user with a randomly generated API key.

        Args:
            name: The name of the user.

        Returns:
            The created User object.
        """
        api_key = str(uuid.uuid4()) # Generate a random UUID for the API key
        created_at = datetime.now()
        is_active = True # New users are active by default

        async with self._atomic() as conn:
            user_id = await conn.fetchval(
                """
                INSERT INTO users (name, api_key, is_active, created_at)
                VALUES ($1, $2, $3, $4)
                RETURNING id
                """,
                name,
                api_key,
                is_active,
                created_at,
            )
            if user_id is None:
                raise RuntimeError(f"Failed to insert user {name}")

            return User(
                id=user_id,
                name=name,
                api_key=api_key,
                is_active=is_active,
                created_at=created_at,
            )

    async def get_products_for_keyword_generation(self, limit: int = 100) -> list[dict[str, Any]]:
        async with self._get_conn() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    p.ean,
                    COALESCE(cp.name, p.name) AS product_name,
                    COALESCE(cp.brand, p.brand) AS brand_name
                FROM products p
                LEFT JOIN chain_products cp ON p.id = cp.product_id
                WHERE p.ean NOT IN (SELECT ean FROM search_keywords)
                ORDER BY LENGTH(COALESCE(cp.name, p.name)) DESC, p.ean
                LIMIT $1
                """,
                limit
            )
            return [
                {"ean": row["ean"], "product_name": row["product_name"], "brand_name": row["brand_name"]}
                for row in rows
            ]
