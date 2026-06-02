"""Public enrichment APIs for the data moat — Open Food Facts, Wikimedia, Open-Meteo."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

OFF_HEADERS = {"User-Agent": "CLI-Market/1.5 (https://cli-market.dev; data-moat)"}
ENRICH_SAMPLE_SIZE = int(os.getenv("ENRICH_SAMPLE_SIZE", "12"))
OFF_REQUEST_DELAY = float(os.getenv("OFF_REQUEST_DELAY", "0.35"))

COUNTRY_WIKI: dict[str, tuple[str, list[str]]] = {
    "PE": ("es", ["Leche", "Arroz", "Supermercado", "Inflación"]),
    "AR": ("es", ["Leche", "Arroz", "Supermercado", "Inflación"]),
    "MX": ("es", ["Leche", "Arroz", "Supermercado", "Inflación"]),
    "CO": ("es", ["Leche", "Arroz", "Supermercado", "Inflación"]),
    "CL": ("es", ["Leche", "Arroz", "Supermercado", "Inflación"]),
    "BR": ("pt", ["Leite", "Arroz", "Supermercado", "Inflação"]),
    "IT": ("it", ["Latte", "Riso", "Supermercato", "Inflazione"]),
    "FR": ("fr", ["Lait", "Riz", "Supermarché", "Inflation"]),
}

# Staple-specific Wikipedia articles for category demand signal
COUNTRY_WIKI_STAPLES: dict[str, tuple[str, list[str]]] = {
    "PE": ("es", ["Leche", "Arroz", "Aceite de cocina"]),
    "AR": ("es", ["Leche", "Arroz", "Aceite de cocina"]),
    "MX": ("es", ["Leche", "Arroz", "Aceite de cocina"]),
    "CO": ("es", ["Leche", "Arroz", "Aceite de cocina"]),
    "CL": ("es", ["Leche", "Arroz", "Aceite de cocina"]),
    "BR": ("pt", ["Leite", "Arroz", "Óleo de cozinha"]),
    "IT": ("it", ["Latte", "Riso", "Olio di semi"]),
    "FR": ("fr", ["Lait", "Riz", "Huile alimentaire"]),
}

COUNTRY_WEATHER: dict[str, tuple[float, float]] = {
    "PE": (-12.046, -77.042),
    "AR": (-34.603, -58.382),
    "MX": (19.433, -99.133),
    "CO": (4.711, -74.072),
    "CL": (-33.449, -70.669),
    "BR": (-23.550, -46.633),
    "IT": (41.902, 12.496),
    "FR": (48.857, 2.352),
}

_EAN_RE = re.compile(r"^\d{8,14}$")


def _looks_like_ean(product_id: str) -> bool:
    return bool(_EAN_RE.match(str(product_id or "").strip()))


def _off_search_term(name: str) -> str:
    words = re.sub(r"[^\w\sáéíóúñ]", " ", name.lower()).split()
    stop = {"de", "la", "el", "en", "x", "pack", "un", "und", "gr", "ml", "lt", "kg"}
    kept = [w for w in words if w not in stop and len(w) > 2][:4]
    return " ".join(kept) if kept else name[:30]


def fetch_off_by_barcode(code: str) -> dict[str, Any] | None:
    try:
        with httpx.Client(timeout=10.0, headers=OFF_HEADERS) as client:
            r = client.get(f"https://world.openfoodfacts.org/api/v2/product/{code}.json")
            if r.status_code != 200:
                return None
            product = r.json().get("product") or {}
            if product.get("status") == 0:
                return None
            return _normalize_off(product, code)
    except Exception as e:
        logger.debug("OFF barcode %s: %s", code, e)
        return None


def fetch_off_by_search(query: str) -> dict[str, Any] | None:
    try:
        with httpx.Client(timeout=12.0, headers=OFF_HEADERS) as client:
            r = client.get(
                "https://world.openfoodfacts.org/cgi/search.pl",
                params={"search_terms": query, "json": 1, "page_size": 1, "fields": "code,product_name,brands,nutriscore_grade,ecoscore_grade,nova_group,categories"},
            )
            if r.status_code != 200:
                return None
            products = r.json().get("products") or []
            if not products:
                return None
            p = products[0]
            return _normalize_off(p, p.get("code", ""))
    except Exception as e:
        logger.debug("OFF search %s: %s", query, e)
        return None


def _normalize_off(product: dict, code: str) -> dict[str, Any]:
    nova = product.get("nova_group") or product.get("nova_groups")
    try:
        nova_i = int(nova) if nova is not None else None
    except (TypeError, ValueError):
        nova_i = None
    grade = (product.get("nutriscore_grade") or "").upper()
    eco = (product.get("ecoscore_grade") or "").upper()
    return {
        "code": code or product.get("code", ""),
        "name": product.get("product_name", ""),
        "brand": product.get("brands", ""),
        "nutriscore": grade,
        "ecoscore": eco,
        "nova_group": nova_i,
        "categories": product.get("categories", ""),
    }


def cache_get(db, cache_key: str, max_age_hours: int = 168) -> dict | None:
    try:
        row = db.execute(
            "SELECT payload_json, recorded_at FROM enrichment_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if not row:
            return None
        raw = str(row["recorded_at"]).replace("T", " ")[:19]
        recorded = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - recorded > timedelta(hours=max_age_hours):
            return None
        return json.loads(row["payload_json"] or "{}")
    except Exception:
        return None


def cache_set(db, cache_key: str, source: str, payload: dict) -> None:
    try:
        db.execute(
            """
            INSERT INTO enrichment_cache (cache_key, source, payload_json, recorded_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(cache_key) DO UPDATE SET
                source=excluded.source,
                payload_json=excluded.payload_json,
                recorded_at=datetime('now')
            """,
            (cache_key, source, json.dumps(payload, ensure_ascii=False)),
        )
    except Exception as e:
        logger.debug("enrichment cache set failed: %s", e)


def resolve_off_for_product(db, product_id: str, name: str) -> dict[str, Any] | None:
    cache_key = f"off:barcode:{product_id}" if _looks_like_ean(product_id) else f"off:search:{_off_search_term(name)}"
    cached = cache_get(db, cache_key)
    if cached:
        return cached

    result = None
    if _looks_like_ean(product_id):
        result = fetch_off_by_barcode(product_id)
    if not result and name:
        result = fetch_off_by_search(_off_search_term(name))
        time.sleep(OFF_REQUEST_DELAY)

    if result:
        cache_set(db, cache_key, "openfoodfacts", result)
    return result


def sample_off_coverage(db, country: str, limit: int | None = None) -> dict[str, Any]:
    """Sample supermercados SKUs and measure Open Food Facts match rate."""
    from market_core import STORES

    limit = limit or ENRICH_SAMPLE_SIZE
    stores = [k for k, v in STORES.items() if v.get("country") == country.upper() and not v.get("disabled")]
    if not stores:
        return {"sampled": 0, "matched": 0, "match_rate_pct": None}

    placeholders = ",".join("?" * len(stores))
    rows = db.execute(
        f"""
        SELECT product_id, name, store, price FROM price_snapshots
        WHERE store IN ({placeholders}) AND line = 'supermercados' AND price > 0 AND name IS NOT NULL
        ORDER BY queried_at DESC LIMIT ?
        """,
        [*stores, limit],
    ).fetchall()

    matched = 0
    nutriscore_good = 0
    nova_vals: list[int] = []
    ultra_processed = 0
    ecoscore_vals: list[int] = []
    samples: list[dict] = []

    _ECO_SCORE = {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1}

    for r in rows:
        off = resolve_off_for_product(db, r["product_id"], r["name"])
        if off:
            matched += 1
            grade = off.get("nutriscore", "")
            if grade in ("A", "B"):
                nutriscore_good += 1
            eco = off.get("ecoscore", "")
            if eco in _ECO_SCORE:
                ecoscore_vals.append(_ECO_SCORE[eco])
            nova = off.get("nova_group")
            if isinstance(nova, int):
                nova_vals.append(nova)
                if nova >= 4:
                    ultra_processed += 1
            samples.append(
                {
                    "product_id": r["product_id"],
                    "name": r["name"][:60],
                    "store": r["store"],
                    "price": r["price"],
                    "off": off,
                }
            )
        if _looks_like_ean(r["product_id"]):
            time.sleep(OFF_REQUEST_DELAY)

    n = len(rows)
    return {
        "sampled": n,
        "matched": matched,
        "match_rate_pct": round(matched / n * 100, 1) if n else None,
        "nutriscore_ab_pct": round(nutriscore_good / matched * 100, 1) if matched else None,
        "nova_avg": round(sum(nova_vals) / len(nova_vals), 2) if nova_vals else None,
        "ultra_processed_pct": round(ultra_processed / matched * 100, 1) if matched else None,
        "ecoscore_avg": round(sum(ecoscore_vals) / len(ecoscore_vals), 2) if ecoscore_vals else None,
        "samples": samples[:5],
    }


def _wiki_momentum_for_articles(wiki_lang: str, articles: list[str]) -> float | None:
    end = datetime.now(timezone.utc).date()
    start_recent = end - timedelta(days=7)
    start_prev = end - timedelta(days=14)

    def _views_for_range(article: str, d0, d1) -> int:
        art = quote(article.replace(" ", "_"), safe="")
        url = (
            f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
            f"{wiki_lang}.wikipedia/all-access/user/{art}/daily/"
            f"{d0.strftime('%Y%m%d')}/{d1.strftime('%Y%m%d')}"
        )
        try:
            with httpx.Client(timeout=12.0, headers={"User-Agent": OFF_HEADERS["User-Agent"]}) as client:
                r = client.get(url)
                if r.status_code != 200:
                    return 0
                items = r.json().get("items") or []
                return sum(int(i.get("views", 0)) for i in items)
        except Exception as e:
            logger.debug("Wiki %s: %s", article, e)
            return 0

    ratios: list[float] = []
    for art in articles:
        recent = _views_for_range(art, start_recent, end)
        prev = _views_for_range(art, start_prev, start_recent - timedelta(days=1))
        if recent > 0 or prev > 0:
            ratios.append(recent / max(prev, 1))
        time.sleep(0.2)

    if not ratios:
        return None
    return round(sum(ratios) / len(ratios), 3)


def fetch_wiki_demand_momentum(country: str) -> float | None:
    cfg = COUNTRY_WIKI.get(country.upper())
    if not cfg:
        return None
    wiki_lang, articles = cfg
    return _wiki_momentum_for_articles(wiki_lang, articles)


def fetch_wiki_staple_momentum(country: str) -> float | None:
    """Pageview momentum for staple articles (leche, arroz, aceite)."""
    cfg = COUNTRY_WIKI_STAPLES.get(country.upper())
    if not cfg:
        return None
    wiki_lang, articles = cfg
    return _wiki_momentum_for_articles(wiki_lang, articles)


def fetch_weather_logistics_stress(country: str) -> float | None:
    coords = COUNTRY_WEATHER.get(country.upper())
    if not coords:
        return None
    lat, lon = coords
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "daily": "precipitation_sum,temperature_2m_max",
                    "forecast_days": 7,
                    "timezone": "auto",
                },
            )
            r.raise_for_status()
            daily = r.json().get("daily") or {}
            precip = daily.get("precipitation_sum") or []
            temps = daily.get("temperature_2m_max") or []
            if not precip:
                return None
            rain_mm = sum(float(x or 0) for x in precip)
            heat_days = sum(1 for t in temps if t is not None and float(t) >= 32)
            # 0–100 stress proxy: rain + heat disruption for fresh logistics
            stress = min(100.0, rain_mm * 2.5 + heat_days * 8)
            return round(stress, 1)
    except Exception as e:
        logger.warning("Open-Meteo failed for %s: %s", country, e)
        return None


def fetch_food_cpi_yoy(country: str) -> float | None:
    wb_map = {"PE": "PE", "AR": "AR", "MX": "MX", "CO": "CO", "CL": "CL", "BR": "BR", "IT": "IT", "FR": "FR", "US": "US"}
    wb = wb_map.get(country.upper())
    if not wb:
        return None
    year = datetime.now().year
    url = f"https://api.worldbank.org/v2/country/{wb}/indicator/FP.CPI.FOOD.ZG?format=json&per_page=5&date={year - 5}:{year}"
    try:
        with httpx.Client(timeout=12.0) as client:
            r = client.get(url)
            r.raise_for_status()
            payload = r.json()
            if isinstance(payload, list) and len(payload) >= 2:
                for entry in payload[1]:
                    val = entry.get("value")
                    if val is not None:
                        return round(float(val), 3)
    except Exception as e:
        logger.debug("Food CPI fetch %s: %s", country, e)
    return None


# ── Tier 2 public sources (IMF, Eurostat, BCB, World Bank labor) ─────────────

IMF_COUNTRY: dict[str, str] = {
    "PE": "PER",
    "AR": "ARG",
    "MX": "MEX",
    "CO": "COL",
    "CL": "CHL",
    "BR": "BRA",
    "IT": "ITA",
    "FR": "FRA",
    "US": "USA",
}

EUROSTAT_GEO: dict[str, str] = {"IT": "IT", "FR": "FR"}

_imf_cache: dict[str, dict[str, float]] = {}


def _imf_indicator_table(indicator: str) -> dict[str, float]:
    if indicator in _imf_cache:
        return _imf_cache[indicator]
    year = datetime.now().year
    url = f"https://www.imf.org/external/datamapper/api/v1/{indicator}?periods={year - 1},{year}"
    try:
        with httpx.Client(timeout=25.0, headers=OFF_HEADERS) as client:
            r = client.get(url)
            r.raise_for_status()
            table = r.json().get("values", {}).get(indicator, {})
            out: dict[str, float] = {}
            for iso3, series in table.items():
                if not isinstance(series, dict):
                    continue
                for yr in (str(year), str(year - 1)):
                    val = series.get(yr)
                    if val is not None:
                        out[iso3] = round(float(val), 3)
                        break
            _imf_cache[indicator] = out
            return out
    except Exception as e:
        logger.debug("IMF %s fetch: %s", indicator, e)
        return {}


def _imf_for_country(indicator: str, country: str) -> float | None:
    imf_code = IMF_COUNTRY.get(country.upper())
    if not imf_code:
        return None
    return _imf_indicator_table(indicator).get(imf_code)


def fetch_imf_inflation_yoy(country: str) -> float | None:
    return _imf_for_country("PCPIPCH", country)


def fetch_imf_gdp_growth_yoy(country: str) -> float | None:
    return _imf_for_country("NGDP_RPCH", country)


def fetch_imf_epi_inflation_yoy(country: str) -> float | None:
    """End-of-period CPI inflation (IMF PCPIEPCH)."""
    return _imf_for_country("PCPIEPCH", country)


def fetch_eurostat_food_hicp_yoy(country: str) -> float | None:
    return _fetch_eurostat_hicp_yoy(country, coicop="CP01")


def fetch_eurostat_headline_hicp_yoy(country: str) -> float | None:
    """All-items HICP annual rate (IT, FR)."""
    return _fetch_eurostat_hicp_yoy(country, coicop="CP00")


def _fetch_eurostat_hicp_yoy(country: str, coicop: str) -> float | None:
    geo = EUROSTAT_GEO.get(country.upper())
    if not geo:
        return None
    url = (
        "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/PRC_HICP_MANR"
        f"?geo={geo}&coicop={coicop}&format=JSON&lastTimePeriod=1"
    )
    try:
        with httpx.Client(timeout=20.0, headers=OFF_HEADERS) as client:
            r = client.get(url)
            r.raise_for_status()
            values = r.json().get("value") or {}
            if not values:
                return None
            latest = list(values.values())[-1]
            return round(float(latest), 3)
    except Exception as e:
        logger.debug("Eurostat HICP %s %s: %s", country, coicop, e)
        return None


def fetch_bcb_food_inflation_mom(country: str) -> float | None:
    if country.upper() != "BR":
        return None
    return _fetch_bcb_series("1635")


def fetch_bcb_headline_inflation_mom(country: str) -> float | None:
    if country.upper() != "BR":
        return None
    return _fetch_bcb_series("433")


def _fetch_bcb_series(series_id: str) -> float | None:
    url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{series_id}/dados/ultimos/1?formato=json"
    try:
        with httpx.Client(timeout=15.0, headers=OFF_HEADERS) as client:
            r = client.get(url)
            r.raise_for_status()
            rows = r.json()
            if rows and rows[0].get("valor") is not None:
                return round(float(rows[0]["valor"]), 3)
    except Exception as e:
        logger.debug("BCB series %s: %s", series_id, e)
    return None


def fetch_wb_unemployment_rate(country: str) -> float | None:
    wb_map = {"PE": "PE", "AR": "AR", "MX": "MX", "CO": "CO", "CL": "CL", "BR": "BR", "IT": "IT", "FR": "FR", "US": "US"}
    wb = wb_map.get(country.upper())
    if not wb:
        return None
    year = datetime.now().year
    url = f"https://api.worldbank.org/v2/country/{wb}/indicator/SL.UEM.TOTL.ZS?format=json&per_page=5&date={year - 5}:{year}"
    try:
        with httpx.Client(timeout=12.0, headers=OFF_HEADERS) as client:
            r = client.get(url)
            r.raise_for_status()
            payload = r.json()
            if isinstance(payload, list) and len(payload) >= 2:
                for entry in payload[1]:
                    val = entry.get("value")
                    if val is not None:
                        return round(float(val), 3)
    except Exception as e:
        logger.debug("WB unemployment %s: %s", country, e)
    return None


def fetch_wb_gdp_growth_yoy(country: str) -> float | None:
    wb_map = {"PE": "PE", "AR": "AR", "MX": "MX", "CO": "CO", "CL": "CL", "BR": "BR", "IT": "IT", "FR": "FR", "US": "US"}
    wb = wb_map.get(country.upper())
    if not wb:
        return None
    year = datetime.now().year
    url = f"https://api.worldbank.org/v2/country/{wb}/indicator/NY.GDP.MKTP.KD.ZG?format=json&per_page=8&date={year - 8}:{year}"
    try:
        with httpx.Client(timeout=12.0, headers=OFF_HEADERS) as client:
            r = client.get(url)
            r.raise_for_status()
            payload = r.json()
            if isinstance(payload, list) and len(payload) >= 2:
                for entry in payload[1]:
                    val = entry.get("value")
                    if val is not None:
                        return round(float(val), 3)
    except Exception as e:
        logger.debug("WB GDP growth %s: %s", country, e)
    return None


def clear_tier2_cache() -> None:
    _imf_cache.clear()
