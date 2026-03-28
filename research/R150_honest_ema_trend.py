"""
R150 -- Honest EMA Trend-Following (No Daily Regime Look-Ahead)

Motivation: Previous strategies (e.g. s98) used daily regime from
_align_higher_to_lower which forward-fills today's daily bar to hour 0,
creating look-ahead bias. This research uses ONLY hourly EMAs for trend
determination, eliminating that issue entirely.

Signal:
  PRIMARY: EMA_168 / EMA_720 cross on hourly bars (approx 1-week / 1-month)
    - EMA_168 crosses above EMA_720 => LONG entry
    - EMA_168 crosses below EMA_720 => SHORT entry
  PULLBACK: price touches EMA_20 from above during LONG trend => additional
            long entry; from below during SHORT trend => additional short entry
    - Must have at least MIN_TREND_BARS since last cross (trend established)
    - 168h cooldown between pullback entries per direction
  MACD CONFIRMATION: histogram > 0 for longs, < 0 for shorts
  BB SQUEEZE: Bollinger bandwidth < 95th percentile (relaxed from 80th)

Exit:
  Trailing stop at 3x ATR(48) -- wide enough for hourly noise
  Breakeven ratchet at 1.5x ATR (once price moves that far in favour,
  stop moves to breakeven)

Costs:
  4 bps taker per side + 3 bps slippage = 7 bps per side = 14 bps round-trip
  + funding from parquet (funding_1h column, longs pay when positive)

Position sizing: 25% equity per position, max 4 concurrent positions
Leverage: sweep 1x, 2x, 3x, 4x
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
import warnings
import time
import os
import sys

warnings.filterwarnings('ignore')

# ============================================================
# CONSTANTS
# ============================================================

DATA_DIR = '/workspace/crypto_backtest/data/perp/1h_cache'
OUTPUT_PATH = '/workspace/crypto_backtest/research/R150_honest_ema_trend_results.md'

TOKENS = ['BTC', 'ETH', 'SOL', 'BNB', 'DOGE', 'XRP', 'ADA', 'AVAX', 'LINK', 'DOT']
LEVERAGE_LEVELS = [1, 2, 3, 4]

# EMA spans
EMA_FAST = 168       # ~1 week on hourly bars
EMA_SLOW = 720       # ~1 month on hourly bars
EMA_PULLBACK = 20    # pullback EMA

# MACD params (standard 12/26/9 on hourly)
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# BB params
BB_PERIOD = 20
BB_STD = 2.0
BB_BW_PCTILE = 95    # relaxed from 80th

# ATR — use longer period for hourly bars to smooth out noise
ATR_PERIOD = 48       # 2 days of hourly bars

# Exit params — wider stops for hourly timeframe
TRAIL_ATR_MULT = 3.0       # 3x ATR trailing stop
BREAKEVEN_ATR_MULT = 1.5   # breakeven ratchet at 1.5x ATR

# Cost
FEE_BPS = 7.0        # 4 bps taker + 3 bps slippage, per side

# Position sizing
POS_SIZE_PCT = 0.25   # 25% of equity per position
MAX_POSITIONS = 4

# EMA warm-up: skip first N bars where EMAs haven't converged
EMA_WARMUP = 720     # at least as long as the slowest EMA

# Pullback cooldown: hours between pullback entries per direction
PB_COOLDOWN_HOURS = 168  # 1 week

# Minimum bars since last cross before pullback entry is allowed
MIN_TREND_BARS = 72  # 3 days — trend must be established

# Hours per year for annualisation
HOURS_PER_YEAR = 8760


# ============================================================
# DATA LOADING & INDICATOR COMPUTATION
# ============================================================

def load_data(token: str) -> pd.DataFrame:
    """Load hourly parquet and compute all indicators."""
    path = os.path.join(DATA_DIR, f'{token}_1h.parquet')
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep='first')]

    # --- EMAs ---
    df['ema_fast'] = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=EMA_SLOW, adjust=False).mean()
    df['ema_pb'] = df['close'].ewm(span=EMA_PULLBACK, adjust=False).mean()

    # --- Trend state ---
    df['trend_long'] = (df['ema_fast'] > df['ema_slow']).astype(bool)
    df['trend_short'] = (df['ema_fast'] < df['ema_slow']).astype(bool)

    # --- EMA cross signals (true crossover, not state) ---
    # CRITICAL: .shift(1) on bool produces object dtype with NaN
    # Must cast back to bool after fillna to avoid bitwise NOT bug
    prev_trend_long = df['trend_long'].shift(1).fillna(False).astype(bool)
    prev_trend_short = df['trend_short'].shift(1).fillna(False).astype(bool)

    df['ema_cross_long'] = df['trend_long'] & ~prev_trend_long
    df['ema_cross_short'] = df['trend_short'] & ~prev_trend_short

    # --- Bars since last cross (for pullback gating) ---
    # Count consecutive bars in current trend
    cross_any = df['ema_cross_long'] | df['ema_cross_short']
    df['bars_since_cross'] = 0
    counter = 0
    bsc = np.zeros(len(df), dtype=int)
    cross_vals = cross_any.values
    for idx in range(len(df)):
        if cross_vals[idx]:
            counter = 0
        else:
            counter += 1
        bsc[idx] = counter
    df['bars_since_cross'] = bsc

    # --- Pullback signals ---
    # Long pullback: trend is long, price dips to EMA_20 (low touches it)
    # and prior bar close was above EMA_20 (approaching from above)
    # AND trend is established (minimum bars since cross)
    df['pb_long'] = (
        df['trend_long'] &
        (df['low'] <= df['ema_pb']) &
        (df['close'].shift(1) > df['ema_pb'].shift(1)) &
        (df['bars_since_cross'] >= MIN_TREND_BARS)
    )
    # Short pullback: trend is short, price rallies to EMA_20 (high touches it)
    df['pb_short'] = (
        df['trend_short'] &
        (df['high'] >= df['ema_pb']) &
        (df['close'].shift(1) < df['ema_pb'].shift(1)) &
        (df['bars_since_cross'] >= MIN_TREND_BARS)
    )

    # --- MACD ---
    ema12 = df['close'].ewm(span=MACD_FAST, adjust=False).mean()
    ema26 = df['close'].ewm(span=MACD_SLOW, adjust=False).mean()
    df['macd_line'] = ema12 - ema26
    df['macd_signal'] = df['macd_line'].ewm(span=MACD_SIGNAL, adjust=False).mean()
    df['macd_hist'] = df['macd_line'] - df['macd_signal']

    # --- Bollinger Bandwidth ---
    df['bb_mid'] = df['close'].rolling(BB_PERIOD).mean()
    df['bb_std'] = df['close'].rolling(BB_PERIOD).std()
    df['bb_upper'] = df['bb_mid'] + BB_STD * df['bb_std']
    df['bb_lower'] = df['bb_mid'] - BB_STD * df['bb_std']
    df['bb_bw'] = (df['bb_upper'] - df['bb_lower']) / df['bb_mid']
    # Percentile computed later via fast numpy version

    # --- ATR (48-period for hourly bars) ---
    tr_high_low = df['high'] - df['low']
    tr_high_close = (df['high'] - df['close'].shift(1)).abs()
    tr_low_close = (df['low'] - df['close'].shift(1)).abs()
    df['true_range'] = pd.concat([tr_high_low, tr_high_close, tr_low_close], axis=1).max(axis=1)
    df['atr'] = df['true_range'].rolling(ATR_PERIOD).mean()

    # --- Funding (already per-hour) ---
    if 'funding_1h' not in df.columns:
        df['funding_1h'] = 0.0

    return df


# ============================================================
# VECTORISED BB PERCENTILE (fast version)
# ============================================================

def fast_bb_pctile(bb_bw: pd.Series, window: int = 720, min_periods: int = 168) -> pd.Series:
    """Compute rolling percentile rank of Bollinger bandwidth using numpy."""
    vals = bb_bw.values
    n = len(vals)
    result = np.full(n, np.nan)
    for i in range(min_periods - 1, n):
        start = max(0, i - window + 1)
        window_vals = vals[start:i+1]
        valid = window_vals[~np.isnan(window_vals)]
        if len(valid) >= min_periods:
            result[i] = np.sum(valid <= vals[i]) / len(valid)
    return pd.Series(result, index=bb_bw.index)


# ============================================================
# SIMULATION ENGINE
# ============================================================

@dataclass
class Position:
    entry_time: pd.Timestamp
    entry_idx: int              # bar index for fast funding slice
    entry_price: float
    direction: int              # +1 long, -1 short
    size_pct: float             # fraction of equity at entry
    leverage: float
    trail_stop: float           # current trailing stop price
    breakeven_hit: bool = False
    highest_price: float = 0.0
    lowest_price: float = 999999999.0
    entry_atr: float = 0.0
    signal_type: str = 'cross'


@dataclass
class Trade:
    token: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: int
    entry_price: float
    exit_price: float
    pnl_pct: float        # return on total equity (after fees+funding)
    bars_held: int
    signal_type: str       # 'cross' or 'pullback'


def simulate(df: pd.DataFrame, token: str, leverage: float) -> Tuple[List[Trade], pd.Series]:
    """
    Run the strategy simulation bar-by-bar.
    Returns list of trades and an equity curve (hourly).
    """
    fee_pct = FEE_BPS / 10000.0  # per side

    # Pre-extract arrays for speed
    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    atrs = df['atr'].values
    macd_hists = df['macd_hist'].values
    bb_pctiles = df['bb_bw_pctile'].values
    trend_longs = df['trend_long'].values.astype(bool)
    trend_shorts = df['trend_short'].values.astype(bool)
    cross_longs = df['ema_cross_long'].values.astype(bool)
    cross_shorts = df['ema_cross_short'].values.astype(bool)
    pb_longs = df['pb_long'].values.astype(bool)
    pb_shorts = df['pb_short'].values.astype(bool)
    fundings = df['funding_1h'].values
    timestamps = df.index

    n = len(df)
    equity = 1.0
    equity_curve = np.ones(n)
    positions: List[Position] = []
    trades: List[Trade] = []

    # Track last pullback entry time per direction to enforce cooldown
    last_pb_long_idx = -PB_COOLDOWN_HOURS - 1
    last_pb_short_idx = -PB_COOLDOWN_HOURS - 1

    for i in range(EMA_WARMUP, n):
        if np.isnan(atrs[i]) or np.isnan(bb_pctiles[i]):
            equity_curve[i] = equity
            continue

        atr_val = atrs[i]
        if atr_val <= 0:
            equity_curve[i] = equity
            continue

        price = closes[i]
        high_val = highs[i]
        low_val = lows[i]

        # ---- UPDATE EXISTING POSITIONS (check exits) ----
        closed_indices = []
        for pidx, pos in enumerate(positions):
            if pos.direction == 1:  # LONG
                # Update highest seen
                if high_val > pos.highest_price:
                    pos.highest_price = high_val
                    # Ratchet trailing stop up
                    new_trail = pos.highest_price - TRAIL_ATR_MULT * pos.entry_atr
                    if new_trail > pos.trail_stop:
                        pos.trail_stop = new_trail

                # Breakeven ratchet: once price moved BREAKEVEN_ATR_MULT in favour
                if not pos.breakeven_hit:
                    if high_val >= pos.entry_price + BREAKEVEN_ATR_MULT * pos.entry_atr:
                        pos.breakeven_hit = True
                        if pos.entry_price > pos.trail_stop:
                            pos.trail_stop = pos.entry_price

                # Check stop hit (use low of bar)
                if low_val <= pos.trail_stop:
                    exit_price = max(pos.trail_stop, low_val)  # can't exit below low
                    exit_price = min(exit_price, high_val)      # can't exit above high
                    raw_return = (exit_price / pos.entry_price - 1.0)
                    levered_return = raw_return * leverage
                    cost = fee_pct * 2 * leverage
                    bars_held = i - pos.entry_idx
                    funding_slice = fundings[pos.entry_idx:i]
                    total_funding = np.nansum(funding_slice) * leverage  # longs pay
                    pnl = (levered_return - cost - total_funding) * pos.size_pct
                    equity += pnl
                    trades.append(Trade(
                        token=token, entry_time=pos.entry_time,
                        exit_time=timestamps[i], direction=pos.direction,
                        entry_price=pos.entry_price, exit_price=exit_price,
                        pnl_pct=pnl, bars_held=bars_held,
                        signal_type=pos.signal_type,
                    ))
                    closed_indices.append(pidx)

            else:  # SHORT
                # Update lowest seen
                if low_val < pos.lowest_price:
                    pos.lowest_price = low_val
                    new_trail = pos.lowest_price + TRAIL_ATR_MULT * pos.entry_atr
                    if new_trail < pos.trail_stop:
                        pos.trail_stop = new_trail

                # Breakeven ratchet
                if not pos.breakeven_hit:
                    if low_val <= pos.entry_price - BREAKEVEN_ATR_MULT * pos.entry_atr:
                        pos.breakeven_hit = True
                        if pos.entry_price < pos.trail_stop:
                            pos.trail_stop = pos.entry_price

                # Check stop hit (use high of bar)
                if high_val >= pos.trail_stop:
                    exit_price = min(pos.trail_stop, high_val)
                    exit_price = max(exit_price, low_val)
                    raw_return = (1.0 - exit_price / pos.entry_price)
                    levered_return = raw_return * leverage
                    cost = fee_pct * 2 * leverage
                    bars_held = i - pos.entry_idx
                    funding_slice = fundings[pos.entry_idx:i]
                    total_funding = -np.nansum(funding_slice) * leverage  # shorts collect
                    pnl = (levered_return - cost - total_funding) * pos.size_pct
                    equity += pnl
                    trades.append(Trade(
                        token=token, entry_time=pos.entry_time,
                        exit_time=timestamps[i], direction=pos.direction,
                        entry_price=pos.entry_price, exit_price=exit_price,
                        pnl_pct=pnl, bars_held=bars_held,
                        signal_type=pos.signal_type,
                    ))
                    closed_indices.append(pidx)

        # Remove closed positions (reverse order to preserve indices)
        for pidx in sorted(closed_indices, reverse=True):
            positions.pop(pidx)

        # ---- Equity floor: if equity <= 0, we're bust ----
        if equity <= 0.01:
            equity_curve[i] = max(equity, 0.0)
            continue

        # ---- CHECK FOR NEW ENTRIES ----
        num_open = len(positions)
        if num_open >= MAX_POSITIONS:
            equity_curve[i] = equity
            continue

        macd_ok_long = macd_hists[i] > 0
        macd_ok_short = macd_hists[i] < 0
        bb_ok = bb_pctiles[i] < (BB_BW_PCTILE / 100.0)

        # Count open positions per direction
        n_long = sum(1 for p in positions if p.direction == 1)
        n_short = sum(1 for p in positions if p.direction == -1)

        # LONG cross entry (only one cross position per token at a time)
        if cross_longs[i] and macd_ok_long and bb_ok and n_long == 0:
            pos = Position(
                entry_time=timestamps[i],
                entry_idx=i,
                entry_price=price,
                direction=1,
                size_pct=POS_SIZE_PCT,
                leverage=leverage,
                trail_stop=price - TRAIL_ATR_MULT * atr_val,
                highest_price=price,
                entry_atr=atr_val,
                signal_type='cross',
            )
            positions.append(pos)
            num_open += 1
            n_long += 1

        # SHORT cross entry
        if cross_shorts[i] and macd_ok_short and bb_ok and n_short == 0 and num_open < MAX_POSITIONS:
            pos = Position(
                entry_time=timestamps[i],
                entry_idx=i,
                entry_price=price,
                direction=-1,
                size_pct=POS_SIZE_PCT,
                leverage=leverage,
                trail_stop=price + TRAIL_ATR_MULT * atr_val,
                lowest_price=price,
                entry_atr=atr_val,
                signal_type='cross',
            )
            positions.append(pos)
            num_open += 1
            n_short += 1

        # PULLBACK long entry (additional position, with cooldown)
        if (pb_longs[i] and macd_ok_long and bb_ok
                and num_open < MAX_POSITIONS
                and trend_longs[i]
                and (i - last_pb_long_idx) >= PB_COOLDOWN_HOURS):
            pos = Position(
                entry_time=timestamps[i],
                entry_idx=i,
                entry_price=price,
                direction=1,
                size_pct=POS_SIZE_PCT,
                leverage=leverage,
                trail_stop=price - TRAIL_ATR_MULT * atr_val,
                highest_price=price,
                entry_atr=atr_val,
                signal_type='pullback',
            )
            positions.append(pos)
            num_open += 1
            last_pb_long_idx = i

        # PULLBACK short entry
        if (pb_shorts[i] and macd_ok_short and bb_ok
                and num_open < MAX_POSITIONS
                and trend_shorts[i]
                and (i - last_pb_short_idx) >= PB_COOLDOWN_HOURS):
            pos = Position(
                entry_time=timestamps[i],
                entry_idx=i,
                entry_price=price,
                direction=-1,
                size_pct=POS_SIZE_PCT,
                leverage=leverage,
                trail_stop=price + TRAIL_ATR_MULT * atr_val,
                lowest_price=price,
                entry_atr=atr_val,
                signal_type='pullback',
            )
            positions.append(pos)
            num_open += 1
            last_pb_short_idx = i

        equity_curve[i] = equity

    # Close any remaining positions at last close
    last_price = closes[-1]
    last_ts = timestamps[-1]
    for pos in positions:
        if pos.direction == 1:
            raw_return = (last_price / pos.entry_price - 1.0)
        else:
            raw_return = (1.0 - last_price / pos.entry_price)
        levered_return = raw_return * leverage
        cost = fee_pct * 2 * leverage
        bars_held = (n - 1) - pos.entry_idx
        funding_slice = fundings[pos.entry_idx:]
        if pos.direction == 1:
            total_funding = np.nansum(funding_slice) * leverage
        else:
            total_funding = -np.nansum(funding_slice) * leverage
        pnl = (levered_return - cost - total_funding) * pos.size_pct
        equity += pnl
        trades.append(Trade(
            token=token, entry_time=pos.entry_time,
            exit_time=last_ts, direction=pos.direction,
            entry_price=pos.entry_price, exit_price=last_price,
            pnl_pct=pnl, bars_held=int(bars_held),
            signal_type=pos.signal_type,
        ))
    equity_curve[-1] = equity

    eq_series = pd.Series(equity_curve, index=df.index)
    return trades, eq_series


# ============================================================
# METRICS
# ============================================================

def compute_metrics(trades: List[Trade], eq_series: pd.Series,
                    start_date: Optional[pd.Timestamp] = None) -> Dict:
    """Compute performance metrics. If start_date given, only count from there."""
    if start_date is not None:
        eq = eq_series.loc[eq_series.index >= start_date]
        relevant_trades = [t for t in trades if t.exit_time >= start_date]
    else:
        eq = eq_series
        relevant_trades = trades

    if len(eq) < 2:
        return _empty_metrics()

    # Returns
    total_return = eq.iloc[-1] / eq.iloc[0] - 1.0
    hours = (eq.index[-1] - eq.index[0]).total_seconds() / 3600
    if hours <= 0:
        return _empty_metrics()

    # Annualise safely
    if total_return <= -1.0:
        annual_return = -1.0
    else:
        annual_return = (1 + total_return) ** (HOURS_PER_YEAR / hours) - 1.0

    # Hourly returns for Sharpe
    hourly_returns = eq.pct_change().dropna()
    hourly_returns = hourly_returns.replace([np.inf, -np.inf], 0.0)
    if len(hourly_returns) < 24:
        return _empty_metrics()

    std = hourly_returns.std()
    sharpe = (hourly_returns.mean() / std) * np.sqrt(HOURS_PER_YEAR) if std > 1e-10 else 0.0

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
        gross_profit = sum(t.pnl_pct for t in wins) if wins else 0.0
        gross_loss = abs(sum(t.pnl_pct for t in losses)) if losses else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0.001 else 99.9
    else:
        win_rate = 0.0
        profit_factor = 0.0

    return {
        'annual_return': annual_return,
        'total_return': total_return,
        'sharpe': sharpe,
        'calmar': calmar,
        'max_dd': max_dd,
        'n_trades': n_trades,
        'win_rate': win_rate,
        'profit_factor': profit_factor,
    }


def _empty_metrics() -> Dict:
    return {
        'annual_return': 0.0, 'total_return': 0.0,
        'sharpe': 0.0, 'calmar': 0.0, 'max_dd': 0.0,
        'n_trades': 0, 'win_rate': 0.0, 'profit_factor': 0.0,
    }


# ============================================================
# MAIN
# ============================================================

def main():
    t_start = time.time()
    print("=" * 70)
    print("R150 -- Honest EMA Trend-Following (No Daily Regime Look-Ahead)")
    print("=" * 70)
    print(f"\nParams: EMA({EMA_FAST}/{EMA_SLOW}), ATR({ATR_PERIOD}), "
          f"Trail={TRAIL_ATR_MULT}x, BE={BREAKEVEN_ATR_MULT}x, "
          f"PB_cool={PB_COOLDOWN_HOURS}h, MinTrend={MIN_TREND_BARS}bars")

    all_results = []
    portfolio_equities = {}  # leverage -> list of eq_series

    # Last 12 months cutoff
    last_12mo = pd.Timestamp.now() - pd.DateOffset(months=12)
    print(f"Last 12mo cutoff: {last_12mo.strftime('%Y-%m-%d')}")
    print(f"Tokens: {', '.join(TOKENS)}")
    print(f"Leverage levels: {LEVERAGE_LEVELS}")
    print(f"EMA warm-up: {EMA_WARMUP} bars skipped")
    print()

    for token in TOKENS:
        print(f"--- Loading {token} ---")
        try:
            df = load_data(token)
        except FileNotFoundError:
            print(f"  WARNING: {token} data not found, skipping")
            continue

        # Compute fast BB percentile
        print(f"  Computing BB percentile ({len(df)} bars)...")
        df['bb_bw_pctile'] = fast_bb_pctile(df['bb_bw'])

        print(f"  Data: {df.index[0].strftime('%Y-%m-%d')} to "
              f"{df.index[-1].strftime('%Y-%m-%d')} ({len(df)} bars)")

        n_cross_long = df['ema_cross_long'].sum()
        n_cross_short = df['ema_cross_short'].sum()
        n_pb_long = df['pb_long'].sum()
        n_pb_short = df['pb_short'].sum()
        print(f"  Raw signals: cross_long={n_cross_long}, cross_short={n_cross_short}, "
              f"pb_long={n_pb_long}, pb_short={n_pb_short}")

        for lev in LEVERAGE_LEVELS:
            t0 = time.time()
            trades, eq = simulate(df, token, leverage=lev)
            elapsed = time.time() - t0

            full_metrics = compute_metrics(trades, eq)
            l12_metrics = compute_metrics(trades, eq, start_date=last_12mo)

            result = {
                'token': token,
                'leverage': lev,
                'full': full_metrics,
                'last_12mo': l12_metrics,
            }
            all_results.append(result)

            # Store for portfolio
            if lev not in portfolio_equities:
                portfolio_equities[lev] = []
            portfolio_equities[lev].append(eq)

            # Count trade types
            n_cross = sum(1 for t in trades if t.signal_type == 'cross')
            n_pb = sum(1 for t in trades if t.signal_type == 'pullback')

            print(f"  {lev}x | Full: Ann={full_metrics['annual_return']:+.1%} "
                  f"Sharpe={full_metrics['sharpe']:.2f} MaxDD={full_metrics['max_dd']:.1%} "
                  f"Trades={full_metrics['n_trades']}(c={n_cross},pb={n_pb}) "
                  f"WR={full_metrics['win_rate']:.0%} PF={full_metrics['profit_factor']:.2f} | "
                  f"L12mo: Ann={l12_metrics['annual_return']:+.1%} "
                  f"Sharpe={l12_metrics['sharpe']:.2f} MaxDD={l12_metrics['max_dd']:.1%} "
                  f"Trades={l12_metrics['n_trades']} "
                  f"({elapsed:.1f}s)")

        print()

    # ---- PORTFOLIO ----
    print("=" * 70)
    print("PORTFOLIO (equal-weight across tokens)")
    print("=" * 70)

    portfolio_results = []
    for lev in LEVERAGE_LEVELS:
        if lev not in portfolio_equities or len(portfolio_equities[lev]) == 0:
            continue

        eqs = portfolio_equities[lev]
        combined = pd.DataFrame({f'eq_{i}': eq for i, eq in enumerate(eqs)})
        combined = combined.sort_index().ffill().bfill()

        # Normalise each equity curve so it starts at 1
        for col in combined.columns:
            first_valid = combined[col].first_valid_index()
            if first_valid is not None:
                combined[col] = combined[col] / combined.loc[first_valid, col]

        # Equal-weight portfolio: average of normalised equity curves
        portfolio_eq_series = combined.mean(axis=1)

        full_port = compute_metrics([], portfolio_eq_series)
        l12_port = compute_metrics([], portfolio_eq_series, start_date=last_12mo)

        # Aggregate trade stats
        total_trades_full = sum(r['full']['n_trades'] for r in all_results if r['leverage'] == lev)
        total_trades_l12 = sum(r['last_12mo']['n_trades'] for r in all_results if r['leverage'] == lev)

        active_full = [r for r in all_results if r['leverage'] == lev and r['full']['n_trades'] > 0]
        avg_wr_full = np.mean([r['full']['win_rate'] for r in active_full]) if active_full else 0
        avg_pf_full = np.mean([r['full']['profit_factor'] for r in active_full]) if active_full else 0

        active_l12 = [r for r in all_results if r['leverage'] == lev and r['last_12mo']['n_trades'] > 0]
        avg_wr_l12 = np.mean([r['last_12mo']['win_rate'] for r in active_l12]) if active_l12 else 0
        avg_pf_l12 = np.mean([r['last_12mo']['profit_factor'] for r in active_l12]) if active_l12 else 0

        port_entry = {
            'leverage': lev,
            'full': full_port,
            'last_12mo': l12_port,
            'total_trades_full': total_trades_full,
            'total_trades_l12': total_trades_l12,
            'avg_wr_full': avg_wr_full,
            'avg_pf_full': avg_pf_full,
            'avg_wr_l12': avg_wr_l12,
            'avg_pf_l12': avg_pf_l12,
        }
        portfolio_results.append(port_entry)

        print(f"  {lev}x | Full: Ann={full_port['annual_return']:+.1%} "
              f"Sharpe={full_port['sharpe']:.2f} MaxDD={full_port['max_dd']:.1%} "
              f"Calmar={full_port['calmar']:.2f} Trades={total_trades_full} "
              f"AvgWR={avg_wr_full:.0%} AvgPF={avg_pf_full:.2f}")
        print(f"       L12mo: Ann={l12_port['annual_return']:+.1%} "
              f"Sharpe={l12_port['sharpe']:.2f} MaxDD={l12_port['max_dd']:.1%} "
              f"Trades={total_trades_l12} AvgWR={avg_wr_l12:.0%} AvgPF={avg_pf_l12:.2f}")

    # ---- WRITE RESULTS ----
    print(f"\nWriting results to {OUTPUT_PATH}...")
    write_results(all_results, portfolio_results, last_12mo)

    total_time = time.time() - t_start
    print(f"\nTotal runtime: {total_time:.1f}s")
    print("Done.")


def write_results(all_results: List[Dict], portfolio_results: List[Dict],
                  last_12mo: pd.Timestamp):
    """Write formatted markdown results."""
    lines = []
    lines.append("# R150 -- Honest EMA Trend-Following Results")
    lines.append("")
    lines.append("## Strategy")
    lines.append(f"- **Trend**: Hourly EMA({EMA_FAST})/EMA({EMA_SLOW}) cross (no daily regime)")
    lines.append(f"- **Pullback**: Price touches EMA({EMA_PULLBACK}) during active trend "
                 f"(min {MIN_TREND_BARS} bars after cross)")
    lines.append("- **Confirmation**: MACD histogram direction, BB bandwidth < 95th pctile")
    lines.append(f"- **Exit**: {TRAIL_ATR_MULT}x ATR({ATR_PERIOD}) trailing stop, "
                 f"{BREAKEVEN_ATR_MULT}x ATR breakeven ratchet")
    lines.append(f"- **Costs**: {FEE_BPS:.0f} bps per side (4 taker + 3 slippage) + hourly funding")
    lines.append(f"- **Sizing**: {POS_SIZE_PCT:.0%} equity per position, max {MAX_POSITIONS} positions")
    lines.append(f"- **Pullback cooldown**: {PB_COOLDOWN_HOURS}h between pullback entries per direction")
    lines.append("")
    lines.append(f"Last 12 months cutoff: {last_12mo.strftime('%Y-%m-%d')}")
    lines.append("")

    # Per-token tables by leverage
    lines.append("## Per-Token Results")
    lines.append("")

    for lev in LEVERAGE_LEVELS:
        lines.append(f"### {lev}x Leverage")
        lines.append("")
        lines.append("| Token | Ann.Ret(Full) | Ann.Ret(12mo) | Sharpe(Full) | Sharpe(12mo) | MaxDD(Full) | MaxDD(12mo) | Calmar(Full) | Trades | WinRate | PF |")
        lines.append("|-------|--------------|--------------|-------------|-------------|------------|------------|-------------|--------|---------|-----|")

        for r in all_results:
            if r['leverage'] != lev:
                continue
            f = r['full']
            l = r['last_12mo']
            lines.append(
                f"| {r['token']} "
                f"| {f['annual_return']:+.1%} "
                f"| {l['annual_return']:+.1%} "
                f"| {f['sharpe']:.2f} "
                f"| {l['sharpe']:.2f} "
                f"| {f['max_dd']:.1%} "
                f"| {l['max_dd']:.1%} "
                f"| {f['calmar']:.2f} "
                f"| {f['n_trades']} "
                f"| {f['win_rate']:.0%} "
                f"| {f['profit_factor']:.2f} |"
            )
        lines.append("")

    # Portfolio table
    lines.append("## Portfolio (Equal-Weight)")
    lines.append("")
    lines.append("| Leverage | Ann.Ret(Full) | Ann.Ret(12mo) | Sharpe(Full) | Sharpe(12mo) | MaxDD(Full) | MaxDD(12mo) | Calmar(Full) | Trades(Full) | Trades(12mo) | AvgWR | AvgPF |")
    lines.append("|---------|--------------|--------------|-------------|-------------|------------|------------|-------------|-------------|-------------|-------|-------|")

    for p in portfolio_results:
        f = p['full']
        l = p['last_12mo']
        lines.append(
            f"| {p['leverage']}x "
            f"| {f['annual_return']:+.1%} "
            f"| {l['annual_return']:+.1%} "
            f"| {f['sharpe']:.2f} "
            f"| {l['sharpe']:.2f} "
            f"| {f['max_dd']:.1%} "
            f"| {l['max_dd']:.1%} "
            f"| {f['calmar']:.2f} "
            f"| {p['total_trades_full']} "
            f"| {p['total_trades_l12']} "
            f"| {p['avg_wr_full']:.0%} "
            f"| {p['avg_pf_full']:.2f} |"
        )

    lines.append("")
    lines.append("## Notes")
    lines.append("- No daily regime used (avoids look-ahead bias from daily bar forward-fill)")
    lines.append(f"- Hourly EMA({EMA_FAST}) ~ 1 week, EMA({EMA_SLOW}) ~ 1 month")
    lines.append(f"- First {EMA_WARMUP} bars skipped for EMA warm-up")
    lines.append("- Funding applied per-hour: longs pay positive funding, shorts collect")
    lines.append("- BB squeeze relaxed to 95th pctile (vs 80th in s98)")
    lines.append(f"- {PB_COOLDOWN_HOURS}h cooldown between pullback entries")
    lines.append(f"- Pullback requires {MIN_TREND_BARS} bars since last cross (trend established)")
    lines.append(f"- ATR period = {ATR_PERIOD} (2 days) for wider stop that respects hourly noise")
    lines.append("")

    with open(OUTPUT_PATH, 'w') as fh:
        fh.write('\n'.join(lines))


if __name__ == '__main__':
    main()
