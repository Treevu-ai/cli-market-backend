"""Collector query rotation and health semantics."""

from collect_prices import (
    SEED_QUERIES,
    _core_queries_by_line,
    _store_health_ok,
    cap_queries_for_cycle,
)


def test_core_queries_per_line():
    cores = _core_queries_by_line()
    assert cores["supermercados"][:3] == [
        ("leche", "supermercados"),
        ("arroz", "supermercados"),
        ("aceite", "supermercados"),
    ]
    assert cores["farmacias"][0] == ("paracetamol", "farmacias")


def test_cap_queries_includes_core_every_cycle():
    expanded = list(SEED_QUERIES[:20])
    c0 = cap_queries_for_cycle(expanded, cycle=0)
    c1 = cap_queries_for_cycle(expanded, cycle=1)
    for q, _line in _core_queries_by_line()["supermercados"]:
        assert (q, "supermercados") in c0
        assert (q, "supermercados") in c1


def test_store_health_ok_empty_rotation_not_failure():
    assert _store_health_ok(collected=0, query_ok=0, query_empty=5, query_fail=0) is True


def test_store_health_ok_hard_failure():
    assert _store_health_ok(collected=0, query_ok=0, query_empty=2, query_fail=3) is False


def test_store_health_ok_partial_success_with_errors():
    assert _store_health_ok(collected=3, query_ok=2, query_empty=1, query_fail=1) is True


def test_store_health_ok_yield_without_errors():
    assert _store_health_ok(collected=0, query_ok=4, query_empty=0, query_fail=0) is True
