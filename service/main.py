print(">>> Importing main.py")
from contextlib import asynccontextmanager
import logging
import sys # Import sys for stdout

from decimal import Decimal # Import Decimal
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, JSONResponse, PlainTextResponse
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from service.routers.v1.stores import router as stores_router
from service.routers.v1.crawler import router as crawler_router
from service.routers.v1.importer import router as importer_router # New import for importer router
from service.routers.v2.products import router as v2_products_router
from service.routers.v2.stores import router as v2_stores_router
from service.routers.v2.chat import router as v2_chat_router
from service.routers.v2.users import router as v2_users_router # New import
from service.routers.v2.user_locations import router as v2_user_locations_router # New import
from service.routers.v2.ai_tools import router as v2_ai_tools_router # New import
from service.routers.v2.shopping_lists import router as v2_shopping_lists_router # New import
from service.routers.v2.shopping_list_items import router as v2_shopping_list_items_router # New import
from service.routers.v2.dashboard import router as v2_dashboard_router # New import for dashboard router
from service.routers.auth import router as auth_router # New import for authentication router
from service.config import get_settings
from service.db.base import database_container, get_db_session # Import from base.py
from service.db.psql import PostgresDatabase # Keep this import for type hinting in settings.get_db()
from prometheus_client import generate_latest, Counter, Histogram

app = FastAPI(
    title="Cijene API",
    description="Service for product pricing data by Croatian grocery chains",
    version=get_settings().version,
    debug=get_settings().debug,
    openapi_components={
        "securitySchemes": {"HTTPBearer": {"type": "http", "scheme": "bearer"}}
    },
)

@app.on_event("startup")
async def startup_event():
    """Startup event handler to initialize the database."""
    database_container.db = get_settings().get_db()
    await database_container.db.connect()
    await database_container.db.create_tables()

@app.on_event("shutdown")
async def shutdown_event():
    """Shutdown event handler to close the database connection."""
    if database_container.db:
        await database_container.db.close()

# Prometheus Metrics
REQUEST_COUNT = Counter(
    'http_requests_total', 'Total HTTP Requests',
    ['method', 'endpoint', 'status_code']
)
REQUEST_LATENCY = Histogram(
    'http_request_duration_seconds', 'HTTP Request Latency',
    ['method', 'endpoint']
)

@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    endpoint = request.url.path
    method = request.method

    with REQUEST_LATENCY.labels(method=method, endpoint=endpoint).time():
        response = await call_next(request)
    
    REQUEST_COUNT.labels(method=method, endpoint=endpoint, status_code=response.status_code).inc()
    return response

@app.get("/metrics", include_in_schema=False)
async def metrics():
    return PlainTextResponse(content=generate_latest().decode("utf-8"), media_type="text/plain")

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
app.include_router(stores_router, prefix="/v1")
app.include_router(crawler_router, prefix="/v1")
app.include_router(importer_router, prefix="/v1") # Include the new importer router
app.include_router(v2_products_router, prefix="/v2")
app.include_router(v2_stores_router, prefix="/v2")
app.include_router(v2_chat_router, prefix="/v2")
app.include_router(v2_users_router, prefix="/v2") # New router
app.include_router(v2_user_locations_router, prefix="/v2") # New router
app.include_router(v2_ai_tools_router, prefix="/v2") # New router
app.include_router(v2_shopping_lists_router, prefix="/v2")
app.include_router(v2_shopping_list_items_router, prefix="/v2")
app.include_router(v2_dashboard_router, prefix="/v2") # Include the new dashboard router
app.include_router(auth_router, prefix="/auth") # Include the new authentication router


@app.get("/", include_in_schema=False)
async def root():
    """Root endpoint redirects to main website."""
    return RedirectResponse(url=get_settings().redirect_url, status_code=302)


@app.get("/health", tags=["Service status"])
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


def main():
    log_level = logging.DEBUG if get_settings().debug else logging.INFO
    
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
        host=get_settings().host,
        port=get_settings().port,
        log_level=log_level,
        reload=False, # Disable auto-reloading for stable logs during testing
    )


if __name__ == "__main__":
    main()
print("<<< Finished importing in main.py")
