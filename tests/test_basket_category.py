"""Tests for the canasta-category guard used by /v1/basket/compare.

`infer_category` delegates to the cli-market-index taxonomy, so these tests are
skipped when the index package is not installed (same policy as other index tests).
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

pytest.importorskip("taxonomy.canasta", reason="cli-market-index not installed")

from index_gate import infer_category


def test_query_maps_to_staple():
    assert infer_category("aceite vegetal") == "aceite"
    assert infer_category("leche evaporada gloria entera") == "leche"
    assert infer_category("arroz superior") == "arroz"


def test_cross_category_products_are_excluded():
    # Canned fish "en aceite vegetal" must NOT count as cooking oil.
    assert infer_category("Filete de Atún Marinero en Aceite Vegetal") is None
    assert infer_category("Grated de Atún en Aceite Vegetal") is None
    # Condensed milk is not the milk staple.
    assert infer_category("Leche Condensada Gloria 393g") is None


def test_matching_products_keep_their_staple():
    assert infer_category("Aceite Vegetal Primor 900ml") == "aceite"
    assert infer_category("Leche Evaporada Entera GLORIA Lata 390g") == "leche"


def test_non_staple_and_empty_return_none():
    assert infer_category("yogurt griego") is None
    assert infer_category("detergente liquido") is None
    assert infer_category("") is None
