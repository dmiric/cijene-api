import os
import asyncio
import httpx
from decimal import Decimal
from dotenv import load_dotenv
import typer
import logging

from service.config import settings
from service.db.psql import PostgresDatabase
from service.db.models import Store # Keep Store, remove StoreWithId

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = typer.Typer()

async def get_db() -> PostgresDatabase:
    """Get a database instance."""
    db = settings.get_db()
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
                lat = Decimal(str(location["lat"])) # Renamed to lat
                lon = Decimal(str(location["lng"])) # Renamed to lon
                logger.info(f"Geocoded '{address}': Lat={lat}, Lng={lon}") # Updated log
                return lat, lon # Renamed to lat, lon
            elif data["status"] == "ZERO_RESULTS":
                logger.warning(f"No results for address: '{address}'")
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
        ungeocoded_stores = await db.get_ungeocoded_stores()
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
            lat, lon = await geocode_address(full_address, google_api_key) # Renamed to lat, lon

            if lat is not None and lon is not None: # Renamed to lat, lon
                updated_store = Store(
                    chain_id=store.chain_id,
                    code=store.code,
                    type=store.type,
                    address=store.address,
                    city=store.city,
                    zipcode=store.zipcode,
                    lat=lat, # Renamed to lat
                    lon=lon # Renamed to lon
                )
                # Use add_store for upserting, it will update existing store by chain_id and code
                await db.add_store(updated_store)
                logger.info(f"Successfully updated store ID {store.id} with Lat={lat}, Lng={lon}") # Updated log
            else:
                logger.error(f"Failed to geocode store ID {store.id}: '{full_address}'")

        logger.info("Geocoding process completed.")

    except Exception as e:
        logger.critical(f"An unrecoverable error occurred: {e}")
        raise typer.Exit(code=1)
    finally:
        if db:
            await db.close()
            logger.info("Database connection closed.")

if __name__ == "__main__":
    asyncio.run(app())
