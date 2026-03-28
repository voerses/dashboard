#!/workspace/venv/bin/python
"""
R161 -- Multi-Strategy Portfolio: R158 Momentum Rotation + R160 Volatility Breakout
====================================================================================

Combines the two best strategies found so far:
  - R158: Cross-Sectional Momentum Rotation (L7, R7, K5, 70/30, regime filter)
  - R160: Volatility Breakout Rotation (Variant B with momentum filter, 1x)

Tests 25 combinations of allocation weights x leverage levels.

Combined daily return:
  combined_return = w_r158 * leverage * r158_daily_return
                  + w_r160 * leverage * r160_daily_return
                  - (leverage - 1) * daily_borrow_rate

Where daily_borrow_rate = 0.05/365.

Date range: 2024-03-17 to 2026-03-17
Last 12 months: 2025-03-17 to 2026-03-17
"""

import sys
import warnings
import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
import time
from itertools import product
import os

warnings.filterwarnings('ignore')

def log(msg):
    print(msg)
    sys.stdout.flush()


# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = PROJECT_DIR / 'data' / 'perp' / '1h_cache'
OUTPUT_MD = PROJECT_DIR / 'research' / 'R161_multi_strategy_results.md'

# ── Date Constants ────────────────────────────────────────────────────────────
SIM_START = pd.Timestamp('2024-03-17')
SIM_END   = pd.Timestamp('2026-03-17')
LAST_12MO_START = pd.Timestamp('2025-03-17')
DATA_CUTOFF = pd.Timestamp('2024-03-17')

DAYS_PER_YEAR = 365
HOURS_PER_YEAR = 8760

# Borrow cost for leverage
ANNUAL_BORROW_RATE = 0.05
DAILY_BORROW_RATE = ANNUAL_BORROW_RATE / 365.0

# Portfolio combos
ALLOCATIONS = [
    (1.0, 0.0),
    (0.8, 0.2),
    (0.7, 0.3),
    (0.6, 0.4),
    (0.5, 0.5),
]
LEVERAGE_LEVELS = [1.0, 1.5, 2.0, 2.5, 3.0]


# ##############################################################################
# R158 — MOMENTUM ROTATION (exact code logic from R158_momentum_rotation.py)
# ##############################################################################

# R158 constants
R158_MIN_AVG_DAILY_VOLUME_USD = 1_000_000
R158_FEE_BPS = 7.0
R158_EMA_FAST_H = 20
R158_EMA_SLOW_H = 50
R158_MAX_LOOKBACK_DAYS = 30

# Best variant params
R158_LOOKBACK = 7
R158_REBALANCE = 7
R158_K = 5
R158_LONG_WT = 0.70
R158_SHORT_WT = 0.30
R158_USE_REGIME_FILTER = True


def r158_load_all_tokens() -> Dict[str, pd.DataFrame]:
    """Load all tokens with data before cutoff and apply volume filter (R158 logic)."""
    files = sorted(os.listdir(DATA_DIR))
    tokens = {}
    for f in files:
        if not f.endswith('_1h.parquet'):
            continue
        ticker = f.replace('_1h.parquet', '')
        path = os.path.join(DATA_DIR, f)
        df = pd.read_parquet(path)
        if df.index.min() >= DATA_CUTOFF:
            continue
        pre_sim = df[df.index < SIM_START]
        if len(pre_sim) < 30 * 24:
            continue
        last_30d = pre_sim.tail(30 * 24)
        daily_dvol = (last_30d['volume'] * last_30d['close']).resample('1D').sum()
        avg_dvol = daily_dvol.mean()
        if avg_dvol < R158_MIN_AVG_DAILY_VOLUME_USD:
            continue
        tokens[ticker] = df
    return tokens


def r158_prepare_daily_data(tokens: Dict[str, pd.DataFrame]):
    """Prepare aligned daily data for R158 (exact same logic)."""
    daily_closes = {}
    daily_volumes = {}
    ema_signals = {}
    hourly_fundings = {}

    for ticker, df in tokens.items():
        daily = df['close'].resample('1D').last().dropna()
        daily_closes[ticker] = daily
        dvol = (df['volume'] * df['close']).resample('1D').sum()
        daily_volumes[ticker] = dvol
        ema_fast = df['close'].ewm(span=R158_EMA_FAST_H, adjust=False).mean()
        ema_slow = df['close'].ewm(span=R158_EMA_SLOW_H, adjust=False).mean()
        ema_diff = (ema_fast - ema_slow).resample('1D').last()
        ema_signals[ticker] = ema_diff
        hourly_fundings[ticker] = df['funding_1h'].fillna(0)

    daily_close = pd.DataFrame(daily_closes)
    daily_volume = pd.DataFrame(daily_volumes)
    ema_signal = pd.DataFrame(ema_signals)

    start_with_buffer = SIM_START - pd.Timedelta(days=R158_MAX_LOOKBACK_DAYS + 10)
    daily_close = daily_close.loc[start_with_buffer:SIM_END]
    daily_volume = daily_volume.reindex(daily_close.index)
    ema_signal = ema_signal.reindex(daily_close.index)

    daily_returns = daily_close.pct_change()

    lookback_returns_shifted = {}
    for L in [R158_LOOKBACK]:
        lb = daily_close.shift(1) / daily_close.shift(1 + L) - 1
        lookback_returns_shifted[L] = lb

    ema_signal_shifted = ema_signal.shift(1)

    rolling_avg_dvol = daily_volume.rolling(30, min_periods=15).mean()
    rolling_avg_dvol_shifted = rolling_avg_dvol.shift(1)

    return (daily_close, daily_returns, lookback_returns_shifted,
            ema_signal_shifted, rolling_avg_dvol_shifted, hourly_fundings)


