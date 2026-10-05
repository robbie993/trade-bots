"""The two sides of an A/B test keep their genomes: evolution and advisers
leave them alone. Live 2026-10-04: proposal #1303 was adopted on the take-profit
test's control twin, so the gap between the two was no longer the exit rules."""

from __future__ import annotations

import json
from decimal import Decimal

from src.trading import proposals
from src.trading.firms.spec import FirmSpec, load_firm_specs


def test_the_take_profit_pair_is_locked_in_the_shipped_config():
    specs = {s.firm_key: s for s in load_firm_specs()}
    assert specs["firm_e_momentum_tp"].genome_locked
    assert specs["firm_e_momentum_ctl"].genome_locked
    assert not FirmSpec.from_mapping("x", {"universe": ["SPY"]}).genome_locked


def _lock(eco, firm_key):
    eco.specs()[firm_key].genome_locked = True


def test_a_locked_firm_is_not_evolved(ecosystem):
    eco = ecosystem
    firm = eco.store.active_firms()[0]
    _lock(eco, firm.firm_key)
    assert eco.evolve(firm.firm_key) == []


def test_a_locked_firm_refuses_advice_before_and_after_the_council(ecosystem, monkeypatch):
    eco = ecosystem
    firm = eco.store.active_firms()[0]
    monkeypatch.setattr(eco.evolver, "trial", lambda *a, **k: {
        "enough_holdout": True, "holdout_bars": 60, "fitted_before": Decimal("1"),
        "fitted_after": Decimal("2"), "holdout_before": Decimal("1"),
        "holdout_after": Decimal("3")})
    pid = eco.db.insert("ai_proposals", {
        "firm_key": firm.firm_key, "question_id": 1, "proposed_by": "claude",
        "why": "w", "changes": json.dumps({"fast_window": 5}), "status": "awaiting_test"})
    proposals.test_one(eco, eco.market())
    approval = eco.gate.get(eco.db.query_one(
        "SELECT approval_id FROM ai_proposals WHERE id = ?", (pid,))["approval_id"])
    before = dict(eco.store.get_firm(firm.firm_key).genome)
    _lock(eco, firm.firm_key)
    note = proposals.adopt(eco, approval.details, "the council")
    assert "locked" in note
    assert eco.store.get_firm(firm.firm_key).genome == before

    pid = eco.db.insert("ai_proposals", {
        "firm_key": firm.firm_key, "question_id": 1, "proposed_by": "claude",
        "why": "w", "changes": json.dumps({"fast_window": 6}), "status": "awaiting_test"})
    proposals.test_one(eco, eco.market())
    row = eco.db.query_one("SELECT * FROM ai_proposals WHERE id = ?", (pid,))
    assert row["status"] == "refused" and "locked" in row["verdict"]
