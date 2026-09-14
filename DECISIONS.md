# Village Decision Log

The memory of *why*, not just *what*. Adapted from `/Users/robbie/trade/ATLAS.md`
and `DECISIONS.md`, which the handoff of 2026-09-14 named as the thing the
village most obviously lacked: **it adopts and kills firms with no recorded
reasoning anywhere.**

That is not quite true of the machinery — `council_rulings` stores every juror's
finding, its weight and an `evidence_digest`, and `brokerage_events` records
kills and wind-ups. What is missing is the layer above: why a *class* of thing
was decided, at what confidence, and when to look again. A ruling says the panel
carried it 85 to 25. It does not say whether anyone should have believed the
numbers the panel was reading, which is the question this whole month has been
about.

**Entries are IMMUTABLE.** Never edit a past decision to match what was learned
later — that erases what was known at the time. New evidence gets a new entry
that supersedes the old one.

---

## The confidence ladder

| Status | Meaning | Influences research? | Influences capital? |
|---|---|---|---|
| **Observed** | one script produced it | yes | no |
| **Reproduced** | independent reruns agree | yes | no |
| **Validated** | walk-forward + robustness agree | yes | candidate only |
| **Confirmed** | live paper agrees over a meaningful period | yes | eligible to promote |

> Confidence rises only when **independent** sources agree. The village has a
> specific way of faking this: the same defect can produce agreement between a
> backtest and a live readout because both read the same broken function. Two
> numbers from one bug are one number. Agreement counts only when the paths are
> genuinely separate — which is why the evolution re-derivation below is
> Reproduced and not more.

## The freeze rule

> Once a firm enters a forward test, its genome is FROZEN.

The village violates this by construction: `TRADE_EVOLVE_EVERY=80` mutates
genomes every 20 hours whether or not anything is being evaluated. That is
defensible while nothing is promoted and everything is paper, and it stops being
defensible the moment a firm is a promotion candidate. **No firm is a candidate
today** (D-V004), so this is recorded as a known, bounded violation rather than
an incident.

## Lifecycle states

> Every firm is in exactly one explicit state. "Still running because nobody
> decided" is not a state.

| State | Meaning |
|---|---|
| Funded experiment | carries capital, trading, being measured |
| Unfunded heir | filed by a wind-up, never funded, cannot trade |
| Bankrupt | wound up, estate closed, postmortem written |
| Killed | terminal; only the court can reverse it |

As of 2026-09-14, from the ledger rather than from intent:

| Count | State |
|---|---|
| 6 | Funded experiment |
| 2 | Active but unfunded (allocation 0, cash 0) |
| 39 | Bankrupt |
| 1 | Killed |

The two *active but unfunded* firms are the state this table exists to catch:
they are neither experiments nor heirs, they are running by inertia. They are
also 2 of the 8 firms the evolution re-derivation ran on.

---

### D-V001 · Evolution's selection rule carries no out-of-sample information
- **Date:** 2026-09-14
- **Question:** Does ranking genomes by in-sample fitness predict held-out rank?
- **Evidence:** 48 cohorts (firm × generation), 384 candidates, purge gap 150
  bars, fitted/holdout overlap 0. Mean within-cohort ρ **+0.0170** (t=+0.259,
  25/48 positive); within-cohort permutation null p=0.356. Reproduction run
  +0.0146/p=0.418. Restricted to the 6 funded firms: **+0.0014** (t=+0.024,
  p=0.472). Live epoch-2 sweep, produced by a different code path: +0.1357
  (t=+0.995, 4/8 positive), not significant.
- **Decision:** Treat the selection rule as carrying no information. **Do not
  spend compute on more generations** to improve results.
- **Reason:** Three independent runs and one independent code path agree, and
  the effect is not distinguishable from a null that holds the cohort structure
  fixed. The prior number (ρ=+0.056, n=280) was measured through a 42%-
  overlapping holdout, a benchmark-free fitness, *and* a pooled statistic that
  reads between-firm differences as skill.
- **Confidence at decision:** **Reproduced.** Not Validated: one 720-bar window,
  8 firms, no walk-forward.
- **Review date:** when epoch-2 accumulates ≥40 cohorts from the live cadence
  (~4 weeks at 1.2 sweeps/day), compare *within epoch 2 only*.

