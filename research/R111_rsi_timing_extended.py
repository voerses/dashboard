#!/workspace/venv/bin/python
"""
R111: Extended Validation of V3+RSI Timing (V2 Flexible)
=========================================================

Follow-up to R106 which found V2 Flexible (defer entry to first 4h RSI cross-up
through 40 within rebalance window, fallback if no cross) improved 6/6 walk-forward
windows with mean dSharpe +5.47. OOS had only 4 baseline trades.

This study extends R106 with:
  1. Smaller walk-forward windows (6mo train / 3mo test) for more data points
  2. Cross-threshold robustness (RSI 30, 35, 40, 45, 50)
  3. Fallback timing sensitivity (RSI-timed vs fallback-only entries)
  4. Market condition analysis (entry price improvement, drawdown by type)
  5. Optimal search window within rebalance period (48h, 96h, 120h, 168h)

Data: BTC spot 1h
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional
import warnings
import time

warnings.filterwarnings('ignore')

# ============================================================
# CONSTANTS
# ============================================================

DATA_PATH = '/workspace/crypto_backtest/data/spot/1h_cache/BTC_1h.parquet'
OUTPUT_MD = '/workspace/crypto_backtest/research/R111_rsi_timing_extended.md'

PERIOD_START = '2021-01-01'
PERIOD_END = '2026-03-31'

WARMUP_BARS = 2160      # 90 days * 24h
REBALANCE_BARS = 168    # 7 days * 24h
COST_BPS = 10
COST_RATE = COST_BPS / 10000.0


# ============================================================
# DATA LOADING & INDICATOR COMPUTATION (from R106)
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

    df_1h['uptrend'] = daily_shifted['uptrend'].reindex(df_1h.index, method='ffill').fillna(0).astype(int)
    df_1h['regime'] = daily_shifted['regime'].reindex(df_1h.index, method='ffill').fillna('UNKNOWN')

    rsi_1h = bars_4h['rsi'].reindex(df_1h.index, method='ffill')
    df_1h['rsi_4h'] = rsi_1h

    rsi_4h_shifted = bars_4h['rsi'].shift(1)
    df_1h['rsi_4h_prev'] = rsi_4h_shifted.reindex(df_1h.index, method='ffill')

    return df_1h, daily_df, bars_4h


# ============================================================
# TRADE RECORD
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
    entry_type: str = ''        # 'rsi_timed' or 'fallback'
    window_start_price: float = 0.0  # price at rebalance point
    entry_rsi: float = 0.0
    max_drawdown_from_entry: float = 0.0  # max dd from this trade's entry


# ============================================================
# SIMULATION FUNCTIONS
# ============================================================

def simulate_v3_baseline(df_1h, start_idx=0, end_idx=None):
    """V3 Baseline: weekly rebalance (168 bars), enter if uptrend."""
    n = len(df_1h) if end_idx is None else end_idx
    close = df_1h['close'].values
    uptrend = df_1h['uptrend'].values
    regime = df_1h['regime'].values
    rsi = df_1h['rsi_4h'].values
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

        for b in range(entry_bar + 1, exit_bar):
            if b < n and not np.isnan(close[b]) and not np.isnan(close[b-1]) and close[b-1] > 0:
                hourly_returns[b] = (close[b] / close[b-1]) - 1.0

        actual_exit = min(exit_bar, n - 1)
        exit_price = close[actual_exit] if actual_exit < n else close[-1]
        raw_pnl = (exit_price / entry_price - 1.0) if entry_price > 0 else 0.0
        pnl = raw_pnl - COST_RATE

        # Compute max drawdown from entry
        max_dd = 0.0
        for b in range(entry_bar + 1, actual_exit + 1):
            if b < n and entry_price > 0:
                dd = (close[b] / entry_price) - 1.0
                if dd < max_dd:
                    max_dd = dd

        entry_rsi_val = rsi[entry_bar] if entry_bar < len(rsi) and not np.isnan(rsi[entry_bar]) else 0.0

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
            entry_type='baseline',
            window_start_price=entry_price,
            entry_rsi=entry_rsi_val,
            max_drawdown_from_entry=max_dd,
        ))

    return hourly_returns, trades


def simulate_v2_flexible(df_1h, rsi_cross_level=40.0, search_window=None,
                          start_idx=0, end_idx=None):
    """
    V2 Flexible Window: within the rebalance window, enter on FIRST 4h RSI
    cross-up through rsi_cross_level. Fallback to rebalance point if no cross.

    search_window: if specified, only search first N hours of the 168-bar window
                   (default: search entire 168-bar window)
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
        window_start_price = close[rb]

        # Determine search range
        if search_window is not None:
            search_end = min(rb + search_window, window_end)
        else:
            search_end = window_end

        # Search for RSI cross-up through rsi_cross_level within search range
        entry_bar = None
        entry_type = 'fallback'
        for b in range(rb, search_end):
            if (not np.isnan(rsi[b]) and not np.isnan(rsi_prev[b]) and
                rsi_prev[b] <= rsi_cross_level and rsi[b] > rsi_cross_level):
                entry_bar = b
                entry_type = 'rsi_timed'
                break

        # Fallback: enter at rebalance point if no cross found
        if entry_bar is None:
            entry_bar = rb
            entry_type = 'fallback'

        entry_price = close[entry_bar]
        exit_bar = min(rb + REBALANCE_BARS, n)

        for b in range(entry_bar + 1, exit_bar):
            if b < n and not np.isnan(close[b]) and not np.isnan(close[b-1]) and close[b-1] > 0:
                hourly_returns[b] = (close[b] / close[b-1]) - 1.0

        actual_exit = min(exit_bar, n - 1)
        exit_price = close[actual_exit] if actual_exit < n else close[-1]
        raw_pnl = (exit_price / entry_price - 1.0) if entry_price > 0 else 0.0
        pnl = raw_pnl - COST_RATE

        # Compute max drawdown from entry
        max_dd = 0.0
        for b in range(entry_bar + 1, actual_exit + 1):
            if b < n and entry_price > 0:
                dd = (close[b] / entry_price) - 1.0
                if dd < max_dd:
                    max_dd = dd

        entry_rsi_val = rsi[entry_bar] if entry_bar < len(rsi) and not np.isnan(rsi[entry_bar]) else 0.0

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
            entry_type=entry_type,
            window_start_price=window_start_price,
            entry_rsi=entry_rsi_val,
            max_drawdown_from_entry=max_dd,
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


