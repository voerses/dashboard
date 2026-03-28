#!/workspace/venv/bin/python
"""
R167: Optimize R160 (Volatility Breakout) for HIGHER RETURNS
==============================================================

Goal: Boost R160 Variant B from +29.9% 12mo to 80-150%+ while keeping MaxDD < 15%.
      The sweet spot enables 2-3x leverage for 300%+ returns with manageable DD.

Approach: 3-phase smart parameter sweep
  Phase 1: Individual parameter sweeps (hold defaults, vary one at a time) — 22 tests
  Phase 2: Full combo sweep of top values from Phase 1 — ~20-50 tests
  Phase 3: Add per-token regime filter (EMA 10h/30h) to best configs

Parameters swept:
  1. BB periods: [10, 15, 20, 30]
  2. BB std multiplier: [1.5, 2.0, 2.5]
  3. Volume threshold: [1.2, 1.5, 2.0]
  4. Max positions: [5, 10, 15]
  5. Momentum filter lookback: [7, 14, 21] days
  6. Partial profit at: [2.0, 3.0, 5.0] x ATR
  7. Trail stop SMA period: [10, 20, 30]

Base (R160 Variant B defaults): BB=20, std=2.0, vol=1.5, maxpos=10, mom=14d, partial=3.0, trail=20

Costs: 7bps per side + funding from parquet
Data: 4H bars from 1h_cache
Token universe: >1yr data, $2M+ daily volume

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
from itertools import product
import time

warnings.filterwarnings('ignore')

def log(msg):
    print(msg)
    sys.stdout.flush()


# ── Paths ──────────────────────────────────────────────────────────────────────
PROJECT_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = PROJECT_DIR / 'data' / 'perp' / '1h_cache'
OUTPUT_MD = PROJECT_DIR / 'research' / 'R167_r160_optimized_results.md'

# ── Default (R160 Variant B) parameters ──────────────────────────────────────
DEFAULTS = {
    'bb_period': 20,
    'bb_std': 2.0,
    'vol_mult': 1.5,
    'max_positions': 10,
    'mom_days': 14,
    'partial_atr_mult': 3.0,
    'trail_sma': 20,
}

# ── Sweep ranges ──────────────────────────────────────────────────────────────
PARAM_RANGES = {
    'bb_period': [10, 15, 20, 30],
    'bb_std': [1.5, 2.0, 2.5],
    'vol_mult': [1.2, 1.5, 2.0],
    'max_positions': [3, 5, 10, 15],
    'mom_days': [7, 14, 21],
    'partial_atr_mult': [2.0, 3.0, 5.0],
    'trail_sma': [10, 20, 30],
}

# ── Fixed constants ───────────────────────────────────────────────────────────
ATR_PERIOD = 20
PARTIAL_CLOSE_FRAC = 0.5
MAX_HOLD_BARS = 180   # 30 days in 4H bars
TOP_N_BREAKOUTS = 5
COST_BPS_PER_SIDE = 7
COST_PER_SIDE = COST_BPS_PER_SIDE / 10000.0
MIN_DATA_DAYS = 365
MIN_AVG_DOLLAR_VOL = 2_000_000
HOURS_PER_YEAR = 8760
BARS_4H_PER_YEAR = HOURS_PER_YEAR / 4  # 2190
L12M_START = pd.Timestamp('2025-03-17')


# ══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
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


def load_1h_data(token: str) -> Optional[pd.DataFrame]:
    """Load raw 1H data for a token."""
    path = DATA_DIR / f'{token}_1h.parquet'
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep='first')]
    if (df.index.max() - df.index.min()).days < MIN_DATA_DAYS:
        return None
    return df


def resample_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Resample 1H data to 4H OHLCV bars (without indicators)."""
    df_4h = df_1h.resample('4h').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
    }).dropna(subset=['close'])

    if 'funding_1h' in df_1h.columns:
        funding_4h = df_1h['funding_1h'].resample('4h').sum()
        df_4h['funding_4h'] = funding_4h
    else:
        df_4h['funding_4h'] = 0.0

    df_4h = df_4h.dropna(subset=['close'])

    # Dollar volume (for filtering)
    df_4h['dollar_volume'] = df_4h['volume'] * df_4h['close']
    # 30-day avg dollar volume (rolling, in 4H bars: 30*6=180)
    df_4h['avg_daily_dollar_vol'] = df_4h['dollar_volume'].rolling(180).mean() * 6

    # ATR (always 20-period, used for partial profit)
    tr_hl = df_4h['high'] - df_4h['low']
    tr_hc = (df_4h['high'] - df_4h['close'].shift(1)).abs()
    tr_lc = (df_4h['low'] - df_4h['close'].shift(1)).abs()
    df_4h['true_range'] = pd.concat([tr_hl, tr_hc, tr_lc], axis=1).max(axis=1)
    df_4h['atr'] = df_4h['true_range'].rolling(ATR_PERIOD).mean()

    return df_4h


def compute_indicators(df_4h: pd.DataFrame, bb_period: int, bb_std: float,
                       vol_mult: float, mom_days: int, trail_sma: int,
                       use_regime_filter: bool = False) -> Optional[pd.DataFrame]:
    """Compute strategy indicators for given parameter set.

    Operates on a copy to avoid mutating the base data.
    """
    df = df_4h.copy()

    if len(df) < max(bb_period, trail_sma) + 50:
        return None

    # Bollinger Bands
    df['sma_bb'] = df['close'].rolling(bb_period).mean()
    df['bb_std_val'] = df['close'].rolling(bb_period).std()
    df['bb_upper'] = df['sma_bb'] + bb_std * df['bb_std_val']
    df['bb_lower'] = df['sma_bb'] - bb_std * df['bb_std_val']

    # Average volume
    df['avg_volume'] = df['volume'].rolling(bb_period).mean()

    # Trail stop SMA
    df['trail_sma_val'] = df['close'].rolling(trail_sma).mean()

    # Breakout signals (raw)
    df['breakout_long_raw'] = df['close'] > df['bb_upper']
    df['breakout_short_raw'] = df['close'] < df['bb_lower']

    # Volume confirmation
    df['volume_confirmed'] = df['volume'] > vol_mult * df['avg_volume']

    # Final breakout signals
    df['breakout_long'] = df['breakout_long_raw'] & df['volume_confirmed']
    df['breakout_short'] = df['breakout_short_raw'] & df['volume_confirmed']

    # Breakout strength
    df['long_strength'] = ((df['close'] - df['bb_upper']) / df['close']).clip(lower=0)
    df['short_strength'] = ((df['bb_lower'] - df['close']) / df['close']).clip(lower=0)

    # Momentum filter (N-day return)
    mom_bars_4h = mom_days * 6  # convert days to 4H bars
    df['return_Nd'] = df['close'].pct_change(mom_bars_4h)

    # Per-token regime filter: EMA crossover on 4H bars
    # Use span=10 (40h ~1.7 days) and span=30 (120h ~5 days) on 4H bars
    # This is a meaningful trend filter that separates bull/bear regimes
    if use_regime_filter:
        df['ema_fast'] = df['close'].ewm(span=10, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=30, adjust=False).mean()
        df['regime_bullish'] = df['ema_fast'] > df['ema_slow']
    else:
        df['regime_bullish'] = True  # no filter

    return df


