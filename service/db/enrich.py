#!/usr/bin/env python3
import argparse
import asyncio
import logging
from decimal import Decimal
from pathlib import Path
from csv import DictReader
from time import time
from typing import List, Dict
from datetime import datetime # Import datetime for parsing timestamps

from service.config import settings
from service.db.models import Product, User, UserLocation, SearchKeyword # Import new models

logger = logging.getLogger("enricher")

db = settings.get_db()


async def read_csv(file_path: Path) -> List[Dict[str, str]]:
    """
    Read a CSV file and return a list of dictionaries.

    Args:
        file_path: Path to the CSV file.

    Returns:
        List of dictionaries where each dictionary represents a row in the CSV.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            reader = DictReader(f)  # type: ignore
            return [row for row in reader]
    except Exception as e:
        logger.error(f"Error reading {file_path}: {e}")
        return []


def convert_unit_and_quantity(unit: str, quantity_str: str) -> tuple[str, Decimal]:
    """
    Convert unit and quantity according to business rules.

    Args:
        unit: Original unit from CSV.
        quantity_str: Original quantity string from CSV.

    Returns:
        Tuple of (converted_unit, converted_quantity).

    Raises:
        ValueError: If unit is not supported or quantity cannot be parsed.
    """
    try:
        quantity = Decimal(quantity_str)
    except (ValueError, TypeError):
        raise ValueError(f"Invalid quantity: {quantity_str}")

    unit = unit.strip().lower()

    if unit == "g":
        return "kg", quantity / Decimal("1000")
    elif unit == "ml":
        return "L", quantity / Decimal("1000")
    elif unit == "l":
        return "L", quantity
    elif unit == "par":
        return "kom", quantity
    elif unit in ["kg", "kom", "m"]:
        return unit, quantity
    else:
        raise ValueError(f"Unsupported unit: {unit}")


async def enrich_products(csv_path: Path) -> None:
    """
    Enrich product information from CSV file.

    Args:
        csv_path: Path to the CSV file containing product enrichment data.
    """

    if not csv_path.exists():
        raise ValueError(f"CSV file does not exist: {csv_path}")

    data = await read_csv(csv_path)
    if not data:
        raise ValueError(f"CSV file is empty or could not be read: {csv_path}")

    csv_columns = set(data[0].keys())
    if csv_columns != {"barcode", "brand", "name", "unit", "quantity"}:
        raise ValueError("CSV file headers do not match expected columns")

    logger.info(
        f"Starting product enrichment from {csv_path} with {len(data)} products"
    )
    t0 = time()

    # Get existing products by EAN
    existing_products = {
        product.ean: product
        for product in await db.get_products_by_ean(
            list(set(row["barcode"] for row in data))
        )
    }

    updated_count = 0
    for row in data:
        product = existing_products.get(row["barcode"])

        if not product:
            # This shouldn't happen but we can gracefully handle it
            await db.add_ean(row["barcode"])
            product = Product(
                ean=row["barcode"],
                brand="",
                name="",
                quantity=Decimal(0),
                unit="kom",
            )

        if product.brand or product.name:
            continue

        unit, qty = convert_unit_and_quantity(row["unit"], row["quantity"])
        updated_product = Product(
            ean=row["barcode"],
            brand=row["brand"],
            name=row["name"],
            quantity=qty,
            unit=unit,
        )

        was_updated = await db.update_product(updated_product)
        if was_updated:
            updated_count += 1

    t1 = time()
    dt = int(t1 - t0)
    logger.info(
        f"Enriched {updated_count} products from {csv_path.name} in {dt} seconds"
    )


async def enrich_stores(csv_path: Path) -> None:
    """
    Enrich store information from CSV file.

    Args:
        csv_path: Path to the CSV file containing store enrichment data.
    """
    if not csv_path.exists():
        raise ValueError(f"CSV file does not exist: {csv_path}")

    data = await read_csv(csv_path)
    if not data:
        raise ValueError(f"CSV file is empty or could not be read: {csv_path}")

    csv_columns = set(data[0].keys())
    expected_columns = {
        "id",
        "chain_code",
        "code",
        "type",
        "address",
        "city",
        "zipcode",
        "lat",
        "lon",
        "phone",
    }
    if csv_columns != expected_columns:
        raise ValueError("CSV file headers do not match expected columns for stores")

    logger.info(f"Starting store enrichment from {csv_path} with {len(data)} stores")
    t0 = time()

    # Fetch all chains and build a code -> id map
    chains = await db.list_chains()
    chain_code_to_id = {chain.code: chain.id for chain in chains}

    updated_count = 0
    for row in data:
        chain_code = row["chain_code"]
        store_code = row["code"]

        chain_id = chain_code_to_id.get(chain_code)
        if chain_id is None:
            logger.warning(
                f"Chain code not found for store: chain_code={chain_code}, code={store_code}"
            )
            continue

        # Convert empty strings to None for nullable fields
        address = row["address"].strip() or None
        city = row["city"].strip() or None
        zipcode = row["zipcode"].strip() or None
        phone = row["phone"].strip() or None

        # lat/lon: convert to float if present and not empty, else None
        lat = None
        lon = None
        if row["lat"].strip():
            try:
                lat = Decimal(row["lat"])
            except Exception:
                logger.warning(
                    f"Invalid lat value for store {store_code} in chain {chain_code}: {row['lat']}"
                )
        if row["lon"].strip():
            try:
                lon = Decimal(row["lon"])
            except Exception:
                logger.warning(
                    f"Invalid lon value for store {store_code} in chain {chain_code}: {row['lon']}"
                )

        # Only update if at least one field is non-empty
        if not any([address, city, zipcode, lat, lon, phone]):
            continue

        was_updated = await db.update_store(
            chain_id=chain_id,
            store_code=store_code,
            address=address,
            city=city,
            zipcode=zipcode,
            lat=lat,
            lon=lon,
            phone=phone,
        )
        if was_updated:
            updated_count += 1
        else:
            logger.warning(
                f"Store not found for update: chain_id={chain_id}, code={store_code}"
            )

    t1 = time()
    dt = int(t1 - t0)
    logger.info(f"Enriched {updated_count} stores from {csv_path.name} in {dt} seconds")


async def enrich_users(csv_path: Path) -> None:
    """
    Enrich user information from CSV file.

    Args:
        csv_path: Path to the CSV file containing user data.
    """
    if not csv_path.exists():
        raise ValueError(f"CSV file does not exist: {csv_path}")

    data = await read_csv(csv_path)
    if not data:
        raise ValueError(f"CSV file is empty or could not be read: {csv_path}")

    csv_columns = set(data[0].keys())
    expected_columns = {"id", "name", "api_key", "is_active", "created_at"}
    if csv_columns != expected_columns:
        raise ValueError("CSV file headers do not match expected columns for users")

    logger.info(f"Starting user enrichment from {csv_path} with {len(data)} users")
    t0 = time()

    users_to_add = []
    for row in data:
        users_to_add.append(
            User(
                id=int(row["id"]),
                name=row["name"],
                api_key=row["api_key"],
                is_active=row["is_active"].lower() == "true",
                created_at=datetime.fromisoformat(row["created_at"]),
            )
        )
    
    added_count = await db.add_many_users(users_to_add)

    t1 = time()
    dt = int(t1 - t0)
    logger.info(f"Enriched {added_count} users from {csv_path.name} in {dt} seconds")


async def enrich_user_locations(csv_path: Path) -> None:
    """
    Enrich user location information from CSV file.

    Args:
        csv_path: Path to the CSV file containing user location data.
    """
    if not csv_path.exists():
        raise ValueError(f"CSV file does not exist: {csv_path}")

    data = await read_csv(csv_path)
    if not data:
        raise ValueError(f"CSV file is empty or could not be read: {csv_path}")

    csv_columns = set(data[0].keys())
    expected_columns = {
        "id", "user_id", "address", "city", "state", "zip_code", "country",
        "latitude", "longitude", "location", "location_name", "created_at", "updated_at"
    }
    if csv_columns != expected_columns:
        raise ValueError("CSV file headers do not match expected columns for user locations")

    logger.info(f"Starting user location enrichment from {csv_path} with {len(data)} locations")
    t0 = time()

    locations_to_add = []
    for row in data:
        locations_to_add.append(
            UserLocation(
                id=int(row["id"]),
                user_id=int(row["user_id"]),
                address=row["address"] if row["address"] else None,
                city=row["city"] if row["city"] else None,
                state=row["state"] if row["state"] else None,
                zip_code=row["zip_code"] if row["zip_code"] else None,
                country=row["country"] if row["country"] else None,
                latitude=Decimal(row["latitude"]) if row["latitude"] else None,
                longitude=Decimal(row["longitude"]) if row["longitude"] else None,
                location_name=row["location_name"] if row["location_name"] else None,
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
        )
    
    added_count = await db.add_many_user_locations(locations_to_add)

    t1 = time()
    dt = int(t1 - t0)
    logger.info(f"Enriched {added_count} user locations from {csv_path.name} in {dt} seconds")


async def enrich_search_keywords(csv_path: Path) -> None:
    """
    Enrich search keyword information from CSV file.

    Args:
        csv_path: Path to the CSV file containing search keyword data.
    """
    if not csv_path.exists():
        raise ValueError(f"CSV file does not exist: {csv_path}")

    data = await read_csv(csv_path)
    if not data:
        raise ValueError(f"CSV file is empty or could not be read: {csv_path}")

    csv_columns = set(data[0].keys())
    expected_columns = {"id", "ean", "keyword", "created_at"}
    if csv_columns != expected_columns:
        raise ValueError("CSV file headers do not match expected columns for search keywords")

    logger.info(f"Starting search keyword enrichment from {csv_path} with {len(data)} keywords")
    t0 = time()

    # Collect all unique EANs from the CSV
    all_eans_in_csv = list(set(row["ean"] for row in data))

    # Get existing products by EAN
    existing_products_by_ean = {
        product.ean: product
        for product in await db.get_products_by_ean(all_eans_in_csv)
    }

    # Identify and add missing EANs to the products table
    missing_eans = [ean for ean in all_eans_in_csv if ean not in existing_products_by_ean]
    if missing_eans:
        logger.info(f"Found {len(missing_eans)} missing EANs in products table. Adding them...")
        for ean in missing_eans:
            await db.add_ean(ean) # Add minimal product entry
        # Re-fetch or update existing_products_by_ean if necessary, or assume add_ean makes them available
        # For simplicity, assuming add_ean makes them immediately available for FK check.

    keywords_to_add = []
    for row in data:
        keywords_to_add.append(
            SearchKeyword(
                id=int(row["id"]),
                ean=row["ean"],
                keyword=row["keyword"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
        )
    
    added_count = await db.add_many_search_keywords(keywords_to_add)

    t1 = time()
    dt = int(t1 - t0)
    logger.info(f"Enriched {added_count} search keywords from {csv_path.name} in {dt} seconds")


async def main():
    """
    Data enrichment tool for the price service API.

    This script enriches existing database records with additional information
    from CSV files.
    """
    parser = argparse.ArgumentParser(
        description=main.__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "csv_file", type=Path, help="Path to the CSV file containing enrichment data"
    )

    parser.add_argument(
        "--type",
        type=str,
        choices=["products", "stores", "users", "user-locations", "search-keywords"],
        required=True,
        help="Type of data to enrich (products, stores, users, user-locations, search-keywords)",
    )

    parser.add_argument(
        "-d", "--debug", action="store_true", help="Enable debug logging"
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s:%(name)s:%(levelname)s:%(message)s",
    )

    await db.connect()
    # await db.create_tables() # Removed this line

    try:
        if args.type == "products":
            await enrich_products(args.csv_file)
        elif args.type == "stores":
            await enrich_stores(args.csv_file)
        elif args.type == "users":
            await enrich_users(args.csv_file)
        elif args.type == "user-locations":
            await enrich_user_locations(args.csv_file)
        elif args.type == "search-keywords":
            await enrich_search_keywords(args.csv_file)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
