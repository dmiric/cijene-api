import os
import sys
import asyncio
import asyncpg
import logging
import logging.config # Import logging.config
import structlog
import json
from glob import glob
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def configure_logging():
    # Configure structlog processors
    processors = [
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
    ]

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json_formatter": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processor": structlog.processors.JSONRenderer(),
                "foreign_pre_chain": processors,
            },
        },
        "handlers": {
            "default": {
                "level": logging.INFO,
                "class": "logging.StreamHandler",
                "formatter": "json_formatter",
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            "": {  # root logger
                "handlers": ["default"],
                "level": logging.INFO,
                "propagate": False,
            },
        },
    }

    logging.config.dictConfig(logging_config)

# Call logging configuration
configure_logging()

logger = structlog.get_logger(__name__)

async def apply_migrations():
    db_url = os.getenv("DB_DSN")
    if not db_url:
        logger.error("DB_DSN environment variable not set.", event_name="DB_DSN_missing")
        sys.exit(1)

    conn = None
    try:
        conn = await asyncpg.connect(db_url)
        logger.info("Connected to database for migrations.", event_name="db_connected")

        # Ensure schema_migrations table exists
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version VARCHAR(255) PRIMARY KEY,
                applied_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            );
        """)
        logger.info("Ensured schema_migrations table exists.", event_name="schema_migrations_table_checked")

        # Get already applied migrations
        applied_migrations = await conn.fetchval(
            "SELECT ARRAY_AGG(version ORDER BY version) FROM schema_migrations"
        )
        applied_migrations = set(applied_migrations) if applied_migrations else set()
        logger.info("Already applied migrations.", applied_migrations=list(applied_migrations), event_name="applied_migrations_list")

        migrations_dir = os.path.join(os.path.dirname(__file__), "migrations")
        migration_files = sorted(glob(os.path.join(migrations_dir, "*.sql")))

        if not migration_files:
            logger.info("No migration files found.", event_name="no_migration_files")
            return

        for file_path in migration_files:
            version = os.path.basename(file_path).split('__')[0]
            if version in applied_migrations:
                logger.info("Migration already applied. Skipping.", version=version, event_name="migration_skipped")
                continue

            logger.info("Applying migration.", version=version, file_path=file_path, event_name="applying_migration")
            try:
                with open(file_path, "r") as f:
                    sql_content = f.read()
                
                async with conn.transaction():
                    await conn.execute(sql_content)
                    await conn.execute(
                        "INSERT INTO schema_migrations (version) VALUES ($1)",
                        version
                    )
                logger.info("Successfully applied migration.", version=version, event_name="migration_applied_success")
            except Exception as e:
                logger.error("Error applying migration.", version=version, error=str(e), event_name="migration_apply_error")
                # Optionally, re-raise to stop deployment if a migration fails
                raise

    except Exception as e:
        logger.error("Database migration failed.", error=str(e), event_name="db_migration_failed")
        sys.exit(1)
    finally:
        if conn:
            await conn.close()
            logger.info("Database connection closed.", event_name="db_connection_closed")

if __name__ == "__main__":
    asyncio.run(apply_migrations())
