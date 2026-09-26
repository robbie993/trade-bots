# Read this first — you are the Claude on Robbie's Windows PC

Everything in this folder was written by the session on the Mac, on 2026-09-24,
specifically so you would not have to be told any of it. Read it before you
start, and before you re-derive anything.

Robbie's goal: **the village running on this PC the way it ran on the Mac.**

## Order

1. `SETUP.md` — clone, environment, start, verify. Six steps.
2. `CREDENTIALS.md` — the one file that must be carried across by hand, and the
   name translation that decides whether the feed is real.
3. `setup.ps1` — automates most of step 1–2. Right-click → Run with PowerShell.
4. `status-dump.ps1` — read-only. Run it when something is wrong; it prints one
   compact report and shows credential *lengths* rather than values.

## The two traps, stated once

**The branch.** This repo has no `main`. Its default HEAD is
`claude/mvv-phase-1-spec-ib4eui`, from 12 September, missing every recent fix.
If `git branch --show-current` says anything other than
`claude/ai-village-trading-build-m4bg19`, stop and switch. A worktree cut from
the default HEAD does this silently.

**The feed.** `src/trading/config.py:314` defaults `TRADE_DATA_SOURCE` to
`"synthetic"`, and `src/trading/data/feeds.py:639` reads `ALPACA_API_KEY_ID` /
`ALPACA_API_SECRET_KEY` — **not** the `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`
names used inside `.alpaca_credentials`. Set the file's names and the village
starts, creates firms, prints scores, and trades a seeded random walk while
every number looks healthy. The Mac session made exactly this mistake in a
Railway script and caught it only on review.

So the check is never "is the variable set". It is **"did the feed return
bars"** — look for symbol fetch lines with fresh timestamps in the loop output.

## What is already true, so you do not measure it again

- **Nothing in the fleet is confirmed profitable.** The account is up $10.4k on
  paper, realised is about −$114, and 74% of the unrealised rests on an
  impossible crypto cost basis (ARB entry $0.010510). See
  `FLEET_PNL_2026-09-22.md`.
- The wheel's **+$3,436** is plausible — net option premium of **+$5,065** is
  verified in the broker journal — but "collects premium" and "beats holding
  the stock" are different claims and only the first is established.
- **`trade live-status` refuses all 51 firms.** Closest is 2 of 11 criteria
  short. Paper only. Do not enable live trading.
- Per-bot attribution is impossible at the broker: every `client_order_id` is
  an anonymous UUID.

Full detail in `FLEET_PNL_PER_BOT_2026-09-22.md` (including two corrections the
Mac session had to make to its own conclusions) and
`CLAUDE_AGENTS_INVENTORY_2026-09-22.md`.

## What was built on 2026-09-22/23, so you know what you have

The arena no longer fights dead firms; Mission Control loads in under a second
instead of timing out; a killed firm can close a short and wind up; there is a
second village on a daily clock running VERITAS (`scripts/village_daily.sh`,
port 8001); the fleet's live bots bridge their real decisions into the signal
board (`scripts/fleet_sync.py`, `bots/fleet_*.py`); the village reads research
itself with a browser (`scripts/research_scout.py`); it can ask another model a
question and keep the answer (`src/trading/council_of_ais.py`); and the
evolver's look-counter is now a hard gate rather than a log line.

`docs/research-dossier.md` is the 117-search literature review this work is
measured against. Its Phase 0 is the priority, and three of its four items are
still open.

## What this PC does for the village (set up 2026-09-25)

The village runs on Railway (`ai-village` project: `village-worker`,
`village-web`, `Postgres`). This PC does the jobs that need a home connection,
`railway login`, or Claude Code. Three Windows scheduled tasks, each a `.cmd`
in `scripts/` that logs to `logs/`:

| task | every | what |
|---|---|---|
| Village fleet sync | 30 min | `fleet_sync.py --to-railway`: every fleet bot's state file over `railway ssh` into `fleet_snapshots` |
| Village video watch | 30 min | `video_watch.py --to-railway`: new uploads on `config/video_channels.yaml`, explicit calls into `youtube_calls` and `intel` |
| Village answer questions | 2 h | `answer_questions.py --to-railway`: Claude Code headless answers the firms' questions |

None of them stores a password: the database URL is asked of Railway each run
(through the Postgres service's public TCP proxy), and the Alpaca keys live only
in Railway's variables. If a task's log shows `railway ... Unauthorized`, run
`railway login` again in PowerShell.

Known state at hand-off: YouTube has rate-limited this PC's caption requests
since the first burst on 2026-09-25 (lists and titles still load; captions
429). Audio downloads work, and a Whisper fallback is next once its model can
be downloaded (Hugging Face was unreachable from here that evening).

