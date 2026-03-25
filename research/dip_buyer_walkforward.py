"""
Deep Dip Buyer — Walk-Forward Validation
=========================================

Signal hypothesis (from ideal_trade_analysis.py hindsight study):
  ENTRY: RSI(14) < 35 AND z_score(20) < -1.5 AND drawdown_72h > 5% AND ADX(14) > 25
  EXIT:  RSI(14) > 75 OR z_score(20) > 2.0 OR stoch_K > 90 OR max_hold(720h)

Walk-forward protocol:
  6 non-overlapping windows: 6-month train + 3-month test
  Parameter sensitivity: 27 combos (RSI thresh x z-score thresh x exit RSI)
  Realistic costs: 0.22% round-trip + 5bps slippage each side
  Cross-asset: BTC, ETH, BNB, SOL
  Hybrid variant: EMA 20/50 trend filter on dip entries

CRITICAL: This is walk-forward validation of a HINDSIGHT signal.
We expect OOS degradation. The question is: does an edge survive?
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

# Force unbuffered output
def log(msg):
    print(msg)
    sys.stdout.flush()


# ============================================================
# CONFIGURATION
# ============================================================

DATA_DIR = '/workspace/crypto_backtest/data/spot/1h_cache'
TOKENS = ['BTC', 'ETH', 'BNB', 'SOL']

# Costs
FEE_RT_PCT = 0.22        # 0.22% round-trip (taker-taker)
SLIPPAGE_BPS = 5          # 5 bps per side for BTC
TOTAL_COST_PCT = FEE_RT_PCT / 100 + 2 * SLIPPAGE_BPS / 10000  # = 0.0032

# Walk-forward: 6 non-overlapping windows, 6mo train + 3mo test
N_WINDOWS = 6
TRAIN_DAYS = 180
TEST_DAYS = 90

# Parameter grids for sensitivity
RSI_ENTRY_GRID = [30, 35, 40]
ZSCORE_ENTRY_GRID = [-1.0, -1.5, -2.0]
RSI_EXIT_GRID = [70, 75, 80]

# Default (hypothesis) params
DEFAULT_PARAMS = {
    'rsi14_entry': 35,
    'rsi7_entry': 25,
    'zscore_entry': -1.5,
    'drawdown_thresh': -0.05,
    'adx_thresh': 25,
    'sr_position_thresh': 0.35,
    'consec_down_min': 2,
    'rsi14_exit': 75,
    'zscore_exit': 2.0,
    'stoch_k_exit': 90,
    'max_hold_hours': 720,
}


# ============================================================
# DATA LOADING
# ============================================================

def load_data(token: str) -> pd.DataFrame:
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
    """Average Directional Index."""
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


def compute_stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
                        k_period: int = 14, d_period: int = 3) -> Tuple[pd.Series, pd.Series]:
    """Stochastic oscillator K and D."""
    lowest = low.rolling(k_period).min()
    highest = high.rolling(k_period).max()
    stoch_k = 100 * (close - lowest) / (highest - lowest + 1e-10)
    stoch_d = stoch_k.rolling(d_period).mean()
    return stoch_k, stoch_d


def compute_bollinger_pct(close: pd.Series, period: int = 20, std_mult: float = 2.0) -> pd.Series:
    """Bollinger Band %B."""
    middle = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = middle + std_mult * std
    lower = middle - std_mult * std
    bb_pct = (close - lower) / (upper - lower + 1e-10)
    return bb_pct


def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all indicators needed for dip-buyer strategy."""
    ind = pd.DataFrame(index=df.index)
    c = df['close']
    h = df['high']
    l = df['low']

    # RSI
    ind['rsi_14'] = compute_rsi(c, 14)
    ind['rsi_7'] = compute_rsi(c, 7)

    # Z-score (20-period)
    rolling_mean = c.rolling(20).mean()
    rolling_std = c.rolling(20).std()
    ind['zscore_20'] = (c - rolling_mean) / (rolling_std + 1e-10)

    # ADX
    ind['adx_14'] = compute_adx(h, l, c, 14)

    # Stochastic
    ind['stoch_k'], ind['stoch_d'] = compute_stochastic(h, l, c, 14, 3)

    # Drawdown from 72h high
    rolling_72h_high = h.rolling(72).max()
    ind['drawdown_72h'] = (c - rolling_72h_high) / rolling_72h_high

    # S/R position: price position in 48h range (0=low, 1=high)
    rolling_48h_high = h.rolling(48).max()
    rolling_48h_low = l.rolling(48).min()
    ind['sr_position_48h'] = (c - rolling_48h_low) / (rolling_48h_high - rolling_48h_low + 1e-10)

    # Consecutive down bars
    down_bar = (c < c.shift(1)).astype(int)
    # Count consecutive down bars (reset on up bar)
    consec = np.zeros(len(down_bar))
    count = 0
    vals = down_bar.values
    for i in range(len(vals)):
        if vals[i] == 1:
            count += 1
        else:
            count = 0
        consec[i] = count
    ind['consec_down'] = consec

    # Bollinger Band %B
    ind['bb_pct'] = compute_bollinger_pct(c, 20, 2.0)

    # Trend: EMA 20/50 for hybrid
    ind['ema_20'] = c.ewm(span=20, adjust=False).mean()
    ind['ema_50'] = c.ewm(span=50, adjust=False).mean()
    ind['trend_bull'] = (ind['ema_20'] > ind['ema_50']).astype(int)

    # Close price (for PnL)
    ind['close'] = c.values

    return ind


# ============================================================
# TRADE SIMULATION
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


