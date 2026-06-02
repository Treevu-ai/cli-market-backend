"""
market_connectors/shopify.py — Shopify connector.

Uses Shopify's public products.json endpoint (legacy, but still exposed
by many stores) or Storefront API when a token is configured.

Store config requires: base (myshopify.com domain or custom domain)
Optional: storefront_token for Storefront API access.
"""

import httpx
from .base import BaseConnector, parse_price, clean_name


class ShopifyConnector(BaseConnector):
    platform = "shopify"

    async def search(self, store_config: dict, term: str,
                     page: int = 1, limit: int = 20) -> list[dict]:
        """
        Try Storefront API first if token is available, else use public
        products.json endpoint.
        """
        base = store_config["base"]
        token = store_config.get("storefront_token", "")

        if token:
            return await self._search_storefront(base, token, term, limit)

        # Public legacy endpoint
        url = f"{base}/products.json"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params={"q": term, "limit": str(limit), "page": str(page)})
            resp.raise_for_status()
            data = resp.json()
            return data.get("products", [])

    async def _search_storefront(self, base: str, token: str,
                                  term: str, limit: int) -> list[dict]:
        """Search via Shopify Storefront GraphQL API."""
        query = """
        query($q: String!, $n: Int!) {
          products(first: $n, query: $q) {
            edges {
              node {
                id
                title
                handle
                vendor
                productType
                variants(first: 1) {
                  edges {
                    node {
                      price { amount currencyCode }
                      compareAtPrice { amount }
                      availableForSale
                    }
                  }
                }
              }
            }
          }
        }
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{base}/api/2024-01/graphql.json",
                json={"query": query, "variables": {"q": term, "n": limit}},
                headers={"X-Shopify-Storefront-Access-Token": token}
            )
            resp.raise_for_status()
            data = resp.json()
            return [e["node"] for e in
                    data.get("data", {}).get("products", {}).get("edges", [])]

    def normalize(self, raw: dict, store_key: str, store_config: dict) -> dict:
        currency = store_config["currency"]

        # Shopify product structure varies: products.json vs Storefront API
        title = raw.get("title", raw.get("name", ""))
        vendor = raw.get("vendor", raw.get("brand", "—"))
        product_type = raw.get("product_type", raw.get("productType", ""))
        handle = raw.get("handle", "")
        product_id = str(raw.get("id", ""))

        # Variants
        variants = raw.get("variants", [])
        if not variants and "variants" in raw:
            pass  # Storefront API wraps differently
        # Storefront API format
        v_edges = raw.get("variants", {}).get("edges", [])
        if v_edges:
            v_node = v_edges[0].get("node", {})
            price_str = v_node.get("price", {}).get("amount", "0")
            compare_str = v_node.get("compareAtPrice")
            compare_str = compare_str.get("amount", "0") if compare_str else "0"
            price = parse_price(price_str)
            list_price = parse_price(compare_str) if parse_price(compare_str) > price else price
            stock = 1 if v_node.get("availableForSale") else 0
        else:
            variant = variants[0] if variants else {}
            price = parse_price(variant.get("price", "0"))
            list_price = parse_price(variant.get("compare_at_price", "0") or price)
            stock = variant.get("inventory_quantity", 0)

        discount = round((1 - price / list_price) * 100) if list_price > price > 0 else None

        return {
            "id": product_id,
            "product_id": product_id,
            "name": clean_name(title),
            "brand": vendor or product_type or "—",
            "category": product_type,
            "price": price,
            "list_price": list_price,
            "discount": discount,
            "stock": stock,
            "store": store_key,
            "store_name": store_config["name"],
            "currency": currency,
            "url": f"{store_config['base']}/products/{handle}" if handle else "",
        }

    async def categories(self, store_config: dict) -> list[dict]:
        """Shopify doesn't have a public category tree — return empty."""
        return []
