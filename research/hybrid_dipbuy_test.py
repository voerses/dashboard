#!/workspace/venv/bin/python
"""
Hybrid Dip-Buy Strategy — Walk-Forward Validation
====================================================

Combining two validated findings:
  1. Trend-following (EMA 20/50 cross) provides directional edge (V3: Sharpe 0.56 OOS)
  2. Counter-trend dip buys (RSI<35, z-score<-1.5) identify optimal entries

Hypothesis: Enter at pullbacks WITHIN confirmed trends instead of at EMA cross.
  - Same directional edge, better entry timing.
  - Should improve Sharpe over V3 baseline by getting better prices.

Four variants tested:
  a) V3 Baseline   — EMA 20/50 cross, enter at cross, exit at reverse cross
  b) Hybrid-RSI    — trend filter + RSI<40 dip entry, RSI>75 exit
  c) Hybrid-Zscore — trend filter + z-score<-1.0 entry, z-score>2.0 exit
  d) Hybrid-Multi  — trend filter + (RSI<40 OR z-score<-1.0 OR DD>3%), multi-exit

Walk-forward: 8 windows, 6mo train / 3mo test
Cross-asset: BTC, ETH, BNB
Costs: 0.22% round-trip + 5bps slippage each side
"""

import sys
import time
import warnings
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional
from itertools import product

warnings.filterwarnings('ignore')

def log(msg):
    print(msg)
    sys.stdout.flush()


# ============================================================
# CONFIGURATION
# ============================================================

DATA_DIR = '/workspace/crypto_backtest/data/spot/1h_cache'
TOKENS = ['BTC', 'ETH', 'BNB']

# Costs (realistic for spot)
FEE_RT_PCT = 0.22        # 0.22% round-trip (taker-taker)
SLIPPAGE_BPS = 5          # 5 bps per side
TOTAL_COST_PCT = FEE_RT_PCT / 100 + 2 * SLIPPAGE_BPS / 10000  # = 0.0032

# Walk-forward: 8 non-overlapping windows, 6mo train + 3mo test
N_WINDOWS = 8
TRAIN_DAYS = 180
TEST_DAYS = 90


# ============================================================
# DATA LOADING
# ============================================================

def load_data(token: str) -> Optional[pd.DataFrame]:
    path = f"{DATA_DIR}/{token}_1h.parquet"
    try:
        df = pd.read_parquet(path)
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        df = df[~df.index.duplicated(keep='first')]
        return df
    except FileNotFoundError:
        log(f"  WARNING: No data for {token}")
        return None


# ============================================================
# INDICATOR COMPUTATION
# ============================================================

def compute_rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr)

    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10))
    adx = dx.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    return adx


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()


