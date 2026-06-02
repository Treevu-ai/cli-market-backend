"""Intelligence API v1 — granular data moat endpoints.

Endpoints:
  GET /v1/quality/flagged   Paginated quality anomalies
  GET /v1/prices            Paginated price snapshots (?clean=1)
  GET /v1/dispersion          Spread groups by subcategory
  GET /v1/basket              Canasta snapshot from DB
  GET /v1/coverage/matrix     Country × line coverage map
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from data_v1_service import (
    build_coverage_matrix,
    query_dispersion,
    query_flagged,
    query_prices,
)
from market_basket import build_canasta_snapshot
from market_core import get_db

router = APIRouter(tags=["intelligence"])


@router.get("/v1/quality/flagged")
def quality_flagged(
    reason: str | None = Query(None, description="discount | outlier | spread"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    db = get_db()
    try:
        return query_flagged(db, reason=reason, limit=limit, offset=offset)
    finally:
        db.close()


@router.get("/v1/prices")
def prices_v1(
    clean: bool = Query(True, alias="clean"),
    country: str | None = None,
    line: str | None = None,
    currency: str | None = None,
    store: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    db = get_db()
    try:
        return query_prices(
            db,
            clean=clean,
            country=country,
            line=line,
            currency=currency,
            store=store,
            limit=limit,
            offset=offset,
        )
    finally:
        db.close()


@router.get("/v1/dispersion")
def dispersion_v1(
    clean: bool = Query(True, alias="clean"),
    line: str | None = None,
    currency: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    db = get_db()
    try:
        return query_dispersion(
            db,
            clean=clean,
            line=line,
            currency=currency,
            limit=limit,
            offset=offset,
        )
    finally:
        db.close()


@router.get("/v1/basket")
def basket_snapshot_v1(
    stores: str | None = Query(None, description="Comma-separated store keys"),
    min_items: int = Query(3, ge=1, le=10),
):
    store_filter = None
    if stores:
        store_filter = {s.strip() for s in stores.split(",") if s.strip()}
    db = get_db()
    try:
        return build_canasta_snapshot(db, min_items=min_items, store_filter=store_filter)
    finally:
        db.close()


@router.get("/v1/coverage/matrix")
def coverage_matrix_v1(line: str | None = None):
    db = get_db()
    try:
        return build_coverage_matrix(db, line=line)
    finally:
        db.close()
