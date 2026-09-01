# Minimum Viable Village — Phase 1: The Experiment Ledger

Spec version 1.0.

The smallest system that can answer one question:

> Can this system consistently find, test, market, fulfill and manage
> profitable products with real money after **all** costs?

If yes, scale it. If no, kill it. This repository is built so that either
answer is cheap to reach and impossible to fudge.

No Monte Carlo. No 68 agents. No $1M/month claims. Five components, concrete
kill thresholds, a human gate in front of every dollar, and **four**
independent stop conditions that let the system conclude it does not work.

---

## Quick start

```bash
git clone <this repo> && cd trade-bots
pip install -r requirements.txt        # or: pip install -e '.[dev]' for the core only

python -m src.main init-db             # SQLite at ./data/mvv.db by default
python -m src.main cash open --once    # seed the $1,000 budget
python -m src.main tick                # discover -> ask permission -> report
python -m src.main approvals           # see what it is waiting on
python -m src.main approve 1 --by you
python -m src.main tick
```

Or drive it from the web gate — the [KILL] [CONTINUE] [REVIEW_DATA] buttons of
spec §4.4:

```bash
./scripts/start.sh                     # migrations, opening balance, gate on :8000
```

Postgres instead of SQLite:

```bash
docker compose up -d db
export DATABASE_URL=postgresql://mvv:mvv@localhost:5432/mvv
./scripts/reset_db.sh
```

Putting it on a public URL: see **[HOSTING.md](HOSTING.md)**. The short version
is that a hosted copy is a read-only mirror — it shows the village and changes
nothing — because the approval gate has no authentication and does not belong
on the internet. The console stays on your machine.

Running it on **real products**, with no API credentials — enter them by hand:

```bash
export MVV_SCOUT_SOURCE=manual          # stop the fixture inventing products
python -m src.main experiment add \
    --name "Wireless Earbuds Pro" \
    --cost 12.50 --price 39.99 \
    --shipping 0.00 \
    --platform temu --url https://temu.com/...

python -m src.main tick                 # asks permission to order a sample
python -m src.main approvals
```

`experiment add` takes the same path `tick` takes for a scouted candidate —
platform gate, duplicate check, economic filter, in that order — and writes a
`discovered` row. It never moves money: the sample order and the ad budget are
still separate approvals. Omit `--price` to get a suggested one; add `--force`
to record a product the economic filter rejected (it is logged as forced).

Twenty of these answer the question the whole system exists for.

The File Court (spec v7.0) — review files, not spending:

```bash
python -m src.main court doctor         # which reviewers are installed?
python -m src.main court review f.py    # one file through the tiers
python -m src.main court watch          # or watch uploads/
```

Setup and the corrected component list are in [VILLAGE.md](VILLAGE.md).

Tests:

```bash
python -m pytest            # 847 tests, no database server, no network
```

---

## Layout (spec §12)

```
src/
├── config.py               all tunables, the whole risk surface in one file
├── asgi.py                 the deploy entrypoint; reports its own import failure
├── deploy.py               hosted = read-only; the gate is not a public URL
├── money.py                Decimal helpers — no float ever touches cash
├── kill_criteria.py        §4 thresholds, pure functions
├── notifications.py        console / file / Slack / email
├── cli.py, main.py         the operator's interface
├── models/                 one module per table
│   ├── experiment.py       §3.1  ├── order.py  §3.2  └── cash_flow.py §3.3
├── agents/                 the five components
│   ├── scout.py                    1. Scout
│   ├── economic_calculator.py      2. Economic Calculator
│   ├── experiment_ledger.py        3. Experiment Ledger
│   ├── human_gate.py               4. Human Approval Gate
│   ├── orchestrator.py             5. Orchestrator
│   ├── web.py              §4.4 approval UI
│   ├── cash_ledger.py      §7 working capital
│   ├── monitoring.py       §8 alerts, §14 scorecard and stop conditions
│   ├── metrics.py          §5.4 ingestion
│   └── sources/            platform adapters
├── court/                  the File Court — see VILLAGE.md
│   ├── backends.py         one class per reviewer
│   ├── file_court.py       escalation, verdicts, the docket
│   └── watcher.py          uploads/ -> review -> processed/
├── trading/                the Trading Edition — see TRADING.md
│   ├── firms/              analysts, bull/bear, trader, risk, kill switch
│   ├── brokerage/          reconcile, evaluate, allocate, rank, KILL_ALL
│   ├── brain/              memory, genome evolution, lessons
│   ├── heart/              six moral foundations, allow/warn/block
│   ├── court/              strategy trials: evidence, jury, advocates, judge
│   ├── competition/        tokens, titles, bouts
│   ├── black_market/       genome licences; capital only via an approval
│   ├── sandbox/            alliances, betrayal — read-only over the ledger
│   ├── data/ execution/ gateway/ audit/
│   ├── web.py              Mission Control, mounted at /village
│   ├── api.py              the same numbers as read-only JSON
│   ├── static/solar.html   the firms as a solar system (vendored, MIT)
│   ├── council/            the village ruling on its own pending decisions
│   ├── flow.py             the village map, and the tick's telemetry
│   ├── snapshot.py         the same village, frozen into one HTML file
│   ├── recruit.py          drop a bot in; a cleared file becomes a firm
│   ├── importer.py         adapt bots written for something else
│   └── ecosystem.py        the tick, in order
└── db/
    ├── connection.py
    └── migrations/         001-006 products, 007-016 trading; Postgres + sqlite/
```

