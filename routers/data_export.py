"""Data moat export endpoints — JSON or CSV.

Endpoints:
  POST /v1/data/export          Current snapshot, filtered by country/line
  POST /v1/data/export-history  Time-range export with date window
"""

from __future__ import annotations

import csv as _csv
import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

from market_core import STORES, get_db

router = APIRouter(tags=["data-export"])


@router.post("/v1/data/export")
def data_export(body: dict):
    """Export data moat as JSON or CSV. Supports filters: country, line, limit (≤1000)."""
    country = body.get("country")
    line = body.get("line")
    fmt = body.get("format", "json")
    limit = min(body.get("limit", 100), 1000)
    db = get_db()
    q = "SELECT * FROM price_snapshots WHERE price > 0"
    params: list = []
    if line:
        q += " AND line = ?"
        params.append(line)
    if country:
        country_stores = [
            s for s, sv in STORES.items()
            if sv.get("country", "").upper() == country.upper()
        ]
        if country_stores:
            placeholders = ",".join("?" * len(country_stores))
            q += f" AND store IN ({placeholders})"
            params.extend(country_stores)
        else:
            db.close()
            return {"format": fmt, "data": [], "total": 0, "filter": {"country": country}}
    q += " ORDER BY queried_at DESC LIMIT ?"
    params.append(limit)
    rows = db.execute(q, params).fetchall()
    db.close()
    data = [dict(r) for r in rows]
    if fmt == "csv":
        buf = io.StringIO()
        if data:
            w = _csv.DictWriter(buf, fieldnames=data[0].keys())
            w.writeheader()
            w.writerows(data)
        return {"format": "csv", "data": buf.getvalue(), "total": len(data)}
    return {"format": "json", "data": data, "total": len(data)}


@router.post("/v1/data/export-history")
def data_export_history(body: dict):
    """Export historical price data with date range and filters."""
    days = body.get("days", 30)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    line = body.get("line")
    store = body.get("store")
    fmt = body.get("format", "json")
    limit = min(body.get("limit", 500), 5000)
    db = get_db()
    q = "SELECT * FROM price_snapshots WHERE price > 0 AND queried_at >= ?"
    params: list = [since]
    if line:
        q += " AND line = ?"
        params.append(line)
    if store:
        q += " AND store = ?"
        params.append(store)
    q += " ORDER BY queried_at DESC LIMIT ?"
    params.append(limit)
    rows = db.execute(q, params).fetchall()
    db.close()
    data = [dict(r) for r in rows]
    prices = [r["price"] for r in data if r.get("price")]
    if fmt == "csv":
        buf = io.StringIO()
        if data:
            w = _csv.DictWriter(buf, fieldnames=data[0].keys())
            w.writeheader()
            w.writerows(data)
        return {"format": "csv", "data": buf.getvalue(), "total": len(data), "since": since}
    return {
        "format": "json",
        "total": len(data),
        "since": since,
        "data": data[:100],
        "stats": (
            {
                "avg_price": round(sum(prices) / len(prices), 2),
                "min_price": min(prices),
                "max_price": max(prices),
            }
            if prices
            else {}
        ),
    }
