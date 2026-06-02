"""
market_connectors/vtex.py — VTEX connector.

Implements BaseConnector for VTEX public catalog API.
Supports standard (/api/) and VTEX IO (/io/api/) path auto-detection.
Includes self-healing fallback with Playwright for Cloudflare-blocked stores.
"""

import asyncio
import json
import logging
import time
from collections import defaultdict
from typing import Optional

import httpx
from .base import BaseConnector, parse_price, clean_name

# Optional Playwright import for fallback mechanism
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

logger = logging.getLogger(__name__)

PAGE_SIZE = 20
CATALOG_PAGE_SIZE = 50

_VTEX_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def _vtex_headers(store_config: dict) -> dict[str, str]:
    """Build VTEX app-key/app-token auth headers when credentials are present."""
    key = store_config.get("vtex_app_key", "")
    token = store_config.get("vtex_app_token", "")
    if key and token:
        return {
            "X-VTEX-API-AppKey": key,
            "X-VTEX-API-AppToken": token,
        }
    return {}


def _client_headers(store_config: dict) -> dict[str, str]:
    """Default request headers (User-Agent + optional VTEX auth)."""
    headers = {"User-Agent": _VTEX_UA}
    headers.update(_vtex_headers(store_config))
    return headers

# Global cookie cache with TTL (per-store thread-safe management)
# Structure: {"store_key": {"cookies": [...], "expires_at": timestamp}}
_STORE_COOKIE_CACHE = {}
_STORE_LOCKS = defaultdict(asyncio.Lock)
_COOKIE_CACHE_TTL = 600  # 10 minutes


