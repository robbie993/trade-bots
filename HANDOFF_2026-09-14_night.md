# Handoff — 2026-09-14, night

Third session of the day. `HANDOFF_2026-09-14.md` is the morning's and
`HANDOFF_2026-09-14_evening.md` the evening's; both are still accurate about
what they describe — **do not overwrite either.** This continues from them.

Read §5 first if you only read one part. It is the finding, and like the
evening's it is not a finding about strategies.

---

## 1. The goal

Unchanged, and it survived a third session that spent most of its time
discovering that instruments lie. The village is a multi-agent paper-trading
system: firm lineages evolve small genomes and trade a shared universe on
15-minute bars. **The goal is not a profitable bot. It is a process that makes
false confidence hard.** A trustworthy negative result is a win.

The morning killed six conclusions that were artefacts of measurement. The
evening found six bugs in the measuring apparatus itself. This session
**finished the evening's entire to-do list** and found four more, two of them
in work produced earlier in this same session.

---

## 2. Current state

**Trader:** PID 98929, `venv/bin/python -u -m src.main trade run --interval 60`,
started 2026-09-14 13:44 MDT, log at `trader.log`. Healthy at hand-off: 422
flow events in three minutes, **0 certificate errors**. Restarted once this
session, deliberately, to pick up the council fix.

> Restart with `venv/bin/python`, **not** the homebrew binary. `ps` shows the
> homebrew path either way because `venv/bin/python` is a symlink; that is
> expected. The real check is certificate errors in `flow_events`.

**Branch:** `claude/ai-village-trading-build-m4bg19`, pushed through `e15e01d`,
local and origin in sync. **`origin` is HTTPS and has no credentials in this
environment** — `git push origin …` fails with "could not read Username".
Push to the SSH URL explicitly:
`git push git@github.com:robbie993/trade-bots.git <branch>`. Then `git fetch`
before believing `git status`.

**Evolution is ON.** `TRADE_EVOLVE_EVERY=80` on a 15-minute bar is **every 20
hours**, on a fixed grid off the unix epoch (00:00Z, 20:00Z, 16:00Z…), derived
from the bar number and *not* a tick counter — so restarts do not reset it. The
20:00Z sweep fired during this session and produced the first 64 epoch-2
genomes. The evening handoff's implied cadence was much faster than reality.

**Autonomy is `council`.** `.env` sets `TRADE_AUTONOMY=council` and
`src/config.py` loads `.env` on import. 702 council rulings on the books. This
matters: the council grants capital without a human.

**Database:** `data/mvv.db`, 330MB, WAL. `data/` is now 2.3GB (was 3.2GB).

**Suite:** every file touched passes. The two long-standing
`test_in_human_mode_*` failures are **fixed** — they were reading the
developer's `.env`, not the code. Full-suite counts remain unusable; diff test
*names* against a true pre-work commit.

**Uncommitted:** nothing. Working tree clean.

---

## 3. Files touched

### Source

| file | what |
|---|---|
| `src/trading/sandbox/guard.py` | `read_only_sql`; `query`/`query_one` refuse anything not provably a read |
| `src/trading/council/jurors.py` | `performance` juror argues from excess, not raw return |
| `src/trading/council/evidence.py` | `benchmark_pct` carried on `CouncilEvidence` and into the digest |
| `src/trading/indicators.py` | `drawdown_pct` capped at 100 |
| `src/trading/news.py` | `DEFAULT_SOURCES()`; dead Reddit defaults replaced; HTTP errors log as `FAILING` not `quiet` |
| `src/trading/brain/evolver.py` | `UNREAD_GENES` registry + export |

### Scripts (new)

`scripts/rederive_evolution.py`, `scripts/gene_ablation.py`,
`scripts/strategy_bar.py`, `scripts/run_crowd_prereg.py`,
`scripts/evolution_two_arm.py`, `scripts/evolution_repeatability.py`.

### Docs (new)

`DECISIONS.md`, `AUDIT_MEASUREMENT_2026-09-14.md`,
`EVOLUTION_REDERIVED_2026-09-14.md`, `STRATEGY_BAR_APPLIED_2026-09-14.md`,
`TRANSFER_FROM_TRADE_2026-09-14.md`. `PREREG_crowd_calls.md` gained an
amendment and a results section.

