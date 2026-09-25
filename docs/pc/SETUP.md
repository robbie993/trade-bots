Set up and run the trading village on this Windows machine. Work through this
in order and tell me where it fails rather than working around it.

1. CLONE THE RIGHT BRANCH. This matters — the repo has no `main` and its default
   HEAD is a branch from 12 Sep that is missing every recent fix.

   git clone -b claude/ai-village-trading-build-m4bg19 https://github.com/robbie993/trade-bots.git
   cd trade-bots
   git log --oneline -1

   Expect 6549a9d or later. If you are on claude/mvv-phase-1-spec-ib4eui you
   have the wrong branch — a worktree cut from the default HEAD will do this.

   Confirm these exist before continuing:
     docs/research-dossier.md
     scripts/research_scout.py
     src/trading/council_of_ais.py
     config/firm_config_daily.yaml
     bots/fleet_scanner.py

2. ENVIRONMENT.

   python -m venv venv
   venv\Scripts\python -m pip install -r requirements.txt
   venv\Scripts\python -m pip install playwright beautifulsoup4 lxml
   venv\Scripts\python -m playwright install chromium

   Do NOT pin playwright to 1.60.0 if you see that referenced anywhere — that
   was a macOS 13 workaround and does not apply on Windows.

3. CREDENTIALS. One file has to be copied from the Mac by hand:

     /Users/robbie/trade/.alpaca_credentials  ->  <repo>\.alpaca_credentials

   It contains ALPACA_ENDPOINT, ALPACA_API_KEY, ALPACA_SECRET_KEY. Without it
   the feed refuses rather than inventing prices — the right failure, but the
   village will not trade. Ask Robbie for it; do not attempt to generate one.

4. START IT. Two windows.

   venv\Scripts\python -m src.main serve --host 127.0.0.1 --port 8000
   venv\Scripts\python -m src.main trade run --interval 120

   Console: http://127.0.0.1:8000/village

   The .sh scripts (village.sh, village_daily.sh) need WSL or Git Bash. The two
   commands above are the same thing without them.

5. VERIFY IT IS REAL, NOT SYNTHETIC. This is the check that matters.

   In the loop output, confirm the feed is alpaca and the bars are fresh. A
   village that starts, creates firms and reports scores can still be trading a
   seeded random walk — that failure has happened on this project before and it
   looks exactly like success. If TRADE_DATA_SOURCE is unset it defaults to
   synthetic.

   Then:
     venv\Scripts\python -m src.main trade reconcile     # must say: no breaks
     venv\Scripts\python -m src.main trade live-status   # expect: nothing ready

6. OPTIONAL — the Mac's history. If Robbie gives you
   village-databases-2026-09-23.zip, unzip mvv.db and mvv_daily.db into
   trade-bots\data\ BEFORE step 4, and verify the checksum first:

     certutil -hashfile data\mvv.db SHA256
     ebe67e25e6750deab2ac16e8ce39b618b6b9b3194786daed2a56a2e89319a6cf

   Skip it and the village starts a fresh book, which is perfectly fine.

WHAT NOT TO DO
  - Do not enable live trading. `trade live-status` currently refuses all 51
    firms; the closest is 2 of 11 criteria short. Paper only.
  - Do not quote the fleet's "+$10.4k" — it is unrealised and 74% of it rests
    on an impossible crypto cost basis. See FLEET_PNL_2026-09-22.md.
  - Do not re-derive today's findings. FLEET_PNL_PER_BOT_2026-09-22.md and
    CLAUDE_AGENTS_INVENTORY_2026-09-22.md are in the repo; read them first.
