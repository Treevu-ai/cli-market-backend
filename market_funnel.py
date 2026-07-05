"""Shim — canonical implementation in cli-market-core (``market_core.market_funnel``).

Backend keeps this top-level module for backward-compatible imports
(``from market_funnel import record_funnel_event``).
"""

from market_core.market_funnel import *  # noqa: F403,F401
