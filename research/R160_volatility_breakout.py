#!/workspace/venv/bin/python
"""
R160: Volatility Breakout Rotation Strategy
=============================================

Core Idea: When a token breaks out of its recent range (high volatility expansion)
with volume confirmation, it tends to continue in that direction. We catch these
breakouts early and ride them.

Signal: Bollinger Band Breakout + Volume Confirmation (4H bars)
  1. Compute 20-period Bollinger Bands on 4H bars (BB_upper = SMA20 + 2*std, BB_lower = SMA20 - 2*std)
  2. Compute 20-period average volume on 4H bars
  3. BREAKOUT LONG:  Close > BB_upper AND volume > 1.5x avg volume
  4. BREAKOUT SHORT: Close < BB_lower AND volume > 1.5x avg volume
  5. No breakout: FLAT

Entry & Exit:
  - Entry: On breakout signal, enter at next bar's open
  - Trail stop: Close below SMA(20) for longs, above SMA(20) for shorts (4H bars)
  - Or: opposite breakout signal
  - Max hold: 30 days (180 4H bars)
  - Partial profit: At +3x ATR(20), close 50% and move stop to breakeven

Portfolio Construction:
  - Scan ALL tokens every 4 hours
  - Enter the top 5 strongest breakouts (by distance from BB as % of price)
  - Equal weight per position (20% each)
  - Both long and short positions allowed simultaneously
  - No more than 10 positions total

Variant A: Base (breakout + volume only)
Variant B: Momentum Filter (only take longs if 14-day return > 0, shorts if 14-day return < 0)

Costs: 7bps per side + funding from parquet
Leverage: 1x, 2x, 3x

Token Universe: All tokens with >1yr data and 30-day avg dollar volume > $2M

Author: Quant Research Agent
Date: 2026-03-28
"""

import sys
import warnings
import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
import time

warnings.filterwarnings('ignore')

# Force unbuffered output
def log(msg):
    print(msg)
    sys.stdout.flush()


# ── Paths ──────────────────────────────────────────────────────────────────────
PROJECT_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = PROJECT_DIR / 'data' / 'perp' / '1h_cache'
OUTPUT_MD = PROJECT_DIR / 'research' / 'R160_volatility_breakout_results.md'

# ── Constants ──────────────────────────────────────────────────────────────────
BB_PERIOD = 20          # Bollinger Band period (on 4H bars)
BB_STD_MULT = 2.0       # BB standard deviation multiplier
VOL_MULT = 1.5          # Volume must exceed 1.5x average
ATR_PERIOD = 20         # ATR period (on 4H bars)
SMA_PERIOD = 20         # SMA for trail stop (same as BB mid)

PARTIAL_PROFIT_ATR_MULT = 3.0   # Take 50% profit at 3x ATR
PARTIAL_CLOSE_FRAC = 0.5        # Close 50% at partial profit target
MAX_HOLD_BARS = 180              # 30 days * 6 bars/day = 180 4H bars

MOMENTUM_LOOKBACK_HOURS = 14 * 24  # 14 days in hours (for Variant B)
MOMENTUM_LOOKBACK_4H = 14 * 6      # 14 days in 4H bars = 84

TOP_N_BREAKOUTS = 5     # Enter top 5 strongest breakouts per scan
MAX_POSITIONS = 10      # No more than 10 positions total
POSITION_SIZE = 0.20    # 20% equity per position (equal weight)

COST_BPS_PER_SIDE = 7   # 7 bps per side (4 taker + 3 slippage)
COST_PER_SIDE = COST_BPS_PER_SIDE / 10000.0

LEVERAGE_LEVELS = [1, 2, 3]

MIN_DATA_DAYS = 365     # >1yr data required
MIN_AVG_DOLLAR_VOL = 2_000_000  # $2M daily avg dollar volume (30-day)

# Time constants
HOURS_PER_YEAR = 8760
BARS_4H_PER_YEAR = HOURS_PER_YEAR / 4  # 2190

# Last 12 months window
L12M_START = pd.Timestamp('2025-03-17')


# ══════════════════════════════════════════════════════════════════════════════
# DATA LOADING & PREPARATION
# ══════════════════════════════════════════════════════════════════════════════

def discover_tokens() -> List[str]:
    """Find all tokens with >1yr data."""
    tokens = []
    for f in sorted(DATA_DIR.glob('*_1h.parquet')):
        token = f.stem.replace('_1h', '')
        try:
            df = pd.read_parquet(f, columns=['close'])
            df.index = pd.to_datetime(df.index)
            duration_days = (df.index.max() - df.index.min()).days
            if duration_days >= MIN_DATA_DAYS:
                tokens.append(token)
        except Exception:
            pass
    return tokens


