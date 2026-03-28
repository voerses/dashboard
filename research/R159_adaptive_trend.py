"""
R159 -- Per-Token ADAPTIVE Trend-Following with Smart Exit Management

Key insight: different tokens have different optimal timeframes and volatility
profiles. Instead of one-size-fits-all, adapt to each token dynamically.

Entry Signal (Per-Token Adaptive):
  - 3 EMA cross timeframes: FAST(8/21h), MEDIUM(20/50h), SLOW(50/200h)
  - Score: +1 per bullish cross, -1 per bearish cross => range [-3, +3]
  - Score >= 2: STRONG LONG (full size)
  - Score == 1: MILD LONG (half size)
  - Score == 0: FLAT
  - Score == -1: MILD SHORT (half size)
  - Score <= -2: STRONG SHORT (full size)

Exit Strategy (Volatility-Calibrated per token):
  1. Trailing stop: 3x ATR(24) from peak/trough (wider than R152's 1.5x)
  2. Partial profit: at +5x ATR close 50%, tighten trail to 2x ATR
  3. Time-based: if position hasn't moved +2x ATR in 10 days, close
  4. Regime exit: if score flips to opposite direction, close immediately

Portfolio Construction:
  - Up to 10 longs + 5 shorts at any time
  - Prioritize strongest scores (|3| > |2|)
  - Equal weight within each group
  - Target: 60% long, 30% short, 10% cash
  - Recheck scores every 4 hours

Token Universe:
  - All tokens from 1h_cache with >1yr data
  - Filter: trailing 30-day average dollar volume > $5M/day

Costs: 7 bps per side + funding from parquet
Leverage sweep: 1x, 2x, 3x
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
import warnings
import time
import os
from collections import defaultdict

warnings.filterwarnings('ignore')

# ============================================================
# CONSTANTS
# ============================================================

DATA_DIR = '/workspace/crypto_backtest/data/perp/1h_cache'
OUTPUT_PATH = '/workspace/crypto_backtest/research/R159_adaptive_trend_results.md'

LEVERAGE_LEVELS = [1, 2, 3]

# EMA spans (hourly)
EMA_FAST_PAIR = (8, 21)       # Fast timeframe
EMA_MED_PAIR = (20, 50)       # Medium timeframe
EMA_SLOW_PAIR = (50, 200)     # Slow timeframe

# ATR period for exits
ATR_PERIOD = 24  # hourly bars

# Exit parameters
TRAIL_ATR_MULT = 3.0           # initial trailing stop at 3x ATR
PARTIAL_PROFIT_ATR = 5.0       # take partial profit at 5x ATR
PARTIAL_CLOSE_FRAC = 0.5       # close 50% at partial profit
TIGHTENED_TRAIL_ATR = 2.0      # tighten trail to 2x ATR after partial
TIME_EXIT_HOURS = 240          # 10 days = 240 hours
TIME_EXIT_ATR_THRESHOLD = 2.0  # must move 2x ATR in 10 days or exit

# Portfolio limits
MAX_LONGS = 10
MAX_SHORTS = 5

# Recheck interval
RECHECK_HOURS = 4

# Costs
FEE_BPS = 7.0  # per side

# Minimum data length (>1 year of hourly)
MIN_HOURS = 8760

# Minimum 30-day average daily dollar volume
MIN_DAILY_DVOL = 5_000_000

# EMA warm-up: need enough data for slowest EMA to converge
EMA_WARMUP = 250  # 200 + buffer

HOURS_PER_YEAR = 8760


# ============================================================
# DATA LOADING & INDICATOR COMPUTATION (VECTORIZED)
# ============================================================

def load_and_prepare_token(token: str) -> Optional[pd.DataFrame]:
    """Load token data and compute all indicators vectorized."""
    path = os.path.join(DATA_DIR, f'{token}_1h.parquet')
    if not os.path.exists(path):
        return None

    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep='first')]

    if len(df) < MIN_HOURS:
        return None

    # --- Volume filter: trailing 30-day avg daily dollar volume ---
    dollar_vol = df['volume'] * df['close']
    # Rolling 720h (30 days) sum, then divide by 30 for daily avg
    rolling_dvol = dollar_vol.rolling(720, min_periods=168).mean() * 24
    # We need the token to pass the filter for at least some period
    # Use the filter dynamically: mask bars where volume is too low
    df['passes_volume'] = (rolling_dvol >= MIN_DAILY_DVOL).astype(np.int8)

    # If the token never passes the volume filter, skip
    if df['passes_volume'].sum() == 0:
        return None

    # --- EMA crosses (3 timeframes) ---
    for label, (fast_span, slow_span) in [
        ('fast', EMA_FAST_PAIR),
        ('med', EMA_MED_PAIR),
        ('slow', EMA_SLOW_PAIR),
    ]:
        ema_f = df['close'].ewm(span=fast_span, adjust=False).mean()
        ema_s = df['close'].ewm(span=slow_span, adjust=False).mean()
        # +1 if fast > slow (bullish), -1 if fast < slow (bearish)
        df[f'cross_{label}'] = np.where(ema_f > ema_s, 1, -1).astype(np.int8)

    # --- Composite trend score: sum of 3 crosses => [-3, +3] ---
    df['trend_score'] = (
        df['cross_fast'].astype(np.int16) +
        df['cross_med'].astype(np.int16) +
        df['cross_slow'].astype(np.int16)
    ).astype(np.int8)

    # --- ATR(24) for exit management ---
    tr_hl = df['high'] - df['low']
    tr_hc = (df['high'] - df['close'].shift(1)).abs()
    tr_lc = (df['low'] - df['close'].shift(1)).abs()
    tr = pd.concat([tr_hl, tr_hc, tr_lc], axis=1).max(axis=1)
    df['atr'] = tr.rolling(ATR_PERIOD, min_periods=ATR_PERIOD).mean()

    # --- Funding ---
    if 'funding_1h' not in df.columns:
        df['funding_1h'] = 0.0

    return df


# ============================================================
# BTC REGIME (for analysis breakdown)
# ============================================================

def compute_btc_regime(btc_df: pd.DataFrame) -> pd.Series:
    """Simple BTC regime: EMA20d > EMA50d = uptrend, else downtrend."""
    # Resample to daily
    daily = btc_df['close'].resample('1D').last().dropna()
    ema20 = daily.ewm(span=20, adjust=False).mean()
    ema50 = daily.ewm(span=50, adjust=False).mean()
    regime_daily = np.where(ema20 > ema50, 'BTC_UP', 'BTC_DOWN')
    regime_series = pd.Series(regime_daily, index=daily.index)
    # Forward-fill to hourly
    regime_hourly = regime_series.reindex(btc_df.index, method='ffill')
    regime_hourly = regime_hourly.fillna('BTC_DOWN')
    return regime_hourly


# ============================================================
# PORTFOLIO SIMULATION (Vectorized score, loop over rebalance points)
# ============================================================

@dataclass
class TokenPosition:
    token: str
    direction: int          # +1 long, -1 short
    size_weight: float      # fraction of portfolio equity
    entry_price: float
    entry_time: pd.Timestamp
    entry_idx: int          # index into the common hourly timeline
    entry_atr: float
    peak_price: float       # highest price since entry (for longs)
    trough_price: float     # lowest price since entry (for shorts)
    trail_stop: float
    trail_mult: float       # current trailing multiplier (starts at 3x, tightens to 2x)
    partial_taken: bool = False
    remaining_frac: float = 1.0  # fraction of original position still open


@dataclass
class ClosedTrade:
    token: str
    direction: int
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    pnl_on_equity: float    # PnL as fraction of total equity at entry
    bars_held: int
    exit_reason: str


def run_portfolio_simulation(
    token_data: Dict[str, pd.DataFrame],
    common_index: pd.DatetimeIndex,
    leverage: float,
) -> Tuple[List[ClosedTrade], pd.Series, Dict]:
    """
    Run the multi-token portfolio simulation.

    We iterate over 4-hour rebalance checkpoints. Between checkpoints,
    we check exits every hour using vectorized lookups.
    """
    fee_pct = FEE_BPS / 10000.0
    n_bars = len(common_index)

    # Pre-extract numpy arrays for each token aligned to common_index
    # This is the key optimization: all lookups are O(1) array access
    token_list = sorted(token_data.keys())
    n_tokens = len(token_list)
    token_idx_map = {t: i for i, t in enumerate(token_list)}

    # Arrays: (n_tokens, n_bars)
    closes = np.full((n_tokens, n_bars), np.nan)
    highs = np.full((n_tokens, n_bars), np.nan)
    lows = np.full((n_tokens, n_bars), np.nan)
    atrs = np.full((n_tokens, n_bars), np.nan)
    scores = np.full((n_tokens, n_bars), 0, dtype=np.int8)
    fundings = np.full((n_tokens, n_bars), 0.0)
    volume_ok = np.full((n_tokens, n_bars), False, dtype=bool)

    for token, df in token_data.items():
        tidx = token_idx_map[token]
        # Reindex to common_index (forward fill for missing hours)
        aligned = df.reindex(common_index)
        closes[tidx] = aligned['close'].values
        highs[tidx] = aligned['high'].values
        lows[tidx] = aligned['low'].values
        atrs[tidx] = aligned['atr'].values
        scores[tidx] = aligned['trend_score'].fillna(0).values.astype(np.int8)
        fundings[tidx] = aligned['funding_1h'].fillna(0).values
        volume_ok[tidx] = aligned['passes_volume'].fillna(0).values.astype(bool)

    # Simulation state
    equity = 1.0
    equity_curve = np.ones(n_bars)
    positions: Dict[str, TokenPosition] = {}  # token -> position
    trades: List[ClosedTrade] = []

    # Track PnL by token and portfolio composition
    token_pnl = defaultdict(float)
    token_time_in_portfolio = defaultdict(int)  # hours held

    # Track PnL by BTC regime
    regime_pnl = defaultdict(float)

    # BTC regime
    btc_token = 'BTC'
    btc_tidx = token_idx_map.get(btc_token)

    def _close_position(pos: TokenPosition, bar_idx: int, reason: str):
        nonlocal equity
        token = pos.token
        tidx = token_idx_map[token]
        exit_price = closes[tidx, bar_idx]
        if np.isnan(exit_price):
            return

        if pos.direction == 1:
            raw_ret = (exit_price / pos.entry_price - 1.0)
        else:
            raw_ret = (1.0 - exit_price / pos.entry_price)

        levered_ret = raw_ret * leverage
        cost = fee_pct * 2 * leverage  # round-trip

        # Funding: accumulate from entry to exit
        funding_slice = fundings[tidx, pos.entry_idx:bar_idx]
        if pos.direction == 1:
            total_funding = np.nansum(funding_slice) * leverage
        else:
            total_funding = -np.nansum(funding_slice) * leverage

        # PnL scaled by position weight and remaining fraction
        pnl = (levered_ret - cost - total_funding) * pos.size_weight * pos.remaining_frac

        equity += pnl
        token_pnl[token] += pnl
        token_time_in_portfolio[token] += (bar_idx - pos.entry_idx)

        trades.append(ClosedTrade(
            token=token,
            direction=pos.direction,
            entry_time=pos.entry_time,
            exit_time=common_index[bar_idx],
            entry_price=pos.entry_price,
            exit_price=exit_price,
            pnl_on_equity=pnl,
            bars_held=bar_idx - pos.entry_idx,
            exit_reason=reason,
        ))

    def _partial_close(pos: TokenPosition, bar_idx: int):
        """Close 50% of position and tighten trailing stop."""
        nonlocal equity
        token = pos.token
        tidx = token_idx_map[token]
        current_price = closes[tidx, bar_idx]
        if np.isnan(current_price):
            return

        if pos.direction == 1:
            raw_ret = (current_price / pos.entry_price - 1.0)
        else:
            raw_ret = (1.0 - current_price / pos.entry_price)

        levered_ret = raw_ret * leverage
        # Only pay exit fee for the partial (entry fee already paid)
        cost_partial = fee_pct * leverage  # one-way fee for partial exit

        funding_slice = fundings[tidx, pos.entry_idx:bar_idx]
        if pos.direction == 1:
            total_funding = np.nansum(funding_slice) * leverage
        else:
            total_funding = -np.nansum(funding_slice) * leverage

        close_frac = PARTIAL_CLOSE_FRAC
        pnl = (levered_ret - cost_partial - total_funding) * pos.size_weight * close_frac * pos.remaining_frac

        equity += pnl
        token_pnl[token] += pnl

        # Record partial trade
        trades.append(ClosedTrade(
            token=token,
            direction=pos.direction,
            entry_time=pos.entry_time,
            exit_time=common_index[bar_idx],
            entry_price=pos.entry_price,
            exit_price=current_price,
            pnl_on_equity=pnl,
            bars_held=bar_idx - pos.entry_idx,
            exit_reason='partial_profit',
        ))

        # Update position: reduce remaining fraction, tighten trail
        pos.remaining_frac *= (1.0 - close_frac)
        pos.partial_taken = True
        pos.trail_mult = TIGHTENED_TRAIL_ATR
        # Re-set trailing stop with tightened multiplier
        if pos.direction == 1:
            pos.trail_stop = pos.peak_price - TIGHTENED_TRAIL_ATR * pos.entry_atr
        else:
            pos.trail_stop = pos.trough_price + TIGHTENED_TRAIL_ATR * pos.entry_atr

    # Main simulation loop
    start_bar = EMA_WARMUP
    last_rebalance = start_bar

    for i in range(start_bar, n_bars):
        # --- CHECK EXITS FOR ALL OPEN POSITIONS ---
        tokens_to_close = []
        for token, pos in positions.items():
            tidx = token_idx_map[token]
            price = closes[tidx, i]
            hi = highs[tidx, i]
            lo = lows[tidx, i]
            atr_val = atrs[tidx, i]

            if np.isnan(price) or np.isnan(hi) or np.isnan(lo):
                continue

            bars_held = i - pos.entry_idx

            # Exit 1: Regime exit — score flips to opposite direction
            current_score = int(scores[tidx, i])
            if pos.direction == 1 and current_score <= -1:
                tokens_to_close.append((token, 'regime_flip'))
                continue
            if pos.direction == -1 and current_score >= 1:
                tokens_to_close.append((token, 'regime_flip'))
                continue

            # Exit 2: Trailing stop
            if pos.direction == 1:
                # Update peak
                if hi > pos.peak_price:
                    pos.peak_price = hi
                    new_trail = pos.peak_price - pos.trail_mult * pos.entry_atr
                    if new_trail > pos.trail_stop:
                        pos.trail_stop = new_trail
                # Check stop
                if lo <= pos.trail_stop:
                    tokens_to_close.append((token, 'trail_stop'))
                    continue
            else:
                # Update trough
                if lo < pos.trough_price:
                    pos.trough_price = lo
                    new_trail = pos.trough_price + pos.trail_mult * pos.entry_atr
                    if new_trail < pos.trail_stop:
                        pos.trail_stop = new_trail
                # Check stop
                if hi >= pos.trail_stop:
                    tokens_to_close.append((token, 'trail_stop'))
                    continue

            # Exit 3: Partial profit at 5x ATR
            if not pos.partial_taken and not np.isnan(atr_val) and atr_val > 0:
                if pos.direction == 1:
                    profit_dist = price - pos.entry_price
                else:
                    profit_dist = pos.entry_price - price
                if profit_dist >= PARTIAL_PROFIT_ATR * pos.entry_atr:
                    _partial_close(pos, i)

            # Exit 4: Time-based exit (dead money)
            if bars_held >= TIME_EXIT_HOURS:
                if not np.isnan(atr_val) and atr_val > 0 and pos.entry_atr > 0:
                    if pos.direction == 1:
                        move = price - pos.entry_price
                    else:
                        move = pos.entry_price - price
                    if move < TIME_EXIT_ATR_THRESHOLD * pos.entry_atr:
                        tokens_to_close.append((token, 'time_exit'))
                        continue

        # Execute closes
        for token, reason in tokens_to_close:
            if token in positions:
                _close_position(positions[token], i, reason)
                del positions[token]

        # Equity floor
        if equity <= 0.01:
            equity_curve[i] = max(equity, 0.0)
            continue

        # --- REBALANCE EVERY 4 HOURS ---
        if (i - last_rebalance) >= RECHECK_HOURS or i == start_bar:
            last_rebalance = i

            # Count current positions
            current_longs = {t: p for t, p in positions.items() if p.direction == 1}
            current_shorts = {t: p for t, p in positions.items() if p.direction == -1}

            # Collect candidate tokens with their scores
            long_candidates = []
            short_candidates = []

            for j, token in enumerate(token_list):
                if token in positions:
                    continue
                if not volume_ok[j, i]:
                    continue
                if np.isnan(closes[j, i]) or np.isnan(atrs[j, i]) or atrs[j, i] <= 0:
                    continue

                score = int(scores[j, i])
                if score >= 2:
                    long_candidates.append((token, score, 1.0))  # full size
                elif score == 1:
                    long_candidates.append((token, score, 0.5))  # half size
                elif score == -1:
                    short_candidates.append((token, score, 0.5))  # half size
                elif score <= -2:
                    short_candidates.append((token, score, 1.0))  # full size

            # Sort by absolute score (strongest first)
            long_candidates.sort(key=lambda x: -x[1])
            short_candidates.sort(key=lambda x: x[1])

            # Determine slots available
            long_slots = MAX_LONGS - len(current_longs)
            short_slots = MAX_SHORTS - len(current_shorts)

            # Enter new longs
            new_longs = long_candidates[:long_slots]
            # Enter new shorts
            new_shorts = short_candidates[:short_slots]

            # Compute position weights
            # Target: 60% long, 30% short, 10% cash
            # Divide equally among positions within each group
            total_longs = len(current_longs) + len(new_longs)
            total_shorts = len(current_shorts) + len(new_shorts)

            if total_longs > 0:
                long_weight_each = 0.60 / total_longs
            else:
                long_weight_each = 0.0

            if total_shorts > 0:
                short_weight_each = 0.30 / total_shorts
            else:
                short_weight_each = 0.0

            # Open new long positions
            for token, score, size_mult in new_longs:
                tidx = token_idx_map[token]
                entry_price = closes[tidx, i]
                atr_val = atrs[tidx, i]
                weight = long_weight_each * size_mult

                positions[token] = TokenPosition(
                    token=token,
                    direction=1,
                    size_weight=weight,
                    entry_price=entry_price,
                    entry_time=common_index[i],
                    entry_idx=i,
                    entry_atr=atr_val,
                    peak_price=entry_price,
                    trough_price=entry_price,
                    trail_stop=entry_price - TRAIL_ATR_MULT * atr_val,
                    trail_mult=TRAIL_ATR_MULT,
                )

            # Open new short positions
            for token, score, size_mult in new_shorts:
                tidx = token_idx_map[token]
                entry_price = closes[tidx, i]
                atr_val = atrs[tidx, i]
                weight = short_weight_each * size_mult

                positions[token] = TokenPosition(
                    token=token,
                    direction=-1,
                    size_weight=weight,
                    entry_price=entry_price,
                    entry_time=common_index[i],
                    entry_idx=i,
                    entry_atr=atr_val,
                    peak_price=entry_price,
                    trough_price=entry_price,
                    trail_stop=entry_price + TRAIL_ATR_MULT * atr_val,
                    trail_mult=TRAIL_ATR_MULT,
                )

        equity_curve[i] = equity

    # Close all remaining positions at end
    for token, pos in list(positions.items()):
        _close_position(pos, n_bars - 1, 'end_of_data')
    equity_curve[-1] = equity

    eq_series = pd.Series(equity_curve, index=common_index)

    stats = {
        'token_pnl': dict(token_pnl),
        'token_time': dict(token_time_in_portfolio),
    }

    return trades, eq_series, stats


# ============================================================
# METRICS
# ============================================================

def compute_metrics(trades: List[ClosedTrade], eq_series: pd.Series,
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

    hourly_returns = eq.pct_change().dropna()
    hourly_returns = hourly_returns.replace([np.inf, -np.inf], 0.0)
    if len(hourly_returns) < 24:
        return _empty_metrics()

    std = hourly_returns.std()
    sharpe = (hourly_returns.mean() / std) * np.sqrt(HOURS_PER_YEAR) if std > 1e-10 else 0.0

    downside = hourly_returns[hourly_returns < 0]
    if len(downside) > 0:
        downside_std = downside.std()
        sortino = (hourly_returns.mean() / downside_std) * np.sqrt(HOURS_PER_YEAR) if downside_std > 1e-10 else 0.0
    else:
        sortino = 99.9

    cummax = eq.cummax()
    dd = (eq - cummax) / cummax
    max_dd = dd.min()

    calmar = annual_return / abs(max_dd) if abs(max_dd) > 0.001 else 0.0

    n_trades = len(relevant_trades)
    if n_trades > 0:
        wins = [t for t in relevant_trades if t.pnl_on_equity > 0]
        losses = [t for t in relevant_trades if t.pnl_on_equity <= 0]
        win_rate = len(wins) / n_trades
        gross_profit = sum(t.pnl_on_equity for t in wins) if wins else 0.0
        gross_loss = abs(sum(t.pnl_on_equity for t in losses)) if losses else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0.001 else 99.9
        avg_pnl = np.mean([t.pnl_on_equity for t in relevant_trades])
        avg_bars = np.mean([t.bars_held for t in relevant_trades])
    else:
        win_rate = 0.0
        profit_factor = 0.0
        avg_pnl = 0.0
        avg_bars = 0.0

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
        'avg_pnl': avg_pnl,
        'avg_bars_held': avg_bars,
    }


def _empty_metrics() -> Dict:
    return {
        'annual_return': 0.0, 'total_return': 0.0,
        'sharpe': 0.0, 'sortino': 0.0, 'calmar': 0.0, 'max_dd': 0.0,
        'n_trades': 0, 'win_rate': 0.0, 'profit_factor': 0.0,
        'avg_pnl': 0.0, 'avg_bars_held': 0.0,
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
    month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    pivot.columns = [month_names[c - 1] for c in pivot.columns]
    return pivot


# ============================================================
# ANALYSIS HELPERS
# ============================================================

def analyze_by_btc_regime(
    trades: List[ClosedTrade],
    eq_series: pd.Series,
    btc_regime: pd.Series,
) -> Dict:
    """Break down portfolio performance by BTC regime."""
    # Compute hourly returns
    hourly_ret = eq_series.pct_change().fillna(0).replace([np.inf, -np.inf], 0)

    results = {}
    for regime_name in ['BTC_UP', 'BTC_DOWN']:
        mask = btc_regime == regime_name
        regime_hours = mask.sum()
        if regime_hours < 24:
            results[regime_name] = {
                'hours': int(regime_hours),
                'pct_time': 0.0,
                'total_return': 0.0,
                'ann_return': 0.0,
                'sharpe': 0.0,
                'n_trades': 0,
            }
            continue

        regime_ret = hourly_ret[mask]
        cum_ret = (1 + regime_ret).prod() - 1
        hours_total = regime_hours
        pct_time = hours_total / len(eq_series) * 100

        std = regime_ret.std()
        mean_ret = regime_ret.mean()
        sharpe = (mean_ret / std) * np.sqrt(HOURS_PER_YEAR) if std > 1e-10 else 0.0

        # Annualize
        if cum_ret <= -1:
            ann_ret = -1.0
        else:
            ann_ret = (1 + cum_ret) ** (HOURS_PER_YEAR / hours_total) - 1

        # Count trades that started in this regime
        regime_trades = []
        for t in trades:
            # Check BTC regime at entry time
            if t.entry_time in btc_regime.index:
                idx = btc_regime.index.get_indexer([t.entry_time], method='ffill')[0]
                if idx >= 0 and btc_regime.iloc[idx] == regime_name:
                    regime_trades.append(t)

        results[regime_name] = {
            'hours': int(hours_total),
            'pct_time': pct_time,
            'total_return': cum_ret,
            'ann_return': ann_ret,
            'sharpe': sharpe,
            'n_trades': len(regime_trades),
        }

    return results


def get_exit_reason_breakdown(trades: List[ClosedTrade]) -> Dict[str, int]:
    """Count trades by exit reason."""
    reasons = defaultdict(int)
    for t in trades:
        reasons[t.exit_reason] += 1
    return dict(reasons)


# ============================================================
# MAIN
# ============================================================

def main():
    t_start = time.time()
    print("=" * 70)
    print("R159 -- Per-Token ADAPTIVE Trend-Following")
    print("=" * 70)
    print(f"\nEMA Timeframes: FAST={EMA_FAST_PAIR}, MED={EMA_MED_PAIR}, SLOW={EMA_SLOW_PAIR}")
    print(f"ATR period: {ATR_PERIOD}h")
    print(f"Trail stop: {TRAIL_ATR_MULT}x ATR (tightens to {TIGHTENED_TRAIL_ATR}x after partial)")
    print(f"Partial profit at {PARTIAL_PROFIT_ATR}x ATR, close {PARTIAL_CLOSE_FRAC:.0%}")
    print(f"Time exit: {TIME_EXIT_HOURS}h ({TIME_EXIT_HOURS/24:.0f}d) if <{TIME_EXIT_ATR_THRESHOLD}x ATR move")
    print(f"Portfolio: max {MAX_LONGS} longs, {MAX_SHORTS} shorts, recheck every {RECHECK_HOURS}h")
    print(f"Costs: {FEE_BPS} bps/side + funding")
    print(f"Min data: {MIN_HOURS}h, Min daily dvol: ${MIN_DAILY_DVOL:,.0f}")
    print()

    # --- LOAD ALL TOKENS ---
    print("Loading token data...")
    token_data = {}
    all_files = sorted([f for f in os.listdir(DATA_DIR) if f.endswith('.parquet')])

    for f in all_files:
        token = f.replace('_1h.parquet', '')
        df = load_and_prepare_token(token)
        if df is not None:
            token_data[token] = df

    print(f"  Loaded {len(token_data)} tokens (from {len(all_files)} total)")
    print(f"  Tokens: {', '.join(sorted(token_data.keys())[:20])}...")
    print()

    # --- BUILD COMMON INDEX ---
    # Use the union of all token indices
    all_indices = [df.index for df in token_data.values()]
    common_index = all_indices[0]
    for idx in all_indices[1:]:
        common_index = common_index.union(idx)
    common_index = common_index.sort_values()
    common_index = common_index[~common_index.duplicated()]
    print(f"  Common index: {common_index[0]} to {common_index[-1]} ({len(common_index)} bars)")
    print()

    # --- BTC regime for analysis ---
    btc_df = token_data.get('BTC')
    btc_regime = None
    if btc_df is not None:
        btc_regime = compute_btc_regime(btc_df)
        # Reindex to common
        btc_regime = btc_regime.reindex(common_index, method='ffill').fillna('BTC_DOWN')

    # --- RUN SIMULATION ---
    last_12mo = pd.Timestamp('2025-03-17')

    all_results = []

    for lev in LEVERAGE_LEVELS:
        t0 = time.time()
        print(f"  Running {lev}x leverage...")
        trades, eq, stats = run_portfolio_simulation(token_data, common_index, leverage=lev)
        elapsed = time.time() - t0

        full_metrics = compute_metrics(trades, eq)
        l12_metrics = compute_metrics(trades, eq, start_date=last_12mo)
        monthly = compute_monthly_returns(eq)

        # Exit reason breakdown
        exit_reasons = get_exit_reason_breakdown(trades)

        # BTC regime breakdown
        regime_breakdown = {}
        if btc_regime is not None:
            regime_breakdown = analyze_by_btc_regime(trades, eq, btc_regime)

        result = {
            'leverage': lev,
            'full': full_metrics,
            'last_12mo': l12_metrics,
            'trades': trades,
            'equity': eq,
            'monthly': monthly,
            'stats': stats,
            'exit_reasons': exit_reasons,
            'regime_breakdown': regime_breakdown,
        }
        all_results.append(result)

        print(f"    Full: Ann={full_metrics['annual_return']:+.1%} "
              f"Sharpe={full_metrics['sharpe']:.2f} "
              f"MaxDD={full_metrics['max_dd']:.1%} "
              f"Trades={full_metrics['n_trades']} "
              f"WR={full_metrics['win_rate']:.0%} "
              f"PF={full_metrics['profit_factor']:.2f}")
        print(f"    L12m: Ann={l12_metrics['annual_return']:+.1%} "
              f"Sharpe={l12_metrics['sharpe']:.2f} "
              f"MaxDD={l12_metrics['max_dd']:.1%} "
              f"Trades={l12_metrics['n_trades']}")
        print(f"    Exit reasons: {exit_reasons}")
        print(f"    ({elapsed:.1f}s)")
        print()

    # --- WRITE RESULTS ---
    print("Writing results...")
    write_results(all_results, last_12mo, common_index, token_data, btc_regime)
    total_time = time.time() - t_start
    print(f"\nTotal runtime: {total_time:.1f}s")
    print("Done.")


def write_results(
    all_results: List[Dict],
    last_12mo: pd.Timestamp,
    common_index: pd.DatetimeIndex,
    token_data: Dict[str, pd.DataFrame],
    btc_regime: Optional[pd.Series],
):
    """Write formatted markdown results."""
    lines = []
    lines.append("# R159 -- Per-Token ADAPTIVE Trend-Following with Smart Exit Management")
    lines.append("")
    lines.append("## Strategy Design")
    lines.append("")
    lines.append("### Entry Signal (Per-Token Adaptive)")
    lines.append(f"- 3 EMA cross timeframes: FAST{EMA_FAST_PAIR}, MEDIUM{EMA_MED_PAIR}, SLOW{EMA_SLOW_PAIR}")
    lines.append("- Score: +1 per bullish cross (fast>slow), -1 per bearish => [-3, +3]")
    lines.append("- Score >= 2: STRONG LONG (full size), == 1: MILD LONG (half size)")
    lines.append("- Score <= -2: STRONG SHORT (full size), == -1: MILD SHORT (half size)")
    lines.append("- Score == 0: FLAT")
    lines.append("")
    lines.append("### Exit Strategy (Volatility-Calibrated)")
    lines.append(f"- **Trailing stop**: {TRAIL_ATR_MULT}x ATR({ATR_PERIOD}) from peak/trough")
    lines.append(f"- **Partial profit**: At +{PARTIAL_PROFIT_ATR}x ATR, close {PARTIAL_CLOSE_FRAC:.0%}, tighten to {TIGHTENED_TRAIL_ATR}x ATR")
    lines.append(f"- **Time exit**: {TIME_EXIT_HOURS}h ({TIME_EXIT_HOURS//24}d) without +{TIME_EXIT_ATR_THRESHOLD}x ATR move")
    lines.append("- **Regime exit**: Score flips to opposite direction => immediate close")
    lines.append("")
    lines.append("### Portfolio Construction")
    lines.append(f"- Max {MAX_LONGS} longs + {MAX_SHORTS} shorts")
    lines.append("- Target: 60% long, 30% short, 10% cash")
    lines.append(f"- Recheck every {RECHECK_HOURS}h, equal weight within groups")
    lines.append(f"- Costs: {FEE_BPS} bps/side + hourly funding")
    lines.append("")
    lines.append(f"### Universe")
    lines.append(f"- {len(token_data)} tokens with >{MIN_HOURS}h data and >${MIN_DAILY_DVOL/1e6:.0f}M daily dollar volume")
    lines.append(f"- Data: {common_index[0].strftime('%Y-%m-%d')} to {common_index[-1].strftime('%Y-%m-%d')} ({len(common_index):,} hourly bars)")
    lines.append("")

    # --- Token universe ---
    lines.append("### Token Universe")
    lines.append("")
    token_list = sorted(token_data.keys())
    # Show in groups of 10
    for i in range(0, len(token_list), 15):
        chunk = token_list[i:i+15]
        lines.append(", ".join(chunk))
    lines.append("")
    lines.append(f"**Total: {len(token_list)} tokens**")
    lines.append("")

    # --- RESULTS BY LEVERAGE ---
    lines.append("## Results by Leverage")
    lines.append("")
    lines.append("### Full Period")
    lines.append("")
    lines.append("| Lev | Ann.Return | Total | Sharpe | Sortino | MaxDD | Calmar | Trades | WR | PF | Avg PnL |")
    lines.append("|-----|-----------|-------|--------|---------|-------|--------|--------|----|----|---------|")
    for r in all_results:
        f = r['full']
        lines.append(
            f"| {r['leverage']}x "
            f"| {f['annual_return']:+.1%} "
            f"| {f['total_return']:+.1%} "
            f"| {f['sharpe']:.2f} "
            f"| {f['sortino']:.2f} "
            f"| {f['max_dd']:.1%} "
            f"| {f['calmar']:.2f} "
            f"| {f['n_trades']} "
            f"| {f['win_rate']:.0%} "
            f"| {f['profit_factor']:.2f} "
            f"| {f['avg_pnl']:+.3%} |"
        )
    lines.append("")

    lines.append(f"### Last 12 Months (since {last_12mo.strftime('%Y-%m-%d')})")
    lines.append("")
    lines.append("| Lev | Ann.Return | Total | Sharpe | Sortino | MaxDD | Calmar | Trades | WR | PF |")
    lines.append("|-----|-----------|-------|--------|---------|-------|--------|--------|----|----|")
    for r in all_results:
        l = r['last_12mo']
        lines.append(
            f"| {r['leverage']}x "
            f"| {l['annual_return']:+.1%} "
            f"| {l['total_return']:+.1%} "
            f"| {l['sharpe']:.2f} "
            f"| {l['sortino']:.2f} "
            f"| {l['max_dd']:.1%} "
            f"| {l['calmar']:.2f} "
            f"| {l['n_trades']} "
            f"| {l['win_rate']:.0%} "
            f"| {l['profit_factor']:.2f} |"
        )
    lines.append("")

    # --- MONTHLY RETURNS ---
    for lev_target in [1, 2]:
        r = [x for x in all_results if x['leverage'] == lev_target][0]
        monthly = r['monthly']
        lines.append(f"## Monthly Returns ({lev_target}x Leverage)")
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

    # --- EXIT REASON BREAKDOWN ---
    lines.append("## Exit Reason Breakdown (1x)")
    lines.append("")
    r_1x = [x for x in all_results if x['leverage'] == 1][0]
    reasons = r_1x['exit_reasons']
    lines.append("| Reason | Count | Pct |")
    lines.append("|--------|-------|-----|")
    total_exits = sum(reasons.values()) if reasons else 1
    for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
        lines.append(f"| {reason} | {count} | {count/total_exits:.1%} |")
    lines.append("")

    # --- TOKEN PNL ATTRIBUTION ---
    lines.append("## Token PnL Attribution (1x Leverage)")
    lines.append("")
    token_pnl = r_1x['stats']['token_pnl']
    token_time = r_1x['stats']['token_time']

    # Sort by PnL
    sorted_pnl = sorted(token_pnl.items(), key=lambda x: -x[1])
    lines.append("### Top 15 Contributors")
    lines.append("")
    lines.append("| Token | PnL (% equity) | Hours Held |")
    lines.append("|-------|---------------|------------|")
    for token, pnl in sorted_pnl[:15]:
        hours = token_time.get(token, 0)
        lines.append(f"| {token} | {pnl:+.2%} | {hours:,} |")
    lines.append("")

    lines.append("### Bottom 15 Contributors")
    lines.append("")
    lines.append("| Token | PnL (% equity) | Hours Held |")
    lines.append("|-------|---------------|------------|")
    for token, pnl in sorted_pnl[-15:]:
        hours = token_time.get(token, 0)
        lines.append(f"| {token} | {pnl:+.2%} | {hours:,} |")
    lines.append("")

    # --- MOST HELD TOKENS ---
    lines.append("## Most Held Tokens (by hours in portfolio, 1x)")
    lines.append("")
    sorted_time = sorted(token_time.items(), key=lambda x: -x[1])
    lines.append("| Token | Hours | Days | PnL |")
    lines.append("|-------|-------|------|-----|")
    for token, hours in sorted_time[:20]:
        pnl = token_pnl.get(token, 0)
        lines.append(f"| {token} | {hours:,} | {hours/24:.0f} | {pnl:+.2%} |")
    lines.append("")

    # --- BTC REGIME BREAKDOWN ---
    if r_1x['regime_breakdown']:
        lines.append("## Performance by BTC Regime (1x)")
        lines.append("")
        lines.append("BTC regime: EMA(20d) > EMA(50d) = UPTREND, else DOWNTREND")
        lines.append("")
        lines.append("| Regime | Hours | % Time | Total Return | Ann.Return | Sharpe | Trades |")
        lines.append("|--------|-------|--------|-------------|-----------|--------|--------|")
        for regime_name, data in r_1x['regime_breakdown'].items():
            lines.append(
                f"| {regime_name} "
                f"| {data['hours']:,} "
                f"| {data['pct_time']:.1f}% "
                f"| {data['total_return']:+.1%} "
                f"| {data['ann_return']:+.1%} "
                f"| {data['sharpe']:.2f} "
                f"| {data['n_trades']} |"
            )
        lines.append("")

    # --- TRADE ANALYSIS ---
    lines.append("## Trade Analysis (1x Leverage)")
    lines.append("")
    trades_1x = r_1x['trades']
    if trades_1x:
        long_trades = [t for t in trades_1x if t.direction == 1]
        short_trades = [t for t in trades_1x if t.direction == -1]
        lines.append(f"- **Total trades**: {len(trades_1x)}")
        lines.append(f"- **Long trades**: {len(long_trades)}")
        lines.append(f"- **Short trades**: {len(short_trades)}")

        if long_trades:
            long_pnls = [t.pnl_on_equity for t in long_trades]
            lines.append(f"- **Long avg PnL**: {np.mean(long_pnls):+.3%}")
            lines.append(f"- **Long win rate**: {sum(1 for p in long_pnls if p > 0)/len(long_pnls):.0%}")

        if short_trades:
            short_pnls = [t.pnl_on_equity for t in short_trades]
            lines.append(f"- **Short avg PnL**: {np.mean(short_pnls):+.3%}")
            lines.append(f"- **Short win rate**: {sum(1 for p in short_pnls if p > 0)/len(short_pnls):.0%}")

        avg_bars = np.mean([t.bars_held for t in trades_1x])
        median_bars = np.median([t.bars_held for t in trades_1x])
        lines.append(f"- **Avg holding period**: {avg_bars:.0f}h ({avg_bars/24:.1f}d)")
        lines.append(f"- **Median holding period**: {median_bars:.0f}h ({median_bars/24:.1f}d)")

        # Best/worst trades
        sorted_trades = sorted(trades_1x, key=lambda t: t.pnl_on_equity, reverse=True)
        lines.append("")
        lines.append("### Top 10 Trades")
        lines.append("| Token | Entry | Exit | Dir | PnL | Bars | Exit Reason |")
        lines.append("|-------|-------|------|-----|-----|------|-------------|")
        for t in sorted_trades[:10]:
            d = "LONG" if t.direction == 1 else "SHORT"
            lines.append(
                f"| {t.token} "
                f"| {t.entry_time.strftime('%Y-%m-%d')} "
                f"| {t.exit_time.strftime('%Y-%m-%d')} "
                f"| {d} | {t.pnl_on_equity:+.2%} "
                f"| {t.bars_held} "
                f"| {t.exit_reason} |"
            )

        lines.append("")
        lines.append("### Bottom 10 Trades")
        lines.append("| Token | Entry | Exit | Dir | PnL | Bars | Exit Reason |")
        lines.append("|-------|-------|------|-----|-----|------|-------------|")
        for t in sorted_trades[-10:]:
            d = "LONG" if t.direction == 1 else "SHORT"
            lines.append(
                f"| {t.token} "
                f"| {t.entry_time.strftime('%Y-%m-%d')} "
                f"| {t.exit_time.strftime('%Y-%m-%d')} "
                f"| {d} | {t.pnl_on_equity:+.2%} "
                f"| {t.bars_held} "
                f"| {t.exit_reason} |"
            )

    lines.append("")
    lines.append("## Notes")
    lines.append("- All signals use current bar's close (EMA is computed on close prices)")
    lines.append("- EMAs are exponential moving averages (ewm, adjust=False)")
    lines.append("- Volume filter is dynamic: token must pass 30-day trailing avg daily dvol > $5M at the bar")
    lines.append("- Funding: longs pay positive funding, shorts collect")
    lines.append("- No pyramiding: one position per token at a time")
    lines.append("- Partial profit taking reduces position by 50% and tightens trail from 3x to 2x ATR")
    lines.append("- Score flip exit: long closed when score goes to -1 or below; short closed when score goes to +1 or above")
    lines.append(f"- First {EMA_WARMUP} bars skipped for EMA warm-up")
    lines.append("")

    with open(OUTPUT_PATH, 'w') as fh:
        fh.write('\n'.join(lines))
    print(f"  Written to {OUTPUT_PATH}")


if __name__ == '__main__':
    main()
