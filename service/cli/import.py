#!/usr/bin/env python3
import argparse
import asyncio
import logging
import zipfile
from csv import DictReader
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import shutil # Import shutil for directory removal
from tempfile import TemporaryDirectory
from time import time
import os
from typing import Any, Dict, List, Optional

from prometheus_client import CollectorRegistry, Gauge, Counter, Summary, push_to_gateway

from service.config import get_settings
from service.db.models import Chain, ChainProduct, Price, Store, User, UserLocation, ImportRun, ImportStatus, CrawlStatus

logger = logging.getLogger("importer")

# The db object will be initialized inside the main() function.
db: Any = None # Optional: Use a type hint for better static analysis

# Prometheus Metrics
registry = CollectorRegistry()

IMPORTS_TOTAL = Counter(
    'importer_imports_total',
    'Total number of import runs initiated',
    ['chain_name', 'status'],
    registry=registry
)
IMPORT_ERRORS_TOTAL = Counter(
    'importer_import_errors_total',
    'Total number of errors during import runs',
    ['chain_name', 'error_type'],
    registry=registry
)
IMPORT_DURATION_SECONDS = Summary(
    'importer_import_duration_seconds',
    'Time spent importing data for a chain',
    ['chain_name', 'status'],
    registry=registry
)
IMPORTED_STORES_COUNT = Gauge(
    'importer_imported_stores_count',
    'Number of stores imported in a run',
    ['chain_name'],
    registry=registry
)
IMPORTED_PRODUCTS_COUNT = Gauge(
    'importer_imported_products_count',
    'Number of products imported in a run',
    ['chain_name'],
    registry=registry
)
IMPORTED_PRICES_COUNT = Gauge(
    'importer_imported_prices_count',
    'Number of prices imported in a run',
    ['chain_name'],
    registry=registry
)


async def read_csv(file_path: Path) -> List[Dict[str, str]]:
    """
    Read a CSV file and return a list of dictionaries.

    Args:
        file_path: Path to the CSV file.

    Returns:
        List of dictionaries where each dictionary represents a row in the CSV.
    """
    try:
        logger.debug(f"Opening CSV file: {file_path}")
        with open(file_path, "r", encoding="utf-8") as f:
            reader = DictReader(f)  # type: ignore
            rows = [row for row in reader]
            logger.debug(f"Finished reading {len(rows)} rows from {file_path}")
            return rows
    except Exception as e:
        logger.error(f"Error reading {file_path}: {e}")
        return []


async def process_stores(stores_path: Path, chain_id: int) -> dict[str, int]:
    """
    Process stores CSV and import to database.

    Args:
        stores_path: Path to the stores CSV file.
        chain_id: ID of the chain to which these stores belong.

    Returns:
        A dictionary mapping store codes to their database IDs.
    """
    logger.debug(f"Importing stores from {stores_path}")

    stores_data = await read_csv(stores_path)
    store_map = {}

    for store_row in stores_data:
        store = Store(
            chain_id=chain_id,
            code=store_row["store_id"],
            type=store_row.get("type"),
            address=store_row.get("address"),
            city=store_row.get("city"),
            zipcode=store_row.get("zipcode"),
        )
        logger.debug(f"Adding store {store.code} to DB...")
        store_id = await db.add_store(store)
        logger.debug(f"Store {store.code} added with ID {store_id}")
        store_map[store.code] = store_id

    logger.debug(f"Processed {len(stores_data)} stores")
    return store_map

