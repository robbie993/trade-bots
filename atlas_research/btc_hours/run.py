"""Does Bitcoin's daily low and high cluster at certain hours, enough to trade?

The one research find both review AIs marked "test" (2026-10-04): "Intraday
Timing of Bitcoin Price and Volume Extremes". The reviewers' test, as run here:

1. 15-minute BTC/USD bars from Alpaca's crypto feed (free, no key needed for
   crypto, but the village keys are used when present).
2. Fit on everything except the last 6 months: for each 15-minute slot of the
   UTC day, how often the day's low and the day's high fall in it.
3. The rule: buy at the slot where the low is most often, sell at the slot
   where the high is most often (the same day, or the next if it comes first).
4. Held out, the last 6 months: the rule's trades after costs, against 2,000
   random rules that hold for the same length from a random slot.

Costs are Alpaca's crypto taker fee (25 bps a side) plus 5 bps half-spread.
Offline research only: nothing here trades or is imported by the village.

    python -m atlas_research.btc_hours.run            # writes results next to it
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

COST_PER_SIDE = 0.0025 + 0.0005
HOLDOUT_DAYS = 182
YEARS = 3
SLOTS = 96
OUT = Path(os.environ.get("BTC_HOURS_OUT", Path(__file__).parent / "results"))


def fetch(start: datetime, end: datetime) -> pd.DataFrame:
    import requests
    h = {}
    k = os.environ.get("ALPACA_API_KEY_ID") or os.environ.get("APCA_API_KEY_ID")
    s = os.environ.get("ALPACA_API_SECRET_KEY") or os.environ.get("APCA_API_SECRET_KEY")
    if k and s:
        h = {"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s}
    rows, token = [], None
    while True:
        p = {"symbols": "BTC/USD", "timeframe": "15Min", "start": start.isoformat(),
             "end": end.isoformat(), "limit": 10000}
        if token:
            p["page_token"] = token
        r = requests.get("https://data.alpaca.markets/v1beta3/crypto/us/bars",
                         params=p, headers=h, timeout=60)
        r.raise_for_status()
        j = r.json()
        rows += (j.get("bars") or {}).get("BTC/USD", [])
        token = j.get("next_page_token")
        if not token:
            break
    df = pd.DataFrame(rows)
    df["t"] = pd.to_datetime(df["t"], utc=True)
    return df.set_index("t")[["o", "h", "l", "c", "v"]].sort_index()


def slot_of(idx) -> np.ndarray:
    return (idx.hour * 4 + idx.minute // 15).to_numpy()


def extremes(df: pd.DataFrame) -> pd.DataFrame:
    """Per UTC day (complete days only): the slot of the low, high and top volume."""
    out = []
    for day, g in df.groupby(df.index.date):
        if len(g) < SLOTS - 2:
            continue
        s = slot_of(g.index)
        out.append({"day": day, "low": s[g["l"].to_numpy().argmin()],
                    "high": s[g["h"].to_numpy().argmax()], "vol": s[g["v"].to_numpy().argmax()]})
    return pd.DataFrame(out)


def trade_returns(df: pd.DataFrame, buy_slot: int, hold_slots: int) -> np.ndarray:
    """Net return of every trade: buy at the close of buy_slot, sell hold_slots later."""
    c = df["c"]
    full = c.resample("15min").last().ffill()
    s = slot_of(full.index)
    entries = np.where(s == buy_slot)[0]
    entries = entries[entries + hold_slots < len(full)]
    px = full.to_numpy()
    gross = px[entries + hold_slots] / px[entries]
    return gross * (1 - COST_PER_SIDE) ** 2 - 1


def main():
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    df = fetch(end - timedelta(days=365 * YEARS), end)
    split = end - timedelta(days=HOLDOUT_DAYS)
    fit, held = df[df.index < split], df[df.index >= split]

    ex = extremes(fit)
    low_freq = np.bincount(ex["low"], minlength=SLOTS) / len(ex)
    high_freq = np.bincount(ex["high"], minlength=SLOTS) / len(ex)
    vol_freq = np.bincount(ex["vol"], minlength=SLOTS) / len(ex)
    buy, sell = int(low_freq.argmax()), int(high_freq.argmax())
    hold = (sell - buy) % SLOTS or SLOTS

    ex_h = extremes(held)
    # does the clustering itself persist out of sample?
    persist_low = float((ex_h["low"] == buy).mean())
    persist_high = float((ex_h["high"] == sell).mean())

    rule = trade_returns(held, buy, hold)
    rng = np.random.default_rng(7)
    rand_means = np.array([trade_returns(held, int(rng.integers(SLOTS)), hold).mean()
                           for _ in range(2000)])
    bh = float(held["c"].iloc[-1] / held["c"].iloc[0] - 1)

    def hhmm(slot):
        return f"{slot // 4:02d}:{(slot % 4) * 15:02d} UTC"

    res = {
        "data": {"bars": len(df), "from": str(df.index[0]), "to": str(df.index[-1]),
                 "fit_days": len(ex), "held_out_days": len(ex_h)},
        "fit": {"low_slot": hhmm(buy), "low_share": round(float(low_freq[buy]), 4),
                "high_slot": hhmm(sell), "high_share": round(float(high_freq[sell]), 4),
                "top_volume_slot": hhmm(int(vol_freq.argmax())),
                "uniform_share": round(1 / SLOTS, 4), "hold_hours": hold / 4},
        "held_out": {"low_in_that_slot": round(persist_low, 4),
                     "high_in_that_slot": round(persist_high, 4),
                     "trades": int(len(rule)),
                     "rule_mean_net_per_trade": round(float(rule.mean()), 5),
                     "rule_win_rate": round(float((rule > 0).mean()), 4),
                     "rule_total_net": round(float(np.prod(1 + rule) - 1), 4),
                     "random_mean_net_per_trade": round(float(rand_means.mean()), 5),
                     "p_value_vs_random": round(float((rand_means >= rule.mean()).mean()), 4),
                     "btc_buy_and_hold": round(bh, 4)},
        "cost_per_side": COST_PER_SIDE,
    }
    res["verdict"] = ("PASS: beats random timing after costs (p<0.05) and makes money"
                      if res["held_out"]["p_value_vs_random"] < 0.05 and rule.mean() > 0
                      else "FAIL: does not beat random timing after costs")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "btc_hours.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