def simulate_dip_buyer(ind: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
                        params: Dict, cost_pct: float = TOTAL_COST_PCT,
                        trend_filter: bool = False) -> List[Trade]:
    """
    Simulate dip-buyer strategy on 1h bars.

    Entry: RSI(14) < rsi14_entry AND zscore(20) < zscore_entry
           AND drawdown_72h < drawdown_thresh AND ADX > adx_thresh
    Optional trend_filter: also require EMA 20 > EMA 50

    Exit: RSI(14) > rsi14_exit OR zscore(20) > zscore_exit
          OR stoch_K > stoch_k_exit OR bars_held > max_hold_hours
    """
    test = ind.loc[start:end].copy()
    if len(test) == 0:
        return []

    rsi14 = test['rsi_14'].values
    rsi7 = test['rsi_7'].values
    zscore = test['zscore_20'].values
    dd72 = test['drawdown_72h'].values
    adx = test['adx_14'].values
    sr_pos = test['sr_position_48h'].values
    consec = test['consec_down'].values
    stoch_k = test['stoch_k'].values
    close = test['close'].values
    trend = test['trend_bull'].values
    timestamps = test.index

    # Extract params
    rsi14_entry = params.get('rsi14_entry', 35)
    zscore_entry = params.get('zscore_entry', -1.5)
    dd_thresh = params.get('drawdown_thresh', -0.05)
    adx_thresh = params.get('adx_thresh', 25)
    rsi14_exit = params.get('rsi14_exit', 75)
    zscore_exit = params.get('zscore_exit', 2.0)
    stoch_exit = params.get('stoch_k_exit', 90)
    max_hold = params.get('max_hold_hours', 720)

    trades = []
    in_trade = False
    entry_price = 0.0
    entry_time = None
    bars_in_trade = 0

    for i in range(len(test)):
        if np.isnan(rsi14[i]) or np.isnan(zscore[i]) or np.isnan(adx[i]):
            continue

        if in_trade:
            bars_in_trade += 1
            exit_now = False
            exit_reason = ''

            if rsi14[i] > rsi14_exit:
                exit_now = True
                exit_reason = 'rsi_exit'
            elif zscore[i] > zscore_exit:
                exit_now = True
                exit_reason = 'zscore_exit'
            elif stoch_k[i] > stoch_exit:
                exit_now = True
                exit_reason = 'stoch_exit'
            elif bars_in_trade >= max_hold:
                exit_now = True
                exit_reason = 'max_hold'

            if exit_now:
                pnl = (close[i] / entry_price - 1) - cost_pct
                trades.append(Trade(
                    entry_time=entry_time,
                    entry_price=entry_price,
                    exit_time=timestamps[i],
                    exit_price=close[i],
                    pnl_pct=pnl,
                    bars_held=bars_in_trade,
                    exit_reason=exit_reason,
                ))
                in_trade = False

        if not in_trade:
            # ENTRY CONDITIONS
            entry_signal = (
                rsi14[i] < rsi14_entry
                and zscore[i] < zscore_entry
                and dd72[i] < dd_thresh
                and adx[i] > adx_thresh
            )
            if trend_filter:
                entry_signal = entry_signal and (trend[i] == 1)

            if entry_signal:
                in_trade = True
                entry_price = close[i]
                entry_time = timestamps[i]
                bars_in_trade = 0

    # Close open trade at end
    if in_trade:
        last_close = close[-1]
        pnl = (last_close / entry_price - 1) - cost_pct
        trades.append(Trade(
            entry_time=entry_time,
            entry_price=entry_price,
            exit_time=timestamps[-1],
            exit_price=last_close,
            pnl_pct=pnl,
            bars_held=bars_in_trade,
            exit_reason='period_end',
        ))

    return trades


# ============================================================
# METRICS
# ============================================================

@dataclass
class Metrics:
    total_pnl: float = 0.0
    sharpe: float = 0.0
    trade_count: int = 0
    win_rate: float = 0.0
    max_dd: float = 0.0
    avg_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    avg_bars_held: float = 0.0
    median_bars_held: float = 0.0


def compute_metrics(trades: List[Trade]) -> Metrics:
    m = Metrics()
    if not trades:
        return m

    pnls = np.array([t.pnl_pct for t in trades])
    bars = np.array([t.bars_held for t in trades])
    m.trade_count = len(trades)
    m.total_pnl = float(np.sum(pnls))
    m.avg_pnl = float(np.mean(pnls))
    m.win_rate = float(np.mean(pnls > 0))
    m.avg_bars_held = float(np.mean(bars))
    m.median_bars_held = float(np.median(bars))

    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    m.avg_win = float(np.mean(wins)) if len(wins) > 0 else 0.0
    m.avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0.0

    gp = float(np.sum(wins)) if len(wins) > 0 else 0.0
    gl = float(np.abs(np.sum(losses))) if len(losses) > 0 else 0.0
    m.profit_factor = gp / gl if gl > 0 else (999.0 if gp > 0 else 0.0)

    if len(pnls) > 1 and np.std(pnls) > 0:
        avg_hold_hrs = max(np.mean(bars), 1)
        trades_per_year = 8760.0 / avg_hold_hrs
        m.sharpe = float((np.mean(pnls) / np.std(pnls)) * np.sqrt(trades_per_year))
    else:
        m.sharpe = 0.0

    # Max drawdown on equity curve
    equity = np.cumprod(1 + pnls)
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    m.max_dd = float(np.abs(np.min(dd))) if len(dd) > 0 else 0.0

    return m


# ============================================================
# WALK-FORWARD WINDOWS
# ============================================================

def build_wf_windows(data_start: pd.Timestamp, data_end: pd.Timestamp) -> List[Dict]:
    """
    6 non-overlapping windows: 6-month train + 3-month test.
    Position windows so that the last test window ends near data_end.
    """
    total_per_window = TRAIN_DAYS + TEST_DAYS  # 270 days each
    total_span = N_WINDOWS * total_per_window
    # Want to cover as much recent data as possible
    # Allow 100 days warmup for indicators
    ideal_first = data_end - pd.Timedelta(days=total_span)
    min_first = data_start + pd.Timedelta(days=100)
    first_start = max(ideal_first, min_first)

    windows = []
    for i in range(N_WINDOWS):
        train_start = first_start + pd.Timedelta(days=i * total_per_window)
        train_end = train_start + pd.Timedelta(days=TRAIN_DAYS - 1)
        test_start = train_end + pd.Timedelta(days=1)
        test_end = test_start + pd.Timedelta(days=TEST_DAYS - 1)

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
# WALK-FORWARD ENGINE
# ============================================================

def run_single_wf(ind: pd.DataFrame, windows: List[Dict], params: Dict,
                   cost_pct: float, trend_filter: bool = False,
                   label: str = '') -> List[Dict]:
    """Run walk-forward on precomputed indicators with fixed params (no train optimization)."""
    results = []
    for w in windows:
        trades = simulate_dip_buyer(ind, w['test_start'], w['test_end'],
                                     params, cost_pct, trend_filter)
        m = compute_metrics(trades)

        # Exit reason breakdown
        exit_reasons = {}
        for t in trades:
            exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1

        results.append({
            'window': w['id'],
            'test_start': w['test_start'],
            'test_end': w['test_end'],
            'metrics': m,
            'trades': trades,
            'exit_reasons': exit_reasons,
        })

    return results


# ============================================================
# PARAMETER SENSITIVITY (27 COMBOS)
# ============================================================

