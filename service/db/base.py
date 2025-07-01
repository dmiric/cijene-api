from abc import ABC, abstractmethod
from datetime import date
from typing import Any, AsyncGenerator, AsyncIterator, Optional, List

import asyncpg
from contextlib import asynccontextmanager
import asyncio # Import asyncio


class BaseRepository(ABC):
    """Base abstract class for all repositories, providing common connection management."""

    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self, pool: asyncpg.Pool) -> None:
        """Initialize the database connection with a shared pool."""
        self.pool = pool

    @asynccontextmanager
    async def _get_conn(self) -> AsyncGenerator[asyncpg.Connection, None]:
        """Context manager to acquire a connection from the pool."""
        if not self.pool:
            raise RuntimeError("Database pool is not initialized for this repository")
        async with self.pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def _atomic(self) -> AsyncIterator[asyncpg.Connection]:
        """Context manager for atomic transactions."""
        async with self._get_conn() as conn:
            async with conn.transaction():
                yield conn

    async def _fetchval(self, query: str, *args: Any) -> Any:
        """Fetch a single value from the database."""
        async with self._get_conn() as conn:
            return await conn.fetchval(query, *args)

    async def _fetchrow(self, query: str, *args: Any) -> Optional[asyncpg.Record]:
        """Fetch a single row from the database."""
        async with self._get_conn() as conn:
            return await conn.fetchrow(query, *args)

    async def _fetch(self, query: str, *args: Any) -> List[asyncpg.Record]:
        """Fetch multiple rows from the database."""
        async with self._get_conn() as conn:
            return await conn.fetch(query, *args)

    async def _execute(self, query: str, *args: Any) -> str:
        """Execute a query and return the status."""
        async with self._get_conn() as conn:
            return await conn.execute(query, *args)


class Database(ABC):
    """Base abstract class for database implementations."""

    @abstractmethod
    async def connect(self) -> None:
        """Initialize the database connection."""
        pass