def load_and_resample_4h(token: str) -> Optional[pd.DataFrame]:
    """Load 1H data, resample to 4H OHLCV bars, compute indicators."""
    path = DATA_DIR / f'{token}_1h.parquet'
    if not path.exists():
        return None

    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep='first')]

    # Check minimum data length
    if (df.index.max() - df.index.min()).days < MIN_DATA_DAYS:
        return None

    # Resample to 4H bars
    df_4h = df.resample('4h').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
    }).dropna(subset=['close'])

    # Also resample funding (sum hourly funding over 4H)
    if 'funding_1h' in df.columns:
        funding_4h = df['funding_1h'].resample('4h').sum()
        df_4h['funding_4h'] = funding_4h
    else:
        df_4h['funding_4h'] = 0.0

    # Drop rows with NaN close
    df_4h = df_4h.dropna(subset=['close'])

    if len(df_4h) < BB_PERIOD + 50:
        return None

    # ── Indicators ──

    # Bollinger Bands (SMA-based)
    df_4h['sma20'] = df_4h['close'].rolling(BB_PERIOD).mean()
    df_4h['bb_std'] = df_4h['close'].rolling(BB_PERIOD).std()
    df_4h['bb_upper'] = df_4h['sma20'] + BB_STD_MULT * df_4h['bb_std']
    df_4h['bb_lower'] = df_4h['sma20'] - BB_STD_MULT * df_4h['bb_std']

    # Average volume (20-period on 4H)
    df_4h['avg_volume'] = df_4h['volume'].rolling(BB_PERIOD).mean()

    # Dollar volume (for filtering)
    df_4h['dollar_volume'] = df_4h['volume'] * df_4h['close']

    # ATR (20-period on 4H)
    tr_hl = df_4h['high'] - df_4h['low']
    tr_hc = (df_4h['high'] - df_4h['close'].shift(1)).abs()
    tr_lc = (df_4h['low'] - df_4h['close'].shift(1)).abs()
    df_4h['true_range'] = pd.concat([tr_hl, tr_hc, tr_lc], axis=1).max(axis=1)
    df_4h['atr'] = df_4h['true_range'].rolling(ATR_PERIOD).mean()

    # Breakout signals (raw — before volume confirmation, used for scanning)
    df_4h['breakout_long_raw'] = df_4h['close'] > df_4h['bb_upper']
    df_4h['breakout_short_raw'] = df_4h['close'] < df_4h['bb_lower']

    # Volume confirmation
    df_4h['volume_confirmed'] = df_4h['volume'] > VOL_MULT * df_4h['avg_volume']

    # Final breakout signals
    df_4h['breakout_long'] = df_4h['breakout_long_raw'] & df_4h['volume_confirmed']
    df_4h['breakout_short'] = df_4h['breakout_short_raw'] & df_4h['volume_confirmed']

    # Breakout strength: distance from BB as % of price
    df_4h['long_strength'] = ((df_4h['close'] - df_4h['bb_upper']) / df_4h['close']).clip(lower=0)
    df_4h['short_strength'] = ((df_4h['bb_lower'] - df_4h['close']) / df_4h['close']).clip(lower=0)

    # 14-day return (for Variant B momentum filter)
    df_4h['return_14d'] = df_4h['close'].pct_change(MOMENTUM_LOOKBACK_4H)

    # 30-day avg dollar volume (rolling, in 4H bars: 30*6=180)
    df_4h['avg_daily_dollar_vol'] = df_4h['dollar_volume'].rolling(180).mean() * 6  # 6 bars per day

    return df_4h


# ══════════════════════════════════════════════════════════════════════════════
# POSITION & TRADE TRACKING
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Position:
    token: str
    entry_time: pd.Timestamp
    entry_bar_idx: int          # index into the token's 4H bar array
    entry_price: float
    direction: int              # +1 long, -1 short
    size_frac: float            # fraction of portfolio equity at entry
    leverage: float
    entry_atr: float
    partial_taken: bool = False
    remaining_frac: float = 1.0  # 1.0 = full, 0.5 = half (after partial)
    breakeven_stop: bool = False
    bars_held: int = 0


@dataclass
class Trade:
    token: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: int
    entry_price: float
    exit_price: float
    pnl_pct: float       # return on total portfolio equity
    bars_held: int
    exit_reason: str      # 'trail_stop', 'opposite_breakout', 'max_hold', 'partial', 'end_of_data'