def r158_run_best_variant(
    daily_close, daily_returns, lookback_returns_shifted,
    ema_signal_shifted, rolling_avg_dvol_shifted, hourly_fundings
) -> pd.Series:
    """Run R158 best variant and return equity curve (exact R158 logic)."""
    lookback_days = R158_LOOKBACK
    rebalance_days = R158_REBALANCE
    k = R158_K
    long_wt = R158_LONG_WT
    short_wt = R158_SHORT_WT
    use_regime_filter = R158_USE_REGIME_FILTER

    sim_dates = daily_close.loc[SIM_START:SIM_END].index
    lb_rets_df = lookback_returns_shifted[lookback_days]
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
        # Apply pending rebalance from yesterday
        if pending_longs is not None:
            new_longs_set = set(pending_longs.keys())
            new_shorts_set = set(pending_shorts.keys())

            long_entries = new_longs_set - prev_longs
            long_exits = prev_longs - new_longs_set
            short_entries = new_shorts_set - prev_shorts
            short_exits = prev_shorts - new_shorts_set

            cost = 0.0
            for t in long_entries:
                cost += pending_longs[t] * (R158_FEE_BPS / 10000)
            for t in long_exits:
                cost += current_longs.get(t, 0) * (R158_FEE_BPS / 10000)
            for t in short_entries:
                cost += pending_shorts[t] * (R158_FEE_BPS / 10000)
            for t in short_exits:
                cost += current_shorts.get(t, 0) * (R158_FEE_BPS / 10000)

            equity *= (1 - cost)
            current_longs = pending_longs
            current_shorts = pending_shorts
            prev_longs = new_longs_set
            prev_shorts = new_shorts_set
            pending_longs = None
            pending_shorts = None

        # Daily PnL from current positions
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

        # Decide rebalance
        if i in rebalance_indices:
            lb_rets = lb_rets_df.loc[date].dropna() if date in lb_rets_df.index else pd.Series(dtype=float)
            vol_at_date = rolling_avg_dvol_shifted.loc[date].dropna() if date in rolling_avg_dvol_shifted.index else pd.Series(dtype=float)
            eligible = vol_at_date[vol_at_date >= R158_MIN_AVG_DAILY_VOLUME_USD].index
            lb_rets = lb_rets[lb_rets.index.isin(eligible)]

            if len(lb_rets) >= 2 * k:
                ranked = lb_rets.sort_values(ascending=False)
                top_k = ranked.head(k).index.tolist()
                bottom_k = ranked.tail(k).index.tolist()

                if use_regime_filter:
                    ema_at_date = ema_signal_shifted.loc[date].dropna() if date in ema_signal_shifted.index else pd.Series(dtype=float)
                    top_k = [t for t in top_k if t in ema_at_date.index and ema_at_date[t] > 0]
                    bottom_k = [t for t in bottom_k if t in ema_at_date.index and ema_at_date[t] < 0]

                n_longs = len(top_k) if len(top_k) > 0 else 1
                n_shorts = len(bottom_k) if len(bottom_k) > 0 else 1

                pending_longs = {t: long_wt / n_longs for t in top_k} if top_k else {}
                pending_shorts = {t: short_wt / n_shorts for t in bottom_k} if bottom_k else {}

    return pd.Series(equity_series)


# ##############################################################################
# R160 — VOLATILITY BREAKOUT (exact code logic from R160_volatility_breakout.py)
# ##############################################################################

# R160 constants
R160_BB_PERIOD = 20
R160_BB_STD_MULT = 2.0
R160_VOL_MULT = 1.5
R160_ATR_PERIOD = 20
R160_SMA_PERIOD = 20
R160_PARTIAL_PROFIT_ATR_MULT = 3.0
R160_PARTIAL_CLOSE_FRAC = 0.5
R160_MAX_HOLD_BARS = 180
R160_MOMENTUM_LOOKBACK_4H = 14 * 6  # 84
R160_TOP_N_BREAKOUTS = 5
R160_MAX_POSITIONS = 10
R160_POSITION_SIZE = 0.20
R160_COST_PER_SIDE = 7 / 10000.0
R160_MIN_DATA_DAYS = 365
R160_MIN_AVG_DOLLAR_VOL = 2_000_000
R160_BARS_4H_PER_YEAR = 8760 / 4


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


