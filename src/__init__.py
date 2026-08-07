"""Minimum Viable Village (MVV) — Phase 1, "The Experiment Ledger".

The smallest system that can answer one question: can it consistently find,
test, market, fulfill and manage profitable products with real money after all
costs? If yes, scale. If no, kill it.

Five components (spec section 2), in src/agents/:

    1. Scout                — discover products
    2. Economic Calculator  — unit economics, margin, CAC, ROI
    3. Experiment Ledger    — the single source of truth
    4. Human Approval Gate  — hard boundary for spend
    5. Orchestrator         — coordinate the loop

Nothing else. See spec section 2.1 for the list of things deliberately not
built in Phase 1.
"""

from __future__ import annotations

__version__ = "1.0.0"