def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all indicators needed for all four strategy variants."""
    ind = pd.DataFrame(index=df.index)
    c = df['close']
    h = df['high']
    l = df['low']

    # EMAs for trend
    ind['ema_20'] = c.ewm(span=20, adjust=False).mean()
    ind['ema_50'] = c.ewm(span=50, adjust=False).mean()
    ind['trend_bull'] = (ind['ema_20'] > ind['ema_50']).astype(int)

    # RSI(14)
    ind['rsi_14'] = compute_rsi(c, 14)

    # Z-score(20)
    rolling_mean = c.rolling(20).mean()
    rolling_std = c.rolling(20).std()
    ind['zscore_20'] = (c - rolling_mean) / (rolling_std + 1e-10)

    # ADX(14) for trend strength filter
    ind['adx_14'] = compute_adx(h, l, c, 14)

    # ATR(14) for trailing stop
    ind['atr_14'] = compute_atr(h, l, c, 14)

    # Drawdown from 72h high
    rolling_72h_high = h.rolling(72).max()
    ind['drawdown_72h'] = (c - rolling_72h_high) / rolling_72h_high

    # Close price
    ind['close'] = c.values

    return ind


# ============================================================
# WALK-FORWARD WINDOWS
# ============================================================

def build_windows(data_start: pd.Timestamp, data_end: pd.Timestamp,
                  train_days: int = TRAIN_DAYS, test_days: int = TEST_DAYS,
                  n_windows: int = N_WINDOWS):
    """Build non-overlapping walk-forward windows, positioned to maximize data coverage."""
    windows = []
    total_span = (n_windows - 1) * test_days + train_days + test_days
    ideal_first_train = data_end - pd.Timedelta(days=total_span - 1)
    min_first_train = data_start + pd.Timedelta(days=60)  # 60d warmup
    first_train_start = max(ideal_first_train, min_first_train)

    for i in range(n_windows):
        train_start = first_train_start + pd.Timedelta(days=i * test_days)
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


# ============================================================
# TRADE DATACLASS
# ============================================================

@dataclass
class Trade:
    entry_time: object
    entry_price: float
    exit_time: object = None
    exit_price: float = 0.0
    pnl_pct: float = 0.0
    bars_held: int = 0
    exit_reason: str = ''


# ============================================================
# STRATEGY SIMULATORS
# ============================================================

def simulate_v3_baseline(ind: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
                         cost_pct: float = TOTAL_COST_PCT) -> List[Trade]:
    """
    V3 Baseline: Long when EMA20 > EMA50 (weekly check), exit when EMA20 < EMA50.
    Rebalances weekly. Simple trend-following.
    """
    test = ind.loc[start:end]
    if len(test) == 0:
        return []

    close = test['close'].values
    trend = test['trend_bull'].values
    timestamps = test.index

    trades = []
    in_trade = False
    current = None

    # Weekly rebalance check: every 168 bars (7 * 24)
    for i in range(0, len(test)):
        # Only check at weekly intervals (or at entry/exit)
        if i % 168 == 0 or not in_trade:
            if not in_trade and trend[i] == 1:
                # Enter long
                current = Trade(
                    entry_time=timestamps[i],
                    entry_price=close[i],
                )
                in_trade = True
            elif in_trade and trend[i] == 0:
                # Exit
                pnl = (close[i] / current.entry_price - 1) - cost_pct
                current.exit_time = timestamps[i]
                current.exit_price = close[i]
                current.pnl_pct = pnl
                current.bars_held = i - np.searchsorted(timestamps, current.entry_time)
                current.exit_reason = 'trend_reverse'
                trades.append(current)
                in_trade = False
                current = None

    # Close open trade at end
    if in_trade and current is not None:
        pnl = (close[-1] / current.entry_price - 1) - cost_pct
        current.exit_time = timestamps[-1]
        current.exit_price = close[-1]
        current.pnl_pct = pnl
        current.bars_held = len(test) - np.searchsorted(timestamps, current.entry_time)
        current.exit_reason = 'end_of_period'
        trades.append(current)

    return trades


def simulate_hybrid_rsi(ind: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
                        rsi_entry: float = 40.0, rsi_exit: float = 75.0,
                        adx_min: float = 20.0, max_hold_hours: int = 720,
                        trail_atr_mult: float = 2.5,
                        cost_pct: float = TOTAL_COST_PCT) -> List[Trade]:
    """
    Hybrid-RSI: Trend filter (EMA20>EMA50) + RSI dip entry + multi-exit.
    Entry: trend_bull AND RSI(14) < rsi_entry AND ADX > adx_min
    Exit: RSI(14) > rsi_exit OR EMA20 < EMA50 OR trailing stop (2.5 ATR) OR max hold
    """
    test = ind.loc[start:end]
    if len(test) == 0:
        return []

    close = test['close'].values
    trend = test['trend_bull'].values
    rsi = test['rsi_14'].values
    adx = test['adx_14'].values
    atr = test['atr_14'].values
    timestamps = test.index

    trades = []
    in_trade = False
    current = None
    hold = 0
    peak_price = 0.0

    for i in range(1, len(test)):
        if np.isnan(rsi[i]) or np.isnan(close[i]) or np.isnan(adx[i]):
            continue

        if in_trade:
            hold += 1
            peak_price = max(peak_price, close[i])
            exit_reason = None

            # Check exits
            if rsi[i] > rsi_exit:
                exit_reason = 'rsi_exit'
            elif trend[i] == 0:
                exit_reason = 'trend_reverse'
            elif not np.isnan(atr[i]) and atr[i] > 0 and (peak_price - close[i]) > trail_atr_mult * atr[i]:
                exit_reason = 'trail_stop'
            elif hold >= max_hold_hours:
                exit_reason = 'max_hold'

            if exit_reason:
                pnl = (close[i] / current.entry_price - 1) - cost_pct
                current.exit_time = timestamps[i]
                current.exit_price = close[i]
                current.pnl_pct = pnl
                current.bars_held = hold
                current.exit_reason = exit_reason
                trades.append(current)
                in_trade = False
                current = None
                hold = 0
                peak_price = 0.0

        if not in_trade:
            if trend[i] == 1 and rsi[i] < rsi_entry and adx[i] > adx_min:
                current = Trade(
                    entry_time=timestamps[i],
                    entry_price=close[i],
                )
                in_trade = True
                hold = 0
                peak_price = close[i]

    # Close open trade
    if in_trade and current is not None:
        pnl = (close[-1] / current.entry_price - 1) - cost_pct
        current.exit_time = timestamps[-1]
        current.exit_price = close[-1]
        current.pnl_pct = pnl
        current.bars_held = hold
        current.exit_reason = 'end_of_period'
        trades.append(current)

    return trades


def simulate_hybrid_zscore(ind: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
                           zscore_entry: float = -1.0, zscore_exit: float = 2.0,
                           adx_min: float = 20.0, max_hold_hours: int = 720,
                           trail_atr_mult: float = 2.5,
                           cost_pct: float = TOTAL_COST_PCT) -> List[Trade]:
    """
    Hybrid-Zscore: Trend filter + z-score dip entry + multi-exit.
    Entry: trend_bull AND zscore(20) < zscore_entry AND ADX > adx_min
    Exit: zscore > zscore_exit OR EMA20 < EMA50 OR trailing stop OR max hold
    """
    test = ind.loc[start:end]
    if len(test) == 0:
        return []

    close = test['close'].values
    trend = test['trend_bull'].values
    zscore = test['zscore_20'].values
    adx = test['adx_14'].values
    atr = test['atr_14'].values
    timestamps = test.index

    trades = []
    in_trade = False
    current = None
    hold = 0
    peak_price = 0.0

    for i in range(1, len(test)):
        if np.isnan(zscore[i]) or np.isnan(close[i]) or np.isnan(adx[i]):
            continue

        if in_trade:
            hold += 1
            peak_price = max(peak_price, close[i])
            exit_reason = None

            if zscore[i] > zscore_exit:
                exit_reason = 'zscore_exit'
            elif trend[i] == 0:
                exit_reason = 'trend_reverse'
            elif not np.isnan(atr[i]) and atr[i] > 0 and (peak_price - close[i]) > trail_atr_mult * atr[i]:
                exit_reason = 'trail_stop'
            elif hold >= max_hold_hours:
                exit_reason = 'max_hold'

            if exit_reason:
                pnl = (close[i] / current.entry_price - 1) - cost_pct
                current.exit_time = timestamps[i]
                current.exit_price = close[i]
                current.pnl_pct = pnl
                current.bars_held = hold
                current.exit_reason = exit_reason
                trades.append(current)
                in_trade = False
                current = None
                hold = 0
                peak_price = 0.0

        if not in_trade:
            if trend[i] == 1 and zscore[i] < zscore_entry and adx[i] > adx_min:
                current = Trade(
                    entry_time=timestamps[i],
                    entry_price=close[i],
                )
                in_trade = True
                hold = 0
                peak_price = close[i]

    # Close open trade
    if in_trade and current is not None:
        pnl = (close[-1] / current.entry_price - 1) - cost_pct
        current.exit_time = timestamps[-1]
        current.exit_price = close[-1]
        current.pnl_pct = pnl
        current.bars_held = hold
        current.exit_reason = 'end_of_period'
        trades.append(current)

    return trades


def simulate_hybrid_multi(ind: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
                          rsi_entry: float = 40.0, zscore_entry: float = -1.0,
                          dd_thresh: float = -0.03,
                          rsi_exit: float = 75.0, zscore_exit: float = 2.0,
                          adx_min: float = 20.0, max_hold_hours: int = 720,
                          trail_atr_mult: float = 2.5,
                          cost_pct: float = TOTAL_COST_PCT) -> List[Trade]:
    """
    Hybrid-Multi: Trend filter + multiple dip conditions (OR) + multi-exit.
    Entry: trend_bull AND (RSI<40 OR zscore<-1.0 OR DD>3%) AND ADX>20
    Exit: RSI>75 OR zscore>2.0 OR EMA20<EMA50 OR trailing stop OR max hold
    """
    test = ind.loc[start:end]
    if len(test) == 0:
        return []

    close = test['close'].values
    trend = test['trend_bull'].values
    rsi = test['rsi_14'].values
    zscore = test['zscore_20'].values
    dd72 = test['drawdown_72h'].values
    adx = test['adx_14'].values
    atr = test['atr_14'].values
    timestamps = test.index

    trades = []
    in_trade = False
    current = None
    hold = 0
    peak_price = 0.0

    for i in range(1, len(test)):
        if np.isnan(rsi[i]) or np.isnan(close[i]) or np.isnan(adx[i]):
            continue

        if in_trade:
            hold += 1
            peak_price = max(peak_price, close[i])
            exit_reason = None

            if rsi[i] > rsi_exit:
                exit_reason = 'rsi_exit'
            elif not np.isnan(zscore[i]) and zscore[i] > zscore_exit:
                exit_reason = 'zscore_exit'
            elif trend[i] == 0:
                exit_reason = 'trend_reverse'
            elif not np.isnan(atr[i]) and atr[i] > 0 and (peak_price - close[i]) > trail_atr_mult * atr[i]:
                exit_reason = 'trail_stop'
            elif hold >= max_hold_hours:
                exit_reason = 'max_hold'

            if exit_reason:
                pnl = (close[i] / current.entry_price - 1) - cost_pct
                current.exit_time = timestamps[i]
                current.exit_price = close[i]
                current.pnl_pct = pnl
                current.bars_held = hold
                current.exit_reason = exit_reason
                trades.append(current)
                in_trade = False
                current = None
                hold = 0
                peak_price = 0.0

        if not in_trade:
            # Entry: trend + any dip signal + ADX
            dip_rsi = rsi[i] < rsi_entry
            dip_zscore = not np.isnan(zscore[i]) and zscore[i] < zscore_entry
            dip_dd = not np.isnan(dd72[i]) and dd72[i] < dd_thresh
            any_dip = dip_rsi or dip_zscore or dip_dd

            if trend[i] == 1 and any_dip and adx[i] > adx_min:
                current = Trade(
                    entry_time=timestamps[i],
                    entry_price=close[i],
                )
                in_trade = True
                hold = 0
                peak_price = close[i]

    # Close open trade
    if in_trade and current is not None:
        pnl = (close[-1] / current.entry_price - 1) - cost_pct
        current.exit_time = timestamps[-1]
        current.exit_price = close[-1]
        current.pnl_pct = pnl
        current.bars_held = hold
        current.exit_reason = 'end_of_period'
        trades.append(current)

    return trades


def simulate_s320a_rsi_timing(ind: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
                               rsi_entry: float = 40.0, adx_min: float = 20.0,
                               search_window_hours: int = 168,
                               cost_pct: float = TOTAL_COST_PCT) -> List[Trade]:
    """
    s320a RSI Timing: Enter s320a-style (weekly rebalance, EMA trend filter)
    but defer entry until RSI < threshold within the search window.
    If no dip occurs, enter at end of search window (fallback).

    This simulates: "Add RSI dip-timing to s320a's entry logic."
    """
    test = ind.loc[start:end]
    if len(test) == 0:
        return []

    close = test['close'].values
    trend = test['trend_bull'].values
    rsi = test['rsi_14'].values
    timestamps = test.index

    trades = []
    in_trade = False
    current = None
    searching = False
    search_start_idx = 0

    # Weekly rebalance points
    for i in range(0, len(test)):
        # Weekly check (every 168 bars)
        if i % 168 == 0:
            if not in_trade and trend[i] == 1:
                # Start searching for RSI dip
                searching = True
                search_start_idx = i
            elif in_trade and trend[i] == 0:
                # Exit on trend reversal
                pnl = (close[i] / current.entry_price - 1) - cost_pct
                current.exit_time = timestamps[i]
                current.exit_price = close[i]
                current.pnl_pct = pnl
                current.bars_held = i - np.searchsorted(timestamps, current.entry_time)
                current.exit_reason = 'trend_reverse'
                trades.append(current)
                in_trade = False
                current = None
                searching = False

        # While searching for RSI dip within search window
        if searching and not in_trade:
            bars_searching = i - search_start_idx
            if not np.isnan(rsi[i]) and rsi[i] < rsi_entry:
                # RSI dip found -> enter
                current = Trade(
                    entry_time=timestamps[i],
                    entry_price=close[i],
                )
                in_trade = True
                searching = False
            elif bars_searching >= search_window_hours:
                # Fallback: enter at end of search window
                current = Trade(
                    entry_time=timestamps[i],
                    entry_price=close[i],
                )
                in_trade = True
                searching = False

    # Close open trade
    if in_trade and current is not None:
        pnl = (close[-1] / current.entry_price - 1) - cost_pct
        current.exit_time = timestamps[-1]
        current.exit_price = close[-1]
        current.pnl_pct = pnl
        current.bars_held = len(test) - np.searchsorted(timestamps, current.entry_time)
        current.exit_reason = 'end_of_period'
        trades.append(current)

    return trades


# ============================================================
# METRICS COMPUTATION
# ============================================================

@dataclass
class Metrics:
    total_pnl: float = 0.0
    sharpe: float = 0.0
    trade_count: int = 0
    win_rate: float = 0.0
    max_dd: float = 0.0
    avg_pnl: float = 0.0
    profit_factor: float = 0.0
    avg_hold_hours: float = 0.0


def compute_metrics(trades: List[Trade]) -> Metrics:
    m = Metrics()
    if not trades:
        return m

    pnls = np.array([t.pnl_pct for t in trades])
    m.trade_count = len(trades)
    m.total_pnl = float(np.sum(pnls))
    m.avg_pnl = float(np.mean(pnls))
    m.win_rate = float(np.sum(pnls > 0) / len(pnls))
    m.avg_hold_hours = float(np.mean([t.bars_held for t in trades]))

    gp = float(np.sum(pnls[pnls > 0])) if np.any(pnls > 0) else 0.0
    gl = float(np.abs(np.sum(pnls[pnls < 0]))) if np.any(pnls < 0) else 0.0
    m.profit_factor = gp / gl if gl > 0 else (999.0 if gp > 0 else 0.0)

    if len(pnls) > 1 and np.std(pnls) > 0:
        avg_hold = np.mean([t.bars_held for t in trades])
        tpy = 8760 / max(avg_hold, 1)
        m.sharpe = float((np.mean(pnls) / np.std(pnls)) * np.sqrt(tpy))
    else:
        m.sharpe = 0.0

    equity = np.cumprod(1 + pnls)
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    m.max_dd = float(np.abs(np.min(dd))) if len(dd) > 0 else 0.0

    return m


def compute_daily_sharpe(trades: List[Trade], start: pd.Timestamp, end: pd.Timestamp) -> float:
    """Compute annualized Sharpe from daily returns series (alternative metric)."""
    if not trades:
        return 0.0

    # Build daily PnL series from trades
    days = pd.date_range(start, end, freq='D')
    daily_pnl = pd.Series(0.0, index=days)

    for t in trades:
        if t.exit_time is None:
            continue
        entry_date = pd.Timestamp(t.entry_time).normalize()
        exit_date = pd.Timestamp(t.exit_time).normalize()
        n_days = max((exit_date - entry_date).days, 1)
        daily_return = t.pnl_pct / n_days
        for d in pd.date_range(entry_date, exit_date, freq='D'):
            if d in daily_pnl.index:
                daily_pnl.loc[d] += daily_return

    if daily_pnl.std() > 0:
        return float((daily_pnl.mean() / daily_pnl.std()) * np.sqrt(365))
    return 0.0


# ============================================================
# WALK-FORWARD ENGINE
# ============================================================

def run_walk_forward_single(ind: pd.DataFrame, strategy_fn, strategy_name: str,
                            windows: List[Dict], default_params: Dict,
                            param_grid: Optional[Dict] = None) -> List[Dict]:
    """
    Run walk-forward for a single strategy variant.
    If param_grid is provided, optimize on train, apply best to test.
    Otherwise use default_params for all windows.
    """
    results = []

    for w in windows:
        if param_grid:
            # Grid search on train period
            best_sharpe = -999.0
            best_params = default_params.copy()

            keys = list(param_grid.keys())
            values = list(param_grid.values())

            for combo in product(*values):
                test_params = default_params.copy()
                for k, v in zip(keys, combo):
                    test_params[k] = v

                train_trades = strategy_fn(ind, w['train_start'], w['train_end'], **test_params)
                train_m = compute_metrics(train_trades)

                if train_m.trade_count >= 3 and train_m.sharpe > best_sharpe:
                    best_sharpe = train_m.sharpe
                    best_params = test_params.copy()

            # Apply best params to OOS
            oos_trades = strategy_fn(ind, w['test_start'], w['test_end'], **best_params)
            train_sharpe = best_sharpe
            optimized_params = best_params
        else:
            # Fixed params
            oos_trades = strategy_fn(ind, w['test_start'], w['test_end'], **default_params)
            train_trades = strategy_fn(ind, w['train_start'], w['train_end'], **default_params)
            train_m = compute_metrics(train_trades)
            train_sharpe = train_m.sharpe
            optimized_params = default_params

        oos_m = compute_metrics(oos_trades)
        daily_sharpe = compute_daily_sharpe(oos_trades, w['test_start'], w['test_end'])

        # Exit reason distribution
        exit_reasons = {}
        for t in oos_trades:
            r = t.exit_reason
            exit_reasons[r] = exit_reasons.get(r, 0) + 1

        results.append({
            'window': w['id'],
            'test_start': w['test_start'],
            'test_end': w['test_end'],
            'train_sharpe': train_sharpe,
            'oos_sharpe': oos_m.sharpe,
            'oos_daily_sharpe': daily_sharpe,
            'oos_return': oos_m.total_pnl,
            'oos_trades': oos_m.trade_count,
            'oos_winrate': oos_m.win_rate,
            'oos_maxdd': oos_m.max_dd,
            'oos_avg_pnl': oos_m.avg_pnl,
            'oos_pf': oos_m.profit_factor,
            'oos_avg_hold': oos_m.avg_hold_hours,
            'params': optimized_params,
            'exit_reasons': exit_reasons,
            'trades_list': oos_trades,
        })

    return results


# ============================================================
# PARAMETER SENSITIVITY (Hybrid-RSI)
# ============================================================

def run_rsi_sensitivity(ind: pd.DataFrame, windows: List[Dict]) -> Dict:
    """
    Test RSI entry/exit threshold sensitivity for Hybrid-RSI.
    RSI entry: 30, 35, 40, 45
    RSI exit: 70, 75, 80
    """
    log("  Running RSI parameter sensitivity...")
    rsi_entries = [30, 35, 40, 45]
    rsi_exits = [70, 75, 80]

    results = {}
    for re_val in rsi_entries:
        for rx_val in rsi_exits:
            key = f"entry{re_val}_exit{rx_val}"
            all_oos_sharpes = []
            all_oos_returns = []
            all_oos_trades = []

            for w in windows:
                trades = simulate_hybrid_rsi(
                    ind, w['test_start'], w['test_end'],
                    rsi_entry=float(re_val), rsi_exit=float(rx_val)
                )
                m = compute_metrics(trades)
                all_oos_sharpes.append(m.sharpe)
                all_oos_returns.append(m.total_pnl)
                all_oos_trades.append(m.trade_count)

            results[key] = {
                'rsi_entry': re_val,
                'rsi_exit': rx_val,
                'mean_sharpe': float(np.mean(all_oos_sharpes)),
                'median_sharpe': float(np.median(all_oos_sharpes)),
                'mean_return': float(np.mean(all_oos_returns)),
                'total_trades': sum(all_oos_trades),
                'pos_windows': sum(1 for s in all_oos_sharpes if s > 0),
                'all_sharpes': all_oos_sharpes,
            }

    return results


# ============================================================
# CORRELATION ANALYSIS
# ============================================================

def compute_strategy_correlation(results_dict: Dict[str, List[Dict]]) -> pd.DataFrame:
    """
    Compute correlation between strategies using per-window OOS returns.
    High correlation = redundant. Low correlation = diversifying.
    """
    strategy_returns = {}
    for name, results in results_dict.items():
        strategy_returns[name] = [r['oos_return'] for r in results]

    df = pd.DataFrame(strategy_returns)
    return df.corr()


def compute_trade_overlap(results_a: List[Dict], results_b: List[Dict]) -> float:
    """Compute how much trades overlap in time between two strategies."""
    trades_a = []
    trades_b = []
    for r in results_a:
        trades_a.extend(r['trades_list'])
    for r in results_b:
        trades_b.extend(r['trades_list'])

    if not trades_a or not trades_b:
        return 0.0

    # Check temporal overlap
    overlap_count = 0
    for ta in trades_a:
        for tb in trades_b:
            if ta.exit_time is None or tb.exit_time is None:
                continue
            # Check if trade periods overlap
            latest_start = max(ta.entry_time, tb.entry_time)
            earliest_end = min(ta.exit_time, tb.exit_time)
            if latest_start < earliest_end:
                overlap_count += 1
                break

    return overlap_count / len(trades_a) if trades_a else 0.0


# ============================================================
# MAIN EXECUTION
# ============================================================

def main():
    t0 = time.time()
    log("=" * 78)
    log("HYBRID DIP-BUY STRATEGY — WALK-FORWARD VALIDATION")
    log("=" * 78)
    log("")

    # ============================================================
    # Part 1: Load data & compute indicators for all tokens
    # ============================================================
    all_indicators = {}
    all_windows = {}

    for token in TOKENS:
        log(f"Loading {token}...")
        df = load_data(token)
        if df is None:
            continue

        ind = compute_all_indicators(df)
        all_indicators[token] = ind

        data_start = ind.index.min()
        data_end = ind.index.max()
        windows = build_windows(data_start, data_end)
        all_windows[token] = windows

        log(f"  Data: {data_start.date()} to {data_end.date()}, {len(ind)} bars")
        log(f"  Windows: {len(windows)}")
        for w in windows:
            log(f"    {w['id']}: train {w['train_start'].date()}-{w['train_end'].date()}, "
                f"test {w['test_start'].date()}-{w['test_end'].date()}")

    log("")

    # ============================================================
    # Part 2: Run all four strategies on all tokens
    # ============================================================
    all_results = {}  # {token: {strategy_name: [window_results]}}

    for token in TOKENS:
        if token not in all_indicators:
            continue

        ind = all_indicators[token]
        windows = all_windows[token]
        all_results[token] = {}

        log(f"\n{'='*78}")
        log(f"  {token} — Running all strategies")
        log(f"{'='*78}")

        # a) V3 Baseline
        log(f"\n  [{token}] V3 Baseline (EMA cross)...")
        v3_results = run_walk_forward_single(
            ind, simulate_v3_baseline, 'V3_Baseline', windows,
            default_params={},
        )
        all_results[token]['V3_Baseline'] = v3_results
        for r in v3_results:
            log(f"    {r['window']}: Sharpe={r['oos_sharpe']:.2f}, Ret={r['oos_return']:.2%}, "
                f"n={r['oos_trades']}, WR={r['oos_winrate']:.1%}, DD={r['oos_maxdd']:.2%}")

        # b) Hybrid-RSI
        log(f"\n  [{token}] Hybrid-RSI (trend + RSI dip)...")
        hybrid_rsi_results = run_walk_forward_single(
            ind, simulate_hybrid_rsi, 'Hybrid_RSI', windows,
            default_params={'rsi_entry': 40.0, 'rsi_exit': 75.0, 'adx_min': 20.0,
                            'max_hold_hours': 720, 'trail_atr_mult': 2.5},
            param_grid={
                'rsi_entry': [35.0, 40.0, 45.0],
                'rsi_exit': [70.0, 75.0, 80.0],
            },
        )
        all_results[token]['Hybrid_RSI'] = hybrid_rsi_results
        for r in hybrid_rsi_results:
            log(f"    {r['window']}: Sharpe={r['oos_sharpe']:.2f}, Ret={r['oos_return']:.2%}, "
                f"n={r['oos_trades']}, WR={r['oos_winrate']:.1%}, DD={r['oos_maxdd']:.2%}, "
                f"params=RSI<{r['params'].get('rsi_entry', '?')}>RSI>{r['params'].get('rsi_exit', '?')}")

        # c) Hybrid-Zscore
        log(f"\n  [{token}] Hybrid-Zscore (trend + z-score dip)...")
        hybrid_zs_results = run_walk_forward_single(
            ind, simulate_hybrid_zscore, 'Hybrid_Zscore', windows,
            default_params={'zscore_entry': -1.0, 'zscore_exit': 2.0, 'adx_min': 20.0,
                            'max_hold_hours': 720, 'trail_atr_mult': 2.5},
            param_grid={
                'zscore_entry': [-0.5, -1.0, -1.5],
                'zscore_exit': [1.5, 2.0, 2.5],
            },
        )
        all_results[token]['Hybrid_Zscore'] = hybrid_zs_results
        for r in hybrid_zs_results:
            log(f"    {r['window']}: Sharpe={r['oos_sharpe']:.2f}, Ret={r['oos_return']:.2%}, "
                f"n={r['oos_trades']}, WR={r['oos_winrate']:.1%}, DD={r['oos_maxdd']:.2%}, "
                f"params=Z<{r['params'].get('zscore_entry', '?')}>Z>{r['params'].get('zscore_exit', '?')}")

        # d) Hybrid-Multi
        log(f"\n  [{token}] Hybrid-Multi (trend + RSI|Z|DD dip)...")
        hybrid_multi_results = run_walk_forward_single(
            ind, simulate_hybrid_multi, 'Hybrid_Multi', windows,
            default_params={'rsi_entry': 40.0, 'zscore_entry': -1.0, 'dd_thresh': -0.03,
                            'rsi_exit': 75.0, 'zscore_exit': 2.0, 'adx_min': 20.0,
                            'max_hold_hours': 720, 'trail_atr_mult': 2.5},
            param_grid={
                'rsi_entry': [35.0, 40.0, 45.0],
                'zscore_entry': [-0.5, -1.0, -1.5],
            },
        )
        all_results[token]['Hybrid_Multi'] = hybrid_multi_results
        for r in hybrid_multi_results:
            log(f"    {r['window']}: Sharpe={r['oos_sharpe']:.2f}, Ret={r['oos_return']:.2%}, "
                f"n={r['oos_trades']}, WR={r['oos_winrate']:.1%}, DD={r['oos_maxdd']:.2%}")

        # e) s320a RSI Timing
        log(f"\n  [{token}] s320a + RSI Timing (defer entry to RSI dip)...")
        s320a_rsi_results = run_walk_forward_single(
            ind, simulate_s320a_rsi_timing, 's320a_RSI_Timing', windows,
            default_params={'rsi_entry': 40.0, 'adx_min': 20.0, 'search_window_hours': 168},
            param_grid={
                'rsi_entry': [35.0, 40.0, 45.0],
                'search_window_hours': [96, 168],
            },
        )
        all_results[token]['s320a_RSI_Timing'] = s320a_rsi_results
        for r in s320a_rsi_results:
            log(f"    {r['window']}: Sharpe={r['oos_sharpe']:.2f}, Ret={r['oos_return']:.2%}, "
                f"n={r['oos_trades']}, WR={r['oos_winrate']:.1%}, DD={r['oos_maxdd']:.2%}")

    # ============================================================
    # Part 3: RSI Parameter Sensitivity (BTC only)
    # ============================================================
    log(f"\n{'='*78}")
    log("PARAMETER SENSITIVITY — Hybrid-RSI on BTC")
    log(f"{'='*78}")

    rsi_sens = run_rsi_sensitivity(all_indicators['BTC'], all_windows['BTC'])
    log("\n  RSI Entry/Exit Sensitivity (mean OOS Sharpe):")
    log(f"  {'':>10s} | RSI Exit 70 | RSI Exit 75 | RSI Exit 80")
    log(f"  {'-'*10}-+-{'-'*11}-+-{'-'*11}-+-{'-'*11}")
    for re_val in [30, 35, 40, 45]:
        row = f"  Entry {re_val:>3d} |"
        for rx_val in [70, 75, 80]:
            key = f"entry{re_val}_exit{rx_val}"
            s = rsi_sens[key]['mean_sharpe']
            row += f" {s:>+10.3f} |"
        log(row)

    # ============================================================
    # Part 4: Correlation Analysis (BTC)
    # ============================================================
    log(f"\n{'='*78}")
    log("CORRELATION ANALYSIS — BTC strategies")
    log(f"{'='*78}")

    btc_results = all_results['BTC']
    corr_matrix = compute_strategy_correlation(btc_results)
    log("\n  Return Correlation Matrix:")
    log(f"  {corr_matrix.to_string()}")

    # Trade overlap
    log("\n  Trade Temporal Overlap:")
    strategies = list(btc_results.keys())
    for i, s1 in enumerate(strategies):
        for s2 in strategies[i+1:]:
            overlap = compute_trade_overlap(btc_results[s1], btc_results[s2])
            log(f"    {s1} vs {s2}: {overlap:.1%} overlap")

    # ============================================================
    # Part 5: Generate Comparison Tables
    # ============================================================
    log(f"\n{'='*78}")
    log("SUMMARY COMPARISON TABLES")
    log(f"{'='*78}")

    for token in TOKENS:
        if token not in all_results:
            continue

        log(f"\n  === {token} ===")
        log(f"  {'Strategy':<20s} | {'Mean Sharpe':>11s} | {'Med Sharpe':>10s} | {'Mean Ret':>9s} | "
            f"{'Trades':>6s} | {'WR':>5s} | {'MaxDD':>6s} | {'Pos Win':>7s}")
        log(f"  {'-'*20}-+-{'-'*11}-+-{'-'*10}-+-{'-'*9}-+-"
            f"{'-'*6}-+-{'-'*5}-+-{'-'*6}-+-{'-'*7}")

        for strat_name, results in all_results[token].items():
            sharpes = [r['oos_sharpe'] for r in results]
            returns = [r['oos_return'] for r in results]
            trades = sum(r['oos_trades'] for r in results)
            wrs = [r['oos_winrate'] for r in results if r['oos_trades'] > 0]
            dds = [r['oos_maxdd'] for r in results if r['oos_trades'] > 0]
            pos_win = sum(1 for s in sharpes if s > 0)

            mean_sharpe = np.mean(sharpes)
            med_sharpe = np.median(sharpes)
            mean_ret = np.mean(returns)
            mean_wr = np.mean(wrs) if wrs else 0.0
            mean_dd = np.mean(dds) if dds else 0.0

            log(f"  {strat_name:<20s} | {mean_sharpe:>+11.3f} | {med_sharpe:>+10.3f} | "
                f"{mean_ret:>+9.2%} | {trades:>6d} | {mean_wr:>5.1%} | "
                f"{mean_dd:>6.2%} | {pos_win:>3d}/{len(sharpes)}")

    # ============================================================
    # Part 6: Generate Report
    # ============================================================
    elapsed = time.time() - t0
    log(f"\nTotal elapsed: {elapsed:.1f}s")
    log(f"\n{'='*78}")
    log("GENERATING REPORT...")
    log(f"{'='*78}")

    report = generate_report(all_results, rsi_sens, corr_matrix, all_windows)
    output_path = '/workspace/crypto_backtest/research/hybrid_dipbuy_results.md'
    with open(output_path, 'w') as f:
        f.write(report)
    log(f"\nReport written to: {output_path}")

    # Final verdict
    log(f"\n{'='*78}")
    log("FINAL VERDICTS")
    log(f"{'='*78}")

    for token in TOKENS:
        if token not in all_results:
            continue
        log(f"\n  {token}:")
        for strat_name, results in all_results[token].items():
            sharpes = [r['oos_sharpe'] for r in results]
            mean_s = np.mean(sharpes)
            pos = sum(1 for s in sharpes if s > 0)
            total = len(sharpes)
            trades = sum(r['oos_trades'] for r in results)

            if pos >= 5 and mean_s > 0.3 and trades >= 20:
                verdict = 'PASS'
            elif pos >= 4 and mean_s > 0.0 and trades >= 10:
                verdict = 'CONDITIONAL'
            else:
                verdict = 'KILL'

            log(f"    {strat_name:<20s}: {verdict:<12s} (mean Sharpe={mean_s:+.3f}, "
                f"pos={pos}/{total}, trades={trades})")


# ============================================================
# REPORT GENERATION
# ============================================================

def generate_report(all_results: Dict, rsi_sens: Dict, corr_matrix: pd.DataFrame,
                    all_windows: Dict) -> str:
    lines = []
    lines.append("# Hybrid Dip-Buy Strategy — Walk-Forward Validation Results")
    lines.append("")
    lines.append(f"**Run date**: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")

    # --- Strategy Definitions ---
    lines.append("## Strategy Definitions")
    lines.append("")
    lines.append("| Strategy | Direction | Entry | Exit | Rationale |")
    lines.append("|----------|-----------|-------|------|-----------|")
    lines.append("| V3 Baseline | Long-only | EMA20 > EMA50 (weekly) | EMA20 < EMA50 | Validated trend-following (Sharpe 0.56 OOS) |")
    lines.append("| Hybrid-RSI | Long-only | Trend + RSI<40 + ADX>20 | RSI>75 / trend reverse / trail stop 2.5ATR | Pullback entry within trend |")
    lines.append("| Hybrid-Zscore | Long-only | Trend + z-score<-1.0 + ADX>20 | z-score>2.0 / trend reverse / trail stop | Mean-reversion entry within trend |")
    lines.append("| Hybrid-Multi | Long-only | Trend + (RSI<40 OR z<-1.0 OR DD>3%) + ADX>20 | RSI>75 / z>2.0 / trend / trail / max hold | Maximum signal coverage |")
    lines.append("| s320a+RSI Timing | Long-only | Trend (weekly) + defer to RSI<40 within 168h | Trend reverse (weekly check) | V2 Flexible RSI timing on s320a entries |")
    lines.append("")

    lines.append("## Walk-Forward Protocol")
    lines.append("")
    lines.append(f"- **Windows**: {N_WINDOWS} rolling, {TRAIN_DAYS}d train / {TEST_DAYS}d test")
    lines.append(f"- **Costs**: {FEE_RT_PCT}% RT fees + {SLIPPAGE_BPS}bps slippage/side = {TOTAL_COST_PCT*100:.2f}% total per trade")
    lines.append("- **Optimization**: Grid search on train, best params applied to OOS")
    lines.append("- **Kill criteria**: Mean OOS Sharpe > 0.3, >=5/8 positive windows, >=20 total trades")
    lines.append("")

    # --- Per-Token Results ---
    for token in ['BTC', 'ETH', 'BNB']:
        if token not in all_results:
            continue

        lines.append(f"## {token} Results")
        lines.append("")

        # Summary table
        lines.append("### Summary")
        lines.append("")
        lines.append("| Strategy | Mean Sharpe | Med Sharpe | Mean Return | Total Trades | Win Rate | Mean MaxDD | Pos Windows |")
        lines.append("|----------|------------|------------|-------------|-------------|----------|------------|-------------|")

        for strat_name, results in all_results[token].items():
            sharpes = [r['oos_sharpe'] for r in results]
            returns = [r['oos_return'] for r in results]
            trades = sum(r['oos_trades'] for r in results)
            wrs = [r['oos_winrate'] for r in results if r['oos_trades'] > 0]
            dds = [r['oos_maxdd'] for r in results if r['oos_trades'] > 0]
            pos_win = sum(1 for s in sharpes if s > 0)

            mean_sharpe = np.mean(sharpes)
            med_sharpe = np.median(sharpes)
            mean_ret = np.mean(returns)
            mean_wr = np.mean(wrs) if wrs else 0.0
            mean_dd = np.mean(dds) if dds else 0.0

            lines.append(
                f"| {strat_name} | {mean_sharpe:+.3f} | {med_sharpe:+.3f} | "
                f"{mean_ret:+.2%} | {trades} | {mean_wr:.1%} | "
                f"{mean_dd:.2%} | {pos_win}/{len(sharpes)} |"
            )
        lines.append("")

        # Per-window detail
        lines.append("### Per-Window OOS Detail")
        lines.append("")
        lines.append("| Window | Test Period | V3 Sharpe | H-RSI Sharpe | H-Zscore Sharpe | H-Multi Sharpe | s320a+RSI Sharpe |")
        lines.append("|--------|------------|-----------|-------------|----------------|---------------|-----------------|")

        strat_names = list(all_results[token].keys())
        n_windows = len(all_results[token][strat_names[0]])

        for wi in range(n_windows):
            w = all_results[token][strat_names[0]][wi]
            row = f"| {w['window']} | {w['test_start'].strftime('%Y-%m-%d')} to {w['test_end'].strftime('%Y-%m-%d')} |"
            for sn in strat_names:
                r = all_results[token][sn][wi]
                row += f" {r['oos_sharpe']:+.2f} |"
            lines.append(row)
        lines.append("")

        # Per-window trade counts
        lines.append("### Per-Window Trade Counts")
        lines.append("")
        lines.append("| Window | V3 | H-RSI | H-Zscore | H-Multi | s320a+RSI |")
        lines.append("|--------|-----|-------|----------|---------|-----------|")
        for wi in range(n_windows):
            w = all_results[token][strat_names[0]][wi]
            row = f"| {w['window']} |"
            for sn in strat_names:
                r = all_results[token][sn][wi]
                row += f" {r['oos_trades']} |"
            lines.append(row)
        lines.append("")

    # --- RSI Sensitivity ---
    lines.append("## Parameter Sensitivity — Hybrid-RSI (BTC)")
    lines.append("")
    lines.append("Mean OOS Sharpe across all windows for each RSI entry/exit combination:")
    lines.append("")
    lines.append("| Entry \\ Exit | RSI 70 | RSI 75 | RSI 80 |")
    lines.append("|-------------|--------|--------|--------|")
    for re_val in [30, 35, 40, 45]:
        row = f"| RSI < {re_val} |"
        for rx_val in [70, 75, 80]:
            key = f"entry{re_val}_exit{rx_val}"
            s = rsi_sens[key]['mean_sharpe']
            t = rsi_sens[key]['total_trades']
            row += f" {s:+.3f} (n={t}) |"
        lines.append(row)
    lines.append("")

    # Best combo
    best_key = max(rsi_sens.keys(), key=lambda k: rsi_sens[k]['mean_sharpe'])
    best = rsi_sens[best_key]
    lines.append(f"**Best combination**: RSI entry < {best['rsi_entry']}, RSI exit > {best['rsi_exit']} "
                 f"(mean Sharpe {best['mean_sharpe']:+.3f}, {best['pos_windows']}/{N_WINDOWS} positive windows)")
    lines.append("")

    # Sensitivity assessment
    all_sens_sharpes = [rsi_sens[k]['mean_sharpe'] for k in rsi_sens]
    sens_range = max(all_sens_sharpes) - min(all_sens_sharpes)
    sens_std = np.std(all_sens_sharpes)
    lines.append(f"**Sensitivity**: Sharpe range {sens_range:.3f}, std {sens_std:.3f}")
    if sens_range > 0.5:
        lines.append("WARNING: High parameter sensitivity detected. Signal may be fragile.")
    elif sens_range < 0.2:
        lines.append("Parameter sensitivity is low. Signal is robust to threshold choices.")
    else:
        lines.append("Moderate parameter sensitivity. Core signal exists but fine-tuning matters.")
    lines.append("")

    # --- Correlation ---
    lines.append("## Correlation Analysis (BTC)")
    lines.append("")
    lines.append("Return correlation between strategies (per-window OOS returns):")
    lines.append("")

    # Format correlation matrix as markdown table
    strats = list(corr_matrix.columns)
    header = "| |" + "|".join(f" {s} " for s in strats) + "|"
    sep = "|---|" + "|".join("---" for _ in strats) + "|"
    lines.append(header)
    lines.append(sep)
    for s1 in strats:
        row = f"| {s1} |"
        for s2 in strats:
            row += f" {corr_matrix.loc[s1, s2]:+.3f} |"
        lines.append(row)
    lines.append("")

    # Interpretation
    v3_hybrid_corrs = []
    for s in strats:
        if s != 'V3_Baseline' and 'V3_Baseline' in corr_matrix.columns:
            v3_hybrid_corrs.append(corr_matrix.loc['V3_Baseline', s])

    if v3_hybrid_corrs:
        avg_v3_corr = np.mean(v3_hybrid_corrs)
        if avg_v3_corr > 0.7:
            lines.append(f"**V3 vs Hybrid avg correlation: {avg_v3_corr:+.3f}** -- Strategies are largely REDUNDANT. "
                         "Hybrid is a refinement of V3, not a diversifier.")
        elif avg_v3_corr > 0.3:
            lines.append(f"**V3 vs Hybrid avg correlation: {avg_v3_corr:+.3f}** -- Moderate correlation. "
                         "Some diversification benefit, but significant overlap.")
        else:
            lines.append(f"**V3 vs Hybrid avg correlation: {avg_v3_corr:+.3f}** -- Low correlation. "
                         "Strategies are genuinely diversifying.")
    lines.append("")

    # --- Final Verdicts ---
    lines.append("## Final Verdicts")
    lines.append("")
    lines.append("| Token | Strategy | Verdict | Mean Sharpe | Pos Windows | Trades | Reason |")
    lines.append("|-------|----------|---------|------------|-------------|--------|--------|")

    for token in ['BTC', 'ETH', 'BNB']:
        if token not in all_results:
            continue
        for strat_name, results in all_results[token].items():
            sharpes = [r['oos_sharpe'] for r in results]
            mean_s = np.mean(sharpes)
            pos = sum(1 for s in sharpes if s > 0)
            total = len(sharpes)
            trades = sum(r['oos_trades'] for r in results)

            if pos >= 5 and mean_s > 0.3 and trades >= 20:
                verdict = 'PASS'
                reason = 'All criteria met'
            elif pos >= 4 and mean_s > 0.0 and trades >= 10:
                verdict = 'CONDITIONAL'
                reasons = []
                if pos < 5:
                    reasons.append(f'pos windows {pos}/{total}')
                if mean_s <= 0.3:
                    reasons.append(f'mean Sharpe {mean_s:.3f} <= 0.3')
                if trades < 20:
                    reasons.append(f'trades {trades} < 20')
                reason = '; '.join(reasons)
            else:
                verdict = 'KILL'
                reasons = []
                if pos < 4:
                    reasons.append(f'pos windows {pos}/{total}')
                if mean_s <= 0.0:
                    reasons.append(f'negative mean Sharpe {mean_s:.3f}')
                if trades < 10:
                    reasons.append(f'trades {trades} < 10')
                reason = '; '.join(reasons) if reasons else 'Below thresholds'

            lines.append(
                f"| {token} | {strat_name} | **{verdict}** | {mean_s:+.3f} | "
                f"{pos}/{total} | {trades} | {reason} |"
            )
    lines.append("")

    # --- Recommendation ---
    lines.append("## Recommendation")
    lines.append("")

    # Find best hybrid for BTC
    if 'BTC' in all_results:
        btc_strats = all_results['BTC']
        best_strat = None
        best_mean = -999
        for sn, res in btc_strats.items():
            sharpes = [r['oos_sharpe'] for r in res]
            ms = np.mean(sharpes)
            if ms > best_mean:
                best_mean = ms
                best_strat = sn

        v3_sharpes = [r['oos_sharpe'] for r in btc_strats.get('V3_Baseline', [])]
        v3_mean = np.mean(v3_sharpes) if v3_sharpes else 0.0

        if best_strat and best_mean > v3_mean and best_strat != 'V3_Baseline':
            improvement = best_mean - v3_mean
            lines.append(f"**Best performer on BTC**: {best_strat} (mean Sharpe {best_mean:+.3f})")
            lines.append(f"**vs V3 Baseline**: {improvement:+.3f} Sharpe improvement")
            lines.append("")
            if improvement > 0.2:
                lines.append("The hybrid dip-buy entry provides a meaningful improvement over V3's "
                             "EMA-cross entry. The better entry timing captures the same trend direction "
                             "at more favorable prices.")
            elif improvement > 0:
                lines.append("The hybrid approach shows marginal improvement over V3. The dip-buy timing "
                             "helps somewhat, but the improvement may not survive real-world execution.")
            lines.append("")
        elif best_strat == 'V3_Baseline':
            lines.append("**V3 Baseline remains the best performer.** The hybrid dip-buy variants do not "
                         "improve on simple trend-following for this dataset.")
            lines.append("")
        else:
            lines.append(f"**Best performer**: {best_strat} (mean Sharpe {best_mean:+.3f})")
            lines.append("")

        # Multi-asset check
        lines.append("### Cross-Asset Robustness")
        lines.append("")
        for sn in btc_strats.keys():
            token_sharpes = {}
            for token in TOKENS:
                if token in all_results and sn in all_results[token]:
                    sharpes = [r['oos_sharpe'] for r in all_results[token][sn]]
                    token_sharpes[token] = np.mean(sharpes)
            if token_sharpes:
                positive_tokens = sum(1 for v in token_sharpes.values() if v > 0)
                lines.append(f"- {sn}: positive on {positive_tokens}/{len(token_sharpes)} tokens "
                             f"({', '.join(f'{t}={s:+.3f}' for t, s in token_sharpes.items())})")
        lines.append("")

    lines.append("---")
    lines.append(f"*Generated {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')} | "
                 f"Walk-forward: {N_WINDOWS} windows | Costs: {TOTAL_COST_PCT*100:.2f}% per trade*")

    return "\n".join(lines)


if __name__ == '__main__':
    main()