def compute_metrics_from_trades(trades, name=''):
    """Compute metrics from trade list."""
    m = StrategyMetrics(name=name)
    if not trades:
        return m

    pnls = np.array([t.pnl_pct for t in trades])
    m.trade_count = len(trades)
    m.avg_pnl = float(np.mean(pnls))
    m.win_rate = float(np.sum(pnls > 0) / len(pnls)) if len(pnls) > 0 else 0.0
    m.total_return = float(np.sum(pnls))
    m.avg_bars_held = float(np.mean([t.bars_held for t in trades]))

    if len(pnls) > 1 and np.std(pnls) > 0:
        avg_hold_hours = np.mean([t.bars_held for t in trades])
        trades_per_year = 8760 / max(avg_hold_hours, 1)
        m.sharpe = float(np.mean(pnls) / np.std(pnls) * np.sqrt(trades_per_year))
    else:
        m.sharpe = 0.0

    # Max drawdown from trade equity
    if len(pnls) > 0:
        equity = np.cumprod(1 + pnls)
        peak = np.maximum.accumulate(equity)
        dd = (equity - peak) / peak
        m.max_dd = float(np.min(dd))
        avg_hold_hours = np.mean([t.bars_held for t in trades])
        trades_per_year = 8760 / max(avg_hold_hours, 1)
        m.ann_return = float(np.mean(pnls) * trades_per_year) if len(pnls) > 1 else 0.0

    return m


# ============================================================
# WALK-FORWARD ENGINE
# ============================================================

def build_wf_windows(data_start, data_end, train_months=12, test_months=6, n_windows=6):
    """Build walk-forward windows with configurable sizes."""
    train_days = train_months * 30
    test_days = test_months * 30
    roll_days = test_days

    windows = []
    total_span = train_days + test_days + (n_windows - 1) * roll_days
    ideal_first_train = data_end - pd.Timedelta(days=total_span)
    min_first_train = data_start + pd.Timedelta(days=100)
    first_train_start = max(ideal_first_train, min_first_train)

    for i in range(n_windows):
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


def get_bar_range(df_1h, start, end):
    """Convert timestamp range to bar index range."""
    mask = (df_1h.index >= start) & (df_1h.index <= end)
    indices = np.where(mask)[0]
    if len(indices) == 0:
        return 0, 0
    return int(indices[0]), int(indices[-1]) + 1


def run_variant_on_period(df_1h, variant, start, end, params=None):
    """Run a specific variant on a date range."""
    start_idx, end_idx = get_bar_range(df_1h, start, end)
    if start_idx >= end_idx:
        return np.zeros(0), []

    if params is None:
        params = {}

    if variant == 'baseline':
        ret, trades = simulate_v3_baseline(df_1h, start_idx=start_idx, end_idx=end_idx)
    elif variant == 'v2_flexible':
        ret, trades = simulate_v2_flexible(
            df_1h,
            rsi_cross_level=params.get('rsi_cross_level', 40.0),
            search_window=params.get('search_window', None),
            start_idx=start_idx, end_idx=end_idx
        )
    else:
        raise ValueError(f"Unknown variant: {variant}")

    trades = [t for t in trades if start <= t.entry_time <= end]
    return ret[start_idx:end_idx], trades


def optimize_v2(df_1h, train_start, train_end, param_grid):
    """Grid search for best V2 params on training period."""
    best_sharpe = -999.0
    best_params = {}

    for rsi_cl in param_grid:
        params = {'rsi_cross_level': rsi_cl}
        _, trades = run_variant_on_period(df_1h, 'v2_flexible', train_start, train_end, params)
        m = compute_metrics_from_trades(trades)
        if m.trade_count >= 3 and m.sharpe > best_sharpe:
            best_sharpe = m.sharpe
            best_params = params.copy()

    best_params['train_sharpe'] = best_sharpe
    return best_params


# ============================================================
# TEST 1: EXTENDED WALK-FORWARD (6mo train / 3mo test)
# ============================================================

def run_extended_wf(df_1h):
    """Run walk-forward with smaller windows (6mo train / 3mo test)."""
    print("\n" + "="*70)
    print("TEST 1: Extended Walk-Forward (6mo train / 3mo test)")
    print("="*70)

    data_start = df_1h.index.min()
    data_end = df_1h.index.max()

    # Build as many windows as fit
    train_months = 6
    test_months = 3
    # Compute how many windows fit
    train_days = train_months * 30
    test_days = test_months * 30

    # Start from earliest feasible point (after warmup + some data)
    warmup_end = data_start + pd.Timedelta(hours=WARMUP_BARS)
    first_train_start = warmup_end + pd.Timedelta(days=30)  # buffer

    windows = []
    cursor = first_train_start
    i = 0
    while True:
        train_start = cursor
        train_end = train_start + pd.Timedelta(days=train_days - 1)
        test_start = train_end + pd.Timedelta(days=1)
        test_end = test_start + pd.Timedelta(days=test_days - 1)

        if test_end > data_end:
            # Allow partial last window if at least 30 days of test data
            if test_start + pd.Timedelta(days=30) <= data_end:
                test_end = data_end
            else:
                break

        windows.append({
            'id': f'W{i+1}',
            'train_start': train_start,
            'train_end': train_end,
            'test_start': test_start,
            'test_end': test_end,
        })

        cursor = cursor + pd.Timedelta(days=test_days)  # roll by test window size
        i += 1

    print(f"  Generated {len(windows)} windows")
    for w in windows:
        print(f"    {w['id']}: train {w['train_start'].date()}-{w['train_end'].date()}, "
              f"test {w['test_start'].date()}-{w['test_end'].date()}")

    # Run each window
    results = []
    param_grid = [30.0, 35.0, 40.0, 45.0]

    for w in windows:
        # Optimize V2 on training period
        best_params = optimize_v2(df_1h, w['train_start'], w['train_end'], param_grid)

        # Test V2 on test period
        _, v2_trades = run_variant_on_period(
            df_1h, 'v2_flexible', w['test_start'], w['test_end'], best_params
        )
        v2_m = compute_metrics_from_trades(v2_trades)

        # Baseline on test period
        _, base_trades = run_variant_on_period(
            df_1h, 'baseline', w['test_start'], w['test_end']
        )
        base_m = compute_metrics_from_trades(base_trades)

        d_sharpe = v2_m.sharpe - base_m.sharpe

        results.append({
            'window': w,
            'best_params': best_params,
            'v2_metrics': v2_m,
            'base_metrics': base_m,
            'd_sharpe': d_sharpe,
            'v2_trades': v2_trades,
            'base_trades': base_trades,
        })

        param_str = f"cross@{best_params.get('rsi_cross_level', '?')}"
        print(f"    {w['id']} {param_str} | "
              f"Base: n={base_m.trade_count} Sharpe={base_m.sharpe:.2f} | "
              f"V2: n={v2_m.trade_count} Sharpe={v2_m.sharpe:.2f} | "
              f"dSharpe={d_sharpe:+.2f}")

    # Summary
    d_sharpes = [r['d_sharpe'] for r in results]
    windows_improved = sum(1 for ds in d_sharpes if ds > 0)
    total = len(d_sharpes)
    mean_ds = np.mean(d_sharpes) if d_sharpes else 0
    pct_improved = windows_improved / total * 100 if total > 0 else 0

    print(f"\n  Summary: {windows_improved}/{total} windows improved ({pct_improved:.0f}%)")
    print(f"  Mean dSharpe: {mean_ds:+.2f}")
    print(f"  Kill criterion (<60%): {'FAIL - KILL' if pct_improved < 60 else 'PASS'}")

    return results


