#!/usr/bin/env python3
"""
s523c Multi-Timeframe Regime Sweep
===================================
Post-hoc analysis using BTC MONTHLY candle context (body sizes, direction,
S/R levels) to dynamically adjust s523c_growth behavior.

Method: run ONE baseline backtest, then for each trade in the log determine
BTC monthly context at entry time and either KEEP, FLIP, or DROP the trade.

Variants: 5 direction modes x 3 S/R modes = 15 + baseline = 16 total.
"""

import sys
import os
import json
import time
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, "/workspace/crypto_backtest")

from v4.portfolio_backtest import run_backtest
from v4.config import PortfolioConfig
from v4.simulator import build_unified_index

# ─── Configuration ────────────────────────────────────────────────────────────

BTC_OHLCV_PATH = "/workspace/crypto_backtest/data/perp/binance/1h_ohlcv/BTC_perp_1h.csv"
RESULTS_PATH = "/workspace/crypto_backtest/research/s523c_multitimeframe_results.json"

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


# ─── Step 2: Build bar-to-timestamp mapping ──────────────────────────────────

def build_bar_to_timestamp(precomputed_signals):
    """Build mapping from global bar index to datetime."""
    unified_ts, _ = build_unified_index(precomputed_signals)
    return pd.DatetimeIndex(unified_ts)


# ─── Step 3: Load BTC data & build monthly OHLCV + SMA-200 ──────────────────

def load_btc_1h():
    """Load BTC 1h OHLCV with tz-naive datetime index."""
    df = pd.read_csv(BTC_OHLCV_PATH, parse_dates=["datetime"])
    df = df.set_index("datetime").sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


def build_btc_monthly(btc_1h):
    """Resample BTC 1h to monthly OHLCV (calendar month boundaries)."""
    close = btc_1h["close"]
    high = btc_1h["high"]
    low = btc_1h["low"]
    monthly = pd.DataFrame({
        "open": close.resample("MS").first(),
        "high": high.resample("MS").max(),
        "low": low.resample("MS").min(),
        "close": close.resample("MS").last(),
    }).dropna()
    return monthly


def build_sma_200d(btc_1h):
    """Build SMA-200 day (200*24 hours) on BTC close."""
    close = btc_1h["close"]
    sma = close.rolling(200 * 24, min_periods=100 * 24).mean()
    return sma


# ─── Step 4: Compute monthly features at each entry time ────────────────────

class MonthlyContext:
    """Precomputed monthly features for fast lookup."""

    def __init__(self, btc_monthly, btc_1h_close, sma_200d):
        self.monthly = btc_monthly
        self.close = btc_1h_close
        self.sma_200d = sma_200d
        # Build a sorted list of completed month-end timestamps
        self.month_starts = btc_monthly.index.tolist()

    def get_features(self, ts):
        """Get monthly features at timestamp ts, using ONLY completed months."""
        # Find the current month start
        current_month_start = ts.replace(day=1, hour=0, minute=0, second=0)

        # Get last 6 COMPLETED months (exclude current incomplete month)
        completed = [m for m in self.month_starts if m < current_month_start]
        last6 = completed[-6:] if len(completed) >= 6 else completed

        if len(last6) < 3:
            return None  # Not enough history

        # Monthly bodies (signed %)
        bodies = []
        abs_bodies = []
        monthly_lows = []
        monthly_highs = []
        for m in last6:
            row = self.monthly.loc[m]
            if row["open"] == 0:
                continue
            body = (row["close"] - row["open"]) / row["open"]
            bodies.append(body)
            abs_bodies.append(abs(body))
            monthly_lows.append(row["low"])
            monthly_highs.append(row["high"])

        if len(bodies) < 3:
            return None

        n = len(bodies)
        avg_body = np.mean(bodies)
        avg_abs_body = np.mean(abs_bodies)
        direction_ratio = sum(1 for b in bodies if b > 0) / n
        trend_accel = bodies[-1] - bodies[-3] if n >= 3 else 0.0

        # S/R levels
        current_price = self.close.asof(ts)
        if pd.isna(current_price) or current_price <= 0:
            sr_position = 0.5
        else:
            supports = [l for l in monthly_lows if l < current_price]
            resistances = [h for h in monthly_highs if h > current_price]
            nearest_support = max(supports) if supports else current_price * 0.8
            nearest_resistance = min(resistances) if resistances else current_price * 1.2
            dist_support = (current_price - nearest_support) / current_price
            dist_resistance = (nearest_resistance - current_price) / current_price
            sr_position = dist_support / max(dist_support + dist_resistance, 0.001)

        # SMA-200 regime
        sma_val = self.sma_200d.asof(ts)
        sma_bull = bool(current_price > sma_val) if not pd.isna(sma_val) else True

        return {
            "avg_body": avg_body,
            "avg_abs_body": avg_abs_body * 100,  # as percentage
            "direction_ratio": direction_ratio,
            "trend_accel": trend_accel,
            "sr_position": sr_position,
            "sma_bull": sma_bull,
            "current_price": current_price,
            "n_months": n,
        }


