"""Orders, checkout (default), and receipt endpoints.

Endpoints:
  POST /checkout                 Default checkout (no payment method gateway)
  GET  /orders                   List user's orders
  GET  /orders/{order_id}        Order detail (with items)
  GET  /orders/{order_id}/receipt  Manual Peruvian sales receipt (BOLETA — NOT SUNAT-emitted)
  POST /orders/reorder           Restore last order into the cart

Payment-method-specific endpoints (/checkout/yape, /checkout/lemon, etc.)
live in routers/payments.py.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from market_connectors.sunat_invoicing import get_company, get_sunat_ruc
from market_core import (
    db_add_to_cart,
    db_clear_cart,
    db_create_order,
    db_get_cart,
    db_get_orders,
)
from server_deps import get_db_dep, require_api_key
from market_core import user_can_checkout
from index_gate import enrich_list

router = APIRouter(tags=["orders"])


class CheckoutRequest(BaseModel):
    payment_method: str = "yape"


@router.post("/checkout")
def checkout(body: CheckoutRequest, authorization: str | None = Header(None)):
    """Generic checkout — legacy instant-complete when MARKET_LEGACY_CHECKOUT=1.

    Production: use POST /checkout/yape, /checkout/paypal, etc.
    """
    username = require_api_key(authorization)
    if not user_can_checkout(username):
        raise HTTPException(
            status_code=403,
            detail="Checkout requires Pro. Use: market upgrade — or gateway /checkout/yape",
        )
    if os.getenv("MARKET_LEGACY_CHECKOUT", "").lower() not in ("1", "true", "yes"):
        raise HTTPException(
            status_code=400,
            detail=(
                "Use a payment gateway: POST /checkout/yape, /checkout/paypal, "
                "/checkout/wise, or /checkout/lemon"
            ),
        )
    cart = db_get_cart(username)
    if not cart:
        raise HTTPException(status_code=400, detail="Carrito vacío")
    total = round(sum(i["price"] * i["quantity"] for i in cart), 2)
    order = db_create_order(username, cart, body.payment_method, total)
    db_clear_cart(username)
    return {"message": "Compra completada", "order": order}


@router.get("/orders")
def order_history(authorization: str | None = Header(None)):
    username = require_api_key(authorization)
    user_orders = db_get_orders(username)
    return {"username": username, "orders": user_orders, "total_orders": len(user_orders)}


def _fetch_order_with_items(db, order_id: str, username: str):
    """Return (order_row, items_rows) in one JOIN query, or (None, []) if not found."""
    rows = db.execute(
        """SELECT o.*, i.id AS item_id, i.name, i.price, i.quantity,
                  i.product_id, i.store, i.store_name, i.url
           FROM app_orders o
           LEFT JOIN app_order_items i ON i.order_id = o.order_id
           WHERE o.order_id = ? AND o.username = ?""",
        (order_id, username),
    ).fetchall()
    if not rows:
        return None, []
    order = {k: rows[0][k] for k in rows[0].keys() if not k.startswith("item_") and k not in
             ("name", "price", "quantity", "product_id", "store", "store_name", "url")}
    items = [
        {k: r[k] for k in ("item_id", "name", "price", "quantity",
                            "product_id", "store", "store_name", "url", "order_id")}
        for r in rows if r["item_id"] is not None
    ]
    return order, items


@router.get("/orders/{order_id}")
def order_status(order_id: str, authorization: str | None = Header(None), db = Depends(get_db_dep)):
    username = require_api_key(authorization)
    order, items = _fetch_order_with_items(db, order_id, username)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    enrich_list(items)
    return {"order": order, "items": items}


@router.get("/orders/{order_id}/receipt")
def order_receipt(order_id: str, authorization: str | None = Header(None), db = Depends(get_db_dep)):
    """Comprobante de pago — emitido por SINAPSIS INNOVADORA S.A.C.

    IMPORTANTE: Emisión MANUAL. No se envía automáticamente a SUNAT.
    Para facturación electrónica oficial, configure SUNAT_PSE_API_KEY + PSE.
    """
    username = require_api_key(authorization)
    order, items = _fetch_order_with_items(db, order_id, username)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    company = get_company()
    total_calc = round(sum(i["price"] * i["quantity"] for i in items), 2)
    return {
        "comprobante_id": f"SIM-{order_id}",
        "tipo": "BOLETA DE VENTA ELECTRÓNICA",
        "emisor": {
            "razon_social": company["razon_social"],
            "ruc": company["ruc"],
            "direccion": company["direccion"],
        },
        "cliente": username,
        "orden_id": order_id,
        "fecha_emision": datetime.now(timezone.utc).isoformat(),
        "metodo_pago": order["payment_method"],
        "estado": order["status"],
        "items": [
            {
                "producto": i["name"],
                "cantidad": i["quantity"],
                "precio_unitario": i["price"],
                "subtotal": round(i["price"] * i["quantity"], 2),
            }
            for i in items
        ],
        "subtotal": total_calc,
        "igv": round(total_calc * 0.18, 2),
        "total": round(total_calc * 1.18, 2),
        "moneda": "PEN",
        "nota": (
            "COMPROBANTE DE EMISIÓN MANUAL — No válido como factura electrónica SUNAT. "
            f"Para facturación oficial contacte a {company['razon_social']} RUC {get_sunat_ruc()}."
        ),
    }


@router.post("/orders/reorder")
def reorder_last(authorization: str | None = Header(None)):
    username = require_api_key(authorization)
    user_orders = db_get_orders(username)
    if not user_orders:
        raise HTTPException(status_code=404, detail="Sin órdenes previas")
    last = user_orders[-1]
    db_clear_cart(username)
    for item in last.get("items", []):
        db_add_to_cart(
            username,
            item.get("product_id", ""),
            item.get("name", ""),
            item.get("price", 0),
            item.get("store", ""),
            item.get("store_name", ""),
            item.get("quantity", 1),
            item.get("url", ""),
        )
    cart = db_get_cart(username)
    return {"message": "Última orden restaurada al carrito", "cart": cart}
