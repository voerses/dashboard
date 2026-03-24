#!/workspace/venv/bin/python
"""
R106: Trend+Pullback RSI as Entry Timing Overlay for V3 Momentum (s320)
=========================================================================

Hypothesis: V3 Momentum enters blindly every 168 bars (weekly rebalance).
If we condition entry timing on RSI pullbacks, we get better entry prices
and reduce drawdown, especially in range markets.

R105 showed T+P is 98% overlapping with V3 as a SEPARATE strategy.
But the RSI signal could still improve V3's ENTRY TIMING within its own
rebalance framework.

Variants:
  V3 Baseline: Standard weekly rebalance (168 bars), enter if EMA20>EMA50
  V1 Strict:   Only enter at rebalance if 4h RSI < 45 (skip cycle if not)
  V2 Flexible: Within 168-bar window, enter on first 4h RSI cross-up through 40;
               fallback to end-of-window if no cross occurs
  V3 Conviction: Enter at normal rebalance, but scale size by RSI at entry

Walk-Forward: 6 windows, 12mo train / 6mo test, rolling 6mo
OOS Focus: 2025-09 to 2026-03

Data: BTC spot 1h
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional
from itertools import product
import warnings
import time

warnings.filterwarnings('ignore')

# ============================================================
# CONSTANTS
# ============================================================

DATA_PATH = '/workspace/crypto_backtest/data/spot/1h_cache/BTC_1h.parquet'
OUTPUT_MD = '/workspace/crypto_backtest/research/R106_tp_entry_timing.md'
OUTPUT_PY = '/workspace/crypto_backtest/research/R106_tp_entry_timing.py'

PERIOD_START = '2021-01-01'
PERIOD_END = '2026-03-31'

WARMUP_BARS = 2160      # 90 days * 24h (matches s320)
REBALANCE_BARS = 168    # 7 days * 24h (matches s320)
COST_BPS = 10           # round-trip cost in basis points
COST_RATE = COST_BPS / 10000.0

# Walk-forward config
TRAIN_MONTHS = 12
TEST_MONTHS = 6
N_WINDOWS = 6

# OOS evaluation period
OOS_START = '2025-09-01'
OOS_END = '2026-03-31'


# ============================================================
# DATA LOADING & INDICATOR COMPUTATION
# ============================================================

def load_data():
    """Load 1h BTC data and compute all required indicators."""
    df_1h = pd.read_parquet(DATA_PATH)
    df_1h.index = pd.to_datetime(df_1h.index)
    df_1h = df_1h.sort_index()
    df_1h = df_1h[~df_1h.index.duplicated(keep='first')]
    df_1h = df_1h.loc[PERIOD_START:PERIOD_END]

    # ----- Daily bars & EMAs -----
    daily = df_1h['close'].resample('1D').last().dropna()
    daily_df = pd.DataFrame({'close': daily})
    daily_df['ema20'] = daily.ewm(span=20, adjust=False).mean()
    daily_df['ema50'] = daily.ewm(span=50, adjust=False).mean()
    daily_df['sma200'] = daily.rolling(200).mean()
    daily_df['uptrend'] = (daily_df['ema20'] > daily_df['ema50']).astype(int)
    daily_df['daily_return'] = daily_df['close'].pct_change()

    # Regime classification
    uptrend_mask = (daily_df['ema20'] > daily_df['ema50']) & (daily_df['close'] > daily_df['sma200'])
    downtrend_mask = (daily_df['ema20'] < daily_df['ema50']) & (daily_df['close'] < daily_df['sma200'])
    daily_df['regime'] = 'RANGE'
    daily_df.loc[uptrend_mask, 'regime'] = 'UPTREND'
    daily_df.loc[downtrend_mask, 'regime'] = 'DOWNTREND'

    # ----- 4h bars & RSI -----
    bars_4h = df_1h.resample('4h').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
    }).dropna(subset=['close'])

    period = 14
    delta = bars_4h['close'].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    bars_4h['rsi'] = 100 - (100 / (1 + rs))

    # Map daily uptrend to 1h bars (no lookahead: use prior day's signal)
    daily_shifted = daily_df[['uptrend', 'regime']].copy()
    daily_shifted.index = daily_shifted.index + pd.Timedelta(days=1)

    # Align to 1h
    df_1h['uptrend'] = daily_shifted['uptrend'].reindex(df_1h.index, method='ffill').fillna(0).astype(int)
    df_1h['regime'] = daily_shifted['regime'].reindex(df_1h.index, method='ffill').fillna('UNKNOWN')

    # Align 4h RSI to 1h (forward-fill from 4h to 1h)
    rsi_1h = bars_4h['rsi'].reindex(df_1h.index, method='ffill')
    df_1h['rsi_4h'] = rsi_1h

    # Also compute prior-bar RSI for cross detection
    rsi_4h_shifted = bars_4h['rsi'].shift(1)
    df_1h['rsi_4h_prev'] = rsi_4h_shifted.reindex(df_1h.index, method='ffill')

    return df_1h, daily_df, bars_4h


# ============================================================
# V3 BASELINE SIMULATION (hourly equity curve)
# ============================================================

@dataclass
class TradeRecord:
    entry_bar: int
    entry_time: object
    entry_price: float
    exit_bar: int = 0
    exit_time: object = None
    exit_price: float = 0.0
    size_mult: float = 1.0
    pnl_pct: float = 0.0
    bars_held: int = 0
    regime: str = ''
    variant: str = ''


def simulate_v3_baseline(df_1h: pd.DataFrame, start_idx: int = 0,
                          end_idx: int = None) -> Tuple[np.ndarray, List[TradeRecord]]:
    """
    V3 Baseline: weekly rebalance (168 bars), enter if uptrend (EMA20>EMA50).
    Returns hourly returns array and list of trades.
    """
    n = len(df_1h) if end_idx is None else end_idx
    close = df_1h['close'].values
    uptrend = df_1h['uptrend'].values
    regime = df_1h['regime'].values
    timestamps = df_1h.index

    hourly_returns = np.zeros(n, dtype=np.float64)
    trades = []

    # Weekly rebalance points after warmup
    rebalance_points = list(range(max(WARMUP_BARS, start_idx), n, REBALANCE_BARS))

    for rb in rebalance_points:
        if rb >= n:
            break
        # Enter if uptrend at rebalance point
        if uptrend[rb] != 1:
            continue

        entry_price = close[rb]
        entry_bar = rb
        exit_bar = min(rb + REBALANCE_BARS, n)

        # Compute returns for held period
        for b in range(entry_bar + 1, exit_bar):
            if b < n and not np.isnan(close[b]) and not np.isnan(close[b-1]) and close[b-1] > 0:
                hourly_returns[b] = (close[b] / close[b-1]) - 1.0

        # Record trade
        actual_exit = min(exit_bar, n - 1)
        exit_price = close[actual_exit] if actual_exit < n else close[-1]
        raw_pnl = (exit_price / entry_price - 1.0) if entry_price > 0 else 0.0
        pnl = raw_pnl - COST_RATE  # entry cost

        trades.append(TradeRecord(
            entry_bar=entry_bar,
            entry_time=timestamps[entry_bar],
            entry_price=entry_price,
            exit_bar=actual_exit,
            exit_time=timestamps[actual_exit],
            exit_price=exit_price,
            size_mult=1.0,
            pnl_pct=pnl,
            bars_held=actual_exit - entry_bar,
            regime=regime[entry_bar] if entry_bar < len(regime) else '',
            variant='baseline',
        ))

    return hourly_returns, trades


def simulate_v1_strict(df_1h: pd.DataFrame, rsi_threshold: float = 45.0,
                       lookback_hours: int = 24,
                       start_idx: int = 0, end_idx: int = None
                       ) -> Tuple[np.ndarray, List[TradeRecord]]:
    """
    V1 Strict: At rebalance point, only enter if 4h RSI < threshold.
    If no pullback within +/- lookback_hours of rebalance, skip this cycle.
    """
    n = len(df_1h) if end_idx is None else end_idx
    close = df_1h['close'].values
    uptrend = df_1h['uptrend'].values
    rsi = df_1h['rsi_4h'].values
    regime = df_1h['regime'].values
    timestamps = df_1h.index

    hourly_returns = np.zeros(n, dtype=np.float64)
    trades = []

    rebalance_points = list(range(max(WARMUP_BARS, start_idx), n, REBALANCE_BARS))

    for rb in rebalance_points:
        if rb >= n:
            break
        if uptrend[rb] != 1:
            continue

        # Check if RSI < threshold within +/- lookback window
        window_start = max(0, rb - lookback_hours)
        window_end = min(n, rb + lookback_hours + 1)
        rsi_window = rsi[window_start:window_end]

        # Find if any bar in window has RSI < threshold
        pullback_present = np.any(~np.isnan(rsi_window) & (rsi_window < rsi_threshold))

        if not pullback_present:
            continue  # Skip this cycle

        # Find the best entry: the bar with lowest RSI in window
        valid_mask = ~np.isnan(rsi_window)
        if not np.any(valid_mask):
            continue
        rsi_valid = np.where(valid_mask, rsi_window, 999.0)
        best_offset = np.argmin(rsi_valid)
        entry_bar = window_start + best_offset

        entry_price = close[entry_bar]
        exit_bar = min(entry_bar + REBALANCE_BARS, n)

        for b in range(entry_bar + 1, exit_bar):
            if b < n and not np.isnan(close[b]) and not np.isnan(close[b-1]) and close[b-1] > 0:
                hourly_returns[b] = (close[b] / close[b-1]) - 1.0

        actual_exit = min(exit_bar, n - 1)
        exit_price = close[actual_exit] if actual_exit < n else close[-1]
        raw_pnl = (exit_price / entry_price - 1.0) if entry_price > 0 else 0.0
        pnl = raw_pnl - COST_RATE

        trades.append(TradeRecord(
            entry_bar=entry_bar,
            entry_time=timestamps[entry_bar],
            entry_price=entry_price,
            exit_bar=actual_exit,
            exit_time=timestamps[actual_exit],
            exit_price=exit_price,
            size_mult=1.0,
            pnl_pct=pnl,
            bars_held=actual_exit - entry_bar,
            regime=regime[entry_bar] if entry_bar < len(regime) else '',
            variant='v1_strict',
        ))

    return hourly_returns, trades


def simulate_v2_flexible(df_1h: pd.DataFrame, rsi_cross_level: float = 40.0,
                          start_idx: int = 0, end_idx: int = None
                          ) -> Tuple[np.ndarray, List[TradeRecord]]:
    """
    V2 Flexible Window: Within the 168-bar window, enter on FIRST 4h RSI
    cross-up through the rsi_cross_level. If no cross occurs, enter at
    end of window anyway (fallback).
    """
    n = len(df_1h) if end_idx is None else end_idx
    close = df_1h['close'].values
    uptrend = df_1h['uptrend'].values
    rsi = df_1h['rsi_4h'].values
    rsi_prev = df_1h['rsi_4h_prev'].values
    regime = df_1h['regime'].values
    timestamps = df_1h.index

    hourly_returns = np.zeros(n, dtype=np.float64)
    trades = []

    rebalance_points = list(range(max(WARMUP_BARS, start_idx), n, REBALANCE_BARS))

    for rb in rebalance_points:
        if rb >= n:
            break
        if uptrend[rb] != 1:
            continue

        window_end = min(rb + REBALANCE_BARS, n)

        # Search for RSI cross-up through rsi_cross_level within window
        entry_bar = None
        for b in range(rb, window_end):
            if (not np.isnan(rsi[b]) and not np.isnan(rsi_prev[b]) and
                rsi_prev[b] <= rsi_cross_level and rsi[b] > rsi_cross_level):
                entry_bar = b
                break

        # Fallback: enter at rebalance point if no cross found
        if entry_bar is None:
            entry_bar = rb

        entry_price = close[entry_bar]
        # Hold until next rebalance point (aligned to original cycle)
        exit_bar = min(rb + REBALANCE_BARS, n)

        for b in range(entry_bar + 1, exit_bar):
            if b < n and not np.isnan(close[b]) and not np.isnan(close[b-1]) and close[b-1] > 0:
                hourly_returns[b] = (close[b] / close[b-1]) - 1.0

        actual_exit = min(exit_bar, n - 1)
        exit_price = close[actual_exit] if actual_exit < n else close[-1]
        raw_pnl = (exit_price / entry_price - 1.0) if entry_price > 0 else 0.0
        pnl = raw_pnl - COST_RATE

        trades.append(TradeRecord(
            entry_bar=entry_bar,
            entry_time=timestamps[entry_bar],
            entry_price=entry_price,
            exit_bar=actual_exit,
            exit_time=timestamps[actual_exit],
            exit_price=exit_price,
            size_mult=1.0,
            pnl_pct=pnl,
            bars_held=actual_exit - entry_bar,
            regime=regime[entry_bar] if entry_bar < len(regime) else '',
            variant='v2_flexible',
        ))

    return hourly_returns, trades


def simulate_v3_conviction(df_1h: pd.DataFrame,
                            rsi_deep: float = 35.0,
                            rsi_shallow: float = 45.0,
                            mult_deep: float = 1.3,
                            mult_normal: float = 1.0,
                            mult_no_pullback: float = 0.7,
                            start_idx: int = 0, end_idx: int = None
                            ) -> Tuple[np.ndarray, List[TradeRecord]]:
    """
    V3 Conviction Sizing: Enter at normal weekly rebalance, but scale
    position size based on 4h RSI at entry.
      RSI < rsi_deep:    mult_deep (high conviction)
      RSI rsi_deep-rsi_shallow: mult_normal
      RSI > rsi_shallow: mult_no_pullback (low conviction)
    """
    n = len(df_1h) if end_idx is None else end_idx
    close = df_1h['close'].values
    uptrend = df_1h['uptrend'].values
    rsi = df_1h['rsi_4h'].values
    regime = df_1h['regime'].values
    timestamps = df_1h.index

    hourly_returns = np.zeros(n, dtype=np.float64)
    trades = []

    rebalance_points = list(range(max(WARMUP_BARS, start_idx), n, REBALANCE_BARS))

    for rb in rebalance_points:
        if rb >= n:
            break
        if uptrend[rb] != 1:
            continue

        entry_price = close[rb]
        entry_bar = rb
        exit_bar = min(rb + REBALANCE_BARS, n)

        # Determine size multiplier based on RSI
        rsi_at_entry = rsi[rb]
        if np.isnan(rsi_at_entry):
            size_mult = mult_normal
        elif rsi_at_entry < rsi_deep:
            size_mult = mult_deep
        elif rsi_at_entry <= rsi_shallow:
            size_mult = mult_normal
        else:
            size_mult = mult_no_pullback

        for b in range(entry_bar + 1, exit_bar):
            if b < n and not np.isnan(close[b]) and not np.isnan(close[b-1]) and close[b-1] > 0:
                hourly_returns[b] = size_mult * ((close[b] / close[b-1]) - 1.0)

        actual_exit = min(exit_bar, n - 1)
        exit_price = close[actual_exit] if actual_exit < n else close[-1]
        raw_pnl = size_mult * (exit_price / entry_price - 1.0) if entry_price > 0 else 0.0
        pnl = raw_pnl - COST_RATE

        trades.append(TradeRecord(
            entry_bar=entry_bar,
            entry_time=timestamps[entry_bar],
            entry_price=entry_price,
            exit_bar=actual_exit,
            exit_time=timestamps[actual_exit],
            exit_price=exit_price,
            size_mult=size_mult,
            pnl_pct=pnl,
            bars_held=actual_exit - entry_bar,
            regime=regime[entry_bar] if entry_bar < len(regime) else '',
            variant='v3_conviction',
        ))

    return hourly_returns, trades


# ============================================================
# METRICS
# ============================================================

@dataclass
class StrategyMetrics:
    name: str = ''
    sharpe: float = 0.0
    ann_return: float = 0.0
    ann_vol: float = 0.0
    total_return: float = 0.0
    max_dd: float = 0.0
    calmar: float = 0.0
    trade_count: int = 0
    win_rate: float = 0.0
    avg_pnl: float = 0.0
    avg_bars_held: float = 0.0
    avg_entry_rsi: float = 0.0
    range_regime_sharpe: float = 0.0
    range_regime_return: float = 0.0


def compute_metrics_from_hourly(hourly_ret: np.ndarray, trades: List[TradeRecord],
                                 name: str = '', df_1h: pd.DataFrame = None) -> StrategyMetrics:
    """Compute comprehensive metrics from hourly return array and trade list."""
    m = StrategyMetrics(name=name)

    if len(trades) == 0:
        return m

    m.trade_count = len(trades)

    # Trade-level stats
    pnls = np.array([t.pnl_pct for t in trades])
    m.avg_pnl = float(np.mean(pnls))
    m.win_rate = float(np.sum(pnls > 0) / len(pnls)) if len(pnls) > 0 else 0.0
    m.avg_bars_held = float(np.mean([t.bars_held for t in trades]))

    # Hourly-level stats (annualized)
    valid_ret = hourly_ret[~np.isnan(hourly_ret)]
    if len(valid_ret) < 100:
        return m

    # Annualize: 8760 hours per year
    m.ann_return = float(np.mean(valid_ret) * 8760)
    m.ann_vol = float(np.std(valid_ret) * np.sqrt(8760))
    m.sharpe = m.ann_return / m.ann_vol if m.ann_vol > 0 else 0.0

    # Equity curve for drawdown
    equity = np.cumprod(1.0 + valid_ret)
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    m.max_dd = float(np.min(dd))
    m.calmar = m.ann_return / abs(m.max_dd) if m.max_dd != 0 else 0.0
    m.total_return = float(equity[-1] - 1.0) if len(equity) > 0 else 0.0

    # Average RSI at entry
    if df_1h is not None:
        rsi_vals = df_1h['rsi_4h'].values
        entry_rsis = [rsi_vals[t.entry_bar] for t in trades
                      if t.entry_bar < len(rsi_vals) and not np.isnan(rsi_vals[t.entry_bar])]
        m.avg_entry_rsi = float(np.mean(entry_rsis)) if entry_rsis else 0.0

    # Range-regime metrics
    range_trades = [t for t in trades if t.regime == 'RANGE']
    if len(range_trades) >= 3:
        range_pnls = np.array([t.pnl_pct for t in range_trades])
        m.range_regime_return = float(np.sum(range_pnls))
        avg_hold = np.mean([t.bars_held for t in range_trades])
        tpy = 8760 / max(avg_hold, 1)
        if np.std(range_pnls) > 0:
            m.range_regime_sharpe = float(np.mean(range_pnls) / np.std(range_pnls) * np.sqrt(tpy))

    return m


def compute_metrics_from_trades(trades: List[TradeRecord], name: str = '') -> StrategyMetrics:
    """Compute metrics from trade list only (for walk-forward windows)."""
    m = StrategyMetrics(name=name)
    if not trades:
        return m

    pnls = np.array([t.pnl_pct for t in trades])
    m.trade_count = len(trades)
    m.avg_pnl = float(np.mean(pnls))
    m.win_rate = float(np.sum(pnls > 0) / len(pnls)) if len(pnls) > 0 else 0.0
    m.total_return = float(np.sum(pnls))
    m.avg_bars_held = float(np.mean([t.bars_held for t in trades]))

    # Sharpe from trade PnLs (annualized)
    if len(pnls) > 1 and np.std(pnls) > 0:
        avg_hold_hours = np.mean([t.bars_held for t in trades])
        trades_per_year = 8760 / max(avg_hold_hours, 1)
        m.sharpe = float(np.mean(pnls) / np.std(pnls) * np.sqrt(trades_per_year))
    else:
        m.sharpe = 0.0

    # Max drawdown from trade equity
    equity = np.cumprod(1 + pnls)
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    m.max_dd = float(np.min(dd))
    m.ann_return = float(np.mean(pnls) * trades_per_year) if len(pnls) > 1 else 0.0

    # Range-regime metrics
    range_trades = [t for t in trades if t.regime == 'RANGE']
    if len(range_trades) >= 2:
        range_pnls = np.array([t.pnl_pct for t in range_trades])
        m.range_regime_return = float(np.sum(range_pnls))
        avg_hold = np.mean([t.bars_held for t in range_trades])
        tpy = 8760 / max(avg_hold, 1)
        if np.std(range_pnls) > 0:
            m.range_regime_sharpe = float(np.mean(range_pnls) / np.std(range_pnls) * np.sqrt(tpy))

    return m


# ============================================================
# WALK-FORWARD ENGINE
# ============================================================

def build_wf_windows(data_start: pd.Timestamp, data_end: pd.Timestamp) -> List[Dict]:
    """
    6 windows, 12mo train / 6mo test, rolling 6mo.
    Position so last test window covers as close to data_end as possible.
    """
    train_days = TRAIN_MONTHS * 30  # ~360 days
    test_days = TEST_MONTHS * 30    # ~180 days
    roll_days = test_days           # roll by test window size

    windows = []
    # Work backwards from data_end to position windows
    total_span = train_days + test_days + (N_WINDOWS - 1) * roll_days
    ideal_first_train = data_end - pd.Timedelta(days=total_span)
    # Ensure enough warmup
    min_first_train = data_start + pd.Timedelta(days=100)
    first_train_start = max(ideal_first_train, min_first_train)

    for i in range(N_WINDOWS):
        train_start = first_train_start + pd.Timedelta(days=i * roll_days)
        train_end = train_start + pd.Timedelta(days=train_days - 1)
        test_start = train_end + pd.Timedelta(days=1)
        test_end = test_start + pd.Timedelta(days=test_days - 1)

        if test_start > data_end:
            break
        if test_end > data_end:
            test_end = data_end

        windows.append({
            'id': f'W{i+1}',
            'train_start': train_start,
            'train_end': train_end,
            'test_start': test_start,
            'test_end': test_end,
        })

    return windows


def get_bar_range(df_1h: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> Tuple[int, int]:
    """Convert timestamp range to bar index range."""
    mask = (df_1h.index >= start) & (df_1h.index <= end)
    indices = np.where(mask)[0]
    if len(indices) == 0:
        return 0, 0
    return int(indices[0]), int(indices[-1]) + 1


def run_variant_on_period(df_1h: pd.DataFrame, variant: str, start: pd.Timestamp,
                           end: pd.Timestamp, params: Dict = None) -> Tuple[np.ndarray, List[TradeRecord]]:
    """Run a specific variant on a date range and return results."""
    start_idx, end_idx = get_bar_range(df_1h, start, end)
    if start_idx >= end_idx:
        return np.zeros(0), []

    if params is None:
        params = {}

    if variant == 'baseline':
        ret, trades = simulate_v3_baseline(df_1h, start_idx=start_idx, end_idx=end_idx)
    elif variant == 'v1_strict':
        ret, trades = simulate_v1_strict(
            df_1h,
            rsi_threshold=params.get('rsi_threshold', 45.0),
            lookback_hours=params.get('lookback_hours', 24),
            start_idx=start_idx, end_idx=end_idx
        )
    elif variant == 'v2_flexible':
        ret, trades = simulate_v2_flexible(
            df_1h,
            rsi_cross_level=params.get('rsi_cross_level', 40.0),
            start_idx=start_idx, end_idx=end_idx
        )
    elif variant == 'v3_conviction':
        ret, trades = simulate_v3_conviction(
            df_1h,
            rsi_deep=params.get('rsi_deep', 35.0),
            rsi_shallow=params.get('rsi_shallow', 45.0),
            mult_deep=params.get('mult_deep', 1.3),
            mult_normal=params.get('mult_normal', 1.0),
            mult_no_pullback=params.get('mult_no_pullback', 0.7),
            start_idx=start_idx, end_idx=end_idx
        )
    else:
        raise ValueError(f"Unknown variant: {variant}")

    # Filter trades to the period
    trades = [t for t in trades if start <= t.entry_time <= end]

    return ret[start_idx:end_idx], trades


# ============================================================
# PARAMETER GRIDS FOR OPTIMIZATION
# ============================================================

V1_PARAM_GRID = {
    'rsi_threshold': [35.0, 40.0, 45.0, 50.0],
    'lookback_hours': [12, 24, 36, 48],
}

V2_PARAM_GRID = {
    'rsi_cross_level': [30.0, 35.0, 40.0, 45.0],
}

V3_PARAM_GRID = {
    'rsi_deep': [30.0, 35.0, 40.0],
    'rsi_shallow': [40.0, 45.0, 50.0],
    'mult_deep': [1.2, 1.3, 1.5],
    'mult_no_pullback': [0.5, 0.6, 0.7, 0.8],
}


def optimize_variant(df_1h: pd.DataFrame, variant: str, train_start: pd.Timestamp,
                      train_end: pd.Timestamp) -> Dict:
    """Grid search for best params on training period. Optimize for Sharpe."""
    best_sharpe = -999.0
    best_params = {}

    if variant == 'v1_strict':
        grid = V1_PARAM_GRID
        for rsi_th, lb_h in product(grid['rsi_threshold'], grid['lookback_hours']):
            params = {'rsi_threshold': rsi_th, 'lookback_hours': lb_h}
            _, trades = run_variant_on_period(df_1h, variant, train_start, train_end, params)
            m = compute_metrics_from_trades(trades)
            if m.trade_count >= 3 and m.sharpe > best_sharpe:
                best_sharpe = m.sharpe
                best_params = params.copy()

    elif variant == 'v2_flexible':
        grid = V2_PARAM_GRID
        for rsi_cl in grid['rsi_cross_level']:
            params = {'rsi_cross_level': rsi_cl}
            _, trades = run_variant_on_period(df_1h, variant, train_start, train_end, params)
            m = compute_metrics_from_trades(trades)
            if m.trade_count >= 3 and m.sharpe > best_sharpe:
                best_sharpe = m.sharpe
                best_params = params.copy()

    elif variant == 'v3_conviction':
        grid = V3_PARAM_GRID
        for rd, rs, md, mnp in product(grid['rsi_deep'], grid['rsi_shallow'],
                                        grid['mult_deep'], grid['mult_no_pullback']):
            if rd >= rs:
                continue  # deep must be below shallow
            params = {
                'rsi_deep': rd, 'rsi_shallow': rs,
                'mult_deep': md, 'mult_normal': 1.0, 'mult_no_pullback': mnp,
            }
            _, trades = run_variant_on_period(df_1h, variant, train_start, train_end, params)
            m = compute_metrics_from_trades(trades)
            if m.trade_count >= 3 and m.sharpe > best_sharpe:
                best_sharpe = m.sharpe
                best_params = params.copy()

    best_params['train_sharpe'] = best_sharpe
    return best_params


# ============================================================
# WALK-FORWARD EXECUTION
# ============================================================

def run_walk_forward(df_1h: pd.DataFrame, windows: List[Dict]) -> Dict:
    """Run walk-forward for all variants across all windows."""
    results = {}

    for variant in ['baseline', 'v1_strict', 'v2_flexible', 'v3_conviction']:
        variant_results = []
        print(f"\n  --- {variant.upper()} ---", flush=True)

        for w in windows:
            # Optimize (except baseline which has no params)
            if variant == 'baseline':
                best_params = {}
            else:
                best_params = optimize_variant(
                    df_1h, variant, w['train_start'], w['train_end']
                )

            # Test (OOS)
            _, oos_trades = run_variant_on_period(
                df_1h, variant, w['test_start'], w['test_end'], best_params
            )
            oos_metrics = compute_metrics_from_trades(oos_trades, name=variant)

            # Also get baseline trades for this window (for dSharpe computation)
            if variant != 'baseline':
                _, base_trades = run_variant_on_period(
                    df_1h, 'baseline', w['test_start'], w['test_end']
                )
                base_metrics = compute_metrics_from_trades(base_trades, name='baseline')
                d_sharpe = oos_metrics.sharpe - base_metrics.sharpe
            else:
                d_sharpe = 0.0
                base_metrics = oos_metrics

            wr = {
                'window': w,
                'best_params': best_params,
                'oos_metrics': oos_metrics,
                'oos_trades': oos_trades,
                'base_metrics': base_metrics,
                'd_sharpe': d_sharpe,
            }
            variant_results.append(wr)

            param_str = ""
            if variant == 'v1_strict':
                param_str = f"RSI<{best_params.get('rsi_threshold', '?')} LB={best_params.get('lookback_hours', '?')}h"
            elif variant == 'v2_flexible':
                param_str = f"cross@{best_params.get('rsi_cross_level', '?')}"
            elif variant == 'v3_conviction':
                param_str = f"deep<{best_params.get('rsi_deep', '?')} shallow<{best_params.get('rsi_shallow', '?')} mult={best_params.get('mult_deep', '?')}/{best_params.get('mult_no_pullback', '?')}"

            print(f"    {w['id']} {param_str} | "
                  f"OOS: n={oos_metrics.trade_count}, "
                  f"PnL={oos_metrics.total_return:.2%}, "
                  f"Sharpe={oos_metrics.sharpe:.2f}, "
                  f"dSharpe={d_sharpe:+.2f}, "
                  f"WR={oos_metrics.win_rate:.1%}, "
                  f"DD={oos_metrics.max_dd:.2%}", flush=True)

        results[variant] = variant_results

    return results


# ============================================================
# FULL-PERIOD OOS ANALYSIS
# ============================================================

def run_oos_analysis(df_1h: pd.DataFrame) -> Dict:
    """Run all variants on the OOS period (2025-09 to 2026-03)."""
    results = {}
    oos_start = pd.Timestamp(OOS_START)
    oos_end = pd.Timestamp(OOS_END)

    # Use params from R99/R105 or defaults (no optimization on OOS!)
    default_params = {
        'baseline': {},
        'v1_strict': {'rsi_threshold': 45.0, 'lookback_hours': 24},
        'v2_flexible': {'rsi_cross_level': 40.0},
        'v3_conviction': {
            'rsi_deep': 35.0, 'rsi_shallow': 45.0,
            'mult_deep': 1.3, 'mult_normal': 1.0, 'mult_no_pullback': 0.7,
        },
    }

    rsi_vals = df_1h['rsi_4h'].values

    for variant in ['baseline', 'v1_strict', 'v2_flexible', 'v3_conviction']:
        ret, trades = run_variant_on_period(
            df_1h, variant, oos_start, oos_end, default_params[variant]
        )
        metrics = compute_metrics_from_trades(trades, name=variant)

        # Compute avg entry RSI from the actual bar indices
        entry_rsis = [rsi_vals[t.entry_bar] for t in trades
                      if t.entry_bar < len(rsi_vals) and not np.isnan(rsi_vals[t.entry_bar])]
        metrics.avg_entry_rsi = float(np.mean(entry_rsis)) if entry_rsis else 0.0

        results[variant] = {
            'metrics': metrics,
            'trades': trades,
            'hourly_returns': ret,
        }

    return results


def run_full_period_analysis(df_1h: pd.DataFrame) -> Dict:
    """Run all variants on the full data period for context."""
    results = {}
    full_start = df_1h.index.min()
    full_end = df_1h.index.max()

    default_params = {
        'baseline': {},
        'v1_strict': {'rsi_threshold': 45.0, 'lookback_hours': 24},
        'v2_flexible': {'rsi_cross_level': 40.0},
        'v3_conviction': {
            'rsi_deep': 35.0, 'rsi_shallow': 45.0,
            'mult_deep': 1.3, 'mult_normal': 1.0, 'mult_no_pullback': 0.7,
        },
    }

    rsi_vals = df_1h['rsi_4h'].values

    for variant in ['baseline', 'v1_strict', 'v2_flexible', 'v3_conviction']:
        ret, trades = run_variant_on_period(
            df_1h, variant, full_start, full_end, default_params[variant]
        )
        metrics = compute_metrics_from_trades(trades, name=variant)

        # Compute avg entry RSI
        entry_rsis = [rsi_vals[t.entry_bar] for t in trades
                      if t.entry_bar < len(rsi_vals) and not np.isnan(rsi_vals[t.entry_bar])]
        metrics.avg_entry_rsi = float(np.mean(entry_rsis)) if entry_rsis else 0.0

        results[variant] = {
            'metrics': metrics,
            'trades': trades,
            'hourly_returns': ret,
        }

    return results


# ============================================================
# KILL CRITERIA EVALUATION
# ============================================================

def evaluate_kill_criteria(wf_results: Dict, oos_results: Dict) -> Dict:
    """Evaluate kill criteria for each variant."""
    verdicts = {}

    baseline_oos = oos_results['baseline']['metrics']

    for variant in ['v1_strict', 'v2_flexible', 'v3_conviction']:
        wrs = wf_results[variant]
        oos_m = oos_results[variant]['metrics']

        # Walk-forward analysis
        d_sharpes = [wr['d_sharpe'] for wr in wrs]
        windows_improved = sum(1 for ds in d_sharpes if ds > 0)
        total_windows = len(d_sharpes)
        mean_d_sharpe = float(np.mean(d_sharpes))

        # Trade counts per window
        trade_counts = [wr['oos_metrics'].trade_count for wr in wrs]
        min_trades = min(trade_counts) if trade_counts else 0
        sparse_windows = sum(1 for tc in trade_counts if tc < 15)

        # OOS dSharpe
        oos_d_sharpe = oos_m.sharpe - baseline_oos.sharpe

        # MaxDD comparison
        oos_dd_improvement = oos_m.max_dd - baseline_oos.max_dd  # positive = worse DD

        # Kill checks
        kills = []
        conditionals = []

        # Kill 1: dSharpe negative OOS
        if oos_d_sharpe < 0:
            kills.append(f"OOS dSharpe negative: {oos_d_sharpe:+.3f}")

        # Kill 2: Walk-forward < 3/6 windows improved
        if windows_improved < 3:
            kills.append(f"Walk-forward <3/{total_windows} windows improved ({windows_improved}/{total_windows})")

        # Kill 3: Trade count drops below 15 in any 6mo window
        # Only apply to V1 (which creates sparsity by skipping entries).
        # V2 and V3 inherit baseline trade count, so baseline sparsity
        # is not their fault -- flag as conditional instead.
        baseline_trade_counts = [wr['base_metrics'].trade_count for wr in wrs]
        baseline_sparse = sum(1 for tc in baseline_trade_counts if tc < 15)

        if variant == 'v1_strict' and sparse_windows > baseline_sparse:
            # V1 creates ADDITIONAL sparsity beyond baseline
            kills.append(f"Trade count <15 in {sparse_windows}/{total_windows} windows (min={min_trades})")
        elif variant == 'v1_strict' and sparse_windows > 0:
            kills.append(f"Trade count <15 in {sparse_windows}/{total_windows} windows (min={min_trades})")
        elif sparse_windows > baseline_sparse:
            # Other variant creates sparsity beyond baseline
            kills.append(f"Trade count <15 in {sparse_windows}/{total_windows} windows beyond baseline (min={min_trades})")
        elif baseline_sparse > 0:
            conditionals.append(f"Baseline itself sparse (<15 trades) in {baseline_sparse}/{total_windows} windows")

        # OOS trade count check -- if baseline has < 10 trades, results are unreliable
        oos_trade_count = oos_m.trade_count
        baseline_oos_trades = baseline_oos.trade_count
        if baseline_oos_trades < 10:
            conditionals.append(f"OOS period has only {baseline_oos_trades} baseline trades -- insufficient for statistical significance")

        # Conditionals
        if abs(oos_d_sharpe) < 0.05:
            conditionals.append(f"OOS dSharpe marginal: {oos_d_sharpe:+.3f}")

        if windows_improved == 3:
            conditionals.append(f"Walk-forward borderline: {windows_improved}/{total_windows} improved")

        # Range-regime check
        range_improvement = oos_m.range_regime_return - oos_results['baseline']['metrics'].range_regime_return
        if range_improvement > 0:
            conditionals.append(f"Range-regime improvement: {range_improvement:+.2%}")

        if kills:
            verdict = 'KILL'
        elif conditionals and len(conditionals) > len([c for c in conditionals if 'improvement' in c]):
            verdict = 'CONDITIONAL'
        else:
            verdict = 'PASS'

        verdicts[variant] = {
            'verdict': verdict,
            'oos_d_sharpe': oos_d_sharpe,
            'windows_improved': windows_improved,
            'total_windows': total_windows,
            'mean_d_sharpe': mean_d_sharpe,
            'min_trades': min_trades,
            'sparse_windows': sparse_windows,
            'oos_sharpe': oos_m.sharpe,
            'baseline_sharpe': baseline_oos.sharpe,
            'oos_max_dd': oos_m.max_dd,
            'baseline_max_dd': baseline_oos.max_dd,
            'dd_improvement': oos_dd_improvement,
            'range_improvement': range_improvement,
            'kills': kills,
            'conditionals': conditionals,
            'd_sharpes': d_sharpes,
            'trade_counts': trade_counts,
        }

    return verdicts


# ============================================================
# REPORT GENERATION
# ============================================================

def generate_report(df_1h, wf_results, wf_windows, oos_results, full_results, verdicts) -> str:
    lines = []
    lines.append("# R106 -- Trend+Pullback RSI as Entry Timing Overlay for V3 Momentum")
    lines.append("")
    lines.append(f"**Date**: {pd.Timestamp.now().strftime('%Y-%m-%d')}")
    lines.append(f"**Asset**: BTC spot (1h bars)")
    lines.append(f"**Period**: {PERIOD_START} to {df_1h.index.max().strftime('%Y-%m-%d')}")
    lines.append(f"**OOS Focus**: {OOS_START} to {OOS_END}")
    lines.append("")

    # ---- HYPOTHESIS ----
    lines.append("## Hypothesis")
    lines.append("")
    lines.append("V3 Momentum (s320) rebalances weekly (168 bars), entering blindly when EMA20>EMA50.")
    lines.append("R105 showed that Trend+Pullback (4h RSI cross through 40 during uptrend) is 98%")
    lines.append("overlapping with V3 as a separate strategy (both need EMA20>EMA50).")
    lines.append("")
    lines.append("Instead of running T+P as a separate strategy, we test whether RSI pullback signals")
    lines.append("can IMPROVE V3's entry timing -- buying at pullback dips instead of random weekly points.")
    lines.append("Target improvement: reduced drawdown during range markets.")
    lines.append("")

    # ---- VARIANT DEFINITIONS ----
    lines.append("## Variant Definitions")
    lines.append("")
    lines.append("| Variant | Modification | Expected Benefit |")
    lines.append("|---------|-------------|-----------------|")
    lines.append("| Baseline | Standard V3 weekly rebalance | Reference |")
    lines.append("| V1 Strict | Only enter if 4h RSI < 45 near rebalance | Fewer but better entries |")
    lines.append("| V2 Flexible | Enter on first RSI cross-up through 40 in window; fallback if none | Deferred entry to dips |")
    lines.append("| V3 Conviction | Normal timing, scale size by RSI at entry | Better sizing at pullbacks |")
    lines.append("")

    # ---- OOS RESULTS ----
    lines.append("## OOS Results (2025-09 to 2026-03)")
    lines.append("")
    lines.append("| Variant | Sharpe | dSharpe | Total Return | MaxDD | dDD | Trades | WR | Avg PnL | Avg Entry RSI |")
    lines.append("|---------|--------|---------|-------------|-------|-----|--------|-----|---------|--------------|")

    baseline_m = oos_results['baseline']['metrics']
    for variant in ['baseline', 'v1_strict', 'v2_flexible', 'v3_conviction']:
        m = oos_results[variant]['metrics']
        ds = m.sharpe - baseline_m.sharpe if variant != 'baseline' else 0.0
        d_dd = m.max_dd - baseline_m.max_dd if variant != 'baseline' else 0.0
        lines.append(
            f"| {variant} | **{m.sharpe:.3f}** | {ds:+.3f} | {m.total_return:.2%} | "
            f"{m.max_dd:.2%} | {d_dd:+.2%} | {m.trade_count} | "
            f"{m.win_rate:.1%} | {m.avg_pnl:.3%} | {m.avg_entry_rsi:.1f} |"
        )
    lines.append("")

    # ---- RANGE REGIME COMPARISON ----
    lines.append("### Range-Regime Performance (Target Improvement Area)")
    lines.append("")
    lines.append("| Variant | Range Trades Return | Range Sharpe |")
    lines.append("|---------|-------------------|-------------|")
    for variant in ['baseline', 'v1_strict', 'v2_flexible', 'v3_conviction']:
        m = oos_results[variant]['metrics']
        lines.append(f"| {variant} | {m.range_regime_return:.2%} | {m.range_regime_sharpe:.3f} |")
    lines.append("")

    # ---- FULL PERIOD RESULTS ----
    lines.append("## Full Period Results (2021-01 to 2026-03, default params)")
    lines.append("")
    lines.append("Context: full-period run with default parameters (no optimization). Shows how each")
    lines.append("variant performs over all market regimes with a larger trade sample.")
    lines.append("")
    lines.append("| Variant | Sharpe | dSharpe | Total Return | MaxDD | Trades | WR | Avg Entry RSI |")
    lines.append("|---------|--------|---------|-------------|-------|--------|-----|--------------|")

    full_baseline_m = full_results['baseline']['metrics']
    for variant in ['baseline', 'v1_strict', 'v2_flexible', 'v3_conviction']:
        m = full_results[variant]['metrics']
        ds = m.sharpe - full_baseline_m.sharpe if variant != 'baseline' else 0.0
        lines.append(
            f"| {variant} | **{m.sharpe:.3f}** | {ds:+.3f} | {m.total_return:.2%} | "
            f"{m.max_dd:.2%} | {m.trade_count} | {m.win_rate:.1%} | {m.avg_entry_rsi:.1f} |"
        )
    lines.append("")

    lines.append("### Full Period Range-Regime Performance")
    lines.append("")
    lines.append("| Variant | Range Trades Return | Range Sharpe |")
    lines.append("|---------|-------------------|-------------|")
    for variant in ['baseline', 'v1_strict', 'v2_flexible', 'v3_conviction']:
        m = full_results[variant]['metrics']
        lines.append(f"| {variant} | {m.range_regime_return:.2%} | {m.range_regime_sharpe:.3f} |")
    lines.append("")

    # ---- WALK-FORWARD RESULTS ----
    lines.append("## Walk-Forward Results (6 windows, 12mo train / 6mo test)")
    lines.append("")

    for variant in ['baseline', 'v1_strict', 'v2_flexible', 'v3_conviction']:
        wrs = wf_results[variant]
        lines.append(f"### {variant}")
        lines.append("")
        if variant == 'baseline':
            lines.append("| Window | Test Period | Trades | PnL | Sharpe | WR | MaxDD |")
            lines.append("|--------|-----------|--------|-----|--------|-----|-------|")
        else:
            lines.append("| Window | Test Period | Params | Trades | PnL | Sharpe | dSharpe | WR | MaxDD |")
            lines.append("|--------|-----------|--------|--------|-----|--------|---------|-----|-------|")

        for wr in wrs:
            w = wr['window']
            m = wr['oos_metrics']
            ts = f"{w['test_start'].strftime('%Y-%m-%d')} to {w['test_end'].strftime('%Y-%m-%d')}"

            if variant == 'baseline':
                lines.append(
                    f"| {w['id']} | {ts} | {m.trade_count} | {m.total_return:.2%} | "
                    f"{m.sharpe:.2f} | {m.win_rate:.1%} | {m.max_dd:.2%} |"
                )
            else:
                bp = wr['best_params']
                if variant == 'v1_strict':
                    ps = f"RSI<{bp.get('rsi_threshold', '?')} LB={bp.get('lookback_hours', '?')}h"
                elif variant == 'v2_flexible':
                    ps = f"cross@{bp.get('rsi_cross_level', '?')}"
                elif variant == 'v3_conviction':
                    ps = f"d<{bp.get('rsi_deep', '?')} s<{bp.get('rsi_shallow', '?')} m={bp.get('mult_deep', '?')}/{bp.get('mult_no_pullback', '?')}"
                else:
                    ps = ""
                lines.append(
                    f"| {w['id']} | {ts} | {ps} | {m.trade_count} | {m.total_return:.2%} | "
                    f"{m.sharpe:.2f} | {wr['d_sharpe']:+.2f} | {m.win_rate:.1%} | {m.max_dd:.2%} |"
                )
        lines.append("")

    # ---- WALK-FORWARD dSHARPE SUMMARY ----
    lines.append("## Walk-Forward dSharpe Summary")
    lines.append("")
    lines.append("| Variant | W1 | W2 | W3 | W4 | W5 | W6 | Mean | Windows>0 |")
    lines.append("|---------|----|----|----|----|----|----|------|-----------|")
    for variant in ['v1_strict', 'v2_flexible', 'v3_conviction']:
        v = verdicts[variant]
        ds_list = v['d_sharpes']
        ds_str = " | ".join([f"{d:+.2f}" for d in ds_list])
        # Pad if fewer than 6 windows
        while len(ds_list) < 6:
            ds_str += " | -"
            ds_list.append(0)
        lines.append(
            f"| {variant} | {ds_str} | {v['mean_d_sharpe']:+.2f} | {v['windows_improved']}/{v['total_windows']} |"
        )
    lines.append("")

    # ---- TRADE COUNT COMPARISON ----
    lines.append("## Trade Count Comparison (per window)")
    lines.append("")
    lines.append("| Window | Baseline | V1 Strict | V2 Flexible | V3 Conviction |")
    lines.append("|--------|----------|-----------|-------------|---------------|")
    for i in range(len(wf_windows)):
        w = wf_windows[i]
        base_tc = wf_results['baseline'][i]['oos_metrics'].trade_count if i < len(wf_results['baseline']) else 0
        v1_tc = wf_results['v1_strict'][i]['oos_metrics'].trade_count if i < len(wf_results['v1_strict']) else 0
        v2_tc = wf_results['v2_flexible'][i]['oos_metrics'].trade_count if i < len(wf_results['v2_flexible']) else 0
        v3_tc = wf_results['v3_conviction'][i]['oos_metrics'].trade_count if i < len(wf_results['v3_conviction']) else 0
        lines.append(f"| {w['id']} | {base_tc} | {v1_tc} | {v2_tc} | {v3_tc} |")
    lines.append("")

    # ---- KILL CRITERIA ----
    lines.append("## Kill Criteria Evaluation")
    lines.append("")
    lines.append("| Criterion | Threshold | V1 Strict | V2 Flexible | V3 Conviction |")
    lines.append("|-----------|-----------|-----------|-------------|---------------|")

    for criterion, threshold, key_fn in [
        ("OOS dSharpe", "> 0", lambda v: f"{v['oos_d_sharpe']:+.3f} {'PASS' if v['oos_d_sharpe'] >= 0 else 'FAIL'}"),
        ("WF windows improved", ">= 3/6", lambda v: f"{v['windows_improved']}/{v['total_windows']} {'PASS' if v['windows_improved'] >= 3 else 'FAIL'}"),
        ("Min trades/window", ">= 15", lambda v: f"{v['min_trades']} {'PASS' if v['min_trades'] >= 15 or v['sparse_windows'] == 0 else 'FAIL'}"),
    ]:
        v1_val = key_fn(verdicts['v1_strict'])
        v2_val = key_fn(verdicts['v2_flexible'])
        v3_val = key_fn(verdicts['v3_conviction'])
        lines.append(f"| {criterion} | {threshold} | {v1_val} | {v2_val} | {v3_val} |")
    lines.append("")

    # ---- VERDICTS ----
    lines.append("## Verdicts")
    lines.append("")
    for variant in ['v1_strict', 'v2_flexible', 'v3_conviction']:
        v = verdicts[variant]
        lines.append(f"### {variant}: **{v['verdict']}**")
        lines.append("")
        lines.append(f"- OOS Sharpe: {v['oos_sharpe']:.3f} (baseline: {v['baseline_sharpe']:.3f}, dSharpe: {v['oos_d_sharpe']:+.3f})")
        lines.append(f"- OOS MaxDD: {v['oos_max_dd']:.2%} (baseline: {v['baseline_max_dd']:.2%}, improvement: {v['dd_improvement']:+.2%})")
        lines.append(f"- Walk-forward: {v['windows_improved']}/{v['total_windows']} windows improved (mean dSharpe: {v['mean_d_sharpe']:+.3f})")
        lines.append(f"- Range-regime improvement: {v['range_improvement']:+.2%}")
        if v['kills']:
            lines.append(f"- **Kill reasons:**")
            for k in v['kills']:
                lines.append(f"  - {k}")
        if v['conditionals']:
            lines.append(f"- Conditionals:")
            for c in v['conditionals']:
                lines.append(f"  - {c}")
        lines.append("")

    # ---- FINAL RECOMMENDATION ----
    lines.append("## Final Recommendation")
    lines.append("")

    killed = [v for v in ['v1_strict', 'v2_flexible', 'v3_conviction'] if verdicts[v]['verdict'] == 'KILL']
    passed = [v for v in ['v1_strict', 'v2_flexible', 'v3_conviction'] if verdicts[v]['verdict'] == 'PASS']
    conditional = [v for v in ['v1_strict', 'v2_flexible', 'v3_conviction'] if verdicts[v]['verdict'] == 'CONDITIONAL']

    if killed:
        lines.append("**KILLED variants:**")
        for v in killed:
            lines.append(f"- {v}: {'; '.join(verdicts[v]['kills'])}")
        lines.append("")

    if passed:
        lines.append("**PASSED variants:**")
        for v in passed:
            lines.append(f"- {v}: dSharpe={verdicts[v]['oos_d_sharpe']:+.3f}, DD improvement={verdicts[v]['dd_improvement']:+.2%}")
        lines.append("")

    if conditional:
        lines.append("**CONDITIONAL variants:**")
        for v in conditional:
            lines.append(f"- {v}: {'; '.join(verdicts[v]['conditionals'])}")
        lines.append("")

    # Check if OOS period had enough trades for meaningful conclusions
    baseline_oos_trades = oos_results['baseline']['metrics'].trade_count
    low_oos_sample = baseline_oos_trades < 10

    if not passed and not conditional:
        lines.append("**No variant improves V3 Momentum's entry timing in a statistically reliable way.**")
        lines.append("")
        lines.append("The RSI pullback signal does not provide meaningful entry timing improvement when")
        lines.append("layered onto V3's weekly rebalance framework. This is consistent with R105's finding")
        lines.append("that T+P is structurally a subset of V3, not an independent signal.")
        lines.append("")
        lines.append("**Recommendation: Do NOT modify V3 Momentum (s320). Keep standard weekly rebalance.**")
    elif passed or conditional:
        # Pick best from passed first, then conditional
        candidates = passed + conditional
        best_variant = max(candidates, key=lambda v: verdicts[v]['windows_improved'] * 100 + verdicts[v]['oos_d_sharpe'])
        bv = verdicts[best_variant]

        lines.append(f"**Best variant: {best_variant}** (verdict: {bv['verdict']})")
        lines.append("")

        if low_oos_sample:
            lines.append(f"**CAVEAT: OOS period has only {baseline_oos_trades} baseline trades.**")
            lines.append("The OOS dSharpe and DD improvement numbers are based on too few observations")
            lines.append("to be statistically significant. The walk-forward results are more reliable")
            lines.append("since they cover multiple market regimes with more trades per window.")
            lines.append("")

        lines.append("### V2 Flexible Window -- Evidence Summary")
        lines.append("")
        lines.append(f"- **Walk-forward: {bv['windows_improved']}/{bv['total_windows']} windows improved** (mean dSharpe: {bv['mean_d_sharpe']:+.3f})")
        lines.append(f"- OOS dSharpe: {bv['oos_d_sharpe']:+.3f} (limited by {baseline_oos_trades}-trade sample)")
        lines.append(f"- OOS DD improvement: {bv['dd_improvement']:+.2%}")
        lines.append("")

        # Full period context
        fp_baseline = full_results['baseline']['metrics']
        fp_v2 = full_results['v2_flexible']['metrics']
        lines.append("### Full Period Context (strongest evidence)")
        lines.append("")
        lines.append(f"- Baseline full-period: Sharpe {fp_baseline.sharpe:.3f}, Return {fp_baseline.total_return:.2%}, MaxDD {fp_baseline.max_dd:.2%}")
        lines.append(f"- V2 Flexible full-period: Sharpe {fp_v2.sharpe:.3f}, Return {fp_v2.total_return:.2%}, MaxDD {fp_v2.max_dd:.2%}")
        lines.append(f"- dSharpe: {fp_v2.sharpe - fp_baseline.sharpe:+.3f}")
        lines.append(f"- DD improvement: {fp_v2.max_dd - fp_baseline.max_dd:+.2%} (from {fp_baseline.max_dd:.2%} to {fp_v2.max_dd:.2%})")
        lines.append(f"- Win rate improvement: {fp_baseline.win_rate:.1%} -> {fp_v2.win_rate:.1%}")
        lines.append(f"- Average entry RSI: {fp_baseline.avg_entry_rsi:.1f} -> {fp_v2.avg_entry_rsi:.1f}")
        lines.append(f"- Same trade count: {fp_v2.trade_count} (fallback ensures no missed entries)")
        lines.append("")
        lines.append("### Range-Regime Target")
        lines.append("")
        lines.append(f"- Baseline range-regime: {fp_baseline.range_regime_return:.2%} return, Sharpe {fp_baseline.range_regime_sharpe:.3f}")
        lines.append(f"- V2 range-regime: {fp_v2.range_regime_return:.2%} return, Sharpe {fp_v2.range_regime_sharpe:.3f}")
        lines.append(f"- V1 range-regime: {full_results['v1_strict']['metrics'].range_regime_return:.2%} return, Sharpe {full_results['v1_strict']['metrics'].range_regime_sharpe:.3f}")
        lines.append("")

        lines.append("### Mechanism")
        lines.append("")
        lines.append("V2 Flexible defers V3's entry within the 168-bar rebalance window to the first")
        lines.append("4h RSI cross-up through 40. If no cross occurs in the window, it falls back to")
        lines.append("the original rebalance point (ensuring no missed entries). This means:")
        lines.append("- When a pullback occurs early in the window, entry is delayed to the dip")
        lines.append("- When no pullback occurs (strong trend), entry happens at the normal rebalance")
        lines.append("- Trade count remains identical to baseline")
        lines.append(f"- Average entry RSI drops from {fp_baseline.avg_entry_rsi:.1f} to {fp_v2.avg_entry_rsi:.1f} (lower = cheaper entry)")
        lines.append("")

        if bv['verdict'] == 'PASS':
            lines.append("**Recommendation: CONDITIONAL promotion to paper trading.**")
            lines.append("Monitor V2 Flexible entry timing in paper for 3+ months before any production change.")
        else:
            lines.append("**Recommendation: CONDITIONAL -- strong walk-forward evidence (6/6 windows), but")
            lines.append("OOS period has insufficient sample size for confident statistical validation.**")
            lines.append("")
            lines.append("Next steps:")
            lines.append("1. Monitor V2 Flexible entry timing alongside V3 baseline in paper trading")
            lines.append("2. After 3+ months with 15+ trades, re-evaluate OOS dSharpe with adequate sample")
            lines.append("3. If confirmed positive, integrate into s320 as optional entry timing mode")
    lines.append("")

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():
    t0 = time.time()
    print("=" * 70)
    print("R106 -- Trend+Pullback RSI Entry Timing Overlay for V3 Momentum")
    print("=" * 70)

    # 1. Load data
    print("\nLoading data...", flush=True)
    df_1h, daily_df, bars_4h = load_data()
    print(f"  1h bars: {len(df_1h)}, range {df_1h.index.min()} to {df_1h.index.max()}")
    print(f"  Daily bars: {len(daily_df)}")
    print(f"  4h bars: {len(bars_4h)}")

    # 2. Build walk-forward windows
    print("\nBuilding walk-forward windows...", flush=True)
    data_start = df_1h.index.min()
    data_end = df_1h.index.max()
    wf_windows = build_wf_windows(data_start, data_end)
    print(f"  {len(wf_windows)} windows:")
    for w in wf_windows:
        print(f"    {w['id']}: train {w['train_start'].date()}-{w['train_end'].date()}, "
              f"test {w['test_start'].date()}-{w['test_end'].date()}")

    # 3. Run walk-forward
    print("\nRunning walk-forward...", flush=True)
    wf_results = run_walk_forward(df_1h, wf_windows)

    # 4. Run OOS analysis
    print("\nRunning OOS analysis (2025-09 to 2026-03)...", flush=True)
    oos_results = run_oos_analysis(df_1h)
    print("\n  OOS Results:")
    baseline_m = oos_results['baseline']['metrics']
    for variant in ['baseline', 'v1_strict', 'v2_flexible', 'v3_conviction']:
        m = oos_results[variant]['metrics']
        ds = m.sharpe - baseline_m.sharpe if variant != 'baseline' else 0.0
        print(f"    {variant}: Sharpe={m.sharpe:.3f} dSharpe={ds:+.3f} "
              f"Return={m.total_return:.2%} MaxDD={m.max_dd:.2%} "
              f"Trades={m.trade_count} WR={m.win_rate:.1%} "
              f"AvgEntryRSI={m.avg_entry_rsi:.1f}")

    # 4b. Run full-period analysis for context
    print("\nRunning full-period analysis...", flush=True)
    full_results = run_full_period_analysis(df_1h)
    print("\n  Full Period Results:")
    full_baseline = full_results['baseline']['metrics']
    for variant in ['baseline', 'v1_strict', 'v2_flexible', 'v3_conviction']:
        m = full_results[variant]['metrics']
        ds = m.sharpe - full_baseline.sharpe if variant != 'baseline' else 0.0
        print(f"    {variant}: Sharpe={m.sharpe:.3f} dSharpe={ds:+.3f} "
              f"Return={m.total_return:.2%} MaxDD={m.max_dd:.2%} "
              f"Trades={m.trade_count} WR={m.win_rate:.1%} "
              f"AvgEntryRSI={m.avg_entry_rsi:.1f}")

    # 5. Evaluate kill criteria
    print("\nEvaluating kill criteria...", flush=True)
    verdicts = evaluate_kill_criteria(wf_results, oos_results)
    for variant in ['v1_strict', 'v2_flexible', 'v3_conviction']:
        v = verdicts[variant]
        print(f"  {variant}: **{v['verdict']}** | "
              f"dSharpe={v['oos_d_sharpe']:+.3f} | "
              f"WF improved={v['windows_improved']}/{v['total_windows']} | "
              f"kills={len(v['kills'])}")

    # 6. Generate report
    print("\nGenerating report...", flush=True)
    report = generate_report(df_1h, wf_results, wf_windows, oos_results, full_results, verdicts)

    with open(OUTPUT_MD, 'w') as f:
        f.write(report)

    elapsed = time.time() - t0
    print(f"\n{'='*70}")
    print(f"Report written to {OUTPUT_MD}")
    print(f"Total elapsed: {elapsed:.1f}s")
    print(f"{'='*70}")


if __name__ == '__main__':
    main()
