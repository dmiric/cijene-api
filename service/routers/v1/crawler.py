from datetime import date
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from service.db.models import CrawlStatus
from service.db.repositories.crawl_run_repo import CrawlRunRepository
from service.db.base import get_db_session # Import get_db_session
from service.db.psql import PostgresDatabase # Import PostgresDatabase

from service.routers.auth import verify_authentication # Import verify_authentication
from service.routers.auth import RequireApiKey # Import RequireApiKey
router = APIRouter(dependencies=[RequireApiKey])

class CrawlStatusReport(BaseModel):
    chain_name: str
    crawl_date: date
    status: CrawlStatus
    error_message: Optional[str] = None
    n_stores: int = 0
    n_products: int = 0
    n_prices: int = 0
    elapsed_time: float = 0.0

@router.post("/crawler/status", status_code=status.HTTP_201_CREATED)
async def report_crawler_status(
    report: CrawlStatusReport,
    db: PostgresDatabase = Depends(get_db_session), # Use PostgresDatabase dependency
):
    repo = CrawlRunRepository(db.pool) # Pass the pool to the repository
    
    # Check if a run for this chain and date already exists
    existing_run = await repo.get_latest_crawl_run(report.chain_name, report.crawl_date)

    if existing_run:
        # Update existing run
        updated_run = await repo.update_crawl_run_status(
            crawl_run_id=existing_run.id,
            status=report.status,
            error_message=report.error_message,
            n_stores=report.n_stores,
            n_products=report.n_products,
            n_prices=report.n_prices,
            elapsed_time=report.elapsed_time,
        )
        if not updated_run:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update crawl run status")
        return {"message": "Crawl status updated successfully", "crawl_run_id": updated_run.id}
    else:
        # Add new run
        new_run = await repo.add_crawl_run(
            chain_name=report.chain_name,
            crawl_date=report.crawl_date,
            status=report.status,
            error_message=report.error_message,
            n_stores=report.n_stores,
            n_products=report.n_products,
            n_prices=report.n_prices,
            elapsed_time=report.elapsed_time,
        )
        return {"message": "Crawl status reported successfully", "crawl_run_id": new_run.id}

@router.get("/crawler/status/{chain_name}/{crawl_date}", response_model=CrawlStatusReport)
async def get_crawler_status(
    chain_name: str,
    crawl_date: date,
    db: PostgresDatabase = Depends(get_db_session), # Use PostgresDatabase dependency
):
    repo = CrawlRunRepository(db.pool) # Pass the pool to the repository
    crawl_run = await repo.get_latest_crawl_run(chain_name, crawl_date)
    if not crawl_run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl run not found")
    
    return CrawlStatusReport(
        chain_name=crawl_run.chain_name,
        crawl_date=crawl_run.crawl_date,
        status=crawl_run.status,
        error_message=crawl_run.error_message,
        n_stores=crawl_run.n_stores,
        n_products=crawl_run.n_products,
        n_prices=crawl_run.n_prices,
        elapsed_time=crawl_run.elapsed_time,
    )

@router.get("/crawler/failed_or_started_runs/{crawl_date}", response_model=List[CrawlStatusReport])
async def get_failed_or_started_runs(
    crawl_date: date,
    db: PostgresDatabase = Depends(get_db_session), # Use PostgresDatabase dependency
):
    repo = CrawlRunRepository(db.pool) # Pass the pool to the repository
    runs = await repo.get_failed_or_started_runs(crawl_date)
    return [
        CrawlStatusReport(
            chain_name=run.chain_name,
            crawl_date=run.crawl_date,
            status=run.status,
            error_message=run.error_message,
            n_stores=run.n_stores,
            n_products=run.n_products,
            n_prices=run.n_prices,
            elapsed_time=run.elapsed_time,
        ) for run in runs
    ]

@router.get("/crawler/successful_runs/{crawl_date}", response_model=List[CrawlStatusReport])
async def get_successful_runs(
    crawl_date: date,
    db: PostgresDatabase = Depends(get_db_session), # Use PostgresDatabase dependency
):
    repo = CrawlRunRepository(db.pool) # Pass the pool to the repository
    runs = await repo.get_successful_runs(crawl_date)
    return [
        CrawlStatusReport(
            chain_name=run.chain_name,
            crawl_date=run.crawl_date,
            status=run.status,
            error_message=run.error_message,
            n_stores=run.n_stores,
            n_products=run.n_products,
            n_prices=run.n_prices,
            elapsed_time=run.elapsed_time,
        ) for run in runs
    ]
