"""
s401 Volatility Breakout (R160/R167-Optimized, Faithful Reproduction)

Port of R168's R160 component with R167 optimal params to v4 engine.
Original achieved (standalone): +74.2% 12M, -10.9% MaxDD, 5205 trades, 49% WR

Key features:
  - BB(20, 2.0) upper band breakout on 4H timeframe
  - Volume confirmation (vol_ratio > 1.5)
  - 7-day momentum filter (positive return over 7 days)
  - SMA(15) trail stop on 4H bars (= SMA(60) on 1H bars)
  - Partial profit at 3x ATR
  - Max 5 concurrent positions (via max_trade_pct)
  - No regime filter (R167 found regime filter hurts)

Market: PERP
Status: EXPERIMENTAL (R172 validated in standalone, porting to v4)
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    rolling_mean)


# ── R167-Optimized Configuration ─────────────────────────────────
BB_PERIOD = 20              # Bollinger Band period (4H bars)
BB_STD = 2.0                # BB standard deviation multiplier
VOL_CONFIRM = 1.5           # Volume ratio threshold
MOM_DAYS = 7                # Momentum lookback in days
MOM_BARS_4H = MOM_DAYS * 6  # 42 four-hour bars = 7 days
TRAIL_SMA = 15              # SMA period for trail stop (4H bars)
TRAIL_SMA_1H = TRAIL_SMA * 4  # 60 one-hour bars (equivalent)
MAX_POS = 5                 # Max concurrent positions
POS_SIZE = 0.20             # Fixed 20% per position (not dynamic 1/N)
PARTIAL_TP_ATR = 3.0        # Partial profit at 3x ATR
WARMUP = 300                # Warmup bars (1H) for indicators
LEVERAGE = 2.5              # Matching R172 standalone

# ── Sizing overrides (read by portfolio_backtest.py) ─────────────
# Aggressive Kelly params so max_trade_pct becomes the binding cap.
# This ensures each position gets ~20% of equity as intended.
SIZING_OVERRIDES = {
    "kelly_mult_override": 0.50,   # Max allowed, fixed (skip ADV curve)
    "target_vol": 0.05,            # Higher target_vol = larger positions
    "cap_pct_override": 0.15,      # Lift capital cap so it doesn't bind
}

# ── Engine feature overrides (read by portfolio_backtest.py) ──────
MAX_CONCURRENT_PER_TOKEN = 3       # Allow multiple breakout re-entries per token
DD_SCALING = [
    (0.05, 0.75),   # 5% DD → 75% sizing
    (0.10, 0.50),   # 10% DD → 50% sizing
    (0.15, 0.25),   # 15% DD → 25% sizing
    (0.20, 0.0),    # 20% DD → stop trading
]


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Volatility breakout: BB upper band breakout with SMA trail stop."""
    close_1h = ctx.ind_1h["close"]
    n = len(close_1h)

    # ── Compute 4H BB and align to 1H ───────────────────────────
    # Use 4H indicators for BB breakout detection
    close_4h = ctx.ind_4h["close"]
    bb_upper_4h = ctx.ind_4h["bb_upper"]
    n_4h = len(close_4h)

    # Compute 4H volume ratio for confirmation
    vol_ratio_4h = ctx.ind_4h.get("vol_ratio", np.ones(n_4h))

    # Compute 7-day momentum on 4H bars (42 bars = 7 days)
    mom_4h = np.zeros(n_4h, dtype=np.float64)
    close_4h_f64 = close_4h.astype(np.float64)
    if n_4h > MOM_BARS_4H:
        mom_4h[MOM_BARS_4H:] = (
            close_4h_f64[MOM_BARS_4H:]
            / np.maximum(close_4h_f64[:-MOM_BARS_4H], 1e-10)
            - 1.0
        )

    # Align 4H signals to 1H timeframe (forward-fill)
    bb_upper_1h = ctx.align_4h_to_1h(bb_upper_4h)
    vol_ratio_1h = ctx.align_4h_to_1h(vol_ratio_4h)
    mom_1h = ctx.align_4h_to_1h(mom_4h)

    # ── Entry signal ─────────────────────────────────────────────
    # BB breakout: 1H close > 4H BB upper band
    bb_breakout = close_1h > np.nan_to_num(bb_upper_1h, nan=1e18)

    # Volume confirmation
    vol_ok = np.nan_to_num(vol_ratio_1h, nan=0.0) > VOL_CONFIRM

    # Momentum filter: 7d return > 0 (no regime filter per R167)
    mom_ok = np.nan_to_num(mom_1h, nan=0.0) > 0

    # Compose entry
    entry = bb_breakout & vol_ok & mom_ok
    entry[:WARMUP] = False

    # ── SMA(15) trail stop on 4H bars aligned to 1H ─────────────
    # Compute SMA(15) of 4H close prices
    sma_15_4h = rolling_mean(close_4h_f64, TRAIL_SMA)
    # Align to 1H
    sma_trail_1h = ctx.align_4h_to_1h(sma_15_4h.astype(np.float32))
    # NaN guard: replace NaN with 0 (handler skips NaN/0 values)
    sma_trail_1h = np.nan_to_num(sma_trail_1h, nan=0.0).astype(np.float32)

    # ── Build result ─────────────────────────────────────────────
    return StrategyResult(
        entry_mask=entry,
        direction=np.ones(n, dtype=np.int8),  # long only
        market_type=MarketType.PERP,
        leverage=LEVERAGE,

        # Exit: SMA(15) trail is the PRIMARY and ONLY exit mechanism (matching R168)
        # No stop loss — SMA trail acts as the stop
        stop_mult=999.0,          # Disable ATR stop (SMA trail handles exits)
        trail_mult=999.0,         # Disable ATR trail (SMA trail handles it)
        target_mult=999.0,        # No fixed target
        no_stop_bars=4,           # Small buffer before SMA trail activates
        min_hold=4,               # Minimum 4 hours (avoid noise exits)
        max_hold=1440,            # Maximum 60 days (matching R168)

        # SMA trail stop (uses custom exit handler)
        sma_trail_vals=sma_trail_1h,

        # No partial profit (R168 did not use partial TP)
        partial_tp_atr=0.0,

        # Position sizing: target 20% of equity per position
        # SIZING_OVERRIDES above make Kelly produce >= 20%, so max_trade_pct binds
        edge=0.40,
        max_trade_pct=POS_SIZE,   # Hard cap: 20% of equity per position
        cap_multiplier=3.0,       # Lift capital_cap above max_trade_pct
        breakeven_atr=0.0,        # No breakeven (SMA trail is the only exit)

        # No regime exit (R167 found regime filter hurts)
        exit_regimes=set(),

        name="s401_vol_breakout_r160",
    )
