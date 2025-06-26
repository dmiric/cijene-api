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
from uuid import UUID, uuid4
import sys
import json
import pgvector.asyncpg

from .base import Database, BaseRepository # Added BaseRepository
from .models import (
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

from .repositories.product_repo import ProductRepository
from .repositories.store_repo import StoreRepository
from .repositories.user_repo import UserRepository
from .repositories.chat_repo import ChatRepository
from .repositories.stats_repo import StatsRepository
from .repositories.golden_product_repo import GoldenProductRepository


class PostgresDatabase(Database):
    """
    A Facade that provides the complete database interface for V1.
    It composes various repositories to handle data access.
    """

    def __init__(self, dsn: str, min_size: int = 10, max_size: int = 30):
        self.dsn = dsn
        self.min_size = min_size
        self.max_size = max_size
        self.pool = None

        # Using print for debugging as logging is not appearing reliably
        def debug_print_db(*args, **kwargs):
            print("[DEBUG psql_v1]", *args, file=sys.stderr, **kwargs)
        self.debug_print = debug_print_db

        # Instantiate legacy repos
        self.products = ProductRepository(dsn, min_size=min_size, max_size=max_size)
        self.stores = StoreRepository(dsn, min_size=min_size, max_size=max_size)
        self.users = UserRepository(dsn, min_size=min_size, max_size=max_size)
        self.chat = ChatRepository(dsn, min_size=min_size, max_size=max_size)
        self.stats = StatsRepository(dsn, min_size=min_size, max_size=max_size)

        # Also instantiate the golden repo for the normalizer's bulk inserts
        self.golden_products = GoldenProductRepository(dsn, min_size=min_size, max_size=max_size)


    async def connect(self) -> None:
        # Connect all repos and ensure they share the same connection pool
        self.pool = await asyncpg.create_pool( # Create the main pool here
            dsn=self.dsn,
            min_size=self.min_size,
            max_size=self.max_size,
            init=self._init_connection, # Keep init for pgvector registration
        )
        await self.products.connect() # Connect products repo to initialize its pool
        self.products.pool = self.pool # Assign the main pool to products repo

        self.stores.pool = self.pool
        self.users.pool = self.pool
        self.chat.pool = self.pool
        self.stats.pool = self.pool
        self.golden_products.pool = self.pool

    async def _init_connection(self, conn):
        # Register the 'vector' type for asyncpg using pgvector's utility
        await pgvector.asyncpg.register_vector(conn)


    @asynccontextmanager
    async def _get_conn(self) -> AsyncGenerator[asyncpg.Connection, None]:
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
        # This method should ideally be handled by migrations or a dedicated setup script
        # For now, it can remain as a placeholder or delegate to a specific repo if needed.
        self.debug_print("create_tables method in psql.py is a placeholder. Use migrations.")
        pass

    async def _fetchval(self, query: str, *args: Any) -> Any:
        async with self._get_conn() as conn:
            return await conn.fetchval(query, *args)

    # --- Implementations of abstract methods from Database (delegating to repositories) ---

    async def add_chain(self, chain: Chain) -> int:
        return await self.products.add_chain(chain)

    async def list_chains(self) -> list[ChainWithId]:
        return await self.products.list_chains()

    async def list_latest_chain_stats(self) -> list[ChainStats]:
        return await self.stats.list_latest_chain_stats()

    async def add_store(self, store: Store) -> int:
        return await self.stores.add_store(store)

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
        return await self.stores.update_store(chain_id, store_code, address=address, city=city, zipcode=zipcode, lat=lat, lon=lon, phone=phone)

    async def list_stores(self, chain_code: str) -> list[StoreWithId]:
        return await self.stores.list_stores(chain_code)

    async def filter_stores(
        self,
        chain_codes: list[str] | None = None,
        city: str | None = None,
        address: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
        d: float = 10.0,
    ) -> list[StoreWithId]:
        return await self.stores.filter_stores(chain_codes, city, address, lat, lon, d)

    async def get_product_barcodes(self) -> dict[str, int]:
        return await self.products.get_product_barcodes()

    async def get_chain_product_map(self, chain_id: int) -> dict[str, int]:
        return await self.products.get_chain_product_map(chain_id)

    async def add_ean(self, ean: str) -> int:
        return await self.products.add_ean(ean)

    async def get_products_by_ean(self, ean: list[ProductWithId]) -> list[ProductWithId]: # Changed type hint to match models
        return await self.products.get_products_by_ean(ean)

    async def update_product(self, product: Product) -> bool:
        return await self.products.update_product(product)

    async def get_chain_products_for_product(
        self,
        product_ids: list[int],
        chain_ids: list[int] | None = None,
    ) -> list[ChainProductWithId]:
        return await self.products.get_chain_products_for_product(product_ids, chain_ids)

    async def search_products(self, query: str) -> list[ProductWithId]: # Changed type hint to match models
        return await self.products.search_products(query)

    async def add_many_prices(self, prices: list[Price]) -> int:
        return await self.products.add_many_prices(prices)

    async def add_many_chain_products(
        self,
        chain_products: List[ChainProduct],
    ) -> int:
        return await self.products.add_many_chain_products(chain_products)

    async def compute_chain_prices(self, date: date) -> None:
        return await self.products.compute_chain_prices(date)

    async def compute_chain_stats(self, date: date) -> None:
        return await self.stats.compute_chain_stats(date)

    async def get_product_prices(
        self,
        product_ids: list[int],
        date: date,
        store_ids: list[int] | None = None, # Added store_ids
    ) -> list[dict[str, Any]]:
        return await self.products.get_product_prices(product_ids, date, store_ids) # Pass store_ids

    async def get_product_store_prices(
        self,
        product_id: int,
        chain_ids: list[int] | None,
    ) -> list[StorePrice]:
        return await self.products.get_product_store_prices(product_id, chain_ids)

    async def get_user_by_api_key(self, api_key: str) -> User | None:
        return await self.users.get_user_by_api_key(api_key)

    # --- Pass-through methods for the Normalizer (operating on V2 tables) ---
    async def add_many_g_products(self, g_products: List[GProduct]) -> int:
        return await self.golden_products.add_many_g_products(g_products)

    async def add_many_g_prices(self, g_prices: List[GPrice]) -> int:
        return await self.golden_products.add_many_g_prices(g_prices)

    async def add_many_g_product_best_offers(self, g_offers: List[GProductBestOffer]) -> int:
        return await self.golden_products.add_many_g_product_best_offers(g_offers)

    async def save_chat_message(
        self,
        user_id: int,
        session_id: str,
        message_text: str,
        is_user_message: bool,
        tool_calls: Optional[List[dict]] = None,
        tool_outputs: Optional[List[dict]] = None,
        ai_response: Optional[str] = None,
    ) -> int:
        return await self.chat.save_chat_message(
            user_id, session_id, message_text, is_user_message, tool_calls, tool_outputs, ai_response
        )

    async def get_stores_within_radius(
        self,
        lat: Decimal,
        lon: Decimal,
        radius_meters: int,
        chain_code: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        return await self.stores.get_stores_within_radius(lat, lon, radius_meters, chain_code)