@dataclass
class R160Trade:
    token: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: int
    entry_price: float
    exit_price: float
    pnl_pct: float
    bars_held: int
    exit_reason: str


def r160_load_and_resample_4h(token: str) -> Optional[pd.DataFrame]:
    """Load 1H data, resample to 4H, compute indicators (exact R160 logic)."""
    path = DATA_DIR / f'{token}_1h.parquet'
    if not path.exists():
        return None

    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep='first')]

    if (df.index.max() - df.index.min()).days < R160_MIN_DATA_DAYS:
        return None

    df_4h = df.resample('4h').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
    }).dropna(subset=['close'])

    if 'funding_1h' in df.columns:
        funding_4h = df['funding_1h'].resample('4h').sum()
        df_4h['funding_4h'] = funding_4h
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


def _r160_calc_funding(arr: dict, start_idx: int, end_idx: int, direction: int, leverage: float) -> float:
    if start_idx >= end_idx:
        return 0.0
    funding_slice = arr['funding_4h'][start_idx:end_idx]
    total = np.nansum(funding_slice)
    if direction == 1:
        return total * leverage
    else:
        return -total * leverage


def r160_run_portfolio_simulation(token_data: Dict[str, pd.DataFrame], leverage: float = 1.0) -> pd.Series:
    """
    Run R160 Variant B (momentum filter) and return 4H equity curve.
    This is the exact simulation logic from R160, variant='B'.
    """
    fee = R160_COST_PER_SIDE
    variant = 'B'

    all_times = set()
    for token, df in token_data.items():
        all_times.update(df.index.tolist())
    all_times = sorted(all_times)

    if len(all_times) < 100:
        return pd.Series(dtype=float)

    token_arrays = {}
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

    token_time_idx = {}
    for token, arrays in token_arrays.items():
        idx_map = {}
        for i, t in enumerate(arrays['index']):
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

        # 1. PROCESS PENDING ENTRIES
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

                    entry_price = arr['open'][bar_idx]
                    entry_atr = arr['atr'][bar_idx]

                    already_in = any(p.token == token and p.direction == direction for p in positions)
                    if already_in:
                        continue

                    entry_cost = fee * leverage
                    equity -= entry_cost * R160_POSITION_SIZE

                    pos = R160Position(
                        token=token, entry_time=current_time, entry_bar_idx=bar_idx,
                        entry_price=entry_price, direction=direction,
                        size_frac=R160_POSITION_SIZE, leverage=leverage,
                        entry_atr=entry_atr,
                    )
                    positions.append(pos)

            pending_entries = []

        # 2. UPDATE EXISTING POSITIONS
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
                    partial_funding = _r160_calc_funding(arr, pos.entry_bar_idx, bar_idx, pos.direction, leverage)
                    partial_pnl = (partial_return - partial_cost - partial_funding) * pos.size_frac * R160_PARTIAL_CLOSE_FRAC
                    equity += partial_pnl
                    pos.partial_taken = True
                    pos.remaining_frac = 1.0 - R160_PARTIAL_CLOSE_FRAC
                    pos.breakeven_stop = True
                elif pos.direction == -1 and close_price <= target:
                    partial_return = (1.0 - close_price / pos.entry_price) * leverage
                    partial_cost = fee * leverage
                    partial_funding = _r160_calc_funding(arr, pos.entry_bar_idx, bar_idx, pos.direction, leverage)
                    partial_pnl = (partial_return - partial_cost - partial_funding) * pos.size_frac * R160_PARTIAL_CLOSE_FRAC
                    equity += partial_pnl
                    pos.partial_taken = True
                    pos.remaining_frac = 1.0 - R160_PARTIAL_CLOSE_FRAC
                    pos.breakeven_stop = True

            # Trail stop
            if pos.direction == 1 and close_price < sma_val:
                exit_reason = 'trail_stop'
                exit_price = close_price
            elif pos.direction == -1 and close_price > sma_val:
                exit_reason = 'trail_stop'
                exit_price = close_price

            # Breakeven stop
            if exit_reason is None and pos.breakeven_stop:
                if pos.direction == 1 and close_price < pos.entry_price:
                    exit_reason = 'trail_stop'
                    exit_price = close_price
                elif pos.direction == -1 and close_price > pos.entry_price:
                    exit_reason = 'trail_stop'
                    exit_price = close_price

            # Max hold
            if exit_reason is None and pos.bars_held >= R160_MAX_HOLD_BARS:
                exit_reason = 'max_hold'
                exit_price = close_price

            # Opposite breakout
            if exit_reason is None:
                if pos.direction == 1 and bar_idx < len(arr['breakout_short']) and arr['breakout_short'][bar_idx]:
                    exit_reason = 'opposite_breakout'
                    exit_price = close_price
                elif pos.direction == -1 and bar_idx < len(arr['breakout_long']) and arr['breakout_long'][bar_idx]:
                    exit_reason = 'opposite_breakout'
                    exit_price = close_price

            if exit_reason is not None:
                if pos.direction == 1:
                    raw_return = (exit_price / pos.entry_price - 1.0)
                else:
                    raw_return = (1.0 - exit_price / pos.entry_price)

                levered_return = raw_return * leverage
                exit_cost = fee * leverage
                funding_cost = _r160_calc_funding(arr, pos.entry_bar_idx, bar_idx, pos.direction, leverage)
                effective_size = pos.size_frac * pos.remaining_frac
                pnl = (levered_return - exit_cost - funding_cost) * effective_size
                equity += pnl
                closed_indices.append(pidx)

        for pidx in sorted(closed_indices, reverse=True):
            positions.pop(pidx)

        # 3. SCAN FOR NEW BREAKOUTS (Variant B)
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
                # Variant B momentum filter
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
                # Variant B momentum filter
                if bar_idx < R160_MOMENTUM_LOOKBACK_4H:
                    continue
                ret_14d = arr['return_14d'][bar_idx]
                if np.isnan(ret_14d) or ret_14d >= 0:
                    continue
                new_candidates.append((token, -1, strength))

        pending_entries = new_candidates
        equity_curve[current_time] = equity

    # Close remaining positions at end
    last_time = all_times[-1]
    for pos in positions:
        token = pos.token
        if token in token_time_idx and last_time in token_time_idx[token]:
            bar_idx = token_time_idx[token][last_time]
            arr = token_arrays[token]
            exit_price = arr['close'][bar_idx]
        else:
            arr = token_arrays[token]
            exit_price = arr['close'][-1]
            bar_idx = len(arr['close']) - 1

        if pos.direction == 1:
            raw_return = (exit_price / pos.entry_price - 1.0)
        else:
            raw_return = (1.0 - exit_price / pos.entry_price)

        levered_return = raw_return * leverage
        exit_cost = fee * leverage
        funding_cost = _r160_calc_funding(arr, pos.entry_bar_idx, min(bar_idx, len(arr['funding_4h']) - 1),
                                          pos.direction, leverage)
        effective_size = pos.size_frac * pos.remaining_frac
        pnl = (levered_return - exit_cost - funding_cost) * effective_size
        equity += pnl

    equity_curve[last_time] = equity
    return pd.Series(equity_curve).sort_index()


