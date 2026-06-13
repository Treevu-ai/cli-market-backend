"""Shim — canonical implementation in cli-market-core (``market_core.market_observatory``).

World keeps this top-level module for backward-compatible imports
(``from market_observatory import ObservatoryMiddleware``).
"""

from market_core.market_observatory import *  # noqa: F403,F401
