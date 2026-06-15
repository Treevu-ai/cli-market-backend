#!/usr/bin/env python3
"""
Verificación de endpoints VTEX para stores EC/BO/UY recién añadidos.

Uso:
    python3 ops/verify_new_stores.py              # solo verificar
    python3 ops/verify_new_stores.py --enable     # verificar + habilitar en market_stores.py

Qué hace:
  - Llama al endpoint estándar VTEX de cada store pendiente
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
    "geant_uy":        {"name": "Geant UY",            "base": "https://www.geant.com.uy",    "platform": "vtex", "country": "UY"},
    # Ecuador
    "tia_ec":          {"name": "TIA Ecuador",          "base": "https://www.tia.com.ec",       "platform": "vtex", "country": "EC"},
    "fybeca_ec":       {"name": "Fybeca",               "base": "https://www.fybeca.com",       "platform": "vtex", "country": "EC"},
    "coral_ec":        {"name": "Coral Hipermercados",  "base": "https://www.coral.com.ec",     "platform": "vtex", "country": "EC"},
    # Bolivia
    "ketal_bo":        {"name": "Ketal",                "base": "https://www.ketal.com.bo",     "platform": "vtex", "country": "BO"},
    "hipermaxi_bo":    {"name": "Hipermaxi",            "base": "https://www.hipermaxi.com.bo", "platform": "vtex", "country": "BO"},
    "farmacorp_bo":    {"name": "Farmacorp",            "base": "https://www.farmacorp.com",    "platform": "vtex", "country": "BO"},
}

# También verifica los 3 UY activos para confirmar antes de primer run del collector
ACTIVE_STORES = {
    "tienda_inglesa_uy": {"name": "Tienda Inglesa", "base": "https://www.tiendainglesa.com.uy", "platform": "vtex", "country": "UY"},
    "disco_uy":          {"name": "Disco UY",        "base": "https://www.disco.com.uy",         "platform": "vtex", "country": "UY"},
    "devoto_uy":         {"name": "Devoto",           "base": "https://www.devoto.com.uy",        "platform": "vtex", "country": "UY"},
}

VTEX_SEARCH_PATH = "/api/catalog_system/pub/products/search?_from=0&_to=3&O=OrderByTopSaleDESC"
TIMEOUT = 12.0

MARKET_STORES_PATH = Path(__file__).resolve().parent.parent.parent / "cli-market-core" / "market_core" / "market_stores.py"


def check_vtex(store_key: str, info: dict, client: httpx.Client) -> dict:
    url = info["base"].rstrip("/") + VTEX_SEARCH_PATH
    result = {
        "store_key": store_key,
        "name": info["name"],
        "country": info["country"],
        "url": url,
        "ok": False,
        "status": None,
        "products": 0,
        "elapsed_ms": None,
        "error": None,
    }
    try:
        t0 = time.perf_counter()
        r = client.get(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "CLI-Market-Verifier/1.0",
            },
            timeout=TIMEOUT,
            follow_redirects=True,
        )
        elapsed = int((time.perf_counter() - t0) * 1000)
        result["status"] = r.status_code
        result["elapsed_ms"] = elapsed

        if r.status_code == 200:
            try:
                data = r.json()
                if isinstance(data, list):
                    result["products"] = len(data)
                    result["ok"] = len(data) > 0
                elif isinstance(data, dict) and "products" in data:
                    result["products"] = len(data["products"])
                    result["ok"] = result["products"] > 0
                else:
                    # Might still be a valid VTEX response with different structure
                    result["ok"] = True
                    result["products"] = -1  # unknown count
            except Exception:
                result["ok"] = False
                result["error"] = "JSON parse error"
        else:
            result["error"] = f"HTTP {r.status_code}"
    except httpx.TimeoutException:
        result["error"] = f"Timeout ({TIMEOUT}s)"
    except Exception as e:
        result["error"] = str(e)[:80]
    return result


def patch_market_stores(passing_keys: list[str]) -> None:
    """Remove disabled=True from stores that passed verification."""
    if not MARKET_STORES_PATH.exists():
        print(f"  ! No se encontró market_stores.py en {MARKET_STORES_PATH}", file=sys.stderr)
        return

    content = MARKET_STORES_PATH.read_text(encoding="utf-8")
    patched = 0
    for key in passing_keys:
        # Match the store entry line and remove disabled=True and disabled_reason
        pattern = rf'("{key}":\s*\{{[^}}]+?),"disabled"\s*:\s*True,"disabled_reason"\s*:\s*"[^"]*"(\}})'
        replacement = r'\1\2'
        new_content, n = re.subn(pattern, replacement, content)
        if n > 0:
            content = new_content
            patched += 1
            print(f"  ✓ Habilitado: {key}")
        else:
            # Try alternate pattern (disabled might be in different position)
            pattern2 = rf'("{key}":\s*\{{[^}}]+?),"disabled"\s*:\s*True([^}}]*?)(\}})'
            new_content, n = re.subn(pattern2, lambda m: m.group(0).replace(',"disabled":True', '').replace(',"disabled_reason":"pending VTEX endpoint verification"', ''), content)
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
    status = f"HTTP {r['status']}" if r["status"] else ""
    time_s = f"{r['elapsed_ms']}ms" if r["elapsed_ms"] else ""
    prod_s = f"{r['products']} productos" if r["products"] >= 0 else "respuesta OK"
    detail = r["error"] or (f"{prod_s} · {time_s}" if r["ok"] else f"{status} · {time_s}")
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
        # Only enable previously-disabled stores (not active ones)
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
            enabled = [k for k in passing if k in PENDING_STORES]
            print(f"\nPara habilitar los que pasan: python3 ops/verify_new_stores.py --enable")

    print()


if __name__ == "__main__":
    main()
