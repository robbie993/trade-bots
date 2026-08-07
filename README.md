# Minimum Viable Village — Phase 1: The Experiment Ledger

The smallest system that can answer one question:

> Can this system consistently find, test, market, fulfill and manage
> profitable products with real money after **all** costs?

If yes, scale it. If no, kill it. This repository is built so that either
answer is cheap to reach and impossible to fudge.

No Monte Carlo. No 68 agents. No $1M/month claims. Five components, concrete
kill thresholds, a human gate in front of every dollar, and a stop condition
that lets the system conclude it does not work.

---

## Quick start

```bash
git clone <this repo> && cd trade-bots
pip install -e '.[dev]'          # no runtime deps; pytest for the suite
python -m mvv init-db            # SQLite at ./data/mvv.db by default
python -m mvv cash open          # seed the $1,000 budget
python -m mvv tick               # discover -> ask permission -> report
python -m mvv approvals          # see what it is waiting on
python -m mvv approve 1 --by you
python -m mvv tick
```

Run the tests:

```bash
python -m pytest            # 185 tests, no database server, no network
```

---

## The five components

| # | Component | Module | Job |
|---|-----------|--------|-----|
| 1 | Scout | `mvv/scout/` | Discover products from one or two platforms |
| 2 | Economic Calculator | `mvv/economics.py` | Unit economics, margin, CAC, breakeven |
| 3 | Experiment Ledger | `mvv/ledger.py` | The single source of truth |
| 4 | Human Approval Gate | `mvv/human_gate.py` | Hard boundary for spend |
| 5 | Orchestrator | `mvv/orchestrator.py` | Coordinate the loop |

Supporting: `kill_criteria.py` (the thresholds), `cash.py` (working capital),
`monitoring.py` (alerts and reports), `metrics.py` (ingestion), `models.py`,
`db.py`, `money.py`, `cli.py`.

Deliberately **not** built: court system, sharks, government, alliances,
black market, marketing team, territory, gamification, risk management beyond
kill criteria, backup/recovery beyond retries.

---

## The loop

One `mvv tick` is the whole business. Each step is marked with who is allowed
to do it.

```
1. DISCOVER   scout -> economic filter -> ledger              autonomous
2. ADVANCE    act on approvals a human already granted        gated
3. MEASURE    pull metrics, recompute, book the cash          autonomous
4. EVALUATE   kill logic -> pause ads -> ask a human          pause autonomous
                                                              kill gated
5. SCALE      propose budget increases                        gated
6. REPORT     health check + stop condition                   autonomous
```

The asymmetry in step 4 is the design: **the system may always stop the
bleeding, and may never start it.** A kill trigger pauses ads within the same
tick, without asking. The kill itself is a row a human has to change.

---

## Kill criteria (spec section 4)

### Sample-size gates — no decision without data

| Gate | Threshold |
|------|-----------|
| Impressions | 1,000 |
| Sessions | 100 |
| Orders | 50 |

All three, no partial credit. Below the gates the answer is
`(False, "Insufficient data")` — not a kill, and not a clean bill of health
either. This is the single most important behaviour in the codebase and it has
its own test: an experiment with 5 orders, 1 click and 3 refunds is *not*
killed, because none of those numbers mean anything yet.

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
`mvv show <id>` prints every condition with its value and whether it fired —
the operator approving a kill sees the whole table, not just the first trip.

Thresholds live in `KillThresholds` and `SampleGate` (`mvv/kill_criteria.py`)
and are configurable, defaulting to exactly the values above.

---

## The human gate (spec section 6)

| Action | Approval | Enforced by |
|--------|----------|-------------|
| Spending money on ads | required | `ApprovalAction.AD_SPEND` |
| Ordering samples | required | `ORDER_SAMPLE` |
| Listing a product | required | `LIST_PRODUCT` |
| Killing a product | required | `KILL_EXPERIMENT` |
| Scaling beyond $50/day | required, separately | `SCALE_AD_SPEND` |
| Adding a platform | required | `ADD_PLATFORM` |