async def process_products(
    products_path: Path,
    chain_id: int,
    chain_code: str,
    barcodes: dict[str, int],
) -> Dict[str, int]:
    """
    Process products CSV and import to database.

    As a side effect, this function will also add any newly
    created EAN codes to the provided `barcodes` dictionary.

    Args:
        products_path: Path to the products CSV file.
        chain_id: ID of the chain to which these products belong.
        chain_code: Code of the retail chain.
        barcodes: Dictionary mapping EAN codes to global product IDs.

    Returns:
        A dictionary mapping product codes to their database IDs for the chain.
    """
    logger.debug(f"Processing products from {products_path}")

    products_data = await read_csv(products_path)
    logger.debug(f"Fetching chain product map for chain_id {chain_id}...")
    chain_product_map = await db.get_chain_product_map(chain_id)
    logger.debug(f"Fetched {len(chain_product_map)} existing chain products.")

    # Ideally the CSV would already have valid barcodes, but some older
    # archives contain invalid ones so we need to clean them up.
    def clean_barcode(data: dict[str, Any]) -> dict:
        barcode = data.get("barcode", "").strip()

        if ":" in barcode:
            return data

        if len(barcode) >= 8 and barcode.isdigit():
            return data

        product_id = data.get("product_id", "")
        if not product_id:
            logger.warning(f"Product has no barcode: {data}")
            return data

        # Construct a chain-specific barcode
        data["barcode"] = f"{chain_code}:{product_id}"
        return data

    new_products = [
        clean_barcode(p)
        for p in products_data
        if p["product_id"] not in chain_product_map
    ]

    if not new_products:
        return chain_product_map

    logger.debug(
        f"Found {len(new_products)} new products out of {len(products_data)} total"
    )

    n_new_barcodes = 0
    for product in new_products:
        barcode = product["barcode"]
        if barcode in barcodes:
            continue

        global_product_id = await db.add_ean(barcode)
        barcodes[barcode] = global_product_id
        n_new_barcodes += 1

    if n_new_barcodes:
        logger.debug(f"Added {n_new_barcodes} new barcodes to global products")

    products_to_create = []
    for product in new_products:
        barcode = product["barcode"]
        code = product["product_id"]
        global_product_id = barcodes[barcode]

        products_to_create.append(
            ChainProduct(
                chain_id=chain_id,
                product_id=global_product_id,
                code=code,
                name=product["name"],
                brand=(product["brand"] or "").strip() or None,
                category=(product["category"] or "").strip() or None,
                unit=(product["unit"] or "").strip() or None,
                quantity=(product["quantity"] or "").strip() or None,
            )
        )

    logger.debug(f"Attempting to insert {len(products_to_create)} new chain products...")
    n_inserts = await db.add_many_chain_products(products_to_create)
    if n_inserts != len(new_products):
        logger.warning(
            f"Expected to insert {len(new_products)} products, but inserted {n_inserts}."
        )
    logger.debug(f"Imported {n_inserts} new chain products.")

    chain_product_map = await db.get_chain_product_map(chain_id)
    return chain_product_map


async def process_prices(
    price_date: date,
    prices_path: Path,
    chain_id: int,
    store_map: dict[str, int],
    chain_product_map: dict[str, int],
) -> int:
    """
    Process prices CSV and import to database.

    Args:
        price_date: The date for which the prices are valid.
        prices_path: Path to the prices CSV file.
        chain_id: ID of the chain to which these prices belong.
        store_map: Dictionary mapping store codes to their database IDs.
        chain_product_map: Dictionary mapping product codes to their database IDs.

    Returns:
        The number of prices successfully inserted into the database.
    """
    logger.debug(f"Reading prices from {prices_path}")

    prices_data = await read_csv(prices_path)

    # Create price objects
    prices_to_create = []
    seen_prices = set() # To track unique price entries

    logger.debug(f"Found {len(prices_data)} price entries, preparing to import")

    def clean_price(value: str) -> Decimal | None:
        if value is None:
            return None
        value = value.strip()
        if value == "":
            return None
        dval = Decimal(value)
        if dval == 0:
            return None
        return dval

    for price_row in prices_data:
        store_id = store_map.get(price_row["store_id"])
        if store_id is None:
            logger.warning(f"Skipping price for unknown store {price_row['store_id']}")
            continue

        product_id = chain_product_map.get(price_row["product_id"])
        if product_id is None:
            # Price for a product that wasn't added, perhaps because the
            # barcode is invalid
            logger.warning(
                f"Skipping price for unknown product {price_row['product_id']}"
            )
            continue

        # Create a unique key for the price entry
        price_key = (product_id, store_id, price_date)
        if price_key in seen_prices:
            # This is a duplicate within the current CSV batch, which will be handled by ON CONFLICT DO UPDATE
            # in the database. No need to log a warning here.
            continue
        seen_prices.add(price_key)

        prices_to_create.append(
            Price(
                chain_product_id=product_id,
                store_id=store_id,
                price_date=price_date,
                regular_price=Decimal(price_row["price"]),
                special_price=clean_price(price_row.get("special_price") or ""),
                unit_price=clean_price(price_row["unit_price"]),
                best_price_30=clean_price(price_row["best_price_30"]),
                anchor_price=clean_price(price_row["anchor_price"]),
            )
        )

    logger.debug(f"Attempting to insert {len(prices_to_create)} unique prices...")
    n_inserted = await db.add_many_prices(prices_to_create)
    logger.debug(f"Inserted {n_inserted} unique prices.")
    return n_inserted


