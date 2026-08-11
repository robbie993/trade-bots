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

## When your strategy is not seven numbers

Everything above translates a bot *into* the village's seven genes, which works
when your strategy really is seven numbers and fails quietly when it is not.
Most real strategies are not: the logic lives in a function, and no amount of
matching on module-level constants will find it.

So there is a second way in. Declare a function, and the village runs it:

```python
def propose(context):
    orders = []
    for symbol in context.universe:
        if context.quantity(symbol) == 0 and context.price(symbol):
            orders.append({"symbol": symbol, "side": "buy", "notional": 5000,
                           "rationale": "why I want this"})
    return orders
```

Point a firm at the file in `config/firm_config.yaml`:

```yaml
firms:
  my_desk:
    name: "My Desk"
    capital_allocation: 50000
    universe: [SPY, QQQ]
    bot: bots/my_bot.py
```

See `example_adapter.py` for a working one.

**What you get.** One argument, holding what a strategy needs and nothing else:

| | |
|---|---|
| `context.universe` | the symbols this firm may trade |
| `context.cash` / `context.equity` | Decimals |
| `context.price(symbol)` | the latest mark, or `None` |
| `context.closes(symbol, n)` | the last n closes, oldest first |
| `context.quantity(symbol)` | how much you hold — `0` if nothing |
| `context.as_of` | the timestamp of the latest bar |

Not the store, not the database, not the gate, not the other firms' books.

**What you return.** A list of dicts with `symbol`, `side`, and either
`quantity` or `notional`. `rationale` is optional and worth writing — it is
what the audit trail shows when somebody asks why the firm bought that.

**What the village still does.** Everything: your order meets the same risk
manager, the same conscience, the same position sizing and the same audit row
as anything the built-in analysts produce. You decide what you want. You do not
decide what happens.

**This runs your file**, which nothing else here does — the court and the
importer parse with `ast` and never execute, because they read files you might
have been handed. A bot runs only when a firm's config names it. Dropping a
file in this folder never causes it to be executed.

A bot that raises is reported and skipped. One that hangs is abandoned after
ten seconds. One that returns nonsense has the nonsense dropped and the rest
kept. In every case the firm has a quiet tick, the reason appears in the tick
summary, and the village carries on.

## Real prices

`TRADE_DATA_SOURCE` picks the feed. The default is a seeded synthetic one that
prices everything instantly with no network — that is what the tests, the
backtests and the evolution loop run on, and a replay that depends on an
exchange being up is not a replay.

| source | covers | needs |
|---|---|---|
| `synthetic` | everything, invented | nothing |
| `alpaca` | **stocks and crypto** | free API keys |
| `yahoo` | stocks, ETFs, indices | nothing |
| `ccxt` | crypto, 100+ exchanges | `pip install ccxt` |
| `csv` | whatever you put in `data/market/` | nothing |

**Alpaca is the one to reach for** if your universe is mixed, because it is the
only one here that covers both halves. Free keys from alpaca.markets, then:

```bash
export ALPACA_API_KEY_ID='...'
export ALPACA_API_SECRET_KEY='...'
TRADE_DATA_SOURCE=alpaca ./scripts/village.sh restart
```

Those are credentials — export them in your shell, do not put them in a file in
this repository. They go in a header, never in a URL, and nothing logs them.

Symbols route by the village's own spelling: `BTC-USD` has a quote currency and
goes to the crypto endpoint, `SPY` is a bare ticker and goes to the stock one.
The free stock tier is IEX rather than the full tape, which for daily bars is a
rounding difference; `TRADE_ALPACA_FEED=sip` switches it if you pay for it.

**Several sources chain**, tried per symbol: `ccxt,yahoo` asks the exchange
first and falls back for what it has never heard of. A symbol nothing in the
chain can price is reported and skipped — that one symbol stops, not the
village. Mixing `synthetic` into a chain with real feeds warns, because a book
priced half on real data and half on invented data is a book that lies.

**A note on Binance**: it does not serve US addresses, and the failure looks
like every pair being unavailable rather than anything mentioning geography.
`TRADE_CCXT_EXCHANGE=coinbase` or `alpaca` if you are in the US.

## Selling short

Off by default. Turn it on with `TRADE_ALLOW_SHORT=1`, and then a bot may
return `{"side": "sell"}` on a symbol it does not hold.

That default is not squeamishness. A long position's worst case is losing what
you put in; a short's has no floor, and a system whose whole claim is that it
can be stopped should not open unbounded risk because nobody said otherwise.

Three things come with it:

| | |
|---|---|
| `TRADE_MAX_GROSS_EXPOSURE` | longs **plus** the absolute value of shorts, against allocation. Default 1.5x |
| `TRADE_BORROW_RATE` | charged per tick on short notional. Default 5% a year |
| the cash floor | applies to shorts too — that is the margin rule, at 100% |

Gross rather than net, because long 100 and short 100 is flat on paper and can
lose on both legs at once. Borrow, because a backtest that treats shorting as
free is a backtest of a strategy nobody can run.

**Closing is always allowed**, including when shorting is switched off — turning
it off must never trap a firm in a position it already holds.

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
