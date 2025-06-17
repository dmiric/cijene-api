import asyncio
import argparse
import logging
from service.config import settings
from service.db.psql import PostgresDatabase

logger = logging.getLogger(__name__)

async def main():
    parser = argparse.ArgumentParser(
        description="Add a new user and generate an API key."
    )
    parser.add_argument("username", type=str, help="The username for the new user.")
    args = parser.parse_args()

    db: PostgresDatabase = settings.get_db()

    try:
        await db.connect()
        user = await db.add_user(args.username)
        print(f"User '{user.name}' added successfully.")
        print(f"API Key: {user.api_key}")
    except Exception as e:
        logger.error(f"Error adding user: {e}")
        print(f"Error: {e}")
    finally:
        await db.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s:%(levelname)s:%(message)s")
    asyncio.run(main())
