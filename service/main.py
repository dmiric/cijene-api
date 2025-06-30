print(">>> Importing main.py")
from contextlib import asynccontextmanager
import logging
import sys # Import sys for stdout

from decimal import Decimal # Import Decimal
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from service.routers.v1 import router as v1_router
from service.routers.v2.products import router as v2_products_router
from service.routers.v2.stores import router as v2_stores_router
from service.routers.v2.chat import router as v2_chat_router
from service.routers.v2.users import router as v2_users_router # New import
from service.routers.v2.user_locations import router as v2_user_locations_router # New import
from service.routers.v2.ai_tools import router as v2_ai_tools_router # New import
from service.routers.v2.shopping_lists import router as v2_shopping_lists_router # New import
from service.routers.v2.shopping_list_items import router as v2_shopping_list_items_router # New import
from service.config import settings

db = settings.get_db()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager to handle startup and shutdown events."""
    await db.connect()
    await db.create_tables()
    yield
    await db.close()


app = FastAPI(
    title="Cijene API",
    description="Service for product pricing data by Croatian grocery chains",
    version=settings.version,
    debug=settings.debug,
    lifespan=lifespan,
    openapi_components={
        "securitySchemes": {"HTTPBearer": {"type": "http", "scheme": "bearer"}}
    },
)

# Custom JSON serializer for Decimal objects
def json_decimal_encoder(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")

app.json_encoder = json_decimal_encoder

# Add CORS middleware to allow all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include versioned routers
app.include_router(v1_router, prefix="/v1")
app.include_router(v2_products_router, prefix="/v2")
app.include_router(v2_stores_router, prefix="/v2")
app.include_router(v2_chat_router, prefix="/v2")
app.include_router(v2_users_router, prefix="/v2") # New router
app.include_router(v2_user_locations_router, prefix="/v2") # New router
app.include_router(v2_ai_tools_router, prefix="/v2") # New router
app.include_router(v2_shopping_lists_router, prefix="/v2")
app.include_router(v2_shopping_list_items_router, prefix="/v2")


@app.exception_handler(404)
async def custom_404_handler(request: Request, exc: HTTPException):
    """Custom 404 handler with helpful message directing to API docs."""
    return JSONResponse(
        status_code=404,
        content={"detail": "Resource not found. Check documentation at /docs"},
    )


@app.get("/", include_in_schema=False)
async def root():
    """Root endpoint redirects to main website."""
    return RedirectResponse(url=settings.redirect_url, status_code=302)


@app.get("/health", tags=["Service status"])
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


def main():
    log_level = logging.DEBUG if settings.debug else logging.INFO
    
    # Configure the root logger to ensure all messages are captured
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Clear existing handlers to prevent duplicate output
    if root_logger.handlers:
        for handler in root_logger.handlers:
            root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    uvicorn.run(
        "service.main:app",
        host=settings.host,
        port=settings.port,
        log_level=log_level,
    )


if __name__ == "__main__":
    main()
print("<<< Finished importing in main.py")