async def process_chain(
    price_date: date,
    chain_dir: Path,
    barcodes: dict[str, int],
    chain_name: str, # Added chain_name here
    import_run_id: Optional[int] = None,
) -> dict[str, Any]:
    """
    Process a single retail chain and import its data.

    The expected directory structure and CSV columns are documented in
    `crawler/store/archive_info.txt`.

    Note: updates the `barcodes` dictionary with any new EAN codes found
    (see the `process_products` function).

    Args:
        price_date: The date for which the prices are valid.
        chain_dir: Path to the directory containing the chain's CSV files.
        barcodes: Dictionary mapping EAN codes to global product IDs.
        chain_name: The actual name of the chain (e.g., "boso", "roto").
        import_run_id: Optional ID of the associated import run.

    Returns:
        A dictionary containing import statistics for the chain.
    """
    code = chain_name # Use the passed chain_name here

    stores_path = chain_dir / "stores.csv"
    if not stores_path.exists():
        logger.warning(f"No stores.csv found for chain {code}")
        return {}

    products_path = chain_dir / "products.csv"
    if not products_path.exists():
        logger.warning(f"No products.csv found for chain {code}")
        return {}

    prices_path = chain_dir / "prices.csv"
    if not prices_path.exists():
        logger.warning(f"No prices.csv found for chain {code}")
        return {}

    logger.debug(f"Processing chain: {code}")

    chain = Chain(code=code)
    chain_id = await db.add_chain(chain)

    store_map = await process_stores(stores_path, chain_id)
    chain_product_map = await process_products(products_path, chain_id, code, barcodes)

    n_new_prices = await process_prices(
        price_date,
        prices_path,
        chain_id,
        store_map,
        chain_product_map,
    )

    logger.info(f"Imported {n_new_prices} new prices for {code}")
    return {
        "n_stores": len(store_map),
        "n_products": len(chain_product_map),
        "n_prices": n_new_prices,
    }


