from dataclasses import dataclass
import os
import datetime
from typing import List, Union, Dict, Optional, Any # Import Any
import logging
from pathlib import Path
from time import time
import httpx
import json
import shutil
from psycopg2.extras import RealDictCursor
from psycopg2.extensions import connection as PgConnection, cursor as PgCursor

from service.db.models import CrawlStatus
from service.config import get_settings # Import get_settings
from service.normaliser.db_utils import get_db_connection # Import get_db_connection

API_BASE_URL = os.getenv("BASE_URL", "http://api:8000")
API_KEY = os.getenv("API_KEY") # Get API_KEY from environment variables

@dataclass
class CrawlResult:
    elapsed_time: float = 0
    n_stores: int = 0
    n_products: int = 0
    n_prices: int = 0
    n_g_prices: int = 0 # Add this line

async def get_g_products_map() -> Dict[str, Dict[str, Any]]:
    """
    Fetches all g_products from the database and returns them as a dictionary
    mapping EAN to g_product details (id, base_unit_type, variants).
    """
    conn: Optional[PgConnection] = None
    g_products_map = {}
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, ean, base_unit_type, variants
                FROM g_products;
            """)
            for row in cur.fetchall():
                g_products_map[row['ean']] = {
                    'id': row['id'],
                    'base_unit_type': row['base_unit_type'],
                    'variants': row['variants']
                }
        logger.info(f"Fetched {len(g_products_map)} g_products from the database.")
    except Exception as e:
        logger.error(f"Error fetching g_products from database: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()
    return g_products_map


async def report_crawl_status(
    chain_name: str,
    crawl_date: datetime.date,
    status: CrawlStatus,
    error_message: Optional[str] = None,
    crawl_result: Optional[CrawlResult] = None,
):
    """Reports the crawl status and metrics to the API endpoint."""
    url = f"{API_BASE_URL}/v1/crawler/status"
    payload = {
        "chain_name": chain_name,
        "crawl_date": crawl_date.isoformat(),
        "status": status.value,
        "error_message": error_message,
        "n_stores": crawl_result.n_stores if crawl_result else 0,
        "n_products": crawl_result.n_products if crawl_result else 0,
        "n_prices": crawl_result.n_prices if crawl_result else 0,
        "elapsed_time": crawl_result.elapsed_time if crawl_result else 0.0,
    }
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["X-API-Key"] = API_KEY # Add API Key to headers
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, content=json.dumps(payload), headers=headers)
            response.raise_for_status()
            logger.info(f"Successfully reported status for {chain_name}: {status.value}")
    except httpx.RequestError as e:
        logger.error(f"Failed to report status for {chain_name} to API: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"An unexpected error occurred while reporting status for {chain_name}: {e}", exc_info=True)


from crawler.store.konzum import KonzumCrawler
from crawler.store.lidl import LidlCrawler
from crawler.store.plodine import PlodineCrawler
from crawler.store.ribola import RibolaCrawler
from crawler.store.roto import RotoCrawler
from crawler.store.spar import SparCrawler
from crawler.store.studenac import StudenacCrawler
from crawler.store.tommy import TommyCrawler
from crawler.store.kaufland import KauflandCrawler
from crawler.store.eurospin import EurospinCrawler
from crawler.store.dm import DmCrawler
from crawler.store.ktc import KtcCrawler
from crawler.store.metro import MetroCrawler
from crawler.store.trgocentar import TrgocentarCrawler
from crawler.store.zabac import ZabacCrawler
from crawler.store.vrutak import VrutakCrawler
from crawler.store.ntl import NtlCrawler
from crawler.store.trgovina_krk import TrgovinaKrkCrawler
from crawler.store.lorenco import LorencoCrawler
from crawler.store.brodokomerc import BrodokomercCrawler
from crawler.store.boso import BosoCrawler


from crawler.store.output import save_chain, copy_archive_info, create_archive

logger = logging.getLogger(__name__)

CRAWLERS = {
    StudenacCrawler.CHAIN: StudenacCrawler,
    SparCrawler.CHAIN: SparCrawler,
    KonzumCrawler.CHAIN: KonzumCrawler,
    PlodineCrawler.CHAIN: PlodineCrawler,
    LidlCrawler.CHAIN: LidlCrawler,
    TommyCrawler.CHAIN: TommyCrawler,
    KauflandCrawler.CHAIN: KauflandCrawler,
    EurospinCrawler.CHAIN: EurospinCrawler,
    DmCrawler.CHAIN: DmCrawler,
    KtcCrawler.CHAIN: KtcCrawler,
    MetroCrawler.CHAIN: MetroCrawler,
    TrgocentarCrawler.CHAIN: TrgocentarCrawler,
    ZabacCrawler.CHAIN: ZabacCrawler,
    VrutakCrawler.CHAIN: VrutakCrawler,
    NtlCrawler.CHAIN: NtlCrawler,
    RibolaCrawler.CHAIN: RibolaCrawler,
    RotoCrawler.CHAIN: RotoCrawler,
    TrgovinaKrkCrawler.CHAIN: TrgovinaKrkCrawler,
    LorencoCrawler.CHAIN: LorencoCrawler,
    BrodokomercCrawler.CHAIN: BrodokomercCrawler,
    BosoCrawler.CHAIN: BosoCrawler,
    NtlCrawler.CHAIN: NtlCrawler,
    ZabacCrawler.CHAIN: ZabacCrawler,
}

DISABLED_CRAWLERS = {
    
    
}


def get_chains() -> List[str]:
    """
    Get the list of retail chains from the crawlers.

    Returns:
        List of retail chain names.
    """
    return [chain for chain in CRAWLERS.keys() if chain not in DISABLED_CRAWLERS]


def crawl_chain(chain: str, date: datetime.date, temp_chain_path: Path, output_dir: Path, g_products_map: Dict[str, Dict[str, Any]]) -> tuple[CrawlResult, Optional[Path]]:
    """
    Crawl a specific retail chain for product/pricing data, save it, and create a zip archive.

    Args:
        chain: The name of the retail chain to crawl.
        date: The date for which to fetch the product data.
        temp_chain_path: Temporary directory path where the data will be saved before zipping.
        output_dir: The directory where the final chain-specific zip file will be saved.

    Returns:
        Tuple containing:
            - CrawlResult object with crawl statistics.
            - Path to the created ZIP archive file for this chain, or None if creation failed.
    """

    crawler_class = CRAWLERS.get(chain)
    if not crawler_class:
        logger.error(f"Unknown retail chain: {chain}")
        raise ValueError(f"Unknown retail chain: {chain}") # Propagate error

    crawler = crawler_class()
    logger.info(f"[{chain}] Starting get_all_products for {date:%Y-%m-%d}")
    t0 = time()
    
    stores = crawler.get_all_products(date) # Allow exceptions to propagate

    logger.info(f"[{chain}] Finished get_all_products for {date:%Y-%m-%d}")

    if not stores:
        # Explicitly raise an error if no stores/products were retrieved
        raise ValueError(f"No stores or products retrieved for {chain} on {date}")

    store_list, product_list, price_list, g_price_list = save_chain(temp_chain_path, stores, g_products_map, date) # save_chain now returns the lists
    t1 = time()

    all_products = set()
    for store in stores:
        for product in store.items:
            all_products.add(product.product_id)

    crawl_result = CrawlResult(
        elapsed_time=t1 - t0,
        n_stores=len(stores),
        n_products=len(all_products),
        n_prices=len(price_list), # Use length of price_list
        n_g_prices=len(g_price_list), # Add n_g_prices
    )

    # Create chain-specific zip file
    try:
        os.makedirs(output_dir, exist_ok=True)
        zip_file_name = f"{chain}.zip"
        zip_file_path = output_dir / zip_file_name
        create_archive(temp_chain_path, zip_file_path)
        logger.info(f"Created archive {zip_file_path} for {chain}")
        return crawl_result, zip_file_path
    except Exception as e:
        logger.error(f"Failed to create zip archive for {chain}: {e}", exc_info=True)
        raise # Re-raise the exception to be caught by the executor


async def crawl(
    root: Path,
    date: datetime.date | None = None,
    # num_workers: int = 4,  # --- REMOVED: This parameter is no longer needed for sequential execution.
    chains: list[str] | None = None,
) -> List[Path]:
    """
    Crawl multiple retail chains for product/pricing data and save it SEQUENTIALLY.

    Args:
        root: The base directory path where the data will be saved.
        date: The date for which to fetch the product data. If None, uses today's date.
        chains: List of retail chain names to crawl. If None, crawls all available chains.

    Returns:
        List of Paths to the created ZIP archive files for each chain.
    """

    if date is None:
        date = datetime.date.today()

    logger.debug(f"Crawl date being used: {date:%Y-%m-%d}")

    # Fetch g_products data once at the beginning
    g_products_map = await get_g_products_map()
    if not g_products_map:
        logger.error("Failed to fetch g_products map. Cannot proceed with crawl.")
        return []

    # This part remains the same: check which chains actually need crawling.
    successful_runs = await get_crawl_runs_from_api(date, CrawlStatus.SUCCESS)
    successful_chains = {run["chain_name"] for run in successful_runs}
    all_available_chains = get_chains()
    
    if chains is None:
        chains_to_process = [
            chain for chain in all_available_chains if chain not in successful_chains
        ]
    else:
        chains_to_process = [
            chain for chain in chains if chain not in successful_chains
        ]

    if not chains_to_process:
        logger.info(f"All requested chains for {date:%Y-%m-%d} are already successful or no chains to process.")
        return []

    logger.info(f"Chains to process sequentially for {date:%Y-%m-%d}: {', '.join(chains_to_process)}")

    # Setup directories
    temp_base_path = root / "temp_crawls" / date.strftime("%Y-%m-%d")
    os.makedirs(temp_base_path, exist_ok=True)
    output_dir_for_date = root / date.strftime("%Y-%m-%d")
    os.makedirs(output_dir_for_date, exist_ok=True)

    results = {}
    created_zip_paths = []
    t0 = time()

    # --- REPLACED ThreadPoolExecutor with a simple for loop for sequential execution ---
    for chain in chains_to_process:
        logger.info(f"--- Starting sequential crawl for {chain} ---")

        # Report that this specific chain crawl has started
        await report_crawl_status(
            chain_name=chain,
            crawl_date=date,
            status=CrawlStatus.STARTED,
        )
        
        # Initialize variables for this specific chain's run
        crawl_result = CrawlResult()
        zip_file_path = None
        error_message = None
        status = CrawlStatus.FAILED
        
        try:
            # Prepare the temporary path for this single chain
            temp_chain_path = temp_base_path / chain
            
            # Directly call the crawl_chain function and wait for it to complete
            # Pass g_products_map to crawl_chain
            crawl_result, zip_file_path = crawl_chain(chain, date, temp_chain_path, output_dir_for_date, g_products_map)
            
            # If we get here, the crawl for this chain was successful
            results[chain] = crawl_result
            if zip_file_path:
                created_zip_paths.append(zip_file_path)
            status = CrawlStatus.SUCCESS
            logger.info(f"--- COMPLETED crawl for {chain} successfully ---")

        except Exception as exc:
            # If anything goes wrong during the crawl_chain call, handle the exception
            error_message = str(exc)
            logger.error(f"Crawl for {chain} generated an exception: {exc}", exc_info=True)
            results[chain] = CrawlResult() # Store an empty result for the failed chain
            status = CrawlStatus.FAILED
        
        finally:
            # Always report the final status (SUCCESS or FAILED) for the chain that just ran
            await report_crawl_status(
                chain_name=chain,
                crawl_date=date,
                status=status,
                error_message=error_message,
                crawl_result=crawl_result,
            )

    # This final summary part remains the same
    t1 = time()
    logger.info(f"Finished processing all chains for {date:%Y-%m-%d} in {t1 - t0:.2f}s")
    for chain, r in results.items():
        logger.info(
            f"  * {chain}: {r.n_stores} stores, {r.n_products} products, {r.n_prices} prices, {r.n_g_prices} g_prices in {r.elapsed_time:.2f}s"
        )

    if os.path.exists(temp_base_path):
        shutil.rmtree(temp_base_path)
        logger.info(f"Cleaned up temporary directory: {temp_base_path}")

    logger.info(f"Created {len(created_zip_paths)} archives in {output_dir_for_date}")
    return created_zip_paths


async def get_crawl_runs_from_api(crawl_date: datetime.date, status_filter: Union[CrawlStatus, List[CrawlStatus]]) -> List[Dict]:
    """Fetches crawl runs from the API based on date and status."""
    status_param = ""
    if isinstance(status_filter, list):
        if CrawlStatus.FAILED in status_filter or CrawlStatus.STARTED in status_filter:
            url = f"{API_BASE_URL}/v1/crawler/failed_or_started_runs/{crawl_date.isoformat()}"
        else:
            url = f"{API_BASE_URL}/v1/crawler/successful_runs/{crawl_date.isoformat()}"
    else:
        if status_filter == CrawlStatus.SUCCESS:
            url = f"{API_BASE_URL}/v1/crawler/successful_runs/{crawl_date.isoformat()}"
        elif status_filter == CrawlStatus.FAILED or status_filter == CrawlStatus.STARTED:
            url = f"{API_BASE_URL}/v1/crawler/failed_or_started_runs/{crawl_date.isoformat()}"
        else:
            logger.warning(f"Unsupported status filter for API: {status_filter.value}")
            return []

    headers = {}
    if API_KEY:
        headers["X-API-Key"] = API_KEY # Add API Key to headers

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
    except httpx.RequestError as e:
        logger.error(f"Failed to fetch crawl runs from API: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"An unexpected error occurred while fetching crawl runs: {e}", exc_info=True)
        return []
