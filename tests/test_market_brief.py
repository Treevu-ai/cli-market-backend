"""Tests for market brief executive summary builder."""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from market_brief import (
    brief_footer,
    brief_title,
    build_brief_summary,
    format_brief_lines,
)


SAMPLE_BRIEF = {
    "headline": "PE: +4.8% en 7d",
    "country": "PE",
    "days": 7,
    "shelf": {
        "promo_intensity": 1.2,
        "price_dispersion": 23.0,
        "basket_stress_index": 102.3,
    },
    "scores": {
        "fairness": {"score": 78.5, "label": "competitive"},
        "basket_stress": {"score": 102.3, "label": "elevated"},
    },
    "confidence": {
        "moat_freshness_pct": 94.0,
        "stores_active": 38,
    },
    "analytics": {
        "indicators": [
            {"key": "shelf_inflation_avg_pct", "value": 4.8},
        ],
    },
    "subcategories": {
        "subcategories": [
            {
                "subcategory": "leche",
                "signals": {"subcat_price_momentum": {"value": 14.0}},
            },
            {
                "subcategory": "arroz",
                "signals": {"subcat_price_momentum": {"value": 2.1}},
            },
        ],
    },
}


def test_build_brief_summary_extracts_kpis():
    summary = build_brief_summary(SAMPLE_BRIEF)
    assert summary["country"] == "PE"
    assert summary["inflation_pct"] == 4.8
    assert summary["pvi"] == 23.0
    assert summary["bai"] == 102.3
    assert summary["pdi"] == 1.2
    assert summary["rcs"] == 78.5
    assert summary["stores_active"] == 38
    assert summary["moat_freshness_pct"] == 94.0


def test_build_brief_summary_picks_largest_anomaly():
    summary = build_brief_summary(SAMPLE_BRIEF)
    anomaly = summary["largest_anomaly"]
    assert anomaly is not None
    assert anomaly["subcategory"] == "leche"
    assert anomaly["delta_pct"] == 14.0


def test_format_brief_lines_spanish():
    summary = build_brief_summary(SAMPLE_BRIEF)
    rows = format_brief_lines(summary, lang="es")
    labels = [r[0] for r in rows]
    assert "Inflación retail" in labels
    assert "Mayor anomalía" in labels
    assert any("leche" in v for _, v in rows)


def test_format_brief_lines_english():
    summary = build_brief_summary(SAMPLE_BRIEF)
    rows = format_brief_lines(summary, lang="en")
    labels = [r[0] for r in rows]
    assert "Retail inflation" in labels
    assert "Largest anomaly" in labels


def test_brief_title_and_footer():
    assert "PE" in brief_title("pe")
    assert "market compare" in brief_footer()


def test_cmd_brief_json_output(capsys):
    import market_cli

    args = MagicMock()
    args.country = "PE"
    args.line = None
    args.days = 7
    args.json = True

    with patch.object(market_cli, "cli_api", return_value=SAMPLE_BRIEF):
        market_cli.cmd_brief(args)

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["headline"] == "PE: +4.8% en 7d"
    assert payload["executive"]["inflation_pct"] == 4.8
    assert payload["executive"]["largest_anomaly"]["subcategory"] == "leche"


def test_cmd_brief_renders_panel(capsys):
    import market_cli

    args = MagicMock()
    args.country = "PE"
    args.line = None
    args.days = 7
    args.json = False

    with patch.object(market_cli, "cli_api", return_value=SAMPLE_BRIEF):
        with patch.object(market_cli, "get_lang", return_value="es"):
            market_cli.cmd_brief(args)

    out = capsys.readouterr().out
    assert "Agentic Commerce Brief" in out
    assert "PE" in out
    assert "leche" in out
    assert "market compare" in out
