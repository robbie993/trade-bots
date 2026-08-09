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

┌──────────────────────────────────────────────────────────────────────────┐
│  ABOVE THE LEDGER — none of it on the tick's critical path                │
│                                                                          │
│  COURT    drop a strategy file · twelve deterministic jurors · a ruling   │
│  ARENA    tokens, titles, bouts — points, never capital                   │
│  MARKET   genome licences for tokens · capital only via an approval       │
│  SANDBOX  alliances, betrayal, espionage — read-only over the ledger      │
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

By default, this is the split. Everything in the right-hand column can be
handed to **the council** instead — see the next section; the last row is the
one it may never touch.

| Autonomous | Needs a human |
|---|---|
| Read data, analyse, debate, propose | Send an order to any live venue |
| Trade on the **paper** venue | Raise a firm's allocation |
| **Cut** a firm's allocation | Kill a firm for good |
| Pause a firm that trips its kill switch | Un-pause a firm |
| Close positions, hand cash back | Add a new live venue |
| Halt the entire ecosystem | Transfer capital between firms |
| Try a strategy file and rule on it | Deploy a genome the court accepted |
| Award tokens, run bouts, plot and betray | — |

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

## The council — letting it run itself

The table above is the default. If you would rather not sit in front of it
approving things, the village has a body that can decide most of them:

```bash
export TRADE_AUTONOMY=council
python -m src.main trade autonomy      # what it may and may not decide
python -m src.main trade run           # and leave it
```

The council is the strategy court's machinery pointed at the approval queue —
deterministic jurors over evidence, weighted, with vetoes. It reaches one of
three verdicts, and the third is the one that matters:

| | |
|---|---|
| **GRANT** | the panel carried it; the tick carries it out on the spot |
| **REFUSE** | the panel rejected it, with its reasons |
| **DEFER** | the evidence does not settle it — it stays pending, for you |

A body that can only say yes or no will say one of them when it should have
said neither. `DEFER` is what stops this being a rubber stamp, and it is why
switching autonomy on does not mean you are never asked anything: it means you
are only asked about the cases the ledger does not answer.

### What it decides on

It reads the ledger, never the request. A request that says a firm is
wonderful does not make it so — the evidence is gathered fresh from the same
tables the brokerage reads, and there is a test that asserts a flattering
request changes nothing.

**Three vetoes outrank every other consideration**, because each is a fact no
amount of good performance argues with:

* the books do not reconcile
* the firm is below the sample gate — *insufficient data is not a reason to act*
* the raise would breach the brokerage's total capital, or the firm's own ceiling

**And one veto exists purely to prevent the failure the human gate was there
for.** A council that grants the same firm a raise every tick is compounding,
and it compounds fastest into whichever firm is currently winning. After three
recent raises to one firm, the fourth goes to you.

The sample gate has one deliberate exception, and it is the same exception
`should_kill_firm` already makes: drawdown, a catastrophic single loss and
consecutive losses are checked *before* the gate, because they are not
statistical claims about an edge — they are the account being emptied.
Demanding twenty trades before acting on a 40% drawdown would reproduce
exactly the mistake the kill switch was written to avoid.

### What it may never decide

**Sending an order to a live venue.** That is the one act in this system that
leaves it: irreversible, outside the ledger, real money sitting at a real
broker. Everything else the council rules on moves numbers between rows of a
database that reconciles, and can be read back and undone.

This is not a setting. `live_trading` has no panel of jurors, so there is no
code path that reaches a verdict on it however the configuration is written,
and there is a test asserting it stays that way.

### Nothing goes round the gate

The council does not get a private path into the ledger. Every decision is
still a row in `human_approvals`, still granted through `HumanGate.approve`,
still carried out by `apply_approvals` — the only thing that changes is who
signs the row, and the signature says `the council` rather than your name.
A second, quieter route into the books is exactly the kind of thing that makes
an audit trail worth nothing.

Every ruling is written to `council_rulings` with each juror's reasoning and
the state of the ledger it was decided on:

```bash
python -m src.main trade council           # the rulings
python -m src.main trade council-case 1    # one ruling, juror by juror
```

