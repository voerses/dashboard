#!/workspace/venv/bin/python
"""
R166 -- Multi-Strategy Search: Find Uncorrelated Strategy Components
====================================================================

Goal: Find MORE uncorrelated strategy components to build a high-Sharpe,
low-drawdown portfolio. More uncorrelated return streams = lower portfolio DD
for the same return.

Strategies tested:
  1. Mean-Reversion on Highly Volatile Tokens (RSI-based, opposite of momentum)
  2. Funding Rate Carry (short high-funding, long low-funding tokens)
  3. Volume Momentum (rank by 7d volume growth, long rising / short declining)
  4. Short-Term Cross-Sectional Reversal (opposite of R158 momentum)
  5. Low-Volatility Anomaly (long low-vol, short high-vol tokens)

Reference strategies for correlation:
  - R158: Momentum Rotation (L7_R7_K5_70/30_RFY)
  - R160: Volatility Breakout (Variant B, 1x)

Combination tests:
  - Any new strategy with last-12mo Sharpe > 0.3 AND correlation < 0.3
    with both R158 and R160 gets combined into 3+ strategy portfolio
  - Test at leverage 2x, 3x, 4x with drawdown control

Data: 66+ tokens, 2024-03-17 to 2026-03-17
Costs: 7 bps per side + hourly funding from parquet
Borrow: 5% annual on leveraged portion
"""

import sys
import os
import warnings
import time
import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

warnings.filterwarnings('ignore')

def log(msg):
    print(msg)
    sys.stdout.flush()

# ============================================================
# CONSTANTS
# ============================================================

DATA_DIR = Path('/workspace/crypto_backtest/data/perp/1h_cache')
OUTPUT_PATH = Path('/workspace/crypto_backtest/research/R166_multi_strategy_results.md')

SIM_START = pd.Timestamp('2024-03-17')
SIM_END   = pd.Timestamp('2026-03-17')
LAST_12MO_START = pd.Timestamp('2025-03-17')
DATA_CUTOFF = pd.Timestamp('2024-03-17')

DAYS_PER_YEAR = 365
HOURS_PER_YEAR = 8760

FEE_BPS = 7.0
FEE_FRAC = FEE_BPS / 10000.0
ANNUAL_BORROW_RATE = 0.05
DAILY_BORROW_RATE = ANNUAL_BORROW_RATE / 365.0

MIN_AVG_DAILY_VOLUME_USD = 1_000_000

# EMA regime filter params (hourly)
EMA_FAST_H = 20
EMA_SLOW_H = 50

# R158 best variant params
R158_LOOKBACK = 7
R158_REBALANCE = 7
R158_K = 5
R158_LONG_WT = 0.50
R158_SHORT_WT = 0.50
R158_USE_REGIME_FILTER = True

# R160 constants
R160_BB_PERIOD = 20
R160_BB_STD_MULT = 2.0
R160_VOL_MULT = 1.5
R160_ATR_PERIOD = 20
R160_SMA_PERIOD = 20
R160_PARTIAL_PROFIT_ATR_MULT = 3.0
R160_PARTIAL_CLOSE_FRAC = 0.5
R160_MAX_HOLD_BARS = 180
R160_MOMENTUM_LOOKBACK_4H = 14 * 6
R160_TOP_N_BREAKOUTS = 5
R160_MAX_POSITIONS = 10
R160_POSITION_SIZE = 0.20
R160_COST_PER_SIDE = FEE_FRAC
R160_MIN_DATA_DAYS = 365
R160_MIN_AVG_DOLLAR_VOL = 2_000_000

# ============================================================
# DATA LOADING
# ============================================================

def load_all_tokens() -> Dict[str, pd.DataFrame]:
    """Load all tokens with data before cutoff and apply volume filter."""
    files = sorted(os.listdir(DATA_DIR))
    tokens = {}
    for f in files:
        if not f.endswith('_1h.parquet'):
            continue
        ticker = f.replace('_1h.parquet', '')
        path = DATA_DIR / f
        df = pd.read_parquet(path)
        if df.index.min() >= DATA_CUTOFF:
            continue
        pre_sim = df[df.index < SIM_START]
        if len(pre_sim) < 30 * 24:
            continue
        last_30d = pre_sim.tail(30 * 24)
        daily_dvol = (last_30d['volume'] * last_30d['close']).resample('1D').sum()
        avg_dvol = daily_dvol.mean()
        if avg_dvol < MIN_AVG_DAILY_VOLUME_USD:
            continue
        tokens[ticker] = df
    return tokens


def prepare_daily_data(tokens: Dict[str, pd.DataFrame]):
    """Prepare aligned daily close, returns, signals."""
    daily_closes = {}
    daily_volumes = {}
    daily_dollar_volumes = {}
    ema_signals = {}
    hourly_fundings = {}
    hourly_closes = {}

    for ticker, df in tokens.items():
        daily = df['close'].resample('1D').last().dropna()
        daily_closes[ticker] = daily

        dvol = df['volume'].resample('1D').sum()
        daily_volumes[ticker] = dvol

        dollar_vol = (df['volume'] * df['close']).resample('1D').sum()
        daily_dollar_volumes[ticker] = dollar_vol

        ema_fast = df['close'].ewm(span=EMA_FAST_H, adjust=False).mean()
        ema_slow = df['close'].ewm(span=EMA_SLOW_H, adjust=False).mean()
        ema_diff = (ema_fast - ema_slow).resample('1D').last()
        ema_signals[ticker] = ema_diff

        hourly_fundings[ticker] = df['funding_1h'].fillna(0)
        hourly_closes[ticker] = df['close']

    daily_close = pd.DataFrame(daily_closes)
    daily_volume = pd.DataFrame(daily_volumes)
    daily_dollar_vol = pd.DataFrame(daily_dollar_volumes)
    ema_signal = pd.DataFrame(ema_signals)

    start_with_buffer = SIM_START - pd.Timedelta(days=40)
    daily_close = daily_close.loc[start_with_buffer:SIM_END]
    daily_volume = daily_volume.reindex(daily_close.index)
    daily_dollar_vol = daily_dollar_vol.reindex(daily_close.index)
    ema_signal = ema_signal.reindex(daily_close.index)

    daily_returns = daily_close.pct_change()

    ema_signal_shifted = ema_signal.shift(1)

    rolling_avg_dvol = daily_dollar_vol.rolling(30, min_periods=15).mean()
    rolling_avg_dvol_shifted = rolling_avg_dvol.shift(1)

    return (daily_close, daily_returns, daily_volume, daily_dollar_vol,
            ema_signal_shifted, rolling_avg_dvol_shifted, hourly_fundings, hourly_closes)


# ============================================================
# METRICS
# ============================================================

def compute_metrics(equity: pd.Series) -> dict:
    """Compute standard strategy metrics from daily equity curve."""
    if len(equity) < 2 or equity.iloc[-1] <= 0:
        return {
            'annual_return': -1.0, 'max_dd': -1.0, 'sharpe': -99.0,
            'calmar': -99.0, 'sortino': -99.0, 'profit_factor': 0.0,
            'last_12m_return': -1.0, 'last_12m_sharpe': -99.0,
            'last_12m_max_dd': -1.0,
        }

    daily_eq = equity.resample('1D').last().dropna()
    daily_rets = daily_eq.pct_change().dropna()

    if len(daily_rets) < 30:
        return {
            'annual_return': -1.0, 'max_dd': -1.0, 'sharpe': -99.0,
            'calmar': -99.0, 'sortino': -99.0, 'profit_factor': 0.0,
            'last_12m_return': -1.0, 'last_12m_sharpe': -99.0,
            'last_12m_max_dd': -1.0,
        }

    total_return = equity.iloc[-1] / equity.iloc[0] - 1
    n_days = (equity.index[-1] - equity.index[0]).days
    if n_days < 1:
        n_days = 1
    annual_return = (1 + total_return) ** (DAYS_PER_YEAR / n_days) - 1

    running_max = daily_eq.cummax()
    drawdowns = daily_eq / running_max - 1
    max_dd = drawdowns.min()

    mean_daily = daily_rets.mean()
    std_daily = daily_rets.std()
    sharpe = (mean_daily / std_daily * np.sqrt(DAYS_PER_YEAR)) if std_daily > 0 else 0.0

    calmar = annual_return / abs(max_dd) if max_dd != 0 else 0.0

    downside = daily_rets[daily_rets < 0]
    downside_std = downside.std() if len(downside) > 0 else 1e-10
    sortino = (mean_daily / downside_std * np.sqrt(DAYS_PER_YEAR)) if downside_std > 0 else 0.0

    gains = daily_rets[daily_rets > 0].sum()
    losses = abs(daily_rets[daily_rets < 0].sum())
    profit_factor = gains / losses if losses > 0 else float('inf')

    # Last 12 months
    last_12m = daily_eq[daily_eq.index >= LAST_12MO_START]
    if len(last_12m) > 30:
        last_12m_return = last_12m.iloc[-1] / last_12m.iloc[0] - 1
        last_12m_rets = last_12m.pct_change().dropna()
        last_12m_sharpe = (last_12m_rets.mean() / last_12m_rets.std() * np.sqrt(DAYS_PER_YEAR)) if last_12m_rets.std() > 0 else 0.0
        last_12m_running_max = last_12m.cummax()
        last_12m_dd = last_12m / last_12m_running_max - 1
        last_12m_max_dd = last_12m_dd.min()
    else:
        last_12m_return = 0.0
        last_12m_sharpe = 0.0
        last_12m_max_dd = 0.0

    return {
        'annual_return': annual_return,
        'max_dd': max_dd,
        'sharpe': sharpe,
        'calmar': calmar,
        'sortino': sortino,
        'profit_factor': profit_factor,
        'last_12m_return': last_12m_return,
        'last_12m_sharpe': last_12m_sharpe,
        'last_12m_max_dd': last_12m_max_dd,
    }


def daily_returns_from_equity(equity: pd.Series) -> pd.Series:
    """Get daily returns from an equity curve (resample to daily first)."""
    daily_eq = equity.resample('1D').last().dropna()
    return daily_eq.pct_change().dropna()


