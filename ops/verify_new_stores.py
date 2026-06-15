#!/usr/bin/env python3
"""
Verificación de endpoints VTEX para stores EC/BO/UY recién añadidos.

Uso:
    python3 ops/verify_new_stores.py              # solo verificar
    python3 ops/verify_new_stores.py --enable     # verificar + habilitar en market_stores.py

Qué hace:
  - Intenta /api/ y /io/api/ (auto-detección igual que el conector real)
  - Usa User-Agent de Chrome para evitar bloqueos de bot
  - Reporta tiempo de respuesta, status HTTP y si devuelve productos
  - Con --enable: parchea market_stores.py eliminando disabled=True de los que pasen
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

try:
    import httpx
except ImportError:
    print("Instalar: pip install httpx", file=sys.stderr)
    sys.exit(1)

# Stores pendientes de verificación (disabled=True en market_stores.py)
PENDING_STORES = {
    # Uruguay
    "geant_uy":     {"name": "Geant UY",           "base": "https://www.geant.com.uy",          "platform": "vtex", "country": "UY"},
    # Ecuador
    "tia_ec":       {"name": "TIA Ecuador",         "base": "https://www.tia.com.ec",            "platform": "vtex", "country": "EC"},
    "fybeca_ec":    {"name": "Fybeca",              "base": "https://www.fybeca.com",            "platform": "vtex", "country": "EC"},
    "coral_ec":     {"name": "Coral Hipermercados", "base": "https://www.hipermercadoscoral.com","platform": "vtex", "country": "EC"},
    # Bolivia (domains confirmed via DNS lookup)
    "ketal_bo":     {"name": "Ketal",               "base": "https://www.ketal.com.bo",          "platform": "vtex", "country": "BO"},
    "hipermaxi_bo": {"name": "Hipermaxi",           "base": "https://www.hipermaxi.com",         "platform": "vtex", "country": "BO"},
    "farmacorp_bo": {"name": "Farmacorp",           "base": "https://www.farmacorp.com",         "platform": "vtex", "country": "BO"},
}

# También verifica los 3 UY activos para confirmar antes de primer run del collector
ACTIVE_STORES = {
    "tienda_inglesa_uy": {"name": "Tienda Inglesa", "base": "https://www.tiendainglesa.com.uy", "platform": "vtex", "country": "UY"},
    "disco_uy":          {"name": "Disco UY",        "base": "https://www.disco.com.uy",         "platform": "vtex", "country": "UY"},
    "devoto_uy":         {"name": "Devoto",          "base": "https://www.devoto.com.uy",        "platform": "vtex", "country": "UY"},
}

VTEX_SEARCH_TERM = "leche"
VTEX_SEARCH_PATHS = [
    "/api/catalog_system/pub/products/search",   # standard VTEX
    "/io/api/catalog_system/pub/products/search", # VTEX IO storefront
]
TIMEOUT = 15.0

# Same Chrome UA used by the real VTEX connector
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

MARKET_STORES_PATH = Path(__file__).resolve().parent.parent.parent / "cli-market-core" / "market_core" / "market_stores.py"


def _try_vtex_path(base: str, path_prefix: str, client: httpx.Client) -> tuple[int | None, int, str | None, int]:
    """Try one VTEX path prefix. Returns (status, elapsed_ms, error, products)."""
    url = base.rstrip("/") + f"{path_prefix}/{VTEX_SEARCH_TERM}?_from=0&_to=3"
    try:
        t0 = time.perf_counter()
        r = client.get(
            url,
            headers={"Accept": "application/json", "User-Agent": _CHROME_UA},
            timeout=TIMEOUT,
            follow_redirects=True,
        )
        elapsed = int((time.perf_counter() - t0) * 1000)
        ct = r.headers.get("content-type", "")

        if r.status_code in (200, 206):
            if "json" not in ct:
                snippet = r.text[:120].replace("\n", " ").strip()
                return r.status_code, elapsed, f"non-JSON response ({ct[:40]}) — {snippet}", 0
            try:
                data = r.json()
                if isinstance(data, list):
                    return r.status_code, elapsed, None, len(data)
                if isinstance(data, dict) and "products" in data:
                    return r.status_code, elapsed, None, len(data["products"])
                return r.status_code, elapsed, None, -1
            except Exception:
                return r.status_code, elapsed, "JSON parse error", 0
        return r.status_code, elapsed, f"HTTP {r.status_code}", 0
    except httpx.TimeoutException:
        return None, int(TIMEOUT * 1000), f"Timeout ({TIMEOUT}s)", 0
    except Exception as e:
        return None, 0, str(e)[:100], 0


def check_vtex(store_key: str, info: dict, client: httpx.Client) -> dict:
    result = {
        "store_key": store_key,
        "name": info["name"],
        "country": info["country"],
        "ok": False,
        "status": None,
        "products": 0,
        "elapsed_ms": None,
        "error": None,
        "path_used": None,
    }

    for path_prefix in VTEX_SEARCH_PATHS:
        status, elapsed, error, products = _try_vtex_path(info["base"], path_prefix, client)
        result["status"] = status
        result["elapsed_ms"] = elapsed

        if error is None:  # success
            result["ok"] = products != 0
            result["products"] = products
            result["path_used"] = path_prefix
            if products == 0:
                result["error"] = "0 productos (store vacío?)"
            return result

        if status is not None and status not in (404, 500):
            # Non-retryable error (auth, SSL, non-JSON) — stop trying
            result["error"] = error
            return result

    # Both paths failed
    result["error"] = error  # last error
    return result


def patch_market_stores(passing_keys: list[str]) -> None:
    """Remove disabled=True from stores that passed verification."""
    if not MARKET_STORES_PATH.exists():
        print(f"  ! No se encontró market_stores.py en {MARKET_STORES_PATH}", file=sys.stderr)
        return

    content = MARKET_STORES_PATH.read_text(encoding="utf-8")
    patched = 0
    for key in passing_keys:
        pattern = rf'("{key}":\s*\{{[^}}]+?),"disabled"\s*:\s*True,"disabled_reason"\s*:\s*"[^"]*"(\}})'
        new_content, n = re.subn(pattern, r'\1\2', content)
        if n > 0:
            content = new_content
            patched += 1
            print(f"  ✓ Habilitado: {key}")
        else:
            pattern2 = rf'("{key}":\s*\{{[^}}]+?),"disabled"\s*:\s*True([^}}]*?)(\}})'
            new_content, n = re.subn(
                pattern2,
                lambda m: m.group(0)
                    .replace(',"disabled":True', '')
                    .replace(',"disabled_reason":"pending VTEX endpoint verification"', ''),
                content,
            )
            if n > 0:
                content = new_content
                patched += 1
                print(f"  ✓ Habilitado: {key}")
            else:
                print(f"  ! No se pudo parchear: {key} (revisar manualmente)")

    if patched > 0:
        MARKET_STORES_PATH.write_text(content, encoding="utf-8")
        print(f"\n  → {MARKET_STORES_PATH} actualizado ({patched} stores habilitados)")
    else:
        print("\n  ! No se aplicaron cambios")


def print_result(r: dict, label: str = "") -> None:
    icon = "✅" if r["ok"] else "❌"
    time_s = f"{r['elapsed_ms']}ms" if r["elapsed_ms"] else ""
    prod_s = f"{r['products']} prod" if isinstance(r["products"], int) and r["products"] >= 0 else "OK"
    path_s = f" [{r['path_used']}]" if r.get("path_used") else ""
    detail = r["error"] or (f"{prod_s} · {time_s}{path_s}" if r["ok"] else f"HTTP {r['status']} · {time_s}")
    tag = f"[{label}]" if label else ""
    print(f"  {icon} {r['store_key']:25s} {r['name']:25s} {tag:12s} {detail}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Verificar endpoints VTEX para stores EC/BO/UY")
    parser.add_argument("--enable", action="store_true", help="Habilitar en market_stores.py los que pasen")
    parser.add_argument("--pending-only", action="store_true", help="Solo verificar disabled (no activos)")
    args = parser.parse_args()

    print("=" * 70)
    print("CLI Market — Verificación VTEX endpoints EC/BO/UY")
    print("=" * 70)

    passing: list[str] = []
    failing: list[str] = []

    with httpx.Client() as client:
        if not args.pending_only:
            print("\n── Stores UY ACTIVOS (confirmar antes de primer run) ──")
            for key, info in ACTIVE_STORES.items():
                r = check_vtex(key, info, client)
                print_result(r, "activo")
                if r["ok"]:
                    passing.append(key)

        print("\n── Stores PENDIENTES (disabled=True) ──")
        for key, info in PENDING_STORES.items():
            r = check_vtex(key, info, client)
            print_result(r, "pendiente")
            if r["ok"]:
                passing.append(key)
            else:
                failing.append(key)

    print("\n" + "=" * 70)
    print(f"Resultado: {len(passing)} OK · {len(failing)} fallan")

    if passing:
        print(f"\nStores que pasan: {', '.join(passing)}")
    if failing:
        print(f"Stores que fallan: {', '.join(failing)}")

    if args.enable and passing:
        to_enable = [k for k in passing if k in PENDING_STORES]
        if to_enable:
            print(f"\n── Habilitando {len(to_enable)} stores en market_stores.py ──")
            patch_market_stores(to_enable)
        else:
            print("\nNada nuevo para habilitar (todos los que pasan ya estaban activos).")
    elif args.enable and not passing:
        print("\nNingún store pasó la verificación — no se habilita nada.")
    else:
        if passing and any(k in PENDING_STORES for k in passing):
            print("\nPara habilitar los que pasan: python3 ops/verify_new_stores.py --enable")

    print()


if __name__ == "__main__":
    main()
