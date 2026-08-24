# Handoff

Written for whoever picks this up next — a person or another agent. It assumes
you have read nothing else. `TRADING.md` is the reference manual; this is the
state of play.

---

## 1. Goal

**Beat SPY.**

Not "make money" — beat the index, net of costs, on a comparison that cannot
flatter itself. A firm up 8% in a year that SPY spent up 12% has lost money in
the way that matters, and until recently nothing in this repository would have
said so; it would have printed a positive return, a healthy score, and proposed
that firm a raise.

Everything else — the firms, the council, the evolution, the court — is
machinery in service of that one number. `trade benchmark` is where it is
answered.

The standing principle the whole system is built on:

> **The system may always stop the bleeding, and may never start it.**

Cutting an allocation, pausing a firm, halting everything: automatic. Raising an
allocation, killing a firm for good, touching real money: a human decides, every
time, with no exceptions and no quorum that can override it.

---

## 2. Current state

**Branch:** `claude/ai-village-trading-build-m4bg19`, **22 commits ahead** of the
default branch `claude/mvv-phase-1-spec-ib4eui` (which is itself 6 ahead of us).
**No PR is open.** 53 files changed, +7,101 / −87.

> *Corrected 2026-08-15.* The previous line said 45 commits and +16,500 lines.
> Measured: `git rev-list --left-right --count` says 6/22, and
> `git diff --shortstat` says 53 files. Nothing turns on the number; it is
> corrected because a handoff that is loose about the checkable things earns
> no trust on the things you cannot check.

**Tests:** 1347 passing, 2 skipped **in the container**. On the operator's
machine the same commit gives **63 failed, 27 errors** — a broken suite, not a
broken branch. See the end of §4a before drawing any conclusion from a suite
run. `ruff check src/` clean (ruff is not installed on the operator's machine,
so this is a container-only claim). Six pre-existing
lint warnings remain in `tests/` (`test_cli.py` E702 ×3, `test_trading_api.py`
F401, `test_trading_living.py` E741 ×2) — untouched, unrelated to this work.

**Money:** all 9 firms are `venue=paper`. **Nothing has ever touched real
money.** `trade live-status --summary` reports 0 of 9 firms meeting the
promotion criteria, which is the expected answer for a village this young.

> **⚠ Read this before you believe any number in this section.**
>
> Everything below was measured **in a container, against that container's own
> `data/mvv.db`**. `data/*.db` is gitignored, so that ledger never left the
> container and the one on the operator's machine is a **different database
> with different contents**. The original text warned that the *feed* differed.
> That was too weak. On the operator's machine, on the same day:
>
> | | this section | the real ledger |
> |---|---|---|
> | firms | 7 active, 2 paused | **8 active, 1 killed** |
> | `firm_a_etf` | cut to 25.4%, restored to $90,000 | **$100,000, never cut** |
> | capital | $620,000 | **$595,472.53** |
>
> The thirteen cuts, the $64,581 restore and the cadence guard all happened to
> a ledger that does not exist anywhere any more. **They are not fixes to the
> running system; they are a description of a bug, verified somewhere else.**
> The bug is real and the guard is real. The money movements are not.

**Autonomy — §1 is conditional, and on the operator's machine the condition is
off.** §1 says killing a firm needs a human "with no exceptions and no quorum
that can override it". The code default is `TRADE_AUTONOMY=human` and honours
that. The machine's `.env` sets `TRADE_AUTONOMY=council`. On 2026-08-14
`firm_e_momentum` was killed and $34,527.47 released, `approved_by='the
council'`, requested and approved **in the same second**, `applied: true`. No
human was involved and none was asked.

Nothing is broken here — the switch is documented and does what it says. But §1
states the invariant as a property of the system, and it is a property of a
setting. If the promise in §1 is the one you want, `TRADE_AUTONOMY` is the line
that keeps it.

**The scoreboard, as of the last run:**

