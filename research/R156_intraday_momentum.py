#!/workspace/venv/bin/python
"""
R156: Intraday Momentum on BTC Perps — Three Variants
======================================================

Hypothesis: BTC shows intraday momentum — when the first N hours of
the day are positive, the rest of the day tends to follow. Combined
with leveraged execution on perps, this could generate high-turnover returns.

Variants:
  A) Opening Range Breakout (4h range, breakout -> hold till EOD)
  B) 4-Hour Momentum (6 decision points/day, trend-follow)
  C) 8-Hour Session Momentum (3 sessions/day, trend-follow)

Each tested at 1x, 2x, 3x leverage with 7bps/side costs and hourly funding.

Data: BTC perpetual swap 1h OHLCV + funding from parquet.

Author: Quant Research Agent
Date: 2026-03-28
"""

import warnings
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

warnings.filterwarnings('ignore')

# ── Paths ──────────────────────────────────────────────────────────────────────
PROJECT_DIR = Path('/workspace/crypto_backtest')
DATA_FILE = PROJECT_DIR / 'data' / 'perp' / '1h_cache' / 'BTC_1h.parquet'
OUTPUT_MD = PROJECT_DIR / 'research' / 'R156_intraday_momentum_results.md'

# ── Config ─────────────────────────────────────────────────────────────────────
COST_BPS_PER_SIDE = 7          # 7 bps per side
COST_PER_SIDE = COST_BPS_PER_SIDE / 10000.0
LEVERAGES = [1, 2, 3]

# Last 12 months window
L12M_START = pd.Timestamp('2025-03-17')
L12M_END = pd.Timestamp('2026-03-17')

# Monthly returns window (last 24 months)
MONTHLY_START = pd.Timestamp('2024-03-17')

HOURS_PER_YEAR = 8760
ANNUAL_FACTOR = np.sqrt(HOURS_PER_YEAR)


# ══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════════════════════════════

def load_data():
    """Load BTC perp 1h data."""
    print("[DATA] Loading BTC perp 1h data...")
    df = pd.read_parquet(DATA_FILE)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep='first')]
    print(f"  Range: {df.index.min()} to {df.index.max()}, {len(df)} bars")
    return df


# ══════════════════════════════════════════════════════════════════════════════
# VARIANT A: Opening Range Breakout
# ══════════════════════════════════════════════════════════════════════════════

def variant_a_signals(df):
    """
    Opening Range Breakout:
    - At hour 0 of each UTC day, compute HIGH and LOW of first 4 hours (00-03 inclusive).
    - If price breaks ABOVE 4h high at any subsequent hour: go LONG until 23:00.
    - If price breaks BELOW 4h low at any subsequent hour: go SHORT until 23:00.
    - If neither: FLAT.
    - Force close at 23:00 UTC.
    """
    print("[VARIANT A] Computing Opening Range Breakout signals...")

    positions = pd.Series(0.0, index=df.index)

    # Group by date
    dates = df.index.normalize().unique()

    trade_count = 0

    for date in dates:
        # Get all bars for this day
        day_mask = df.index.normalize() == date
        day_df = df[day_mask]

        if len(day_df) < 5:
            continue  # Need at least the opening range + 1 bar

        # Opening range: hours 0-3 (first 4 bars)
        opening_bars = day_df[day_df.index.hour < 4]
        if len(opening_bars) < 4:
            continue

        range_high = opening_bars['high'].max()
        range_low = opening_bars['low'].min()

        # Trading bars: hours 4-22 (close all at 23:00 means hold through 22:00 bar)
        trading_bars = day_df[(day_df.index.hour >= 4) & (day_df.index.hour <= 22)]

        if len(trading_bars) == 0:
            continue

        # Determine breakout direction (first breakout wins)
        direction = 0
        breakout_hour_idx = None

        for i, (ts, row) in enumerate(trading_bars.iterrows()):
            if row['high'] > range_high and direction == 0:
                direction = 1  # Long
                breakout_hour_idx = i
                break
            elif row['low'] < range_low and direction == 0:
                direction = -1  # Short
                breakout_hour_idx = i
                break

        if direction != 0 and breakout_hour_idx is not None:
            # Position from breakout bar through hour 22
            fill_bars = trading_bars.index[breakout_hour_idx:]
            positions.loc[fill_bars] = direction
            trade_count += 1

    print(f"  Total trade days: {trade_count}")
    return positions


