# The bots that live in Claude, not on Railway — 2026-09-22

The Railway fleet is not the whole fleet. Five bots run as **scheduled Claude
agents** on this machine, and they trade the same paper account as everything
else. None of them appears in `FLEET_PNL_PER_BOT_2026-09-22.md`, which makes
that document incomplete — the account has a third source of orders that the
per-bot decomposition did not know about.

## The five scheduled agents

All `enabled: true`. All dormant.

| task | what it does | schedule | last ran |
|---|---|---|---|
| `tsla-wheel-monitor` | TSLA wheel — CSPs, covered calls, assignments, early closes | every 15m, 06:00–13:59 Mon–Fri | **2026-08-11** |
| `tsla-trailing-stop-monitor` | TSLA trailing stop, ladder re-entries, per-leg stops | every 15m, 06:00–13:59 Mon–Fri | **2026-08-11** |
| `tsla-wheel-daily-summary` | daily wheel summary at the close | 13:13 Mon–Fri | **2026-08-11** |
| `politician-copy-trader` | copies Ro Khanna & Nancy Pelosi filings to Alpaca, $500/trade | every 4h Mon–Fri | **2026-08-11** |
| `weekly-bot-graduation-review` | the bots + live account against "ready for real money" | Sundays 18:05 | **2026-08-10** |

Six weeks idle while still enabled. Next fire is 2026-09-23, so they are not
cancelled — they simply have not run. Worth finding out why before assuming
either that they work or that they don't.

## What they actually are

**The TSLA wheel is a Claude agent, not a Python bot.** The whole strategy —
stage machine, cost basis, premium tracking, assignment handling — lives in
`~/.claude/scheduled-tasks/tsla-wheel-monitor/SKILL.md` as instructions, with
state in `/Users/robbie/trade/wheel_state.json`. That file currently reads
`{"stage":"csp","active_contract":false,"contract_symbol":null,"cost_basis":null}`
— a reset state, consistent with six weeks of not running.

This matters for the wheel question in `FLEET_PNL_PER_BOT`: there are **two
different wheels**. The Railway `wheel` bot on `sincere-appreciation` reporting
+$3,436, and this TSLA wheel agent. They are not the same thing and their
results must not be added.

**The politician copy trader is real code and it is still executing.**
`/Users/robbie/trade/politician_bot/` — `bot.py`, `scraper.py`, `trader.py`,
`processed_trades.json`. It scrapes Capitol Trades with a **headless Chromium
browser**, diffs against processed trades, and sends $500 market orders to the
paper account.

Its log shows runs as recently as **2026-09-18**, all failing:

```
2026-09-18 12:29:39  ERROR  Could not reach Alpaca: {"message": "unauthorized."}
```

So it is running and dead at the same time — scraping successfully, then failing
to place anything. The same shape as the FOMO 401 outage.

## Two things to act on

**1. There is already a working headless-browser scraper in this fleet.**
`politician_bot/scraper.py` drives headless Chromium against Capitol Trades.
That is the pattern for giving the village its own research — it exists, it
works, and it needs no API key. Task 7 should start by reading it rather than
inventing a second approach.

**2. Plaintext Alpaca credentials.** `tsla-wheel-monitor/SKILL.md` contains the
API key and secret in the clear, as instructions to be read by an agent. They are
the live paper-account keys. Rotating them is cheap and the file should reference
`/Users/robbie/trade/.alpaca_credentials` the way every other bot does. (Not
reproduced here on purpose.)

## The rest of the transcript inventory

Research sessions worth mining, by title:

- **Options trading scanner research** (2026-08-02)
- **Bot improvement discovery** (2026-08-04)
- **Wheel strategy bot with discovery data against SPY** (2026-08-08)
- **Form4 bot misread and scanner** (2026-08-08)
- **Gatekeeper bot revenue analysis** (2026-08-23)
- **Market temperature and hydrodynamics research** (2026-09-18)
- **Crypto strategies for Spidey bot** (2026-09-20)
- **Bot portfolio profit outlook** (2026-07-23)
- **Krypt.Trader AppImage review** / **Tritonix Studio link** — the two audits
  whose verdicts are already in memory
- **Code review: DecisionJournal event sourcing** (2026-07-24)

Three sessions are still marked `isRunning: true`: `politician-copy-trader` and
`tsla-wheel-daily-summary` (both 2026-09-18) and a `tsla-trailing-stop-monitor`
from 2026-08-11. Running sessions that have produced nothing for weeks are worth
closing.

## Correction this forces

`FLEET_PNL_PER_BOT_2026-09-22.md` attributes the account's positions across the
Railway bots only. The TSLA position (+$1,037.55 unrealised) and possibly some
of the option legs may belong to these agents instead. The per-bot table should
be read as "the Railway fleet's self-reported numbers", not "everything that
touched the account".