# ##############################################################################
# PORTFOLIO COMBINATION AND METRICS
# ##############################################################################

def equity_to_daily_returns(equity: pd.Series) -> pd.Series:
    """Convert an equity curve (possibly sub-daily) to daily returns."""
    daily_eq = equity.resample('1D').last().dropna()
    daily_rets = daily_eq.pct_change().dropna()
    return daily_rets


def compute_combined_metrics(daily_returns: pd.Series, label: str = '') -> dict:
    """Compute metrics from a daily returns series."""
    if len(daily_returns) < 30:
        return {
            'label': label,
            'annual_return': 0.0, 'sharpe': 0.0, 'max_dd': 0.0,
            'calmar': 0.0, 'sortino': 0.0,
            'last_12m_return': 0.0, 'last_12m_sharpe': 0.0, 'last_12m_max_dd': 0.0,
        }

    # Build equity from returns
    equity = (1 + daily_returns).cumprod()

    total_return = equity.iloc[-1] / equity.iloc[0] - 1
    n_days = (equity.index[-1] - equity.index[0]).days
    if n_days < 1:
        n_days = 1
    annual_return = (1 + total_return) ** (DAYS_PER_YEAR / n_days) - 1

    running_max = equity.cummax()
    drawdowns = equity / running_max - 1
    max_dd = drawdowns.min()

    mean_daily = daily_returns.mean()
    std_daily = daily_returns.std()
    sharpe = (mean_daily / std_daily * np.sqrt(DAYS_PER_YEAR)) if std_daily > 0 else 0.0

    calmar = annual_return / abs(max_dd) if max_dd != 0 else 0.0

    downside = daily_returns[daily_returns < 0]
    downside_std = downside.std() if len(downside) > 0 else 1e-10
    sortino = (mean_daily / downside_std * np.sqrt(DAYS_PER_YEAR)) if downside_std > 0 else 0.0

    # Last 12 months
    l12m_rets = daily_returns[daily_returns.index >= LAST_12MO_START]
    if len(l12m_rets) > 10:
        l12m_eq = (1 + l12m_rets).cumprod()
        l12m_return = l12m_eq.iloc[-1] - 1.0
        l12m_mean = l12m_rets.mean()
        l12m_std = l12m_rets.std()
        l12m_sharpe = (l12m_mean / l12m_std * np.sqrt(DAYS_PER_YEAR)) if l12m_std > 0 else 0.0
        l12m_maxdd = (l12m_eq / l12m_eq.cummax() - 1).min()
    else:
        l12m_return = 0.0
        l12m_sharpe = 0.0
        l12m_maxdd = 0.0

    return {
        'label': label,
        'annual_return': annual_return,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'calmar': calmar,
        'sortino': sortino,
        'last_12m_return': l12m_return,
        'last_12m_sharpe': l12m_sharpe,
        'last_12m_max_dd': l12m_maxdd,
    }