| Action | Autonomy |
|--------|----------|
| Scraping product data | full — costs nothing |
| Calculating metrics | full — math, not decisions |
| Monitoring kill conditions | full — alerting only |
| Pausing ads on a trigger | full — stops the bleeding |
| Writing the ledger | full — logs everything |

Properties the tests pin down:

* An approval for $10/day does **not** authorise $80/day. The stored amount is
  a ceiling.
* An approval for one experiment does not cover another.
* Approving ad spend does not approve a kill.
* A decision cannot be made twice.
* Duplicate requests do not re-notify — an operator who is spammed stops
  reading.
* A stale launch approval cannot silently restart ads that a kill trigger
  paused. Restarting spend is an explicit act: `mvv resume <id> --approval-id
  <n> --budget <x>`.

### CONTINUE is not a snooze

When the operator answers CONTINUE to a kill trigger, the system records
*which condition* was overridden and stops re-escalating that exact trigger.
Otherwise the same alert fires every hour on unchanged data and the operator
learns to ignore it. A *different* kill condition still escalates normally,
the daily health check still reports the condition as failing, and ads stay
paused until a separate spend approval resumes them.

---

## Cash, not profit (spec section 7)

Real stores fail from cash. The ledger tracks three different numbers and
never confuses them:

* **total balance** — everything on the books. The accounting number.
* **available cash** — what has actually settled. Held money does not count
  until its release date passes.
* **spendable** — available cash minus the untouchable emergency buffer.
  This is the only number a spend decision may look at.

A customer payment is split into a settling amount (released after
`settlement_days`), a processor rolling reserve (released after
`processor_hold_days`) and an immediate fee debit. So a store showing $600 on
the books can correctly refuse a $100 ad buy, which is the failure this module
exists to prevent.

All money is `Decimal`. No binary float ever touches a cash figure — see
`mvv/money.py`. Money is summed in Python, never in SQL, because SQLite's
NUMERIC affinity would store it as a float.

---

## Stop condition (spec section 9)

After 20 experiments, if the store's contribution margin is not positive, the
system files a `KILL_ALL` request, pauses every live experiment and tells the
operator to stop. `mvv health` exits with status 2 when this fires, so a cron
job or CI check can act on it.

`mvv weekly` reports where Phase 1 stands against its own bar: experiments
run, data quality, kill-decision accuracy, cash survival, store
profitability.

Kill-decision accuracy is measured honestly: of the killed experiments that
had enough data to judge, how many were genuinely losing money. A product
killed while profitable counts against the system.

---

## What is real, and what is not

Being straight about this matters more than a longer feature list.

**Fully implemented and tested:** the database schema, the domain model and
all derived metrics, the kill criteria, the economic calculator, the
experiment ledger with its audit trail, the cash flow ledger, the human
approval gate, the monitoring and reports, the orchestrator loop, the CLI.

**Deliberately stubbed, with a working seam:**

* **Platform connectors.** `mvv/scout/aliexpress.py` and `mvv/scout/temu.py`
  implement the payload → candidate mapping (unit-tested, including Temu's
  minor-unit prices) and the credential handling. `fetch_raw()` raises
  `SourceNotConfigured` with a precise message. DSers' product API needs an
  authenticated merchant account, and a scraper written against a page
  structure nobody has looked at would be a plausible-looking lie rather than
  a component. To go live, implement `fetch_raw()` or pass
  `transport=<callable>` — everything downstream already works.
* **Metrics ingestion.** No Meta/TikTok/Shopify credentials exist here, so
  metrics come from `mvv metrics` (typed in) or a JSON file
  (`--metrics-source file`). Copying seven numbers a day out of a dashboard
  is a legitimate way to run Phase 1 — cheaper than an integration for a
  system whose whole point is to be killable. A real provider implements one
  method (`fetch`) and nothing else changes.