class VtexConnector(BaseConnector):
    platform = "vtex"

    def _get_cached_cookies(self, store_key: str) -> Optional[list]:
        """Retrieve cached cookies if valid (not expired)."""
        cache_entry = _STORE_COOKIE_CACHE.get(store_key)
        if cache_entry:
            if time.time() < cache_entry["expires_at"]:
                logger.debug(f"Cookie cache hit for {store_key}")
                return cache_entry["cookies"]
            else:
                logger.debug(f"Cookie cache expired for {store_key}, will refresh")
                del _STORE_COOKIE_CACHE[store_key]
        return None

    def _set_cached_cookies(self, store_key: str, cookies: list) -> None:
        """Store cookies with TTL expiration."""
        _STORE_COOKIE_CACHE[store_key] = {
            "cookies": cookies,
            "expires_at": time.time() + _COOKIE_CACHE_TTL
        }
        logger.debug(f"Cached cookies for {store_key} (expires in {_COOKIE_CACHE_TTL}s)")

    async def _search_playwright_fallback(self, url: str, params: dict, store_key: str, store_config: dict) -> list[dict]:
        """
        Self-healing fallback: Use headless Playwright to resolve Cloudflare and extract JSON.
        Caches resulting cookies for future httpx calls.
        """
        if not PLAYWRIGHT_AVAILABLE:
            logger.error(f"Playwright fallback requested for {store_key} but not installed. Install with: pip install playwright")
            raise RuntimeError("Playwright not installed. Install with: pip install 'cli-market[playwright]'")

        store_name = store_config.get("name", store_key)
        logger.info(f"Initiating Playwright fallback for {store_name} (store_key={store_key})")

        async with async_playwright() as p:
            browser = None
            try:
                # Launch headless browser with security options
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",  # Reduce memory usage
                        "--disable-blink-features=AutomationControlled",
                    ]
                )
                logger.debug(f"Playwright browser launched for {store_key}")

                # Create context with realistic user-agent
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    viewport={"width": 1280, "height": 720}
                )

                # Inject cached cookies if available (previous cf_clearance, etc.)
                cached_cookies = self._get_cached_cookies(store_key)
                if cached_cookies:
                    try:
                        await context.add_cookies(cached_cookies)
                        logger.debug(f"Injected {len(cached_cookies)} cached cookies for {store_key}")
                    except Exception as e:
                        logger.warning(f"Failed to inject cached cookies for {store_key}: {e}")

                page = await context.new_page()

                # Build final URL with query parameters
                query_parts = [f"{k}={v}" for k, v in params.items()]
                query_string = "&".join(query_parts)
                final_url = f"{url}?{query_string}" if query_string else url

                logger.debug(f"Navigating to {final_url}")

                # Navigate with timeout - wait for DOM load + stabilization
                try:
                    response = await page.goto(
                        final_url,
                        wait_until="domcontentloaded",  # Faster than networkidle
                        timeout=15000  # 15 seconds
                    )
                    logger.debug(f"Page response status: {response.status if response else 'None'}")
                except asyncio.TimeoutError:
                    logger.warning(f"Playwright navigation timeout for {store_key}, attempting to extract content anyway")

                # Wait briefly for body to be available
                try:
                    await page.wait_for_selector("body", timeout=2000)
                except asyncio.TimeoutError:
                    logger.warning(f"Body selector not found for {store_key}")

                # Extract page content (should be JSON for API endpoints)
                content = await page.evaluate("() => document.body.textContent || document.documentElement.innerHTML")

                logger.debug(f"Extracted {len(content)} chars from {store_key}")

                # Retrieve cookies from resolved session (including cf_clearance if Cloudflare was involved)
                cookies = await context.cookies()
                self._set_cached_cookies(store_key, cookies)

                # Close context and browser
                await context.close()

                # Parse JSON from extracted content
                try:
                    data = json.loads(content)
                    if isinstance(data, list):
                        logger.info(f"Playwright fallback succeeded for {store_key}, extracted {len(data)} items")
                        return data
                    else:
                        logger.warning(f"Playwright resolved page but returned non-list JSON for {store_key}")
                        return []
                except json.JSONDecodeError as e:
                    logger.error(f"Playwright resolved page but content is not valid JSON for {store_key}: {str(e)[:200]}")
                    # Try to extract from a script tag as fallback
                    script_content = await page.evaluate(
                        "() => document.querySelector('script')?.textContent || ''"
                    )
                    try:
                        data = json.loads(script_content)
                        if isinstance(data, list):
                            logger.info(f"Extracted JSON from script tag for {store_key}")
                            return data
                    except:
                        pass
                    return []

            except Exception as e:
                logger.error(f"Playwright fallback failed for {store_key}: {type(e).__name__}: {e}")
                raise

            finally:
                if browser:
                    try:
                        await browser.close()
                        logger.debug(f"Browser closed for {store_key}")
                    except Exception as e:
                        logger.warning(f"Error closing browser for {store_key}: {e}")

    async def _detect_io(self, store_config: dict) -> str:
        base = store_config["base"]
        headers = _client_headers(store_config)
        async with httpx.AsyncClient(timeout=8.0) as c:
            try:
                r = await c.get(f"{base}/api/catalog_system/pub/category/tree/10", headers=headers)
                ct = r.headers.get("content-type", "")
                if r.status_code == 200 and "json" in ct:
                    store_config["_io_path"] = ""
                    return ""
            except Exception:
                pass
            try:
                r = await c.get(f"{base}/io/api/catalog_system/pub/category/tree/10", headers=headers)
                ct = r.headers.get("content-type", "")
                if r.status_code == 200 and "json" in ct:
                    store_config["_io_path"] = "/io"
                    return "/io"
            except Exception:
                pass
        store_config["_io_path"] = ""
        return ""

    def _api_url(self, store_config: dict, path: str) -> str:
        base = store_config["base"]
        io = store_config.get("_io_path")
        return f"{base}{io or ''}/api/{path}"

    async def search(self, store_config: dict, term: str,
                     page: int = 1, limit: int = PAGE_SIZE) -> list[dict]:
        """Search products with fallback mechanism for blocked stores."""
        if store_config.get("_io_path") is None:
            await self._detect_io(store_config)

        store_key = store_config.get("_store_key", store_config.get("name", "unknown"))
        
        # Acquire per-store lock to avoid concurrent Playwright sessions
        async with _STORE_LOCKS[store_key]:
            url = f"{self._api_url(store_config, 'catalog_system/pub/products/search')}/{term}"
            _from = (page - 1) * PAGE_SIZE
            _to = min(_from + limit - 1, _from + PAGE_SIZE - 1)
            params = {"_from": str(_from), "_to": str(_to)}
            headers = _client_headers(store_config)

            # First attempt: Try with cached cookies (if available)
            cached_cookies = self._get_cached_cookies(store_key)
            if cached_cookies:
                logger.debug(f"Attempting search with cached cookies for {store_key}")
                try:
                    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, cookies=cached_cookies) as client:
                        resp = await client.get(url, params=params, headers=headers)
                        if resp.status_code == 200:
                            ct = resp.headers.get("content-type", "")
                            if "json" in ct:
                                data = resp.json()
                                if isinstance(data, list) and len(data) > 0:
                                    logger.debug(f"Cache-assisted search succeeded for {store_key}")
                                    return data
                except Exception as e:
                    logger.debug(f"Cached cookie attempt failed for {store_key}: {e}")

            # Second attempt: Direct HTTP request (no cookies initially)
            logger.debug(f"Attempting direct HTTP search for {store_key}")
            try:
                async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                    resp = await client.get(url, params=params, headers=headers)
                    
                    # Handle rate limiting with retry
                    if resp.status_code == 429:
                        logger.warning(f"Rate limited (429) for {store_key}, retrying after delay")
                        await asyncio.sleep(2.0)
                        resp = await client.get(url, params=params, headers=headers)

                    # Check for success
                    if resp.status_code == 200:
                        ct = resp.headers.get("content-type", "")
                        if "json" in ct:
                            data = resp.json()
                            if isinstance(data, list):
                                logger.debug(f"Direct search succeeded for {store_key}")
                                return data

                    # Log the failure before attempting fallback
                    logger.warning(f"Direct HTTP search failed for {store_key}: status={resp.status_code}, content_type={resp.headers.get('content-type', 'unknown')}")

            except httpx.HTTPError as e:
                logger.warning(f"Direct HTTP search raised exception for {store_key}: {type(e).__name__}: {e}")

            # Third attempt: Playwright fallback (if available and not already attempted)
            logger.info(f"Attempting Playwright fallback for {store_key}")
            try:
                fallback_data = await self._search_playwright_fallback(url, params, store_key, store_config)
                return fallback_data
            except Exception:
                logger.error(f"All search attempts failed for {store_key}, returning empty list")
                return []

    async def fetch_all_products(self, store_config: dict,
                                  max_pages: int = 10) -> list[dict]:
        """Fetch all products with fallback mechanism for blocked stores."""
        if store_config.get("_io_path") is None:
            await self._detect_io(store_config)

        store_key = store_config.get("_store_key", store_config.get("name", "unknown"))
        url = self._api_url(store_config, "catalog_system/pub/products/search")
        all_products = []
        headers = _client_headers(store_config)

        async with _STORE_LOCKS[store_key]:
            # Try direct HTTP first
            logger.debug(f"Fetching all products for {store_key}")
            cached_cookies = self._get_cached_cookies(store_key)
            
            try:
                if cached_cookies:
                    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, cookies=cached_cookies) as client:
                        for page in range(1, max_pages + 1):
                            _from = (page - 1) * CATALOG_PAGE_SIZE
                            _to = page * CATALOG_PAGE_SIZE - 1
                            resp = await client.get(url, params={
                                "_from": str(_from), "_to": str(_to),
                                "O": "OrderByTopSaleDESC",
                            }, headers=headers)
                            if resp.status_code in (200, 206):
                                data = resp.json()
                                all_products.extend(data)
                                if len(data) < CATALOG_PAGE_SIZE:
                                    break
                            else:
                                break
                        if all_products:
                            logger.debug(f"fetch_all_products succeeded with cached cookies for {store_key}")
                            return all_products
            except Exception as e:
                logger.debug(f"Cached fetch failed for {store_key}: {e}")
                all_products = []

            # Try direct HTTP without cookies
            try:
                async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                    for page in range(1, max_pages + 1):
                        _from = (page - 1) * CATALOG_PAGE_SIZE
                        _to = page * CATALOG_PAGE_SIZE - 1
                        resp = await client.get(url, params={
                            "_from": str(_from), "_to": str(_to),
                            "O": "OrderByTopSaleDESC",
                        }, headers=headers)
                        if resp.status_code in (200, 206):
                            data = resp.json()
                            all_products.extend(data)
                            if len(data) < CATALOG_PAGE_SIZE:
                                break
                        else:
                            break
                    if all_products:
                        logger.debug(f"fetch_all_products succeeded with direct HTTP for {store_key}")
                        return all_products
            except Exception as e:
                logger.debug(f"Direct HTTP fetch failed for {store_key}: {e}")

            # Fallback to Playwright for first page
            logger.info(f"Attempting Playwright fallback for fetch_all_products on {store_key}")
            try:
                _from = 0
                _to = CATALOG_PAGE_SIZE - 1
                params = {
                    "_from": str(_from), "_to": str(_to),
                    "O": "OrderByTopSaleDESC",
                }
                fallback_data = await self._search_playwright_fallback(url, params, store_key, store_config)
                return fallback_data
            except Exception as e:
                logger.error(f"fetch_all_products fallback failed for {store_key}: {e}")
                return []

    def normalize(self, raw: dict, store_key: str, store_config: dict) -> dict:
        items = raw.get("items", [])
        item = items[0] if items else {}
        sellers = item.get("sellers", [])
        seller = sellers[0] if sellers else {}
        offer = seller.get("commertialOffer", {})
        price = parse_price(offer.get("Price"))
        list_price = parse_price(offer.get("ListPrice"))
        discount = round((1 - price / list_price) * 100) if list_price > price > 0 else None
        return {
            "id": raw.get("productReference", raw.get("productId", "")),
            "product_id": raw.get("productReference", raw.get("productId", "")),
            "name": clean_name(raw.get("productName", "")),
            "brand": raw.get("brand") or "—",
            "category": raw.get("categoryId", ""),
            "price": price,
            "list_price": list_price,
            "discount": discount,
            "stock": offer.get("AvailableQuantity", 0),
            "store": store_key,
            "store_name": store_config["name"],
            "currency": store_config["currency"],
            "url": f"{store_config.get('link_base', store_config['base'])}/{raw.get('linkText', '')}/p",
        }

    async def categories(self, store_config: dict) -> list[dict]:
        if store_config.get("_io_path") is None:
            await self._detect_io(store_config)
        url = self._api_url(store_config, "catalog_system/pub/category/tree/10")
        headers = _client_headers(store_config)
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.json()
