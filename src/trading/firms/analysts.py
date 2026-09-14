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
from ..indicators import average_range, ibs, momentum_pct, rsi, sma, volatility_pct, zscore
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


class ReversionAnalyst:
    """Short-horizon mean reversion: RSI(2), Internal Bar Strength, and the
    pullback from a recent high, arbitrated by ``rsi_entry``, ``ibs_entry``
    and ``pullback_atr``.

    Those three genes are VERITAS's own published parameters — the only
    strategy in this village with real pre-registered evidence behind it (5/5
    criteria, p<0.002 against 500 random-entry runs; see
    ``bots/veritas_reversion.py`` and the sibling repo's ``veritas_bot.py``).
    Before this seat existed the genes sat in the vocabulary and nothing read
    them: a submission carrying them was scored by ``TechnicalAnalyst`` and
    ``FundamentalAnalyst`` alone on whatever those two default to, which is a
    verdict on a different strategy wearing VERITAS's name.

    Connors' rule is a conjunction, not either leg alone — RSI(2) below
    threshold *and* IBS below threshold. Loosening that to an OR is the first
    thing a tuner would try, and exactly what the pre-registration forbids. So
    the two legs are required to agree before the score commits to a
    direction; when they disagree the reading collapses toward silence rather
    than averaging into a conviction neither leg alone earned. The pullback
    distance is a third, independent confirmation — real, but not required by
    the published variant that was actually tested.
    """

    name = "reversion"
    #: RSI(2) needs 3 closes; the pullback and range legs need 25. 30 leaves
    #: a small margin rather than sitting exactly on the edge of "computable".
    minimum_bars = 30

    def analyse(self, symbol: str, market: MarketData, genome: dict) -> Signal:
        if not market.is_ready(symbol, self.minimum_bars):
            return _silent(self.name, symbol, self.minimum_bars)

        bars = market.history(symbol, self.minimum_bars)
        closes = [b.close for b in bars]
        highs = [b.high for b in bars]
        lows = [b.low for b in bars]

        rsi2 = rsi(closes, 2)
        strength = ibs(highs[-1], lows[-1], closes[-1])
        if rsi2 is None or strength is None:
            return _silent(self.name, symbol, self.minimum_bars)

        rsi_threshold = _genome(genome, "rsi_entry", 10)
        ibs_threshold = _genome(genome, "ibs_entry", "0.3")
        atr_mult = _genome(genome, "pullback_atr", "2.5")

        # Positive means oversold (bullish) on that leg, negative overbought.
        rsi_gap = rsi_threshold - rsi2
        ibs_gap = (ibs_threshold - strength) * D(100)

        recent_high = max(highs[-10:])
        avg_rng = average_range(highs, lows, 25)
        pullback_gap = ZERO
        if avg_rng is not None and avg_rng > 0:
            pullback_gap = recent_high - closes[-1] - atr_mult * avg_rng

        agree = (rsi_gap > 0) == (ibs_gap > 0)
        if agree:
            core = (rsi_gap * D(3) + ibs_gap) / D(4)
        else:
            # The conjunction did not fire. Damped rather than zeroed: a
            # near-miss on one leg is not the same as two legs in open
            # contradiction, and the debate can still see which way this
            # leaned even though VERITAS itself would not have entered.
            core = (rsi_gap + ibs_gap) / D(6)

        confirm = _clamp(pullback_gap / D(4)) if avg_rng else ZERO
        score = _clamp(core + confirm * D("0.25"))

        confidence = _clamp_confidence(
            (abs(rsi_gap) * D(2) + abs(ibs_gap)) / D(3) + (D(15) if agree else ZERO)
        )
        verdict = "oversold" if score > 0 else "overbought" if score < 0 else "neutral"
        return Signal(
            self.name,
            symbol,
            score,
            confidence,
            f"{verdict}: RSI(2) {rsi2} vs {rsi_threshold}, IBS {strength} vs {ibs_threshold}"
            f"{', legs agree' if agree else ', legs disagree'}",
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


class SignalAnalyst:
    """The seat where somebody else's scanner sits.

    Every other analyst on this list computes its own number from the bars.
    This one repeats what the configured scanners published — see
    ``src/trading/signals.py`` for what a scanner is and why it cannot do
    anything except talk.

    Two rules make that safe to listen to. It reads **only** the readings
    stamped with the bar it is standing on, so a scanner that stopped running
    goes silent immediately instead of voting from the grave. And it is one
    seat: a scanner screaming +100 wins a share of one debate at one firm, and
    still has to get past the trader, the risk manager, the conscience and the
    gate, none of which know it exists.

    With no board wired up, or no scanner speaking, the answer is confidence 0
    — silence, which the debate already knows how to hear.
    """

    name = "signals"
    minimum_bars = 0
    needs_board = True

    #: Publishers this seat listens to. `None` means "everything not claimed by
    #: another seat" — see EXCLUSIVE below.
    publishers = None
    #: The gene that scales this seat's confidence. One per seat, so a desk can
    #: believe its price scanner and ignore its news feed, or the reverse.
    trust_gene = "signal_trust"
    quiet_note = "no scanner spoke on this bar"

    def __init__(self, board=None):
        self.board = board

    def analyse(self, symbol: str, market: MarketData, genome: dict) -> Signal:
        if self.board is None:
            return Signal(self.name, symbol, ZERO, ZERO, "no signal board wired up")
        reading = self.board.reading(
            symbol, market.as_of(),
            publishers=self.publishers,
            exclude=None if self.publishers else EXCLUSIVE,
        )
        if reading is None:
            return Signal(self.name, symbol, ZERO, ZERO, self.quiet_note)

        # How much of a stranger's opinion this firm wants. A gene rather than
        # a constant because trusting an imported screener is a choice the
        # evolver is allowed to turn down, and turning it to 0 mutes the seat
        # without editing the firm's analyst list.
        trust = _genome(genome, self.trust_gene, 100) / D(100)
        return Signal(
            self.name,
            symbol,
            reading.score,
            _clamp_confidence(reading.confidence * trust),
            reading.note,
        )


class NewsAnalyst(SignalAnalyst):
    """The news desk's seat, kept separate from the scanners' on purpose.

    Every publisher used to be averaged into a single number before any firm
    saw it, so a headline, a moving average and a bankruptcy postmortem
    arrived as one opinion — and `signal_trust` being one gene meant a desk
    had to weight all three identically or mute all three together.

    They are not the same kind of evidence and they are not equally useful to
    the same desk. A news feed may be worth hearing on memecoins and worthless
    on Treasuries; a price screener has the opposite problem. Which of them a
    firm believes is exactly what the evolver is for, and it cannot answer the
    question while they arrive pre-mixed.

    So this seat hears `news` and nothing else, and scales on its own gene.
    """

    name = "news"
    publishers = ("news",)
    trust_gene = "news_trust"
    quiet_note = "no news on this bar"


class ScribeAnalyst(SignalAnalyst):
    """The dead firms' seat. Hears only the scribe.

    Separate for the same reason as the news, and for one more: what the
    scribe publishes is survivorship-shaped evidence about strategies that
    already failed, which is a different claim from "this name is going up".
    A desk should be able to weigh a warning from a corpse differently from a
    headline, and now it can.
    """

    name = "scribe"
    publishers = ("scribe",)
    trust_gene = "scribe_trust"
    quiet_note = "the scribe said nothing on this bar"


#: Publishers that have a seat of their own. The general `signals` seat
#: excludes them so nothing is heard twice — once in its own right and again
#: inside an average — which would let one source vote in two places.
EXCLUSIVE: tuple = ("news", "scribe")


ANALYSTS: dict = {
    "technical": TechnicalAnalyst,
    "fundamental": FundamentalAnalyst,
    "reversion": ReversionAnalyst,
    "sentiment": SentimentAnalyst,
    "macro": MacroAnalyst,
    "onchain": OnChainAnalyst,
    "signals": SignalAnalyst,
    "news": NewsAnalyst,
    "scribe": ScribeAnalyst,
}

# What people write when they mean `signals`. Aliases rather than extra entries
# in ANALYSTS so the error message still lists each seat once.
ALIASES: dict = {"signal": "signals", "scanner": "signals", "scanners": "signals",
                 "headlines": "news", "newsdesk": "news",
                 "meanreversion": "reversion", "rsi2": "reversion"}


def build_analysts(names, board=None) -> list:
    """Instantiate analysts by name, ignoring unknown seats loudly at config
    time rather than silently at run time.

    ``board`` is the published-signal board, handed to the seats that need one.
    It is optional so that a firm built outside the ecosystem — a backtest, a
    test — still assembles, and the signals seat is simply silent there.
    """
    out = []
    unknown = []
    for raw in names:
        key = str(raw).strip().lower().replace(" ", "").replace("-", "").replace("_", "")
        key = key.replace("analyst", "")
        key = ALIASES.get(key, key)
        if key in ANALYSTS:
            seat = ANALYSTS[key]
            out.append(seat(board) if getattr(seat, "needs_board", False) else seat())
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
    "ALIASES",
    "ANALYSTS",
    "Analyst",
    "FundamentalAnalyst",
    "MacroAnalyst",
    "OnChainAnalyst",
    "ReversionAnalyst",
    "SentimentAnalyst",
    "SignalAnalyst",
    "TechnicalAnalyst",
    "build_analysts",
]
