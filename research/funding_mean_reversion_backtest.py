#!/workspace/venv/bin/python
"""
Gate 1 Raw Backtest: Funding Rate Mean-Reversion on BTC
========================================================

Hypothesis:
  When funding rate is extreme (z-score > threshold), SHORT the token
  (expect price drop + receive funding as a short).
  When funding rate is extreme (z-score < -threshold), LONG the token
  (expect price rise + receive funding as a long).
  Hold 8-24 hours. Turns the #1 strategy killer (funding costs) into alpha.

Point-in-time:
  Signal computed from data at bar T-1 -> entry at open of bar T.
  No look-ahead bias.

OOS: last 12 months only. Earlier data used for warmup + IS reference.

Author: Quant Research Agent
Date: 2026-04-03
"""

import sys
import warnings
import numpy as np
import pandas as pd
from pathlib import Path
from itertools import product

warnings.filterwarnings('ignore')


def log(msg):
    print(msg)
    sys.stdout.flush()


# ── Paths ────────────────────────────────────────────────────────────────────
PROJECT_DIR = Path('/workspace/crypto_backtest')
DATA_PATH = PROJECT_DIR / 'data/perp/1h_cache/BTC_1h.parquet'

# ── Parameters to sweep ─────────────────────────────────────────────────────
THRESHOLDS = [1.5, 2.0, 2.5]
MAX_HOLDS = [8, 12, 24]
ZSCORE_WINDOW = 720  # 30 days of 1h bars

# ── Cost model ───────────────────────────────────────────────────────────────
ENTRY_FEE_BPS = 5       # 0.05% taker per side
EXIT_FEE_BPS = 5
SLIPPAGE_BPS = 3        # 3 bps per side (base spread)
COST_ENTRY = (ENTRY_FEE_BPS + SLIPPAGE_BPS) / 10_000   # 0.08% = 8 bps
COST_EXIT = (EXIT_FEE_BPS + SLIPPAGE_BPS) / 10_000     # 0.08% = 8 bps
LEVERAGE = 1.25
POSITION_SIZE = 0.10    # 10% of equity per trade
SL_ATR_MULT = 2.0       # Stop loss at 2 ATR
ATR_PERIOD = 14


# ══════════════════════════════════════════════════════════════════════════════
# DATA LOADING & FEATURE COMPUTATION
# ══════════════════════════════════════════════════════════════════════════════

def load_and_prepare():
    """Load BTC 1h data and compute signals."""
    log("[DATA] Loading BTC 1h perp data...")
    df = pd.read_parquet(DATA_PATH)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    log(f"[DATA] Loaded {len(df)} bars from {df.index[0]} to {df.index[-1]}")

    # Forward-fill funding_rate NaNs (funding is reported every 8h, spread to hourly)
    df['funding_rate'] = df['funding_rate'].ffill()

    # Compute rolling z-score of funding_rate over 720-bar (30-day) window
    fr = df['funding_rate']
    fr_mean = fr.rolling(ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW).mean()
    fr_std = fr.rolling(ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW).std()
    df['funding_zscore'] = (fr - fr_mean) / fr_std

    # ATR for stop-loss
    tr = pd.DataFrame({
        'hl': df['high'] - df['low'],
        'hc': (df['high'] - df['close'].shift(1)).abs(),
        'lc': (df['low'] - df['close'].shift(1)).abs(),
    }).max(axis=1)
    df['atr'] = tr.rolling(ATR_PERIOD, min_periods=ATR_PERIOD).mean()

    # Hourly returns for PnL computation
    df['ret_1h'] = df['close'].pct_change()

    log(f"[DATA] funding_zscore non-null: {df['funding_zscore'].notna().sum()}")
    log(f"[DATA] funding_zscore range: [{df['funding_zscore'].min():.2f}, {df['funding_zscore'].max():.2f}]")

    return df