# ══════════════════════════════════════════════════════════════════════════════
# VARIANT B: 4-Hour Momentum
# ══════════════════════════════════════════════════════════════════════════════

def variant_b_signals(df):
    """
    4-Hour Momentum:
    - Every 4 hours (00, 04, 08, 12, 16, 20), compute the return of the last 4 hours.
    - If 4h return > 0: go LONG for the next 4 hours.
    - If 4h return < 0: go SHORT for the next 4 hours.
    - Position: 100% equity. Max 6 trades/day.
    """
    print("[VARIANT B] Computing 4-Hour Momentum signals...")

    positions = pd.Series(0.0, index=df.index)
    decision_hours = [0, 4, 8, 12, 16, 20]

    trade_count = 0

    for i in range(len(df)):
        ts = df.index[i]
        if ts.hour in decision_hours:
            # Look back 4 hours
            if i < 4:
                continue
            ret_4h = (df['close'].iloc[i] / df['close'].iloc[i - 4]) - 1.0

            direction = 1 if ret_4h > 0 else -1
            trade_count += 1

            # Apply position for the next 4 hours (indices i to i+3)
            end_idx = min(i + 4, len(df))
            positions.iloc[i:end_idx] = direction

    print(f"  Total trades: {trade_count}")
    return positions


# ══════════════════════════════════════════════════════════════════════════════
# VARIANT C: 8-Hour Session Momentum
# ══════════════════════════════════════════════════════════════════════════════

def variant_c_signals(df):
    """
    8-Hour Session Momentum:
    - Every 8 hours (00, 08, 16 UTC), look at previous 8h session return.
    - If return > 0: go long for next 8h session.
    - If return < 0: go short for next 8h session.
    - Max 3 trades/day.
    """
    print("[VARIANT C] Computing 8-Hour Session Momentum signals...")

    positions = pd.Series(0.0, index=df.index)
    decision_hours = [0, 8, 16]

    trade_count = 0

    for i in range(len(df)):
        ts = df.index[i]
        if ts.hour in decision_hours:
            if i < 8:
                continue
            ret_8h = (df['close'].iloc[i] / df['close'].iloc[i - 8]) - 1.0

            direction = 1 if ret_8h > 0 else -1
            trade_count += 1

            # Apply position for the next 8 hours (indices i to i+7)
            end_idx = min(i + 8, len(df))
            positions.iloc[i:end_idx] = direction

    print(f"  Total trades: {trade_count}")
    return positions


# ══════════════════════════════════════════════════════════════════════════════
# BACKTEST ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def run_backtest(df, positions, leverage=1):
    """
    Given hourly positions (-1, 0, +1) and leverage, compute equity curve.

    Returns dict with:
      - equity: pd.Series of equity curve
      - gross_equity: pd.Series of equity curve before costs
      - stats: dict of performance metrics
      - trades: list of trade records
    """
    close = df['close']
    funding_1h = df['funding_1h']

    # Hourly return on the underlying
    ret_1h = close.pct_change().fillna(0)

    # Position changes -> trades -> costs
    pos_change = positions.diff().fillna(positions.iloc[0])
    trade_indicator = (pos_change != 0).astype(float)

    # Cost per trade: 7bps per side. A full position change (e.g. 0 -> 1 or 1 -> -1)
    # costs the notional traded. Going from +1 to -1 is 2x notional.
    turnover = pos_change.abs()  # fraction of equity traded
    cost_per_bar = turnover * COST_PER_SIDE  # cost as fraction of equity

    # Gross hourly PnL (before costs and funding)
    gross_pnl = positions.shift(1).fillna(0) * ret_1h * leverage

    # Funding cost: if long, pay funding_1h; if short, receive funding_1h
    # funding_1h is the rate the long pays. Positive = longs pay shorts.
    funding_pnl = -positions.shift(1).fillna(0) * funding_1h * leverage

    # Net PnL
    net_pnl = gross_pnl + funding_pnl - cost_per_bar * leverage

    # Equity curves
    gross_equity = (1 + gross_pnl + funding_pnl).cumprod()
    net_equity = (1 + net_pnl).cumprod()

    # Extract trades
    trades = extract_trades(df, positions, leverage)

    # Compute stats
    stats = compute_stats(net_equity, gross_equity, net_pnl, gross_pnl, funding_pnl,
                          cost_per_bar * leverage, trades, leverage)

    return {
        'equity': net_equity,
        'gross_equity': gross_equity,
        'net_pnl': net_pnl,
        'gross_pnl': gross_pnl,
        'stats': stats,
        'trades': trades,
        'positions': positions,
    }