That table stores the evidence as JSON, and the jurors are pure functions of
it — so a stored ruling replays to the stored verdict months later. There is a
test that does exactly that, because "the machine decided" is not an answer.

### It stays quiet

While the council is sitting, a request it is about to grant no longer pages
you. Only a **deferred** one does — the outcome that actually needs a person.
Live trading and the whole product village notify exactly as they always did.

### It also lets firms back in

Pausing is autonomous because it stops the bleeding; starting again is the
other direction, so it goes through the gate like everything else. Without
somebody able to grant it, a long run only ever empties the village out — so a
paused firm whose condition has cleared asks to resume, and the council rules
on that too.

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

### Seeing it — the vault as a graph

The database is where memory is *kept*; the vault is where it can be *read*.
Every tick writes the brain into `vault/brain/` as plain Markdown:

```
vault/brain/
  index.md
  lessons/SPY-loser.md          a conclusion, and when it was drawn
  symbols/SPY.md                what the trades in that name actually say
  genomes/g00042.md             one candidate strategy, and its parent
```

These are cross-linked, and **the links are relationships out of the
database, not decoration**. A lesson links to the symbol it is about; the
symbol links back to every firm that traded it; a genome links to the genome
it was mutated from. That last one is the interesting edge: `parent_id` now
records which genome each mutant descends from, so a firm's strategy has a
recoverable lineage instead of an undated pile of candidates.

Which means you get the graph view for free, with nothing to install and no
data leaving the machine:

```bash
python -m src.main trade audit --write     # refresh the brain notes
```

Then open `vault/` as an Obsidian vault and use its **Graph View** — lessons,
symbols, firms and genome lineages as one network. The
[obsidian-galaxy](https://github.com/agentage/obsidian-galaxy) plugin renders
the same thing as a 3D force graph if you want the galaxy version. Neither
needs an integration, because the vault was already the right shape.

**Records are appended; views are regenerated, and they live in different
folders.** `firms/`, `brokerage/` and `brain/lessons/` are append-only — a
decision that has been written cannot be quietly amended, and a lesson's note
gains a dated entry each time it is re-concluded, so you can see *when* the
village came to believe something. `brain/symbols/` and `brain/genomes/` are
rebuilt from the database: a symbol's track record moves with every closed
trade, and regenerating it loses nothing because the trades it counts are
recorded in the firm notes. Every regenerated note says so at the top.

A symbol note also separates fills from closed trades, because "20 trades
(1 won, 1 lost)" reads as though eighteen went missing. They did not — an
opening buy is remembered with a realised P&L of zero, and only the closing
leg of a round trip settles.

Writing the vault is best-effort, like the flow recorder: a tick does not fail
because a note could not be written.

---

## Mission Control

Everything above on one page, at `/village`:

```bash
python -m src.main serve          # gate on /, Mission Control on /village
```

Six panels — ecosystem health and reconciliation, firms, brokerage and
allocations, the court docket, the token standings, the market and the
sandbox — plus a firm page with its live kill table, positions and recent
proposals, and a case page showing every juror's finding.

The buttons do what the equivalent CLI command does and **no more**: run a
tick, run a season, drop a strategy file in, list and buy with tokens, form an
alliance, betray one. Anything that would move capital writes an approval
request and stops, exactly as it does from a terminal. The page has no
privileged path into the ledger, and the two places a decision is granted are
still the gate at `/` and `mvv approve`.

Server-rendered HTML, no build step, no framework, no external service. Every
number is a `SELECT` away in the same database the CLI reads. It is worth
saying why that matters rather than treating it as a style preference: a
dashboard that needs a gateway, a WebSocket bus and an npm install to render a
table is three more things that can be down at the moment you want to know
whether a firm is bleeding.

Uploaded strategy files go through exactly the same court as the CLI — read
with `ast`, never executed. There is a test that uploads a file which would
write a marker on import and asserts the marker never appears.

### The village map

`/village/flow` is the village, drawn as a village: fifteen buildings, the
roads between them, and a little person walking a road for every proposal,
fill and refusal that actually happened.

```
 Market  →  Firm     →  Temple  →  Trading  →  Counting  →  Library
 well       quarter                post        house           |
             | | |         ↓          ↓                        ↓
 Arena ←─────┘ | └──→ The pound  The pound     Town hall ←──────┘
               |                                 |  |  ↓
 Courthouse ←──┤                    Gatehouse ←──┘  |  Archive
      └────────┼──────────→ Library                 ↓
 Bazaar  ←─────┤                                 Bell tower
      └────────┼──────────→ Gatehouse
 Tavern  ←─────┘
```

Six of them are the tick, in order along the top road:

| Building | What goes on inside |
| --- | --- |
| Market well | prices arrive, up to the cursor and never past it |
| Firm quarter | analysts, the bull/bear debate, the trader, the risk manager |
| Temple | the six moral foundations, consulted before anything executes |
| Trading post | paper by default; live venues turn orders away without approval |
| Counting house | position, cash and P&L move together or not at all |
| Library | remembers the fill and the argument that produced it |

Five are what happens to a tick's output: **the pound** (refused proposals
stop there), **the gatehouse** (kills and capital raises wait — for you, or
for the council), **the town hall** (reconcile, score, kill, allocate), **the
archive** (the Obsidian vault) and **the bell tower** (`KILL_ALL`).

