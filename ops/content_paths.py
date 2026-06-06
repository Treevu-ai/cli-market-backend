#!/usr/bin/env python3
"""Paths for GTM / LinkedIn content (separate repo or local clone).

Set CLI_MARKET_CONTENT_DIR to the root of cli-market-content.

Resolution order:
  1. CLI_MARKET_CONTENT_DIR env
  2. <repo>/cli-market-content/ (local, gitignored in product repo)
  3. ../cli-market-content/ (sibling checkout)
  4. <repo>/docs/ (legacy — deprecated)
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_TEMPLATE = ROOT / "tools" / "content-repo-template"


def content_root() -> Path:
    env = os.getenv("CLI_MARKET_CONTENT_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()

    local = ROOT / "cli-market-content"
    if local.is_dir() and (local / "linkedin").is_dir():
        return local.resolve()

    sibling = ROOT.parent / "cli-market-content"
    if sibling.is_dir() and (sibling / "linkedin").is_dir():
        return sibling.resolve()

    # Monorepo template (Cloud Agent / dev without private content repo)
    if (_TEMPLATE / "linkedin").is_dir():
        return _TEMPLATE.resolve()

    legacy = ROOT / "docs"
    if (legacy / "linkedin").is_dir():
        warnings.warn(
            "Using docs/linkedin in product repo (legacy). "
            "Run: python3 ops/init_content_repo.py",
            stacklevel=2,
        )
        return legacy.resolve()

    # Prefer creating sibling path for clearer errors
    return sibling.resolve()


def linkedin_dir() -> Path:
    root = content_root()
    if root.name == "docs" and (root / "linkedin").is_dir():
        return root / "linkedin"
    return root / "linkedin"


def metrics_dir() -> Path:
    root = content_root()
    if (root / "metrics").is_dir():
        return root / "metrics"
    return root / "metrics"


def strategy_dir() -> Path:
    return content_root() / "strategy"


def calendar_dir() -> Path:
    return content_root() / "calendar"


def assets_root() -> Path:
    return linkedin_dir() / "assets"


def template_dir() -> Path:
    return _TEMPLATE


def rel_to_content(path: Path) -> str:
    """Path string for markdown (posix, relative to content root)."""
    try:
        return path.resolve().relative_to(content_root().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def display_path(path: Path) -> str:
    """Human-readable path for logs (relative to content root, else absolute)."""
    try:
        return path.resolve().relative_to(content_root().resolve()).as_posix()
    except ValueError:
        try:
            return path.resolve().relative_to(ROOT.resolve()).as_posix()
        except ValueError:
            return str(path.resolve())
