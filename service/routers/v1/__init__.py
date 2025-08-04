# This makes the 'v1' directory a Python package.

from .stores import router as stores_router
# from .users import router as users_router # Removed as v1 users is deprecated

# Create a main router for v1 that includes all sub-routers
from fastapi import APIRouter

router = APIRouter()
router.include_router(stores_router)
# router.include_router(users_router) # Removed as v1 users is deprecated