async def _import_single_chain_data(
    chain_name: str,
    chain_data_path: Path, # This is the directory containing stores.csv, products.csv, prices.csv
    price_date: datetime,
    crawl_run_id: Optional[int] = None,
    unzipped_path: Optional[str] = None, # This is the path to the original zip file
    semaphore: Optional[asyncio.Semaphore] = None, # Added semaphore parameter
    timeout: Optional[int] = None, # Added timeout parameter
    price_computation_lock: Optional[asyncio.Lock] = None, # New: Lock for serializing price computations
) -> None:
    """
    Imports data for a single chain and logs the import run.
    """
    t0 = time()
    total_stores = 0
    total_products = 0
    total_prices = 0
    error_message: Optional[str] = None
    status = ImportStatus.STARTED

    # If not already successfully imported, proceed with adding/updating the import run record
    # This needs to be outside the try-except for timeout, so we always have an import_run_id
    import_run_id = await db.import_runs.add_import_run(
        chain_name=chain_name,
        import_date=price_date.date(),
        crawl_run_id=crawl_run_id,
        unzipped_path=unzipped_path,
    )

    try:
        # Check if this chain and date combination has already been successfully imported
        # This check should be inside the semaphore context if it involves DB access
        # or if we want to skip acquiring semaphore for already imported runs.
        # For now, let's keep it outside the timeout, but inside the semaphore if present.
        if semaphore:
            async with semaphore:
                existing_import_run = await db.import_runs.get_import_run_by_chain_and_date(
                    chain_name=chain_name, import_date=price_date.date()
                )

                if existing_import_run and existing_import_run.status == ImportStatus.SUCCESS:
                    logger.info(
                        f"Skipping import for chain {chain_name} on {price_date.date()} as it was already successfully imported (ID: {existing_import_run.id})."
                    )
                    # If the unzipped directory exists and was created by this process, delete it.
                    if chain_data_path.is_dir() and chain_data_path.name == chain_name:
                        try:
                            shutil.rmtree(chain_data_path)
                            logger.debug(f"Successfully deleted unzipped directory: {chain_data_path}")
                        except OSError as e:
                            logger.error(f"Error deleting unzipped directory {chain_data_path}: {e}")
                    # Update the import run status to SKIPPED if we have such a status, or just return
                    await db.import_runs.update_import_run_status(
                        import_run_id=import_run_id,
                        status=ImportStatus.SKIPPED if hasattr(ImportStatus, 'SKIPPED') else ImportStatus.SUCCESS, # Use SKIPPED if available
                        error_message="Already successfully imported",
                        n_stores=0, n_products=0, n_prices=0, elapsed_time=0
                    )
                    return # Skip the rest of the import process for this chain
        else: # No semaphore, still check for existing run
            existing_import_run = await db.import_runs.get_import_run_by_chain_and_date(
                chain_name=chain_name, import_date=price_date.date()
            )

            if existing_import_run and existing_import_run.status == ImportStatus.SUCCESS:
                logger.info(
                    f"Skipping import for chain {chain_name} on {price_date.date()} as it was already successfully imported (ID: {existing_import_run.id})."
                    )
                if chain_data_path.is_dir() and chain_data_path.name == chain_name:
                    try:
                        shutil.rmtree(chain_data_path)
                        logger.debug(f"Successfully deleted unzipped directory: {chain_data_path}")
                    except OSError as e:
                        logger.error(f"Error deleting unzipped directory {chain_data_path}: {e}")
                await db.import_runs.update_import_run_status(
                    import_run_id=import_run_id,
                    status=ImportStatus.SKIPPED if hasattr(ImportStatus, 'SKIPPED') else ImportStatus.SUCCESS,
                    error_message="Already successfully imported",
                    n_stores=0, n_products=0, n_prices=0, elapsed_time=0
                )
                return

        # The actual import logic that might hang, wrapped in timeout
        async def _perform_import_logic():
            nonlocal total_stores, total_products, total_prices # Allow modification of outer scope variables
            barcodes = await db.get_product_barcodes()
            chain_stats = await process_chain(price_date, chain_data_path, barcodes, chain_name, import_run_id)
            total_stores = chain_stats.get("n_stores", 0)
            total_products = chain_stats.get("n_products", 0)
            total_prices = chain_stats.get("n_prices", 0)

            logger.debug(f"Computing average chain prices for {price_date:%Y-%m-%d}")
            # Acquire lock to ensure only one price computation runs at a time
            if price_computation_lock:
                async with price_computation_lock:
                    await db.compute_chain_prices(price_date)
            else:
                await db.compute_chain_prices(price_date)

            logger.debug(f"Computing chain stats for {price_date:%Y-%m-%d}")
            await db.compute_chain_stats(price_date)

        if timeout:
            await asyncio.wait_for(_perform_import_logic(), timeout=timeout)
        else:
            await _perform_import_logic()
        
        if total_prices > 0:
            status = ImportStatus.SUCCESS
        else:
            status = ImportStatus.FAILED
            error_message = "No new prices were imported."

    except asyncio.TimeoutError:
        logger.warning(f"Import for chain {chain_name} timed out after {timeout} seconds. Marking as FAILED.")
        status = ImportStatus.FAILED
        error_message = f"Timed out after {timeout} seconds."
    except Exception as e:
        logger.error(f"Error during import for chain {chain_name}: {e}", exc_info=True)
        status = ImportStatus.FAILED
        error_message = str(e)
    finally:
        t1 = time()
        elapsed_time = t1 - t0
        await db.import_runs.update_import_run_status(
            import_run_id=import_run_id,
            status=status,
            error_message=error_message,
            n_stores=total_stores,
            n_products=total_products,
            n_prices=total_prices,
            elapsed_time=elapsed_time,
        )
        logger.info(f"Imported chain {chain_name} in {int(elapsed_time)} seconds with status {status.value}")

        # Update Prometheus metrics
        IMPORTS_TOTAL.labels(chain_name=chain_name, status=status.value).inc()
        IMPORT_DURATION_SECONDS.labels(chain_name=chain_name, status=status.value).observe(elapsed_time)
        IMPORTED_STORES_COUNT.labels(chain_name=chain_name).set(total_stores)
        IMPORTED_PRODUCTS_COUNT.labels(chain_name=chain_name).set(total_products)
        IMPORTED_PRICES_COUNT.labels(chain_name=chain_name).set(total_prices)
        if status == ImportStatus.FAILED:
            IMPORT_ERRORS_TOTAL.labels(chain_name=chain_name, error_type="import_failed").inc()

        # Push metrics to Pushgateway
        try:
            pushgateway_url = os.getenv("PROMETHEUS_PUSHGATEWAY_URL")
            if pushgateway_url:
                job_name = f"importer_{chain_name}_{price_date.strftime('%Y%m%d')}"
                push_to_gateway(pushgateway_url, job=job_name, registry=registry)
                logger.info(f"Metrics pushed to Pushgateway for chain {chain_name}.")
            else:
                logger.warning("PROMETHEUS_PUSHGATEWAY_URL not set, skipping pushing metrics to Pushgateway.")
        except Exception as e:
            logger.error(f"Error pushing metrics to Pushgateway for chain {chain_name}: {e}", exc_info=True)

        # Delete the unzipped directory if it was created by this process
        if chain_data_path.is_dir() and chain_data_path.name == chain_name: # Ensure it's an unzipped directory, not the original source
            try:
                shutil.rmtree(chain_data_path)
                logger.debug(f"Successfully deleted unzipped directory: {chain_data_path}")
            except OSError as e:
                logger.error(f"Error deleting unzipped directory {chain_data_path}: {e}")


