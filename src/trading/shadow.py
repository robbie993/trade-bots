"""The shadow desk — options on paper, on the side the evidence actually favours.

**It sells premium.** The first version of this file bought it, and three
independent measurements say that was the wrong side of the same trade:

* 210,509 bar-days of 2024 option history: buy a contract and hold five days
  and the mean return is **-12.98%**, the median **-45.28%**, winning 23.4% of
  the time — before any spread;
* the Alpaca account's own 21 written contracts, imported into this table:
  **+$2,931 realised** across 13 closed, every one of them short;
* the sibling repo's wheel backtest at Sharpe 0.84 and a 94.4% win rate, and
  `option_bot.py`'s evidence base — the Cboe PUT index over 32 years at
  +1.61%/yr alpha, Sharpe 0.65 against the S&P's 0.49.

Buying premium pays theta every day. Selling it collects theta every day. The
village's live account has been on the collecting side and is up; this desk was
built on the paying side and measured how reliably it loses.

**Why it still cannot do this for real.** `risk_manager` blocks a written
option because no fraction-of-capital limit can size an unbounded loss, and
that rule is right. A shadow desk risks nothing — its worst outcome is a row in
a table — so it can explore the side the ledger must not, and produce the
evidence that would justify ever changing that rule.

**What keeps it honest.**

*A seller receives the bid and pays the ask to close.* The mirror of a buyer,
and stated explicitly, because getting this backwards would manufacture the
exact edge the desk exists to test.

*Real and imagined never mix.* Imported Alpaca fills carry `source='real'`;
this desk writes `source='shadow'`. A score blending them would be part
evidence and part invention with no way to separate them afterwards.

*Its knobs are genes.* The first version hardcoded every constant, so the
evolver had nothing to change and the desk could have run for a year without
learning anything. They are in `GENES` now, and mutants face the same held-out
test that rejected three firm genomes on 31 August.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from ..money import D, ZERO, money
from .data.options_feed import AlpacaOptionFeed

DESK = "shadow_options"

#: Genes this desk is tuned on. Registered in `brain/evolver.GENES` so the
#: evolver can actually move them — the whole reason the first version could
#: not learn is that its equivalents were module constants.
GENE_DEFAULTS = {
    "shadow_dte_min": 21,
    "shadow_dte_max": 45,
    "shadow_strike_sd": 1.0,      # how far below spot to write, in realised SD
    "shadow_spread_cap": 15.0,    # round-trip % beyond which a contract is refused
    "shadow_confidence": 20.0,
}


@dataclass
class ShadowReport:
    opened: list = field(default_factory=list)
    closed: list = field(default_factory=list)
    refused: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def _gene(genome, name):
    try:
        return D(str((genome or {}).get(name, GENE_DEFAULTS[name])))
    except Exception:  # noqa: BLE001 - a malformed gene is the default gene
        return D(str(GENE_DEFAULTS[name]))


def _strike_of(occ: str) -> Optional[Decimal]:
    try:
        return D(int(occ[-8:])) / D(1000)
    except (ValueError, TypeError):
        return None


def _expiry_of(occ: str):
    """The expiry date in an OCC symbol, or None.

    The date is the six digits before the C/P, which sit at a fixed offset from
    the *end* rather than from the start — roots are two to six characters, so
    counting forwards gets SPY right and SOFI wrong.
    """
    from datetime import date

    try:
        stamp = occ[-15:-9]
        return date(2000 + int(stamp[:2]), int(stamp[2:4]), int(stamp[4:6]))
    except (ValueError, TypeError, IndexError):
        return None


def _realised_sd(closes) -> Optional[Decimal]:
    """Standard deviation of recent returns. None when it cannot be measured.

    Used to place the strike rather than to forecast anything: writing "one SD
    below spot" is the rule `option_bot.py` settled on, and its own header notes
    that every entry filter tried on top of it made results worse.
    """
    if not closes or len(closes) < 20:
        return None
    rets = [(b - a) / a for a, b in zip(closes, closes[1:]) if a]
    if len(rets) < 10:
        return None
    mean = sum(rets, ZERO) / D(len(rets))
    var = sum(((r - mean) ** 2 for r in rets), ZERO) / D(len(rets))
    return var.sqrt() if var > 0 else None


#: How many genomes are graded against each other every bar. The `live` arm is
#: the desk's actual stance; the rest are counterfactuals.
ARMS = int(os.environ.get("TRADE_SHADOW_ARMS", "5") or 5)


def _seed(bar: str, index: int) -> int:
    """A stable seed for one arm on one bar.

    **Not `hash()`.** Python salts string hashing per process, so `hash(bar)`
    returns a different number every time the loop restarts — three runs of the
    same expression gave 3384743159, 3098802827 and 2029932392. Seeding the
    arms with it would have made the reproducibility this module claims a plain
    falsehood: the same bar would grade different genomes on every restart, the
    arm names would never recur, and no arm could ever accumulate the evidence
    it is supposed to be judged on. A digest is stable across processes.
    """
    import hashlib

    digest = hashlib.sha256(f"{bar}|{index}".encode()).digest()
    return int.from_bytes(digest[:4], "big")


def _mutate(genome: dict, seed: int) -> dict:
    """One neighbour of the current genome, deterministically.

    Seeded from the bar rather than the clock, so a replay of the same history
    grades the same arms — the property the evolver insists on and the reason
    its results can be audited at all.
    """
    import random

    from .brain.evolver import GENES

    rng = random.Random(seed)
    out = dict(genome)
    for name in GENE_DEFAULTS:
        low, high, is_int = GENES[name]
        current = D(str(out.get(name, GENE_DEFAULTS[name])))
        # A neighbour, not a fresh draw: a genome that jumps the whole range
        # each bar is a random search wearing evolution's clothes.
        span = (high - low) * D("0.25")
        moved = current + D(str(rng.uniform(-float(span), float(span))))
        moved = max(low, min(high, moved))
        out[name] = int(moved) if is_int else float(round(float(moved), 3))
    if out["shadow_dte_max"] <= out["shadow_dte_min"]:
        out["shadow_dte_max"] = out["shadow_dte_min"] + 7
    return out


class ShadowDesk:
    """Writes puts on paper, and grades several genomes doing it at once.

    **Why this learns faster than a firm.** A firm evolves once every
    `TRADE_EVOLVE_EVERY` bars and yields one genome's worth of evidence in that
    window. This desk evaluates `ARMS` genomes against the *same* chain on
    every bar and records what each would have written. A bar produces as many
    observations as there are arms, at no risk, because none of it touches
    money — and they are compared on identical prices, which is the condition
    the evolver already insists on and the reason a comparison means anything.

    The chain is fetched once per underlying and shared across the arms, so
    five genomes cost one network call rather than five. The rate limit has
    caused real outages in this village and the arms are not worth another.
    """

    name = DESK

    def __init__(self, store, board, universe=None, feed=None, genome=None):
        self.store = store
        self.board = board
        self.feed = feed or AlpacaOptionFeed()
        self.universe = list(universe or ("SPY", "QQQ", "IWM"))
        self.genome = dict(genome or {})

    # -- its own book ------------------------------------------------------
    def open_trades(self) -> list:
        try:
            return self.store.db.query(
                "SELECT * FROM shadow_trades WHERE desk = ? AND source = 'shadow' "
                "AND closed_at IS NULL", (DESK,)) or []
        except Exception:  # noqa: BLE001
            return []

    def _holds(self, underlying: str) -> bool:
        return any(str(r["underlying"]) == underlying for r in self.open_trades())


    def _pick(self, quotes, spot, genome, bullish: bool):
        """The contract this genome would write, or (None, why not).

        **Both sides.** Bullish writes a put below the market; bearish writes a
        call above it. The first version wrote puts only, on the stated basis
        that "the account's own record is 21 short puts" — which was simply
        false. It is 10 puts and 11 calls, and the calls are the majority by
        count. Restricting the desk to puts excluded more than half of what the
        account actually does, on a fact nobody had checked.

        The put side has made more money ($2,469 against $462), which is a
        reason to expect a difference between the two and precisely not a
        reason to hide one of them from the desk. Whether that gap is edge or
        sample is exactly what the arms are for.
        """
        sd_mult = _gene(genome, "shadow_strike_sd")
        cap = _gene(genome, "shadow_spread_cap")
        right = "P" if bullish else "C"
        # A writer wants the market to stay on their side of the strike: a put
        # below spot when bullish, a call above it when bearish.
        target = spot * (D(1) - sd_mult * self._sd) if bullish else \
                 spot * (D(1) + sd_mult * self._sd)
        best, gap = None, None
        for quote in quotes:
            if quote.symbol[-9] != right or quote.bid <= 0:
                continue
            if quote.round_trip_pct > cap:
                continue
            strike = _strike_of(quote.symbol)
            if strike is None:
                continue
            # Out of the money, whichever side: below spot for a put, above
            # for a call. Writing in the money is a different trade entirely.
            if bullish and strike >= spot:
                continue
            if not bullish and strike <= spot:
                continue
            distance = abs(strike - target)
            if gap is None or distance < gap:
                best, gap = quote, distance
        if best is None:
            kind = "put" if bullish else "call"
            return None, f"no {kind} inside {cap}% near {target:.2f}"
        return best, ""

    def arms(self, bar: str) -> dict:
        """The genomes graded this bar: the live one, plus its neighbours.

        Named by content rather than by index, so an arm that recurs across
        bars accumulates evidence under one name instead of being counted as a
        new idea every time.
        """
        out = {"live": dict(self.genome)}
        for i in range(1, ARMS):
            mutant = _mutate(self.genome, _seed(bar, i))
            key = ("sd{shadow_strike_sd}/dte{shadow_dte_min}-{shadow_dte_max}"
                   "/cap{shadow_spread_cap}").format(**mutant)
            out[key] = mutant
        return out

    # -- the bar -----------------------------------------------------------
    def run(self, market, as_of=None) -> ShadowReport:
        report = ShadowReport()
        as_of = as_of if as_of is not None else market.as_of()
        if as_of is None:
            return report
        bar = str(as_of)

        self._settle(bar, report)
        arms = self.arms(bar)

        for underlying in self.universe:
            reading = self.board.reading(underlying, as_of)
            if reading is None:
                continue
            # **Both directions.** Bullish writes a put below the market,
            # bearish writes a call above it. This used to skip every bearish
            # reading, on the stated grounds that the account only wrote puts.
            # It writes 10 puts and 11 calls; the claim was never checked and
            # half the strategy was excluded because of it.
            bullish = D(reading.score) > 0
            if D(reading.score) == 0:
                continue

            spot = D(market.mark(underlying))
            self._sd = _realised_sd(market.closes(underlying, 60))
            if spot <= 0 or self._sd is None:
                continue

            # One chain, every arm. Five genomes for one network call.
            lo = min(int(_gene(g, "shadow_dte_min")) for g in arms.values())
            hi = max(int(_gene(g, "shadow_dte_max")) for g in arms.values())
            today = date.today()
            # Ask for the side and the strikes we can actually use. Without
            # `right` the chain comes back calls-only (symbol order, "C"
            # before "P"), so the bullish branch was offered nothing to write
            # and silently skipped every bar. Without the strike window the
            # 100-contract limit is spent on strikes 260 points away.
            widest = max(_gene(g, "shadow_strike_sd") for g in arms.values())
            reach = spot * widest * self._sd * D(2)
            quotes = self.feed.chain(
                underlying,
                expiry_gte=(today + timedelta(days=lo)).isoformat(),
                expiry_lte=(today + timedelta(days=hi)).isoformat(),
                right="put" if bullish else "call",
                strike_gte=(spot - reach) if bullish else spot,
                strike_lte=spot if bullish else (spot + reach))
            if not quotes:
                report.notes.append(
                    f"{underlying}: no chain — "
                    f"{self.feed.last_error.get(underlying, 'nothing quoted')}")
                continue

            for arm, genome in arms.items():
                if D(reading.confidence) < _gene(genome, "shadow_confidence"):
                    continue
                if self._holds_arm(underlying, arm):
                    # Say so. A silent skip here is why a bearish bar looked
                    # like the desk doing nothing rather than the desk
                    # declining to double its position.
                    report.notes.append(
                        f"{underlying}/{arm}: already holds one, not adding")
                    continue
                # Each arm sees only the expiries its own genes allow, out of
                # the one chain everybody shares. **This used to be
                # `if lo <= x or True`** — a filter that always passed, so every
                # arm saw the whole window and `shadow_dte_min`/`_max` changed
                # nothing. The arms were named after genes that had no effect,
                # and any difference between them was strike and spread alone.
                arm_lo = int(_gene(genome, "shadow_dte_min"))
                arm_hi = int(_gene(genome, "shadow_dte_max"))
                window = []
                for quote in quotes:
                    expiry = _expiry_of(quote.symbol)
                    if expiry is None:
                        continue
                    dte = (expiry - today).days
                    if arm_lo <= dte <= arm_hi:
                        window.append(quote)
                pick, why = self._pick(window, spot, genome, bullish)
                if pick is None:
                    if arm == "live":
                        report.refused.append(f"{underlying}: {why}")
                    continue
                self.store.db.insert("shadow_trades", {
                    "desk": DESK, "source": "shadow", "arm": arm,
                    "contract": pick.symbol, "underlying": underlying,
                    "side": "sell", "quantity": 1,
                    # A seller is filled at the bid. The one sign that would
                    # invent an edge if it were wrong.
                    "entry_price": str(pick.sell_at),
                    "entry_mid": str(pick.mid),
                    "spread_pct": str(pick.round_trip_pct),
                    "reason": (f"{arm}: wrote {'put' if bullish else 'call'} "
                               f"{_gene(genome,'shadow_strike_sd')}sd "
                               f"{'below' if bullish else 'above'} {spot:.2f}; "
                               f"conf {reading.confidence}"),
                    "opened_bar": bar,
                })
                if arm == "live":
                    report.opened.append(
                        f"SOLD {pick.symbol} at {pick.sell_at} "
                        f"(spread {pick.round_trip_pct:.1f}%)")
        return report

    def _holds_arm(self, underlying: str, arm: str) -> bool:
        return any(str(r["underlying"]) == underlying and str(r["arm"]) == arm
                   for r in self.open_trades())

    def _settle(self, bar: str, report: ShadowReport) -> None:
        """Buy the written put back, at the ask, when it can be quoted."""
        for row in self.open_trades():
            quotes = {q.symbol: q for q in self.feed.chain(str(row["underlying"]))}
            quote = quotes.get(str(row["contract"]))
            if quote is None or quote.ask <= 0:
                continue
            received = D(str(row["entry_price"]))
            # Sold at the bid, bought back at the ask: premium kept minus cost.
            realised = money((received - quote.buy_at) * D(str(row["quantity"])) * D(100))
            self.store.db.execute(
                "UPDATE shadow_trades SET exit_price = ?, realized = ?, "
                "closed_bar = ?, closed_at = ? WHERE id = ?",
                (str(quote.buy_at), str(realised), bar,
                 datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 row["id"]))
            report.closed.append(
                f"BOUGHT BACK {row['contract']} at {quote.buy_at} ({realised:+})")

    # -- the scoreboard ----------------------------------------------------
    def leaderboard(self) -> list:
        """Every arm, ranked by what it actually made. The desk's own report card.

        This is the whole point of running arms in parallel: a firm waits
        TRADE_EVOLVE_EVERY bars to learn one genome's worth, and this compares
        several on identical chains every bar. `n` is stated beside the money
        because an arm that is ahead on three trades is ahead on nothing — the
        village has already been fooled once by a 100% win rate over 13 short
        premium trades, which is the expected shape rather than an edge.
        """
        try:
            rows = self.store.db.query(
                "SELECT arm, realized FROM shadow_trades WHERE desk = ? "
                "AND source = 'shadow' AND closed_at IS NOT NULL", (DESK,)) or []
        except Exception:  # noqa: BLE001
            return []
        by_arm: dict = {}
        for row in rows:
            by_arm.setdefault(str(row["arm"]), []).append(D(str(row["realized"] or 0)))
        out = []
        for arm, values in by_arm.items():
            wins = sum(1 for v in values if v > 0)
            out.append({
                "arm": arm,
                "n": len(values),
                "net": money(sum(values, ZERO)),
                "mean": money(sum(values, ZERO) / D(len(values))),
                "win_rate": round(100.0 * wins / len(values), 1),
            })
        return sorted(out, key=lambda r: -r["mean"])

    def adopt_best(self, min_trades: int = 20) -> Optional[dict]:
        """Promote the winning arm, but only once it has earned the right.

        `min_trades` is the guard that stops this being a random walk. Without
        it the desk would chase whichever neighbour got lucky on its first
        trade, which is not learning — it is drift with a leaderboard.
        """
        board = [r for r in self.leaderboard() if r["n"] >= min_trades]
        if len(board) < 2:
            return None
        best = board[0]
        if best["arm"] == "live":
            return None
        return best

    def record(self, source: str = "shadow") -> dict:
        """What this desk made, and what the real account made, side by side."""
        try:
            rows = self.store.db.query(
                "SELECT realized FROM shadow_trades WHERE desk = ? AND source = ? "
                "AND closed_at IS NOT NULL", (DESK, source)) or []
        except Exception:  # noqa: BLE001
            return {"closed": 0}
        if not rows:
            return {"closed": 0, "net": money(ZERO), "win_rate": 0.0}
        values = [D(str(r["realized"] or 0)) for r in rows]
        wins = sum(1 for v in values if v > 0)
        return {
            "closed": len(values),
            "net": money(sum(values, ZERO)),
            "win_rate": round(100.0 * wins / len(values), 1),
        }


__all__ = ["ShadowDesk", "ShadowReport", "DESK", "GENE_DEFAULTS"]
