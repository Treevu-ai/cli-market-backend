"""
market_connectors/base.py — Abstract connector interface.

Every e-commerce platform connector implements this protocol.
The rest of the system never knows which platform a store runs on.
"""

from abc import ABC, abstractmethod
from typing import Any


class BaseConnector(ABC):
    platform: str = ""

    @abstractmethod
    async def search(self, store_config: dict, term: str,
                     page: int = 1, limit: int = 20) -> list[dict]:
        """Search products. Returns raw platform-specific dicts."""

    @abstractmethod
    def normalize(self, raw: dict, store_key: str, store_config: dict) -> dict:
        """Convert raw product into unified schema:
        {id, product_id, name, brand, category, price, list_price,
         discount, stock, store, store_name, currency, url, line, line_name}"""

    @abstractmethod
    async def categories(self, store_config: dict) -> list[dict]:
        """Return category tree."""


def parse_price(price: Any) -> float:
    try: return float(price or 0)
    except (ValueError, TypeError): return 0.0

def clean_name(name: str) -> str:
    return name.replace("-", " ")
