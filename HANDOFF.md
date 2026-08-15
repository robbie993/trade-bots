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

**Branch:** `claude/ai-village-trading-build-m4bg19`, **45 commits ahead** of the
default branch `claude/mvv-phase-1-spec-ib4eui`. **No PR is open.** ~100 files,
+16,500 lines.

**Tests:** 1347 passing, 2 skipped. `ruff check src/` clean. Six pre-existing
lint warnings remain in `tests/` (`test_cli.py` E702 ×3, `test_trading_api.py`
F401, `test_trading_living.py` E741 ×2) — untouched, unrelated to this work.

**Money:** all 9 firms are `venue=paper`. 7 active, 2 paused. **Nothing has ever
touched real money.** `trade live-status --summary` reports 0 of 9 firms meeting
the promotion criteria, which is the expected answer for a village this young.

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

## 5. Failed attempts

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

1. **Run `trade benchmark` on the real machine.** Every number in §2 comes from
   a container that cannot reach Alpaca. The real answer may differ. Nothing
   below is worth doing if the scoreboard says something else.
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