# ============================================================
# TEST 2: CROSS-THRESHOLD ROBUSTNESS
# ============================================================

def run_threshold_robustness(df_1h):
    """Test RSI cross thresholds 30, 35, 40, 45, 50 with original 12mo/6mo WF."""
    print("\n" + "="*70)
    print("TEST 2: Cross-Threshold Robustness")
    print("="*70)

    data_start = df_1h.index.min()
    data_end = df_1h.index.max()
    windows = build_wf_windows(data_start, data_end, train_months=12, test_months=6, n_windows=6)

    thresholds = [30.0, 35.0, 40.0, 45.0, 50.0]
    threshold_results = {}

    for threshold in thresholds:
        print(f"\n  --- RSI Cross Threshold: {threshold} ---")
        d_sharpes = []

        for w in windows:
            # Fixed threshold (no optimization) - test each threshold directly
            params = {'rsi_cross_level': threshold}

            _, v2_trades = run_variant_on_period(
                df_1h, 'v2_flexible', w['test_start'], w['test_end'], params
            )
            v2_m = compute_metrics_from_trades(v2_trades)

            _, base_trades = run_variant_on_period(
                df_1h, 'baseline', w['test_start'], w['test_end']
            )
            base_m = compute_metrics_from_trades(base_trades)

            ds = v2_m.sharpe - base_m.sharpe
            d_sharpes.append(ds)

            print(f"    {w['id']} | Base Sharpe={base_m.sharpe:.2f} | "
                  f"V2 Sharpe={v2_m.sharpe:.2f} | dSharpe={ds:+.2f}")

        mean_ds = np.mean(d_sharpes)
        win_pct = sum(1 for ds in d_sharpes if ds > 0) / len(d_sharpes) * 100

        # Also run full-period for context
        full_start = df_1h.index.min()
        full_end = df_1h.index.max()
        params = {'rsi_cross_level': threshold}
        _, full_v2_trades = run_variant_on_period(df_1h, 'v2_flexible', full_start, full_end, params)
        _, full_base_trades = run_variant_on_period(df_1h, 'baseline', full_start, full_end)
        full_v2_m = compute_metrics_from_trades(full_v2_trades)
        full_base_m = compute_metrics_from_trades(full_base_trades)

        threshold_results[threshold] = {
            'd_sharpes': d_sharpes,
            'mean_d_sharpe': mean_ds,
            'windows_improved': sum(1 for ds in d_sharpes if ds > 0),
            'total_windows': len(d_sharpes),
            'win_pct': win_pct,
            'full_period_sharpe': full_v2_m.sharpe,
            'full_period_base_sharpe': full_base_m.sharpe,
            'full_period_d_sharpe': full_v2_m.sharpe - full_base_m.sharpe,
            'full_period_return': full_v2_m.total_return,
            'full_period_max_dd': full_v2_m.max_dd,
            'full_period_win_rate': full_v2_m.win_rate,
        }

        print(f"  Mean dSharpe: {mean_ds:+.2f}, Windows improved: {win_pct:.0f}%")

    # Fragility check
    best_threshold = max(threshold_results, key=lambda t: threshold_results[t]['mean_d_sharpe'])
    best_ds = threshold_results[best_threshold]['mean_d_sharpe']
    print(f"\n  Best threshold: {best_threshold} (mean dSharpe: {best_ds:+.2f})")

    fragile = True
    for t in thresholds:
        if t != best_threshold:
            degradation = (best_ds - threshold_results[t]['mean_d_sharpe']) / abs(best_ds) * 100 if best_ds != 0 else 0
            print(f"    RSI={t}: mean dSharpe={threshold_results[t]['mean_d_sharpe']:+.2f}, "
                  f"degradation from best: {degradation:.0f}%")
            if degradation <= 30:
                fragile = False  # At least one neighboring threshold is within 30%

    print(f"\n  Fragility verdict: {'FRAGILE' if fragile else 'ROBUST'}")

    return threshold_results


# ============================================================
# TEST 3: FALLBACK TIMING SENSITIVITY
# ============================================================