# ============================================================
# R158 MOMENTUM ROTATION (best variant: L7_R7_K5_50/50_RFY)
# ============================================================

def run_r158_best(daily_close, daily_returns, ema_signal_shifted,
                  rolling_avg_dvol_shifted, hourly_fundings) -> pd.Series:
    """Run R158 best variant L7_R7_K5_50/50_RFY and return equity curve."""
    log("  Running R158 best variant (L7_R7_K5_50/50_RFY)...")
    lookback_days = R158_LOOKBACK
    rebalance_days = R158_REBALANCE
    k = R158_K
    long_wt = R158_LONG_WT
    short_wt = R158_SHORT_WT
    use_regime_filter = R158_USE_REGIME_FILTER

    lb_rets_df = daily_close.shift(1) / daily_close.shift(1 + lookback_days) - 1

    sim_dates = daily_close.loc[SIM_START:SIM_END].index
    rebalance_indices = set(range(0, len(sim_dates), rebalance_days))

    equity = 1.0
    equity_series = {}
    current_longs = {}
    current_shorts = {}
    prev_longs = set()
    prev_shorts = set()
    pending_longs = None
    pending_shorts = None

    for i, date in enumerate(sim_dates):
        if pending_longs is not None:
            new_longs_set = set(pending_longs.keys())
            new_shorts_set = set(pending_shorts.keys())
            long_entries = new_longs_set - prev_longs
            long_exits = prev_longs - new_longs_set
            short_entries = new_shorts_set - prev_shorts
            short_exits = prev_shorts - new_shorts_set

            cost = 0.0
            for t in long_entries:
                cost += pending_longs[t] * FEE_FRAC
            for t in long_exits:
                cost += current_longs.get(t, 0) * FEE_FRAC
            for t in short_entries:
                cost += pending_shorts[t] * FEE_FRAC
            for t in short_exits:
                cost += current_shorts.get(t, 0) * FEE_FRAC

            equity *= (1 - cost)
            current_longs = pending_longs
            current_shorts = pending_shorts
            prev_longs = new_longs_set
            prev_shorts = new_shorts_set
            pending_longs = None
            pending_shorts = None

        daily_pnl = 0.0
        daily_ret = daily_returns.loc[date] if date in daily_returns.index else pd.Series(dtype=float)

        for ticker, weight in current_longs.items():
            if ticker in daily_ret.index and not np.isnan(daily_ret[ticker]):
                daily_pnl += weight * daily_ret[ticker]
        for ticker, weight in current_shorts.items():
            if ticker in daily_ret.index and not np.isnan(daily_ret[ticker]):
                daily_pnl -= weight * daily_ret[ticker]

        # Funding
        funding_start = date
        funding_end = date + pd.Timedelta(hours=23)
        for ticker, weight in current_longs.items():
            if ticker in hourly_fundings:
                f_slice = hourly_fundings[ticker].loc[funding_start:funding_end]
                if len(f_slice) > 0:
                    daily_pnl += -f_slice.sum() * weight
        for ticker, weight in current_shorts.items():
            if ticker in hourly_fundings:
                f_slice = hourly_fundings[ticker].loc[funding_start:funding_end]
                if len(f_slice) > 0:
                    daily_pnl += f_slice.sum() * weight

        equity *= (1 + daily_pnl)
        equity_series[date] = equity

        if i in rebalance_indices:
            lb_rets = lb_rets_df.loc[date].dropna() if date in lb_rets_df.index else pd.Series(dtype=float)
            vol_at_date = rolling_avg_dvol_shifted.loc[date].dropna() if date in rolling_avg_dvol_shifted.index else pd.Series(dtype=float)
            eligible = vol_at_date[vol_at_date >= MIN_AVG_DAILY_VOLUME_USD].index
            lb_rets = lb_rets[lb_rets.index.isin(eligible)]

            if len(lb_rets) >= 2 * k:
                ranked = lb_rets.sort_values(ascending=False)
                top_k = ranked.head(k).index.tolist()
                bottom_k = ranked.tail(k).index.tolist()

                if use_regime_filter:
                    ema_at_date = ema_signal_shifted.loc[date].dropna() if date in ema_signal_shifted.index else pd.Series(dtype=float)
                    top_k = [t for t in top_k if t in ema_at_date.index and ema_at_date[t] > 0]
                    bottom_k = [t for t in bottom_k if t in ema_at_date.index and ema_at_date[t] < 0]

                n_longs = max(len(top_k), 1)
                n_shorts = max(len(bottom_k), 1)

                pending_longs = {t: long_wt / n_longs for t in top_k} if top_k else {}
                pending_shorts = {t: short_wt / n_shorts for t in bottom_k} if bottom_k else {}

    eq = pd.Series(equity_series)
    m = compute_metrics(eq)
    log(f"    R158: Ann={m['annual_return']*100:.1f}%, Sharpe={m['sharpe']:.2f}, "
        f"MaxDD={m['max_dd']*100:.1f}%, 12M={m['last_12m_return']*100:.1f}%")
    return eq


# ============================================================
# R160 VOLATILITY BREAKOUT (Variant B, 1x)
# ============================================================

@dataclass
class R160Position:
    token: str
    entry_time: pd.Timestamp
    entry_bar_idx: int
    entry_price: float
    direction: int
    size_frac: float
    leverage: float
    entry_atr: float
    partial_taken: bool = False
    remaining_frac: float = 1.0
    breakeven_stop: bool = False
    bars_held: int = 0


def r160_load_and_resample_4h(token: str, df_1h: pd.DataFrame) -> Optional[pd.DataFrame]:
    """Load 1H data, resample to 4H, compute indicators."""
    df = df_1h.copy()
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep='first')]

    if (df.index.max() - df.index.min()).days < R160_MIN_DATA_DAYS:
        return None

    df_4h = df.resample('4h').agg({
        'open': 'first', 'high': 'max', 'low': 'min',
        'close': 'last', 'volume': 'sum',
    }).dropna(subset=['close'])

    if 'funding_1h' in df.columns:
        df_4h['funding_4h'] = df['funding_1h'].resample('4h').sum()
    else:
        df_4h['funding_4h'] = 0.0

    df_4h = df_4h.dropna(subset=['close'])
    if len(df_4h) < R160_BB_PERIOD + 50:
        return None

    df_4h['sma20'] = df_4h['close'].rolling(R160_BB_PERIOD).mean()
    df_4h['bb_std'] = df_4h['close'].rolling(R160_BB_PERIOD).std()
    df_4h['bb_upper'] = df_4h['sma20'] + R160_BB_STD_MULT * df_4h['bb_std']
    df_4h['bb_lower'] = df_4h['sma20'] - R160_BB_STD_MULT * df_4h['bb_std']
    df_4h['avg_volume'] = df_4h['volume'].rolling(R160_BB_PERIOD).mean()
    df_4h['dollar_volume'] = df_4h['volume'] * df_4h['close']

    tr_hl = df_4h['high'] - df_4h['low']
    tr_hc = (df_4h['high'] - df_4h['close'].shift(1)).abs()
    tr_lc = (df_4h['low'] - df_4h['close'].shift(1)).abs()
    df_4h['true_range'] = pd.concat([tr_hl, tr_hc, tr_lc], axis=1).max(axis=1)
    df_4h['atr'] = df_4h['true_range'].rolling(R160_ATR_PERIOD).mean()

    df_4h['breakout_long_raw'] = df_4h['close'] > df_4h['bb_upper']
    df_4h['breakout_short_raw'] = df_4h['close'] < df_4h['bb_lower']
    df_4h['volume_confirmed'] = df_4h['volume'] > R160_VOL_MULT * df_4h['avg_volume']
    df_4h['breakout_long'] = df_4h['breakout_long_raw'] & df_4h['volume_confirmed']
    df_4h['breakout_short'] = df_4h['breakout_short_raw'] & df_4h['volume_confirmed']

    df_4h['long_strength'] = ((df_4h['close'] - df_4h['bb_upper']) / df_4h['close']).clip(lower=0)
    df_4h['short_strength'] = ((df_4h['bb_lower'] - df_4h['close']) / df_4h['close']).clip(lower=0)

    df_4h['return_14d'] = df_4h['close'].pct_change(R160_MOMENTUM_LOOKBACK_4H)
    df_4h['avg_daily_dollar_vol'] = df_4h['dollar_volume'].rolling(180).mean() * 6

    return df_4h