```
the village made 1.28% where buy-and-hold made 1.22% — BEATING the index by 0.06%
  4 of 9 firms beat it individually
  capital $620,000.00 · village $627,906.41 · buy-and-hold $627,591.77
```

A 0.06% edge over 90 bars is **not** a victory, and `benchmark.py` refuses to
call one below 60 bars for a reason. Treat it as "not yet losing".

**Winners:** `firm_h_global` (+6.95% vs SPY), `firm_b_stocks` (+3.73%),
`firm_d_value` (+1.83%), `firm_f_bonds` (+1.25%).
**Losers:** `firm_c_crypto` (−5.03%), `firm_i_memecoins` (−3.48%),
`firm_a_etf` (−3.11%), `firm_e_momentum` (−2.72%), `firm_g_commodities` (−1.77%).

**Deployment:** Railway has **never been deployed**. The config exists
(`railway.json`, `HOSTING.md`, `scripts/railway_setup.sh`); nobody has run it.

**Data:** this container's proxy blocks `data.alpaca.markets`, Yahoo Finance and
GitHub HTML (403). `git ls-remote` and PyPI work. Numbers above come from the
local SQLite ledger, not live Alpaca data. **On the user's own machine
`trade benchmark` will give a different — and real — answer.**

> *2026-08-15, from the machine that can reach Alpaca.* It gave a different
> answer for a reason nobody predicted: **the feed had not returned a bar
> newer than 2026-06-04 since the day it started.** See §4a. Everything the
> running village decided between 2026-08-13 and 2026-08-15 was decided on
> ten-week-old prices, and the ledger it wrote is not evidence about anything.

---

## 3. Active files

The ones you will actually need. Everything else follows from these.

| File | Why it matters |
|---|---|
| `src/money.py` | `Decimal` everywhere. Floats never touch cash. Start here. |
| `src/trading/resolution.py` | **The one place a bar's length is known.** Read this before touching anything time-related — see §5. |
| `src/trading/benchmark.py` | The goal, measured. `compare()`, `village()`. |
| `src/trading/postmortem.py` | Where the money went, named as one of four causes. |
| `src/trading/brokerage/allocator.py` | Capital in and out. The cadence guard lives here. |
| `src/trading/brokerage/evaluator.py` | Scores. The `by_bar` sort in here is load-bearing — see §5. |
| `src/trading/brokerage/reconciliation.py` | The two identities that make every other number trustworthy. |
| `src/trading/ecosystem.py` | The tick. Everything is wired together here. |
| `src/trading/options.py` | Contracts. `contract_size()` is the important function. |
| `src/trading/expiry.py` | The day a contract stops existing. |
| `src/trading/firms/risk_manager.py` | Every limit a trade must clear. |
| `src/trading/store.py` | `settle()` is the only path money moves through. |
| `config/firm_config.yaml` | The nine firms, their universes and cost models. |
| `scripts/restore_allocation.py` | Putting capital back, by hand, with a name on it. |
| `scripts/why_dead.py` | Read-only. First thing to run when a firm looks wrong. |

**The two identities.** Everything downstream rests on these; if they break,
the tick refuses to run rather than making decisions from broken books:

```
cash   = allocation + Σ fill.cash_delta
equity = allocation + Σ realized_pnl + unrealized_pnl − Σ fees
```

Note `fees`, not `fees + slippage` — slippage is inside the fill price and
therefore already inside `realized_pnl`. Subtracting it again is a bug that has
been made once already (§5).

---

## 4. Changes made

Most recent session, oldest first.

**Alpaca costs, not Coinbase.** The crypto desks were costed at 50/60bps from a
Coinbase dossier. Every firm is `venue=paper` and goes live on **Alpaca**;
Coinbase appears nowhere in `src/`. Corrected to 25bps, with wider slippage on
the memecoin desk for the thin books it trades. *The user caught this, not me.*

**`trade benchmark` (new).** Buy-and-hold over the bars each firm actually
lived, with that firm's own capital, charged one entry at the venue's costs and
no exit — because the alternative being modelled is that you bought it and are
still holding it. Refuses rather than guesses: an unpriceable benchmark, an
unvaluable book, or a window under 60 bars all produce a reason, never a number.