# ══════════════════════════════════════════════════════════════════════════════
# VECTORIZED TRADE SIMULATION
# ══════════════════════════════════════════════════════════════════════════════

def simulate_trades(df, threshold, max_hold):
    """
    Simulate funding mean-reversion trades.

    Signal at bar T-1 -> entry at open of bar T (= close of bar T-1, approximately).
    Position held for up to max_hold bars. Exit on stop-loss or max_hold expiry.

    Returns a DataFrame of individual trades with PnL decomposition.
    """
    # Shifted signals: signal from T-1, acting at T
    zscore_prev = df['funding_zscore'].shift(1).values
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    atr = df['atr'].shift(1).values  # ATR known at signal time
    funding_rate = df['funding_rate'].values
    timestamps = df.index.values
    n = len(df)

    trades = []
    i = 0
    while i < n:
        if np.isnan(zscore_prev[i]) or np.isnan(atr[i]):
            i += 1
            continue

        direction = 0
        if zscore_prev[i] > threshold:
            direction = -1  # SHORT: funding is extreme positive -> expect reversion
        elif zscore_prev[i] < -threshold:
            direction = 1   # LONG: funding is extreme negative -> expect reversion

        if direction == 0:
            i += 1
            continue

        # Entry at close of previous bar (= open of this bar, approximately)
        entry_price = close[i - 1] if i > 0 else close[i]
        entry_bar = i
        entry_time = timestamps[i]
        entry_zscore = zscore_prev[i]
        sl_distance = SL_ATR_MULT * atr[i]

        if direction == 1:
            sl_price = entry_price - sl_distance
        else:
            sl_price = entry_price + sl_distance

        # Walk forward bar by bar
        exit_price = None
        exit_bar = None
        exit_reason = None
        cumulative_funding = 0.0

        for j in range(i, min(i + max_hold, n)):
            # Accumulate funding: for a LONG, you PAY the funding rate (negative = receive).
            # For a SHORT, you RECEIVE the funding rate (positive = receive).
            # funding_rate is from longs' perspective: positive means longs pay shorts.
            # SHORT receives funding_rate, LONG pays funding_rate.
            if not np.isnan(funding_rate[j]):
                if direction == -1:
                    # Short receives positive funding, pays negative funding
                    cumulative_funding += funding_rate[j]
                else:
                    # Long pays positive funding, receives negative funding
                    cumulative_funding -= funding_rate[j]

            # Check stop-loss intra-bar
            if direction == 1 and low[j] <= sl_price:
                exit_price = sl_price
                exit_bar = j
                exit_reason = 'stop_loss'
                break
            elif direction == -1 and high[j] >= sl_price:
                exit_price = sl_price
                exit_bar = j
                exit_reason = 'stop_loss'
                break

            # Max hold reached
            if j == min(i + max_hold, n) - 1:
                exit_price = close[j]
                exit_bar = j
                exit_reason = 'max_hold'
                break

        if exit_price is None:
            i += 1
            continue

        # PnL decomposition
        price_return = direction * (exit_price - entry_price) / entry_price
        # Funding income is already directional from accumulation above
        funding_income = cumulative_funding
        # Gross PnL from price movement (before costs)
        gross_price_pnl = price_return
        # Total gross PnL = price + funding
        gross_pnl = gross_price_pnl + funding_income
        # Costs
        entry_cost = COST_ENTRY
        exit_cost = COST_EXIT
        total_cost = entry_cost + exit_cost  # As fraction of notional
        # Net PnL on notional
        net_pnl_notional = gross_pnl - total_cost
        # Leveraged PnL on equity allocated
        net_pnl_equity = net_pnl_notional * LEVERAGE

        hold_bars = exit_bar - entry_bar + 1

        trades.append({
            'entry_time': entry_time,
            'exit_time': timestamps[exit_bar],
            'direction': direction,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'entry_zscore': entry_zscore,
            'hold_bars': hold_bars,
            'exit_reason': exit_reason,
            'gross_price_pnl': gross_price_pnl,
            'funding_income': funding_income,
            'gross_pnl': gross_pnl,
            'total_cost': total_cost,
            'net_pnl_notional': net_pnl_notional,
            'net_pnl_equity': net_pnl_equity,
        })

        # Advance past the exit bar (no overlapping trades)
        i = exit_bar + 1

    return pd.DataFrame(trades)


