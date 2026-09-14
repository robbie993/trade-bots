# What the sibling repo knows that the village does not — 2026-09-14

Handoff §7.8, the reading owed. The morning handoff recorded that Robbie pushed
back on the claim that the unread corpus was noise, **and was right** — the
fleet packaging, the max-statistic null and the six-point bar all came out of
the small slice that had been read.

## What was actually read, and what was not

Read this session: all 12 unread `PREREG_*.md` (methodology, criteria and kill
sections), `STRATEGY_BAR.md` in full, `ATLAS.md`, `DECISIONS.md`, and 6 of the
`insider_research` result files including `results_reddit_scanner.txt`, which
the handoff named specifically.

**Not read, and not claimed:** most of the 643 Python files, and all ~100 chat
transcripts. Those cannot be read to any standard in a session, and reporting
§7.8 as "done" would be the kind of claim this project exists to stop. What
follows is from the part that was read.

---

## 1. Report the difference, not the level

The strongest single habit in the corpus, and the village does the opposite.

```
REDDIT SCANNER, 5-day hold
  SCREEN PASSES            n=26,842   mean -0.27%   t=-5.95
  control (liquid, failed) n=180,573  mean -0.30%   t=-19.89
  DIFFERENCE                          +0.03%        t=+0.67
```

A screen returning −0.27% with t=−5.95 looks like a strong negative edge. Against
a control experiencing the same market it is **+0.03%, t=+0.67** — nothing. The
level measured the market; only the difference measured the screen.

**The village reports levels everywhere.** `trade benchmark` is the exception
and it is recent. Fitness became excess-based only on 2026-09-14.

## 2. Walk-forward, and expect the sign to flip

The same file, on its one significant result:

```
2023H1  mean -0.82%  t=-2.43        2024H2  mean +0.61%  t=+3.60
2023H2  mean -1.55%  t=-8.33        2025H1  mean -2.13%  t=-6.90
2024H1  mean -2.00%  t=-11.96       2025H2  mean +1.23%  t=+5.56
                                    2026H1  mean +0.32%  t=+1.10
```

Signs flip, and two windows are significant in *opposite* directions.

**This is the village's largest remaining methodological gap.** Every number
produced today — the evolution null, the alpha, the gene ablation — is a
**single window**. The confidence ladder in `DECISIONS.md` caps them at
*Reproduced* for exactly this reason: Validated requires walk-forward, and the
village has never run one.

## 3. "If it cannot beat its own shuffled self, stop"

`PREREG_kronos.md` K2, listed as a kill criterion rather than a pass criterion:

> K2 fails — indistinguishable from its own shuffled signal. **Stop
> immediately, don't renegotiate.**

The village now does this in two places — `shadow.max_statistic_null` and the
within-cohort permutation in `scripts/rederive_evolution.py`. Both arrived on
2026-09-14. Nothing else in the village has a shuffle control.

## 4. A correctness experiment must not report P&L

`PREREG_veritas_v2.md`, Experiment A:

> **Explicitly NOT a criterion: P&L.** Experiment A is agnostic to whether the
> four trades made money. Reporting A's P&L as evidence of edge is forbidden by
> this document.

A is asking whether live execution matches the backtest — a *correctness*
question. Letting a correctness experiment report P&L invites the answer "it
works" when the finding was "it does what it says".

The village has no such separation. `fitness` is simultaneously how it checks
that a genome functions and how it decides which genome is better.

## 5. Two arms, so that a null can be read

`PREREG_kronos.md` runs a fleet-shaped arm and a breadth-shaped arm because:

> A null here is ambiguous — it may mean the model is useless, or that our few-
> position shape cannot express a cross-sectional ranking edge.

**The evolution null is single-armed and has this exact ambiguity.** "The
selection rule carries no information" may mean the search is worthless, or that
79% of the genes it searches are inert (which the ablation says they are). Those
are different findings and today's design cannot separate them. A second arm —
evolving *only* the four genes that move fitness — would.

That is the single most valuable experiment the village could run next, and it
is cheap.

## 6. Total return, not price return

`PREREG_kronos.md` C3:

> SPY **total return** (not price return — that inflates by ~1.9%/yr)

**This indicts today's own alpha measurement.** `scripts/strategy_bar.py`
regresses against SPY *close prices*, which exclude dividends, so the benchmark
is understated and alpha is correspondingly flattered.

Sized rather than waved at: the window is 18 days, so at roughly 1.2%/yr the
missing dividend is about 0.06% over the window, or 0.0004% per bar against a
measured alpha of −0.0239% per bar. **It does not change the conclusion, and it
moves it in the direction of making the village look worse, not better.**
Recorded because a bias that happens to be small this time is still a bias, and
on a one-year window it would be 1.9%.

---

## What to take, in order

1. **Walk-forward every result.** The gap that caps everything at *Reproduced*.
2. **Add the second evolution arm** — evolve only the four live genes. It
   separates "search is worthless" from "search space is mostly inert", which
   today's null cannot.
3. **Difference, not level**, wherever a number is quoted.
4. **Fix `strategy_bar.py` to use total return** before it is run on a window
   long enough for it to matter.