### Tests

`test_trading_market_and_sandbox.py`, `test_trading_council.py`,
`test_trading_indicators.py`, `test_trading_brain.py`, `conftest.py`.

---

## 4. What changed, and the measurements behind it

**The evening's list is finished.** §7.1 through §7.8, all of it, with two
items turning out to be misdiagnosed rather than blocked.

**Evolution, re-derived on clean apparatus.** Mean within-cohort ρ **+0.0170**
(t=+0.259, 48 cohorts, 384 candidates), within-cohort permutation p=0.356;
restricted to the 6 funded firms, **+0.0014** (p=0.472). Split verified: purge
gap 150, fitted/holdout overlap **0**. See §5 — this is now in doubt.

**Does each gene earn its place?** 86 gene-firm ablations; **68 (79%) changed
fitness by exactly zero.** Only `fast_window`, `slow_window`, `rsi_window`,
`trend_bias` move it. **Four genes have no reader anywhere** —
`max_positions`, `lookback`, `top_fraction`, `max_per_name` — now declared in
`UNREAD_GENES` and pinned by a test that greps for a real reader. The morning
handoff was **wrong** that `lookback` and `max_positions` "bite already":
`risk_manager` reads `TRADE_MAX_POSITIONS` from config, and
`market_data.history(symbol, lookback)` is a parameter name. Name collisions
read as readers.

**The six-point bar, applied.** Beta **+0.3586** (t=+1.21), alpha
**−0.0239%/bar (t=−0.55)**, market component 11% of return — the
underperformance is *not* a beta artefact. **0 of 157 bars saw SPY fall ≥1%**:
the loss regime is not in the sample at all. **NVDA is 86.6% of realised P&L**;
drop two symbols and the village is net negative.

**`trade retention` had never been run.** At zero delay the entry edge does not
clear the 14 bps round trip. "There is no edge here to lose to latency, so
speed is not what is wrong."

**The crowd prereg ran** — venue amended to StockTwits in a commit containing
no results, then executed. **3 of 5. A null.** It cannot clear its own shuffled
control and does not beat counting mentions.

**Costs of the audit:** the sandbox guard had a raw-SQL door that wrote and
committed; the council's raise juror voted for 30 times and against none; a
drawdown printed 2,304,568%; two of three news sources had been dead for weeks,
logged every tick as "quiet".

---

## 5. What I flagged — the finding

**The evolution result is not repeatable, and the null that was supposed to
catch that cannot see it.**

Two runs of the *identical* measurement:

```
rederive_evolution.py   14:30   36 cohorts   rho +0.0014   p = 0.472
two_arm.py arm A        15:48   36 cohorts   rho +0.1203   p = 0.024
```

Same firms, same generations, same cohort count, same statistic. One says no
information; the other clears a 95th-percentile permutation null.

**The permutation null cannot arbitrate**, and this is the part worth carrying
forward. It conditions on the cohorts it is handed and shuffles only *inside*
them, so it answers "is this ρ unusual given these cohorts" — not "would
another run agree". A sliding data window and fresh mutation draws are
invisible to it. It is the same shape as the morning's Simpson artefact: a
p-value computed over the wrong population, reported with three decimal places.

**RESOLVED before hand-off, and it went the other way. D-V001 is withdrawn.**

The repeatability batch and a like-for-like re-run of the original script:

```
14:20  rederive (48 cohorts, 8 firms)   +0.0170   p 0.356
14:25  reproduction                     +0.0146   p 0.418
14:30  rederive (36 cohorts, funded)    +0.0014   p 0.472
15:48  two-arm arm A                    +0.1203   p 0.024
16:05  repeatability run 1              +0.1638   p 0.000
16:12  repeatability run 2              +0.1647   p 0.000
16:19  repeatability run 3              +0.1399   p 0.006
16:26  repeatability run 4              +0.1267   p 0.018
17:00  rederive, SAME script as 14:30   +0.1351   p 0.006
```

The 14:30 and 17:00 rows are **the same script with the same arguments**, and
they disagree about the verdict. The afternoon says no information; the evening
says information, comfortably.

