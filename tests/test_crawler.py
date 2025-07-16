import pytest
import httpx
import asyncio
import time
import subprocess
import os
from datetime import date, datetime, timezone
from typing import List, Optional

import asyncpg

from service.db.models import CrawlStatus, CrawlRun
from service.db.psql import PostgresDatabase # Needed for get_db_session in main.py

# Base URL for the API, adjusted for v1 crawler endpoints
BASE_URL = "http://api:8000/v1"
HEALTH_URL = "http://api:8000/health"

# Database connection details from .env
DB_HOST = os.getenv("DB_HOST")
DB_PORT = int(os.getenv("DB_PORT"))
DB_USER = os.getenv("POSTGRES_USER")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD")
DB_NAME = os.getenv("POSTGRES_DB")

@pytest.fixture(scope="session", autouse=True)
def setup_api():
    print("\nEnsuring API is running before tests...")
    max_retries = 10
    retry_delay = 1 # seconds
    for i in range(max_retries):
        try:
            response = httpx.get(HEALTH_URL, timeout=1)
            if response.status_code == 200:
                print(f"API is healthy after {i+1} retries.")
                break
        except httpx.ConnectError as e:
            print(f"API not reachable via httpx, retrying in {retry_delay}s... ({i+1}/{max_retries}) - Error: {e}")
            try:
                curl_result = subprocess.run(
                    ["curl", "-v", HEALTH_URL],
                    capture_output=True, text=True, check=False, timeout=2
                )
                print("Curl stdout:", curl_result.stdout)
                print("Curl stderr:", curl_result.stderr)
            except Exception as curl_e:
                print(f"Curl command failed: {curl_e}")
            time.sleep(retry_delay)
    else:
        pytest.fail(f"API did not become healthy after {max_retries} retries.")
    pass

@pytest.fixture(scope="function")
async def db_connection():
    """Provides an asyncpg connection for database operations."""
    conn = await asyncpg.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME
    )
    yield conn
    await conn.close()

@pytest.fixture(scope="function")
async def cleanup_crawl_runs_fixture(db_connection: asyncpg.Connection):
    """
    Cleans up the crawl_runs table.
    """
    try:
        await db_connection.execute("TRUNCATE TABLE crawl_runs RESTART IDENTITY CASCADE;")
        print("\nCrawl runs table truncated successfully before test.")
    except Exception as e:
        print(f"Error during database cleanup: {e}")
    yield

# Helper functions for crawler API operations
async def report_crawl_status_helper(
    client: httpx.AsyncClient,
    chain_name: str,
    crawl_date: date,
    status: CrawlStatus,
    error_message: Optional[str] = None,
    n_stores: int = 0,
    n_products: int = 0,
    n_prices: int = 0,
    elapsed_time: float = 0.0,
):
    payload = {
        "chain_name": chain_name,
        "crawl_date": crawl_date.isoformat(),
        "status": status.value,
        "error_message": error_message,
        "n_stores": n_stores,
        "n_products": n_products,
        "n_prices": n_prices,
        "elapsed_time": elapsed_time,
    }
    response = await client.post("/crawler/status", json=payload)
    response.raise_for_status()
    return response.json()

async def get_successful_runs_helper(client: httpx.AsyncClient, crawl_date: date):
    response = await client.get(f"/crawler/successful_runs/{crawl_date.isoformat()}")
    response.raise_for_status()
    return response.json()

async def get_failed_or_started_runs_helper(client: httpx.AsyncClient, crawl_date: date):
    response = await client.get(f"/crawler/failed_or_started_runs/{crawl_date.isoformat()}")
    response.raise_for_status()
    return response.json()

async def get_crawl_status_by_chain_and_date_helper(client: httpx.AsyncClient, chain_name: str, crawl_date: date):
    response = await client.get(f"/crawler/status/{chain_name}/{crawl_date.isoformat()}")
    response.raise_for_status()
    return response.json()

@pytest.mark.asyncio
async def test_report_new_crawl_status(
    cleanup_crawl_runs_fixture,
    db_connection: asyncpg.Connection,
):
    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        test_date = date(2025, 7, 1)
        chain = "test_chain_new"
        
        # Report a new status
        response_data = await report_crawl_status_helper(
            client, chain, test_date, CrawlStatus.SUCCESS,
            n_stores=10, n_products=100, n_prices=500, elapsed_time=120.5
        )
        assert response_data["message"] == "Crawl status reported successfully"
        assert "crawl_run_id" in response_data

        # Verify in DB
        record = await db_connection.fetchrow(
            "SELECT * FROM crawl_runs WHERE id = $1", response_data["crawl_run_id"]
        )
        assert record is not None
        assert record["chain_name"] == chain
        assert record["crawl_date"] == test_date
        assert record["status"] == CrawlStatus.SUCCESS.value
        assert record["n_stores"] == 10
        assert record["n_products"] == 100
        assert record["n_prices"] == 500
        assert record["elapsed_time"] == 120.5

