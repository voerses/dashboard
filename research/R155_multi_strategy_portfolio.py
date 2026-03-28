"""
R155 -- Multi-Strategy Portfolio Combiner with Leverage Optimization

Goal: Combine simple BTC trend strategies with leverage to maximize returns.
Target: 300% return in last 12 months (2025-03-17 to 2026-03-17).
BTC was DOWN 10.8% in that period.

Parts:
  A) BTC Simple Long/Flat Trend: EMA(20d) > EMA(50d) = long, else flat
  B) BTC Long/Short Trend: EMA(20d) > EMA(50d) = long, < = short
  C) BTC + Alt Basket Combo: 50% BTC + 50% alts when bullish, flat otherwise
  D) Kelly-Optimal Leverage for each of A, B, C
  E) Multi-Timeframe Overlay: Fast EMA(8/21) + Slow EMA(20/50)

All signals use PREVIOUS day's close (shift by 1) to avoid look-ahead bias.
Costs: 7 bps per side + hourly funding from parquet.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
import warnings
import time
import os

warnings.filterwarnings('ignore')

# ============================================================
# CONSTANTS
# ============================================================

DATA_DIR = '/workspace/crypto_backtest/data/perp/1h_cache'
OUTPUT_PATH = '/workspace/crypto_backtest/research/R155_multi_strategy_results.md'

TOKENS = ['BTC', 'ETH', 'SOL', 'BNB']
ALT_TOKENS = ['ETH', 'SOL', 'BNB']

LEVERAGE_LEVELS = [1, 2, 3]

# EMA spans (daily bars)
EMA_FAST_SLOW = (20, 50)       # slow signal
EMA_FAST_FAST = (8, 21)        # fast signal for Part E

# Cost
FEE_BPS = 7.0                  # per side

# Daily bars per year
DAYS_PER_YEAR = 365

# Date ranges
LAST_12MO_START = pd.Timestamp('2025-03-17')
LAST_24MO_START = pd.Timestamp('2024-03-17')
DATA_END = pd.Timestamp('2026-03-17')

# Warm-up: skip first N daily bars for EMA convergence
DAILY_WARMUP = 60  # 60 days > max EMA span (50)


# ============================================================
# DATA LOADING
# ============================================================

def load_hourly(token: str) -> pd.DataFrame:
    """Load hourly parquet data."""
    path = os.path.join(DATA_DIR, f'{token}_1h.parquet')
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep='first')]
    return df


def resample_to_daily(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Resample 1h bars to daily OHLCV + daily funding sum."""
    agg = {
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
    }
    if 'funding_1h' in df_1h.columns:
        agg['funding_1h'] = 'sum'

    df_d = df_1h.resample('1D').agg(agg).dropna(subset=['open'])

    if 'funding_1h' in df_d.columns:
        df_d.rename(columns={'funding_1h': 'funding_daily'}, inplace=True)
    else:
        df_d['funding_daily'] = 0.0

    return df_d


def compute_daily_emas(df_d: pd.DataFrame, fast: int, slow: int) -> pd.DataFrame:
    """Add EMA columns to daily dataframe."""
    df_d[f'ema_{fast}'] = df_d['close'].ewm(span=fast, adjust=False).mean()
    df_d[f'ema_{slow}'] = df_d['close'].ewm(span=slow, adjust=False).mean()
    return df_d


def load_all_daily() -> Dict[str, pd.DataFrame]:
    """Load and resample all tokens to daily."""
    data = {}
    for token in TOKENS:
        print(f"  Loading {token}...")
        df_1h = load_hourly(token)
        df_d = resample_to_daily(df_1h)
        # Compute both slow and fast EMAs
        df_d = compute_daily_emas(df_d, *EMA_FAST_SLOW)
        df_d = compute_daily_emas(df_d, *EMA_FAST_FAST)
        data[token] = df_d
        print(f"    {df_d.index[0].date()} to {df_d.index[-1].date()}, {len(df_d)} days")
    return data


# ============================================================
# SIMULATION HELPERS
# ============================================================

def daily_returns(close: np.ndarray) -> np.ndarray:
    """Compute simple daily returns (close-to-close)."""
    ret = np.zeros(len(close))
    ret[1:] = close[1:] / close[:-1] - 1.0
    return ret


def apply_signal_shift(signal: np.ndarray) -> np.ndarray:
    """Shift signal by 1 day for causality (use PREVIOUS day's signal)."""
    shifted = np.zeros_like(signal)
    shifted[1:] = signal[:-1]
    return shifted


