#!/usr/bin/env python3
"""
s523c Regime-Adaptive Sweep
===========================
Post-hoc analysis: run ONE baseline backtest, then test different regime-flip
rules by manipulating trade PnL signs. Finds which regime detection makes
s523c_growth profitable across ALL market conditions (2022-2025).
"""

import sys
import os
import json
import time
import dataclasses
from collections import defaultdict

import numpy as np
import pandas as pd

# Ensure project root is on path
sys.path.insert(0, "/workspace/crypto_backtest")

from v4.portfolio_backtest import run_backtest
from v4.config import PortfolioConfig
from v4.simulator import build_unified_index
from v4.metrics import compute_metrics


# ─── Configuration ────────────────────────────────────────────────────────────

BTC_OHLCV_PATH = "/workspace/crypto_backtest/data/perp/binance/1h_ohlcv/BTC_perp_1h.csv"
BTC_FUNDING_PATH = "/workspace/crypto_backtest/data/perp/binance/funding/BTC_funding.csv"
RESULTS_PATH = "/workspace/crypto_backtest/research/s523c_regime_sweep_results.json"

CAPITAL = 50_000
MONTHS = 51
END_DATE = "2026-04-05"
STRATEGY = "s523c_growth"

YEAR_RANGES = {
    "2022": ("2022-01-01", "2022-12-31"),
    "2023": ("2023-01-01", "2023-12-31"),
    "2024": ("2024-01-01", "2024-12-31"),
    "2025": ("2025-01-01", "2025-12-31"),
}


# ─── Step 1: Run baseline backtest ───────────────────────────────────────────

def run_baseline():
    """Run the s523c_growth backtest for 51 months ending 2026-04-05."""
    print("=" * 80)
    print("  STEP 1: Running baseline s523c_growth backtest (51 months)")
    print("=" * 80)

    config = PortfolioConfig(
        exchange="binance",
        concentration_limit=0.30,
        adv_cap_pct=0.05,
        max_portfolio_positions=30,
        conviction_mode="ranked",
        seed=42,
        raw_mode=False,
        skip_walk_forward=True,
    )

    t0 = time.time()
    metrics, extra_info, trades, precomputed_signals, eq_daily = run_backtest(
        strategy_ids=[STRATEGY],
        months=MONTHS,
        capital=CAPITAL,
        config=config,
        market="perp",
        end_date=pd.Timestamp(END_DATE),
    )
    elapsed = time.time() - t0
    print(f"\n  Baseline complete: {len(trades)} trades in {elapsed:.1f}s")
    print(f"  Total return: {metrics.total_return_pct:+.1f}%  Sharpe: {metrics.sharpe_ratio:.2f}  "
          f"MaxDD: {metrics.max_drawdown_pct:.1f}%")

    return metrics, extra_info, trades, precomputed_signals, eq_daily


# ─── Step 2: Build unified timeline & map entry_bar → timestamp ──────────────

def build_bar_to_timestamp(precomputed_signals):
    """Build mapping from global bar index to datetime."""
    unified_ts, _ = build_unified_index(precomputed_signals)
    return pd.DatetimeIndex(unified_ts)


# ─── Step 3: Load BTC data & compute regime indicators ──────────────────────

def load_btc_1h():
    """Load BTC 1h OHLCV and return as DataFrame with tz-naive datetime index."""
    df = pd.read_csv(BTC_OHLCV_PATH, parse_dates=["datetime"])
    df = df.set_index("datetime").sort_index()
    # Strip timezone to match tz-naive unified timestamps
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


def load_btc_funding():
    """Load BTC funding rates with tz-naive index."""
    df = pd.read_csv(BTC_FUNDING_PATH, parse_dates=["datetime"])
    df = df.set_index("datetime").sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


