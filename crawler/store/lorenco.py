import datetime
import logging
from typing import Any, List
from crawler.store.models import Store
from .base import BaseCrawler

logger = logging.getLogger(__name__)

class LorencoCrawler(BaseCrawler):
    """Crawler for Lorenco store prices."""
    CHAIN = "lorenco"
    BASE_URL = "https://lorenco.hr"
    # Lorenco has global prices, not per-store prices
    STORE_ID = "all"
    STORE_NAME = "Lorenco"

    # Map CSV columns to price fields
    PRICE_MAP = {
        "unit_price": ("MpcJmj", False),
        "price": ("MPC", False),
        "anchor_price": ("CijenaSid", False),
    }

    # Map CSV columns to other fields
    FIELD_MAP = {
        "product": ("Naziv", True),
        "barcode": ("Barkod", True),
        "unit": ("JMjere", False),
    }

    def generate_csv_url(self, date: datetime.date) -> str:
        """
        Generate the CSV URL for a specific date.

        Args:
            date: The date to generate URL for

        Returns:
            URL for the CSV file
        """
        # Format: https://lorenco.hr/wp-content/uploads/YYYY/MM/Cijenik-DD.MM.YYYY.csv
        year = date.year
        month = f"{date.month:02d}"
        day = f"{date.day:02d}"
        formatted_date = f"{day}.{month}.{year}"
        url = f"{self.BASE_URL}/wp-content/uploads/{year}/{month}/Cijenik-{formatted_date}.csv"
        return url

    def fix_product_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Override base class method to handle missing fields specific to Lorenco.
        """
        # Set default values for fields not available in Lorenco CSV
        data["product_id"] = data["barcode"]
        data["brand"] = ""
        data["category"] = ""
        data["quantity"] = ""
        # Call parent method to apply common fixups
        return super().fix_product_data(data)

    def get_all_products(self, date: datetime.date) -> List[Store]:
        """
        Main method to fetch and parse all product and price info.

        Args:
            date: The date to search for in the price list.

        Returns:
            List with a single Store object containing all products.

        Raises:
            ValueError: If no price list is found for the given date.
        """
        csv_url = self.generate_csv_url(date)
        logger.info(f"Fetching CSV from: {csv_url}")

        # Fetch CSV content with proper encoding
        try:
            csv_content = self.fetch_text(csv_url, encodings=["windows-1250"])
        except Exception as e:
            logger.error(f"Failed to fetch CSV from {csv_url}: {e}")
            raise ValueError(f"No price list found for date {date}: {e}")

        if not csv_content:
            logger.warning(f"No content found at {csv_url}")
            raise ValueError(f"No price list found for date {date}")

        # Parse CSV data using base class method
        products = self.parse_csv(csv_content, delimiter=";")

        if not products:
            logger.warning(f"No products found for date {date}")
            return []

        # Create a global store
        store = Store(
            chain=self.CHAIN,
            store_type="store",
            store_id=self.STORE_ID,
            name=self.STORE_NAME,
            street_address="",
            zipcode="",
            city="",
            items=products,
        )
        return [store]

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    crawler = LorencoCrawler()
    stores = crawler.crawl(datetime.date.today())
    print(stores[0])
    if stores[0].items:
        print(stores[0].items[0])
