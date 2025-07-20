import os
import asyncio
import httpx
from decimal import Decimal
from dotenv import load_dotenv
import typer
import logging

from service.config import get_settings
from service.db.psql import PostgresDatabase
from service.db.models import Store # Keep Store, remove StoreWithId

# Custom exception for API rate limits
class RateLimitExceededError(Exception):
    pass

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Suppress debug and info logs from httpx and httpcore
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

app = typer.Typer()

async def get_db() -> PostgresDatabase:
    """Get a database instance."""
    db = get_settings().get_db()
    if not isinstance(db, PostgresDatabase):
        raise RuntimeError("Database is not a PostgresDatabase instance.")
    await db.connect()
    return db

async def geocode_address(address: str, api_key: str) -> tuple[Decimal, Decimal] | None:
    """
    Geocodes an address using the Google Geocoding API.
    Returns (lat, lon) or None if geocoding fails.
    """
    base_url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "address": address,
        "key": api_key
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(base_url, params=params, timeout=10.0)
            response.raise_for_status()
            data = response.json()

            if data["status"] == "OK" and data["results"]:
                location = data["results"][0]["geometry"]["location"]
                lat = Decimal(str(location["lat"]))
                lon = Decimal(str(location["lng"]))
                logger.info(f"Geocoded '{address}': Lat={lat}, Lng={lon}")
                return lat, lon
            elif data["status"] == "ZERO_RESULTS":
                logger.warning(f"No results for address: '{address}'")
                return None
            elif data["status"] == "OVER_QUERY_LIMIT":
                # Raise custom exception to stop the process immediately
                raise RateLimitExceededError(f"Geocoding API rate limit exceeded for '{address}'. Please check your API key usage.")
            else:
                logger.error(f"Geocoding API error for '{address}': {data.get('error_message', 'Unknown error')}")
                return None
        except httpx.RequestError as e:
            logger.error(f"HTTP request failed for '{address}': {e}")
            return None
        except Exception as e:
            logger.error(f"An unexpected error occurred during geocoding for '{address}': {e}")
            return None

@app.command()
async def geocode_stores():
    """
    Geocodes stores in the database that are missing lat/lon. # Updated description
    """
    load_dotenv() # Load environment variables from .env file
    google_api_key = os.getenv("GOOGLE_API_KEY")

    if not google_api_key:
        logger.error("GOOGLE_API_KEY environment variable not set. Please set it in your .env file.")
        raise typer.Exit(code=1)

    db = None
    try:
        db = await get_db()
        logger.info("Fetching ungeocoded stores...")
        ungeocoded_stores = await db.stores.get_ungeocoded_stores()
        logger.info(f"Found {len(ungeocoded_stores)} stores to geocode.")

        if not ungeocoded_stores:
            logger.info("No stores found requiring geocoding. Exiting.")
            return

        for store in ungeocoded_stores:
            full_address = ", ".join(filter(None, [store.address, store.city, store.zipcode, "Croatia"])) # Assuming Croatia as country
            if not full_address.strip():
                logger.warning(f"Skipping store ID {store.id} due to empty address components.")
                continue

            logger.info(f"Geocoding store ID {store.id}: '{full_address}'")
            try:
                result = await geocode_address(full_address, google_api_key)

                if result is not None:
                    lat, lon = result
                    updated_store = Store(
                        chain_id=store.chain_id,
                        code=store.code,
                        type=store.type,
                        address=store.address,
                        city=store.city,
                        zipcode=store.zipcode,
                        lat=lat,
                        lon=lon
                    )
                    # Use add_store for upserting, it will update existing store by chain_id and code
                    await db.stores.add_store(updated_store)
                    logger.info(f"Successfully updated store ID {store.id} with Lat={lat}, Lng={lon}")
                else:
                    # If result is None, it means geocoding failed for other reasons
                    logger.error(f"Failed to geocode store ID {store.id}: '{full_address}'. Skipping.")
            except RateLimitExceededError as e:
                logger.critical(str(e))
                break # Exit the loop immediately on rate limit

        logger.info("Geocoding process completed.")

    except Exception as e:
        logger.critical(f"An unrecoverable error occurred: {e}")
    finally:
        if db:
            # After geocoding attempts, count remaining ungeocoded stores
            remaining_ungeocoded = await db.stores.get_ungeocoded_stores()
            if remaining_ungeocoded:
                logger.warning(f"Geocoding finished with {len(remaining_ungeocoded)} stores still missing lat/lon.")
            else:
                logger.info("All stores have been geocoded.")
            await db.close()
            logger.info("Database connection closed.")

if __name__ == "__main__":
    asyncio.run(app())