@pytest.mark.asyncio
async def test_update_existing_crawl_status(
    cleanup_crawl_runs_fixture,
    db_connection: asyncpg.Connection,
):
    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        test_date = date(2025, 7, 2)
        chain = "test_chain_update"

        # 1. Report initial status (STARTED)
        initial_response = await report_crawl_status_helper(
            client, chain, test_date, CrawlStatus.STARTED,
            n_stores=0, n_products=0, n_prices=0, elapsed_time=0.0
        )
        initial_id = initial_response["crawl_run_id"]

        # 2. Update status to FAILED
        updated_response = await report_crawl_status_helper(
            client, chain, test_date, CrawlStatus.FAILED,
            error_message="Crawl failed due to network error",
            n_stores=5, n_products=50, n_prices=200, elapsed_time=60.0
        )
        assert updated_response["message"] == "Crawl status updated successfully"
        assert updated_response["crawl_run_id"] == initial_id # Should update the same record

        # Verify in DB
        record = await db_connection.fetchrow(
            "SELECT * FROM crawl_runs WHERE id = $1", initial_id
        )
        assert record is not None
        assert record["chain_name"] == chain
        assert record["crawl_date"] == test_date
        assert record["status"] == CrawlStatus.FAILED.value
        assert record["error_message"] == "Crawl failed due to network error"
        assert record["n_stores"] == 5
        assert record["n_products"] == 50
        assert record["n_prices"] == 200
        assert record["elapsed_time"] == 60.0

@pytest.mark.asyncio
async def test_get_successful_runs(
    cleanup_crawl_runs_fixture,
    db_connection: asyncpg.Connection,
):
    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        test_date = date(2025, 7, 3)

        # Add a successful run
        await report_crawl_status_helper(client, "chain_s1", test_date, CrawlStatus.SUCCESS)
        # Add another successful run
        await report_crawl_status_helper(client, "chain_s2", test_date, CrawlStatus.SUCCESS)
        # Add a failed run (should not be returned)
        await report_crawl_status_helper(client, "chain_f1", test_date, CrawlStatus.FAILED)
        # Add a successful run for a different date (should not be returned)
        await report_crawl_status_helper(client, "chain_s3", date(2025, 7, 4), CrawlStatus.SUCCESS)

        successful_runs = await get_successful_runs_helper(client, test_date)
        assert len(successful_runs) == 2
        assert {r["chain_name"] for r in successful_runs} == {"chain_s1", "chain_s2"}
        assert all(r["status"] == CrawlStatus.SUCCESS.value for r in successful_runs)

@pytest.mark.asyncio
async def test_get_failed_or_started_runs(
    cleanup_crawl_runs_fixture,
    db_connection: asyncpg.Connection,
):
    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        test_date = date(2025, 7, 4)

        # Add a failed run
        await report_crawl_status_helper(client, "chain_f1", test_date, CrawlStatus.FAILED)
        # Add a started run
        await report_crawl_status_helper(client, "chain_st1", test_date, CrawlStatus.STARTED)
        # Add a successful run (should not be returned)
        await report_crawl_status_helper(client, "chain_s1", test_date, CrawlStatus.SUCCESS)
        # Add a failed run for a different date (should not be returned)
        await report_crawl_status_helper(client, "chain_f2", date(2025, 7, 5), CrawlStatus.FAILED)

        failed_or_started_runs = await get_failed_or_started_runs_helper(client, test_date)
        assert len(failed_or_started_runs) == 2
        assert {r["chain_name"] for r in failed_or_started_runs} == {"chain_f1", "chain_st1"}
        assert all(r["status"] in [CrawlStatus.FAILED.value, CrawlStatus.STARTED.value] for r in failed_or_started_runs)

@pytest.mark.asyncio
async def test_get_crawl_status_not_found(
    cleanup_crawl_runs_fixture,
):
    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        test_date = date(2025, 7, 5)
        chain = "non_existent_chain"

        response = await client.get(f"/crawler/status/{chain}/{test_date.isoformat()}")
        assert response.status_code == 404
        assert response.json()["detail"] == "Crawl run not found"

@pytest.mark.asyncio
async def test_report_skipped_status(
    cleanup_crawl_runs_fixture,
    db_connection: asyncpg.Connection,
):
    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        test_date = date(2025, 7, 6)
        chain = "test_chain_skipped"
        
        # Report a skipped status
        response_data = await report_crawl_status_helper(
            client, chain, test_date, CrawlStatus.SKIPPED,
            error_message="Already successfully crawled."
        )
        assert response_data["message"] == "Crawl status reported successfully"
        assert "crawl_run_id" in response_data

        # Verify in DB
        record = await db_connection.fetchrow(
            "SELECT * FROM crawl_runs WHERE id = $1", response_data["crawl_run_id"]
        )
        assert record is not None
        assert record["chain_name"] == chain
        assert record["crawl_date"] == test_date
        assert record["status"] == CrawlStatus.SKIPPED.value
        assert record["error_message"] == "Already successfully crawled."
