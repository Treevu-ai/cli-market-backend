"""HTTP router modules for cli-market.

Each module exposes a `router: APIRouter` that market_server.py registers via
`app.include_router(...)`. Routers must:
  - Import shared helpers from `server_deps` (never from `market_server`, to
    avoid circular imports).
  - Import data-layer functions from `market_core`.
  - Define their own Pydantic request/response models locally.

Adding a new router:
  1. Create routers/<domain>.py with `router = APIRouter()`.
  2. Add `from routers.<domain> import router as <domain>_router` in
     market_server.py and `app.include_router(<domain>_router)`.
  3. Add tests under tests/test_<domain>.py or extend tests/test_server.py.
"""