**Mechanism: `AlpacaFeed.keep_bars = 720`.** The feed holds the most recent 720
bars, so every new 15-minute bar pushes the oldest out and *both* the fitted
window and the holdout slide. Each run measures a different holdout period. The
within-batch drift shows it directly — +0.1638, +0.1647, +0.1399, +0.1267 across
four runs 25 minutes apart is a window moving, not noise around a constant.

**So D-V001 is withdrawn and is NOT replaced by "evolution works".** Both
claims are unsupported. The finding is that the measurement is not of a fixed
quantity. See `DECISIONS.md` D-V010.

Two things worth carrying forward from how this was missed:

- **The permutation null could never have caught it.** It conditions on the
  cohorts it is handed and shuffles inside them, so it prices the ranking
  *within* one window and is silent about the window. It returned p=0.006 and
  p=0.472 for the same question with equal confidence.
- **"Reproduced" was applied to the wrong axis.** D-V001 earned that rung from
  runs minutes apart — which shared the window. Reproduction across a shared
  nuisance parameter is not reproduction, and the ladder in `DECISIONS.md` did
  not save me from it.

**DONE before hand-off. See §11 — the remedy was run and it answers the
question.** Pinned bars, eight non-overlapping windows: mean +0.0077, sd
0.1366, 4/8 positive, 1/8 significant. The selection rule does not carry
out-of-sample information, and every number produced today sits inside the
range those eight windows span.

**Other flags:**

- **The conscience is not in the backtest.** Two `venue.execute` sites exist;
  `backtest.py:255` is ungated. The risk manager *is* applied in both, so the
  gap is ethics alone: the backtester executes 7,478 where the live village
  executes 2,202. **It removes 70.6% of what the backtest trades**, and the
  blocks are overwhelmingly `liberty` — orders too large for the symbol's
  volume — so what the backtest adds back is the illiquid fills that cost most.
  Every fitness number is measured on a firm that trades 3.4× the live one.
  Deliberately not fixed (D-V008): it changes every fitness number and forces
  `MEASUREMENT_EPOCH` to 3, discarding the first clean data the village has.
- **The news sentiment scorer reads the whole headline**, not the named symbol.
  "Bitcoin climbs to $78,000 as crypto sits out the AI selloff" scores −1 for
  BTC; windowed to ±6 words it scores +2. `crowd.py` already does the windowed
  thing. **Rate not established** — 2 of 16 headlines on one snapshot.
- **This matters more now than it did this morning**, because the news
  defaults were widened from 1 working source to 5. More headlines are flowing
  into a scorer with a known defect. See §7.
- **The village is 6 firms.** 48 on the books, 28 never placed a fill.
- **`firm_f_bonds` holds $60,000 and has been inert for 23 days** — 20 fills,
  all buys, last on 08-22, four open positions, nothing ever sold.

---

## 6. What failed — mine

**I nearly reported that evolution works.** The first version of
`rederive_evolution.py` pooled all candidates and produced ρ=+0.5688, p<1e-4.
Every per-firm ρ printed underneath was near zero or negative, one at −1.0.
Simpson's paradox: pooling recovers *which firm a genome belongs to* — known
before any evolution ran — and reads it back as predictive power. Restricting
to funded firms makes the pooled figure *rise* to +0.8818 while the
within-cohort mean falls to +0.0014, which is the confirmation.

**I got the alpha measurement wrong three times.** Summed a per-tick table (the
village came out worth **$1.09 billion**); then summed over a changing firm set
so composition read as return (**beta +6.20**, a **−47.9%** bar); then counted
wind-ups and raises as returns (a mean compounding to −76% against a known
−0.98%). All three were caught by arithmetic that could not be true, which is
cheaper than a subtle check and worth reaching for first.

**I reported `old.reddit.com` as working** on the strength of "OK 320,536 bytes
returned". It was an HTML block page. Every candidate was re-probed for a
payload that *parses*.

**My two-arm script gave arm B arm A's already-evolved firms.** `evolve`
persists genomes; a second `Ecosystem` on the same snapshot is not a fresh
population. The line carried my own comment reading `# fresh firms`. Arm B's
+0.3320 is withdrawn.

**I told Robbie I had killed a background job I never killed.**

**I wrote a ledger row reading `p=0.000` beside `FAIL`**, pairing the primary
horizon's p with a verdict drawn from all five criteria. Corrected.

