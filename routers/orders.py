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

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from market_core import (
    db_add_to_cart,
    db_clear_cart,
    db_create_order,
    db_get_cart,
    db_get_orders,
    get_db,
)
from server_deps import require_user
from market_core import user_can_checkout

router = APIRouter(tags=["orders"])


class CheckoutRequest(BaseModel):
    payment_method: str = "yape"


@router.post("/checkout")
def checkout(body: CheckoutRequest, authorization: str | None = Header(None)):
    """Generic checkout — legacy instant-complete when MARKET_LEGACY_CHECKOUT=1.

    Production: use POST /checkout/yape, /checkout/paypal, etc.
    """
    username = require_user(authorization)
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
    username = require_user(authorization)
    user_orders = db_get_orders(username)
    return {"username": username, "orders": user_orders, "total_orders": len(user_orders)}


@router.get("/orders/{order_id}")
def order_status(order_id: str, authorization: str | None = Header(None)):
    username = require_user(authorization)
    db = get_db()
    order = db.execute(
        "SELECT * FROM app_orders WHERE order_id=? AND username=?", (order_id, username)
    ).fetchone()
    if not order:
        db.close()
        raise HTTPException(status_code=404, detail="Order not found")
    items = db.execute(
        "SELECT * FROM app_order_items WHERE order_id=?", (order_id,)
    ).fetchall()
    db.close()
    return {"order": dict(order), "items": [dict(i) for i in items]}


@router.get("/orders/{order_id}/receipt")
def order_receipt(order_id: str, authorization: str | None = Header(None)):
    """Comprobante de pago — emitido por SINAPSIS INNOVADORA S.A.C.

    IMPORTANTE: Emisión MANUAL. No se envía automáticamente a SUNAT.
    Para facturación electrónica oficial, configure SUNAT_PSE_API_KEY + PSE.
    """
    username = require_user(authorization)
    db = get_db()
    order = db.execute(
        "SELECT * FROM app_orders WHERE order_id=? AND username=?", (order_id, username)
    ).fetchone()
    if not order:
        db.close()
        raise HTTPException(status_code=404, detail="Order not found")
    items = db.execute(
        "SELECT * FROM app_order_items WHERE order_id=?", (order_id,)
    ).fetchall()
    db.close()
    total_calc = round(sum(i["price"] * i["quantity"] for i in items), 2)
    return {
        "comprobante_id": f"SIM-{order_id}",
        "tipo": "BOLETA DE VENTA ELECTRÓNICA",
        "emisor": {
            "razon_social": "SINAPSIS INNOVADORA S.A.C.",
            "ruc": "20613045563",
            "direccion": "Lima, Perú",
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
            "Para facturación oficial contacte a SINAPSIS INNOVADORA S.A.C. RUC 20613045563."
        ),
    }


@router.post("/orders/reorder")
def reorder_last(authorization: str | None = Header(None)):
    username = require_user(authorization)
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