# ─── Step 5: Direction mode rules ───────────────────────────────────────────

def direction_D1(feat):
    """SMA-200 binary: bear -> flip, bull -> keep."""
    if not feat["sma_bull"]:
        return "flip"
    return "keep"


def direction_D2(feat):
    """Monthly direction ratio."""
    dr = feat["direction_ratio"]
    if dr > 0.67:
        return "keep"  # 4+ green of 6, contrarian works
    elif dr < 0.33:
        return "flip"  # 4+ red of 6, trend-follow
    return "keep"  # choppy = contrarian


def direction_D3(feat):
    """Monthly direction + magnitude."""
    dr = feat["direction_ratio"]
    ab = feat["avg_abs_body"]  # already in %
    if dr > 0.67 and ab > 5:
        return "keep"  # strong bull
    elif dr < 0.33 and ab > 5:
        return "flip"  # strong bear
    elif ab < 3:
        return "keep"  # consolidation
    return "keep"  # unclear


def direction_D4(feat):
    """Combined SMA-200 + monthly confirmation."""
    sma_bear = not feat["sma_bull"]
    dr = feat["direction_ratio"]
    if sma_bear and dr < 0.5:
        return "flip"  # both agree bear
    return "keep"  # either says bull


def direction_D5(feat):
    """Three-regime with size modulation."""
    dr = feat["direction_ratio"]
    ab = feat["avg_abs_body"]  # in %
    if dr > 0.67 and ab > 5:
        return ("keep", 1.0)  # bull trending
    elif dr < 0.33 and ab > 5:
        return ("flip", 1.0)  # bear trending
    elif ab < 3:
        return ("keep", 0.5)  # consolidating
    return ("keep", 0.75)  # choppy


DIRECTION_MODES = {
    "D1": direction_D1,
    "D2": direction_D2,
    "D3": direction_D3,
    "D4": direction_D4,
    "D5": direction_D5,
}


# ─── Step 6: S/R proximity rules ────────────────────────────────────────────

def sr_S0(pnl, direction, feat):
    """No S/R filter."""
    return pnl


def sr_S1(pnl, direction, feat):
    """S/R direction filter: near support -> only longs, near resistance -> only shorts."""
    sr = feat["sr_position"]
    if sr < 0.25:
        # Near support: only keep longs
        if direction == -1:
            return 0.0  # drop short
    elif sr > 0.75:
        # Near resistance: only keep shorts
        if direction == 1:
            return 0.0  # drop long
    return pnl


def sr_S2(pnl, direction, feat):
    """S/R size modulation."""
    sr = feat["sr_position"]
    if sr < 0.25:
        if direction == 1:
            return pnl * 1.5
        else:
            return pnl * 0.5
    elif sr > 0.75:
        if direction == -1:
            return pnl * 1.5
        else:
            return pnl * 0.5
    return pnl


SR_MODES = {
    "S0": sr_S0,
    "S1": sr_S1,
    "S2": sr_S2,
}


# ─── Step 7: Apply variant to trade list ────────────────────────────────────