def compute_equity_curve(
    positions: np.ndarray,       # position sizing per day (can be -1 to +N for leverage)
    asset_returns: np.ndarray,   # daily returns of the asset
    funding_daily: np.ndarray,   # daily funding cost
    leverage: float,             # leverage multiplier applied to position
    fee_bps: float = 7.0,       # per-side fee
    label: str = "",
) -> Tuple[np.ndarray, Dict]:
    """
    Vectorized equity curve computation.

    positions[t]: fraction of equity allocated. +1 = 100% long, -1 = 100% short, 0 = flat.
    The position is ALREADY shifted (uses yesterday's signal).
    Leverage is applied on top: effective_pos = positions * leverage.

    Returns equity curve and trade stats dict.
    """
    n = len(positions)
    fee_pct = fee_bps / 10_000.0

    # Detect position changes (entries/exits/flips)
    pos_change = np.zeros(n)
    pos_change[1:] = np.abs(positions[1:] - positions[:-1])
    # First bar with a position is also a trade
    pos_change[0] = np.abs(positions[0])

    # Trading cost: fee per side * notional change * leverage
    # When flipping long->short, that's 2 units of change, both sides = 2 * fee * lev
    # The pos_change captures the absolute change in position fraction
    trade_cost = pos_change * fee_pct * leverage

    # Daily PnL = position * leverage * asset_return - funding * |position| * leverage - trade_cost
    daily_pnl = (
        positions * leverage * asset_returns
        - np.abs(positions) * leverage * funding_daily
        - trade_cost
    )

    # Equity curve (multiplicative: equity = product of (1 + daily_pnl))
    equity = np.cumprod(1.0 + daily_pnl)

    # Clamp at zero (can't go below 0)
    # If equity goes to 0 or below, stay at 0
    blown_up = False
    for i in range(len(equity)):
        if equity[i] <= 0:
            equity[i:] = 0.0
            blown_up = True
            break

    # Trade stats
    # A "trade" is a contiguous block with the SAME position sign/size.
    # For L/S strategies (never zero), direction flips count as new trades.
    # We detect trades by finding where the position changes value.
    trade_boundaries = [0]  # start of first segment
    for i in range(1, n):
        if positions[i] != positions[i - 1]:
            trade_boundaries.append(i)
    trade_boundaries.append(n)  # end sentinel

    # Extract trade segments (only non-zero positions count as trades)
    trade_segments = []
    for j in range(len(trade_boundaries) - 1):
        s = trade_boundaries[j]
        e = trade_boundaries[j + 1] - 1  # inclusive end
        if positions[s] != 0:
            trade_segments.append((s, e))

    n_trades = len(trade_segments)

    # Win rate: compute return per trade
    trade_returns = []
    for s, e in trade_segments:
        if s > 0 and equity[s - 1] > 0:
            tr = equity[e] / equity[s - 1] - 1.0
        elif s == 0:
            tr = equity[e] / 1.0 - 1.0
        else:
            tr = 0.0
        trade_returns.append(tr)

    wins = sum(1 for r in trade_returns if r > 0)
    losses = sum(1 for r in trade_returns if r <= 0)
    win_rate = wins / n_trades if n_trades > 0 else 0.0

    gross_profit = sum(r for r in trade_returns if r > 0)
    gross_loss = abs(sum(r for r in trade_returns if r <= 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0.001 else 99.9

    avg_hold = 0
    if n_trades > 0:
        holds = [e - s + 1 for s, e in trade_segments]
        avg_hold = np.mean(holds)

    total_cost = np.sum(trade_cost)
    total_funding = np.sum(np.abs(positions) * leverage * funding_daily)

    stats = {
        'n_trades': n_trades,
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'avg_hold_days': avg_hold,
        'total_cost_pct': total_cost,
        'total_funding_pct': total_funding,
        'blown_up': blown_up,
    }

    return equity, stats


def compute_metrics_from_equity(
    equity: np.ndarray,
    dates: pd.DatetimeIndex,
    stats: Dict,
    start_date: Optional[pd.Timestamp] = None,
) -> Dict:
    """Compute performance metrics from equity curve array."""
    eq_series = pd.Series(equity, index=dates)

    if start_date is not None:
        eq = eq_series.loc[eq_series.index >= start_date]
    else:
        eq = eq_series

    if len(eq) < 2 or eq.iloc[0] <= 0:
        return _empty_metrics()

    total_return = eq.iloc[-1] / eq.iloc[0] - 1.0
    days = (eq.index[-1] - eq.index[0]).total_seconds() / 86400
    if days <= 0:
        return _empty_metrics()

    if total_return <= -1.0:
        annual_return = -1.0
    else:
        annual_return = (1 + total_return) ** (DAYS_PER_YEAR / days) - 1.0

    # Daily returns for Sharpe/Sortino
    daily_rets = eq.pct_change().dropna()
    daily_rets = daily_rets.replace([np.inf, -np.inf], 0.0)

    if len(daily_rets) < 10:
        return _empty_metrics()

    std = daily_rets.std()
    sharpe = (daily_rets.mean() / std) * np.sqrt(DAYS_PER_YEAR) if std > 1e-10 else 0.0

    # Sortino
    downside = daily_rets[daily_rets < 0]
    if len(downside) > 0:
        downside_std = downside.std()
        sortino = (daily_rets.mean() / downside_std) * np.sqrt(DAYS_PER_YEAR) if downside_std > 1e-10 else 0.0
    else:
        sortino = 99.9

    # Max drawdown
    cummax = eq.cummax()
    dd = (eq - cummax) / cummax
    max_dd = dd.min()

    # Calmar
    calmar = annual_return / abs(max_dd) if abs(max_dd) > 0.001 else 0.0

    m = {
        'annual_return': annual_return,
        'total_return': total_return,
        'sharpe': sharpe,
        'sortino': sortino,
        'calmar': calmar,
        'max_dd': max_dd,
    }
    m.update(stats)
    return m


def _empty_metrics() -> Dict:
    return {
        'annual_return': 0.0, 'total_return': 0.0,
        'sharpe': 0.0, 'sortino': 0.0, 'calmar': 0.0, 'max_dd': 0.0,
        'n_trades': 0, 'win_rate': 0.0, 'profit_factor': 0.0,
        'avg_hold_days': 0.0, 'total_cost_pct': 0.0, 'total_funding_pct': 0.0,
        'blown_up': False,
    }


def compute_monthly_returns(equity: np.ndarray, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Compute monthly returns from equity curve."""
    eq_series = pd.Series(equity, index=dates)
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


def kelly_optimal_leverage(daily_returns_when_in: np.ndarray) -> float:
    """
    Compute Kelly-optimal leverage for a strategy.

    Kelly fraction f* = mu / sigma^2
    where mu = mean daily excess return, sigma = daily std.

    For a long/flat strategy: the returns when "in" determine the edge.
    """
    if len(daily_returns_when_in) < 30:
        return 1.0

    mu = np.mean(daily_returns_when_in)
    var = np.var(daily_returns_when_in, ddof=1)

    if var < 1e-12 or mu <= 0:
        return 1.0

    kelly = mu / var
    # Cap at 10x for sanity
    return min(kelly, 10.0)


# ============================================================
# PART A: BTC Simple Long/Flat Trend
# ============================================================

def run_part_a(data: Dict[str, pd.DataFrame]) -> List[Dict]:
    """
    Simple BTC trend: Long when EMA(20) > EMA(50), Flat otherwise.
    Use PREVIOUS day's signal (shifted by 1).
    """
    print("\n" + "=" * 60)
    print("PART A: BTC Simple Long/Flat Trend (EMA 20/50 daily)")
    print("=" * 60)

    df = data['BTC'].copy()
    closes = df['close'].values
    funding = df['funding_daily'].values
    ret = daily_returns(closes)

    # Signal: 1 when EMA(20) > EMA(50), 0 otherwise
    raw_signal = (df['ema_20'].values > df['ema_50'].values).astype(float)
    signal = apply_signal_shift(raw_signal)
    # Zero out warm-up period
    signal[:DAILY_WARMUP] = 0.0

    dates = df.index
    results = []

    for lev in LEVERAGE_LEVELS:
        equity, stats = compute_equity_curve(signal, ret, funding, lev, FEE_BPS, f"A_{lev}x")
        full = compute_metrics_from_equity(equity, dates, stats)
        l12 = compute_metrics_from_equity(equity, dates, stats, LAST_12MO_START)
        monthly = compute_monthly_returns(equity, dates)

        results.append({
            'name': f'A: BTC Long/Flat {lev}x',
            'leverage': lev,
            'full': full,
            'last_12mo': l12,
            'monthly': monthly,
            'equity': equity,
            'dates': dates,
            'signal': signal,
            'asset_returns': ret,
        })

        print(f"  {lev}x | Full: Ann={full['annual_return']:+.1%} "
              f"Sharpe={full['sharpe']:.2f} MaxDD={full['max_dd']:.1%} "
              f"Calmar={full['calmar']:.2f} "
              f"Trades={full['n_trades']} WR={full['win_rate']:.0%} "
              f"PF={full['profit_factor']:.2f}")
        print(f"       L12mo: Ann={l12['annual_return']:+.1%} "
              f"Sharpe={l12['sharpe']:.2f} MaxDD={l12['max_dd']:.1%} "
              f"Total={l12['total_return']:+.1%}")
        print(f"       Costs: trading={full['total_cost_pct']:.2%} "
              f"funding={full['total_funding_pct']:.2%} "
              f"AvgHold={full['avg_hold_days']:.1f}d")

    return results


# ============================================================
# PART B: BTC Long/Short Trend
# ============================================================

def run_part_b(data: Dict[str, pd.DataFrame]) -> List[Dict]:
    """
    BTC trend: Long when EMA(20) > EMA(50), Short when EMA(20) < EMA(50).
    Use PREVIOUS day's signal (shifted by 1).
    """
    print("\n" + "=" * 60)
    print("PART B: BTC Long/Short Trend (EMA 20/50 daily)")
    print("=" * 60)

    df = data['BTC'].copy()
    closes = df['close'].values
    funding = df['funding_daily'].values
    ret = daily_returns(closes)

    # Signal: +1 long, -1 short
    raw_signal = np.where(
        df['ema_20'].values > df['ema_50'].values, 1.0, -1.0
    )
    signal = apply_signal_shift(raw_signal)
    signal[:DAILY_WARMUP] = 0.0

    dates = df.index
    results = []

    for lev in LEVERAGE_LEVELS:
        equity, stats = compute_equity_curve(signal, ret, funding, lev, FEE_BPS, f"B_{lev}x")
        full = compute_metrics_from_equity(equity, dates, stats)
        l12 = compute_metrics_from_equity(equity, dates, stats, LAST_12MO_START)
        monthly = compute_monthly_returns(equity, dates)

        results.append({
            'name': f'B: BTC Long/Short {lev}x',
            'leverage': lev,
            'full': full,
            'last_12mo': l12,
            'monthly': monthly,
            'equity': equity,
            'dates': dates,
            'signal': signal,
            'asset_returns': ret,
        })

        print(f"  {lev}x | Full: Ann={full['annual_return']:+.1%} "
              f"Sharpe={full['sharpe']:.2f} MaxDD={full['max_dd']:.1%} "
              f"Calmar={full['calmar']:.2f} "
              f"Trades={full['n_trades']} WR={full['win_rate']:.0%} "
              f"PF={full['profit_factor']:.2f}")
        print(f"       L12mo: Ann={l12['annual_return']:+.1%} "
              f"Sharpe={l12['sharpe']:.2f} MaxDD={l12['max_dd']:.1%} "
              f"Total={l12['total_return']:+.1%}")
        print(f"       Costs: trading={full['total_cost_pct']:.2%} "
              f"funding={full['total_funding_pct']:.2%} "
              f"AvgHold={full['avg_hold_days']:.1f}d")

    return results


# ============================================================
# PART C: BTC + Alt Basket Combo
# ============================================================

def run_part_c(data: Dict[str, pd.DataFrame]) -> List[Dict]:
    """
    When BTC EMA(20) > EMA(50): 50% BTC + 50% Alt basket (ETH, SOL, BNB equal weight).
    When BTC EMA(20) < EMA(50): Flat everything.
    Alts only traded when they have data.
    """
    print("\n" + "=" * 60)
    print("PART C: BTC + Alt Basket Combo (EMA 20/50 daily)")
    print("=" * 60)

    btc = data['BTC']
    # BTC signal
    raw_signal = (btc['ema_20'].values > btc['ema_50'].values).astype(float)
    btc_signal = apply_signal_shift(raw_signal)
    btc_signal[:DAILY_WARMUP] = 0.0

    # Align all tokens to common dates (intersection)
    common_dates = btc.index.copy()
    for tok in ALT_TOKENS:
        common_dates = common_dates.intersection(data[tok].index)

    print(f"  Common date range: {common_dates[0].date()} to {common_dates[-1].date()} "
          f"({len(common_dates)} days)")

    # Re-extract aligned data
    btc_aligned = btc.loc[common_dates]
    btc_close = btc_aligned['close'].values
    btc_ret = daily_returns(btc_close)
    btc_funding = btc_aligned['funding_daily'].values

    # BTC signal aligned to common dates
    btc_signal_idx = btc.index.get_indexer(common_dates)
    btc_sig_aligned = btc_signal[btc_signal_idx]

    # Alt returns and funding
    alt_returns = []
    alt_fundings = []
    for tok in ALT_TOKENS:
        df_tok = data[tok].loc[common_dates]
        alt_returns.append(daily_returns(df_tok['close'].values))
        alt_fundings.append(df_tok['funding_daily'].values)

    # Equal-weight alt basket return
    alt_basket_ret = np.mean(alt_returns, axis=0)
    alt_basket_funding = np.mean(alt_fundings, axis=0)

    # Combined portfolio: 50% BTC + 50% alt basket when signal is on
    # Position for BTC: signal * 0.5
    # Position for alts: signal * 0.5
    # Combined daily return = pos_btc * btc_ret + pos_alt * alt_ret
    # Funding = pos_btc * btc_funding + pos_alt * alt_funding

    dates = common_dates
    results = []

    for lev in LEVERAGE_LEVELS:
        fee_pct = FEE_BPS / 10_000.0

        # Compute combined PnL day by day
        n = len(dates)
        pos_btc = btc_sig_aligned * 0.5  # 50% to BTC
        pos_alt = btc_sig_aligned * 0.5  # 50% to alt basket

        # Position changes for cost calculation
        pos_btc_change = np.zeros(n)
        pos_btc_change[1:] = np.abs(pos_btc[1:] - pos_btc[:-1])
        pos_btc_change[0] = np.abs(pos_btc[0])

        pos_alt_change = np.zeros(n)
        pos_alt_change[1:] = np.abs(pos_alt[1:] - pos_alt[:-1])
        pos_alt_change[0] = np.abs(pos_alt[0])

        # Cost: each leg pays fee independently
        trade_cost = (pos_btc_change + pos_alt_change) * fee_pct * lev

        # Daily PnL
        daily_pnl = (
            pos_btc * lev * btc_ret
            + pos_alt * lev * alt_basket_ret
            - np.abs(pos_btc) * lev * btc_funding
            - np.abs(pos_alt) * lev * alt_basket_funding
            - trade_cost
        )

        equity = np.cumprod(1.0 + daily_pnl)

        # Check for blow-up
        blown_up = False
        for i in range(len(equity)):
            if equity[i] <= 0:
                equity[i:] = 0.0
                blown_up = True
                break

        # Trade stats (detect direction/size changes as separate trades)
        trade_bounds = [0]
        for ii in range(1, n):
            if btc_sig_aligned[ii] != btc_sig_aligned[ii - 1]:
                trade_bounds.append(ii)
        trade_bounds.append(n)
        trade_segs = []
        for jj in range(len(trade_bounds) - 1):
            ss = trade_bounds[jj]
            ee = trade_bounds[jj + 1] - 1
            if btc_sig_aligned[ss] != 0:
                trade_segs.append((ss, ee))
        n_trades = len(trade_segs)

        trade_returns = []
        for s, e in trade_segs:
            if s > 0 and equity[s - 1] > 0:
                tr = equity[e] / equity[s - 1] - 1.0
            elif s == 0:
                tr = equity[e] / 1.0 - 1.0
            else:
                tr = 0.0
            trade_returns.append(tr)

        wins = sum(1 for r in trade_returns if r > 0)
        win_rate = wins / n_trades if n_trades > 0 else 0.0
        gp = sum(r for r in trade_returns if r > 0)
        gl = abs(sum(r for r in trade_returns if r <= 0))
        pf = gp / gl if gl > 0.001 else 99.9
        avg_hold = np.mean([e - s + 1 for s, e in trade_segs]) if n_trades > 0 else 0

        stats = {
            'n_trades': n_trades,
            'win_rate': win_rate,
            'profit_factor': pf,
            'avg_hold_days': avg_hold,
            'total_cost_pct': np.sum(trade_cost),
            'total_funding_pct': np.sum(
                np.abs(pos_btc) * lev * btc_funding
                + np.abs(pos_alt) * lev * alt_basket_funding
            ),
            'blown_up': blown_up,
        }

        full = compute_metrics_from_equity(equity, dates, stats)
        l12 = compute_metrics_from_equity(equity, dates, stats, LAST_12MO_START)
        monthly = compute_monthly_returns(equity, dates)

        results.append({
            'name': f'C: BTC+Alt Combo {lev}x',
            'leverage': lev,
            'full': full,
            'last_12mo': l12,
            'monthly': monthly,
            'equity': equity,
            'dates': dates,
            'combined_signal': btc_sig_aligned,
            'btc_ret': btc_ret,
            'alt_ret': alt_basket_ret,
        })

        print(f"  {lev}x | Full: Ann={full['annual_return']:+.1%} "
              f"Sharpe={full['sharpe']:.2f} MaxDD={full['max_dd']:.1%} "
              f"Calmar={full['calmar']:.2f} "
              f"Trades={full['n_trades']} WR={full['win_rate']:.0%} "
              f"PF={full['profit_factor']:.2f}")
        print(f"       L12mo: Ann={l12['annual_return']:+.1%} "
              f"Sharpe={l12['sharpe']:.2f} MaxDD={l12['max_dd']:.1%} "
              f"Total={l12['total_return']:+.1%}")
        print(f"       Costs: trading={full['total_cost_pct']:.2%} "
              f"funding={full['total_funding_pct']:.2%} "
              f"AvgHold={full['avg_hold_days']:.1f}d")

    return results


# ============================================================
# PART D: Kelly-Optimal Leverage
# ============================================================

def run_part_d(
    data: Dict[str, pd.DataFrame],
    results_a: List[Dict],
    results_b: List[Dict],
    results_c: List[Dict],
) -> List[Dict]:
    """
    For each strategy (A, B, C at 1x), compute Kelly-optimal leverage,
    then test at full Kelly and half Kelly.
    """
    print("\n" + "=" * 60)
    print("PART D: Kelly-Optimal Leverage")
    print("=" * 60)

    kelly_results = []

    strategies = [
        ('A', results_a[0]),  # 1x versions
        ('B', results_b[0]),
        ('C', results_c[0]),
    ]

    for label, res_1x in strategies:
        # Compute Kelly from the 1x returns
        signal = res_1x.get('signal', res_1x.get('combined_signal'))
        if 'asset_returns' in res_1x:
            # Single asset strategy
            in_position = signal != 0
            strategy_returns = signal * res_1x['asset_returns']
        else:
            # Multi-asset (Part C)
            in_position = signal != 0
            strategy_returns = signal * 0.5 * res_1x['btc_ret'] + signal * 0.5 * res_1x['alt_ret']

        # Use only in-position returns for Kelly
        active_returns = strategy_returns[in_position]
        if len(active_returns) < 30:
            print(f"  {label}: Not enough data for Kelly")
            continue

        full_kelly = kelly_optimal_leverage(active_returns)
        half_kelly = full_kelly / 2.0

        print(f"\n  Strategy {label}:")
        print(f"    Mean daily return (in position): {np.mean(active_returns):.4%}")
        print(f"    Daily vol (in position): {np.std(active_returns, ddof=1):.4%}")
        print(f"    Full Kelly: {full_kelly:.2f}x")
        print(f"    Half Kelly: {half_kelly:.2f}x")

        # Now re-run the strategy at Kelly and half-Kelly
        for kelly_label, kelly_lev in [('Full Kelly', full_kelly), ('Half Kelly', half_kelly)]:
            if label == 'A':
                df = data['BTC']
                raw_signal = (df['ema_20'].values > df['ema_50'].values).astype(float)
                sig = apply_signal_shift(raw_signal)
                sig[:DAILY_WARMUP] = 0.0
                ret = daily_returns(df['close'].values)
                funding = df['funding_daily'].values
                dates = df.index

                equity, stats = compute_equity_curve(sig, ret, funding, kelly_lev, FEE_BPS)
                full_m = compute_metrics_from_equity(equity, dates, stats)
                l12_m = compute_metrics_from_equity(equity, dates, stats, LAST_12MO_START)
                monthly = compute_monthly_returns(equity, dates)

            elif label == 'B':
                df = data['BTC']
                raw_signal = np.where(
                    df['ema_20'].values > df['ema_50'].values, 1.0, -1.0
                )
                sig = apply_signal_shift(raw_signal)
                sig[:DAILY_WARMUP] = 0.0
                ret = daily_returns(df['close'].values)
                funding = df['funding_daily'].values
                dates = df.index

                equity, stats = compute_equity_curve(sig, ret, funding, kelly_lev, FEE_BPS)
                full_m = compute_metrics_from_equity(equity, dates, stats)
                l12_m = compute_metrics_from_equity(equity, dates, stats, LAST_12MO_START)
                monthly = compute_monthly_returns(equity, dates)

            elif label == 'C':
                # Re-run Part C logic at Kelly leverage
                btc = data['BTC']
                raw_signal = (btc['ema_20'].values > btc['ema_50'].values).astype(float)
                btc_signal = apply_signal_shift(raw_signal)
                btc_signal[:DAILY_WARMUP] = 0.0

                common_dates = btc.index.copy()
                for tok in ALT_TOKENS:
                    common_dates = common_dates.intersection(data[tok].index)

                btc_aligned = btc.loc[common_dates]
                btc_close = btc_aligned['close'].values
                btc_ret = daily_returns(btc_close)
                btc_funding = btc_aligned['funding_daily'].values

                btc_signal_idx = btc.index.get_indexer(common_dates)
                btc_sig_aligned = btc_signal[btc_signal_idx]

                alt_rets_list = []
                alt_fund_list = []
                for tok in ALT_TOKENS:
                    df_tok = data[tok].loc[common_dates]
                    alt_rets_list.append(daily_returns(df_tok['close'].values))
                    alt_fund_list.append(df_tok['funding_daily'].values)

                alt_basket_ret = np.mean(alt_rets_list, axis=0)
                alt_basket_funding = np.mean(alt_fund_list, axis=0)

                n = len(common_dates)
                fee_pct = FEE_BPS / 10_000.0
                pos_btc = btc_sig_aligned * 0.5
                pos_alt = btc_sig_aligned * 0.5

                pos_btc_change = np.zeros(n)
                pos_btc_change[1:] = np.abs(pos_btc[1:] - pos_btc[:-1])
                pos_btc_change[0] = np.abs(pos_btc[0])
                pos_alt_change = np.zeros(n)
                pos_alt_change[1:] = np.abs(pos_alt[1:] - pos_alt[:-1])
                pos_alt_change[0] = np.abs(pos_alt[0])

                trade_cost = (pos_btc_change + pos_alt_change) * fee_pct * kelly_lev
                daily_pnl = (
                    pos_btc * kelly_lev * btc_ret
                    + pos_alt * kelly_lev * alt_basket_ret
                    - np.abs(pos_btc) * kelly_lev * btc_funding
                    - np.abs(pos_alt) * kelly_lev * alt_basket_funding
                    - trade_cost
                )
                equity = np.cumprod(1.0 + daily_pnl)
                blown_up = False
                for i in range(len(equity)):
                    if equity[i] <= 0:
                        equity[i:] = 0.0
                        blown_up = True
                        break

                # Trade counting via position-change boundaries
                tb = [0]
                for ii in range(1, n):
                    if btc_sig_aligned[ii] != btc_sig_aligned[ii - 1]:
                        tb.append(ii)
                tb.append(n)
                t_segs = [(tb[jj], tb[jj+1]-1) for jj in range(len(tb)-1) if btc_sig_aligned[tb[jj]] != 0]
                nt = len(t_segs)
                trade_rets = []
                for s, e in t_segs:
                    if s > 0 and equity[s - 1] > 0:
                        trade_rets.append(equity[e] / equity[s - 1] - 1.0)
                    elif s == 0:
                        trade_rets.append(equity[e] / 1.0 - 1.0)
                    else:
                        trade_rets.append(0.0)
                w = sum(1 for r in trade_rets if r > 0)
                gpp = sum(r for r in trade_rets if r > 0)
                gll = abs(sum(r for r in trade_rets if r <= 0))

                stats = {
                    'n_trades': nt,
                    'win_rate': w / nt if nt > 0 else 0.0,
                    'profit_factor': gpp / gll if gll > 0.001 else 99.9,
                    'avg_hold_days': np.mean([e - s + 1 for s, e in t_segs]) if nt > 0 else 0,
                    'total_cost_pct': np.sum(trade_cost),
                    'total_funding_pct': np.sum(
                        np.abs(pos_btc) * kelly_lev * btc_funding
                        + np.abs(pos_alt) * kelly_lev * alt_basket_funding
                    ),
                    'blown_up': blown_up,
                }
                dates = common_dates
                full_m = compute_metrics_from_equity(equity, dates, stats)
                l12_m = compute_metrics_from_equity(equity, dates, stats, LAST_12MO_START)
                monthly = compute_monthly_returns(equity, dates)

            kr = {
                'name': f'D: {label} @ {kelly_label} ({kelly_lev:.2f}x)',
                'leverage': kelly_lev,
                'kelly_type': kelly_label,
                'strategy': label,
                'full': full_m,
                'last_12mo': l12_m,
                'monthly': monthly,
                'equity': equity,
                'dates': dates,
            }
            kelly_results.append(kr)

            print(f"    {kelly_label} ({kelly_lev:.2f}x): "
                  f"Full Ann={full_m['annual_return']:+.1%} "
                  f"Sharpe={full_m['sharpe']:.2f} MaxDD={full_m['max_dd']:.1%} "
                  f"{'BLOWN UP!' if full_m.get('blown_up', False) else ''}")
            print(f"      L12mo: Ann={l12_m['annual_return']:+.1%} "
                  f"Total={l12_m['total_return']:+.1%} "
                  f"MaxDD={l12_m['max_dd']:.1%}")

    return kelly_results


# ============================================================
# PART E: Multi-Timeframe Overlay
# ============================================================

def run_part_e(data: Dict[str, pd.DataFrame]) -> List[Dict]:
    """
    Multi-timeframe overlay:
    - Fast signal: EMA(8) > EMA(21) on daily
    - Slow signal: EMA(20) > EMA(50) on daily

    Position sizing:
    - BOTH bullish: 100% long
    - Fast bearish, slow bullish: 50% long (pullback in uptrend)
    - Slow bearish: flat (or short)

    Variants:
    E1) Long/Flat: slow bearish = flat
    E2) Long/Short: slow bearish = short
    """
    print("\n" + "=" * 60)
    print("PART E: Multi-Timeframe Overlay (EMA 8/21 + EMA 20/50 daily)")
    print("=" * 60)

    df = data['BTC'].copy()
    closes = df['close'].values
    funding = df['funding_daily'].values
    ret = daily_returns(closes)
    dates = df.index

    # Signals
    fast_bull = df['ema_8'].values > df['ema_21'].values  # fast signal
    slow_bull = df['ema_20'].values > df['ema_50'].values  # slow signal

    results = []

    # E1: Long/Flat variant
    print("\n  E1: Multi-TF Long/Flat")
    raw_signal_e1 = np.where(
        slow_bull & fast_bull, 1.0,           # both bullish: 100% long
        np.where(slow_bull & ~fast_bull, 0.5,  # slow bull, fast bear: 50% long
                 0.0)                           # slow bearish: flat
    )
    signal_e1 = apply_signal_shift(raw_signal_e1)
    signal_e1[:DAILY_WARMUP] = 0.0

    for lev in LEVERAGE_LEVELS:
        equity, stats = compute_equity_curve(signal_e1, ret, funding, lev, FEE_BPS, f"E1_{lev}x")
        full = compute_metrics_from_equity(equity, dates, stats)
        l12 = compute_metrics_from_equity(equity, dates, stats, LAST_12MO_START)
        monthly = compute_monthly_returns(equity, dates)

        results.append({
            'name': f'E1: Multi-TF Long/Flat {lev}x',
            'leverage': lev,
            'full': full,
            'last_12mo': l12,
            'monthly': monthly,
            'equity': equity,
            'dates': dates,
            'signal': signal_e1,
            'asset_returns': ret,
        })

        print(f"    {lev}x | Full: Ann={full['annual_return']:+.1%} "
              f"Sharpe={full['sharpe']:.2f} MaxDD={full['max_dd']:.1%} "
              f"Calmar={full['calmar']:.2f} "
              f"Trades={full['n_trades']} WR={full['win_rate']:.0%}")
        print(f"         L12mo: Ann={l12['annual_return']:+.1%} "
              f"Total={l12['total_return']:+.1%} "
              f"MaxDD={l12['max_dd']:.1%}")

    # E2: Long/Short variant
    print("\n  E2: Multi-TF Long/Short")
    raw_signal_e2 = np.where(
        slow_bull & fast_bull, 1.0,            # both bullish: 100% long
        np.where(slow_bull & ~fast_bull, 0.5,   # slow bull, fast bear: 50% long
                 -1.0)                           # slow bearish: 100% short
    )
    signal_e2 = apply_signal_shift(raw_signal_e2)
    signal_e2[:DAILY_WARMUP] = 0.0

    for lev in LEVERAGE_LEVELS:
        equity, stats = compute_equity_curve(signal_e2, ret, funding, lev, FEE_BPS, f"E2_{lev}x")
        full = compute_metrics_from_equity(equity, dates, stats)
        l12 = compute_metrics_from_equity(equity, dates, stats, LAST_12MO_START)
        monthly = compute_monthly_returns(equity, dates)

        results.append({
            'name': f'E2: Multi-TF Long/Short {lev}x',
            'leverage': lev,
            'full': full,
            'last_12mo': l12,
            'monthly': monthly,
            'equity': equity,
            'dates': dates,
            'signal': signal_e2,
            'asset_returns': ret,
        })

        print(f"    {lev}x | Full: Ann={full['annual_return']:+.1%} "
              f"Sharpe={full['sharpe']:.2f} MaxDD={full['max_dd']:.1%} "
              f"Calmar={full['calmar']:.2f} "
              f"Trades={full['n_trades']} WR={full['win_rate']:.0%}")
        print(f"         L12mo: Ann={l12['annual_return']:+.1%} "
              f"Total={l12['total_return']:+.1%} "
              f"MaxDD={l12['max_dd']:.1%}")

    # E1 Kelly
    print("\n  E1 Kelly Analysis:")
    sig_1x = signal_e1
    in_pos = sig_1x != 0
    strat_ret = sig_1x * ret
    active_ret = strat_ret[in_pos]
    if len(active_ret) > 30:
        fk = kelly_optimal_leverage(active_ret)
        hk = fk / 2.0
        print(f"    Full Kelly: {fk:.2f}x, Half Kelly: {hk:.2f}x")

        for kl_label, kl_lev in [('Full Kelly', fk), ('Half Kelly', hk)]:
            equity, stats = compute_equity_curve(signal_e1, ret, funding, kl_lev, FEE_BPS)
            full = compute_metrics_from_equity(equity, dates, stats)
            l12 = compute_metrics_from_equity(equity, dates, stats, LAST_12MO_START)
            monthly = compute_monthly_returns(equity, dates)

            results.append({
                'name': f'E1: Multi-TF L/F @ {kl_label} ({kl_lev:.2f}x)',
                'leverage': kl_lev,
                'full': full,
                'last_12mo': l12,
                'monthly': monthly,
                'equity': equity,
                'dates': dates,
            })

            print(f"    {kl_label} ({kl_lev:.2f}x): "
                  f"Full Ann={full['annual_return']:+.1%} "
                  f"Sharpe={full['sharpe']:.2f} MaxDD={full['max_dd']:.1%}")
            print(f"      L12mo: Ann={l12['annual_return']:+.1%} "
                  f"Total={l12['total_return']:+.1%}")

    return results


# ============================================================
# BTC BUY & HOLD BENCHMARK
# ============================================================

def run_benchmark(data: Dict[str, pd.DataFrame]) -> Dict:
    """BTC buy & hold benchmark for comparison."""
    print("\n" + "=" * 60)
    print("BENCHMARK: BTC Buy & Hold")
    print("=" * 60)

    df = data['BTC']
    closes = df['close'].values
    ret = daily_returns(closes)
    funding = df['funding_daily'].values
    dates = df.index

    # Buy and hold = always long 1x
    signal = np.ones(len(dates))
    signal[:DAILY_WARMUP] = 0.0

    equity, stats = compute_equity_curve(signal, ret, funding, 1.0, FEE_BPS)
    full = compute_metrics_from_equity(equity, dates, stats)
    l12 = compute_metrics_from_equity(equity, dates, stats, LAST_12MO_START)
    monthly = compute_monthly_returns(equity, dates)

    result = {
        'name': 'BTC Buy & Hold 1x',
        'leverage': 1,
        'full': full,
        'last_12mo': l12,
        'monthly': monthly,
        'equity': equity,
        'dates': dates,
    }

    print(f"  Full: Ann={full['annual_return']:+.1%} "
          f"Sharpe={full['sharpe']:.2f} MaxDD={full['max_dd']:.1%}")
    print(f"  L12mo: Ann={l12['annual_return']:+.1%} "
          f"Total={l12['total_return']:+.1%} "
          f"MaxDD={l12['max_dd']:.1%}")

    return result


# ============================================================
# RESULTS WRITER
# ============================================================

def write_results(
    benchmark: Dict,
    results_a: List[Dict],
    results_b: List[Dict],
    results_c: List[Dict],
    results_d: List[Dict],
    results_e: List[Dict],
):
    """Write comprehensive results markdown."""
    lines = []

    lines.append("# R155 -- Multi-Strategy Portfolio Combiner with Leverage Optimization")
    lines.append("")
    lines.append("## Context")
    lines.append("- Target: 300% return in last 12 months (2025-03-17 to 2026-03-17)")
    lines.append("- BTC was DOWN ~10.8% in that period")
    lines.append("- Individual strategies showed near-zero 12-month returns")
    lines.append("- This research tests whether combining strategies with leverage can bridge the gap")
    lines.append("")
    lines.append("## Methodology")
    lines.append("- All signals computed on DAILY bars (1h aggregated to 1D)")
    lines.append("- Signals use PREVIOUS day's values (shift by 1) -- no look-ahead bias")
    lines.append("- Costs: 7 bps per side on entries/exits + daily funding from perp data")
    lines.append("- EMA warm-up: 60 days skipped")
    lines.append(f"- Data range: 2020-01-01 to 2026-03-17")
    lines.append(f"- Last 12mo window: 2025-03-17 to 2026-03-17")
    lines.append("")

    # ---- BENCHMARK ----
    lines.append("---")
    lines.append("## Benchmark: BTC Buy & Hold")
    lines.append("")
    _write_metrics_table(lines, [benchmark])
    lines.append("")

    # ---- PART A ----
    lines.append("---")
    lines.append("## Part A: BTC Simple Long/Flat Trend (EMA 20/50 daily)")
    lines.append("")
    lines.append("**Signal:** Long BTC when EMA(20) > EMA(50), Flat otherwise.")
    lines.append("")
    _write_metrics_table(lines, results_a)
    lines.append("")
    _write_monthly_table(lines, results_a, last_n_months=24)
    lines.append("")

    # ---- PART B ----
    lines.append("---")
    lines.append("## Part B: BTC Long/Short Trend (EMA 20/50 daily)")
    lines.append("")
    lines.append("**Signal:** Long when EMA(20) > EMA(50), Short when EMA(20) < EMA(50).")
    lines.append("")
    _write_metrics_table(lines, results_b)
    lines.append("")
    _write_monthly_table(lines, results_b, last_n_months=24)
    lines.append("")

    # ---- PART C ----
    lines.append("---")
    lines.append("## Part C: BTC + Alt Basket Combo")
    lines.append("")
    lines.append("**Signal:** When BTC EMA(20) > EMA(50): 50% BTC + 50% alts (ETH/SOL/BNB equal weight). Else flat.")
    lines.append("")
    _write_metrics_table(lines, results_c)
    lines.append("")
    _write_monthly_table(lines, results_c, last_n_months=24)
    lines.append("")

    # ---- PART D ----
    lines.append("---")
    lines.append("## Part D: Kelly-Optimal Leverage")
    lines.append("")
    lines.append("Kelly criterion: f* = mu / sigma^2 (expected return / variance of return).")
    lines.append("Half-Kelly is the practical recommendation (accounts for estimation error).")
    lines.append("")
    _write_metrics_table(lines, results_d)
    lines.append("")

    # ---- PART E ----
    lines.append("---")
    lines.append("## Part E: Multi-Timeframe Overlay")
    lines.append("")
    lines.append("**Signal:**")
    lines.append("- Fast: EMA(8) > EMA(21) daily")
    lines.append("- Slow: EMA(20) > EMA(50) daily")
    lines.append("- Both bullish: 100% long")
    lines.append("- Fast bearish, slow bullish: 50% long (pullback in uptrend)")
    lines.append("- E1: Slow bearish = flat | E2: Slow bearish = 100% short")
    lines.append("")
    _write_metrics_table(lines, results_e)
    lines.append("")
    _write_monthly_table(lines, results_e[:6], last_n_months=24)  # first 6 = E1/E2 at 1x/2x/3x
    lines.append("")

    # ---- SUMMARY ----
    lines.append("---")
    lines.append("## Summary: Best Variants by Last 12mo Return")
    lines.append("")

    all_results = [benchmark] + results_a + results_b + results_c + results_d + results_e
    sorted_by_l12 = sorted(all_results, key=lambda r: r['last_12mo'].get('total_return', 0), reverse=True)

    lines.append("| Rank | Strategy | Leverage | L12mo Return | L12mo Sharpe | L12mo MaxDD | Full Ann | Full Sharpe | Full MaxDD |")
    lines.append("|------|----------|----------|-------------|-------------|------------|----------|-------------|------------|")
    for i, r in enumerate(sorted_by_l12[:20], 1):
        l12 = r['last_12mo']
        full = r['full']
        blown = " BLOWN" if full.get('blown_up', False) or l12.get('blown_up', False) else ""
        lines.append(
            f"| {i} | {r['name']} | {r['leverage']:.2f}x | "
            f"{l12.get('total_return', 0):+.1%}{blown} | {l12.get('sharpe', 0):.2f} | "
            f"{l12.get('max_dd', 0):.1%} | {full.get('annual_return', 0):+.1%} | "
            f"{full.get('sharpe', 0):.2f} | {full.get('max_dd', 0):.1%} |"
        )
    lines.append("")

    # ---- CONCLUSION ----
    lines.append("---")
    lines.append("## Conclusion")
    lines.append("")

    best = sorted_by_l12[0]
    best_l12_ret = best['last_12mo'].get('total_return', 0)
    lines.append(f"**Best last-12mo return: {best['name']} at {best_l12_ret:+.1%}**")
    lines.append("")
    if best_l12_ret >= 3.0:
        lines.append("Target of 300% achieved!")
    elif best_l12_ret >= 1.0:
        lines.append(f"Achieved {best_l12_ret:+.0%} -- significant improvement over BTC buy-and-hold "
                     f"but short of the 300% target.")
    else:
        lines.append(f"Even with leverage and strategy combinations, the best result was {best_l12_ret:+.1%}.")
        lines.append("The 300% target appears unreachable with simple EMA trend-following on BTC/alts")
        lines.append("in a period where BTC was down 10.8%. Possible next steps:")
        lines.append("- More aggressive mean-reversion strategies")
        lines.append("- Options-based strategies (gamma farming)")
        lines.append("- Cross-exchange arbitrage")
        lines.append("- Higher-frequency (intraday) signals")
    lines.append("")

    # ---- RISK WARNINGS ----
    lines.append("## Risk Warnings")
    lines.append("")
    lines.append("- Leveraged strategies can lose more than the initial capital")
    lines.append("- Kelly-optimal leverage assumes known distribution parameters -- estimation error is real")
    lines.append("- Funding costs compound at higher leverage")
    lines.append("- Alt basket has survivorship bias (we chose tokens that still exist)")
    lines.append("- All results are backtested -- live performance will differ due to slippage, latency, liquidity")
    lines.append("")

    with open(OUTPUT_PATH, 'w') as f:
        f.write('\n'.join(lines))

    print(f"\nResults written to {OUTPUT_PATH}")


def _write_metrics_table(lines: List[str], results: List[Dict]):
    """Write a metrics comparison table."""
    lines.append("| Strategy | Ann Return | Sharpe | Sortino | MaxDD | Calmar | PF | Trades | WR | AvgHold | L12mo Return | L12mo Sharpe | L12mo MaxDD |")
    lines.append("|----------|-----------|--------|---------|-------|--------|-----|--------|-----|---------|-------------|-------------|------------|")
    for r in results:
        f = r['full']
        l = r['last_12mo']
        blown_f = " BLOWN" if f.get('blown_up', False) else ""
        blown_l = " BLOWN" if l.get('blown_up', False) else ""
        lines.append(
            f"| {r['name']} | {f.get('annual_return', 0):+.1%}{blown_f} | "
            f"{f.get('sharpe', 0):.2f} | {f.get('sortino', 0):.2f} | "
            f"{f.get('max_dd', 0):.1%} | {f.get('calmar', 0):.2f} | "
            f"{f.get('profit_factor', 0):.2f} | {f.get('n_trades', 0)} | "
            f"{f.get('win_rate', 0):.0%} | {f.get('avg_hold_days', 0):.1f}d | "
            f"{l.get('total_return', 0):+.1%}{blown_l} | {l.get('sharpe', 0):.2f} | "
            f"{l.get('max_dd', 0):.1%} |"
        )


def _write_monthly_table(lines: List[str], results: List[Dict], last_n_months: int = 24):
    """Write monthly returns for the last N months."""
    lines.append(f"### Monthly Returns (Last {last_n_months} Months)")
    lines.append("")

    for r in results:
        if 'monthly' not in r:
            continue
        monthly = r['monthly']
        if monthly is None or monthly.empty:
            continue

        lines.append(f"**{r['name']}**")
        lines.append("")

        # Get last 24 months
        # Filter to years >= 2024
        recent = monthly.loc[monthly.index >= 2024]
        if recent.empty:
            lines.append("_No data for recent period._")
            lines.append("")
            continue

        # Header
        cols = recent.columns.tolist()
        lines.append("| Year | " + " | ".join(cols) + " |")
        lines.append("|------|" + "|".join(["------"] * len(cols)) + "|")

        for year in recent.index:
            row = recent.loc[year]
            cells = []
            for c in cols:
                v = row.get(c, np.nan)
                if pd.isna(v):
                    cells.append("--")
                else:
                    cells.append(f"{v:+.1%}")
            lines.append(f"| {year} | " + " | ".join(cells) + " |")

        lines.append("")


# ============================================================
# MAIN
# ============================================================

def main():
    t_start = time.time()
    print("=" * 70)
    print("R155 -- Multi-Strategy Portfolio Combiner with Leverage Optimization")
    print("=" * 70)
    print(f"\nTarget: 300% return in last 12 months (2025-03-17 to 2026-03-17)")
    print(f"Tokens: {TOKENS}")
    print(f"Leverage levels: {LEVERAGE_LEVELS}")
    print(f"Costs: {FEE_BPS} bps/side + funding")
    print(f"EMA spans: Slow={EMA_FAST_SLOW}, Fast={EMA_FAST_FAST}")
    print()

    # Load all data
    print("--- Loading Data ---")
    data = load_all_daily()
    print()

    # Run benchmark
    benchmark = run_benchmark(data)

    # Run all parts
    results_a = run_part_a(data)
    results_b = run_part_b(data)
    results_c = run_part_c(data)
    results_d = run_part_d(data, results_a, results_b, results_c)
    results_e = run_part_e(data)

    # Write results
    print("\n" + "=" * 60)
    print("WRITING RESULTS")
    print("=" * 60)
    write_results(benchmark, results_a, results_b, results_c, results_d, results_e)

    total_time = time.time() - t_start
    print(f"\nTotal runtime: {total_time:.1f}s")
    print("Done.")


if __name__ == '__main__':
    main()
