"""Regression test: a slow store in /v1/basket/compare must not discard
results from fast stores in the same batch, nor abort subsequent batches.

Bug: the old code wrapped the whole batch in a single
asyncio.wait_for(asyncio.gather(*batch_tasks), timeout=...). One slow store
(VTEX direct-HTTP failure -> Playwright fallback, seen taking 2-20s for
Sodimac/Ripley) timed out the entire gather, discarding every store in that
batch — including fast, healthy ones that had already finished — and then
`break` aborted all remaining batches outright.
"""

import asyncio

import pytest

from routers import search


def _product(name, price, store):
    return {
        "id": f"{store}-{name}", "product_id": f"{store}-{name}",
        "name": name, "brand": "Marca", "price": price, "store": store,
        "store_name": store.title(), "currency": "PEN", "category": "abarrotes",
    }


@pytest.mark.asyncio
async def test_slow_store_does_not_discard_fast_store_results(monkeypatch):
    monkeypatch.setattr(search, "require_api_key", lambda _auth: "tester")
    monkeypatch.setattr(search, "enrich_list", lambda *_a, **_k: None)
    monkeypatch.setattr(search, "STORES", {
        "wong": {"name": "Wong", "currency": "PEN"},
        "sodimac": {"name": "Sodimac", "currency": "PEN"},
    })

    async def fake_fetch_store(store, _query, *_args, **_kwargs):
        if store == "sodimac":
            # Simulate the Playwright-fallback stall — far longer than the
            # per-store timeout we set below, well within the batch timeout.
            await asyncio.sleep(0.3)
        return [_product("Arroz 1kg", 4.0, store)]

    monkeypatch.setattr(search, "fetch_store", fake_fetch_store)
    monkeypatch.setattr(search, "product_from_json", lambda p, _s: p)
    monkeypatch.setenv("BASKET_STORE_TIMEOUT", "0.05")  # sodimac's 0.3s sleep must time out
    monkeypatch.setenv("BASKET_TIMEOUT", "5.0")          # batch-level backstop, should never fire
    monkeypatch.setenv("BASKET_PARALLEL_BATCH", "8")

    body = search.BasketRequest(items=[{"name": "arroz 1kg"}], stores=["wong", "sodimac"])
    result = await search.basket_compare(body, "Bearer token")

    assert "wong" in result["comparison"]  # fast store survives despite sodimac timing out
    assert "sodimac" not in result["comparison"]  # slow store excluded, not silently included wrong
    assert result["best_store"] == "wong"


@pytest.mark.asyncio
async def test_slow_store_in_first_batch_does_not_abort_second_batch(monkeypatch):
    monkeypatch.setattr(search, "require_api_key", lambda _auth: "tester")
    monkeypatch.setattr(search, "enrich_list", lambda *_a, **_k: None)
    monkeypatch.setattr(search, "STORES", {
        "slow1": {"name": "Slow1", "currency": "PEN"},
        "fast2": {"name": "Fast2", "currency": "PEN"},
    })

    async def fake_fetch_store(store, _query, *_args, **_kwargs):
        if store == "slow1":
            await asyncio.sleep(0.3)
        return [_product("Arroz 1kg", 4.0, store)]

    monkeypatch.setattr(search, "fetch_store", fake_fetch_store)
    monkeypatch.setattr(search, "product_from_json", lambda p, _s: p)
    monkeypatch.setenv("BASKET_STORE_TIMEOUT", "0.05")
    monkeypatch.setenv("BASKET_TIMEOUT", "5.0")
    monkeypatch.setenv("BASKET_PARALLEL_BATCH", "1")  # forces slow1 and fast2 into separate batches

    body = search.BasketRequest(items=[{"name": "arroz 1kg"}], stores=["slow1", "fast2"])
    result = await search.basket_compare(body, "Bearer token")

    # The old `break` on batch timeout would have stopped before ever trying
    # fast2's batch. It must still be reached and succeed.
    assert "fast2" in result["comparison"]
    assert result["best_store"] == "fast2"