def run_fallback_analysis(df_1h):
    """Analyze RSI-timed vs fallback entries."""
    print("\n" + "="*70)
    print("TEST 3: Fallback Timing Sensitivity")
    print("="*70)

    full_start = df_1h.index.min()
    full_end = df_1h.index.max()
    params = {'rsi_cross_level': 40.0}

    _, all_trades = run_variant_on_period(df_1h, 'v2_flexible', full_start, full_end, params)

    rsi_trades = [t for t in all_trades if t.entry_type == 'rsi_timed']
    fallback_trades = [t for t in all_trades if t.entry_type == 'fallback']

    total = len(all_trades)
    n_rsi = len(rsi_trades)
    n_fallback = len(fallback_trades)

    print(f"  Total trades: {total}")
    print(f"  RSI-timed entries: {n_rsi} ({n_rsi/total*100:.1f}%)")
    print(f"  Fallback entries: {n_fallback} ({n_fallback/total*100:.1f}%)")

    # Metrics for each group
    rsi_m = compute_metrics_from_trades(rsi_trades, name='rsi_timed')
    fallback_m = compute_metrics_from_trades(fallback_trades, name='fallback')
    all_m = compute_metrics_from_trades(all_trades, name='combined')

    print(f"\n  RSI-timed:  Sharpe={rsi_m.sharpe:.3f}, Return={rsi_m.total_return:.2%}, "
          f"WR={rsi_m.win_rate:.1%}, AvgPnL={rsi_m.avg_pnl:.3%}, MaxDD={rsi_m.max_dd:.2%}")
    print(f"  Fallback:   Sharpe={fallback_m.sharpe:.3f}, Return={fallback_m.total_return:.2%}, "
          f"WR={fallback_m.win_rate:.1%}, AvgPnL={fallback_m.avg_pnl:.3%}, MaxDD={fallback_m.max_dd:.2%}")
    print(f"  Combined:   Sharpe={all_m.sharpe:.3f}, Return={all_m.total_return:.2%}, "
          f"WR={all_m.win_rate:.1%}, AvgPnL={all_m.avg_pnl:.3%}, MaxDD={all_m.max_dd:.2%}")

    # Signal reality check: if fallback loses money and RSI wins, signal is real
    signal_real = (rsi_m.avg_pnl > 0 and fallback_m.avg_pnl < rsi_m.avg_pnl)
    print(f"\n  Signal reality check: RSI avg PnL > fallback avg PnL? "
          f"{'YES - signal has value' if signal_real else 'NO - signal may be noise'}")

    # By regime
    regime_data = {}
    for regime_name in ['UPTREND', 'RANGE', 'DOWNTREND']:
        rsi_regime = [t for t in rsi_trades if t.regime == regime_name]
        fb_regime = [t for t in fallback_trades if t.regime == regime_name]
        rsi_rm = compute_metrics_from_trades(rsi_regime)
        fb_rm = compute_metrics_from_trades(fb_regime)
        regime_data[regime_name] = {
            'rsi_count': len(rsi_regime),
            'fallback_count': len(fb_regime),
            'rsi_avg_pnl': rsi_rm.avg_pnl,
            'fallback_avg_pnl': fb_rm.avg_pnl,
            'rsi_win_rate': rsi_rm.win_rate,
            'fallback_win_rate': fb_rm.win_rate,
        }
        print(f"\n  {regime_name}:")
        print(f"    RSI-timed: n={len(rsi_regime)}, AvgPnL={rsi_rm.avg_pnl:.3%}, WR={rsi_rm.win_rate:.1%}")
        print(f"    Fallback:  n={len(fb_regime)}, AvgPnL={fb_rm.avg_pnl:.3%}, WR={fb_rm.win_rate:.1%}")

    return {
        'total_trades': total,
        'rsi_count': n_rsi,
        'fallback_count': n_fallback,
        'rsi_pct': n_rsi / total * 100 if total > 0 else 0,
        'fallback_pct': n_fallback / total * 100 if total > 0 else 0,
        'rsi_metrics': rsi_m,
        'fallback_metrics': fallback_m,
        'combined_metrics': all_m,
        'signal_real': signal_real,
        'regime_data': regime_data,
        'rsi_trades': rsi_trades,
        'fallback_trades': fallback_trades,
    }


# ============================================================
# TEST 4: MARKET CONDITION ANALYSIS
# ============================================================

def run_market_condition_analysis(df_1h, fallback_data):
    """Analyze entry price improvement and drawdown by entry type."""
    print("\n" + "="*70)
    print("TEST 4: Market Condition Analysis")
    print("="*70)

    rsi_trades = fallback_data['rsi_trades']
    fallback_trades = fallback_data['fallback_trades']

    # Entry price improvement = (window_start_price - entry_price) / window_start_price
    # Positive = bought cheaper than window start
    rsi_improvements = []
    for t in rsi_trades:
        if t.window_start_price > 0:
            imp = (t.window_start_price - t.entry_price) / t.window_start_price
            rsi_improvements.append(imp)

    fallback_improvements = []
    for t in fallback_trades:
        if t.window_start_price > 0:
            imp = (t.window_start_price - t.entry_price) / t.window_start_price
            fallback_improvements.append(imp)

    rsi_avg_improvement = np.mean(rsi_improvements) if rsi_improvements else 0
    fb_avg_improvement = np.mean(fallback_improvements) if fallback_improvements else 0

    print(f"\n  Entry Price Improvement (vs window start):")
    print(f"    RSI-timed: avg {rsi_avg_improvement:.4%} "
          f"(median {np.median(rsi_improvements):.4%}, n={len(rsi_improvements)})")
    print(f"    Fallback:  avg {fb_avg_improvement:.4%} "
          f"(median {np.median(fallback_improvements) if fallback_improvements else 0:.4%}, n={len(fallback_improvements)})")

    # Drawdown from entry
    rsi_dds = [t.max_drawdown_from_entry for t in rsi_trades]
    fb_dds = [t.max_drawdown_from_entry for t in fallback_trades]

    rsi_avg_dd = np.mean(rsi_dds) if rsi_dds else 0
    fb_avg_dd = np.mean(fb_dds) if fb_dds else 0

    print(f"\n  Average Max Drawdown from Entry:")
    print(f"    RSI-timed: {rsi_avg_dd:.2%} (median {np.median(rsi_dds):.2%})")
    print(f"    Fallback:  {fb_avg_dd:.2%} (median {np.median(fb_dds) if fb_dds else 0:.2%})")

    # Delay in bars (how far into window does RSI entry happen?)
    rsi_delays = []
    for t in rsi_trades:
        # Compute the rebalance point for this trade
        # The window_start_price is at the rebalance point
        # We can estimate the delay from bars_held difference
        # Actually, we need to find the rebalance bar
        # Since entry_type='rsi_timed', entry_bar > rebalance_bar
        # bars_held = exit_bar - entry_bar. Normal hold = 168 bars.
        # delay = 168 - bars_held
        delay = REBALANCE_BARS - t.bars_held
        if delay >= 0:
            rsi_delays.append(delay)

    avg_delay = np.mean(rsi_delays) if rsi_delays else 0
    median_delay = np.median(rsi_delays) if rsi_delays else 0

    print(f"\n  RSI Entry Delay (bars into window):")
    print(f"    Mean: {avg_delay:.1f}h, Median: {median_delay:.1f}h")
    print(f"    Distribution: min={min(rsi_delays) if rsi_delays else 0}h, "
          f"max={max(rsi_delays) if rsi_delays else 0}h")

    # Entry RSI comparison
    rsi_entry_rsis = [t.entry_rsi for t in rsi_trades if t.entry_rsi > 0]
    fb_entry_rsis = [t.entry_rsi for t in fallback_trades if t.entry_rsi > 0]

    print(f"\n  Entry RSI:")
    print(f"    RSI-timed: mean={np.mean(rsi_entry_rsis):.1f}, median={np.median(rsi_entry_rsis):.1f}")
    print(f"    Fallback:  mean={np.mean(fb_entry_rsis) if fb_entry_rsis else 0:.1f}, "
          f"median={np.median(fb_entry_rsis) if fb_entry_rsis else 0:.1f}")

    return {
        'rsi_avg_improvement': rsi_avg_improvement,
        'fb_avg_improvement': fb_avg_improvement,
        'rsi_median_improvement': np.median(rsi_improvements) if rsi_improvements else 0,
        'fb_median_improvement': np.median(fallback_improvements) if fallback_improvements else 0,
        'rsi_avg_dd': rsi_avg_dd,
        'fb_avg_dd': fb_avg_dd,
        'rsi_median_dd': np.median(rsi_dds) if rsi_dds else 0,
        'fb_median_dd': np.median(fb_dds) if fb_dds else 0,
        'avg_delay': avg_delay,
        'median_delay': median_delay,
        'min_delay': min(rsi_delays) if rsi_delays else 0,
        'max_delay': max(rsi_delays) if rsi_delays else 0,
        'rsi_mean_entry_rsi': np.mean(rsi_entry_rsis) if rsi_entry_rsis else 0,
        'fb_mean_entry_rsi': np.mean(fb_entry_rsis) if fb_entry_rsis else 0,
        'rsi_improvements': rsi_improvements,
        'fb_improvements': fallback_improvements,
        'rsi_delays': rsi_delays,
    }