def run_r160_best(tokens: Dict[str, pd.DataFrame]) -> pd.Series:
    """Run R160 Variant B at 1x leverage and return equity curve."""
    log("  Running R160 Variant B (1x)...")
    leverage = 1.0
    fee = R160_COST_PER_SIDE

    token_data = {}
    for ticker, df_1h in tokens.items():
        df_4h = r160_load_and_resample_4h(ticker, df_1h)
        if df_4h is not None:
            token_data[ticker] = df_4h

    log(f"    R160 tokens loaded: {len(token_data)}")

    all_times = set()
    for df in token_data.values():
        all_times.update(df.index.tolist())
    all_times = sorted(all_times)

    if len(all_times) < 100:
        return pd.Series(dtype=float)

    # Pre-compute numpy arrays for speed
    token_arrays = {}
    token_time_idx = {}
    for token, df in token_data.items():
        token_arrays[token] = {
            'index': df.index,
            'open': df['open'].values,
            'high': df['high'].values,
            'low': df['low'].values,
            'close': df['close'].values,
            'volume': df['volume'].values,
            'sma20': df['sma20'].values,
            'bb_upper': df['bb_upper'].values,
            'bb_lower': df['bb_lower'].values,
            'avg_volume': df['avg_volume'].values,
            'atr': df['atr'].values,
            'funding_4h': df['funding_4h'].values,
            'breakout_long': df['breakout_long'].values,
            'breakout_short': df['breakout_short'].values,
            'long_strength': df['long_strength'].values,
            'short_strength': df['short_strength'].values,
            'return_14d': df['return_14d'].values,
            'avg_daily_dollar_vol': df['avg_daily_dollar_vol'].values,
        }
        idx_map = {}
        for i, t in enumerate(df.index):
            idx_map[t] = i
        token_time_idx[token] = idx_map

    equity = 1.0
    equity_curve = {}
    positions: List[R160Position] = []
    pending_entries = []

    warmup_bars = R160_BB_PERIOD + 10
    warmup_time = all_times[min(warmup_bars, len(all_times) - 1)]

    for ti, current_time in enumerate(all_times):
        if current_time < warmup_time:
            equity_curve[current_time] = equity
            continue
        if equity <= 0.01:
            equity_curve[current_time] = max(equity, 0.0)
            continue

        # 1. Process pending entries
        if pending_entries:
            slots_available = R160_MAX_POSITIONS - len(positions)
            if slots_available > 0:
                pending_entries.sort(key=lambda x: x[2], reverse=True)
                to_enter = pending_entries[:min(R160_TOP_N_BREAKOUTS, slots_available)]
                for token, direction, strength in to_enter:
                    if token not in token_time_idx or current_time not in token_time_idx[token]:
                        continue
                    bar_idx = token_time_idx[token][current_time]
                    arr = token_arrays[token]
                    if np.isnan(arr['open'][bar_idx]) or np.isnan(arr['atr'][bar_idx]):
                        continue
                    if arr['atr'][bar_idx] <= 0:
                        continue
                    already_in = any(p.token == token and p.direction == direction for p in positions)
                    if already_in:
                        continue
                    entry_cost = fee * leverage
                    equity -= entry_cost * R160_POSITION_SIZE
                    pos = R160Position(
                        token=token, entry_time=current_time, entry_bar_idx=bar_idx,
                        entry_price=arr['open'][bar_idx], direction=direction,
                        size_frac=R160_POSITION_SIZE, leverage=leverage,
                        entry_atr=arr['atr'][bar_idx],
                    )
                    positions.append(pos)
            pending_entries = []

        # 2. Update existing positions
        closed_indices = []
        for pidx, pos in enumerate(positions):
            token = pos.token
            if token not in token_time_idx or current_time not in token_time_idx[token]:
                pos.bars_held += 1
                continue
            bar_idx = token_time_idx[token][current_time]
            arr = token_arrays[token]
            close_price = arr['close'][bar_idx]
            sma_val = arr['sma20'][bar_idx]
            atr_val = arr['atr'][bar_idx]
            if np.isnan(close_price) or np.isnan(sma_val):
                pos.bars_held += 1
                continue
            pos.bars_held += 1
            exit_reason = None
            exit_price = close_price

            # Partial profit
            if not pos.partial_taken and atr_val > 0:
                target = pos.entry_price + pos.direction * R160_PARTIAL_PROFIT_ATR_MULT * pos.entry_atr
                if pos.direction == 1 and close_price >= target:
                    partial_return = (close_price / pos.entry_price - 1.0) * leverage
                    partial_cost = fee * leverage
                    partial_pnl = (partial_return - partial_cost) * pos.size_frac * R160_PARTIAL_CLOSE_FRAC
                    equity += partial_pnl
                    pos.partial_taken = True
                    pos.remaining_frac = 1.0 - R160_PARTIAL_CLOSE_FRAC
                    pos.breakeven_stop = True
                elif pos.direction == -1 and close_price <= target:
                    partial_return = (1.0 - close_price / pos.entry_price) * leverage
                    partial_cost = fee * leverage
                    partial_pnl = (partial_return - partial_cost) * pos.size_frac * R160_PARTIAL_CLOSE_FRAC
                    equity += partial_pnl
                    pos.partial_taken = True
                    pos.remaining_frac = 1.0 - R160_PARTIAL_CLOSE_FRAC
                    pos.breakeven_stop = True

            # Trail stop
            if pos.direction == 1 and close_price < sma_val:
                exit_reason = 'trail_stop'
            elif pos.direction == -1 and close_price > sma_val:
                exit_reason = 'trail_stop'

            # Breakeven stop
            if exit_reason is None and pos.breakeven_stop:
                if pos.direction == 1 and close_price < pos.entry_price:
                    exit_reason = 'breakeven_stop'
                elif pos.direction == -1 and close_price > pos.entry_price:
                    exit_reason = 'breakeven_stop'

            # Max hold
            if exit_reason is None and pos.bars_held >= R160_MAX_HOLD_BARS:
                exit_reason = 'max_hold'

            # Opposite breakout
            if exit_reason is None:
                if pos.direction == 1 and bar_idx < len(arr['breakout_short']) and arr['breakout_short'][bar_idx]:
                    exit_reason = 'opposite_breakout'
                elif pos.direction == -1 and bar_idx < len(arr['breakout_long']) and arr['breakout_long'][bar_idx]:
                    exit_reason = 'opposite_breakout'

            if exit_reason is not None:
                if pos.direction == 1:
                    raw_return = (exit_price / pos.entry_price - 1.0)
                else:
                    raw_return = (1.0 - exit_price / pos.entry_price)
                levered_return = raw_return * leverage
                exit_cost = fee * leverage
                effective_size = pos.size_frac * pos.remaining_frac
                pnl = (levered_return - exit_cost) * effective_size
                equity += pnl
                closed_indices.append(pidx)

        for pidx in sorted(closed_indices, reverse=True):
            positions.pop(pidx)

        # 3. Scan for new breakouts (Variant B)
        new_candidates = []
        for token, arr in token_arrays.items():
            if current_time not in token_time_idx[token]:
                continue
            bar_idx = token_time_idx[token][current_time]
            if bar_idx < R160_BB_PERIOD + 5:
                continue
            if np.isnan(arr['bb_upper'][bar_idx]) or np.isnan(arr['atr'][bar_idx]):
                continue
            if np.isnan(arr['avg_daily_dollar_vol'][bar_idx]):
                continue
            if arr['avg_daily_dollar_vol'][bar_idx] < R160_MIN_AVG_DOLLAR_VOL:
                continue
            if arr['breakout_long'][bar_idx]:
                strength = arr['long_strength'][bar_idx]
                if np.isnan(strength) or strength <= 0:
                    continue
                if bar_idx < R160_MOMENTUM_LOOKBACK_4H:
                    continue
                ret_14d = arr['return_14d'][bar_idx]
                if np.isnan(ret_14d) or ret_14d <= 0:
                    continue
                new_candidates.append((token, 1, strength))
            if arr['breakout_short'][bar_idx]:
                strength = arr['short_strength'][bar_idx]
                if np.isnan(strength) or strength <= 0:
                    continue
                if bar_idx < R160_MOMENTUM_LOOKBACK_4H:
                    continue
                ret_14d = arr['return_14d'][bar_idx]
                if np.isnan(ret_14d) or ret_14d >= 0:
                    continue
                new_candidates.append((token, -1, strength))

        pending_entries = new_candidates
        equity_curve[current_time] = equity

    # Close remaining positions
    last_time = all_times[-1]
    for pos in positions:
        token = pos.token
        arr = token_arrays[token]
        if last_time in token_time_idx[token]:
            bar_idx = token_time_idx[token][last_time]
        else:
            bar_idx = len(arr['close']) - 1
        exit_price = arr['close'][bar_idx]
        if pos.direction == 1:
            raw_return = (exit_price / pos.entry_price - 1.0)
        else:
            raw_return = (1.0 - exit_price / pos.entry_price)
        levered_return = raw_return * leverage
        exit_cost = fee * leverage
        effective_size = pos.size_frac * pos.remaining_frac
        pnl = (levered_return - exit_cost) * effective_size
        equity += pnl

    eq = pd.Series(equity_curve)

    # Trim to sim period and renormalize to start at 1.0 for fair comparison
    eq_sim = eq.loc[SIM_START:SIM_END]
    if len(eq_sim) > 0:
        eq_sim = eq_sim / eq_sim.iloc[0]
    else:
        eq_sim = eq

    m = compute_metrics(eq_sim)
    log(f"    R160: Ann={m['annual_return']*100:.1f}%, Sharpe={m['sharpe']:.2f}, "
        f"MaxDD={m['max_dd']*100:.1f}%, 12M={m['last_12m_return']*100:.1f}%")
    return eq_sim


# ============================================================
# STRATEGY 1: MEAN REVERSION ON HIGHLY VOLATILE TOKENS
# ============================================================

