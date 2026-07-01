"""Unit tests for the search/basket relevance filter.

Covers `_is_relevant`, in particular the `require_all` mode used by
`/v1/basket/compare` to avoid cross-brand / cross-type false matches.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from market_core.market_food_match import matches_food_basket_query
from routers.search import _is_relevant, _query_tokens


def test_query_tokens_normalizes_accents_and_case():
    assert _query_tokens("Leche Evaporada GLORIA") == ["leche", "evaporada", "gloria"]
    assert _query_tokens("Atún") == ["atun"]


def test_any_token_mode_is_lenient():
    tokens = _query_tokens("leche evaporada gloria entera")
    # One shared word ("gloria") is enough in the default (search/compare) mode.
    assert _is_relevant("Shake Capuccino UHT GLORIA Botella 320ml", tokens) is True


def test_require_all_rejects_cross_brand_match():
    tokens = _query_tokens("leche evaporada gloria entera")
    # Missing "evaporada"/"entera" -> not the requested product.
    assert _is_relevant("Shake Capuccino UHT GLORIA Botella 320ml", tokens, require_all=True) is False
    # Missing brand "gloria" -> wrong brand.
    assert _is_relevant("Leche Evaporada Entera Cuisine & Co Lata 410g", tokens, require_all=True) is False


def test_require_all_accepts_full_match():
    tokens = _query_tokens("leche evaporada gloria entera")
    assert _is_relevant("Leche Evaporada Entera GLORIA Lata 390g", tokens, require_all=True) is True
    # Word order and extra descriptors do not matter.
    assert _is_relevant("Pack x6 Leche Evaporada Gloria Entera", tokens, require_all=True) is True


def test_word_boundary_prevents_prefix_false_positive():
    tokens = _query_tokens("pan")
    assert _is_relevant("Pan de Molde Bimbo", tokens) is True
    assert _is_relevant("Pantalon Jogger Azul", tokens) is False


def test_empty_tokens_matches_everything():
    assert _is_relevant("Cualquier Producto", []) is True
    assert _is_relevant("Cualquier Producto", [], require_all=True) is True


def test_basket_resolver_uses_staple_exclusion_not_word_match_alone():
    """Regression for cli-market-backend#127: _fetch_basket_store used to gate
    candidates with infer_category(), whose taxonomy.canasta dependency was
    missing from this deployment — the import silently failed, disabling the
    category guard entirely and leaving _is_relevant's plain word-boundary
    match as the only filter, which a chocolate bar named "... con Leche"
    satisfies just as well as real milk. The resolver now calls
    matches_food_basket_query(), which enforces the real staple/exclusion
    taxonomy regardless of the word match.
    """
    # Passes _is_relevant's word-boundary check (contains "leche") but must
    # still be rejected as a "leche" canasta match.
    assert _is_relevant("Chocolate con Leche Sublime 90g", ["leche"], require_all=True) is True
    assert matches_food_basket_query("leche", {"name": "Chocolate con Leche Sublime 90g", "line": "supermercados"}) is False
    # A real milk product passes both.
    assert _is_relevant("Leche Descremada La Serenisima 1L", ["leche"], require_all=True) is True
    assert matches_food_basket_query("leche", {"name": "Leche Descremada La Serenisima 1L", "line": "supermercados"}) is True
