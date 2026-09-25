GETTING THE ALPACA KEYS ONTO THE PC
===================================

The keys live in one file on the Mac. Copy the file — do not retype the values,
and do not paste them into a chat window.

    Mac:  /Users/robbie/trade/.alpaca_credentials

Open it with TextEdit, or in Finder press Cmd+Shift+G and paste that path. Put
it on a USB stick, or AirDrop/email it to yourself. It is three lines.

On the PC, save it as:

    <repo>\.alpaca_credentials          e.g. C:\Users\robbie\trade-bots\.alpaca_credentials


THE PART THAT WILL CATCH YOU OUT
--------------------------------

The names inside that file are a LOCAL CONVENTION. The village does not read
them.

    the file has        ALPACA_API_KEY           ALPACA_SECRET_KEY
    the village reads   ALPACA_API_KEY_ID        ALPACA_API_SECRET_KEY

(src/trading/data/feeds.py:639)

Set only the file's names and the village starts, creates its firms, reports
scores — and finds no feed. Because src/trading/config.py:314 defaults
TRADE_DATA_SOURCE to "synthetic", it then trades a SEEDED RANDOM WALK while
every number on screen looks completely normal. This has already happened on
this project.


PASTE THIS INTO POWERSHELL ON THE PC
------------------------------------

Reads the file, sets the names the village actually wants, for this session:

    Get-Content .alpaca_credentials | ForEach-Object {
      if ($_ -match '^\s*([A-Z_]+)\s*=\s*(.+?)\s*$') {
        $n = $matches[1]; $v = $matches[2]
        if ($n -eq 'ALPACA_API_KEY')    { $env:ALPACA_API_KEY_ID     = $v }
        if ($n -eq 'ALPACA_SECRET_KEY') { $env:ALPACA_API_SECRET_KEY = $v }
        if ($n -eq 'ALPACA_ENDPOINT')   { $env:ALPACA_ENDPOINT       = $v }
      }
    }
    $env:TRADE_DATA_SOURCE = "alpaca"
    $env:TRADE_ALPACA_FEED = "sip"

Check it took, without printing the secrets:

    "KEY_ID    : $($env:ALPACA_API_KEY_ID.Length) chars"
    "SECRET_KEY: $($env:ALPACA_API_SECRET_KEY.Length) chars"
    "SOURCE    : $env:TRADE_DATA_SOURCE"

Expect 26 and 44 characters, and alpaca. Zero means the file did not parse.

To make it permanent instead of per-session, swap $env:NAME = for
[Environment]::SetEnvironmentVariable("NAME", $v, "User") and reopen the shell.


THEN START IT
-------------

    venv\Scripts\python -m src.main serve --host 127.0.0.1 --port 8000
    venv\Scripts\python -m src.main trade run --interval 120


THE ONLY CHECK THAT MATTERS
---------------------------

Do not trust the variable list. Trust the fetches. In the loop output you want
to see real symbol fetches with fresh timestamps, like:

    2026-09-24T14:22:01Z  SPY  feed=sip  bars=3680  newest=... age=0.4h

No fetch lines means synthetic, no matter how healthy everything else looks.
Then:

    venv\Scripts\python -m src.main trade reconcile      # must say: no breaks
