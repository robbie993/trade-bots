# The village, agent by agent

What each part is, what it may decide alone, and what it must hand to a human.
Written for people first and parsed by `npx lattice-agents .` second, which is
why every entry says what a thing *refuses* to do — that is the interesting
column, and the one a graph of arrows cannot show you.

The single rule the whole layout serves:

> **The system may always stop the bleeding, and may never start it.**

Anything that reduces risk — pausing a firm, cutting an allocation, halting the
village — happens on the system's own authority. Anything that increases it —
funding, raising an allocation, trading a live venue — stops at the gate.

---

## The tick, in order

```
market data → firms propose → heart reviews → venue fills → ledger settles
            → brain remembers → brokerage reconciles, scores, allocates
            → council rules → audit written
```

The order *is* the safety argument. Ethics runs before execution, settlement is
atomic, and the brokerage refuses to do anything at all until the books
reconcile — including the parts that would look like progress.

---

## Agents

### Firms (`src/trading/firms/`)
Nine trading pods, each a full desk: analysts → bull/bear debate → trader →
risk manager. A firm produces *proposals*; it never executes.
- **May alone:** pause itself.
- **May never:** un-pause itself, or change its own allocation.
- **Talks to:** market data, the heart, the signal board.

### Analysts (`src/trading/firms/analysts.py`)
Six seats. Five compute a score from bars; `signals` repeats what scanners
published. All are deterministic functions — no model calls anywhere.
- **Refuses:** to speak below its minimum history. Confidence 0 is *silence*,
  not neutrality.

### Scanners (`src/trading/signals.py`)
Somebody else's screener, publishing a score rather than an order. A
TradingView alert arrives here too.
- **May never:** reach the ledger. There is no path from a reading to a fill.
- **Goes silent:** on the next bar. No grace period.

### The Heart (`src/trading/heart/`)
The conscience. Reviews every proposal before any venue sees it.
- **May alone:** block a trade.

### The Risk Manager (`src/trading/firms/risk_manager.py`)
Allow / resize / block. Reducing exposure is checked for solvency only;
opening it must clear the risk limit, position cap, count and cash floor.

### The Ledger (`src/trading/store.py`)
The only code in the package that moves cash, and it moves it atomically.
Everything the reconciler later checks is decided here.

### The Brokerage (`src/trading/brokerage/`)
Reconcile → evaluate → kill checks → allocate → leaderboard.
- **May alone:** pause a firm, cut an allocation, halt everything.
- **May never:** raise an allocation, or kill a firm for good.
- **Refuses:** to run at all when the books do not reconcile.

### The Kill Switch (`src/trading/firms/kill_switch.py`)
Three distinct ways to decline a verdict, kept separate on purpose:
`Insufficient data` (too few trades), `Cannot value` (the feed has no prices),
`Mixed prices` (real prices, wrong book).
- **Refuses:** to kill on numbers that are not measurements.

### The Court (`src/trading/court/`)
Tries a strategy file before it is allowed to be a firm. Evidence, a
deterministic jury, advocates, a judge.
- **May never:** execute the file. It is parsed with `ast` and never run.

### The Council (`src/trading/council/`)
Rules on what the ledger settles; grants, refuses, or **defers**.
- **May never:** decide live trading. It has no panel for it, by construction.

### The Brain (`src/trading/brain/`)
Memory, lessons, genome evolution.

### The Gate (`src/agents/human_gate.py`)
Every decision that increases risk becomes a row here and waits for a person.

### The Venues (`src/trading/execution/`)
`paper` fills against the reference price with slippage and fees, always
against the trader. Live venues **refuse to send an order** until the venue
itself is approved.

---

## What is not an agent

The **feeds** (`src/trading/data/`) and the **gateway** narration. Both are
plumbing. The gateway has a deterministic fallback and the village runs
identically without it.
