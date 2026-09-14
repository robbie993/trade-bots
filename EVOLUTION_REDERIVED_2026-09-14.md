# Evolution, re-derived on a clean apparatus — 2026-09-14

Closes item §7.1 of `HANDOFF_2026-09-14_evening.md`, which was the stated
blocker on everything else.

**The null survives. The selection rule carries no out-of-sample information.**

---

## 1. What was wrong with the old number

"Evolution does not work — Spearman +0.056, n=280" was measured through two
defects found later the same day:

- the holdout overlapped its fitted window by **42%** (`_split` handed an index
  to a backtester that counts from its 90-bar warmup), and
- fitness had **no benchmark**, so a firm returning +8% while its own universe
  returned +54% scored positively.

Both are fixed. The handoff's expectation was that the null would survive,
because leakage inflates correlation and the old figure was therefore if
anything overstated. It does survive — but not for quite the reason expected,
and the re-derivation turned up a third defect in the measurement itself.

## 2. The split is now actually clean

Refused to report any correlation until this was true:

```
purge gap          : 150 bars  (max lookback in the vocabulary)
backtester warmup  : 90
fitted window      : [90, 354)    264 bars
holdout            : [504, 720)   216 bars
OVERLAP            : 0 bars
gap actually left  : 150 bars
```

## 3. The result

48 cohorts — one cohort is one firm's one generation, the eight candidates the
selection rule actually chooses between — across the 8 non-killed firms, 6
generations each, 384 scored candidates. Run against a snapshot, never the live
village.

```
mean within-cohort rho   : +0.0170        (reproduction: +0.0146)
sd 0.4547  se 0.0656  t  : +0.259         (reproduction: +0.218)
cohorts with rho > 0     : 25/48          (reproduction: 23/48)
within-cohort shuffle null, 500 draws:
  null mean              : -0.0004
  5th / 95th pct         : -0.0797 / +0.0839
  at or above observed   : 178/500  ->  empirical p = 0.356   (repro: 0.418)
```

Two runs of the same script differ because the evolver's mutation draws are
random; the verdict does not move. 25 of 48 cohorts positive is a coin flip.

## 4. The third defect: the pooled statistic lied

The first version of this script pooled all candidates and reported

```
rho = +0.5688   p < 1e-4   n = 64
```

while **every per-firm rho was near zero or negative, one of them -1.0.** That
gap is Simpson's paradox. Candidates belonging to a well-capitalised firm score
high on both windows and candidates belonging to a poor one score low on both,
so the pooled statistic recovers *which firm a genome belongs to* — a fact
known before any evolution happened — and reports it as predictive power. At
384 candidates the pooled figure is +0.5972, p < 1e-4, and it is still an
artefact.

The selection rule never chooses between firms. It chooses the best of eight
mutants inside one firm's generation. **The cohort is the unit**, and the null
has to permute within it: shuffling the whole pool would destroy the
between-firm structure too and so would test a claim nobody is making.

Scope of the defect, stated precisely:

- The evolver's own standing readout, `Evolver._record_rank_test`, calls
  `_spearman_of(candidates)` on **one generation's candidates**. It is
  within-cohort already and is **not affected**.
- The retrospective that produced **n=280 / rho=+0.056 was pooled** across
  firms and generations. It carries this defect on top of the leaking holdout
  and the benchmark-free fitness. Since pooling inflates here (+0.60 pooled
  against +0.02 within-cohort), the within-cohort value behind that number is
  lower than +0.056, very likely negative. Every correction found so far points
  the same way.

## 5. The live village agrees, independently

The 20:00Z sweep fired while this was being written — the first generation to
run on the fixed apparatus — and wrote **64 epoch-2 genomes**, 8 cohorts of 8.
That is live data, not the snapshot, and it was produced by the evolver's own
per-generation readout rather than by this script:

```
firm 21 gen 4  rho = +0.9009      firm 45 gen 4  rho = +0.3094
firm 48 gen 3  rho = +0.3514      firm  7 gen 5  rho = +0.1190
firm  6 gen 5  rho = +0.0000      firm 15 gen 5  rho = -0.1078
firm 47 gen 3  rho = -0.1622      firm 46 gen 4  rho = -0.3250

mean within-cohort rho +0.1357   sd 0.3857   se 0.1364   t = +0.995
positive cohorts 4/8
```

Not significant, and consistent with the snapshot result. **The one cohort the
p-value ledger recorded as `PASS` is the +0.9009**, and it is the reason the
ledger counts looks: it is one arm out of eight tested on the same day, and
dropping it takes the mean to +0.0264 (t=+0.280). Eight looks produce a best-of-
eight; this is what that looks like, not a firm that has learned something.

Eight cohorts cannot settle anything on their own — the snapshot run's 48 are
the load-bearing measurement. They are reported together because they were
produced by different code paths and agree.

## 6. What this does and does not license

It licenses: not spending more compute on more generations. Item §7.5 of the
morning handoff — *"stop evolving harder"* — now rests on a measurement that is
clean rather than one that was wrong in three ways.

It does not license: a claim about *these strategies*. The test says the
ranking does not predict, not that the genomes are bad. And it is 8 firms on
one 720-bar window; the honest reading is "no evidence of a working selection
rule here", not "evolution cannot work".

Counted in `data/pvalue_ledger.json` under `evolver:selection_rule` — the look
is recorded whether or not the answer flattered the run.

## 7. Reproducing it

```bash
EVO_SCRATCH=/tmp/evo EVO_GENERATIONS=6 python scripts/rederive_evolution.py
```

Take the snapshot with the sqlite backup API first, not `cp` — the database is
in WAL mode and `cp` is not a consistent copy of one. The script's docstring
carries the one-liner.
