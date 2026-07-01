"""Regression test for cli-market-backend#127 (ask O3): without
ANTHROPIC_API_KEY configured, /agent/ask fell back to a naive classifier
that forwarded the full literal question as a product search query for any
prompt that didn't contain an exact action keyword ("compra", "compara",
etc.) — e.g. a WH-question like "donde compro leche mas barata en
argentina" searched for that entire sentence and always returned zero
results."""

from __future__ import annotations

from routers.agent import _regex_fallback


def test_wh_question_extracts_staple_and_country_instead_of_literal_sentence():
    result = _regex_fallback("donde compro leche mas barata en argentina")
    assert result["action"] == "search"
    assert result["query"] == "leche"
    assert result["country"] == "AR"


def test_wh_question_without_country_still_extracts_staple():
    result = _regex_fallback("cual es el precio del arroz")
    assert result["query"] == "arroz"
    assert "country" not in result


def test_unrecognized_prompt_with_no_staple_falls_back_to_raw_text():
    result = _regex_fallback("hola como estas")
    assert result["action"] == "search"
    assert result["query"] == "hola como estas"


def test_explicit_action_keywords_still_take_priority_over_staple_extraction():
    result = _regex_fallback("compara leche")
    assert result["action"] == "compare"
    assert result["query"] == "leche"