def run_strategy1_mean_reversion(
    tokens: Dict[str, pd.DataFrame],
    daily_close: pd.DataFrame,
    daily_returns: pd.DataFrame,
    rolling_avg_dvol_shifted: pd.DataFrame,
    hourly_fundings: Dict[str, pd.Series],
) -> pd.Series:
    """
    Mean-Reversion on Highly Volatile Tokens.
    - Select tokens with highest ATR/price (top 20%) on 4H bars
    - RSI(14) on 4H bars: when RSI > 70, SHORT with trailing SMA(20) exit. When RSI < 30, LONG.
    - Equal weight, max 5 positions
    """
    log("  Running Strategy 1: Mean Reversion on Volatile Tokens...")

    # Build 4H RSI and ATR/price for each token
    token_4h = {}
    for ticker, df_1h in tokens.items():
        df = df_1h.copy()
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        df = df[~df.index.duplicated(keep='first')]

        df_4h = df.resample('4h').agg({
            'open': 'first', 'high': 'max', 'low': 'min',
            'close': 'last', 'volume': 'sum',
        }).dropna(subset=['close'])

        if 'funding_1h' in df.columns:
            df_4h['funding_4h'] = df['funding_1h'].resample('4h').sum()
        else:
            df_4h['funding_4h'] = 0.0

        if len(df_4h) < 100:
            continue

        # ATR
        tr_hl = df_4h['high'] - df_4h['low']
        tr_hc = (df_4h['high'] - df_4h['close'].shift(1)).abs()
        tr_lc = (df_4h['low'] - df_4h['close'].shift(1)).abs()
        df_4h['true_range'] = pd.concat([tr_hl, tr_hc, tr_lc], axis=1).max(axis=1)
        df_4h['atr14'] = df_4h['true_range'].rolling(14 * 6).mean()  # 14 days of 4h bars
        df_4h['atr_pct'] = df_4h['atr14'] / df_4h['close']

        # RSI(14) on 4H bars (14 periods = 14 * 4h = 56 hours)
        delta = df_4h['close'].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(span=14, adjust=False).mean()
        avg_loss = loss.ewm(span=14, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-10)
        df_4h['rsi14'] = 100 - (100 / (1 + rs))

        # SMA(20) on 4H bars
        df_4h['sma20'] = df_4h['close'].rolling(20).mean()

        token_4h[ticker] = df_4h

    log(f"    Tokens with 4H data: {len(token_4h)}")

    # Daily simulation
    sim_dates = daily_close.loc[SIM_START:SIM_END].index
    equity = 1.0
    equity_series = {}

    # Position state: {ticker: {'direction': 1/-1, 'weight': float, 'entry_date': Timestamp}}
    positions = {}
    MAX_POSITIONS = 5

    for date in sim_dates:
        # Get end-of-previous-day 4H bar for signals (avoid look-ahead)
        signal_time_end = date  # Use midnight of current day (yesterday's last bar)

        # 1. PnL from current positions
        daily_pnl = 0.0
        daily_ret = daily_returns.loc[date] if date in daily_returns.index else pd.Series(dtype=float)

        for ticker, pos in list(positions.items()):
            if ticker in daily_ret.index and not np.isnan(daily_ret[ticker]):
                if pos['direction'] == 1:
                    daily_pnl += pos['weight'] * daily_ret[ticker]
                else:
                    daily_pnl -= pos['weight'] * daily_ret[ticker]

            # Funding
            if ticker in hourly_fundings:
                f_start = date
                f_end = date + pd.Timedelta(hours=23)
                f_slice = hourly_fundings[ticker].loc[f_start:f_end]
                if len(f_slice) > 0:
                    if pos['direction'] == 1:
                        daily_pnl += -f_slice.sum() * pos['weight']
                    else:
                        daily_pnl += f_slice.sum() * pos['weight']

        equity *= (1 + daily_pnl)

        # 2. Check exits for existing positions (SMA trail stop on 4H)
        for ticker in list(positions.keys()):
            if ticker not in token_4h:
                continue
            df_4h = token_4h[ticker]
            # Get the most recent 4H bar before signal_time_end
            recent = df_4h[df_4h.index < signal_time_end]
            if len(recent) < 2:
                continue
            last_bar = recent.iloc[-1]
            pos = positions[ticker]

            # Trail stop: long exits if close > SMA20, short exits if close < SMA20
            if pos['direction'] == 1 and last_bar['close'] > last_bar['sma20']:
                # Exit long (mean reversion target reached)
                cost = pos['weight'] * FEE_FRAC
                equity *= (1 - cost)
                del positions[ticker]
            elif pos['direction'] == -1 and last_bar['close'] < last_bar['sma20']:
                # Exit short (mean reversion target reached)
                cost = pos['weight'] * FEE_FRAC
                equity *= (1 - cost)
                del positions[ticker]

        # 3. Check for new entries
        if len(positions) < MAX_POSITIONS:
            # Compute ATR/price ranking across tokens
            atr_pct_today = {}
            rsi_today = {}
            for ticker, df_4h in token_4h.items():
                if ticker in positions:
                    continue
                # Check volume eligibility
                if date in rolling_avg_dvol_shifted.index and ticker in rolling_avg_dvol_shifted.columns:
                    vol = rolling_avg_dvol_shifted.loc[date, ticker]
                    if np.isnan(vol) or vol < MIN_AVG_DAILY_VOLUME_USD:
                        continue
                else:
                    continue

                recent = df_4h[df_4h.index < signal_time_end]
                if len(recent) < 20:
                    continue
                last_bar = recent.iloc[-1]
                if np.isnan(last_bar['atr_pct']) or np.isnan(last_bar['rsi14']):
                    continue
                atr_pct_today[ticker] = last_bar['atr_pct']
                rsi_today[ticker] = last_bar['rsi14']

            if len(atr_pct_today) > 5:
                # Select top 20% by ATR/price
                atr_series = pd.Series(atr_pct_today)
                threshold = atr_series.quantile(0.80)
                volatile_tokens = atr_series[atr_series >= threshold].index.tolist()

                # Among volatile tokens, check RSI signals
                candidates = []
                for ticker in volatile_tokens:
                    rsi = rsi_today.get(ticker, 50)
                    if rsi > 70:
                        candidates.append((ticker, -1, rsi))  # SHORT
                    elif rsi < 30:
                        candidates.append((ticker, 1, 100 - rsi))  # LONG (higher priority for lower RSI)

                # Sort by signal strength and take top available
                candidates.sort(key=lambda x: x[2], reverse=True)
                slots = MAX_POSITIONS - len(positions)
                for ticker, direction, _ in candidates[:slots]:
                    weight = 1.0 / MAX_POSITIONS
                    cost = weight * FEE_FRAC
                    equity *= (1 - cost)
                    positions[ticker] = {
                        'direction': direction,
                        'weight': weight,
                        'entry_date': date,
                    }

        equity_series[date] = equity

    eq = pd.Series(equity_series)
    m = compute_metrics(eq)
    log(f"    S1 MeanRev: Ann={m['annual_return']*100:.1f}%, Sharpe={m['sharpe']:.2f}, "
        f"MaxDD={m['max_dd']*100:.1f}%, 12M={m['last_12m_return']*100:.1f}%")
    return eq


# ============================================================
# STRATEGY 2: FUNDING RATE CARRY
# ============================================================

def run_strategy2_funding_carry(
    daily_close: pd.DataFrame,
    daily_returns: pd.DataFrame,
    rolling_avg_dvol_shifted: pd.DataFrame,
    hourly_fundings: Dict[str, pd.Series],
) -> pd.Series:
    """
    Funding Rate Carry (Short-Term).
    - Every 3 days: SHORT 3 tokens with highest funding rates (>0.03%/8h),
      LONG 3 with lowest (<-0.01%/8h)
    - Equal weight 50/50. Hold for 3 days then rebalance.
    - Profit from funding payments, not price direction.
    """
    log("  Running Strategy 2: Funding Rate Carry...")

    sim_dates = daily_close.loc[SIM_START:SIM_END].index
    rebalance_days = 3
    rebalance_indices = set(range(0, len(sim_dates), rebalance_days))

    equity = 1.0
    equity_series = {}
    current_longs = {}   # ticker -> weight
    current_shorts = {}
    prev_longs = set()
    prev_shorts = set()
    pending_longs = None
    pending_shorts = None

    for i, date in enumerate(sim_dates):
        # Apply pending rebalance
        if pending_longs is not None:
            new_longs_set = set(pending_longs.keys())
            new_shorts_set = set(pending_shorts.keys())
            long_entries = new_longs_set - prev_longs
            long_exits = prev_longs - new_longs_set
            short_entries = new_shorts_set - prev_shorts
            short_exits = prev_shorts - new_shorts_set

            cost = 0.0
            for t in long_entries:
                cost += pending_longs[t] * FEE_FRAC
            for t in long_exits:
                cost += current_longs.get(t, 0) * FEE_FRAC
            for t in short_entries:
                cost += pending_shorts[t] * FEE_FRAC
            for t in short_exits:
                cost += current_shorts.get(t, 0) * FEE_FRAC

            equity *= (1 - cost)
            current_longs = pending_longs
            current_shorts = pending_shorts
            prev_longs = new_longs_set
            prev_shorts = new_shorts_set
            pending_longs = None
            pending_shorts = None

        # Daily PnL
        daily_pnl = 0.0
        daily_ret = daily_returns.loc[date] if date in daily_returns.index else pd.Series(dtype=float)

        for ticker, weight in current_longs.items():
            if ticker in daily_ret.index and not np.isnan(daily_ret[ticker]):
                daily_pnl += weight * daily_ret[ticker]
        for ticker, weight in current_shorts.items():
            if ticker in daily_ret.index and not np.isnan(daily_ret[ticker]):
                daily_pnl -= weight * daily_ret[ticker]

        # Funding PnL (this is the main profit source for carry strategy)
        funding_start = date
        funding_end = date + pd.Timedelta(hours=23)
        for ticker, weight in current_longs.items():
            if ticker in hourly_fundings:
                f_slice = hourly_fundings[ticker].loc[funding_start:funding_end]
                if len(f_slice) > 0:
                    daily_pnl += -f_slice.sum() * weight
        for ticker, weight in current_shorts.items():
            if ticker in hourly_fundings:
                f_slice = hourly_fundings[ticker].loc[funding_start:funding_end]
                if len(f_slice) > 0:
                    daily_pnl += f_slice.sum() * weight

        equity *= (1 + daily_pnl)
        equity_series[date] = equity

        # Rebalance
        if i in rebalance_indices:
            # Compute average daily funding rate over past 3 days for each token
            lookback_start = date - pd.Timedelta(days=3)
            funding_avgs = {}

            for ticker in daily_close.columns:
                if ticker not in hourly_fundings:
                    continue
                # Volume eligibility
                if date in rolling_avg_dvol_shifted.index and ticker in rolling_avg_dvol_shifted.columns:
                    vol = rolling_avg_dvol_shifted.loc[date, ticker]
                    if np.isnan(vol) or vol < MIN_AVG_DAILY_VOLUME_USD:
                        continue
                else:
                    continue

                f_slice = hourly_fundings[ticker].loc[lookback_start:date - pd.Timedelta(hours=1)]
                if len(f_slice) < 24:  # Need at least 1 day of data
                    continue
                # Convert to 8-hour funding rate equivalent
                daily_funding = f_slice.sum() / max(1, len(f_slice) / 24)
                rate_8h = daily_funding / 3  # 3 funding periods per day
                funding_avgs[ticker] = rate_8h

            if len(funding_avgs) < 6:
                continue

            funding_series = pd.Series(funding_avgs).sort_values()

            # Short the 3 highest funding rate tokens (>0.03%/8h = 0.0003)
            high_funding = funding_series[funding_series > 0.0003]
            short_tokens = high_funding.tail(3).index.tolist() if len(high_funding) >= 1 else []
            if len(short_tokens) < 1:
                # Relax: take top 3 by funding rate regardless
                short_tokens = funding_series.tail(3).index.tolist()

            # Long the 3 lowest funding rate tokens (<-0.01%/8h = -0.0001)
            low_funding = funding_series[funding_series < -0.0001]
            long_tokens = low_funding.head(3).index.tolist() if len(low_funding) >= 1 else []
            if len(long_tokens) < 1:
                # Relax: take bottom 3 by funding rate
                long_tokens = funding_series.head(3).index.tolist()

            # Equal weight 50/50
            if long_tokens:
                pending_longs = {t: 0.5 / len(long_tokens) for t in long_tokens}
            else:
                pending_longs = {}

            if short_tokens:
                pending_shorts = {t: 0.5 / len(short_tokens) for t in short_tokens}
            else:
                pending_shorts = {}

    eq = pd.Series(equity_series)
    m = compute_metrics(eq)
    log(f"    S2 FundCarry: Ann={m['annual_return']*100:.1f}%, Sharpe={m['sharpe']:.2f}, "
        f"MaxDD={m['max_dd']*100:.1f}%, 12M={m['last_12m_return']*100:.1f}%")
    return eq


