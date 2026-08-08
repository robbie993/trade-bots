# The AI Village — Trading Edition

A multi-firm trading ecosystem with a brokerage layer above it, built to the
"Trading Edition" build document and living inside the same repository as the
product village.

Same village, different goods. The product village (`src/agents`) tests
products; this one (`src/trading`) tests strategies. The invariants are
identical, which is why they share a database, an approvals table and a
principle:

> **The system may always stop the bleeding, and may never start it.**

```
┌──────────────────────────────────────────────────────────────────────────┐
│  THE BROKERAGE            reconcile → evaluate → kill → allocate → rank   │
│                                                                          │
│  cuts capital by itself · asks a human before adding any                  │
│  refuses to act at all while the ledger does not reconcile                │
└──────────────────────────────────────────────────────────────────────────┘
        │                        │                        │
        ▼                        ▼                        ▼
┌────────────────┐      ┌────────────────┐      ┌────────────────┐
│  FIRM A (ETF)  │      │ FIRM B (stocks)│      │ FIRM C (crypto)│
│                │      │                │      │                │
│  analysts      │      │  analysts      │      │  analysts      │
│  bull vs bear  │      │  bull vs bear  │      │  bull vs bear  │
│  trader        │      │  trader        │      │  trader        │
│  risk manager  │      │  risk manager  │      │  risk manager  │
│  kill switch   │      │  kill switch   │      │  kill switch   │
└────────────────┘      └────────────────┘      └────────────────┘
        │                        │                        │
        ▼                        ▼                        ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  BRAIN   memory · evolution over genomes · lessons with their evidence    │
│  HEART   six moral foundations, allow / warn / block, before every fill   │
│  GATEWAY optional LLM narration — never a decision                        │
│  AUDIT   an Obsidian vault of plain Markdown                              │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Quick start

Nothing below needs an API key, a market data subscription, or a network
connection.

```bash
python -m src.main init-db          # adds the trading tables to the same database
python -m src.main trade init       # create firms from config/firm_config.yaml
python -m src.main trade tick       # one full pass of the ecosystem
python -m src.main trade leaderboard
```

Watch a whole quarter go by in about two seconds — this drives the *real* loop
(ledger, ethics, kill switches, allocations, audit trail) over historical bars:

```bash
python -m src.main trade simulate --days 120
python -m src.main trade allocations     # who got cut, who was proposed a raise
python -m src.main trade kill-status     # every kill condition, per firm
python -m src.main approvals             # what it is waiting on you for
```

---

## The tick, in order

```
market data  →  firms propose
             →  heart reviews every proposal      (before anything executes)
             →  venue fills what survived
             →  ledger settles                     (position + cash + P&L, atomically)
             →  brain remembers
             →  brokerage reconciles, scores, kills, allocates
             →  audit trail written
```

The order *is* the safety argument. Ethics runs before execution; settlement
is all-or-nothing; and if the books do not reconcile, nothing after that point
runs — including the parts that would look like progress.

---

## What the system may and may not do alone

| Autonomous | Needs a human |
|---|---|
| Read data, analyse, debate, propose | Send an order to any live venue |
| Trade on the **paper** venue | Raise a firm's allocation |
| **Cut** a firm's allocation | Kill a firm for good |
| Pause a firm that trips its kill switch | Un-pause a firm |
| Close positions, hand cash back | Add a new live venue |
| Halt the entire ecosystem | — |

Every entry in the right-hand column writes a row in `human_approvals` — the
same table, the same `mvv approve` command, as the product village. A pending
request spends nothing and expires into nothing.

```bash
python -m src.main trade live-request --venue alpaca   # asks; sends no order
python -m src.main approvals
python -m src.main approve 3 --by you
python -m src.main trade apply-approvals               # carries out what you decided
```

---

## The firms

A firm is a pod with its own capital, risk limit and kill switch, defined in
`config/firm_config.yaml`:

```yaml
firms:
  firm_a_etf:
    name: "ETF Specialist"
    asset_class: "ETF"
    risk_limit: 0.02              # of allocation, per new position
    capital_allocation: 100000
    universe: [SPY, QQQ, IWM, DIA, EFA]
    analysts: [fundamental, technical, sentiment]
    genome:
      trend_bias: 65              # 100 = pure momentum, 0 = pure mean reversion