def apply_variant(trades, bar_timestamps, monthly_ctx, dir_name, dir_fn, sr_name, sr_fn):
    """Apply direction + S/R variant to trade list. Returns modified (pnl, entry_time, direction, margin) tuples."""
    result = []
    stats = {"kept": 0, "flipped": 0, "dropped": 0, "no_feat": 0, "size_mod": 0}

    for t in trades:
        entry_time = bar_timestamps[t.entry_bar] if t.entry_bar < len(bar_timestamps) else None
        if entry_time is None:
            result.append((t.pnl, None, t.direction, t.margin_usd))
            stats["kept"] += 1
            continue

        feat = monthly_ctx.get_features(entry_time)
        if feat is None:
            result.append((t.pnl, entry_time, t.direction, t.margin_usd))
            stats["no_feat"] += 1
            continue

        # Direction rule
        dir_result = dir_fn(feat)
        if isinstance(dir_result, tuple):
            action, size_mult = dir_result
        else:
            action = dir_result
            size_mult = 1.0

        if action == "flip":
            pnl = -t.pnl * size_mult
            stats["flipped"] += 1
        else:
            pnl = t.pnl * size_mult
            if size_mult != 1.0:
                stats["size_mod"] += 1
            else:
                stats["kept"] += 1

        # S/R rule
        pnl_after_sr = sr_fn(pnl, t.direction, feat)
        if pnl_after_sr == 0.0 and pnl != 0.0:
            stats["dropped"] += 1
            stats["kept"] -= 1  # undo the kept count

        result.append((pnl_after_sr, entry_time, t.direction, t.margin_usd))

    return result, stats


# ─── Step 8: Compute metrics ────────────────────────────────────────────────

def compute_equity_curve(modified_trades, capital):
    """Build daily equity curve from modified trade list."""
    daily_pnl = defaultdict(float)
    for pnl, entry_time, direction, margin in modified_trades:
        if entry_time is not None:
            date = pd.Timestamp(entry_time).normalize()
            daily_pnl[date] += pnl

    if not daily_pnl:
        return pd.Series([capital], index=[pd.Timestamp("2022-01-01")])

    dates = sorted(daily_pnl.keys())
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
            "total_return_pct": 0.0, "sharpe": 0.0, "max_dd_pct": 0.0,
            "calmar": 0.0, "n_trades": 0, "win_rate": 0.0,
        }

    total_return = (equity_curve.iloc[-1] / equity_curve.iloc[0] - 1) * 100

    daily_returns = equity_curve.pct_change().dropna()
    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe = (daily_returns.mean() / daily_returns.std()) * np.sqrt(365)
    else:
        sharpe = 0.0

    cummax = equity_curve.cummax()
    drawdown = (equity_curve - cummax) / cummax
    max_dd = drawdown.min() * 100

    pnls = [pnl for pnl, _, _, _ in modified_trades if pnl != 0]
    n_trades = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    win_rate = (wins / n_trades * 100) if n_trades > 0 else 0.0

    return {
        "total_return_pct": total_return,
        "sharpe": sharpe,
        "max_dd_pct": max_dd,
        "n_trades": n_trades,
        "win_rate": win_rate,
    }


def compute_per_year_metrics(modified_trades, capital):
    """Compute metrics for each year and the full period."""
    results = {}

    eq_full = compute_equity_curve(modified_trades, capital)
    results["FULL"] = compute_variant_metrics(modified_trades, capital, eq_full)

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
                "n_trades": 0, "win_rate": 0.0,
            }
            continue

        eq_year = compute_equity_curve(year_trades, capital)
        results[year] = compute_variant_metrics(year_trades, capital, eq_year)

    return results


# ─── Step 9: Top-3 detailed analysis ────────────────────────────────────────

def print_monthly_returns(modified_trades, capital, variant_name):
    """Print monthly returns for a variant."""
    daily_pnl = defaultdict(float)
    for pnl, entry_time, direction, margin in modified_trades:
        if entry_time is not None:
            date = pd.Timestamp(entry_time).normalize()
            daily_pnl[date] += pnl

    if not daily_pnl:
        print("  No trades with timestamps.")
        return

    dates = sorted(daily_pnl.keys())
    full_range = pd.date_range(dates[0], dates[-1], freq="D")
    equity = capital
    eq_series = {}
    for d in full_range:
        equity += daily_pnl.get(d, 0.0)
        eq_series[d] = equity

    eq = pd.Series(eq_series)
    monthly_eq = eq.resample("MS").last()
    monthly_ret = monthly_eq.pct_change() * 100

    print(f"\n  Monthly returns for {variant_name}:")
    print(f"  {'Month':>10s}  {'Return':>8s}  {'Equity':>12s}")
    print(f"  {'-'*10}  {'-'*8}  {'-'*12}")
    for ts, ret in monthly_ret.dropna().items():
        eq_val = monthly_eq.loc[ts]
        print(f"  {ts.strftime('%Y-%m'):>10s}  {ret:>+7.1f}%  ${eq_val:>11,.0f}")


