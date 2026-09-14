# Handoff — 2026-09-14, evening

Second session of the day. `HANDOFF_2026-09-14.md` is the morning's and is still
accurate about everything it describes — **do not overwrite it**; this one
continues from it.

Read §5 first if you only read one part. It is the finding, and it is not a
finding about strategies.

---

## 1. The goal

Unchanged, and it earned itself twice in one day. The village is a multi-agent
paper-trading system: nine firm lineages evolve small genomes and trade a shared
universe on 15-minute bars. **The goal is not a profitable bot. It is a process
that makes false confidence hard.** A trustworthy negative result is a win.

The morning session killed six conclusions that were artefacts of the
measurement. This session found **six bugs in the measuring apparatus itself**,
every one of them in code that had tests, and every one of those tests passing.

---

## 2. Current state

**Trader:** PID 95517, `venv/bin/python -u -m src.main trade run --interval 60`,
log at `trader.log`. Restarted four times this session (each fix needed to go
live); healthy at hand-off — 500 flow events in two minutes, **0 certificate
errors**.

> Restart with `venv/bin/python`, **not** the homebrew binary. `ps` shows the
> homebrew path either way because `venv/bin/python` is a symlink — that is
> expected and is not evidence of anything. The real check is certificate errors
> in `flow_events`, which is the failure mode the morning handoff describes.

**Evolution is ON.** The morning handoff and my own early summary both implied
otherwise; `trade switches` says `evolution ON`. `TRADE_EVOLVE_EVERY=80`, so
every change below reaches real generations, not just a repo.

**Branch:** `claude/ai-village-trading-build-m4bg19`, pushed through `e0dea85`,
clean against origin. Nine commits this session (`5ce242b`..`e0dea85`).

**Database:** `data/mvv.db`, 329MB, WAL mode. Clean pre-change backup at
`data/mvv.db.before-epoch-20260914` (taken with the sqlite backup API, not `cp`
— see §6).

**Suite:** 372 pass across every file touched, run in isolation. **The full-suite
count is not a usable signal** — three runs of identical code gave 70 failed/31
errors, 65/27, and 30/13. The instability is `importlib.reload` in the
web/API tests (`test_access`, `test_trading_webhooks`, `test_deploy`,
`test_trading_api`, `test_trading_web`) and has nothing to do with trading code.
Diff failing test **names** against a true pre-work commit; never counts.

---

## 3. Files touched

### Source

| file | what |
|---|---|
| `src/trading/brain/evolver.py` | 7 new genes + `NOT_GENES`; `WINDOW_GENES`/`max_lookback_bars()`; `_split` rewritten (both leaks); `purge_bars()`; `MEASUREMENT_EPOCH`; `mark_epoch()` |
| `src/trading/backtest.py` | `benchmark_pct`/`cash_hurdle_pct`/`hurdle_note`; `hurdle_pct`, `excess_pct`; fitness now excess-based; `_hold_pct`, `_cash_pct`; `comparable_with` refuses mismatched hurdles |
| `src/trading/firms/analysts.py` | **new** `ReversionAnalyst` (seat `reversion`), registered + aliases |
| `src/trading/indicators.py` | **new** `ibs()`, `average_range()` |
| `src/trading/brokerage/evaluator.py` | `Scorecard.benchmark_pct`; `_benchmark_pct()`; score's return term is excess; `return_basis`; docstring corrected |
| `src/trading/brokerage/allocator.py` | `_basis()`; capital moves record how the score was computed |
| `src/trading/promotion.py` | `min_excess_pct`; **"Beats buy-and-hold"** criterion |
| `src/trading/shadow.py` | `_cohort_seed()`; `arms()` no longer seeds from the bar; `max_statistic_null()`; `adopt_best` gated on it |
| `src/trading/court/evidence.py` | `NOT_GENES` noted not charged; `Evidence.analysts` read from `ANALYSTS:`/`analysts:` |
| `src/trading/court/docket.py` | backtests with the submission's own analysts; stamps epoch |
| `src/trading/court/jury.py` | `return` juror argues from excess, not raw return |
| `src/trading/crowd.py` | **new** — explicit-call extraction + `shuffle_symbols` null control |
| `src/trading/config.py` | `purge_bars`, `cash_yield_pct` |
| `src/db/migrations/{,sqlite/}025_genome_epoch.sql` | **new** — `genome_epoch` side table + backfill |

### Docs / tests

`PREREG_crowd_calls.md` **new**. Tests added or amended in
`test_trading_{crowd,brain,evolution,rank_persistence,court,firms,indicators,shadow,promotion,brokerage}.py`,
`test_db.py`, `conftest.py`.

