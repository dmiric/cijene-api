from typing import List, Any, Optional
from service.db.models import ProductSearchItemV2 # Updated import
from .repositories.golden_product_repo import GoldenProductRepository
from .base import Database

class PostgresDatabaseV2(Database):
    """
    A Facade for all V2 ('g_') table interactions.
    It composes the GoldenProductRepository to handle all data access.
    """
    def __init__(self, dsn: str, **kwargs):
        # This facade now only needs to know about the golden product repo.
        self.golden_products = GoldenProductRepository(dsn, **kwargs)

    async def connect(self):
        await self.golden_products.connect()

    async def close(self):
        await self.golden_products.close()

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

    async def get_g_products_hybrid_search(
        self,
        query: str,
        limit: int = 20,
        offset: int = 0,
        sort_by: Optional[str] = None,
        category: Optional[str] = None,
        brand: Optional[str] = None,
    ) -> list[ProductSearchItemV2]:
        return await self.golden_products.get_g_products_hybrid_search(query, limit, offset, sort_by, category, brand)

    async def get_g_stores_nearby(
        self,
        lat: float,
        lon: float,
        radius_meters: int,
        chain_code: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        return await self.golden_products.get_g_stores_nearby(lat, lon, radius_meters, chain_code)

    async def get_g_product_prices_by_location(
        self, product_id: int, store_ids: list[int]
    ) -> list[dict[str, Any]]:
        return await self.golden_products.get_g_product_prices_by_location(product_id, store_ids)

    async def get_g_product_details(self, product_id: int) -> dict[str, Any] | None:
        return await self.golden_products.get_g_product_details(product_id)
