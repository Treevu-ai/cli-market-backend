# ── CLI Market LATAM — Backend Dockerfile ──
FROM python:3.12-slim

WORKDIR /app

# cli-market-core and cli-market-index are private GitHub repos (git+https in
# requirements.txt). Pass a fine-grained or classic PAT with repo read scope.
#
# Railway: create GITHUB_TOKEN on the API service and enable
# "Available at Build Time". Railway forwards matching ARG names automatically.
ARG GITHUB_TOKEN

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev git tesseract-ocr tesseract-ocr-spa && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN set -eux; \
    if [ -z "${GITHUB_TOKEN:-}" ]; then \
      echo "ERROR: GITHUB_TOKEN build-arg is required to install private GitHub dependencies." >&2; \
      echo "Railway: add GITHUB_TOKEN to the API service with build-time access enabled." >&2; \
      exit 1; \
    fi; \
    git config --global url."https://x-access-token:${GITHUB_TOKEN}@github.com/".insteadOf "https://github.com/"; \
    sed "s|git+https://github.com/|git+https://x-access-token:${GITHUB_TOKEN}@github.com/|g" requirements.txt > /tmp/requirements.build.txt; \
    pip install --no-cache-dir -r /tmp/requirements.build.txt; \
    rm -f /tmp/requirements.build.txt; \
    git config --global --remove-section url

# Force layer rebuild on deploy (2026-06-01-refresh-v2)
ARG CACHE_BUST=2026-06-06-semantic
COPY *.py pyproject.toml ./
COPY routers/ ./routers/
COPY ops/ ./ops/

RUN mkdir -p /data
ENV MARKET_DATA_DIR=/data

EXPOSE 8765

# API only — collector runs as a separate Railway service (Dockerfile.collector)
CMD ["sh", "-c", "python -m uvicorn market_server:app --host 0.0.0.0 --port $PORT"]