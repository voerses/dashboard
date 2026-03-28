"""
R152 -- BTC Trend-Following with V4 Regime Filter

Motivation: R150 removed the daily regime filter and got -6.3% annual.
This research RESTORES the regime filter using the V4 detector (confirmed
causal via np.roll shift at engine.py line 1071).

Signal:
  EMA 168 / 720 hourly cross (bullish cross = long, bearish cross = short)

Regime Filter (V4 logic, replicated from engine.py detect_daily_regime):
  - UPTREND: trade at 70% equity
  - RANGE/QUIET: trade at 30% equity
  - DOWNTREND/CRISIS: go flat (0% equity)
  Regime is computed on DAILY bars (resampled from 1h) using EMA 20d/50d + ADX,
  then shifted by 1 day (np.roll) to be causal, then forward-filled to hourly.

Exit:
  ATR(14) trailing stop at 1.5x ATR, breakeven ratchet at 0.5x ATR

Costs:
  4 bps taker per side + 3 bps slippage = 7 bps per side = 14 bps round-trip
  + funding from parquet (funding_1h column, 8h settlement)

Leverage sweep: 1x, 2x, 3x
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
import warnings
import time
import os

warnings.filterwarnings('ignore')

# ============================================================
# CONSTANTS
# ============================================================

DATA_DIR = '/workspace/crypto_backtest/data/perp/1h_cache'
OUTPUT_PATH = '/workspace/crypto_backtest/research/R152_trend_regime_results.md'

TOKEN = 'BTC'
LEVERAGE_LEVELS = [1, 2, 3]

# EMA spans (hourly)
EMA_FAST = 168       # ~1 week on hourly bars
EMA_SLOW = 720       # ~1 month on hourly bars

# ATR — standard 14-period on hourly
ATR_PERIOD = 14

# Exit params — per spec
TRAIL_ATR_MULT = 1.5       # 1.5x ATR trailing stop
BREAKEVEN_ATR_MULT = 0.5   # breakeven ratchet at 0.5x ATR

# Cost
FEE_BPS = 7.0        # 4 bps taker + 3 bps slippage, per side

# EMA warm-up: skip first N bars where EMAs haven't converged
EMA_WARMUP = 720     # at least as long as the slowest EMA

# Hours per year for annualisation
HOURS_PER_YEAR = 8760

# Regime constants (same as v4/engine.py)
CRISIS, QUIET, UPTREND, RANGE, DOWNTREND = 0, 1, 2, 3, 4

# Position sizing by regime
POS_SIZE_UPTREND = 0.70    # 70% equity in UPTREND
POS_SIZE_RANGE   = 0.30    # 30% in RANGE/QUIET
POS_SIZE_DOWN    = 0.00    # 0% in DOWNTREND/CRISIS


# ============================================================
# HELPER FUNCTIONS (replicated from v4/engine.py)
# ============================================================

def _ema(arr, span):
    """Vectorized EMA (same as engine.py)."""
    return pd.Series(arr).ewm(span=span, adjust=False).mean().values


def _rolling_std(arr, w):
    """O(n) rolling std via cumsum (same as engine.py)."""
    a = np.nan_to_num(arr, 0.0)
    cs = np.insert(np.cumsum(a), 0, 0.0)
    cs2 = np.insert(np.cumsum(a ** 2), 0, 0.0)
    s = cs[w:] - cs[:-w]
    s2 = cs2[w:] - cs2[:-w]
    var = (s2 - s ** 2 / w) / max(w - 1, 1)
    var = np.maximum(var, 0)
    out = np.full(len(arr), np.nan)
    out[w - 1:] = np.sqrt(var)
    return out


def _rolling_mean(arr, w):
    """O(n) rolling mean via cumsum (same as engine.py)."""
    cs = np.cumsum(np.nan_to_num(arr, 0.0))
    cs = np.insert(cs, 0, 0.0)
    out = np.full(len(arr), np.nan)
    out[w - 1:] = (cs[w:] - cs[:-w]) / w
    return out


# ============================================================
# REGIME DETECTION (exact V4 logic from engine.py)
# ============================================================

def compute_daily_regime(df_1h: pd.DataFrame) -> pd.Series:
    """
    Compute V4 daily regime from 1h data.

    Steps:
    1. Resample 1h -> daily OHLCV
    2. Compute daily indicators (ADX, EMA 20/50, vol_20)
    3. Run detect_daily_regime() (same logic as engine.py)
    4. Shift by 1 day (np.roll) for causality
    5. Forward-fill to hourly index
    """
    # Step 1: Resample to daily
    df_daily = df_1h.resample('1D').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
    }).dropna(subset=['open'])

    close_d = df_daily['close'].values.astype(np.float64)
    high_d = df_daily['high'].values.astype(np.float64)
    low_d = df_daily['low'].values.astype(np.float64)
    volume_d = df_daily['volume'].values.astype(np.float64)
    n_d = len(close_d)

    # Step 2: Compute daily indicators needed for regime detection
    # True Range
    tr = np.zeros(n_d)
    tr[1:] = np.maximum(
        high_d[1:] - low_d[1:],
        np.maximum(
            np.abs(high_d[1:] - close_d[:-1]),
            np.abs(low_d[1:] - close_d[:-1])
        )
    )

    # ADX (same as engine.py compute_adx_indicators)
    plus_dm = np.zeros(n_d)
    minus_dm = np.zeros(n_d)
    plus_dm[1:] = np.where(
        (high_d[1:] - high_d[:-1]) > (low_d[:-1] - low_d[1:]),
        np.maximum(high_d[1:] - high_d[:-1], 0), 0
    )
    minus_dm[1:] = np.where(
        (low_d[:-1] - low_d[1:]) > (high_d[1:] - high_d[:-1]),
        np.maximum(low_d[:-1] - low_d[1:], 0), 0
    )
    smooth_atr = _ema(tr, 14)
    plus_di = 100 * _ema(plus_dm, 14) / np.maximum(smooth_atr, 1e-10)
    minus_di = 100 * _ema(minus_dm, 14) / np.maximum(smooth_atr, 1e-10)
    dx = np.abs(plus_di - minus_di) / np.maximum(plus_di + minus_di, 1e-10) * 100
    adx = _ema(dx, 14)

    # Daily returns and vol_20
    ret_1 = np.log(close_d / np.maximum(np.roll(close_d, 1), 1e-10))
    ret_1[0] = 0
    vol_20 = _rolling_std(ret_1, 20)

    # EMA 20/50 on daily close
    ema_20 = _ema(close_d, 20)
    ema_50 = _ema(close_d, 50)

    # Step 3: detect_daily_regime (exact V4 logic)
    adx_threshold = 25
    crisis_mult = 2.0
    quiet_mult = 0.7
    min_periods = 60

    vol_series = pd.Series(vol_20)
    vol_p75 = vol_series.expanding(min_periods=min_periods).quantile(0.75).values
    vol_p25 = vol_series.expanding(min_periods=min_periods).quantile(0.25).values

    regimes = np.full(n_d, RANGE, dtype=np.int8)

    valid = ~np.isnan(adx) & ~np.isnan(vol_20) & ~np.isnan(vol_p75)
    crisis = valid & (vol_20 > vol_p75 * crisis_mult)
    quiet = valid & ~crisis & (vol_20 < vol_p25 * quiet_mult)
    strong = valid & ~crisis & ~quiet & (adx > adx_threshold)
    uptrend = strong & (ema_20 > ema_50)
    downtrend = strong & ~uptrend

    regimes[crisis] = CRISIS
    regimes[quiet] = QUIET
    regimes[uptrend] = UPTREND
    regimes[downtrend] = DOWNTREND

    regimes[:20] = RANGE

    # Step 4: Shift by 1 day for causality (exact V4 logic: np.roll)
    regimes_shifted = np.roll(regimes, 1)
    regimes_shifted[0] = RANGE  # default for first bar

    # Step 5: Forward-fill to hourly index
    regime_daily_series = pd.Series(regimes_shifted.astype(float), index=df_daily.index)
    regime_1h = regime_daily_series.reindex(df_1h.index, method='ffill')
    regime_1h = regime_1h.fillna(RANGE).astype(np.int8)

    return regime_1h


def get_position_size_for_regime(regime: int) -> float:
    """Map regime to position size fraction."""
    if regime == UPTREND:
        return POS_SIZE_UPTREND
    elif regime in (RANGE, QUIET):
        return POS_SIZE_RANGE
    else:  # CRISIS or DOWNTREND
        return POS_SIZE_DOWN


# ============================================================
# DATA LOADING & INDICATOR COMPUTATION
# ============================================================

def load_data(token: str) -> pd.DataFrame:
    """Load hourly parquet and compute indicators."""
    path = os.path.join(DATA_DIR, f'{token}_1h.parquet')
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep='first')]

    # --- EMAs ---
    df['ema_fast'] = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=EMA_SLOW, adjust=False).mean()

    # --- Trend state ---
    df['trend_long'] = (df['ema_fast'] > df['ema_slow']).astype(bool)
    df['trend_short'] = (df['ema_fast'] < df['ema_slow']).astype(bool)

    # --- EMA cross signals ---
    prev_trend_long = df['trend_long'].shift(1).fillna(False).astype(bool)
    prev_trend_short = df['trend_short'].shift(1).fillna(False).astype(bool)

    df['ema_cross_long'] = df['trend_long'] & ~prev_trend_long
    df['ema_cross_short'] = df['trend_short'] & ~prev_trend_short

    # --- ATR (14-period per spec) ---
    tr_high_low = df['high'] - df['low']
    tr_high_close = (df['high'] - df['close'].shift(1)).abs()
    tr_low_close = (df['low'] - df['close'].shift(1)).abs()
    df['true_range'] = pd.concat([tr_high_low, tr_high_close, tr_low_close], axis=1).max(axis=1)
    df['atr'] = df['true_range'].rolling(ATR_PERIOD).mean()

    # --- Funding (already per-hour) ---
    if 'funding_1h' not in df.columns:
        df['funding_1h'] = 0.0

    # --- Regime (V4 daily regime, causal, forward-filled to hourly) ---
    print("  Computing V4 daily regime...")
    df['regime'] = compute_daily_regime(df)

    return df


# ============================================================
# SIMULATION ENGINE
# ============================================================

@dataclass
class Position:
    entry_time: pd.Timestamp
    entry_idx: int
    entry_price: float
    direction: int              # +1 long, -1 short
    size_pct: float             # fraction of equity at entry (regime-dependent)
    leverage: float
    trail_stop: float
    breakeven_hit: bool = False
    highest_price: float = 0.0
    lowest_price: float = 999999999.0
    entry_atr: float = 0.0


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
    regime_at_entry: int


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
    trend_longs = df['trend_long'].values.astype(bool)
    trend_shorts = df['trend_short'].values.astype(bool)
    cross_longs = df['ema_cross_long'].values.astype(bool)
    cross_shorts = df['ema_cross_short'].values.astype(bool)
    fundings = df['funding_1h'].values
    regimes = df['regime'].values.astype(np.int8)
    timestamps = df.index

    n = len(df)
    equity = 1.0
    equity_curve = np.ones(n)
    position: Optional[Position] = None  # single position at a time
    trades: List[Trade] = []

    for i in range(EMA_WARMUP, n):
        if np.isnan(atrs[i]):
            equity_curve[i] = equity
            continue

        atr_val = atrs[i]
        if atr_val <= 0:
            equity_curve[i] = equity
            continue

        price = closes[i]
        high_val = highs[i]
        low_val = lows[i]
        regime = regimes[i]

        # Get position size allowed by current regime
        regime_size = get_position_size_for_regime(regime)

        # ---- Force close position if regime goes to DOWNTREND/CRISIS ----
        if position is not None and regime_size == 0.0:
            # Close at current bar close
            pos = position
            if pos.direction == 1:
                raw_return = (price / pos.entry_price - 1.0)
            else:
                raw_return = (1.0 - price / pos.entry_price)
            levered_return = raw_return * leverage
            cost = fee_pct * 2 * leverage
            bars_held = i - pos.entry_idx
            funding_slice = fundings[pos.entry_idx:i]
            if pos.direction == 1:
                total_funding = np.nansum(funding_slice) * leverage
            else:
                total_funding = -np.nansum(funding_slice) * leverage
            pnl = (levered_return - cost - total_funding) * pos.size_pct
            equity += pnl
            trades.append(Trade(
                token=token, entry_time=pos.entry_time,
                exit_time=timestamps[i], direction=pos.direction,
                entry_price=pos.entry_price, exit_price=price,
                pnl_pct=pnl, bars_held=bars_held,
                regime_at_entry=regime,
            ))
            position = None

        # ---- UPDATE EXISTING POSITION (check exits via trailing stop) ----
        if position is not None:
            pos = position
            if pos.direction == 1:  # LONG
                # Update highest seen
                if high_val > pos.highest_price:
                    pos.highest_price = high_val
                    # Ratchet trailing stop up
                    new_trail = pos.highest_price - TRAIL_ATR_MULT * pos.entry_atr
                    if new_trail > pos.trail_stop:
                        pos.trail_stop = new_trail

                # Breakeven ratchet
                if not pos.breakeven_hit:
                    if high_val >= pos.entry_price + BREAKEVEN_ATR_MULT * pos.entry_atr:
                        pos.breakeven_hit = True
                        if pos.entry_price > pos.trail_stop:
                            pos.trail_stop = pos.entry_price

                # Check stop hit (use low of bar)
                if low_val <= pos.trail_stop:
                    exit_price = max(pos.trail_stop, low_val)
                    exit_price = min(exit_price, high_val)
                    raw_return = (exit_price / pos.entry_price - 1.0)
                    levered_return = raw_return * leverage
                    cost = fee_pct * 2 * leverage
                    bars_held = i - pos.entry_idx
                    funding_slice = fundings[pos.entry_idx:i]
                    total_funding = np.nansum(funding_slice) * leverage
                    pnl = (levered_return - cost - total_funding) * pos.size_pct
                    equity += pnl
                    trades.append(Trade(
                        token=token, entry_time=pos.entry_time,
                        exit_time=timestamps[i], direction=pos.direction,
                        entry_price=pos.entry_price, exit_price=exit_price,
                        pnl_pct=pnl, bars_held=bars_held,
                        regime_at_entry=regime,
                    ))
                    position = None

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
                    total_funding = -np.nansum(funding_slice) * leverage
                    pnl = (levered_return - cost - total_funding) * pos.size_pct
                    equity += pnl
                    trades.append(Trade(
                        token=token, entry_time=pos.entry_time,
                        exit_time=timestamps[i], direction=pos.direction,
                        entry_price=pos.entry_price, exit_price=exit_price,
                        pnl_pct=pnl, bars_held=bars_held,
                        regime_at_entry=regime,
                    ))
                    position = None

        # ---- Equity floor: if equity <= 0, we're bust ----
        if equity <= 0.01:
            equity_curve[i] = max(equity, 0.0)
            continue

        # ---- CHECK FOR NEW ENTRIES ----
        if position is not None:
            # Already have a position, skip entry logic
            equity_curve[i] = equity
            continue

        # No position open — check for entries
        if regime_size == 0.0:
            # Regime says stay flat
            equity_curve[i] = equity
            continue

        # LONG cross entry
        if cross_longs[i]:
            position = Position(
                entry_time=timestamps[i],
                entry_idx=i,
                entry_price=price,
                direction=1,
                size_pct=regime_size,
                leverage=leverage,
                trail_stop=price - TRAIL_ATR_MULT * atr_val,
                highest_price=price,
                entry_atr=atr_val,
            )
            equity_curve[i] = equity
            continue

        # SHORT cross entry
        if cross_shorts[i]:
            position = Position(
                entry_time=timestamps[i],
                entry_idx=i,
                entry_price=price,
                direction=-1,
                size_pct=regime_size,
                leverage=leverage,
                trail_stop=price + TRAIL_ATR_MULT * atr_val,
                lowest_price=price,
                entry_atr=atr_val,
            )

        equity_curve[i] = equity

    # Close any remaining position at last close
    if position is not None:
        pos = position
        last_price = closes[-1]
        last_ts = timestamps[-1]
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
            regime_at_entry=regimes[-1],
        ))
    equity_curve[-1] = equity

    eq_series = pd.Series(equity_curve, index=df.index)
    return trades, eq_series


# ============================================================
# METRICS
# ============================================================

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

    # Hourly returns for Sharpe/Sortino
    hourly_returns = eq.pct_change().dropna()
    hourly_returns = hourly_returns.replace([np.inf, -np.inf], 0.0)
    if len(hourly_returns) < 24:
        return _empty_metrics()

    std = hourly_returns.std()
    sharpe = (hourly_returns.mean() / std) * np.sqrt(HOURS_PER_YEAR) if std > 1e-10 else 0.0

    # Sortino (downside deviation)
    downside = hourly_returns[hourly_returns < 0]
    if len(downside) > 0:
        downside_std = downside.std()
        sortino = (hourly_returns.mean() / downside_std) * np.sqrt(HOURS_PER_YEAR) if downside_std > 1e-10 else 0.0
    else:
        sortino = 99.9

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
        'sortino': sortino,
        'calmar': calmar,
        'max_dd': max_dd,
        'n_trades': n_trades,
        'win_rate': win_rate,
        'profit_factor': profit_factor,
    }


def _empty_metrics() -> Dict:
    return {
        'annual_return': 0.0, 'total_return': 0.0,
        'sharpe': 0.0, 'sortino': 0.0, 'calmar': 0.0, 'max_dd': 0.0,
        'n_trades': 0, 'win_rate': 0.0, 'profit_factor': 0.0,
    }


def compute_monthly_returns(eq_series: pd.Series) -> pd.DataFrame:
    """Compute monthly returns from equity curve."""
    monthly = eq_series.resample('ME').last()
    monthly_ret = monthly.pct_change().dropna()
    # Create table with Year rows and Month columns
    df = pd.DataFrame({
        'year': monthly_ret.index.year,
        'month': monthly_ret.index.month,
        'return': monthly_ret.values,
    })
    pivot = df.pivot_table(index='year', columns='month', values='return', aggfunc='first')
    pivot.columns = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][:len(pivot.columns)]
    return pivot


# ============================================================
# MAIN
# ============================================================

def main():
    t_start = time.time()
    print("=" * 70)
    print("R152 -- BTC Trend-Following with V4 Regime Filter")
    print("=" * 70)
    print(f"\nParams: EMA({EMA_FAST}/{EMA_SLOW}), ATR({ATR_PERIOD}), "
          f"Trail={TRAIL_ATR_MULT}x, BE={BREAKEVEN_ATR_MULT}x")
    print(f"Regime sizing: UPTREND={POS_SIZE_UPTREND:.0%}, "
          f"RANGE/QUIET={POS_SIZE_RANGE:.0%}, DOWN/CRISIS=0%")

    # Last 12 months cutoff
    last_12mo = pd.Timestamp('2025-03-28')
    print(f"Last 12mo cutoff: {last_12mo.strftime('%Y-%m-%d')}")
    print(f"Token: {TOKEN}")
    print(f"Leverage levels: {LEVERAGE_LEVELS}")
    print(f"EMA warm-up: {EMA_WARMUP} bars skipped")
    print()

    # Load data
    print(f"--- Loading {TOKEN} ---")
    df = load_data(TOKEN)

    print(f"  Data: {df.index[0].strftime('%Y-%m-%d')} to "
          f"{df.index[-1].strftime('%Y-%m-%d')} ({len(df)} bars)")

    # Regime distribution
    regime_map = {CRISIS: 'CRISIS', QUIET: 'QUIET', UPTREND: 'UPTREND',
                  RANGE: 'RANGE', DOWNTREND: 'DOWNTREND'}
    regime_counts = pd.Series(df['regime'].values).value_counts().sort_index()
    print("  Regime distribution (hourly bars):")
    for regime_val, count in regime_counts.items():
        pct = count / len(df) * 100
        name = regime_map.get(regime_val, f'UNK({regime_val})')
        print(f"    {name}: {count} ({pct:.1f}%)")

    n_cross_long = df['ema_cross_long'].sum()
    n_cross_short = df['ema_cross_short'].sum()
    print(f"  Raw signals: cross_long={n_cross_long}, cross_short={n_cross_short}")
    print()

    all_results = []

    for lev in LEVERAGE_LEVELS:
        t0 = time.time()
        trades, eq = simulate(df, TOKEN, leverage=lev)
        elapsed = time.time() - t0

        full_metrics = compute_metrics(trades, eq)
        l12_metrics = compute_metrics(trades, eq, start_date=last_12mo)
        monthly = compute_monthly_returns(eq)

        result = {
            'leverage': lev,
            'full': full_metrics,
            'last_12mo': l12_metrics,
            'trades': trades,
            'equity': eq,
            'monthly': monthly,
        }
        all_results.append(result)

        # Count trades by direction
        n_long = sum(1 for t in trades if t.direction == 1)
        n_short = sum(1 for t in trades if t.direction == -1)

        # Regime trade counts
        regime_trades = {}
        for t in trades:
            r_name = regime_map.get(t.regime_at_entry, 'UNK')
            regime_trades[r_name] = regime_trades.get(r_name, 0) + 1

        print(f"  {lev}x | Full: Ann={full_metrics['annual_return']:+.1%} "
              f"Sharpe={full_metrics['sharpe']:.2f} "
              f"Sortino={full_metrics['sortino']:.2f} "
              f"MaxDD={full_metrics['max_dd']:.1%} "
              f"Calmar={full_metrics['calmar']:.2f} "
              f"Trades={full_metrics['n_trades']}(L={n_long},S={n_short}) "
              f"WR={full_metrics['win_rate']:.0%} PF={full_metrics['profit_factor']:.2f}")
        print(f"       L12mo: Ann={l12_metrics['annual_return']:+.1%} "
              f"Sharpe={l12_metrics['sharpe']:.2f} "
              f"Sortino={l12_metrics['sortino']:.2f} "
              f"MaxDD={l12_metrics['max_dd']:.1%} "
              f"Trades={l12_metrics['n_trades']} "
              f"WR={l12_metrics['win_rate']:.0%}")
        print(f"       Regime trades: {regime_trades}")
        print(f"       ({elapsed:.1f}s)")
        print()

    # ---- WRITE RESULTS ----
    print(f"\nWriting results to {OUTPUT_PATH}...")
    write_results(all_results, last_12mo, df)

    total_time = time.time() - t_start
    print(f"\nTotal runtime: {total_time:.1f}s")
    print("Done.")


def write_results(all_results: List[Dict], last_12mo: pd.Timestamp,
                  df: pd.DataFrame):
    """Write formatted markdown results."""
    regime_map = {CRISIS: 'CRISIS', QUIET: 'QUIET', UPTREND: 'UPTREND',
                  RANGE: 'RANGE', DOWNTREND: 'DOWNTREND'}

    lines = []
    lines.append("# R152 -- BTC Trend-Following with V4 Regime Filter")
    lines.append("")
    lines.append("## Strategy")
    lines.append(f"- **Entry signal**: Hourly EMA({EMA_FAST})/EMA({EMA_SLOW}) cross "
                 f"(bullish cross = long, bearish cross = short)")
    lines.append(f"- **Regime filter**: V4 daily regime (EMA 20d/50d + ADX + vol), "
                 f"shifted by 1 day for causality (np.roll)")
    lines.append(f"- **Position sizing**: UPTREND={POS_SIZE_UPTREND:.0%}, "
                 f"RANGE/QUIET={POS_SIZE_RANGE:.0%}, DOWNTREND/CRISIS=0%")
    lines.append(f"- **Exit**: {TRAIL_ATR_MULT}x ATR({ATR_PERIOD}) trailing stop, "
                 f"{BREAKEVEN_ATR_MULT}x ATR breakeven ratchet")
    lines.append(f"- **Costs**: {FEE_BPS:.0f} bps per side (4 taker + 3 slippage) + hourly funding")
    lines.append(f"- **Market**: BTC perp (Binance)")
    lines.append(f"- **Data**: {df.index[0].strftime('%Y-%m-%d')} to {df.index[-1].strftime('%Y-%m-%d')} "
                 f"({len(df)} hourly bars)")
    lines.append("")

    # Regime distribution
    lines.append("## Regime Distribution")
    lines.append("")
    regime_counts = pd.Series(df['regime'].values).value_counts().sort_index()
    lines.append("| Regime | Hours | Pct |")
    lines.append("|--------|-------|-----|")
    for regime_val, count in regime_counts.items():
        pct = count / len(df) * 100
        name = regime_map.get(regime_val, f'UNK({regime_val})')
        lines.append(f"| {name} | {count:,} | {pct:.1f}% |")
    lines.append("")

    # Results table
    lines.append("## Results by Leverage")
    lines.append("")
    lines.append(f"Last 12 months cutoff: {last_12mo.strftime('%Y-%m-%d')}")
    lines.append("")
    lines.append("### Full Period")
    lines.append("")
    lines.append("| Leverage | Ann.Return | Sharpe | Sortino | MaxDD | Calmar | Trades | WinRate | PF |")
    lines.append("|----------|-----------|--------|---------|-------|--------|--------|---------|-----|")

    for r in all_results:
        f = r['full']
        lines.append(
            f"| {r['leverage']}x "
            f"| {f['annual_return']:+.1%} "
            f"| {f['sharpe']:.2f} "
            f"| {f['sortino']:.2f} "
            f"| {f['max_dd']:.1%} "
            f"| {f['calmar']:.2f} "
            f"| {f['n_trades']} "
            f"| {f['win_rate']:.0%} "
            f"| {f['profit_factor']:.2f} |"
        )
    lines.append("")

    lines.append("### Last 12 Months (2025-03-28 to 2026-03-28)")
    lines.append("")
    lines.append("| Leverage | Ann.Return | Sharpe | Sortino | MaxDD | Calmar | Trades | WinRate | PF |")
    lines.append("|----------|-----------|--------|---------|-------|--------|--------|---------|-----|")

    for r in all_results:
        l = r['last_12mo']
        lines.append(
            f"| {r['leverage']}x "
            f"| {l['annual_return']:+.1%} "
            f"| {l['sharpe']:.2f} "
            f"| {l['sortino']:.2f} "
            f"| {l['max_dd']:.1%} "
            f"| {l['calmar']:.2f} "
            f"| {l['n_trades']} "
            f"| {l['win_rate']:.0%} "
            f"| {l['profit_factor']:.2f} |"
        )
    lines.append("")

    # Monthly returns (for 1x leverage only)
    r_1x = [r for r in all_results if r['leverage'] == 1][0]
    monthly = r_1x['monthly']
    lines.append("## Monthly Returns (1x Leverage)")
    lines.append("")
    cols = list(monthly.columns)
    lines.append("| Year | " + " | ".join(cols) + " | YTD |")
    lines.append("|------|" + "|".join(["------"] * len(cols)) + "|------|")
    for year, row in monthly.iterrows():
        vals = []
        for c in cols:
            if pd.notna(row.get(c)):
                vals.append(f"{row[c]:+.1%}")
            else:
                vals.append("-")
        ytd = sum(row.get(c, 0) for c in cols if pd.notna(row.get(c)))
        lines.append(f"| {year} | " + " | ".join(vals) + f" | {ytd:+.1%} |")
    lines.append("")

    # Monthly returns for 2x
    r_2x = [r for r in all_results if r['leverage'] == 2][0]
    monthly_2x = r_2x['monthly']
    lines.append("## Monthly Returns (2x Leverage)")
    lines.append("")
    cols2 = list(monthly_2x.columns)
    lines.append("| Year | " + " | ".join(cols2) + " | YTD |")
    lines.append("|------|" + "|".join(["------"] * len(cols2)) + "|------|")
    for year, row in monthly_2x.iterrows():
        vals = []
        for c in cols2:
            if pd.notna(row.get(c)):
                vals.append(f"{row[c]:+.1%}")
            else:
                vals.append("-")
        ytd = sum(row.get(c, 0) for c in cols2 if pd.notna(row.get(c)))
        lines.append(f"| {year} | " + " | ".join(vals) + f" | {ytd:+.1%} |")
    lines.append("")

    # Trade detail analysis
    lines.append("## Trade Analysis (1x Leverage)")
    lines.append("")
    trades_1x = r_1x['trades']
    if trades_1x:
        long_trades = [t for t in trades_1x if t.direction == 1]
        short_trades = [t for t in trades_1x if t.direction == -1]

        lines.append(f"- Total trades: {len(trades_1x)}")
        lines.append(f"- Long trades: {len(long_trades)}")
        lines.append(f"- Short trades: {len(short_trades)}")

        if long_trades:
            long_pnls = [t.pnl_pct for t in long_trades]
            lines.append(f"- Long avg PnL: {np.mean(long_pnls):+.2%}")
            lines.append(f"- Long win rate: {sum(1 for p in long_pnls if p > 0) / len(long_pnls):.0%}")

        if short_trades:
            short_pnls = [t.pnl_pct for t in short_trades]
            lines.append(f"- Short avg PnL: {np.mean(short_pnls):+.2%}")
            lines.append(f"- Short win rate: {sum(1 for p in short_pnls if p > 0) / len(short_pnls):.0%}")

        # Avg bars held
        avg_bars = np.mean([t.bars_held for t in trades_1x])
        lines.append(f"- Avg holding period: {avg_bars:.0f} hours ({avg_bars/24:.1f} days)")

        # Best/worst trades
        sorted_trades = sorted(trades_1x, key=lambda t: t.pnl_pct, reverse=True)
        lines.append("")
        lines.append("### Top 5 Trades")
        lines.append("| Entry | Exit | Dir | PnL | Bars |")
        lines.append("|-------|------|-----|-----|------|")
        for t in sorted_trades[:5]:
            d = "LONG" if t.direction == 1 else "SHORT"
            lines.append(f"| {t.entry_time.strftime('%Y-%m-%d')} "
                         f"| {t.exit_time.strftime('%Y-%m-%d')} "
                         f"| {d} | {t.pnl_pct:+.2%} | {t.bars_held} |")

        lines.append("")
        lines.append("### Bottom 5 Trades")
        lines.append("| Entry | Exit | Dir | PnL | Bars |")
        lines.append("|-------|------|-----|-----|------|")
        for t in sorted_trades[-5:]:
            d = "LONG" if t.direction == 1 else "SHORT"
            lines.append(f"| {t.entry_time.strftime('%Y-%m-%d')} "
                         f"| {t.exit_time.strftime('%Y-%m-%d')} "
                         f"| {d} | {t.pnl_pct:+.2%} | {t.bars_held} |")

    lines.append("")
    lines.append("## Notes")
    lines.append("- V4 regime detection is CAUSAL: np.roll(regimes, 1) shifts daily regime by 1 day")
    lines.append("- Regime uses expanding (causal) percentiles for volatility thresholds")
    lines.append(f"- Hourly EMA({EMA_FAST}) ~ 1 week, EMA({EMA_SLOW}) ~ 1 month")
    lines.append(f"- First {EMA_WARMUP} bars skipped for EMA warm-up")
    lines.append("- Funding applied per-hour: longs pay positive funding, shorts collect")
    lines.append("- Single position at a time (no pyramiding)")
    lines.append("- Position is force-closed when regime transitions to DOWNTREND/CRISIS")
    lines.append(f"- Comparison: R150 (no regime filter) got -6.3% annual at 1x")
    lines.append("")

    with open(OUTPUT_PATH, 'w') as fh:
        fh.write('\n'.join(lines))
    print(f"  Written to {OUTPUT_PATH}")


if __name__ == '__main__':
    main()
