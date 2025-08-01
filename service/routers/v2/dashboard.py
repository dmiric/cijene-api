from fastapi import APIRouter, Depends
from service.db.psql import PostgresDatabase
from service.dashboard.dashboard import DashboardService
from service.config import get_db

router = APIRouter()

@router.get("/dashboard/import-status")
async def get_import_status(db: PostgresDatabase = Depends(get_db)):
    dashboard_service = DashboardService(db)
    return await dashboard_service.get_today_import_status()
