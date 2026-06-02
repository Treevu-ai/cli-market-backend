"""Retailer self-serve applications → approved store credentials."""

from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import urlparse

from market_core import get_db
from market_stores import STORES as STORE_CATALOG

_COUNTRY_CURRENCY = {
    "PE": "PEN", "AR": "ARS", "BR": "BRL", "MX": "MXN", "CO": "COP", "CL": "CLP",
    "US": "USD", "IT": "EUR", "FR": "EUR", "ES": "EUR", "CH": "CHF",
}


def token_hint(token: str) -> str:
    token = (token or "").strip()
    if not token:
        return ""
    if len(token) <= 4:
        return "****"
    return f"...{token[-4:]}"


def normalize_website(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return base.lower()


def slugify_store_id(name: str, country: str) -> str:
    raw = unicodedata.normalize("NFKD", name)
    raw = raw.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")[:40]
    suffix = country.lower()
    if not slug:
        slug = "store"
    candidate = f"{slug}_{suffix}"
    n = 2
    while candidate in STORE_CATALOG:
        candidate = f"{slug}_{suffix}_{n}"
        n += 1
    return candidate


def guess_store_id(website: str, platform: str, country: str) -> str | None:
    site = normalize_website(website)
    if not site:
        return None
    for store_id, cfg in STORE_CATALOG.items():
        if cfg.get("country") != country:
            continue
        plat = cfg.get("platform", "vtex")
        if platform not in ("other", plat):
            continue
        base = normalize_website(cfg.get("base", ""))
        if base and (site == base or site.startswith(base) or base in site):
            return store_id
    return None


def _platform_credential_fields(platform: str, creds: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    if platform == "magento":
        tok = creds.get("magento_token") or creds.get("api_token", "")
        if tok:
            out["magento_token"] = tok
    elif platform == "shopify":
        tok = creds.get("storefront_token") or creds.get("api_token", "")
        if tok:
            out["storefront_token"] = tok
    elif platform == "vtex":
        key = creds.get("vtex_app_key", "")
        tok = creds.get("vtex_app_token", "")
        if key:
            out["vtex_app_key"] = key
        if tok:
            out["vtex_app_token"] = tok
    return out


def db_list_retailer_applications(status: str | None = None) -> list[dict]:
    db = get_db()
    if status:
        rows = db.execute(
            "SELECT * FROM retailer_applications WHERE status=? ORDER BY created_at DESC",
            (status,),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM retailer_applications ORDER BY created_at DESC"
        ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def db_get_retailer_application(app_id: str) -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT * FROM retailer_applications WHERE id=?",
        (app_id,),
    ).fetchone()
    db.close()
    return dict(row) if row else None


def db_public_application(row: dict) -> dict:
    """Strip secret fields before admin/API list responses."""
    public = {k: v for k, v in row.items() if k != "api_token"}
    return public


def db_upsert_store_credentials(
    *,
    store_id: str,
    platform: str,
    application_id: str = "",
    store_name: str = "",
    base: str = "",
    country: str = "",
    line: str = "supermercados",
    currency: str = "",
    magento_token: str = "",
    storefront_token: str = "",
    vtex_app_key: str = "",
    vtex_app_token: str = "",
) -> None:
    if not currency and country:
        currency = _COUNTRY_CURRENCY.get(country.upper(), "USD")
    db = get_db()
    db.execute(
        """
        INSERT INTO store_credentials
            (store_id, platform, store_name, base, country, currency, line,
             magento_token, storefront_token, vtex_app_key, vtex_app_token,
             application_id, active, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, datetime('now'))
        ON CONFLICT(store_id) DO UPDATE SET
            platform=excluded.platform,
            store_name=COALESCE(NULLIF(excluded.store_name, ''), store_credentials.store_name),
            base=COALESCE(NULLIF(excluded.base, ''), store_credentials.base),
            country=COALESCE(NULLIF(excluded.country, ''), store_credentials.country),
            currency=COALESCE(NULLIF(excluded.currency, ''), store_credentials.currency),
            line=COALESCE(NULLIF(excluded.line, ''), store_credentials.line),
            magento_token=COALESCE(NULLIF(excluded.magento_token, ''), store_credentials.magento_token),
            storefront_token=COALESCE(NULLIF(excluded.storefront_token, ''), store_credentials.storefront_token),
            vtex_app_key=COALESCE(NULLIF(excluded.vtex_app_key, ''), store_credentials.vtex_app_key),
            vtex_app_token=COALESCE(NULLIF(excluded.vtex_app_token, ''), store_credentials.vtex_app_token),
            application_id=COALESCE(NULLIF(excluded.application_id, ''), store_credentials.application_id),
            active=1,
            updated_at=datetime('now')
        """,
        (
            store_id,
            platform,
            store_name,
            base,
            country.upper() if country else "",
            currency,
            line,
            magento_token,
            storefront_token,
            vtex_app_key,
            vtex_app_token,
            application_id,
        ),
    )
    db.commit()
    db.close()


def approve_retailer_application(
    app_id: str,
    *,
    store_id: str | None = None,
    magento_token: str = "",
    storefront_token: str = "",
    vtex_app_key: str = "",
    vtex_app_token: str = "",
    line: str = "supermercados",
    review_notes: str = "",
) -> dict[str, Any]:
    from store_credentials import invalidate_credential_cache

    app = db_get_retailer_application(app_id)
    if not app:
        raise ValueError("application_not_found")
    if app.get("status") != "pending":
        raise ValueError(f"application_not_pending:{app.get('status')}")

    platform = app.get("platform", "vtex")
    if platform == "other":
        platform = "vtex"

    resolved_id = (store_id or app.get("store_id") or "").strip()
    if not resolved_id:
        resolved_id = guess_store_id(app.get("website", ""), platform, app.get("country", ""))
    if not resolved_id:
        resolved_id = slugify_store_id(app.get("store_name", "store"), app.get("country", "XX"))

    app_token = (app.get("api_token") or "").strip()
    creds = {
        "magento_token": magento_token or app_token,
        "storefront_token": storefront_token or app_token,
        "vtex_app_key": vtex_app_key,
        "vtex_app_token": vtex_app_token or app_token,
        "api_token": app_token,
    }
    fields = _platform_credential_fields(platform, creds)

    catalog = STORE_CATALOG.get(resolved_id, {})
    base = normalize_website(app.get("website", "")) or catalog.get("base", "")
    store_name = app.get("store_name") or catalog.get("name", resolved_id)
    country = app.get("country") or catalog.get("country", "")
    store_line = catalog.get("line") or line
    currency = catalog.get("currency") or _COUNTRY_CURRENCY.get(country, "USD")

    if platform in ("magento", "shopify") and not fields:
        raise ValueError("credentials_required_for_platform")
    if platform == "vtex" and not fields and not base:
        raise ValueError("website_or_credentials_required")

    db_upsert_store_credentials(
        store_id=resolved_id,
        platform=platform if platform in ("vtex", "shopify", "magento") else "vtex",
        application_id=app_id,
        store_name=store_name,
        base=base,
        country=country,
        line=store_line,
        currency=currency,
        **fields,
    )

    db = get_db()
    db.execute(
        """
        UPDATE retailer_applications
        SET status='approved', store_id=?, reviewed_at=datetime('now'), review_notes=?
        WHERE id=?
        """,
        (resolved_id, review_notes.strip(), app_id),
    )
    db.commit()
    db.close()

    invalidate_credential_cache()

    return {
        "ok": True,
        "application_id": app_id,
        "store_id": resolved_id,
        "platform": platform,
        "catalog_match": resolved_id in STORE_CATALOG,
        "credentials_fields": sorted(fields.keys()),
    }


def reject_retailer_application(app_id: str, review_notes: str = "") -> dict[str, Any]:
    app = db_get_retailer_application(app_id)
    if not app:
        raise ValueError("application_not_found")
    if app.get("status") != "pending":
        raise ValueError(f"application_not_pending:{app.get('status')}")

    db = get_db()
    db.execute(
        """
        UPDATE retailer_applications
        SET status='rejected', reviewed_at=datetime('now'), review_notes=?
        WHERE id=?
        """,
        (review_notes.strip(), app_id),
    )
    db.commit()
    db.close()
    return {"ok": True, "application_id": app_id, "status": "rejected"}