def run_param_sensitivity(ind: pd.DataFrame, windows: List[Dict],
                           cost_pct: float, trend_filter: bool = False) -> pd.DataFrame:
    """Test all 27 parameter combinations across walk-forward windows."""
    rows = []
    for rsi_e, zs_e, rsi_x in product(RSI_ENTRY_GRID, ZSCORE_ENTRY_GRID, RSI_EXIT_GRID):
        params = DEFAULT_PARAMS.copy()
        params['rsi14_entry'] = rsi_e
        params['zscore_entry'] = zs_e
        params['rsi14_exit'] = rsi_x

        all_trades = []
        window_pnls = []
        window_sharpes = []
        for w in windows:
            trades = simulate_dip_buyer(ind, w['test_start'], w['test_end'],
                                         params, cost_pct, trend_filter)
            m = compute_metrics(trades)
            all_trades.extend(trades)
            window_pnls.append(m.total_pnl)
            window_sharpes.append(m.sharpe)

        agg = compute_metrics(all_trades)
        pos_windows = sum(1 for p in window_pnls if p > 0)

        rows.append({
            'rsi_entry': rsi_e,
            'zscore_entry': zs_e,
            'rsi_exit': rsi_x,
            'total_trades': agg.trade_count,
            'total_pnl': agg.total_pnl,
            'sharpe': agg.sharpe,
            'win_rate': agg.win_rate,
            'max_dd': agg.max_dd,
            'profit_factor': agg.profit_factor,
            'avg_hold_hrs': agg.avg_bars_held,
            'pos_windows': pos_windows,
            'mean_window_sharpe': float(np.mean(window_sharpes)) if window_sharpes else 0.0,
            'trend_filter': trend_filter,
        })

    return pd.DataFrame(rows)


# ============================================================
# CORRELATION ANALYSIS
# ============================================================

def compute_daily_returns_from_trades(trades: List[Trade], ind: pd.DataFrame) -> pd.Series:
    """Convert trades to daily return series for correlation analysis."""
    if not trades:
        return pd.Series(dtype=float)

    # Create hourly PnL attribution
    hourly_pnl = pd.Series(0.0, index=ind.index)
    for t in trades:
        if t.bars_held <= 0:
            continue
        mask = (ind.index >= t.entry_time) & (ind.index <= t.exit_time)
        n_bars = mask.sum()
        if n_bars > 0:
            pnl_per_bar = t.pnl_pct / n_bars
            hourly_pnl.loc[mask] = pnl_per_bar

    # Resample to daily
    daily_ret = hourly_pnl.resample('1D').sum()
    return daily_ret


def compute_strategy_correlation(trades_a: List[Trade], trades_b: List[Trade],
                                  ind: pd.DataFrame) -> float:
    """Compute correlation between two strategy return streams."""
    ret_a = compute_daily_returns_from_trades(trades_a, ind)
    ret_b = compute_daily_returns_from_trades(trades_b, ind)

    # Align
    common_idx = ret_a.index.intersection(ret_b.index)
    if len(common_idx) < 30:
        return np.nan
    return float(ret_a.loc[common_idx].corr(ret_b.loc[common_idx]))


# ============================================================
# SIMULATE V3-STYLE TREND STRATEGY FOR COMPARISON
# ============================================================

def simulate_v3_trend(ind: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
                       cost_pct: float) -> List[Trade]:
    """
    Simplified V3 trend strategy: EMA 20 > EMA 50 -> long.
    Exit when EMA 20 < EMA 50. Weekly rebalance (168h check).
    For correlation comparison.
    """
    test = ind.loc[start:end].copy()
    if len(test) < 168:
        return []

    close = test['close'].values
    trend = test['trend_bull'].values
    timestamps = test.index

    trades = []
    in_trade = False
    entry_price = 0.0
    entry_time = None
    bars_in_trade = 0

    for i in range(0, len(test), 1):  # Check every bar
        if np.isnan(close[i]):
            continue

        if in_trade:
            bars_in_trade += 1
            # Exit when trend flips or max hold
            if trend[i] == 0 or bars_in_trade >= 720:
                pnl = (close[i] / entry_price - 1) - cost_pct
                trades.append(Trade(
                    entry_time=entry_time,
                    entry_price=entry_price,
                    exit_time=timestamps[i],
                    exit_price=close[i],
                    pnl_pct=pnl,
                    bars_held=bars_in_trade,
                    exit_reason='trend_flip' if trend[i] == 0 else 'max_hold',
                ))
                in_trade = False

        if not in_trade:
            # Entry: weekly rebalance (every 168 bars approx)
            if i % 168 == 0 and trend[i] == 1:
                in_trade = True
                entry_price = close[i]
                entry_time = timestamps[i]
                bars_in_trade = 0

    if in_trade:
        pnl = (close[-1] / entry_price - 1) - cost_pct
        trades.append(Trade(
            entry_time=entry_time,
            entry_price=entry_price,
            exit_time=timestamps[-1],
            exit_price=close[-1],
            pnl_pct=pnl,
            bars_held=bars_in_trade,
            exit_reason='period_end',
        ))

    return trades


# ============================================================
# BUY-AND-HOLD BENCHMARK
# ============================================================

def compute_buyhold(ind: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
                     cost_pct: float) -> Metrics:
    """Simple buy-and-hold return for comparison."""
    test = ind.loc[start:end]
    if len(test) < 2:
        return Metrics()
    entry_price = test['close'].iloc[0]
    exit_price = test['close'].iloc[-1]
    pnl = (exit_price / entry_price - 1) - cost_pct
    bars = len(test)
    m = Metrics()
    m.total_pnl = pnl
    m.trade_count = 1
    m.win_rate = 1.0 if pnl > 0 else 0.0
    m.avg_pnl = pnl
    m.avg_bars_held = bars
    return m


# ============================================================
# MAIN ANALYSIS
# ============================================================

