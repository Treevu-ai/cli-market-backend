"""Pre-checkout validation — price freshness + optional index linkage.

Read-only gate invoked before creating pending orders. Does not touch collector.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from market_core import db_get_subscription, user_can_checkout
from price_snapshots_schema import ensure_canonical_product_id_column, price_snapshots_has_canonical_id


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


MAX_SNAPSHOT_AGE_SEC = lambda: _env_int("CHECKOUT_MAX_SNAPSHOT_AGE_SEC", 900)
MAX_PRICE_DRIFT_PCT = lambda: _env_float("CHECKOUT_MAX_PRICE_DRIFT_PCT", 3.0)
REQUIRE_INDEX_LINK = lambda: _env_bool("CHECKOUT_REQUIRE_INDEX_LINK", False)


def _snapshot_age_sec(queried_at: Any) -> int | None:
    from routers.health import _age_hours

    hours = _age_hours(queried_at)
    if hours is None:
        return None
    return int(hours * 3600)


def _latest_snapshot(product_id: str, store: str) -> dict | None:
    from market_core import ensure_db_initialized, get_db

    ensure_db_initialized()
    db = get_db()
    try:
        ensure_canonical_product_id_column(db)
        has_canonical = price_snapshots_has_canonical_id(db)
        cols = "price, queried_at"
        if has_canonical:
            cols += ", canonical_product_id"
        row = db.execute(
            f"""
            SELECT {cols}
            FROM price_snapshots
            WHERE product_id = ? AND store = ?
            ORDER BY queried_at DESC
            LIMIT 1
            """,
            (product_id, store),
        ).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def _resolve_prod_id(item: dict, snapshot: dict | None) -> str | None:
    if snapshot:
        prod = (snapshot.get("canonical_product_id") or "").strip()
        if prod:
            return prod
    try:
        from index_gate import index_resolve

        resolved = index_resolve(
            {
                "name": item.get("name", ""),
                "store": item.get("store", ""),
                "product_id": item.get("product_id", ""),
                "sku": item.get("product_id", ""),
                "price": float(item.get("price", 0) or 0),
                "url": item.get("url", ""),
            }
        )
        if resolved.get("resolved") and resolved.get("product"):
            return resolved["product"].get("id")
    except Exception:
        pass
    return None


@dataclass
class ValidateResult:
    ok: bool
    cart_total: float
    validated_total: float
    currency: str = "PEN"
    trace: list[dict] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    action: str | None = None

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "ok": self.ok,
            "validated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "cart_total": self.cart_total,
            "validated_total": self.validated_total,
            "currency": self.currency,
            "trace": self.trace,
            "items": self.items,
            "warnings": self.warnings,
        }
        if self.error:
            out["error"] = self.error
        if self.action:
            out["action"] = self.action
        return out


def pre_checkout_validate(
    username: str,
    cart: list[dict],
    *,
    currency: str = "PEN",
) -> ValidateResult:
    """Validate cart against latest snapshots. No cart/order side effects."""
    trace: list[dict] = []
    warnings: list[str] = []
    item_results: list[dict] = []

    tier = "free"
    if user_can_checkout(username):
        sub = db_get_subscription(username) or {}
        tier = sub.get("tier", "pro")
        trace.append({"step": "tier", "status": "ok", "detail": tier})
    else:
        trace.append({"step": "tier", "status": "fail", "detail": "checkout_not_allowed"})
        return ValidateResult(
            ok=False,
            cart_total=0.0,
            validated_total=0.0,
            currency=currency,
            trace=trace,
            error="checkout_not_allowed",
            action="upgrade_pro",
        )

    if not cart:
        trace.append({"step": "cart", "status": "fail", "items": 0})
        return ValidateResult(
            ok=False,
            cart_total=0.0,
            validated_total=0.0,
            currency=currency,
            trace=trace,
            error="empty_cart",
        )

    trace.append({"step": "cart", "status": "ok", "items": len(cart)})

    max_age = MAX_SNAPSHOT_AGE_SEC()
    max_drift = MAX_PRICE_DRIFT_PCT()
    require_index = REQUIRE_INDEX_LINK()

    stale_count = 0
    drift_count = 0
    missing_count = 0
    linked = 0
    index_skipped = 0

    cart_total = round(sum(float(i.get("price", 0) or 0) * int(i.get("quantity", 1) or 1) for i in cart), 2)
    validated_total = 0.0
    all_ok = True

    for item in cart:
        product_id = item.get("product_id", "")
        store = item.get("store", "")
        cart_price = float(item.get("price", 0) or 0)
        qty = int(item.get("quantity", 1) or 1)
        snapshot = _latest_snapshot(product_id, store) if product_id and store else None

        row: dict[str, Any] = {
            "product_id": product_id,
            "name": item.get("name", ""),
            "store": store,
            "cart_price": cart_price,
            "quantity": qty,
        }

        if not snapshot:
            missing_count += 1
            all_ok = False
            row.update(
                status="missing_snapshot",
                message="Sin snapshot reciente para este producto/tienda",
            )
            item_results.append(row)
            validated_total += cart_price * qty
            continue

        snapshot_price = float(snapshot.get("price", 0) or 0)
        age_sec = _snapshot_age_sec(snapshot.get("queried_at"))
        row["snapshot_price"] = snapshot_price
        row["snapshot_age_sec"] = age_sec

        prod_id = _resolve_prod_id(item, snapshot)
        if prod_id:
            row["prod_id"] = prod_id
            linked += 1
        else:
            index_skipped += 1
            if require_index:
                all_ok = False
                row["status"] = "index_unlinked"
                row["message"] = "Producto sin Golden Record (prod_*)"
                item_results.append(row)
                validated_total += cart_price * qty
                continue

        if age_sec is None or age_sec > max_age:
            stale_count += 1
            all_ok = False
            row["status"] = "stale"
            row["message"] = f"Snapshot demasiado antiguo ({age_sec}s > {max_age}s)"
            item_results.append(row)
            validated_total += snapshot_price * qty
            continue

        if cart_price > 0:
            drift_pct = abs(cart_price - snapshot_price) / cart_price * 100
        else:
            drift_pct = 0.0 if snapshot_price == 0 else 100.0

        if drift_pct > max_drift:
            drift_count += 1
            all_ok = False
            sign = "+" if snapshot_price > cart_price else "-"
            row.update(
                status="drift",
                drift_pct=round(drift_pct, 2),
                message=f"Precio cambió {sign}{drift_pct:.1f}% desde que agregaste al carrito",
            )
            item_results.append(row)
            validated_total += snapshot_price * qty
            continue

        row["status"] = "ok"
        item_results.append(row)
        validated_total += snapshot_price * qty

    validated_total = round(validated_total, 2)

    freshness_status = "ok" if all_ok and not (stale_count or drift_count or missing_count) else "fail"
    trace.append(
        {
            "step": "price_freshness",
            "status": freshness_status,
            "checked": len(cart),
            "stale": stale_count,
            "drift": drift_count,
            "missing": missing_count,
        }
    )
    trace.append(
        {
            "step": "index_identity",
            "status": "ok" if not require_index or linked == len(cart) else "fail",
            "linked": linked,
            "skipped": index_skipped,
        }
    )

    if not all_ok:
        _record_validate_event(username, ok=False, stale=stale_count, drift=drift_count, missing=missing_count)
        return ValidateResult(
            ok=False,
            cart_total=cart_total,
            validated_total=validated_total,
            currency=currency,
            trace=trace,
            items=item_results,
            warnings=warnings,
            error="price_stale_or_drift",
            action="refresh_cart",
        )

    _record_validate_event(username, ok=True, stale=0, drift=0, missing=0)
    return ValidateResult(
        ok=True,
        cart_total=cart_total,
        validated_total=validated_total,
        currency=currency,
        trace=trace,
        items=item_results,
        warnings=warnings,
    )


def _record_validate_event(
    username: str,
    *,
    ok: bool,
    stale: int,
    drift: int,
    missing: int,
) -> None:
    try:
        from market_funnel import record_funnel_event

        record_funnel_event(
            "checkout_validate",
            username=username,
            meta={"ok": ok, "stale": stale, "drift": drift, "missing": missing},
            dedupe=False,
        )
    except Exception:
        pass
