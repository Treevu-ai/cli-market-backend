"""Voice + ticket scanning via Whisper / Tesseract subprocesses.

These shell out to local binaries:
  - tesseract for OCR
  - whisper for speech-to-text

Both gracefully degrade with placeholder text if the binary is not installed
(common on serverless / minimal images).

Endpoints:
  POST /v1/ticket/scan         Upload image → OCR + price-moat match
  POST /v1/ticket/scan-url     OCR from a public image URL
  POST /v1/voice/transcribe    Upload audio → transcript
  POST /v1/voice/transcribe-url  Transcript from a public audio URL
"""

from __future__ import annotations

import os
import subprocess
import tempfile

import httpx
from fastapi import APIRouter, File, HTTPException, UploadFile

from market_core import get_db
from market_security import validate_public_http_url

router = APIRouter(tags=["media"])


async def _fetch_public_url(url: str) -> bytes:
    try:
        safe_url = validate_public_http_url(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        r = await client.get(safe_url)
        if r.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot fetch resource: HTTP {r.status_code}",
            )
        return r.content


# ── Ticket scanning (OCR via tesseract) ───────────────────────────────────────

@router.post("/v1/ticket/scan")
async def ticket_scan(file: UploadFile = File(...), country: str | None = None):
    """Upload a ticket image → OCR → match each line against the data moat
    to surface potential savings vs the cheapest known store."""
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        result = subprocess.run(
            ["tesseract", tmp_path, "stdout", "-l", "spa", "--psm", "6"],
            capture_output=True, text=True, timeout=15,
        )
        ocr_text = result.stdout.strip() if result.returncode == 0 else ""
    except FileNotFoundError:
        ocr_text = "[Tesseract no instalado. Instalar: sudo apt install tesseract-ocr tesseract-ocr-spa]"
    finally:
        os.unlink(tmp_path)
    lines = [ln.strip() for ln in ocr_text.split("\n") if ln.strip() and len(ln.strip()) > 3]
    db = get_db()
    items_found: list[dict] = []
    for line in lines[:20]:
        words = line.split()
        if len(words) < 2:
            continue
        query = "%" + "%".join(words[:3]) + "%"
        row = db.execute(
            "SELECT name, store_name, price, currency FROM price_snapshots "
            "WHERE name LIKE ? ORDER BY price ASC LIMIT 1",
            (query,),
        ).fetchone()
        if row:
            items_found.append(
                {
                    "ticket_text": line[:50],
                    "best_match": row["name"],
                    "store": row["store_name"],
                    "price": row["price"],
                    "currency": row["currency"],
                }
            )
    db.close()
    savings = sum((i.get("price", 0) or 0) for i in items_found) if items_found else 0
    return {
        "ocr_text": ocr_text[:500],
        "items_detected": len(lines),
        "items_matched": len(items_found),
        "potential_savings": round(savings, 2),
        "items": items_found,
        "message": (
            "Compara contra los precios mas baratos de nuestro data moat."
            if items_found
            else "No se detectaron productos."
        ),
    }


@router.post("/v1/ticket/scan-url")
async def ticket_scan_url(body: dict):
    """OCR from a public image URL. Same as /v1/ticket/scan but without upload."""
    url = body.get("url", "")
    country = body.get("country")
    content = await _fetch_public_url(url)
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        result = subprocess.run(
            ["tesseract", tmp_path, "stdout", "-l", "spa", "--psm", "6"],
            capture_output=True, text=True, timeout=30,
        )
        ocr_text = result.stdout.strip() if result.returncode == 0 else ""
    except FileNotFoundError:
        ocr_text = "[Tesseract no instalado]"
    finally:
        os.unlink(tmp_path)
    return {"ocr_text": ocr_text[:1000], "country": country, "message": "OCR completado"}


# ── Voice transcription (Whisper) ─────────────────────────────────────────────

@router.post("/v1/voice/transcribe")
async def voice_transcribe(file: UploadFile = File(...)):
    """Audio upload → Whisper transcription (ES, tiny model). Returns plain text."""
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        result = subprocess.run(
            ["whisper", tmp_path, "--model", "tiny", "--language", "es",
             "--output_format", "txt", "--output_dir", "/tmp"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            txt_file = tmp_path.replace(".ogg", ".txt")
            transcript = open(txt_file).read().strip() if os.path.exists(txt_file) else ""
        else:
            transcript = "[Transcripción no disponible - instalar whisper]"
    except FileNotFoundError:
        transcript = "[Whisper no instalado. Instalar: pip install openai-whisper]"
    finally:
        os.unlink(tmp_path)
        try:
            os.unlink(tmp_path.replace(".ogg", ".txt"))
        except OSError:
            pass
    return {"transcript": transcript, "language": "es"}


@router.post("/v1/voice/transcribe-url")
async def voice_transcribe_url(body: dict):
    """Transcribe audio from a public URL."""
    url = body.get("url", "")
    suffix = ".ogg"
    if url.endswith(".mp3"):
        suffix = ".mp3"
    elif url.endswith(".wav"):
        suffix = ".wav"
    content = await _fetch_public_url(url)
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        result = subprocess.run(
            ["whisper", tmp_path, "--model", "tiny", "--language", "es",
             "--output_format", "txt", "--output_dir", "/tmp"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            txt_file = tmp_path.rsplit(".", 1)[0] + ".txt"
            transcript = open(txt_file).read().strip() if os.path.exists(txt_file) else ""
        else:
            transcript = "[Transcripción no disponible]"
    except FileNotFoundError:
        transcript = "[Whisper no instalado]"
    finally:
        os.unlink(tmp_path)
    return {"transcript": transcript[:2000], "language": "es"}