# ══════════════════════════════════════════════════════════════════════════════
# PORTFOLIO SIMULATION ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def run_portfolio_simulation(
    token_data: Dict[str, pd.DataFrame],
    leverage: float,
    variant: str = 'A',
) -> Tuple[List[Trade], pd.Series]:
    """
    Run the portfolio-level volatility breakout rotation.

    Every 4H bar:
      1. Update and check exits for existing positions
      2. Scan all tokens for new breakouts
      3. Rank by strength, enter top N (subject to max position constraint)

    Returns (trades, equity_curve).
    """
    fee = COST_PER_SIDE

    # Build aligned time index (union of all token timestamps)
    all_times = set()
    for token, df in token_data.items():
        all_times.update(df.index.tolist())
    all_times = sorted(all_times)

    if len(all_times) < 100:
        return [], pd.Series(dtype=float)

    # Build per-token arrays for fast access
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

    # Build time-to-index mapping per token
    token_time_idx = {}
    for token, arrays in token_arrays.items():
        idx_map = {}
        for i, t in enumerate(arrays['index']):
            idx_map[t] = i
        token_time_idx[token] = idx_map

    equity = 1.0
    equity_curve = {}
    positions: List[Position] = []
    trades: List[Trade] = []
    pending_entries = []  # (token, direction, strength) to enter at next bar

    warmup_bars = BB_PERIOD + 10  # skip warmup
    warmup_time = all_times[min(warmup_bars, len(all_times) - 1)]

    for ti, current_time in enumerate(all_times):
        if current_time < warmup_time:
            equity_curve[current_time] = equity
            continue

        if equity <= 0.01:
            equity_curve[current_time] = max(equity, 0.0)
            continue

        # ── 1. PROCESS PENDING ENTRIES (from previous bar's signals) ──
        if pending_entries:
            slots_available = MAX_POSITIONS - len(positions)
            if slots_available > 0:
                # Sort by strength descending, take top N
                pending_entries.sort(key=lambda x: x[2], reverse=True)
                to_enter = pending_entries[:min(TOP_N_BREAKOUTS, slots_available)]

                for token, direction, strength in to_enter:
                    if token not in token_time_idx or current_time not in token_time_idx[token]:
                        continue
                    bar_idx = token_time_idx[token][current_time]
                    arr = token_arrays[token]

                    # Sanity checks
                    if np.isnan(arr['open'][bar_idx]) or np.isnan(arr['atr'][bar_idx]):
                        continue
                    if arr['atr'][bar_idx] <= 0:
                        continue

                    entry_price = arr['open'][bar_idx]  # enter at next bar's open
                    entry_atr = arr['atr'][bar_idx]

                    # Check we don't already have a position in this token+direction
                    already_in = any(
                        p.token == token and p.direction == direction
                        for p in positions
                    )
                    if already_in:
                        continue

                    # Apply entry cost
                    entry_cost = fee * leverage
                    equity -= entry_cost * POSITION_SIZE

                    pos = Position(
                        token=token,
                        entry_time=current_time,
                        entry_bar_idx=bar_idx,
                        entry_price=entry_price,
                        direction=direction,
                        size_frac=POSITION_SIZE,
                        leverage=leverage,
                        entry_atr=entry_atr,
                    )
                    positions.append(pos)

            pending_entries = []

        # ── 2. UPDATE EXISTING POSITIONS (check exits) ──
        closed_indices = []
        for pidx, pos in enumerate(positions):
            token = pos.token
            if token not in token_time_idx or current_time not in token_time_idx[token]:
                # Token doesn't have data at this time — hold position
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

            # ── Partial profit: at +3x ATR, close 50% ──
            if not pos.partial_taken and atr_val > 0:
                target = pos.entry_price + pos.direction * PARTIAL_PROFIT_ATR_MULT * pos.entry_atr
                if pos.direction == 1 and close_price >= target:
                    # Take partial profit on longs
                    partial_return = (close_price / pos.entry_price - 1.0) * leverage
                    partial_cost = fee * leverage  # exit cost for partial
                    partial_funding = _calc_funding(arr, pos.entry_bar_idx, bar_idx, pos.direction, leverage)
                    partial_pnl = (partial_return - partial_cost - partial_funding) * pos.size_frac * PARTIAL_CLOSE_FRAC
                    equity += partial_pnl
                    trades.append(Trade(
                        token=token, entry_time=pos.entry_time, exit_time=current_time,
                        direction=pos.direction, entry_price=pos.entry_price,
                        exit_price=close_price, pnl_pct=partial_pnl,
                        bars_held=pos.bars_held, exit_reason='partial',
                    ))
                    pos.partial_taken = True
                    pos.remaining_frac = 1.0 - PARTIAL_CLOSE_FRAC
                    pos.breakeven_stop = True  # Move stop to breakeven
                    # Don't close position — continue with remaining half

                elif pos.direction == -1 and close_price <= target:
                    # Take partial profit on shorts
                    partial_return = (1.0 - close_price / pos.entry_price) * leverage
                    partial_cost = fee * leverage
                    partial_funding = _calc_funding(arr, pos.entry_bar_idx, bar_idx, pos.direction, leverage)
                    partial_pnl = (partial_return - partial_cost - partial_funding) * pos.size_frac * PARTIAL_CLOSE_FRAC
                    equity += partial_pnl
                    trades.append(Trade(
                        token=token, entry_time=pos.entry_time, exit_time=current_time,
                        direction=pos.direction, entry_price=pos.entry_price,
                        exit_price=close_price, pnl_pct=partial_pnl,
                        bars_held=pos.bars_held, exit_reason='partial',
                    ))
                    pos.partial_taken = True
                    pos.remaining_frac = 1.0 - PARTIAL_CLOSE_FRAC
                    pos.breakeven_stop = True

            # ── Trail stop: close below SMA(20) for longs, above for shorts ──
            if pos.direction == 1 and close_price < sma_val:
                exit_reason = 'trail_stop'
                exit_price = close_price
            elif pos.direction == -1 and close_price > sma_val:
                exit_reason = 'trail_stop'
                exit_price = close_price

            # ── Breakeven stop (after partial taken) ──
            if exit_reason is None and pos.breakeven_stop:
                if pos.direction == 1 and close_price < pos.entry_price:
                    exit_reason = 'trail_stop'
                    exit_price = close_price
                elif pos.direction == -1 and close_price > pos.entry_price:
                    exit_reason = 'trail_stop'
                    exit_price = close_price

            # ── Max hold ──
            if exit_reason is None and pos.bars_held >= MAX_HOLD_BARS:
                exit_reason = 'max_hold'
                exit_price = close_price

            # ── Opposite breakout signal ──
            if exit_reason is None:
                if pos.direction == 1 and bar_idx < len(arr['breakout_short']) and arr['breakout_short'][bar_idx]:
                    exit_reason = 'opposite_breakout'
                    exit_price = close_price
                elif pos.direction == -1 and bar_idx < len(arr['breakout_long']) and arr['breakout_long'][bar_idx]:
                    exit_reason = 'opposite_breakout'
                    exit_price = close_price

            if exit_reason is not None:
                # Close remaining position
                if pos.direction == 1:
                    raw_return = (exit_price / pos.entry_price - 1.0)
                else:
                    raw_return = (1.0 - exit_price / pos.entry_price)

                levered_return = raw_return * leverage
                exit_cost = fee * leverage
                funding_cost = _calc_funding(arr, pos.entry_bar_idx, bar_idx, pos.direction, leverage)

                # Only charge funding on remaining fraction if partial was taken
                effective_size = pos.size_frac * pos.remaining_frac
                pnl = (levered_return - exit_cost - funding_cost) * effective_size

                equity += pnl
                trades.append(Trade(
                    token=token, entry_time=pos.entry_time, exit_time=current_time,
                    direction=pos.direction, entry_price=pos.entry_price,
                    exit_price=exit_price, pnl_pct=pnl,
                    bars_held=pos.bars_held, exit_reason=exit_reason,
                ))
                closed_indices.append(pidx)

        # Remove closed positions (reverse order)
        for pidx in sorted(closed_indices, reverse=True):
            positions.pop(pidx)

        # ── 3. SCAN FOR NEW BREAKOUTS ──
        new_candidates = []
        for token, arr in token_arrays.items():
            if current_time not in token_time_idx[token]:
                continue
            bar_idx = token_time_idx[token][current_time]

            # Skip if not enough data
            if bar_idx < BB_PERIOD + 5:
                continue

            # Skip if NaN indicators
            if np.isnan(arr['bb_upper'][bar_idx]) or np.isnan(arr['atr'][bar_idx]):
                continue

            # Check minimum dollar volume
            if np.isnan(arr['avg_daily_dollar_vol'][bar_idx]):
                continue
            if arr['avg_daily_dollar_vol'][bar_idx] < MIN_AVG_DOLLAR_VOL:
                continue

            # Check breakout signals
            if arr['breakout_long'][bar_idx]:
                strength = arr['long_strength'][bar_idx]
                if np.isnan(strength) or strength <= 0:
                    continue

                # Variant B: momentum filter
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

                # Variant B: momentum filter
                if variant == 'B':
                    if bar_idx < MOMENTUM_LOOKBACK_4H:
                        continue
                    ret_14d = arr['return_14d'][bar_idx]
                    if np.isnan(ret_14d) or ret_14d >= 0:
                        continue

                new_candidates.append((token, -1, strength))

        # Queue entries for next bar (avoid look-ahead: signal on this bar, enter next bar)
        pending_entries = new_candidates

        equity_curve[current_time] = equity

    # ── Close remaining positions at end ──
    last_time = all_times[-1]
    for pos in positions:
        token = pos.token
        if token in token_time_idx and last_time in token_time_idx[token]:
            bar_idx = token_time_idx[token][last_time]
            arr = token_arrays[token]
            exit_price = arr['close'][bar_idx]
        else:
            # Use last available price
            arr = token_arrays[token]
            exit_price = arr['close'][-1]
            bar_idx = len(arr['close']) - 1

        if pos.direction == 1:
            raw_return = (exit_price / pos.entry_price - 1.0)
        else:
            raw_return = (1.0 - exit_price / pos.entry_price)

        levered_return = raw_return * leverage
        exit_cost = fee * leverage
        # Approximate funding from entry to last available bar
        funding_cost = _calc_funding(arr, pos.entry_bar_idx, min(bar_idx, len(arr['funding_4h']) - 1),
                                     pos.direction, leverage)
        effective_size = pos.size_frac * pos.remaining_frac
        pnl = (levered_return - exit_cost - funding_cost) * effective_size
        equity += pnl

        trades.append(Trade(
            token=token, entry_time=pos.entry_time, exit_time=last_time,
            direction=pos.direction, entry_price=pos.entry_price,
            exit_price=exit_price, pnl_pct=pnl,
            bars_held=pos.bars_held, exit_reason='end_of_data',
        ))

    equity_curve[last_time] = equity

    eq_series = pd.Series(equity_curve).sort_index()
    return trades, eq_series


