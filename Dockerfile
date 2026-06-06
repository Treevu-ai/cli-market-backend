# ── CLI Market LATAM — Backend Dockerfile ──
FROM python:3.12-slim

WORKDIR /app

# cli-market-core and cli-market-index are public git dependencies — pinned by
# commit in requirements.txt (git required for the git+https install).
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev git tesseract-ocr tesseract-ocr-spa && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Force layer rebuild on deploy (2026-06-01-refresh-v2)
ARG CACHE_BUST=2026-06-06-shims-fix
COPY *.py pyproject.toml ./
COPY routers/ ./routers/
COPY ops/ ./ops/

RUN mkdir -p /data
ENV MARKET_DATA_DIR=/data

EXPOSE 8765

# API only — collector runs as a separate Railway service (Dockerfile.collector)
CMD ["sh", "-c", "python -m uvicorn market_server:app --host 0.0.0.0 --port $PORT"]