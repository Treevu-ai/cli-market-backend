"""DEPRECATED — v1 routes moved to market_core.api_routes (shared between backend and CLI).

This file is no longer imported. Routes are now defined in:
  C:\Users\acuba\Projects\cli-market-core\market_core\api_routes.py

The backend imports them via:
  from market_core.api_routes import router as v1_router
  app.include_router(v1_router, prefix="/v1")

Auth is wired by setting _auth_fn = require_api_key before app startup.
"""
