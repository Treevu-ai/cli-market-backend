"""Cart endpoints — SQLite-backed shopping cart per user.

Endpoints:
  POST   /cart/add              Add a product (or increment quantity)
  GET    /cart                  View current cart + total
  PUT    /cart/update           Change quantity by cart_id or product_id
  DELETE /cart/{product_id}     Remove an item by cart_id or product_id
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from market_core import (
    STORES,
    db_add_to_cart,
    db_get_cart,
    db_remove_cart_item,
    db_update_cart_item,
)
from server_deps import require_user

router = APIRouter(tags=["cart"])


class AddToCartRequest(BaseModel):
    product_id: str
    name: str
    price: float
    store: str
    quantity: int = 1
    url: str = ""


class UpdateCartRequest(BaseModel):
    product_id: str
    quantity: int


def _cart_total(cart: list[dict]) -> float:
    return round(sum(i["price"] * i["quantity"] for i in cart), 2)


@router.post("/cart/add")
def cart_add(body: AddToCartRequest, authorization: str | None = Header(None)):
    username = require_user(authorization)
    store_name = STORES.get(body.store, {}).get("name", body.store)
    cart_id = db_add_to_cart(
        username, body.product_id, body.name, body.price,
        body.store, store_name, body.quantity, body.url or "",
    )
    cart = db_get_cart(username)
    return {
        "message": "Agregado al carrito",
        "cart": cart,
        "total": _cart_total(cart),
        "items": len(cart),
        "cart_id": str(cart_id),
    }


@router.get("/cart")
def view_cart(authorization: str | None = Header(None)):
    username = require_user(authorization)
    cart = db_get_cart(username)
    return {
        "username": username,
        "cart": cart,
        "total": _cart_total(cart),
        "items": len(cart),
    }


@router.put("/cart/update")
def cart_update(body: UpdateCartRequest, authorization: str | None = Header(None)):
    username = require_user(authorization)
    cart = db_get_cart(username)
    item = next(
        (i for i in cart if i["cart_id"] == body.product_id or i["product_id"] == body.product_id),
        None,
    )
    if not item:
        raise HTTPException(status_code=404, detail="Producto no encontrado en el carrito")
    db_update_cart_item(username, int(item["cart_id"]), body.quantity)
    cart = db_get_cart(username)
    return {"message": "Carrito actualizado", "cart": cart}


@router.delete("/cart/{product_id}")
def cart_remove(product_id: str, authorization: str | None = Header(None)):
    username = require_user(authorization)
    cart = db_get_cart(username)
    item = next(
        (i for i in cart if i["cart_id"] == product_id or i["product_id"] == product_id),
        None,
    )
    if not item:
        raise HTTPException(status_code=404, detail="Producto no encontrado en el carrito")
    db_remove_cart_item(username, int(item["cart_id"]))
    cart = db_get_cart(username)
    return {
        "message": "Producto eliminado del carrito",
        "cart": cart,
        "total": _cart_total(cart),
        "items": len(cart),
    }