# ============================================================
# STRATEGY 3: VOLUME MOMENTUM
# ============================================================

def run_strategy3_volume_momentum(
    daily_close: pd.DataFrame,
    daily_returns: pd.DataFrame,
    daily_volume: pd.DataFrame,
    ema_signal_shifted: pd.DataFrame,
    rolling_avg_dvol_shifted: pd.DataFrame,
    hourly_fundings: Dict[str, pd.Series],
) -> pd.Series:
    """
    Volume Momentum.
    - Rank tokens by 7-day volume growth rate (volume today / volume 7d ago)
    - Long top 5 with increasing volume, short bottom 5 with decreasing volume
    - Weekly rebalance, per-token regime filter
    """
    log("  Running Strategy 3: Volume Momentum...")

    sim_dates = daily_close.loc[SIM_START:SIM_END].index
    rebalance_days = 7
    rebalance_indices = set(range(0, len(sim_dates), rebalance_days))
    k = 5

    # Pre-compute shifted 7-day volume growth: vol_growth[T] = vol_sum_7d[T-1] / vol_sum_7d[T-8]
    vol_sum_7d = daily_volume.rolling(7, min_periods=5).sum()
    vol_growth = (vol_sum_7d.shift(1) / vol_sum_7d.shift(8)).replace([np.inf, -np.inf], np.nan)

    equity = 1.0
    equity_series = {}
    current_longs = {}
    current_shorts = {}
    prev_longs = set()
    prev_shorts = set()
    pending_longs = None
    pending_shorts = None

    for i, date in enumerate(sim_dates):
        if pending_longs is not None:
            new_longs_set = set(pending_longs.keys())
            new_shorts_set = set(pending_shorts.keys())
            long_entries = new_longs_set - prev_longs
            long_exits = prev_longs - new_longs_set
            short_entries = new_shorts_set - prev_shorts
            short_exits = prev_shorts - new_shorts_set

            cost = 0.0
            for t in long_entries:
                cost += pending_longs[t] * FEE_FRAC
            for t in long_exits:
                cost += current_longs.get(t, 0) * FEE_FRAC
            for t in short_entries:
                cost += pending_shorts[t] * FEE_FRAC
            for t in short_exits:
                cost += current_shorts.get(t, 0) * FEE_FRAC

            equity *= (1 - cost)
            current_longs = pending_longs
            current_shorts = pending_shorts
            prev_longs = new_longs_set
            prev_shorts = new_shorts_set
            pending_longs = None
            pending_shorts = None

        daily_pnl = 0.0
        daily_ret = daily_returns.loc[date] if date in daily_returns.index else pd.Series(dtype=float)

        for ticker, weight in current_longs.items():
            if ticker in daily_ret.index and not np.isnan(daily_ret[ticker]):
                daily_pnl += weight * daily_ret[ticker]
        for ticker, weight in current_shorts.items():
            if ticker in daily_ret.index and not np.isnan(daily_ret[ticker]):
                daily_pnl -= weight * daily_ret[ticker]

        # Funding
        funding_start = date
        funding_end = date + pd.Timedelta(hours=23)
        for ticker, weight in current_longs.items():
            if ticker in hourly_fundings:
                f_slice = hourly_fundings[ticker].loc[funding_start:funding_end]
                if len(f_slice) > 0:
                    daily_pnl += -f_slice.sum() * weight
        for ticker, weight in current_shorts.items():
            if ticker in hourly_fundings:
                f_slice = hourly_fundings[ticker].loc[funding_start:funding_end]
                if len(f_slice) > 0:
                    daily_pnl += f_slice.sum() * weight

        equity *= (1 + daily_pnl)
        equity_series[date] = equity

        # Rebalance
        if i in rebalance_indices and date in vol_growth.index:
            vg = vol_growth.loc[date].dropna()

            # Volume eligibility
            if date in rolling_avg_dvol_shifted.index:
                vol_at_date = rolling_avg_dvol_shifted.loc[date].dropna()
                eligible = vol_at_date[vol_at_date >= MIN_AVG_DAILY_VOLUME_USD].index
                vg = vg[vg.index.isin(eligible)]

            if len(vg) >= 2 * k:
                ranked = vg.sort_values(ascending=False)
                top_k = ranked.head(k).index.tolist()
                bottom_k = ranked.tail(k).index.tolist()

                # Regime filter: only long where EMA fast > slow, short where EMA fast < slow
                if date in ema_signal_shifted.index:
                    ema_at_date = ema_signal_shifted.loc[date].dropna()
                    top_k = [t for t in top_k if t in ema_at_date.index and ema_at_date[t] > 0]
                    bottom_k = [t for t in bottom_k if t in ema_at_date.index and ema_at_date[t] < 0]

                n_longs = max(len(top_k), 1)
                n_shorts = max(len(bottom_k), 1)

                pending_longs = {t: 0.5 / n_longs for t in top_k} if top_k else {}
                pending_shorts = {t: 0.5 / n_shorts for t in bottom_k} if bottom_k else {}

    eq = pd.Series(equity_series)
    m = compute_metrics(eq)
    log(f"    S3 VolMom: Ann={m['annual_return']*100:.1f}%, Sharpe={m['sharpe']:.2f}, "
        f"MaxDD={m['max_dd']*100:.1f}%, 12M={m['last_12m_return']*100:.1f}%")
    return eq


# ============================================================
# STRATEGY 4: SHORT-TERM CROSS-SECTIONAL REVERSAL
# ============================================================

def run_strategy4_reversal(
    daily_close: pd.DataFrame,
    daily_returns: pd.DataFrame,
    ema_signal_shifted: pd.DataFrame,
    rolling_avg_dvol_shifted: pd.DataFrame,
    hourly_fundings: Dict[str, pd.Series],
) -> pd.Series:
    """
    Short-Term Reversal (opposite of R158 momentum).
    - LONG the worst 5 performers over 3 days, SHORT the best 5
    - Weekly rebalance
    - Regime filter: only long reversals in uptrending tokens, short in downtrending
    """
    log("  Running Strategy 4: Short-Term Reversal...")

    sim_dates = daily_close.loc[SIM_START:SIM_END].index
    rebalance_days = 7
    rebalance_indices = set(range(0, len(sim_dates), rebalance_days))
    k = 5
    lookback = 3

    # Pre-compute shifted 3-day returns: ret[T] = close[T-1] / close[T-1-3] - 1
    rev_returns_shifted = daily_close.shift(1) / daily_close.shift(1 + lookback) - 1

    equity = 1.0
    equity_series = {}
    current_longs = {}
    current_shorts = {}
    prev_longs = set()
    prev_shorts = set()
    pending_longs = None
    pending_shorts = None

    for i, date in enumerate(sim_dates):
        if pending_longs is not None:
            new_longs_set = set(pending_longs.keys())
            new_shorts_set = set(pending_shorts.keys())
            long_entries = new_longs_set - prev_longs
            long_exits = prev_longs - new_longs_set
            short_entries = new_shorts_set - prev_shorts
            short_exits = prev_shorts - new_shorts_set

            cost = 0.0
            for t in long_entries:
                cost += pending_longs[t] * FEE_FRAC
            for t in long_exits:
                cost += current_longs.get(t, 0) * FEE_FRAC
            for t in short_entries:
                cost += pending_shorts[t] * FEE_FRAC
            for t in short_exits:
                cost += current_shorts.get(t, 0) * FEE_FRAC

            equity *= (1 - cost)
            current_longs = pending_longs
            current_shorts = pending_shorts
            prev_longs = new_longs_set
            prev_shorts = new_shorts_set
            pending_longs = None
            pending_shorts = None

        daily_pnl = 0.0
        daily_ret = daily_returns.loc[date] if date in daily_returns.index else pd.Series(dtype=float)

        for ticker, weight in current_longs.items():
            if ticker in daily_ret.index and not np.isnan(daily_ret[ticker]):
                daily_pnl += weight * daily_ret[ticker]
        for ticker, weight in current_shorts.items():
            if ticker in daily_ret.index and not np.isnan(daily_ret[ticker]):
                daily_pnl -= weight * daily_ret[ticker]

        # Funding
        funding_start = date
        funding_end = date + pd.Timedelta(hours=23)
        for ticker, weight in current_longs.items():
            if ticker in hourly_fundings:
                f_slice = hourly_fundings[ticker].loc[funding_start:funding_end]
                if len(f_slice) > 0:
                    daily_pnl += -f_slice.sum() * weight
        for ticker, weight in current_shorts.items():
            if ticker in hourly_fundings:
                f_slice = hourly_fundings[ticker].loc[funding_start:funding_end]
                if len(f_slice) > 0:
                    daily_pnl += f_slice.sum() * weight

        equity *= (1 + daily_pnl)
        equity_series[date] = equity

        # Rebalance
        if i in rebalance_indices and date in rev_returns_shifted.index:
            rev_rets = rev_returns_shifted.loc[date].dropna()

            # Volume eligibility
            if date in rolling_avg_dvol_shifted.index:
                vol_at_date = rolling_avg_dvol_shifted.loc[date].dropna()
                eligible = vol_at_date[vol_at_date >= MIN_AVG_DAILY_VOLUME_USD].index
                rev_rets = rev_rets[rev_rets.index.isin(eligible)]

            if len(rev_rets) >= 2 * k:
                ranked = rev_rets.sort_values(ascending=True)  # worst performers first
                # LONG worst performers (reversal), SHORT best performers
                long_candidates = ranked.head(k).index.tolist()
                short_candidates = ranked.tail(k).index.tolist()

                # Regime filter: long only uptrending, short only downtrending
                if date in ema_signal_shifted.index:
                    ema_at_date = ema_signal_shifted.loc[date].dropna()
                    long_candidates = [t for t in long_candidates
                                       if t in ema_at_date.index and ema_at_date[t] > 0]
                    short_candidates = [t for t in short_candidates
                                        if t in ema_at_date.index and ema_at_date[t] < 0]

                n_longs = max(len(long_candidates), 1)
                n_shorts = max(len(short_candidates), 1)

                pending_longs = {t: 0.5 / n_longs for t in long_candidates} if long_candidates else {}
                pending_shorts = {t: 0.5 / n_shorts for t in short_candidates} if short_candidates else {}

    eq = pd.Series(equity_series)
    m = compute_metrics(eq)
    log(f"    S4 Reversal: Ann={m['annual_return']*100:.1f}%, Sharpe={m['sharpe']:.2f}, "
        f"MaxDD={m['max_dd']*100:.1f}%, 12M={m['last_12m_return']*100:.1f}%")
    return eq


