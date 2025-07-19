from datetime import date, datetime
from typing import List, Optional, Any

from asyncpg import Connection, Record, Pool

from service.db.models import ImportRun, ImportStatus, CrawlStatus


class ImportRunRepository:
    def __init__(self):
        self.pool = None

    async def connect(self, pool: Pool) -> None:
        self.pool = pool

    async def add_import_run(
        self,
        chain_name: str,
        import_date: date,
        crawl_run_id: Optional[int] = None,
        unzipped_path: Optional[str] = None,
    ) -> int:
        async with self.pool.acquire() as conn:
            query = """
                INSERT INTO import_runs (chain_name, import_date, crawl_run_id, unzipped_path, status)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (chain_name, import_date) DO UPDATE SET
                    crawl_run_id = EXCLUDED.crawl_run_id,
                    unzipped_path = EXCLUDED.unzipped_path,
                    status = EXCLUDED.status,
                    error_message = NULL,
                    n_stores = 0,
                    n_products = 0,
                    n_prices = 0,
                    elapsed_time = 0.0,
                    timestamp = CURRENT_TIMESTAMP
                RETURNING id
            """
            import_run_id = await conn.fetchval(
                query, chain_name, import_date, crawl_run_id, unzipped_path, ImportStatus.STARTED.value
            )
            return import_run_id

    async def update_import_run_status(
        self,
        import_run_id: int,
        status: ImportStatus,
        error_message: Optional[str] = None,
        n_stores: Optional[int] = None,
        n_products: Optional[int] = None,
        n_prices: Optional[int] = None,
        elapsed_time: Optional[float] = None,
    ) -> None:
        async with self.pool.acquire() as conn:
            updates = []
            args = []
            if status is not None:
                updates.append("status = $1")
                args.append(status.value)
            if error_message is not None:
                updates.append(f"error_message = ${len(args) + 1}")
                args.append(error_message)
            if n_stores is not None:
                updates.append(f"n_stores = ${len(args) + 1}")
                args.append(n_stores)
            if n_products is not None:
                updates.append(f"n_products = ${len(args) + 1}")
                args.append(n_products)
            if n_prices is not None:
                updates.append(f"n_prices = ${len(args) + 1}")
                args.append(n_prices)
            if elapsed_time is not None:
                updates.append(f"elapsed_time = ${len(args) + 1}")
                args.append(elapsed_time)

            if not updates:
                return

            query = f"""
                UPDATE import_runs
                SET {', '.join(updates)}
                WHERE id = ${len(args) + 1}
            """
            args.append(import_run_id)
            await conn.execute(query, *args)

    async def get_import_run_by_crawl_run_id(
        self, crawl_run_id: int
    ) -> Optional[ImportRun]:
        async with self.pool.acquire() as conn:
            query = """
                SELECT id, crawl_run_id, chain_name, import_date, status, error_message,
                       n_stores, n_products, n_prices, elapsed_time, timestamp, unzipped_path
                FROM import_runs
                WHERE crawl_run_id = $1
            """
            record: Optional[Record] = await conn.fetchrow(query, crawl_run_id)
            if record:
                return ImportRun(
                    id=record["id"],
                    crawl_run_id=record["crawl_run_id"],
                    chain_name=record["chain_name"],
                    import_date=record["import_date"],
                    status=ImportStatus(record["status"]),
                    error_message=record["error_message"],
                    n_stores=record["n_stores"],
                    n_products=record["n_products"],
                    n_prices=record["n_prices"],
                    elapsed_time=record["elapsed_time"],
                    timestamp=record["timestamp"],
                    unzipped_path=record["unzipped_path"],
                )
            return None

    async def get_latest_successful_import_run_for_chain(
        self, chain_name: str
    ) -> Optional[ImportRun]:
        async with self.pool.acquire() as conn:
            query = """
                SELECT id, crawl_run_id, chain_name, import_date, status, error_message,
                       n_stores, n_products, n_prices, elapsed_time, timestamp, unzipped_path
                FROM import_runs
                WHERE chain_name = $1 AND status = $2
                ORDER BY import_date DESC, timestamp DESC
                LIMIT 1
            """
            record: Optional[Record] = await conn.fetchrow(
                query, chain_name, ImportStatus.SUCCESS.value
            )
            if record:
                return ImportRun(
                    id=record["id"],
                    crawl_run_id=record["crawl_run_id"],
                    chain_name=record["chain_name"],
                    import_date=record["import_date"],
                    status=ImportStatus(record["status"]),
                    error_message=record["error_message"],
                    n_stores=record["n_stores"],
                    n_products=record["n_products"],
                    n_prices=record["n_prices"],
                    elapsed_time=record["elapsed_time"],
                    timestamp=record["timestamp"],
                    unzipped_path=record["unzipped_path"],
                )
            return None

    async def get_import_run_by_chain_and_date(
        self, chain_name: str, import_date: date
    ) -> Optional[ImportRun]:
        async with self.pool.acquire() as conn:
            query = """
                SELECT id, crawl_run_id, chain_name, import_date, status, error_message,
                       n_stores, n_products, n_prices, elapsed_time, timestamp, unzipped_path
                FROM import_runs
                WHERE chain_name = $1 AND import_date = $2
            """
            record: Optional[Record] = await conn.fetchrow(query, chain_name, import_date)
            if record:
                return ImportRun(
                    id=record["id"],
                    crawl_run_id=record["crawl_run_id"],
                    chain_name=record["chain_name"],
                    import_date=record["import_date"],
                    status=ImportStatus(record["status"]),
                    error_message=record["error_message"],
                    n_stores=record["n_stores"],
                    n_products=record["n_products"],
                    n_prices=record["n_prices"],
                    elapsed_time=record["elapsed_time"],
                    timestamp=record["timestamp"],
                    unzipped_path=record["unzipped_path"],
                )
            return None

    async def get_successful_crawl_runs_not_imported(self) -> List[Record]:
        async with self.pool.acquire() as conn:
            query = """
                SELECT cr.id, cr.chain_name, cr.crawl_date
                FROM crawl_runs cr
                LEFT JOIN import_runs ir ON cr.id = ir.crawl_run_id
                WHERE cr.status = $1 AND ir.id IS NULL
                ORDER BY cr.crawl_date ASC, cr.timestamp ASC
            """
            records: List[Record] = await conn.fetch(
                query, CrawlStatus.SUCCESS.value
            )
            return records

    async def get_failed_or_started_runs(self, import_date: date) -> List[ImportRun]:
        async with self.pool.acquire() as conn:
            query = """
                SELECT id, crawl_run_id, chain_name, import_date, status, error_message,
                       n_stores, n_products, n_prices, elapsed_time, timestamp, unzipped_path
                FROM import_runs
                WHERE import_date = $1 AND (status = $2 OR status = $3)
                ORDER BY chain_name ASC
            """
            records: List[Record] = await conn.fetch(
                query, import_date, ImportStatus.FAILED.value, ImportStatus.STARTED.value
            )
            return [
                ImportRun(
                    id=record["id"],
                    crawl_run_id=record["crawl_run_id"],
                    chain_name=record["chain_name"],
                    import_date=record["import_date"],
                    status=ImportStatus(record["status"]),
                    error_message=record["error_message"],
                    n_stores=record["n_stores"],
                    n_products=record["n_products"],
                    n_prices=record["n_prices"],
                    elapsed_time=record["elapsed_time"],
                    timestamp=record["timestamp"],
                    unzipped_path=record["unzipped_path"],
                )
                for record in records
            ]

    async def get_successful_runs(self, import_date: date) -> List[ImportRun]:
        async with self.pool.acquire() as conn:
            query = """
                SELECT id, crawl_run_id, chain_name, import_date, status, error_message,
                       n_stores, n_products, n_prices, elapsed_time, timestamp, unzipped_path
                FROM import_runs
                WHERE import_date = $1 AND status = $2
                ORDER BY chain_name ASC
            """
            records: List[Record] = await conn.fetch(
                query, import_date, ImportStatus.SUCCESS.value
            )
            return [
                ImportRun(
                    id=record["id"],
                    crawl_run_id=record["crawl_run_id"],
                    chain_name=record["chain_name"],
                    import_date=record["import_date"],
                    status=ImportStatus(record["status"]),
                    error_message=record["error_message"],
                    n_stores=record["n_stores"],
                    n_products=record["n_products"],
                    n_prices=record["n_prices"],
                    elapsed_time=record["elapsed_time"],
                    timestamp=record["timestamp"],
                    unzipped_path=record["unzipped_path"],
                )
                for record in records
            ]
