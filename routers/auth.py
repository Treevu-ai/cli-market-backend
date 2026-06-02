"""Authentication, API keys, and subscription tier endpoints.

Endpoints:
  POST   /auth/login              Username/password → session token
  GET    /auth/whoami             Token → username
  POST   /auth/keys               Create API key (sk-...) — scopes: read | read_write
  GET    /auth/keys               List user's API keys (prefix only, no secret)
  DELETE /auth/keys/{key_id}      Revoke an API key
  GET    /auth/subscription       Current tier + limits
"""

from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from market_core import (
    db_create_api_key,
    db_get_subscription,
    db_get_users,
    db_list_api_keys,
    db_revoke_api_key,
    db_save_user,
)
from server_deps import (
    check_auth_brute_force,
    check_rate_limit,
    hash_password,
    record_auth_failure,
    require_user,
    verify_password,
)

router = APIRouter(tags=["auth"])


# ── Request models ────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class CreateApiKeyRequest(BaseModel):
    scopes: str = "read"
    label: str = ""


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/auth/register")
def register():
    """Create a new API key. Public endpoint — rate limited."""
    check_rate_limit("auth")
    username = f"user-{uuid.uuid4().hex[:12]}"
    # Random password — access is via sk- API key, not password login.
    db_save_user(username, hash_password(uuid.uuid4().hex), None)
    result = db_create_api_key(username, "read_write", "register")
    return {
        "username": username,
        "api_key": result["key"],
        "prefix": result["prefix"],
        "scopes": result["scopes"],
        "message": "API key generada. Guardala — no se vuelve a mostrar.",
    }

@router.post("/auth/login")
def login(body: LoginRequest):
    check_rate_limit("auth")
    check_auth_brute_force(body.username)
    users = db_get_users()
    if not users:
        admin_pass = os.getenv("MARKET_ADMIN_PASSWORD", "market")
        db_save_user("admin", hash_password(admin_pass), str(uuid.uuid4()))
        users = db_get_users()
    user = users.get(body.username)
    if not user or not verify_password(body.password, user["password"]):
        record_auth_failure(body.username)
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    token = user.get("token")
    if not token:
        token = str(uuid.uuid4())
        db_save_user(body.username, user["password"], token)
    return {"message": "Autenticado", "username": body.username, "token": token}


@router.get("/auth/whoami")
def whoami(authorization: str | None = Header(None)):
    username = require_user(authorization)
    return {"username": username}


@router.post("/auth/keys")
def create_api_key(body: CreateApiKeyRequest, authorization: str | None = Header(None)):
    username = require_user(authorization)
    if body.scopes not in ("read", "read_write"):
        raise HTTPException(status_code=400, detail="Scopes must be 'read' or 'read_write'")
    result = db_create_api_key(username, body.scopes, body.label)
    return {
        "message": "API key created. Store it safely — it won't be shown again.",
        "key": result["key"],
        "prefix": result["prefix"],
        "scopes": result["scopes"],
        "label": result["label"],
    }


@router.get("/auth/keys")
def list_api_keys(authorization: str | None = Header(None)):
    username = require_user(authorization)
    keys = db_list_api_keys(username)
    return {"keys": keys, "total": len(keys)}


@router.delete("/auth/keys/{key_id}")
def revoke_api_key(key_id: int, authorization: str | None = Header(None)):
    username = require_user(authorization)
    ok = db_revoke_api_key(username, key_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"message": "Key revoked"}


@router.get("/auth/subscription")
def get_subscription(authorization: str | None = Header(None)):
    """Preserves the original shape — returns the full subscription record
    plus a count of the user's API keys."""
    username = require_user(authorization)
    sub = db_get_subscription(username)
    keys = db_list_api_keys(username)
    return {"username": username, "subscription": sub, "api_keys": len(keys)}
