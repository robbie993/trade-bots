"""What a post, video or repository is *about*, beyond whether it calls a trade.

The crowd seats keep only explicit calls, and that stays the rule for anything
that votes. But the operator wants the village looking for ideas too: trading
strategies, AI agents (Jarvis-style assistants, trading bots), and systems like
the village itself. None of that is a buy or a sell, so it never reaches a
debate; it is tagged here, kept in `intel` with its tags, and the tagged items
go into the outside minds' daily research review (ask.py), which says what, if
anything, is worth testing.

Word lists, not a model: cheap, inspectable, and wrong in obvious ways that are
easy to fix by editing a line.
"""

from __future__ import annotations

import re

TOPICS: dict = {
    "strategy": (
        "trading strategy", "strategy", "backtest", "backtested", "algo trading",
        "algorithmic", "quant", "quantitative", "mean reversion", "momentum",
        "breakout", "indicator", "rsi", "macd", "moving average", "options strategy",
        "covered call", "wheel strategy", "iron condor", "scalping", "swing trade",
        "trading system", "edge", "win rate", "risk management", "position sizing",
    ),
    "ai_agent": (
        "ai agent", "ai agents", "agentic", "jarvis", "trading bot", "trading bots",
        "llm", "gpt", "chatgpt", "claude", "gemini", "deepseek", "autonomous agent",
        "ai model", "ai trader", "ai trading", "machine learning", "reinforcement learning",
        "openai", "anthropic",
    ),
    "village": (
        "multi-agent", "multi agent", "swarm", "hive mind", "agent society",
        "agent village", "ai village", "council of agents", "agents that trade",
        "agents compete", "evolving agents", "genetic algorithm",
    ),
}

_PATTERNS = {topic: re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I)
             for topic, words in TOPICS.items()}


def tag(text: str) -> list:
    """The topics a text is about, e.g. ['ai_agent', 'strategy']. Empty if none."""
    text = text or ""
    return sorted(t for t, pat in _PATTERNS.items() if pat.search(text))


#: Markets talk in general: what the village trades and the talk around it.
#: Wider than TOPICS and kept out of it on purpose, because a tag sends a post
#: to the research review and "stocks" is not an idea to test. It only decides
#: whether a post a For You feed picked unasked is worth keeping.
MARKETS = (
    "stock", "stocks", "stock market", "shares", "trading", "trader", "traders",
    "day trading", "daytrading", "day trader", "invest", "investing", "investor",
    "options trading", "call options", "put options", "0dte", "forex", "futures",
    "nasdaq", "s&p", "sp500", "dow jones", "wall street", "earnings", "dividend",
    "dividends", "etf", "etfs", "portfolio", "bull market", "bear market", "bullish",
    "bearish", "hedge fund", "candlestick", "federal reserve", "fomc", "interest rates",
    "inflation", "recession", "market", "markets", "crypto", "cryptocurrency",
    "bitcoin", "btc", "ethereum", "eth", "solana", "memecoin", "memecoins",
    "meme coin", "meme coins", "altcoin", "altcoins", "dogecoin", "doge", "shib",
    "shiba inu", "pepe", "pump.fun", "pumpfun", "dexscreener", "defi", "airdrop",
    "blockchain", "polymarket", "kalshi", "prediction market", "robinhood", "webull",
    "coinbase", "binance",
)

_MARKETS = re.compile(r"\b(" + "|".join(re.escape(w) for w in MARKETS) + r")\b"
                      r"|\$[A-Za-z]{2,6}\b", re.I)


def on_topic(text: str) -> bool:
    """Whether a text is about markets ($TICKER included) or a TOPICS idea."""
    text = text or ""
    return bool(_MARKETS.search(text)) or bool(tag(text))


__all__ = ["MARKETS", "TOPICS", "on_topic", "tag"]
