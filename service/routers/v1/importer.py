from datetime import date
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from service.db.models import ImportStatus
from service.db.repositories.import_run_repo import ImportRunRepository
from service.db.base import get_db_session
from service.db.psql import PostgresDatabase

from service.routers.auth import RequireApiKey

router = APIRouter(dependencies=[RequireApiKey])

class ImportStatusReport(BaseModel):
    chain_name: str
    import_date: date
    status: ImportStatus
    error_message: Optional[str] = None
    n_stores: int = 0
    n_products: int = 0
    n_prices: int = 0
    elapsed_time: float = 0.0
    crawl_run_id: Optional[int] = None
    unzipped_path: Optional[str] = None

@router.post("/importer/status", status_code=status.HTTP_201_CREATED)
async def report_importer_status(
    report: ImportStatusReport,
    db: PostgresDatabase = Depends(get_db_session),
):
    repo = ImportRunRepository(db.pool)
    
    existing_run = await repo.get_import_run_by_chain_and_date(report.chain_name, report.import_date)

    if existing_run:
        await repo.update_import_run_status(
            import_run_id=existing_run.id,
            status=report.status,
            error_message=report.error_message,
            n_stores=report.n_stores,
            n_products=report.n_products,
            n_prices=report.n_prices,
            elapsed_time=report.elapsed_time,
        )
        return {"message": "Import status updated successfully", "import_run_id": existing_run.id}
    else:
        new_run_id = await repo.add_import_run(
            chain_name=report.chain_name,
            import_date=report.import_date,
            crawl_run_id=report.crawl_run_id,
            unzipped_path=report.unzipped_path,
        )
        await repo.update_import_run_status(
            import_run_id=new_run_id,
            status=report.status,
            error_message=report.error_message,
            n_stores=report.n_stores,
            n_products=report.n_products,
            n_prices=report.n_prices,
            elapsed_time=report.elapsed_time,
        )
        return {"message": "Import status reported successfully", "import_run_id": new_run_id}

@router.get("/importer/status/{chain_name}/{import_date}", response_model=ImportStatusReport)
async def get_importer_status(
    chain_name: str,
    import_date: date,
    db: PostgresDatabase = Depends(get_db_session),
):
    repo = ImportRunRepository(db.pool)
    import_run = await repo.get_import_run_by_chain_and_date(chain_name, import_date)
    if not import_run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import run not found")
    
    return ImportStatusReport(
        chain_name=import_run.chain_name,
        import_date=import_run.import_date,
        status=import_run.status,
        error_message=import_run.error_message,
        n_stores=import_run.n_stores,
        n_products=import_run.n_products,
        n_prices=import_run.n_prices,
        elapsed_time=import_run.elapsed_time,
        crawl_run_id=import_run.crawl_run_id,
        unzipped_path=import_run.unzipped_path,
    )

@router.get("/importer/failed_or_started_runs/{import_date}", response_model=List[ImportStatusReport])
async def get_failed_or_started_imports(
    import_date: date,
    db: PostgresDatabase = Depends(get_db_session),
):
    repo = ImportRunRepository(db.pool)
    runs = await repo.get_failed_or_started_runs(import_date)
    return [
        ImportStatusReport(
            chain_name=run.chain_name,
            import_date=run.import_date,
            status=run.status,
            error_message=run.error_message,
            n_stores=run.n_stores,
            n_products=run.n_products,
            n_prices=run.n_prices,
            elapsed_time=run.elapsed_time,
            crawl_run_id=run.crawl_run_id,
            unzipped_path=run.unzipped_path,
        ) for run in runs
    ]

@router.get("/importer/successful_runs/{import_date}", response_model=List[ImportStatusReport])
async def get_successful_imports(
    import_date: date,
    db: PostgresDatabase = Depends(get_db_session),
):
    repo = ImportRunRepository(db.pool)
    runs = await repo.get_successful_runs(import_date)
    return [
        ImportStatusReport(
            chain_name=run.chain_name,
            import_date=run.import_date,
            status=run.status,
            error_message=run.error_message,
            n_stores=run.n_stores,
            n_products=run.n_products,
            n_prices=run.n_prices,
            elapsed_time=run.elapsed_time,
            crawl_run_id=run.crawl_run_id,
            unzipped_path=run.unzipped_path,
        ) for run in runs
    ]