Deliberately **not** built (spec §2.1): sharks, government, alliances, black
market, marketing team, territory, gamification, risk management beyond kill
criteria, backup/recovery beyond retries, and any agent beyond the five.

The **court system** was on that list and has since been built — see
[VILLAGE.md](VILLAGE.md). It reviews files and writes to its own `file_cases`
table. It does not touch experiments, budgets or approvals: every dollar still
goes through the human gate of §6.

The **Trading Edition** has since been built too — see [TRADING.md](TRADING.md).
Competing trading firms under a brokerage that cuts the losers, with the same
asymmetry in front of every decision: it may always close a position, pause a
firm or take capital back on its own, and it may never open a live order or
raise an allocation without an approved row in the same `human_approvals`
table. It trades on a paper venue by default and shares nothing with the
product village except the database, the gate and the principle.

```bash
python -m src.main trade init
python -m src.main trade simulate --days 120
python -m src.main trade leaderboard
python -m src.main serve          # gate on /, Mission Control on /village
python -m src.main trade dashboard  # or freeze it into one shareable file

TRADE_AUTONOMY=council python -m src.main trade run   # and let it run itself
```

And there is a third thing in here, standalone and much smaller: the
**[Evolving Hive-Mind Trader](hive_mind/README.md)** in `hive_mind/`. Five
scouts, a council that votes, and a genome that evolves — wrapped in a
walk-forward lock that will not let it near real money until it has survived
data it was structurally prevented from reading. It is **not** part of the
village — no database, no ledger, no firms, no gate, and nothing under `src/`
imports it — but it reuses the village's arithmetic rather than growing a
second copy: `src/money.py`, `src/trading/indicators.py`, the `Bar` and `Fill`
models, and the paper venue's fill costs.

```bash
python -m hive_mind                        # watch it think, one day at a time
python -m hive_mind --lock --overfit-demo  # watch it be refused, and see why
python -m hive_mind.crucible_real          # and the same, against real SPY history
```

---

## The loop

One `tick` is the whole business. Each step is marked with who may do it.

```
1. DISCOVER   scout -> economic filter -> ledger              autonomous
2. ADVANCE    act on approvals a human already granted        gated
3. MEASURE    pull metrics, recompute, book the cash          autonomous
4. EVALUATE   kill logic -> pause ads -> ask a human          pause autonomous
                                                              kill gated
5. SCALE      propose budget increases                        gated
6. REPORT     health check + four stop conditions             autonomous
```

The asymmetry in step 4 is the design: **the system may always stop the
bleeding, and may never start it.** A kill trigger pauses ads within the same
tick, without asking. The kill itself is a row a human has to change.

---

## Kill criteria (§4)

### Sample-size gates — no decision without data

| Gate | Threshold |
|------|-----------|
| Impressions | 1,000 |
| Sessions | 100 |
| Orders | 50 |

All three, no partial credit. Below the gates the answer is
`(False, "Insufficient data")` — not a kill, and not a clean bill of health
either. This is the most important behaviour in the codebase and it has its
own test: an experiment with 5 orders, 1 click and 3 refunds is *not* killed,
because none of those numbers mean anything yet.

### Kill conditions, evaluated in this order

| # | Condition | Trigger |
|---|-----------|---------|
| 1 | CTR | < 0.5% |
| 2 | Conversion rate | < 1% |
| 3 | CAC | > selling price × 0.5 |
| 4 | Contribution margin | ≤ 0 |
| 5 | Refund rate | > 8% |
| 6 | Chargeback rate | > 1% |
| 7 | Avg delivery | > 10 days |

