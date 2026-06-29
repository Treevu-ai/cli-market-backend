"""Agent-shaped endpoints — LLM-powered intent orchestration + per-user prefs.

Endpoints:
  GET  /agent/preferences  User's order patterns (favorite stores, spend)
  POST /agent/ask          Natural-language → Claude Haiku agent with tool use

market_ask wires Claude Haiku to three internal tools:
  - search_products(query, country, limit)
  - compare_basket(items, country)
  - get_stores(country)

When ANTHROPIC_API_KEY is not set, falls back to the original regex classifier
so existing integrations don't break.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, Header
from pydantic import BaseModel

from market_core import STORES, db_get_orders, get_default_stores
from server_deps import require_api_key

logger = logging.getLogger("market.server").getChild("agent")

router = APIRouter(tags=["agent"])

_ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 1024
_MAX_TOOL_ROUNDS = 3

# ── Tool definitions exposed to the LLM ─────────────────────────────────────

_TOOLS: list[dict] = [
    {
        "name": "search_products",
        "description": (
            "Busca productos en el moat de CLI Market (supermercados y retailers LATAM). "
            "Devuelve lista de productos con precio, tienda y stock. "
            "Nota: solo cubre productos envasados/secos de supermercados, no perecibles frescos."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Término de búsqueda, ej: 'arroz integral 1kg'"},
                "country": {"type": "string", "description": "Código de país ISO-2, ej: 'PE', 'CL', 'MX'", "default": "PE"},
                "limit": {"type": "integer", "description": "Máximo de resultados (1-20)", "default": 8},
            },
            "required": ["query"],
        },
    },
    {
        "name": "compare_basket",
        "description": (
            "Compara una canasta de múltiples productos entre tiendas de un país. "
            "Útil para procurement: dado una lista de ítems, devuelve cuál tienda cubre "
            "más ítems al menor costo total."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "Lista de ítems a comparar",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "qty": {"type": "number", "default": 1},
                        },
                        "required": ["name"],
                    },
                },
                "country": {"type": "string", "description": "Código de país ISO-2", "default": "PE"},
            },
            "required": ["items"],
        },
    },
    {
        "name": "get_stores",
        "description": "Lista los retailers activos en CLI Market para un país dado.",
        "input_schema": {
            "type": "object",
            "properties": {
                "country": {"type": "string", "description": "Código de país ISO-2", "default": "PE"},
            },
        },
    },
]

_SYSTEM_PROMPT = """Eres un asistente de procurement y compras para LATAM que trabaja sobre el moat de CLI Market.

Tienes acceso a datos reales de precios verificados de supermercados y retailers en Perú, Chile, México, Colombia y otros países LATAM.