---

### D-V002 · Most of the genome vocabulary is inert; do not wire it up yet
- **Date:** 2026-09-14
- **Question:** Does each gene earn its place?
- **Evidence:** 86 gene-firm ablations; **68 (79%) changed fitness by exactly
  zero**. Only `fast_window`, `slow_window`, `rsi_window`, `trend_bias` move it.
  Four genes — `max_positions`, `lookback`, `top_fraction`, `max_per_name` —
  have no reader in any trading path.
- **Decision:** Declare the four in `UNREAD_GENES` and pin them with a test.
  **Do not wire them up.**
- **Reason:** Wiring them changes what a live village does, on the same day its
  selection rule was shown not to work. Adding four working dimensions to a
  search that cannot rank is not an improvement, it is a bigger search. Deleting
  them would silently rewrite every stored genome.
- **Confidence at decision:** **Observed** for the ablation magnitudes (one
  script, one window); **Confirmed** for the four having no reader, which is a
  fact about the source and was checked by grep, not by measurement.
- **Review date:** before any future claim that evolution is working.

---

### D-V003 · The village has no alpha, and has never seen a down market
- **Date:** 2026-09-14
- **Question:** Is the underperformance a beta artefact, and is the loss regime
  in the sample?
- **Evidence:** 15-firm balanced panel, 158 bars, capital movements excluded.
  Beta **+0.3586** (t=+1.21), alpha **-0.0239%/bar** (t=-0.55), market component
  11% of return. **0 of 157 bars** saw SPY fall ≥1%; worst bar -0.98%, 6.7 sd.
  Separately: NVDA is **86.6%** of realised P&L and dropping two symbols makes
  the village net negative.
- **Decision:** "The village underperforms buy-and-hold" stands and is not a
  beta story. "The village has a drawdown profile" is **withdrawn** — untested.
  "The village has a repeating edge" is **withdrawn** — it has NVDA.
- **Reason:** A t of -0.55 on alpha is not a negative result about skill, it is
  an absence of evidence either way, which is what costs plus noise look like.
  The regime finding is not a small-sample caveat: it is zero observations.
- **Confidence at decision:** **Observed.** One window, one benchmark, and the
  measurement was wrong three times before it was right.
- **Review date:** after the first week in which SPY falls ≥1% on any bar.

---

### D-V004 · No firm is a promotion candidate
- **Date:** 2026-09-14
- **Question:** Is anything close to live money?
- **Evidence:** 48 of 48 firms NOT READY. All 48 fail closed-trades ≥50 (29 have
  zero). 47 of 48 fail expectancy t ≥ 2.0. The nearest is `firm_g_commodities`
  at 2 of 11 unmet — it passes beats-buy-and-hold (+2.65%), drawdown (2.07%) and
  win rate (41.67%) on **12 closed trades** with **t=0.89**.
- **Decision:** Nothing is promoted. `firm_g_commodities` is **not** treated as
  a near-miss and its numbers are not to be quoted as encouraging.
- **Reason:** t=0.89 on 12 trades is the criterion doing its job. This fleet's
  record is eight nulls, and each looked promising at four of five criteria.
  Commodities specifically has a recorded prior of stale bars manufacturing its
  signal.
- **Confidence at decision:** high — this is the safe default and the gates are
  pre-registered.
- **Review date:** when any firm reaches 50 closed trades on 30+ days of market.

---

### D-V005 · `PREREG_crowd_calls.md` is blocked, not answered
- **Date:** 2026-09-14
- **Question:** Can the crowd-call experiment be run?
- **Evidence:** `RedditSource` returns HTTP 403 on every subreddit;
  `old.reddit.com` returns HTML, not JSON. No stored corpus exists — `signals`
  holds no Reddit rows and the memecoin symbols carry only synthetic
  `example`-publisher data. Obtainable calls: **zero**.
- **Decision:** Record as a **blocked experiment**. Do not substitute a newswire
  for a crowd venue.
- **Reason:** "We ran it and found nothing" and "we could not run it" are
  different claims, and the second decays into the first if nobody writes it
  down. Swapping the venue after seeing the original fail is the substitution
  the prereg exists to prevent.
