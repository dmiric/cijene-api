from contextlib import asynccontextmanager
import asyncpg
from typing import (
    List,
    Any,
    Optional,
)
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pgvector.asyncpg
from service.utils.timing import timing_decorator # Import the decorator

from .base import Database
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
from service.db.repositories.shopping_list_repo import ShoppingListRepository # New import
from service.db.repositories.shopping_list_item_repo import ShoppingListItemRepository # New import
from service.db.repositories.import_run_repo import ImportRunRepository # New import


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

        # Instantiate legacy repos
        self.products = ProductRepository()
        self.stores = StoreRepository()
        self.users = UserRepository()
        self.chat = ChatRepository()
        self.stats = StatsRepository()

        # Also instantiate the golden repo for the normalizer's bulk inserts
        self.golden_products = GoldenProductRepository()
        self.shopping_lists = ShoppingListRepository() # New instance
        self.shopping_list_items = ShoppingListItemRepository() # New instance
        self.import_runs = ImportRunRepository() # New instance, pass pool here


    async def connect(self) -> None:
        # Create the main pool here
        self.pool = await asyncpg.create_pool(
            dsn=self.dsn,
            min_size=self.min_size,
            max_size=self.max_size,
            init=self._init_connection,  # Keep init for pgvector registration
        )
        # Connect all repos and ensure they share the same connection pool
        await self.products.connect(self.pool)
        await self.stores.connect(self.pool)
        await self.users.connect(self.pool)
        await self.chat.connect(self.pool)
        await self.stats.connect(self.pool)
        await self.golden_products.connect(self.pool)
        await self.shopping_lists.connect(self.pool)  # Connect new repos
        await self.shopping_list_items.connect(self.pool)  # Connect new repos
        await self.import_runs.connect(self.pool) # Connect new repos

    async def _init_connection(self, conn):
        # Register the 'vector' type for asyncpg using pgvector's utility
        await pgvector.asyncpg.register_vector(conn)
        # Set a default statement timeout for all commands on this connection (e.g., 60 seconds)
        await conn.execute('SET statement_timeout = 60000')

    async def close(self) -> None:
        """Close all database connections."""
        if self.pool:
            await self.pool.close()

    async def create_tables(self) -> None:
        # This method should ideally be handled by migrations or a dedicated setup script
        # For now, it can remain as a placeholder or delegate to a specific repo if needed.
        pass

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
        store_ids: list[int] | None = None,
        fields: Optional[List[str]] = None, # Pass fields argument
    ) -> list[dict[str, Any]]:
        return await self.products.get_product_prices(product_ids, date, store_ids, fields)

    
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
    
    async def save_chat_message_from_object(self, message: ChatMessage) -> None:
        """
        Passes a ChatMessage Pydantic object directly to the ChatRepository for saving.
        """
        await self.chat.save_chat_message_from_object(message)

    
    async def get_stores_within_radius(
        self,
        lat: Decimal,
        lon: Decimal,
        radius_meters: int,
        chain_code: Optional[str] = None,
        fields: Optional[List[str]] = None, # Pass fields argument
    ) -> list[dict[str, Any]]:
        return await self.stores.get_stores_within_radius(lat, lon, radius_meters, chain_code, fields)

    async def find_nearby_stores(
        self,
        lat: Decimal,
        lon: Decimal,
        radius_meters: int,
        chain_code: Optional[str] = None,
        fields: Optional[List[str]] = None,
    ) -> list[dict[str, Any]]:
        """
        Finds stores within a specified radius of a geographic point.
        Delegates to the StoreRepository.
        """
        return await self.stores.get_stores_within_radius(
            lat=lat,
            lon=lon,
            radius_meters=radius_meters,
            chain_code=chain_code,
            fields=fields
        )

    # --- V2-specific methods (delegating to GoldenProductRepository) ---

    async def get_g_products_hybrid_search(
        self,
        query: str,
        limit: int = 3,
        offset: int = 0,
        sort_by: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        return await self.golden_products.get_g_products_hybrid_search(query, limit, offset, sort_by)

    async def get_g_products_by_ean(
        self,
        ean: str,
        store_ids: List[int],
        limit: int = 20,
        offset: int = 0,
        sort_by: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        return await self.golden_products.get_g_products_by_ean_with_prices(ean, store_ids, limit, offset, sort_by)
    
    async def get_g_products_hybrid_search_with_prices(
        self,
        query: str,
        store_ids: List[int],
        limit: int = 3,
        offset: int = 0,
        sort_by: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        return await self.golden_products.get_g_products_hybrid_search_with_prices(query, store_ids, limit, offset, sort_by)

    async def get_g_product_prices_by_location(
        self, product_id: int, store_ids: list[int]
    ) -> list[dict[str, Any]]:
        return await self.golden_products.get_g_product_prices_by_location(product_id, store_ids)

    async def get_g_product_details(self, product_id: int) -> dict[str, Any] | None:
        return await self.golden_products.get_g_product_details(product_id)