def monthly_returns_table(daily_returns: pd.Series) -> str:
    """Generate a monthly returns table as markdown from daily returns."""
    equity = (1 + daily_returns).cumprod()
    monthly_eq = equity.resample('ME').last().dropna()
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


# ##############################################################################
# MAIN
# ##############################################################################

def main():
    t0 = time.time()
    log("=" * 70)
    log("R161 -- Multi-Strategy Portfolio: R158 + R160")
    log("=" * 70)

    # ── Step 1: Run R158 best variant ─────────────────────────────────────────
    log("\n[1/5] Running R158 (Momentum Rotation: L7, R7, K5, 70/30, RF)...")
    t1 = time.time()

    r158_tokens = r158_load_all_tokens()
    log(f"  R158: Loaded {len(r158_tokens)} tokens")

    (r158_daily_close, r158_daily_returns, r158_lb_shifted,
     r158_ema_shifted, r158_dvol_shifted, r158_fundings) = r158_prepare_daily_data(r158_tokens)
    log(f"  R158: Daily close shape: {r158_daily_close.shape}")

    r158_equity = r158_run_best_variant(
        r158_daily_close, r158_daily_returns, r158_lb_shifted,
        r158_ema_shifted, r158_dvol_shifted, r158_fundings
    )
    log(f"  R158: Equity curve length: {len(r158_equity)}")
    log(f"  R158: Final equity: {r158_equity.iloc[-1]:.4f} (total return: {(r158_equity.iloc[-1]/r158_equity.iloc[0]-1)*100:.1f}%)")
    log(f"  R158: Took {time.time()-t1:.1f}s")

    # ── Step 2: Run R160 best variant (Variant B, 1x) ────────────────────────
    log("\n[2/5] Running R160 (Volatility Breakout Variant B, 1x)...")
    t2 = time.time()

    # Discover and load tokens
    all_r160_tokens = []
    for f in sorted(DATA_DIR.glob('*_1h.parquet')):
        token = f.stem.replace('_1h', '')
        try:
            df = pd.read_parquet(f, columns=['close'])
            df.index = pd.to_datetime(df.index)
            duration_days = (df.index.max() - df.index.min()).days
            if duration_days >= R160_MIN_DATA_DAYS:
                all_r160_tokens.append(token)
        except Exception:
            pass
    log(f"  R160: Found {len(all_r160_tokens)} tokens with >1yr data")

    r160_token_data = {}
    for i, token in enumerate(all_r160_tokens):
        if (i + 1) % 20 == 0:
            log(f"  R160: Loading {i+1}/{len(all_r160_tokens)}...")
        df_4h = r160_load_and_resample_4h(token)
        if df_4h is not None:
            r160_token_data[token] = df_4h

    # Volume filter
    r160_filtered = {}
    for token, df_4h in r160_token_data.items():
        valid_vol = df_4h['avg_daily_dollar_vol'].dropna()
        if len(valid_vol) > 0 and valid_vol.iloc[-1] >= R160_MIN_AVG_DOLLAR_VOL:
            r160_filtered[token] = df_4h
    log(f"  R160: {len(r160_filtered)} tokens pass volume filter")

    r160_equity = r160_run_portfolio_simulation(r160_filtered, leverage=1.0)
    log(f"  R160: Equity curve length: {len(r160_equity)}")
    log(f"  R160: Final equity: {r160_equity.iloc[-1]:.4f} (total return: {(r160_equity.iloc[-1]/r160_equity.iloc[0]-1)*100:.1f}%)")
    log(f"  R160: Took {time.time()-t2:.1f}s")

    # ── Step 3: Compute daily returns and correlation ─────────────────────────
    log("\n[3/5] Computing daily returns and correlation...")

    r158_daily_rets = equity_to_daily_returns(r158_equity)
    r160_daily_rets = equity_to_daily_returns(r160_equity)

    # Align the two series to same dates
    combined_idx = r158_daily_rets.index.intersection(r160_daily_rets.index)
    # Further filter to sim period
    combined_idx = combined_idx[(combined_idx >= SIM_START) & (combined_idx <= SIM_END)]
    r158_aligned = r158_daily_rets.reindex(combined_idx).fillna(0)
    r160_aligned = r160_daily_rets.reindex(combined_idx).fillna(0)

    correlation = r158_aligned.corr(r160_aligned)
    log(f"  Aligned {len(combined_idx)} trading days")
    log(f"  R158 vs R160 daily return correlation: {correlation:.4f}")

    # Last 12m correlation
    l12m_mask = combined_idx >= LAST_12MO_START
    if l12m_mask.sum() > 10:
        l12m_corr = r158_aligned[l12m_mask].corr(r160_aligned[l12m_mask])
        log(f"  Last 12M correlation: {l12m_corr:.4f}")
    else:
        l12m_corr = np.nan

    # Individual strategy metrics
    r158_metrics = compute_combined_metrics(r158_aligned, 'R158 (1x)')
    r160_metrics = compute_combined_metrics(r160_aligned, 'R160 (1x)')
    log(f"\n  R158: AnnRet={r158_metrics['annual_return']*100:.1f}%, Sharpe={r158_metrics['sharpe']:.2f}, "
        f"MaxDD={r158_metrics['max_dd']*100:.1f}%, 12M={r158_metrics['last_12m_return']*100:.1f}%")
    log(f"  R160: AnnRet={r160_metrics['annual_return']*100:.1f}%, Sharpe={r160_metrics['sharpe']:.2f}, "
        f"MaxDD={r160_metrics['max_dd']*100:.1f}%, 12M={r160_metrics['last_12m_return']*100:.1f}%")

    # ── Step 4: Test 25 portfolio combinations ────────────────────────────────
    log("\n[4/5] Testing 25 portfolio combinations (5 allocations x 5 leverage)...")

    all_results = []
    for (w_r158, w_r160), lev in product(ALLOCATIONS, LEVERAGE_LEVELS):
        # Combined daily return
        combined_daily = (
            w_r158 * lev * r158_aligned
            + w_r160 * lev * r160_aligned
            - (lev - 1) * DAILY_BORROW_RATE
        )

        label = f"R158={int(w_r158*100)}%_R160={int(w_r160*100)}%_Lev={lev:.1f}x"
        metrics = compute_combined_metrics(combined_daily, label)
        metrics['w_r158'] = w_r158
        metrics['w_r160'] = w_r160
        metrics['leverage'] = lev
        metrics['daily_returns'] = combined_daily  # store for later use
        all_results.append(metrics)

    log(f"  Computed metrics for {len(all_results)} combinations")

    # ── Step 5: Generate report ───────────────────────────────────────────────
    log("\n[5/5] Generating report...")

    lines = []
    lines.append("# R161 -- Multi-Strategy Portfolio Results")
    lines.append("")
    lines.append(f"**Generated:** {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Simulation Period:** {SIM_START.date()} to {SIM_END.date()} (~2 years)")
    lines.append(f"**Last 12 Months:** {LAST_12MO_START.date()} to {SIM_END.date()}")
    lines.append(f"**Borrow cost:** {ANNUAL_BORROW_RATE*100:.0f}% annual ({DAILY_BORROW_RATE*10000:.2f} bps/day)")
    lines.append("")
    lines.append("## Component Strategies")
    lines.append("")
    lines.append("| Strategy | Description | Variant |")
    lines.append("|----------|-------------|---------|")
    lines.append("| R158 | Cross-sectional momentum rotation | L7, R7, K5, 70/30, regime filter |")
    lines.append("| R160 | Volatility breakout rotation | Variant B (momentum filter), 1x leverage |")
    lines.append("")

    # ── Individual strategy metrics ──
    lines.append("## Individual Strategy Metrics (1x, no borrow)")
    lines.append("")
    lines.append("| Metric | R158 | R160 |")
    lines.append("|--------|------|------|")
    lines.append(f"| Annual Return | {r158_metrics['annual_return']*100:.1f}% | {r160_metrics['annual_return']*100:.1f}% |")
    lines.append(f"| Sharpe Ratio | {r158_metrics['sharpe']:.2f} | {r160_metrics['sharpe']:.2f} |")
    lines.append(f"| Max Drawdown | {r158_metrics['max_dd']*100:.1f}% | {r160_metrics['max_dd']*100:.1f}% |")
    lines.append(f"| Calmar Ratio | {r158_metrics['calmar']:.2f} | {r160_metrics['calmar']:.2f} |")
    lines.append(f"| Sortino Ratio | {r158_metrics['sortino']:.2f} | {r160_metrics['sortino']:.2f} |")
    lines.append(f"| Last 12M Return | {r158_metrics['last_12m_return']*100:.1f}% | {r160_metrics['last_12m_return']*100:.1f}% |")
    lines.append(f"| Last 12M Sharpe | {r158_metrics['last_12m_sharpe']:.2f} | {r160_metrics['last_12m_sharpe']:.2f} |")
    lines.append(f"| Last 12M MaxDD | {r158_metrics['last_12m_max_dd']*100:.1f}% | {r160_metrics['last_12m_max_dd']*100:.1f}% |")
    lines.append("")

    # ── Correlation matrix ──
    lines.append("## Daily Return Correlation")
    lines.append("")
    lines.append("| | R158 | R160 |")
    lines.append("|--|------|------|")
    lines.append(f"| R158 | 1.000 | {correlation:.4f} |")
    lines.append(f"| R160 | {correlation:.4f} | 1.000 |")
    lines.append("")
    lines.append(f"**Full period correlation:** {correlation:.4f}")
    if not np.isnan(l12m_corr):
        lines.append(f"**Last 12 months correlation:** {l12m_corr:.4f}")
    lines.append("")
    if correlation < 0.3:
        lines.append("Correlation is LOW -- strong diversification benefit expected.")
    elif correlation < 0.6:
        lines.append("Correlation is MODERATE -- some diversification benefit.")
    else:
        lines.append("Correlation is HIGH -- limited diversification benefit.")
    lines.append("")

    # ── All 25 combinations sorted by last 12mo return ──
    lines.append("---")
    lines.append("")
    lines.append("## All 25 Combinations (sorted by Last 12M Return)")
    lines.append("")
    lines.append("| # | Allocation | Lev | Ann Ret | Sharpe | MaxDD | Calmar | Sortino | 12M Ret | 12M Sharpe | 12M MaxDD | 300%+ |")
    lines.append("|---|------------|-----|---------|--------|-------|--------|---------|---------|------------|-----------|-------|")

    sorted_results = sorted(all_results, key=lambda x: x['last_12m_return'], reverse=True)
    for rank, r in enumerate(sorted_results, 1):
        alloc = f"{int(r['w_r158']*100)}/{int(r['w_r160']*100)}"
        flag_300 = "YES" if r['last_12m_return'] >= 3.0 else ""
        lines.append(
            f"| {rank} | {alloc} | {r['leverage']:.1f}x "
            f"| {r['annual_return']*100:.1f}% "
            f"| {r['sharpe']:.2f} "
            f"| {r['max_dd']*100:.1f}% "
            f"| {r['calmar']:.2f} "
            f"| {r['sortino']:.2f} "
            f"| **{r['last_12m_return']*100:.1f}%** "
            f"| {r['last_12m_sharpe']:.2f} "
            f"| {r['last_12m_max_dd']*100:.1f}% "
            f"| {flag_300} |"
        )
    lines.append("")

    # ── Highlight 300%+ combos ──
    combos_300 = [r for r in sorted_results if r['last_12m_return'] >= 3.0]
    lines.append("## Combos Achieving 300%+ Last 12M Return")
    lines.append("")
    if combos_300:
        lines.append(f"**{len(combos_300)} combinations** achieve 300%+ last 12-month return:")
        lines.append("")
        lines.append("| Allocation | Leverage | 12M Return | Ann Return | Sharpe | MaxDD | Calmar |")
        lines.append("|------------|----------|------------|------------|--------|-------|--------|")
        for r in combos_300:
            alloc = f"{int(r['w_r158']*100)}/{int(r['w_r160']*100)}"
            lines.append(
                f"| {alloc} | {r['leverage']:.1f}x "
                f"| {r['last_12m_return']*100:.1f}% "
                f"| {r['annual_return']*100:.1f}% "
                f"| {r['sharpe']:.2f} "
                f"| {r['max_dd']*100:.1f}% "
                f"| {r['calmar']:.2f} |"
            )
        lines.append("")

        # Monthly returns for the best 300%+ combo
        best_300 = combos_300[0]
        lines.append(f"### Monthly Returns: Best 300%+ Combo")
        lines.append(f"**{best_300['label']}** (12M Return: {best_300['last_12m_return']*100:.1f}%)")
        lines.append("")
        lines.append(monthly_returns_table(best_300['daily_returns']))
        lines.append("")
    else:
        lines.append("**No combinations achieve 300%+ last 12-month return.**")
        lines.append("")
        # Show monthly returns for the best combo anyway
        best = sorted_results[0]
        lines.append(f"### Monthly Returns: Best Combo")
        lines.append(f"**{best['label']}** (12M Return: {best['last_12m_return']*100:.1f}%)")
        lines.append("")
        lines.append(monthly_returns_table(best['daily_returns']))
        lines.append("")

    # ── MaxDD analysis: best return per unit of MaxDD ──
    lines.append("---")
    lines.append("")
    lines.append("## MaxDD Analysis: Best Return per Unit of Drawdown")
    lines.append("")
    lines.append("Sorted by Calmar ratio (Annual Return / |MaxDD|):")
    lines.append("")
    lines.append("| # | Allocation | Lev | Ann Ret | MaxDD | Calmar | 12M Ret | 12M MaxDD | Sharpe |")
    lines.append("|---|------------|-----|---------|-------|--------|---------|-----------|--------|")

    by_calmar = sorted(all_results, key=lambda x: x['calmar'], reverse=True)
    for rank, r in enumerate(by_calmar[:10], 1):
        alloc = f"{int(r['w_r158']*100)}/{int(r['w_r160']*100)}"
        lines.append(
            f"| {rank} | {alloc} | {r['leverage']:.1f}x "
            f"| {r['annual_return']*100:.1f}% "
            f"| {r['max_dd']*100:.1f}% "
            f"| **{r['calmar']:.2f}** "
            f"| {r['last_12m_return']*100:.1f}% "
            f"| {r['last_12m_max_dd']*100:.1f}% "
            f"| {r['sharpe']:.2f} |"
        )
    lines.append("")

    # Also show best by last_12m_return / |last_12m_max_dd|
    lines.append("### Best Last-12M Return per Unit of Last-12M MaxDD")
    lines.append("")
    lines.append("| # | Allocation | Lev | 12M Ret | 12M MaxDD | 12M Ret/|MaxDD| | 12M Sharpe | Full Sharpe |")
    lines.append("|---|------------|-----|---------|-----------|-----------------|------------|-------------|")

    for r in all_results:
        r['l12m_calmar'] = (r['last_12m_return'] / abs(r['last_12m_max_dd'])) if r['last_12m_max_dd'] != 0 else 0.0

    by_l12m_calmar = sorted(all_results, key=lambda x: x['l12m_calmar'], reverse=True)
    for rank, r in enumerate(by_l12m_calmar[:10], 1):
        alloc = f"{int(r['w_r158']*100)}/{int(r['w_r160']*100)}"
        lines.append(
            f"| {rank} | {alloc} | {r['leverage']:.1f}x "
            f"| {r['last_12m_return']*100:.1f}% "
            f"| {r['last_12m_max_dd']*100:.1f}% "
            f"| **{r['l12m_calmar']:.2f}** "
            f"| {r['last_12m_sharpe']:.2f} "
            f"| {r['sharpe']:.2f} |"
        )
    lines.append("")

    # ── Observations ──
    lines.append("---")
    lines.append("")
    lines.append("## Key Observations")
    lines.append("")

    best_12m = sorted_results[0]
    best_sharpe_r = sorted(all_results, key=lambda x: x['sharpe'], reverse=True)[0]
    best_calmar_r = by_calmar[0]

    lines.append(f"1. **Correlation** between R158 and R160 daily returns: **{correlation:.4f}** -- "
                 f"{'low correlation enables meaningful diversification' if correlation < 0.3 else 'moderate correlation limits diversification benefits' if correlation < 0.6 else 'high correlation limits diversification benefits'}.")
    lines.append("")
    lines.append(f"2. **Best by last 12M return:** {best_12m['label']} "
                 f"({best_12m['last_12m_return']*100:.1f}% return, {best_12m['max_dd']*100:.1f}% MaxDD)")
    lines.append("")
    lines.append(f"3. **Best by Sharpe:** {best_sharpe_r['label']} "
                 f"(Sharpe={best_sharpe_r['sharpe']:.2f}, Ann={best_sharpe_r['annual_return']*100:.1f}%)")
    lines.append("")
    lines.append(f"4. **Best by Calmar (return/DD):** {best_calmar_r['label']} "
                 f"(Calmar={best_calmar_r['calmar']:.2f}, Ann={best_calmar_r['annual_return']*100:.1f}%, DD={best_calmar_r['max_dd']*100:.1f}%)")
    lines.append("")

    n_300 = len(combos_300)
    if n_300 > 0:
        lines.append(f"5. **{n_300} combinations achieve 300%+ last 12M return.** Minimum leverage needed: {min(r['leverage'] for r in combos_300):.1f}x")
    else:
        lines.append("5. **No combinations achieve 300%+ last 12M return** at these allocation/leverage levels.")
    lines.append("")

    # Write report
    report_text = "\n".join(lines)
    with open(OUTPUT_MD, 'w') as f:
        f.write(report_text)
    log(f"\nReport written to {OUTPUT_MD}")

    # Print summary
    log("\n" + "=" * 70)
    log("QUICK SUMMARY")
    log("=" * 70)
    log(f"\nCorrelation: {correlation:.4f}")
    log(f"\nTop 5 by last 12M return:")
    for r in sorted_results[:5]:
        alloc = f"{int(r['w_r158']*100)}/{int(r['w_r160']*100)}"
        log(f"  {alloc} @ {r['leverage']:.1f}x: 12M={r['last_12m_return']*100:.1f}%, "
            f"Ann={r['annual_return']*100:.1f}%, Sharpe={r['sharpe']:.2f}, MaxDD={r['max_dd']*100:.1f}%")

    log(f"\nTop 5 by Sharpe:")
    by_sharpe = sorted(all_results, key=lambda x: x['sharpe'], reverse=True)
    for r in by_sharpe[:5]:
        alloc = f"{int(r['w_r158']*100)}/{int(r['w_r160']*100)}"
        log(f"  {alloc} @ {r['leverage']:.1f}x: Sharpe={r['sharpe']:.2f}, "
            f"Ann={r['annual_return']*100:.1f}%, 12M={r['last_12m_return']*100:.1f}%, MaxDD={r['max_dd']*100:.1f}%")

    log(f"\n300%+ combos: {n_300}")
    log(f"Total runtime: {time.time()-t0:.1f}s")


if __name__ == '__main__':
    main()