- **Confidence at decision:** n/a — no measurement was made. That is the point.
- **Review date:** when a venue is chosen (StockTwits and 4chan `/biz/` both
  answer) and the prereg is amended **before** any arm runs.

---

### D-V006 · Four measurement defects fixed; none had changed a decision yet
- **Date:** 2026-09-14
- **Question:** What did the audit find, and did any of it cost anything?
- **Evidence:** (1) sandbox `SandboxWriter.query` executed and committed
  arbitrary SQL through the object whose purpose is preventing that; (2) the
  council's `performance` juror voted for capital raises on raw return, having
  voted **for 30 times and against none**; (3) drawdown printed 2,304,568%;
  (4) two of three live news sources had been dead with HTTP 403.
- **Decision:** All four fixed. None is recorded as an incident.
- **Reason:** Measured, not assumed: no grant would have flipped (all 12 council
  flips were defer→refuse, and defer is the conservative state); no current firm
  changes its vote; the drawdown cap is decision-neutral at every threshold; the
  dead feeds were logged every tick and simply unread.
- **Confidence at decision:** **Confirmed** — each was verified against the live
  ledger, and the sandbox hole was demonstrated on a throwaway database rather
  than argued.
- **Review date:** none. Superseded only by a new defect in the same surface.

---

### D-V007 · The 48-firm village is 6 firms
- **Date:** 2026-09-14
- **Question:** What is the denominator under every population number?
- **Evidence:** 48 on the books; 20 have ever placed a fill; **28 never have**;
  6 are active and funded. The 39 bankrupt rows are 39 events across 13 firms,
  six of them wound up 4–5 times by the `file_successor` cascade — a bug already
  found, already fixed, and pinned by `test_trading_heir_cascade.py`.
- **Decision:** Quote "six firms" in any future claim about the village. Leave
  the 26 orphan heirs in the ledger.
- **Reason:** Deleting or re-statusing rows in a live ledger is a decision, not
  a cleanup, and the reconciler passes on all 48 today. The count is the thing
  that was misleading, not the rows.
- **Confidence at decision:** **Confirmed** — counted from the ledger.
- **Review date:** if the orphan count changes, the cascade is back.

---

### D-V008 · The conscience is not in the backtest; do not wire it in yet
- **Date:** 2026-09-14
- **Question:** Does fitness measure the strategy the village actually runs?
- **Evidence:** Two `venue.execute` call sites exist. The live one is gated by
  `heart.consider`; `backtest.py:255` is not, and a backtest never sets
  `ethics_verdict`. The risk manager *is* applied in both (it lives inside
  `firm.propose`). Measured over 57,247 proposals: 7,478 passed risk, and the
  conscience blocked **5,276 of those** — the backtester executes 7,478 where
  the live village executes 2,202. **The conscience removes 70.6% of what the
  backtest trades**, and the blocks are overwhelmingly `liberty` (order too
  large against typical daily volume).
- **Decision:** Record it. **Do not wire the conscience into the backtester.**
- **Reason:** Two reasons, and the second is the stronger. First, it would
  change every fitness number in the system and require `MEASUREMENT_EPOCH` to
  go to 3, discarding the epoch-2 data gathered today — the first clean data
  the village has ever had. Second, it should not be done on the same day the
  selection rule was shown to carry no information: making fitness more
  faithful is only worth the disruption if fitness is being used to choose
  something, and right now it demonstrably is not.
- **Confidence at decision:** **Confirmed** for the gap and its size — counted
  from the ledger, and the two call sites were enumerated rather than assumed.
  **Observed** for the claim that the bias flatters: the blocked trades are
  illiquid by construction, but their counterfactual P&L was not computed.
- **Review date:** before the next attempt to improve evolution, and before any
  firm becomes a promotion candidate. Whichever comes first.

---

### D-V009 · An on-chain seat needs a hypothesis, not an endpoint
- **Date:** 2026-09-14
- **Question:** The handoff (§7.6) records the on-chain analyst as blocked on a
  data source. Which keyless sources actually answer?