`bots/veritas_reversion.py` gained `ANALYSTS = ("reversion",)` — **gitignored**,
local only.

---

## 4. What changed, and the measurements behind it

**The reversion genes had no reader.** `rsi_entry`/`ibs_entry`/`pullback_atr`
were in the vocabulary and `grep` found them in exactly one module — the one
that defines them. VERITAS was backtested by analysts that never touched its
genome. `ReversionAnalyst` reads them as a conjunction (Connors' rule is an AND;
loosening it to an OR is the first thing a tuner tries). The court now also
reads a submission's own declared `ANALYSTS`, which it never did — so it was
scoring every submission with the default three seats regardless of genome.

**The holdout was never held out.** Two separate leaks:

```
_split computed an index and evolve passed it as `steps`;
the backtester counts steps from its 90-bar warmup.

  720 bars: fitted [90,594)  "holdout" [504,720)  -> 90 of 216 bars (42%) overlap
  180 bars: fitted [90,180)  "holdout" [126,180)  -> 100% of the holdout inside the fit
```

Every held-out number the evolver has ever reported was partly a re-read of the
bars it was selected on. Fixed, then a purge gap (derived from `WINDOW_GENES`,
150 today) added on top. Live village after: 264 fitted, 150 purged, 216 truly
held out — overlap 0, indicator leak 0.

**Fitness had no benchmark.** `return_pct − maxDD/2`. Measured, 720 synthetic
bars, SPY+QQQ, BASE_GENOME: returned **+8.05% while holding its own universe
returned +53.91%** — old fitness **+3.04 (selected for)**, new **−50.87**. Double
hurdle, max(own-universe hold, cash), from `hive_mind/lock.py:670`. The cash leg
bites independently: a BTC genome at −6.58% against a −10.91% hold beats the
market and still fails.

**The same gap was in two more places** (§5).

**The shadow desk could never conclude anything.** 517 arms over 183 bars, one
ever recurring. Cohort now pinned to the incumbent, plus a max-statistic null.

**Crowd calls** — `crowd.py` + `PREREG_crowd_calls.md`, wired to nothing.

---

## 5. What I flagged — the finding

**Six bugs, all in tested code, all tests passing.**

| bug | the test that existed | what it asked |
|---|---|---|
| holdout 100% inside the fitted window | `test_the_candidates_are_fitted_without_seeing_the_tail` | that `steps` was not None and > 0 |
| fitness scored +8% vs a +54% universe as good | `test_fitness_penalises_drawdown` | that more drawdown scores worse |
| 517 arms, 1 ever recurring | 16 shadow tests | that a put could be written, priced, settled |
| firm score → capital, no benchmark | `test_score_components_are_reported_with_the_score` | that the component keys were present |
| **promotion → live money, no benchmark** | 39 promotion tests | every criterion against *zero* |
| score components never persisted | — | the docstring asserted it; nothing checked |

**None asked "is the measurement valid". They asked "did the code run".** More
coverage does not fix that; different questions do.

**Three of the six were the same missing benchmark**, in three places, with
`benchmark.py` sitting unwired beside all of them — a module whose first
paragraph is *"A firm up 8% in a year when SPY did 12% has lost money in the way
that matters, and until now nothing here would have said so."*

The worst two:

- **Firm score → capital.** A firm returning +6% while its universe returned
  +30% scored **62, above the raise threshold of 60**. More money for
  underperforming by 24 points. A defensive desk that lost 2% while its universe
  lost 20% scored 43, a hair above the cut.
- **Promotion → live money.** Status, feed, bars, days, closed trades,
  expectancy-t, drawdown, win rate — all eight measure against zero. A firm
  could clear every one on a real feed with 50 closed trades while losing to
  buy-and-hold throughout, and be handed live capital.

**Surfaces audited and found sound** (so the next pass need not redo them):
`leaderboard` (reads the corrected score, gates on sample); `allocator` (fixed
upstream); **`kill_switch` — deliberately not benchmarked, and that is right.**
A 25% drawdown is unacceptable whether or not the market fell 30%; it is a risk
control, not a performance measure. Not every surface should be benchmarked.

**Other flags:**

- **Live effect of the score fix:** 38 of 48 firms score differently, all by
  0.6–1.6 points, **none crossing a raise or cut threshold**. Structural, not
  yet biting — the village is 11–29 bars old with 0.02–0.80% benchmark returns.
- **The village is losing to buy-and-hold by 1.26%** (−0.89% vs +0.37%); 2 of 14
  comparable firms beat it.
- **300 `strategy_genomes` rows are fenced as epoch 1** and are not comparable
  with anything written from now on. Every conclusion drawn from them — the
  ρ=+0.056 selection null included — was measured through a leaking holdout
  *and* a benchmark-free fitness, and none has been re-derived.
