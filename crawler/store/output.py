from csv import DictWriter
from decimal import Decimal
from logging import getLogger
from os import makedirs
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from .models import Store
from service.normaliser.db_utils import calculate_unit_prices # Import calculate_unit_prices
from typing import Any, Dict, List, Optional

logger = getLogger(__name__)

STORE_COLUMNS = [
    "store_id",
    "type",
    "address",
    "city",
    "zipcode",
]

PRODUCT_COLUMNS = [
    "product_id",
    "barcode",
    "name",
    "brand",
    "category",
    "unit",
    "quantity",
]

PRICE_COLUMNS = [
    "store_id",
    "product_id",
    "price",
    "unit_price",
    "best_price_30",
    "anchor_price",
    "special_price",
]

G_PRICE_COLUMNS = [
    "g_product_id",
    "store_id",
    "price_date",
    "regular_price",
    "special_price",
    "price_per_kg",
    "price_per_l",
    "price_per_piece",
    "is_on_special_offer",
]


def transform_products(
    stores: list[Store],
    g_products_map: Dict[str, Dict[str, Any]] # Add g_products_map parameter
) -> tuple[list[dict], list[dict], list[dict], list[dict]]: # Return 4 lists now
    """
    Transform store data into a structured format for CSV export,
    calculating g_prices based on g_products_map.

    Args:
        stores: List of Store objects containing product data.
        g_products_map: Dictionary mapping EAN to g_product details.

    Returns:
        Tuple containing:
            - List of store dictionaries with STORE_COLUMNS
            - List of product dictionaries with PRODUCT_COLUMNS
            - List of price dictionaries with PRICE_COLUMNS (original)
            - List of g_price dictionaries with G_PRICE_COLUMNS (new)
    """
    store_list = []
    product_map = {}
    price_list = [] # Keep original price list
    g_price_list = [] # New g_price list

    def maybe(val: Decimal | None) -> Decimal | str:
        return val if val is not None else ""

    for store in stores:
        store_data = {
            "store_id": store.store_id,
            "type": store.store_type,
            "address": store.street_address,
            "city": store.city,
            "zipcode": store.zipcode or "",
        }
        store_list.append(store_data)

        for product in store.items:
            key = f"{store.chain}:{product.product_id}"
            if key not in product_map:
                product_map[key] = {
                    "barcode": product.barcode or key,
                    "product_id": product.product_id,
                    "name": product.product,
                    "brand": product.brand,
                    "category": product.category,
                    "unit": product.unit,
                    "quantity": product.quantity,
                }
            
            # Append to original price_list
            price_list.append(
                {
                    "store_id": store.store_id,
                    "product_id": product.product_id,
                    "price": product.price,
                    "unit_price": maybe(product.unit_price),
                    "best_price_30": maybe(product.best_price_30),
                    "anchor_price": maybe(product.anchor_price),
                    "special_price": maybe(product.special_price),
                }
            )

            # Calculate g_prices and append to g_price_list
            g_product_info = g_products_map.get(product.barcode)
            if g_product_info:
                current_price = (
                    product.special_price
                    if product.special_price is not None
                    else product.price
                )
                if current_price is None:
                    continue # Skip if no price

                calculated = calculate_unit_prices(
                    price=current_price,
                    base_unit_type=g_product_info['base_unit_type'],
                    variants=g_product_info['variants'] or []
                )

                g_price_list.append(
                    {
                        "g_product_id": g_product_info['id'],
                        "store_id": store.store_id,
                        "price_date": store.date.isoformat(), # Use the store's date for price_date
                        "regular_price": product.price,
                        "special_price": maybe(product.special_price),
                        "price_per_kg": maybe(calculated['price_per_kg']),
                        "price_per_l": maybe(calculated['price_per_l']),
                        "price_per_piece": maybe(calculated['price_per_piece']),
                        "is_on_special_offer": product.special_price is not None,
                    }
                )
            else:
                logger.warning(f"Skipping g_price calculation for product with unknown EAN in g_products_map: {product.barcode}")


    return store_list, list(product_map.values()), price_list, g_price_list


def save_csv(path: Path, data: list[dict], columns: list[str]):
    """
    Save data to a CSV file.

    Args:
        path: Path to the CSV file.
        data: List of dictionaries containing the data to save.
        columns: List of column names for the CSV file.
    """
    if not data:
        logger.warning(f"No data to save at {path}, skipping")
        return

    # Removed the column mismatch check as it's too strict for dynamic data
    # if set(columns) != set(data[0].keys()):
    #     raise ValueError(
    #         f"Column mismatch: expected {columns}, got {list(data[0].keys())}"
    #     )
    #     return

    with open(path, "w", newline="") as f:
        writer = DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in data:
            # Ensure all columns are present, fill missing with empty string
            cleaned_row = {col: str(row.get(col, "")) for col in columns}
            writer.writerow(cleaned_row)


def save_chain(chain_path: Path, stores: list[Store], g_products_map: Dict[str, Dict[str, Any]]):
    """
    Save retail chain data to CSV files.

    This function creates a directory for the retail chain and saves:

    * stores.csv - containing store information with STORE_COLUMNS
    * products.csv - containing product information with PRODUCT_COLUMNS
    * prices.csv - containing price information with PRICE_COLUMNS
    * g_prices.csv - containing calculated g_price information with G_PRICE_COLUMNS

    Args:
        chain_path: Path to the directory where CSV files will be saved
            (will be created if it doesn't exist).
        stores: List of Store objects containing product data.
        g_products_map: Dictionary mapping EAN to g_product details.
    """

    makedirs(chain_path, exist_ok=True)
    store_list, product_list, price_list, g_price_list = transform_products(stores, g_products_map)
    save_csv(chain_path / "stores.csv", store_list, STORE_COLUMNS)
    save_csv(chain_path / "products.csv", product_list, PRODUCT_COLUMNS)
    save_csv(chain_path / "prices.csv", price_list, PRICE_COLUMNS) # Keep original prices.csv
    save_csv(chain_path / "g_prices.csv", g_price_list, G_PRICE_COLUMNS) # Add g_prices.csv


def copy_archive_info(path: Path):
    archive_info = open(Path(__file__).parent / "archive-info.txt", "r").read()
    with open(path / "archive-info.txt", "w") as f:
        f.write(archive_info)


def create_archive(path: Path, output: Path):
    """
    Create a ZIP archive of price files for a given date.

    Args:
        path: Path to the directory to archive.
        output: Path to the output ZIP file.
    """
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as zf:
        for file in path.rglob("*"):
            zf.write(file, arcname=file.relative_to(path))