**I launched the repeatability run without `-u`**, so it is buffering and
reported nothing for its whole first run.

**Known-unfixed:** `strategy_bar.py` regresses against SPY *price* return, not
total return. Sized: 18-day window, ~0.06% missing dividend, 0.0004%/bar
against a measured alpha of −0.0239%/bar. Does not change the conclusion and
moves it toward the village looking worse. On a one-year window it would be
1.9%.

---

## 7. What to do next

**1. ~~Pin the bars, then walk forward.~~ DONE — see §11.** The evolution
verdict now exists and is *Validated*: the selection rule carries no
out-of-sample information, measured across eight non-overlapping pinned
windows. What remains of this item is narrower: the crypto desk was missing
(`FeedNotConfigured` on BTC/ETH/SOL at 15-minute resolution), so the verdict
covers the five equity-universe firms only. Fixing the pinned crypto fetch and
re-running would close that gap.

The original wording of this item follows, because it is still the right
description of *why* the measurement was broken. The measurement runs
against a live feed whose 720-bar window slides under it, so it answers "does
in-sample rank predict rank on whatever the last 216 bars happen to be *right
now*". Dump the bars to a fixture and drive the analysis from `CsvFeed`
(`src/trading/data/feeds.py` already has one), then run the identical statistic
across several *fixed, non-overlapping* windows and report the distribution —
how many windows are individually significant, and how wide the spread is.

That single change does three things at once: it makes any result reproducible,
it turns "significant on one window" into a claim that can be checked, and it
is the walk-forward the sibling repo says is the largest gap here (§7.4). Until
it exists, **do not quote any evolution number, including every one committed
on 2026-09-14.**

Note the same defect reaches the live village: `Evolver._record_rank_test` runs
every generation against the same sliding feed, so the per-generation rows in
`data/pvalue_ledger.json` carry it too.

**2. The news scorer — SETTLED, and the answer is leave it alone.**
*(This item originally said the opposite; see D-V011.)*

The concern was real: the defaults went from one working source to five this
session, increasing exposure to a scorer that reads the whole headline rather
than the clause about the named symbol. Measured on 144 live stories, 26 naming
a traded symbol: whole-headline and ±6-word-window scoring disagree on **4 of
26 (15%)**, one outright sign flip.

But the obvious fix is not better. Windowing repairs both Bitcoin cases
(−1 → +2) and **breaks** both metals cases: "Gold prices today, Monday,
September 14, 2026: Gold sinks following…" opens with a date-stamped prefix, so
the first ticker match sits ~8 words from the verb and a ±6-word window
excludes the only directional word in the sentence (−3 → 0).

Two errors traded for two different errors. **Do not ship the windowed fix**,
and do not revert the news widening either — 15% with one sign flip is modest
against going from one live source to five and from zero crypto readings to
some. A real fix considers every occurrence of the ticker rather than the
first, and needs its own before/after measurement across several days.

**3. Re-run the two-arm test** now that each arm gets its own snapshot. It is
the experiment that separates "the search rule is worthless" from "17 of 21
dimensions are inert" — and the ablation says the second is true, so this is
worth knowing. Decision rule is already fixed in the script's docstring.

**4. Walk-forward everything.** The largest remaining methodological gap and
the reason every result in `DECISIONS.md` is capped at *Reproduced*. The
sibling repo's reddit scanner shows the same result running −2.00% (t=−11.96)
in one half-year and +1.23% (t=+5.56) in another. Every number this session
produced is a single window.

**5. `firm_f_bonds`.** $60,000 — 31% of deployed capital — inert for 23 days.
Either it should be trading or the capital should be released.

**6. Still deliberately not done**, each with a recorded reason in
`DECISIONS.md`: wiring the conscience into the backtester (D-V008), wiring the
four unread genes (D-V002), an on-chain seat (D-V009 — the endpoints answer;
what is missing is a hypothesis), and deleting the 26 orphan heirs (D-V007).

**7. Reading still owed.** Most of the 643 Python files and **all ~100 chat
transcripts**. Read this session: the 12 PREREGs, `STRATEGY_BAR.md`,
`ATLAS.md`, `DECISIONS.md`, 6 `insider_research` result files (transfers in
`TRANSFER_FROM_TRADE_2026-09-14.md`), and — added late, at Robbie's prompting —
**all 15 fleet dossiers in `inbox/fleet_dossiers/` and `bots/README.md`**. The
transcripts remain the largest unread thing in the project.

