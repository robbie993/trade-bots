"""Option data for the overlay. NOT USED until Atlas Core has a result.

Two kinds of option history exist in this project and must never be pooled:

* **Real quotes**: Alpaca option bars 2024-2026, imported by
  `scripts/import_alpaca_options.py` and used by trade-bot-2's
  `insider_research/options_backtest.py`. Bid/ask is not in the bars, so a
  spread model is still needed (`option_cost_reality.py` in trade.zip).
* **Modeled prices** (Black-Scholes off realized vol) for anything earlier.
  Any result that uses them is labelled SYNTHETIC and reported separately;
  it can never decide the real-world conclusion.

The overlay sits on top of a frozen core so that premium selling cannot be
what rescues a weak equity signal.
"""
SYNTHETIC = "SYNTHETIC"
REAL = "REAL"
