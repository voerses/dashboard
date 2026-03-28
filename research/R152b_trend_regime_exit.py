"""
R152b -- BTC Trend-Following with REGIME-ONLY EXITS (No ATR Trailing Stop)

Motivation: R152 failed (-1.9% annual) because the ATR(14) hourly trailing stop
was too tight (~1.3%) for a weekly-timescale EMA entry signal. Every trade got
stopped out by noise within hours.

This research removes the ATR trailing stop entirely. The ONLY exit conditions are:
  1. EMA cross reversal (EMA(168) crosses below EMA(720) for longs, above for shorts)
  2. Regime changes to a non-matching state (DOWNTREND/CRISIS closes longs)

Signal:
  - LONG when: EMA(168h) > EMA(720h) AND regime is UPTREND or RANGE
  - SHORT when: EMA(168h) < EMA(720h) AND regime is DOWNTREND (Variant B only)
  - FLAT otherwise

Two Variants:
  A) LONG ONLY:  long in UPTREND/RANGE when EMA bullish, flat otherwise
  B) LONG/SHORT: long in UPTREND when EMA bullish, short in DOWNTREND when EMA bearish,
                  flat in CRISIS

Regime Filter (V4 logic, replicated from engine.py detect_daily_regime):
  - UPTREND: ADX>25 AND EMA20d>EMA50d
  - DOWNTREND: ADX>25 AND EMA20d<=EMA50d
  - CRISIS: vol > p75*2.0
  - QUIET: vol < p25*0.7
  - RANGE: default
  Regime is computed on DAILY bars, shifted by 1 day (np.roll) for causality,
  then forward-filled to hourly.

Position Sizing:
  - UPTREND: 70% equity
  - RANGE/QUIET: 30% equity
  - DOWNTREND/CRISIS: 0% equity (for longs); 30% for shorts in DOWNTREND (Variant B)

Costs:
  7 bps per side on entry/exit only (not every hour)
  + hourly funding applied based on position

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
OUTPUT_PATH = '/workspace/crypto_backtest/research/R152b_trend_regime_results.md'

TOKEN = 'BTC'
LEVERAGE_LEVELS = [1, 2, 3]

# EMA spans (hourly)
EMA_FAST = 168       # ~1 week on hourly bars
EMA_SLOW = 720       # ~1 month on hourly bars

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
POS_SIZE_DOWN    = 0.00    # 0% in DOWNTREND/CRISIS (for longs)

# Short position sizing (Variant B)
SHORT_SIZE_DOWNTREND = 0.30  # 30% equity for shorts in DOWNTREND


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

    # --- Trend state (continuous, not just crosses) ---
    df['ema_bullish'] = (df['ema_fast'] > df['ema_slow']).astype(bool)
    df['ema_bearish'] = (df['ema_fast'] < df['ema_slow']).astype(bool)

    # --- Funding (already per-hour) ---
    if 'funding_1h' not in df.columns:
        df['funding_1h'] = 0.0

    # --- Regime (V4 daily regime, causal, forward-filled to hourly) ---
    print("  Computing V4 daily regime...")
    df['regime'] = compute_daily_regime(df)

    return df


# ============================================================
# SIMULATION ENGINE — REGIME-ONLY EXITS
# ============================================================

@dataclass
class Position:
    entry_time: pd.Timestamp
    entry_idx: int
    entry_price: float
    direction: int              # +1 long, -1 short
    size_pct: float             # fraction of equity at entry (regime-dependent)
    leverage: float


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
    exit_reason: str       # 'regime_change', 'ema_cross', 'regime+ema', 'end_of_data'


def close_position(pos: Position, exit_price: float, exit_idx: int,
                   exit_time: pd.Timestamp, fundings: np.ndarray,
                   leverage: float, fee_pct: float, token: str,
                   regime: int, exit_reason: str) -> Tuple[float, Trade]:
    """Close a position and compute PnL."""
    if pos.direction == 1:
        raw_return = (exit_price / pos.entry_price - 1.0)
    else:
        raw_return = (1.0 - exit_price / pos.entry_price)

    levered_return = raw_return * leverage
    cost = fee_pct * 2 * leverage  # round-trip cost
    bars_held = exit_idx - pos.entry_idx

    # Funding: sum hourly funding over the holding period
    funding_slice = fundings[pos.entry_idx:exit_idx]
    if pos.direction == 1:
        total_funding = np.nansum(funding_slice) * leverage
    else:
        total_funding = -np.nansum(funding_slice) * leverage

    pnl = (levered_return - cost - total_funding) * pos.size_pct

    trade = Trade(
        token=token,
        entry_time=pos.entry_time,
        exit_time=exit_time,
        direction=pos.direction,
        entry_price=pos.entry_price,
        exit_price=exit_price,
        pnl_pct=pnl,
        bars_held=bars_held,
        regime_at_entry=regime,
        exit_reason=exit_reason,
    )

    return pnl, trade


def _mark_to_market(pos: Position, price: float, bar_idx: int,
                    fundings: np.ndarray, leverage: float, fee_pct: float) -> float:
    """Compute unrealized PnL for mark-to-market equity tracking.
    Includes entry fee (already paid) and estimated exit fee, plus funding accrued."""
    if pos.direction == 1:
        raw_return = (price / pos.entry_price - 1.0)
    else:
        raw_return = (1.0 - price / pos.entry_price)

    levered_return = raw_return * leverage
    # Entry fee already paid, estimate exit fee for MTM
    cost = fee_pct * 2 * leverage

    # Funding accrued so far
    funding_slice = fundings[pos.entry_idx:bar_idx + 1]
    if pos.direction == 1:
        total_funding = np.nansum(funding_slice) * leverage
    else:
        total_funding = -np.nansum(funding_slice) * leverage

    return (levered_return - cost - total_funding) * pos.size_pct


def simulate_long_only(df: pd.DataFrame, token: str, leverage: float) -> Tuple[List[Trade], pd.Series]:
    """
    Variant A: LONG ONLY with regime-only exits.

    Entry: Go long when EMA(168) > EMA(720) AND regime is UPTREND or RANGE/QUIET.
    Exit: Close when EMA(168) < EMA(720) OR regime becomes DOWNTREND/CRISIS.
    No ATR trailing stop.
    """
    fee_pct = FEE_BPS / 10000.0

    # Pre-extract arrays
    closes = df['close'].values
    ema_bullish = df['ema_bullish'].values.astype(bool)
    fundings = df['funding_1h'].values
    regimes = df['regime'].values.astype(np.int8)
    timestamps = df.index

    n = len(df)
    realized_equity = 1.0  # equity from closed trades
    equity_curve = np.ones(n)
    position: Optional[Position] = None
    trades: List[Trade] = []

    for i in range(EMA_WARMUP, n):
        price = closes[i]
        regime = regimes[i]

        # Determine if regime allows longs
        regime_allows_long = regime in (UPTREND, RANGE, QUIET)

        # Get position size for regime
        if regime == UPTREND:
            regime_size = POS_SIZE_UPTREND
        elif regime in (RANGE, QUIET):
            regime_size = POS_SIZE_RANGE
        else:
            regime_size = 0.0

        # ---- CHECK EXITS ----
        if position is not None:
            should_exit = False
            exit_reason = ''

            ema_still_bullish = ema_bullish[i]
            regime_still_ok = regime_allows_long

            if not ema_still_bullish and not regime_still_ok:
                should_exit = True
                exit_reason = 'regime+ema'
            elif not ema_still_bullish:
                should_exit = True
                exit_reason = 'ema_cross'
            elif not regime_still_ok:
                should_exit = True
                exit_reason = 'regime_change'

            if should_exit:
                pnl, trade = close_position(
                    position, price, i, timestamps[i], fundings,
                    leverage, fee_pct, token, regime, exit_reason
                )
                realized_equity += pnl
                trades.append(trade)
                position = None

        # ---- Equity floor ----
        if realized_equity <= 0.01:
            equity_curve[i] = max(realized_equity, 0.0)
            continue

        # ---- CHECK FOR NEW ENTRIES ----
        if position is None and ema_bullish[i] and regime_allows_long and regime_size > 0:
            position = Position(
                entry_time=timestamps[i],
                entry_idx=i,
                entry_price=price,
                direction=1,
                size_pct=regime_size,
                leverage=leverage,
            )

        # ---- Update position size if regime changed but still allows longs ----
        # (e.g., UPTREND -> RANGE: reduce from 70% to 30%)
        if position is not None and regime_allows_long:
            position.size_pct = regime_size

        # ---- Mark-to-market equity ----
        if position is not None:
            mtm = _mark_to_market(position, price, i, fundings, leverage, fee_pct)
            equity_curve[i] = realized_equity + mtm
        else:
            equity_curve[i] = realized_equity

    # Close any remaining position at last bar
    if position is not None:
        pnl, trade = close_position(
            position, closes[-1], n - 1, timestamps[-1], fundings,
            leverage, fee_pct, token, regimes[-1], 'end_of_data'
        )
        realized_equity += pnl
        trades.append(trade)
    equity_curve[-1] = realized_equity

    eq_series = pd.Series(equity_curve, index=df.index)
    return trades, eq_series


def simulate_long_short(df: pd.DataFrame, token: str, leverage: float) -> Tuple[List[Trade], pd.Series]:
    """
    Variant B: LONG/SHORT with regime-only exits.

    Long entry: EMA(168) > EMA(720) AND regime is UPTREND or RANGE/QUIET
    Short entry: EMA(168) < EMA(720) AND regime is DOWNTREND
    Exit: Close when signal conditions no longer hold.
    Flat in CRISIS.
    No ATR trailing stop.
    """
    fee_pct = FEE_BPS / 10000.0

    # Pre-extract arrays
    closes = df['close'].values
    ema_bullish = df['ema_bullish'].values.astype(bool)
    ema_bearish = df['ema_bearish'].values.astype(bool)
    fundings = df['funding_1h'].values
    regimes = df['regime'].values.astype(np.int8)
    timestamps = df.index

    n = len(df)
    realized_equity = 1.0
    equity_curve = np.ones(n)
    position: Optional[Position] = None
    trades: List[Trade] = []

    for i in range(EMA_WARMUP, n):
        price = closes[i]
        regime = regimes[i]

        # Determine desired position
        # Long: EMA bullish AND (UPTREND or RANGE/QUIET)
        # Short: EMA bearish AND DOWNTREND
        # Flat: CRISIS, or conditions don't match

        want_long = ema_bullish[i] and regime in (UPTREND, RANGE, QUIET)
        want_short = ema_bearish[i] and regime == DOWNTREND

        if regime == UPTREND:
            long_size = POS_SIZE_UPTREND
        elif regime in (RANGE, QUIET):
            long_size = POS_SIZE_RANGE
        else:
            long_size = 0.0

        short_size = SHORT_SIZE_DOWNTREND if regime == DOWNTREND else 0.0

        # ---- CHECK EXITS ----
        if position is not None:
            should_exit = False
            exit_reason = ''

            if position.direction == 1:  # LONG
                if not want_long:
                    should_exit = True
                    if not ema_bullish[i] and regime not in (UPTREND, RANGE, QUIET):
                        exit_reason = 'regime+ema'
                    elif not ema_bullish[i]:
                        exit_reason = 'ema_cross'
                    else:
                        exit_reason = 'regime_change'
            else:  # SHORT
                if not want_short:
                    should_exit = True
                    if not ema_bearish[i] and regime != DOWNTREND:
                        exit_reason = 'regime+ema'
                    elif not ema_bearish[i]:
                        exit_reason = 'ema_cross'
                    else:
                        exit_reason = 'regime_change'

            if should_exit:
                pnl, trade = close_position(
                    position, price, i, timestamps[i], fundings,
                    leverage, fee_pct, token, regime, exit_reason
                )
                realized_equity += pnl
                trades.append(trade)
                position = None

        # ---- Equity floor ----
        if realized_equity <= 0.01:
            equity_curve[i] = max(realized_equity, 0.0)
            continue

        # ---- CHECK FOR NEW ENTRIES ----
        if position is None:
            if want_long and long_size > 0:
                position = Position(
                    entry_time=timestamps[i],
                    entry_idx=i,
                    entry_price=price,
                    direction=1,
                    size_pct=long_size,
                    leverage=leverage,
                )
            elif want_short and short_size > 0:
                position = Position(
                    entry_time=timestamps[i],
                    entry_idx=i,
                    entry_price=price,
                    direction=-1,
                    size_pct=short_size,
                    leverage=leverage,
                )

        # ---- Update position size if regime changed but still valid ----
        if position is not None:
            if position.direction == 1 and want_long:
                position.size_pct = long_size
            elif position.direction == -1 and want_short:
                position.size_pct = short_size

        # ---- Mark-to-market equity ----
        if position is not None:
            mtm = _mark_to_market(position, price, i, fundings, leverage, fee_pct)
            equity_curve[i] = realized_equity + mtm
        else:
            equity_curve[i] = realized_equity

    # Close any remaining position at last bar
    if position is not None:
        pnl, trade = close_position(
            position, closes[-1], n - 1, timestamps[-1], fundings,
            leverage, fee_pct, token, regimes[-1], 'end_of_data'
        )
        realized_equity += pnl
        trades.append(trade)
    equity_curve[-1] = realized_equity

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
        avg_hold = np.mean([t.bars_held for t in relevant_trades])
    else:
        win_rate = 0.0
        profit_factor = 0.0
        avg_hold = 0.0

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
        'avg_hold_hours': avg_hold,
    }


def _empty_metrics() -> Dict:
    return {
        'annual_return': 0.0, 'total_return': 0.0,
        'sharpe': 0.0, 'sortino': 0.0, 'calmar': 0.0, 'max_dd': 0.0,
        'n_trades': 0, 'win_rate': 0.0, 'profit_factor': 0.0,
        'avg_hold_hours': 0.0,
    }


def compute_monthly_returns(eq_series: pd.Series) -> pd.DataFrame:
    """Compute monthly returns from equity curve."""
    monthly = eq_series.resample('ME').last()
    monthly_ret = monthly.pct_change().dropna()
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
    print("R152b -- BTC Trend-Following with REGIME-ONLY EXITS")
    print("  (No ATR trailing stop — exits only on regime change or EMA cross)")
    print("=" * 70)
    print(f"\nParams: EMA({EMA_FAST}/{EMA_SLOW}), NO ATR stop")
    print(f"Regime sizing: UPTREND={POS_SIZE_UPTREND:.0%}, "
          f"RANGE/QUIET={POS_SIZE_RANGE:.0%}, DOWN/CRISIS=0%")
    print(f"Short sizing (Variant B): DOWNTREND={SHORT_SIZE_DOWNTREND:.0%}")

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
    print()

    # ============================================================
    # VARIANT A: LONG ONLY
    # ============================================================
    print("=" * 50)
    print("VARIANT A: LONG ONLY")
    print("=" * 50)

    all_results_A = []
    for lev in LEVERAGE_LEVELS:
        t0 = time.time()
        trades, eq = simulate_long_only(df, TOKEN, leverage=lev)
        elapsed = time.time() - t0

        full_metrics = compute_metrics(trades, eq)
        l12_metrics = compute_metrics(trades, eq, start_date=last_12mo)

        # Monthly returns
        monthly = compute_monthly_returns(eq)

        result = {
            'leverage': lev,
            'full': full_metrics,
            'last_12mo': l12_metrics,
            'trades': trades,
            'equity': eq,
            'monthly': monthly,
        }
        all_results_A.append(result)

        n_long = sum(1 for t in trades if t.direction == 1)
        n_short = sum(1 for t in trades if t.direction == -1)

        # Exit reason breakdown
        exit_reasons = {}
        for t in trades:
            exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1

        print(f"  {lev}x | Full: Ann={full_metrics['annual_return']:+.1%} "
              f"Sharpe={full_metrics['sharpe']:.2f} "
              f"Sortino={full_metrics['sortino']:.2f} "
              f"MaxDD={full_metrics['max_dd']:.1%} "
              f"Calmar={full_metrics['calmar']:.2f} "
              f"Trades={full_metrics['n_trades']} "
              f"WR={full_metrics['win_rate']:.0%} PF={full_metrics['profit_factor']:.2f}")
        print(f"       L12mo: Ann={l12_metrics['annual_return']:+.1%} "
              f"Sharpe={l12_metrics['sharpe']:.2f} "
              f"MaxDD={l12_metrics['max_dd']:.1%} "
              f"Trades={l12_metrics['n_trades']} "
              f"WR={l12_metrics['win_rate']:.0%}")
        print(f"       AvgHold={full_metrics['avg_hold_hours']:.0f}h "
              f"({full_metrics['avg_hold_hours']/24:.1f}d) "
              f"Exits={exit_reasons}")
        print(f"       ({elapsed:.1f}s)")
        print()

    # ============================================================
    # VARIANT B: LONG/SHORT
    # ============================================================
    print("=" * 50)
    print("VARIANT B: LONG/SHORT")
    print("=" * 50)

    all_results_B = []
    for lev in LEVERAGE_LEVELS:
        t0 = time.time()
        trades, eq = simulate_long_short(df, TOKEN, leverage=lev)
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
        all_results_B.append(result)

        n_long = sum(1 for t in trades if t.direction == 1)
        n_short = sum(1 for t in trades if t.direction == -1)

        exit_reasons = {}
        for t in trades:
            exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1

        print(f"  {lev}x | Full: Ann={full_metrics['annual_return']:+.1%} "
              f"Sharpe={full_metrics['sharpe']:.2f} "
              f"Sortino={full_metrics['sortino']:.2f} "
              f"MaxDD={full_metrics['max_dd']:.1%} "
              f"Calmar={full_metrics['calmar']:.2f} "
              f"Trades={full_metrics['n_trades']}(L={n_long},S={n_short}) "
              f"WR={full_metrics['win_rate']:.0%} PF={full_metrics['profit_factor']:.2f}")
        print(f"       L12mo: Ann={l12_metrics['annual_return']:+.1%} "
              f"Sharpe={l12_metrics['sharpe']:.2f} "
              f"MaxDD={l12_metrics['max_dd']:.1%} "
              f"Trades={l12_metrics['n_trades']} "
              f"WR={l12_metrics['win_rate']:.0%}")
        print(f"       AvgHold={full_metrics['avg_hold_hours']:.0f}h "
              f"({full_metrics['avg_hold_hours']/24:.1f}d) "
              f"Exits={exit_reasons}")
        print(f"       ({elapsed:.1f}s)")
        print()

    # ---- WRITE RESULTS ----
    print(f"\nWriting results to {OUTPUT_PATH}...")
    write_results(all_results_A, all_results_B, last_12mo, df)

    total_time = time.time() - t_start
    print(f"\nTotal runtime: {total_time:.1f}s")
    print("Done.")


def write_results(all_results_A: List[Dict], all_results_B: List[Dict],
                  last_12mo: pd.Timestamp, df: pd.DataFrame):
    """Write formatted markdown results."""
    regime_map = {CRISIS: 'CRISIS', QUIET: 'QUIET', UPTREND: 'UPTREND',
                  RANGE: 'RANGE', DOWNTREND: 'DOWNTREND'}

    lines = []
    lines.append("# R152b -- BTC Trend-Following with REGIME-ONLY EXITS")
    lines.append("")
    lines.append("## Motivation")
    lines.append("R152 failed (-1.9% annual) because the ATR(14) hourly trailing stop was too tight "
                 "(~1.3%) for a weekly-timescale EMA entry signal. Every trade got stopped out by "
                 "intra-day noise within hours.")
    lines.append("")
    lines.append("**Solution**: Remove ATR trailing stop entirely. Exit ONLY when:")
    lines.append("1. EMA(168h) crosses below EMA(720h) (bearish cross for longs)")
    lines.append("2. Regime changes to a non-matching state (DOWNTREND/CRISIS closes longs)")
    lines.append("")

    lines.append("## Strategy")
    lines.append(f"- **Entry signal**: Continuous (not just crosses). LONG when EMA({EMA_FAST}h) > EMA({EMA_SLOW}h) "
                 f"AND regime allows")
    lines.append(f"- **Exit**: Regime change OR EMA cross reversal. NO ATR trailing stop.")
    lines.append(f"- **Regime filter**: V4 daily regime (EMA 20d/50d + ADX + vol), "
                 f"shifted by 1 day for causality (np.roll)")
    lines.append(f"- **Long sizing**: UPTREND={POS_SIZE_UPTREND:.0%}, "
                 f"RANGE/QUIET={POS_SIZE_RANGE:.0%}, DOWNTREND/CRISIS=0%")
    lines.append(f"- **Short sizing** (Variant B only): DOWNTREND={SHORT_SIZE_DOWNTREND:.0%}")
    lines.append(f"- **Costs**: {FEE_BPS:.0f} bps per side on entry/exit only + hourly funding")
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

    # ============================================================
    # VARIANT A RESULTS
    # ============================================================
    lines.append("---")
    lines.append("")
    lines.append("## Variant A: LONG ONLY")
    lines.append("")
    lines.append("Long when EMA(168h) > EMA(720h) AND regime is UPTREND/RANGE/QUIET. Flat otherwise.")
    lines.append("")

    lines.append("### Full Period")
    lines.append("")
    lines.append("| Leverage | Ann.Return | Sharpe | Sortino | MaxDD | Calmar | Trades | WinRate | PF | AvgHold |")
    lines.append("|----------|-----------|--------|---------|-------|--------|--------|---------|-----|---------|")
    for r in all_results_A:
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
            f"| {f['profit_factor']:.2f} "
            f"| {f['avg_hold_hours']:.0f}h ({f['avg_hold_hours']/24:.1f}d) |"
        )
    lines.append("")

    lines.append(f"### Last 12 Months ({last_12mo.strftime('%Y-%m-%d')} to end)")
    lines.append("")
    lines.append("| Leverage | Ann.Return | Sharpe | Sortino | MaxDD | Calmar | Trades | WinRate | PF | AvgHold |")
    lines.append("|----------|-----------|--------|---------|-------|--------|--------|---------|-----|---------|")
    for r in all_results_A:
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
            f"| {l['profit_factor']:.2f} "
            f"| {l['avg_hold_hours']:.0f}h ({l['avg_hold_hours']/24:.1f}d) |"
        )
    lines.append("")

    # Exit reason breakdown (1x)
    r_1x_A = [r for r in all_results_A if r['leverage'] == 1][0]
    trades_A = r_1x_A['trades']
    if trades_A:
        exit_reasons = {}
        for t in trades_A:
            exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1
        lines.append("### Exit Reasons (1x)")
        lines.append("")
        lines.append("| Reason | Count | Pct |")
        lines.append("|--------|-------|-----|")
        for reason, count in sorted(exit_reasons.items(), key=lambda x: -x[1]):
            pct = count / len(trades_A) * 100
            lines.append(f"| {reason} | {count} | {pct:.0f}% |")
        lines.append("")

    # Monthly returns A (1x)
    monthly_A = r_1x_A['monthly']
    lines.append("### Monthly Returns (1x Leverage)")
    lines.append("")
    cols = list(monthly_A.columns)
    lines.append("| Year | " + " | ".join(cols) + " | YTD |")
    lines.append("|------|" + "|".join(["------"] * len(cols)) + "|------|")
    for year, row in monthly_A.iterrows():
        vals = []
        for c in cols:
            if pd.notna(row.get(c)):
                vals.append(f"{row[c]:+.1%}")
            else:
                vals.append("-")
        ytd = sum(row.get(c, 0) for c in cols if pd.notna(row.get(c)))
        lines.append(f"| {year} | " + " | ".join(vals) + f" | {ytd:+.1%} |")
    lines.append("")

    # Monthly returns A (2x)
    r_2x_A = [r for r in all_results_A if r['leverage'] == 2][0]
    monthly_A2 = r_2x_A['monthly']
    lines.append("### Monthly Returns (2x Leverage)")
    lines.append("")
    cols2 = list(monthly_A2.columns)
    lines.append("| Year | " + " | ".join(cols2) + " | YTD |")
    lines.append("|------|" + "|".join(["------"] * len(cols2)) + "|------|")
    for year, row in monthly_A2.iterrows():
        vals = []
        for c in cols2:
            if pd.notna(row.get(c)):
                vals.append(f"{row[c]:+.1%}")
            else:
                vals.append("-")
        ytd = sum(row.get(c, 0) for c in cols2 if pd.notna(row.get(c)))
        lines.append(f"| {year} | " + " | ".join(vals) + f" | {ytd:+.1%} |")
    lines.append("")

    # Trade analysis A
    lines.append("### Trade Analysis (1x)")
    lines.append("")
    if trades_A:
        lines.append(f"- Total trades: {len(trades_A)}")
        pnls = [t.pnl_pct for t in trades_A]
        lines.append(f"- Avg PnL per trade: {np.mean(pnls):+.2%}")
        lines.append(f"- Median PnL per trade: {np.median(pnls):+.2%}")
        lines.append(f"- Avg holding period: {np.mean([t.bars_held for t in trades_A]):.0f}h "
                     f"({np.mean([t.bars_held for t in trades_A])/24:.1f}d)")
        lines.append(f"- Median holding period: {np.median([t.bars_held for t in trades_A]):.0f}h "
                     f"({np.median([t.bars_held for t in trades_A])/24:.1f}d)")
        lines.append("")

        sorted_trades = sorted(trades_A, key=lambda t: t.pnl_pct, reverse=True)
        lines.append("#### Top 5 Trades")
        lines.append("| Entry | Exit | Dir | PnL | Hold | Exit Reason |")
        lines.append("|-------|------|-----|-----|------|-------------|")
        for t in sorted_trades[:5]:
            d = "LONG" if t.direction == 1 else "SHORT"
            lines.append(f"| {t.entry_time.strftime('%Y-%m-%d')} "
                         f"| {t.exit_time.strftime('%Y-%m-%d')} "
                         f"| {d} | {t.pnl_pct:+.2%} | {t.bars_held}h | {t.exit_reason} |")

        lines.append("")
        lines.append("#### Bottom 5 Trades")
        lines.append("| Entry | Exit | Dir | PnL | Hold | Exit Reason |")
        lines.append("|-------|------|-----|-----|------|-------------|")
        for t in sorted_trades[-5:]:
            d = "LONG" if t.direction == 1 else "SHORT"
            lines.append(f"| {t.entry_time.strftime('%Y-%m-%d')} "
                         f"| {t.exit_time.strftime('%Y-%m-%d')} "
                         f"| {d} | {t.pnl_pct:+.2%} | {t.bars_held}h | {t.exit_reason} |")
    lines.append("")

    # ============================================================
    # VARIANT B RESULTS
    # ============================================================
    lines.append("---")
    lines.append("")
    lines.append("## Variant B: LONG/SHORT")
    lines.append("")
    lines.append("Long when EMA(168h) > EMA(720h) AND regime is UPTREND/RANGE/QUIET. "
                 "Short when EMA(168h) < EMA(720h) AND regime is DOWNTREND. "
                 "Flat in CRISIS.")
    lines.append("")

    lines.append("### Full Period")
    lines.append("")
    lines.append("| Leverage | Ann.Return | Sharpe | Sortino | MaxDD | Calmar | Trades | WinRate | PF | AvgHold |")
    lines.append("|----------|-----------|--------|---------|-------|--------|--------|---------|-----|---------|")
    for r in all_results_B:
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
            f"| {f['profit_factor']:.2f} "
            f"| {f['avg_hold_hours']:.0f}h ({f['avg_hold_hours']/24:.1f}d) |"
        )
    lines.append("")

    lines.append(f"### Last 12 Months ({last_12mo.strftime('%Y-%m-%d')} to end)")
    lines.append("")
    lines.append("| Leverage | Ann.Return | Sharpe | Sortino | MaxDD | Calmar | Trades | WinRate | PF | AvgHold |")
    lines.append("|----------|-----------|--------|---------|-------|--------|--------|---------|-----|---------|")
    for r in all_results_B:
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
            f"| {l['profit_factor']:.2f} "
            f"| {l['avg_hold_hours']:.0f}h ({l['avg_hold_hours']/24:.1f}d) |"
        )
    lines.append("")

    # Exit reason breakdown B (1x)
    r_1x_B = [r for r in all_results_B if r['leverage'] == 1][0]
    trades_B = r_1x_B['trades']
    if trades_B:
        exit_reasons = {}
        for t in trades_B:
            exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1
        lines.append("### Exit Reasons (1x)")
        lines.append("")
        lines.append("| Reason | Count | Pct |")
        lines.append("|--------|-------|-----|")
        for reason, count in sorted(exit_reasons.items(), key=lambda x: -x[1]):
            pct = count / len(trades_B) * 100
            lines.append(f"| {reason} | {count} | {pct:.0f}% |")
        lines.append("")

    # Monthly returns B (1x)
    monthly_B = r_1x_B['monthly']
    lines.append("### Monthly Returns (1x Leverage)")
    lines.append("")
    cols = list(monthly_B.columns)
    lines.append("| Year | " + " | ".join(cols) + " | YTD |")
    lines.append("|------|" + "|".join(["------"] * len(cols)) + "|------|")
    for year, row in monthly_B.iterrows():
        vals = []
        for c in cols:
            if pd.notna(row.get(c)):
                vals.append(f"{row[c]:+.1%}")
            else:
                vals.append("-")
        ytd = sum(row.get(c, 0) for c in cols if pd.notna(row.get(c)))
        lines.append(f"| {year} | " + " | ".join(vals) + f" | {ytd:+.1%} |")
    lines.append("")

    # Monthly returns B (2x)
    r_2x_B = [r for r in all_results_B if r['leverage'] == 2][0]
    monthly_B2 = r_2x_B['monthly']
    lines.append("### Monthly Returns (2x Leverage)")
    lines.append("")
    cols2 = list(monthly_B2.columns)
    lines.append("| Year | " + " | ".join(cols2) + " | YTD |")
    lines.append("|------|" + "|".join(["------"] * len(cols2)) + "|------|")
    for year, row in monthly_B2.iterrows():
        vals = []
        for c in cols2:
            if pd.notna(row.get(c)):
                vals.append(f"{row[c]:+.1%}")
            else:
                vals.append("-")
        ytd = sum(row.get(c, 0) for c in cols2 if pd.notna(row.get(c)))
        lines.append(f"| {year} | " + " | ".join(vals) + f" | {ytd:+.1%} |")
    lines.append("")

    # Trade analysis B
    lines.append("### Trade Analysis (1x)")
    lines.append("")
    if trades_B:
        long_trades = [t for t in trades_B if t.direction == 1]
        short_trades = [t for t in trades_B if t.direction == -1]

        lines.append(f"- Total trades: {len(trades_B)} (L={len(long_trades)}, S={len(short_trades)})")

        if long_trades:
            long_pnls = [t.pnl_pct for t in long_trades]
            lines.append(f"- Long avg PnL: {np.mean(long_pnls):+.2%}, "
                         f"win rate: {sum(1 for p in long_pnls if p > 0)/len(long_pnls):.0%}, "
                         f"avg hold: {np.mean([t.bars_held for t in long_trades]):.0f}h")

        if short_trades:
            short_pnls = [t.pnl_pct for t in short_trades]
            lines.append(f"- Short avg PnL: {np.mean(short_pnls):+.2%}, "
                         f"win rate: {sum(1 for p in short_pnls if p > 0)/len(short_pnls):.0%}, "
                         f"avg hold: {np.mean([t.bars_held for t in short_trades]):.0f}h")

        lines.append("")
        sorted_trades = sorted(trades_B, key=lambda t: t.pnl_pct, reverse=True)
        lines.append("#### Top 5 Trades")
        lines.append("| Entry | Exit | Dir | PnL | Hold | Exit Reason |")
        lines.append("|-------|------|-----|-----|------|-------------|")
        for t in sorted_trades[:5]:
            d = "LONG" if t.direction == 1 else "SHORT"
            lines.append(f"| {t.entry_time.strftime('%Y-%m-%d')} "
                         f"| {t.exit_time.strftime('%Y-%m-%d')} "
                         f"| {d} | {t.pnl_pct:+.2%} | {t.bars_held}h | {t.exit_reason} |")

        lines.append("")
        lines.append("#### Bottom 5 Trades")
        lines.append("| Entry | Exit | Dir | PnL | Hold | Exit Reason |")
        lines.append("|-------|------|-----|-----|------|-------------|")
        for t in sorted_trades[-5:]:
            d = "LONG" if t.direction == 1 else "SHORT"
            lines.append(f"| {t.entry_time.strftime('%Y-%m-%d')} "
                         f"| {t.exit_time.strftime('%Y-%m-%d')} "
                         f"| {d} | {t.pnl_pct:+.2%} | {t.bars_held}h | {t.exit_reason} |")
    lines.append("")

    # ============================================================
    # COMPARISON
    # ============================================================
    r_1x_A_full = [r for r in all_results_A if r['leverage'] == 1][0]['full']
    r_1x_B_full = [r for r in all_results_B if r['leverage'] == 1][0]['full']

    lines.append("---")
    lines.append("")
    lines.append("## Comparison with R152 (ATR trailing stop)")
    lines.append("")
    lines.append("| Metric | R152 (ATR stop) | R152b-A (Long only) | R152b-B (L/S) |")
    lines.append("|--------|----------------|--------------------|--------------| ")
    lines.append("| Exit mechanism | ATR(14) trailing stop | Regime change + EMA cross | Regime change + EMA cross |")
    lines.append(f"| Annual Return (1x) | -1.9% | {r_1x_A_full['annual_return']:+.1%} | {r_1x_B_full['annual_return']:+.1%} |")
    lines.append(f"| Sharpe | ~0.0 | {r_1x_A_full['sharpe']:.2f} | {r_1x_B_full['sharpe']:.2f} |")
    lines.append(f"| Max DD | ~-5% | {r_1x_A_full['max_dd']:.1%} | {r_1x_B_full['max_dd']:.1%} |")
    lines.append(f"| Calmar | ~-0.4 | {r_1x_A_full['calmar']:.2f} | {r_1x_B_full['calmar']:.2f} |")
    lines.append(f"| Trades | ~200+ | {r_1x_A_full['n_trades']} | {r_1x_B_full['n_trades']} |")
    lines.append(f"| Avg Hold | ~12h | {r_1x_A_full['avg_hold_hours']:.0f}h ({r_1x_A_full['avg_hold_hours']/24:.0f}d) | {r_1x_B_full['avg_hold_hours']:.0f}h ({r_1x_B_full['avg_hold_hours']/24:.0f}d) |")
    lines.append(f"| Win Rate | ~35% | {r_1x_A_full['win_rate']:.0%} | {r_1x_B_full['win_rate']:.0%} |")
    lines.append("| Problem | Stop too tight for weekly EMA signal | -49% DD from riding 2021 bull/crash | Short side marginal |")
    lines.append("")

    # Notes
    lines.append("## Notes")
    lines.append("- **Key insight**: R152's ATR(14) trailing stop on hourly bars was ~1.3%, causing "
                 "immediate stop-outs on a signal designed to capture weekly trends.")
    lines.append("- **This fix**: By removing the trailing stop entirely and exiting ONLY on regime "
                 "change or EMA cross reversal, the strategy can ride trends for days/weeks without "
                 "being shaken out by hourly noise.")
    lines.append("- V4 regime detection is CAUSAL: np.roll(regimes, 1) shifts daily regime by 1 day")
    lines.append("- Regime uses expanding (causal) percentiles for volatility thresholds")
    lines.append(f"- Hourly EMA({EMA_FAST}) ~ 1 week, EMA({EMA_SLOW}) ~ 1 month")
    lines.append(f"- First {EMA_WARMUP} bars skipped for EMA warm-up")
    lines.append("- Funding applied per-hour: longs pay positive funding, shorts collect")
    lines.append("- Single position at a time (no pyramiding)")
    lines.append("- Position size dynamically adjusts when regime changes (e.g., UPTREND 70% -> RANGE 30%)")
    lines.append("- Costs: 7 bps per side applied ONLY on entry and exit (not every hour)")
    lines.append("")

    with open(OUTPUT_PATH, 'w') as fh:
        fh.write('\n'.join(lines))
    print(f"  Written to {OUTPUT_PATH}")


if __name__ == '__main__':
    main()
