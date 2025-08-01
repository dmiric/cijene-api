from __future__ import annotations

import datetime
import logging
import re
from typing import NamedTuple
from urllib.parse import urlparse
from csv import DictReader # Added DictReader import

from bs4 import BeautifulSoup

from crawler.store.base import BaseCrawler
from crawler.store.models import Product, Store

logger = logging.getLogger(__name__)


class RotoCrawler(BaseCrawler):
    """
    Crawler for Roto store prices.
    https://www.rotodinamic.hr/cjenici/
    """

    CHAIN = "roto"
    BASE_URL = "https://www.rotodinamic.hr"
    INDEX_URL = f"{BASE_URL}/cjenici/"

    ANCHOR_PRICE_COLUMN = "sidrena cijena na 2.5.2025."
    PRICE_MAP = {
        "price": ("MPC", True),
        "unit_price": ("Cijena za jedinicu mjere", True),
        "special_price": ("MPC za vrijeme posebnog oblika prodaje", False),
        "best_price_30": ("Najniža cijena u posljednjih 30 dana", False),
        "anchor_price": (ANCHOR_PRICE_COLUMN, False),
    }

    FIELD_MAP = {
        "product": ("Naziv artikla", False),
        "product_id": ("Šifra artikla", True),
        "brand": ("BRAND", False),
        "quantity": ("neto količina", False),
        "unit": ("Jedinica mjere", False),
        "barcode": ("Barkod", False),
        "category": ("Kategorija proizvoda", False),
    }

    def get_csv_url(self, soup: BeautifulSoup, date: datetime.date) -> str:
        anchors = soup.select("a.cjenici-table-row")
        hr_date = date.strftime("%d.%m.%Y")

        for anchor in anchors:
            url = anchor.attrs["href"]
            assert isinstance(url, str)
            url_date = urlparse(url).path.split(",")[-2].strip()
            if url_date == hr_date:
                return url

        raise ValueError(f"No price list found for {date}")

    def read_csv(self, text: str, delimiter: str = ",") -> DictReader:
        lines = text.splitlines()
        if len(lines) > 1 and not any(c.isalnum() for c in lines[0]):
            # Check if the second line looks like a header
            second_line = lines[1]
            if "Šifra artikla" in second_line and "Naziv artikla" in second_line:
                logger.debug("Skipping first empty/non-alphanumeric line and using second line as header in RotoCrawler.")
                lines = lines[1:]
            else:
                logger.debug("First line is empty/non-alphanumeric, but second line does not look like a header. Processing all lines in RotoCrawler.")
        return DictReader(lines, delimiter=delimiter)  # type: ignore

    def get_all_products(self, date: datetime.date) -> list[Store]:
        html_content = self.fetch_text(self.INDEX_URL)
        soup = BeautifulSoup(html_content, "html.parser")
        csv_url = self.get_csv_url(soup, date)
        addresses = self.parse_store_addresses(soup)

        # Roto has the same prices for all stores
        products = self.get_store_products(csv_url)
        return list(self.get_stores(csv_url, products, addresses))

    def get_products_from_hardcoded_csv(self) -> list[Product]:
        csv_content = """
;;;;;;;;;;;
Šifra artikla;Naziv artikla;Kategorija proizvoda;BRAND;Barkod;neto količina;Jedinica mjere;MPC;Cijena za jedinicu mjere;MPC za vrijeme posebnog oblika prodaje;Najniža cijena u posljednjih 30 dana;sidrena cijena na 2.5.2025.
022243;KRAJANČIĆ POŠIP INTRADA  0,75 (6)*;VINO;KRAJANČIĆ;3859890966012;0,7500;KOM;16,69;22,25;;16,69;16,69
640280;KRAJANČIĆ JEŽINA POŠIP 0,75 (6)*;VINO;KORTA KATARINA;3859891637355;0,7500;KOM;12,21;16,28;;12,21;12,21
9144;MATIĆ MALVAZIJA ISTARSKA AFRODITA 0,75 (6)*;VINO;MATIĆ VINA;3859891681099;0,7500;KOM;25,09;33,45;;25,09;25,09
9333;MATIĆ MALVAZIJA ISTARSKA CONCEPT 0,75 (12)*;VINO;MATIĆ VINA;2000000039800;0,7500;KOM;8,84;11,79;;8,84;8,84
9334;MATIĆ MERLOT CONCEPT 0,75 (12)*;VINO;MATIĆ VINA;2000000039817;0,7500;KOM;8,84;11,79;;8,84;8,84
2941;VALANDOVO VENUS CRNI 1,0 (6)*;VINO;VIZBA VALANDOVO;3859892866884;1,0000;KOM;4,96;4,96;;4,96;4,96
2971;VALANDOVO VRANAC BAG IN BOX 3,0 (6);VINO;VIZBA VALANDOVO;5310122000020;3,0000;KOM;10,31;3,44;;10,31;10,31
2972;VALANDOVO VRANAC VINEA BAG IN BOX 5,0 (4);VINO;VIZBA VALANDOVO;5310122000112;5,0000;KOM;14,24;2,85;;14,24;14,24
3073;VALANDOVO VRANAC VINEA 1,0 (6)*;VINO;VIZBA VALANDOVO;3859892866938;1,0000;KOM;4,73;4,73;;4,96;4,96
7813;SPECIAL SELECTION VRANAC 0,75 (6)*;VINO;VIZBA VALANDOVO;5310122001553;0,7500;KOM;5,79;7,72;;5,79;5,79
7814;SPECIAL SELECTION CABERNET SAUVIGNON 0,75 (6)*;VINO;VIZBA VALANDOVO;5310122001683;0,7500;KOM;5,79;7,72;;5,79;5,79
7815;SPECIAL SELECTION MERLOT 0,75 (6)*;VINO;VIZBA VALANDOVO;5310122001690;0,7500;KOM;5,79;7,72;;5,79;5,79
022580;ASTORIA CORDERIE PROSECCO SUPERIORE D.O.C.G. 0,75(6)*;VINO;ASTORIA;8003905042351;0,7500;KOM;11,01;14,68;;11,59;11,59
022581;ASTORIA CUVEE LOUNGE PROSECCO D.O.C. 0,75 (6)*;VINO;ASTORIA;8003905096590;0,7500;KOM;8,84;11,79;;8,84;8,84
022642;ASTORIA MILLESIMATO PROSECCO D.O.C.G. SUPERIORE 0,75 (6)*;VINO;ASTORIA;8003905101454;0,7500;KOM;14,84;19,79;;14,84;14,84
022767;ASTORIA LOUNGE 0,75 (6)*;VINO;ASTORIA;8003905104080;0,7500;KOM;6,75;9,00;;4,93;7,10
"""
        return self.parse_csv(csv_content, delimiter=";")

    def get_store_products(self, csv_url: str) -> list[Product]:
        try:
            content = self.fetch_text(csv_url, encodings=["cp1250"])
            return self.parse_csv(content, delimiter=";")
        except Exception:
            logger.exception(f"Failed to get store prices from {csv_url}")
            return []

    def get_stores(
        self,
        csv_url: str,
        products: list[Product],
        addresses: dict[str, Address],
    ):
        # Extract store ids and names from the CSV file
        matches = []
        parts = urlparse(csv_url).path.split(",")
        for part in parts:
            part = part.strip()
            if re.match("D[0-9]+ ", part):
                store_id, name = part.split(" ")
                matches.append((store_id, name))

        # Ideally the count will match the addresses extracted from the web page
        if len(matches) != len(addresses):
            logger.warning(
                f"Store count mismatch: found {len(matches)} stores in CSV name and {len(addresses)} stores on the roto web page."
            )

        for store_id, name in matches:
            if name in addresses:
                street_address, zipcode, city = addresses[name]
            else:
                street_address, zipcode, city = "", "", ""
                logger.warning(f"Unable to find address for {store_id} {name}")

            yield Store(
                chain=self.CHAIN,
                store_type="Cash & Carry",
                store_id=store_id,
                name=f"Cash & Carry {name}",
                street_address=street_address,
                zipcode=zipcode,
                city=city,
                items=products,
            )

    def parse_store_addresses(self, soup: BeautifulSoup) -> dict[str, Address]:
        """Returns store address indexed by store name"""
        addresses = {}

        spans = soup.select(".container > div.mBottom50 > p > span.bold")
        for span in spans:
            name = span.text
            assert span.parent is not None
            _, address = span.parent
            assert isinstance(address, str)
            street_address, zipcode_city = address.strip(" -").split(", ")
            zipcode, city = zipcode_city.split(" ", maxsplit=1)

            # Remove unwanted address prefix
            to_strip = "Jankomir- "
            if street_address.startswith(to_strip):
                street_address = street_address[len(to_strip) :]

            if name in addresses:
                logger.warning(f"Duplicate store: {name}")

            addresses[name] = Address(street_address, zipcode, city)

        return addresses


class Address(NamedTuple):
    street_address: str
    zipcode: str
    city: str


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG) # Changed to DEBUG for more detailed logs
    crawler = RotoCrawler()
    # Use the hardcoded CSV for testing
    products = crawler.get_products_from_hardcoded_csv()
    from pprint import pp

    pp(products[0])
    logger.info(f"Successfully parsed {len(products)} products from hardcoded CSV.")
