from contextlib import asynccontextmanager
import logging
import sys # Import sys for stdout
import structlog # Import structlog
import json # Import json for structlog's JSON renderer

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

# OpenTelemetry imports
from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

# Set up OpenTelemetry
resource = Resource.create({SERVICE_NAME: "cijene-api"})
trace.set_tracer_provider(TracerProvider(resource=resource))
tracer = trace.get_tracer(__name__)

# Configure OTLP exporter to send traces to Tempo
otlp_exporter = OTLPSpanExporter(endpoint="tempo:4317", insecure=True)
span_processor = BatchSpanProcessor(otlp_exporter)
trace.get_tracer_provider().add_span_processor(span_processor)

app = FastAPI(
    title="Cijene API",
    description="Service for product pricing data by Croatian grocery chains",
    version=get_settings().version,
    debug=get_settings().debug,
    openapi_components={
        "securitySchemes": {"HTTPBearer": {"type": "http", "scheme": "bearer"}}
    },
)

# Instrument FastAPI
# We are explicitly telling it to not exclude any URLs to ensure all endpoints are traced.
FastAPIInstrumentor.instrument_app(
    app,
    excluded_urls=None  # Ensure no URLs are excluded
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


import logging.config

def configure_logging():
    log_level = logging.DEBUG if get_settings().debug else logging.INFO
    is_debug = get_settings().debug

    # Configure structlog processors
    # These processors are applied to the event dictionary before rendering
    # They transform the event dictionary, but do not render it to a string.
    processors = [
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,  # Add this for better exception info in debug
        # structlog.processors.JSONRenderer(), # REMOVED: This should be the final step in the formatter
    ]

    # Configure structlog to use standard library logging
    structlog.configure(
        processors=processors + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Define logging configuration using dictConfig
    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json_formatter": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processor": structlog.processors.JSONRenderer(),  # FINAL JSON rendering step
                "foreign_pre_chain": processors,  # Use the common processors here
            },
            "console_formatter": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processor": structlog.dev.ConsoleRenderer(),  # FINAL console rendering step
                "foreign_pre_chain": processors,  # Use the common processors here
            },
        },
        "handlers": {
            "default": {
                "level": log_level,
                "class": "logging.StreamHandler",
                "formatter": "console_formatter" if is_debug else "json_formatter",
                "stream": "ext://sys.stdout",  # Explicitly use sys.stdout
            },
        },
        "loggers": {
            "": {  # root logger
                "handlers": ["default"],
                "level": log_level,
                "propagate": False,
            },
            "uvicorn": {
                "handlers": ["default"],
                "level": log_level,
                "propagate": True, # Propagate to root logger for JSON formatting
            },
            "uvicorn.error": {
                "handlers": ["default"],
                "level": log_level,
                "propagate": True, # Propagate to root logger for JSON formatting
            },
            "uvicorn.access": {
                "handlers": ["default"],
                "level": log_level,
                "propagate": True, # Propagate to root logger for JSON formatting
            },
        },
    }

    logging.config.dictConfig(logging_config)

    # ==================== ADD THIS PART ====================
    # Get a logger instance AFTER configuration is applied
    log = structlog.get_logger()

    # Log the configuration values using structured key-value pairs
    log.info(
        "Logging configured", 
        debug_mode=is_debug, 
        log_level=logging.getLevelName(log_level)
    )
    # =======================================================
    log.info("Logger level.", logger_name="uvicorn")
    log.info("Uvicorn logger configured with structlog.", logger_name="uvicorn")
    log.info("Uvicorn error logger configured with structlog.", logger_name="uvicorn.error")
    log.info("Uvicorn access logger configured with structlog.", logger_name="uvicorn.access")
    log.info("Starting application...")

# Call logging configuration at the module level
configure_logging()

# The uvicorn.run call should be outside of any function if it's meant to be the entry point
# when running directly, but since Uvicorn imports 'app', this part is handled by Uvicorn itself.
# We only need to ensure 'app' is defined and logging is configured.
# The original uvicorn.run call was inside main(), which is not executed when imported.
# We remove it as it's not needed for the Uvicorn server.
# If direct execution is still desired for local testing without docker,
# a simple uvicorn.run call can be added here, but it's usually handled by a separate script or Makefile.

# The print statement at the end of the file is also removed as it's part of the old main() block.

# This block is added for local development/testing without Docker,
# allowing direct execution of the script with Uvicorn.
if __name__ == "__main__":
    uvicorn.run(
        "service.main:app",
        host=get_settings().host,
        port=get_settings().port,
        log_config=None,  # Disable Uvicorn's default logging
        reload=False,  # Disable auto-reloading for stable logs during testing
        access_log=False  # Explicitly disable Uvicorn's access log, structlog will handle it
    )
