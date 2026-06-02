#!/usr/bin/env python3
"""
market-mcp — MCP server para Agentic Market.

Expone búsqueda, carrito y checkout como tools MCP (JSON-RPC sobre stdio)
para que cualquier agente IA (DeepSeek TUI, Claude, etc.) pueda operar
el supermercado.

Uso:
    python market_mcp.py
    → se conecta vía stdio, listo para MCP
"""

import json
import sys


# Re-exports kept for backwards compat — tests/test_server.py verifies these
# are reachable through market_mcp. Ruff would otherwise drop the unused ones.
from market_core import API, SESSION_FILE, api, get_token  # noqa: F401

TOOLS = [
    {
        "name": "market_login",
        "description": "Authenticate in CLI Market. CALL FIRST before cart, checkout, or orders. Returns session token persisted locally. On 401 errors, re-run this tool.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "username": {"type": "string"},
                "password": {"type": "string"},
            },
        },
    },
    {
        "name": "market_lines",
        "description": "Listar las 6 líneas de negocio verificadas (supermercados, farmacias, electro, hogar, departamentales, moda) con sus retailers VTEX, países y monedas.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "market_search",
        "description": "Search products across 30 verified retailers (8 countries). Returns JSON with product_id, name, price, store_key (required for market_add), store, line. Filter by line (supermercados, farmacias, electro) or store ID from market_stores. Prefer this over scraping.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Término de búsqueda"},
                "store": {"type": "string", "description": "ID de tienda específica (vacío = todas). Usar market_lines para ver IDs válidos."},
                "line": {"type": "string", "description": "Línea de negocio: supermercados, farmacias, electro, moda, deportes, hogar"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "market_compare",
        "description": "Compare prices for one query across all retailers side-by-side. Use when user asks 'cheapest' or 'compare X across stores'. For single-store lookup use market_search instead.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Producto a comparar"},
                "line": {"type": "string", "description": "Línea de negocio: supermercados, farmacias, electro, moda, deportes, hogar"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "market_add",
        "description": "Add to cart. Copy ALL four fields from market_search result: product_id, name, price, store (use store_key value as store). Missing fields cause 422 — re-run market_search if needed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "Copiar del campo 'product_id' en market_search"},
                "name":       {"type": "string", "description": "Copiar del campo 'name' en market_search"},
                "price":      {"type": "number", "description": "Copiar del campo 'price' en market_search"},
                "store":      {"type": "string", "description": "Copiar del campo 'store_key' en market_search"},
                "quantity":   {"type": "integer", "default": 1},
            },
            "required": ["product_id", "name", "price", "store"],
        },
    },
    {
        "name": "market_cart",
        "description": "Ver carrito actual con productos, cantidades, precios y total.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "market_cart_update",
        "description": "Cambiar la cantidad de un producto en el carrito. Usar quantity=0 para eliminar.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "ID del producto en el carrito (campo 'product_id')"},
                "quantity":   {"type": "integer", "description": "Nueva cantidad (0 = eliminar)"},
            },
            "required": ["product_id", "quantity"],
        },
    },
    {
        "name": "market_cart_remove",
        "description": "Eliminar un producto del carrito por su ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "ID del producto a eliminar del carrito"},
            },
            "required": ["product_id"],
        },
    },
    {
        "name": "market_checkout",
        "description": "Complete purchase. Requires items in cart (market_cart first). payment_method: yape, plin, paypal, tarjeta. Sandbox checkout — not a live charge unless Pro tier.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "payment_method": {"type": "string", "default": "yape", "description": "yape | plin | paypal | tarjeta"},
            },
        },
    },
    {
        "name": "market_orders",
        "description": "Ver historial de órdenes.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "market_reorder",
        "description": "Repetir la última orden.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "market_ask",
        "description": "Compra por lenguaje natural. 'compra leche', 'repite la última compra', 'compara arroz'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Instrucción en lenguaje natural"},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "market_basket",
        "description": "Comparar el costo total de una canasta de productos entre retailers. Pasa una lista de items con nombre y cantidad. Retorna el total por tienda y cuál es la más barata.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "items": {"type": "array", "description": "Lista de productos, ej: [{\"name\":\"leche\",\"qty\":2},{\"name\":\"arroz\",\"qty\":1}]"},
                "stores": {"type": "array", "description": "Lista opcional de stores. Vacío = todos los retailers."},
            },
            "required": ["items"],
        },
    },
    {
        "name": "market_inflation",
        "description": "Consultar la variación de precios (inflación) desde el data moat. Retorna productos con su delta de precio y un promedio de inflación. Filtrar por país o línea.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "country": {"type": "string", "description": "Código de país: AR, BR, MX, CO, PE, CL, IT, FR"},
                "line": {"type": "string", "description": "Línea de negocio: supermercados, farmacias, electro, hogar"},
                "days": {"type": "integer", "description": "Ventana de días para el análisis (default 30)"},
            },
        },
    },
    {
        "name": "market_indicators",
        "description": "Catálogo de indicadores del data moat (promo intensity, FX, CPI oficial, basket stress, etc.).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "market_scores",
        "description": "Scores compuestos del moat: retail_aggression, price_fairness, basket_stress, data_confidence, macro_alignment.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "country": {"type": "string", "description": "PE, AR, MX, BR, CO, CL"},
                "line": {"type": "string", "description": "supermercados, farmacias, electro"},
            },
        },
    },
    {
        "name": "market_intel_refresh",
        "description": "Recalcular indicadores internos y fetch de APIs públicas (FX, World Bank CPI, OFF, Wikimedia, clima).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "country": {"type": "string"},
                "line": {"type": "string"},
            },
        },
    },
    {
        "name": "market_enrichment",
        "description": "Indicadores de enriquecimiento: Open Food Facts match rate, NOVA, Wikimedia demand, clima logístico, food CPI.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "country": {"type": "string", "description": "PE, AR, MX, BR, CO, CL"},
            },
        },
    },
    {
        "name": "market_enrichment_subcategories",
        "description": "Enriquecimiento por subcategoría de canasta (10 básicos): momentum precio, wiki, min precio.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "country": {"type": "string", "description": "PE, AR, MX, BR, CO, CL"},
            },
        },
    },
    {
        "name": "market_enrichment_refresh",
        "description": "Refrescar solo indicadores de enriquecimiento (OFF, Wiki, clima, food CPI) sin recalcular todo el moat.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "country": {"type": "string", "description": "PE, AR, MX, BR, CO, CL"},
            },
        },
    },
    {
        "name": "market_categories",
        "description": "Explorar el árbol de categorías de un retailer VTEX. Retorna la jerarquía completa de categorías y subcategorías.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "store": {"type": "string", "description": "ID de la tienda (usar market_lines para ver IDs válidos)"},
            },
            "required": ["store"],
        },
    },
    {
        "name": "market_barcode",
        "description": "Buscar producto por código de barras EAN/UPC.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Código de barras EAN/UPC"},
            },
            "required": ["code"],
        },
    },
    {
        "name": "market_enrich",
        "description": "Buscar productos en Open Food Facts para enriquecer con datos nutricionales.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Producto a buscar"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "market_stores",
        "description": "Listar los 30 retailers verificados con país, moneda, línea de negocio y emoji. Usar para descubrir qué tiendas están disponibles.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "market_countries",
        "description": "Listar los 8 países disponibles con sus retailers y conteo de tiendas por país.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    # ── New tools (20–30) ────────────────────────────────────────────────────
    {
        "name": "market_ticket",
        "description": "Escanear ticket de compra vía OCR y comparar precios contra el data moat. Pasa la URL pública de la imagen del ticket.",
        "inputSchema": {"type":"object","properties":{"url":{"type":"string","description":"URL de la imagen del ticket (.jpg,.png)"},"country":{"type":"string","description":"Código de país (opcional): PE,AR,BR,MX,CO,CL"}},"required":["url"]},
    },
    {
        "name": "market_voice",
        "description": "Transcribir audio de voz a texto. Pasa la URL pública del archivo de audio (.ogg,.mp3,.wav).",
        "inputSchema": {"type":"object","properties":{"url":{"type":"string","description":"URL del archivo de audio"}},"required":["url"]},
    },
    {
        "name": "market_price_history",
        "description": "Historial de precios de un producto. Muestra la evolución del precio en el data moat.",
        "inputSchema": {"type":"object","properties":{"product_id":{"type":"string"},"store":{"type":"string"},"line":{"type":"string"},"limit":{"type":"integer","default":50}}},
    },
    {
        "name": "market_stats",
        "description": "Estadísticas del data moat: total de precios, tiendas activas, productos rastreados, última actualización.",
        "inputSchema": {"type":"object","properties":{}},
    },
    {
        "name": "market_analytics_indicators",
        "description": "Últimos valores de indicadores del moat (promo intensity, FX, CPI, basket stress, scores inputs).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "country": {"type": "string"},
                "line": {"type": "string"},
                "limit": {"type": "integer", "default": 30},
            },
        },
    },
    {
        "name": "market_alerts",
        "description": "Alertas de precio: productos que bajaron más de X% en los últimos días.",
        "inputSchema": {"type":"object","properties":{"product":{"type":"string","description":"Producto a monitorear"},"store":{"type":"string"},"threshold_pct":{"type":"number","default":5.0},"limit":{"type":"integer","default":10}},"required":["product"]},
    },
    {
        "name": "market_whoami",
        "description": "Verificar identidad: username y tier de suscripción del usuario autenticado.",
        "inputSchema": {"type":"object","properties":{}},
    },
    {
        "name": "market_preferences",
        "description": "Preferencias del usuario basadas en historial de compras: tiendas favoritas, total gastado.",
        "inputSchema": {"type":"object","properties":{}},
    },
    {
        "name": "market_subscription",
        "description": "Consultar el plan de suscripción actual: tier, rate limits, API keys disponibles.",
        "inputSchema": {"type":"object","properties":{}},
    },
    {
        "name": "market_export",
        "description": "Exportar datos del data moat en formato CSV o JSON.",
        "inputSchema": {"type":"object","properties":{"country":{"type":"string"},"line":{"type":"string"},"format":{"type":"string","default":"json"},"limit":{"type":"integer","default":100}}},
    },
    {
        "name": "market_trending",
        "description": "Productos con mayor movimiento de precio en los últimos 7 días.",
        "inputSchema": {"type":"object","properties":{"country":{"type":"string"},"line":{"type":"string"},"limit":{"type":"integer","default":10}}},
    },
    {
        "name": "market_scan",
        "description": "Escanear nuevas tiendas VTEX. Busca retailers que respondan a la API VTEX y retorna candidatos verificados.",
        "inputSchema": {"type":"object","properties":{"line":{"type":"string","description":"Filtrar por línea de negocio (opcional)"}}},
    },
    {
        "name": "market_stock",
        "description": "Verificar disponibilidad de stock de un producto en una tienda específica.",
        "inputSchema": {"type":"object","properties":{"product_id":{"type":"string"},"store":{"type":"string"}},"required":["product_id","store"]},
    },
    {
        "name": "market_brands",
        "description": "Listar las marcas más frecuentes en el data moat. Filtrar por línea o país.",
        "inputSchema": {"type":"object","properties":{"line":{"type":"string"},"country":{"type":"string"},"limit":{"type":"integer","default":20}}},
    },
    {
        "name": "market_favorites",
        "description": "Gestionar productos favoritos: listar, agregar o eliminar. Sin action=list retorna la lista.",
        "inputSchema": {"type":"object","properties":{"action":{"type":"string","description":"list, add, remove"},"product_id":{"type":"string"},"name":{"type":"string"},"store":{"type":"string"}}},
    },
    {
        "name": "market_notify",
        "description": "Configurar alertas de precio. Recibir notificación cuando un producto baje del umbral.",
        "inputSchema": {"type":"object","properties":{"product":{"type":"string"},"store":{"type":"string"},"threshold_pct":{"type":"number","default":5.0}},"required":["product"]},
    },
    {
        "name": "market_exchange",
        "description": "Convertir montos entre monedas de los países donde operamos (PEN, ARS, BRL, MXN, COP, CLP, EUR).",
        "inputSchema": {"type":"object","properties":{"amount":{"type":"number"},"from_currency":{"type":"string"},"to_currency":{"type":"string"}},"required":["amount","from_currency","to_currency"]},
    },
    {
        "name": "market_delivery",
        "description": "Consultar opciones de entrega disponibles para un producto y código postal.",
        "inputSchema": {"type":"object","properties":{"product_id":{"type":"string"},"store":{"type":"string"},"zipcode":{"type":"string"}},"required":["product_id","store"]},
    },
]