Four sit off the main road because none of them is on the tick's critical
path: the **courthouse** (a dropped strategy file, twelve jurors, a ruling),
the **arena** (bouts, milestones, tokens), the **bazaar** (genome licences —
and capital only via the gatehouse) and the **tavern** (alliances, betrayal,
espionage, none of which reaches the ledger).

Three things make it worth looking at rather than decorative:

**The villagers are real.** Each walker is a row `Ecosystem.tick()` wrote as
it happened, read back from `flow_events`, and it walks the road its event
travelled. Nothing loops on a timer — when the village is quiet, the roads are
empty, and that is information.

The figures standing in the yard below the firm quarter are the firms
themselves, one each, and their posture is their status: a paused firm sits
one out, a killed firm has faded.

**Blocked things leave the road.** A proposal the risk manager or the
conscience refused walks to *the pound* and stops. It does not carry on to the
trading post in a different colour. Kills and capital raises walk to the
*gatehouse*, which is where they wait for you. An animation where everything
reaches the end is an expensive way to learn nothing.

**Telemetry can never break a tick.** `FlowRecorder.emit` swallows its own
errors, and there is a test that drops the `flow_events` table mid-run and
asserts the tick still completes with no errors. The ledger does not read this
table and the recorder never checks whether its write succeeded.

It goes through the database rather than an in-process queue, because the
usual arrangement is `trade run` in one terminal and the dashboard in another
— two processes. A queue inside the web server would render an empty diagram
in exactly the case the diagram exists for. The page polls; the table keeps
its most recent 400 rows and prunes itself.

There is no authentication. Bind it to localhost.

### Taking a copy of it

```bash
python -m src.main trade dashboard --out dashboard/village.html
```

That freezes Mission Control and the village map into **one self-contained
HTML file** — no server, no build step, no network, no CDN. Open it by
double-clicking it, email it, put it in a bucket, commit it.

It is the same code that renders the live pages, run once, which is the only
reason it is worth having: a second dashboard written by hand would be a
second set of numbers to keep true, and this one cannot drift from `/village`
because it *is* `/village`.

Freezing costs two things, and the file says both out loud:

**The buttons are gone — removed, not disabled.** A snapshot with a "Run a
tick" button that silently does nothing is worse than one with no button,
because it invites you to believe the page is live. Every form, button and
outbound link is stripped, and the banner says where the working controls
are. There is nothing in the file that could grant an approval, because there
is nothing in the file to grant it with.

**The villagers are a recording.** The live page asks the database what has
happened since the last poll; a file has nothing to ask, so the events travel
inside it and replay on load. They are still real — every one is a row the
tick wrote — but they are a recording of a moment, which is why the banner
carries the timestamp, the database and the data source. A number without its
as-of is a rumour.

`dashboard/` is in `.gitignore`. A snapshot of your own book is yours, not the
repository's.

There is a workflow, `.github/workflows/add-village-dashboard.yml`, that runs
the same command on GitHub and opens a PR with the result. The runner cannot
see your database, so it seeds one and simulates against the deterministic
synthetic feed: what it produces is a **demo**, labelled as one in the banner.
For your own numbers, run the command locally.

