# The drop box

Put a bot in here and run:

```bash
python -m src.main trade recruit-watch --dir bots
```

Each file goes through the **strategy court** — parsed with `ast`, never
imported, never executed — and a file the court accepts becomes a firm in the
village: its own capital, its own kill switch, its own planet at
`/village/solar`, its own resident in the village map.

A recruited firm is created **paused and holding nothing**. Funding it is a
separate approval, because creating a funded trading firm from a file would be
starting the bleeding, and this system may never do that on its own:

```bash
python -m src.main trade recruits          # who is waiting
python -m src.main approvals
python -m src.main approve <id> --by you
python -m src.main trade apply-approvals   # funds it and sets it trading
```

## Bringing in a bot written for something else

If your file does not already have `GENOME` and `UNIVERSE`, adapt it first:

```bash
python -m src.main trade import ~/my-bots --dry-run   # read it, write nothing
python -m src.main trade import ~/my-bots             # writes into bots/
```

It reads `FAST_MA`, `TICKERS`, `params: {...}` and the other names people
actually use, and prints exactly what it mapped, guessed, defaulted, ignored
and could not carry. Read that before recruiting the result.

## What a bot has to declare

Two module-level names. Both must be literals — the court reads the syntax
tree, so anything computed at import time is invisible to it and the file is
refused rather than run.

```python
GENOME = {
    "fast_window": 8,        # 3..30
    "slow_window": 34,       # 20..120
    "rsi_window": 14,        # 5..30
    "trend_bias": 80,        # 0..100 — 100 = pure momentum, 0 = pure reversion
    "value_window": 60,      # 30..150
    "fair_band_pct": 6,      # 2..25
    "calm_vol_pct": 30,      # 10..90
}

UNIVERSE = ["SPY", "QQQ"]
```

YAML and JSON work too, with `genome:` and `universe:` keys.

## What gets a file rejected

* it does not parse
* it imports something outside a strategy's business — `socket`, `subprocess`,
  `requests`, `os`
* it evaluates code at runtime — `eval`, `exec`, `open`
* no readable `GENOME`, or genes outside their ranges
* it loses money on its own backtest, or the jury cannot separate it from noise

The verdict, juror by juror, is in `trade court-docket` and on the case page at
`/village/court/<id>`.

## A note on what a "bot" is here

A firm in this village is a **genome** — the parameters the built-in analysts
read — not arbitrary code. The court will not execute your file, so a bot with
its own custom logic in Python functions cannot be run as-is; what carries over
is the configuration. If you have bots with real logic you want to bring in,
express the behaviour as a genome, or tell me what they do and the analyst
roster can grow to cover it.
