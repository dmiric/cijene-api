import os
import sys
import asyncio
import asyncpg
import logging
from glob import glob

# Configure logging
logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def apply_migrations():
    db_url = os.getenv("DB_DSN")
    if not db_url:
        logger.error("DB_DSN environment variable not set.")
        sys.exit(1)

    conn = None
    try:
        conn = await asyncpg.connect(db_url)
        logger.info("Connected to database for migrations.")

        # Ensure schema_migrations table exists
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version VARCHAR(255) PRIMARY KEY,
                applied_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            );
        """)
        logger.info("Ensured schema_migrations table exists.")

        # Get already applied migrations
        applied_migrations = await conn.fetchval(
            "SELECT ARRAY_AGG(version ORDER BY version) FROM schema_migrations"
        )
        applied_migrations = set(applied_migrations) if applied_migrations else set()
        logger.info(f"Already applied migrations: {applied_migrations}")

        migrations_dir = os.path.join(os.path.dirname(__file__), "migrations")
        migration_files = sorted(glob(os.path.join(migrations_dir, "*.sql")))

        if not migration_files:
            logger.info("No migration files found.")
            return

        for file_path in migration_files:
            version = os.path.basename(file_path).split('__')[0]
            if version in applied_migrations:
                logger.info(f"Migration {version} already applied. Skipping.")
                continue

            logger.info(f"Applying migration: {version} from {file_path}")
            try:
                with open(file_path, "r") as f:
                    sql_content = f.read()
                
                async with conn.transaction():
                    await conn.execute(sql_content)
                    await conn.execute(
                        "INSERT INTO schema_migrations (version) VALUES ($1)",
                        version
                    )
                logger.info(f"Successfully applied migration: {version}")
            except Exception as e:
                logger.error(f"Error applying migration {version}: {e}")
                # Optionally, re-raise to stop deployment if a migration fails
                raise

    except Exception as e:
        logger.error(f"Database migration failed: {e}")
        sys.exit(1)
    finally:
        if conn:
            await conn.close()
            logger.info("Database connection closed.")

if __name__ == "__main__":
    asyncio.run(apply_migrations())