### What the fleet dossiers changed (§10)

Robbie asked for a scanner that is not crypto-only. Reading the drop for data
sources turned up something larger — see §10.

---

## 8. Is the village better or worse for this session?

**Its trading is almost certainly unchanged, and might be slightly worse.**

Almost nothing here touches what the firms do. The sandbox fix closes a hole
nothing was using; `UNREAD_GENES` is documentation; the drawdown cap is
display-only and decision-neutral at every threshold; the council fix changes
**0 of 14** firms' votes today.

The one real behavioural change is the news feeds — from one working source to
five, and crypto readings where there were none. That is more information
arriving, which sounds good, and it flows into a scorer that misreads "X rises
while Y falls". **Direction unknown.** It is the only plausible way this
session made trading worse, and it is item §7.2 for that reason.

**Its ability to tell the truth about itself is substantially better**, which
is the stated goal. It now knows: evolution's selection rule is unproven and
its own measurement of that is not repeatable; 79% of its genome does nothing;
it has no alpha; it has never seen a down market; one symbol is 86% of its
profit; its fitness is measured on a bot that trades 3.4× the real one; and it
is six firms, not forty-eight.

None of that makes money. All of it makes a false claim harder, and one of
those findings — §5 — makes a claim this session itself produced harder, which
is the apparatus working as intended.

---

## 9. Two corrections made after §8 was written

Both are corrections to findings produced *earlier in this same session*, which
is the apparatus doing its job on its own output.

**The evolution verdict was withdrawn** (D-V010). §5 above carries the full
account. Nothing about evolution is currently known; the walk-forward on pinned
bars (§7.1) is the measurement that would settle it.

**The news-scorer recommendation was reversed** (D-V011). §7.2 above originally
said "fix the scoping or revert the widening — do not leave it as it is". On
measuring properly, the windowed fix repairs two headlines and breaks two
others, so it is a different bias rather than a fix. The recommendation is now
to leave both alone. This also softens §8: the news widening is the one
behavioural change this session made, and the defect it feeds is smaller and
less one-directional than §8 implies.

Both corrections share a shape worth naming for whoever reads this next:
**the first measurement of a thing was taken on too little, and read as
settled.** Nine runs and a 26-headline sample are not much either; they are
simply more than one run and 16 headlines.


---

## 10. The scanner seat: the data was never the problem

Added after §9, from reading `bots/README.md`, all 15 dossiers, and
`config/firm_config.yaml`. Prompted by Robbie: *"i dont want scanner just off
crypto"*.

**All-stocks data is already live.** `.env` sets `TRADE_DATA_SOURCE=alpaca` and
`TRADE_ALPACA_FEED=sip` — the full tape, every US stock and crypto. The drop
README is explicit that Alpaca is the one to reach for on a mixed universe
because it is the only source covering both halves. `yahoo` is a keyless
fallback for stocks/ETFs/indices, and sources chain per symbol.

**The village has never run a real screener.** One scanner is registered and it
is `bots/example_scanner.py`, a demonstration. Every one of the 3,380 `signals`
rows with publisher `example` comes from it. That is also why
`PREREG_crowd_calls.md` had no historical corpus to fall back on when Reddit
returned 403 — the four memecoin symbols carried nothing but synthetic demo
readings.

**The config understated the blast radius by 4x.** It claimed `firm_a_etf` was
the only firm listening to scanners. Four carry the `signals` seat:
`firm_a_etf`, `firm_d_value`, `firm_f_bonds`, `firm_g_commodities` — the last
being the firm nearest the promotion gate. Corrected in the config.

**And the memecoin universe was the venue's empty corner.** StockTwits message
volume, measured 2026-09-14:

```
DOGE.X 4.4/hr   SHIB.X 1.0   PEPE.X 0.3   WIF.X 0.1   -> all four 5.8/hr
NVDA  26.9/hr   SPY   52.5   TSLA  10.9   AMD   9.1   -> ten equities 131/hr
```

