# ── CLI Market LATAM — Backend Dockerfile ──
FROM python:3.12-slim

WORKDIR /app

# cli-market-core is a private git dependency — GITHUB_TOKEN authenticates the
# pip install. The data-moat modules and market_connectors/ live in that package.
ARG GITHUB_TOKEN
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev git tesseract-ocr tesseract-ocr-spa && rm -rf /var/lib/apt/lists/*
RUN git config --global url."https://${GITHUB_TOKEN}@github.com/".insteadOf "https://github.com/" && \
    pip install --no-cache-dir -r requirements.txt && \
    git config --global --unset url."https://${GITHUB_TOKEN}@github.com/".insteadOf

# Force layer rebuild on deploy (2026-06-01-refresh-v2)
ARG CACHE_BUST=1
COPY *.py pyproject.toml ./
COPY routers/ ./routers/

RUN mkdir -p /data
ENV MARKET_DATA_DIR=/data

EXPOSE 8765

# API only — collector runs as a separate Railway service (Dockerfile.collector)
CMD ["sh", "-c", "python -m uvicorn market_server:app --host 0.0.0.0 --port $PORT"]