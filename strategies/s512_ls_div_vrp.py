"""
s512 — s506 L/S Divergence + VRP Sizing Overlay
=================================================
Class C Overlay: wraps s506 with VRP (Volatility Risk Premium) sizing.

VRP = Implied Vol (BTC DVOL) - Realized Vol (20d).
When VRP is high (vol overpriced), market is complacent → size up.
When VRP is low/negative (vol cheap), turbulence expected → size down.

This is orthogonal to the L/S divergence signal (rho=0.031 proven).
VRP overlay added +0.498 OOS dSharpe on top of positioning in research.

VRP sizing rules (from proven research):
  z > 1.0:         1.3x (complacent market)
  -0.5 < z <= 1.0: 1.0x (normal)
  -1.5 < z <= -0.5: 0.5x (caution — vol cheap)
  z <= -1.5:       0.3x (extreme turbulence)

Data: BTC DVOL daily from Deribit + realized vol from BTC price.
Applied as market-level regime indicator to all tokens.

Base: s506_ls_divergence (contrarian MR, perp, 1x)
Status: EXPERIMENTAL (Gate 3O prototype)
"""

import os
import json
import numpy as np
import pandas as pd
from strategies.s506_ls_divergence import strategy as base_strategy

# VRP sizing thresholds (proven in research/vrp_sizing_overlay_test.py)
VRP_SCHEDULE = [
    (1.0, 1.3),     # z > 1.0: complacent → size up
    (-0.5, 1.0),    # -0.5 < z <= 1.0: normal
    (-1.5, 0.5),    # -1.5 < z <= -0.5: caution
    (None, 0.3),    # z <= -1.5: extreme → minimum size
]

DVOL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "alternative", "deribit_options", "dvol", "btc_dvol_daily.json",
)

RV_WINDOW = 20       # 20-day realized vol
ZSCORE_WINDOW = 60   # 60-day rolling z-score of VRP

# Module-level cache
_vrp_daily: pd.Series = None
_vrp_loaded: bool = False
_vrp_aligned_cache: dict = {}


def _load_vrp_data():
    """Load BTC DVOL and compute daily VRP z-score. Cached at module level."""
    global _vrp_daily, _vrp_loaded
    if _vrp_loaded:
        return
    _vrp_loaded = True

    if not os.path.exists(DVOL_PATH):
        print(f"  [s512] WARNING: DVOL not found at {DVOL_PATH}")
        return

    # Load DVOL
    with open(DVOL_PATH) as f:
        data = json.load(f)

    records = []
    for row in data:
        ts = pd.Timestamp(row[0], unit='ms')
        records.append({'date': ts, 'dvol': row[4]})

    dvol_df = pd.DataFrame(records).set_index('date').sort_index()
    dvol_df = dvol_df[~dvol_df.index.duplicated(keep='last')]

    # Load BTC daily close for realized vol
    btc_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "spot", "1h_cache", "BTC_1h.parquet",
    )
    if not os.path.exists(btc_path):
        # Try perp
        btc_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "perp", "1h_cache", "BTC_1h.parquet",
        )

    if not os.path.exists(btc_path):
        print(f"  [s512] WARNING: BTC price data not found")
        return

    btc = pd.read_parquet(btc_path)
    btc.index = pd.to_datetime(btc.index)
    btc_daily_close = btc['close'].resample('D').last().dropna()

    # Realized vol: 20d rolling std of log returns, annualized
    log_ret = np.log(btc_daily_close / btc_daily_close.shift(1))
    rv = log_ret.rolling(RV_WINDOW, min_periods=RV_WINDOW).std() * np.sqrt(365) * 100

    # Align DVOL and RV on common dates
    common_idx = dvol_df.index.intersection(rv.dropna().index)
    if len(common_idx) == 0:
        print("  [s512] WARNING: No overlapping DVOL/RV dates")
        return

    dvol_aligned = dvol_df.loc[common_idx, 'dvol']
    rv_aligned = rv.loc[common_idx]

    # VRP = implied - realized
    vrp = dvol_aligned - rv_aligned

    # Rolling z-score
    vrp_mean = vrp.rolling(ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW).mean()
    vrp_std = vrp.rolling(ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW).std()
    vrp_z = (vrp - vrp_mean) / vrp_std.replace(0, np.nan)

    _vrp_daily = vrp_z.dropna()
    print(f"  [s512] Loaded VRP z-score: {len(_vrp_daily)} daily values, "
          f"{_vrp_daily.index.min().date()} to {_vrp_daily.index.max().date()}")


def _vrp_size_multiplier(idx_1h: pd.DatetimeIndex) -> np.ndarray:
    """Map daily VRP z-score to hourly size multiplier via ffill."""
    cache_key = (len(idx_1h), idx_1h[0], idx_1h[-1])
    if cache_key in _vrp_aligned_cache:
        return _vrp_aligned_cache[cache_key]

    n = len(idx_1h)
    if _vrp_daily is None or len(_vrp_daily) == 0:
        result = np.ones(n, dtype=np.float64)
        _vrp_aligned_cache[cache_key] = result
        return result

    # Align daily VRP z to hourly index via ffill
    aligned = _vrp_daily.reindex(idx_1h.normalize(), method='ffill')
    aligned.index = idx_1h
    z = aligned.values.astype(np.float64)

    # Map z-score to size multiplier
    mult = np.full(n, 1.0, dtype=np.float64)
    mult[z > 1.0] = 1.3
    # -0.5 < z <= 1.0 stays 1.0
    mult[(z <= -0.5) & (z > -1.5)] = 0.5
    mult[z <= -1.5] = 0.3
    # NaN z-scores get 1.0 (no adjustment)
    mult[np.isnan(z)] = 1.0

    _vrp_aligned_cache[cache_key] = mult
    return mult


def strategy(ctx):
    """s506 + VRP sizing overlay."""
    _load_vrp_data()
    result = base_strategy(ctx)

    size_mult = _vrp_size_multiplier(ctx.idx_1h)

    from engine import StrategyResult
    return StrategyResult(
        entry_mask=result.entry_mask,
        direction=result.direction,
        market_type=result.market_type,
        leverage=result.leverage,
        stop_mult=result.stop_mult,
        trail_mult=result.trail_mult,
        target_mult=result.target_mult,
        no_stop_bars=result.no_stop_bars,
        min_hold=result.min_hold,
        max_hold=result.max_hold,
        edge=result.edge,
        name='s512_ls_div_vrp',
        size_multiplier=size_mult,
        breakeven_atr=0.5,
    )
