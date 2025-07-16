from fastapi import APIRouter, Depends
from service.routers.auth import RequireAuth

# Import the new routers
from .v1 import stores
from .v1 import crawler # Import the new crawler router

router = APIRouter(tags=["API v1"], dependencies=[Depends(RequireAuth)])

# Include the routers from the new files
router.include_router(stores.router)
router.include_router(crawler.router) # Include the new crawler router
