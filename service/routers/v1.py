from fastapi import APIRouter, Depends
from service.routers.auth import RequireAuth

# Import the new routers
from .v1 import stores

router = APIRouter(tags=["API v1"], dependencies=[Depends(RequireAuth)])

# Include the routers from the new files
router.include_router(stores.router)