- **Evidence:** Probed and verified parsing, not merely reachable:
  `blockchair` (BTC and ETH stats, and it covers Dogecoin), `mempool.space`
  (BTC fees), `blockstream` (tip height), Solana mainnet RPC (`getEpochInfo`
  returns), `etherscan` without a key, `defillama` (467 chains). The current
  `OnChainAnalyst` is a volume-per-unit-of-move proxy whose docstring already
  says it has never seen a blockchain.
- **Decision:** Record the working sources. **Do not wire an on-chain seat.**
- **Reason:** The block was described as "needs a node or an indexer", and that
  turns out not to be the binding constraint — several indexers answer keyless.
  The binding constraint is that **no hypothesis has been stated**: which
  on-chain quantity is claimed to predict which return, over what horizon,
  against what control. The universe makes this concrete rather than abstract —
  DOGE is its own chain, SHIB and PEPE are ERC-20, WIF is SPL — so "an on-chain
  seat" is three integrations, and which three depends entirely on the
  hypothesis. Wiring one now would add a fourth unmeasured analyst to a village
  whose existing analysts have no measured edge, on the day its selection rule
  was shown to carry no information.
- **Confidence at decision:** **Confirmed** for which endpoints answer — each
  was parsed, not pinged. n/a for anything about signal, because nothing was
  measured.
- **Review date:** when a hypothesis exists and has a prereg. Not before.

---

### D-V010 · SUPERSEDES D-V001 — the evolution measurement is window-dependent and settles nothing
- **Date:** 2026-09-14 (night)
- **Question:** D-V001 concluded the selection rule carries no out-of-sample
  information. Does that survive being measured again?
- **Evidence:** the identical statistic, the same script, the same 6 funded
  firms, the same 6 generations, 36 cohorts every time — run nine times across
  one afternoon:

  | when | rho | permutation p |
  |---|---|---|
  | ~14:20 (48 cohorts, 8 firms) | +0.0170 | 0.356 |
  | ~14:25 reproduction | +0.0146 | 0.418 |
  | ~14:30 (36 cohorts, funded) | **+0.0014** | 0.472 |
  | 15:48 (two-arm, arm A) | +0.1203 | 0.024 |
  | ~16:05 | +0.1638 | 0.000 |
  | ~16:12 | +0.1647 | 0.000 |
  | ~16:19 | +0.1399 | 0.006 |
  | ~16:26 | +0.1267 | 0.018 |
  | ~17:00 (same script as 14:30) | **+0.1351** | 0.006 |

  The afternoon runs say no information. The evening runs say information,
  comfortably past their own null. **The 14:30 and 17:00 runs are the same
  script with the same arguments.**

  Mechanism: `AlpacaFeed.keep_bars = 720`. The feed keeps the most recent 720
  bars, so every new 15-minute bar pushes the oldest out and **both the fitted
  window and the holdout slide**. Each run measures a different holdout period.
  The within-batch drift is visible directly — +0.1638, +0.1647, +0.1399,
  +0.1267 across four runs 25 minutes apart is a window moving, not noise
  around a constant.
- **Decision:** **D-V001 is withdrawn.** It is *not* replaced by "the selection
  rule carries information". Both claims are unsupported. The recorded finding
  is now: **this measurement is not of a fixed quantity and cannot settle the
  question either way.**
- **Reason:** A statistic that moves from +0.0014 (p=0.472) to +0.1351 (p=0.006)
  in ninety minutes, with no code change, is not estimating a property of the
  selection rule. The permutation null cannot detect this by construction — it
  conditions on the cohorts it is handed and shuffles inside them, so it prices
  the ranking *within one window* and is silent about the window itself. It
  reported p=0.006 and p=0.472 for the same underlying question with equal
  confidence.
- **What was wrong with D-V001 specifically:** it called itself *Reproduced* on
  the strength of runs that were minutes apart and therefore shared a window.
  Reproduction across a shared nuisance parameter is not reproduction. The
  confidence ladder was applied correctly to the wrong axis.
- **Confidence at decision:** **Confirmed** that the measurement is
  window-dependent — nine runs, a known mechanism, and a within-batch drift
  consistent with it. **Nothing at all** about whether evolution works.
- **Review date:** when the measurement is pinned to a fixed bar range and
  walked forward across several windows, reporting the distribution rather than
  one draw. Until then no evolution verdict may be quoted, including the ones
  committed earlier today.