### The JSON API

Mission Control renders HTML because a dashboard needing a gateway and an npm
install to show a table is three more things that can be down when you want to
know whether a firm is bleeding. For everything that is *not* this browser —
another dashboard, a notebook, a status board on a spare monitor — the same
numbers are available as JSON:

| | |
|---|---|
| `GET /api/status` | the one-screen health check, including `reconciled` |
| `GET /api/firms` | every firm as the brokerage currently scores it |
| `GET /api/council` | recent rulings |
| `GET /api/tokens` | the standings |

**Read-only by construction, not by convention.** There is no POST in that
router and no route that takes a decision — there is a test that enumerates the
routes and asserts the method set is `{GET, HEAD}`. Capital still moves only
through the approval gate.

Two things it will not do to a number. **Money stays a string** — `"49448.85"`,
not `49448.85` — because JSON has no decimal type and every parser on the other
end will hand you a float, undoing the reason `src/money.py` exists. And
**`sufficient_data` rides along on every scorecard**, so a consumer can tell
"score 42 on four trades" from "score 42 on forty". Anything that drops that
flag and plots the number is drawing a conclusion the ledger refused to draw.

### The solar system

`/village/solar` is the same firms as planets: one per firm, sized by the
capital it holds, orbiting in rank order, colour by status — a killed firm
keeps its orbit and loses its colour. It polls `/api/firms` every fifteen
seconds.

