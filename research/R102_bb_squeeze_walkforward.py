"""
R102: BB Squeeze Breakout -- Walk-Forward Validation
Signal from R96: Bollinger Band squeeze detection with breakout entry.

Walk-Forward Protocol:
- 10 rolling windows: 180-day train / 90-day test, rolling 90 days
- Train: grid-search over BB params (period, std mult, squeeze pctile, min duration, max hold, stop loss)
- Test: apply best in-sample params OOS
- Kill criteria per spec
"""

import sys
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional
from itertools import product
from collections import Counter
import warnings
import time

warnings.filterwarnings('ignore')

# Force unbuffered output
def log(msg):
    print(msg)
    sys.stdout.flush()

# ============================================================
# CONFIGURATION
# ============================================================

DATA_PATHS = {
    'BTC': '/workspace/crypto_backtest/data/spot/1h_cache/BTC_1h.parquet',
    'ETH': '/workspace/crypto_backtest/data/spot/1h_cache/ETH_1h.parquet',
}

# Walk-forward windows: 180d train / 90d test, rolling 90d
# Start at 2021-07-01 to allow warmup (18+ months of prior data)
WF_START = pd.Timestamp('2021-07-01')
NUM_WINDOWS = 10
TRAIN_DAYS = 180
TEST_DAYS = 90
ROLL_DAYS = 90

# Parameter grid for optimization
PARAM_GRID = {
    'bb_period': [15, 18, 20, 22, 25],
    'bb_std': [1.5, 1.75, 2.0, 2.25, 2.5],
    'squeeze_pctile': [10, 15, 20, 25, 30],
    'min_squeeze_bars': [12, 18, 24, 30, 36],
    'max_hold_bars': [24, 36, 48, 60, 72],
    'stop_loss_pct': [1.0, 1.5, 2.0, 2.5, 3.0],
}

# Fee assumption: 10bps round trip (5bps each way for spot)
FEE_RT = 10.0 / 10000 * 2  # round trip fee as fraction

# ============================================================
# DATA LOADING
# ============================================================

def load_data(token: str) -> pd.DataFrame:
    df = pd.read_parquet(DATA_PATHS[token])
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep='first')]
    return df


# ============================================================
# NUMPY-BASED INDICATORS AND SIMULATION
# ============================================================

def compute_bb_numpy(close: np.ndarray, period: int, std_mult: float):
    """Compute Bollinger Bands using numpy. Returns (middle, upper, lower, width) arrays."""
    n = len(close)
    middle = np.full(n, np.nan)
    std = np.full(n, np.nan)

    # Rolling mean and std using cumsum trick for speed
    for i in range(period - 1, n):
        window = close[i - period + 1:i + 1]
        middle[i] = np.mean(window)
        std[i] = np.std(window, ddof=1)

    upper = middle + std_mult * std
    lower = middle - std_mult * std
    width = (upper - lower) / middle

    return middle, upper, lower, width


def compute_bb_pandas(close_series: pd.Series, period: int, std_mult: float):
    """Compute BB using pandas rolling (faster than manual numpy for large arrays)."""
    middle = close_series.rolling(period).mean().values
    std = close_series.rolling(period).std().values
    upper = middle + std_mult * std
    lower = middle - std_mult * std
    # Avoid division by zero
    with np.errstate(divide='ignore', invalid='ignore'):
        width = np.where(middle != 0, (upper - lower) / middle, np.nan)
    return middle, upper, lower, width


def detect_squeeze_numpy(width: np.ndarray, pctile: float, min_bars: int) -> np.ndarray:
    """
    Numpy-based squeeze detection.
    Squeeze = BB Width < rolling 100-bar Xth percentile for >= min_bars consecutive bars.
    Returns boolean array where True = squeeze active.
    """
    n = len(width)
    below = np.zeros(n, dtype=bool)

    # Rolling percentile threshold
    for i in range(99, n):
        if np.isnan(width[i]):
            continue
        window = width[max(0, i - 99):i + 1]
        valid = window[~np.isnan(window)]
        if len(valid) > 0:
            threshold = np.percentile(valid, pctile)
            below[i] = width[i] < threshold

    # Count consecutive True values
    consecutive = np.zeros(n, dtype=int)
    count = 0
    for i in range(n):
        if below[i]:
            count += 1
        else:
            count = 0
        consecutive[i] = count

    return consecutive >= min_bars


def detect_squeeze_fast(width: np.ndarray, pctile: float, min_bars: int) -> np.ndarray:
    """
    Fast squeeze detection using pandas for rolling quantile then numpy for consecutive count.
    """
    n = len(width)
    # Use pandas for rolling quantile (highly optimized in C)
    width_series = pd.Series(width)
    threshold = width_series.rolling(100, min_periods=50).quantile(pctile / 100.0).values

    # Below threshold
    below = np.zeros(n, dtype=bool)
    valid = ~np.isnan(width) & ~np.isnan(threshold)
    below[valid] = width[valid] < threshold[valid]

    # Consecutive count (pure numpy loop, but on booleans - fast)
    consecutive = np.zeros(n, dtype=np.int32)
    count = 0
    for i in range(n):
        if below[i]:
            count += 1
        else:
            count = 0
        consecutive[i] = count

    return consecutive >= min_bars