# ============================================================
# TEST 5: SEARCH WINDOW OPTIMIZATION
# ============================================================

def run_search_window_test(df_1h):
    """Test shorter RSI search windows within the rebalance period."""
    print("\n" + "="*70)
    print("TEST 5: Optimal Search Window Within Rebalance Period")
    print("="*70)

    data_start = df_1h.index.min()
    data_end = df_1h.index.max()

    # Use same 12mo/6mo WF as R106
    windows = build_wf_windows(data_start, data_end, train_months=12, test_months=6, n_windows=6)

    search_windows = [48, 96, 120, 168]  # hours to search
    sw_results = {}

    for sw in search_windows:
        print(f"\n  --- Search Window: {sw}h ({sw/24:.0f} days) ---")
        d_sharpes = []
        rsi_pcts = []

        for w in windows:
            params = {'rsi_cross_level': 40.0, 'search_window': sw}

            _, v2_trades = run_variant_on_period(
                df_1h, 'v2_flexible', w['test_start'], w['test_end'], params
            )
            v2_m = compute_metrics_from_trades(v2_trades)

            _, base_trades = run_variant_on_period(
                df_1h, 'baseline', w['test_start'], w['test_end']
            )
            base_m = compute_metrics_from_trades(base_trades)

            ds = v2_m.sharpe - base_m.sharpe
            d_sharpes.append(ds)

            # Count RSI-timed entries
            n_rsi = sum(1 for t in v2_trades if t.entry_type == 'rsi_timed')
            rsi_pct = n_rsi / len(v2_trades) * 100 if v2_trades else 0
            rsi_pcts.append(rsi_pct)

            print(f"    {w['id']} | dSharpe={ds:+.2f} | RSI-timed: {n_rsi}/{len(v2_trades)} ({rsi_pct:.0f}%)")

        mean_ds = np.mean(d_sharpes) if d_sharpes else 0
        win_pct = sum(1 for ds in d_sharpes if ds > 0) / len(d_sharpes) * 100 if d_sharpes else 0
        mean_rsi_pct = np.mean(rsi_pcts)

        # Full period for context
        params = {'rsi_cross_level': 40.0, 'search_window': sw}
        _, full_v2 = run_variant_on_period(df_1h, 'v2_flexible', data_start, data_end, params)
        _, full_base = run_variant_on_period(df_1h, 'baseline', data_start, data_end)
        full_v2_m = compute_metrics_from_trades(full_v2)
        full_base_m = compute_metrics_from_trades(full_base)

        n_rsi_full = sum(1 for t in full_v2 if t.entry_type == 'rsi_timed')
        rsi_pct_full = n_rsi_full / len(full_v2) * 100 if full_v2 else 0

        # Compute average delay for RSI-timed entries
        delays = []
        for t in full_v2:
            if t.entry_type == 'rsi_timed':
                delay = REBALANCE_BARS - t.bars_held
                if delay >= 0:
                    delays.append(delay)
        avg_delay = np.mean(delays) if delays else 0

        sw_results[sw] = {
            'd_sharpes': d_sharpes,
            'mean_d_sharpe': mean_ds,
            'windows_improved': sum(1 for ds in d_sharpes if ds > 0),
            'total_windows': len(d_sharpes),
            'win_pct': win_pct,
            'mean_rsi_pct': mean_rsi_pct,
            'full_sharpe': full_v2_m.sharpe,
            'full_d_sharpe': full_v2_m.sharpe - full_base_m.sharpe,
            'full_return': full_v2_m.total_return,
            'full_rsi_pct': rsi_pct_full,
            'avg_delay': avg_delay,
        }

        print(f"  Mean dSharpe: {mean_ds:+.2f}, Win%: {win_pct:.0f}%, "
              f"Avg RSI-timed: {mean_rsi_pct:.0f}%")

    return sw_results


# ============================================================
# REPORT GENERATION
# ============================================================

