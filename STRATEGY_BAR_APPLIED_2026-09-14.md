# The six-point bar, applied to the village — 2026-09-14

Discharges two of the four items owed since the morning handoff of 2026-09-14
(§7.5): **beta-adjusted alpha** and **is the loss regime in the sample?** Point
3 (survives dropping the top contributors) is included because it was cheap and
because it turned out to be the loudest of the three.

The bar is `/Users/robbie/trade/STRATEGY_BAR.md` — six points, each one there
because it converted a fake result into a real one during the insider/options
study. The village had independently re-derived four; these are the two it had
not, and they are the two that needed a benchmark the village did not keep.

Reproduce with `python scripts/strategy_bar.py`. Reads only.

---

## Point 1 — beta-adjusted alpha, not raw return

The village reports "we made X, buy-and-hold made Y, the difference is Z". That
is not alpha. A desk running beta 0.3 in a rising market collects a market
component it did not choose and `trade benchmark` credits it as skill.

Village against SPY, 15-minute bars, balanced panel of 15 firms, 158 bars
(2026-08-28 → 2026-09-14):

```
village mean return : -0.0268% per bar
SPY mean return     : -0.0082% per bar

beta                : +0.3586      (t=+1.21)
alpha               : -0.0239% per bar   (t=-0.55)
market component    : -0.0029% per bar   = beta x SPY
share of return that is market : 11.0%
```

**Alpha is negative and not distinguishable from zero (t=-0.55).** Beta is
0.36, so only 11% of the village's (negative) return is market exposure. The
underperformance is *not* a beta story — it is not that the village was
defensively positioned in a rising market, nor that it was leveraged into a
falling one. It is alpha, and the alpha is indistinguishable from zero, which
is what costs and noise look like with no signal underneath.

## Point 4 — the loss regime is not in the sample

```
SPY bar move          n     village mean     worst bar
[-999%, -2.0%)        0         —                —
[-2.0%, -1.0%)        0         —                —
[-1.0%, -0.5%)        1      +0.5786%         +0.5786%
[-0.5%,  0.5%)      155      -0.0339%         -6.2445%
[ 0.5%,  1.0%)        1      +0.4679%         +0.4679%
[ 1.0%,  2.0%)        0         —                —
[ 2.0%,  999%)        0         —                —

worst single SPY bar : -0.98%   (6.7 sd against a bar sd of 0.146%)
bars with SPY <= -1% : 0 of 157   (0.0%)
```

**Zero observations of a down move.** 155 of 157 bars sit inside ±0.5%. The
village has never once been asked what it does when the market falls, so every
statement about its drawdown behaviour — including its own kill-switch
thresholds — is untested. This is the strangle-in-2020 case from the bar: the
sample contains no instance of the state the strategy would lose in.

Note also the asymmetry the two populated tails hint at, and do not trust it:
n=1 on each side.

## Point 3 — it does not survive dropping the top contributors

Realised P&L by symbol, all firms, whole history:

```
realised P&L across 30 symbols : $3,406.72

NVDA       $2,951.65   (40 closed)   86.6% of total
USO        $1,906.61   ( 3 closed)   56.0% of total
DOGE-USD   $1,614.54   (18 closed)   47.4% of total
PG         $1,252.75   (19 closed)   36.8% of total
JNJ        $1,103.29   (40 closed)   32.4% of total

drop top 1 :    $455.07    (+13.4% of original)
drop top 2 : -$1,451.54    (-42.6% of original)
drop top 5 : -$5,422.12   (-159.2% of original)
```

**One symbol is 86.6% of the village's realised P&L. Drop two and it is
negative.** `USO` produced $1,906 on *three* closed trades.

This is the concentration attack the bar's point 3 describes, and the village
fails it outright. Whatever the village has been doing, it is not a
diversified process with a small edge repeated many times; it is a handful of
trades on a handful of names, and the rest is a drag.

## What broke while measuring this — three times

Recorded because the measurement was wrong three times before it was right, and
each failure is the same family the rest of this project keeps finding.

1. **`firm_performance` is per tick, not per bar.** 624 rows share one `as_of`.
   Summing by `as_of` adds each firm to itself dozens of times: the village came
   out worth **$1.09 billion**.
2. **The set of firms changes between bars.** Summing equity over whoever is
   present makes the total jump on *composition*, not prices. That version
   reported **beta +6.20**, a single bar of **-47.9%**, and a mean compounding
   to -76% while the village was down 0.98%.
3. **Equity changes are not all returns.** A wind-up hands capital back and
   zeroes a firm's equity; a raise adds to it. Neither is performance. Only
   firm-bars whose `capital_base` is unchanged now contribute, and the worst bar
   fell from -47.9% to -6.2%.

A beta of +6.20 for a long-only book was the tell on (2), and a mean that
compounds to -76% against a known -0.98% was the tell on (3). Both were
arithmetic that could not be true, which is cheaper to notice than a subtle
bias and is worth checking first every time.

## What this licenses

Nothing is promoted, nothing changes allocation. What it changes is what may
be claimed:

- "The village underperforms buy-and-hold" stands, and is now known **not** to
  be a beta artefact.
- "The village has a drawdown profile" does **not** stand. It has never seen a
  down market.
- "The village has an edge that repeats" does **not** stand. It has NVDA.

Still owed from §7.5: the per-gene ablation ("does each gene earn its place?")
and the ATLAS/DECISIONS confidence ladder.