def simulate_trades_fast(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    middle: np.ndarray,
    upper: np.ndarray,
    lower: np.ndarray,
    squeeze: np.ndarray,
    start_idx: int,
    end_idx: int,
    max_hold_bars: int,
    stop_loss_pct: float,
    fee_rt: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Fast trade simulation using numpy arrays.
    Returns: (pnls, directions, bars_held, exit_reasons, entry_indices)
    directions: 1=long, -1=short
    exit_reasons: 0=stop_loss, 1=max_hold, 2=cross_inside, 3=end_of_period
    """
    # Pre-compute squeeze_recent: squeeze active within last 3 bars
    n = len(close)
    squeeze_recent = np.zeros(n, dtype=bool)
    for i in range(n):
        if squeeze[i]:
            squeeze_recent[i] = True
        elif i >= 1 and squeeze[i-1]:
            squeeze_recent[i] = True
        elif i >= 2 and squeeze[i-2]:
            squeeze_recent[i] = True

    pnls = []
    directions = []
    bars_held_list = []
    exit_reasons = []
    entry_indices = []

    in_trade = False
    entry_price = 0.0
    direction = 0  # 1=long, -1=short
    bars_in_trade = 0
    stop_frac = stop_loss_pct / 100.0

    for i in range(start_idx, end_idx + 1):
        if i >= n:
            break

        c = close[i]
        h = high[i]
        l = low[i]

        if np.isnan(c) or np.isnan(upper[i]) or np.isnan(lower[i]) or np.isnan(middle[i]):
            continue

        u = upper[i]
        lo = lower[i]
        mid = middle[i]
        sq = squeeze_recent[i]

        if in_trade:
            bars_in_trade += 1

            if direction == 1:  # Long
                stop_price = entry_price * (1 - stop_frac)
                if l <= stop_price:
                    pnl = (stop_price / entry_price - 1) - fee_rt
                    pnls.append(pnl)
                    directions.append(1)
                    bars_held_list.append(bars_in_trade)
                    exit_reasons.append(0)
                    in_trade = False
                    continue
                if bars_in_trade >= max_hold_bars:
                    pnl = (c / entry_price - 1) - fee_rt
                    pnls.append(pnl)
                    directions.append(1)
                    bars_held_list.append(bars_in_trade)
                    exit_reasons.append(1)
                    in_trade = False
                    continue
                if c < mid:
                    pnl = (c / entry_price - 1) - fee_rt
                    pnls.append(pnl)
                    directions.append(1)
                    bars_held_list.append(bars_in_trade)
                    exit_reasons.append(2)
                    in_trade = False
                    continue

            elif direction == -1:  # Short
                stop_price = entry_price * (1 + stop_frac)
                if h >= stop_price:
                    pnl = (entry_price / stop_price - 1) - fee_rt
                    pnls.append(pnl)
                    directions.append(-1)
                    bars_held_list.append(bars_in_trade)
                    exit_reasons.append(0)
                    in_trade = False
                    continue
                if bars_in_trade >= max_hold_bars:
                    pnl = (entry_price / c - 1) - fee_rt
                    pnls.append(pnl)
                    directions.append(-1)
                    bars_held_list.append(bars_in_trade)
                    exit_reasons.append(1)
                    in_trade = False
                    continue
                if c > mid:
                    pnl = (entry_price / c - 1) - fee_rt
                    pnls.append(pnl)
                    directions.append(-1)
                    bars_held_list.append(bars_in_trade)
                    exit_reasons.append(2)
                    in_trade = False
                    continue

        if not in_trade and sq:
            if h > u and c > u:
                entry_price = c
                direction = 1
                in_trade = True
                bars_in_trade = 0
                entry_indices.append(i)
            elif l < lo and c < lo:
                entry_price = c
                direction = -1
                in_trade = True
                bars_in_trade = 0
                entry_indices.append(i)

    # Close open trade at end
    if in_trade:
        last_c = close[min(end_idx, n-1)]
        if direction == 1:
            pnl = (last_c / entry_price - 1) - fee_rt
        else:
            pnl = (entry_price / last_c - 1) - fee_rt
        pnls.append(pnl)
        directions.append(direction)
        bars_held_list.append(bars_in_trade)
        exit_reasons.append(3)

    return (
        np.array(pnls),
        np.array(directions),
        np.array(bars_held_list),
        np.array(exit_reasons),
        np.array(entry_indices),
    )


# ============================================================
# METRICS (NUMPY-BASED)
# ============================================================

@dataclass
class Metrics:
    trade_count: int = 0
    long_count: int = 0
    short_count: int = 0
    win_rate: float = 0.0
    long_win_rate: float = 0.0
    short_win_rate: float = 0.0
    profit_factor: float = 0.0
    total_pnl_pct: float = 0.0
    long_pnl_pct: float = 0.0
    short_pnl_pct: float = 0.0
    sharpe: float = 0.0
    long_sharpe: float = 0.0
    short_sharpe: float = 0.0
    max_dd_pct: float = 0.0
    avg_bars_held: float = 0.0


def compute_sharpe(pnls: np.ndarray, bars_held: np.ndarray) -> float:
    if len(pnls) < 2:
        return 0.0
    if np.std(pnls) == 0:
        return 0.0
    avg_hold = max(np.mean(bars_held), 1)
    trades_per_year = 8760 / avg_hold
    raw = (np.mean(pnls) / np.std(pnls)) * np.sqrt(trades_per_year)
    # Cap extreme Sharpe values to avoid misleading numbers
    return np.clip(raw, -100.0, 100.0)


def compute_max_dd(pnls: np.ndarray) -> float:
    if len(pnls) == 0:
        return 0.0
    equity = np.cumprod(1 + pnls)
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    return abs(dd.min()) * 100


def compute_metrics_fast(pnls: np.ndarray, directions: np.ndarray, bars_held: np.ndarray) -> Metrics:
    m = Metrics()
    if len(pnls) == 0:
        return m

    m.trade_count = len(pnls)

    long_mask = directions == 1
    short_mask = directions == -1
    long_pnls = pnls[long_mask]
    short_pnls = pnls[short_mask]
    long_bars = bars_held[long_mask]
    short_bars = bars_held[short_mask]

    m.long_count = len(long_pnls)
    m.short_count = len(short_pnls)

    m.win_rate = np.mean(pnls > 0) if len(pnls) > 0 else 0.0
    m.long_win_rate = np.mean(long_pnls > 0) if len(long_pnls) > 0 else 0.0
    m.short_win_rate = np.mean(short_pnls > 0) if len(short_pnls) > 0 else 0.0

    gross_profit = np.sum(pnls[pnls > 0])
    gross_loss = abs(np.sum(pnls[pnls < 0]))
    m.profit_factor = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    m.total_pnl_pct = np.sum(pnls)
    m.long_pnl_pct = np.sum(long_pnls)
    m.short_pnl_pct = np.sum(short_pnls)

    m.sharpe = compute_sharpe(pnls, bars_held)
    m.long_sharpe = compute_sharpe(long_pnls, long_bars) if len(long_pnls) > 0 else 0.0
    m.short_sharpe = compute_sharpe(short_pnls, short_bars) if len(short_pnls) > 0 else 0.0

    m.max_dd_pct = compute_max_dd(pnls)
    m.avg_bars_held = np.mean(bars_held)

    return m


# ============================================================
# PRECOMPUTE AND CACHE INDICATORS
# ============================================================

class IndicatorCache:
    """Pre-compute and cache BB indicators for all (period, std) combos."""

    def __init__(self, close_series: pd.Series):
        self.close = close_series.values
        self.close_series = close_series
        self.bb_cache = {}  # (period, std) -> (middle, upper, lower, width)
        self.squeeze_cache = {}  # (period, std, pctile, min_bars) -> squeeze array

    def get_bb(self, period: int, std_mult: float):
        key = (period, std_mult)
        if key not in self.bb_cache:
            middle, upper, lower, width = compute_bb_pandas(self.close_series, period, std_mult)
            self.bb_cache[key] = (middle, upper, lower, width)
        return self.bb_cache[key]

    def get_squeeze(self, period: int, std_mult: float, pctile: float, min_bars: int):
        key = (period, std_mult, pctile, min_bars)
        if key not in self.squeeze_cache:
            _, _, _, width = self.get_bb(period, std_mult)
            squeeze = detect_squeeze_fast(width, pctile, min_bars)
            self.squeeze_cache[key] = squeeze
        return self.squeeze_cache[key]

    def clear_squeeze_cache(self):
        """Clear squeeze cache to save memory between windows."""
        self.squeeze_cache.clear()


# ============================================================
# OPTIMIZATION (FAST)
# ============================================================

def find_index(timestamps: np.ndarray, target: pd.Timestamp) -> int:
    """Find the closest index >= target in sorted timestamp array."""
    target_np = np.datetime64(target)
    idx = np.searchsorted(timestamps, target_np)
    return min(idx, len(timestamps) - 1)


def optimize_params_fast(
    cache: IndicatorCache,
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    timestamps: np.ndarray,
    train_start: pd.Timestamp,
    train_end: pd.Timestamp,
) -> Dict:
    """Fast grid search using cached indicators."""
    start_idx = find_index(timestamps, train_start)
    end_idx = find_index(timestamps, train_end)

    best_score = -999.0
    best_params = None

    np.random.seed(42)
    all_combos = list(product(
        PARAM_GRID['bb_period'],
        PARAM_GRID['bb_std'],
        PARAM_GRID['squeeze_pctile'],
        PARAM_GRID['min_squeeze_bars'],
        PARAM_GRID['max_hold_bars'],
        PARAM_GRID['stop_loss_pct'],
    ))

    # Sample 120 random combos + R96 default
    n_sample = min(120, len(all_combos))
    indices = np.random.choice(len(all_combos), n_sample, replace=False)
    sampled = [all_combos[i] for i in indices]

    prior_best = (20, 2.0, 20, 24, 48, 1.5)
    if prior_best not in sampled:
        sampled.append(prior_best)

    for bb_period, bb_std, sq_pctile, min_sq, max_hold, stop_loss in sampled:
        middle, upper, lower, width = cache.get_bb(bb_period, bb_std)
        squeeze = cache.get_squeeze(bb_period, bb_std, sq_pctile, min_sq)

        pnls, dirs, bars, exits, _ = simulate_trades_fast(
            close, high, low, middle, upper, lower, squeeze,
            start_idx, end_idx, max_hold, stop_loss, FEE_RT,
        )

        if len(pnls) < 5:
            continue

        # Quick Sharpe
        if np.std(pnls) == 0:
            continue
        avg_hold = max(np.mean(bars), 1)
        tpy = 8760 / avg_hold
        sharpe = (np.mean(pnls) / np.std(pnls)) * np.sqrt(tpy)

        score = sharpe
        if len(pnls) < 10:
            score -= 0.2

        if score > best_score:
            best_score = score
            best_params = {
                'bb_period': bb_period,
                'bb_std': bb_std,
                'squeeze_pctile': sq_pctile,
                'min_squeeze_bars': min_sq,
                'max_hold_bars': max_hold,
                'stop_loss_pct': stop_loss,
            }

    if best_params is None:
        best_params = {
            'bb_period': 20, 'bb_std': 2.0, 'squeeze_pctile': 20,
            'min_squeeze_bars': 24, 'max_hold_bars': 48, 'stop_loss_pct': 1.5,
        }

    return best_params


# ============================================================
# WALK-FORWARD ENGINE
# ============================================================

def build_windows():
    windows = []
    for i in range(NUM_WINDOWS):
        train_start = WF_START + pd.Timedelta(days=ROLL_DAYS * i)
        train_end = train_start + pd.Timedelta(days=TRAIN_DAYS) - pd.Timedelta(hours=1)
        test_start = train_end + pd.Timedelta(hours=1)
        test_end = test_start + pd.Timedelta(days=TEST_DAYS) - pd.Timedelta(hours=1)
        windows.append({
            'name': f'W{i+1}',
            'train_start': train_start,
            'train_end': train_end,
            'test_start': test_start,
            'test_end': test_end,
        })
    return windows


@dataclass
class WindowResult:
    window_name: str
    token: str
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    params: Dict
    is_metrics: Metrics
    oos_metrics: Metrics
    oos_pnls: np.ndarray = field(default_factory=lambda: np.array([]))
    oos_dirs: np.ndarray = field(default_factory=lambda: np.array([]))
    oos_bars: np.ndarray = field(default_factory=lambda: np.array([]))
    oos_exits: np.ndarray = field(default_factory=lambda: np.array([]))


def run_walkforward(token: str, df: pd.DataFrame) -> List[WindowResult]:
    windows = build_windows()
    results = []

    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    timestamps = df.index.values

    # Build indicator cache for this token
    cache = IndicatorCache(df['close'])

    for w in windows:
        log(f"  {w['name']}: train {w['train_start'].date()} to {w['train_end'].date()}, "
            f"test {w['test_start'].date()} to {w['test_end'].date()}")

        if w['test_start'] > df.index.max():
            log("    SKIP: no data")
            continue

        actual_test_end = min(w['test_end'], df.index.max())

        t0 = time.time()

        # Clear squeeze cache between windows to save memory
        cache.clear_squeeze_cache()

        # Optimize
        best_params = optimize_params_fast(
            cache, close, high, low, timestamps,
            w['train_start'], w['train_end'],
        )
        opt_time = time.time() - t0
        log(f"    Optimized in {opt_time:.1f}s: {best_params}")

        # Get indicators for best params
        middle, upper, lower, width = cache.get_bb(best_params['bb_period'], best_params['bb_std'])
        squeeze = cache.get_squeeze(
            best_params['bb_period'], best_params['bb_std'],
            best_params['squeeze_pctile'], best_params['min_squeeze_bars'],
        )

        # In-sample
        is_start = find_index(timestamps, w['train_start'])
        is_end = find_index(timestamps, w['train_end'])
        is_pnls, is_dirs, is_bars, _, _ = simulate_trades_fast(
            close, high, low, middle, upper, lower, squeeze,
            is_start, is_end, best_params['max_hold_bars'],
            best_params['stop_loss_pct'], FEE_RT,
        )
        is_metrics = compute_metrics_fast(is_pnls, is_dirs, is_bars)

        # OOS
        oos_start = find_index(timestamps, w['test_start'])
        oos_end = find_index(timestamps, actual_test_end)
        oos_pnls, oos_dirs, oos_bars, oos_exits, _ = simulate_trades_fast(
            close, high, low, middle, upper, lower, squeeze,
            oos_start, oos_end, best_params['max_hold_bars'],
            best_params['stop_loss_pct'], FEE_RT,
        )
        oos_metrics = compute_metrics_fast(oos_pnls, oos_dirs, oos_bars)

        log(f"    IS: {is_metrics.trade_count} trades, Sharpe={is_metrics.sharpe:.2f}, PnL={is_metrics.total_pnl_pct:.2%}")
        log(f"    OOS: {oos_metrics.trade_count} trades, Sharpe={oos_metrics.sharpe:.2f}, PnL={oos_metrics.total_pnl_pct:.2%}")
        log(f"    OOS Long: {oos_metrics.long_count} trades, PnL={oos_metrics.long_pnl_pct:.2%}, Short: {oos_metrics.short_count} trades, PnL={oos_metrics.short_pnl_pct:.2%}")

        results.append(WindowResult(
            window_name=w['name'],
            token=token,
            train_start=str(w['train_start'].date()),
            train_end=str(w['train_end'].date()),
            test_start=str(w['test_start'].date()),
            test_end=str(actual_test_end.date()),
            params=best_params,
            is_metrics=is_metrics,
            oos_metrics=oos_metrics,
            oos_pnls=oos_pnls,
            oos_dirs=oos_dirs,
            oos_bars=oos_bars,
            oos_exits=oos_exits,
        ))

    return results


# ============================================================
# PARAMETER SENSITIVITY
# ============================================================

def run_sensitivity(token: str, df: pd.DataFrame, base_params: Dict) -> Dict:
    """Vary each param by +-20% and measure Sharpe degradation."""
    windows = build_windows()
    test_start = windows[0]['test_start']
    test_end = min(windows[-1]['test_end'], df.index.max())

    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    timestamps = df.index.values
    cache = IndicatorCache(df['close'])

    start_idx = find_index(timestamps, test_start)
    end_idx = find_index(timestamps, test_end)

    def run_with_params(params):
        middle, upper, lower, width = cache.get_bb(params['bb_period'], params['bb_std'])
        squeeze = cache.get_squeeze(
            params['bb_period'], params['bb_std'],
            params['squeeze_pctile'], params['min_squeeze_bars'],
        )
        pnls, dirs, bars, _, _ = simulate_trades_fast(
            close, high, low, middle, upper, lower, squeeze,
            start_idx, end_idx, params['max_hold_bars'],
            params['stop_loss_pct'], FEE_RT,
        )
        m = compute_metrics_fast(pnls, dirs, bars)
        return m

    base_m = run_with_params(base_params)
    base_sharpe = base_m.sharpe

    results = {
        'base': {
            'params': base_params,
            'sharpe': base_sharpe,
            'pnl': base_m.total_pnl_pct,
            'trades': base_m.trade_count,
            'degradation_pct': 0.0,
        }
    }

    for param_name, base_val in base_params.items():
        for dir_name, mult in [('minus20', 0.8), ('plus20', 1.2)]:
            varied = base_params.copy()
            new_val = base_val * mult
            if param_name in ('bb_period', 'min_squeeze_bars', 'max_hold_bars', 'squeeze_pctile'):
                new_val = max(int(round(new_val)), 5)
            else:
                new_val = round(new_val, 2)
            varied[param_name] = new_val

            m = run_with_params(varied)
            deg = ((base_sharpe - m.sharpe) / abs(base_sharpe) * 100) if base_sharpe != 0 else 0

            results[f"{param_name}_{dir_name}"] = {
                'params': varied,
                'sharpe': m.sharpe,
                'pnl': m.total_pnl_pct,
                'trades': m.trade_count,
                'degradation_pct': deg,
            }

    return results


# ============================================================
# KILL CRITERIA
# ============================================================

def evaluate_kill_criteria(btc_results, eth_results) -> Dict:
    verdicts = {}

    for token, results in [('BTC', btc_results), ('ETH', eth_results)]:
        positive_windows = sum(1 for r in results if r.oos_metrics.total_pnl_pct > 0)
        total_windows = len(results)
        mean_sharpe = np.mean([r.oos_metrics.sharpe for r in results]) if results else 0
        total_trades = sum(r.oos_metrics.trade_count for r in results)

        long_positive = sum(1 for r in results if r.oos_metrics.long_pnl_pct > 0 and r.oos_metrics.long_count > 0)
        long_windows_with_trades = sum(1 for r in results if r.oos_metrics.long_count > 0)
        long_total_trades = sum(r.oos_metrics.long_count for r in results)
        long_sharpes = [r.oos_metrics.long_sharpe for r in results if r.oos_metrics.long_count > 0]
        long_mean_sharpe = np.mean(long_sharpes) if long_sharpes else 0

        short_positive = sum(1 for r in results if r.oos_metrics.short_pnl_pct > 0 and r.oos_metrics.short_count > 0)
        short_windows_with_trades = sum(1 for r in results if r.oos_metrics.short_count > 0)
        short_total_trades = sum(r.oos_metrics.short_count for r in results)
        short_sharpes = [r.oos_metrics.short_sharpe for r in results if r.oos_metrics.short_count > 0]
        short_mean_sharpe = np.mean(short_sharpes) if short_sharpes else 0

        flags = []

        if positive_windows < 5:
            flags.append(f"KILL: Only {positive_windows}/{total_windows} positive OOS windows (need >=5)")
        if mean_sharpe < 0.3:
            flags.append(f"KILL: Mean OOS Sharpe={mean_sharpe:.3f} < 0.3")
        if total_trades < 30:
            flags.append(f"KILL: Only {total_trades} total OOS trades (need >=30)")

        long_works = long_positive >= 3 and long_mean_sharpe > 0
        short_works = short_positive >= 3 and short_mean_sharpe > 0
        if long_works and not short_works:
            flags.append(f"CONDITIONAL: Long works ({long_positive} pos windows, Sharpe={long_mean_sharpe:.2f}) but short doesn't ({short_positive} pos, Sharpe={short_mean_sharpe:.2f})")
        elif short_works and not long_works:
            flags.append(f"CONDITIONAL: Short works ({short_positive} pos windows, Sharpe={short_mean_sharpe:.2f}) but long doesn't ({long_positive} pos, Sharpe={long_mean_sharpe:.2f})")

        kill_flags = [f for f in flags if f.startswith("KILL:")]
        conditional_flags = [f for f in flags if f.startswith("CONDITIONAL:")]
        verdict = "KILL" if kill_flags else ("CONDITIONAL PASS" if conditional_flags else "PASS")

        verdicts[token] = {
            'verdict': verdict,
            'flags': flags,
            'positive_windows': positive_windows,
            'total_windows': total_windows,
            'mean_sharpe': mean_sharpe,
            'total_trades': total_trades,
            'long_positive': long_positive,
            'long_total_trades': long_total_trades,
            'long_mean_sharpe': long_mean_sharpe,
            'short_positive': short_positive,
            'short_total_trades': short_total_trades,
            'short_mean_sharpe': short_mean_sharpe,
        }

        for direction, pos, tot, sh, tt in [
            ('long', long_positive, long_windows_with_trades, long_mean_sharpe, long_total_trades),
            ('short', short_positive, short_windows_with_trades, short_mean_sharpe, short_total_trades),
        ]:
            d_flags = []
            if pos < 3:
                d_flags.append(f"KILL: Only {pos}/{tot} positive {direction} OOS windows")
            if sh < 0.3:
                d_flags.append(f"KILL: {direction} mean OOS Sharpe={sh:.3f} < 0.3")
            if tt < 10:
                d_flags.append(f"KILL: Only {tt} total {direction} OOS trades")

            verdicts[f"{token}_{direction}"] = {
                'verdict': "KILL" if d_flags else "PASS",
                'flags': d_flags,
                'positive': pos,
                'total': tot,
                'mean_sharpe': sh,
                'total_trades': tt,
            }

    return verdicts


# ============================================================
# REPORT GENERATION
# ============================================================

def generate_report(btc_results, eth_results, verdicts, btc_sensitivity, eth_sensitivity) -> str:
    lines = []
    lines.append("# R102: BB Squeeze Breakout Walk-Forward Validation")
    lines.append("")
    lines.append("**Signal**: Bollinger Band squeeze detection (BB Width < rolling percentile for N+ consecutive hours) with breakout entry")
    lines.append("**Protocol**: 10 rolling windows, 180-day train / 90-day test, rolling 90 days")
    lines.append(f"**Date**: {pd.Timestamp.now().strftime('%Y-%m-%d')}")
    lines.append("**Prior result**: PF 4.54 on full sample (R96), short-biased. This study validates OOS.")
    lines.append("")
    lines.append("## Signal Definition")
    lines.append("")
    lines.append("- **Setup**: BB Width (N-period, K std) falls below its rolling 100-bar Xth percentile for at least D consecutive hours = squeeze detected")
    lines.append("- **Entry**: When price closes above upper BB during/after squeeze -> LONG. When price closes below lower BB -> SHORT.")
    lines.append("- **Exit**: Price crosses back below middle BB (long) / above middle BB (short), OR max hold H hours, OR stop loss S%.")
    lines.append("- **Fees**: 10bps round-trip (5bps each way)")
    lines.append("")

    for token, results in [('BTC', btc_results), ('ETH', eth_results)]:
        v = verdicts[token]
        lines.append(f"## {token} Walk-Forward Results")
        lines.append("")
        lines.append(f"- Positive OOS windows: **{v['positive_windows']}/{v['total_windows']}**")
        lines.append(f"- Mean OOS Sharpe: **{v['mean_sharpe']:.3f}**")
        lines.append(f"- Total OOS trades: **{v['total_trades']}**")
        lines.append(f"- Long: {v['long_total_trades']} trades, {v['long_positive']} positive windows, mean Sharpe={v['long_mean_sharpe']:.3f}")
        lines.append(f"- Short: {v['short_total_trades']} trades, {v['short_positive']} positive windows, mean Sharpe={v['short_mean_sharpe']:.3f}")
        lines.append("")

        # Per-window OOS table
        lines.append("### Per-Window OOS Metrics")
        lines.append("")
        lines.append("| Window | Test Period | Trades | L/S | Win Rate | PF | PnL | Sharpe | MaxDD | Params |")
        lines.append("|--------|------------|--------|-----|----------|-----|-----|--------|-------|--------|")
        for r in results:
            m = r.oos_metrics
            p = r.params
            ps = f"p={p['bb_period']},s={p['bb_std']},q={p['squeeze_pctile']},d={p['min_squeeze_bars']},h={p['max_hold_bars']},sl={p['stop_loss_pct']}"
            lines.append(
                f"| {r.window_name} | {r.test_start} to {r.test_end} | {m.trade_count} | {m.long_count}/{m.short_count} | "
                f"{m.win_rate:.1%} | {m.profit_factor:.2f} | {m.total_pnl_pct:.2%} | {m.sharpe:.2f} | "
                f"{m.max_dd_pct:.1f}% | {ps} |"
            )
        lines.append("")

        # IS vs OOS
        lines.append("### In-Sample vs OOS Comparison")
        lines.append("")
        lines.append("| Window | IS Trades | IS Sharpe | IS PnL | OOS Trades | OOS Sharpe | OOS PnL | Degradation |")
        lines.append("|--------|-----------|-----------|--------|------------|------------|---------|-------------|")
        for r in results:
            ism = r.is_metrics
            om = r.oos_metrics
            deg = f"{(ism.sharpe - om.sharpe) / abs(ism.sharpe) * 100:+.0f}%" if ism.sharpe != 0 else "N/A"
            lines.append(
                f"| {r.window_name} | {ism.trade_count} | {ism.sharpe:.2f} | {ism.total_pnl_pct:.2%} | "
                f"{om.trade_count} | {om.sharpe:.2f} | {om.total_pnl_pct:.2%} | {deg} |"
            )
        lines.append("")

        # Long vs Short
        lines.append("### Long vs Short OOS Breakdown")
        lines.append("")
        lines.append("| Window | Long Trades | Long WR | Long PnL | Long Sharpe | Short Trades | Short WR | Short PnL | Short Sharpe |")
        lines.append("|--------|-------------|---------|----------|-------------|--------------|----------|-----------|--------------|")
        for r in results:
            m = r.oos_metrics
            lines.append(
                f"| {r.window_name} | {m.long_count} | {m.long_win_rate:.1%} | {m.long_pnl_pct:.2%} | {m.long_sharpe:.2f} | "
                f"{m.short_count} | {m.short_win_rate:.1%} | {m.short_pnl_pct:.2%} | {m.short_sharpe:.2f} |"
            )
        lines.append("")

    # Parameter Sensitivity
    lines.append("## Parameter Sensitivity Analysis")
    lines.append("")
    lines.append("Vary each parameter by +/-20% from modal values across windows. >30% Sharpe degradation = fragile.")
    lines.append("")

    for token, sensitivity in [('BTC', btc_sensitivity), ('ETH', eth_sensitivity)]:
        lines.append(f"### {token} Sensitivity")
        lines.append("")
        lines.append("| Variation | Sharpe | PnL | Trades | Degradation |")
        lines.append("|-----------|--------|-----|--------|-------------|")

        for key, data in sorted(sensitivity.items()):
            deg = data.get('degradation_pct', 0)
            tag = " **FRAGILE**" if key != 'base' and abs(deg) > 30 else ""
            lines.append(
                f"| {key} | {data['sharpe']:.3f} | {data['pnl']:.2%} | {data['trades']} | {deg:+.1f}%{tag} |"
            )
        lines.append("")

        fragile_count = sum(1 for k, d in sensitivity.items() if k != 'base' and abs(d.get('degradation_pct', 0)) > 30)
        total_var = sum(1 for k in sensitivity if k != 'base')
        lines.append(f"Fragile parameters: {fragile_count}/{total_var}")
        if fragile_count > total_var * 0.3:
            lines.append("**WARNING: >30% of parameter variations show fragility -- signal is parameter-sensitive**")
        lines.append("")

    # Kill Criteria table
    lines.append("## Kill Criteria Assessment")
    lines.append("")
    lines.append("| Token | Direction | Criterion | Threshold | Result | Status |")
    lines.append("|-------|-----------|-----------|-----------|--------|--------|")

    for token in ['BTC', 'ETH']:
        v = verdicts[token]
        lines.append(f"| {token} | Combined | Positive OOS windows | >=5/10 | {v['positive_windows']}/{v['total_windows']} | {'PASS' if v['positive_windows'] >= 5 else 'KILL'} |")
        lines.append(f"| {token} | Combined | Mean OOS Sharpe | >=0.3 | {v['mean_sharpe']:.3f} | {'PASS' if v['mean_sharpe'] >= 0.3 else 'KILL'} |")
        lines.append(f"| {token} | Combined | Total OOS trades | >=30 | {v['total_trades']} | {'PASS' if v['total_trades'] >= 30 else 'KILL'} |")

        for direction in ['long', 'short']:
            dv = verdicts[f"{token}_{direction}"]
            for flag in dv.get('flags', []):
                lines.append(f"| {token} | {direction} | {flag.split(':', 1)[1].strip()} | - | - | KILL |")
            if not dv.get('flags'):
                lines.append(f"| {token} | {direction} | All criteria | - | - | PASS |")
    lines.append("")

    # Exit reason breakdown
    lines.append("## Exit Reason Breakdown (All OOS Trades)")
    lines.append("")
    exit_names = {0: 'stop_loss', 1: 'max_hold', 2: 'cross_inside', 3: 'end_of_period'}
    for token, results in [('BTC', btc_results), ('ETH', eth_results)]:
        all_pnls = np.concatenate([r.oos_pnls for r in results]) if results else np.array([])
        all_exits = np.concatenate([r.oos_exits for r in results]) if results else np.array([])
        if len(all_pnls) > 0:
            lines.append(f"### {token}")
            lines.append("")
            lines.append("| Exit Reason | Count | Avg PnL | Total PnL |")
            lines.append("|-------------|-------|---------|-----------|")
            for code, name in sorted(exit_names.items()):
                mask = all_exits == code
                if mask.any():
                    ep = all_pnls[mask]
                    lines.append(f"| {name} | {len(ep)} | {np.mean(ep):.3%} | {np.sum(ep):.2%} |")
            lines.append("")

    # Final Verdicts
    lines.append("## Final Verdicts")
    lines.append("")

    for token in ['BTC', 'ETH']:
        v = verdicts[token]
        lines.append(f"### {token}: **{v['verdict']}**")
        lines.append("")
        if v['flags']:
            for f in v['flags']:
                lines.append(f"- {f}")
        else:
            lines.append("- All kill criteria passed.")
        lines.append("")

        for direction in ['long', 'short']:
            dv = verdicts[f"{token}_{direction}"]
            lines.append(f"#### {token} {direction.upper()}: **{dv['verdict']}**")
            lines.append("")
            if dv['flags']:
                for f in dv['flags']:
                    lines.append(f"- {f}")
            else:
                lines.append("- All direction-specific criteria passed.")
            lines.append("")

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

if __name__ == '__main__':
    log("=" * 60)
    log("R102: BB Squeeze Breakout Walk-Forward Validation")
    log("=" * 60)

    log("\nLoading data...")
    btc_df = load_data('BTC')
    eth_df = load_data('ETH')
    log(f"  BTC: {btc_df.shape[0]} bars, {btc_df.index[0].date()} to {btc_df.index[-1].date()}")
    log(f"  ETH: {eth_df.shape[0]} bars, {eth_df.index[0].date()} to {eth_df.index[-1].date()}")

    log("\n--- BTC Walk-Forward ---")
    btc_results = run_walkforward('BTC', btc_df)

    log("\n--- ETH Walk-Forward ---")
    eth_results = run_walkforward('ETH', eth_df)

    log("\n--- Kill Criteria ---")
    verdicts = evaluate_kill_criteria(btc_results, eth_results)

    for token in ['BTC', 'ETH']:
        v = verdicts[token]
        log(f"\n{token} Combined: {v['verdict']}")
        for f in v['flags']:
            log(f"  {f}")
        for direction in ['long', 'short']:
            dv = verdicts[f"{token}_{direction}"]
            log(f"  {direction}: {dv['verdict']}")

    log("\n--- Parameter Sensitivity ---")

    def most_common_params(results):
        param_counts = {}
        for p in ['bb_period', 'bb_std', 'squeeze_pctile', 'min_squeeze_bars', 'max_hold_bars', 'stop_loss_pct']:
            vals = [r.params[p] for r in results]
            param_counts[p] = Counter(vals).most_common(1)[0][0]
        return param_counts

    btc_modal = most_common_params(btc_results) if btc_results else {
        'bb_period': 20, 'bb_std': 2.0, 'squeeze_pctile': 20,
        'min_squeeze_bars': 24, 'max_hold_bars': 48, 'stop_loss_pct': 1.5,
    }
    eth_modal = most_common_params(eth_results) if eth_results else btc_modal.copy()

    log(f"  BTC modal params: {btc_modal}")
    log(f"  ETH modal params: {eth_modal}")

    btc_sens = run_sensitivity('BTC', btc_df, btc_modal)
    eth_sens = run_sensitivity('ETH', eth_df, eth_modal)

    for token, sens in [('BTC', btc_sens), ('ETH', eth_sens)]:
        fragile = [k for k, v in sens.items() if k != 'base' and abs(v.get('degradation_pct', 0)) > 30]
        log(f"  {token} fragile: {len(fragile)}/{len(sens)-1}")

    log("\n--- Generating Report ---")
    report = generate_report(btc_results, eth_results, verdicts, btc_sens, eth_sens)

    output_path = '/workspace/crypto_backtest/research/R102_bb_squeeze_walkforward.md'
    with open(output_path, 'w') as f:
        f.write(report)
    log(f"Report written to {output_path}")

    log("\n" + "=" * 60)
    log("QUICK SUMMARY")
    log("=" * 60)
    for token in ['BTC', 'ETH']:
        v = verdicts[token]
        log(f"\n{token}:")
        log(f"  Combined verdict: {v['verdict']}")
        log(f"  Positive windows: {v['positive_windows']}/{v['total_windows']}")
        log(f"  Mean OOS Sharpe: {v['mean_sharpe']:.3f}")
        log(f"  Total trades: {v['total_trades']}")
        for direction in ['long', 'short']:
            dv = verdicts[f"{token}_{direction}"]
            log(f"  {direction}: {dv['verdict']} (Sharpe={dv['mean_sharpe']:.3f}, trades={dv['total_trades']})")
