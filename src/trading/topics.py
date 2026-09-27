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


__all__ = ["TOPICS", "tag"]