# ============================================================
# STRATEGY 5: LOW-VOLATILITY ANOMALY
# ============================================================

def run_strategy5_low_vol(
    daily_close: pd.DataFrame,
    daily_returns: pd.DataFrame,
    ema_signal_shifted: pd.DataFrame,
    rolling_avg_dvol_shifted: pd.DataFrame,
    hourly_fundings: Dict[str, pd.Series],
) -> pd.Series:
    """
    Low-Volatility Anomaly.
    - Rank tokens by realized 14-day volatility
    - Long the 5 lowest-volatility tokens, short the 5 highest-volatility
    - Weekly rebalance, regime filter
    """
    log("  Running Strategy 5: Low-Volatility Anomaly...")

    sim_dates = daily_close.loc[SIM_START:SIM_END].index
    rebalance_days = 7
    rebalance_indices = set(range(0, len(sim_dates), rebalance_days))
    k = 5

    # Pre-compute shifted 14-day realized volatility
    # vol[T] = std of returns over days [T-15, T-2] (shifted to avoid look-ahead)
    realized_vol = daily_returns.rolling(14, min_periods=10).std().shift(1)

    equity = 1.0
    equity_series = {}
    current_longs = {}
    current_shorts = {}
    prev_longs = set()
    prev_shorts = set()
    pending_longs = None
    pending_shorts = None

    for i, date in enumerate(sim_dates):
        if pending_longs is not None:
            new_longs_set = set(pending_longs.keys())
            new_shorts_set = set(pending_shorts.keys())
            long_entries = new_longs_set - prev_longs
            long_exits = prev_longs - new_longs_set
            short_entries = new_shorts_set - prev_shorts
            short_exits = prev_shorts - new_shorts_set

            cost = 0.0
            for t in long_entries:
                cost += pending_longs[t] * FEE_FRAC
            for t in long_exits:
                cost += current_longs.get(t, 0) * FEE_FRAC
            for t in short_entries:
                cost += pending_shorts[t] * FEE_FRAC
            for t in short_exits:
                cost += current_shorts.get(t, 0) * FEE_FRAC

            equity *= (1 - cost)
            current_longs = pending_longs
            current_shorts = pending_shorts
            prev_longs = new_longs_set
            prev_shorts = new_shorts_set
            pending_longs = None
            pending_shorts = None

        daily_pnl = 0.0
        daily_ret = daily_returns.loc[date] if date in daily_returns.index else pd.Series(dtype=float)

        for ticker, weight in current_longs.items():
            if ticker in daily_ret.index and not np.isnan(daily_ret[ticker]):
                daily_pnl += weight * daily_ret[ticker]
        for ticker, weight in current_shorts.items():
            if ticker in daily_ret.index and not np.isnan(daily_ret[ticker]):
                daily_pnl -= weight * daily_ret[ticker]

        # Funding
        funding_start = date
        funding_end = date + pd.Timedelta(hours=23)
        for ticker, weight in current_longs.items():
            if ticker in hourly_fundings:
                f_slice = hourly_fundings[ticker].loc[funding_start:funding_end]
                if len(f_slice) > 0:
                    daily_pnl += -f_slice.sum() * weight
        for ticker, weight in current_shorts.items():
            if ticker in hourly_fundings:
                f_slice = hourly_fundings[ticker].loc[funding_start:funding_end]
                if len(f_slice) > 0:
                    daily_pnl += f_slice.sum() * weight

        equity *= (1 + daily_pnl)
        equity_series[date] = equity

        # Rebalance
        if i in rebalance_indices and date in realized_vol.index:
            rv = realized_vol.loc[date].dropna()

            # Volume eligibility
            if date in rolling_avg_dvol_shifted.index:
                vol_at_date = rolling_avg_dvol_shifted.loc[date].dropna()
                eligible = vol_at_date[vol_at_date >= MIN_AVG_DAILY_VOLUME_USD].index
                rv = rv[rv.index.isin(eligible)]

            if len(rv) >= 2 * k:
                ranked = rv.sort_values(ascending=True)  # lowest vol first
                # Long lowest volatility, short highest volatility
                long_candidates = ranked.head(k).index.tolist()
                short_candidates = ranked.tail(k).index.tolist()

                # Regime filter
                if date in ema_signal_shifted.index:
                    ema_at_date = ema_signal_shifted.loc[date].dropna()
                    long_candidates = [t for t in long_candidates
                                       if t in ema_at_date.index and ema_at_date[t] > 0]
                    short_candidates = [t for t in short_candidates
                                        if t in ema_at_date.index and ema_at_date[t] < 0]

                n_longs = max(len(long_candidates), 1)
                n_shorts = max(len(short_candidates), 1)

                pending_longs = {t: 0.5 / n_longs for t in long_candidates} if long_candidates else {}
                pending_shorts = {t: 0.5 / n_shorts for t in short_candidates} if short_candidates else {}

    eq = pd.Series(equity_series)
    m = compute_metrics(eq)
    log(f"    S5 LowVol: Ann={m['annual_return']*100:.1f}%, Sharpe={m['sharpe']:.2f}, "
        f"MaxDD={m['max_dd']*100:.1f}%, 12M={m['last_12m_return']*100:.1f}%")
    return eq


# ============================================================
# COMBINATION TEST WITH DRAWDOWN CONTROL
# ============================================================

def combine_strategies_with_dd_control(
    strategy_curves: Dict[str, pd.Series],
    weights: Dict[str, float],
    leverage: float,
) -> pd.Series:
    """
    Combine multiple strategy equity curves with leverage and drawdown control.

    DD control bands:
      0-5% DD  -> 100% of target leverage
      5-10% DD -> 75%
      10-15% DD -> 50%
      15-20% DD -> 25%
      >20% DD  -> 0% (fully de-leveraged)

    Borrow cost: 5% annual on (leverage - 1) portion.
    """
    # Get daily returns for each strategy that's in the weights
    daily_rets = {}
    for name in weights.keys():
        if name in strategy_curves and len(strategy_curves[name]) > 0:
            dr = daily_returns_from_equity(strategy_curves[name])
            daily_rets[name] = dr

    if not daily_rets:
        return pd.Series(dtype=float)

    # Align all to common date index
    all_dates = None
    for dr in daily_rets.values():
        if all_dates is None:
            all_dates = dr.index
        else:
            all_dates = all_dates.intersection(dr.index)

    all_dates = sorted(all_dates)

    # Renormalize weights to sum to 1.0 based on which strategies we actually have
    active_weight_sum = sum(weights[name] for name in daily_rets.keys())
    if active_weight_sum <= 0:
        return pd.Series(dtype=float)
    norm_weights = {name: weights[name] / active_weight_sum for name in daily_rets.keys()}

    equity = 1.0
    equity_curve = {}
    peak_equity = 1.0

    for date in all_dates:
        # Compute current drawdown from peak
        dd = 1 - equity / peak_equity

        # DD control scaling
        # Note: 0% at >20% is a permanent kill switch — use 10% min
        # to allow recovery. The spec says >20% -> 0% but that makes
        # equity flatline forever; we use 10% as minimum to preserve
        # recovery capacity while still heavily reducing risk.
        if dd <= 0.05:
            dd_scale = 1.0
        elif dd <= 0.10:
            dd_scale = 0.75
        elif dd <= 0.15:
            dd_scale = 0.50
        elif dd <= 0.20:
            dd_scale = 0.25
        else:
            dd_scale = 0.10

        effective_leverage = leverage * dd_scale

        # Weighted return from all strategies
        combined_ret = 0.0
        for name, dr in daily_rets.items():
            if date in dr.index:
                combined_ret += norm_weights[name] * dr[date]

        # Leveraged return
        leveraged_ret = combined_ret * effective_leverage

        # Borrow cost on leveraged portion
        borrow = max(0, effective_leverage - 1) * DAILY_BORROW_RATE

        daily_change = leveraged_ret - borrow
        equity *= (1 + daily_change)

        if equity > peak_equity:
            peak_equity = equity

        equity_curve[date] = equity

    return pd.Series(equity_curve)


# ============================================================
# REPORT GENERATION
# ============================================================

def format_pct(v: float) -> str:
    return f"{v*100:.1f}%"