**The allocator has a cadence.** `firm_a_etf` took **thirteen 10% cuts in four
wall-clock seconds** (`0.9^13 = 0.254`, and it landed on exactly 25.4% of its
capital). Cuts are now one per firm per market bar, guarded from the event log
rather than memory — the loop restarts routinely and a guard that resets on
restart scales with how often you deploy. Every one of those cuts also landed on
a firm the kill switch had **paused** two seconds earlier: a paused firm isn't
trading, so its score is frozen, so it qualifies for a cut every bar forever.
Cuts now apply only to active firms.

**Three measurement bugs, all flattering in the same direction.** The benchmark
first reported the village losing to SPY by 11.80%. It wasn't:

- Capital *withdrawn* was counted as a trading loss (equity measured against the
  opening mandate, not capital entrusted). `firm_a_etf` read −76.29% having lost
  1.66%. That one firm turned a real +0.20% into a reported −11.80%.
- Firms whose mandate moved mid-window now say so — it's a return on capital
  entrusted, not a time-weighted return, and that caveat is printed rather than
  left for the reader to work out.
- Slippage was subtracted twice. The post-mortem showed $1,828.94 of causes for
  a $1,705.46 loss, with **every row individually true**.

**$64,581 restored to `firm_a_etf`.** To $90,000, not $100,000 — twelve of the
thirteen cuts were the bug, but the first (score 25.79 against a floor of 40) was
a real judgement and stands. Done through `apply_approved_increase`, which moves
cash and allocation together. `trade reconcile` after: 9 firms, no breaks.
`apply_approved_increase` now records a reason and an authoriser, because "earned
a raise" and "we took it by mistake" arrive through the same door and only one is
evidence about the strategy.

**Options a firm can hold.** The multiplier reached none of the money — one
contract at $3.20 was booked as $3.20, not $320, a silent hundred-fold error in
cash, equity, realised P&L, fees and every risk cap. Contract size is now read
from the symbol (`options.contract_size`) rather than stored, because a stored
multiplier is a column that can be wrong four different ways. A stock is a
contract of size one, which is why all 1324 existing tests passed unchanged.
Writing options is **blocked**, not sized — max loss is unbounded and no capital
limit can bound it — and that block is independent of `allow_short`. `expiry.py`
settles expired contracts at intrinsic value before any firm deliberates.

---

## 4a. The session of 2026-08-15 — run on the real machine

The first time any of this was checked against the operator's own hardware.
Three defects, all in the same organ, all invisible from inside the container.

**The village had been blind since it started.** `AlpacaFeed` read one page of
Alpaca's answer and ignored `next_page_token`. Alpaca returns the **oldest**
page first, so a request for eighty days of hourly bars came back with the
oldest ~260 of them and a token for the rest, which nobody followed. Measured
on 2026-08-15: the newest `ETH-USD` bar the village could see was
**2026-06-04**, and the newest `SPY` bar was **2026-07-01**. Every mark, every
indicator, every score and every ethics verdict for three days was computed
against June. Credentials were valid, every request returned 200, nothing was
logged, and no test failed. Fixed by following the token, plus `_assert_fresh`,
which refuses a series whose newest bar is older than `TRADE_MAX_STALE_DAYS`
(default 4) — because pagination was one way to go blind and it will not be the
last, and the check that catches the next one must not need to know its name.

**The bar count that was a day count.** `bars = bars[-self.days:]` — and
`self.days` is *calendar days*, which is what `_start()` reaches back by. On a
daily feed the two are the same number, which is why it survived. On the `1h`
bar the village actually runs, it kept **43 bars of the 180 it asked for**.
Fixed by giving the feed a separate `keep_bars`. **This is the eighth
appearance of the bug class in §5** and the second in this one function.

