# Measurement audit — 2026-09-14

Handoff §7.3: *"Keep auditing, with the §5 question."* Not "is the code
correct" but **"is the measurement valid"** — the question that found six bugs
in tested code on the morning of 2026-09-14 and eight more since.

Surfaces named as unasked: `conscience`, `retention`, `council`,
`competition/arena`, `black_market`, `sandbox`. All six are now asked. This is
what each said.

---

## `sandbox` — a raw-SQL door on the object that enforces the fence

**Defect, fixed (`feb2782`).** `SandboxWriter._check(table)` guarded `insert`
and `update`; `query` passed its string to `Database.query`, which is
`cursor.execute` and does not care what verb it is given.

```
guarded path: refused -> the sandbox may not write to 'firms'
query path  : no exception raised
after       : equity 10000.0 -> 1.0   (and DELETE emptied the table)
after reopen: 1.0                     (it committed)
```

The existing test is the point: `test_the_sandbox_cannot_reach_raw_sql`
asserts the *attribute* `db` is refused — it is — while the raw-SQL door on
the same object was never asked about.

## `council` — the missing benchmark, in the one body with no human

**Defect, fixed (`a946d9d`).** The `performance` juror voted MEDIUM for a
capital raise on `return_pct > 0`. Live: `.env` sets `TRADE_AUTONOMY=council`.

```
rulings where the juror voted            30
... of which FOR                         30   — never once against
verdict flips if it votes the other way  12 of 30 (40%)
grants that would flip                    0
```

No grant would have flipped — all 12 flips are defer→refuse, and defer is the
conservative state. A juror that votes for 30 times and against none is not
weighing anything.

## `competition/arena` + `tokens` — cleared

**No defect.** The arena awards a "profitable" milestone on `return_pct > 0`,
unbenchmarked. Traced the consumers: tokens and reputation reach only the web
display and the sandbox, which is itself fenced. It is a closed cosmetic loop
and it says so. Like `kill_switch`, **correctly not benchmarked.**

One thing left unchased and recorded here: `Arena.bout` can settle on
`return_pct` between firms with *different universes*, so a crypto desk beats
an ETF desk on raw return regardless of skill. The default metric is `score`,
which is benchmarked since the evening fix, and nothing downstream moves
capital — so this is noted, not filed.

## `black_market` — cleared

**No defect found.** Its fence is that capital listings do not settle inside
it: a capital sale is a cut plus a raise, a raise needs a human, so the market
requests one and stops. Read the path; it holds.

## `retention` — the instrument existed and had never been read

**No defect in the module. A finding from running it.** The morning session
built `retention.py`, called R(T) *"the one number that decides whether a
village is worth building"*, added `trade retention`, and nobody ran it.

```
     delay      mean    median   per-bar  entries  bars
  no delay      2.97     -1.52      9.67      637   146  FRAGILE  R=1.00
    +1 bar      2.67     -1.49      7.53      636   145  FRAGILE  R=0.90
    +4 bar     -0.47     -2.30      3.89      635   144            R=-0.16

T* : no delay clears the 14.00 bps round trip
```

**At zero latency, by all three summaries, the entry edge does not cover
costs.** The module's own reading: *"there is no edge here to lose to latency,
so speed is not what is wrong."* The village is not slow. There is nothing to
be fast about.

## `conscience` — sound, and it is not in the backtest

**No defect in the conscience.** Two things were checked and both hold:

*Every order passes it.* There are exactly two `venue.execute` call sites in
the repository. The live one (`ecosystem.py:1073`) is preceded by
`heart.consider` and a `block` verdict `continue`s before the venue.

*The units bug is genuinely fixed.* Its docstring records a liberty check that
read a 6.66 ETH order as 318% of a day's volume. Blocks at that magnitude are
**historical** — 1,166 on 2026-08-17, 93 on 2026-08-19, and recent liberty
blocks read 10–51%. Zero in the last 24 hours.

**The finding is the other call site.** `backtest.py:255` executes with no
conscience at all. `is_executable` checks `ethics_verdict != BLOCK`, and a
backtest never sets that field.

The risk manager *is* applied in both — it lives inside `firm.propose` via
`_review`, so the backtester honours it. The gap is the ethics layer alone,
and it is not small:

```
total proposals                    57,247
passed the risk manager             7,478
ethics-blocked among those          5,276

the backtester executes             7,478
the live village executes           2,202
conscience removes 70.6% of what the backtest trades
```

**Every fitness number in the village is measured on a firm that makes 3.4x
the trades the live firm makes.** Fitness ranks genomes, scores court
submissions, and feeds the holdout rank test.

Worse than the ratio: the removed trades are not a random 70%. The blocks are
overwhelmingly `liberty` — the order is too large against the symbol's typical
daily volume — so what the backtest adds back is precisely the illiquid
fills that would have been most expensive in reality. The bias has a direction,
and it flatters.

Not fixed here. Wiring the conscience into the backtester changes every fitness
number in the system and would require `MEASUREMENT_EPOCH` to go to 3,
discarding the epoch-2 data collected today. That is a decision — see
`DECISIONS.md` D-V008 — not a patch.

---

## What the six surfaces say together

Two real defects (`sandbox`, `council`), two cleared (`arena`,
`black_market`), and two where the module was fine and **running it was the
finding** (`retention`, `conscience`).

The pattern the evening session named still holds: none of the existing tests
asked whether the measurement was valid. They asked whether the code ran. But
the two largest findings today came from neither reading code nor writing
tests — they came from *executing the instrument that already existed* and
reading what it said.
