"""Tests for Agentic Commerce Pulse report generation."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from market_pulse import (
    adapt_brief_for_summary,
    build_commerce_pulse,
    build_executive_highlights,
    build_price_pulse_markdown,
    iso_week,
    render_pulse_markdown,
)


SAMPLE_BRIEF = {
    "headline": "RPV +4.8% over 7d",
    "country": "PE",
    "days": 7,
    "shelf": {
        "retail_price_velocity_7d_pct": 4.8,
        "price_dispersion": 23.0,
        "basket_stress_index": 102.3,
        "promo_intensity": 1.3,
    },
    "scores": {"fairness": {"score": 78.5, "label": "competitive"}},
    "confidence": {"moat_freshness_pct": 94.0, "stores_active": 38},
    "subcategories": {
        "subcategories": [
            {"key": "subcat_leche_price_momentum", "value": 14.0},
        ]
    },
    "disclaimer": "Not official CPI.",
}

SAMPLE_DASHBOARD = {
    "kpis": {
        "total_indexed": 55000,
        "snapshots_24h": 12000,
        "stores_indexed": 38,
        "coverage_7d_pct": 92.0,
    }
}


def test_iso_week_format():
    assert iso_week().count("-W") == 1


def test_adapt_brief_for_summary_maps_core_fields():
    adapted = adapt_brief_for_summary(SAMPLE_BRIEF)
    summary_infl = None
    for ind in adapted["analytics"]["indicators"]:
        if ind["key"] == "shelf_inflation_avg_pct":
            summary_infl = ind["value"]
    assert summary_infl == 4.8


def test_build_commerce_pulse_structure():
    pulse = build_commerce_pulse(SAMPLE_BRIEF, dashboard=SAMPLE_DASHBOARD, country="PE", lang="es")
    assert pulse["report"] == "agentic_commerce_pulse"
    assert pulse["country"] == "PE"
    assert pulse["publishable"] is True
    assert len(pulse["executive_highlights"]) >= 3
    assert pulse["kpis"]["inflation_pct"] == 4.8
    assert pulse["kpis"]["pvi"] == 23.0
    assert "Executive Highlights" in pulse["markdown"]
    assert "Agentic Commerce Pulse" in pulse["markdown"]


def test_build_executive_highlights_spanish_causal():
    from market_brief import build_brief_summary

    adapted = adapt_brief_for_summary(SAMPLE_BRIEF)
    summary = build_brief_summary(adapted)
    highlights = build_executive_highlights(
        summary,
        brief=SAMPLE_BRIEF,
        moat={"total_indexed": 55000, "snapshots_24h": 12000},
        lang="es",
    )
    assert any("inflación" in h.lower() or "presión" in h.lower() for h in highlights)
    assert any("anomalía" in h.lower() or "leche" in h.lower() for h in highlights)


def test_render_pulse_markdown_includes_moat_table():
    pulse = build_commerce_pulse(SAMPLE_BRIEF, dashboard=SAMPLE_DASHBOARD)
    md = render_pulse_markdown(pulse)
    assert "55,000" in md
    assert "12,000" in md


def test_build_price_pulse_markdown_delegates(isolated_db):
    with patch("market_pulse.generate_commerce_pulse") as gen:
        gen.return_value = {"markdown": "# Pulse test"}
        md = build_price_pulse_markdown(SAMPLE_DASHBOARD, {})
        assert md == "# Pulse test"
        gen.assert_called_once()


def test_cmd_pulse_json_output(capsys):
    import json

    import market_cli

    sample_pulse = build_commerce_pulse(SAMPLE_BRIEF, dashboard=SAMPLE_DASHBOARD)
    args = MagicMock()
    args.country = "PE"
    args.days = 7
    args.json = True
    args.markdown = False

    with patch.object(market_cli, "cli_api", return_value=sample_pulse):
        market_cli.cmd_pulse(args)

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["report"] == "agentic_commerce_pulse"


def test_cmd_pulse_markdown_output(capsys):
    import market_cli

    sample_pulse = build_commerce_pulse(SAMPLE_BRIEF, dashboard=SAMPLE_DASHBOARD)
    args = MagicMock()
    args.country = "PE"
    args.days = 7
    args.json = False
    args.markdown = True

    with patch.object(market_cli, "cli_api", return_value=sample_pulse):
        market_cli.cmd_pulse(args)

    out = capsys.readouterr().out
    assert "Executive Highlights" in out