def print_regime_timeline(monthly_ctx, start="2022-01-01", end="2025-12-31"):
    """Print the monthly regime classification."""
    print(f"\n  Monthly regime classification (BTC monthly candle context):")
    print(f"  {'Month':>10s}  {'DirRatio':>8s}  {'AvgAbs%':>7s}  {'SMA200':>6s}  {'S/R pos':>7s}  {'Regime':>14s}")
    print(f"  {'-'*10}  {'-'*8}  {'-'*7}  {'-'*6}  {'-'*7}  {'-'*14}")

    months = pd.date_range(start, end, freq="MS")
    for m in months:
        mid = m + pd.Timedelta(days=15)
        feat = monthly_ctx.get_features(mid)
        if feat is None:
            print(f"  {m.strftime('%Y-%m'):>10s}  {'N/A':>8s}")
            continue

        dr = feat["direction_ratio"]
        ab = feat["avg_abs_body"]
        sma = "BULL" if feat["sma_bull"] else "BEAR"
        sr = feat["sr_position"]

        # Classify regime (D5 logic)
        if dr > 0.67 and ab > 5:
            regime = "BULL_TRENDING"
        elif dr < 0.33 and ab > 5:
            regime = "BEAR_TRENDING"
        elif ab < 3:
            regime = "CONSOLIDATING"
        else:
            regime = "CHOPPY"

        print(f"  {m.strftime('%Y-%m'):>10s}  {dr:>8.2f}  {ab:>6.1f}%  {sma:>6s}  {sr:>7.2f}  {regime:>14s}")


