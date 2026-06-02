"""Per-store API credentials from environment and approved retailer applications.

Env pattern (store id uppercased, e.g. falabella_pe → FALABELLA_PE):

  STORE_FALABELLA_PE_MAGENTO_TOKEN       Magento / Adobe Commerce integration token
  STORE_GYMSHARK_STOREFRONT_TOKEN        Shopify Storefront API token
  STORE_WONG_VTEX_APP_KEY                VTEX app key (optional — higher rate limits)
  STORE_WONG_VTEX_APP_TOKEN              VTEX app token

Approved applications are persisted in ``store_credentials`` (see retailer_onboarding.py).
Env vars override DB values for the same field.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

from market_stores import STORES

_ENV_PREFIX = "STORE_"
_ENV_KEY = re.compile(
    r"^STORE_([A-Z0-9_]+)_(MAGENTO_TOKEN|STOREFRONT_TOKEN|VTEX_APP_KEY|VTEX_APP_TOKEN|API_TOKEN)$"
)

_DB_CACHE_TTL = float(os.getenv("STORE_CREDENTIALS_CACHE_SEC", "30"))
_db_cache_at: float = 0.0
_db_credentials: dict[str, dict[str, str]] = {}
_db_profiles: dict[str, dict[str, Any]] = {}


def _env_store_id(raw: str) -> str:
    return raw.lower()


def load_credentials_from_env() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for key, value in os.environ.items():
        if not key.startswith(_ENV_PREFIX) or not value.strip():
            continue
        m = _ENV_KEY.match(key)
        if not m:
            continue
        store_id = _env_store_id(m.group(1))
        kind = m.group(2)
        bucket = out.setdefault(store_id, {})
        if kind == "MAGENTO_TOKEN":
            bucket["magento_token"] = value.strip()
        elif kind == "STOREFRONT_TOKEN":
            bucket["storefront_token"] = value.strip()
        elif kind == "VTEX_APP_KEY":
            bucket["vtex_app_key"] = value.strip()
        elif kind == "VTEX_APP_TOKEN":
            bucket["vtex_app_token"] = value.strip()
        elif kind == "API_TOKEN":
            bucket["api_token"] = value.strip()
    return out


_ENV_CREDENTIALS: dict[str, dict[str, str]] = load_credentials_from_env()


def reload_credentials() -> None:
    """Re-read env (tests)."""
    global _ENV_CREDENTIALS
    _ENV_CREDENTIALS = load_credentials_from_env()
    invalidate_credential_cache()


def invalidate_credential_cache() -> None:
    global _db_cache_at
    _db_cache_at = 0.0


def _load_credentials_from_db() -> tuple[dict[str, dict[str, str]], dict[str, dict[str, Any]]]:
    try:
        from market_core import get_db
    except Exception:
        return {}, {}

    try:
        db = get_db()
    except Exception:
        return {}, {}

    try:
        rows = db.execute(
            """
            SELECT store_id, platform, store_name, base, country, currency, line,
                   magento_token, storefront_token, vtex_app_key, vtex_app_token, active
            FROM store_credentials
            WHERE active=1
            """
        ).fetchall()
    except Exception:
        db.close()
        return {}, {}

    creds: dict[str, dict[str, str]] = {}
    profiles: dict[str, dict[str, Any]] = {}
    for row in rows:
        r = dict(row)
        store_id = r["store_id"]
        bucket: dict[str, str] = {}
        for field in (
            "magento_token",
            "storefront_token",
            "vtex_app_key",
            "vtex_app_token",
        ):
            val = (r.get(field) or "").strip()
            if val:
                bucket[field] = val
        if bucket:
            creds[store_id] = bucket

        profiles[store_id] = {
            "name": r.get("store_name") or store_id,
            "base": r.get("base") or "",
            "country": r.get("country") or "",
            "currency": r.get("currency") or "USD",
            "line": r.get("line") or "supermercados",
            "platform": r.get("platform") or "vtex",
            "disabled": False,
            "source": "approved_application",
        }
    db.close()
    return creds, profiles


def _ensure_db_cache() -> None:
    global _db_cache_at, _db_credentials, _db_profiles
    now = time.monotonic()
    if now - _db_cache_at < _DB_CACHE_TTL:
        return
    _db_credentials, _db_profiles = _load_credentials_from_db()
    _db_cache_at = now


def get_custom_store_ids() -> list[str]:
    _ensure_db_cache()
    return [sid for sid in _db_profiles if sid not in STORES]


def get_store_profile(store_id: str) -> dict[str, Any] | None:
    if store_id in STORES:
        return dict(STORES[store_id])
    _ensure_db_cache()
    profile = _db_profiles.get(store_id)
    return dict(profile) if profile else None


def store_exists(store_id: str) -> bool:
    return store_id in STORES or get_store_profile(store_id) is not None


def get_store_credentials(store_id: str) -> dict[str, str]:
    merged: dict[str, str] = {}
    try:
        _ensure_db_cache()
        merged.update(_db_credentials.get(store_id, {}))
    except Exception:
        pass
    merged.update(_ENV_CREDENTIALS.get(store_id, {}))
    return merged


def resolve_store_config(store_id: str) -> dict[str, Any]:
    profile = get_store_profile(store_id)
    if not profile:
        raise KeyError(store_id)
    cfg = dict(profile)
    creds = get_store_credentials(store_id)
    if cfg.get("platform") == "need_token":
        cfg["platform"] = "vtex"
    if not creds:
        return cfg

    platform = cfg.get("platform", "vtex")
    generic = creds.get("api_token", "")

    if creds.get("magento_token"):
        cfg["magento_token"] = creds["magento_token"]
    elif platform == "magento" and generic:
        cfg["magento_token"] = generic

    if creds.get("storefront_token"):
        cfg["storefront_token"] = creds["storefront_token"]
    elif platform == "shopify" and generic:
        cfg["storefront_token"] = generic

    if creds.get("vtex_app_key"):
        cfg["vtex_app_key"] = creds["vtex_app_key"]
    if creds.get("vtex_app_token"):
        cfg["vtex_app_token"] = creds["vtex_app_token"]
    elif platform == "vtex" and generic and not cfg.get("vtex_app_token"):
        cfg["vtex_app_token"] = generic

    return cfg


def _credentials_sufficient(platform: str, creds: dict[str, str], *, public_vtex: bool = False) -> bool:
    if platform == "magento":
        return bool(creds.get("magento_token") or creds.get("api_token"))
    if platform == "shopify":
        return bool(creds.get("storefront_token") or creds.get("api_token"))
    if platform == "vtex":
        if creds.get("vtex_app_key") and creds.get("vtex_app_token"):
            return True
        return public_vtex
    return bool(creds.get("api_token"))


def has_store_credentials(store_id: str) -> bool:
    profile = get_store_profile(store_id)
    if not profile:
        return False
    platform = profile.get("platform", "vtex")
    creds = get_store_credentials(store_id)
    public_vtex = (
        platform == "vtex"
        and bool(profile.get("base"))
        and profile.get("source") == "approved_application"
    )
    return _credentials_sufficient(platform, creds, public_vtex=public_vtex)


def compute_default_stores() -> list[str]:
    active: list[str] = []
    seen: set[str] = set()

    for store_id, cfg in STORES.items():
        if not cfg.get("disabled"):
            active.append(store_id)
            seen.add(store_id)
        elif cfg.get("enable_with_credentials") and has_store_credentials(store_id):
            active.append(store_id)
            seen.add(store_id)

    for store_id in get_custom_store_ids():
        if store_id in seen:
            continue
        if has_store_credentials(store_id):
            active.append(store_id)
            seen.add(store_id)

    return active


def get_default_stores() -> list[str]:
    """Active catalog (recomputed — includes freshly approved DB credentials)."""
    return compute_default_stores()


def credential_summary() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    default = set(get_default_stores())
    checked: set[str] = set()

    for store_id in list(STORES.keys()) + get_custom_store_ids():
        if store_id in checked:
            continue
        checked.add(store_id)
        creds = get_store_credentials(store_id)
        if not creds and store_id not in _db_profiles:
            continue
        profile = get_store_profile(store_id) or {}
        rows.append({
            "store": store_id,
            "platform": profile.get("platform"),
            "source": profile.get("source", "catalog"),
            "enabled_via_credentials": store_id in default,
            "fields": sorted(creds.keys()),
        })
    return rows
