from datetime import date, datetime
from typing import Optional, List, Any
import asyncpg

from service.db.models import CrawlRun, CrawlStatus
from service.db.base import BaseRepository # Import BaseRepository from service.db.base


class CrawlRunRepository(BaseRepository): # Inherit from BaseRepository
    def __init__(self, pool: asyncpg.Pool): # Accept asyncpg.Pool
        super().__init__()
        self.pool = pool # Store the pool

    async def add_crawl_run(
        self,
        chain_name: str,
        crawl_date: date,
        status: CrawlStatus = CrawlStatus.STARTED,
        error_message: Optional[str] = None,
        n_stores: int = 0,
        n_products: int = 0,
        n_prices: int = 0,
        elapsed_time: float = 0.0,
    ) -> CrawlRun:
        query = """
            INSERT INTO crawl_runs (
                chain_name, crawl_date, status, error_message,
                n_stores, n_products, n_prices, elapsed_time, timestamp
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, NOW()
            ) RETURNING id, chain_name, crawl_date, status, error_message,
                        n_stores, n_products, n_prices, elapsed_time, timestamp;
        """
        record = await self._fetchrow(
            query,
            chain_name,
            crawl_date,
            status.value, # Store enum value
            error_message,
            n_stores,
            n_products,
            n_prices,
            elapsed_time,
        )
        if record:
            return CrawlRun(
                id=record["id"],
                chain_name=record["chain_name"],
                crawl_date=record["crawl_date"],
                status=CrawlStatus(record["status"]), # Convert back to enum
                error_message=record["error_message"],
                n_stores=record["n_stores"],
                n_products=record["n_products"],
                n_prices=record["n_prices"],
                elapsed_time=record["elapsed_time"],
                timestamp=record["timestamp"],
            )
        raise RuntimeError("Failed to add crawl run")

    async def get_latest_crawl_run(
        self, chain_name: str, crawl_date: date
    ) -> Optional[CrawlRun]:
        query = """
            SELECT id, chain_name, crawl_date, status, error_message,
                   n_stores, n_products, n_prices, elapsed_time, timestamp
            FROM crawl_runs
            WHERE chain_name = $1 AND crawl_date = $2
            ORDER BY timestamp DESC
            LIMIT 1;
        """
        record = await self._fetchrow(query, chain_name, crawl_date)
        if record:
            return CrawlRun(
                id=record["id"],
                chain_name=record["chain_name"],
                crawl_date=record["crawl_date"],
                status=CrawlStatus(record["status"]),
                error_message=record["error_message"],
                n_stores=record["n_stores"],
                n_products=record["n_products"],
                n_prices=record["n_prices"],
                elapsed_time=record["elapsed_time"],
                timestamp=record["timestamp"],
            )
        return None

    async def update_crawl_run_status(
        self,
        crawl_run_id: int,
        status: CrawlStatus,
        error_message: Optional[str] = None,
        n_stores: int = 0,
        n_products: int = 0,
        n_prices: int = 0,
        elapsed_time: float = 0.0,
    ) -> Optional[CrawlRun]:
        query = """
            UPDATE crawl_runs
            SET status = $2,
                error_message = $3,
                n_stores = $4,
                n_products = $5,
                n_prices = $6,
                elapsed_time = $7,
                timestamp = NOW()
            WHERE id = $1
            RETURNING id, chain_name, crawl_date, status, error_message,
                      n_stores, n_products, n_prices, elapsed_time, timestamp;
        """
        record = await self._fetchrow(
            query,
            crawl_run_id,
            status.value,
            error_message,
            n_stores,
            n_products,
            n_prices,
            elapsed_time,
        )
        if record:
            return CrawlRun(
                id=record["id"],
                chain_name=record["chain_name"],
                crawl_date=record["crawl_date"],
                status=CrawlStatus(record["status"]),
                error_message=record["error_message"],
                n_stores=record["n_stores"],
                n_products=record["n_products"],
                n_prices=record["n_prices"],
                elapsed_time=record["elapsed_time"],
                timestamp=record["timestamp"],
            )
        return None

    async def get_failed_or_started_runs(self, crawl_date: date) -> List[CrawlRun]:
        query = """
            SELECT id, chain_name, crawl_date, status, error_message,
                   n_stores, n_products, n_prices, elapsed_time, timestamp
            FROM crawl_runs
            WHERE crawl_date = $1 AND (status = $2 OR status = $3);
        """
        records = await self._fetch(
            query, crawl_date, CrawlStatus.FAILED.value, CrawlStatus.STARTED.value
        )
        return [
            CrawlRun(
                id=record["id"],
                chain_name=record["chain_name"],
                crawl_date=record["crawl_date"],
                status=CrawlStatus(record["status"]),
                error_message=record["error_message"],
                n_stores=record["n_stores"],
                n_products=record["n_products"],
                n_prices=record["n_prices"],
                elapsed_time=record["elapsed_time"],
                timestamp=record["timestamp"],
            )
            for record in records
        ]

    async def get_successful_runs(self, crawl_date: date) -> List[CrawlRun]:
        query = """
            SELECT id, chain_name, crawl_date, status, error_message,
                   n_stores, n_products, n_prices, elapsed_time, timestamp
            FROM crawl_runs
            WHERE crawl_date = $1 AND status = $2;
        """
        records = await self._fetch(query, crawl_date, CrawlStatus.SUCCESS.value)
        return [
            CrawlRun(
                id=record["id"],
                chain_name=record["chain_name"],
                crawl_date=record["crawl_date"],
                status=CrawlStatus(record["status"]),
                error_message=record["error_message"],
                n_stores=record["n_stores"],
                n_products=record["n_products"],
                n_prices=record["n_prices"],
                elapsed_time=record["elapsed_time"],
                timestamp=record["timestamp"],
            )
            for record in records
        ]

    async def get_all_runs_for_date(self, crawl_date: date) -> List[CrawlRun]:
        query = """
            SELECT id, chain_name, crawl_date, status, error_message,
                   n_stores, n_products, n_prices, elapsed_time, timestamp
            FROM crawl_runs
            WHERE crawl_date = $1;
        """
        records = await self._fetch(query, crawl_date)
        return [
            CrawlRun(
                id=record["id"],
                chain_name=record["chain_name"],
                crawl_date=record["crawl_date"],
                status=CrawlStatus(record["status"]),
                error_message=record["error_message"],
                n_stores=record["n_stores"],
                n_products=record["n_products"],
                n_prices=record["n_prices"],
                elapsed_time=record["elapsed_time"],
                timestamp=record["timestamp"],
            )
            for record in records
        ]