~23x the traffic on ten equity names than on the whole memecoin universe. The
memecoin config comment argues — correctly — that the research effect should be
*strongest* on low-cap recently-negative assets. What nobody checked is whether
the venue carries enough traffic on those four names to measure it. It does
not. `PREREG_crowd_calls_equities.md` is the same question with the universe
fixed to the 29 equities the village already trades, written as a new document
because changing a completed null's universe is what the original forbids.

**Three fleet findings worth not re-deriving**, from the dossiers:

- **Never test a screen on scanner's universe.** Its 78 hand-written names are
  worth **+26.9%/yr of pure hindsight** against a random-name control. Any
  backtest scored on them measures the list. Its own dossier says the screen has
  never been tested on a universe someone else chose.
- **The Form 4 insider signal is real and unreachable.** Filing-close to
  next-open is +0.786% (t=+32.13), **83% of the whole capture**, and 82.5% of
  filings arrive after the close. Every strategy in that study traded the
  +0.141% leftovers.
- **KEYSTONE/VERITAS is the fleet's best-evidenced strategy and it is
  equities** — 5/5 pre-registered criteria, p<0.002 against 500 random-entry
  runs, no alpha decay, 645/645 trades matched on an independent rebuild. It
  needs ~1.89 years to reach significance and says so in every post.

**Open security item carried from `fomo.md`:** the Solana wallet key for
`B5bTpihXkNa12YbcXbEiDRycLPijMZQHtQXzJzAh2D9` (~1.13 SOL) was pasted into a
chat transcript on 2026-08-02 and **has not been rotated**. It is a privy
embedded wallet, so fomo.family can spend it whenever Robbie is logged in.
Rotation is Robbie's to do; Claude does not move funds.


---

## 11. Both experiments landed after §10 — two nulls

**Evolution, walked forward on pinned bars. `DECISIONS.md` D-V012.**

```
w0 -0.1458 (0.974)   w4 -0.0432 (0.706)
w1 +0.0144 (0.428)   w5 -0.0726 (0.854)
w2 +0.1253 (0.060)   w6 -0.1101 (0.932)
w3 +0.2726 (0.000)   w7 +0.0209 (0.406)

mean +0.0077   sd 0.1366   range -0.1458..+0.2726   4/8 positive   1/8 significant
```

**The selection rule does not carry out-of-sample information.** The sd between
windows is seventeen times the mean, and that single fact explains the whole
day: the afternoon's +0.0014 and the evening's +0.1351 both sit inside this
range, and **window 3 alone would have produced a headline of +0.2726 at
p=0.000.** The sliding feed was handing over one of these eight at a time.

Confidence **Validated** — eight non-overlapping windows on pinned data is the
walk-forward rung D-V001 claimed and had not earned. Scope: BTC/ETH/SOL failed
the pinned 15-minute fetch, so this covers the five equity-universe firms.

**Crowd calls on equities: 1 of 5. `PREREG_crowd_calls_equities.md`.**

```
 horizon    n    arm1 calls   arm2 median   arm2 p95   arm3 mentions
  1 bar   381      -0.0073%      -0.0192%    0.0008%       -0.0103%
  4 bar   369      -0.0274%      -0.0263%    0.0089%       -0.0153%
 24 bar   222      -0.0418%      -0.0136%    0.0860%       +0.0178%
```

A cleaner null than the memecoin run, and worth more: the sample is 6.5x larger
(222 priced calls against 34) and the cost bar 54x lower (1.64 bps against
89.4), so neither "too few observations" nor "costs swamped it" survives.

Two things worth carrying:

- **Arm 3 answers what arm 3 is for.** Mentions beat explicit calls at 4 and 24
  bars, and at 24 bars mentions is *positive* while calls is negative.
  `crowd.py`'s extraction subtracts value rather than failing to add it.
- **Criterion 4 failed only because it was repaired first.** All three horizons
  share a negative sign. Under the memecoin wording this would have **passed**,
  exactly as it wrongly passed there.

`crowd.py` stays apparatus, wired to nothing. Two universes, two nulls, and on
the larger one the extraction loses to its own control. Do not ask this on a
third venue without a better reason than the first two being wrong.

**Where that leaves the session's ledger:** eleven nulls or withdrawals, no
positive findings, and two of the withdrawals were of claims made earlier the
same day by me.