The rendering engine is [solar-system-agents](https://github.com/Audazia/solar-system-agents)
(MIT), vendored into `src/trading/static/solar.html`. Four things changed:

* **The eight invented demo agents are gone.** A planet is a firm on the ledger
  or it is not drawn.
* **The polling is written here.** Upstream's README advertises "API Polling
  (built-in)" and ships a `gateway` config block, but the project contains no
  `fetch()`, `XMLHttpRequest` or `WebSocket` — none of it is implemented, and
  the gateway config is read by nothing.
* **An upstream `ReferenceError` is fixed.** A trail-drawing line reads `i`
  after the `let`-scoped loop that declared it, throwing inside
  `requestAnimationFrame` on every frame — which is why nothing orbits. The
  line was dead; its value was overwritten two lines later.
* **The Google Fonts import is removed.** It was the only outbound request on
  the page, and nothing else in this repository phones anywhere.

It is served by the app rather than opened as a file, so the fetch is
same-origin — a `file://` page polling localhost is a CORS request, and the
fixes for that are a browser flag or opening the API to arbitrary origins.

## Bringing your own bots

Drop a file in `bots/` and run:

```bash
python -m src.main trade recruit-watch --dir bots
```

A file the court clears becomes a **firm of its own** — its own capital, its
own kill switch, its own planet at `/village/solar`, its own resident in the
village map, its own line on the leaderboard. There is also
`trade recruit <file>` for one, and a **Recruit it as a firm** button on
Mission Control.

Three things hold, and they are the same three that hold everywhere else here.

**The trial is not optional.** A recruit goes through the same strategy court
as `court-submit`: parsed with `ast`, never imported, never executed. A file
that reaches for `socket`, evals at runtime, or cannot be parsed is refused
with reasons. There is no trusted flag that skips the jury, including for
files you wrote.

**A recruit is born paused and holding nothing.** Creating a funded, trading
firm from a file would be starting the bleeding. So the firm is created at
`paused` with an allocation of zero, and its capital is one
`ALLOCATE_CAPITAL` approval:

```bash
python -m src.main trade recruits          # who is waiting, and for how much
python -m src.main approve <id> --by you
python -m src.main trade apply-approvals   # funds it and sets it trading
```

**MODIFY does not recruit.** The court's middle verdict means it could not
tell. A body that reads "I don't know" as a yes is not worth having.

### When the council is sitting

It rules on recruits too, on a panel of its own — because a brand-new firm has
no track record, so the sample gate is the wrong question and would veto every
recruit forever. **The evidence for a recruit is its trial**: what the court
ruled, the backtest fitness, whether it named symbols, and whether the stake is
modest. Asking for more than half of the remaining capital is a veto: an
untried firm does not get that without you.

### What a bot has to declare

Two module-level literals. The court reads the syntax tree, so anything
computed at import time is invisible to it — and the file is refused rather
than run.

```python
GENOME = {
    "fast_window": 8, "slow_window": 60, "rsi_window": 14,
    "trend_bias": 88,        # 100 = pure momentum, 0 = pure mean reversion
    "value_window": 60, "fair_band_pct": 6, "calm_vol_pct": 30,
}
UNIVERSE = ["SPY", "QQQ"]
```

YAML and JSON work too, with `genome:` and `universe:` keys. `bots/` ships one
of each as a template, and `bots/README.md` has the full contract.

**One limit worth being plain about.** A firm here *is* a genome — the
parameters the built-in analysts read — not arbitrary code. The court will not
execute your file, so a bot whose edge lives in its own Python functions cannot
be run as-is; what carries over is the configuration. If your bots have real
logic you want in the village, either express the behaviour as a genome, or the
analyst roster needs to grow to cover what they do.

`bots/*` is gitignored apart from the examples: a strategy you wrote is yours,
and the drop box should not be a way to commit it by accident.

### Importing bots written for something else

`recruit` needs a file that already speaks the dialect. A bot you wrote last
year does not: it has `FAST_MA = 12`, `TICKERS = [...]`, and its edge in a
function called `should_buy`. So there is an adapter:

```bash
python -m src.main trade import ~/my-bots --dry-run   # read it, write nothing
python -m src.main trade import ~/my-bots             # writes into bots/
python -m src.main trade recruit-watch --dir bots
```

It never runs your code — parsed with `ast`, same as the court — and it prints
what it did with every value:

```
meme_sniper.py -> meme_sniper
  symbols   : DOGE-USD, SHIB-USD, PEPE-USD, BONK-USD
  guessed   : fast_window     <- FAST_MA   (alias — check it)
  guessed   : slow_window     <- SLOW_MA   (alias — check it)
  defaulted : fair_band_pct, trend_bias
  NOT CARRIED: 3 function(s)/class(es) — fetch_ohlcv, should_buy, MemeSniper
  ignored   : MIN_LIQUIDITY_USD, TAKE_PROFIT_PCT, STOP_LOSS_PCT
```

Four things it will not do:

**It will not pretend your logic came across.** A firm here is a genome. If the
edge lived in `should_buy`, that function is named under `NOT CARRIED` and it
is not here. An importer that emitted a plausible genome and let you believe
the strategy had been ported would be worse than one that refuses.

**It will not invent a number.** A gene the file never mentions is left at the
village default and listed under `defaulted`. A name matched by alias rather
than exactly is listed under `guessed`, for you to check.

**It will not copy a credential.** A file that looks like it holds an API key
is reported and nothing is written.

**It will tell you when the bot does something the village does not.** Options,
shorting, leverage and intraday bars each get a line. Those warnings match on
parameter *names* rather than the file text, because matching the text found
"short" inside `short_window` and "tick" inside `TICKERS` — and a confident
wrong warning teaches you to ignore the section that matters.

## Deploying it

The build error Vercel gives you is real and its suggested fix is the right
one — there are two FastAPI apps in the tree and it cannot pick. That is now
declared:

```toml
[tool.vercel]
entrypoint = "src.agents.web:app"
```

But making the build pass is the easy half. **A hosted deployment runs
read-only**, and that is not a limitation to work around — it is the point.

Every page here carries the same warning: *this page has no authentication;
bind it to localhost*. On a public URL that warning becomes load-bearing,
because the approval gate is the one hard boundary in front of every dollar
and it grants on an unauthenticated form post:

```
POST /approvals/3/approve      approved_by=web
```

So when the app detects a serverless host — `VERCEL`, `AWS_LAMBDA_*`,
`K_SERVICE`, `FLY_APP_NAME` and friends — it:

* refuses every non-`GET` request, **in middleware**, so it covers the gate,
  the village actions and any route added later without anyone remembering to
  guard it;
* **strips the forms and buttons from the HTML**, because a control that 403s
  when clicked is worse than no control;
* says on the page that it is a mirror, so nobody mistakes it for the console.

Reading still works — every page, plus the whole JSON API. Force it either way
with `MVV_PUBLIC=1` or `MVV_PUBLIC=0`.

This is not authentication. It is the *absence of a write surface*, which is
the part you can verify by reading `src/deploy.py`.

### It also needs a real database

Serverless filesystems are read-only apart from a per-instance `/tmp` that
disappears between requests, so the default SQLite file cannot work: every
tick, approval and fill would be written to storage that is about to vanish.
Attach Postgres. If you do not, **every page answers 503 with the reason**
before the handler runs — checked up front rather than after, because every
page opens the database to render, so a handler on a host with no writable
disk raises while connecting and there is no response left to put a banner in.
That is what `FUNCTION_INVOCATION_FAILED` was. The audit vault is off there for
the same reason.

Whether the database works is *probed*, not inferred from the URL. Deciding
from the scheme alone was wrong in both directions: it refused a perfectly
good SQLite file when previewing the mirror locally, and it would have served
pages against a Postgres URL that does not answer.

You do not have to name it `DATABASE_URL`. Managed add-ons each invent their
own variable and none of them is that one — Vercel Postgres and Neon set
`POSTGRES_URL`, Vercel's Prisma preset sets `POSTGRES_PRISMA_URL` — so those
are read too, and `postgres://` is normalised to the `postgresql://` psycopg
expects. `DATABASE_URL` still wins when you set it. Without this, attaching a
database appeared to do nothing and the app fell back to a SQLite file that
does not persist, which is the worst failure available because it looks like
it worked.

### Probably what you actually want

If the goal is to show somebody the village rather than to operate it from a
phone, there is a better answer than hosting the app:

```bash
python -m src.main trade dashboard --out dashboard/village.html
```

One self-contained file with real numbers, no server, no database, no write
surface to reason about — and Vercel serves static files perfectly. The
working console, with the buttons and the approvals, belongs on your machine.

---

## The strategy court

Drop a strategy file in and get a verdict you can check:

```bash
python -m src.main trade court-submit strategies/new_idea.yaml
python -m src.main trade court-docket
python -m src.main trade court-case 3     # juror by juror
```

**The file is read, never run.** `evidence.py` parses it with `ast` and
`json`/YAML; nothing in the package imports, execs or evaluates a submission.
That is not caution about crashes — it is code an outsider wrote arriving at a
system that moves money, and "run it and see" would hand it the process before
the court had said anything.

Twelve jurors, each a deterministic function of the file and one backtest, so
every line of a ruling can be re-derived from the stored case row. They split
into two kinds, and the split is what keeps the verdict honest:

| Kind | Jurors | Can vote |
|---|---|---|
| Hygiene | parses, imports, calls, genome_present, gene_ranges, universe, determinism | **against only** — a clean result abstains |
| Performance | return, drawdown, sample_size, kill_criteria, costs | either way |

"Imports nothing dangerous" is a prerequisite, not a merit. The first
calibration of the jury counted it for the defence, and a strategy that lost
4% was admitted on the strength of having tidy imports; hygiene jurors now
abstain when clean. Three of them are vetoes: a file that reaches for the
network, evaluates strings at runtime, or does not parse is refused however
well it backtests.

**ACCEPT is admission, not deployment.** An accepted strategy is written to
`strategy_genomes` *unselected*. It reaches a firm only by beating that firm's
incumbent in the evolver, on the same data, or by you deploying it. A file
that arrived this morning has no track record, and a ruling is not one.

---

## Competition: tokens, titles, bouts

```bash
python -m src.main trade season            # every bout, plus milestones
python -m src.main trade bout alpha beta --metric return
python -m src.main trade tokens
```

Firms earn tokens for winning bouts and hitting milestones, and climb from
Novice to Trading God. A bout below the sample gate is **no contest** — the
standings would otherwise be a record of who got lucky first, which is the
same mistake the allocator refuses to make.

Tokens are points. They live in their own tables, nothing in `brokerage/`
reads them, and there is no function anywhere that converts a balance into an
allocation. A currency the firms can award themselves must never become a
second route to the money.

---

## The black market

```bash
python -m src.main trade market-sell alpha --asset genome --price 100
python -m src.main trade market-buy beta 1
python -m src.main trade market
```

Two kinds of listing, and the difference is the safety story:

**Genomes, data and compute** are priced in tokens and settle in the market.
A genome licence is delivered as a *forced mutation* of what was bought, and
lands unselected. Two costs, both deliberate: the mutation stops the pods
converging (three firms running one strategy is one strategy with three names,
and their drawdowns arrive on the same day), and the candidate status means a
bought genome earns its place like every other one.

**Capital does not settle here.** A capital sale is a cut on the seller and a
raise on the buyer — and a raise needs a human. So the market writes an
approval request and stops; once approved, `market-settle` applies both legs
in one transaction. The reconciler's identity still holds on both firms, and
the market cannot become a route around the only gate that stops capital
growing without a person signing for it. A firm can only list capital it is
not currently using.

---

## The sandbox: alliances, betrayal, espionage

```bash
python -m src.main trade sandbox-form "The Pact" alpha beta gamma
python -m src.main trade sandbox-spy beta alpha
python -m src.main trade sandbox-betray alpha "The Pact"
python -m src.main trade sandbox-sabotage gamma beta
python -m src.main trade sandbox
```

The adversarial layer is real as a game and inert as far as the money is
concerned. It is handed two objects and nothing else:

* `ReadOnlyStore` — the ledger, reads only. Every write method raises
  `SandboxViolation`, and so does any attribute not on the allow-list,
  including `db`, so nobody can reach around it to raw SQL.
* `SandboxWriter` — inserts and updates restricted to `alliances` and
  `sandbox_events`.

Both are enforced by `__getattr__`, not by convention, so a future edit that
tries to write through the sandbox fails at the first call.

What each move actually does, stated plainly rather than sold:

| Move | Reach |
|---|---|
| **Betrayal** | fully real — reward, reputation collapse, alliance broken |
| **Espionage** | copies a rival's genome into the sandbox record. It installs nothing: a stolen genome enters the real system only through the strategy court, like any other submission |
| **Sabotage** | moves `shadow_equity`, the sandbox's own scoreboard, and never the target's books |

Sabotage is deliberately weaker than the fiction, and here is why. The
brokerage cuts capital on a score, and the kill switch's reason has to be
reproducible from stored metrics. If one firm could move another's equity,
"Drawdown 22% exceeds 20%" would stop being a fact about that firm's strategy,
the allocator would start punishing victims, and every kill reason would
become a guess. A saboteur can win the tournament; a saboteur cannot cost the
operator a cent.

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
| `/village/flow` | the village map, walked as the tick runs |
| `trade import <path>` | adapt bots written for something else |
| `trade recruit <f>` | drop a bot in; a cleared file becomes a firm |
| `trade recruit-watch --dir D` | try every bot in a directory |
| `trade recruits` | recruited firms waiting to be funded |
| `trade dashboard` | freeze it all into one self-contained HTML file |
| `trade autonomy` | what the village decides for itself |
| `trade council` | the council's rulings |
| `trade council-case <id>` | one ruling, juror by juror |
| `trade court-submit <f>` | put a strategy file on trial |
| `trade court-docket` | recent strategy cases |
| `trade court-case <id>` | one case in full, juror by juror |
| `trade court-watch --dir D` | try every strategy file in a directory |
| `trade tokens` | the token standings (points, not capital) |
| `trade season` | run every bout, award milestones |
| `trade bout <a> <b>` | one head-to-head |
| `trade market` | what firms have for sale |
| `trade market-sell/buy` | list or buy; capital needs an approval |
| `trade market-settle <id>` | apply an approved capital transfer |
| `trade sandbox` | alliances, intrigue, shadow scoreboard |
| `trade sandbox-form/betray/spy/sabotage` | play the adversarial game |
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
├── court/               strategy trials: evidence, jury, advocates, judge
├── competition/         tokens, titles, bouts, milestones
├── black_market/        listings, escrow, licences, capital transfers
├── sandbox/             alliances, betrayal, espionage — read-only guard
├── gateway/             OmniRoute, with an offline fallback
├── execution/           paper (default) and the live venues that refuse
└── audit/               the Obsidian vault, including brain/ as a graph
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
