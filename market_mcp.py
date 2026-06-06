#!/usr/bin/env python3
"""
CLI Market MCP server — core tools + semantic index tools.

Shadows the cli-market-core market_mcp when run from the backend repo.
Adds index_resolve, index_lookup, index_stats (46 tools total).
"""

from __future__ import annotations

import importlib.util
import json
import site
import sys
from pathlib import Path


def _mcp_search_roots() -> list[Path]:
    roots: list[Path] = []
    for root in site.getsitepackages():
        roots.append(Path(root))
    try:
        roots.append(Path(site.getusersitepackages()))
    except AttributeError:
        pass
    return roots


def _load_core_mcp():
    here = Path(__file__).resolve()
    for root in _mcp_search_roots():
        path = root / "market_mcp.py"
        if not path.is_file() or path.resolve() == here:
            continue
        spec = importlib.util.spec_from_file_location("_market_mcp_core", path)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise ImportError("cli-market-core market_mcp not installed")


_core = _load_core_mcp()

INDEX_TOOLS = [
    {
        "name": "index_resolve",
        "description": (
            "Resolve a raw retailer product snapshot to a Golden Record (prod_*). "
            "Use when normalizing names across stores or building cross-retailer compare."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Raw product title from retailer"},
                "brand": {"type": "string"},
                "store": {"type": "string", "description": "Store key (wong, metro_pe, ...)"},
                "sku": {"type": "string", "description": "Retailer product_id / SKU"},
                "price": {"type": "number"},
                "currency": {"type": "string", "default": "USD"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "index_lookup",
        "description": "Fetch a canonical Golden Record by prod_* id from the semantic index.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "Golden Record id, e.g. prod_gloria_leche_1l"},
            },
            "required": ["product_id"],
        },
    },
    {
        "name": "index_stats",
        "description": (
            "Semantic moat metrics: registry size, snapshots linked to prod_*, linkage %."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]

TOOLS = _core.TOOLS + INDEX_TOOLS


def _index_resolve(args: dict) -> dict:
    from index_gate import index_resolve

    return index_resolve(args)


def _index_lookup(args: dict) -> dict:
    from index_gate import index_lookup

    product_id = args.get("product_id", "")
    result = index_lookup(product_id)
    if result is None:
        return {"error": f"Product {product_id} not found"}
    return result


def _index_stats(_args: dict) -> dict:
    from index_gate import index_stats

    return index_stats()


def handle_tool(name: str, args: dict) -> str:
    index_handlers = {
        "index_resolve": _index_resolve,
        "index_lookup": _index_lookup,
        "index_stats": _index_stats,
    }
    handler = index_handlers.get(name)
    if handler:
        try:
            return json.dumps(handler(args or {}), ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"error": str(exc)})
    return _core.handle_tool(name, args)


def main():
    """MCP JSON-RPC loop over stdio (core loop + index tools)."""
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
                    "serverInfo": {"name": "cli-market", "version": "1.1.0"},
                },
            }
        elif method == "tools/list":
            response = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": TOOLS},
            }
        elif method == "tools/call":
            params = request.get("params") or {}
            tool_name = params.get("name", "")
            tool_args = params.get("arguments") or {}
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

        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()