Reglas:
- Responde siempre en el mismo idioma que el usuario.
- Usa las tools disponibles para obtener datos reales antes de responder precios.
- Si el usuario pide una canasta con múltiples productos, usa compare_basket en una sola llamada.
- Si busca un producto específico, usa search_products.
- Sé conciso: presenta los resultados en formato tabla o lista con precio y tienda.
- Advierte explícitamente si el producto pedido puede ser perecible fresco (frutas, verduras, pan artesanal) ya que esos NO están en el catálogo.
- Nunca inventes precios. Si una búsqueda no devuelve resultados, dilo.
- Máximo 3 llamadas a tools por respuesta."""


# ── Tool execution (calls internal router functions directly) ────────────────

async def _exec_search_products(query: str, country: str = "PE", limit: int = 8) -> dict:
    from routers.search import _search_products, SearchRequest
    try:
        req = SearchRequest(query=query, country=country.upper(), limit=min(limit, 20))
        result = await _search_products(req)
        products = result.get("products", [])
        return {
            "total": result.get("total", len(products)),
            "products": [
                {
                    "name": p.get("name", ""),
                    "price": p.get("price"),
                    "currency": p.get("currency", ""),
                    "store": p.get("store_name", p.get("store", "")),
                    "brand": p.get("brand", ""),
                    "stock": p.get("stock"),
                }
                for p in products[:limit]
            ],
        }
    except Exception as e:
        logger.warning("search_products tool error: %s", e)
        return {"error": str(e), "products": []}


async def _exec_compare_basket(items: list[dict], country: str = "PE") -> dict:
    from routers.search import _fetch_basket_store
    import asyncio, os
    try:
        cc = country.upper()
        stores = [
            k for k, v in STORES.items()
            if v.get("country") == cc and not v.get("disabled")
        ]
        parallel_batch = int(os.getenv("BASKET_PARALLEL_BATCH", "8"))
        timeout_s = float(os.getenv("BASKET_TIMEOUT", "20.0"))
        results: dict[str, dict] = {}
        for i in range(0, len(stores), parallel_batch):
            batch = stores[i: i + parallel_batch]
            try:
                batch_results = await asyncio.wait_for(
                    asyncio.gather(*[_fetch_basket_store(s, items) for s in batch]),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                break
            for store, store_data in batch_results:
                if store_data is not None:
                    results[store] = store_data
        best = min(results, key=lambda s: results[s]["total"]) if results else None
        return {
            "basket": items,
            "comparison": results,
            "best_store": best,
            "best_total": results[best]["total"] if best else None,
            "stores_compared": len(results),
        }
    except Exception as e:
        logger.warning("compare_basket tool error: %s", e)
        return {"error": str(e)}


def _exec_get_stores(country: str = "PE") -> dict:
    cc = country.upper()
    stores = [
        {"id": k, "name": v["name"], "line": v.get("line", ""), "currency": v.get("currency", "")}
        for k, v in STORES.items()
        if v.get("country") == cc and not v.get("disabled")
    ]
    return {"country": cc, "stores": stores, "total": len(stores)}


async def _dispatch_tool(name: str, inputs: dict) -> Any:
    if name == "search_products":
        return await _exec_search_products(
            query=inputs["query"],
            country=inputs.get("country", "PE"),
            limit=inputs.get("limit", 8),
        )
    if name == "compare_basket":
        return await _exec_compare_basket(
            items=inputs["items"],
            country=inputs.get("country", "PE"),
        )
    if name == "get_stores":
        return _exec_get_stores(country=inputs.get("country", "PE"))
    return {"error": f"unknown tool: {name}"}


# ── Anthropic API caller ─────────────────────────────────────────────────────

async def _run_agent(prompt: str, country: str | None) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return _regex_fallback(prompt)

    user_content = prompt
    if country:
        user_content = f"[País: {country.upper()}] {prompt}"

    messages: list[dict] = [{"role": "user", "content": user_content}]
    tools_used: list[str] = []

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        for _round in range(_MAX_TOOL_ROUNDS):
            payload = {
                "model": _MODEL,
                "max_tokens": _MAX_TOKENS,
                "system": _SYSTEM_PROMPT,
                "tools": _TOOLS,
                "messages": messages,
            }
            try:
                resp = await client.post(_ANTHROPIC_API_URL, json=payload, headers=headers)
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                logger.error("Anthropic API error %s: %s", e.response.status_code, e.response.text[:200])
                return _regex_fallback(prompt)
            except Exception as e:
                logger.error("Anthropic API call failed: %s", e)
                return _regex_fallback(prompt)

            data = resp.json()
            stop_reason = data.get("stop_reason")
            content = data.get("content", [])

            # Append assistant turn
            messages.append({"role": "assistant", "content": content})

            if stop_reason == "end_turn":
                # Extract final text response
                text = " ".join(
                    block["text"] for block in content if block.get("type") == "text"
                ).strip()
                return {
                    "answer": text,
                    "tools_used": tools_used,
                    "model": _MODEL,
                    "rounds": _round + 1,
                }

            if stop_reason == "tool_use":
                tool_results = []
                for block in content:
                    if block.get("type") != "tool_use":
                        continue
                    tool_name = block["name"]
                    tool_id = block["id"]
                    tool_input = block.get("input", {})
                    tools_used.append(tool_name)
                    logger.info("agent tool_use: %s(%s)", tool_name, list(tool_input.keys()))
                    result = await _dispatch_tool(tool_name, tool_input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": json.dumps(result, ensure_ascii=False),
                    })
                messages.append({"role": "user", "content": tool_results})
                continue

            # Unexpected stop reason
            break

    # Exhausted rounds — return whatever text we have
    last_text = ""
    for block in (content if isinstance(content, list) else []):
        if block.get("type") == "text":
            last_text = block.get("text", "")
    return {
        "answer": last_text or "No pude completar la consulta en el límite de iteraciones.",
        "tools_used": tools_used,
        "model": _MODEL,
        "rounds": _MAX_TOOL_ROUNDS,
    }


# ── Regex fallback (original behavior) ──────────────────────────────────────

def _regex_fallback(prompt: str) -> dict:
    import re as _re
    p = prompt.lower().strip()
    if any(w in p for w in ("compra", "comprar", "agregar", "add")):
        words = _re.sub(r"[^a-záéíóúñ ]", "", p).split()
        qty = next((int(w) for w in words if w.isdigit()), 1)
        query = (
            p.replace("compra", "").replace("comprar", "")
            .replace("agrega", "").replace("agregar", "").replace("add", "").strip()
        )
        return {"action": "search", "query": query, "quantity": qty, "message": f"Buscando '{query}'..."}
    if any(w in p for w in ("repite", "repetir", "reorder")):
        return {"action": "reorder", "message": "Repitiendo última orden..."}
    if any(w in p for w in ("compara", "comparar", "compare")):
        query = p.replace("compara", "").replace("comparar", "").replace("compare", "").strip()
        return {"action": "compare", "query": query, "message": f"Comparando '{query}'..."}
    if any(w in p for w in ("carrito", "cart", "ver")):
        return {"action": "cart", "message": "Mostrando carrito..."}
    if any(w in p for w in ("pagar", "checkout", "finalizar")):
        return {"action": "checkout", "message": "Iniciando checkout..."}
    return {"action": "search", "query": prompt, "quantity": 1, "message": f"Buscando '{prompt}'..."}


# ── Request / response models ────────────────────────────────────────────────

class AskRequest(BaseModel):
    prompt: str
    country: str | None = None
    budget: float | None = None


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/agent/preferences")
def agent_preferences(authorization: str | None = Header(None)):
    """Order history → favorite stores + total spent."""
    username = require_api_key(authorization)
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
    """Natural-language procurement query → Claude Haiku agent with tool use.

    Uses three internal tools (search_products, compare_basket, get_stores) and
    returns a structured answer with cited prices. Falls back to keyword
    classification when ANTHROPIC_API_KEY is not configured.
    """
    require_api_key(authorization)
    return await _run_agent(body.prompt, body.country)