def _calc_funding(arr: dict, start_idx: int, end_idx: int, direction: int, leverage: float) -> float:
    """Calculate cumulative funding cost between two bar indices."""
    if start_idx >= end_idx:
        return 0.0
    funding_slice = arr['funding_4h'][start_idx:end_idx]
    total = np.nansum(funding_slice)
    if direction == 1:
        return total * leverage       # longs pay positive funding
    else:
        return -total * leverage      # shorts collect positive funding


# ══════════════════════════════════════════════════════════════════════════════
# METRICS
# ══════════════════════════════════════════════════════════════════════════════

def compute_metrics(trades: List[Trade], eq_series: pd.Series,
                    start_date: Optional[pd.Timestamp] = None) -> Dict:
    """Compute performance metrics."""
    if start_date is not None:
        eq = eq_series.loc[eq_series.index >= start_date]
        relevant_trades = [t for t in trades if t.exit_time >= start_date]
    else:
        eq = eq_series
        relevant_trades = trades

    if len(eq) < 2:
        return _empty_metrics()

    total_return = eq.iloc[-1] / eq.iloc[0] - 1.0
    hours = (eq.index[-1] - eq.index[0]).total_seconds() / 3600
    if hours <= 0:
        return _empty_metrics()

    # Annualise
    if total_return <= -1.0:
        annual_return = -1.0
    else:
        annual_return = (1 + total_return) ** (HOURS_PER_YEAR / hours) - 1.0

    # Returns for Sharpe (4H bars)
    bar_returns = eq.pct_change().dropna()
    bar_returns = bar_returns.replace([np.inf, -np.inf], 0.0)
    if len(bar_returns) < 24:
        return _empty_metrics()

    std = bar_returns.std()
    # Annualise Sharpe: bars are 4H, so sqrt(BARS_4H_PER_YEAR) annualisation factor
    sharpe = (bar_returns.mean() / std) * np.sqrt(BARS_4H_PER_YEAR) if std > 1e-10 else 0.0

    # Max drawdown
    cummax = eq.cummax()
    dd = (eq - cummax) / cummax
    max_dd = dd.min()

    # Calmar
    calmar = annual_return / abs(max_dd) if abs(max_dd) > 0.001 else 0.0

    # Trade stats
    n_trades = len(relevant_trades)
    if n_trades > 0:
        wins = [t for t in relevant_trades if t.pnl_pct > 0]
        losses = [t for t in relevant_trades if t.pnl_pct <= 0]
        win_rate = len(wins) / n_trades
        avg_winner = np.mean([t.pnl_pct for t in wins]) if wins else 0.0
        avg_loser = np.mean([t.pnl_pct for t in losses]) if losses else 0.0
        gross_profit = sum(t.pnl_pct for t in wins) if wins else 0.0
        gross_loss = abs(sum(t.pnl_pct for t in losses)) if losses else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0.001 else 99.9
        avg_hold_bars = np.mean([t.bars_held for t in relevant_trades])
        avg_hold_hours = avg_hold_bars * 4  # 4H bars
        avg_hold_days = avg_hold_hours / 24
    else:
        win_rate = 0.0
        avg_winner = 0.0
        avg_loser = 0.0
        profit_factor = 0.0
        avg_hold_bars = 0.0
        avg_hold_hours = 0.0
        avg_hold_days = 0.0

    # Monthly returns
    monthly_returns = eq.resample('ME').last().pct_change().dropna()

    return {
        'annual_return': annual_return,
        'total_return': total_return,
        'sharpe': sharpe,
        'calmar': calmar,
        'max_dd': max_dd,
        'n_trades': n_trades,
        'win_rate': win_rate,
        'avg_winner': avg_winner,
        'avg_loser': avg_loser,
        'profit_factor': profit_factor,
        'avg_hold_days': avg_hold_days,
        'monthly_returns': monthly_returns,
    }


