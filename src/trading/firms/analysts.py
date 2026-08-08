"""The analysts — section 2.2 of the build document.

Each analyst turns a price history into one ``Signal``: a score from -100 to
+100 and a confidence from 0 to 100. They are deterministic functions of the
data, not LLM calls. The gateway can narrate an analyst's reading afterwards
(``gateway.omniroute``), but it is never what produces the number, for the
same reason the kill criteria are not an LLM call: the thing that decides
whether money moves has to be reproducible from the stored inputs.

Confidence is the honesty channel. An analyst without enough history returns
confidence 0, and the debate reads that as silence, not as a neutral vote.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional, Protocol

from ...money import D, ZERO, percent
from ..data.market_data import MarketData
from ..indicators import momentum_pct, rsi, sma, volatility_pct, zscore
from ..models import Signal


class Analyst(Protocol):
    name: str
    minimum_bars: int

    def analyse(self, symbol: str, market: MarketData, genome: dict) -> Signal: ...


def _genome(genome: dict, key: str, default) -> Decimal:
    try:
        return D(genome.get(key, default))
    except Exception:
        return D(default)


def _silent(name: str, symbol: str, bars_needed: int) -> Signal:
    return Signal(
        analyst=name,
        symbol=symbol,
        score=ZERO,
        confidence=ZERO,
        note=f"insufficient history (needs {bars_needed} bars)",
    )


class TechnicalAnalyst:
    """Trend versus mean reversion, arbitrated by the genome.

    ``trend_bias`` at 100 is pure momentum, at 0 pure reversion. It is the
    main gene the evolver turns, because it is the one that decides whether a
    firm buys strength or buys weakness.
    """

    name = "technical"
    minimum_bars = 60

    def analyse(self, symbol: str, market: MarketData, genome: dict) -> Signal:
        if not market.is_ready(symbol, self.minimum_bars):
            return _silent(self.name, symbol, self.minimum_bars)

        closes = market.closes(symbol)
        fast = int(_genome(genome, "fast_window", 10))
        slow = int(_genome(genome, "slow_window", 30))
        bias = _genome(genome, "trend_bias", 60) / D(100)

        fast_ma, slow_ma = sma(closes, fast), sma(closes, slow)
        mom = momentum_pct(closes, slow)
        strength = rsi(closes, int(_genome(genome, "rsi_window", 14)))
        deviation = zscore(closes[-slow:])

        if fast_ma is None or slow_ma is None or slow_ma == 0:
            return _silent(self.name, symbol, self.minimum_bars)

        # Trend leg: how far the fast average sits above the slow one.
        spread_pct = (fast_ma - slow_ma) / slow_ma * D(100)
        trend_score = _clamp(spread_pct * D(12) + (mom or ZERO) * D("1.5"))

        # Reversion leg: stretched price and stretched RSI both pull back.
        reversion = ZERO
        if deviation is not None:
            reversion += _clamp(-deviation * D(30))
        if strength is not None:
            reversion += _clamp((D(50) - strength) * D("1.6"))
        reversion = _clamp(reversion / D(2))

        score = _clamp(trend_score * bias + reversion * (D(1) - bias))
        # Confidence rises with how decisive the two legs are and falls when
        # they contradict each other.
        agreement = D(100) - min(D(100), abs(trend_score - reversion) / D(2))
        confidence = _clamp_confidence((abs(score) + agreement) / D(2))
        return Signal(
            self.name,
            symbol,
            score,
            confidence,
            f"fast/slow spread {percent(spread_pct)}%, RSI {strength}, z {deviation}",
        )


class FundamentalAnalyst:
    """Valuation without a fundamentals vendor.

    Real fundamentals need a data subscription. What this reads instead is the
    price's own distance from its long-run level and how much of the recent
    move volume actually supported — a cheap, honest proxy that says "this has
    run far ahead of itself" or "this is unloved". It is labelled clearly so
    nobody mistakes it for a discounted cash flow.
    """

    name = "fundamental"
    minimum_bars = 90

    def analyse(self, symbol: str, market: MarketData, genome: dict) -> Signal:
        if not market.is_ready(symbol, self.minimum_bars):
            return _silent(self.name, symbol, self.minimum_bars)

        closes = market.closes(symbol)
        anchor = sma(closes, min(len(closes), int(_genome(genome, "value_window", 90))))
        if anchor is None or anchor == 0:
            return _silent(self.name, symbol, self.minimum_bars)

        premium_pct = (closes[-1] - anchor) / anchor * D(100)
        fair_band = _genome(genome, "fair_band_pct", 8)
        # Cheap relative to its own history reads bullish; expensive, bearish.
        score = _clamp(-premium_pct * D(3))
        confidence = _clamp_confidence(
            ZERO if abs(premium_pct) <= fair_band else min(D(85), abs(premium_pct) * D(4))
        )
        verdict = "rich" if premium_pct > fair_band else "cheap" if premium_pct < -fair_band else "fair"
        return Signal(
            self.name,
            symbol,
            score,
            confidence,
            f"{verdict}: {percent(premium_pct)}% vs long-run mean (price-based proxy)",
        )


class SentimentAnalyst:
    """Crowd behaviour, read off volume and the shape of recent candles.

    Volume expanding into a rally is participation; volume expanding into a
    fall is capitulation. No social media feed is involved, and the note says
    so — an unlabelled proxy is how a system ends up trusting a number nobody
    can source.
    """

    name = "sentiment"
    minimum_bars = 30

    def analyse(self, symbol: str, market: MarketData, genome: dict) -> Signal:
        if not market.is_ready(symbol, self.minimum_bars):
            return _silent(self.name, symbol, self.minimum_bars)

        bars = market.history(symbol, 30)
        volumes = [b.volume for b in bars]
        baseline = sma(volumes, len(volumes))
        recent = sma(volumes, 5)
        if baseline is None or recent is None or baseline == 0:
            return _silent(self.name, symbol, self.minimum_bars)

        surge = (recent - baseline) / baseline  # fraction
        direction = ZERO
        for bar in bars[-5:]:
            if bar.close > bar.open:
                direction += D(1)
            elif bar.close < bar.open:
                direction -= D(1)

        score = _clamp(direction * D(12) * (D(1) + surge))
        confidence = _clamp_confidence(min(D(80), abs(surge) * D(120) + abs(direction) * D(8)))
        return Signal(
            self.name,
            symbol,
            score,
            confidence,
            f"volume {percent(surge * D(100))}% vs 30d, {int(direction)}/5 up days (volume proxy)",
        )


class MacroAnalyst:
    """The regime the whole universe is in, not the individual name.

    Reads the average trend and average volatility across every symbol the
    firm follows. Its job is to be the analyst that says "not now": in a
    high-volatility, falling market it drags every score down at once.
    """

    name = "macro"
    minimum_bars = 60

    def analyse(self, symbol: str, market: MarketData, genome: dict) -> Signal:
        universe = market.symbols or [symbol]
        trends: list[Decimal] = []
        vols: list[Decimal] = []
        for name in universe:
            if not market.is_ready(name, self.minimum_bars):
                continue
            closes = market.closes(name)
            mom = momentum_pct(closes, 30)
            vol = volatility_pct(market.returns(name, 60))
            if mom is not None:
                trends.append(mom)
            if vol is not None:
                vols.append(vol)
        if not trends:
            return _silent(self.name, symbol, self.minimum_bars)

        breadth = sum(trends, ZERO) / D(len(trends))
        stress = (sum(vols, ZERO) / D(len(vols))) if vols else ZERO
        calm_vol = _genome(genome, "calm_vol_pct", 35)

        score = _clamp(breadth * D(2))
        if stress > calm_vol:
            # Risk-off: shrink conviction in both directions rather than
            # flipping it. A violent market is a reason to size down, not a
            # reason to be sure of the opposite view.
            score = _clamp(score / D(2))
        confidence = _clamp_confidence(min(D(75), abs(breadth) * D(6) + D(20)))
        return Signal(
            self.name,
            symbol,
            score,
            confidence,
            f"breadth {percent(breadth)}%, cross-sectional vol {percent(stress)}%",
        )


class OnChainAnalyst:
    """The crypto pod's extra seat.

    Without a node or an indexer there is no real on-chain data, so this reads
    the closest honest proxy: whether volume per unit of price movement is
    rising (accumulation) or thinning out (distribution). Named for the seat
    it fills; the note never claims to have seen a blockchain.
    """

    name = "onchain"
    minimum_bars = 45

    def analyse(self, symbol: str, market: MarketData, genome: dict) -> Signal:
        if not market.is_ready(symbol, self.minimum_bars):
            return _silent(self.name, symbol, self.minimum_bars)

        bars = market.history(symbol, 45)
        efforts: list[Decimal] = []
        for bar in bars:
            move = abs(bar.close - bar.open)
            if move == 0 or bar.close == 0:
                continue
            efforts.append(bar.volume / (move / bar.close))
        if len(efforts) < 10:
            return _silent(self.name, symbol, self.minimum_bars)

        early = sum(efforts[: len(efforts) // 2], ZERO) / D(len(efforts) // 2)
        late = sum(efforts[len(efforts) // 2 :], ZERO) / D(len(efforts) - len(efforts) // 2)
        if early == 0:
            return _silent(self.name, symbol, self.minimum_bars)

        shift = (late - early) / early
        score = _clamp(shift * D(60))
        confidence = _clamp_confidence(min(D(70), abs(shift) * D(90)))
        state = "accumulation" if shift > 0 else "distribution"
        return Signal(
            self.name,
            symbol,
            score,
            confidence,
            f"{state}: effort/result shifted {percent(shift * D(100))}% (volume proxy)",
        )


ANALYSTS: dict = {
    "technical": TechnicalAnalyst,
    "fundamental": FundamentalAnalyst,
    "sentiment": SentimentAnalyst,
    "macro": MacroAnalyst,
    "onchain": OnChainAnalyst,
}


def build_analysts(names) -> list:
    """Instantiate analysts by name, ignoring unknown seats loudly at config
    time rather than silently at run time."""
    out = []
    unknown = []
    for raw in names:
        key = str(raw).strip().lower().replace(" ", "").replace("-", "").replace("_", "")
        key = key.replace("analyst", "")
        if key in ANALYSTS:
            out.append(ANALYSTS[key]())
        else:
            unknown.append(raw)
    if unknown:
        raise ValueError(
            f"unknown analyst(s): {', '.join(map(str, unknown))}. "
            f"Available: {', '.join(sorted(ANALYSTS))}"
        )
    return out


def _clamp(value: Decimal) -> Decimal:
    return percent(max(D(-100), min(D(100), D(value))))


def _clamp_confidence(value: Optional[Decimal]) -> Decimal:
    if value is None:
        return ZERO
    return percent(max(ZERO, min(D(100), D(value))))


__all__ = [
    "ANALYSTS",
    "Analyst",
    "FundamentalAnalyst",
    "MacroAnalyst",
    "OnChainAnalyst",
    "SentimentAnalyst",
    "TechnicalAnalyst",
    "build_analysts",
]