**A cache with no expiry.** `series()` stored bars per symbol and never let go,
and `ecosystem.feed` builds one feed for the life of the process. The loop had
been up since 2026-08-14. Even with pagination fixed, every tick after the
first would have been answered from the bars fetched at boot. The cache now
expires after one bar (`ttl_s=bar.seconds`) — long enough to keep forty symbols
a minute under the rate limit, short enough that nothing new can have happened.

**The ethics layer blocked an exit, 1,053 times.** Of 3,866 brokerage events on
the real ledger, **3,847 were ethics BLOCKs** — and all of them were the same
three orders, re-proposed and re-refused every sixty seconds from 2026-08-13 to
2026-08-15. The village had not filled an order since 2026-08-14T15:06Z. One of
the three was `firm_c_crypto` trying to **sell** 6.66 ETH.

`_liberty` divided the order by the mean volume of 20 **bars** and called the
result a share of a **day** — the same bug class again, ninth appearance — so
6.66 ETH read as 318% of a day's volume when it was nearer 4%. But the unit bug
is the smaller half. The larger half is that the check applied to a *reducing*
order at all: the foundation whose entire purpose is that the operator can
always get out was the thing standing between a firm and the way out, while
`care` in the same payload said "reducing exposure cannot harm the capital".

**A reducing order is now never refused by `liberty`** — not for size, not for
liquidity, not for anything that foundation knows about. Illiquidity is a
reason to be slower getting *in*. It is never a reason to be barred from
getting out. The denominator goes through `resolution.py` (and crypto's day is
24 hours of bars, not the 6.5-hour equity session).

