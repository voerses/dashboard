"""
s517 -- Macro Cluster (13 macro-responsive tokens, contrarian long-only)
=========================================================================

Signal: SP500 5-day ROC + VIX 14-day z-score for macro regime detection.
R204 validated these 13 tokens as primarily macro-driven across all 4
walk-forward quarters.

Thesis: When macro turns risk-off (sp500 down + vix spiking), crypto has
already sold off. Buying the dip at regime transitions captures the
mean-reversion bounce. Short side loses to structural long bias.

Entry (contrarian, daily rebalance):
  LONG when macro regime transitions to RISK-OFF:
    sp500_roc5 < 0 AND vix_z14 > 0.5 (fear spike = buy opportunity)
  Edge detection: only on FIRST day of new regime (not every day in regime)

Bias rules:
  - Macro data lagged 1 day (day D data available at D+1)
  - All signals computed from data <= T-1
  - Entry at T+1 open

Exit: trail 2.5 ATR, stop 3.0 ATR, max hold 14 days, 3x leverage.

L12M (skip-wf): +29.9%, Sharpe 0.69, Calmar 1.25, MaxDD -24.5%, 222 trades

Tokens: AAVE, ATOM, AVAX, DOGE, ETH, FIL, LINK, NEAR, PEPE, SEI, SUI, TIA, XRP

Data: data/alternative/macro/sp500.parquet, data/alternative/macro/vix.parquet
Market: PERP | Leverage: 3x | Hold: 1-14 days
"""

import os
import numpy as np
import pandas as pd
from engine import StrategyContext, StrategyResult, MarketType, rolling_zscore


# ======================================================================
#  TOKEN ALLOWLIST (R204 macro-responsive, 4/4 WF quarters)
# ======================================================================
ALLOWED_TOKENS = {
    'AAVE', 'ATOM', 'AVAX', 'DOGE', 'ETH', 'FIL', 'LINK',
    'NEAR', 'PEPE', 'SEI', 'SUI', 'TIA', 'XRP',
}

# ======================================================================
#  PARAMETERS
# ======================================================================

# -- Signal thresholds --
SP500_ROC_WINDOW = 5          # 5-day ROC for SP500 momentum
VIX_ZSCORE_WINDOW = 14        # 14-day z-score window for VIX
VIX_SHORT_THRESH = 0.5        # vix z-score threshold for short entry

# -- Trade management --
LEVERAGE = 3.0
STOP_MULT = 3.0               # hard stop in ATR
TRAIL_MULT = 2.5              # trailing stop in ATR
TARGET_MULT = 999.0           # no fixed TP -- trail captures profit
MAX_HOLD = 336                # 14 days in hours
MIN_HOLD = 24                 # 24h minimum hold
NO_STOP_BARS = 24             # 24h stop protection after entry
EDGE = 0.30
BREAKEVEN_ATR = 0.5

# -- Warmup --
WARMUP = 800                  # ~33 days of hourly bars

# -- Market --
MARKET = MarketType.PERP


# ======================================================================
#  MACRO DATA CACHE (module-level, loaded once)
# ======================================================================

_MACRO_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "alternative", "macro",
)

_macro_loaded = False
_sp500_roc5_daily = None      # pd.Series: date -> sp500 5-day ROC
_vix_z14_daily = None         # pd.Series: date -> vix 14-day z-score
_aligned_cache: dict = {}


def _load_macro():
    """Load SP500 and VIX macro data from parquets. No-op after first call."""
    global _macro_loaded, _sp500_roc5_daily, _vix_z14_daily
    if _macro_loaded:
        return
    _macro_loaded = True

    # -- SP500: 5-day ROC --
    sp500_path = os.path.join(_MACRO_DIR, "sp500.parquet")
    if os.path.exists(sp500_path):
        try:
            sp = pd.read_parquet(sp500_path, columns=["Date", "Close"])
            sp["Date"] = pd.to_datetime(sp["Date"]).dt.tz_localize(None)
            sp = sp.sort_values("Date").drop_duplicates(subset="Date", keep="last")
            sp = sp.set_index("Date")
            # 5-day ROC: (close - close_5d_ago) / close_5d_ago
            roc5 = sp["Close"].pct_change(SP500_ROC_WINDOW)
            _sp500_roc5_daily = roc5.dropna()
            print(f"  [s517] SP500 ROC5 loaded: {len(_sp500_roc5_daily)} days")
        except Exception as exc:
            print(f"  [s517] WARNING: Failed to load SP500: {exc}")
    else:
        print(f"  [s517] WARNING: SP500 parquet not found at {sp500_path}")

    # -- VIX: 14-day z-score --
    vix_path = os.path.join(_MACRO_DIR, "vix.parquet")
    if os.path.exists(vix_path):
        try:
            vx = pd.read_parquet(vix_path, columns=["Date", "Close"])
            vx["Date"] = pd.to_datetime(vx["Date"]).dt.tz_localize(None)
            vx = vx.sort_values("Date").drop_duplicates(subset="Date", keep="last")
            vx = vx.set_index("Date")
            # 14-day z-score of VIX close
            vix_vals = vx["Close"].values.astype(np.float64)
            vix_z = rolling_zscore(vix_vals, VIX_ZSCORE_WINDOW)
            _vix_z14_daily = pd.Series(vix_z, index=vx.index).dropna()
            print(f"  [s517] VIX z14 loaded: {len(_vix_z14_daily)} days")
        except Exception as exc:
            print(f"  [s517] WARNING: Failed to load VIX: {exc}")
    else:
        print(f"  [s517] WARNING: VIX parquet not found at {vix_path}")


