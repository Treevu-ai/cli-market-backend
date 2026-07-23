"""Enterprise-tier subscribers (and the platform admin) see every
customer-facing MCP tool (the "full" profile, 66 tools) via tools/list
instead of the 44-tool curated default profile — requested explicitly
2026-07-23: "necesito que mi perfil enterprise exponga todas las tools
por defecto".

Before this, tools/list served a single module-level constant computed
once at import time for the "default" profile only — every caller saw
the same 44 tools regardless of subscription tier.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _reset_cache():
    from routers import mcp_http

    mcp_http._PROFILE_CACHE.clear()


def test_enterprise_subscriber_gets_full_profile():
    _reset_cache()
    from routers.mcp_http import _tools_for_token, _FULL_TOOLS

    with patch("routers.mcp_http.auth_user", return_value="enterprise_user"), patch(
        "market_billing.db_get_subscription", return_value={"tier": "enterprise"}
    ):
        tools = _tools_for_token("sk-enterprise-token")

    assert tools == _FULL_TOOLS
    assert len(tools) > 44


def test_free_tier_still_gets_default_profile():
    _reset_cache()
    from routers.mcp_http import _tools_for_token, _TOOLS

    with patch("routers.mcp_http.auth_user", return_value="free_user"), patch(
        "market_billing.db_get_subscription", return_value={"tier": "free"}
    ):
        tools = _tools_for_token("sk-free-token")

    assert tools == _TOOLS
    assert len(tools) == 44


def test_pro_tier_still_gets_default_profile():
    """Only enterprise (and platform admin) get the expanded profile —
    pro/starter/builder are unaffected by this change."""
    _reset_cache()
    from routers.mcp_http import _tools_for_token, _TOOLS

    with patch("routers.mcp_http.auth_user", return_value="pro_user"), patch(
        "market_billing.db_get_subscription", return_value={"tier": "pro"}
    ):
        tools = _tools_for_token("sk-pro-token")

    assert tools == _TOOLS


def test_platform_admin_gets_full_profile_regardless_of_subscription():
    _reset_cache()
    from routers.mcp_http import _tools_for_token, _FULL_TOOLS

    with patch("routers.mcp_http.auth_user", return_value="admin"), patch(
        "market_core.platform_admin.is_platform_admin", return_value=True
    ), patch("market_billing.db_get_subscription", return_value={"tier": "free"}):
        tools = _tools_for_token("sk-admin-token")

    assert tools == _FULL_TOOLS


def test_missing_token_falls_back_to_default_profile():
    from routers.mcp_http import _tools_for_token, _TOOLS

    assert _tools_for_token(None) == _TOOLS


def test_invalid_token_falls_back_to_default_profile_without_raising():
    """auth_user() raises HTTPException(401) for an invalid token — this
    must not break tools/list for probes/misconfigured clients."""
    _reset_cache()
    from fastapi import HTTPException

    from routers.mcp_http import _tools_for_token, _TOOLS

    with patch("routers.mcp_http.auth_user", side_effect=HTTPException(status_code=401, detail="bad token")):
        tools = _tools_for_token("sk-garbage")

    assert tools == _TOOLS


def test_profile_resolution_is_cached_per_token():
    """A second call within the TTL must not hit the subscription DB again."""
    _reset_cache()
    from routers.mcp_http import _tools_for_token

    with patch("routers.mcp_http.auth_user", return_value="enterprise_user") as fake_auth, patch(
        "market_billing.db_get_subscription", return_value={"tier": "enterprise"}
    ) as fake_sub:
        _tools_for_token("sk-cached-token")
        _tools_for_token("sk-cached-token")

    fake_auth.assert_called_once()
    fake_sub.assert_called_once()


def test_profile_cache_expires_after_ttl():
    _reset_cache()
    from routers import mcp_http
    from routers.mcp_http import _tools_for_token, _FULL_TOOLS, _TOOLS

    with patch("routers.mcp_http.auth_user", return_value="enterprise_user"), patch(
        "market_billing.db_get_subscription", return_value={"tier": "enterprise"}
    ):
        first = _tools_for_token("sk-ttl-token")
    assert first == _FULL_TOOLS

    # Force the cached entry to look stale, then downgrade the mocked tier —
    # the next call must re-resolve instead of trusting the expired cache.
    stale_profile, _ = mcp_http._PROFILE_CACHE["sk-ttl-token"]
    mcp_http._PROFILE_CACHE["sk-ttl-token"] = (stale_profile, time.time() - mcp_http._PROFILE_CACHE_TTL - 1)

    with patch("routers.mcp_http.auth_user", return_value="enterprise_user"), patch(
        "market_billing.db_get_subscription", return_value={"tier": "pro"}
    ):
        second = _tools_for_token("sk-ttl-token")
    assert second == _TOOLS