- **The ~645 open shadow positions** (still growing) belong to arms that cannot recur. That
  history is largely unusable for the leaderboard; comparable evidence starts
  accumulating now. Earliest settlement ~23 Sep (`EXIT_DTE=2`).
- `data/` holds **~3.2 GB of `mvv.db.before-*` and similar backups**, and there is a stray
  empty SQLite file named `db` in the repo root from 27 Aug.

---

## 6. What failed — mine

**I broke three tests in `5ce242b` and did not catch it.** Adding seven genes
shifted `mutate`'s shared RNG stream, so `fast_window` stopped being drawn at
the fixed seed, all eight candidates scored identically, and
`test_trading_evolution.py` failed with "no mutant beat the incumbent" — a
sentence about fitness for a problem entirely about mutation. **My regression
check was invalid**: I stashed the working tree but left my own commits in the
"baseline", so the baseline was already broken. Bisecting against `cc32910`
found it. Tests now run at `mutation_rate=1` with a `_varied()` guard that fails
by name.

**I took a `cp` backup of a WAL-mode database** with 4.4MB uncommitted in the
WAL — not a faithful snapshot. Caught it, discarded it, redid it with the sqlite
backup API after stopping the trader.

**I ran `trade init-db`, which does not exist** (it is `trade init`). And `trade
init` re-reads firm config, which is more side effect than a schema change
warrants on a 48-firm village — so I applied migration 025 via `init_schema()`
alone.

**I claimed "evolution is off by default"** in a way that implied it was off
here. It is ON. Corrected in-session.

**I nearly asserted five untested decision modules** from a `ls | grep` on
filenames. They are tested, in differently-named files. Checked before
asserting; the "audit untested modules" framing would have been wrong.

**I claimed a live misallocation example** (`firm_d_value_iii`, score 64.5 above
the raise threshold) and had to retract — it is +0.03% *ahead* of its benchmark.
No live instance has fired yet; the defect is structural.

**Two extractor bugs caught by my own tests before commit:** "buying DOGE and
selling PEPE" gave PEPE a *buy* (first trigger by index won, not nearest), and a
distance-based dedupe let one excited post vote three times.

**Known-unfixed:** `crowd.py` records only the *nearer* call when one ticker
mention has verbs on both sides — pinned by a test so it cannot drift silently.

---

## 7. What to do next

**1. Re-derive the evolution verdict on clean data.** This is the blocker on
everything else. "Evolution does not work (ρ=+0.056, n=280)" was measured
through a 42%-overlapping holdout *and* a benchmark-free fitness. The null very
likely survives — leakage inflates correlation — but it has not been
re-measured, and the rank test now records itself every generation with the
purge gap in its ledger note. Wait for epoch-2 generations to accumulate, then
compare **only within epoch 2**. `genome_epoch` exists to stop you doing
otherwise.

**2. Do not trust any pre-2026-09-14 number without re-deriving it.** Not the
fitness levels, not the holdout scores, not the firm scores. The apparatus was
wrong in six ways today.

**3. Keep auditing, with the §5 question.** The six were found by asking "is the
measurement valid", not by reading more code. Surfaces not yet asked:
`conscience`, `retention`, `council`, `competition/arena`, `black_market`,
`sandbox`. Expect more; three of six were the same missing benchmark.

**4. Run `PREREG_crowd_calls.md`.** Fixed in advance, four arms, 500 shuffle
seeds, five criteria, "fewer than five of five is a null". Unblocked by the
morning's cost correction (39 bps round trip, not 120).

**5. Still owed from the morning handoff:** beta-adjusted alpha; "is the loss
regime in the sample?"; per-filter-leg ablation ("does each gene earn its
place?" — the village has never asked that); `ATLAS.md`/`DECISIONS.md`
confidence ladder and recorded reasoning for adopt/kill. The max-statistic null
is now **done** (`shadow.max_statistic_null`).

**6. Blocked on Robbie:** the on-chain analyst stub needs a data source.
`OnChainAnalyst` today is a volume proxy that says so in its note; a real one
needs a node or an indexer, and that is a decision, not work.

**7. Housekeeping:** the stray `db` file; ~3.2 GB of `mvv.db.before-*` and similar backups.

**8. Reading still owed** — unchanged from the morning: 12 of 13 `PREREG_*.md`,
~70 `insider_research` files, most of 643 Python files, and all ~100 chat
transcripts. The morning handoff notes Robbie was right that the unread part is
not noise; the fleet packaging, the max-statistic null and the six-point bar all
came out of the small slice that was read.