def _checkout_api(args: dict) -> dict:
    pm = (args.get("payment_method") or "yape").lower()
    routes = {"yape": "/checkout/yape", "plin": "/checkout/yape", "paypal": "/checkout/paypal", "tarjeta": "/checkout/paypal"}
    return api("POST", routes.get(pm, "/checkout/yape"), {})


def handle_tool(name: str, args: dict) -> str:
    """Dispatch MCP tool calls to the API."""
    tool_map = {
        "market_login":      lambda a: api("POST", "/auth/login", {"username": a["username"], "password": a["password"]}),
        "market_lines":      lambda a: api("GET", "/lines"),
        "market_search":     lambda a: api("POST", "/products/search", {"query": a["query"], "store": a.get("store"), "line": a.get("line"), "limit": a.get("limit", 10)}),
        "market_compare":    lambda a: api("POST", "/products/compare", {"query": a["query"], "line": a.get("line"), "limit": a.get("limit", 10)}),
        "market_add":        lambda a: api("POST", "/cart/add", {"product_id": a["product_id"], "name": a["name"], "price": a["price"], "store": a["store"], "quantity": a.get("quantity", 1)}),
        "market_cart":       lambda a: api("GET", "/cart"),
        "market_cart_update": lambda a: api("PUT", "/cart/update", {"product_id": a["product_id"], "quantity": a["quantity"]}),
        "market_cart_remove": lambda a: api("DELETE", f"/cart/{a['product_id']}"),
        "market_checkout":   lambda a: _checkout_api(a),
        "market_orders":     lambda a: api("GET", "/orders"),
        "market_reorder":    lambda a: api("POST", "/orders/reorder"),
        "market_ask":        lambda a: api("POST", "/agent/ask", {"prompt": a["prompt"]}),
        "market_basket":     lambda a: api("POST", "/v1/basket/compare", {"items": a["items"], "stores": a.get("stores")}),
        "market_inflation":  lambda a: api("GET", f"/v1/intel/inflation?country={a.get('country', '')}&line={a.get('line', '')}"),
        "market_indicators": lambda a: api("GET", "/v1/intel/indicators"),
        "market_scores":     lambda a: api("GET", f"/v1/intel/scores?country={a.get('country', '')}&line={a.get('line', '')}"),
        "market_intel_refresh": lambda a: api("POST", f"/v1/intel/refresh?country={a.get('country', '')}&line={a.get('line', '')}"),
        "market_enrichment": lambda a: api("GET", f"/v1/intel/enrichment?country={a.get('country', '')}"),
        "market_enrichment_refresh": lambda a: api("POST", f"/v1/intel/enrichment/refresh?country={a.get('country', '')}"),
        "market_enrichment_subcategories": lambda a: api("GET", f"/v1/intel/enrichment/subcategories?country={a.get('country', 'PE')}"),
        "market_categories": lambda a: api("GET", f"/categories/{a['store']}"),
        "market_barcode":    lambda a: api("GET", f"/products/barcode/{a['code']}"),
        "market_enrich":     lambda a: api("GET", f"/products/enrich?query={a['query']}&limit={a.get('limit', 5)}"),
        "market_stores":     lambda a: api("GET", "/stores"),
        "market_countries":  lambda a: api("GET", "/countries"),
        # ── New handlers (20–30) ──
        "market_ticket":     lambda a: api("POST", "/v1/ticket/scan-url", {"url": a["url"], "country": a.get("country")}),
        "market_voice":      lambda a: api("POST", "/v1/voice/transcribe-url", {"url": a["url"]}),
        "market_price_history": lambda a: api("GET", f"/analytics/price-history?product_id={a.get('product_id','')}&store={a.get('store','')}&line={a.get('line','')}&limit={a.get('limit',50)}"),
        "market_stats":      lambda a: api("GET", "/analytics/stats"),
        "market_analytics_indicators": lambda a: api(
            "GET",
            f"/analytics/indicators?country={a.get('country', '')}&line={a.get('line', '')}&limit={a.get('limit', 30)}",
        ),
        "market_alerts":     lambda a: api("GET", f"/v1/intel/alerts?product={a['product']}&store={a.get('store','')}&threshold_pct={a.get('threshold_pct',5.0)}&limit={a.get('limit',10)}"),
        "market_whoami":     lambda a: api("GET", "/auth/whoami"),
        "market_preferences": lambda a: api("GET", "/agent/preferences"),
        "market_subscription": lambda a: api("GET", "/auth/subscription"),
        "market_export":     lambda a: api("POST", "/v1/data/export", {"country": a.get("country"), "line": a.get("line"), "format": a.get("format", "json"), "limit": a.get("limit", 100)}),
        "market_trending":   lambda a: api("GET", f"/analytics/trending?country={a.get('country','')}&line={a.get('line','')}&limit={a.get('limit',10)}"),
        "market_scan":       lambda a: api("POST", "/v1/admin/scan-stores", {"line": a.get("line")}),
        "market_stock":      lambda a: api("GET", f"/products/stock/{a['product_id']}?store={a['store']}"),
        "market_brands":     lambda a: api("GET", f"/analytics/brands?line={a.get('line','')}&country={a.get('country','')}&limit={a.get('limit',20)}"),
        "market_favorites":  lambda a: api("POST", "/favorites", {"action": a.get("action","list"), "product_id": a.get("product_id",""), "name": a.get("name",""), "store": a.get("store","")}),
        "market_notify":     lambda a: api("GET", f"/v1/intel/alerts?product={a['product']}&store={a.get('store','')}&threshold_pct={a.get('threshold_pct',5.0)}"),
        "market_exchange":   lambda a: api("POST", "/v1/utils/exchange", {"amount": a["amount"], "from": a["from_currency"], "to": a["to_currency"]}),
        "market_delivery":   lambda a: api("GET", f"/products/delivery/{a['product_id']}?store={a['store']}&zipcode={a.get('zipcode','')}"),
    }
    handler = tool_map.get(name)
    if not handler:
        return json.dumps({"error": f"Unknown tool: {name}"})
    try:
        result = handler(args)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})


def main():
    """MCP JSON-RPC loop over stdio."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = request.get("method", "")
        req_id = request.get("id")

        if method == "initialize":
            response = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "cli-market", "version": "1.0.0"},
                },
            }
        elif method == "tools/list":
            response = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": TOOLS},
            }
        elif method == "tools/call":
            tool_name = request["params"]["name"]
            tool_args = request["params"].get("arguments", {})
            content = handle_tool(tool_name, tool_args)
            response = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": content}]},
            }
        else:
            response = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }

        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