def extract_trades(df, positions, leverage):
    """Extract individual trade records from position series."""
    trades = []
    in_trade = False
    entry_time = None
    entry_price = None
    trade_dir = 0

    for i in range(1, len(positions)):
        prev_pos = positions.iloc[i - 1]
        curr_pos = positions.iloc[i]

        if prev_pos == 0 and curr_pos != 0:
            # Entry
            in_trade = True
            entry_time = df.index[i]
            entry_price = df['close'].iloc[i]
            trade_dir = int(np.sign(curr_pos))

        elif prev_pos != 0 and curr_pos == 0:
            # Exit
            if in_trade and entry_price is not None:
                exit_time = df.index[i]
                exit_price = df['close'].iloc[i]
                ret = trade_dir * (exit_price / entry_price - 1) * leverage
                # Subtract round-trip cost
                cost = 2 * COST_PER_SIDE * leverage
                net_ret = ret - cost
                trades.append({
                    'entry_time': entry_time,
                    'exit_time': exit_time,
                    'direction': trade_dir,
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'gross_return': ret,
                    'cost': cost,
                    'net_return': net_ret,
                    'hold_hours': (exit_time - entry_time).total_seconds() / 3600,
                })
            in_trade = False
            entry_time = None
            entry_price = None
            trade_dir = 0

        elif prev_pos != 0 and curr_pos != 0 and np.sign(prev_pos) != np.sign(curr_pos):
            # Flip: close old trade, open new
            if in_trade and entry_price is not None:
                exit_time = df.index[i]
                exit_price = df['close'].iloc[i]
                ret = trade_dir * (exit_price / entry_price - 1) * leverage
                cost = 2 * COST_PER_SIDE * leverage
                net_ret = ret - cost
                trades.append({
                    'entry_time': entry_time,
                    'exit_time': exit_time,
                    'direction': trade_dir,
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'gross_return': ret,
                    'cost': cost,
                    'net_return': net_ret,
                    'hold_hours': (exit_time - entry_time).total_seconds() / 3600,
                })

            # New trade
            entry_time = df.index[i]
            entry_price = df['close'].iloc[i]
            trade_dir = int(np.sign(curr_pos))
            in_trade = True

    return trades


