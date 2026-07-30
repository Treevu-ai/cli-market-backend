# Changelog

All notable changes to `cli-market-backend`. This repo is **frozen/
deprecated as of 2026-07-25** — new development happens in
`cli-market-world`; see that repo's
[CHANGELOG.md](https://github.com/Treevu-ai/cli-market-world/blob/main/CHANGELOG.md)
for the full ecosystem picture. This file only tracks changes that
actually landed here (mostly dependency-pin bumps kept in sync for
correctness/dependency-graph reasons).

## [2026-07-30] — bump cli-market-core pin to 1.11.95 (register deltron_pe, disabled)

`requirements.txt` pin bumped to `cli-market-core==1.11.95` for
dependency-graph correctness. This repo is frozen and not deployed; the
release itself (`deltron_pe` added to `STORES`, disabled — B2B wholesale
login-gated prices, custom PHP storefront, no JSON API) shipped through
`cli-market-world`. See that repo's changelog for details.

## [2026-07-27] — cli-market-core pin bumped 1.11.86 → 1.11.88 (market_prices/basket_snapshot + GOLDEN_RECORDS_COUNT)

- Corrects a gap: the entry below documented the 1.11.87 pin bump but
  the `requirements.txt` commit for it was accidentally never made —
  caught while doing an end-of-session sweep. This commit jumps
  straight to 1.11.88, which also includes `GOLDEN_RECORDS_COUNT` /
  `GOLDEN_RECORDS_LABEL` (see cli-market-core's own CHANGELOG for
  detail — resolves a stale "97k+" placeholder in the intel-latam
  landing page with the real live count, 28,842).
- Same caveat as always: this repo is frozen, pin bump is for
  dependency-graph/history correctness only.

## [2026-07-27] — cli-market-core pin bumped 1.11.86 → 1.11.87 (market_prices, market_basket_snapshot)

- Kept in sync with `cli-market-core` 1.11.87. Same caveat as the entry
  below — this repo is frozen, pin bump is for dependency-graph/history
  correctness only. Production changes (this pin + 6 more world-only
  MCP tools + 3 tier-gating bug fixes) landed in
  [cli-market-world/CHANGELOG.md 2026-07-27](https://github.com/Treevu-ai/cli-market-world/blob/main/CHANGELOG.md).

## [2026-07-27] — cli-market-core pin bumped 1.11.85 → 1.11.86 (5 new quality/receipts MCP tools)

- Kept in sync with `cli-market-core` 1.11.86 (`market_receipts`,
  `market_quality_scores`, `market_quality_flagged`, `market_dispersion`,
  `market_coverage_matrix` added to the MCP registry). This repo is
  frozen — the pin bump is for dependency-graph/history correctness
  only; the tools don't actually deploy from here (see fly.toml's
  DO-NOT-DEPLOY note). Production deploy + the equivalent hand-written
  HTTP MCP tool additions happened in
  [cli-market-world/CHANGELOG.md 2026-07-27](https://github.com/Treevu-ai/cli-market-world/blob/main/CHANGELOG.md).

## [2026-07-26] — cli-market-core pin bumped 1.11.76 → 1.11.85, unblocking a broken test collection

**`cli-market-core` re-pinned across three bumps (c50aafc, 72c7fe1, 37c04b9)**
- Bumped `requirements.txt`'s `cli-market-core` pin in step with
  `cli-market-core`'s three same-day releases (1.11.83 → 1.11.84 →
  1.11.85), the middle one specifically to pick up a fix for a
  top-level `STORES` import regression.
- That regression (`from market_core import STORES` broken since
  `cli-market-core` commit `2529d54`) was live in every published
  version this repo could have installed from ~1.11.7x through 1.11.83
  — confirmed via a fresh venv install: 25 test modules here
  (`routers/agent.py`, `market_server.py`, `routers/search.py` all
  import `STORES` at the package level) failed collection with
  `ImportError` before the fix landed upstream, 0 failed after
  upgrading past it. All 405 tests now collect and run clean (399
  passed, 6 skipped).
- This means any clean deploy of this repo pinned to a `cli-market-core`
  version in that broken range (not relying on a stale cached wheel
  from before the regression) would have failed to start. Moot for
  this repo specifically since it's frozen and `fly.toml` deliberately
  blocks deploys from here with a fake app name — but the same
  regression did block real production deploys via `cli-market-world`'s
  pipeline until the fix landed there. Full root-cause writeup lives in
  `cli-market-core`'s changelog.