```

`trade init` is safe to re-run: it updates the mandate (name, universe,
analysts, genome) and never re-funds a firm that already exists. Capital only
moves through the allocator.

**The analysts** turn price history into a score from -100 to +100 with a
confidence from 0 to 100. They are deterministic functions of the data, not
model calls. An analyst without enough history returns confidence 0, and the
debate treats that as *silence*, not as a neutral vote.

Three of the five are honest proxies rather than the real thing, and they say
so in every note they produce: `sentiment` reads volume and candle shape (no
social feed), `fundamental` reads distance from a long-run price anchor (no
fundamentals vendor), `onchain` reads effort-versus-result in volume (no node).
An unlabelled proxy is how a system ends up trusting a number nobody can source.

**Bull and Bear** each get the same signals and may only cite the ones that
support their side. The winner is decided by weight of evidence, and the
*margin* becomes the trader's confidence — so a 90/10 argument produces a full
position and a 51/49 argument produces nothing.

**The trader** converts conviction into size, and that is its only judgement.
Selling is never gated on confidence: if the bear case wins on a name the firm
holds, it sells. The exit does not have to argue as hard as the entry did.

**The risk manager** returns allow / resize / block. Reducing exposure is
checked for solvency and nothing else; opening or increasing it must clear the
risk limit, the position cap, the position count and the cash floor.

---

## Kill switches

Per firm (`src/trading/firms/kill_switch.py`), evaluated in a fixed order so
the reason attached to a kill is reproducible from the stored metrics:

| Condition | Default | Waits for 20 trades? |
|---|---|---|
| Drawdown | > 20% | **no** |
| Single-trade loss | > 10% of capital | **no** |
| Consecutive losses | > 5 | **no** |
| Win rate | < 30% | yes |
| Sharpe | < 0.5 | yes |

The first three are the account emptying, not a statistical judgement about a
strategy's edge — waiting twenty trades to notice a 40% drawdown would be the
system failing to stop the bleeding. The last two are claims about skill, and
below the sample gate the answer is "insufficient data", never "kill".

A trip **pauses** the firm — no new positions, exits still allowed — and asks
a human. Pausing is autonomous because it stops the bleeding. Killing is not,
because it is final.

The ecosystem-wide KILL_ALL fires when every firm is dead, total drawdown
passes 25%, a firm kill has gone unanswered past the timeout, or losses plus
fees exceed a tenth of deployed capital. It pauses everything and asks.

---

## The brokerage

```
reconcile → evaluate → kill checks → allocate → leaderboard
```

**Reconciliation comes first and is not optional.** Two independent identities
are checked per firm, derived from different columns so a bug that fakes one
will not fake the other:

```
cash   = allocation + Σ fill.cash_delta
equity = allocation + Σ realized_pnl + unrealized_pnl - Σ fees
```

**The score** is a plain weighted sum in the 0-100 band — 50 base, plus return,
minus drawdown, plus win rate and Sharpe once there are enough trades — and
every component is stored next to the total, so a capital cut can be
re-derived from the row months later. No part of it comes from a language
model.

Two measurement details that took a long simulation to surface, and that are
worth stating because they are easy to get wrong:

* Return is measured against the capital the firm was **given**, not against
  what it currently holds. Otherwise a losing firm's numbers improve simply
  because money was taken away from it.
* A capital withdrawal moves the high-water mark with it. Otherwise taking
  $90k off a firm reads as a 90% drawdown, the score collapses, and the cut
  triggers further cuts — a death spiral driven by bookkeeping.

**The allocator** cuts by itself and can only ever *request* an increase. A cut
is limited to the firm's uninvested cash; the rest is taken on a later pass as
positions close, because pushing cash negative would block the very sells that
free the money.

A firm's capital splits into buckets that behave differently, named once in
`models.CashView` rather than recomputed at each call site:

| Bucket | Meaning |
|---|---|
| `in_positions` | mark-to-market of what is open |
| `reserve` | `cash_floor_pct` × allocation — never spent |
| `available` | cash − reserve: what the **risk manager** may deploy |
| `withdrawable` | cash including the reserve: what the **brokerage** may take back |

`available` and `withdrawable` differ on purpose. Returning capital lowers the
allocation the floor is a fraction of, so a withdrawal cannot breach the
reserve; only the risk manager is bound by it. Conflating those two is what
drove cash negative in the first long simulation, so they now have names.

---

## The heart

Six moral foundations, each a pure function of the proposal, the book and the
market, each returning allow / warn / block:

| Foundation | Refuses |
|---|---|
| care | a trade bigger than the firm; warns on concentration |
| fairness | wash trades and churn |
| loyalty | symbols outside the firm's mandated universe |
| authority | buying while paused, killed, or on an unapproved venue |
| sanctity | the operator's restricted symbols, and restricted *categories* |
| liberty | a position too large to exit against typical volume |

The build document points at `lex-conscience` for this. That repository is not
reachable (the GitHub path 404s), so the foundation model is implemented
natively — which is the better outcome here anyway: an ethics check that can
be *unavailable* is not an ethics check. Set `TRADE_ETHICS_STRICT=1` to turn
every warning into a block.

Sanctity works in two layers. `TRADE_RESTRICTED_SYMBOLS` refuses individual
tickers; `config/restricted_assets.yaml` plus `TRADE_RESTRICTED_CATEGORIES`
refuses whole categories — "I will not hold weapons manufacturers" is a rule
about a category, and restating it as a ticker list you have to keep current
is how it quietly stops being enforced.

The mapping is yours. This system ships **no** classification of what any
company does: the file is empty, and inferring an industry from a ticker would
produce refusals nobody could justify and, worse, silent approvals for
something listed under another symbol. With categories enabled, a symbol you
have not classified is recorded on the proposal as *unclassified — category
rules cannot be applied to it*, so the gap shows up in the audit trail instead
of looking like a pass.

Verdicts are stamped onto the proposal row, including on trades that were
blocked. "Why did this firm buy that?" — and "why didn't it?" — are both
answerable from a row.

---

## The brain

**Memory** writes every closed trade to `trade_memory`, recallable by symbol,
firm or outcome. Recall is keyword and outcome matching over rows, not
embeddings, because a memory that justifies a trade has to be quotable in an
audit and "something similar" cannot be quoted.

**Evolution** mutates the strategy genome, backtests every candidate on
identical bars, and promotes the winner only if it beat the incumbent on that
same data. Seeded from `TRADE_EVO_SEED`: same seed, same data, same survivors,
on any machine.

```bash
python -m src.main trade evolve --firm firm_c_crypto --generations 3
python -m src.main trade backtest
python -m src.main trade memory --symbol SPY
```

**Lessons** are conclusions across firms, each stated with the evidence that
produced it. They never change behaviour on their own — only a promoted
genome, an allocation change, or a human does that. A system that silently
rewrote its own strategy from its own conclusions is exactly what a kill
switch cannot see coming.

---

## Market data

| Source | Needs | Use it for |
|---|---|---|
| `synthetic` (default) | nothing | development, tests, demos |
| `csv` | `data/market/<SYMBOL>.csv` | your own history, reproducibly |
| `yahoo` | `requests` + network | real daily bars |

The synthetic feed is a seeded random walk whose drift and volatility are
derived from a hash of the symbol, so `BTC-USD` is reliably wilder than `SPY`
and every run is byte-identical. The Yahoo feed refuses a truncated history
rather than quietly returning fewer bars — a short series silently changes
every indicator in the system.

Every analyst reads through `MarketData`, which will not hand out a bar past
its cursor. That single rule is what makes the backtest and the live loop the
same code path, and what stops an indicator from reading tomorrow's close.

---

## Execution

`paper` is the default and the only venue that trades without a human. It
crosses the spread and charges a fee, because a simulator that fills at the
mid for free reports an edge that does not exist — and every downstream
decision, including which genome survives, would then rest on a number that
was never real.

Alpaca, Binance and Webull adapters exist so that pointing this at a real
broker is a config change rather than a rewrite. Each refuses to send an order
without an approved `live_trading` row **for that specific venue**; approving
Alpaca does not approve Binance. Credentials come from the environment and are
never logged, stored in the ledger, or written to the vault. (Webull has no
supported public trading API; that adapter says so instead of routing orders
through an unofficial endpoint.)

---

## The external frameworks

```bash
python -m src.main trade frameworks   # what is installed here, right now
./scripts/clone_all.sh                # clone the reachable ones into vendor/
./scripts/install_all.sh              # each into its own virtualenv
```

Every framework named in the build document is optional, and none is in the
critical path. This inverts the plan's week 1-5 ordering on purpose: a trading
system whose kill switch lives in someone else's repository is a trading system
whose kill switch can be deleted by someone else. The refusals — kill criteria,
risk limits, reconciliation, the human gate — are implemented here and stay
here.

Reachability, checked with `git ls-remote` on 2026-08-08:

| Framework | Status |
|---|---|
| LLMQuant/Magents | reachable |
| TauricResearch/TradingAgents | reachable |
| KCNyu/clawock | reachable |
| OpenSourceAGI/ai-broker-investing-agent | reachable |
| cubexch/ai-fund | reachable |
| ruvnet/ruflo | reachable |
| EvoMap/evolver-claude-code-plugin | reachable |
| JaceHo/AgentMem | reachable |
| diegosouzapw/OmniRoute | reachable |
| Drakkar-Software/OctoBot-AI | reachable |
| lex-conscience/lex-conscience | **not reachable** — heart implemented natively |

The two Claude Code plugins install from inside Claude Code:

```
/plugin marketplace add EvoMap/evolver-claude-code-plugin
/plugin install evolver@evolver
/plugin marketplace add ruvnet/ruflo
/plugin install ruflo-core@ruflo
```

---

## Command reference

| Command | What it does |
|---|---|
| `trade init` | create firms from config |
| `trade firms` | what exists, and how it is doing |
| `trade show <firm>` | one firm in full: kill table, positions, proposals |
| `trade tick` | one pass of the ecosystem |
| `trade run --interval N` | the loop, forever |
| `trade simulate --days N` | replay N bars through the whole loop |
| `trade leaderboard` | the ranking, with its evidence |
| `trade allocations` | who holds what capital, and what changed |
| `trade kill-status` | every kill condition, per firm, plus KILL_ALL |
| `trade reconcile` | do the books add up? (exit 1 if not) |
| `trade backtest [--firm F]` | strategy in isolation; writes nothing |
| `trade evolve [--generations N]` | genome evolution; refuses on broken books |
| `trade memory [--symbol S]` | what the brain remembers |
| `trade audit [--write]` | the full audit report |
| `trade status` | one-screen health check |
| `trade monitor --watch` | status, repeatedly |
| `trade frameworks` | which external frameworks are installed |
| `trade live-request --venue V` | ask to trade live; sends no order |
| `trade apply-approvals` | carry out what a human approved |
| `trade resume <firm> --by you` | un-pause a firm |

---

## Layout

```
src/trading/
├── config.py            every tunable, one file, all env-overridable
├── models.py            Bar, Signal, TradeProposal, Fill, Position, FirmRecord
├── store.py             the only place SQL is written
├── indicators.py        pure functions; None when there is not enough data
├── backtest.py          the same pod, the same fills, no database
├── ecosystem.py         the tick, in order
├── adapters.py          optional bridges to the external frameworks
├── cli.py               `trade ...`
├── data/                synthetic / csv / yahoo, behind one cursor
├── firms/               analysts, researchers, trader, risk, kill switch, spec
├── brokerage/           reconciliation, evaluator, allocator, leaderboard, kill
├── brain/               memory, evolver, learning
├── heart/               conscience, restrictions, compliance, ethics
├── gateway/             OmniRoute, with an offline fallback
├── execution/           paper (default) and the live venues that refuse
└── audit/               the Obsidian vault
```

Configuration lives in `config/firm_config.yaml` (the firms) and is documented
in `config/brokerage_config.yaml` and `config/brain_config.yaml`, which map
every setting to the environment variable that sets it.

---

## What is real, and what is not

**Real.** The ledger, the reconciliation, the kill criteria, the human gate,
the risk limits, the ethics checks, the leaderboard, the allocator, the
evolution, the audit trail, the paper fills and their costs. All of it is
tested, and all of it runs with no network.

**Not real.** The market data, by default — `synthetic` is a random walk, and a
strategy that looks profitable on it has demonstrated nothing about the world.
The `fundamental`, `sentiment` and `onchain` analysts are price and volume
proxies wearing the names of the seats they fill; each says so in every note.
No money has moved: the paper venue is a simulation, and every live venue
refuses to send an order until you personally approve it.

What this repository gives you is the *machinery* — an ecosystem that measures
strategies honestly, cuts the losers by itself, and cannot start the bleeding
without you. Point it at real data when you want to find out whether any of
these strategies are worth anything. Expect the answer to be no; that is what
the kill switches are for.
