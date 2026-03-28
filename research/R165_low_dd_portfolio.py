#!/workspace/venv/bin/python
"""
R165 -- Low-Drawdown Portfolio: Targeting 300%+ Returns with MaxDD < 20%

Combines three uncorrelated base strategies with a drawdown control overlay:

Base Strategies:
  1. R158 K10 Diversified: L7_R7_K10_50/50 with regime filter (10 tokens per leg)
  2. R158 K5 Balanced: L7_R7_K5_50/50 with regime filter (original best Sharpe)
  3. R160 Variant B: Volatility breakout with momentum filter (1x leverage base)

Drawdown Control Overlay:
  - Portfolio DD 0-5%:   100% of target exposure
  - Portfolio DD 5-10%:  75% of target exposure
  - Portfolio DD 10-15%: 50% of target exposure
  - Portfolio DD 15-20%: 25% of target exposure
  - Portfolio DD > 20%:  0% exposure (all cash)
  Recovery: +1 step per day when DD recovers below threshold.

Test Configurations:
  Group A: R160-heavy (low DD base) -- 4 configs
  Group B: Balanced with DD control -- 3 configs
  Group C: Same as A+B without DD control -- 7 configs
  Group D: Pure R160 with high leverage -- 3 configs
  Total: 17 configurations.

Costs: 7 bps per side + funding from parquet + 5% annual borrow on leveraged capital.

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
import os
from itertools import product

warnings.filterwarnings('ignore')

def log(msg):
    print(msg)
    sys.stdout.flush()


# ============================================================
# CONSTANTS
# ============================================================

DATA_DIR = Path('/workspace/crypto_backtest/data/perp/1h_cache')
OUTPUT_PATH = Path('/workspace/crypto_backtest/research/R165_low_dd_results.md')

SIM_START = pd.Timestamp('2024-03-17')
SIM_END = pd.Timestamp('2026-03-17')
LAST_12MO_START = pd.Timestamp('2025-03-17')

# R158 constants
MIN_AVG_DAILY_VOLUME_R158 = 1_000_000  # $1M
FEE_BPS = 7.0
EMA_FAST_H = 20
EMA_SLOW_H = 50
MAX_LOOKBACK_DAYS = 30
HOURS_PER_YEAR = 8760
DAYS_PER_YEAR = 365

# R160 constants
BB_PERIOD = 20
BB_STD_MULT = 2.0
VOL_MULT = 1.5
ATR_PERIOD = 20
SMA_PERIOD = 20
PARTIAL_PROFIT_ATR_MULT = 3.0
PARTIAL_CLOSE_FRAC = 0.5
MAX_HOLD_BARS = 180
MOMENTUM_LOOKBACK_4H = 14 * 6
TOP_N_BREAKOUTS = 5
MAX_POSITIONS = 10
POSITION_SIZE = 0.20
COST_PER_SIDE = FEE_BPS / 10000.0
MIN_DATA_DAYS = 365
MIN_AVG_DOLLAR_VOL_R160 = 2_000_000
BARS_4H_PER_YEAR = HOURS_PER_YEAR / 4

# Borrow cost
ANNUAL_BORROW_RATE = 0.05  # 5% per year


# ============================================================
# PART 1A: R158 Data Loading & Strategy
# ============================================================

def load_r158_tokens() -> Dict[str, pd.DataFrame]:
    """Load all tokens for R158 (volume filter $1M)."""
    files = sorted(os.listdir(DATA_DIR))
    tokens = {}
    cutoff = pd.Timestamp('2024-03-17')

    for f in files:
        if not f.endswith('_1h.parquet'):
            continue
        ticker = f.replace('_1h.parquet', '')
        path = os.path.join(DATA_DIR, f)
        df = pd.read_parquet(path)

        if df.index.min() >= cutoff:
            continue

        pre_sim = df[df.index < SIM_START]
        if len(pre_sim) < 30 * 24:
            continue

        last_30d = pre_sim.tail(30 * 24)
        daily_dvol = (last_30d['volume'] * last_30d['close']).resample('1D').sum()
        avg_dvol = daily_dvol.mean()

        if avg_dvol < MIN_AVG_DAILY_VOLUME_R158:
            continue

        tokens[ticker] = df

    return tokens


def prepare_r158_daily_data(tokens: Dict[str, pd.DataFrame]):
    """Prepare R158 daily data panels."""
    daily_closes = {}
    daily_volumes = {}
    ema_signals = {}
    hourly_fundings = {}

    for ticker, df in tokens.items():
        daily = df['close'].resample('1D').last().dropna()
        daily_closes[ticker] = daily

        dvol = (df['volume'] * df['close']).resample('1D').sum()
        daily_volumes[ticker] = dvol

        ema_fast = df['close'].ewm(span=EMA_FAST_H, adjust=False).mean()
        ema_slow = df['close'].ewm(span=EMA_SLOW_H, adjust=False).mean()
        ema_diff = (ema_fast - ema_slow).resample('1D').last()
        ema_signals[ticker] = ema_diff

        hourly_fundings[ticker] = df['funding_1h'].fillna(0)

    daily_close = pd.DataFrame(daily_closes)
    daily_volume = pd.DataFrame(daily_volumes)
    ema_signal = pd.DataFrame(ema_signals)

    start_with_buffer = SIM_START - pd.Timedelta(days=MAX_LOOKBACK_DAYS + 10)
    daily_close = daily_close.loc[start_with_buffer:SIM_END]
    daily_volume = daily_volume.reindex(daily_close.index)
    ema_signal = ema_signal.reindex(daily_close.index)

    daily_returns = daily_close.pct_change()

    lookback_returns_shifted = {}
    for L in [7]:  # Only need L=7 for our configs
        lb = daily_close.shift(1) / daily_close.shift(1 + L) - 1
        lookback_returns_shifted[L] = lb

    ema_signal_shifted = ema_signal.shift(1)

    rolling_avg_dvol = daily_volume.rolling(30, min_periods=15).mean()
    rolling_avg_dvol_shifted = rolling_avg_dvol.shift(1)

    return (daily_close, daily_returns, lookback_returns_shifted,
            ema_signal_shifted, rolling_avg_dvol_shifted, hourly_fundings)


def run_r158_momentum(
    daily_close, daily_returns, lookback_returns_shifted,
    ema_signal_shifted, rolling_avg_dvol_shifted, hourly_fundings,
    k: int,
) -> pd.Series:
    """
    Run R158 momentum rotation with fixed params: L7, R7, 50/50, regime filter.
    Returns daily equity curve (starting at 1.0).
    """
    lookback_days = 7
    rebalance_days = 7
    long_wt = 0.50
    short_wt = 0.50

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
                cost += pending_longs[t] * (FEE_BPS / 10000)
            for t in long_exits:
                cost += current_longs.get(t, 0) * (FEE_BPS / 10000)
            for t in short_entries:
                cost += pending_shorts[t] * (FEE_BPS / 10000)
            for t in short_exits:
                cost += current_shorts.get(t, 0) * (FEE_BPS / 10000)

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
            eligible = vol_at_date[vol_at_date >= MIN_AVG_DAILY_VOLUME_R158].index
            lb_rets = lb_rets[lb_rets.index.isin(eligible)]

            if len(lb_rets) >= 2 * k:
                ranked = lb_rets.sort_values(ascending=False)
                top_k = ranked.head(k).index.tolist()
                bottom_k = ranked.tail(k).index.tolist()

                # Regime filter (always on)
                ema_at_date = ema_signal_shifted.loc[date].dropna() if date in ema_signal_shifted.index else pd.Series(dtype=float)
                top_k = [t for t in top_k if t in ema_at_date.index and ema_at_date[t] > 0]
                bottom_k = [t for t in bottom_k if t in ema_at_date.index and ema_at_date[t] < 0]

                n_longs = len(top_k) if len(top_k) > 0 else 1
                n_shorts = len(bottom_k) if len(bottom_k) > 0 else 1

                pending_longs = {t: long_wt / n_longs for t in top_k} if top_k else {}
                pending_shorts = {t: short_wt / n_shorts for t in bottom_k} if bottom_k else {}

    return pd.Series(equity_series)


# ============================================================
# PART 1B: R160 Data Loading & Strategy
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


def load_r160_token_data() -> Dict[str, pd.DataFrame]:
    """Load and resample tokens for R160."""
    tokens_4h = {}

    for f in sorted(DATA_DIR.glob('*_1h.parquet')):
        token = f.stem.replace('_1h', '')
        try:
            df = pd.read_parquet(f)
            df.index = pd.to_datetime(df.index)
            df = df.sort_index()
            df = df[~df.index.duplicated(keep='first')]

            if (df.index.max() - df.index.min()).days < MIN_DATA_DAYS:
                continue

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

            if len(df_4h) < BB_PERIOD + 50:
                continue

            # Indicators
            df_4h['sma20'] = df_4h['close'].rolling(BB_PERIOD).mean()
            df_4h['bb_std'] = df_4h['close'].rolling(BB_PERIOD).std()
            df_4h['bb_upper'] = df_4h['sma20'] + BB_STD_MULT * df_4h['bb_std']
            df_4h['bb_lower'] = df_4h['sma20'] - BB_STD_MULT * df_4h['bb_std']
            df_4h['avg_volume'] = df_4h['volume'].rolling(BB_PERIOD).mean()
            df_4h['dollar_volume'] = df_4h['volume'] * df_4h['close']

            tr_hl = df_4h['high'] - df_4h['low']
            tr_hc = (df_4h['high'] - df_4h['close'].shift(1)).abs()
            tr_lc = (df_4h['low'] - df_4h['close'].shift(1)).abs()
            df_4h['true_range'] = pd.concat([tr_hl, tr_hc, tr_lc], axis=1).max(axis=1)
            df_4h['atr'] = df_4h['true_range'].rolling(ATR_PERIOD).mean()

            df_4h['breakout_long_raw'] = df_4h['close'] > df_4h['bb_upper']
            df_4h['breakout_short_raw'] = df_4h['close'] < df_4h['bb_lower']
            df_4h['volume_confirmed'] = df_4h['volume'] > VOL_MULT * df_4h['avg_volume']
            df_4h['breakout_long'] = df_4h['breakout_long_raw'] & df_4h['volume_confirmed']
            df_4h['breakout_short'] = df_4h['breakout_short_raw'] & df_4h['volume_confirmed']
            df_4h['long_strength'] = ((df_4h['close'] - df_4h['bb_upper']) / df_4h['close']).clip(lower=0)
            df_4h['short_strength'] = ((df_4h['bb_lower'] - df_4h['close']) / df_4h['close']).clip(lower=0)
            df_4h['return_14d'] = df_4h['close'].pct_change(MOMENTUM_LOOKBACK_4H)
            df_4h['avg_daily_dollar_vol'] = df_4h['dollar_volume'].rolling(180).mean() * 6

            # Volume filter
            valid_vol = df_4h['avg_daily_dollar_vol'].dropna()
            if len(valid_vol) > 0 and valid_vol.iloc[-1] >= MIN_AVG_DOLLAR_VOL_R160:
                tokens_4h[token] = df_4h

        except Exception:
            pass

    return tokens_4h


def _calc_funding_r160(arr, start_idx, end_idx, direction, leverage):
    if start_idx >= end_idx:
        return 0.0
    funding_slice = arr['funding_4h'][start_idx:end_idx]
    total = np.nansum(funding_slice)
    if direction == 1:
        return total * leverage
    else:
        return -total * leverage


def run_r160_breakout(token_data: Dict[str, pd.DataFrame], leverage: float = 1.0) -> pd.Series:
    """
    Run R160 Variant B (momentum filtered) at given leverage.
    Returns 4H equity curve (starting at 1.0).
    """
    variant = 'B'
    fee = COST_PER_SIDE

    all_times = set()
    for token, df in token_data.items():
        all_times.update(df.index.tolist())
    all_times = sorted(all_times)

    if len(all_times) < 100:
        return pd.Series(dtype=float)

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

    warmup_bars = BB_PERIOD + 10
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
            slots_available = MAX_POSITIONS - len(positions)
            if slots_available > 0:
                pending_entries.sort(key=lambda x: x[2], reverse=True)
                to_enter = pending_entries[:min(TOP_N_BREAKOUTS, slots_available)]

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
                    equity -= entry_cost * POSITION_SIZE

                    pos = R160Position(
                        token=token, entry_time=current_time, entry_bar_idx=bar_idx,
                        entry_price=entry_price, direction=direction,
                        size_frac=POSITION_SIZE, leverage=leverage, entry_atr=entry_atr,
                    )
                    positions.append(pos)

            pending_entries = []

        # Update positions
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
                target = pos.entry_price + pos.direction * PARTIAL_PROFIT_ATR_MULT * pos.entry_atr
                if pos.direction == 1 and close_price >= target:
                    partial_return = (close_price / pos.entry_price - 1.0) * leverage
                    partial_cost = fee * leverage
                    partial_funding = _calc_funding_r160(arr, pos.entry_bar_idx, bar_idx, pos.direction, leverage)
                    partial_pnl = (partial_return - partial_cost - partial_funding) * pos.size_frac * PARTIAL_CLOSE_FRAC
                    equity += partial_pnl
                    pos.partial_taken = True
                    pos.remaining_frac = 1.0 - PARTIAL_CLOSE_FRAC
                    pos.breakeven_stop = True
                elif pos.direction == -1 and close_price <= target:
                    partial_return = (1.0 - close_price / pos.entry_price) * leverage
                    partial_cost = fee * leverage
                    partial_funding = _calc_funding_r160(arr, pos.entry_bar_idx, bar_idx, pos.direction, leverage)
                    partial_pnl = (partial_return - partial_cost - partial_funding) * pos.size_frac * PARTIAL_CLOSE_FRAC
                    equity += partial_pnl
                    pos.partial_taken = True
                    pos.remaining_frac = 1.0 - PARTIAL_CLOSE_FRAC
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
            if exit_reason is None and pos.bars_held >= MAX_HOLD_BARS:
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
                funding_cost = _calc_funding_r160(arr, pos.entry_bar_idx, bar_idx, pos.direction, leverage)
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

            if bar_idx < BB_PERIOD + 5:
                continue
            if np.isnan(arr['bb_upper'][bar_idx]) or np.isnan(arr['atr'][bar_idx]):
                continue
            if np.isnan(arr['avg_daily_dollar_vol'][bar_idx]):
                continue
            if arr['avg_daily_dollar_vol'][bar_idx] < MIN_AVG_DOLLAR_VOL_R160:
                continue

            if arr['breakout_long'][bar_idx]:
                strength = arr['long_strength'][bar_idx]
                if np.isnan(strength) or strength <= 0:
                    continue
                if variant == 'B':
                    if bar_idx < MOMENTUM_LOOKBACK_4H:
                        continue
                    ret_14d = arr['return_14d'][bar_idx]
                    if np.isnan(ret_14d) or ret_14d <= 0:
                        continue
                new_candidates.append((token, 1, strength))

            if arr['breakout_short'][bar_idx]:
                strength = arr['short_strength'][bar_idx]
                if np.isnan(strength) or strength <= 0:
                    continue
                if variant == 'B':
                    if bar_idx < MOMENTUM_LOOKBACK_4H:
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
        funding_cost = _calc_funding_r160(arr, pos.entry_bar_idx, min(bar_idx, len(arr['funding_4h']) - 1),
                                          pos.direction, leverage)
        effective_size = pos.size_frac * pos.remaining_frac
        pnl = (levered_return - exit_cost - funding_cost) * effective_size
        equity += pnl

    equity_curve[last_time] = equity

    eq_series = pd.Series(equity_curve).sort_index()
    return eq_series


# ============================================================
# PART 2: Combine into daily returns + drawdown control
# ============================================================

def equity_to_daily_returns(eq: pd.Series) -> pd.Series:
    """Convert equity curve (any frequency) to daily returns."""
    daily_eq = eq.resample('1D').last().dropna()
    daily_rets = daily_eq.pct_change().dropna()
    return daily_rets


def combine_strategies(
    strategy_returns: Dict[str, pd.Series],
    weights: Dict[str, float],
    leverage: float,
    use_dd_control: bool,
    dd_trailing_days: int = 60,
    label: str = "",
) -> pd.Series:
    """
    Combine strategy daily returns with allocation weights, leverage,
    drawdown control overlay, and borrow cost.

    DD control uses a TRAILING WINDOW peak (default 60 days) so that
    after a drawdown, the reference resets and exposure can resume.

    Returns portfolio equity curve.
    """
    # Align all returns to common daily index
    all_rets = pd.DataFrame(strategy_returns)
    all_rets = all_rets.fillna(0.0)

    # Trim to sim period
    all_rets = all_rets.loc[SIM_START:SIM_END]

    # Weighted combination (before leverage)
    combined_rets = pd.Series(0.0, index=all_rets.index)
    for strat, wt in weights.items():
        if strat in all_rets.columns:
            combined_rets += wt * all_rets[strat]

    # Apply leverage + borrow cost
    # Borrow cost: 5% annual on (leverage - 1) * notional, charged daily
    daily_borrow_cost = ANNUAL_BORROW_RATE * max(leverage - 1, 0) / DAYS_PER_YEAR

    # Build equity with DD control
    equity = 1.0
    equity_series = {}
    equity_history = []  # for trailing peak computation
    current_exposure_step = 4  # 0=0%, 1=25%, 2=50%, 3=75%, 4=100%

    for date, base_ret in combined_rets.items():
        if use_dd_control:
            # Compute trailing peak over last N days
            equity_history.append(equity)
            window = equity_history[-dd_trailing_days:] if len(equity_history) >= dd_trailing_days else equity_history
            trailing_peak = max(window)

            # Compute current drawdown from trailing peak
            dd = (equity - trailing_peak) / trailing_peak  # negative number

            # Determine target exposure step based on DD
            if dd >= -0.05:
                target_step = 4  # 100%
            elif dd >= -0.10:
                target_step = 3  # 75%
            elif dd >= -0.15:
                target_step = 2  # 50%
            elif dd >= -0.20:
                target_step = 1  # 25%
            else:
                target_step = 0  # 0%

            # Gradual recovery: +1 step per day max when recovering
            if target_step > current_exposure_step:
                current_exposure_step = min(current_exposure_step + 1, target_step)
            else:
                current_exposure_step = target_step

            exposure_frac = current_exposure_step / 4.0
        else:
            exposure_frac = 1.0

        # Day's return = leveraged return * exposure - borrow cost * exposure
        day_return = base_ret * leverage * exposure_frac - daily_borrow_cost * exposure_frac

        equity *= (1 + day_return)
        equity_series[date] = equity

    return pd.Series(equity_series)


# ============================================================
# PART 4: Metrics
# ============================================================

def compute_portfolio_metrics(eq: pd.Series, label: str = "") -> dict:
    """Compute comprehensive metrics for a portfolio equity curve."""
    if len(eq) < 2 or eq.iloc[-1] <= 0:
        return _empty_portfolio_metrics(label)

    daily_eq = eq.resample('1D').last().dropna() if not eq.index.is_monotonic_increasing else eq
    daily_rets = daily_eq.pct_change().dropna()

    if len(daily_rets) < 30:
        return _empty_portfolio_metrics(label)

    # Full period metrics
    total_return = daily_eq.iloc[-1] / daily_eq.iloc[0] - 1
    n_days = (daily_eq.index[-1] - daily_eq.index[0]).days
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

    # Last 12 months
    l12m_eq = daily_eq[daily_eq.index >= LAST_12MO_START]
    if len(l12m_eq) > 1:
        l12m_return = l12m_eq.iloc[-1] / l12m_eq.iloc[0] - 1
        l12m_rets = l12m_eq.pct_change().dropna()
        l12m_running_max = l12m_eq.cummax()
        l12m_dd = l12m_eq / l12m_running_max - 1
        l12m_max_dd = l12m_dd.min()
        l12m_mean = l12m_rets.mean()
        l12m_std = l12m_rets.std()
        l12m_sharpe = (l12m_mean / l12m_std * np.sqrt(DAYS_PER_YEAR)) if l12m_std > 0 else 0.0
        l12m_calmar = ((1 + l12m_return) ** (DAYS_PER_YEAR / max(1, (l12m_eq.index[-1] - l12m_eq.index[0]).days)) - 1) / abs(l12m_max_dd) if l12m_max_dd != 0 else 0.0
    else:
        l12m_return = 0.0
        l12m_max_dd = 0.0
        l12m_sharpe = 0.0
        l12m_calmar = 0.0

    # Monthly returns for worst month/quarter
    monthly_eq = daily_eq.resample('ME').last().dropna()
    monthly_rets = monthly_eq.pct_change().dropna()
    worst_month = monthly_rets.min() if len(monthly_rets) > 0 else 0.0

    quarterly_eq = daily_eq.resample('QE').last().dropna()
    quarterly_rets = quarterly_eq.pct_change().dropna()
    worst_quarter = quarterly_rets.min() if len(quarterly_rets) > 0 else 0.0

    return {
        'label': label,
        'total_return': total_return,
        'annual_return': annual_return,
        'max_dd': max_dd,
        'sharpe': sharpe,
        'calmar': calmar,
        'l12m_return': l12m_return,
        'l12m_max_dd': l12m_max_dd,
        'l12m_sharpe': l12m_sharpe,
        'l12m_calmar': l12m_calmar,
        'worst_month': worst_month,
        'worst_quarter': worst_quarter,
    }


def _empty_portfolio_metrics(label=""):
    return {
        'label': label,
        'total_return': -1.0, 'annual_return': -1.0, 'max_dd': -1.0,
        'sharpe': -99.0, 'calmar': -99.0,
        'l12m_return': -1.0, 'l12m_max_dd': -1.0,
        'l12m_sharpe': -99.0, 'l12m_calmar': -99.0,
        'worst_month': -1.0, 'worst_quarter': -1.0,
    }


# ============================================================
# PART 5: Generate Report
# ============================================================

def generate_report(all_results: List[dict], base_metrics: dict) -> str:
    lines = []
    lines.append("# R165 -- Low-Drawdown Portfolio Results")
    lines.append("")
    lines.append(f"**Generated:** {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Simulation Period:** {SIM_START.date()} to {SIM_END.date()} (~2 years)")
    lines.append(f"**Last 12 Months:** {LAST_12MO_START.date()} to {SIM_END.date()}")
    lines.append(f"**Borrow Cost:** {ANNUAL_BORROW_RATE*100:.0f}% annual on leveraged capital")
    lines.append(f"**Trading Cost:** {FEE_BPS:.0f} bps per side + hourly funding from parquet")
    lines.append("")

    # Base strategy metrics
    lines.append("## Base Strategy Performance (1x, no DD control)")
    lines.append("")
    lines.append("| Strategy | Full Return | Full MaxDD | Full Sharpe | 12M Return | 12M MaxDD | 12M Sharpe |")
    lines.append("|----------|------------|-----------|------------|-----------|----------|-----------|")
    for name, m in base_metrics.items():
        lines.append(
            f"| {name} "
            f"| {m['total_return']*100:+.1f}% "
            f"| {m['max_dd']*100:.1f}% "
            f"| {m['sharpe']:.2f} "
            f"| {m['l12m_return']*100:+.1f}% "
            f"| {m['l12m_max_dd']*100:.1f}% "
            f"| {m['l12m_sharpe']:.2f} |"
        )
    lines.append("")

    # Group the results
    groups = {
        'A': [r for r in all_results if r['label'].startswith('A')],
        'B': [r for r in all_results if r['label'].startswith('B')],
        'C': [r for r in all_results if r['label'].startswith('C')],
        'D': [r for r in all_results if r['label'].startswith('D')],
        'E': [r for r in all_results if r['label'].startswith('E')],
        'F': [r for r in all_results if r['label'].startswith('F')],
    }

    header = "| Config | Allocation | Lev | DD Ctrl | 12M Ret | 12M MaxDD | 12M Sharpe | 12M Calmar | Full Ret | Full MaxDD | Full Sharpe | Full Calmar | Worst Mo | Worst Qtr |"
    sep = "|--------|-----------|-----|---------|---------|----------|-----------|-----------|---------|-----------|------------|------------|----------|-----------|"

    for group_name, group_desc in [
        ('A', 'Group A: R160-Heavy (Low DD Base) -- With DD Control (60d trail)'),
        ('B', 'Group B: Balanced -- With DD Control (60d trail)'),
        ('C', 'Group C: Same Allocations -- NO DD Control (Comparison)'),
        ('D', 'Group D: Pure R160 -- High Leverage with DD Control (60d trail)'),
        ('E', 'Group E: K5-Heavy at Higher Leverage -- DD Control (60d trail)'),
        ('F', 'Group F: K5-Heavy -- DD Control with SHORTER Trail (30d)'),
    ]:
        group = groups[group_name]
        if not group:
            continue
        lines.append(f"## {group_desc}")
        lines.append("")
        lines.append(header)
        lines.append(sep)

        for r in group:
            lines.append(
                f"| **{r['label']}** "
                f"| {r.get('alloc_desc', '')} "
                f"| {r.get('leverage', '')}x "
                f"| {'Yes' if r.get('dd_control', False) else 'No'} "
                f"| **{r['l12m_return']*100:+.1f}%** "
                f"| {r['l12m_max_dd']*100:.1f}% "
                f"| {r['l12m_sharpe']:.2f} "
                f"| {r['l12m_calmar']:.1f} "
                f"| {r['total_return']*100:+.1f}% "
                f"| {r['max_dd']*100:.1f}% "
                f"| {r['sharpe']:.2f} "
                f"| {r['calmar']:.1f} "
                f"| {r['worst_month']*100:.1f}% "
                f"| {r['worst_quarter']*100:.1f}% |"
            )
        lines.append("")

    # Highlight section
    lines.append("---")
    lines.append("")
    lines.append("## HIGHLIGHTED CONFIGURATIONS")
    lines.append("")

    # Target: 12M return > 300% AND 12M MaxDD > -20%
    target_hit = [r for r in all_results if r['l12m_return'] > 3.0 and r['l12m_max_dd'] > -0.20]
    lines.append("### 12M Return > 300% AND 12M MaxDD > -20%")
    lines.append("")
    if target_hit:
        for r in sorted(target_hit, key=lambda x: x['l12m_return'], reverse=True):
            lines.append(f"- **{r['label']}**: 12M={r['l12m_return']*100:+.1f}%, MaxDD={r['l12m_max_dd']*100:.1f}%, Calmar={r['l12m_calmar']:.1f}")
    else:
        lines.append("**None found.** Closest configs:")
        lines.append("")
        # Show configs closest to the target
        by_ret = sorted(all_results, key=lambda x: x['l12m_return'], reverse=True)
        for r in by_ret[:5]:
            lines.append(f"- **{r['label']}**: 12M={r['l12m_return']*100:+.1f}%, MaxDD={r['l12m_max_dd']*100:.1f}%, Calmar={r['l12m_calmar']:.1f}")
        lines.append("")
        # And configs with best MaxDD that still have high return
        by_dd = sorted([r for r in all_results if r['l12m_return'] > 1.0], key=lambda x: x['l12m_max_dd'], reverse=True)
        if by_dd:
            lines.append("Best DD among configs with 12M > 100%:")
            for r in by_dd[:5]:
                lines.append(f"- **{r['label']}**: 12M={r['l12m_return']*100:+.1f}%, MaxDD={r['l12m_max_dd']*100:.1f}%, Calmar={r['l12m_calmar']:.1f}")
    lines.append("")

    # Calmar > 3
    high_calmar = [r for r in all_results if r['l12m_calmar'] > 3.0]
    lines.append("### 12M Calmar > 3")
    lines.append("")
    if high_calmar:
        for r in sorted(high_calmar, key=lambda x: x['l12m_calmar'], reverse=True):
            lines.append(f"- **{r['label']}**: Calmar={r['l12m_calmar']:.1f}, 12M={r['l12m_return']*100:+.1f}%, MaxDD={r['l12m_max_dd']*100:.1f}%")
    else:
        lines.append("None found. Best Calmar configs:")
        by_calmar = sorted(all_results, key=lambda x: x['l12m_calmar'], reverse=True)
        for r in by_calmar[:5]:
            lines.append(f"- **{r['label']}**: Calmar={r['l12m_calmar']:.1f}, 12M={r['l12m_return']*100:+.1f}%, MaxDD={r['l12m_max_dd']*100:.1f}%")
    lines.append("")

    # Sharpe > 2
    high_sharpe = [r for r in all_results if r['l12m_sharpe'] > 2.0]
    lines.append("### 12M Sharpe > 2")
    lines.append("")
    if high_sharpe:
        for r in sorted(high_sharpe, key=lambda x: x['l12m_sharpe'], reverse=True):
            lines.append(f"- **{r['label']}**: Sharpe={r['l12m_sharpe']:.2f}, 12M={r['l12m_return']*100:+.1f}%, MaxDD={r['l12m_max_dd']*100:.1f}%")
    else:
        lines.append("None found. Best Sharpe configs:")
        by_sharpe = sorted(all_results, key=lambda x: x['l12m_sharpe'], reverse=True)
        for r in by_sharpe[:5]:
            lines.append(f"- **{r['label']}**: Sharpe={r['l12m_sharpe']:.2f}, 12M={r['l12m_return']*100:+.1f}%, MaxDD={r['l12m_max_dd']*100:.1f}%")
    lines.append("")

    # Full summary table sorted by 12M Calmar
    lines.append("---")
    lines.append("")
    lines.append("## All Configurations Ranked by 12M Calmar")
    lines.append("")
    lines.append("| Rank | Config | 12M Ret | 12M MaxDD | 12M Sharpe | 12M Calmar | Full Ret | Full MaxDD | Full Sharpe |")
    lines.append("|------|--------|---------|----------|-----------|-----------|---------|-----------|------------|")

    all_sorted = sorted(all_results, key=lambda x: x['l12m_calmar'], reverse=True)
    for rank, r in enumerate(all_sorted, 1):
        lines.append(
            f"| {rank} | {r['label']} "
            f"| {r['l12m_return']*100:+.1f}% "
            f"| {r['l12m_max_dd']*100:.1f}% "
            f"| {r['l12m_sharpe']:.2f} "
            f"| {r['l12m_calmar']:.1f} "
            f"| {r['total_return']*100:+.1f}% "
            f"| {r['max_dd']*100:.1f}% "
            f"| {r['sharpe']:.2f} |"
        )
    lines.append("")

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():
    t0 = time.time()
    log("=" * 70)
    log("R165 -- Low-Drawdown Portfolio Optimization")
    log("=" * 70)

    # ── Step 1: Load R158 data and run base strategies ──
    log("\n[1/6] Loading R158 token data...")
    r158_tokens = load_r158_tokens()
    log(f"  Loaded {len(r158_tokens)} tokens for R158")

    log("\n[2/6] Preparing R158 daily data...")
    (daily_close, daily_returns, lookback_returns_shifted,
     ema_signal_shifted, rolling_avg_dvol_shifted, hourly_fundings) = prepare_r158_daily_data(r158_tokens)
    log(f"  Daily close shape: {daily_close.shape}")

    log("\n[3/6] Running R158 K10 (L7_R7_K10_50/50_RF)...")
    t1 = time.time()
    r158_k10_eq = run_r158_momentum(
        daily_close, daily_returns, lookback_returns_shifted,
        ema_signal_shifted, rolling_avg_dvol_shifted, hourly_fundings, k=10
    )
    log(f"  Done in {time.time()-t1:.1f}s, equity curve length={len(r158_k10_eq)}")
    log(f"  Final equity: {r158_k10_eq.iloc[-1]:.4f}")

    log("\n[4/6] Running R158 K5 (L7_R7_K5_50/50_RF)...")
    t1 = time.time()
    r158_k5_eq = run_r158_momentum(
        daily_close, daily_returns, lookback_returns_shifted,
        ema_signal_shifted, rolling_avg_dvol_shifted, hourly_fundings, k=5
    )
    log(f"  Done in {time.time()-t1:.1f}s, equity curve length={len(r158_k5_eq)}")
    log(f"  Final equity: {r158_k5_eq.iloc[-1]:.4f}")

    # ── Step 2: Load R160 data and run breakout strategy ──
    log("\n[5/6] Loading R160 token data (4H resample, ~60s)...")
    t1 = time.time()
    r160_token_data = load_r160_token_data()
    log(f"  Loaded {len(r160_token_data)} tokens for R160 in {time.time()-t1:.1f}s")

    log("\n[6/6] Running R160 Variant B (1x leverage)...")
    t1 = time.time()
    r160_eq = run_r160_breakout(r160_token_data, leverage=1.0)
    log(f"  Done in {time.time()-t1:.1f}s, equity curve length={len(r160_eq)}")
    log(f"  Final equity: {r160_eq.iloc[-1]:.4f}")

    # ── Convert to daily returns ──
    log("\n[COMBINE] Converting to daily returns...")
    r158_k10_rets = equity_to_daily_returns(r158_k10_eq)
    r158_k5_rets = equity_to_daily_returns(r158_k5_eq)
    r160_rets = equity_to_daily_returns(r160_eq)

    strategy_returns = {
        'K10': r158_k10_rets,
        'K5': r158_k5_rets,
        'R160': r160_rets,
    }

    # Base strategy metrics
    base_metrics = {}
    for name, eq in [('R158 K10', r158_k10_eq), ('R158 K5', r158_k5_eq), ('R160 VarB', r160_eq)]:
        base_metrics[name] = compute_portfolio_metrics(eq, name)

    log("\nBase Strategy Performance:")
    for name, m in base_metrics.items():
        log(f"  {name}: Total={m['total_return']*100:+.1f}%, 12M={m['l12m_return']*100:+.1f}%, "
            f"MaxDD={m['max_dd']*100:.1f}%, Sharpe={m['sharpe']:.2f}")

    # ── Step 3: Build all configurations ──
    log("\n[CONFIGS] Building portfolio configurations...")

    configs = []

    # Group A: R160-heavy with DD control
    configs.append({
        'label': 'A1', 'alloc_desc': '30%K10+70%R160',
        'weights': {'K10': 0.30, 'R160': 0.70},
        'leverage': 3.0, 'dd_control': True,
    })
    configs.append({
        'label': 'A2', 'alloc_desc': '40%K10+60%R160',
        'weights': {'K10': 0.40, 'R160': 0.60},
        'leverage': 3.0, 'dd_control': True,
    })
    configs.append({
        'label': 'A3', 'alloc_desc': '20%K10+80%R160',
        'weights': {'K10': 0.20, 'R160': 0.80},
        'leverage': 4.0, 'dd_control': True,
    })
    configs.append({
        'label': 'A4', 'alloc_desc': '30%K5+70%R160',
        'weights': {'K5': 0.30, 'R160': 0.70},
        'leverage': 3.0, 'dd_control': True,
    })

    # Group B: Balanced with DD control
    configs.append({
        'label': 'B1', 'alloc_desc': '50%K10+50%R160',
        'weights': {'K10': 0.50, 'R160': 0.50},
        'leverage': 2.0, 'dd_control': True,
    })
    configs.append({
        'label': 'B2', 'alloc_desc': '50%K5+50%R160',
        'weights': {'K5': 0.50, 'R160': 0.50},
        'leverage': 2.0, 'dd_control': True,
    })
    configs.append({
        'label': 'B3', 'alloc_desc': '40%K10+30%K5+30%R160',
        'weights': {'K10': 0.40, 'K5': 0.30, 'R160': 0.30},
        'leverage': 2.0, 'dd_control': True,
    })

    # Group C: Same allocations WITHOUT DD control
    configs.append({
        'label': 'C1', 'alloc_desc': '30%K10+70%R160',
        'weights': {'K10': 0.30, 'R160': 0.70},
        'leverage': 3.0, 'dd_control': False,
    })
    configs.append({
        'label': 'C2', 'alloc_desc': '40%K10+60%R160',
        'weights': {'K10': 0.40, 'R160': 0.60},
        'leverage': 3.0, 'dd_control': False,
    })
    configs.append({
        'label': 'C3', 'alloc_desc': '20%K10+80%R160',
        'weights': {'K10': 0.20, 'R160': 0.80},
        'leverage': 4.0, 'dd_control': False,
    })
    configs.append({
        'label': 'C4', 'alloc_desc': '30%K5+70%R160',
        'weights': {'K5': 0.30, 'R160': 0.70},
        'leverage': 3.0, 'dd_control': False,
    })
    configs.append({
        'label': 'C5', 'alloc_desc': '50%K10+50%R160',
        'weights': {'K10': 0.50, 'R160': 0.50},
        'leverage': 2.0, 'dd_control': False,
    })
    configs.append({
        'label': 'C6', 'alloc_desc': '50%K5+50%R160',
        'weights': {'K5': 0.50, 'R160': 0.50},
        'leverage': 2.0, 'dd_control': False,
    })
    configs.append({
        'label': 'C7', 'alloc_desc': '40%K10+30%K5+30%R160',
        'weights': {'K10': 0.40, 'K5': 0.30, 'R160': 0.30},
        'leverage': 2.0, 'dd_control': False,
    })

    # Group D: Pure R160 with high leverage + DD control
    configs.append({
        'label': 'D1', 'alloc_desc': '100%R160',
        'weights': {'R160': 1.0},
        'leverage': 5.0, 'dd_control': True,
    })
    configs.append({
        'label': 'D2', 'alloc_desc': '100%R160',
        'weights': {'R160': 1.0},
        'leverage': 8.0, 'dd_control': True,
    })
    configs.append({
        'label': 'D3', 'alloc_desc': '100%R160',
        'weights': {'R160': 1.0},
        'leverage': 10.0, 'dd_control': True,
    })

    # Group E: K5-heavy at higher leverage with DD control (K5 has best 12M return)
    configs.append({
        'label': 'E1', 'alloc_desc': '30%K5+70%R160',
        'weights': {'K5': 0.30, 'R160': 0.70},
        'leverage': 4.0, 'dd_control': True,
    })
    configs.append({
        'label': 'E2', 'alloc_desc': '30%K5+70%R160',
        'weights': {'K5': 0.30, 'R160': 0.70},
        'leverage': 5.0, 'dd_control': True,
    })
    configs.append({
        'label': 'E3', 'alloc_desc': '50%K5+50%R160',
        'weights': {'K5': 0.50, 'R160': 0.50},
        'leverage': 3.0, 'dd_control': True,
    })
    configs.append({
        'label': 'E4', 'alloc_desc': '50%K5+50%R160',
        'weights': {'K5': 0.50, 'R160': 0.50},
        'leverage': 4.0, 'dd_control': True,
    })
    configs.append({
        'label': 'E5', 'alloc_desc': '20%K10+30%K5+50%R160',
        'weights': {'K10': 0.20, 'K5': 0.30, 'R160': 0.50},
        'leverage': 4.0, 'dd_control': True,
    })
    configs.append({
        'label': 'E6', 'alloc_desc': '40%K5+60%R160',
        'weights': {'K5': 0.40, 'R160': 0.60},
        'leverage': 4.0, 'dd_control': True,
    })
    configs.append({
        'label': 'E7', 'alloc_desc': '40%K5+60%R160',
        'weights': {'K5': 0.40, 'R160': 0.60},
        'leverage': 5.0, 'dd_control': True,
    })

    # Group F: Shorter trailing window (30d) for faster DD recovery
    configs.append({
        'label': 'F1', 'alloc_desc': '30%K5+70%R160',
        'weights': {'K5': 0.30, 'R160': 0.70},
        'leverage': 4.0, 'dd_control': True, 'dd_trailing_days': 30,
    })
    configs.append({
        'label': 'F2', 'alloc_desc': '30%K5+70%R160',
        'weights': {'K5': 0.30, 'R160': 0.70},
        'leverage': 5.0, 'dd_control': True, 'dd_trailing_days': 30,
    })
    configs.append({
        'label': 'F3', 'alloc_desc': '50%K5+50%R160',
        'weights': {'K5': 0.50, 'R160': 0.50},
        'leverage': 4.0, 'dd_control': True, 'dd_trailing_days': 30,
    })
    configs.append({
        'label': 'F4', 'alloc_desc': '50%K5+50%R160',
        'weights': {'K5': 0.50, 'R160': 0.50},
        'leverage': 5.0, 'dd_control': True, 'dd_trailing_days': 30,
    })
    configs.append({
        'label': 'F5', 'alloc_desc': '40%K5+60%R160',
        'weights': {'K5': 0.40, 'R160': 0.60},
        'leverage': 5.0, 'dd_control': True, 'dd_trailing_days': 30,
    })
    configs.append({
        'label': 'F6', 'alloc_desc': '30%K5+70%R160',
        'weights': {'K5': 0.30, 'R160': 0.70},
        'leverage': 6.0, 'dd_control': True, 'dd_trailing_days': 30,
    })
    configs.append({
        'label': 'F7', 'alloc_desc': '20%K10+30%K5+50%R160',
        'weights': {'K10': 0.20, 'K5': 0.30, 'R160': 0.50},
        'leverage': 5.0, 'dd_control': True, 'dd_trailing_days': 30,
    })

    log(f"  Total configurations: {len(configs)}")

    # ── Run all configurations ──
    all_results = []
    for cfg in configs:
        dd_trail = cfg.get('dd_trailing_days', 60)
        log(f"\n  Running {cfg['label']}: {cfg['alloc_desc']} @ {cfg['leverage']}x "
            f"{'+ DD control' if cfg['dd_control'] else '(no DD control)'}"
            f"{f' (trail={dd_trail}d)' if cfg['dd_control'] and dd_trail != 60 else ''}...")

        port_eq = combine_strategies(
            strategy_returns=strategy_returns,
            weights=cfg['weights'],
            leverage=cfg['leverage'],
            use_dd_control=cfg['dd_control'],
            dd_trailing_days=dd_trail,
            label=cfg['label'],
        )

        metrics = compute_portfolio_metrics(port_eq, cfg['label'])
        metrics['alloc_desc'] = cfg['alloc_desc']
        metrics['leverage'] = cfg['leverage']
        metrics['dd_control'] = cfg['dd_control']

        all_results.append(metrics)

        log(f"    12M: {metrics['l12m_return']*100:+.1f}%, MaxDD: {metrics['l12m_max_dd']*100:.1f}%, "
            f"Sharpe: {metrics['l12m_sharpe']:.2f}, Calmar: {metrics['l12m_calmar']:.1f}")
        log(f"    Full: {metrics['total_return']*100:+.1f}%, MaxDD: {metrics['max_dd']*100:.1f}%, "
            f"Sharpe: {metrics['sharpe']:.2f}")

    # ── Generate report ──
    log("\n[REPORT] Generating results report...")
    report = generate_report(all_results, base_metrics)

    with open(OUTPUT_PATH, 'w') as f:
        f.write(report)
    log(f"  Report written to {OUTPUT_PATH}")

    # ── Quick summary ──
    log("\n" + "=" * 70)
    log("QUICK SUMMARY")
    log("=" * 70)

    by_calmar = sorted(all_results, key=lambda x: x['l12m_calmar'], reverse=True)
    log("\nTop 5 by 12M Calmar:")
    for r in by_calmar[:5]:
        log(f"  {r['label']:4s}: 12M={r['l12m_return']*100:+.1f}%, MaxDD={r['l12m_max_dd']*100:.1f}%, "
            f"Sharpe={r['l12m_sharpe']:.2f}, Calmar={r['l12m_calmar']:.1f}")

    by_ret = sorted(all_results, key=lambda x: x['l12m_return'], reverse=True)
    log("\nTop 5 by 12M Return:")
    for r in by_ret[:5]:
        log(f"  {r['label']:4s}: 12M={r['l12m_return']*100:+.1f}%, MaxDD={r['l12m_max_dd']*100:.1f}%, "
            f"Sharpe={r['l12m_sharpe']:.2f}, Calmar={r['l12m_calmar']:.1f}")

    # Check for target hit
    target_hit = [r for r in all_results if r['l12m_return'] > 3.0 and r['l12m_max_dd'] > -0.20]
    if target_hit:
        log(f"\n*** TARGET HIT: {len(target_hit)} configs with 12M>300% AND MaxDD>-20% ***")
        for r in target_hit:
            log(f"  {r['label']}: 12M={r['l12m_return']*100:+.1f}%, MaxDD={r['l12m_max_dd']*100:.1f}%")
    else:
        log("\n  No configs hit 300%+/MaxDD<20% target.")
        # Show Pareto frontier
        log("  Pareto frontier (best return for given DD):")
        for dd_threshold in [-0.10, -0.15, -0.20, -0.30, -0.50]:
            candidates = [r for r in all_results if r['l12m_max_dd'] > dd_threshold]
            if candidates:
                best = max(candidates, key=lambda x: x['l12m_return'])
                log(f"    MaxDD>{dd_threshold*100:.0f}%: {best['label']} -> 12M={best['l12m_return']*100:+.1f}%, "
                    f"MaxDD={best['l12m_max_dd']*100:.1f}%")

    total_time = time.time() - t0
    log(f"\nTotal runtime: {total_time:.1f}s")


if __name__ == '__main__':
    main()