The order is fixed so the same data always produces the same recorded reason.
`show <id>` and the web REVIEW_DATA page print every condition with its value
and whether it fired — the operator approving a kill sees the whole table, not
just the first trip.

---

## The human gate (§6)

| Action | Approval | `human_approvals.action` |
|--------|----------|--------------------------|
| Spending money on ads | required | `ad_spend` |
| Ordering samples | required | `sample_order` |
| Listing a product | required | `launch` |
| Killing a product | required | `kill` |
| Scaling beyond $50/day | required, separately | `scale` |
| Adding a platform | required | `add_platform` |

Autonomous: scraping, calculating, monitoring, **pausing** ads, writing the
ledger.

Properties the tests pin down:

* An approval for $10/day does **not** authorise $80/day. The stored amount is
  a ceiling.
* An approval for one experiment does not cover another.
* Approving ad spend does not approve a kill.
* A decision cannot be made twice.
* Duplicate requests do not re-notify — an operator who is spammed stops
  reading.
* A stale launch approval cannot silently restart ads that a kill trigger
  paused. Restarting spend is explicit: `resume <id> --approval-id <n>
  --budget <x>`.
* The web tier can only *decide*. Every route writes an approval row through
  the same `HumanGate` the CLI uses; none of them spends money or kills
  anything. The orchestrator acts on the decision on its next tick.

### CONTINUE is not a snooze

When the operator answers CONTINUE, the system records *which condition* was
overridden and stops re-escalating that exact trigger. Otherwise the same
alert fires every hour on unchanged data and the operator learns to ignore it.
A *different* condition still escalates, the health check still reports the
condition as failing, and ads stay paused until a separate spend approval
resumes them.

---

## Cash, not profit (§7)

Real stores fail from cash. The ledger tracks three numbers and never confuses
them:

* **total balance** — everything on the books. The accounting number.
* **available cash** — what has actually settled. Held money does not count
  until its release date.
* **spendable** — available cash minus the untouchable emergency buffer. The
  only number a spend decision may look at.

A customer payment splits into a settling amount, a processor rolling reserve
and an immediate fee debit. So a store showing $600 on the books correctly
refuses a $100 ad buy — the failure this module exists to prevent.

All money is `Decimal`, summed in Python, never in SQL (SQLite's NUMERIC
affinity would store it as a float).

---

## Stop conditions (§1.3, §14.3)

Four, independent. **Any one ends Phase 1.** `health` prints all four and
exits 2 if any fired, so cron or CI can act on it.

| # | Condition | Measured from |
|---|-----------|---------------|
| 1 | After 20 experiments, not one has a positive contribution margin | the ledger, **per experiment** |
| 2 | Cash flow negative for 7 consecutive days | replaying the cash ledger day by day |
| 3 | A kill decision unanswered for more than 72 hours | `human_approvals.requested_at` |
| 4 | Ad costs > 2× revenue over the trailing 30 days | the cash ledger |

Two readings worth stating, since the spec's prose leaves them open:

* **#1 is per-experiment, not store-total** (spec §14.3's code says
  `[e for e in experiments if e.contribution_margin > 0]`). One genuine winner
  among twenty means the system works and the losers were the cost of finding
  it.
* **#4 requires 30 days of history before it can fire.** Otherwise a store
  three days old trips it before making its first sale.

Condition 3 exists because the gate is the whole safety model. A gate nobody
is watching stops the system rather than quietly becoming decoration.

`weekly` reports the §1.2 scorecard: experiments run, data quality, kill
accuracy, cash survival, **profitable products found**, store P&L. Kill
accuracy is measured honestly — of the killed experiments that had enough data
to judge, how many were genuinely losing money. A product killed while
profitable counts against the system.

---

## What is real, and what is not

Being straight about this matters more than a longer feature list.

**Fully implemented and tested:** the schema and migrations, the domain model
and all derived metrics, the kill criteria, the economic calculator, the
experiment ledger with its audit trail, the cash ledger, the human approval
gate (CLI and web), the monitoring, scorecard and all four stop conditions,
the orchestrator loop, the CLI.

**Deliberately stubbed, with a working seam:**

* **Platform connectors.** `src/agents/sources/aliexpress.py` and `temu.py`
  implement the payload → candidate mapping (unit-tested, including Temu's
  minor-unit prices) and credential handling. `fetch_raw()` raises
  `SourceNotConfigured` with a precise message. DSers' product API needs an
  authenticated merchant account, and a scraper written against a page
  structure nobody has looked at would be a plausible-looking lie rather than
  a component. To go live, implement `fetch_raw()` or pass
  `transport=<callable>`.
