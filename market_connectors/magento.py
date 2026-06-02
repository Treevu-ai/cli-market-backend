"""
market_connectors/magento.py — Magento / Adobe Commerce connector.

Uses Magento's REST API (/rest/V1/products).
Store config: base (domain), optional store_code (e.g. "default").
"""

import httpx
from .base import BaseConnector, clean_name


def _magento_headers(store_config: dict) -> dict[str, str]:
    token = store_config.get("magento_token") or store_config.get("integration_token", "")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


class MagentoConnector(BaseConnector):
    platform = "magento"

    async def search(self, store_config: dict, term: str,
                     page: int = 1, limit: int = 20) -> list[dict]:
        base = store_config["base"]
        store_code = store_config.get("store_code", "default")
        urls = [
            f"{base}/rest/{store_code}/V1/products",
            f"{base}/rest/V1/products",
        ]
        params = {
            "searchCriteria[filterGroups][0][filters][0][field]": "name",
            "searchCriteria[filterGroups][0][filters][0][value]": f"%{term}%",
            "searchCriteria[filterGroups][0][filters][0][conditionType]": "like",
            "searchCriteria[pageSize]": str(limit),
            "searchCriteria[currentPage]": str(page),
        }
        headers = _magento_headers(store_config)
        async with httpx.AsyncClient(timeout=12.0) as client:
            for url in urls:
                try:
                    resp = await client.get(url, params=params, headers=headers)
                    if resp.status_code == 200:
                        return resp.json().get("items", [])
                except Exception:
                    continue
            return []

    def normalize(self, raw: dict, store_key: str, store_config: dict) -> dict:
        currency = store_config["currency"]
        name = raw.get("name", "")
        product_id = str(raw.get("id", ""))
        custom = {a.get("attribute_code",""): a.get("value","") for a in raw.get("custom_attributes", [])}
        brand = custom.get("brand", custom.get("manufacturer", "—"))
        price_val = float(raw.get("price", 0) or 0)
        special = custom.get("special_price")
        list_price_val = price_val
        if special:
            try:
                s = float(special)
                if s < price_val:
                    list_price_val = price_val
                    price_val = s
            except (ValueError, TypeError):
                pass
        stock = 1 if raw.get("status") == 1 else 0
        ext = raw.get("extension_attributes", {})
        if "stock_item" in ext:
            stock = ext["stock_item"].get("qty", stock)
        discount = round((1 - price_val / list_price_val) * 100) if list_price_val > price_val > 0 else None
        url_key = custom.get("url_key", "")
        product_url = f"{store_config['base']}/{url_key}.html" if url_key else ""
        return {
            "id": product_id, "product_id": product_id,
            "name": clean_name(name), "brand": brand or "—",
            "category": custom.get("category", ""),
            "price": round(price_val, 2), "list_price": round(list_price_val, 2),
            "discount": discount, "stock": int(stock),
            "store": store_key, "store_name": store_config["name"],
            "currency": currency, "url": product_url,
        }

    async def categories(self, store_config: dict) -> list[dict]:
        base = store_config["base"]
        store_code = store_config.get("store_code", "default")
        urls = [f"{base}/rest/{store_code}/V1/categories", f"{base}/rest/V1/categories"]
        headers = _magento_headers(store_config)
        async with httpx.AsyncClient(timeout=8.0) as client:
            for url in urls:
                try:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 200:
                        return resp.json()
                except Exception:
                    continue
            return []
