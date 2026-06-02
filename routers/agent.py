"""Agent-shaped endpoints — natural-language intent → action mapping + per-user prefs.

Endpoints:
  GET  /agent/preferences  User's order patterns (favorite stores, spend)
  POST /agent/ask          Natural-language → structured action ({action, query, ...})

This is the simplest possible natural-language layer; it intentionally does
NOT call an LLM. The action mapping is a finite-state classifier suitable
for an MCP-tool dispatch where the LLM is on the caller side.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Header
from pydantic import BaseModel

from market_core import db_get_orders
from server_deps import require_user

router = APIRouter(tags=["agent"])


class AskRequest(BaseModel):
    prompt: str


@router.get("/agent/preferences")
def agent_preferences(authorization: str | None = Header(None)):
    """Order history → favorite stores + total spent. Used by the CLI to
    personalize results."""
    username = require_user(authorization)
    user_orders = db_get_orders(username)
    stores: dict[str, float] = {}
    total_spent = 0.0
    for o in user_orders:
        total_spent += o.get("total", 0)
        for item in o.get("items", []):
            s = item.get("store_name", "?")
            stores[s] = stores.get(s, 0) + item.get("price", 0) * item.get("quantity", 1)
    return {
        "username": username,
        "total_orders": len(user_orders),
        "total_spent": round(total_spent, 2),
        "favorite_stores": sorted(stores.items(), key=lambda x: x[1], reverse=True)[:3],
    }


@router.post("/agent/ask")
async def agent_ask(body: AskRequest, authorization: str | None = Header(None)):
    """Map a natural-language prompt to a structured action dict.

    Action vocabulary: search, reorder, compare, cart, checkout.
    The MCP server uses this for chat-style intent dispatch.
    """
    prompt = body.prompt.lower().strip()
    if any(w in prompt for w in ("compra", "comprar", "agregar", "add")):
        words = re.sub(r"[^a-záéíóúñ ]", "", prompt).split()
        qty = 1
        for w in words:
            if w.isdigit():
                qty = int(w)
                break
        query = (
            prompt.replace("compra", "")
            .replace("comprar", "")
            .replace("agrega", "")
            .replace("agregar", "")
            .replace("add", "")
            .strip()
        )
        return {"action": "search", "query": query, "quantity": qty, "message": f"Buscando '{query}'..."}
    if any(w in prompt for w in ("repite", "repetir", "reorder")):
        return {"action": "reorder", "message": "Repitiendo última orden..."}
    if any(w in prompt for w in ("compara", "comparar", "compare")):
        query = prompt.replace("compara", "").replace("comparar", "").replace("compare", "").strip()
        return {"action": "compare", "query": query, "message": f"Comparando '{query}'..."}
    if any(w in prompt for w in ("carrito", "cart", "ver")):
        return {"action": "cart", "message": "Mostrando carrito..."}
    if any(w in prompt for w in ("pagar", "checkout", "finalizar")):
        return {"action": "checkout", "message": "Iniciando checkout..."}
    return {"action": "search", "query": prompt, "quantity": 1, "message": f"Buscando '{prompt}'..."}
