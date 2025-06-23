import asyncio
import logging
from service.config import settings
from service.db.psql import PostgresDatabase

logger = logging.getLogger(__name__)

async def main():
    db: PostgresDatabase = settings.get_db()

    try:
        await db.connect()
        user = await db.get_user_by_id(1) # Attempt to get user with ID 1
        if user:
            print(f"User ID 1 found. Name: {user.name}, API Key: {user.api_key}")
        else:
            print("User ID 1 not found in the database.")
    except Exception as e:
        logger.error(f"Error retrieving user: {e}")
        print(f"Error: {e}")
    finally:
        await db.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s:%(levelname)s:%(message)s")
    asyncio.run(main())
