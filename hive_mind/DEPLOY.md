# Deploying it

Six steps. The first four are tonight; the last two are months apart, and the
gap is the point.

⚠️ Everything below is a research harness. Step 6 is the only one that involves
money, it involves $100, and nothing in this repository will send an order to a
broker — that adapter does not exist here and writing it is your decision, not
a missing feature.

---

## 0. Install

```bash
git clone <this repo> && cd trade-bots
pip install -e '.[dev]'          # or nothing at all — the engine is stdlib only
pip install yfinance             # optional; `stooq` needs no library
python -m pytest tests/test_hive_mind*.py -q     # 94 tests, ~17s, no network
```

If those pass, the machinery works. Nothing about a strategy has been claimed
yet.

---

## 1. Get the tape (5 minutes)

```bash
python -m hive_mind.providers
```

Writes `data/market/SPY.csv` and `data/market/VIX.csv`. It tries `stooq`
first (no key, no library), then `yfinance`, then the keyed providers. It
prints which one answered — write that down, it belongs with any number you
quote later.

If every provider refuses (corporate firewall, VPN, a bad afternoon at a
vendor), download a CSV by hand — Kaggle has S&P 500 dailies — and load it:

```bash
python -m hive_mind.providers --symbol SPY --provider csv --source ~/Downloads/spy.csv
```

**Check what you got before you use it:**

```bash
python -m hive_mind.real_feed
```

```
SPY: 8,290 bars, 1993-01-29 to 2026-09-01
  vix       : real ^VIX (VIX.csv)
  sentiment : PROXY: 20-day return/volatility blend — not news
```

If the VIX line says `PROXY`, stop and fix it. Without real VIX every
VIX branch in the genome is dead code, and the survival number you are about
to generate is measuring a plain momentum strategy wearing this one's name.

---

## 2. Start the news clock (1 minute, then daily forever)

```bash
python -m hive_mind.news
```

This does **not** help step 3 and will not for months. RSS hands back the last
few dozen headlines; it cannot tell you how the market felt in 2009, and no
free source can. Phases 1, 2 and 2b need years of daily sentiment, so they run
on the proxy regardless.

What this does is start the clock. Run it daily and in six months you have six
months of real sentiment recorded forward and never backfilled — the only kind
that can honestly appear in a backtest.

```bash
# cron, or launchd, or Task Scheduler — once a day, after the close
0 22 * * 1-5 cd /path/to/trade-bots && python -m hive_mind.news >> news.log 2>&1
```

`python -m hive_mind.news --show` prints what has accumulated.

---

## 3. Run the crucible (a couple of hours)

```bash
python -m hive_mind.crucible_real
```

100 random genomes through four phases against real history. Roughly 10
seconds each on 30 years of bars — the harness times the first one and prints
the estimate, so you know at minute one whether this is a coffee or a night.

It seals the last five years out of the run and tells you which look at this
dataset this is. Read the output in this order:

1. **Where they died.** "36% survived" is equally consistent with a gauntlet
   that is far too easy and one that is far too hard. The phase histogram tells
   you which. Deaths spread across phases 2, 2b and 3 is a working pipeline;
   everything dying at one phase means that phase's threshold is doing all the
   work and the others are decoration.
2. **Buy and hold, printed above the result.** A strategy that clears the
   gauntlet while underperforming a nap has not shown an edge.
3. **The survival rate, last.** And read a *high* one as a warning: these
   genomes are drawn at random, and if most random strategies pass, that is
   news about the pipeline, not about the architecture.

Survivors are written to `data/market/certified/<fingerprint>.json`.

### What to do with the number

| Rate | What it means | What to do |
|---|---|---|
| 0% | The most common honest outcome. | Nothing is broken. Random strategies don't have edges. |
| 1–20% | The pipeline discriminates. | Look at the survivors individually. Do they beat buy-and-hold? |
| 20–60% | Either a real effect or a lenient gauntlet. | Tighten `stress_worst_case_pct` and `gauntlet_min_trades` and rerun. |
| >60% | Almost certainly a lenient gauntlet. | Check that the stress windows really are unseen and that phase 2's floor is not trivially clearable. |
| mostly crashed | A broken harness, not a result. | Fix the exception. Read no number above it. |

### The thing that will actually go wrong

You will want to tweak the scout logic and run it again. That is the right
instinct and it has a cost: there is exactly one real SPY history, so tuning
code against it until the number looks good is the training-data trap moved
from the genome to you — identical overfitting, and this time no backtest can
see it, because you are the thing being fitted.

The harness counts. Every run appends to `data/market/.crucible_log.jsonl` and
prints `👁 This is look #7 at this dataset`. A 70% rate on the seventh look,
after six rounds of tweaking, is much weaker evidence than the same number on
the first. Counting is the only defence there is; use it rather than working
around it.

**Do not lower a threshold because a genome you like keeps failing it.** The
thresholds are the lock. `--lenient` exists to watch the machinery run and
nothing that passes under it has proved anything.

---

## 4. Spend the holdout — once (10 minutes)