def _empty_metrics() -> Dict:
    return {
        'annual_return': 0.0, 'total_return': 0.0,
        'sharpe': 0.0, 'calmar': 0.0, 'max_dd': 0.0,
        'n_trades': 0, 'win_rate': 0.0, 'avg_winner': 0.0,
        'avg_loser': 0.0, 'profit_factor': 0.0,
        'avg_hold_days': 0.0,
        'monthly_returns': pd.Series(dtype=float),
    }


# ══════════════════════════════════════════════════════════════════════════════
# ANALYSIS HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def token_contribution(trades: List[Trade]) -> pd.DataFrame:
    """Compute PnL contribution by token."""
    if not trades:
        return pd.DataFrame()

    df = pd.DataFrame([{
        'token': t.token,
        'direction': 'LONG' if t.direction == 1 else 'SHORT',
        'pnl_pct': t.pnl_pct,
        'bars_held': t.bars_held,
        'exit_reason': t.exit_reason,
    } for t in trades])

    token_stats = df.groupby('token').agg(
        n_trades=('pnl_pct', 'count'),
        total_pnl=('pnl_pct', 'sum'),
        avg_pnl=('pnl_pct', 'mean'),
        win_rate=('pnl_pct', lambda x: (x > 0).mean()),
        avg_hold_bars=('bars_held', 'mean'),
    ).sort_values('total_pnl', ascending=False)

    return token_stats


