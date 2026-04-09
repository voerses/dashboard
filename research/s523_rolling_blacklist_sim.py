#!/usr/bin/env python3
"""
Rolling dynamic blacklist simulation for s523c.

Instead of a static 50-token blacklist, compute which tokens to blacklist at each
MONTHLY recalibration point using trailing performance data.

Uses s523c_growth_nobl 51-month trade log (s523c with blacklist removed) as the base,
then post-hoc filters trades to simulate dynamic blacklists.

Criteria tested:
  A: Bottom X% by trailing PnL (X = 10,20,30,40,50%)
  B: Negative trailing PnL tokens (variable size)
  C: Below-average performers (variable size)
  D: Negative PnL AND low trade count (<3)
  E: Both 90d and 180d trailing PnL negative (sustained losers)

Trailing windows: 30d, 60d, 90d, 180d, 365d
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# ── Config ──────────────────────────────────────────────────────────────────
BASE_DIR = Path("/workspace/crypto_backtest")
RESULTS_DIR = BASE_DIR / "results" / "v4"
TRADES_FILE = RESULTS_DIR / "s523c_growth_nobl_51mo_50k_trades.json"
EC_FILE = RESULTS_DIR / "s523c_growth_nobl_51mo_50k_equity_curve.json"
STATIC_BL_EC_FILE = RESULTS_DIR / "s523c_growth_51mo_50k_equity_curve.json"

INITIAL_CAPITAL = 50_000.0
BACKTEST_START = pd.Timestamp("2022-01-05 00:00")  # from equity curve first date

# Static blacklist from s523c_growth.py
STATIC_BLACKLIST = {
    "EIGEN", "BAN", "CETUS", "ONT", "BANANA", "DOT",
    "STRK", "SOL", "PIPPIN", "DEGO", "SUI", "NEIRO",
    "ZEN", "MINA", "STEEM", "ANKR", "AVAX", "BCH",
    "BTC", "CRV", "DASH", "DUSK", "ENA", "ETC",
    "FET", "FIL", "G", "GRASS", "INJ", "JUP",
    "KAS", "KAVA", "NEO", "OGN", "POLYX", "RENDER",
    "RVN", "SAND", "SIREN", "TAO", "UNI", "WLD",
    "LTC", "LINK", "AAVE", "XRP", "ADA", "TRX",
    "DOGE", "SHIB",
}

TRAILING_WINDOWS = [30, 60, 90, 180, 365]
PERCENTILE_CUTOFFS = [10, 20, 30, 40, 50]


# ── Load data ───────────────────────────────────────────────────────────────
def load_trades():
    with open(TRADES_FILE) as f:
        trades = json.load(f)
    # Add datetime fields
    for t in trades:
        t["entry_dt"] = BACKTEST_START + pd.Timedelta(hours=int(t["entry_bar"]))
        t["exit_dt"] = BACKTEST_START + pd.Timedelta(hours=int(t["exit_bar"]))
        t["pnl_f"] = float(t["pnl"])
    return trades


def load_equity_curve():
    with open(EC_FILE) as f:
        ec = json.load(f)
    return pd.Series(ec, dtype=float)


# ── Equity curve from filtered trades ───────────────────────────────────────
def compute_equity(trades, ec_index):
    """Build equity curve from a trade list, using the full date index."""
    realized = pd.Series(0.0, index=ec_index)
    for tr in trades:
        exit_dt = tr["exit_dt"].normalize()
        if exit_dt in realized.index:
            realized.loc[exit_dt] += tr["pnl_f"]
        elif exit_dt > realized.index[-1]:
            realized.iloc[-1] += tr["pnl_f"]
        else:
            ix = realized.index.get_indexer([exit_dt], method="pad")[0]
            if ix >= 0:
                realized.iloc[ix] += tr["pnl_f"]
    equity = INITIAL_CAPITAL + realized.cumsum()
    return equity


def compute_metrics(equity):
    """Compute standard metrics from equity series."""
    final = float(equity.iloc[-1])
    total = (final / INITIAL_CAPITAL - 1) * 100
    rp = equity.cummax()
    dd = (equity - rp) / rp
    maxdd = float(dd.min() * 100)
    daily = equity.pct_change().dropna()
    sharpe = float(daily.mean() / daily.std() * np.sqrt(365)) if daily.std() > 0 else 0.0
    calmar = total / abs(maxdd) if maxdd < 0 else 0.0
    return {
        "final_equity": final,
        "total_return_pct": round(total, 2),
        "max_drawdown_pct": round(maxdd, 2),
        "sharpe": round(sharpe, 3),
        "calmar": round(calmar, 3),
        "n_trades": len([1 for _ in []]),  # placeholder, set externally
    }


def compute_yearly_returns(equity):
    """Compute per-year returns."""
    yearly = {}
    for year in [2022, 2023, 2024, 2025, 2026]:
        year_start = f"{year}-01-01"
        year_end = f"{year}-12-31"
        mask = (equity.index >= year_start) & (equity.index <= year_end)
        if mask.sum() < 2:
            continue
        year_eq = equity[mask]
        start_val = float(year_eq.iloc[0])
        end_val = float(year_eq.iloc[-1])
        ret = (end_val / start_val - 1) * 100
        yearly[year] = round(ret, 2)
    return yearly


# ── Build per-token trailing metrics at each month ──────────────────────────
def build_monthly_blacklists(trades, criterion, window_days, percentile=None):
    """
    For each month in the backtest, compute which tokens to blacklist
    based on trailing performance data.

    Returns dict: {month_str: set_of_blacklisted_tokens}
    """
    # Build lookup: all trades sorted by exit date
    trades_by_exit = sorted(trades, key=lambda t: t["exit_dt"])

    # Get all unique tokens that have traded
    all_tokens = sorted(set(t["token"] for t in trades))

    # Monthly recalibration dates: first of each month
    start_month = pd.Timestamp("2022-02-01")  # first recal after some warmup
    end_month = pd.Timestamp("2026-04-01")
    months = pd.date_range(start_month, end_month, freq="MS")

    blacklists = {}

    for month_dt in months:
        month_str = month_dt.strftime("%Y-%m")
        cutoff = month_dt
        window_start = cutoff - pd.Timedelta(days=window_days)

        # Get all trades that CLOSED before cutoff and after window_start
        window_trades = [
            t for t in trades_by_exit
            if t["exit_dt"] < cutoff and t["exit_dt"] >= window_start
        ]

        if not window_trades:
            blacklists[month_str] = set()
            continue

        # Per-token metrics in this window
        token_pnl = defaultdict(float)
        token_count = defaultdict(int)
        token_wins = defaultdict(int)

        for t in window_trades:
            tok = t["token"]
            token_pnl[tok] += t["pnl_f"]
            token_count[tok] += 1
            if t["pnl_f"] > 0:
                token_wins[tok] += 1

        active_tokens = list(token_pnl.keys())

        if not active_tokens:
            blacklists[month_str] = set()
            continue

        if criterion == "A":
            # Bottom X% by trailing PnL
            pnls = np.array([token_pnl[tok] for tok in active_tokens])
            cutoff_val = np.percentile(pnls, percentile)
            bl = {tok for tok in active_tokens if token_pnl[tok] <= cutoff_val}
            blacklists[month_str] = bl

        elif criterion == "B":
            # Any token with negative trailing PnL
            bl = {tok for tok in active_tokens if token_pnl[tok] < 0}
            blacklists[month_str] = bl

        elif criterion == "C":
            # Below-average performers
            mean_pnl = np.mean([token_pnl[tok] for tok in active_tokens])
            bl = {tok for tok in active_tokens if token_pnl[tok] < mean_pnl}
            blacklists[month_str] = bl

        elif criterion == "D":
            # Negative PnL AND low trade count
            bl = {
                tok for tok in active_tokens
                if token_pnl[tok] < 0 and token_count[tok] < 3
            }
            blacklists[month_str] = bl

        elif criterion == "E":
            # Need both short and long window — use window_days as short, 2x as long
            long_window_start = cutoff - pd.Timedelta(days=window_days * 2)
            long_trades = [
                t for t in trades_by_exit
                if t["exit_dt"] < cutoff and t["exit_dt"] >= long_window_start
            ]
            long_pnl = defaultdict(float)
            for t in long_trades:
                long_pnl[t["token"]] += t["pnl_f"]

            bl = {
                tok for tok in active_tokens
                if token_pnl[tok] < 0 and long_pnl.get(tok, 0) < 0
            }
            blacklists[month_str] = bl
        else:
            blacklists[month_str] = set()

    return blacklists


def filter_trades(trades, blacklists):
    """Remove trades where token was blacklisted at entry time."""
    filtered = []
    for t in trades:
        entry_month = t["entry_dt"].to_period("M")
        # Find the most recent recalibration month <= entry
        month_str = entry_month.strftime("%Y-%m")
        bl = blacklists.get(month_str, set())
        if t["token"] not in bl:
            filtered.append(t)
    return filtered


# ── Main simulation ─────────────────────────────────────────────────────────
def run_simulation():
    print("Loading trades and equity curve...")
    trades = load_trades()
    ec_raw = load_equity_curve()
    ec_index = pd.DatetimeIndex(ec_raw.index)

    print(f"Total trades (no blacklist): {len(trades)}")
    print(f"Equity curve: {ec_index[0].date()} to {ec_index[-1].date()}")
    print(f"Unique tokens: {len(set(t['token'] for t in trades))}")

    # Baseline: no blacklist
    baseline_eq = compute_equity(trades, ec_index)
    baseline_m = compute_metrics(baseline_eq)
    baseline_m["n_trades"] = len(trades)
    baseline_yearly = compute_yearly_returns(baseline_eq)

    print(f"\n{'='*80}")
    print(f"BASELINE (no blacklist): {baseline_m['total_return_pct']:.1f}% return, "
          f"Sharpe {baseline_m['sharpe']:.3f}, MaxDD {baseline_m['max_drawdown_pct']:.1f}%")
    print(f"  Per-year: {baseline_yearly}")

    # Static blacklist comparison
    static_filtered = [t for t in trades if t["token"] not in STATIC_BLACKLIST]
    static_eq = compute_equity(static_filtered, ec_index)
    static_m = compute_metrics(static_eq)
    static_m["n_trades"] = len(static_filtered)
    static_yearly = compute_yearly_returns(static_eq)

    print(f"\nSTATIC BLACKLIST ({len(STATIC_BLACKLIST)} tokens): {static_m['total_return_pct']:.1f}% return, "
          f"Sharpe {static_m['sharpe']:.3f}, MaxDD {static_m['max_drawdown_pct']:.1f}%")
    print(f"  Per-year: {static_yearly}")

    # ── Run all criterion/window combos ─────────────────────────────────────
    results = {}
    all_combos = []

    criteria_configs = [
        ("A_p10", "A", 10),
        ("A_p20", "A", 20),
        ("A_p30", "A", 30),
        ("A_p40", "A", 40),
        ("A_p50", "A", 50),
        ("B", "B", None),
        ("C", "C", None),
        ("D", "D", None),
        ("E", "E", None),
    ]

    print(f"\n{'='*80}")
    print("Running dynamic blacklist simulations...")
    print(f"{'Criterion':<12} {'Window':<8} {'Return%':>10} {'Sharpe':>8} {'MaxDD%':>10} "
          f"{'Trades':>8} {'2022':>8} {'2023':>8} {'2024':>8} {'2025':>8}")
    print("-" * 100)

    for crit_name, crit, pct in criteria_configs:
        for window in TRAILING_WINDOWS:
            blacklists = build_monthly_blacklists(trades, crit, window, pct)
            filtered = filter_trades(trades, blacklists)
            eq = compute_equity(filtered, ec_index)
            m = compute_metrics(eq)
            m["n_trades"] = len(filtered)
            yearly = compute_yearly_returns(eq)

            combo_key = f"{crit_name}_w{window}"
            results[combo_key] = {
                "criterion": crit_name,
                "window_days": window,
                "metrics": m,
                "yearly_returns": yearly,
                "blacklist_sizes": {k: len(v) for k, v in blacklists.items()},
                "blacklists": {k: sorted(v) for k, v in blacklists.items()},
            }

            all_combos.append({
                "key": combo_key,
                "criterion": crit_name,
                "window": window,
                "total_return": m["total_return_pct"],
                "sharpe": m["sharpe"],
                "maxdd": m["max_drawdown_pct"],
                "n_trades": len(filtered),
                "yearly": yearly,
            })

            y22 = yearly.get(2022, "N/A")
            y23 = yearly.get(2023, "N/A")
            y24 = yearly.get(2024, "N/A")
            y25 = yearly.get(2025, "N/A")

            print(f"{crit_name:<12} {window:<8} {m['total_return_pct']:>10.1f} {m['sharpe']:>8.3f} "
                  f"{m['max_drawdown_pct']:>10.1f} {len(filtered):>8} "
                  f"{str(y22):>8} {str(y23):>8} {str(y24):>8} {str(y25):>8}")

    # ── Find the best criterion ─────────────────────────────────────────────
    print(f"\n{'='*80}")
    print("RANKING BY TOTAL RETURN:")
    print("-" * 80)

    sorted_combos = sorted(all_combos, key=lambda x: x["total_return"], reverse=True)
    for i, c in enumerate(sorted_combos[:15]):
        all_years_positive = all(
            c["yearly"].get(y, 0) > 0 for y in [2022, 2023, 2024, 2025]
        )
        marker = " <<< ALL YEARS POSITIVE" if all_years_positive else ""
        print(f"  {i+1:2d}. {c['key']:<20} {c['total_return']:>8.1f}% return, "
              f"Sharpe {c['sharpe']:.3f}, MaxDD {c['maxdd']:.1f}%{marker}")

    # Check which combos have all years positive
    print(f"\n{'='*80}")
    print("COMBOS WITH ALL YEARS POSITIVE (2022-2025):")
    print("-" * 80)
    all_pos = [c for c in all_combos if all(
        c["yearly"].get(y, 0) > 0 for y in [2022, 2023, 2024, 2025]
    )]
    if all_pos:
        all_pos.sort(key=lambda x: x["total_return"], reverse=True)
        for c in all_pos:
            y = c["yearly"]
            print(f"  {c['key']:<20} Total: {c['total_return']:>8.1f}%  "
                  f"2022: {y.get(2022,0):>6.1f}%  2023: {y.get(2023,0):>6.1f}%  "
                  f"2024: {y.get(2024,0):>6.1f}%  2025: {y.get(2025,0):>6.1f}%  "
                  f"Sharpe: {c['sharpe']:.3f}")
    else:
        print("  None found! Showing best by sum-of-positive-years...")
        # Show best by sum of yearly returns
        for c in sorted_combos[:10]:
            y = c["yearly"]
            sum_ret = sum(y.get(yr, 0) for yr in [2022, 2023, 2024, 2025])
            print(f"  {c['key']:<20} Sum: {sum_ret:>8.1f}%  "
                  f"2022: {y.get(2022,0):>6.1f}%  2023: {y.get(2023,0):>6.1f}%  "
                  f"2024: {y.get(2024,0):>6.1f}%  2025: {y.get(2025,0):>6.1f}%")

    # ── Best criterion deep dive ────────────────────────────────────────────
    # Pick best by Sharpe among top-10 returners
    best = sorted_combos[0]
    # But if there are all-years-positive combos, prefer the best of those
    if all_pos:
        best = all_pos[0]

    best_key = best["key"]
    best_data = results[best_key]

    print(f"\n{'='*80}")
    print(f"DEEP DIVE: {best_key}")
    print(f"  Total return: {best['total_return']:.1f}%")
    print(f"  Sharpe: {best['sharpe']:.3f}")
    print(f"  MaxDD: {best['maxdd']:.1f}%")
    print(f"  Trades: {best['n_trades']}")
    print(f"  Yearly: {best['yearly']}")

    # Monthly blacklist size
    bl_sizes = best_data["blacklist_sizes"]
    print(f"\n  Monthly blacklist size:")
    sizes = list(bl_sizes.values())
    print(f"    Range: {min(sizes)} - {max(sizes)}")
    print(f"    Mean: {np.mean(sizes):.1f}")
    print(f"    Median: {np.median(sizes):.1f}")

    # Show size over time
    print(f"\n  Blacklist size by month (first of each quarter):")
    for m_str, size in sorted(bl_sizes.items()):
        if m_str.endswith("-01") or m_str.endswith("-04") or m_str.endswith("-07") or m_str.endswith("-10"):
            print(f"    {m_str}: {size} tokens")

    # Token frequency in blacklist
    token_bl_count = defaultdict(int)
    for m_str, bl_tokens in best_data["blacklists"].items():
        for tok in bl_tokens:
            token_bl_count[tok] += 1

    total_months = len(bl_sizes)
    print(f"\n  Most frequently blacklisted tokens (of {total_months} months):")
    sorted_tokens = sorted(token_bl_count.items(), key=lambda x: x[1], reverse=True)
    for tok, count in sorted_tokens[:15]:
        pct = count / total_months * 100
        in_static = "STATIC" if tok in STATIC_BLACKLIST else ""
        print(f"    {tok:<12} {count:>3}/{total_months} months ({pct:>5.1f}%)  {in_static}")

    print(f"\n  Least frequently blacklisted tokens:")
    for tok, count in sorted_tokens[-10:]:
        pct = count / total_months * 100
        in_static = "STATIC" if tok in STATIC_BLACKLIST else ""
        print(f"    {tok:<12} {count:>3}/{total_months} months ({pct:>5.1f}%)  {in_static}")

    # Turnover analysis
    print(f"\n  Monthly blacklist turnover:")
    prev_bl = None
    high_turnover_months = []
    for m_str in sorted(best_data["blacklists"].keys()):
        bl_set = set(best_data["blacklists"][m_str])
        if prev_bl is not None and (len(bl_set) + len(prev_bl)) > 0:
            union_size = len(bl_set | prev_bl)
            changed = len(bl_set.symmetric_difference(prev_bl))
            turnover = changed / union_size if union_size > 0 else 0
            if turnover > 0.3:
                high_turnover_months.append((m_str, turnover, changed))
        prev_bl = bl_set

    if high_turnover_months:
        print(f"    High turnover months (>30%):")
        for m_str, turnover, changed in high_turnover_months:
            print(f"      {m_str}: {turnover:.0%} turnover ({changed} tokens changed)")
    else:
        print(f"    No months with >30% turnover")

    # Overlap with static blacklist per year
    print(f"\n  Overlap with static blacklist by year:")
    for year in [2022, 2023, 2024, 2025, 2026]:
        year_bls = [
            set(best_data["blacklists"][m])
            for m in sorted(best_data["blacklists"].keys())
            if m.startswith(str(year))
        ]
        if not year_bls:
            continue
        # Average overlap across months in this year
        overlaps = [len(bl & STATIC_BLACKLIST) for bl in year_bls]
        avg_bl_size = np.mean([len(bl) for bl in year_bls])
        avg_overlap = np.mean(overlaps)
        union_all = set()
        for bl in year_bls:
            union_all |= bl
        print(f"    {year}: avg BL size {avg_bl_size:.0f}, avg overlap w/static {avg_overlap:.0f}, "
              f"unique tokens BL'd: {len(union_all)}")

    # ── Save results ────────────────────────────────────────────────────────
    output = {
        "baseline": {
            "metrics": baseline_m,
            "yearly": baseline_yearly,
        },
        "static_blacklist": {
            "metrics": static_m,
            "yearly": static_yearly,
            "n_blacklisted": len(STATIC_BLACKLIST),
        },
        "dynamic_results": {},
        "ranking": [
            {"key": c["key"], "total_return": c["total_return"], "sharpe": c["sharpe"],
             "maxdd": c["maxdd"], "yearly": c["yearly"]}
            for c in sorted_combos
        ],
        "all_years_positive": [c["key"] for c in all_pos] if all_pos else [],
        "best_criterion": best_key,
    }

    for combo_key, data in results.items():
        output["dynamic_results"][combo_key] = {
            "criterion": data["criterion"],
            "window_days": data["window_days"],
            "metrics": data["metrics"],
            "yearly_returns": data["yearly_returns"],
            "blacklist_size_stats": {
                "min": min(data["blacklist_sizes"].values()) if data["blacklist_sizes"] else 0,
                "max": max(data["blacklist_sizes"].values()) if data["blacklist_sizes"] else 0,
                "mean": round(float(np.mean(list(data["blacklist_sizes"].values()))), 1) if data["blacklist_sizes"] else 0,
            },
        }

    out_path = BASE_DIR / "research" / "s523_rolling_blacklist_results.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")

    # ── Final summary ───────────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    print(f"Baseline (no BL):     {baseline_m['total_return_pct']:>8.1f}% | Sharpe {baseline_m['sharpe']:.3f}")
    print(f"Static BL (50 tok):   {static_m['total_return_pct']:>8.1f}% | Sharpe {static_m['sharpe']:.3f}")
    print(f"Best dynamic:         {best['total_return']:>8.1f}% | Sharpe {best['sharpe']:.3f} ({best_key})")

    if all_pos:
        print(f"\nAll-years-positive combos: {len(all_pos)}")
        best_pos = all_pos[0]
        sum_ret = sum(best_pos["yearly"].get(yr, 0) for yr in [2022, 2023, 2024, 2025])
        print(f"Best all-positive: {best_pos['key']} with sum-of-returns = {sum_ret:.1f}%")
    else:
        print(f"\nNo combo achieved all years positive.")
        # Show which years are negative for top combos
        for c in sorted_combos[:5]:
            neg_years = [y for y in [2022, 2023, 2024, 2025] if c["yearly"].get(y, 0) <= 0]
            print(f"  {c['key']}: negative in {neg_years}")


if __name__ == "__main__":
    run_simulation()
