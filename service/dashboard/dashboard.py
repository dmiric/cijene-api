from datetime import date
from typing import List, Dict, Any

from service.db.repositories.import_run_repo import ImportRunRepository
from service.db.repositories.store_repo import StoreRepository
from service.db.psql import PostgresDatabase

class DashboardService:
    def __init__(self, db: PostgresDatabase):
        self.import_run_repo = ImportRunRepository(db)
        self.store_repo = StoreRepository(db)

    async def get_today_import_status(self) -> List[Dict[str, Any]]:
        today = date.today()
        stores = await self.store_repo.get_all_stores()
        
        dashboard_data = []
        for store in stores:
            last_import_run = await self.import_run_repo.get_last_import_run_for_store_on_date(
                store_id=store["id"],
                import_date=today
            )
            
            status = "No import today"
            if last_import_run:
                status = last_import_run["status"]
            
            dashboard_data.append({
                "store_name": store["name"],
                "last_import_status": status,
                "import_date": today.isoformat()
            })
        return dashboard_data
