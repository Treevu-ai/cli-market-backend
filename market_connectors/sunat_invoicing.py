"""
market_connectors/sunat_invoicing.py — SUNAT electronic invoice via PSE.

SUNAT direct is complex. Recommended: use a PSE (Facturalo, Efact, Digifact).
This connector builds UBL 2.1 + submits via PSE REST API.
"""

import os, httpx
from datetime import datetime, timezone

COMPANY = {"razon_social": "SINAPSIS INNOVADORA S.A.C.", "ruc": "20613045563",
           "direccion": "Lima, Perú", "web": "https://cli-market.dev"}

PSE_PROVIDER = os.getenv("SUNAT_PSE_PROVIDER", "facturalo")
PSE_API_KEY = os.getenv("SUNAT_PSE_API_KEY", "")
PSE_API_URL = os.getenv("SUNAT_PSE_API_URL", "")
SUNAT_MODE = os.getenv("SUNAT_MODE", "demo")

# ── SUNAT Consulta Integrada (validation) ─────────────────────────────────
SUNAT_CLIENT_ID = os.getenv("SUNAT_CLIENT_ID", "")
SUNAT_CLIENT_SECRET = os.getenv("SUNAT_CLIENT_SECRET", "")
SUNAT_TOKEN_URL = "https://api-seguridad.sunat.gob.pe/v1/clientesextranet"
SUNAT_VALIDATE_URL = "https://api.sunat.gob.pe/v1/contribuyente/contribuyentes"


async def _get_sunat_token() -> str:
    """Get OAuth2 token from SUNAT for consulta integrada."""
    if not SUNAT_CLIENT_ID or not SUNAT_CLIENT_SECRET:
        raise ValueError("SUNAT_CLIENT_ID and SUNAT_CLIENT_SECRET not configured")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{SUNAT_TOKEN_URL}/{SUNAT_CLIENT_ID}/oauth2/token/",
            data={"grant_type": "client_credentials",
                  "scope": "https://api.sunat.gob.pe/v1/contribuyente/contribuyentes",
                  "client_id": SUNAT_CLIENT_ID, "client_secret": SUNAT_CLIENT_SECRET},
        )
        if resp.status_code == 200:
            return resp.json()["access_token"]
        raise Exception(f"SUNAT auth failed: {resp.text}")


async def validate_receipt(num_ruc: str, cod_comp: str, serie: str,
                           numero: int, fecha_emision: str, monto: float) -> dict:
    """
    Validate a receipt against SUNAT's database.

    Parameters:
    - num_ruc: RUC of the issuer (11 digits)
    - cod_comp: 01=Factura, 03=Boleta de Venta
    - serie: Series (e.g., "F001")
    - numero: Invoice number (integer, up to 8 digits)
    - fecha_emision: Date in dd/mm/yyyy format
    - monto: Total amount (required for electronic receipts)
    """
    if not SUNAT_CLIENT_ID:
        return {"status": "sin_credenciales",
                "message": "SUNAT validation not configured. Set SUNAT_CLIENT_ID and SUNAT_CLIENT_SECRET."}
    try:
        token = await _get_sunat_token()
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{SUNAT_VALIDATE_URL}/{num_ruc}/validarcomprobante",
                json={"numRuc": num_ruc, "codComp": cod_comp, "numeroSerie": serie,
                      "numero": numero, "fechaEmision": fecha_emision, "monto": monto},
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                estado_map = {"0": "NO EXISTE", "1": "ACEPTADO", "2": "ANULADO",
                              "3": "AUTORIZADO", "4": "NO AUTORIZADO"}
                return {
                    "success": data.get("success", False),
                    "estado_comprobante": estado_map.get(str(data.get("data", {}).get("estadoCp", "")), "DESCONOCIDO"),
                    "estado_contribuyente": data.get("data", {}).get("estadoRuc", ""),
                    "observaciones": data.get("data", {}).get("Observaciones", []),
                }
            return {"success": False, "error": resp.text}
    except ValueError as e:
        return {"status": "sin_credenciales", "message": str(e)}


def build_invoice_ubl(order: dict, items: list[dict]) -> dict:
    subtotal = round(sum(i["price"] * i["quantity"] for i in items), 2)
    igv = round(subtotal * 0.18, 2)
    return {
        "tipo_documento": "01", "serie": "F001",
        "numero": order.get("order_id", "").replace("ORD-", ""),
        "fecha_emision": datetime.now(timezone.utc).isoformat(), "moneda": "PEN",
        "emisor": COMPANY,
        "cliente": {"nombre": order.get("username", "Cliente"), "tipo_documento": "0", "numero_documento": "-"},
        "items": [{"codigo": i.get("product_id",""), "descripcion": i.get("name",""),
                    "cantidad": i.get("quantity",1), "precio_unitario": i.get("price",0),
                    "subtotal": round(i.get("price",0)*i.get("quantity",1),2),
                    "unidad": "NIU",
                    "igv": round(i.get("price",0)*i.get("quantity",1)*0.18,2)} for i in items],
        "subtotal": subtotal, "igv": igv, "total": round(subtotal+igv,2),
    }


async def emit_invoice(order: dict, items: list[dict]) -> dict:
    if not PSE_API_KEY:
        return {"status": "demo", "message": f"SUNAT PSE no configurado ({PSE_PROVIDER}). Set SUNAT_PSE_API_KEY.",
                "invoice": build_invoice_ubl(order, items),
                "ruc": COMPANY["ruc"], "razon_social": COMPANY["razon_social"]}

    invoice = build_invoice_ubl(order, items)
    # Submit to PSE provider (Facturalo, Efact, or Digifact)
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{PSE_API_URL}/v1/invoices", json=invoice,
            headers={"Authorization": f"Bearer {PSE_API_KEY}"},
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            return {"status": "emitida", "invoice_id": data.get("id",""),
                    "serie_numero": f"{invoice['serie']}-{invoice['numero']}",
                    "cdr_status": data.get("cdr_status","accepted"),
                    "pdf_url": data.get("pdf_url",""), "ruc": COMPANY["ruc"]}
    return {"status": "demo", "invoice": invoice,
            "message": f"PSE {PSE_PROVIDER} responded {resp.status_code}. Modo {SUNAT_MODE}."}