* **Metrics ingestion.** No Meta/TikTok/Shopify credentials exist here, so
  metrics come from `metrics` (typed in) or a JSON file
  (`--metrics-source file`). Copying seven numbers a day out of a dashboard is
  a legitimate way to run Phase 1 — cheaper than an integration for a system
  whose point is to be killable. A real provider implements one method
  (`fetch`).
* **Ad pausing** updates the ledger's `ads_paused` flag. Wiring it to the ad
  platform is one call inside `ExperimentLedger.pause_ads`; until then the
  operator pauses in the dashboard when the alert arrives. The alert says so.

The fixture scout (`data/products.sample.json`) is not a placeholder — it is
how the loop is meant to run while the connectors are unbuilt. Paste in
products you found by hand and the machine treats them like scraped ones.

**Security note on the web gate:** it has no authentication. Anyone who can
reach the page can approve spending. It binds to `127.0.0.1` by default and
warns on stderr if you bind it wider. Put your own auth in front of it before
exposing it.

---

## Command reference

| Command | What it does |
|---------|--------------|
| `init-db` | Apply migrations 001-005 |
| `config` | Print the effective configuration — the whole risk surface |
| `scout` | Discover and price candidates. **Writes nothing** |
| `tick` / `run` | One pass of the loop / forever |
| `serve` | The web approval gate (§4.4 buttons) |
| `list` / `show <id>` | Experiments; full audit view of one |
| `metrics <id> --orders N ...` | Record observed metrics |
| `evaluate` | Run the kill logic now |
| `health` | Daily health check + all four stop conditions (exit 2 if any fired) |
| `weekly` | Weekly summary and the Phase 1 scorecard |
| `approvals` / `approve <id>` / `reject <id>` | The human decision. On a kill request these are KILL and CONTINUE |
| `resume <id> --approval-id <n> --budget <x>` | Restart paused ads |
| `cash open --once` / `cash add` / `cash` | The cash flow ledger |
| `order <id> --customer-paid X` | Record a real order and its cash consequences |
| `recompute <id>` | Rebuild order metrics from the orders table |
| `export <table> --out f.csv` | Export for analysis (pandas, falls back to stdlib csv) |

Scripts (§12): `scripts/start.sh` (migrations + balance + gate),
`scripts/reset_db.sh` (destroys everything, asks first),
`scripts/run_loop.sh [once|loop|health]`.

---

## Implementation notes

Deviations from the spec's sample code, each on purpose:

1. **`Decimal`, not `float`.** The spec's dataclass uses floats. Money that
   drifts by fractions of a cent is not auditable. Derived metrics are
   quantized to the precision they are stored at, so the number that triggers
   a kill is the number in the ledger.
2. **The loop does not block on approvals by default.** The spec's skeleton
   calls `gate.wait_for_decision()` inline. One unanswered notification would
   then stop the system monitoring every *other* experiment. Default: pause
   ads, file the request, keep going — and stop condition 3 catches the
   request nobody answered. `MVV_BLOCK_ON_APPROVAL=true` restores the spec's
   behaviour; `wait_for_decision()` is implemented either way.
3. **COGS includes shipping.** A supplier listing $5 + $2 shipping costs $7.
   Discovery stores landed cost as `unit_cost`, so contribution margin is not
   quietly overstated on every experiment.
4. **Order rows and typed-in totals are alternatives, not layers.** `order`
   records an order and its cash but leaves aggregate metrics alone;
   recomputing from a partial orders table would overwrite 55
   dashboard-sourced orders with the 1 row that exists, and the kill logic
   runs on those numbers. `recompute` does it explicitly and refuses to lower
   the order count without `--force`.
5. **`human_approvals` keeps the spec's column names**, including
   `approved_at` / `approved_by`. Those record when a decision was made and by
   whom *whatever the outcome*; `status` distinguishes approved from rejected,
   so a rejection is timestamped and attributed rather than losing its author.
6. **`requirements.txt` is the spec's list**, with a note per line on what
   each package is for. `pandas`/`numpy` serve `export` and reporting only —
   money arithmetic deliberately does not go through them. The core loop
   itself imports nothing outside the standard library, so the thing that
   decides whether to spend money has no supply chain.

Four layers now sit above that ledger, none of them on the tick's critical
path: a **strategy court** that tries a dropped file with twelve deterministic
jurors, a **competition** of tokens and bouts, a **black market** where firms
license genomes, and a **sandbox** for alliances and betrayal that holds a
read-only view of the books. Spec §2.1 ruled out the black market and
gamification; that call was reversed deliberately, and the fences are
documented in [TRADING.md](TRADING.md).