def trade_analysis(trades: List[Trade]) -> Dict:
    """Detailed trade analysis."""
    if not trades:
        return {}

    df = pd.DataFrame([{
        'token': t.token,
        'direction': t.direction,
        'pnl_pct': t.pnl_pct,
        'bars_held': t.bars_held,
        'exit_reason': t.exit_reason,
        'entry_time': t.entry_time,
        'exit_time': t.exit_time,
    } for t in trades])

    longs = df[df['direction'] == 1]
    shorts = df[df['direction'] == -1]

    result = {
        'total_trades': len(df),
        'long_trades': len(longs),
        'short_trades': len(shorts),
        'long_win_rate': (longs['pnl_pct'] > 0).mean() if len(longs) > 0 else 0,
        'short_win_rate': (shorts['pnl_pct'] > 0).mean() if len(shorts) > 0 else 0,
        'avg_hold_days': df['bars_held'].mean() * 4 / 24,
        'median_hold_days': df['bars_held'].median() * 4 / 24,
    }

    # Exit reason breakdown
    exit_counts = df['exit_reason'].value_counts()
    result['exit_reasons'] = exit_counts.to_dict()

    # Avg PnL by exit reason
    exit_pnl = df.groupby('exit_reason')['pnl_pct'].mean()
    result['exit_avg_pnl'] = exit_pnl.to_dict()

    return result


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    t_start = time.time()
    log("=" * 70)
    log("R160 -- Volatility Breakout Rotation Strategy")
    log("=" * 70)

    # ── Discover tokens ──
    log("\n[1] Discovering tokens with >1yr data...")
    all_tokens = discover_tokens()
    log(f"  Found {len(all_tokens)} tokens with >1yr data")

    # ── Load and resample all tokens ──
    log("\n[2] Loading and resampling to 4H bars...")
    token_data_raw = {}
    load_t0 = time.time()
    for i, token in enumerate(all_tokens):
        if (i + 1) % 20 == 0:
            log(f"  Loading {i+1}/{len(all_tokens)}...")
        df_4h = load_and_resample_4h(token)
        if df_4h is not None:
            token_data_raw[token] = df_4h
    load_elapsed = time.time() - load_t0
    log(f"  Loaded {len(token_data_raw)} tokens in {load_elapsed:.1f}s")

    # ── Filter by minimum dollar volume ──
    log("\n[3] Filtering by minimum 30-day avg dollar volume ($2M)...")
    token_data = {}
    for token, df_4h in token_data_raw.items():
        # Check if token ever had sufficient volume (use last available value)
        valid_vol = df_4h['avg_daily_dollar_vol'].dropna()
        if len(valid_vol) > 0 and valid_vol.iloc[-1] >= MIN_AVG_DOLLAR_VOL:
            token_data[token] = df_4h
    log(f"  {len(token_data)} tokens pass volume filter (out of {len(token_data_raw)})")

    qualified_tokens = sorted(token_data.keys())
    log(f"  Qualified tokens: {', '.join(qualified_tokens[:20])}{'...' if len(qualified_tokens) > 20 else ''}")

    # ── Run simulations ──
    results = {}  # (variant, leverage) -> (trades, eq_series, metrics_full, metrics_12m)

    for variant in ['A', 'B']:
        log(f"\n{'='*70}")
        log(f"VARIANT {variant}: {'Base Breakout + Volume' if variant == 'A' else 'Breakout + Volume + Momentum Filter'}")
        log(f"{'='*70}")

        for lev in LEVERAGE_LEVELS:
            sim_t0 = time.time()
            log(f"\n  Simulating {variant} @ {lev}x leverage...")

            trades, eq_series = run_portfolio_simulation(token_data, leverage=lev, variant=variant)

            sim_elapsed = time.time() - sim_t0
            log(f"  Simulation took {sim_elapsed:.1f}s, {len(trades)} trades")

            full_metrics = compute_metrics(trades, eq_series)
            l12m_metrics = compute_metrics(trades, eq_series, start_date=L12M_START)

            results[(variant, lev)] = (trades, eq_series, full_metrics, l12m_metrics)

            fm = full_metrics
            lm = l12m_metrics
            log(f"  FULL:  Ann={fm['annual_return']:+.1%}  Sharpe={fm['sharpe']:.2f}  "
                f"MaxDD={fm['max_dd']:.1%}  Calmar={fm['calmar']:.2f}  "
                f"Trades={fm['n_trades']}  WR={fm['win_rate']:.0%}  PF={fm['profit_factor']:.2f}  "
                f"AvgHold={fm['avg_hold_days']:.1f}d")
            log(f"  L12M:  Ann={lm['annual_return']:+.1%}  Sharpe={lm['sharpe']:.2f}  "
                f"MaxDD={lm['max_dd']:.1%}  Calmar={lm['calmar']:.2f}  "
                f"Trades={lm['n_trades']}  WR={lm['win_rate']:.0%}  PF={lm['profit_factor']:.2f}")

    # ── Analysis: token contributions & trade stats ──
    log(f"\n{'='*70}")
    log("DETAILED ANALYSIS (Variant A, 1x)")
    log(f"{'='*70}")

    base_trades = results[('A', 1)][0]
    tc = token_contribution(base_trades)
    if len(tc) > 0:
        log("\nTop 10 Contributing Tokens (by total PnL):")
        log(tc.head(10).to_string())

    ta = trade_analysis(base_trades)
    if ta:
        log(f"\nTrade Analysis:")
        log(f"  Total: {ta['total_trades']} ({ta['long_trades']} long, {ta['short_trades']} short)")
        log(f"  Long WR: {ta['long_win_rate']:.0%}  Short WR: {ta['short_win_rate']:.0%}")
        log(f"  Avg hold: {ta['avg_hold_days']:.1f} days  Median: {ta['median_hold_days']:.1f} days")
        log(f"  Exit reasons: {ta['exit_reasons']}")
        log(f"  Avg PnL by exit: {ta['exit_avg_pnl']}")

    # ── Write results ──
    log(f"\n[WRITE] Writing results to {OUTPUT_MD}...")
    write_results(results, token_data, qualified_tokens)

    total_time = time.time() - t_start
    log(f"\nTotal runtime: {total_time:.1f}s")
    log("Done.")


# ══════════════════════════════════════════════════════════════════════════════
# RESULTS OUTPUT
# ══════════════════════════════════════════════════════════════════════════════

