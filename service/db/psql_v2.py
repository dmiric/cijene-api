import asyncpg
import pgvector.asyncpg
from typing import List, Any, Optional
from datetime import date # Added date import
from service.db.models import ProductSearchItemV2
from .repositories.golden_product_repo import GoldenProductRepository
from .repositories.chat_repo import ChatRepository # Added ChatRepository import
from .base import Database
from service.utils.timing import timing_decorator # Import the decorator

from service.config import settings # Import settings
from contextlib import asynccontextmanager # Import asynccontextmanager

class PostgresDatabaseV2(Database):
    """
    A Facade for all V2 ('g_') table interactions.
    It composes the GoldenProductRepository to handle all data access.
    """
    def __init__(self):
        self.dsn = settings.db_dsn
        self.pool = None
        # Instantiate repositories WITHOUT connection details
        self.golden_products = GoldenProductRepository()
        self.chat_repo = ChatRepository() # Initialize ChatRepository

    async def connect(self):
        if not self.pool:
            self.pool = await asyncpg.create_pool(
                dsn=self.dsn,
                min_size=settings.db_min_connections,
                max_size=settings.db_max_connections,
                # This is the critical part for pgvector
                init=pgvector.asyncpg.register_vector
            )
            # Connect all repositories to the SHARED pool
            await self.golden_products.connect(self.pool)
            await self.chat_repo.connect(self.pool) # Connect chat_repo

    async def disconnect(self):
        if self.pool:
            await self.pool.close()
            self.pool = None

    async def close(self) -> None:
        """Close all database connections."""
        await self.disconnect()

    async def create_tables(self) -> None:
        """
        Create all necessary tables and indices if they don't exist.
        For V2 tables, this is typically handled by migrations, so this method can pass.
        """
        pass

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

    # --- Pass-through V2 methods ---

    @timing_decorator
    async def get_g_products_hybrid_search(
        self,
        query: str,
        limit: int = 20,
        offset: int = 0,
        sort_by: Optional[str] = None,
        category: Optional[str] = None,
        brand: Optional[str] = None,
        fields: Optional[List[str]] = None, # Add fields parameter
    ) -> list[ProductSearchItemV2]:
        return await self.golden_products.get_g_products_hybrid_search(query, limit, offset, sort_by, category, brand, fields) # Pass fields

    @timing_decorator
    async def get_g_stores_nearby(
        self,
        lat: float,
        lon: float,
        radius_meters: int,
        chain_code: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        return await self.golden_products.get_g_stores_nearby(lat, lon, radius_meters, chain_code)

    @timing_decorator
    async def get_g_product_prices_by_location(
        self, product_id: int, store_ids: list[int]
    ) -> list[dict[str, Any]]:
        return await self.golden_products.get_g_product_prices_by_location(product_id, store_ids)

    @timing_decorator
    async def get_g_product_details(self, product_id: int) -> dict[str, Any] | None:
        return await self.golden_products.get_g_product_details(product_id)

    @timing_decorator
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
        return await self.chat_repo.save_chat_message(
            user_id, session_id, message_text, is_user_message, tool_calls, tool_outputs, ai_response
        )