def _get_macro_aligned(idx_1h: pd.DatetimeIndex):
    """Align daily macro signals to 1H index with 1-day lag.

    BIAS FIX: Macro data for day D is only available at D+1. We shift
    the daily index by +1 day so that day D's signal is first used on
    D+1 bars. This prevents look-ahead bias.

    Returns (sp500_roc5, vix_z14) as numpy arrays aligned to idx_1h.
    """
    cache_key = (len(idx_1h), idx_1h[0], idx_1h[-1])
    if cache_key in _aligned_cache:
        return _aligned_cache[cache_key]

    n = len(idx_1h)
    sp_out = np.full(n, np.nan, dtype=np.float64)
    vix_out = np.full(n, np.nan, dtype=np.float64)

    dates_norm = idx_1h.normalize()

    if _sp500_roc5_daily is not None:
        # Shift by 1 day: data for day D available at D+1
        shifted = _sp500_roc5_daily.copy()
        shifted.index = shifted.index + pd.Timedelta(days=1)
        aligned = shifted.reindex(dates_norm, method="ffill")
        aligned.index = idx_1h
        sp_out = aligned.values.astype(np.float64)

    if _vix_z14_daily is not None:
        shifted = _vix_z14_daily.copy()
        shifted.index = shifted.index + pd.Timedelta(days=1)
        aligned = shifted.reindex(dates_norm, method="ffill")
        aligned.index = idx_1h
        vix_out = aligned.values.astype(np.float64)

    result = (sp_out, vix_out)
    _aligned_cache[cache_key] = result
    return result


# ======================================================================
#  STRATEGY
# ======================================================================

def strategy(ctx: StrategyContext) -> StrategyResult:
    """Macro Cluster -- risk-on/risk-off regime trading for macro-responsive tokens."""
    _load_macro()

    close = ctx.ind_1h['close']
    n = len(close)

    # -- Empty result helper --
    def _empty():
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.zeros(n, dtype=np.int8),
            market_type=MARKET,
            leverage=LEVERAGE,
            stop_mult=STOP_MULT,
            trail_mult=TRAIL_MULT,
            target_mult=TARGET_MULT,
            no_stop_bars=NO_STOP_BARS,
            min_hold=MIN_HOLD,
            max_hold=MAX_HOLD,
            edge=EDGE,
            name='s517_macro_cluster',
            breakeven_atr=BREAKEVEN_ATR,
        )

    # Token allowlist filter
    if ctx.ticker not in ALLOWED_TOKENS:
        return _empty()

    # No macro data loaded
    if _sp500_roc5_daily is None or _vix_z14_daily is None:
        return _empty()

    # Align macro signals to 1H index (with 1-day lag)
    sp500_roc5, vix_z14 = _get_macro_aligned(ctx.idx_1h)

    # Replace NaN with neutral values (no signal)
    sp500_roc5 = np.nan_to_num(sp500_roc5, nan=0.0)
    vix_z14 = np.nan_to_num(vix_z14, nan=0.0)

    # Day boundaries -- entry only on first bar of each new day
    dates = ctx.idx_1h.normalize()
    day_change = np.zeros(n, dtype=bool)
    day_change[0] = True
    day_change[1:] = dates[1:] != dates[:-1]

    # Regime classification (every bar)
    # +1 = risk-on, -1 = risk-off, 0 = neutral
    regime = np.where((sp500_roc5 > 0) & (vix_z14 < 0), 1,
                      np.where((sp500_roc5 < 0) & (vix_z14 > VIX_SHORT_THRESH), -1,
                               0)).astype(np.int8)

    # Edge detection: only enter on FIRST day of a new regime
    # (prevents re-entering the same direction every day)
    regime_prev = np.roll(regime, 24)  # compare to prior day (24h bars)
    regime_prev[:24] = 0
    regime_change = regime != regime_prev

    # CONTRARIAN LONG-ONLY: when macro turns risk-off (sp500 down +
    # vix spiking), crypto has already sold off -- LONG the bounce.
    # Short side loses to structural long bias so we skip it.

    # LONG: regime just turned risk-OFF (contrarian bounce)
    long_signal = (regime == -1) & regime_change & day_change

    entry = long_signal
    direction = np.ones(n, dtype=np.int8)

    # Suppress warmup period
    entry[:WARMUP] = False

    return StrategyResult(
        entry_mask=entry,
        direction=direction,
        market_type=MARKET,
        leverage=LEVERAGE,
        stop_mult=STOP_MULT,
        trail_mult=TRAIL_MULT,
        target_mult=TARGET_MULT,
        no_stop_bars=NO_STOP_BARS,
        min_hold=MIN_HOLD,
        max_hold=MAX_HOLD,
        edge=EDGE,
        name='s517_macro_cluster',
        breakeven_atr=BREAKEVEN_ATR,
    )
