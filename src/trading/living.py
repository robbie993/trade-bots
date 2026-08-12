"""What the village does on its own, beyond trading.

Three quarters of the map were built and then left dark: the arena holds
seasons only when somebody runs one, the bazaar lists nothing unless somebody
lists it, and the tavern's alliances form only by hand. They were part of the
village on the map and not in the loop, which is a strange kind of half-built —
you can see the building and nobody is ever inside it.

This module is the inside. Each tick it asks whether anything should happen in
those three places, and usually the answer is no: a season is a rare event, an
alliance is a rare event, and a village where something dramatic happens every
few seconds is noise rather than life.

**What it may not do.** The whole point of the ecosystem is that a kill reason
is reproducible from stored numbers, so:

* the tavern cannot reach the money at all — it is handed a read-only store and
  a writer restricted to two tables, enforced in ``sandbox/guard.py``, and this
  module gains no exception to that;
* the bazaar here trades only in **tokens**. Capital listings still exist and
  still work, and they still stop and ask a human before a dollar moves. This
  module does not create them, because a village that files capital-transfer
  requests while you sleep has turned the gate into a queue to be cleared
  rather than a decision to be made;
* nothing here votes on an approval. The council does that, in its own module,
  under its own config.

**Determinism.** The clock is the market's own bar date, not the wall clock, so
a replay of the same history produces the same village: the same alliance forms
on the same day, the same season runs. The RNG is seeded per event from that
date and the configured brain seed, so two runs agree and neither is
predictable enough to be dull.

Off unless asked for. ``TRADE_LIVING=on`` — see ``LivingConfig``.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from .config import TradingConfig

# Kept small on purpose: these are rare events, and the interesting failure
# mode of an automated village is not "too quiet".
DEFAULT_SEASON_EVERY = 10
DEFAULT_BAZAAR_EVERY = 4
DEFAULT_TAVERN_EVERY = 3

# A firm needs this many tokens spare before it will spend any. Espionage that
# bankrupts the spy is not intrigue, it is a bug with a story attached.
TOKEN_FLOOR = Decimal("40")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class LivingConfig:
    """Whether the arena, bazaar and tavern run themselves, and how often.

    Default off. Everything this module does is visible, reversible and
    incapable of moving cash — but it is still the village acting without being
    asked, and that should be a thing you turned on rather than a thing you
    discovered.

    The intervals are counted in *market bars*, not ticks, so they mean the
    same thing whether you are replaying history at speed or ticking once a
    minute against a live feed.
    """

    enabled: bool = field(
        default_factory=lambda: os.environ.get("TRADE_LIVING", "").strip().lower()
        in ("1", "true", "yes", "on")
    )
    season_every: int = field(
        default_factory=lambda: _env_int("TRADE_SEASON_EVERY", DEFAULT_SEASON_EVERY)
    )
    bazaar_every: int = field(
        default_factory=lambda: _env_int("TRADE_BAZAAR_EVERY", DEFAULT_BAZAAR_EVERY)
    )
    tavern_every: int = field(
        default_factory=lambda: _env_int("TRADE_TAVERN_EVERY", DEFAULT_TAVERN_EVERY)
    )


def _clock(market) -> Optional[int]:
    """The market's own bar date as a day number.

    Using the bar rather than the wall clock is what makes a replay
    reproducible: simulate the same 45 days twice and the same things happen on
    the same days.
    """
    try:
        stamp = market.as_of()
    except Exception:  # noqa: BLE001 - a feed without bars is not an error here
        return None
    if stamp is None:
        return None
    if isinstance(stamp, datetime):
        return stamp.date().toordinal()
    try:
        return stamp.toordinal()
    except AttributeError:
        return None


def _roll(seed: int, *parts: object) -> float:
    """A deterministic number in [0, 1) from the seed and what is happening."""
    key = ":".join(str(p) for p in (seed, *parts))
    digest = hashlib.sha256(key.encode()).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


class Living:
    """One tick of village life. Every method is best-effort and self-contained.

    Nothing here may break a tick. The ledger, the kill switches and the gate
    are the system; alliances and market stalls are decoration on top of it, and
    decoration that can take the system down is not decoration. So each quarter
    runs inside its own ``try`` and reports what went wrong instead of raising.
    """

    def __init__(self, eco, config: Optional[TradingConfig] = None):
        self.eco = eco
        self.config = config or eco.config
        self.living = getattr(self.config, "living", None) or LivingConfig()
        self.seed = int(getattr(self.config.brain, "seed", 0) or 0)

    def _open(self, quarter: str) -> bool:
        """Whether a quarter is open right now.

        The switch on the wall wins over the environment, because it is the
        more recent decision and the one made by a person looking at the
        village. An unset switch means nobody has overridden the config, so
        the environment decides — which is how this stays off by default.
        """
        return self.eco.settings.get(quarter, default=self.living.enabled)

    # =====================================================================
    def run(self, market, report) -> None:
        """Ask each quarter whether anything happens today."""
        day = _clock(market)
        if day is None:
            return

        every = self.living
        if every.season_every and day % every.season_every == 0 and self._open("arena"):
            self._guard("arena", report, self._season)
        if every.bazaar_every and day % every.bazaar_every == 0 and self._open("bazaar"):
            self._guard("bazaar", report, self._bazaar, day)
        if every.tavern_every and day % every.tavern_every == 0 and self._open("tavern"):
            self._guard("tavern", report, self._tavern, day)

    def _guard(self, where: str, report, func, *args) -> None:
        try:
            func(report, *args)
        except Exception as exc:  # noqa: BLE001 - see the class docstring
            report.village.append(f"{where}: {type(exc).__name__}: {exc}")

    # =====================================================================
    # the arena — a season of head-to-head, already written and already safe
    # =====================================================================
    def _season(self, report) -> None:
        result = self.eco.run_season()
        bouts = len(result.get("bouts") or [])
        awards = len(result.get("milestones") or [])
        if bouts or awards:
            report.village.append(
                f"arena: a season ran — {bouts} bout(s), {awards} milestone(s)"
            )

    # =====================================================================
    # the bazaar — tokens only, never capital
    # =====================================================================
    def _bazaar(self, report, day: int) -> None:
        market = self.eco.black_market
        firms = list(self.eco.store.firms())
        if len(firms) < 2:
            return

        self._maybe_list(report, market, firms, day)
        self._maybe_buy(report, market, firms, day)

    def _maybe_list(self, report, market, firms, day: int) -> None:
        """A firm that is not trading may sell the strategy it is not using."""
        idle = [
            f for f in firms
            if (f.is_killed or getattr(f.status, "value", "") == "paused") and f.genome
        ]
        if not idle:
            return
        already = {
            listing.seller for listing in market.listings(status="active")
            if listing.asset_type == "genome"
        }
        for firm in idle:
            if firm.firm_key in already:
                continue
            if _roll(self.seed, "list", day, firm.firm_key) > 0.5:
                continue
            price = Decimal(60) + Decimal(int(_roll(self.seed, "price", day, firm.firm_key) * 60))
            listing = market.list_asset(
                firm.firm_key,
                "genome",
                price,
                title=f"{firm.name}'s book, going spare",
            )
            report.village.append(
                f"bazaar: {firm.firm_key} listed its genome for {price} tokens "
                f"(listing #{listing.id})"
            )
            self.eco.flow.move("firms", "bazaar", f"{firm.firm_key} listed a genome",
                               firm=firm.firm_key, detail=listing.title)
            return          # one listing a day is plenty

    def _maybe_buy(self, report, market, firms, day: int) -> None:
        """Somebody with tokens to spare may buy what is on the stall."""
        listings = [
            listing for listing in market.listings(status="active")
            if listing.currency == "tokens"
        ]
        if not listings:
            return
        listing = listings[0]

        for firm in firms:
            if firm.firm_key == listing.seller or firm.is_killed:
                continue
            balance = self.eco.tokens.balance(firm.firm_key)
            spare = Decimal(balance.balance) - TOKEN_FLOOR
            if spare < Decimal(listing.price):
                continue
            if _roll(self.seed, "buy", day, firm.firm_key, listing.id) > 0.4:
                continue
            market.buy(firm.firm_key, listing.id)
            report.village.append(
                f"bazaar: {firm.firm_key} bought listing #{listing.id} "
                f"from {listing.seller} for {listing.price} tokens"
            )
            self.eco.flow.move("bazaar", "firms", f"{firm.firm_key} bought #{listing.id}",
                               firm=firm.firm_key, detail=listing.title)
            return

    # =====================================================================
    # the tavern — alliances and intrigue, which cannot reach the ledger
    # =====================================================================
    def _tavern(self, report, day: int) -> None:
        sandbox = self.eco.sandbox
        firms = [f for f in self.eco.store.firms() if not f.is_killed]
        if len(firms) < 2:
            return

        if self._maybe_ally(report, sandbox, firms, day):
            return
        self._maybe_scheme(report, sandbox, firms, day)

    def _maybe_ally(self, report, sandbox, firms, day: int) -> bool:
        """Two firms trading similar things find they have something to discuss."""
        existing = sandbox.alliances.all(status="active")
        spoken_for = {member for a in existing for member in a.members}
        free = [f for f in firms if f.firm_key not in spoken_for]
        if len(free) < 2 or len(existing) >= max(1, len(firms) // 3):
            return False
        if _roll(self.seed, "ally", day) > 0.35:
            return False

        # The pair with the most overlapping universe — firms that hold the
        # same things have the most to agree about.
        best, overlap = None, -1
        for i, one in enumerate(free):
            for other in free[i + 1:]:
                shared = len(set(one.universe or []) & set(other.universe or []))
                if shared > overlap:
                    best, overlap = (one, other), shared
        if best is None:
            return False

        one, other = best
        name = f"{one.firm_key}-{other.firm_key}"
        sandbox.form(name, one.firm_key, [other.firm_key],
                     pact=f"{one.name} and {other.name} agree to act together")
        report.village.append(
            f"tavern: {one.firm_key} and {other.firm_key} formed the alliance {name}"
            + (f" ({overlap} symbols in common)" if overlap > 0 else "")
        )
        self.eco.flow.move("firms", "tavern", f"{name} formed", firm=one.firm_key,
                           detail=f"{one.firm_key} + {other.firm_key}")
        return True

    def _maybe_scheme(self, report, sandbox, firms, day: int) -> None:
        """Somebody behind on the table takes an interest in somebody ahead."""
        if _roll(self.seed, "scheme", day) > 0.25:
            return
        ranked = sorted(
            firms,
            key=lambda f: Decimal(str(getattr(f, "score", 0) or 0)),
            reverse=True,
        )
        if len(ranked) < 2:
            return
        leader, laggard = ranked[0], ranked[-1]
        if leader.firm_key == laggard.firm_key:
            return

        balance = self.eco.tokens.balance(laggard.firm_key)
        if Decimal(balance.balance) - TOKEN_FLOOR <= 0:
            return

        result = sandbox.spy(laggard.firm_key, leader.firm_key)
        caught = getattr(result, "caught", False)
        report.village.append(
            f"tavern: {laggard.firm_key} spied on {leader.firm_key}"
            + (" — and was caught" if caught else "")
        )
        self.eco.flow.move(
            "firms", "tavern", f"{laggard.firm_key} spied on {leader.firm_key}",
            kind="alarm" if caught else "ok", firm=laggard.firm_key,
            detail="copied into the sandbox record; installs nothing",
        )


__all__ = ["Living", "LivingConfig", "TOKEN_FLOOR"]