When you have stopped changing the code, and only then:

```bash
python -m hive_mind.crucible_real --spend-holdout
```

This runs the five sealed years. They are now out of sample forever after,
and the harness logs the spend so a future you cannot pretend otherwise.
Getting a similar rate here to the tune window is the strongest evidence this
setup can produce. Getting a much worse one means the earlier numbers were a
fit — which is exactly what you sealed the years to find out.

---

## 5. Paper forward, for months (no code changes)

```bash
python -m hive_mind.live --once --catch-up   # opens the book, trades today
python -m hive_mind.live --status            # what it holds right now
```

Then two cron lines, and nothing else for six months:

```bash
0 21 * * 1-5  cd /path/to/trade-bots && python -m hive_mind.providers --force >> live.log 2>&1
0 22 * * 1-5  cd /path/to/trade-bots && python -m hive_mind.live --once      >> live.log 2>&1
0 22 * * 1-5  cd /path/to/trade-bots && python -m hive_mind.news             >> news.log 2>&1
```

Refresh the tape, take one decision, collect the headlines. The book lives in
`data/market/live_book.json` and every decision is appended to
`live_journal.jsonl` — state and history kept separately, because one of them
you overwrite and the other you never do.

It is built for the days it goes wrong rather than the days it works. Running
twice on the same bar does nothing (a doubled bar is a doubled position, and it
would look like a good day). A week of missed bars is processed in full, in
order, because taking only the newest would leave holes in an equity curve that
still plots. The genome is loaded from its certificate, checked against its own
fingerprint, and never mutates — this phase measures what the crucible
certified, and a genome that moves here is measuring nothing.

**Paper only.** There is no venue but the hostile paper one, no credential is
read, and no code path in this package reaches a broker.

Do not evolve it, do not tune it, do not restart it when it has a bad
fortnight. The point of this phase is to spend real calendar time discovering
whether the thing holds up on days nobody has seen, including you.

Six months at minimum. If forward returns are positive with a drawdown you
would actually have tolerated while watching it, continue. Otherwise stop —
and note that stopping here has cost you nothing but time, which is the whole
architecture working as designed.

**Before pointing it at anything live, watch for this:**

```
ProfileMismatch: this genome was measured on different data than it is being
run on — sentiment: certified on 'proxy:returns-vol-20', this feed is
'news:rss-lexicon-v1'.
```

That is the guard doing its job. A genome fitted against the proxy and
deployed reading real news is reading a *different distribution through the
same thresholds* — `fear_threshold = -0.5` is a rare event on a series with a
standard deviation of 0.18 and a Tuesday on one with a standard deviation of
1.5. If you have collected enough real sentiment (step 2) to switch, re-run
the crucible against that feed and get a new certificate. Do not override the
check.

---

## 6. The suicide fund ($100, one month)

Only after step 5. Deploy with $100 — not $1,000, not "just to see" — and let
only the sizing genes move. The strategy genes are frozen under the
certificate, and `Evolver.mutate` enforces that rather than trusting you to
remember it.

To clear this phase: $100 → $120 with a Sharpe above 1.5. You will probably
find that bar unreachable on a small account over one month once costs are
charged, and that is not a bug in the thresholds — it is the honest shape of
the problem, and finding it out for $100 is the cheapest lesson on this page.

If it fails, the lock drops back to the paper phase by itself and the genome
stops trading real money. That is deliberate: a failed suicide fund is a stop,
not a smaller position.

---

## What this repository will not do for you

**There is no broker adapter here.** Nothing in `hive_mind/` can send an order.
Step 6 requires you to write that integration, and the moment you do, you own
every failure mode this harness cannot model: partial fills, halts, gaps
through your stop, latency, an API that returns 200 and does nothing, and a
position you cannot value because the feed died. The village next door
(`src/trading/`) has adapters that refuse to trade without a human's written
approval; read `src/trading/execution/live.py` before you write your own.

**The bars are daily.** Every result here is about decisions made once a day at
the close. An intraday strategy is a different system with different failure
modes, and none of these numbers transfer.

**Out-of-sample bars are still past bars.** A cleared pipeline means one
narrow, real thing: this genome was measured on data that could not have
shaped it. It is not a prediction, and the market is not obliged to keep
resembling its own history.

---

## Daily and weekly, once it is running

```bash
python -m hive_mind.live --once                 # daily: one decision
python -m hive_mind.live --status               # daily: what it holds
python -m hive_mind.news                        # daily: keep the clock running
python -m hive_mind.providers --force           # daily: refresh the tape
python -m hive_mind.crucible_real --genomes 20  # after any code change, ever
python -m pytest tests/test_hive_mind*.py -q    # before trusting a result
```

The last one is not ceremony. The first real run of `crucible_real.py` crashed
all 100 genomes on a missing method, printed `⛔ 100 of 100 runs CRASHED — this
is not a result about strategies, it is a broken harness`, and nothing else in
the package noticed. That is the separation working. Run the tests so you know
which kind of zero you are looking at.