**Verified after the fix, against live Alpaca:** `ETH-USD` newest bar
`2026-08-16T04:00Z`, `WIF-USD` `2026-08-16T05:00Z`, `SPY` `2026-08-14T20:00Z`
(Friday's close — correct for a Saturday), 180 bars each instead of 43.

**Deployed and observed.** Both processes were stopped and restarted on the
fixed code at `2026-08-17T05:40Z`. First tick five minutes later:

```
tick @ 2026-08-17 05:45 — 6 proposal(s), 1 filled, 3 blocked by risk, 2 blocked by ethics
reconciled: 9 firm(s), no breaks
```

The first fill since `2026-08-14T15:06Z`. The two identities still hold. The
ETH exit is no longer refused — and note *why* it is not simply passing now:
against August prices the firm no longer wants to sell ETH at all. **The exit
that was blocked 1,053 times was itself a June artefact.** The two remaining
ethics blocks are `SOL-USD` and `DOGE-USD` **buys** at 20.9% and 42.0% of daily
volume — entries, correctly refused, and now with numbers that mean something.

**One honest caveat: the retry loop is not gone, it is only correct.** Those
two buys are re-proposed and re-refused every sixty seconds, exactly as the ETH
sell was. Nothing is wrong with either decision; the firm simply wants a
position bigger than Alpaca's book will absorb and has no way to learn that.
Vetoing an order the venue cannot fill, forever, is still the wrong shape — it
should be **sized down to the liquidity limit**, not refused. Until then the
"alarm on repetition" in the conscience section below is what would have caught
the original outage on day one, and it would catch this too.

### What this does *not* fix

- **The ledger is not a track record.** Positions were opened on June prices
  and are now marked at August ones. The P&L that appears is a measurement
  artefact of the fix, not a result. Do not benchmark across 2026-08-15.
- **`firm_e_momentum` is killed and still holds $45,472.53** in three open
  positions (NVDA, AMZN, TSLA). The kill returned $34,527.47 of *cash* and
  abandoned the book. A killed firm does not tick, and `_authority` blocks its
  orders anyway — including sells — so nothing will ever close them. The same
  inversion as `liberty`, in a second place.

  **There is already a right way to handle this and it was not invented here.**
  `brokerage.revive_firm` exists for "a kill decided on numbers that were not
  real", and its docstring records the precedent: an Alpaca outage once killed
  a village of eleven by marking every position to zero. This kill qualifies on
  the facts — the six consecutive losing trades that triggered it were all
  executed against June prices on 2026-08-14. The function is guarded exactly
  as you would want: it re-evaluates against a working feed and **refuses if
  the firm would be killed again today**, and it restores status only, never
  capital.

  ```bash
  trade revive firm_e_momentum --by "<name>"
  ```

  **Left for the operator deliberately.** It is not a bug fix — it either
  reverses a judgement or moves $45k of book back into play, and the guard
  makes it safe to *attempt*, not automatic to decide.
- **The test suite reads the operator's `.env`.** `src/config.py:22` calls
  `_load_dotenv()` at import, so `TRADE_BAR=1h` from the machine's `.env` lands
  in every test process. `test_ccxt_is_reachable_by_configuration` fails on a
  clean checkout on that machine and passes in the container, on identical
  code. Two tests now pin their own resolution; the general problem — a suite
  whose result depends on machine config — is untouched.

- **"1347 passing" does not reproduce here, and the gap is not this branch.**
  Measured on the operator's machine, at commit `3a6e3b6`, *before* any change
  in §4a: **63 failed, 27 errors.** After: 70 failed, 27 errors — and every one
  of the seven new failures is a test added in §4a that **passes when its file
  is run alone**. One baseline failure was fixed. No existing test changed from
  passing to failing.

  The cause is not the branch, it is the suite. `test_asgi.py`,
  `test_deploy.py`, `test_trading_webhooks.py` and `test_access.py` call
  `importlib.reload()`, and when those files fail — as they do on this machine
  — they leave `sys.modules` half-rebuilt. Everything downstream that
  monkeypatches a module global then patches an object the code no longer
  resolves against, and the fake is silently bypassed. Demonstrated:

  ```
  pytest tests/test_trading_ccxt_feed.py                       →  54 passed
  pytest tests/test_asgi.py tests/test_deploy.py \
         tests/test_trading_ccxt_feed.py                       →  20 failed
  ```

  The visible symptom is the worst possible one: **the feed tests stop being
  fakes and make real HTTP requests to Alpaca**, with the fixture's dummy
  credentials, and fail on 401. Seventeen such calls in one run. A suite that
  reaches the network when it believes it is offline can fail for reasons that
  have nothing to do with the code, and — more dangerously — can *pass* for
  reasons that have nothing to do with the code.

  **Do not read a green or red full-suite run on this machine as evidence
  about a change.** Run the files that cover what you touched, alone, and
  compare against the same files alone at the previous commit. That is what
  was done for §4a.

## 4b. The week of 2026-08-17 — six firms killed, three of them profitable

A week of live running after §4a. 336 fills, then six of nine firms dead. The
kill reason on **all six** was the same: *"6 consecutive losing trades (limit
5)"*. Their actual records:

| firm | closed | win rate | realised P&L | killed |
|---|---|---|---|---|
| `firm_b_stocks` | 34 | **71%** | **+$2,367.67** | yes |
| `firm_i_memecoins` | 9 | 22% | **+$1,400.61** | yes |
| `firm_c_crypto` | 7 | 14% | **+$38.67** | yes |
| `firm_h_global` | 8 | 12% | −$596.29 | yes |
| `firm_e_momentum` | 6 | 0% | −$278.96 | yes |
| `firm_a_etf` | 6 | 0% | −$220.79 | yes |

**The best firm in the village was killed.** 71% win rate, the largest realised
profit of any desk, and six days earlier the council had raised it six times
from $100,000 to $177,156 on scores of 67–83.

Here are the six "consecutive losing trades" that killed it:

```
2026-08-22T03:08:24Z  JNJ sell  13.7980 @ 267.45  pnl= -40.97
2026-08-22T03:07:23Z  JNJ sell   7.9325 @ 267.45  pnl= -23.55
2026-08-22T03:06:23Z  JNJ sell  15.8650 @ 267.45  pnl= -47.10
2026-08-22T03:05:23Z  JNJ sell  12.0663 @ 267.45  pnl= -35.82
2026-08-22T03:04:22Z  JNJ sell   4.4690 @ 267.45  pnl= -13.27
2026-08-22T03:03:22Z  JNJ sell   8.9380 @ 267.45  pnl= -26.54
```