def main():
    t_start = time.time()

    log("=" * 78)
    log("DEEP DIP BUYER -- WALK-FORWARD VALIDATION")
    log("=" * 78)
    log(f"Tokens: {TOKENS}")
    log(f"Walk-forward: {N_WINDOWS} windows, {TRAIN_DAYS}d train / {TEST_DAYS}d test")
    log(f"Costs: {FEE_RT_PCT}% RT fee + {SLIPPAGE_BPS}bps slippage/side = {TOTAL_COST_PCT*100:.3f}% total")
    log("")

    # ================================================================
    # STEP 1: Load data and compute indicators
    # ================================================================
    log("STEP 1: Loading data and computing indicators...")
    all_data = {}
    all_ind = {}
    for token in TOKENS:
        df = load_data(token)
        if df is not None:
            log(f"  {token}: {len(df)} bars, {df.index[0].date()} to {df.index[-1].date()}")
            ind = compute_all_indicators(df)
            all_data[token] = df
            all_ind[token] = ind
        else:
            log(f"  {token}: SKIPPED (no data)")
    log("")

    # ================================================================
    # STEP 2: Build walk-forward windows (based on BTC data range)
    # ================================================================
    btc_ind = all_ind['BTC']
    windows = build_wf_windows(btc_ind.index[0], btc_ind.index[-1])
    log("STEP 2: Walk-forward windows:")
    for w in windows:
        log(f"  {w['id']}: train {w['train_start'].date()}-{w['train_end'].date()}, "
            f"test {w['test_start'].date()}-{w['test_end'].date()}")
    log("")

    # ================================================================
    # STEP 3: Walk-forward test -- DEFAULT PARAMS (BTC, no trend filter)
    # ================================================================
    log("=" * 78)
    log("STEP 3: WALK-FORWARD -- DEFAULT PARAMS (BTC)")
    log("=" * 78)
    log(f"Entry: RSI14<{DEFAULT_PARAMS['rsi14_entry']}, z-score<{DEFAULT_PARAMS['zscore_entry']}, "
        f"DD72h<{DEFAULT_PARAMS['drawdown_thresh']}, ADX>{DEFAULT_PARAMS['adx_thresh']}")
    log(f"Exit: RSI14>{DEFAULT_PARAMS['rsi14_exit']} OR z>{DEFAULT_PARAMS['zscore_exit']} "
        f"OR stochK>{DEFAULT_PARAMS['stoch_k_exit']} OR max_hold={DEFAULT_PARAMS['max_hold_hours']}h")
    log("")

    btc_wf = run_single_wf(btc_ind, windows, DEFAULT_PARAMS, TOTAL_COST_PCT, trend_filter=False)

    log(f"{'Window':<8} {'Period':<25} {'Trades':<8} {'PnL':>8} {'Sharpe':>8} {'WR':>7} "
        f"{'MaxDD':>8} {'PF':>7} {'AvgHold':>8} {'Exits':>30}")
    log("-" * 120)

    all_btc_trades = []
    btc_window_sharpes = []
    btc_window_pnls = []

    for wr in btc_wf:
        m = wr['metrics']
        period_str = f"{wr['test_start'].strftime('%Y-%m-%d')} to {wr['test_end'].strftime('%Y-%m-%d')}"
        exits_str = str(wr['exit_reasons']) if wr['exit_reasons'] else '-'
        log(f"{wr['window']:<8} {period_str:<25} {m.trade_count:<8} {m.total_pnl:>7.2%} "
            f"{m.sharpe:>8.2f} {m.win_rate:>6.1%} {m.max_dd:>7.2%} {m.profit_factor:>7.2f} "
            f"{m.avg_bars_held:>7.0f}h {exits_str:>30}")
        all_btc_trades.extend(wr['trades'])
        btc_window_sharpes.append(m.sharpe)
        btc_window_pnls.append(m.total_pnl)

    btc_agg = compute_metrics(all_btc_trades)
    pos_windows = sum(1 for p in btc_window_pnls if p > 0)
    log("")
    log(f"AGGREGATE: trades={btc_agg.trade_count}, PnL={btc_agg.total_pnl:.2%}, "
        f"Sharpe={btc_agg.sharpe:.2f}, WR={btc_agg.win_rate:.1%}, "
        f"MaxDD={btc_agg.max_dd:.2%}, PF={btc_agg.profit_factor:.2f}")
    log(f"  Mean window Sharpe: {np.mean(btc_window_sharpes):.2f}")
    log(f"  Positive windows: {pos_windows}/{len(windows)}")
    log(f"  Avg win: {btc_agg.avg_win:.2%}, Avg loss: {btc_agg.avg_loss:.2%}")
    log(f"  Median hold: {btc_agg.median_bars_held:.0f}h ({btc_agg.median_bars_held/24:.1f}d)")
    log("")

    # ================================================================
    # STEP 4: HYBRID (trend-filtered dip buyer)
    # ================================================================
    log("=" * 78)
    log("STEP 4: HYBRID -- TREND-FILTERED DIP BUYER (BTC)")
    log("  Only buy dips when EMA(20) > EMA(50) -- bullish trend")
    log("=" * 78)
    log("")

    btc_hybrid_wf = run_single_wf(btc_ind, windows, DEFAULT_PARAMS, TOTAL_COST_PCT, trend_filter=True)

    log(f"{'Window':<8} {'Period':<25} {'Trades':<8} {'PnL':>8} {'Sharpe':>8} {'WR':>7} "
        f"{'MaxDD':>8} {'PF':>7} {'AvgHold':>8}")
    log("-" * 100)

    all_hybrid_trades = []
    hybrid_window_sharpes = []
    hybrid_window_pnls = []

    for wr in btc_hybrid_wf:
        m = wr['metrics']
        period_str = f"{wr['test_start'].strftime('%Y-%m-%d')} to {wr['test_end'].strftime('%Y-%m-%d')}"
        log(f"{wr['window']:<8} {period_str:<25} {m.trade_count:<8} {m.total_pnl:>7.2%} "
            f"{m.sharpe:>8.2f} {m.win_rate:>6.1%} {m.max_dd:>7.2%} {m.profit_factor:>7.2f} "
            f"{m.avg_bars_held:>7.0f}h")
        all_hybrid_trades.extend(wr['trades'])
        hybrid_window_sharpes.append(m.sharpe)
        hybrid_window_pnls.append(m.total_pnl)

    hybrid_agg = compute_metrics(all_hybrid_trades)
    hybrid_pos = sum(1 for p in hybrid_window_pnls if p > 0)
    log("")
    log(f"HYBRID AGGREGATE: trades={hybrid_agg.trade_count}, PnL={hybrid_agg.total_pnl:.2%}, "
        f"Sharpe={hybrid_agg.sharpe:.2f}, WR={hybrid_agg.win_rate:.1%}, "
        f"MaxDD={hybrid_agg.max_dd:.2%}, PF={hybrid_agg.profit_factor:.2f}")
    log(f"  Mean window Sharpe: {np.mean(hybrid_window_sharpes):.2f}")
    log(f"  Positive windows: {hybrid_pos}/{len(windows)}")
    log("")

    # ================================================================
    # STEP 5: PARAMETER SENSITIVITY (27 combos) -- BTC
    # ================================================================
    log("=" * 78)
    log("STEP 5: PARAMETER SENSITIVITY (27 combos)")
    log("  RSI entry: {30, 35, 40}, z-score entry: {-1.0, -1.5, -2.0}, RSI exit: {70, 75, 80}")
    log("=" * 78)
    log("")

    sens_df = run_param_sensitivity(btc_ind, windows, TOTAL_COST_PCT, trend_filter=False)
    sens_hybrid_df = run_param_sensitivity(btc_ind, windows, TOTAL_COST_PCT, trend_filter=True)

    log("--- PURE DIP-BUYER (no trend filter) ---")
    log(f"{'RSI_E':>6} {'ZS_E':>6} {'RSI_X':>6} {'Trades':>7} {'PnL':>8} {'Sharpe':>8} "
        f"{'WR':>6} {'MaxDD':>8} {'PF':>7} {'PosW':>5}")
    log("-" * 80)
    for _, row in sens_df.sort_values('sharpe', ascending=False).iterrows():
        log(f"{row['rsi_entry']:>6.0f} {row['zscore_entry']:>6.1f} {row['rsi_exit']:>6.0f} "
            f"{row['total_trades']:>7.0f} {row['total_pnl']:>7.2%} {row['sharpe']:>8.2f} "
            f"{row['win_rate']:>5.1%} {row['max_dd']:>7.2%} {row['profit_factor']:>7.2f} "
            f"{row['pos_windows']:>5.0f}/{len(windows)}")
    log("")

    log("--- HYBRID DIP-BUYER (with trend filter) ---")
    log(f"{'RSI_E':>6} {'ZS_E':>6} {'RSI_X':>6} {'Trades':>7} {'PnL':>8} {'Sharpe':>8} "
        f"{'WR':>6} {'MaxDD':>8} {'PF':>7} {'PosW':>5}")
    log("-" * 80)
    for _, row in sens_hybrid_df.sort_values('sharpe', ascending=False).iterrows():
        log(f"{row['rsi_entry']:>6.0f} {row['zscore_entry']:>6.1f} {row['rsi_exit']:>6.0f} "
            f"{row['total_trades']:>7.0f} {row['total_pnl']:>7.2%} {row['sharpe']:>8.2f} "
            f"{row['win_rate']:>5.1%} {row['max_dd']:>7.2%} {row['profit_factor']:>7.2f} "
            f"{row['pos_windows']:>5.0f}/{len(windows)}")
    log("")

    # Robustness check: how many of 27 configs are profitable?
    profitable_pure = (sens_df['total_pnl'] > 0).sum()
    profitable_hybrid = (sens_hybrid_df['total_pnl'] > 0).sum()
    log(f"Parameter robustness (pure):   {profitable_pure}/27 configs profitable OOS")
    log(f"Parameter robustness (hybrid): {profitable_hybrid}/27 configs profitable OOS")
    log("")

    # ================================================================
    # STEP 6: CROSS-ASSET VALIDATION
    # ================================================================
    log("=" * 78)
    log("STEP 6: CROSS-ASSET VALIDATION")
    log("=" * 78)
    log("")

    cross_asset_results = {}
    for token in TOKENS:
        if token not in all_ind:
            continue
        ind = all_ind[token]

        # Use same windows (some tokens may have less data; skip if not enough)
        token_windows = []
        for w in windows:
            if w['test_start'] >= ind.index[0] and w['test_start'] <= ind.index[-1]:
                token_windows.append(w)

        if not token_windows:
            log(f"  {token}: No valid windows, SKIPPED")
            continue

        wf = run_single_wf(ind, token_windows, DEFAULT_PARAMS, TOTAL_COST_PCT, trend_filter=False)
        wf_hybrid = run_single_wf(ind, token_windows, DEFAULT_PARAMS, TOTAL_COST_PCT, trend_filter=True)

        all_trades_pure = [t for wr in wf for t in wr['trades']]
        all_trades_hybrid = [t for wr in wf_hybrid for t in wr['trades']]

        m_pure = compute_metrics(all_trades_pure)
        m_hybrid = compute_metrics(all_trades_hybrid)

        token_pure_pos = sum(1 for wr in wf if wr['metrics'].total_pnl > 0)
        token_hybrid_pos = sum(1 for wr in wf_hybrid if wr['metrics'].total_pnl > 0)

        cross_asset_results[token] = {
            'pure': m_pure,
            'hybrid': m_hybrid,
            'pure_pos_windows': token_pure_pos,
            'hybrid_pos_windows': token_hybrid_pos,
            'n_windows': len(token_windows),
            'wf_pure': wf,
            'wf_hybrid': wf_hybrid,
        }

        log(f"  {token} PURE:   trades={m_pure.trade_count:>4}, PnL={m_pure.total_pnl:>7.2%}, "
            f"Sharpe={m_pure.sharpe:>6.2f}, WR={m_pure.win_rate:>5.1%}, "
            f"DD={m_pure.max_dd:>6.2%}, PF={m_pure.profit_factor:>6.2f}, "
            f"pos_w={token_pure_pos}/{len(token_windows)}")
        log(f"  {token} HYBRID: trades={m_hybrid.trade_count:>4}, PnL={m_hybrid.total_pnl:>7.2%}, "
            f"Sharpe={m_hybrid.sharpe:>6.2f}, WR={m_hybrid.win_rate:>5.1%}, "
            f"DD={m_hybrid.max_dd:>6.2%}, PF={m_hybrid.profit_factor:>6.2f}, "
            f"pos_w={token_hybrid_pos}/{len(token_windows)}")
        log("")

    # Per-window detail for each asset
    for token in TOKENS:
        if token not in cross_asset_results:
            continue
        log(f"  --- {token} per-window detail (PURE) ---")
        log(f"  {'Window':<8} {'Period':<25} {'Trades':<7} {'PnL':>8} {'Sharpe':>8} {'WR':>6}")
        for wr in cross_asset_results[token]['wf_pure']:
            m = wr['metrics']
            ps = f"{wr['test_start'].strftime('%Y-%m-%d')} to {wr['test_end'].strftime('%Y-%m-%d')}"
            log(f"  {wr['window']:<8} {ps:<25} {m.trade_count:<7} {m.total_pnl:>7.2%} "
                f"{m.sharpe:>8.2f} {m.win_rate:>5.1%}")
        log("")

    # ================================================================
    # STEP 7: CORRELATION WITH V3 TREND STRATEGY
    # ================================================================
    log("=" * 78)
    log("STEP 7: CORRELATION WITH V3 TREND STRATEGY")
    log("=" * 78)
    log("")

    # Run V3-style trend strategy on same test windows
    v3_all_trades = []
    for w in windows:
        v3_trades = simulate_v3_trend(btc_ind, w['test_start'], w['test_end'], TOTAL_COST_PCT)
        v3_all_trades.extend(v3_trades)
    v3_metrics = compute_metrics(v3_all_trades)

    log(f"V3 trend (BTC): trades={v3_metrics.trade_count}, PnL={v3_metrics.total_pnl:.2%}, "
        f"Sharpe={v3_metrics.sharpe:.2f}")

    # Correlation
    corr_pure = compute_strategy_correlation(all_btc_trades, v3_all_trades, btc_ind)
    corr_hybrid = compute_strategy_correlation(all_hybrid_trades, v3_all_trades, btc_ind)
    log(f"Correlation(pure dip, V3 trend): {corr_pure:.3f}")
    log(f"Correlation(hybrid dip, V3 trend): {corr_hybrid:.3f}")
    log("")

    if abs(corr_pure) < 0.3:
        log("  -> Pure dip-buyer is LOW CORRELATION with V3 trend: ADDITIVE potential")
    elif abs(corr_pure) < 0.6:
        log("  -> Pure dip-buyer is MODERATE CORRELATION: some diversification benefit")
    else:
        log("  -> Pure dip-buyer is HIGH CORRELATION: largely REDUNDANT with V3 trend")
    log("")

    # ================================================================
    # STEP 8: COST SENSITIVITY
    # ================================================================
    log("=" * 78)
    log("STEP 8: COST SENSITIVITY")
    log("=" * 78)
    log("")

    cost_levels = [
        ('Zero cost', 0.0),
        ('Low (0.10%)', 0.0010),
        ('Medium (0.22%+5bps)', TOTAL_COST_PCT),
        ('High (0.30%+10bps)', 0.0030 + 0.0020),
        ('Very high (0.40%+15bps)', 0.0040 + 0.0030),
    ]

    log(f"{'Cost Level':<25} {'Trades':>7} {'PnL':>8} {'Sharpe':>8} {'WR':>6} {'PF':>7}")
    log("-" * 70)
    for label, cost in cost_levels:
        wf = run_single_wf(btc_ind, windows, DEFAULT_PARAMS, cost, trend_filter=False)
        all_t = [t for wr in wf for t in wr['trades']]
        m = compute_metrics(all_t)
        log(f"{label:<25} {m.trade_count:>7} {m.total_pnl:>7.2%} {m.sharpe:>8.2f} "
            f"{m.win_rate:>5.1%} {m.profit_factor:>7.2f}")
    log("")

    # Also for hybrid
    log("--- Hybrid cost sensitivity ---")
    log(f"{'Cost Level':<25} {'Trades':>7} {'PnL':>8} {'Sharpe':>8} {'WR':>6} {'PF':>7}")
    log("-" * 70)
    for label, cost in cost_levels:
        wf = run_single_wf(btc_ind, windows, DEFAULT_PARAMS, cost, trend_filter=True)
        all_t = [t for wr in wf for t in wr['trades']]
        m = compute_metrics(all_t)
        log(f"{label:<25} {m.trade_count:>7} {m.total_pnl:>7.2%} {m.sharpe:>8.2f} "
            f"{m.win_rate:>5.1%} {m.profit_factor:>7.2f}")
    log("")

    # ================================================================
    # STEP 9: ENTRY/EXIT SIGNAL CONTRIBUTION ANALYSIS
    # ================================================================
    log("=" * 78)
    log("STEP 9: ENTRY CONDITION CONTRIBUTION")
    log("  Testing each entry filter individually vs combined")
    log("=" * 78)
    log("")

    ablation_configs = {
        'Full signal':          DEFAULT_PARAMS.copy(),
        'RSI only':             {**DEFAULT_PARAMS, 'zscore_entry': 999, 'drawdown_thresh': 0, 'adx_thresh': 0},
        'Z-score only':         {**DEFAULT_PARAMS, 'rsi14_entry': 100, 'drawdown_thresh': 0, 'adx_thresh': 0},
        'Drawdown only':        {**DEFAULT_PARAMS, 'rsi14_entry': 100, 'zscore_entry': 999, 'adx_thresh': 0},
        'ADX only':             {**DEFAULT_PARAMS, 'rsi14_entry': 100, 'zscore_entry': 999, 'drawdown_thresh': 0},
        'RSI + Z-score':        {**DEFAULT_PARAMS, 'drawdown_thresh': 0, 'adx_thresh': 0},
        'RSI + Drawdown':       {**DEFAULT_PARAMS, 'zscore_entry': 999, 'adx_thresh': 0},
        'RSI + Z-score + DD':   {**DEFAULT_PARAMS, 'adx_thresh': 0},
    }

    log(f"{'Config':<25} {'Trades':>7} {'PnL':>8} {'Sharpe':>8} {'WR':>6} {'PF':>7} {'AvgHold':>8}")
    log("-" * 80)
    for label, params in ablation_configs.items():
        wf = run_single_wf(btc_ind, windows, params, TOTAL_COST_PCT, trend_filter=False)
        all_t = [t for wr in wf for t in wr['trades']]
        m = compute_metrics(all_t)
        log(f"{label:<25} {m.trade_count:>7} {m.total_pnl:>7.2%} {m.sharpe:>8.2f} "
            f"{m.win_rate:>5.1%} {m.profit_factor:>7.2f} {m.avg_bars_held:>7.0f}h")
    log("")

    # ================================================================
    # STEP 10: EXIT SIGNAL CONTRIBUTION
    # ================================================================
    log("=" * 78)
    log("STEP 10: EXIT SIGNAL CONTRIBUTION")
    log("=" * 78)
    log("")

    exit_configs = {
        'All exits':            DEFAULT_PARAMS.copy(),
        'RSI exit only':        {**DEFAULT_PARAMS, 'zscore_exit': 999, 'stoch_k_exit': 999},
        'Z-score exit only':    {**DEFAULT_PARAMS, 'rsi14_exit': 999, 'stoch_k_exit': 999},
        'Stoch exit only':      {**DEFAULT_PARAMS, 'rsi14_exit': 999, 'zscore_exit': 999},
        'Max hold only':        {**DEFAULT_PARAMS, 'rsi14_exit': 999, 'zscore_exit': 999, 'stoch_k_exit': 999},
        'RSI + Z-score exits':  {**DEFAULT_PARAMS, 'stoch_k_exit': 999},
    }

    log(f"{'Config':<25} {'Trades':>7} {'PnL':>8} {'Sharpe':>8} {'WR':>6} {'PF':>7} {'AvgHold':>8}")
    log("-" * 80)
    for label, params in exit_configs.items():
        wf = run_single_wf(btc_ind, windows, params, TOTAL_COST_PCT, trend_filter=False)
        all_t = [t for wr in wf for t in wr['trades']]
        m = compute_metrics(all_t)
        log(f"{label:<25} {m.trade_count:>7} {m.total_pnl:>7.2%} {m.sharpe:>8.2f} "
            f"{m.win_rate:>5.1%} {m.profit_factor:>7.2f} {m.avg_bars_held:>7.0f}h")
    log("")

    # ================================================================
    # FINAL VERDICT
    # ================================================================
    log("=" * 78)
    log("FINAL VERDICT")
    log("=" * 78)
    log("")

    # Kill criteria (following R99/R102 protocol)
    kills = []
    passes = []
    conditionals = []

    # 1. Positive OOS windows (need >= 4/6)
    if pos_windows >= 4:
        passes.append(f"PASS: {pos_windows}/{len(windows)} positive OOS windows (>=4 required)")
    else:
        kills.append(f"KILL: Only {pos_windows}/{len(windows)} positive OOS windows (>=4 required)")

    # 2. Mean OOS Sharpe
    mean_sharpe_btc = np.mean(btc_window_sharpes)
    if mean_sharpe_btc >= 0.3:
        passes.append(f"PASS: Mean OOS Sharpe = {mean_sharpe_btc:.2f} (>= 0.3)")
    elif mean_sharpe_btc >= 0.0:
        conditionals.append(f"CONDITIONAL: Mean OOS Sharpe = {mean_sharpe_btc:.2f} (positive but < 0.3)")
    else:
        kills.append(f"KILL: Mean OOS Sharpe = {mean_sharpe_btc:.2f} (negative)")

    # 3. Trade count
    if btc_agg.trade_count >= 15:
        passes.append(f"PASS: {btc_agg.trade_count} OOS trades (>= 15 for low-freq strategy)")
    elif btc_agg.trade_count >= 6:
        conditionals.append(f"CONDITIONAL: Only {btc_agg.trade_count} OOS trades (low sample)")
    else:
        kills.append(f"KILL: Only {btc_agg.trade_count} OOS trades (insufficient sample)")

    # 4. Profit factor
    if btc_agg.profit_factor >= 1.2:
        passes.append(f"PASS: Profit factor = {btc_agg.profit_factor:.2f} (>= 1.2)")
    elif btc_agg.profit_factor >= 1.0:
        conditionals.append(f"CONDITIONAL: Profit factor = {btc_agg.profit_factor:.2f} (barely profitable)")
    else:
        kills.append(f"KILL: Profit factor = {btc_agg.profit_factor:.2f} (< 1.0)")

    # 5. Cost survival
    wf_nocost = run_single_wf(btc_ind, windows, DEFAULT_PARAMS, 0.0, trend_filter=False)
    all_nocost = [t for wr in wf_nocost for t in wr['trades']]
    m_nocost = compute_metrics(all_nocost)
    if btc_agg.total_pnl > 0:
        passes.append(f"PASS: Edge survives realistic costs (PnL={btc_agg.total_pnl:.2%} after {TOTAL_COST_PCT*100:.3f}% RT)")
    elif m_nocost.total_pnl > 0:
        conditionals.append(f"CONDITIONAL: Edge exists pre-cost ({m_nocost.total_pnl:.2%}) but dies with costs ({btc_agg.total_pnl:.2%})")
    else:
        kills.append(f"KILL: No edge even pre-cost (PnL={m_nocost.total_pnl:.2%})")

    # 6. Parameter robustness
    if profitable_pure >= 18:
        passes.append(f"PASS: {profitable_pure}/27 param combos profitable (>= 67% robust)")
    elif profitable_pure >= 10:
        conditionals.append(f"CONDITIONAL: {profitable_pure}/27 param combos profitable (37-67%)")
    else:
        kills.append(f"KILL: Only {profitable_pure}/27 param combos profitable (< 37% -- fragile)")

    # 7. Cross-asset generalization
    generalizes = 0
    for token in ['ETH', 'BNB', 'SOL']:
        if token in cross_asset_results:
            if cross_asset_results[token]['pure'].total_pnl > 0:
                generalizes += 1
    if generalizes >= 2:
        passes.append(f"PASS: Generalizes to {generalizes}/3 alts")
    elif generalizes >= 1:
        conditionals.append(f"CONDITIONAL: Generalizes to only {generalizes}/3 alts")
    else:
        kills.append(f"KILL: Does not generalize to any alt ({generalizes}/3)")

    # 8. Correlation with existing strategies
    if not np.isnan(corr_pure) and abs(corr_pure) < 0.3:
        passes.append(f"PASS: Low correlation with V3 trend ({corr_pure:.3f}) -- ADDITIVE")
    elif not np.isnan(corr_pure) and abs(corr_pure) < 0.6:
        passes.append(f"PASS: Moderate correlation with V3 trend ({corr_pure:.3f}) -- some diversification")
    elif not np.isnan(corr_pure):
        conditionals.append(f"CONDITIONAL: High correlation with V3 trend ({corr_pure:.3f}) -- redundant")
    else:
        conditionals.append("CONDITIONAL: Cannot compute correlation (insufficient overlapping trades)")

    # Determine overall verdict
    if kills:
        verdict = 'KILL'
    elif conditionals:
        verdict = 'RECYCLE'
    else:
        verdict = 'PASS'

    log(f"BTC Pure Dip-Buyer: **{verdict}**")
    log("")
    for p in passes:
        log(f"  [+] {p}")
    for c in conditionals:
        log(f"  [~] {c}")
    for k in kills:
        log(f"  [-] {k}")
    log("")

    # Hybrid verdict
    hybrid_kills = []
    hybrid_passes = []
    hybrid_cond = []

    hybrid_mean_sharpe = np.mean(hybrid_window_sharpes)
    hybrid_pos_w = sum(1 for p in hybrid_window_pnls if p > 0)

    if hybrid_pos_w >= 4:
        hybrid_passes.append(f"PASS: {hybrid_pos_w}/{len(windows)} positive windows")
    else:
        hybrid_kills.append(f"KILL: Only {hybrid_pos_w}/{len(windows)} positive windows")

    if hybrid_mean_sharpe >= 0.3:
        hybrid_passes.append(f"PASS: Mean Sharpe = {hybrid_mean_sharpe:.2f}")
    elif hybrid_mean_sharpe >= 0.0:
        hybrid_cond.append(f"CONDITIONAL: Mean Sharpe = {hybrid_mean_sharpe:.2f} (positive but < 0.3)")
    else:
        hybrid_kills.append(f"KILL: Mean Sharpe = {hybrid_mean_sharpe:.2f} (negative)")

    if hybrid_agg.trade_count >= 6:
        hybrid_passes.append(f"PASS: {hybrid_agg.trade_count} trades")
    else:
        hybrid_kills.append(f"KILL: Only {hybrid_agg.trade_count} trades")

    if profitable_hybrid >= 18:
        hybrid_passes.append(f"PASS: {profitable_hybrid}/27 param combos profitable (robust)")
    elif profitable_hybrid >= 10:
        hybrid_cond.append(f"CONDITIONAL: {profitable_hybrid}/27 param combos profitable")
    else:
        hybrid_kills.append(f"KILL: Only {profitable_hybrid}/27 param combos profitable")

    if hybrid_kills:
        hybrid_verdict = 'KILL'
    elif hybrid_cond:
        hybrid_verdict = 'RECYCLE'
    else:
        hybrid_verdict = 'PASS'

    log(f"BTC Hybrid Dip-Buyer (trend-filtered): **{hybrid_verdict}**")
    log("")
    for p in hybrid_passes:
        log(f"  [+] {p}")
    for c in hybrid_cond:
        log(f"  [~] {c}")
    for k in hybrid_kills:
        log(f"  [-] {k}")
    log("")

    # ================================================================
    # SUMMARY TABLE
    # ================================================================
    log("=" * 78)
    log("SUMMARY TABLE")
    log("=" * 78)
    log("")
    log(f"{'Strategy':<30} {'Token':<6} {'Trades':>7} {'PnL':>8} {'Sharpe':>8} {'WR':>6} "
        f"{'MaxDD':>8} {'PF':>7} {'PosW':>5} {'Verdict':<10}")
    log("-" * 110)

    # BTC Pure
    log(f"{'Deep Dip (pure)':<30} {'BTC':<6} {btc_agg.trade_count:>7} {btc_agg.total_pnl:>7.2%} "
        f"{btc_agg.sharpe:>8.2f} {btc_agg.win_rate:>5.1%} {btc_agg.max_dd:>7.2%} "
        f"{btc_agg.profit_factor:>7.2f} {pos_windows:>3}/{len(windows)} {verdict:<10}")

    # BTC Hybrid
    log(f"{'Deep Dip (hybrid)':<30} {'BTC':<6} {hybrid_agg.trade_count:>7} {hybrid_agg.total_pnl:>7.2%} "
        f"{hybrid_agg.sharpe:>8.2f} {hybrid_agg.win_rate:>5.1%} {hybrid_agg.max_dd:>7.2%} "
        f"{hybrid_agg.profit_factor:>7.2f} {hybrid_pos:>3}/{len(windows)} {hybrid_verdict:<10}")

    # V3 Trend
    log(f"{'V3 Trend (benchmark)':<30} {'BTC':<6} {v3_metrics.trade_count:>7} {v3_metrics.total_pnl:>7.2%} "
        f"{v3_metrics.sharpe:>8.2f} {v3_metrics.win_rate:>5.1%} {v3_metrics.max_dd:>7.2%} "
        f"{v3_metrics.profit_factor:>7.2f} {'  -':>5} {'BENCHMARK':<10}")

    # Cross-asset
    for token in ['ETH', 'BNB', 'SOL']:
        if token in cross_asset_results:
            mp = cross_asset_results[token]['pure']
            pw = cross_asset_results[token]['pure_pos_windows']
            nw = cross_asset_results[token]['n_windows']
            log(f"{'Deep Dip (pure)':<30} {token:<6} {mp.trade_count:>7} {mp.total_pnl:>7.2%} "
                f"{mp.sharpe:>8.2f} {mp.win_rate:>5.1%} {mp.max_dd:>7.2%} "
                f"{mp.profit_factor:>7.2f} {pw:>3}/{nw} {'-':<10}")

    for token in ['ETH', 'BNB', 'SOL']:
        if token in cross_asset_results:
            mh = cross_asset_results[token]['hybrid']
            hw = cross_asset_results[token]['hybrid_pos_windows']
            nw = cross_asset_results[token]['n_windows']
            log(f"{'Deep Dip (hybrid)':<30} {token:<6} {mh.trade_count:>7} {mh.total_pnl:>7.2%} "
                f"{mh.sharpe:>8.2f} {mh.win_rate:>5.1%} {mh.max_dd:>7.2%} "
                f"{mh.profit_factor:>7.2f} {hw:>3}/{nw} {'-':<10}")

    log("")

    # ================================================================
    # RECOMMENDATIONS
    # ================================================================
    log("=" * 78)
    log("RECOMMENDATIONS")
    log("=" * 78)
    log("")

    if verdict == 'PASS':
        log("1. PURE DIP-BUYER: PASS -- proceed to production implementation")
        log("   - Implement as standalone strategy with 1x spot sizing")
        log("   - Consider as entry timing overlay for V3 trend base")
    elif verdict == 'RECYCLE':
        log("1. PURE DIP-BUYER: RECYCLE -- has promise but needs refinement")
        for c in conditionals:
            log(f"   - Fix: {c}")
        log("   - Consider relaxing entry filters for more trades")
        log("   - Consider using as TIMING OVERLAY on V3 instead of standalone")
    else:
        log("1. PURE DIP-BUYER: KILL -- does not survive walk-forward validation")
        for k in kills:
            log(f"   - {k}")
        log("   - The hindsight analysis was curve-fitted to past extremes")

    if hybrid_verdict == 'PASS':
        log("2. HYBRID DIP-BUYER: PASS -- trend filter improves robustness")
        log("   - This is the recommended variant for implementation")
    elif hybrid_verdict == 'RECYCLE':
        log("2. HYBRID DIP-BUYER: RECYCLE -- trend filter helps but not enough")
    else:
        log("2. HYBRID DIP-BUYER: KILL")

    if not np.isnan(corr_pure) and abs(corr_pure) < 0.4:
        log("3. DIVERSIFICATION: Signal is additive to V3 trend -- portfolio benefit likely")
    else:
        log("3. DIVERSIFICATION: Signal overlaps significantly with V3 trend -- limited portfolio benefit")

    log("")
    elapsed = time.time() - t_start
    log(f"Total runtime: {elapsed:.1f}s")
    log("=" * 78)


if __name__ == '__main__':
    main()