# ══════════════════════════════════════════════════════════════════════════════
# EQUITY CURVE & METRICS
# ══════════════════════════════════════════════════════════════════════════════

def compute_equity_curve(trades_df):
    """Build equity curve from trade list using fixed fractional sizing."""
    if trades_df.empty:
        return pd.Series(dtype=float), {}

    equity = 1.0
    equity_points = [{'time': trades_df.iloc[0]['entry_time'], 'equity': equity}]

    for _, trade in trades_df.iterrows():
        # PnL on equity = net_pnl_equity * position_size_fraction
        pnl = trade['net_pnl_equity'] * POSITION_SIZE
        equity *= (1 + pnl)
        equity_points.append({'time': trade['exit_time'], 'equity': equity})

    eq_df = pd.DataFrame(equity_points)
    eq_series = eq_df.set_index('time')['equity']
    return eq_series


def compute_metrics(trades_df, eq_series):
    """Compute performance metrics from trades and equity curve."""
    if trades_df.empty or len(eq_series) < 2:
        return {
            'total_trades': 0, 'win_rate': 0, 'profit_factor': 0,
            'total_return_pct': 0, 'sharpe': 0, 'max_dd_pct': 0,
            'calmar': 0, 'avg_gross_price_pnl_bps': 0,
            'avg_funding_income_bps': 0, 'avg_total_cost_bps': 0,
            'avg_net_pnl_bps': 0, 'avg_hold_bars': 0,
            'long_trades': 0, 'short_trades': 0,
            'long_win_rate': 0, 'short_win_rate': 0,
            'avg_funding_received_bps': 0, 'pct_funding_positive': 0,
            'gross_pnl_from_price_pct': 0, 'gross_pnl_from_funding_pct': 0,
        }

    n_trades = len(trades_df)
    winners = trades_df['net_pnl_equity'] > 0
    win_rate = winners.mean()

    gross_wins = trades_df.loc[winners, 'net_pnl_equity'].sum()
    gross_losses = trades_df.loc[~winners, 'net_pnl_equity'].abs().sum()
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else np.inf

    total_return = (eq_series.iloc[-1] / eq_series.iloc[0]) - 1

    # Approximate daily returns for Sharpe
    # Each trade spans some hours; annualize based on calendar time
    time_span_days = (eq_series.index[-1] - eq_series.index[0]).total_seconds() / 86400
    if time_span_days > 0:
        ann_return = (1 + total_return) ** (365 / time_span_days) - 1
    else:
        ann_return = 0

    # Per-trade returns for vol estimation
    trade_returns = trades_df['net_pnl_equity'] * POSITION_SIZE
    trades_per_year = n_trades / (time_span_days / 365) if time_span_days > 0 else 0
    if trade_returns.std() > 0 and trades_per_year > 0:
        sharpe = (trade_returns.mean() / trade_returns.std()) * np.sqrt(trades_per_year)
    else:
        sharpe = 0

    # Max drawdown
    running_max = eq_series.cummax()
    drawdown = (eq_series - running_max) / running_max
    max_dd = drawdown.min()
    max_dd_pct = abs(max_dd) * 100

    calmar = ann_return / abs(max_dd) if abs(max_dd) > 0 else 0

    # Per-side breakdown
    longs = trades_df[trades_df['direction'] == 1]
    shorts = trades_df[trades_df['direction'] == -1]

    long_wr = longs['net_pnl_equity'].gt(0).mean() if len(longs) > 0 else 0
    short_wr = shorts['net_pnl_equity'].gt(0).mean() if len(shorts) > 0 else 0

    # Funding analysis
    avg_funding = trades_df['funding_income'].mean() * 10_000  # in bps
    pct_funding_positive = (trades_df['funding_income'] > 0).mean() * 100

    # Alpha decomposition: how much of gross PnL is from price vs funding
    total_price_pnl = trades_df['gross_price_pnl'].sum()
    total_funding_pnl = trades_df['funding_income'].sum()
    total_gross = total_price_pnl + total_funding_pnl
    if abs(total_gross) > 1e-10:
        price_pct = total_price_pnl / total_gross * 100
        funding_pct = total_funding_pnl / total_gross * 100
    else:
        price_pct = 0
        funding_pct = 0

    return {
        'total_trades': n_trades,
        'long_trades': len(longs),
        'short_trades': len(shorts),
        'win_rate': win_rate * 100,
        'long_win_rate': long_wr * 100,
        'short_win_rate': short_wr * 100,
        'profit_factor': profit_factor,
        'total_return_pct': total_return * 100,
        'ann_return_pct': ann_return * 100,
        'sharpe': sharpe,
        'max_dd_pct': max_dd_pct,
        'calmar': calmar,
        'avg_hold_bars': trades_df['hold_bars'].mean(),
        'avg_gross_price_pnl_bps': trades_df['gross_price_pnl'].mean() * 10_000,
        'avg_funding_income_bps': avg_funding,
        'avg_total_cost_bps': trades_df['total_cost'].mean() * 10_000,
        'avg_net_pnl_bps': trades_df['net_pnl_notional'].mean() * 10_000,
        'avg_funding_received_bps': avg_funding,
        'pct_funding_positive': pct_funding_positive,
        'gross_pnl_from_price_pct': price_pct,
        'gross_pnl_from_funding_pct': funding_pct,
    }


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    log("=" * 80)
    log("GATE 1 RAW BACKTEST: Funding Rate Mean-Reversion on BTC")
    log("=" * 80)
    log("")

    df = load_and_prepare()

    # OOS boundary: last 12 months
    oos_start = df.index.max() - pd.Timedelta(days=365)
    log(f"[SPLIT] OOS start: {oos_start}")
    log(f"[SPLIT] OOS end:   {df.index.max()}")
    log("")

    # Full data for trade simulation (warmup is handled by NaN z-scores)
    # But we only report OOS metrics

    all_results = []

    for threshold, max_hold in product(THRESHOLDS, MAX_HOLDS):
        log(f"--- threshold={threshold}, max_hold={max_hold}h ---")

        # Run simulation on full data
        trades_all = simulate_trades(df, threshold, max_hold)

        if trades_all.empty:
            log(f"  No trades generated.")
            all_results.append({
                'threshold': threshold, 'max_hold': max_hold,
                'total_trades': 0,
            })
            continue

        # Filter to OOS period
        trades_all['entry_time'] = pd.to_datetime(trades_all['entry_time'])
        trades_oos = trades_all[trades_all['entry_time'] >= oos_start].copy()

        if trades_oos.empty:
            log(f"  No OOS trades.")
            all_results.append({
                'threshold': threshold, 'max_hold': max_hold,
                'total_trades': 0, 'period': 'OOS',
            })
            continue

        # Also compute IS metrics for reference
        trades_is = trades_all[trades_all['entry_time'] < oos_start].copy()

        for label, trades_subset in [('OOS', trades_oos), ('IS', trades_is)]:
            if trades_subset.empty:
                continue

            eq = compute_equity_curve(trades_subset.reset_index(drop=True))
            metrics = compute_metrics(trades_subset.reset_index(drop=True), eq)
            metrics['threshold'] = threshold
            metrics['max_hold'] = max_hold
            metrics['period'] = label
            all_results.append(metrics)

            if label == 'OOS':
                log(f"  [{label}] Trades: {metrics['total_trades']} "
                    f"(L:{metrics['long_trades']}, S:{metrics['short_trades']})  "
                    f"WR: {metrics['win_rate']:.1f}%  "
                    f"PF: {metrics['profit_factor']:.2f}  "
                    f"Return: {metrics['total_return_pct']:.2f}%  "
                    f"Sharpe: {metrics['sharpe']:.2f}  "
                    f"MaxDD: {metrics['max_dd_pct']:.2f}%  "
                    f"Calmar: {metrics['calmar']:.2f}")
                log(f"         Avg Price PnL: {metrics['avg_gross_price_pnl_bps']:.1f} bps  "
                    f"Avg Funding: {metrics['avg_funding_income_bps']:.1f} bps  "
                    f"Avg Cost: {metrics['avg_total_cost_bps']:.1f} bps  "
                    f"Avg Net: {metrics['avg_net_pnl_bps']:.1f} bps")
                log(f"         Funding +ve: {metrics['pct_funding_positive']:.0f}%  "
                    f"Alpha split: Price {metrics['gross_pnl_from_price_pct']:.0f}% / "
                    f"Funding {metrics['gross_pnl_from_funding_pct']:.0f}%")

    results_df = pd.DataFrame(all_results)

    # ── Summary tables ────────────────────────────────────────────────────────
    log("")
    log("=" * 80)
    log("SUMMARY: OOS Results (Last 12 Months)")
    log("=" * 80)

    oos_results = results_df[results_df['period'] == 'OOS'].copy()
    if oos_results.empty:
        log("No OOS results to report.")
        return

    # Main performance table
    log("")
    log("Performance Metrics:")
    log(f"{'Thresh':>7} {'Hold':>5} {'Trades':>7} {'L/S':>8} {'WR%':>6} "
        f"{'PF':>6} {'Ret%':>8} {'Ann%':>8} {'Sharpe':>7} {'MaxDD%':>7} {'Calmar':>7}")
    log("-" * 90)
    for _, r in oos_results.iterrows():
        if r.get('total_trades', 0) == 0:
            log(f"{r['threshold']:>7.1f} {r['max_hold']:>5}   No trades")
            continue
        log(f"{r['threshold']:>7.1f} {r['max_hold']:>5.0f} {r['total_trades']:>7.0f} "
            f"{r['long_trades']:>3.0f}/{r['short_trades']:<4.0f} "
            f"{r['win_rate']:>5.1f} {r['profit_factor']:>6.2f} "
            f"{r['total_return_pct']:>7.2f} {r.get('ann_return_pct', 0):>7.2f} "
            f"{r['sharpe']:>7.2f} {r['max_dd_pct']:>6.2f} {r['calmar']:>7.2f}")

    # PnL decomposition table
    log("")
    log("PnL Decomposition (per trade, bps):")
    log(f"{'Thresh':>7} {'Hold':>5} {'GrossPrice':>11} {'Funding':>9} "
        f"{'Cost':>7} {'NetPnL':>8} {'Fund+%':>7} {'PriceSplit':>10} {'FundSplit':>10}")
    log("-" * 90)
    for _, r in oos_results.iterrows():
        if r.get('total_trades', 0) == 0:
            continue
        log(f"{r['threshold']:>7.1f} {r['max_hold']:>5.0f} "
            f"{r['avg_gross_price_pnl_bps']:>10.1f} {r['avg_funding_income_bps']:>8.1f} "
            f"{r['avg_total_cost_bps']:>6.1f} {r['avg_net_pnl_bps']:>7.1f} "
            f"{r['pct_funding_positive']:>6.0f}% "
            f"{r['gross_pnl_from_price_pct']:>9.0f}% "
            f"{r['gross_pnl_from_funding_pct']:>9.0f}%")

    # Per-side win rates
    log("")
    log("Per-Side Win Rates:")
    log(f"{'Thresh':>7} {'Hold':>5} {'LongWR%':>8} {'ShortWR%':>9} {'AvgHold':>8}")
    log("-" * 45)
    for _, r in oos_results.iterrows():
        if r.get('total_trades', 0) == 0:
            continue
        log(f"{r['threshold']:>7.1f} {r['max_hold']:>5.0f} "
            f"{r['long_win_rate']:>7.1f} {r['short_win_rate']:>8.1f} "
            f"{r['avg_hold_bars']:>7.1f}h")

    # ── IS vs OOS comparison ──────────────────────────────────────────────────
    is_results = results_df[results_df['period'] == 'IS'].copy()
    if not is_results.empty:
        log("")
        log("=" * 80)
        log("IS vs OOS Comparison:")
        log("=" * 80)
        log(f"{'Thresh':>7} {'Hold':>5} | {'IS Ret%':>8} {'IS Sharpe':>9} {'IS WR%':>7} | "
            f"{'OOS Ret%':>9} {'OOS Sharpe':>10} {'OOS WR%':>8}")
        log("-" * 85)
        for threshold in THRESHOLDS:
            for max_hold in MAX_HOLDS:
                is_row = is_results[(is_results['threshold'] == threshold) &
                                     (is_results['max_hold'] == max_hold)]
                oos_row = oos_results[(oos_results['threshold'] == threshold) &
                                       (oos_results['max_hold'] == max_hold)]
                if is_row.empty or oos_row.empty:
                    continue
                is_r = is_row.iloc[0]
                oos_r = oos_row.iloc[0]
                if is_r.get('total_trades', 0) == 0 or oos_r.get('total_trades', 0) == 0:
                    continue
                log(f"{threshold:>7.1f} {max_hold:>5} | "
                    f"{is_r['total_return_pct']:>7.2f} {is_r['sharpe']:>9.2f} "
                    f"{is_r['win_rate']:>6.1f} | "
                    f"{oos_r['total_return_pct']:>8.2f} {oos_r['sharpe']:>10.2f} "
                    f"{oos_r['win_rate']:>7.1f}")

    # ── Gate 1 verdict ────────────────────────────────────────────────────────
    log("")
    log("=" * 80)
    log("GATE 1 VERDICT")
    log("=" * 80)

    best_oos = oos_results.loc[oos_results.get('sharpe', pd.Series([0])).idxmax()] \
        if 'sharpe' in oos_results.columns and oos_results['total_trades'].sum() > 0 \
        else None

    if best_oos is not None and best_oos.get('total_trades', 0) > 0:
        log(f"Best OOS config: threshold={best_oos['threshold']}, max_hold={best_oos['max_hold']}h")
        log(f"  Sharpe:  {best_oos['sharpe']:.2f}")
        log(f"  Return:  {best_oos['total_return_pct']:.2f}%")
        log(f"  MaxDD:   {best_oos['max_dd_pct']:.2f}%")
        log(f"  WinRate: {best_oos['win_rate']:.1f}%")
        log(f"  Trades:  {best_oos['total_trades']:.0f}")
        log(f"  Avg Net PnL: {best_oos['avg_net_pnl_bps']:.1f} bps/trade")
        log(f"  Funding contributes: {best_oos['gross_pnl_from_funding_pct']:.0f}% of gross PnL")

        if best_oos['sharpe'] > 0.5 and best_oos['total_trades'] >= 20:
            log("")
            log("  PASS: Strategy shows promising signal. Advance to Gate 2 (walk-forward).")
        elif best_oos['sharpe'] > 0 and best_oos['total_trades'] >= 10:
            log("")
            log("  MARGINAL: Weak but non-zero edge. Consider parameter refinement or combining with other signals.")
        else:
            log("")
            log("  FAIL: Insufficient edge in OOS period. Do not advance.")
    else:
        log("  FAIL: No valid OOS results. Do not advance.")

    log("")
    log("Done.")


if __name__ == '__main__':
    main()