One symbol. One price. Six consecutive minutes. **That is one exit, executed in
six slices** — and the counter scored it as six failures of judgement.

`store.settle` is where it happens, and it runs once per *fill*:

```python
consecutive = firm.consecutive_losses
if realized < 0:
    consecutive += 1
elif realized > 0:
    consecutive = 0
```

**A count of fills used as a count of trades** — the ninth appearance of the
class in §5, and the first one that is not about time. Nothing resets the
counter when a *position* closes; only a profitable fill clears it. So a firm
that leaves one position at a loss, in more pieces than the limit, dies for it
regardless of everything else it has ever done.

Two aggravating details:

- **Dust counts as evidence.** The fills immediately before were JNJ sells of
  0.0008, 0.0015, 0.0030, 0.0060 and 0.0121 shares, booking one to nine cents
  each. Sub-penny remainders both increment and reset this counter.
- **Both profitable kills happened with the market shut.** `firm_b_stocks` died
  on Saturday 2026-08-22 and `firm_i_memecoins` on Sunday the 23rd, and every
  JNJ slice above filled at $267.45 — Friday's close, frozen. The village runs
  an hourly loop against the last available bar seven days a week, so a
  weekend exit books the same loss over and over against a price that cannot
  move, and the counter climbs against a closed exchange.

**Not fixed — it is a decision, not a bug fix.** This rule decides which firms
live, and the repair has real choices in it: count round trips rather than
fills, reset on a position close, debounce per bar, or ignore fills below a
size floor. Whichever is chosen, the six kills above were not judgements about
strategy and the firms that suffered them have a claim under `revive_firm`,
which refuses anything that would fail again today.

**Read this section before proposing anything.** Most of it is me being wrong.

### The bug class that has now appeared six times

> **A rate quoted per unit of market time, applied per unit of wall-clock time.**

1. Sharpe computed per tick, not per bar → killed a firm at 80% win rate, 1.27%
   drawdown, positive return.
2. Bar count incremented per scorecard → "136 bars on alpaca" after two hours.
3. Borrow charged `rate/365` every tick → a year of financing every six hours.
4. Evolution cadence `day % every == 0` stayed true all bar → a generation swept
   every minute.
5. The `by_bar` insertion-order scramble (below).
6. Allocation cuts per tick → the thirteen cuts.
7. `bars[-self.days:]` — calendar days used as a bar count → 43 bars of the
   180 asked for, on every symbol, for the life of the deployment (§4a).
8. `_liberty` dividing by mean *bar* volume and calling it *daily* volume →
   an exit refused 1,053 times (§4a).

9. `consecutive_losses` incremented once per **fill** rather than once per
   **trade** (§4b) → the village's best firm killed by a single exit.

Two of those eight were found on the same day, in the same organ, by running
the thing on a machine that could reach the market. The container could not,
so in the container both were unreachable and neither test suite nor review
would ever have caught them. **The bug class is not slowing down.**

`src/trading/resolution.py` exists so there is exactly one place that knows how
long a bar is. **If you are writing anything that divides by time, go through
it.** Assume this bug is still hiding somewhere and look for it.

### Things I recommended, tested, and was wrong about

- **Stop losses.** I was confident an 8.5x payoff ratio meant a broken exit and
  that a stop would fix it. A/B on real history: `stop=0` → **+$16,587**;
  `stop=8%` → **+$9,671**. Shipped as a gene defaulting to **0 (off)**. Do not
  turn stops on without re-running that experiment.
- **A correlation limit.** I recommended one, then measured: only **3 of 36**
  firm pairs share a single symbol, and the worst concurrent concentration was
  1 of 9. **Did not build it.** The problem did not exist.

### Bugs in my own fixes

- **The cadence guard's first version never fired.** It compared the *market*
  bar against the row's `created_at` — two different clocks, which is the exact
  substitution that caused the original bug. The bar is now written into the
  event payload and read back from there.