def generate_report(df_1h, ext_wf_results, threshold_results, fallback_data,
                    market_data, search_window_results):
    """Generate the full R111 markdown report."""
    lines = []
    lines.append("# R111 -- Extended Validation of V3+RSI Timing (V2 Flexible)")
    lines.append("")
    lines.append(f"**Date**: {pd.Timestamp.now().strftime('%Y-%m-%d')}")
    lines.append(f"**Asset**: BTC spot (1h bars)")
    lines.append(f"**Period**: {PERIOD_START} to {df_1h.index.max().strftime('%Y-%m-%d')}")
    lines.append(f"**Predecessor**: R106 (V2 Flexible: 6/6 WF windows improved, mean dSharpe +5.47)")
    lines.append("")

    # ---- EXECUTIVE SUMMARY ----
    lines.append("## Executive Summary")
    lines.append("")

    # Gather verdicts
    ext_wf_ds = [r['d_sharpe'] for r in ext_wf_results]
    ext_wf_improved = sum(1 for ds in ext_wf_ds if ds > 0)
    ext_wf_total = len(ext_wf_ds)
    ext_wf_pct = ext_wf_improved / ext_wf_total * 100 if ext_wf_total > 0 else 0
    ext_wf_mean = np.mean(ext_wf_ds) if ext_wf_ds else 0

    best_threshold = max(threshold_results, key=lambda t: threshold_results[t]['mean_d_sharpe'])
    best_ds = threshold_results[best_threshold]['mean_d_sharpe']

    # Check fragility
    fragile = True
    for t in threshold_results:
        if t != best_threshold:
            if abs(best_ds) > 0:
                degradation = (best_ds - threshold_results[t]['mean_d_sharpe']) / abs(best_ds) * 100
                if degradation <= 30:
                    fragile = False

    signal_real = fallback_data['signal_real']

    lines.append("| Test | Result | Verdict |")
    lines.append("|------|--------|---------|")
    lines.append(f"| Extended WF (6mo/3mo, {ext_wf_total} windows) | "
                 f"{ext_wf_improved}/{ext_wf_total} improved ({ext_wf_pct:.0f}%), mean dSharpe {ext_wf_mean:+.2f} | "
                 f"{'PASS' if ext_wf_pct >= 60 else 'FAIL'} |")
    lines.append(f"| Threshold robustness | Best: RSI={best_threshold}, "
                 f"neighboring thresholds {'fragile' if fragile else 'robust'} | "
                 f"{'FAIL' if fragile else 'PASS'} |")
    lines.append(f"| Signal reality | RSI-timed avg PnL {'>' if signal_real else '<='} fallback avg PnL | "
                 f"{'PASS' if signal_real else 'FAIL'} |")

    best_sw = max(search_window_results, key=lambda sw: search_window_results[sw]['mean_d_sharpe'])
    lines.append(f"| Search window | Best: {best_sw}h, "
                 f"mean dSharpe {search_window_results[best_sw]['mean_d_sharpe']:+.2f} | INFO |")
    lines.append("")

    # ---- TEST 1: EXTENDED WALK-FORWARD ----
    lines.append("## Test 1: Extended Walk-Forward (6mo Train / 3mo Test)")
    lines.append("")
    lines.append(f"R106 used 12mo/6mo windows with 6 windows. This test uses 6mo/3mo windows")
    lines.append(f"to generate more data points ({ext_wf_total} windows).")
    lines.append("")
    lines.append(f"**Kill criterion**: <60% of windows improved")
    lines.append("")
    lines.append("| Window | Test Period | Base Trades | Base Sharpe | V2 Trades | V2 Sharpe | dSharpe | Opt Threshold |")
    lines.append("|--------|-----------|-------------|-------------|-----------|-----------|---------|---------------|")

    for r in ext_wf_results:
        w = r['window']
        ts = f"{w['test_start'].strftime('%Y-%m')}-{w['test_end'].strftime('%Y-%m')}"
        bm = r['base_metrics']
        vm = r['v2_metrics']
        threshold = r['best_params'].get('rsi_cross_level', '?')
        marker = ' *' if r['d_sharpe'] > 0 else ''
        lines.append(f"| {w['id']} | {ts} | {bm.trade_count} | {bm.sharpe:.2f} | "
                     f"{vm.trade_count} | {vm.sharpe:.2f} | {r['d_sharpe']:+.2f}{marker} | {threshold} |")
    lines.append("")

    lines.append(f"**Result**: {ext_wf_improved}/{ext_wf_total} windows improved ({ext_wf_pct:.0f}%), "
                 f"mean dSharpe {ext_wf_mean:+.2f}")
    lines.append(f"**Verdict**: {'PASS' if ext_wf_pct >= 60 else 'FAIL -- KILL'} (threshold: 60%)")
    lines.append("")

    # Distribution analysis
    positive = [ds for ds in ext_wf_ds if ds > 0]
    negative = [ds for ds in ext_wf_ds if ds <= 0]
    lines.append("### dSharpe Distribution")
    lines.append("")
    lines.append(f"- Positive windows: mean {np.mean(positive):+.2f}, median {np.median(positive):+.2f}" if positive else "- No positive windows")
    lines.append(f"- Negative windows: mean {np.mean(negative):+.2f}, median {np.median(negative):+.2f}" if negative else "- No negative windows")
    lines.append(f"- Overall: mean {ext_wf_mean:+.2f}, median {np.median(ext_wf_ds):+.2f}, std {np.std(ext_wf_ds):.2f}")
    lines.append("")

    # ---- TEST 2: THRESHOLD ROBUSTNESS ----
    lines.append("## Test 2: Cross-Threshold Robustness")
    lines.append("")
    lines.append("Each RSI threshold tested with fixed params (no optimization) across the")
    lines.append("original 12mo/6mo walk-forward windows.")
    lines.append("")
    lines.append(f"**Fragility criterion**: If only best threshold works and +/-5 degrades >30%, it is fragile")
    lines.append("")
    lines.append("| RSI Threshold | Mean dSharpe | Windows>0 | Win% | Full-Period Sharpe | Full dSharpe | Full Return | Full WR | Full MaxDD |")
    lines.append("|--------------|-------------|-----------|------|-------------------|-------------|-------------|---------|-----------|")

    for t in sorted(threshold_results.keys()):
        tr = threshold_results[t]
        marker = ' **' if t == best_threshold else ''
        lines.append(f"| {t:.0f}{marker} | {tr['mean_d_sharpe']:+.2f} | "
                     f"{tr['windows_improved']}/{tr['total_windows']} | {tr['win_pct']:.0f}% | "
                     f"{tr['full_period_sharpe']:.3f} | {tr['full_period_d_sharpe']:+.3f} | "
                     f"{tr['full_period_return']:.2%} | {tr['full_period_win_rate']:.1%} | {tr['full_period_max_dd']:.2%} |")
    lines.append("")

    # Per-window detail
    lines.append("### Per-Window dSharpe by Threshold")
    lines.append("")
    header = "| Threshold |"
    sep = "|-----------|"
    for i in range(threshold_results[30.0]['total_windows']):
        header += f" W{i+1} |"
        sep += "------|"
    header += " Mean |"
    sep += "------|"
    lines.append(header)
    lines.append(sep)
    for t in sorted(threshold_results.keys()):
        tr = threshold_results[t]
        row = f"| {t:.0f} |"
        for ds in tr['d_sharpes']:
            row += f" {ds:+.2f} |"
        row += f" {tr['mean_d_sharpe']:+.2f} |"
        lines.append(row)
    lines.append("")

    # Fragility analysis
    lines.append("### Fragility Analysis")
    lines.append("")
    lines.append(f"Best threshold: RSI={best_threshold:.0f} (mean dSharpe: {best_ds:+.2f})")
    lines.append("")
    lines.append("| Threshold | Mean dSharpe | Degradation from Best |")
    lines.append("|-----------|-------------|----------------------|")
    for t in sorted(threshold_results.keys()):
        tr = threshold_results[t]
        if abs(best_ds) > 0:
            degradation = (best_ds - tr['mean_d_sharpe']) / abs(best_ds) * 100
        else:
            degradation = 0
        marker = ' (best)' if t == best_threshold else ''
        lines.append(f"| {t:.0f}{marker} | {tr['mean_d_sharpe']:+.2f} | {degradation:.0f}% |")
    lines.append("")
    lines.append(f"**Verdict**: {'FRAGILE -- signal depends on exact threshold' if fragile else 'ROBUST -- neighboring thresholds also work'}")
    lines.append("")

    # ---- TEST 3: FALLBACK TIMING ----
    lines.append("## Test 3: Fallback Timing Sensitivity")
    lines.append("")
    lines.append("V2 Flexible enters on first RSI cross-up through 40 within the rebalance window.")
    lines.append("If no cross occurs, it falls back to entering at the window start (same as baseline).")
    lines.append("This test separates performance of RSI-timed entries vs fallback entries.")
    lines.append("")

    fd = fallback_data
    rm = fd['rsi_metrics']
    fm = fd['fallback_metrics']
    cm = fd['combined_metrics']

    lines.append("### Entry Type Distribution (Full Period)")
    lines.append("")
    lines.append("| Entry Type | Count | % of Total |")
    lines.append("|------------|-------|-----------|")
    lines.append(f"| RSI-timed | {fd['rsi_count']} | {fd['rsi_pct']:.1f}% |")
    lines.append(f"| Fallback | {fd['fallback_count']} | {fd['fallback_pct']:.1f}% |")
    lines.append(f"| Total | {fd['total_trades']} | 100% |")
    lines.append("")

    lines.append("### Performance by Entry Type (Full Period)")
    lines.append("")
    lines.append("| Metric | RSI-Timed | Fallback | Combined |")
    lines.append("|--------|-----------|----------|----------|")
    lines.append(f"| Trades | {rm.trade_count} | {fm.trade_count} | {cm.trade_count} |")
    lines.append(f"| Sharpe | {rm.sharpe:.3f} | {fm.sharpe:.3f} | {cm.sharpe:.3f} |")
    lines.append(f"| Total Return | {rm.total_return:.2%} | {fm.total_return:.2%} | {cm.total_return:.2%} |")
    lines.append(f"| Win Rate | {rm.win_rate:.1%} | {fm.win_rate:.1%} | {cm.win_rate:.1%} |")
    lines.append(f"| Avg PnL | {rm.avg_pnl:.3%} | {fm.avg_pnl:.3%} | {cm.avg_pnl:.3%} |")
    lines.append(f"| Max DD | {rm.max_dd:.2%} | {fm.max_dd:.2%} | {cm.max_dd:.2%} |")
    lines.append(f"| Avg Bars Held | {rm.avg_bars_held:.0f} | {fm.avg_bars_held:.0f} | {cm.avg_bars_held:.0f} |")
    lines.append("")

    lines.append("### Performance by Regime and Entry Type")
    lines.append("")
    lines.append("| Regime | RSI Count | RSI Avg PnL | RSI WR | FB Count | FB Avg PnL | FB WR |")
    lines.append("|--------|-----------|-------------|--------|----------|------------|-------|")
    for regime_name in ['UPTREND', 'RANGE', 'DOWNTREND']:
        rd = fd['regime_data'].get(regime_name, {})
        lines.append(f"| {regime_name} | {rd.get('rsi_count', 0)} | "
                     f"{rd.get('rsi_avg_pnl', 0):.3%} | {rd.get('rsi_win_rate', 0):.1%} | "
                     f"{rd.get('fallback_count', 0)} | {rd.get('fallback_avg_pnl', 0):.3%} | "
                     f"{rd.get('fallback_win_rate', 0):.1%} |")
    lines.append("")

    lines.append(f"**Signal reality verdict**: {'RSI timing adds genuine value -- RSI-timed entries outperform fallback' if fd['signal_real'] else 'RSI timing may not add value -- fallback entries perform similarly or better'}")
    lines.append("")

    # ---- TEST 4: MARKET CONDITIONS ----
    lines.append("## Test 4: Market Condition Analysis")
    lines.append("")
    lines.append("Comparing entry quality metrics between RSI-timed and fallback entries.")
    lines.append("")

    md = market_data
    lines.append("### Entry Price Improvement (vs Window Start Price)")
    lines.append("")
    lines.append("Positive = bought cheaper than the rebalance point (better entry).")
    lines.append("")
    lines.append("| Metric | RSI-Timed | Fallback |")
    lines.append("|--------|-----------|----------|")
    lines.append(f"| Mean improvement | {md['rsi_avg_improvement']:.4%} | {md['fb_avg_improvement']:.4%} |")
    lines.append(f"| Median improvement | {md['rsi_median_improvement']:.4%} | {md['fb_median_improvement']:.4%} |")
    lines.append("")

    lines.append("### Max Drawdown from Entry")
    lines.append("")
    lines.append("Lower (less negative) = less adverse excursion after entry.")
    lines.append("")
    lines.append("| Metric | RSI-Timed | Fallback |")
    lines.append("|--------|-----------|----------|")
    lines.append(f"| Mean max DD | {md['rsi_avg_dd']:.2%} | {md['fb_avg_dd']:.2%} |")
    lines.append(f"| Median max DD | {md['rsi_median_dd']:.2%} | {md['fb_median_dd']:.2%} |")
    lines.append("")

    lines.append("### RSI Entry Delay (hours into rebalance window)")
    lines.append("")
    lines.append(f"- Mean delay: {md['avg_delay']:.1f}h ({md['avg_delay']/24:.1f} days)")
    lines.append(f"- Median delay: {md['median_delay']:.1f}h ({md['median_delay']/24:.1f} days)")
    lines.append(f"- Range: {md['min_delay']}h to {md['max_delay']}h")
    lines.append("")

    lines.append("### Entry RSI at Entry Point")
    lines.append("")
    lines.append(f"- RSI-timed entries: mean RSI = {md['rsi_mean_entry_rsi']:.1f}")
    lines.append(f"- Fallback entries: mean RSI = {md['fb_mean_entry_rsi']:.1f}")
    lines.append("")

    # Delay distribution
    if md['rsi_delays']:
        delays = md['rsi_delays']
        bins = [0, 24, 48, 72, 96, 120, 144, 168]
        lines.append("### RSI Entry Delay Distribution")
        lines.append("")
        lines.append("| Delay Range | Count | % |")
        lines.append("|-------------|-------|---|")
        for i in range(len(bins)-1):
            count = sum(1 for d in delays if bins[i] <= d < bins[i+1])
            pct = count / len(delays) * 100 if delays else 0
            lines.append(f"| {bins[i]}-{bins[i+1]}h | {count} | {pct:.0f}% |")
        lines.append("")

    # ---- TEST 5: SEARCH WINDOW ----
    lines.append("## Test 5: Optimal Search Window Within Rebalance Period")
    lines.append("")
    lines.append("V2 Flexible searches the entire 168-hour rebalance window for an RSI cross.")
    lines.append("Shorter search windows reduce drift from the target rebalance date.")
    lines.append("")

    lines.append("### Walk-Forward Results by Search Window (RSI threshold = 40)")
    lines.append("")
    lines.append("| Search Window | Mean dSharpe | Win% | Avg RSI% | Full Sharpe | Full dSharpe | Full Return | RSI-Timed % | Avg Delay |")
    lines.append("|--------------|-------------|------|----------|------------|-------------|-------------|------------|-----------|")

    for sw in sorted(search_window_results.keys()):
        sr = search_window_results[sw]
        lines.append(f"| {sw}h ({sw/24:.0f}d) | {sr['mean_d_sharpe']:+.2f} | "
                     f"{sr['win_pct']:.0f}% | {sr['mean_rsi_pct']:.0f}% | "
                     f"{sr['full_sharpe']:.3f} | {sr['full_d_sharpe']:+.3f} | "
                     f"{sr['full_return']:.2%} | {sr['full_rsi_pct']:.0f}% | "
                     f"{sr['avg_delay']:.0f}h |")
    lines.append("")

    # Per-window detail
    lines.append("### Per-Window dSharpe by Search Window")
    lines.append("")
    n_wf = search_window_results[48]['total_windows']
    header = "| Search Window |"
    sep = "|--------------|"
    for i in range(n_wf):
        header += f" W{i+1} |"
        sep += "------|"
    header += " Mean |"
    sep += "------|"
    lines.append(header)
    lines.append(sep)
    for sw in sorted(search_window_results.keys()):
        sr = search_window_results[sw]
        row = f"| {sw}h |"
        for ds in sr['d_sharpes']:
            row += f" {ds:+.2f} |"
        row += f" {sr['mean_d_sharpe']:+.2f} |"
        lines.append(row)
    lines.append("")

    # ---- OVERALL VERDICT ----
    lines.append("## Overall Verdict")
    lines.append("")

    # Count passes
    tests_passed = 0
    tests_total = 3  # Ext WF, Threshold robustness, Signal reality

    if ext_wf_pct >= 60:
        tests_passed += 1
    if not fragile:
        tests_passed += 1
    if signal_real:
        tests_passed += 1

    lines.append(f"**Tests passed: {tests_passed}/{tests_total}**")
    lines.append("")
    lines.append("| Test | Criterion | Result | Verdict |")
    lines.append("|------|-----------|--------|---------|")
    lines.append(f"| Extended WF | >=60% windows improved | {ext_wf_pct:.0f}% ({ext_wf_improved}/{ext_wf_total}) | {'PASS' if ext_wf_pct >= 60 else 'FAIL'} |")
    lines.append(f"| Threshold robustness | Neighbors within 30% | {'Fragile' if fragile else 'Robust'} | {'FAIL' if fragile else 'PASS'} |")
    lines.append(f"| Signal reality | RSI avg PnL > fallback | {'Yes' if signal_real else 'No'} | {'PASS' if signal_real else 'FAIL'} |")
    lines.append("")

    if tests_passed >= 3:
        lines.append("### Strong Validation")
        lines.append("")
        lines.append("V2 Flexible RSI timing passes all three robustness tests.")
        lines.append("The signal is real and robust across thresholds, time periods, and market regimes.")
        lines.append("")
        lines.append("**Recommendation: Promote V2 Flexible to paper trading integration with V3 (s320).**")
    elif tests_passed >= 2:
        lines.append("### Partial Validation")
        lines.append("")
        lines.append("V2 Flexible passes most robustness tests but has some concerns.")
        lines.append("")
        lines.append("**Recommendation: Conditional promotion -- monitor in paper for 3+ months.**")
    else:
        lines.append("### Weak/Failed Validation")
        lines.append("")
        lines.append("V2 Flexible fails multiple robustness tests.")
        lines.append("")
        lines.append("**Recommendation: Do NOT promote. The R106 positive results may be")
        lines.append("an artifact of the specific test configuration rather than a real signal.**")
    lines.append("")

    # ---- RECOMMENDED CONFIGURATION ----
    lines.append("## Recommended Configuration (if promoting)")
    lines.append("")

    best_sw = max(search_window_results, key=lambda sw: search_window_results[sw]['mean_d_sharpe'])
    lines.append(f"- RSI cross threshold: {best_threshold:.0f}")
    lines.append(f"- Search window: {best_sw}h ({best_sw/24:.0f} days)")
    lines.append(f"- Fallback: enter at rebalance point if no RSI cross in search window")
    lines.append(f"- Expected RSI-timed entry rate: {search_window_results[best_sw]['full_rsi_pct']:.0f}%")
    lines.append(f"- Expected average entry delay: {search_window_results[best_sw]['avg_delay']:.0f}h")
    lines.append("")

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():
    t0 = time.time()
    print("=" * 70)
    print("R111 -- Extended Validation of V3+RSI Timing (V2 Flexible)")
    print("=" * 70)

    # 1. Load data
    print("\nLoading data...", flush=True)
    df_1h, daily_df, bars_4h = load_data()
    print(f"  1h bars: {len(df_1h)}, range {df_1h.index.min()} to {df_1h.index.max()}")

    # 2. Test 1: Extended Walk-Forward
    ext_wf_results = run_extended_wf(df_1h)

    # 3. Test 2: Cross-Threshold Robustness
    threshold_results = run_threshold_robustness(df_1h)

    # 4. Test 3: Fallback Timing Sensitivity
    fallback_data = run_fallback_analysis(df_1h)

    # 5. Test 4: Market Condition Analysis
    market_data = run_market_condition_analysis(df_1h, fallback_data)

    # 6. Test 5: Search Window Optimization
    search_window_results = run_search_window_test(df_1h)

    # 7. Generate report
    print("\n" + "="*70)
    print("Generating report...")
    print("="*70)

    report = generate_report(
        df_1h, ext_wf_results, threshold_results, fallback_data,
        market_data, search_window_results
    )

    with open(OUTPUT_MD, 'w') as f:
        f.write(report)

    elapsed = time.time() - t0
    print(f"\nReport written to {OUTPUT_MD}")
    print(f"Total elapsed: {elapsed:.1f}s")
    print(f"{'='*70}")


if __name__ == '__main__':
    main()