async def main():
    """
    Import price data from directories or zip archives.

    This script expects the directories to be named in the format YYYY-MM-DD,
    containing subdirectories for each retail chain. Each chain directory
    should contain CSV files named `stores.csv`, `products.csv`, and `prices.csv`.
    The CSV files should follow the structure documented in
    `crawler/store/archive_info.txt`.

    Zip archives should be named YYYY-MM-DD.zip and contain the same resources
    as directories described above.

    Database connection settings are loaded from the service configuration, see
    `service/config.py` for details.
    """
    parser = argparse.ArgumentParser(
        description=main.__doc__,
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "path",
        type=Path,
        nargs="?", # Make the path argument optional
        help="Directory containing price data in YYYY-MM-DD format. If not provided, assumes today's date.",
    )
    parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5, # Default to 5 concurrent imports
        help="Number of concurrent chain imports",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Timeout in seconds for each individual chain import",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s:%(name)s:%(levelname)s:%(message)s",
    )

    # --- FIX 1: Add a delay to avoid a race condition with the crawler job ---
    # This gives the database a moment to finish writes and release locks from the previous step.
    DELAY_SECONDS = 3
    logger.info(f"Waiting for {DELAY_SECONDS} seconds to allow database to settle...")
    await asyncio.sleep(DELAY_SECONDS)

    # --- FIX 2: Defer DB object initialization to inside main() ---
    global db
    db = get_settings().get_db()

    # --- FIX 3: Connect to the database with a timeout to prevent silent hangs ---
    CONNECT_TIMEOUT = 60  # seconds
    try:
        logger.info(f"Attempting to connect to the database (timeout: {CONNECT_TIMEOUT}s)...")
        await asyncio.wait_for(db.connect(), timeout=CONNECT_TIMEOUT)
        logger.info("Database connection successful.")
    except asyncio.TimeoutError:
        logger.critical(
            f"Database connection timed out after {CONNECT_TIMEOUT} seconds. "
            "The database may be overloaded or deadlocked. Exiting."
        )
        return  # Exit gracefully
    except Exception as e:
        logger.critical(f"A critical error occurred while connecting to the database: {e}", exc_info=True)
        return  # Exit gracefully

    semaphore = asyncio.Semaphore(args.concurrency)
    price_computation_lock = asyncio.Lock() # Initialize the lock here
    try:
        price_date: datetime
        if args.path:
            path_arg = args.path
            if not path_arg.is_dir():
                logger.error(f"Provided path `{path_arg}` is not a directory.")
                return

            price_date_str = path_arg.name
            try:
                price_date = datetime.strptime(price_date_str, "%Y-%m-%d")
            except ValueError:
                logger.error(f"Directory `{price_date_str}` is not a valid date in YYYY-MM-DD format.")
                return

            tasks = []
            # First, check for zip files within the date directory
            zip_files = [f.resolve() for f in path_arg.iterdir() if f.suffix.lower() == ".zip"]
            if zip_files:
                for zip_file in zip_files:
                    chain_name_from_zip = zip_file.stem
                    # Create a unique temporary directory for each zip to avoid conflicts
                    unzip_target_dir = Path(TemporaryDirectory(prefix=f"import_{chain_name_from_zip}_").name)
                    unzip_target_dir.mkdir(parents=True, exist_ok=True)

                    logger.debug(f"Extracting archive {zip_file} to {unzip_target_dir}")
                    with zipfile.ZipFile(zip_file, "r") as zip_ref:
                        zip_ref.extractall(unzip_target_dir)

                    tasks.append(_import_single_chain_data(chain_name_from_zip, unzip_target_dir, price_date, None, str(zip_file), semaphore, args.timeout, price_computation_lock))
            else:
                logger.warning(f"No zip files found in directory {path_arg}. Looking for subdirectories instead.")
                # Fallback to looking for subdirectories if no zips found
                chain_dirs = [d.resolve() for d in path_arg.iterdir() if d.is_dir()]
                if not chain_dirs:
                    logger.warning(f"No chain directories found in {path_arg}")
                    return
                for chain_dir in chain_dirs:
                    tasks.append(_import_single_chain_data(chain_dir.name, chain_dir, price_date, None, None, semaphore, args.timeout, price_computation_lock))

            if tasks:
                logger.info(f"Starting import for {len(tasks)} chains concurrently from path: {path_arg} (concurrency: {args.concurrency}, timeout: {args.timeout}s)...")
                await asyncio.gather(*tasks, return_exceptions=True) # Run tasks concurrently, collect exceptions
                logger.info("All import tasks completed (or encountered exceptions) for the provided path.")
        else:
            price_date = datetime.now()
            logger.info(f"No path provided, assuming today's date: {price_date.date()}")
            logger.info("Checking for successful crawl runs to import...")
            successful_crawl_runs = await db.import_runs.get_successful_crawl_runs_not_imported()
            if not successful_crawl_runs:
                logger.info("No new successful crawl runs found to import.")
                return

            tasks = []
            for crawl_run in successful_crawl_runs:
                crawl_run_id = crawl_run["id"]
                chain_name = crawl_run["chain_name"]
                crawl_date = crawl_run["crawl_date"]

                date_str = crawl_date.strftime("%Y-%m-%d")
                chain_zip_path = Path(f"/app/crawler_output/{date_str}/{chain_name}.zip")

                logger.info(f"Attempting to import data for chain '{chain_name}' from crawl run ID {crawl_run_id} from zip: {chain_zip_path}")

                if not chain_zip_path.is_file():
                    logger.error(f"Chain zip file not found for crawl run ID {crawl_run_id} at {chain_zip_path}. Skipping import.")
                    await db.import_runs.add_import_run(
                        chain_name=chain_name,
                        import_date=crawl_date,
                        crawl_run_id=crawl_run_id,
                        status=ImportStatus.FAILED,
                        error_message="Chain zip file not found",
                    )
                    continue

                # Use a temporary directory to handle extraction and cleanup
                unzip_target_dir = Path(TemporaryDirectory(prefix=f"import_{chain_name}_").name)
                
                logger.debug(f"Extracting archive {chain_zip_path} to {unzip_target_dir}")
                with zipfile.ZipFile(chain_zip_path, "r") as zip_ref:
                    # Assuming the zip contains the CSVs at the root
                    zip_ref.extractall(unzip_target_dir)

                tasks.append(_import_single_chain_data(chain_name, unzip_target_dir, crawl_date, crawl_run_id, str(chain_zip_path), semaphore, args.timeout, price_computation_lock))

            if tasks:
                logger.info(f"Starting import for {len(tasks)} successful crawl runs concurrently (concurrency: {args.concurrency}, timeout: {args.timeout}s)...")
                await asyncio.gather(*tasks, return_exceptions=True)
                logger.info("All automatic import tasks completed (or encountered exceptions).")
            else:
                logger.info("No automatic import tasks to run.")
    finally:
        logger.info("Closing database connection.")
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