class Database(ABC):
    """Base abstract class for database implementations."""

    @abstractmethod
    async def connect(self) -> None:
        """Initialize the database connection."""
        pass

    @abstractmethod
    async def create_tables(self) -> None:
        """Create all necessary tables and indices if they don't exist."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close all database connections."""
        pass

    @abstractmethod
    async def add_chain(self, chain: Any) -> int:
        """
        Add a new chain or get existing one and return its ID.

        Args:
            chain: Chain object containing code.

        Returns:
            The database ID of the created or existing chain.
        """
        pass

    @abstractmethod
    async def list_chains(self) -> list[Any]:
        """
        List all chains in the database.

        Returns:
            A list of Chain objects representing all chains.
        """
        pass

    @abstractmethod
    async def list_latest_chain_stats(self) -> list[Any]:
        """
        Returns the latest available chain stats for each chain.

        Returns:
            A list of ChainStats objects.
        """
        pass

    @abstractmethod
    async def add_store(self, store: Any) -> int:
        """
        Add a new store or update existing one and return its ID.

        Args:
            store: Store object containing chain_id, code, type,
                address, city, and zipcode.

        Returns:
            The database ID of the created or updated store.
        """
        pass

    @abstractmethod
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
        """
        Update store information by chain_id and store code.

        Args:
            chain_id: The ID of the chain.
            store_code: The code of the store.
            address: New address (optional).
            city: New city (optional).
            zipcode: New zipcode (optional).
            lat: New latitude (optional).
            lon: New longitude (optional).
            phone: New phone (optional).

        Returns:
            True if the store was updated, False if not found.
        """
        pass

    @abstractmethod
    async def list_stores(self, chain_code: str) -> list[Any]:
        """
        List all stores for a particular chain.

        Args:
            chain_code: The code of the chain to list stores for.

        Returns:
            A list of Store objects representing chain stores.
        """
        pass

    @abstractmethod
    async def filter_stores(
        self,
        chain_codes: list[str] | None = None,
        city: str | None = None,
        address: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
        d: float = 10.0,
    ) -> list[Any]:
        """
        Filter stores by chain codes, city, address, and/or geolocation.

        Args:
            chain_codes: List of chain codes to filter by (optional).
            city: City name for case-insensitive substring match (optional).
            address: Address for case-insensitive substring match (optional).
            lat: Latitude coordinate for geolocation search (optional).
            lon: Longitude coordinate for geolocation search (optional).
            d: Distance in kilometers for geolocation search (default: 10.0).

        Returns:
            A list of StoreWithId objects matching the filters.

        Raises:
            ValueError: If only one of lat/lon is provided.
        """
        pass

    @abstractmethod
    async def get_product_barcodes(self) -> dict[str, int]:
        """
        Get all product barcodes (EANs).

        Returns:
            A dictionary mapping EANs to product IDs.
        """
        pass

    @abstractmethod
    async def get_chain_product_map(self, chain_id: int) -> dict[str, int]:
        """
        Get a mapping from chain product codes to database IDs.

        Args:
            chain_id: The ID of the chain to fetch products for.

        Returns:
            A dictionary mapping product codes to product IDs in the database.
        """
        pass

    @abstractmethod
    async def add_ean(self, ean: str) -> int:
        """
        Add empty product with only EAN.

        Args:
            ean: The EAN code to add.

        Returns:
            The ID of the created product.
        """
        pass

    @abstractmethod
    async def get_products_by_ean(self, ean: list[Any]) -> list[Any]:
        """
        Get products by their EAN codes.

        Args:
            ean: The EAN codes to search for.

        Returns:
            A list of Product objects matching the EAN codes.
        """
        pass

    @abstractmethod
    async def update_product(self, product: Any) -> bool:
        """
        Update product information by EAN code.

        Args:
            product: Product object containing the EAN and fields to update.
                    Only non-None fields will be updated in the database.

        Returns:
            True if the product was updated, False if not found.
        """
        pass

    @abstractmethod
    async def get_chain_products_for_product(
        self,
        product_ids: list[int],
        chain_ids: list[int] | None = None,
    ) -> list[Any]:
        """
        Get all chain products for specified product IDs.

        Args:
            product_ids: The IDs of the products to search for.
            chain_ids: Optional list of chain IDs to filter by.

        Returns:
            A list of ChainProduct objects associated with the products.
        """
        pass

    @abstractmethod
    async def search_products(self, query: str) -> list[Any]:
        """
        Search for products by name using full text search.

        Args:
            query: The search query string.

        Returns:
            A list of products matching the search query,
            ordered by relevance.
        """
        pass

    @abstractmethod
    async def add_many_prices(self, prices: list[Any]) -> int:
        """
        Add multiple prices in a batch operation.

        Prices that already exist will be skipped without update.

        Args:
            prices: List of Price objects to add.

        Returns:
            The number of prices newly added.
        """
        pass

    @abstractmethod
    async def add_many_chain_products(
        self,
        chain_products: list[Any],
    ) -> int:
        """
        Add multiple chain products in a batch operation.

        Chain products that already exist will be skipped without
        update.

        Args:
            chain_products: List of ChainProduct objects to add.

        Returns:
            The number of chain products newly added.
        """
        pass

    @abstractmethod
    async def compute_chain_prices(self, date: date) -> None:
        """
        Compute chain prices for a specific date.

        This method computes min/avg/max prices for all products in all stores
        for a given chain and date, and stores them in a separate table.

        Args:
            date: The date for which to compute prices.
        """
        pass

    @abstractmethod
    async def compute_chain_stats(self, date: date) -> None:
        """
        Compute chain statistics and populate chain_stats for a given date.

        Args:
            date: The date for which to compute stats.
        """
        pass

    @abstractmethod
    async def get_product_prices(
        self,
        product_ids: list[int],
        date: date,
    ) -> list[dict[str, Any]]:
        """
        Get computed chain prices across all chains for specified products
        on a given date. If there are no prices for a product on that date,
        return the latest available prices for that product.

        Shape of the returned dictionaries is:
        {
            "chain": str,
            "product_id": int,
            "min_price": Decimal,
            "max_price": Decimal,
            "avg_price": Decimal
        }

        Args:
            product_ids: The IDs of the products to search for.
            date: The date for which to fetch prices.

        Returns:
            Information for the specified products and date.
        """
        pass

    @abstractmethod
    async def get_product_store_prices(
        self,
        product_id: int,
        chain_ids: list[int] | None,
    ) -> list[Any]:
        """
        For a given product return latest available prices per store.

        Args:
            product_id: The ID of the product to fetch
            chain_ids: Optional list of chain IDs to filter by.

        Returns:
            A list of StorePrice objects
        """
        pass

    @abstractmethod
    async def get_user_by_api_key(self, api_key: str) -> Any | None:
        """
        Get active user by API key.

        Args:
            api_key: The API key to search for.

        Returns:
            User object if found and active, None otherwise.
        """
        pass

    @staticmethod
    def from_url(url: str, **kwargs: Any) -> "Database":
        """
        Get the database instance based on the configured settings.

        Returns:
            An instance of the Database subclass based on the DSN.

        Raises:
            ValueError: If the database type is not supported.
        """

        from service.db.psql import PostgresDatabase

        if url.startswith("postgresql"):
            return PostgresDatabase(
                dsn=url,
                **kwargs,
            )
        else:
            raise ValueError(f"Unsupported database: {url}")

# A container for the database instance, to be initialized during startup
class DatabaseContainer:
    def __init__(self):
        self.db: Optional["PostgresDatabase"] = None

database_container = DatabaseContainer()

async def get_db_session() -> "PostgresDatabase":
    """
    Dependency that provides the initialized database instance.
    Waits for the database to be initialized if not ready.
    """
    # Add a small loop to wait for the database to be initialized
    # This handles potential race conditions where dependencies are resolved
    # before the startup event fully completes.
    max_retries = 10
    retry_delay = 0.5 # seconds
    for i in range(max_retries):
        if database_container.db is not None:
            return database_container.db
        await asyncio.sleep(retry_delay)
    raise RuntimeError("Database not initialized after multiple retries")