* **Ad pausing** updates the ledger's `ads_paused` flag. Wiring that to the
  ad platform's API is one call inside `ExperimentLedger.pause_ads`; until
  then the operator must pause in the dashboard when the alert arrives. The
  alert says so.

The fixture scout (`data/products.sample.json`) is not a placeholder to be
replaced later — it is how the loop is meant to run while the connectors are
unbuilt. Paste in products you found by hand and the rest of the machine
treats them exactly like scraped ones.

---

## Database

The spec names PostgreSQL as the source of truth, and it is: point
`DATABASE_URL` at Postgres and `db/schema.postgres.sql` is applied verbatim.
SQLite is the zero-setup default, because a Phase 1 system whose tests need a
database server does not get run. Both schemas are kept in lockstep by a test
that compares their tables and columns.

Tables 1–3 are the spec schema (sections 3.1–3.3) with additive columns only.
Two tables are Phase 1 additions:

* `approvals` — required by section 6. The gate has to be a row somewhere.
* `experiment_events` — an append-only audit trail. Every state change writes
  one. "Auditable" is a requirement, not an adjective.

Additive columns on `experiments`: `product_url`, `ads_paused`,
`daily_ad_budget`, `pending_kill_reason`, `kill_override_reason`. On `orders`:
`external_id`. On `cash_flow`: `experiment_id`, `order_id`.

---

## Command reference

| Command | What it does |
|---------|--------------|
| `init-db` | Create the schema |
| `config` | Print the effective configuration — the whole risk surface |
| `scout` | Discover and price candidates. **Writes nothing** |
| `tick` | One pass of the loop |
| `run` | The loop, forever |
| `list` | All experiments with their live metrics and flags |
| `show <id>` | Full audit view: gates, every kill condition, event history |
| `metrics <id> --orders N ...` | Record observed metrics |
| `evaluate` | Run the kill logic now |
| `health` | Daily health check (exit 2 if `KILL_ALL` fires) |
| `weekly` | Weekly summary and Phase 1 scorecard |
| `approvals` | Pending requests |
| `approve <id>` / `reject <id>` | The human decision. On a kill request these are the KILL and CONTINUE buttons |
| `resume <id> --approval-id <n> --budget <x>` | Restart paused ads |
| `cash open` / `cash add` / `cash` | The cash flow ledger |
| `order <id> --customer-paid X` | Record a real order and its cash consequences |
| `recompute <id>` | Rebuild order metrics from the orders table |

---

## Configuration

Everything is environment-driven with safe defaults; see `.env.example` and
`mvv/config.py`. All tunables live in that one file so the risk surface can be
read in a single sitting.

---

## Implementation notes

Three places where this deviates from the spec's sample code, each on purpose:

1. **`Decimal`, not `float`.** The spec's dataclass uses floats. Money that
   drifts by fractions of a cent is not auditable. Derived metrics are
   quantized to the precision they are stored at, so the number that triggers
   a kill is the number in the ledger.
2. **The loop does not block on approvals by default.** The spec's skeleton
   calls `gate.wait_for_decision()` inline. One unanswered notification would
   then stop the system monitoring every *other* experiment — a worse failure
   than a delayed kill. Default: pause ads, file the request, keep going. Set
   `MVV_BLOCK_ON_APPROVAL=true` for the spec's behaviour;
   `wait_for_decision()` is implemented either way.
3. **COGS includes shipping.** A supplier listing $5 + $2 shipping costs $7.
   Discovery stores landed cost as `unit_cost`, so contribution margin is not
   quietly overstated on every experiment.

And one rule that only shows up once you run it: **order rows and typed-in
totals are alternatives, not layers.** `mvv order` records an order and its
cash consequences but leaves the aggregate metrics alone, because recomputing
from a partial orders table would overwrite 55 dashboard-sourced orders with
the 1 row that happens to exist — and the kill logic runs on those numbers.
`mvv recompute` does it explicitly, and refuses to lower the order count
without `--force`.