def compute_regime_indicators(btc_1h, btc_funding):
    """Compute all regime indicators, returning a dict of pd.Series (bool: True=bull)."""
    close = btc_1h["close"]
    regimes = {}

    # (a) BTC N-day return sign
    for n_days in [7, 14, 30, 60, 90]:
        n_bars = n_days * 24
        ret = close / close.shift(n_bars) - 1
        regimes[f"btc_ret_{n_days}d"] = ret > 0

    # (b) BTC SMA cross (N in hours)
    for n_hours in [50 * 24, 100 * 24, 200 * 24]:
        n_days_label = n_hours // 24
        sma = close.rolling(n_hours, min_periods=n_hours // 2).mean()
        regimes[f"sma_{n_days_label}d"] = close > sma

    # (c) BTC realized vol regime
    log_ret = np.log(close / close.shift(1))
    vol_720h = log_ret.rolling(720, min_periods=360).std()
    # 1-year rolling median of vol
    vol_median_1yr = vol_720h.rolling(365 * 24, min_periods=180 * 24).median()
    # High vol = bear/choppy (keep contrarian) → bull = LOW vol
    regimes["low_vol"] = vol_720h < vol_median_1yr

    # (d) Funding rate regime
    if btc_funding is not None and len(btc_funding) > 0:
        funding = btc_funding["funding_rate"]
        # Resample to 1h (forward fill) since funding is 8h
        funding_1h = funding.reindex(close.index, method="ffill")
        # 7-day rolling mean
        funding_7d = funding_1h.rolling(7 * 24, min_periods=3 * 24).mean()
        regimes["funding_bull"] = funding_7d > 0.0001  # 0.01%

    # (e) Composite 2-of-3: BTC ret 30d + SMA 200d + low vol
    r1 = regimes["btc_ret_30d"].astype(float)
    r2 = regimes["sma_200d"].astype(float)
    r3 = regimes["low_vol"].astype(float)
    composite = r1 + r2 + r3
    regimes["composite_2of3"] = composite >= 2

    return regimes


# ─── Step 4: Apply regime-flip rules to trade list ───────────────────────────

def get_trade_entry_time(trade, bar_timestamps):
    """Get the entry timestamp for a trade."""
    if trade.entry_bar < len(bar_timestamps):
        return bar_timestamps[trade.entry_bar]
    return None


def apply_regime_flip(trades, bar_timestamps, regime_series, flip_mode):
    """Apply a regime-flip rule to trades, returning modified (pnl, included) list.

    flip_mode:
        "flip_in_bull" — flip PnL when regime=bull (keep contrarian in bear)
        "flip_in_bear" — flip PnL when regime=bear (keep contrarian in bull)
        "long_only"    — only keep trades whose direction matches regime
                         (long in bull, short in bear; drop the rest)
    Returns: list of (pnl, entry_time, direction, margin_usd) tuples
    """
    result = []
    for t in trades:
        entry_time = get_trade_entry_time(t, bar_timestamps)
        if entry_time is None:
            result.append((t.pnl, None, t.direction, t.margin_usd))
            continue

        # Look up regime at entry time
        # Find nearest regime value (regime is hourly, so exact match or ffill)
        try:
            # Use asof for nearest-before lookup
            is_bull = regime_series.asof(entry_time)
            if pd.isna(is_bull):
                is_bull = False
            else:
                is_bull = bool(is_bull)
        except Exception:
            is_bull = False

        if flip_mode == "flip_in_bull":
            pnl = -t.pnl if is_bull else t.pnl
            result.append((pnl, entry_time, t.direction, t.margin_usd))
        elif flip_mode == "flip_in_bear":
            pnl = -t.pnl if not is_bull else t.pnl
            result.append((pnl, entry_time, t.direction, t.margin_usd))
        elif flip_mode == "long_only":
            # In bull: keep only long trades (direction=1)
            # In bear: keep only short trades (direction=-1)
            if is_bull and t.direction == 1:
                result.append((t.pnl, entry_time, t.direction, t.margin_usd))
            elif not is_bull and t.direction == -1:
                result.append((t.pnl, entry_time, t.direction, t.margin_usd))
            else:
                # Drop this trade (set pnl=0 but keep for counting)
                result.append((0.0, entry_time, t.direction, t.margin_usd))
        else:
            raise ValueError(f"Unknown flip_mode: {flip_mode}")

    return result


# ─── Step 5: Compute metrics from modified trade list ────────────────────────

def compute_equity_curve(modified_trades, capital):
    """Build daily equity curve from modified trade list.

    Each trade's PnL is assigned to its entry date. We accumulate
    chronologically.
    """
    # Group PnL by date
    daily_pnl = defaultdict(float)
    for pnl, entry_time, direction, margin in modified_trades:
        if entry_time is not None:
            date = pd.Timestamp(entry_time).normalize()
            daily_pnl[date] += pnl

    if not daily_pnl:
        return pd.Series([capital], index=[pd.Timestamp("2022-01-01")])

    dates = sorted(daily_pnl.keys())
    # Build complete date range
    full_range = pd.date_range(dates[0], dates[-1], freq="D")
    equity = capital
    eq_values = []
    for d in full_range:
        equity += daily_pnl.get(d, 0.0)
        eq_values.append(equity)

    return pd.Series(eq_values, index=full_range)


def compute_variant_metrics(modified_trades, capital, equity_curve=None):
    """Compute performance metrics for a variant."""
    if equity_curve is None:
        equity_curve = compute_equity_curve(modified_trades, capital)

    if len(equity_curve) < 2:
        return {
            "total_return_pct": 0.0,
            "sharpe": 0.0,
            "max_dd_pct": 0.0,
            "calmar": 0.0,
            "n_trades": 0,
            "win_rate": 0.0,
        }

    # Total return
    total_return = (equity_curve.iloc[-1] / equity_curve.iloc[0] - 1) * 100

    # Daily returns
    daily_returns = equity_curve.pct_change().dropna()

    # Sharpe
    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe = (daily_returns.mean() / daily_returns.std()) * np.sqrt(365)
    else:
        sharpe = 0.0

    # Max drawdown
    cummax = equity_curve.cummax()
    drawdown = (equity_curve - cummax) / cummax
    max_dd = drawdown.min() * 100

    # Calmar
    years = len(equity_curve) / 365
    ann_return = ((equity_curve.iloc[-1] / equity_curve.iloc[0]) ** (1 / max(years, 0.01)) - 1) * 100
    calmar = ann_return / abs(max_dd) if abs(max_dd) > 0.01 else 0.0

    # Trade stats
    pnls = [pnl for pnl, _, _, _ in modified_trades if pnl != 0]
    n_trades = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    win_rate = (wins / n_trades * 100) if n_trades > 0 else 0.0

    return {
        "total_return_pct": total_return,
        "sharpe": sharpe,
        "max_dd_pct": max_dd,
        "calmar": calmar,
        "n_trades": n_trades,
        "win_rate": win_rate,
    }


def compute_per_year_metrics(modified_trades, capital):
    """Compute metrics for each year and the full period."""
    results = {}

    # Full period
    eq_full = compute_equity_curve(modified_trades, capital)
    results["FULL"] = compute_variant_metrics(modified_trades, capital, eq_full)

    # Per-year
    for year, (start, end) in YEAR_RANGES.items():
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)

        year_trades = [
            (pnl, et, d, m) for pnl, et, d, m in modified_trades
            if et is not None and start_ts <= pd.Timestamp(et) <= end_ts
        ]

        if not year_trades:
            results[year] = {
                "total_return_pct": 0.0, "sharpe": 0.0, "max_dd_pct": 0.0,
                "calmar": 0.0, "n_trades": 0, "win_rate": 0.0,
            }
            continue

        # For per-year, we use the equity at the start of the year as base
        # Approximate: sum PnL for the year, compute return on capital
        year_pnl = sum(pnl for pnl, _, _, _ in year_trades)
        # Use running equity up to this year as the capital base
        eq_year = compute_equity_curve(year_trades, capital)
        results[year] = compute_variant_metrics(year_trades, capital, eq_year)

    return results