def print_trade_stats(stats, variant_name, total_trades):
    """Print trade action breakdown."""
    print(f"\n  Trade breakdown for {variant_name}:")
    total = stats["kept"] + stats["flipped"] + stats["dropped"] + stats["no_feat"] + stats["size_mod"]
    print(f"    Kept:      {stats['kept']:>5d}  ({stats['kept']/total_trades*100:>5.1f}%)")
    print(f"    Flipped:   {stats['flipped']:>5d}  ({stats['flipped']/total_trades*100:>5.1f}%)")
    print(f"    Dropped:   {stats['dropped']:>5d}  ({stats['dropped']/total_trades*100:>5.1f}%)")
    print(f"    Size mod:  {stats['size_mod']:>5d}  ({stats['size_mod']/total_trades*100:>5.1f}%)")
    print(f"    No feat:   {stats['no_feat']:>5d}  ({stats['no_feat']/total_trades*100:>5.1f}%)")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    t_start = time.time()

    # Step 1: Run baseline backtest
    metrics, extra_info, trades, precomputed_signals, eq_daily = run_baseline()

    # Step 2: Build bar-to-timestamp mapping
    print("\n  Building bar-to-timestamp mapping...")
    bar_timestamps = build_bar_to_timestamp(precomputed_signals)
    print(f"  Timeline: {bar_timestamps[0]} -> {bar_timestamps[-1]} ({len(bar_timestamps)} bars)")

    # Step 3: Load BTC data and build monthly features
    print("\n  Loading BTC data...")
    btc_1h = load_btc_1h()
    btc_monthly = build_btc_monthly(btc_1h)
    sma_200d = build_sma_200d(btc_1h)
    print(f"  BTC monthly: {btc_monthly.index[0]} -> {btc_monthly.index[-1]} ({len(btc_monthly)} months)")
    print(f"  SMA-200d first valid: {sma_200d.dropna().index[0]}")

    monthly_ctx = MonthlyContext(btc_monthly, btc_1h["close"], sma_200d)

    # Step 4: Run all 16 variants
    print("\n" + "=" * 80)
    print("  STEP 2: Running multi-timeframe sweep (16 variants)")
    print("=" * 80)

    # Baseline
    baseline_trades = [(t.pnl, bar_timestamps[t.entry_bar] if t.entry_bar < len(bar_timestamps) else None,
                         t.direction, t.margin_usd) for t in trades]
    all_results = {}
    all_stats = {}
    all_modified = {}

    all_results["baseline"] = compute_per_year_metrics(baseline_trades, CAPITAL)
    all_stats["baseline"] = {"kept": len(trades), "flipped": 0, "dropped": 0, "no_feat": 0, "size_mod": 0}
    all_modified["baseline"] = baseline_trades
    print(f"  baseline: done")

    for dir_name, dir_fn in DIRECTION_MODES.items():
        for sr_name, sr_fn in SR_MODES.items():
            variant_name = f"{dir_name}_{sr_name}"
            modified, stats = apply_variant(trades, bar_timestamps, monthly_ctx,
                                            dir_name, dir_fn, sr_name, sr_fn)
            all_results[variant_name] = compute_per_year_metrics(modified, CAPITAL)
            all_stats[variant_name] = stats
            all_modified[variant_name] = modified
            r = all_results[variant_name]
            print(f"  {variant_name}: FULL={r['FULL']['total_return_pct']:+.1f}%, "
                  f"Sharpe={r['FULL']['sharpe']:.2f}")

    # Step 5: Print results table
    print("\n" + "=" * 110)
    print("  MULTI-TIMEFRAME REGIME SWEEP -- s523c on real engine trades")
    print("=" * 110)

    # Determine which variants have all 4 years positive
    def all_years_positive(metrics_dict):
        return all(
            metrics_dict[year]["total_return_pct"] > 0
            for year in ["2022", "2023", "2024", "2025"]
        )

    # Sort: all-years-positive first, then by full-period Sharpe desc
    sorted_variants = sorted(
        all_results.items(),
        key=lambda x: (
            0 if x[0] == "baseline" else (-1 if all_years_positive(x[1]) else 1),
            -x[1]["FULL"]["sharpe"]
        )
    )

    header = (f"  {'variant':<28s}  {'2022':>8s}  {'2023':>8s}  {'2024':>8s}  {'2025':>8s}"
              f"  {'FULL':>8s}  {'Sharpe':>7s}  {'MaxDD':>8s}  {'All+':>4s}")
    print(header)
    print("  " + "-" * 108)

    for variant, mdict in sorted_variants:
        check = "Y" if all_years_positive(mdict) else ""
        row = f"  {variant:<28s}"
        for year in ["2022", "2023", "2024", "2025", "FULL"]:
            ret = mdict[year]["total_return_pct"]
            row += f"  {ret:>+7.1f}%"
        row += f"  {mdict['FULL']['sharpe']:>7.2f}"
        row += f"  {mdict['FULL']['max_dd_pct']:>7.1f}%"
        row += f"  {check:>4s}"
        print(row)

    print("=" * 110)

    # Step 6: Identify winners and top 3
    winners = [v for v, m in all_results.items() if v != "baseline" and all_years_positive(m)]
    if winners:
        print(f"\n  WINNERS (all 4 years positive): {len(winners)}")
        for w in sorted(winners, key=lambda v: -all_results[v]["FULL"]["sharpe"]):
            m = all_results[w]["FULL"]
            print(f"    {w}: Sharpe={m['sharpe']:.2f}, MaxDD={m['max_dd_pct']:.1f}%, "
                  f"Return={m['total_return_pct']:+.1f}%")

    # Top 3 by Sharpe (excluding baseline)
    top3 = sorted(
        [(v, m) for v, m in all_results.items() if v != "baseline"],
        key=lambda x: -x[1]["FULL"]["sharpe"]
    )[:3]

    print(f"\n  TOP 3 BY SHARPE:")
    for rank, (variant, mdict) in enumerate(top3, 1):
        print(f"\n  --- #{rank}: {variant} ---")
        m = mdict["FULL"]
        print(f"  Full: Return={m['total_return_pct']:+.1f}%, Sharpe={m['sharpe']:.2f}, "
              f"MaxDD={m['max_dd_pct']:.1f}%, Trades={m['n_trades']}, WinRate={m['win_rate']:.1f}%")
        for y in ["2022", "2023", "2024", "2025"]:
            ym = mdict[y]
            print(f"  {y}: Return={ym['total_return_pct']:+.1f}%, Trades={ym['n_trades']}")

        print_trade_stats(all_stats[variant], variant, len(trades))
        print_monthly_returns(all_modified[variant], CAPITAL, variant)

    # Print regime timeline once
    print_regime_timeline(monthly_ctx)

    # Step 7: Save results
    json_results = {}
    for variant, metrics_dict in all_results.items():
        json_results[variant] = {}
        for period, m in metrics_dict.items():
            json_results[variant][period] = {
                k: round(float(v), 4) if isinstance(v, (float, np.floating)) else int(v)
                for k, v in m.items()
            }
        json_results[variant]["stats"] = {
            k: int(v) for k, v in all_stats.get(variant, {}).items()
        }

    with open(RESULTS_PATH, "w") as f:
        json.dump(json_results, f, indent=2)
    print(f"\n  Results saved to {RESULTS_PATH}")

    elapsed = time.time() - t_start
    print(f"\n  Total elapsed: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
