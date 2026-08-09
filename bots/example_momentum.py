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
}

UNIVERSE = ["SPY", "QQQ", "IWM"]
