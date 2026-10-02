"""Every R3 number, frozen. See PREREG_R3.md; this file is what it describes.

The forward ledger hashes this file together with the strategy code and
refuses to continue if the hash changes without a recorded reason.
"""

START_EQUITY = 100_000.0

# costs, per side, as fractions
HALF_SPREAD_LARGE = 1e-4        # benchmark ETFs + top 100 universe names by $ volume
HALF_SPREAD_SMALL = 3e-4        # every other stock
LARGE_RANK = 100
SLIPPAGE = 2e-4
OPTION_FEE_PER_CONTRACT = 0.03
OPTION_MODELED_SPREAD = 0.03    # +/- off the bar close when no quote was captured

# universe
MIN_PRICE = 10.0
MIN_DOLLAR_VOLUME = 50e6
DOLLAR_VOLUME_DAYS = 60

# R3-A
A_CORE = 0.50
A_LOOKBACK = 126
A_SKIP = 5
A_PERCENTILE = 0.80
A_TREND_SMA = 200
A_NAMES = 20
A_VOL_DAYS = 63
A_NAME_CAP = 0.10               # of the sleeve

# R3-B
B_MIN_REACTION = 0.04
B_TOP_SHARE = 0.10
B_REACTION_WINDOW = 63
B_ACCEL_DAYS = 63
B_TREND_SMA = 200
B_SLOTS = 20
B_SLOT_WEIGHT = 0.05
B_HOLD_DAYS = 60
B_AFTER_HOURS = "16:00"          # America/New_York

# R3-C
C_TREND_SMA = 200
C_DELTA_UP = 1.2
C_DELTA_DOWN = 0.5
C_DTE_MIN, C_DTE_MAX, C_DTE_PREFERRED = 60, 120, 90
C_TARGET_DELTA = 0.70
C_ROLL_DTE = 30
C_DELTA_BAND = (0.50, 0.90)
C_RESIZE_TOLERANCE = 0.15
C_PREMIUM_CAP = 0.25

# benchmarks
MOM_ETFS = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "LQD", "HYG",
            "GLD", "SLV", "DBC", "VNQ", "UUP"]
MOM_LOOKBACK, MOM_SKIP, MOM_TOP = 252, 5, 3
