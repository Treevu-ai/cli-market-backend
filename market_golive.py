"""Shim — canonical implementation in cli-market-core (``market_core.market_golive``).

Backend keeps this top-level module for backward-compatible imports
(``from market_golive import go_live_summary``).
"""

from market_core.market_golive import *  # noqa: F403,F401
