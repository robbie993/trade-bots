"""R3-C: SPY calls plus T-bills, sized to a delta target.

Positions are whole contracts (100 shares each), which is why this book
starts at $1,000,000: at $100,000 one contract is about 40% of the delta
target and the book could not track it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from . import rules as R

C_START_EQUITY = 1_000_000.0


def _norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_call(S, K, T, vol, r=0.04):
    """Black-Scholes call price and delta. Only for marking when no quote exists."""
    if T <= 0 or vol <= 0:
        return max(S - K, 0.0), (1.0 if S > K else 0.0)
    d1 = (math.log(S / K) + (r + vol * vol / 2) * T) / (vol * math.sqrt(T))
    d2 = d1 - vol * math.sqrt(T)
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2), _norm_cdf(d1)


@dataclass
class OptionBook:
    name: str = "R3-C trend + convexity"
    cash: float = C_START_EQUITY
    positions: dict = field(default_factory=dict)   # occ -> {qty, expiry, strike}
    pending: list = field(default_factory=list)     # [(occ, expiry, strike, dqty)]
    equity: float = C_START_EQUITY
    last_day: str | None = None
    trades: int = 0
    costs_paid: float = 0.0
    modeled_fills: int = 0
    history: list = field(default_factory=list)     # [day, equity, delta exposure]

    def to_dict(self):
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        d["pending"] = [tuple(x) for x in d.get("pending", [])]
        return cls(**d)


def _dte(expiry: str, day: date) -> int:
    return (date.fromisoformat(expiry) - day).days


def _quote(feed, occ, day, spy, strike, expiry, vol):
    """(bid, ask, mid, delta, modeled?) for one contract on one day."""
    q = feed.option_quote(occ, day)
    T = max(_dte(expiry, day), 0) / 365
    model_px, model_delta = bs_call(spy, strike, T, vol)
    if q and q.get("bid") and q.get("ask"):
        mid = (q["bid"] + q["ask"]) / 2
        return q["bid"], q["ask"], mid, q.get("delta") or model_delta, False
    close = (q or {}).get("close") or model_px
    return close * (1 - R.OPTION_MODELED_SPREAD), close * (1 + R.OPTION_MODELED_SPREAD), close, \
        (q or {}).get("delta") or model_delta, True


def step(book: OptionBook, feed, day: date, spy: float, spy_sma: float, spy_vol: float,
         cash_rate: float) -> None:
    # 1. interest on cash, settle anything that expired
    if book.last_day is not None:
        book.cash *= 1 + cash_rate
    for occ, p in list(book.positions.items()):
        if _dte(p["expiry"], day) < 0:
            book.cash += p["qty"] * 100 * max(spy - p["strike"], 0.0)
            del book.positions[occ]

    # 2. fill yesterday's decisions at today's quotes
    for occ, expiry, strike, dq in book.pending:
        bid, ask, _, _, modeled = _quote(feed, occ, day, spy, strike, expiry, spy_vol)
        px = ask if dq > 0 else bid
        book.cash -= dq * 100 * px + abs(dq) * R.OPTION_FEE_PER_CONTRACT
        book.costs_paid += abs(dq) * (100 * (ask - bid) / 2 + R.OPTION_FEE_PER_CONTRACT)
        book.trades += 1
        book.modeled_fills += int(modeled)
        p = book.positions.setdefault(occ, {"qty": 0, "expiry": expiry, "strike": strike})
        p["qty"] += dq
        if p["qty"] == 0:
            del book.positions[occ]
    book.pending = []

    # 3. mark the book
    marks = {}
    for occ, p in book.positions.items():
        marks[occ] = _quote(feed, occ, day, spy, p["strike"], p["expiry"], spy_vol)
    book.equity = book.cash + sum(p["qty"] * 100 * marks[o][2] for o, p in book.positions.items())
    delta_sh = sum(p["qty"] * 100 * marks[o][3] for o, p in book.positions.items())
    exposure = delta_sh * spy / book.equity if book.equity > 0 else 0.0
    book.history.append([str(day), round(book.equity, 2), round(exposure, 4)])
    book.last_day = str(day)

    # 4. decide at today's close
    target = R.C_DELTA_UP if spy > spy_sma else R.C_DELTA_DOWN
    orders = []
    keep_delta = 0.0
    for occ, p in book.positions.items():
        d = marks[occ][3]
        if _dte(p["expiry"], day) <= R.C_ROLL_DTE or not (R.C_DELTA_BAND[0] <= d <= R.C_DELTA_BAND[1]):
            orders.append((occ, p["expiry"], p["strike"], -p["qty"]))
        else:
            keep_delta += p["qty"] * 100 * d * spy / book.equity
    rolled = bool(orders)
    gap = target - keep_delta
    if not rolled and abs(keep_delta / target - 1) <= R.C_RESIZE_TOLERANCE:
        return
    if gap < 0:
        # too much delta: trim every remaining position by the same share
        share = min(1.0, -gap / keep_delta) if keep_delta > 0 else 0.0
        for occ, p in book.positions.items():
            if any(o[0] == occ for o in orders):
                continue
            n = math.floor(p["qty"] * share)
            if n:
                orders.append((occ, p["expiry"], p["strike"], -n))
        book.pending = orders
        return
    pick = choose_contract(feed.option_chain(day), day)
    if pick is None:
        book.pending = orders
        return
    bid, ask, mid, d, _ = _quote(feed, pick["symbol"], day, spy, pick["strike"], pick["expiry"], spy_vol)
    n = math.floor(gap * book.equity / (100 * d * spy)) if d > 0 else 0
    held_premium = sum(p["qty"] * 100 * marks[o][2] for o, p in book.positions.items()
                       if not any(x[0] == o for x in orders))
    room = R.C_PREMIUM_CAP * book.equity - held_premium
    n = max(0, min(n, math.floor(room / (100 * ask)) if ask > 0 else 0))
    if n:
        orders.append((pick["symbol"], pick["expiry"], pick["strike"], n))
    book.pending = orders


def choose_contract(chain: list, day: date):
    """60-120 DTE, expiry nearest 90 days, then delta nearest 0.70."""
    ok = [c for c in chain if R.C_DTE_MIN <= _dte(c["expiry"], day) <= R.C_DTE_MAX and c.get("delta")]
    if not ok:
        return None
    exp = min({c["expiry"] for c in ok}, key=lambda e: abs(_dte(e, day) - R.C_DTE_PREFERRED))
    same = [c for c in ok if c["expiry"] == exp]
    return min(same, key=lambda c: abs(c["delta"] - R.C_TARGET_DELTA))
