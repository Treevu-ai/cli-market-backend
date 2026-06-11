# ── CLI Market LATAM — Backend Dockerfile ──
FROM python:3.12-slim

WORKDIR /app

# cli-market-core from PyPI; cli-market-index from private git (requirements.txt).
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev git tesseract-ocr tesseract-ocr-spa && rm -rf /var/lib/apt/lists/*

# Private cli-market-index clone during pip install.
# Railway → API service → Variables: GITHUB_TOKEN (PAT with repo scope on cli-market-index).
ARG GITHUB_TOKEN

COPY requirements.txt requirements-private.txt .
RUN set -eux; \
    if [ -z "${GITHUB_TOKEN}" ]; then \
      echo "error: GITHUB_TOKEN build arg required for private cli-market-index (git+https)" >&2; \
      exit 1; \
    fi; \
    git config --global url."https://x-access-token:${GITHUB_TOKEN}@github.com/".insteadOf "https://github.com/"; \
    pip install --no-cache-dir -r requirements.txt -r requirements-private.txt; \
    rm -f /root/.gitconfig

# Force layer rebuild on deploy (2026-06-01-refresh-v2)
ARG CACHE_BUST=2026-06-10-release-1.9.28
COPY *.py pyproject.toml ./
COPY routers/ ./routers/
COPY ops/ ./ops/

RUN mkdir -p /data
ENV MARKET_DATA_DIR=/data

EXPOSE 8765

# API only — collector runs as a separate Railway service (Dockerfile.collector)
CMD ["sh", "-c", "python -m uvicorn market_server:app --host 0.0.0.0 --port $PORT"]