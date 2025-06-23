from fastapi import APIRouter, Depends
from service.routers.auth import RequireAuth

# Import the new routers
from .v1 import chat
from .v1 import products
from .v1 import stores
from .v1 import users

router = APIRouter(tags=["API v1"], dependencies=[Depends(RequireAuth)])

# Include the routers from the new files
router.include_router(chat.router)
router.include_router(products.router)
router.include_router(stores.router)
router.include_router(users.router)
