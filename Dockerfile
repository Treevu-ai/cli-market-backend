# ── CLI Market LATAM — Backend Dockerfile ──
FROM python:3.12-slim

WORKDIR /app

# cli-market-core from PyPI; cli-market-index from private git (requirements.txt).
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev git tesseract-ocr tesseract-ocr-spa && rm -rf /var/lib/apt/lists/*

# Private cli-market-index clone during pip install.
# Fly.io → API service → `fly secrets set`: GITHUB_TOKEN or GH_TOKEN (PAT, repo read on cli-market-index).
ARG GITHUB_TOKEN
ARG GH_TOKEN
ARG CACHE_BUST=2026-06-29-core-0a6c847

COPY requirements.txt requirements-private.txt .
RUN set -eux; \
    echo "cache_bust=${CACHE_BUST}"; \
    INDEX_TOKEN="${GITHUB_TOKEN:-${GH_TOKEN:-}}"; \
    if [ -z "${INDEX_TOKEN}" ]; then \
      echo "error: set GITHUB_TOKEN or GH_TOKEN on Fly.io for cli-market-index (git+https)" >&2; \
      exit 1; \
    fi; \
    git config --global url."https://x-access-token:${INDEX_TOKEN}@github.com/".insteadOf "https://github.com/"; \
    pip install --no-cache-dir -r requirements.txt -r requirements-private.txt; \
    rm -f /root/.gitconfig
COPY *.py pyproject.toml ./
COPY routers/ ./routers/
COPY ops/ ./ops/

RUN mkdir -p /data
ENV MARKET_DATA_DIR=/data

EXPOSE 8080

# API only — collector runs as a separate Fly app (Dockerfile.collector / fly.collector.toml)
# PORT defaults to 8080 (Fly.io standard); overridden automatically via fly.toml internal_port
ENV PORT=8080
CMD ["python", "-m", "uvicorn", "market_server:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]