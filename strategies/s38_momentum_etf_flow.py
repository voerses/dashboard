"""
s38 Momentum ETF Flow — s11 + N2 ETF Flow Sizing Overlay

Wraps s11_momentum_burst with BTC ETF flow-based position sizing:
  N2: Scale position by 3-day cumulative BTC ETF net inflow.
      Strong inflows (>$200M 3d) → full position (1.0x).
      Moderate inflows (0-$200M) → reduced (0.75x).
      Outflows (negative) → small position (0.3x).

Uses T-1 daily flow to avoid look-ahead bias (flows published end-of-day).
For dates before ETF data availability (pre Dec 2024), defaults to 1.0.

Does NOT modify s11. Calls s11.strategy(), then applies size_multiplier.
Base strategy signals, entries, and exits are unchanged.

Gate 0: PASS — ETF flows represent marginal buyer/seller pressure
Gate 2: PASS — no existing overlay uses external (non-price) data
Gate 3O: PASS — uses size_multiplier field in StrategyResult

Status: EXPERIMENTAL
"""

import os
import numpy as np
import pandas as pd
from engine import StrategyContext, StrategyResult
from strategies.s11_momentum_burst import strategy as s11_strategy

# N2 parameters
FLOW_WINDOW = 3        # 3-day rolling sum of daily flows (millions USD)
FLOW_HIGH_THRESH = 200  # >$200M 3d inflow → full position
FLOW_LOW_THRESH = 0     # <$0 (net outflow) → minimal position
SIZE_FULL = 1.0         # full position when flows strong
SIZE_REDUCED = 0.75     # moderate flows
SIZE_MINIMAL = 0.3      # outflows — keep small exposure

# Load ETF flow data once at module level
_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ETF_FLOW_PATH = os.path.join(_PROJECT_DIR, 'data', 'etf_flows', 'btc_etf_flows.parquet')

# Pre-computed dense array: index by (day_ordinal - base_ordinal) → size_mult
_flow_arr = None      # np.ndarray, dense daily mult values
_flow_base_ord = 0    # ordinal of first day in array
_flow_loaded = False


def _load_etf_flows():
    """Load ETF flows, compute 3d rolling sum, build dense ordinal→mult array."""
    global _flow_arr, _flow_base_ord, _flow_loaded
    if _flow_loaded:
        return

    _flow_loaded = True
    if not os.path.exists(_ETF_FLOW_PATH):
        _flow_arr = np.array([], dtype=np.float64)
        return

    df = pd.read_parquet(_ETF_FLOW_PATH)
    df = df.sort_values('date')
    dates = pd.DatetimeIndex(df['date'])
    flows = df['total_inflow_mm'].values.astype(np.float64)

    # 3-day rolling sum (vectorized via cumsum)
    cs = np.nancumsum(flows)
    flow_3d = np.empty_like(flows)
    for i in range(min(FLOW_WINDOW, len(flows))):
        flow_3d[i] = cs[i]
    if len(flows) > FLOW_WINDOW:
        flow_3d[FLOW_WINDOW:] = cs[FLOW_WINDOW:] - cs[:-FLOW_WINDOW]

    # Convert to size multipliers at data points
    data_mults = np.where(
        flow_3d > FLOW_HIGH_THRESH, SIZE_FULL,
        np.where(flow_3d > FLOW_LOW_THRESH, SIZE_REDUCED, SIZE_MINIMAL)
    )

    # Build dense array covering all days from first to last (with forward-fill)
    ordinals = np.array([d.toordinal() for d in dates], dtype=np.int64)
    _flow_base_ord = int(ordinals[0])
    n_days = int(ordinals[-1] - ordinals[0]) + 1
    _flow_arr = np.ones(n_days, dtype=np.float64)  # default 1.0

    # Fill known days then forward-fill gaps (vectorized via pandas ffill)
    indices = (ordinals - _flow_base_ord).astype(np.intp)
    _flow_arr[:] = np.nan  # mark all as missing
    _flow_arr[indices] = data_mults
    # Forward-fill NaNs: use pandas for efficiency
    s = pd.Series(_flow_arr)
    _flow_arr = s.ffill().fillna(1.0).values


def _etf_flow_size_mult(idx_1h) -> np.ndarray:
    """Map daily ETF flow signal to hourly bars with T-1 lag. Dense array lookup."""
    _load_etf_flows()
    n = len(idx_1h)
    mult = np.ones(n, dtype=np.float64)

    if _flow_arr is None or len(_flow_arr) == 0:
        return mult

    # Convert hourly timestamps to day ordinals with T-1 lag (fully vectorized)
    epoch_ord = 719163  # date(1970,1,1).toordinal()
    ts_ns = idx_1h.astype(np.int64)
    day_ords = (ts_ns // 86_400_000_000_000 + epoch_ord - 1).astype(np.int64)

    # Map to array indices
    arr_idx = day_ords - _flow_base_ord
    n_days = len(_flow_arr)

    # Vectorized: clamp to valid range, then lookup
    valid = (arr_idx >= 0) & (arr_idx < n_days)
    clamped = np.clip(arr_idx, 0, n_days - 1)
    mult[valid] = _flow_arr[clamped[valid]]
    # Days after data ends: use last known value
    after = arr_idx >= n_days
    if np.any(after):
        mult[after] = _flow_arr[-1]

    return mult


def strategy(ctx: StrategyContext) -> StrategyResult:
    """s11 momentum burst with ETF flow sizing overlay."""
    # Get base s11 result (unchanged)
    result = s11_strategy(ctx)

    # Compute ETF flow sizing
    size_mult = _etf_flow_size_mult(ctx.idx_1h)

    return StrategyResult(
        entry_mask=result.entry_mask,
        direction=result.direction,
        stop_mult=result.stop_mult,
        trail_mult=result.trail_mult,
        target_mult=result.target_mult,
        no_stop_bars=result.no_stop_bars,
        min_hold=result.min_hold,
        max_hold=result.max_hold,
        edge=result.edge,
        exit_regimes=result.exit_regimes,
        name='s38_momentum_etf_flow',
        max_trade_pct=result.max_trade_pct,
        size_multiplier=size_mult,
        breakeven_atr=0.5,
    )