# ─── Step 6: Run all variants ────────────────────────────────────────────────

def run_sweep(trades, bar_timestamps, regimes):
    """Run all regime-flip variants and collect results."""
    print("\n" + "=" * 80)
    print("  STEP 3: Running regime-flip sweep")
    print("=" * 80)

    # Baseline (no flip)
    baseline_trades = [(t.pnl, get_trade_entry_time(t, bar_timestamps),
                        t.direction, t.margin_usd) for t in trades]
    all_results = {}
    all_results["baseline"] = compute_per_year_metrics(baseline_trades, CAPITAL)
    print(f"  baseline: done")

    # Define all variants
    regime_names = list(regimes.keys())
    flip_modes = ["flip_in_bull", "flip_in_bear", "long_only"]

    for rname in regime_names:
        regime_series = regimes[rname]
        for mode in flip_modes:
            variant_name = f"{rname}_{mode}"
            modified = apply_regime_flip(trades, bar_timestamps, regime_series, mode)
            all_results[variant_name] = compute_per_year_metrics(modified, CAPITAL)
            print(f"  {variant_name}: done")

    return all_results


# ─── Step 7: Print results table ─────────────────────────────────────────────

def print_results_table(all_results):
    """Print a clean comparison table."""
    print("\n" + "=" * 120)
    print("  REGIME-ADAPTIVE s523c SWEEP — per-year and full-period metrics")
    print("=" * 120)

    header = f"  {'variant':<40s}  {'2022':>8s}  {'2023':>8s}  {'2024':>8s}  {'2025':>8s}  {'FULL':>8s}  {'Sharpe':>7s}  {'MaxDD':>8s}  {'Calmar':>7s}  {'WinR%':>6s}  {'Trades':>6s}"
    print(header)
    print("  " + "-" * 118)

    # Sort: baseline first, then by full-period Sharpe descending
    sorted_variants = sorted(
        all_results.items(),
        key=lambda x: (0 if x[0] == "baseline" else 1, -x[1]["FULL"]["sharpe"])
    )

    for variant, metrics in sorted_variants:
        row = f"  {variant:<40s}"
        for year in ["2022", "2023", "2024", "2025", "FULL"]:
            ret = metrics[year]["total_return_pct"]
            row += f"  {ret:>+7.1f}%"
        row += f"  {metrics['FULL']['sharpe']:>7.2f}"
        row += f"  {metrics['FULL']['max_dd_pct']:>7.1f}%"
        row += f"  {metrics['FULL']['calmar']:>7.2f}"
        row += f"  {metrics['FULL']['win_rate']:>5.1f}%"
        row += f"  {metrics['FULL']['n_trades']:>6d}"
        print(row)

    print("=" * 120)


