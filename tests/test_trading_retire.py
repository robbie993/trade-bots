"""Retiring a firm: shut down on purpose, wound up, and no heir filed.

Deleting a firm's block from the YAML never deleted the firm, and killing one
filed an unfunded heir in its place, so ten idle Astral firms could only be
swapped for ten idle heirs. The `retired:` block is the third way out.
"""

from pathlib import Path

from src.money import D
from src.trading.firms import bankruptcy
from src.trading.firms.spec import load_firm_specs, load_retired
from src.trading.models import FirmStatus

REPO = Path(__file__).resolve().parent.parent


def _retire(eco, **why):
    path = Path(eco.config.firms_config)
    path.write_text(path.read_text() + "retired:\n" + "".join(
        f"  {key}: \"{reason}\"\n" for key, reason in why.items()))


def test_a_retired_firm_is_shut_down_wound_up_and_leaves_no_heir(ecosystem):
    eco = ecosystem
    _retire(eco, beta="withdrawn for the test")
    eco.init_firms()

    beta = eco.store.get_firm("beta")
    assert beta.is_killed and beta.kill_reason.startswith(bankruptcy.RETIRED)
    assert "alpha" in {f.firm_key for f in eco.store.active_firms()}, "only the named firm goes"

    closed = bankruptcy.wind_up(eco, beta)
    assert closed is not None and closed["successor"] == ""
    settled = eco.store.get_firm("beta")
    assert settled.status == FirmStatus.BANKRUPT.value and D(settled.cash) == 0
    assert not [f for f in eco.store.firms()
                if (f.genome or {}).get("inherited_from") == "beta"], "a retired firm files no heir"
    assert not [m for m in eco.memory.recall(firm_id=beta.id)
                if m.memory_type == "bankruptcy"], "and writes no lesson for the scribe"


def test_retiring_is_idempotent_and_returns_the_cash_once(ecosystem):
    eco = ecosystem
    _retire(eco, beta="withdrawn for the test")
    assert eco.retire_listed() == ["beta"]
    assert eco.retire_listed() == []
    released = eco.db.query(
        "SELECT id FROM brokerage_events WHERE event_type = 'allocation_released' "
        "AND firm_id = ?",
        (eco.store.get_firm("beta").id,))
    assert len(released) == 1


def test_a_retired_name_that_was_never_created_is_ignored(ecosystem):
    _retire(ecosystem, ghost="never existed")
    assert ecosystem.retire_listed() == []


def test_the_shipped_config_retires_the_astral_firms_and_no_live_one():
    config = REPO / "config" / "firm_config.yaml"
    retired = load_retired(path=config)
    assert {k for k in retired if k.startswith("astral_")} == {
        "astral_semis", "astral_megacap", "astral_indices", "astral_banks", "astral_energy",
        "astral_metals", "astral_btc_proxies", "astral_airlines", "astral_homebuilders",
        "astral_crypto"}
    live = {spec.firm_key for spec in load_firm_specs(path=config)}
    assert not live & set(retired), "a firm cannot be both configured and retired"
    assert all(retired.values()), "every retirement says why"
