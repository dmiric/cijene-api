import asyncio
import argparse
import logging
import uuid
from service.config import settings
from service.db.psql import PostgresDatabase

logger = logging.getLogger(__name__)

async def main():
    parser = argparse.ArgumentParser(
        description="Add a new user and generate an API key."
    )
    parser.add_argument("username", type=str, help="The username for the new user.")
    parser.add_argument("email", type=str, help="The email for the new user.") # Added email argument
    args = parser.parse_args()

    db: PostgresDatabase = settings.get_db()

    try:
        await db.connect()
        user, user_personal_data = await db.users.add_user(args.username, args.email) # Updated call and return type
        print(f"User '{user_personal_data.name}' (ID: {user.id}) added successfully.") # Access name from user_personal_data
        print(f"API Key: {user_personal_data.api_key}") # Access API key from user_personal_data
    except Exception as e:
        logger.error(f"Error adding user: {e}")
        print(f"Error: {e}")
    finally:
        await db.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s:%(levelname)s:%(message)s")
    asyncio.run(main())
