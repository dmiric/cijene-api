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
    ChainStats,
)


class StatsRepository(BaseRepository): # Changed inheritance
    """
    Contains all logic for interacting with stats-related tables
    (chain_stats).
    """

    def __init__(self, dsn: str, min_size: int = 10, max_size: int = 30):
        self.dsn = dsn
        self.min_size = min_size
        self.max_size = max_size
        self.pool = None
        def debug_print_db(*args, **kwargs):
            print("[DEBUG stats_repo]", *args, file=sys.stderr, **kwargs)
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

    async def list_latest_chain_stats(self) -> list[ChainStats]:
        async with self._get_conn() as conn:
            rows = await conn.fetch("""
                SELECT
                    c.code AS chain_code,
                    cs.price_date,
                    cs.price_count,
                    cs.store_count,
                    cs.created_at
                FROM chains c
                JOIN LATERAL (
                    SELECT *
                    FROM chain_stats
                    WHERE chain_id = c.id
                    ORDER BY price_date DESC
                    LIMIT 1
                ) cs ON true;
            """)
            return [ChainStats(**row) for row in rows]  # type: ignore

    async def compute_chain_stats(self, date: date) -> None:
        async with self._atomic() as conn:
            stats = await conn.fetch(
                """
                SELECT
                    cp.chain_id,
                    COUNT(*) AS price_count,
                    COUNT(DISTINCT p.store_id) AS store_count
                FROM prices p
                JOIN chain_products cp ON cp.id = p.chain_product_id
                WHERE p.price_date = $1
                GROUP BY cp.chain_id
                """,
                date,
            )

            for record in stats:
                await conn.execute(
                    """
                    INSERT INTO chain_stats(chain_id, price_date, price_count, store_count)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (chain_id, price_date)
                    DO UPDATE SET
                        price_count = EXCLUDED.price_count,
                        store_count = EXCLUDED.store_count;
                    """,
                    record["chain_id"],
                    date,
                    record["price_count"],
                    record["store_count"],
                )