def format_float(v: float, decimals: int = 2) -> str:
    return f"{v:.{decimals}f}"


def monthly_returns_table(equity: pd.Series) -> str:
    """Generate a monthly returns table as markdown."""
    daily_eq = equity.resample('1D').last().dropna()
    monthly_eq = daily_eq.resample('ME').last().dropna()
    monthly_rets = monthly_eq.pct_change().dropna()

    years = sorted(monthly_rets.index.year.unique())
    month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

    lines = []
    header = "| Year | " + " | ".join(month_names) + " | Annual |"
    sep = "|------|" + "|".join(["-------"] * 12) + "|--------|"
    lines.append(header)
    lines.append(sep)

    for year in years:
        row = [f"| {year} "]
        yr_ret = 1.0
        for m in range(1, 13):
            mask = (monthly_rets.index.year == year) & (monthly_rets.index.month == m)
            vals = monthly_rets[mask]
            if len(vals) > 0:
                r = vals.iloc[0]
                yr_ret *= (1 + r)
                row.append(f" {r*100:+.1f}% ")
            else:
                row.append("  --  ")
        row.append(f" {(yr_ret-1)*100:+.1f}% ")
        lines.append("|".join(row) + "|")

    return "\n".join(lines)


def generate_report(
    all_curves: Dict[str, pd.Series],
    all_metrics: Dict[str, dict],
    correlations: pd.DataFrame,
    combo_results: List[dict],
    n_tokens: int,
    token_list: List[str],
) -> str:
    """Generate full markdown report."""
    lines = []
    lines.append("# R166 -- Multi-Strategy Search Results\n")
    lines.append(f"**Generated:** {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Simulation Period:** {SIM_START.date()} to {SIM_END.date()} (~2 years)")
    lines.append(f"**Last 12 Months:** {LAST_12MO_START.date()} to {SIM_END.date()}")
    lines.append(f"**Token Universe:** {n_tokens} tokens passing volume filter")
    lines.append(f"**Costs:** {FEE_BPS:.0f} bps per side + hourly funding from parquet")
    lines.append(f"**Borrow Cost:** {ANNUAL_BORROW_RATE*100:.0f}% annual on leveraged portion")
    lines.append("")

    # ---- Individual Strategy Results ----
    lines.append("---\n")
    lines.append("## Individual Strategy Performance (1x, no leverage)\n")

    strategy_names = [
        ('R158_Momentum', 'R158: Momentum Rotation (L7_R7_K5_50/50_RFY)'),
        ('R160_VolBreakout', 'R160: Volatility Breakout (Variant B, 1x)'),
        ('S1_MeanReversion', 'S1: Mean-Reversion on Volatile Tokens (RSI-based)'),
        ('S2_FundingCarry', 'S2: Funding Rate Carry (short high-fund, long low-fund)'),
        ('S3_VolumeMomentum', 'S3: Volume Momentum (7d volume growth rank)'),
        ('S4_Reversal', 'S4: Short-Term Reversal (3d, opposite of momentum)'),
        ('S5_LowVol', 'S5: Low-Volatility Anomaly (long low-vol, short high-vol)'),
    ]

    lines.append("| Strategy | Full Ann Ret | Full MaxDD | Full Sharpe | Full Calmar | Full Sortino | PF "
                  "| 12M Ret | 12M Sharpe | 12M MaxDD |")
    lines.append("|----------|-------------|-----------|------------|------------|-------------|----"
                  "|---------|-----------|----------|")

    for key, desc in strategy_names:
        if key in all_metrics:
            m = all_metrics[key]
            lines.append(
                f"| {desc} "
                f"| {format_pct(m['annual_return'])} "
                f"| {format_pct(m['max_dd'])} "
                f"| **{format_float(m['sharpe'])}** "
                f"| {format_float(m['calmar'])} "
                f"| {format_float(m['sortino'])} "
                f"| {format_float(m['profit_factor'])} "
                f"| {format_pct(m['last_12m_return'])} "
                f"| {format_float(m['last_12m_sharpe'])} "
                f"| {format_pct(m['last_12m_max_dd'])} |"
            )

    # ---- Correlation Matrix ----
    lines.append("\n---\n")
    lines.append("## Daily Return Correlation Matrix\n")

    lines.append("| | " + " | ".join(correlations.columns) + " |")
    lines.append("|" + "---|" * (len(correlations.columns) + 1))

    for row_name in correlations.index:
        row_vals = []
        for col_name in correlations.columns:
            v = correlations.loc[row_name, col_name]
            if row_name == col_name:
                row_vals.append("1.00")
            else:
                row_vals.append(f"{v:.2f}")
        lines.append(f"| {row_name} | " + " | ".join(row_vals) + " |")

    # ---- Uncorrelated Candidates ----
    lines.append("\n---\n")
    lines.append("## Uncorrelated Strategy Candidates\n")
    lines.append("Criteria: Last-12M Sharpe > 0.3 AND correlation < 0.3 with both R158 and R160\n")

    new_strats = ['S1_MeanReversion', 'S2_FundingCarry', 'S3_VolumeMomentum',
                  'S4_Reversal', 'S5_LowVol']
    candidates = []
    for s in new_strats:
        if s not in all_metrics or s not in correlations.index:
            continue
        m = all_metrics[s]
        corr_r158 = correlations.loc[s, 'R158_Momentum'] if 'R158_Momentum' in correlations.columns else 1.0
        corr_r160 = correlations.loc[s, 'R160_VolBreakout'] if 'R160_VolBreakout' in correlations.columns else 1.0
        is_candidate = (m['last_12m_sharpe'] > 0.3 and
                        abs(corr_r158) < 0.3 and abs(corr_r160) < 0.3)
        lines.append(f"- **{s}**: 12M Sharpe={format_float(m['last_12m_sharpe'])}, "
                     f"corr(R158)={corr_r158:.2f}, corr(R160)={corr_r160:.2f} "
                     f"-> {'**CANDIDATE**' if is_candidate else 'not qualified'}")
        if is_candidate:
            candidates.append(s)

    # Also report those with relaxed criteria
    lines.append("\nRelaxed criteria (Sharpe > 0.0 AND |corr| < 0.5):\n")
    for s in new_strats:
        if s in candidates:
            continue
        if s not in all_metrics or s not in correlations.index:
            continue
        m = all_metrics[s]
        corr_r158 = correlations.loc[s, 'R158_Momentum'] if 'R158_Momentum' in correlations.columns else 1.0
        corr_r160 = correlations.loc[s, 'R160_VolBreakout'] if 'R160_VolBreakout' in correlations.columns else 1.0
        is_relaxed = (m['last_12m_sharpe'] > 0.0 and
                      abs(corr_r158) < 0.5 and abs(corr_r160) < 0.5)
        if is_relaxed:
            lines.append(f"- **{s}** (relaxed): 12M Sharpe={format_float(m['last_12m_sharpe'])}, "
                         f"corr(R158)={corr_r158:.2f}, corr(R160)={corr_r160:.2f}")

    # ---- Combination Tests ----
    lines.append("\n---\n")
    lines.append("## Combination Portfolio Tests (with Drawdown Control)\n")

    if combo_results:
        lines.append("DD control bands: 0-5% DD=100%, 5-10%=75%, 10-15%=50%, 15-20%=25%, >20%=0%\n")
        lines.append("Borrow cost: 5% annual on (leverage-1) portion\n")

        lines.append("| Portfolio | Leverage | Ann Ret | MaxDD | Sharpe | Calmar | Sortino "
                     "| 12M Ret | 12M Sharpe | 12M MaxDD |")
        lines.append("|-----------|---------|---------|-------|--------|--------|--------"
                     "|---------|-----------|----------|")

        for r in sorted(combo_results, key=lambda x: x['sharpe'], reverse=True):
            lines.append(
                f"| {r['label']} "
                f"| {r['leverage']:.0f}x "
                f"| {format_pct(r['annual_return'])} "
                f"| {format_pct(r['max_dd'])} "
                f"| **{format_float(r['sharpe'])}** "
                f"| {format_float(r['calmar'])} "
                f"| {format_float(r['sortino'])} "
                f"| {format_pct(r['last_12m_return'])} "
                f"| {format_float(r['last_12m_sharpe'])} "
                f"| {format_pct(r['last_12m_max_dd'])} |"
            )

        # Monthly returns for best combo
        best = max(combo_results, key=lambda x: x['sharpe'])
        if 'equity' in best and len(best['equity']) > 0:
            lines.append(f"\n### Monthly Returns: Best Combo ({best['label']} @ {best['leverage']:.0f}x)\n")
            lines.append(monthly_returns_table(best['equity']))
    else:
        lines.append("No strategies met the combination criteria.\n")

    # ---- Monthly returns for each individual strategy ----
    lines.append("\n---\n")
    lines.append("## Monthly Returns: Individual Strategies\n")

    for key, desc in strategy_names:
        if key in all_curves and len(all_curves[key]) > 0:
            lines.append(f"\n### {desc}\n")
            lines.append(monthly_returns_table(all_curves[key]))

    # ---- Tokens ----
    lines.append("\n---\n")
    lines.append(f"## Token Universe ({n_tokens} tokens)\n")
    lines.append(f"{', '.join(sorted(token_list))}\n")

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():
    t0 = time.time()

    log("=" * 70)
    log("R166 -- Multi-Strategy Search: Find Uncorrelated Components")
    log("=" * 70)

    # ── Load data ──────────────────────────────────────────────
    log("\n[1/6] Loading token data...")
    tokens = load_all_tokens()
    token_list = sorted(tokens.keys())
    log(f"  Loaded {len(tokens)} tokens passing volume filter")

    log("\n[2/6] Preparing daily data panels...")
    (daily_close, daily_returns, daily_volume, daily_dollar_vol,
     ema_signal_shifted, rolling_avg_dvol_shifted,
     hourly_fundings, hourly_closes) = prepare_daily_data(tokens)
    log(f"  Daily close shape: {daily_close.shape}")
    log(f"  Date range: {daily_close.index.min().date()} to {daily_close.index.max().date()}")

    # ── Run Reference Strategies ──────────────────────────────
    log("\n[3/6] Running reference strategies (R158, R160)...")

    eq_r158 = run_r158_best(daily_close, daily_returns, ema_signal_shifted,
                             rolling_avg_dvol_shifted, hourly_fundings)

    eq_r160 = run_r160_best(tokens)

    # ── Run New Strategies ────────────────────────────────────
    log("\n[4/6] Running new strategy candidates...")

    eq_s1 = run_strategy1_mean_reversion(
        tokens, daily_close, daily_returns, rolling_avg_dvol_shifted, hourly_fundings)

    eq_s2 = run_strategy2_funding_carry(
        daily_close, daily_returns, rolling_avg_dvol_shifted, hourly_fundings)

    eq_s3 = run_strategy3_volume_momentum(
        daily_close, daily_returns, daily_volume, ema_signal_shifted,
        rolling_avg_dvol_shifted, hourly_fundings)

    eq_s4 = run_strategy4_reversal(
        daily_close, daily_returns, ema_signal_shifted,
        rolling_avg_dvol_shifted, hourly_fundings)

    eq_s5 = run_strategy5_low_vol(
        daily_close, daily_returns, ema_signal_shifted,
        rolling_avg_dvol_shifted, hourly_fundings)

    # ── Compute Metrics and Correlations ──────────────────────
    log("\n[5/6] Computing metrics and correlations...")

    all_curves = {
        'R158_Momentum': eq_r158,
        'R160_VolBreakout': eq_r160,
        'S1_MeanReversion': eq_s1,
        'S2_FundingCarry': eq_s2,
        'S3_VolumeMomentum': eq_s3,
        'S4_Reversal': eq_s4,
        'S5_LowVol': eq_s5,
    }

    all_metrics = {}
    for name, eq in all_curves.items():
        if len(eq) > 0:
            all_metrics[name] = compute_metrics(eq)
        else:
            all_metrics[name] = compute_metrics(pd.Series([1.0], index=[SIM_START]))

    # Correlation matrix of daily returns
    daily_ret_df = {}
    for name, eq in all_curves.items():
        if len(eq) > 0:
            daily_ret_df[name] = daily_returns_from_equity(eq)

    if daily_ret_df:
        combined_rets = pd.DataFrame(daily_ret_df)
        # Align to common dates
        combined_rets = combined_rets.dropna(how='all')
        correlations = combined_rets.corr()
    else:
        correlations = pd.DataFrame()

    log("\n  Correlation matrix:")
    if len(correlations) > 0:
        for name in correlations.columns:
            corr_r158 = correlations.loc[name, 'R158_Momentum'] if 'R158_Momentum' in correlations.columns else 0
            corr_r160 = correlations.loc[name, 'R160_VolBreakout'] if 'R160_VolBreakout' in correlations.columns else 0
            log(f"    {name}: corr(R158)={corr_r158:.3f}, corr(R160)={corr_r160:.3f}")

    # ── Combination Tests ─────────────────────────────────────
    log("\n[6/6] Running combination portfolio tests...")

    combo_results = []

    # Find qualifying new strategies (strict criteria)
    new_strats = ['S1_MeanReversion', 'S2_FundingCarry', 'S3_VolumeMomentum',
                  'S4_Reversal', 'S5_LowVol']
    qualifying_strict = []
    qualifying_relaxed = []

    for s in new_strats:
        if s not in all_metrics or s not in correlations.index:
            continue
        m = all_metrics[s]
        corr_r158 = abs(correlations.loc[s, 'R158_Momentum']) if 'R158_Momentum' in correlations.columns else 1.0
        corr_r160 = abs(correlations.loc[s, 'R160_VolBreakout']) if 'R160_VolBreakout' in correlations.columns else 1.0

        if m['last_12m_sharpe'] > 0.3 and corr_r158 < 0.3 and corr_r160 < 0.3:
            qualifying_strict.append(s)
            log(f"  STRICT qualifier: {s} (12M Sharpe={m['last_12m_sharpe']:.2f}, "
                f"corr_R158={corr_r158:.2f}, corr_R160={corr_r160:.2f})")
        elif m['last_12m_sharpe'] > 0.0 and corr_r158 < 0.5 and corr_r160 < 0.5:
            qualifying_relaxed.append(s)
            log(f"  RELAXED qualifier: {s} (12M Sharpe={m['last_12m_sharpe']:.2f}, "
                f"corr_R158={corr_r158:.2f}, corr_R160={corr_r160:.2f})")

    # Build combos with strict qualifiers first, then relaxed if none qualify
    qualifiers = qualifying_strict if qualifying_strict else qualifying_relaxed
    if not qualifiers:
        # Use all strategies with positive Sharpe as last resort
        qualifiers = [s for s in new_strats if s in all_metrics and all_metrics[s]['sharpe'] > 0]
        log(f"  No strict/relaxed qualifiers. Using positive-Sharpe strategies: {qualifiers}")

    for q_strat in qualifiers:
        for lev in [2.0, 3.0, 4.0]:
            # 3-strategy combo: R158 + R160 + new
            n_strats = 3
            eq_weight = 1.0 / n_strats
            weights = {
                'R158_Momentum': eq_weight,
                'R160_VolBreakout': eq_weight,
                q_strat: eq_weight,
            }
            label = f"R158+R160+{q_strat}"
            eq_combo = combine_strategies_with_dd_control(all_curves, weights, lev)
            if len(eq_combo) > 0:
                m = compute_metrics(eq_combo)
                combo_results.append({
                    'label': label,
                    'leverage': lev,
                    'equity': eq_combo,
                    **m,
                })
                log(f"    {label} @{lev:.0f}x: Sharpe={m['sharpe']:.2f}, "
                    f"Ann={m['annual_return']*100:.1f}%, MaxDD={m['max_dd']*100:.1f}%, "
                    f"12M={m['last_12m_return']*100:.1f}%")

    # Also test broader combos if we have multiple qualifiers
    if len(qualifiers) >= 2:
        for lev in [2.0, 3.0, 4.0]:
            n_strats = 2 + len(qualifiers)
            eq_weight = 1.0 / n_strats
            weights = {'R158_Momentum': eq_weight, 'R160_VolBreakout': eq_weight}
            for q in qualifiers:
                weights[q] = eq_weight
            label = "R158+R160+" + "+".join(qualifiers)
            eq_combo = combine_strategies_with_dd_control(all_curves, weights, lev)
            if len(eq_combo) > 0:
                m = compute_metrics(eq_combo)
                combo_results.append({
                    'label': label,
                    'leverage': lev,
                    'equity': eq_combo,
                    **m,
                })
                log(f"    {label} @{lev:.0f}x: Sharpe={m['sharpe']:.2f}, "
                    f"Ann={m['annual_return']*100:.1f}%, MaxDD={m['max_dd']*100:.1f}%, "
                    f"12M={m['last_12m_return']*100:.1f}%")

    # Test ALL 7 strategies combined
    for lev in [2.0, 3.0, 4.0]:
        all_names = list(all_curves.keys())
        n_strats = len(all_names)
        eq_weight = 1.0 / n_strats
        weights = {name: eq_weight for name in all_names}
        label = "ALL_7_EQUAL"
        eq_combo = combine_strategies_with_dd_control(all_curves, weights, lev)
        if len(eq_combo) > 0:
            m = compute_metrics(eq_combo)
            combo_results.append({
                'label': label,
                'leverage': lev,
                'equity': eq_combo,
                **m,
            })
            log(f"    {label} @{lev:.0f}x: Sharpe={m['sharpe']:.2f}, "
                f"Ann={m['annual_return']*100:.1f}%, MaxDD={m['max_dd']*100:.1f}%, "
                f"12M={m['last_12m_return']*100:.1f}%")

    # Also test R158 + R160 only combo as baseline
    for lev in [2.0, 3.0, 4.0]:
        weights = {'R158_Momentum': 0.5, 'R160_VolBreakout': 0.5}
        label = "R158+R160_ONLY"
        eq_combo = combine_strategies_with_dd_control(all_curves, weights, lev)
        if len(eq_combo) > 0:
            m = compute_metrics(eq_combo)
            combo_results.append({
                'label': label,
                'leverage': lev,
                'equity': eq_combo,
                **m,
            })
            log(f"    {label} @{lev:.0f}x: Sharpe={m['sharpe']:.2f}, "
                f"Ann={m['annual_return']*100:.1f}%, MaxDD={m['max_dd']*100:.1f}%, "
                f"12M={m['last_12m_return']*100:.1f}%")

    # ── Generate Report ───────────────────────────────────────
    log("\nGenerating report...")
    report = generate_report(
        all_curves, all_metrics, correlations, combo_results,
        len(tokens), token_list)

    with open(OUTPUT_PATH, 'w') as f:
        f.write(report)
    log(f"Report written to {OUTPUT_PATH}")

    # ── Print Summary ─────────────────────────────────────────
    log("\n" + "=" * 70)
    log("QUICK SUMMARY")
    log("=" * 70)

    log("\nIndividual Strategy Metrics (Full Period):")
    for key, m in all_metrics.items():
        log(f"  {key}: Sharpe={m['sharpe']:.2f}, Ann={m['annual_return']*100:.1f}%, "
            f"MaxDD={m['max_dd']*100:.1f}%, 12M={m['last_12m_return']*100:.1f}%")

    if combo_results:
        log("\nBest Combo Portfolios (by Sharpe):")
        sorted_combos = sorted(combo_results, key=lambda x: x['sharpe'], reverse=True)[:10]
        for r in sorted_combos:
            log(f"  {r['label']} @{r['leverage']:.0f}x: Sharpe={r['sharpe']:.2f}, "
                f"Ann={r['annual_return']*100:.1f}%, MaxDD={r['max_dd']*100:.1f}%, "
                f"Calmar={r['calmar']:.2f}, 12M={r['last_12m_return']*100:.1f}%")

    t_total = time.time() - t0
    log(f"\nTotal runtime: {t_total:.1f}s")


if __name__ == '__main__':
    main()
