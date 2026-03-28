#!/workspace/venv/bin/python
"""
R164 -- THE FINAL Multi-Strategy Portfolio
============================================

Combines the best findings from R158-R163:

1. R162 Optimized Momentum Rotation (best variant: L7_K3_80/20_EMA10/30_VF)
   - Triple filter: momentum rank L7 + EMA10/30 regime + ATR volatility filter
   - K=3 concentrated, weekly rebalance
   - ~298% last 12 months at 1x

2. R160 Volatility Breakout Variant B
   - BB breakout + volume + momentum filter on 4H bars
   - ~30% last 12 months, Sharpe 2.20, MaxDD -7.5%
   - Very low correlation with momentum (0.028)

3. R163 MoM adaptive leverage concept
   - Scale leverage based on trailing 14-day return

Portfolio Configurations:
  A: R162 alone + MoM leverage (aggressive)
  B: R162 (70%) + R160 (30%) + static 1.5x leverage
  C: R162 (60%) + R160 (40%) + MoM leverage on combined
  D: R162 (50%) + R160 (50%) + MoM leverage on combined
  E: R162 alone at static 1.1x
  F: R162 alone + conservative MoM leverage

Date range: 2024-03-17 to 2026-03-17. Last 12 months from 2025-03-17.
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

warnings.filterwarnings('ignore')

def log(msg):
    print(msg)
    sys.stdout.flush()

# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

PROJECT_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = PROJECT_DIR / 'data' / 'perp' / '1h_cache'
OUTPUT_MD = PROJECT_DIR / 'research' / 'R164_final_portfolio_results.md'

# Date constraints
DATA_CUTOFF = pd.Timestamp('2024-03-17')
SIM_START = pd.Timestamp('2024-03-17')
SIM_END = pd.Timestamp('2026-03-17')
LAST_12MO_START = pd.Timestamp('2025-03-17')

# Shared constants
HOURS_PER_YEAR = 8760
DAYS_PER_YEAR = 365
FEE_BPS = 7.0

# ---- R162 constants ----
R162_MIN_AVG_DAILY_VOLUME_USD = 1_000_000
R162_MAX_LOOKBACK_DAYS = 30

# Best R162 variant parameters
R162_LOOKBACK = 7
R162_REBALANCE = 7  # weekly
R162_K = 3
R162_LONG_WT = 0.80
R162_SHORT_WT = 0.20
R162_EMA_FAST = 10
R162_EMA_SLOW = 30
R162_VOL_FILTER = True

# ---- R160 constants ----
R160_BB_PERIOD = 20
R160_BB_STD_MULT = 2.0
R160_VOL_MULT = 1.5
R160_ATR_PERIOD = 20
R160_SMA_PERIOD = 20
R160_PARTIAL_PROFIT_ATR_MULT = 3.0
R160_PARTIAL_CLOSE_FRAC = 0.5
R160_MAX_HOLD_BARS = 180
R160_MOMENTUM_LOOKBACK_4H = 14 * 6  # 84 4H bars = 14 days
R160_TOP_N_BREAKOUTS = 5
R160_MAX_POSITIONS = 10
R160_POSITION_SIZE = 0.20
R160_COST_PER_SIDE = FEE_BPS / 10000.0
R160_MIN_DATA_DAYS = 365
R160_MIN_AVG_DOLLAR_VOL = 2_000_000
R160_BARS_4H_PER_YEAR = HOURS_PER_YEAR / 4

# MoM leverage thresholds (aggressive)
MOM_THRESHOLDS_AGGRESSIVE = [
    (0.05, 3.0),   # trailing 14d ret > 5% -> 3x
    (0.00, 2.0),   # 0-5% -> 2x
    (-0.05, 1.0),  # -5% to 0% -> 1x
    (None, 0.5),   # < -5% -> 0.5x
]

# MoM leverage thresholds (conservative)
MOM_THRESHOLDS_CONSERVATIVE = [
    (0.05, 2.0),   # trailing 14d ret > 5% -> 2x
    (0.00, 1.5),   # 0-5% -> 1.5x
    (-0.05, 1.0),  # -5% to 0% -> 1x
    (None, 0.5),   # < -5% -> 0.5x
]

BORROW_RATE_ANNUAL = 0.05  # 5% annual on leveraged portion


# ══════════════════════════════════════════════════════════════════════════════
# PART 1: R162 MOMENTUM ROTATION (Best Variant)
# ══════════════════════════════════════════════════════════════════════════════

def load_r162_tokens() -> Dict[str, pd.DataFrame]:
    """Load all tokens for R162 (same as R162 code)."""
    import os
    files = sorted(os.listdir(str(DATA_DIR)))
    tokens = {}
    for f in files:
        if not f.endswith('_1h.parquet'):
            continue
        ticker = f.replace('_1h.parquet', '')
        path = DATA_DIR / f
        df = pd.read_parquet(str(path))
        if df.index.min() >= DATA_CUTOFF:
            continue
        pre_sim = df[df.index < SIM_START]
        if len(pre_sim) < 30 * 24:
            continue
        last_30d = pre_sim.tail(30 * 24)
        daily_dvol = (last_30d['volume'] * last_30d['close']).resample('1D').sum()
        avg_dvol = daily_dvol.mean()
        if avg_dvol < R162_MIN_AVG_DAILY_VOLUME_USD:
            continue
        tokens[ticker] = df
    return tokens


def prepare_r162_data(tokens: Dict[str, pd.DataFrame]):
    """Prepare R162 data (same as R162 code)."""
    ema_params_list = [(R162_EMA_FAST, R162_EMA_SLOW)]
    daily_closes = {}
    daily_volumes = {}
    ema_signals_by_params = {params: {} for params in ema_params_list}
    hourly_fundings = {}

    for ticker, df in tokens.items():
        daily = df['close'].resample('1D').last().dropna()
        daily_closes[ticker] = daily
        dvol = (df['volume'] * df['close']).resample('1D').sum()
        daily_volumes[ticker] = dvol
        for (fast_h, slow_h) in ema_params_list:
            ema_fast = df['close'].ewm(span=fast_h, adjust=False).mean()
            ema_slow = df['close'].ewm(span=slow_h, adjust=False).mean()
            ema_diff = (ema_fast - ema_slow).resample('1D').last()
            ema_signals_by_params[(fast_h, slow_h)][ticker] = ema_diff
        hourly_fundings[ticker] = df['funding_1h'].fillna(0)

    daily_close = pd.DataFrame(daily_closes)
    daily_volume = pd.DataFrame(daily_volumes)

    ema_signals_shifted = {}
    for params in ema_params_list:
        ema_df = pd.DataFrame(ema_signals_by_params[params])
        start_with_buffer = SIM_START - pd.Timedelta(days=R162_MAX_LOOKBACK_DAYS + 10)
        ema_df = ema_df.reindex(daily_close.loc[start_with_buffer:SIM_END].index)
        ema_signals_shifted[params] = ema_df.shift(1)

    start_with_buffer = SIM_START - pd.Timedelta(days=R162_MAX_LOOKBACK_DAYS + 10)
    daily_close = daily_close.loc[start_with_buffer:SIM_END]
    daily_volume = daily_volume.reindex(daily_close.index)
    daily_returns = daily_close.pct_change()

    lb = daily_close.shift(1) / daily_close.shift(1 + R162_LOOKBACK) - 1
    lookback_returns_shifted = {R162_LOOKBACK: lb}

    rolling_avg_dvol = daily_volume.rolling(30, min_periods=15).mean()
    rolling_avg_dvol_shifted = rolling_avg_dvol.shift(1)

    # ATR/price ratio for volatility filter
    daily_highs = {}
    daily_lows_dict = {}
    for ticker, df in tokens.items():
        daily_highs[ticker] = df['high'].resample('1D').max().dropna()
        daily_lows_dict[ticker] = df['low'].resample('1D').min().dropna()

    df_high = pd.DataFrame(daily_highs).reindex(daily_close.index)
    df_low = pd.DataFrame(daily_lows_dict).reindex(daily_close.index)
    df_close_prev = daily_close.shift(1)

    tr1 = df_high - df_low
    tr2 = (df_high - df_close_prev).abs()
    tr3 = (df_low - df_close_prev).abs()
    true_range = pd.concat([tr1, tr2, tr3]).groupby(level=0).max()
    true_range = true_range.reindex(daily_close.index)
    atr_14 = true_range.rolling(14, min_periods=7).mean()
    atr_ratio = atr_14 / daily_close
    atr_ratio_shifted = atr_ratio.shift(1)

    return (daily_close, daily_returns, lookback_returns_shifted,
            ema_signals_shifted, rolling_avg_dvol_shifted, hourly_fundings,
            atr_ratio_shifted)


def run_r162_best_variant(daily_close, daily_returns, lookback_returns_shifted,
                          ema_signals_shifted, rolling_avg_dvol_shifted,
                          hourly_fundings, atr_ratio_shifted) -> pd.Series:
    """
    Run the best R162 variant: L7_K3_80/20_EMA10/30_VF.
    Returns daily equity curve starting at 1.0.
    """
    ema_signal = ema_signals_shifted[(R162_EMA_FAST, R162_EMA_SLOW)]
    sim_dates = daily_close.loc[SIM_START:SIM_END].index

    lb_rets_df = lookback_returns_shifted[R162_LOOKBACK]
    rebalance_indices = set(range(0, len(sim_dates), R162_REBALANCE))

    equity = 1.0
    equity_series = {}
    current_longs = {}
    current_shorts = {}
    prev_longs = set()
    prev_shorts = set()
    trade_count = 0
    total_cost = 0.0
    total_funding = 0.0
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
            n_trades = len(long_entries) + len(long_exits) + len(short_entries) + len(short_exits)
            trade_count += n_trades

            cost = 0.0
            for t in long_entries:
                cost += pending_longs[t] * (FEE_BPS / 10000)
            for t in long_exits:
                cost += current_longs.get(t, 0) * (FEE_BPS / 10000)
            for t in short_entries:
                cost += pending_shorts[t] * (FEE_BPS / 10000)
            for t in short_exits:
                cost += current_shorts.get(t, 0) * (FEE_BPS / 10000)

            total_cost += cost * equity
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

        # Funding
        funding_start = date
        funding_end = date + pd.Timedelta(hours=23)

        for ticker, weight in current_longs.items():
            if ticker in hourly_fundings:
                f_slice = hourly_fundings[ticker].loc[funding_start:funding_end]
                if len(f_slice) > 0:
                    f_pnl = -f_slice.sum() * weight
                    daily_pnl += f_pnl
                    total_funding += f_pnl * equity

        for ticker, weight in current_shorts.items():
            if ticker in hourly_fundings:
                f_slice = hourly_fundings[ticker].loc[funding_start:funding_end]
                if len(f_slice) > 0:
                    f_pnl = f_slice.sum() * weight
                    daily_pnl += f_pnl
                    total_funding += f_pnl * equity

        equity *= (1 + daily_pnl)
        equity_series[date] = equity

        # Decide rebalance
        if i in rebalance_indices:
            lb_rets = lb_rets_df.loc[date].dropna() if date in lb_rets_df.index else pd.Series(dtype=float)
            vol_at_date = rolling_avg_dvol_shifted.loc[date].dropna() if date in rolling_avg_dvol_shifted.index else pd.Series(dtype=float)
            eligible = vol_at_date[vol_at_date >= R162_MIN_AVG_DAILY_VOLUME_USD].index
            lb_rets = lb_rets[lb_rets.index.isin(eligible)]

            # Volatility filter
            if R162_VOL_FILTER and date in atr_ratio_shifted.index:
                atr_at_date = atr_ratio_shifted.loc[date].dropna()
                atr_eligible = atr_at_date[atr_at_date.index.isin(lb_rets.index)]
                if len(atr_eligible) > 0:
                    median_atr = atr_eligible.median()
                    high_vol_tokens = atr_eligible[atr_eligible > median_atr].index
                    lb_rets = lb_rets[lb_rets.index.isin(high_vol_tokens)]

            if len(lb_rets) >= 2 * R162_K:
                ranked = lb_rets.sort_values(ascending=False)
                top_k = ranked.head(R162_K).index.tolist()
                bottom_k = ranked.tail(R162_K).index.tolist()

                # Regime filter (EMA)
                ema_at_date = ema_signal.loc[date].dropna() if date in ema_signal.index else pd.Series(dtype=float)
                top_k = [t for t in top_k if t in ema_at_date.index and ema_at_date[t] > 0]
                bottom_k = [t for t in bottom_k if t in ema_at_date.index and ema_at_date[t] < 0]

                n_longs = len(top_k) if len(top_k) > 0 else 1
                n_shorts = len(bottom_k) if len(bottom_k) > 0 else 1

                pending_longs = {t: R162_LONG_WT / n_longs for t in top_k} if top_k else {}
                pending_shorts = {t: R162_SHORT_WT / n_shorts for t in bottom_k} if bottom_k else {}

    eq_series = pd.Series(equity_series)
    log(f"  R162 best variant: final equity = {equity:.4f}, trades = {trade_count}")
    log(f"  R162 total cost = {total_cost*100:.2f}% of initial, funding = {total_funding*100:.2f}%")
    return eq_series


# ══════════════════════════════════════════════════════════════════════════════
# PART 2: R160 VOLATILITY BREAKOUT VARIANT B
# ══════════════════════════════════════════════════════════════════════════════

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


def load_r160_tokens() -> Dict[str, pd.DataFrame]:
    """Load and resample tokens for R160."""
    all_tokens = []
    for f in sorted(DATA_DIR.glob('*_1h.parquet')):
        token = f.stem.replace('_1h', '')
        try:
            df = pd.read_parquet(str(f), columns=['close'])
            df.index = pd.to_datetime(df.index)
            duration_days = (df.index.max() - df.index.min()).days
            if duration_days >= R160_MIN_DATA_DAYS:
                all_tokens.append(token)
        except Exception:
            pass

    token_data = {}
    for token in all_tokens:
        path = DATA_DIR / f'{token}_1h.parquet'
        df = pd.read_parquet(str(path))
        df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        df = df[~df.index.duplicated(keep='first')]

        if (df.index.max() - df.index.min()).days < R160_MIN_DATA_DAYS:
            continue

        # Resample to 4H
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
            continue

        # Indicators
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

        # Volume filter
        valid_vol = df_4h['avg_daily_dollar_vol'].dropna()
        if len(valid_vol) > 0 and valid_vol.iloc[-1] >= R160_MIN_AVG_DOLLAR_VOL:
            token_data[token] = df_4h

    return token_data


def _r160_calc_funding(arr, start_idx, end_idx, direction, leverage):
    if start_idx >= end_idx:
        return 0.0
    funding_slice = arr['funding_4h'][start_idx:end_idx]
    total = np.nansum(funding_slice)
    if direction == 1:
        return total * leverage
    else:
        return -total * leverage


def run_r160_variant_b(token_data: Dict[str, pd.DataFrame], leverage: float = 1.0) -> pd.Series:
    """Run R160 Variant B at given leverage. Returns 4H equity curve."""
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
    positions = []
    trades_count = 0
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

        # Process pending entries
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
                    trades_count += 1
            pending_entries = []

        # Update existing positions
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

        # Scan for new breakouts
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
                # Variant B: momentum filter
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
    if positions:
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
            funding_cost = _r160_calc_funding(arr, pos.entry_bar_idx,
                                              min(bar_idx, len(arr['funding_4h']) - 1),
                                              pos.direction, leverage)
            effective_size = pos.size_frac * pos.remaining_frac
            pnl = (levered_return - exit_cost - funding_cost) * effective_size
            equity += pnl
        equity_curve[last_time] = equity

    eq_series = pd.Series(equity_curve).sort_index()
    log(f"  R160 Variant B: final equity = {equity:.4f}, trades = {trades_count}")
    return eq_series


# ══════════════════════════════════════════════════════════════════════════════
# PART 3: PORTFOLIO CONFIGURATIONS
# ══════════════════════════════════════════════════════════════════════════════

def to_daily_returns(eq_series: pd.Series) -> pd.Series:
    """Convert any frequency equity curve to daily returns."""
    daily_eq = eq_series.resample('1D').last().dropna()
    daily_rets = daily_eq.pct_change().fillna(0)
    return daily_rets


def apply_mom_leverage(daily_rets: pd.Series, thresholds: list, borrow_rate: float) -> Tuple[pd.Series, pd.Series]:
    """
    Apply Momentum-of-Momentum (MoM) adaptive leverage to daily returns.

    Args:
        daily_rets: daily return series
        thresholds: list of (min_ret, leverage) tuples, sorted descending by min_ret
        borrow_rate: annual borrow cost rate

    Returns:
        (leveraged_equity, leverage_series)
    """
    equity = 1.0
    equity_vals = []
    leverage_vals = []
    dates = daily_rets.index

    for i, date in enumerate(dates):
        # Compute trailing 14-day return
        if i >= 14:
            trailing_eq_start = 1.0
            for j in range(i - 14, i):
                trailing_eq_start *= (1 + daily_rets.iloc[j])
            trailing_14d_ret = trailing_eq_start - 1.0
        else:
            trailing_14d_ret = 0.0

        # Determine leverage from thresholds
        lev = 1.0
        for min_ret, target_lev in thresholds:
            if min_ret is None:
                lev = target_lev
                break
            if trailing_14d_ret > min_ret:
                lev = target_lev
                break

        leverage_vals.append(lev)

        # Apply leveraged return with borrow cost
        raw_ret = daily_rets.iloc[i]
        leveraged_ret = lev * raw_ret
        # Borrow cost on the leveraged portion above 1x
        if lev > 1.0:
            leveraged_ret -= (lev - 1.0) * borrow_rate / DAYS_PER_YEAR

        equity *= (1 + leveraged_ret)
        equity_vals.append(equity)

    eq_series = pd.Series(equity_vals, index=dates)
    lev_series = pd.Series(leverage_vals, index=dates)
    return eq_series, lev_series


def compute_portfolio_metrics(eq_series: pd.Series, period_start=None, period_end=None) -> dict:
    """Compute standard metrics for an equity curve."""
    if period_start is not None:
        eq = eq_series.loc[eq_series.index >= period_start]
    else:
        eq = eq_series

    if period_end is not None:
        eq = eq.loc[eq.index <= period_end]

    if len(eq) < 2:
        return {'annual_return': 0, 'sharpe': 0, 'max_dd': 0, 'calmar': 0, 'sortino': 0, 'total_return': 0}

    # Normalize to start at its first value
    daily_rets = eq.pct_change().dropna()
    total_return = eq.iloc[-1] / eq.iloc[0] - 1
    n_days = (eq.index[-1] - eq.index[0]).days
    if n_days < 1:
        n_days = 1

    annual_return = (1 + total_return) ** (DAYS_PER_YEAR / n_days) - 1

    running_max = eq.cummax()
    drawdowns = eq / running_max - 1
    max_dd = drawdowns.min()

    if len(daily_rets) < 10:
        return {'annual_return': annual_return, 'sharpe': 0, 'max_dd': max_dd,
                'calmar': 0, 'sortino': 0, 'total_return': total_return}

    mean_daily = daily_rets.mean()
    std_daily = daily_rets.std()
    sharpe = (mean_daily / std_daily * np.sqrt(DAYS_PER_YEAR)) if std_daily > 0 else 0.0
    calmar = annual_return / abs(max_dd) if max_dd != 0 else 0.0

    downside = daily_rets[daily_rets < 0]
    downside_std = downside.std() if len(downside) > 0 else 1e-10
    sortino = (mean_daily / downside_std * np.sqrt(DAYS_PER_YEAR)) if downside_std > 0 else 0.0

    return {
        'annual_return': annual_return,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'calmar': calmar,
        'sortino': sortino,
        'total_return': total_return,
    }


def run_config_a(r162_daily_rets: pd.Series) -> Tuple[pd.Series, pd.Series]:
    """Config A: R162 alone + aggressive MoM leverage."""
    return apply_mom_leverage(r162_daily_rets, MOM_THRESHOLDS_AGGRESSIVE, BORROW_RATE_ANNUAL)


def run_config_b(r162_daily_rets: pd.Series, r160_daily_rets: pd.Series) -> pd.Series:
    """Config B: R162 (70%) + R160 (30%) + static 1.5x leverage."""
    # Align dates
    common_idx = r162_daily_rets.index.intersection(r160_daily_rets.index)
    r162 = r162_daily_rets.reindex(common_idx).fillna(0)
    r160 = r160_daily_rets.reindex(common_idx).fillna(0)

    combined_ret = 0.70 * 1.5 * r162 + 0.30 * 1.5 * r160 - 0.5 * BORROW_RATE_ANNUAL / DAYS_PER_YEAR
    equity = (1 + combined_ret).cumprod()
    return equity


def run_config_c(r162_daily_rets: pd.Series, r160_daily_rets: pd.Series) -> Tuple[pd.Series, pd.Series]:
    """Config C: R162 (60%) + R160 (40%) + MoM leverage on combined."""
    common_idx = r162_daily_rets.index.intersection(r160_daily_rets.index)
    r162 = r162_daily_rets.reindex(common_idx).fillna(0)
    r160 = r160_daily_rets.reindex(common_idx).fillna(0)
    combined_ret = 0.60 * r162 + 0.40 * r160
    return apply_mom_leverage(combined_ret, MOM_THRESHOLDS_AGGRESSIVE, BORROW_RATE_ANNUAL)


def run_config_d(r162_daily_rets: pd.Series, r160_daily_rets: pd.Series) -> Tuple[pd.Series, pd.Series]:
    """Config D: R162 (50%) + R160 (50%) + MoM leverage on combined."""
    common_idx = r162_daily_rets.index.intersection(r160_daily_rets.index)
    r162 = r162_daily_rets.reindex(common_idx).fillna(0)
    r160 = r160_daily_rets.reindex(common_idx).fillna(0)
    combined_ret = 0.50 * r162 + 0.50 * r160
    return apply_mom_leverage(combined_ret, MOM_THRESHOLDS_AGGRESSIVE, BORROW_RATE_ANNUAL)


def run_config_e(r162_daily_rets: pd.Series) -> pd.Series:
    """Config E: R162 alone at static 1.1x."""
    leveraged_ret = 1.1 * r162_daily_rets - 0.1 * BORROW_RATE_ANNUAL / DAYS_PER_YEAR
    equity = (1 + leveraged_ret).cumprod()
    return equity


def run_config_f(r162_daily_rets: pd.Series) -> Tuple[pd.Series, pd.Series]:
    """Config F: R162 alone + conservative MoM leverage."""
    return apply_mom_leverage(r162_daily_rets, MOM_THRESHOLDS_CONSERVATIVE, BORROW_RATE_ANNUAL)


# ══════════════════════════════════════════════════════════════════════════════
# PART 4: ROBUSTNESS ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def robustness_analysis(eq_series: pd.Series, lev_series: pd.Series = None) -> str:
    """Generate robustness analysis for the winning config."""
    lines = []

    # Monthly returns table
    daily_eq = eq_series.resample('1D').last().dropna() if not eq_series.index.freq else eq_series
    monthly_eq = daily_eq.resample('ME').last().dropna()
    monthly_rets = monthly_eq.pct_change().dropna()

    years = sorted(monthly_rets.index.year.unique())
    month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

    lines.append("### Monthly Returns\n")
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

    lines.append("")

    # Average leverage
    if lev_series is not None:
        lines.append(f"### Average Leverage: {lev_series.mean():.2f}x")
        lines.append(f"- Min leverage: {lev_series.min():.1f}x")
        lines.append(f"- Max leverage: {lev_series.max():.1f}x")
        lines.append(f"- % time at max leverage: {(lev_series == lev_series.max()).mean()*100:.1f}%")
        lines.append(f"- % time at min leverage: {(lev_series == lev_series.min()).mean()*100:.1f}%")
        lines.append("")

    # Worst month
    if len(monthly_rets) > 0:
        worst_month = monthly_rets.min()
        worst_month_date = monthly_rets.idxmin()
        best_month = monthly_rets.max()
        best_month_date = monthly_rets.idxmax()
        lines.append(f"### Worst Month: {worst_month*100:.1f}% ({worst_month_date.strftime('%Y-%m')})")
        lines.append(f"### Best Month: {best_month*100:+.1f}% ({best_month_date.strftime('%Y-%m')})")
        lines.append("")

    # Max consecutive losing months
    losing_streak = 0
    max_losing_streak = 0
    for r in monthly_rets:
        if r < 0:
            losing_streak += 1
            max_losing_streak = max(max_losing_streak, losing_streak)
        else:
            losing_streak = 0
    lines.append(f"### Max Consecutive Losing Months: {max_losing_streak}")
    lines.append("")

    # Quarterly performance (last 12 months)
    lines.append("### Quarterly Performance (Last 12 Months)\n")
    quarters = [
        ("Q2 2025 (Mar-Jun)", pd.Timestamp('2025-03-17'), pd.Timestamp('2025-06-16')),
        ("Q3 2025 (Jun-Sep)", pd.Timestamp('2025-06-17'), pd.Timestamp('2025-09-16')),
        ("Q4 2025 (Sep-Dec)", pd.Timestamp('2025-09-17'), pd.Timestamp('2025-12-16')),
        ("Q1 2026 (Dec-Mar)", pd.Timestamp('2025-12-17'), pd.Timestamp('2026-03-17')),
    ]

    lines.append("| Quarter | Return | MaxDD | Sharpe |")
    lines.append("|---------|--------|-------|--------|")
    for q_name, q_start, q_end in quarters:
        q_eq = daily_eq.loc[(daily_eq.index >= q_start) & (daily_eq.index <= q_end)]
        if len(q_eq) > 1:
            q_ret = q_eq.iloc[-1] / q_eq.iloc[0] - 1
            q_dd = (q_eq / q_eq.cummax() - 1).min()
            q_daily_rets = q_eq.pct_change().dropna()
            q_sharpe = (q_daily_rets.mean() / q_daily_rets.std() * np.sqrt(DAYS_PER_YEAR)) if q_daily_rets.std() > 0 else 0
            lines.append(f"| {q_name} | {q_ret*100:+.1f}% | {q_dd*100:.1f}% | {q_sharpe:.2f} |")
        else:
            lines.append(f"| {q_name} | N/A | N/A | N/A |")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    t0 = time.time()
    log("=" * 70)
    log("R164 -- THE FINAL Multi-Strategy Portfolio")
    log("=" * 70)

    # ── PART 1: R162 Optimized Momentum Rotation ──
    log("\n" + "=" * 70)
    log("PART 1: Running R162 Best Variant (L7_K3_80/20_EMA10/30_VF)")
    log("=" * 70)

    log("\n[1a] Loading R162 token data...")
    r162_tokens = load_r162_tokens()
    log(f"  Loaded {len(r162_tokens)} tokens for R162")

    log("[1b] Preparing R162 data panels...")
    (daily_close, daily_returns, lookback_returns_shifted,
     ema_signals_shifted, rolling_avg_dvol_shifted, hourly_fundings,
     atr_ratio_shifted) = prepare_r162_data(r162_tokens)
    log(f"  Daily close shape: {daily_close.shape}")

    log("[1c] Running R162 simulation...")
    r162_equity = run_r162_best_variant(
        daily_close, daily_returns, lookback_returns_shifted,
        ema_signals_shifted, rolling_avg_dvol_shifted, hourly_fundings,
        atr_ratio_shifted
    )

    r162_daily_rets = to_daily_returns(r162_equity)
    r162_full_metrics = compute_portfolio_metrics(r162_equity)
    r162_12m_metrics = compute_portfolio_metrics(r162_equity, period_start=LAST_12MO_START)
    log(f"  R162 Full: Return={r162_full_metrics['total_return']*100:+.1f}%, "
        f"Sharpe={r162_full_metrics['sharpe']:.2f}, MaxDD={r162_full_metrics['max_dd']*100:.1f}%")
    log(f"  R162 12M:  Return={r162_12m_metrics['total_return']*100:+.1f}%, "
        f"Sharpe={r162_12m_metrics['sharpe']:.2f}, MaxDD={r162_12m_metrics['max_dd']*100:.1f}%")

    # ── PART 2: R160 Volatility Breakout Variant B ──
    log("\n" + "=" * 70)
    log("PART 2: Running R160 Variant B (BB breakout + volume + momentum)")
    log("=" * 70)

    log("\n[2a] Loading R160 token data (4H bars)...")
    r160_token_data = load_r160_tokens()
    log(f"  Loaded {len(r160_token_data)} tokens for R160")

    log("[2b] Running R160 Variant B simulation...")
    r160_equity = run_r160_variant_b(r160_token_data, leverage=1.0)

    r160_daily_rets = to_daily_returns(r160_equity)
    r160_full_metrics = compute_portfolio_metrics(r160_equity)
    r160_12m_metrics = compute_portfolio_metrics(r160_equity, period_start=LAST_12MO_START)
    log(f"  R160 Full: Return={r160_full_metrics['total_return']*100:+.1f}%, "
        f"Sharpe={r160_full_metrics['sharpe']:.2f}, MaxDD={r160_full_metrics['max_dd']*100:.1f}%")
    log(f"  R160 12M:  Return={r160_12m_metrics['total_return']*100:+.1f}%, "
        f"Sharpe={r160_12m_metrics['sharpe']:.2f}, MaxDD={r160_12m_metrics['max_dd']*100:.1f}%")

    # ── Correlation check ──
    common_idx = r162_daily_rets.index.intersection(r160_daily_rets.index)
    r162_aligned = r162_daily_rets.reindex(common_idx).fillna(0)
    r160_aligned = r160_daily_rets.reindex(common_idx).fillna(0)
    correlation = r162_aligned.corr(r160_aligned)
    log(f"\n  Strategy correlation (daily returns): {correlation:.4f}")

    # ── PART 3: Portfolio Configurations ──
    log("\n" + "=" * 70)
    log("PART 3: Testing Portfolio Configurations")
    log("=" * 70)

    configs = {}

    # Config A: R162 alone + aggressive MoM leverage
    log("\n  [A] R162 + Aggressive MoM Leverage...")
    eq_a, lev_a = run_config_a(r162_daily_rets)
    configs['A'] = {
        'name': 'R162 + MoM Aggressive',
        'desc': 'R162 alone + MoM leverage (3x/2x/1x/0.5x)',
        'equity': eq_a,
        'leverage': lev_a,
        'full': compute_portfolio_metrics(eq_a),
        '12m': compute_portfolio_metrics(eq_a, period_start=LAST_12MO_START),
    }
    log(f"    Full: {configs['A']['full']['total_return']*100:+.1f}%, "
        f"Sharpe={configs['A']['full']['sharpe']:.2f}, MaxDD={configs['A']['full']['max_dd']*100:.1f}%")
    log(f"    12M:  {configs['A']['12m']['total_return']*100:+.1f}%, "
        f"Sharpe={configs['A']['12m']['sharpe']:.2f}, MaxDD={configs['A']['12m']['max_dd']*100:.1f}%")
    log(f"    Avg leverage: {lev_a.mean():.2f}x")

    # Config B: 70/30 + static 1.5x
    log("\n  [B] R162 (70%) + R160 (30%) + 1.5x static...")
    eq_b = run_config_b(r162_daily_rets, r160_daily_rets)
    configs['B'] = {
        'name': '70/30 + 1.5x Static',
        'desc': '0.70*1.5*R162 + 0.30*1.5*R160, borrow=5%',
        'equity': eq_b,
        'leverage': pd.Series(1.5, index=eq_b.index),
        'full': compute_portfolio_metrics(eq_b),
        '12m': compute_portfolio_metrics(eq_b, period_start=LAST_12MO_START),
    }
    log(f"    Full: {configs['B']['full']['total_return']*100:+.1f}%, "
        f"Sharpe={configs['B']['full']['sharpe']:.2f}, MaxDD={configs['B']['full']['max_dd']*100:.1f}%")
    log(f"    12M:  {configs['B']['12m']['total_return']*100:+.1f}%, "
        f"Sharpe={configs['B']['12m']['sharpe']:.2f}, MaxDD={configs['B']['12m']['max_dd']*100:.1f}%")

    # Config C: 60/40 + MoM aggressive
    log("\n  [C] R162 (60%) + R160 (40%) + MoM Aggressive...")
    eq_c, lev_c = run_config_c(r162_daily_rets, r160_daily_rets)
    configs['C'] = {
        'name': '60/40 + MoM Aggressive',
        'desc': '0.60*R162 + 0.40*R160, then MoM leverage (3x/2x/1x/0.5x)',
        'equity': eq_c,
        'leverage': lev_c,
        'full': compute_portfolio_metrics(eq_c),
        '12m': compute_portfolio_metrics(eq_c, period_start=LAST_12MO_START),
    }
    log(f"    Full: {configs['C']['full']['total_return']*100:+.1f}%, "
        f"Sharpe={configs['C']['full']['sharpe']:.2f}, MaxDD={configs['C']['full']['max_dd']*100:.1f}%")
    log(f"    12M:  {configs['C']['12m']['total_return']*100:+.1f}%, "
        f"Sharpe={configs['C']['12m']['sharpe']:.2f}, MaxDD={configs['C']['12m']['max_dd']*100:.1f}%")
    log(f"    Avg leverage: {lev_c.mean():.2f}x")

    # Config D: 50/50 + MoM aggressive
    log("\n  [D] R162 (50%) + R160 (50%) + MoM Aggressive...")
    eq_d, lev_d = run_config_d(r162_daily_rets, r160_daily_rets)
    configs['D'] = {
        'name': '50/50 + MoM Aggressive',
        'desc': '0.50*R162 + 0.50*R160, then MoM leverage (3x/2x/1x/0.5x)',
        'equity': eq_d,
        'leverage': lev_d,
        'full': compute_portfolio_metrics(eq_d),
        '12m': compute_portfolio_metrics(eq_d, period_start=LAST_12MO_START),
    }
    log(f"    Full: {configs['D']['full']['total_return']*100:+.1f}%, "
        f"Sharpe={configs['D']['full']['sharpe']:.2f}, MaxDD={configs['D']['full']['max_dd']*100:.1f}%")
    log(f"    12M:  {configs['D']['12m']['total_return']*100:+.1f}%, "
        f"Sharpe={configs['D']['12m']['sharpe']:.2f}, MaxDD={configs['D']['12m']['max_dd']*100:.1f}%")
    log(f"    Avg leverage: {lev_d.mean():.2f}x")

    # Config E: R162 alone at static 1.1x
    log("\n  [E] R162 + Static 1.1x...")
    eq_e = run_config_e(r162_daily_rets)
    configs['E'] = {
        'name': 'R162 + 1.1x Static',
        'desc': 'R162 * 1.1x, borrow=5% on 0.1x',
        'equity': eq_e,
        'leverage': pd.Series(1.1, index=eq_e.index),
        'full': compute_portfolio_metrics(eq_e),
        '12m': compute_portfolio_metrics(eq_e, period_start=LAST_12MO_START),
    }
    log(f"    Full: {configs['E']['full']['total_return']*100:+.1f}%, "
        f"Sharpe={configs['E']['full']['sharpe']:.2f}, MaxDD={configs['E']['full']['max_dd']*100:.1f}%")
    log(f"    12M:  {configs['E']['12m']['total_return']*100:+.1f}%, "
        f"Sharpe={configs['E']['12m']['sharpe']:.2f}, MaxDD={configs['E']['12m']['max_dd']*100:.1f}%")

    # Config F: R162 alone + conservative MoM leverage
    log("\n  [F] R162 + Conservative MoM Leverage...")
    eq_f, lev_f = run_config_f(r162_daily_rets)
    configs['F'] = {
        'name': 'R162 + MoM Conservative',
        'desc': 'R162 alone + conservative MoM (2x/1.5x/1x/0.5x)',
        'equity': eq_f,
        'leverage': lev_f,
        'full': compute_portfolio_metrics(eq_f),
        '12m': compute_portfolio_metrics(eq_f, period_start=LAST_12MO_START),
    }
    log(f"    Full: {configs['F']['full']['total_return']*100:+.1f}%, "
        f"Sharpe={configs['F']['full']['sharpe']:.2f}, MaxDD={configs['F']['full']['max_dd']*100:.1f}%")
    log(f"    12M:  {configs['F']['12m']['total_return']*100:+.1f}%, "
        f"Sharpe={configs['F']['12m']['sharpe']:.2f}, MaxDD={configs['F']['12m']['max_dd']*100:.1f}%")
    log(f"    Avg leverage: {lev_f.mean():.2f}x")

    # ── PART 4: Determine winner and run robustness ──
    log("\n" + "=" * 70)
    log("PART 4: Selecting Winner & Robustness Check")
    log("=" * 70)

    # Find best by 12M return
    best_key = max(configs.keys(), key=lambda k: configs[k]['12m']['total_return'])
    log(f"\n  Winner by 12M return: Config {best_key} ({configs[best_key]['name']})")

    # Find best by Sharpe
    best_sharpe_key = max(configs.keys(), key=lambda k: configs[k]['12m']['sharpe'])
    log(f"  Best Sharpe: Config {best_sharpe_key} ({configs[best_sharpe_key]['name']})")

    # Robustness for winner
    winner_eq = configs[best_key]['equity']
    winner_lev = configs[best_key].get('leverage', None)
    robustness_text = robustness_analysis(winner_eq, winner_lev)

    # Also do robustness for best Sharpe if different
    robustness_sharpe_text = ""
    if best_sharpe_key != best_key:
        sharpe_eq = configs[best_sharpe_key]['equity']
        sharpe_lev = configs[best_sharpe_key].get('leverage', None)
        robustness_sharpe_text = robustness_analysis(sharpe_eq, sharpe_lev)

    # ── WRITE REPORT ──
    log("\n" + "=" * 70)
    log("WRITING REPORT")
    log("=" * 70)

    report_lines = []
    report_lines.append("# R164 -- THE FINAL Multi-Strategy Portfolio Results\n")
    report_lines.append(f"**Generated:** {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    report_lines.append(f"**Period:** {SIM_START.date()} to {SIM_END.date()} (~2 years)")
    report_lines.append(f"**Last 12 Months:** {LAST_12MO_START.date()} to {SIM_END.date()}")
    report_lines.append(f"**R162 tokens:** {len(r162_tokens)}")
    report_lines.append(f"**R160 tokens:** {len(r160_token_data)}")
    report_lines.append(f"**Strategy correlation:** {correlation:.4f}")
    report_lines.append("")

    # ── Base strategies ──
    report_lines.append("---\n")
    report_lines.append("## Base Strategy Performance (1x, No Leverage)\n")
    report_lines.append("| Strategy | Period | Return | Ann Return | Sharpe | MaxDD | Calmar | Sortino |")
    report_lines.append("|----------|--------|--------|------------|--------|-------|--------|---------|")

    for name, metrics, period in [
        ("R162 (L7_K3_80/20_EMA10/30_VF)", r162_full_metrics, "Full"),
        ("R162 (L7_K3_80/20_EMA10/30_VF)", r162_12m_metrics, "12M"),
        ("R160 Variant B", r160_full_metrics, "Full"),
        ("R160 Variant B", r160_12m_metrics, "12M"),
    ]:
        report_lines.append(
            f"| {name} | {period} | {metrics['total_return']*100:+.1f}% "
            f"| {metrics['annual_return']*100:+.1f}% | {metrics['sharpe']:.2f} "
            f"| {metrics['max_dd']*100:.1f}% | {metrics['calmar']:.2f} "
            f"| {metrics['sortino']:.2f} |"
        )
    report_lines.append("")

    # ── HEADLINE TABLE ──
    report_lines.append("---\n")
    report_lines.append("## FINAL PORTFOLIO RECOMMENDATION\n")
    report_lines.append("| Config | Description | 12M Return | Ann Return | Sharpe | MaxDD | Calmar | Sortino | Avg Leverage |")
    report_lines.append("|--------|-------------|------------|------------|--------|-------|--------|---------|-------------|")

    for key in sorted(configs.keys()):
        c = configs[key]
        avg_lev = c['leverage'].mean() if isinstance(c['leverage'], pd.Series) else c['leverage']
        m12 = c['12m']
        report_lines.append(
            f"| **{key}** | {c['name']} | **{m12['total_return']*100:+.1f}%** "
            f"| {m12['annual_return']*100:+.1f}% | {m12['sharpe']:.2f} "
            f"| {m12['max_dd']*100:.1f}% | {m12['calmar']:.2f} "
            f"| {m12['sortino']:.2f} | {avg_lev:.2f}x |"
        )
    report_lines.append("")

    # ── Full period table ──
    report_lines.append("### Full Period Metrics\n")
    report_lines.append("| Config | Description | Full Return | Ann Return | Sharpe | MaxDD | Calmar | Sortino | Avg Leverage |")
    report_lines.append("|--------|-------------|-------------|------------|--------|-------|--------|---------|-------------|")

    for key in sorted(configs.keys()):
        c = configs[key]
        avg_lev = c['leverage'].mean() if isinstance(c['leverage'], pd.Series) else c['leverage']
        mf = c['full']
        report_lines.append(
            f"| **{key}** | {c['name']} | {mf['total_return']*100:+.1f}% "
            f"| {mf['annual_return']*100:+.1f}% | {mf['sharpe']:.2f} "
            f"| {mf['max_dd']*100:.1f}% | {mf['calmar']:.2f} "
            f"| {mf['sortino']:.2f} | {avg_lev:.2f}x |"
        )
    report_lines.append("")

    # ── Config descriptions ──
    report_lines.append("---\n")
    report_lines.append("## Configuration Descriptions\n")
    report_lines.append("| Config | Formula |")
    report_lines.append("|--------|---------|")
    report_lines.append("| A | R162 returns -> MoM leverage (>5%:3x, 0-5%:2x, -5-0%:1x, <-5%:0.5x) - 5% borrow |")
    report_lines.append("| B | 0.70*1.5*R162 + 0.30*1.5*R160 - 0.5*5%/365 daily |")
    report_lines.append("| C | (0.60*R162 + 0.40*R160) -> MoM leverage (same thresholds) |")
    report_lines.append("| D | (0.50*R162 + 0.50*R160) -> MoM leverage (same thresholds) |")
    report_lines.append("| E | R162 * 1.1x - 0.1*5%/365 daily |")
    report_lines.append("| F | R162 returns -> Conservative MoM (>5%:2x, 0-5%:1.5x, -5-0%:1x, <-5%:0.5x) - 5% borrow |")
    report_lines.append("")

    # ── Winner analysis ──
    report_lines.append("---\n")
    report_lines.append(f"## Winner: Config {best_key} ({configs[best_key]['name']})\n")
    report_lines.append(f"**12M Return:** {configs[best_key]['12m']['total_return']*100:+.1f}%")
    report_lines.append(f"**Sharpe:** {configs[best_key]['12m']['sharpe']:.2f}")
    report_lines.append(f"**MaxDD:** {configs[best_key]['12m']['max_dd']*100:.1f}%")
    avg_lev_winner = configs[best_key]['leverage'].mean() if isinstance(configs[best_key]['leverage'], pd.Series) else configs[best_key]['leverage']
    report_lines.append(f"**Avg Leverage:** {avg_lev_winner:.2f}x\n")
    report_lines.append(robustness_text)
    report_lines.append("")

    if best_sharpe_key != best_key:
        report_lines.append("---\n")
        report_lines.append(f"## Best Risk-Adjusted: Config {best_sharpe_key} ({configs[best_sharpe_key]['name']})\n")
        report_lines.append(f"**12M Return:** {configs[best_sharpe_key]['12m']['total_return']*100:+.1f}%")
        report_lines.append(f"**Sharpe:** {configs[best_sharpe_key]['12m']['sharpe']:.2f}")
        report_lines.append(f"**MaxDD:** {configs[best_sharpe_key]['12m']['max_dd']*100:.1f}%\n")
        report_lines.append(robustness_sharpe_text)
        report_lines.append("")

    # ── Key insights ──
    report_lines.append("---\n")
    report_lines.append("## Key Insights\n")
    report_lines.append(f"1. **Strategy correlation is {correlation:.3f}** -- R162 momentum and R160 vol breakout are "
                       f"{'nearly uncorrelated' if abs(correlation) < 0.1 else 'weakly correlated' if abs(correlation) < 0.3 else 'moderately correlated'}")
    report_lines.append(f"2. **R162 base at 1x:** 12M = {r162_12m_metrics['total_return']*100:+.1f}%, Sharpe = {r162_12m_metrics['sharpe']:.2f}")
    report_lines.append(f"3. **R160 base at 1x:** 12M = {r160_12m_metrics['total_return']*100:+.1f}%, Sharpe = {r160_12m_metrics['sharpe']:.2f}")

    # MoM vs static comparison
    mom_configs = ['A', 'C', 'D', 'F']
    static_configs = ['B', 'E']
    avg_mom_12m = np.mean([configs[k]['12m']['total_return'] for k in mom_configs])
    avg_static_12m = np.mean([configs[k]['12m']['total_return'] for k in static_configs])
    report_lines.append(f"4. **MoM leverage avg 12M return:** {avg_mom_12m*100:+.1f}% vs **static leverage:** {avg_static_12m*100:+.1f}%")

    avg_mom_sharpe = np.mean([configs[k]['12m']['sharpe'] for k in mom_configs])
    avg_static_sharpe = np.mean([configs[k]['12m']['sharpe'] for k in static_configs])
    report_lines.append(f"5. **MoM leverage avg Sharpe:** {avg_mom_sharpe:.2f} vs **static leverage:** {avg_static_sharpe:.2f}")

    # Config E check
    e_12m = configs['E']['12m']['total_return']
    report_lines.append(f"6. **Config E (1.1x R162):** 12M = {e_12m*100:+.1f}% -- "
                       f"{'CLEARS' if e_12m > 3.0 else 'DOES NOT CLEAR'} 300% target")

    report_lines.append("")

    # Write
    report = "\n".join(report_lines)
    with open(str(OUTPUT_MD), 'w') as f:
        f.write(report)
    log(f"\nReport written to {OUTPUT_MD}")

    # ── STDOUT Summary ──
    log("\n" + "=" * 70)
    log("FINAL SUMMARY")
    log("=" * 70)
    log(f"\n{'Config':<8} {'Name':<30} {'12M Ret':>10} {'Sharpe':>8} {'MaxDD':>8} {'Avg Lev':>8}")
    log("-" * 72)
    for key in sorted(configs.keys()):
        c = configs[key]
        avg_lev = c['leverage'].mean() if isinstance(c['leverage'], pd.Series) else c['leverage']
        m12 = c['12m']
        log(f"{key:<8} {c['name']:<30} {m12['total_return']*100:>+9.1f}% {m12['sharpe']:>7.2f} "
            f"{m12['max_dd']*100:>7.1f}% {avg_lev:>7.2f}x")

    log(f"\nWinner by 12M return: Config {best_key}")
    log(f"Best risk-adjusted:   Config {best_sharpe_key}")

    total_time = time.time() - t0
    log(f"\nTotal runtime: {total_time:.1f}s")
    log("Done.")


if __name__ == '__main__':
    main()