def compute_stats(equity, gross_equity, net_pnl, gross_pnl, funding_pnl,
                  cost_series, trades, leverage):
    """Compute comprehensive performance statistics."""
    # Time in market
    n_hours = len(equity)
    n_years = n_hours / HOURS_PER_YEAR

    # Returns
    total_return = equity.iloc[-1] / equity.iloc[0] - 1
    annual_return = (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 else 0

    gross_total = gross_equity.iloc[-1] / gross_equity.iloc[0] - 1
    gross_annual = (1 + gross_total) ** (1 / n_years) - 1 if n_years > 0 else 0

    # Volatility and Sharpe
    hourly_vol = net_pnl.std()
    annual_vol = hourly_vol * ANNUAL_FACTOR
    sharpe = annual_return / annual_vol if annual_vol > 0 else 0

    # Sortino
    downside = net_pnl[net_pnl < 0].std()
    downside_annual = downside * ANNUAL_FACTOR if downside > 0 else 0.001
    sortino = annual_return / downside_annual

    # Drawdown
    cummax = equity.cummax()
    drawdown = (equity - cummax) / cummax
    max_dd = drawdown.min()

    # Calmar
    calmar = annual_return / abs(max_dd) if max_dd != 0 else 0

    # Cost analysis
    total_cost_drag = cost_series.sum()
    total_funding = funding_pnl.sum()

    # Trade stats
    n_trades = len(trades)
    trades_per_year = n_trades / n_years if n_years > 0 else 0

    if n_trades > 0:
        trade_rets = [t['net_return'] for t in trades]
        gross_rets = [t['gross_return'] for t in trades]
        winners = [r for r in trade_rets if r > 0]
        losers = [r for r in trade_rets if r <= 0]

        win_rate = len(winners) / n_trades
        avg_trade = np.mean(trade_rets)
        avg_winner = np.mean(winners) if winners else 0
        avg_loser = np.mean(losers) if losers else 0

        gross_profit = sum(r for r in trade_rets if r > 0)
        gross_loss = abs(sum(r for r in trade_rets if r <= 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    else:
        win_rate = 0
        avg_trade = 0
        avg_winner = 0
        avg_loser = 0
        profit_factor = 0
        trades_per_year = 0

    return {
        'annual_return': annual_return,
        'annual_return_pct': annual_return * 100,
        'gross_annual_return': gross_annual,
        'gross_annual_return_pct': gross_annual * 100,
        'max_dd': max_dd,
        'max_dd_pct': max_dd * 100,
        'sharpe': sharpe,
        'sortino': sortino,
        'calmar': calmar,
        'annual_vol': annual_vol,
        'annual_vol_pct': annual_vol * 100,
        'n_trades': n_trades,
        'trades_per_year': trades_per_year,
        'win_rate': win_rate,
        'win_rate_pct': win_rate * 100,
        'avg_trade_pct': avg_trade * 100,
        'avg_winner_pct': avg_winner * 100,
        'avg_loser_pct': avg_loser * 100,
        'profit_factor': profit_factor,
        'total_cost_drag_pct': total_cost_drag * 100,
        'total_funding_pct': total_funding * 100,
        'leverage': leverage,
        'n_years': n_years,
        'total_return_pct': total_return * 100,
    }


def compute_last_12m_stats(df, positions, leverage):
    """Compute stats for just the last 12 months."""
    mask = (df.index >= L12M_START) & (df.index <= L12M_END)
    if mask.sum() < 100:
        return None
    return run_backtest(df[mask], positions[mask], leverage)


def compute_monthly_returns(equity, start_date=None):
    """Compute monthly return table."""
    if start_date is not None:
        equity = equity[equity.index >= start_date]

    monthly = equity.resample('ME').last()
    monthly_ret = monthly.pct_change().dropna()
    return monthly_ret


# ══════════════════════════════════════════════════════════════════════════════
# REPORTING
# ══════════════════════════════════════════════════════════════════════════════

def format_stats_table(results_dict):
    """Format a comparison table across leverages for one variant."""
    lines = []
    lines.append("| Metric | 1x | 2x | 3x |")
    lines.append("|--------|---:|---:|---:|")

    metrics = [
        ('Annual Return', 'annual_return_pct', '{:+.1f}%'),
        ('Gross Annual Return', 'gross_annual_return_pct', '{:+.1f}%'),
        ('Max Drawdown', 'max_dd_pct', '{:.1f}%'),
        ('Sharpe', 'sharpe', '{:.2f}'),
        ('Calmar', 'calmar', '{:.2f}'),
        ('Sortino', 'sortino', '{:.2f}'),
        ('Annual Vol', 'annual_vol_pct', '{:.1f}%'),
        ('Trades/Year', 'trades_per_year', '{:.0f}'),
        ('Win Rate', 'win_rate_pct', '{:.1f}%'),
        ('Avg Trade', 'avg_trade_pct', '{:+.3f}%'),
        ('Avg Winner', 'avg_winner_pct', '{:+.3f}%'),
        ('Avg Loser', 'avg_loser_pct', '{:.3f}%'),
        ('Profit Factor', 'profit_factor', '{:.2f}'),
        ('Total Cost Drag', 'total_cost_drag_pct', '{:.1f}%'),
        ('Total Funding PnL', 'total_funding_pct', '{:+.2f}%'),
        ('Total Return', 'total_return_pct', '{:+.1f}%'),
    ]

    for label, key, fmt in metrics:
        vals = []
        for lev in LEVERAGES:
            s = results_dict[lev]['stats']
            vals.append(fmt.format(s[key]))
        lines.append(f"| {label} | {vals[0]} | {vals[1]} | {vals[2]} |")

    return '\n'.join(lines)


def format_monthly_table(equity, start_date=None):
    """Format monthly returns as a markdown table."""
    monthly_ret = compute_monthly_returns(equity, start_date)
    if len(monthly_ret) == 0:
        return "No data available."

    # Pivot by year and month
    df_m = pd.DataFrame({
        'year': monthly_ret.index.year,
        'month': monthly_ret.index.month,
        'return': monthly_ret.values * 100,
    })

    pivot = df_m.pivot(index='year', columns='month', values='return')
    pivot.columns = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][:len(pivot.columns)]

    # Fill missing months
    all_months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    for m in all_months:
        if m not in pivot.columns:
            pivot[m] = np.nan
    pivot = pivot[all_months]

    lines = []
    header = "| Year | " + " | ".join(all_months) + " | YTD |"
    sep = "|------|" + "|".join(["-----:" for _ in all_months]) + "|-----:|"
    lines.append(header)
    lines.append(sep)

    for year in pivot.index:
        row_vals = pivot.loc[year]
        ytd = row_vals.sum()
        cells = []
        for v in row_vals:
            if pd.isna(v):
                cells.append("  -  ")
            else:
                cells.append(f"{v:+.1f}%")
        lines.append(f"| {year} | " + " | ".join(cells) + f" | {ytd:+.1f}% |")

    return '\n'.join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 80)
    print("R156: Intraday Momentum on BTC Perps")
    print("=" * 80)

    df = load_data()

    # ── Generate signals for each variant ────────────────────────────────────
    print("\n" + "=" * 60)
    signals = {}
    signals['A'] = variant_a_signals(df)
    signals['B'] = variant_b_signals(df)
    signals['C'] = variant_c_signals(df)

    # ── Run backtests ────────────────────────────────────────────────────────
    results = {}  # results[variant][leverage] = backtest result
    l12m_results = {}

    for variant in ['A', 'B', 'C']:
        print(f"\n{'=' * 60}")
        print(f"  VARIANT {variant}")
        print(f"{'=' * 60}")
        results[variant] = {}
        l12m_results[variant] = {}

        for lev in LEVERAGES:
            print(f"\n  [BACKTEST] Variant {variant} @ {lev}x leverage...")
            bt = run_backtest(df, signals[variant], leverage=lev)
            results[variant][lev] = bt

            s = bt['stats']
            print(f"    Ann Return: {s['annual_return_pct']:+.1f}%  |  "
                  f"MaxDD: {s['max_dd_pct']:.1f}%  |  "
                  f"Sharpe: {s['sharpe']:.2f}  |  "
                  f"Trades/yr: {s['trades_per_year']:.0f}")

            # Last 12 months
            l12m = compute_last_12m_stats(df, signals[variant], lev)
            l12m_results[variant][lev] = l12m
            if l12m:
                ls = l12m['stats']
                print(f"    L12M Ann Return: {ls['annual_return_pct']:+.1f}%  |  "
                      f"MaxDD: {ls['max_dd_pct']:.1f}%  |  "
                      f"Sharpe: {ls['sharpe']:.2f}")

    # ── Generate report ──────────────────────────────────────────────────────
    print("\n\n[REPORT] Generating results markdown...")
    report = generate_report(df, results, l12m_results, signals)

    with open(OUTPUT_MD, 'w') as f:
        f.write(report)
    print(f"[REPORT] Written to {OUTPUT_MD}")

    # Print summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    for variant in ['A', 'B', 'C']:
        s1 = results[variant][1]['stats']
        print(f"\nVariant {variant} (1x): Ann {s1['annual_return_pct']:+.1f}%  "
              f"Sharpe {s1['sharpe']:.2f}  MaxDD {s1['max_dd_pct']:.1f}%  "
              f"Calmar {s1['calmar']:.2f}  Trades/yr {s1['trades_per_year']:.0f}")

    print("\nDone.")


def generate_report(df, results, l12m_results, signals):
    """Generate the full markdown report."""
    lines = []
    lines.append("# R156: Intraday Momentum on BTC Perps")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"**Data:** BTC perpetual swap 1h OHLCV, {df.index.min().date()} to {df.index.max().date()}")
    lines.append(f"**Costs:** {COST_BPS_PER_SIDE} bps per side")
    lines.append(f"**Funding:** Applied per hour from parquet data based on position direction")
    lines.append("")

    # ── Hypothesis ──────────────────────────────────────────────────────────
    lines.append("## Hypothesis")
    lines.append("")
    lines.append("BTC shows intraday momentum -- when the first N hours of the day are positive, "
                 "the rest of the day tends to follow. This is a well-documented effect in equity "
                 "markets. Combined with leveraged execution on perpetual swaps, this could generate "
                 "high-turnover returns.")
    lines.append("")

    # ── Variant descriptions ────────────────────────────────────────────────
    lines.append("## Strategy Variants")
    lines.append("")
    lines.append("### Variant A: Opening Range Breakout")
    lines.append("- Compute HIGH and LOW of first 4 hours (00:00-03:00 UTC)")
    lines.append("- If price breaks above 4h high: go LONG until 22:00 UTC")
    lines.append("- If price breaks below 4h low: go SHORT until 22:00 UTC")
    lines.append("- If neither breakout: FLAT for the day")
    lines.append("- Force close at end of day")
    lines.append("")
    lines.append("### Variant B: 4-Hour Momentum")
    lines.append("- Every 4 hours (00, 04, 08, 12, 16, 20 UTC), compute last 4h return")
    lines.append("- If 4h return > 0: LONG for next 4 hours")
    lines.append("- If 4h return < 0: SHORT for next 4 hours")
    lines.append("- Max 6 trades per day")
    lines.append("")
    lines.append("### Variant C: 8-Hour Session Momentum")
    lines.append("- Every 8 hours (00, 08, 16 UTC), look at previous 8h return")
    lines.append("- If return > 0: LONG for next 8h session")
    lines.append("- If return < 0: SHORT for next 8h session")
    lines.append("- Max 3 trades per day")
    lines.append("")

    # ── Full period results ─────────────────────────────────────────────────
    for variant in ['A', 'B', 'C']:
        vname = {'A': 'Opening Range Breakout', 'B': '4-Hour Momentum',
                 'C': '8-Hour Session Momentum'}[variant]
        lines.append(f"---")
        lines.append(f"## Variant {variant}: {vname} -- Full Period Results")
        lines.append("")
        lines.append(format_stats_table(results[variant]))
        lines.append("")

        # Cost analysis
        lines.append(f"### Cost Analysis (Variant {variant})")
        lines.append("")
        lines.append("| Metric | 1x | 2x | 3x |")
        lines.append("|--------|---:|---:|---:|")

        for label, key, fmt in [
            ('Gross Annual Return', 'gross_annual_return_pct', '{:+.1f}%'),
            ('Net Annual Return', 'annual_return_pct', '{:+.1f}%'),
            ('Cost Drag (total period)', 'total_cost_drag_pct', '{:.1f}%'),
            ('Funding PnL (total period)', 'total_funding_pct', '{:+.2f}%'),
        ]:
            vals = []
            for lev in LEVERAGES:
                s = results[variant][lev]['stats']
                vals.append(fmt.format(s[key]))
            lines.append(f"| {label} | {vals[0]} | {vals[1]} | {vals[2]} |")

        # Annualized cost drag
        lines.append("")
        for lev in LEVERAGES:
            s = results[variant][lev]['stats']
            ann_cost = s['total_cost_drag_pct'] / s['n_years'] if s['n_years'] > 0 else 0
            ann_funding = s['total_funding_pct'] / s['n_years'] if s['n_years'] > 0 else 0
            lines.append(f"- **{lev}x:** Annualized cost drag = {ann_cost:.1f}%/yr, "
                         f"Annualized funding PnL = {ann_funding:+.2f}%/yr")
        lines.append("")

    # ── Last 12 Months ──────────────────────────────────────────────────────
    lines.append("---")
    lines.append(f"## Last 12 Months Performance ({L12M_START.date()} to {L12M_END.date()})")
    lines.append("")

    for variant in ['A', 'B', 'C']:
        vname = {'A': 'Opening Range Breakout', 'B': '4-Hour Momentum',
                 'C': '8-Hour Session Momentum'}[variant]
        lines.append(f"### Variant {variant}: {vname}")
        lines.append("")

        if l12m_results[variant][1] is None:
            lines.append("*Insufficient data for last 12 months.*")
            lines.append("")
            continue

        lines.append("| Metric | 1x | 2x | 3x |")
        lines.append("|--------|---:|---:|---:|")

        metrics = [
            ('Annual Return', 'annual_return_pct', '{:+.1f}%'),
            ('Max Drawdown', 'max_dd_pct', '{:.1f}%'),
            ('Sharpe', 'sharpe', '{:.2f}'),
            ('Calmar', 'calmar', '{:.2f}'),
            ('Sortino', 'sortino', '{:.2f}'),
            ('Trades', 'n_trades', '{:.0f}'),
            ('Win Rate', 'win_rate_pct', '{:.1f}%'),
            ('Profit Factor', 'profit_factor', '{:.2f}'),
        ]

        for label, key, fmt in metrics:
            vals = []
            for lev in LEVERAGES:
                l12m = l12m_results[variant][lev]
                if l12m:
                    vals.append(fmt.format(l12m['stats'][key]))
                else:
                    vals.append("-")
            lines.append(f"| {label} | {vals[0]} | {vals[1]} | {vals[2]} |")
        lines.append("")

    # ── Monthly returns tables ──────────────────────────────────────────────
    lines.append("---")
    lines.append("## Monthly Returns (Last 24 Months, 1x Leverage)")
    lines.append("")

    for variant in ['A', 'B', 'C']:
        vname = {'A': 'Opening Range Breakout', 'B': '4-Hour Momentum',
                 'C': '8-Hour Session Momentum'}[variant]
        lines.append(f"### Variant {variant}: {vname}")
        lines.append("")
        eq = results[variant][1]['equity']
        lines.append(format_monthly_table(eq, start_date=MONTHLY_START))
        lines.append("")

    # ── Comparison Summary ──────────────────────────────────────────────────
    lines.append("---")
    lines.append("## Cross-Variant Comparison (1x Leverage)")
    lines.append("")
    lines.append("| Metric | Variant A | Variant B | Variant C |")
    lines.append("|--------|----------:|----------:|----------:|")

    comp_metrics = [
        ('Annual Return', 'annual_return_pct', '{:+.1f}%'),
        ('Gross Annual Return', 'gross_annual_return_pct', '{:+.1f}%'),
        ('Max Drawdown', 'max_dd_pct', '{:.1f}%'),
        ('Sharpe', 'sharpe', '{:.2f}'),
        ('Calmar', 'calmar', '{:.2f}'),
        ('Sortino', 'sortino', '{:.2f}'),
        ('Trades/Year', 'trades_per_year', '{:.0f}'),
        ('Win Rate', 'win_rate_pct', '{:.1f}%'),
        ('Profit Factor', 'profit_factor', '{:.2f}'),
        ('Avg Trade', 'avg_trade_pct', '{:+.4f}%'),
    ]

    for label, key, fmt in comp_metrics:
        vals = []
        for v in ['A', 'B', 'C']:
            vals.append(fmt.format(results[v][1]['stats'][key]))
        lines.append(f"| {label} | {vals[0]} | {vals[1]} | {vals[2]} |")
    lines.append("")

    # ── Verdict ─────────────────────────────────────────────────────────────
    lines.append("---")
    lines.append("## Verdict & Analysis")
    lines.append("")

    # Determine best variant
    best_sharpe = -999
    best_variant = None
    for v in ['A', 'B', 'C']:
        s = results[v][1]['stats']['sharpe']
        if s > best_sharpe:
            best_sharpe = s
            best_variant = v

    lines.append(f"**Best variant by Sharpe (1x):** Variant {best_variant} "
                 f"(Sharpe = {best_sharpe:.2f})")
    lines.append("")

    # Cost impact analysis
    lines.append("### Cost Impact")
    lines.append("")
    for v in ['A', 'B', 'C']:
        s = results[v][1]['stats']
        cost_drag_ann = s['total_cost_drag_pct'] / s['n_years'] if s['n_years'] > 0 else 0
        gross_ann = s['gross_annual_return_pct']
        net_ann = s['annual_return_pct']
        lines.append(f"- **Variant {v}:** Gross {gross_ann:+.1f}% -> Net {net_ann:+.1f}% "
                     f"(cost drag: {cost_drag_ann:.1f}%/yr, trades/yr: {s['trades_per_year']:.0f})")
    lines.append("")

    # Leverage analysis
    lines.append("### Leverage Analysis")
    lines.append("")
    for v in ['A', 'B', 'C']:
        lines.append(f"**Variant {v}:**")
        for lev in LEVERAGES:
            s = results[v][lev]['stats']
            lines.append(f"  - {lev}x: Return {s['annual_return_pct']:+.1f}%, "
                         f"MaxDD {s['max_dd_pct']:.1f}%, "
                         f"Sharpe {s['sharpe']:.2f}")
        lines.append("")

    # Key observations
    lines.append("### Key Observations")
    lines.append("")

    # Check if high-frequency variant (B) is killed by costs
    b_gross = results['B'][1]['stats']['gross_annual_return_pct']
    b_net = results['B'][1]['stats']['annual_return_pct']
    b_cost_ann = results['B'][1]['stats']['total_cost_drag_pct'] / results['B'][1]['stats']['n_years']

    lines.append(f"1. **Variant B cost sensitivity:** With 6 trades/day, annualized cost drag is "
                 f"{b_cost_ann:.1f}%/yr. Gross return {b_gross:+.1f}% becomes {b_net:+.1f}% net. "
                 f"{'Costs destroy the edge.' if b_net < 0 else 'Edge survives costs.'}")
    lines.append("")

    # Check if any variant has positive Sharpe
    any_positive = any(results[v][1]['stats']['sharpe'] > 0 for v in ['A', 'B', 'C'])
    all_negative = all(results[v][1]['stats']['sharpe'] < 0 for v in ['A', 'B', 'C'])

    if all_negative:
        lines.append("2. **All variants have negative Sharpe at 1x.** Intraday momentum does not "
                     "appear to be a reliable signal on BTC perps. The hypothesis is rejected.")
    elif any_positive:
        positive_variants = [v for v in ['A', 'B', 'C'] if results[v][1]['stats']['sharpe'] > 0]
        lines.append(f"2. **Positive Sharpe variants:** {', '.join(positive_variants)}. "
                     "Some evidence of intraday momentum, but review cost drag and recent performance.")
    lines.append("")

    # Recent vs full period
    lines.append("3. **Recent vs Full Period:**")
    for v in ['A', 'B', 'C']:
        full_s = results[v][1]['stats']['sharpe']
        l12m = l12m_results[v][1]
        l12m_s = l12m['stats']['sharpe'] if l12m else float('nan')
        lines.append(f"   - Variant {v}: Full Sharpe {full_s:.2f} vs L12M Sharpe {l12m_s:.2f}")
    lines.append("")

    return '\n'.join(lines)


if __name__ == '__main__':
    main()