- **The `by_bar` scramble.** Python dicts preserve first-insertion order, so a
  replay produced bars 31→60 then 1→30. Same clean database, two runs: Sharpe
  **3.4004** and a proposed raise, then **−0.0644** and a kill request against a
  firm at 69% win rate, 0.42% drawdown, +$3,332. Fixed by sorting explicitly and
  rejecting non-forward steps. **Never rely on dict ordering for a time series.**
- **A test that asserted nothing.** `test_the_identity_adds_up` only counted
  table rows. Its fixture wrote fills directly to the database, bypassing
  `store.settle`, so equity never moved and the identity could not have been
  checked. Now tested against a constructed object instead.

### External material, checked

- **`install_all.sh`**: **5 of 7 clone URLs do not exist.** Real ones:
  `Audazia/solar-system-agents`, `Traxin3/ryan-rl-trader`, `ygwyg/MAHORAGA`,
  `AlphaGBM`, plus `mindcache` on PyPI. `git ls-remote` is the definitive test
  here — plain `curl` on github.com returns 403 from this proxy regardless, so
  a 404 proves nothing.
- **MindCache**: verified real, **deliberately not installed**. Its premise (an
  LLM rewriting stored memory) contradicts "the ledger is the only source of
  truth". Took the taxonomy, left the package.
- **`npx the-agents-hub`, `docker run mintplexlabs/anythingllm`, `install_all.sh`**:
  these execute strangers' code on the machine holding the keys and the ledger.
  Flagged, not run.
- I was about to correct another assistant about `solar-system-agents` and
  **checked first**. They were right; the attribution is in
  `src/trading/static/solar.html:4`. Check before correcting.

---

## 6. Next steps

Roughly in order of value to the goal.

0. **Decide what happens to the poisoned ledger** *(new, and ahead of the
   benchmark)*. The village traded for three days on June prices and then
   stopped trading at all. Its 262 fills are not a record of a strategy; they
   are a record of a broken feed. Benchmarking it answers a question nobody
   asked. The two honest options are to reset the paper ledger and start the
   clock at the fix, or to keep it and mark everything before 2026-08-15 as
   uncounted. `data/mvv.db.before-feedfix-2026-08-15` is the pre-fix ledger,
   kept for exactly this decision.

1. **Run `trade benchmark` on the real machine.** Every number in §2 comes from
   a container that cannot reach Alpaca. The real answer may differ. Nothing
   below is worth doing if the scoreboard says something else. *Do this after
   0, not before — on the current ledger it measures the bug.*
2. **The five losing firms.** `trade post-mortem <firm>` names one of four
   causes — costs, exit, win rate, one bad symbol. `firm_c_crypto` (−5.03%) and
   `firm_i_memecoins` (−3.48%) are the worst. Note the post-mortem refuses to
   diagnose below 20 closed trades, and that refusal is correct.
