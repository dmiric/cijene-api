from dataclasses import dataclass
import os
import datetime
from typing import List, Union, Dict, Optional
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from time import time
import httpx
import json
import shutil

from service.db.models import CrawlStatus

API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000")

@dataclass
class CrawlResult:
    elapsed_time: float = 0
    n_stores: int = 0
    n_products: int = 0
    n_prices: int = 0

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
}

DISABLED_CRAWLERS = {
    ZabacCrawler.CHAIN: ZabacCrawler,
    NtlCrawler.CHAIN: NtlCrawler,
}


def get_chains() -> List[str]:
    """
    Get the list of retail chains from the crawlers.

    Returns:
        List of retail chain names.
    """
    return [chain for chain in CRAWLERS.keys() if chain not in DISABLED_CRAWLERS]


def crawl_chain(chain: str, date: datetime.date, temp_chain_path: Path, output_dir: Path) -> tuple[CrawlResult, Optional[Path]]:
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

    save_chain(temp_chain_path, stores)
    t1 = time()

    all_products = set()
    for store in stores:
        for product in store.items:
            all_products.add(product.product_id)

    crawl_result = CrawlResult(
        elapsed_time=t1 - t0,
        n_stores=len(stores),
        n_products=len(all_products),
        n_prices=sum(len(store.items) for store in stores),
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
    num_workers: int = 4,
    chains: list[str] | None = None,
) -> List[Path]:
    """
    Crawl multiple retail chains for product/pricing data and save it.

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

    # Fetch previous crawl statuses
    successful_runs = await get_crawl_runs_from_api(date, CrawlStatus.SUCCESS)
    failed_or_started_runs = await get_crawl_runs_from_api(date, [CrawlStatus.FAILED, CrawlStatus.STARTED])

    successful_chains = {run["chain_name"] for run in successful_runs}
    failed_or_started_chains = {run["chain_name"] for run in failed_or_started_runs}

    logger.debug(f"Successful chains for {date:%Y-%m-%d}: {successful_chains}")
    logger.debug(f"Failed or started chains for {date:%Y-%m-%d}: {failed_or_started_chains}")

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

    logger.info(f"Chains to process for {date:%Y-%m-%d}: {', '.join(chains_to_process)}")
    # The 'chains' variable here should now refer to the filtered list for processing
    # No need to reassign 'chains = chains_to_process' as it's used below.

    temp_base_path = root / "temp_crawls" / date.strftime("%Y-%m-%d")
    os.makedirs(temp_base_path, exist_ok=True)

    output_dir_for_date = root / date.strftime("%Y-%m-%d")
    os.makedirs(output_dir_for_date, exist_ok=True)

    results = {}
    created_zip_paths = []
    t0 = time()

    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        future_to_chain = {}
        for chain in chains_to_process: # Changed from 'chains' to 'chains_to_process'
            logger.info(f"Submitting crawl for {chain} on {date:%Y-%m-%d}")
            await report_crawl_status(
                chain_name=chain,
                crawl_date=date,
                status=CrawlStatus.STARTED,
            )
            temp_chain_path = temp_base_path / chain
            future = executor.submit(crawl_chain, chain, date, temp_chain_path, output_dir_for_date)
            future_to_chain[future] = chain

        for future in as_completed(future_to_chain):
            chain = future_to_chain[future]
            result = CrawlResult()
            zip_file_path = None
            error_message = None
            status = CrawlStatus.FAILED

            try:
                result, zip_file_path = future.result()
                results[chain] = result
                if zip_file_path:
                    created_zip_paths.append(zip_file_path)
                status = CrawlStatus.SUCCESS
                logger.info(f"COMPLETED crawl for {chain}")
            except Exception as exc:
                error_message = str(exc)
                logger.error(f"Crawl for {chain} generated an exception: {exc}", exc_info=True)
                results[chain] = CrawlResult()
                status = CrawlStatus.FAILED
            finally:
                await report_crawl_status(
                    chain_name=chain,
                    crawl_date=date,
                    status=status,
                    error_message=error_message,
                    crawl_result=result,
                )

    t1 = time()

    logger.info(f"Finished processing chains for {date:%Y-%m-%d} in {t1 - t0:.2f}s")
    for chain, r in results.items():
        logger.info(
            f"  * {chain}: {r.n_stores} stores, {r.n_products} products, {r.n_prices} prices in {r.elapsed_time:.2f}s"
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

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
    except httpx.RequestError as e:
        logger.error(f"Failed to fetch crawl runs from API: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"An unexpected error occurred while fetching crawl runs: {e}", exc_info=True)
        return []