def write_results(results: Dict, token_data: Dict, qualified_tokens: List[str]):
    """Write comprehensive results to markdown."""
    lines = []

    lines.append("# R160 -- Volatility Breakout Rotation Strategy Results")
    lines.append("")
    lines.append("## Strategy Summary")
    lines.append("")
    lines.append("**Core Idea**: Bollinger Band breakout + volume confirmation on 4H bars, rotated across all liquid tokens.")
    lines.append("")
    lines.append("### Signal")
    lines.append(f"- Bollinger Bands: {BB_PERIOD}-period SMA +/- {BB_STD_MULT} std on 4H bars")
    lines.append(f"- Volume confirmation: volume > {VOL_MULT}x 20-period avg volume")
    lines.append(f"- BREAKOUT LONG: Close > BB_upper AND volume confirmed")
    lines.append(f"- BREAKOUT SHORT: Close < BB_lower AND volume confirmed")
    lines.append("")
    lines.append("### Entry & Exit")
    lines.append("- Entry: At next 4H bar open after breakout signal")
    lines.append(f"- Trail stop: Close below SMA({SMA_PERIOD}) for longs, above for shorts")
    lines.append(f"- Partial profit: At +{PARTIAL_PROFIT_ATR_MULT}x ATR({ATR_PERIOD}), close {PARTIAL_CLOSE_FRAC:.0%}, move stop to breakeven")
    lines.append(f"- Max hold: {MAX_HOLD_BARS} bars ({MAX_HOLD_BARS * 4 / 24:.0f} days)")
    lines.append("- Or: opposite breakout signal")
    lines.append("")
    lines.append("### Portfolio Construction")
    lines.append(f"- Scan all {len(qualified_tokens)} qualified tokens every 4H")
    lines.append(f"- Enter top {TOP_N_BREAKOUTS} strongest breakouts (by distance from BB as % of price)")
    lines.append(f"- Equal weight: {POSITION_SIZE:.0%} per position")
    lines.append(f"- Max {MAX_POSITIONS} positions total")
    lines.append("")
    lines.append("### Costs & Data")
    lines.append(f"- {COST_BPS_PER_SIDE} bps per side (4 taker + 3 slippage) + funding from parquet")
    lines.append(f"- Token universe: {len(qualified_tokens)} tokens with >1yr data and $2M+ daily volume")
    lines.append("")
    lines.append("### Variants")
    lines.append("- **Variant A (Base)**: Breakout + volume confirmation only")
    lines.append("- **Variant B (Momentum Filter)**: Same as A, but longs only if 14-day return > 0, shorts only if 14-day return < 0")
    lines.append("")

    # ── Summary table ──
    lines.append("## Performance Summary")
    lines.append("")
    lines.append("| Variant | Leverage | Ann.Ret(Full) | Ann.Ret(12mo) | Sharpe(Full) | Sharpe(12mo) | MaxDD(Full) | MaxDD(12mo) | Calmar | Trades | WR | PF | AvgHold |")
    lines.append("|---------|----------|---------------|---------------|-------------|-------------|------------|------------|--------|--------|----|----|---------|")

    for variant in ['A', 'B']:
        for lev in LEVERAGE_LEVELS:
            key = (variant, lev)
            if key not in results:
                continue
            trades, eq, fm, lm = results[key]
            lines.append(
                f"| {variant} | {lev}x "
                f"| {fm['annual_return']:+.1%} "
                f"| {lm['annual_return']:+.1%} "
                f"| {fm['sharpe']:.2f} "
                f"| {lm['sharpe']:.2f} "
                f"| {fm['max_dd']:.1%} "
                f"| {lm['max_dd']:.1%} "
                f"| {fm['calmar']:.2f} "
                f"| {fm['n_trades']} "
                f"| {fm['win_rate']:.0%} "
                f"| {fm['profit_factor']:.2f} "
                f"| {fm['avg_hold_days']:.1f}d |"
            )
    lines.append("")

    # ── Trade analysis for best variant ──
    lines.append("## Trade Analysis (Variant A, 1x)")
    lines.append("")

    base_trades = results[('A', 1)][0]
    ta = trade_analysis(base_trades)
    if ta:
        lines.append(f"- **Total trades**: {ta['total_trades']} ({ta['long_trades']} long, {ta['short_trades']} short)")
        lines.append(f"- **Long win rate**: {ta['long_win_rate']:.1%}")
        lines.append(f"- **Short win rate**: {ta['short_win_rate']:.1%}")
        lines.append(f"- **Avg hold time**: {ta['avg_hold_days']:.1f} days")
        lines.append(f"- **Median hold time**: {ta['median_hold_days']:.1f} days")
        lines.append("")

        # Avg winner vs avg loser
        fm_a1 = results[('A', 1)][2]
        lines.append(f"- **Avg winner**: {fm_a1['avg_winner']:+.4%} (of portfolio equity)")
        lines.append(f"- **Avg loser**: {fm_a1['avg_loser']:+.4%} (of portfolio equity)")
        lines.append("")

        # Exit reason breakdown
        lines.append("### Exit Reason Breakdown")
        lines.append("")
        lines.append("| Exit Reason | Count | Avg PnL |")
        lines.append("|-------------|-------|---------|")
        for reason, count in sorted(ta.get('exit_reasons', {}).items(), key=lambda x: -x[1]):
            avg_pnl = ta.get('exit_avg_pnl', {}).get(reason, 0)
            lines.append(f"| {reason} | {count} | {avg_pnl:+.4%} |")
        lines.append("")

    # ── Token contributions ──
    lines.append("## Top Contributing Tokens (Variant A, 1x)")
    lines.append("")

    tc = token_contribution(base_trades)
    if len(tc) > 0:
        lines.append("### Best performers")
        lines.append("")
        lines.append("| Token | Trades | Total PnL | Avg PnL | Win Rate | Avg Hold (bars) |")
        lines.append("|-------|--------|-----------|---------|----------|-----------------|")
        top_10 = tc.head(10)
        for token, row in top_10.iterrows():
            lines.append(
                f"| {token} | {row['n_trades']:.0f} "
                f"| {row['total_pnl']:+.4%} "
                f"| {row['avg_pnl']:+.4%} "
                f"| {row['win_rate']:.0%} "
                f"| {row['avg_hold_bars']:.0f} |"
            )
        lines.append("")

        lines.append("### Worst performers")
        lines.append("")
        lines.append("| Token | Trades | Total PnL | Avg PnL | Win Rate | Avg Hold (bars) |")
        lines.append("|-------|--------|-----------|---------|----------|-----------------|")
        bottom_10 = tc.tail(10)
        for token, row in bottom_10.iterrows():
            lines.append(
                f"| {token} | {row['n_trades']:.0f} "
                f"| {row['total_pnl']:+.4%} "
                f"| {row['avg_pnl']:+.4%} "
                f"| {row['win_rate']:.0%} "
                f"| {row['avg_hold_bars']:.0f} |"
            )
        lines.append("")

    # ── Monthly returns ──
    lines.append("## Monthly Returns")
    lines.append("")

    for variant in ['A', 'B']:
        lev = 1  # Show 1x leverage monthly returns
        key = (variant, lev)
        if key not in results:
            continue
        fm = results[key][2]
        monthly = fm.get('monthly_returns', pd.Series(dtype=float))
        if len(monthly) == 0:
            continue

        lines.append(f"### Variant {variant} ({lev}x)")
        lines.append("")

        # Pivot to year x month table
        monthly_df = monthly.to_frame('return')
        monthly_df['year'] = monthly_df.index.year
        monthly_df['month'] = monthly_df.index.month

        years = sorted(monthly_df['year'].unique())
        month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                       'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

        lines.append("| Year | " + " | ".join(month_names) + " | YTD |")
        lines.append("|------|" + "|".join(["------"] * 12) + "|------|")

        for year in years:
            year_data = monthly_df[monthly_df['year'] == year]
            row = f"| {year} "
            ytd = 1.0
            for m in range(1, 13):
                month_data = year_data[year_data['month'] == m]
                if len(month_data) > 0:
                    ret = month_data['return'].iloc[0]
                    ytd *= (1 + ret)
                    row += f"| {ret:+.1%} "
                else:
                    row += "| - "
            row += f"| {ytd - 1:+.1%} |"
            lines.append(row)

        lines.append("")

    # ── Variant B vs A comparison ──
    lines.append("## Variant B vs A Comparison")
    lines.append("")
    lines.append("Variant B adds a 14-day momentum filter: only take long breakouts when the token's 14-day return is positive, and short breakouts when negative. This filters out false breakouts against the prevailing trend.")
    lines.append("")
    lines.append("| Metric | A (1x) | B (1x) | A (2x) | B (2x) | A (3x) | B (3x) |")
    lines.append("|--------|--------|--------|--------|--------|--------|--------|")

    for metric_name, metric_key, fmt in [
        ('Ann. Return', 'annual_return', '{:+.1%}'),
        ('Sharpe', 'sharpe', '{:.2f}'),
        ('Max DD', 'max_dd', '{:.1%}'),
        ('Calmar', 'calmar', '{:.2f}'),
        ('Trades', 'n_trades', '{:.0f}'),
        ('Win Rate', 'win_rate', '{:.0%}'),
        ('Profit Factor', 'profit_factor', '{:.2f}'),
    ]:
        row = f"| {metric_name} "
        for lev in LEVERAGE_LEVELS:
            for variant in ['A', 'B']:
                key = (variant, lev)
                if key in results:
                    val = results[key][2][metric_key]
                    row += f"| {fmt.format(val)} "
                else:
                    row += "| - "
        row += "|"
        lines.append(row)
    lines.append("")

    # ── Qualified tokens list ──
    lines.append("## Token Universe")
    lines.append("")
    lines.append(f"**{len(qualified_tokens)} tokens** passed filters (>1yr data, $2M+ daily volume):")
    lines.append("")
    # Display in rows of 10
    for i in range(0, len(qualified_tokens), 10):
        chunk = qualified_tokens[i:i+10]
        lines.append(", ".join(chunk))
    lines.append("")

    # ── Notes ──
    lines.append("## Notes")
    lines.append("")
    lines.append("- 4H bars resampled from 1H data (OHLCV aggregation)")
    lines.append("- Signals generated on bar close, entry at NEXT bar open (no look-ahead)")
    lines.append("- Volume confirmation is key: breakouts without volume are filtered out")
    lines.append("- Funding applied per 4H bar: longs pay positive funding, shorts collect")
    lines.append(f"- Warmup period: first {BB_PERIOD + 10} bars skipped for indicator convergence")
    lines.append("- Portfolio rotation: every 4H bar, scan all tokens, rank breakouts, enter top 5")
    lines.append("- Partial profit mechanism closes 50% at 3x ATR, moves stop to breakeven")
    lines.append("- Trail stop uses SMA(20) cross (not ATR-based) for trend-following exits")
    lines.append("")

    with open(OUTPUT_MD, 'w') as fh:
        fh.write('\n'.join(lines))
    log(f"  Results written to {OUTPUT_MD}")


if __name__ == '__main__':
    main()
