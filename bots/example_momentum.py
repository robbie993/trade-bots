"""An example bot. Copy this, change the numbers, drop it in `bots/`.

Nothing here is executed by the village: the court reads the syntax tree and
takes the two literals below. That is the whole contract.
"""

# A slow-confirmation trend follower: a short fast window against a long slow
# one, and a heavy momentum bias.
GENOME = {
    "fast_window": 8,
    "slow_window": 60,
    "rsi_window": 14,
    "trend_bias": 88,
    "value_window": 60,
    "fair_band_pct": 6,
    "calm_vol_pct": 30,
    # How far a position may fall from what it cost before the firm closes it
    # without a debate. 0 turns the stop off — which is what every firm here
    # did before this gene existed, and is how you end up holding losers eight
    # times the size of your winners.
    "stop_loss_pct": 0,
}

UNIVERSE = ["SPY", "QQQ", "IWM"]