# ══════════════════════════════════════════════════════════════════════════════
# POSITION & TRADE TRACKING
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Position:
    token: str
    entry_time: pd.Timestamp
    entry_bar_idx: int
    entry_price: float
    direction: int              # +1 long, -1 short
    size_frac: float
    leverage: float
    entry_atr: float
    partial_taken: bool = False
    remaining_frac: float = 1.0
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
    pnl_pct: float
    bars_held: int
    exit_reason: str


# ══════════════════════════════════════════════════════════════════════════════
# PORTFOLIO SIMULATION ENGINE (parameterised)
# ══════════════════════════════════════════════════════════════════════════════

def run_simulation(
    token_data: Dict[str, pd.DataFrame],
    bb_period: int,
    bb_std: float,
    vol_mult: float,
    max_positions: int,
    mom_days: int,
    partial_atr_mult: float,
    trail_sma: int,
    use_regime_filter: bool = False,
    leverage: float = 1.0,
) -> Tuple[List[Trade], pd.Series]:
    """
    Run R160 Variant B simulation with given parameters.
    Always uses momentum filter (Variant B).
    """
    fee = COST_PER_SIDE
    position_size = 1.0 / max_positions  # equal weight

    mom_bars_4h = mom_days * 6

    # Compute indicators for each token
    token_indicators = {}
    for token, df_4h in token_data.items():
        df_ind = compute_indicators(df_4h, bb_period, bb_std, vol_mult,
                                     mom_days, trail_sma, use_regime_filter)
        if df_ind is not None:
            token_indicators[token] = df_ind

    if len(token_indicators) < 5:
        return [], pd.Series(dtype=float)

    # Build aligned time index
    all_times = set()
    for df in token_indicators.values():
        all_times.update(df.index.tolist())
    all_times = sorted(all_times)

    if len(all_times) < 100:
        return [], pd.Series(dtype=float)

    # Build per-token arrays for fast access
    token_arrays = {}
    token_time_idx = {}
    for token, df in token_indicators.items():
        token_arrays[token] = {
            'index': df.index,
            'open': df['open'].values,
            'close': df['close'].values,
            'volume': df['volume'].values,
            'trail_sma_val': df['trail_sma_val'].values,
            'bb_upper': df['bb_upper'].values,
            'bb_lower': df['bb_lower'].values,
            'atr': df['atr'].values,
            'funding_4h': df['funding_4h'].values,
            'breakout_long': df['breakout_long'].values,
            'breakout_short': df['breakout_short'].values,
            'long_strength': df['long_strength'].values,
            'short_strength': df['short_strength'].values,
            'return_Nd': df['return_Nd'].values,
            'avg_daily_dollar_vol': df['avg_daily_dollar_vol'].values,
            'regime_bullish': df['regime_bullish'].values,
        }
        idx_map = {}
        for i, t in enumerate(df.index):
            idx_map[t] = i
        token_time_idx[token] = idx_map

    equity = 1.0
    equity_curve = {}
    positions: List[Position] = []
    trades: List[Trade] = []
    pending_entries = []

    warmup_bars = max(bb_period, trail_sma) + 10
    warmup_time = all_times[min(warmup_bars, len(all_times) - 1)]

    for ti, current_time in enumerate(all_times):
        if current_time < warmup_time:
            equity_curve[current_time] = equity
            continue

        if equity <= 0.01:
            equity_curve[current_time] = max(equity, 0.0)
            continue

        # ── 1. PROCESS PENDING ENTRIES ──
        if pending_entries:
            slots_available = max_positions - len(positions)
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

                    already_in = any(
                        p.token == token and p.direction == direction
                        for p in positions
                    )
                    if already_in:
                        continue

                    entry_cost = fee * leverage
                    equity -= entry_cost * position_size

                    pos = Position(
                        token=token,
                        entry_time=current_time,
                        entry_bar_idx=bar_idx,
                        entry_price=entry_price,
                        direction=direction,
                        size_frac=position_size,
                        leverage=leverage,
                        entry_atr=entry_atr,
                    )
                    positions.append(pos)

            pending_entries = []

        # ── 2. UPDATE EXISTING POSITIONS ──
        closed_indices = []
        for pidx, pos in enumerate(positions):
            token = pos.token
            if token not in token_time_idx or current_time not in token_time_idx[token]:
                pos.bars_held += 1
                continue

            bar_idx = token_time_idx[token][current_time]
            arr = token_arrays[token]

            close_price = arr['close'][bar_idx]
            sma_val = arr['trail_sma_val'][bar_idx]
            atr_val = arr['atr'][bar_idx]

            if np.isnan(close_price) or np.isnan(sma_val):
                pos.bars_held += 1
                continue

            pos.bars_held += 1
            exit_reason = None
            exit_price = close_price

            # ── Partial profit ──
            if not pos.partial_taken and atr_val > 0:
                target = pos.entry_price + pos.direction * partial_atr_mult * pos.entry_atr
                if pos.direction == 1 and close_price >= target:
                    partial_return = (close_price / pos.entry_price - 1.0) * leverage
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

                elif pos.direction == -1 and close_price <= target:
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

            # ── Trail stop: close below trail_sma for longs, above for shorts ──
            if pos.direction == 1 and close_price < sma_val:
                exit_reason = 'trail_stop'
                exit_price = close_price
            elif pos.direction == -1 and close_price > sma_val:
                exit_reason = 'trail_stop'
                exit_price = close_price

            # ── Breakeven stop ──
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

            # ── Opposite breakout ──
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
                funding_cost = _calc_funding(arr, pos.entry_bar_idx, bar_idx, pos.direction, leverage)
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

        for pidx in sorted(closed_indices, reverse=True):
            positions.pop(pidx)

        # ── 3. SCAN FOR NEW BREAKOUTS (Variant B always) ──
        new_candidates = []
        for token, arr in token_arrays.items():
            if current_time not in token_time_idx[token]:
                continue
            bar_idx = token_time_idx[token][current_time]

            if bar_idx < max(bb_period, trail_sma) + 5:
                continue

            if np.isnan(arr['bb_upper'][bar_idx]) or np.isnan(arr['atr'][bar_idx]):
                continue

            if np.isnan(arr['avg_daily_dollar_vol'][bar_idx]):
                continue
            if arr['avg_daily_dollar_vol'][bar_idx] < MIN_AVG_DOLLAR_VOL:
                continue

            # Check long breakout
            if arr['breakout_long'][bar_idx]:
                strength = arr['long_strength'][bar_idx]
                if np.isnan(strength) or strength <= 0:
                    continue

                # Momentum filter (always on — Variant B)
                if bar_idx < mom_bars_4h:
                    continue
                ret_Nd = arr['return_Nd'][bar_idx]
                if np.isnan(ret_Nd) or ret_Nd <= 0:
                    continue

                # Regime filter (Phase 3 only)
                if use_regime_filter and not arr['regime_bullish'][bar_idx]:
                    continue

                new_candidates.append((token, 1, strength))

            # Check short breakout
            if arr['breakout_short'][bar_idx]:
                strength = arr['short_strength'][bar_idx]
                if np.isnan(strength) or strength <= 0:
                    continue

                # Momentum filter
                if bar_idx < mom_bars_4h:
                    continue
                ret_Nd = arr['return_Nd'][bar_idx]
                if np.isnan(ret_Nd) or ret_Nd >= 0:
                    continue

                # Regime filter (Phase 3): only short when bearish
                if use_regime_filter and arr['regime_bullish'][bar_idx]:
                    continue

                new_candidates.append((token, -1, strength))

        pending_entries = new_candidates
        equity_curve[current_time] = equity

    # ── Close remaining positions ──
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
        funding_cost = _calc_funding(arr, pos.entry_bar_idx,
                                      min(bar_idx, len(arr['funding_4h']) - 1),
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
        return total * leverage
    else:
        return -total * leverage


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

    if total_return <= -1.0:
        annual_return = -1.0
    else:
        annual_return = (1 + total_return) ** (HOURS_PER_YEAR / hours) - 1.0

    bar_returns = eq.pct_change().dropna()
    bar_returns = bar_returns.replace([np.inf, -np.inf], 0.0)
    if len(bar_returns) < 24:
        return _empty_metrics()

    std = bar_returns.std()
    sharpe = (bar_returns.mean() / std) * np.sqrt(BARS_4H_PER_YEAR) if std > 1e-10 else 0.0

    cummax = eq.cummax()
    dd = (eq - cummax) / cummax
    max_dd = dd.min()

    calmar = annual_return / abs(max_dd) if abs(max_dd) > 0.001 else 0.0

    n_trades = len(relevant_trades)
    if n_trades > 0:
        wins = [t for t in relevant_trades if t.pnl_pct > 0]
        losses = [t for t in relevant_trades if t.pnl_pct <= 0]
        win_rate = len(wins) / n_trades
        avg_hold_bars = np.mean([t.bars_held for t in relevant_trades])
        avg_hold_days = avg_hold_bars * 4 / 24
    else:
        win_rate = 0.0
        avg_hold_days = 0.0

    return {
        'annual_return': annual_return,
        'total_return': total_return,
        'sharpe': sharpe,
        'calmar': calmar,
        'max_dd': max_dd,
        'n_trades': n_trades,
        'win_rate': win_rate,
        'avg_hold_days': avg_hold_days,
    }


def _empty_metrics() -> Dict:
    return {
        'annual_return': 0.0, 'total_return': 0.0,
        'sharpe': 0.0, 'calmar': 0.0, 'max_dd': 0.0,
        'n_trades': 0, 'win_rate': 0.0, 'avg_hold_days': 0.0,
    }


# ══════════════════════════════════════════════════════════════════════════════
# RESULT TRACKING
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ConfigResult:
    label: str
    params: Dict
    full_metrics: Dict
    l12m_metrics: Dict
    phase: str  # 'phase1', 'phase2', 'phase3'
    regime_filter: bool = False


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    t_start = time.time()
    log("=" * 70)
    log("R167 -- Optimize R160 Volatility Breakout for Higher Returns")
    log("=" * 70)

    # ── Load data ──
    log("\n[1] Discovering tokens with >1yr data...")
    all_tokens = discover_tokens()
    log(f"  Found {len(all_tokens)} tokens")

    log("\n[2] Loading and resampling to 4H bars...")
    token_data_raw = {}
    load_t0 = time.time()
    for i, token in enumerate(all_tokens):
        if (i + 1) % 20 == 0:
            log(f"  Loading {i+1}/{len(all_tokens)}...")
        df_1h = load_1h_data(token)
        if df_1h is not None:
            df_4h = resample_4h(df_1h)
            if len(df_4h) > 100:
                token_data_raw[token] = df_4h
    load_elapsed = time.time() - load_t0
    log(f"  Loaded {len(token_data_raw)} tokens in {load_elapsed:.1f}s")

    # Filter by volume
    log("\n[3] Filtering by $2M+ daily volume...")
    token_data = {}
    for token, df_4h in token_data_raw.items():
        valid_vol = df_4h['avg_daily_dollar_vol'].dropna()
        if len(valid_vol) > 0 and valid_vol.iloc[-1] >= MIN_AVG_DOLLAR_VOL:
            token_data[token] = df_4h
    log(f"  {len(token_data)} tokens pass volume filter")

    all_results: List[ConfigResult] = []

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 1: Individual parameter sweeps
    # ══════════════════════════════════════════════════════════════════════
    log(f"\n{'='*70}")
    log("PHASE 1: Individual Parameter Sweeps (hold defaults, vary one)")
    log(f"{'='*70}")

    phase1_best = dict(DEFAULTS)  # will update with best per param

    for param_name, values in PARAM_RANGES.items():
        log(f"\n  --- Sweeping {param_name}: {values} ---")
        best_score = -999
        best_val = DEFAULTS[param_name]

        for val in values:
            params = dict(DEFAULTS)
            params[param_name] = val

            label = f"P1_{param_name}={val}"
            sim_t0 = time.time()

            trades, eq_series = run_simulation(
                token_data,
                bb_period=params['bb_period'],
                bb_std=params['bb_std'],
                vol_mult=params['vol_mult'],
                max_positions=params['max_positions'],
                mom_days=params['mom_days'],
                partial_atr_mult=params['partial_atr_mult'],
                trail_sma=params['trail_sma'],
                use_regime_filter=False,
                leverage=1.0,
            )

            sim_elapsed = time.time() - sim_t0
            full_m = compute_metrics(trades, eq_series)
            l12m_m = compute_metrics(trades, eq_series, start_date=L12M_START)

            result = ConfigResult(
                label=label, params=dict(params),
                full_metrics=full_m, l12m_metrics=l12m_m,
                phase='phase1',
            )
            all_results.append(result)

            # Score: prioritise 12m return, penalise DD > 15%
            ret_12m = l12m_m['total_return']
            dd_12m = l12m_m['max_dd']
            sharpe_12m = l12m_m['sharpe']
            # Combined score: high return, good sharpe, low DD
            dd_penalty = max(0, abs(dd_12m) - 0.15) * 5  # penalise DD > 15%
            score = ret_12m + sharpe_12m * 0.1 - dd_penalty

            log(f"    {param_name}={val}: 12m_ret={ret_12m:+.1%}  12m_DD={dd_12m:.1%}  "
                f"12m_Sharpe={sharpe_12m:.2f}  trades={l12m_m['n_trades']}  "
                f"score={score:.3f}  ({sim_elapsed:.1f}s)")

            if score > best_score:
                best_score = score
                best_val = val

        log(f"  >> Best {param_name} = {best_val} (score={best_score:.3f})")
        phase1_best[param_name] = best_val

    log(f"\n  Phase 1 best params: {phase1_best}")

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 2: Full combo sweep of promising values
    # ══════════════════════════════════════════════════════════════════════
    log(f"\n{'='*70}")
    log("PHASE 2: Full Combo Sweep of Top Values")
    log(f"{'='*70}")

    # For each param, pick top 2 values from phase 1 results
    param_top_values = {}
    for param_name, values in PARAM_RANGES.items():
        # Score each value
        scored = []
        for val in values:
            matching = [r for r in all_results
                        if r.phase == 'phase1'
                        and r.params.get(param_name) == val
                        and all(r.params.get(k) == DEFAULTS[k] for k in DEFAULTS if k != param_name)]
            if matching:
                r = matching[0]
                ret_12m = r.l12m_metrics['total_return']
                dd_12m = r.l12m_metrics['max_dd']
                sharpe_12m = r.l12m_metrics['sharpe']
                dd_penalty = max(0, abs(dd_12m) - 0.15) * 5
                score = ret_12m + sharpe_12m * 0.1 - dd_penalty
                scored.append((val, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        # Take top 2 values (or just top 1 if they're very close)
        top_vals = [scored[0][0]]
        if len(scored) > 1 and scored[1][1] > scored[0][1] - 0.05:
            top_vals.append(scored[1][0])
        param_top_values[param_name] = top_vals
        log(f"  {param_name}: top values = {top_vals}")

    # Generate all combinations
    combo_lists = [param_top_values[k] for k in ['bb_period', 'bb_std', 'vol_mult',
                                                    'max_positions', 'mom_days',
                                                    'partial_atr_mult', 'trail_sma']]
    all_combos = list(product(*combo_lists))
    log(f"\n  Phase 2: {len(all_combos)} combinations to test")

    for ci, combo in enumerate(all_combos):
        bb_p, bb_s, vol_m, max_p, mom_d, part_m, trail_s = combo
        params = {
            'bb_period': bb_p, 'bb_std': bb_s, 'vol_mult': vol_m,
            'max_positions': max_p, 'mom_days': mom_d,
            'partial_atr_mult': part_m, 'trail_sma': trail_s,
        }
        label = (f"P2_BB{bb_p}_std{bb_s}_vol{vol_m}_"
                 f"pos{max_p}_mom{mom_d}_part{part_m}_trail{trail_s}")

        sim_t0 = time.time()
        trades, eq_series = run_simulation(
            token_data,
            bb_period=bb_p, bb_std=bb_s, vol_mult=vol_m,
            max_positions=max_p, mom_days=mom_d,
            partial_atr_mult=part_m, trail_sma=trail_s,
            use_regime_filter=False, leverage=1.0,
        )
        sim_elapsed = time.time() - sim_t0

        full_m = compute_metrics(trades, eq_series)
        l12m_m = compute_metrics(trades, eq_series, start_date=L12M_START)

        result = ConfigResult(
            label=label, params=dict(params),
            full_metrics=full_m, l12m_metrics=l12m_m,
            phase='phase2',
        )
        all_results.append(result)

        ret_12m = l12m_m['total_return']
        dd_12m = l12m_m['max_dd']
        sharpe_12m = l12m_m['sharpe']

        if (ci + 1) % 5 == 0 or ci == 0:
            log(f"    [{ci+1}/{len(all_combos)}] {label}: "
                f"12m_ret={ret_12m:+.1%}  12m_DD={dd_12m:.1%}  "
                f"12m_Sharpe={sharpe_12m:.2f}  ({sim_elapsed:.1f}s)")

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 2.5: Hand-Picked Aggressive Combos
    # ══════════════════════════════════════════════════════════════════════
    log(f"\n{'='*70}")
    log("PHASE 2.5: Hand-Picked Aggressive Combos")
    log(f"{'='*70}")

    # Test highly concentrated (3 positions) and best momentum combos
    aggressive_combos = [
        # (bb_period, bb_std, vol_mult, max_positions, mom_days, partial_atr_mult, trail_sma)
        (20, 2.0, 1.5, 3, 7,  3.0, 20),   # ultra-concentrated, fast mom
        (20, 2.0, 1.5, 3, 21, 3.0, 20),   # ultra-concentrated, slow mom
        (20, 2.0, 1.5, 3, 14, 3.0, 20),   # ultra-concentrated, default mom
        (20, 2.0, 1.2, 3, 7,  3.0, 20),   # ultra-concentrated, loose vol
        (20, 2.0, 1.2, 3, 21, 3.0, 20),   # ultra-concentrated, loose vol, slow mom
        (20, 2.0, 1.5, 3, 7,  2.0, 20),   # ultra-conc, fast partial
        (20, 2.0, 1.5, 3, 7,  5.0, 20),   # ultra-conc, slow partial
        (20, 2.0, 1.5, 5, 7,  2.0, 20),   # concentrated, fast mom, fast partial
        (20, 2.0, 1.5, 5, 7,  5.0, 20),   # concentrated, fast mom, slow partial
        (20, 2.0, 1.5, 5, 21, 2.0, 20),   # concentrated, slow mom, fast partial
        (20, 2.0, 1.5, 5, 21, 5.0, 20),   # concentrated, slow mom, slow partial
        (20, 2.0, 1.5, 3, 7,  3.0, 10),   # ultra-conc, fast trail
        (20, 2.0, 1.5, 3, 7,  3.0, 30),   # ultra-conc, slow trail
        (20, 2.0, 1.5, 5, 7,  3.0, 10),   # conc, fast trail
        (20, 2.0, 1.5, 5, 7,  3.0, 30),   # conc, slow trail
        (15, 2.0, 1.5, 3, 7,  3.0, 20),   # shorter BB, ultra-conc
        (15, 2.0, 1.5, 5, 7,  3.0, 20),   # shorter BB, conc
        (10, 2.0, 1.5, 3, 7,  3.0, 20),   # very short BB, ultra-conc
        (20, 1.5, 1.5, 3, 7,  3.0, 20),   # tight BB, ultra-conc
        (20, 1.5, 1.5, 5, 7,  3.0, 20),   # tight BB, conc
    ]

    for ci, combo in enumerate(aggressive_combos):
        bb_p, bb_s, vol_m, max_p, mom_d, part_m, trail_s = combo
        params = {
            'bb_period': bb_p, 'bb_std': bb_s, 'vol_mult': vol_m,
            'max_positions': max_p, 'mom_days': mom_d,
            'partial_atr_mult': part_m, 'trail_sma': trail_s,
        }
        label = (f"P25_BB{bb_p}_std{bb_s}_vol{vol_m}_"
                 f"pos{max_p}_mom{mom_d}_part{part_m}_trail{trail_s}")

        # Skip if already tested in Phase 2
        already_tested = any(
            r.params == params and not r.regime_filter
            for r in all_results
        )
        if already_tested:
            log(f"    [{ci+1}/{len(aggressive_combos)}] {label}: already tested, skipping")
            continue

        sim_t0 = time.time()
        trades, eq_series = run_simulation(
            token_data,
            bb_period=bb_p, bb_std=bb_s, vol_mult=vol_m,
            max_positions=max_p, mom_days=mom_d,
            partial_atr_mult=part_m, trail_sma=trail_s,
            use_regime_filter=False, leverage=1.0,
        )
        sim_elapsed = time.time() - sim_t0

        full_m = compute_metrics(trades, eq_series)
        l12m_m = compute_metrics(trades, eq_series, start_date=L12M_START)

        result = ConfigResult(
            label=label, params=dict(params),
            full_metrics=full_m, l12m_metrics=l12m_m,
            phase='phase2.5',
        )
        all_results.append(result)

        ret_12m = l12m_m['total_return']
        dd_12m = l12m_m['max_dd']
        sharpe_12m = l12m_m['sharpe']

        log(f"    [{ci+1}/{len(aggressive_combos)}] {label}: "
            f"12m_ret={ret_12m:+.1%}  12m_DD={dd_12m:.1%}  "
            f"12m_Sharpe={sharpe_12m:.2f}  trades={l12m_m['n_trades']}  ({sim_elapsed:.1f}s)")

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 3: Regime filter on top configs
    # ══════════════════════════════════════════════════════════════════════
    log(f"\n{'='*70}")
    log("PHASE 3: Per-Token Regime Filter (EMA 10h/30h)")
    log(f"{'='*70}")

    # Pick top 10 configs from phases 1+2+2.5 by composite score
    def composite_score(r):
        ret = r.l12m_metrics['total_return']
        dd = r.l12m_metrics['max_dd']
        sharpe = r.l12m_metrics['sharpe']
        n_trades = r.l12m_metrics['n_trades']
        # Filter out degenerate results (< 20 trades)
        if n_trades < 20:
            return -999
        dd_penalty = max(0, abs(dd) - 0.15) * 5
        return ret + sharpe * 0.1 - dd_penalty

    # Sort all non-regime results by score
    non_regime = [r for r in all_results if not r.regime_filter
                  and r.l12m_metrics['n_trades'] >= 20]
    non_regime.sort(key=composite_score, reverse=True)
    top_configs = non_regime[:10]

    log(f"  Testing regime filter on top {len(top_configs)} configs...")

    for ci, cfg in enumerate(top_configs):
        params = cfg.params
        label = f"P3_regime_{cfg.label.replace('P1_', '').replace('P2_', '')}"

        sim_t0 = time.time()
        trades, eq_series = run_simulation(
            token_data,
            bb_period=params['bb_period'],
            bb_std=params['bb_std'],
            vol_mult=params['vol_mult'],
            max_positions=params['max_positions'],
            mom_days=params['mom_days'],
            partial_atr_mult=params['partial_atr_mult'],
            trail_sma=params['trail_sma'],
            use_regime_filter=True,
            leverage=1.0,
        )
        sim_elapsed = time.time() - sim_t0

        full_m = compute_metrics(trades, eq_series)
        l12m_m = compute_metrics(trades, eq_series, start_date=L12M_START)

        result = ConfigResult(
            label=label, params=dict(params),
            full_metrics=full_m, l12m_metrics=l12m_m,
            phase='phase3', regime_filter=True,
        )
        all_results.append(result)

        ret_12m = l12m_m['total_return']
        dd_12m = l12m_m['max_dd']
        sharpe_12m = l12m_m['sharpe']

        log(f"    [{ci+1}/{len(top_configs)}] {label}: "
            f"12m_ret={ret_12m:+.1%}  12m_DD={dd_12m:.1%}  "
            f"12m_Sharpe={sharpe_12m:.2f}  ({sim_elapsed:.1f}s)")

    # ══════════════════════════════════════════════════════════════════════
    # PHASE 4: Deep Dive on Best Findings
    # ══════════════════════════════════════════════════════════════════════
    log(f"\n{'='*70}")
    log("PHASE 4: Deep Dive — Combining Best Findings")
    log(f"{'='*70}")

    # Key findings to combine:
    # - Regime filter improves Sharpe significantly
    # - BB10 + 3 positions + 7d mom = high Sharpe (2.60)
    # - vol1.2 + 10 positions + 7d mom + regime = Sharpe 2.75
    # - 5 positions with trail_sma=10 gets high return but too much DD
    # - trail_sma=15 might be a good middle ground
    # - Test trail_sma=10 with more positions (diversification reduces DD)

    deep_combos = [
        # Regime filter combos with best base params
        # (bb_period, bb_std, vol_mult, max_pos, mom_days, partial_atr, trail_sma, regime)
        (10, 2.0, 1.5, 3,  7,  3.0, 20, True),   # BB10, 3pos, regime
        (10, 2.0, 1.5, 5,  7,  3.0, 20, True),   # BB10, 5pos, regime
        (10, 2.0, 1.5, 10, 7,  3.0, 20, True),   # BB10, 10pos, regime
        (10, 2.0, 1.5, 3,  21, 3.0, 20, True),   # BB10, 3pos, slow mom, regime
        (10, 2.0, 1.5, 5,  21, 3.0, 20, True),   # BB10, 5pos, slow mom, regime
        (10, 2.0, 1.2, 5,  7,  3.0, 20, True),   # BB10, loose vol, regime
        (10, 2.0, 1.2, 10, 7,  3.0, 20, True),   # BB10, loose vol, 10pos, regime

        # Trail SMA 15 (middle ground between 10 and 20)
        (20, 2.0, 1.5, 5,  7,  3.0, 15, False),  # trail15, 5pos, fast mom
        (20, 2.0, 1.5, 5,  7,  3.0, 15, True),   # trail15, 5pos, fast mom, regime
        (20, 2.0, 1.5, 10, 7,  3.0, 15, False),  # trail15, 10pos, fast mom
        (20, 2.0, 1.5, 10, 7,  3.0, 15, True),   # trail15, 10pos, fast mom, regime
        (20, 2.0, 1.2, 10, 7,  3.0, 15, True),   # trail15, loose vol, 10pos, regime
        (20, 2.0, 1.5, 3,  7,  3.0, 15, False),  # trail15, 3pos, fast mom
        (20, 2.0, 1.5, 3,  7,  3.0, 15, True),   # trail15, 3pos, fast mom, regime

        # Trail SMA 10 with more positions to reduce DD
        (20, 2.0, 1.5, 10, 7,  3.0, 10, False),  # trail10, 10pos (diversify away DD)
        (20, 2.0, 1.5, 10, 7,  3.0, 10, True),   # trail10, 10pos, regime
        (20, 2.0, 1.5, 10, 21, 3.0, 10, False),  # trail10, 10pos, slow mom
        (20, 2.0, 1.5, 10, 21, 3.0, 10, True),   # trail10, 10pos, slow mom, regime
        (20, 2.0, 1.2, 10, 7,  3.0, 10, True),   # trail10, loose vol, 10pos, regime
        (20, 2.0, 1.5, 15, 7,  3.0, 10, True),   # trail10, 15pos, regime
        (20, 2.0, 1.5, 3,  7,  3.0, 10, True),   # trail10, 3pos, regime

        # Best regime combo (vol1.2_pos10_mom7) with trail tweaks
        (20, 2.0, 1.2, 10, 7,  3.0, 15, False),  # already in list above
        (20, 2.0, 1.2, 10, 7,  2.0, 20, True),   # faster partial + regime
        (20, 2.0, 1.2, 10, 7,  5.0, 20, True),   # slower partial + regime
        (20, 2.0, 1.2, 5,  7,  3.0, 20, True),   # fewer pos + regime
        (20, 2.0, 1.2, 10, 21, 3.0, 20, True),   # slow mom + regime

        # BB10 + trail 10/15 combos (exploring the concentration-speed space)
        (10, 2.0, 1.5, 5,  7,  3.0, 15, True),
        (10, 2.0, 1.5, 5,  7,  3.0, 10, True),
        (10, 2.0, 1.5, 10, 7,  3.0, 15, True),
        (10, 2.0, 1.5, 10, 7,  3.0, 10, True),
    ]

    for ci, combo in enumerate(deep_combos):
        bb_p, bb_s, vol_m, max_p, mom_d, part_m, trail_s, regime = combo
        params = {
            'bb_period': bb_p, 'bb_std': bb_s, 'vol_mult': vol_m,
            'max_positions': max_p, 'mom_days': mom_d,
            'partial_atr_mult': part_m, 'trail_sma': trail_s,
        }
        label = (f"P4_BB{bb_p}_std{bb_s}_vol{vol_m}_"
                 f"pos{max_p}_mom{mom_d}_part{part_m}_trail{trail_s}"
                 f"{'_REG' if regime else ''}")

        # Skip duplicates
        already_tested = any(
            r.params == params and r.regime_filter == regime
            for r in all_results
        )
        if already_tested:
            continue

        sim_t0 = time.time()
        trades, eq_series = run_simulation(
            token_data,
            bb_period=bb_p, bb_std=bb_s, vol_mult=vol_m,
            max_positions=max_p, mom_days=mom_d,
            partial_atr_mult=part_m, trail_sma=trail_s,
            use_regime_filter=regime, leverage=1.0,
        )
        sim_elapsed = time.time() - sim_t0

        full_m = compute_metrics(trades, eq_series)
        l12m_m = compute_metrics(trades, eq_series, start_date=L12M_START)

        result = ConfigResult(
            label=label, params=dict(params),
            full_metrics=full_m, l12m_metrics=l12m_m,
            phase='phase4', regime_filter=regime,
        )
        all_results.append(result)

        ret_12m = l12m_m['total_return']
        dd_12m = l12m_m['max_dd']
        sharpe_12m = l12m_m['sharpe']

        log(f"    [{ci+1}/{len(deep_combos)}] {label}: "
            f"12m_ret={ret_12m:+.1%}  12m_DD={dd_12m:.1%}  "
            f"12m_Sharpe={sharpe_12m:.2f}  trades={l12m_m['n_trades']}  ({sim_elapsed:.1f}s)")

    # ══════════════════════════════════════════════════════════════════════
    # LEVERAGE TEST on top 5 configs
    # ══════════════════════════════════════════════════════════════════════
    log(f"\n{'='*70}")
    log("LEVERAGE TEST: 2x and 3x on Top 5 Configs")
    log(f"{'='*70}")

    def composite_score_lev(r):
        ret = r.l12m_metrics['total_return']
        dd = r.l12m_metrics['max_dd']
        sharpe = r.l12m_metrics['sharpe']
        n_trades = r.l12m_metrics['n_trades']
        if n_trades < 20:
            return -999
        dd_penalty = max(0, abs(dd) - 0.15) * 5
        return ret + sharpe * 0.1 - dd_penalty

    # Filter out leverage and degenerate results, pick top 5
    base_for_lev = [r for r in all_results
                    if not r.phase.startswith('leverage')
                    and r.l12m_metrics['n_trades'] >= 20]
    base_for_lev.sort(key=composite_score_lev, reverse=True)
    top5_for_lev = base_for_lev[:5]

    lev_results = []
    for cfg in top5_for_lev:
        for lev in [2, 3]:
            params = cfg.params
            label = f"LEV{lev}x_{cfg.label}"

            sim_t0 = time.time()
            trades, eq_series = run_simulation(
                token_data,
                bb_period=params['bb_period'],
                bb_std=params['bb_std'],
                vol_mult=params['vol_mult'],
                max_positions=params['max_positions'],
                mom_days=params['mom_days'],
                partial_atr_mult=params['partial_atr_mult'],
                trail_sma=params['trail_sma'],
                use_regime_filter=cfg.regime_filter,
                leverage=float(lev),
            )
            sim_elapsed = time.time() - sim_t0

            full_m = compute_metrics(trades, eq_series)
            l12m_m = compute_metrics(trades, eq_series, start_date=L12M_START)

            lev_result = ConfigResult(
                label=label, params=dict(params),
                full_metrics=full_m, l12m_metrics=l12m_m,
                phase=f'leverage_{lev}x', regime_filter=cfg.regime_filter,
            )
            lev_results.append(lev_result)

            ret_12m = l12m_m['total_return']
            dd_12m = l12m_m['max_dd']
            sharpe_12m = l12m_m['sharpe']

            log(f"    {label}: 12m_ret={ret_12m:+.1%}  12m_DD={dd_12m:.1%}  "
                f"12m_Sharpe={sharpe_12m:.2f}  ({sim_elapsed:.1f}s)")

    all_results.extend(lev_results)

    # ══════════════════════════════════════════════════════════════════════
    # WRITE RESULTS
    # ══════════════════════════════════════════════════════════════════════
    log(f"\n[WRITE] Writing results to {OUTPUT_MD}...")
    write_results(all_results, token_data, phase1_best, param_top_values)

    total_time = time.time() - t_start
    log(f"\nTotal runtime: {total_time:.1f}s")
    log("Done.")


def write_results(all_results: List[ConfigResult], token_data: Dict,
                  phase1_best: Dict, param_top_values: Dict):
    """Write comprehensive results to markdown."""
    lines = []

    lines.append("# R167 -- R160 Volatility Breakout Optimization Results")
    lines.append("")
    lines.append("## Objective")
    lines.append("")
    lines.append("Optimize R160 Variant B (volatility breakout + momentum filter) for HIGHER RETURNS")
    lines.append("while maintaining excellent Sharpe and low MaxDD.")
    lines.append("")
    lines.append("**Baseline (R160 Variant B 1x)**: +29.9% 12mo, Sharpe 2.20, MaxDD -7.5%")
    lines.append("")
    lines.append("**Target**: 80-150%+ 12mo return, MaxDD < 15%, Sharpe > 1.5")
    lines.append("(enables 2-3x leverage for 300%+ with DD < 40%)")
    lines.append("")

    # ── Phase 1 results ──
    lines.append("## Phase 1: Individual Parameter Sweeps")
    lines.append("")
    lines.append("Hold all other params at R160 defaults, vary one at a time.")
    lines.append("")

    phase1 = [r for r in all_results if r.phase == 'phase1']
    for param_name in PARAM_RANGES:
        param_results = [r for r in phase1 if param_name in r.label]
        param_results.sort(key=lambda r: r.l12m_metrics['total_return'], reverse=True)

        lines.append(f"### {param_name}")
        lines.append("")
        lines.append(f"| Value | 12mo Return | 12mo MaxDD | 12mo Sharpe | 12mo Calmar | Full Return | Full MaxDD | Trades | WR | AvgHold |")
        lines.append(f"|-------|-------------|------------|-------------|-------------|-------------|------------|--------|----|---------| ")

        for r in param_results:
            val = r.params[param_name]
            lm = r.l12m_metrics
            fm = r.full_metrics
            default_marker = " *" if val == DEFAULTS[param_name] else ""
            lines.append(
                f"| {val}{default_marker} "
                f"| {lm['total_return']:+.1%} "
                f"| {lm['max_dd']:.1%} "
                f"| {lm['sharpe']:.2f} "
                f"| {lm['calmar']:.2f} "
                f"| {fm['total_return']:+.1%} "
                f"| {fm['max_dd']:.1%} "
                f"| {fm['n_trades']} "
                f"| {fm['win_rate']:.0%} "
                f"| {fm['avg_hold_days']:.1f}d |"
            )
        lines.append("")
        lines.append(f"**Best**: {phase1_best[param_name]}")
        lines.append("")

    # ── Phase 1 summary ──
    lines.append("### Phase 1 Summary: Best Individual Values")
    lines.append("")
    lines.append("| Parameter | Default | Best | Improvement |")
    lines.append("|-----------|---------|------|-------------|")
    for param_name in PARAM_RANGES:
        default_results = [r for r in phase1 if r.params[param_name] == DEFAULTS[param_name]
                           and all(r.params.get(k) == DEFAULTS[k] for k in DEFAULTS if k != param_name)]
        best_results = [r for r in phase1 if r.params[param_name] == phase1_best[param_name]
                        and all(r.params.get(k) == DEFAULTS[k] for k in DEFAULTS if k != param_name)]
        if default_results and best_results:
            default_ret = default_results[0].l12m_metrics['total_return']
            best_ret = best_results[0].l12m_metrics['total_return']
            delta = best_ret - default_ret
            lines.append(f"| {param_name} | {DEFAULTS[param_name]} | {phase1_best[param_name]} | {delta:+.1%} |")
    lines.append("")

    # ── Phase 2 results ──
    lines.append("## Phase 2: Full Combo Sweep of Top Values")
    lines.append("")
    lines.append("Top values per parameter selected for full combination sweep:")
    lines.append("")
    for param_name, vals in param_top_values.items():
        lines.append(f"- **{param_name}**: {vals}")
    lines.append("")

    phase2 = [r for r in all_results if r.phase == 'phase2']
    phase2.sort(key=lambda r: r.l12m_metrics['total_return'], reverse=True)

    # Top 20 by return (with DD < 20%)
    good_phase2 = [r for r in phase2 if abs(r.l12m_metrics['max_dd']) < 0.20]
    good_phase2.sort(key=lambda r: r.l12m_metrics['total_return'], reverse=True)

    lines.append(f"### Top Phase 2 Configs (12mo DD < 20%, sorted by return)")
    lines.append("")
    lines.append("| Rank | Config | 12mo Ret | 12mo DD | 12mo Sharpe | 12mo Calmar | Full Ret | Full DD | Trades | WR | AvgHold |")
    lines.append("|------|--------|----------|---------|-------------|-------------|----------|---------|--------|----|---------| ")

    for rank, r in enumerate(good_phase2[:20], 1):
        lm = r.l12m_metrics
        fm = r.full_metrics
        short_label = r.label.replace('P2_', '')
        lines.append(
            f"| {rank} | {short_label} "
            f"| {lm['total_return']:+.1%} "
            f"| {lm['max_dd']:.1%} "
            f"| {lm['sharpe']:.2f} "
            f"| {lm['calmar']:.2f} "
            f"| {fm['total_return']:+.1%} "
            f"| {fm['max_dd']:.1%} "
            f"| {fm['n_trades']} "
            f"| {fm['win_rate']:.0%} "
            f"| {fm['avg_hold_days']:.1f}d |"
        )
    lines.append("")

    # ── Phase 2.5 results ──
    phase25 = [r for r in all_results if r.phase == 'phase2.5']
    if phase25:
        phase25.sort(key=lambda r: r.l12m_metrics['total_return'], reverse=True)
        lines.append("## Phase 2.5: Aggressive Hand-Picked Combos")
        lines.append("")
        lines.append("Ultra-concentrated (3 positions = 33% each) and aggressive parameter combos.")
        lines.append("")
        lines.append("| Rank | Config | 12mo Ret | 12mo DD | 12mo Sharpe | 12mo Calmar | Full Ret | Full DD | Trades | WR | AvgHold |")
        lines.append("|------|--------|----------|---------|-------------|-------------|----------|---------|--------|----|---------| ")

        for rank, r in enumerate(phase25[:20], 1):
            lm = r.l12m_metrics
            fm = r.full_metrics
            short_label = r.label.replace('P25_', '')
            lines.append(
                f"| {rank} | {short_label} "
                f"| {lm['total_return']:+.1%} "
                f"| {lm['max_dd']:.1%} "
                f"| {lm['sharpe']:.2f} "
                f"| {lm['calmar']:.2f} "
                f"| {fm['total_return']:+.1%} "
                f"| {fm['max_dd']:.1%} "
                f"| {fm['n_trades']} "
                f"| {fm['win_rate']:.0%} "
                f"| {fm['avg_hold_days']:.1f}d |"
            )
        lines.append("")

    # ── Phase 3 results ──
    lines.append("## Phase 3: Per-Token Regime Filter (EMA 10/30 on 4H bars = 40h/120h)")
    lines.append("")
    lines.append("Applied to top 10 configs from Phases 1-2. Only take long breakouts")
    lines.append("when token's fast EMA > slow EMA, short when fast < slow.")
    lines.append("")

    phase3 = [r for r in all_results if r.phase == 'phase3']
    phase3.sort(key=lambda r: r.l12m_metrics['total_return'], reverse=True)

    lines.append("| Rank | Config | 12mo Ret | 12mo DD | 12mo Sharpe | 12mo Calmar | Full Ret | Full DD | Trades | WR |")
    lines.append("|------|--------|----------|---------|-------------|-------------|----------|---------|--------|----|")

    for rank, r in enumerate(phase3[:10], 1):
        lm = r.l12m_metrics
        fm = r.full_metrics
        short_label = r.label.replace('P3_regime_', '')
        lines.append(
            f"| {rank} | {short_label} "
            f"| {lm['total_return']:+.1%} "
            f"| {lm['max_dd']:.1%} "
            f"| {lm['sharpe']:.2f} "
            f"| {lm['calmar']:.2f} "
            f"| {fm['total_return']:+.1%} "
            f"| {fm['max_dd']:.1%} "
            f"| {fm['n_trades']} "
            f"| {fm['win_rate']:.0%} |"
        )
    lines.append("")

    # ── Phase 4 results ──
    phase4 = [r for r in all_results if r.phase == 'phase4' and r.l12m_metrics['n_trades'] >= 20]
    if phase4:
        phase4.sort(key=lambda r: r.l12m_metrics['total_return'], reverse=True)
        lines.append("## Phase 4: Deep Dive — Combining Best Findings")
        lines.append("")
        lines.append("Combining BB10, regime filter, trail_sma 10/15, varied concentration.")
        lines.append("")
        lines.append("| Rank | Config | Regime | 12mo Ret | 12mo DD | 12mo Sharpe | 12mo Calmar | Full Ret | Full DD | Trades | WR | AvgHold |")
        lines.append("|------|--------|--------|----------|---------|-------------|-------------|----------|---------|--------|----|---------| ")

        for rank, r in enumerate(phase4[:25], 1):
            lm = r.l12m_metrics
            fm = r.full_metrics
            short_label = r.label.replace('P4_', '')
            regime = "Yes" if r.regime_filter else "No"
            lines.append(
                f"| {rank} | {short_label} | {regime} "
                f"| {lm['total_return']:+.1%} "
                f"| {lm['max_dd']:.1%} "
                f"| {lm['sharpe']:.2f} "
                f"| {lm['calmar']:.2f} "
                f"| {fm['total_return']:+.1%} "
                f"| {fm['max_dd']:.1%} "
                f"| {fm['n_trades']} "
                f"| {fm['win_rate']:.0%} "
                f"| {fm['avg_hold_days']:.1f}d |"
            )
        lines.append("")

    # ── Leverage test results ──
    lev_results = [r for r in all_results if r.phase.startswith('leverage')
                   and r.l12m_metrics['n_trades'] >= 20]
    if lev_results:
        lines.append("## Leverage Test (2x, 3x on Top 5)")
        lines.append("")
        lines.append("| Config | 12mo Ret | 12mo DD | 12mo Sharpe | 12mo Calmar | Full Ret | Full DD | Trades |")
        lines.append("|--------|----------|---------|-------------|-------------|----------|---------|--------|")

        lev_results.sort(key=lambda r: r.l12m_metrics['total_return'], reverse=True)
        for r in lev_results:
            lm = r.l12m_metrics
            fm = r.full_metrics
            lines.append(
                f"| {r.label} "
                f"| {lm['total_return']:+.1%} "
                f"| {lm['max_dd']:.1%} "
                f"| {lm['sharpe']:.2f} "
                f"| {lm['calmar']:.2f} "
                f"| {fm['total_return']:+.1%} "
                f"| {fm['max_dd']:.1%} "
                f"| {fm['n_trades']} |"
            )
        lines.append("")

    # ── Grand summary: ALL configs ranked by composite score ──
    lines.append("## Grand Summary: All Configs Ranked")
    lines.append("")
    lines.append("Score = 12mo_return + 0.1 * sharpe - 5 * max(0, |DD| - 0.15)")
    lines.append("")

    def composite_score(r):
        ret = r.l12m_metrics['total_return']
        dd = r.l12m_metrics['max_dd']
        sharpe = r.l12m_metrics['sharpe']
        n_trades = r.l12m_metrics['n_trades']
        if n_trades < 20:
            return -999
        dd_penalty = max(0, abs(dd) - 0.15) * 5
        return ret + sharpe * 0.1 - dd_penalty

    # Only 1x leverage results with sufficient trades for grand summary
    base_results = [r for r in all_results
                    if not r.phase.startswith('leverage')
                    and r.l12m_metrics['n_trades'] >= 20]
    base_results.sort(key=composite_score, reverse=True)

    lines.append("### Top 20 Overall (1x leverage)")
    lines.append("")
    lines.append("| Rank | Phase | Config | Regime | 12mo Ret | 12mo DD | 12mo Sharpe | 12mo Calmar | Full Ret | Full DD | Score |")
    lines.append("|------|-------|--------|--------|----------|---------|-------------|-------------|----------|---------|-------|")

    for rank, r in enumerate(base_results[:20], 1):
        lm = r.l12m_metrics
        fm = r.full_metrics
        score = composite_score(r)
        regime = "Yes" if r.regime_filter else "No"
        lines.append(
            f"| {rank} | {r.phase} | {r.label} | {regime} "
            f"| {lm['total_return']:+.1%} "
            f"| {lm['max_dd']:.1%} "
            f"| {lm['sharpe']:.2f} "
            f"| {lm['calmar']:.2f} "
            f"| {fm['total_return']:+.1%} "
            f"| {fm['max_dd']:.1%} "
            f"| {score:.3f} |"
        )
    lines.append("")

    # ── Sweet spot configs: DD < 15%, highest return ──
    lines.append("### Sweet Spot: 12mo DD < 15% AND Highest Return")
    lines.append("")
    lines.append("These are the configs we can lever 2-3x for 300%+ with DD < 40%.")
    lines.append("")

    sweet = [r for r in base_results if abs(r.l12m_metrics['max_dd']) < 0.15
             and r.l12m_metrics['n_trades'] >= 20]
    sweet.sort(key=lambda r: r.l12m_metrics['total_return'], reverse=True)

    if sweet:
        lines.append("| Rank | Phase | Config | Regime | 12mo Ret | 12mo DD | 12mo Sharpe | 12mo Calmar | Full Ret | Full DD |")
        lines.append("|------|-------|--------|--------|----------|---------|-------------|-------------|----------|---------|")

        for rank, r in enumerate(sweet[:15], 1):
            lm = r.l12m_metrics
            fm = r.full_metrics
            regime = "Yes" if r.regime_filter else "No"
            lines.append(
                f"| {rank} | {r.phase} | {r.label} | {regime} "
                f"| {lm['total_return']:+.1%} "
                f"| {lm['max_dd']:.1%} "
                f"| {lm['sharpe']:.2f} "
                f"| {lm['calmar']:.2f} "
                f"| {fm['total_return']:+.1%} "
                f"| {fm['max_dd']:.1%} |"
            )
        lines.append("")

        # Best sweet spot config details
        best = sweet[0]
        lines.append("### Recommended Config (Best Sweet Spot)")
        lines.append("")
        lines.append(f"**Config**: {best.label}")
        lines.append(f"**Regime Filter**: {'Yes' if best.regime_filter else 'No'}")
        lines.append("")
        lines.append("| Parameter | Value |")
        lines.append("|-----------|-------|")
        for k, v in best.params.items():
            default_note = f" (default: {DEFAULTS[k]})" if v != DEFAULTS[k] else " (default)"
            lines.append(f"| {k} | {v}{default_note} |")
        lines.append("")
        lines.append("**12-Month Performance (1x)**:")
        lm = best.l12m_metrics
        lines.append(f"- Return: {lm['total_return']:+.1%}")
        lines.append(f"- MaxDD: {lm['max_dd']:.1%}")
        lines.append(f"- Sharpe: {lm['sharpe']:.2f}")
        lines.append(f"- Calmar: {lm['calmar']:.2f}")
        lines.append(f"- Trades: {lm['n_trades']}")
        lines.append(f"- Win Rate: {lm['win_rate']:.0%}")
        lines.append(f"- Avg Hold: {lm['avg_hold_days']:.1f} days")
        lines.append("")

        # Projected leveraged performance
        lines.append("**Projected Leveraged Performance**:")
        lines.append("")
        lines.append("| Leverage | Est. 12mo Return | Est. 12mo MaxDD | Risk-Adjusted |")
        lines.append("|----------|-------------------|-----------------|---------------|")
        for lev in [1, 2, 3]:
            est_ret = lm['total_return'] * lev
            est_dd = abs(lm['max_dd']) * lev
            risk_note = "OK" if est_dd < 0.40 else "HIGH"
            lines.append(f"| {lev}x | {est_ret:+.1%} | -{est_dd:.1%} | {risk_note} |")
        lines.append("")
    else:
        lines.append("No configs found with 12mo DD < 15%. See grand summary above.")
        lines.append("")

    # ── Comparison to R160 baseline ──
    lines.append("## Comparison to R160 Baseline")
    lines.append("")
    lines.append("| Metric | R160 Var B (1x) | Best R167 (1x) | Improvement |")
    lines.append("|--------|-----------------|----------------|-------------|")
    if sweet:
        best = sweet[0]
        lm = best.l12m_metrics
        metrics_pairs = [
            ("12mo Return", 0.299, lm['total_return']),
            ("12mo MaxDD", -0.075, lm['max_dd']),
            ("12mo Sharpe", 2.20, lm['sharpe']),
        ]
        for name, baseline, optimized in metrics_pairs:
            if 'DD' in name:
                improvement = f"{abs(baseline) - abs(optimized):+.1%}"
            else:
                improvement = f"{optimized - baseline:+.2f}" if 'Sharpe' in name else f"{optimized - baseline:+.1%}"
            lines.append(f"| {name} | {baseline:+.1%} | {optimized:+.1%} | {improvement} |"
                         if 'Sharpe' not in name else
                         f"| {name} | {baseline:.2f} | {optimized:.2f} | {improvement} |")
    lines.append("")

    # ── Conclusion ──
    lines.append("## Conclusion")
    lines.append("")
    if sweet:
        best = sweet[0]
        lm = best.l12m_metrics
        ret_pct = lm['total_return'] * 100
        dd_pct = abs(lm['max_dd']) * 100
        lines.append(f"Best config achieves {ret_pct:+.1f}% 12mo return with {dd_pct:.1f}% MaxDD.")
        if ret_pct > 50:
            lines.append(f"At 2x leverage, estimated ~{ret_pct*2:.0f}% return with ~{dd_pct*2:.0f}% DD.")
            lines.append(f"At 3x leverage, estimated ~{ret_pct*3:.0f}% return with ~{dd_pct*3:.0f}% DD.")
        lines.append("")
        lines.append("Key parameter changes vs R160 defaults:")
        for k, v in best.params.items():
            if v != DEFAULTS[k]:
                lines.append(f"- {k}: {DEFAULTS[k]} -> {v}")
    else:
        lines.append("No configurations met the DD < 15% constraint. Consider relaxing to 20%.")
    lines.append("")

    # Write file
    OUTPUT_MD.write_text('\n'.join(lines))
    log(f"  Results written to {OUTPUT_MD}")


if __name__ == '__main__':
    main()