def print_regime_timeline(regimes, winning_variants):
    """Print monthly regime classification for winning variants."""
    print("\n" + "=" * 100)
    print("  MONTHLY REGIME CLASSIFICATION (top variants)")
    print("=" * 100)

    months = pd.date_range("2022-01-01", "2025-12-31", freq="MS")

    for variant_name in winning_variants:
        # Extract regime name from variant
        parts = variant_name.rsplit("_", 2)
        if len(parts) >= 3:
            # e.g. btc_ret_30d_flip_in_bull → regime=btc_ret_30d
            mode = parts[-1]
            if mode in ("bull", "bear", "only"):
                regime_name = "_".join(variant_name.split("_")[:-2])
                if regime_name.endswith("_flip_in") or regime_name.endswith("_long"):
                    regime_name = "_".join(variant_name.split("_")[:-3])
            else:
                regime_name = "_".join(parts[:-2])
        else:
            continue

        if regime_name not in regimes:
            # Try harder to find the regime name
            for rn in regimes:
                if variant_name.startswith(rn):
                    regime_name = rn
                    break
            else:
                continue

        regime_series = regimes[regime_name]
        print(f"\n  {variant_name} (regime: {regime_name})")
        print(f"  {'Month':>10s}  {'Regime':>8s}")
        print(f"  {'-'*10}  {'-'*8}")

        for m in months:
            # Sample regime at mid-month
            mid = m + pd.Timedelta(days=15)
            try:
                val = regime_series.asof(mid)
                label = "BULL" if (not pd.isna(val) and bool(val)) else "BEAR"
            except Exception:
                label = "N/A"
            print(f"  {m.strftime('%Y-%m'):>10s}  {label:>8s}")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    t_start = time.time()

    # Step 1: Run baseline
    metrics, extra_info, trades, precomputed_signals, eq_daily = run_baseline()

    # Step 2: Build bar-to-timestamp mapping
    print("\n  Building bar-to-timestamp mapping...")
    bar_timestamps = build_bar_to_timestamp(precomputed_signals)
    print(f"  Timeline: {bar_timestamps[0]} → {bar_timestamps[-1]} ({len(bar_timestamps)} bars)")

    # Step 3: Load BTC data and compute regime indicators
    print("\n  Loading BTC data and computing regime indicators...")
    btc_1h = load_btc_1h()
    btc_funding = load_btc_funding()
    regimes = compute_regime_indicators(btc_1h, btc_funding)
    print(f"  Computed {len(regimes)} regime indicators")

    # Quick regime summary
    for rname, rseries in regimes.items():
        bull_pct = rseries.dropna().mean() * 100
        print(f"    {rname}: {bull_pct:.1f}% bull")

    # Step 4: Run sweep
    all_results = run_sweep(trades, bar_timestamps, regimes)

    # Step 5: Print results
    print_results_table(all_results)

    # Step 6: Find winners (profitable all 4 years)
    winners = []
    best_sharpe = -999
    best_variant = None
    for variant, metrics_dict in all_results.items():
        all_positive = all(
            metrics_dict[year]["total_return_pct"] > 0
            for year in ["2022", "2023", "2024", "2025"]
        )
        if all_positive:
            winners.append(variant)
        if metrics_dict["FULL"]["sharpe"] > best_sharpe:
            best_sharpe = metrics_dict["FULL"]["sharpe"]
            best_variant = variant

    if winners:
        print(f"\n  WINNERS (profitable all 4 years): {len(winners)}")
        for w in winners:
            m = all_results[w]["FULL"]
            print(f"    {w}: Sharpe={m['sharpe']:.2f}, MaxDD={m['max_dd_pct']:.1f}%, "
                  f"Return={m['total_return_pct']:+.1f}%")
        print_regime_timeline(regimes, winners[:5])
    else:
        print(f"\n  NO variant profitable in ALL 4 years.")
        print(f"  Best overall Sharpe: {best_variant} (Sharpe={best_sharpe:.2f})")

        # Show top 5 by number of profitable years
        year_wins = {}
        for variant, metrics_dict in all_results.items():
            n_pos = sum(1 for y in ["2022", "2023", "2024", "2025"]
                        if metrics_dict[y]["total_return_pct"] > 0)
            year_wins[variant] = n_pos
        top5 = sorted(year_wins.items(), key=lambda x: (-x[1], -all_results[x[0]]["FULL"]["sharpe"]))[:5]
        print(f"\n  Top 5 by profitable years:")
        for v, n in top5:
            m = all_results[v]
            years_str = " ".join(
                f"{y}:{m[y]['total_return_pct']:+.1f}%"
                for y in ["2022", "2023", "2024", "2025"]
            )
            print(f"    {v} ({n}/4 years): {years_str}  Sharpe={m['FULL']['sharpe']:.2f}")

        print_regime_timeline(regimes, [v for v, _ in top5[:3]])

    # Step 7: Save results
    # Convert to JSON-serializable format
    json_results = {}
    for variant, metrics_dict in all_results.items():
        json_results[variant] = {}
        for period, m in metrics_dict.items():
            json_results[variant][period] = {
                k: round(float(v), 4) if isinstance(v, (float, np.floating)) else int(v) if isinstance(v, (np.integer,)) else v
                for k, v in m.items()
            }

    with open(RESULTS_PATH, "w") as f:
        json.dump(json_results, f, indent=2)
    print(f"\n  Results saved to {RESULTS_PATH}")

    elapsed = time.time() - t_start
    print(f"\n  Total elapsed: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
