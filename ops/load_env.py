"""Load SLACK_* and MARKET_* from repo .env files (best-effort, no extra deps)."""

from __future__ import annotations

import os
from pathlib import Path


def load_repo_env() -> None:
    root = Path(__file__).resolve().parent.parent
    for path in (root / ".env", root / "ops" / ".env", root / "ops" / ".rotation-local.txt"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value

    # Windows user-level vars (Cursor subprocess may not inherit User scope).
    if os.name == "nt":
        for key in (
            "SLACK_BOT_TOKEN",
            "SLACK_CHANNEL_COMMAND_CONTROL",
            "SLACK_CHANNEL_CLI_MARKET_PRO",
            "SLACK_CHANNEL_FUNNEL",
            "SLACK_SIGNING_SECRET",
            "SLACK_WEBHOOK_FUNNEL",
            "SLACK_WEBHOOK_CLI_MARKET_PRO",
            "MARKET_API_TOKEN",
            "PEPY_API_KEY",
            "PEPY_PROJECT",
            "CLOUDFLARE_API_TOKEN",
            "PROCUREMENT_WEBHOOK_SECRET",
            "PROCURE_PUBLIC_URL",
        ):
            if os.getenv(key):
                continue
            try:
                import ctypes

                buf = ctypes.create_unicode_buffer(8192)
                if ctypes.windll.kernel32.GetEnvironmentVariableW(key, buf, 8192):
                    os.environ[key] = buf.value
            except Exception:
                pass