3. **Assignment at expiry** *(task #32)*. `expiry.py` cash-settles at intrinsic.
   Real options assign into shares and consume `strike × 100` of capital a firm
   may not have — so the current model gets P&L exactly right and the capital
   requirement wrong, **in the flattering direction**. `SETTLES_FOR_CASH` is the
   flag to flip.
4. **Open a PR.** 45 commits, no PR, no review.
5. **Deploy to Railway.** Never done. `HOSTING.md` has the procedure. Note the
   approval gate has **no authentication of its own** — a hosted deployment must
   run read-only unless `MVV_GATE_TOKEN` is set.
6. **`Traxin3/ryan-rl-trader`** — verified real, never examined.
7. AlphaGBM / Unusual Whales as inputs to the signals board.
8. Lattice at `/village/lattice` *(#25)*, OmniRoute *(#26)*, Perplexica +
   AnythingLLM *(#27)*.

### On keeping the conscience at all

Asked directly on 2026-08-15: should the ethics layer be deleted? The record,
from the real ledger, before any interpretation:

| | |
|---|---|
| reviews performed | 3,860 |
| non-`allow` findings | 3,860 |
| …from `liberty` | **3,860 (100%)** |
| …from the other five foundations | **0** |
| correct refusals, all time | **0** |

Every refusal the conscience has ever issued came from one foundation, and
every one of them was the unit bug in §4a. `care`, `fairness`, `loyalty`,
`authority` and `sanctity` have not objected to anything, once, ever.

That is a real argument for deletion and it should not be waved away. The
argument against deletion is that it is the wrong cut. Read the six by what
they *do* rather than what they are named:

| foundation | what it actually checks | who else already checks it |
|---|---|---|
| `care` | concentration, equity floor | `risk_manager` |
| `loyalty` | symbol ∈ firm universe | `risk_manager` |
| `authority` | killed / paused / venue gates | the gate |
| `fairness` | wash trades, churn | nobody — and it matters live |
| `sanctity` | operator's restricted list | nobody — operator policy |
| `liberty` | order vs. venue liquidity | nobody — real risk |

Three duplicate checks that already exist upstream, which is *why* they have
never fired: something else refuses first. Three are load-bearing and have no
other home.

So: **keep the checks, drop the frame.** The moral vocabulary is not neutral
decoration — it is what caused the outage. "Liberty: never take a position the
operator cannot get out of" is a sentence about *entering*, and implemented as
a veto over every order it silently became a veto over *leaving*. Had it been
called `liquidity_risk` and lived in `risk_manager` beside the other sizing
rules, the exemption for a reducing order would have been the first thing
anyone wrote, because `risk_manager` already knows the difference between
opening and closing and the conscience does not.

A second cost: six foundations reporting on every order made 3,847 identical
blocks look like a system working hard rather than a system stuck. The signal
that mattered — *nothing has filled in 33 hours* — was not in the payload
anywhere.

**Recommended, not done** (each is a decision, not a bug fix):

- Fold `care`, `loyalty` and `authority` into `risk_manager`; delete the
  duplicates rather than keeping two places that can disagree.
- Rename what remains for what it is: `restricted_list` and `liquidity_risk`.
  Keep `fairness` — a wash trade is meaningless on paper and not meaningless
  on a real venue.
- Alarm on *repetition*: the same order refused for the same reason N times is
  a defect report, whatever the reason is. That alarm would have caught this on
  day one and does not depend on anyone guessing which check breaks next.

### Standing constraints — do not quietly relax these

- **API keys never enter the repository or GitHub.** They live in the shell or a
  gitignored `.env`. `AlpacaFeed` sends them in a header, never a URL. `dotenv.load()`
  returns names, never values.
- **TLS verification is never disabled.** There is a test asserting
  `CERT_REQUIRED` and `check_hostname`.
- **The council may never decide `LIVE_TRADING`.** It has no panel for it, by
  construction. Do not add one.
- The approval gate has no authentication of its own and must never be exposed
  on a public URL with a write surface.
- `MVV_WEBHOOK_TOKEN` is deliberately **separate** from `MVV_GATE_TOKEN`, because
  TradingView stores the alert body in plaintext in its own UI.
- The court and importer **never execute** user files — parsed with `ast` only.
- The sandbox/tavern gets a `ReadOnlyStore`. It cannot reach the money.
- The living-village bazaar trades **tokens only**, never capital.
- New state goes in **new tables**: SQLite has no `ADD COLUMN IF NOT EXISTS`, and
  migrations are dual-dialect (Postgres + a `sqlite/` mirror). A parity test
  asserts the migration count — currently **21**.
- Feeds use the **standard library only** (`urllib`, not `requests`).

### Two open items the user has already been told about, once each

- The Alpaca keys pasted into the chat are **live**. I advised rotating them; the
  user said they were fine with it. That was their call and I have not raised it
  again. **Do not re-litigate it.**
- The `fomo` dossier records a **wallet key exposed 2026-08-02, not rotated**,
  with ~1.13 SOL live. Flagged once. Same standing.
