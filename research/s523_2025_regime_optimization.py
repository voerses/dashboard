#!/usr/bin/env python3
"""
S523 2025 Regime Optimization
==============================
Characterize 2025 BTC regime, analyze baseline trades, sweep short gates
through the REAL v4 engine for 15mo ending 2026-04-05.
"""

import sys
import os
import json
import time
import subprocess
import shutil
import numpy as np
import pandas as pd
from collections import defaultdict

sys.path.insert(0, "/workspace/crypto_backtest")

BTC_PATH = "/workspace/crypto_backtest/data/perp/binance/1h_ohlcv/BTC_perp_1h.csv"
STRATEGY_PATH = "/workspace/crypto_backtest/strategies/s523h_regime_adaptive.py"
BACKUP_PATH = "/workspace/crypto_backtest/strategies/s523h_regime_adaptive.py.bak"
METRICS_PATH = "/workspace/crypto_backtest/results/v4/s523h_regime_adaptive_15mo_50k_metrics.json"
TRADES_PATH = "/workspace/crypto_backtest/results/v4/s523h_regime_adaptive_15mo_50k_trades.json"
RESULTS_OUT = "/workspace/crypto_backtest/research/s523_2025_regime_results.json"

BACKTEST_CMD = [
    "/workspace/venv/bin/python", "v4/portfolio_backtest.py",
    "--strategy", "s523h_regime_adaptive",
    "--months", "15", "--capital", "50000",
    "--market", "perp", "--conviction-mode", "ranked",
    "--max-portfolio-positions", "30", "--concentration", "0.30",
    "--skip-wf", "--end-date", "2026-04-05",
]


# ======================================================================
#  STEP 1: BTC 2025 Regime Characterization
# ======================================================================

def step1_btc_characterization():
    print("=" * 80)
    print("  STEP 1: BTC 2025 Regime Characterization")
    print("=" * 80)

    df = pd.read_csv(BTC_PATH, parse_dates=["datetime"])
    df = df.set_index("datetime").sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    close = df["close"]

    # Monthly returns for 2025 window (Apr 2025 - Apr 2026)
    monthly = close.resample("MS").first()
    monthly_end = close.resample("MS").last()
    monthly_ret = (monthly_end / monthly - 1) * 100

    # Weekly SMAs
    sma20w = close.rolling(20 * 7 * 24, min_periods=100).mean()  # ~20 weeks
    sma50w = close.rolling(50 * 7 * 24, min_periods=200).mean()  # ~50 weeks
    sma200d = close.rolling(200 * 24, min_periods=100).mean()     # 200 days

    # Realized vol (30d annualized)
    log_ret = np.log(close / close.shift(1))
    vol_30d = log_ret.rolling(30 * 24).std() * np.sqrt(365 * 24) * 100

    # Filter to 2025 window
    start = "2025-01-01"
    end = "2026-04-05"

    print(f"\n  Month-by-month BTC summary ({start} to {end}):")
    print(f"  {'Month':<12} {'Open':>10} {'Close':>10} {'Return%':>10} {'vs SMA200d':>12} {'vs SMA50w':>12} {'Vol30d%':>10}")
    print("  " + "-" * 78)

    results = {}
    for month_start in pd.date_range(start, end, freq="MS"):
        month_end = min(month_start + pd.offsets.MonthEnd(1), pd.Timestamp(end))
        mask = (close.index >= month_start) & (close.index <= month_end)
        if not mask.any():
            continue

        mo_close = close[mask]
        mo_open = mo_close.iloc[0]
        mo_end = mo_close.iloc[-1]
        mo_ret = (mo_end / mo_open - 1) * 100
        mo_label = month_start.strftime("%Y-%m")

        # SMA values at month start
        sma200_val = sma200d.asof(month_start)
        sma50w_val = sma50w.asof(month_start)
        vs_sma200 = "ABOVE" if mo_open > sma200_val else "BELOW"
        vs_sma50w = "ABOVE" if mo_open > sma50w_val else "BELOW"
        vol_val = vol_30d.asof(month_start)

        print(f"  {mo_label:<12} {mo_open:>10,.0f} {mo_end:>10,.0f} {mo_ret:>+10.1f} {vs_sma200:>12} {vs_sma50w:>12} {vol_val:>10.1f}")

        results[mo_label] = {
            "open": float(mo_open), "close": float(mo_end),
            "return_pct": float(mo_ret),
            "above_sma200d": vs_sma200 == "ABOVE",
            "above_sma50w": vs_sma50w == "ABOVE",
            "vol30d_ann": float(vol_val) if not np.isnan(vol_val) else None,
        }

    # SMA cross history in 2025
    print("\n  SMA Cross Events in 2025:")
    sma20w_2025 = sma20w[start:end]
    sma50w_2025 = sma50w[start:end]
    close_2025 = close[start:end]

    # Daily samples for cross detection
    close_daily = close_2025.resample("D").last().dropna()
    sma200d_daily = sma200d.reindex(close_daily.index, method="ffill")
    sma50w_daily = sma50w.reindex(close_daily.index, method="ffill")

    above_200d = close_daily > sma200d_daily
    crosses_200d = above_200d != above_200d.shift(1)
    for dt in crosses_200d[crosses_200d].index:
        direction = "UP through" if above_200d[dt] else "DOWN through"
        print(f"    {dt.strftime('%Y-%m-%d')}: BTC crossed {direction} SMA200d (price={close_daily[dt]:,.0f}, SMA={sma200d_daily[dt]:,.0f})")

    above_50w = close_daily > sma50w_daily
    crosses_50w = above_50w != above_50w.shift(1)
    for dt in crosses_50w[crosses_50w].index:
        direction = "UP through" if above_50w[dt] else "DOWN through"
        print(f"    {dt.strftime('%Y-%m-%d')}: BTC crossed {direction} SMA50w (price={close_daily[dt]:,.0f}, SMA={sma50w_daily[dt]:,.0f})")

    return results


# ======================================================================
#  STEP 2: Baseline s523c 2025 Trade Analysis
# ======================================================================

def step2_baseline_analysis():
    print("\n" + "=" * 80)
    print("  STEP 2: Baseline s523c 2025 Trade Analysis")
    print("=" * 80)

    # Run s523c_growth baseline for 15 months
    print("  Running s523c_growth baseline (15mo)...")
    cmd = [
        "/workspace/venv/bin/python", "v4/portfolio_backtest.py",
        "--strategy", "s523c_growth",
        "--months", "15", "--capital", "50000",
        "--market", "perp", "--conviction-mode", "ranked",
        "--max-portfolio-positions", "30", "--concentration", "0.30",
        "--skip-wf", "--end-date", "2026-04-05",
    ]
    t0 = time.time()
    proc = subprocess.run(cmd, cwd="/workspace/crypto_backtest",
                         capture_output=True, text=True, timeout=600)
    elapsed = time.time() - t0
    print(f"  Baseline backtest completed in {elapsed:.0f}s")

    # Read results
    baseline_metrics_path = "/workspace/crypto_backtest/results/v4/s523c_growth_15mo_50k_metrics.json"
    baseline_trades_path = "/workspace/crypto_backtest/results/v4/s523c_growth_15mo_50k_trades.json"

    with open(baseline_metrics_path) as f:
        metrics = json.load(f)
    with open(baseline_trades_path) as f:
        trades = json.load(f)

    print(f"  Total return: {metrics['total_return_pct']}%  Trades: {metrics['total_trades']}")

    # Monthly PnL breakdown
    print(f"\n  Monthly PnL breakdown (s523c_growth baseline, 15mo):")
    print(f"  {'Month':<12} {'PnL':>10} {'Trades':>8} {'Longs':>8} {'Shorts':>8} {'WinRate':>10}")
    print("  " + "-" * 60)

    monthly_stats = defaultdict(lambda: {"pnl": 0, "trades": 0, "longs": 0, "shorts": 0, "wins": 0})

    # We need entry timestamps. Since we only have entry_bar, approximate from trade data.
    # Group by approximate month using entry_bar relative to start.
    # Start date: 15 months before 2026-04-05 = 2025-01-05
    start_ts = pd.Timestamp("2025-01-05")

    for t in trades:
        entry_bar = t["entry_bar"]
        # Each bar is 1 hour
        entry_ts = start_ts + pd.Timedelta(hours=entry_bar)
        mo = entry_ts.strftime("%Y-%m")
        pnl = float(t["pnl"])
        monthly_stats[mo]["pnl"] += pnl
        monthly_stats[mo]["trades"] += 1
        if t["direction"] == 1:
            monthly_stats[mo]["longs"] += 1
        else:
            monthly_stats[mo]["shorts"] += 1
        if pnl > 0:
            monthly_stats[mo]["wins"] += 1

    # Top tokens
    token_pnl = defaultdict(float)
    token_count = defaultdict(int)
    for t in trades:
        token_pnl[t["token"]] += float(t["pnl"])
        token_count[t["token"]] += 1

    for mo in sorted(monthly_stats.keys()):
        s = monthly_stats[mo]
        wr = s["wins"] / s["trades"] * 100 if s["trades"] > 0 else 0
        print(f"  {mo:<12} {s['pnl']:>+10,.0f} {s['trades']:>8} {s['longs']:>8} {s['shorts']:>8} {wr:>10.1f}%")

    print(f"\n  Top 10 tokens by PnL (s523c_growth baseline):")
    sorted_tokens = sorted(token_pnl.items(), key=lambda x: x[1], reverse=True)
    for tok, pnl in sorted_tokens[:10]:
        print(f"    {tok:<10} PnL={pnl:>+10,.0f}  trades={token_count[tok]}")
    print(f"\n  Bottom 10 tokens by PnL:")
    for tok, pnl in sorted_tokens[-10:]:
        print(f"    {tok:<10} PnL={pnl:>+10,.0f}  trades={token_count[tok]}")

    return {
        "total_return": metrics["total_return_pct"],
        "trades": len(trades),
        "monthly": {mo: {"pnl": s["pnl"], "longs": s["longs"], "shorts": s["shorts"]}
                   for mo, s in monthly_stats.items()},
        "top_tokens": {tok: round(pnl, 2) for tok, pnl in sorted_tokens[:10]},
        "bottom_tokens": {tok: round(pnl, 2) for tok, pnl in sorted_tokens[-10:]},
    }


# ======================================================================
#  STEP 3: Short Gate Sweep (REAL v4 engine backtests)
# ======================================================================

# The gate code snippets that replace the entry gating section
GATE_VARIANTS = {
    "a_both_free": {
        "desc": "No gate, both directions fire freely (no RSI)",
        "code": """\
    # VARIANT: both_free — no gate, no RSI
    long_signal = long_signal & day_change
    short_signal = short_signal & day_change
""",
        "blacklist": False,
    },
    "b_long_only": {
        "desc": "Long only, no shorts",
        "code": """\
    # VARIANT: long_only
    long_signal = long_signal & day_change & rsi_long_window
    short_signal = np.zeros(n, dtype=bool)
""",
        "blacklist": False,
    },
    "c_short_only": {
        "desc": "Short only, no longs",
        "code": """\
    # VARIANT: short_only
    long_signal = np.zeros(n, dtype=bool)
    short_signal = short_signal & day_change & rsi_short_window
""",
        "blacklist": False,
    },
    "d_1mo_red": {
        "desc": "Shorts when last month red",
        "code": """\
    # VARIANT: 1mo_red — shorts gated by BTC monthly return
    _btc_1mo_ret = btc_aligned / btc_aligned.shift(30 * 24) - 1
    _last_month_red = (_btc_1mo_ret < 0).values
    long_signal = long_signal & day_change & rsi_long_window
    short_signal = short_signal & day_change & rsi_short_window & _last_month_red
""",
        "blacklist": False,
    },
    "e_3mo_red": {
        "desc": "Shorts when 3-month avg return < 0",
        "code": """\
    # VARIANT: 3mo_red — shorts gated by 3-month BTC return
    _btc_3mo_ret = btc_aligned / btc_aligned.shift(90 * 24) - 1
    _3mo_red = (_btc_3mo_ret < 0).values
    long_signal = long_signal & day_change & rsi_long_window
    short_signal = short_signal & day_change & rsi_short_window & _3mo_red
""",
        "blacklist": False,
    },
    "f_below_w50": {
        "desc": "Shorts when BTC < weekly SMA50",
        "code": """\
    # VARIANT: below_w50 — shorts when BTC below weekly SMA50
    _sma50w = btc_aligned.rolling(50 * 7 * 24, min_periods=200).mean()
    _below_w50 = (btc_aligned < _sma50w).values
    long_signal = long_signal & day_change & rsi_long_window
    short_signal = short_signal & day_change & rsi_short_window & _below_w50
""",
        "blacklist": False,
    },
    "g_below_w50_or_1mo_red": {
        "desc": "Shorts when below SMA50 OR last month red",
        "code": """\
    # VARIANT: below_w50_or_1mo_red
    _sma50w = btc_aligned.rolling(50 * 7 * 24, min_periods=200).mean()
    _below_w50 = (btc_aligned < _sma50w).values
    _btc_1mo_ret = btc_aligned / btc_aligned.shift(30 * 24) - 1
    _last_month_red = (_btc_1mo_ret < 0).values
    _short_gate = _below_w50 | _last_month_red
    long_signal = long_signal & day_change & rsi_long_window
    short_signal = short_signal & day_change & rsi_short_window & _short_gate
""",
        "blacklist": False,
    },
    "h_contrarian_shorts": {
        "desc": "Shorts when ABOVE SMA50 AND last month green (contrarian)",
        "code": """\
    # VARIANT: contrarian shorts — above SMA50 AND 1mo green
    _sma50w = btc_aligned.rolling(50 * 7 * 24, min_periods=200).mean()
    _above_w50 = (btc_aligned > _sma50w).values
    _btc_1mo_ret = btc_aligned / btc_aligned.shift(30 * 24) - 1
    _last_month_green = (_btc_1mo_ret > 0).values
    _short_gate = _above_w50 & _last_month_green
    long_signal = long_signal & day_change & rsi_long_window
    short_signal = short_signal & day_change & rsi_short_window & _short_gate
""",
        "blacklist": False,
    },
    "i_always_short_rsi": {
        "desc": "Both directions, shorts always on but RSI-gated",
        "code": """\
    # VARIANT: always_short_rsi — both dirs, all RSI gated
    long_signal = long_signal & day_change & rsi_long_window
    short_signal = short_signal & day_change & rsi_short_window
""",
        "blacklist": False,
    },
    "j_both_no_rsi": {
        "desc": "Both directions, NO RSI filter",
        "code": """\
    # VARIANT: both_no_rsi — no RSI filter at all
    long_signal = long_signal & day_change
    short_signal = short_signal & day_change
""",
        "blacklist": False,
    },
    "k_both_free_with_bl": {
        "desc": "Both directions, no RSI, WITH blacklist",
        "code": """\
    # VARIANT: both_free + WITH blacklist (no RSI)
    long_signal = long_signal & day_change
    short_signal = short_signal & day_change
""",
        "blacklist": True,
    },
    "l_1mo_red_rsi_always_no_bl": {
        "desc": "Longs always RSI-gated, shorts RSI+1mo_red (the current best)",
        "code": """\
    # VARIANT: 1mo_red + RSI_always (current best for 22-24)
    _btc_1mo_ret = btc_aligned / btc_aligned.shift(30 * 24) - 1
    _last_month_red = (_btc_1mo_ret < 0).values
    long_signal = long_signal & day_change & rsi_long_window
    short_signal = short_signal & day_change & rsi_short_window & _last_month_red
""",
        "blacklist": False,
    },
    "m_long_rsi_short_3mo_red": {
        "desc": "Longs RSI-gated, shorts only when 3mo red + RSI",
        "code": """\
    # VARIANT: longs RSI, shorts 3mo_red + RSI
    _btc_3mo_ret = btc_aligned / btc_aligned.shift(90 * 24) - 1
    _3mo_red = (_btc_3mo_ret < 0).values
    long_signal = long_signal & day_change & rsi_long_window
    short_signal = short_signal & day_change & rsi_short_window & _3mo_red
""",
        "blacklist": False,
    },
    "n_long_only_with_bl": {
        "desc": "Long only WITH blacklist",
        "code": """\
    # VARIANT: long_only + blacklist
    long_signal = long_signal & day_change & rsi_long_window
    short_signal = np.zeros(n, dtype=bool)
""",
        "blacklist": True,
    },
    "o_below_d200_short_only": {
        "desc": "Shorts only when BTC < SMA200d",
        "code": """\
    # VARIANT: shorts only when below SMA200d
    _below_200d = bear_regime  # already computed above
    long_signal = long_signal & day_change & rsi_long_window
    short_signal = short_signal & day_change & rsi_short_window & _below_200d
""",
        "blacklist": False,
    },
    "p_long_always_short_bear_only": {
        "desc": "Longs always (RSI), shorts only in bear (below SMA200d)",
        "code": """\
    # VARIANT: longs always RSI, shorts only in bear regime (below SMA200d)
    _below_200d = bear_regime  # already computed above
    long_signal = long_signal & day_change & rsi_long_window
    short_signal = short_signal & day_change & rsi_short_window & _below_200d
""",
        "blacklist": False,
    },
    "q_both_rsi_with_bl": {
        "desc": "Both dirs RSI-gated WITH blacklist (closest to baseline)",
        "code": """\
    # VARIANT: both RSI + blacklist
    long_signal = long_signal & day_change & rsi_long_window
    short_signal = short_signal & day_change & rsi_short_window
""",
        "blacklist": True,
    },
}

# The blacklist from s523c_growth
# The full blacklist text as it appears in the strategy file
FULL_BLACKLIST = '''TOKEN_BLACKLIST = {
    "EIGEN", "BAN", "CETUS", "ONT", "BANANA", "DOT",
    "STRK", "SOL", "PIPPIN", "DEGO", "SUI", "NEIRO",
    "ZEN", "MINA", "STEEM", "ANKR", "AVAX", "BCH",
    "BTC", "CRV", "DASH", "DUSK", "ENA", "ETC",
    "FET", "FIL", "G", "GRASS", "INJ", "JUP",
    "KAS", "KAVA", "NEO", "OGN", "POLYX", "RENDER",
    "RVN", "SAND", "SIREN", "TAO", "UNI", "WLD",
    "LTC", "LINK", "AAVE", "XRP", "ADA", "TRX",
    "DOGE", "SHIB",
}'''

NO_BLACKLIST = 'TOKEN_BLACKLIST = set()'

# The old entry gating code to replace
OLD_ENTRY_GATE = """\
    # Only fire on first bar of new day AND RSI timing condition
    long_signal = long_signal & day_change & rsi_long_window
    short_signal = short_signal & day_change & rsi_short_window"""


def patch_strategy(variant_key, variant_info):
    """Patch s523h_regime_adaptive.py with the given variant's entry gate code."""
    with open(STRATEGY_PATH, "r") as f:
        code = f.read()

    # Replace entry gate
    new_code = code.replace(OLD_ENTRY_GATE, variant_info["code"].rstrip())

    # Handle blacklist: original file has FULL blacklist
    # For "no blacklist" variants, replace full blacklist with empty set
    # For "with blacklist" variants, keep the full blacklist as-is
    if not variant_info["blacklist"]:
        new_code = new_code.replace(FULL_BLACKLIST, NO_BLACKLIST)

    # Set zw=22 for all variants (original has 30)
    new_code = new_code.replace("ZSCORE_WINDOW_DAYS = 30", "ZSCORE_WINDOW_DAYS = 22")

    with open(STRATEGY_PATH, "w") as f:
        f.write(new_code)


def restore_strategy():
    """Restore original strategy file from backup."""
    shutil.copy(BACKUP_PATH, STRATEGY_PATH)


def run_backtest_variant(variant_key, variant_info):
    """Run a single backtest variant and return metrics."""
    patch_strategy(variant_key, variant_info)
    try:
        t0 = time.time()
        proc = subprocess.run(
            BACKTEST_CMD,
            cwd="/workspace/crypto_backtest",
            capture_output=True, text=True, timeout=600,
        )
        elapsed = time.time() - t0

        if proc.returncode != 0:
            print(f"    ERROR running {variant_key}: {proc.stderr[-500:]}")
            return None

        with open(METRICS_PATH) as f:
            metrics = json.load(f)

        # Also read trades for monthly breakdown
        with open(TRADES_PATH) as f:
            trades = json.load(f)

        # Compute monthly PnL from trades
        start_ts = pd.Timestamp("2025-01-05")
        monthly_pnl = defaultdict(float)
        monthly_trades = defaultdict(int)
        for t in trades:
            entry_ts = start_ts + pd.Timedelta(hours=t["entry_bar"])
            mo = entry_ts.strftime("%Y-%m")
            monthly_pnl[mo] += float(t["pnl"])
            monthly_trades[mo] += 1

        result = {
            "ret": float(metrics["total_return_pct"]),
            "sharpe": float(metrics["sharpe_ratio"]),
            "maxdd": float(metrics["max_drawdown_pct"]),
            "trades": int(metrics["total_trades"]),
            "win_rate": float(metrics["win_rate_pct"]),
            "calmar": float(metrics.get("calmar_ratio", 0)),
            "elapsed": elapsed,
            "monthly_pnl": dict(monthly_pnl),
            "monthly_trades": dict(monthly_trades),
        }

        print(f"    {variant_key}: ret={result['ret']:+.1f}%  sharpe={result['sharpe']:.2f}  "
              f"maxdd={result['maxdd']:.1f}%  trades={result['trades']}  ({elapsed:.0f}s)")

        return result
    finally:
        restore_strategy()


def step3_short_gate_sweep():
    print("\n" + "=" * 80)
    print("  STEP 3: Short Gate Sweep (15mo ending 2026-04-05)")
    print("=" * 80)

    # Back up original
    shutil.copy(STRATEGY_PATH, BACKUP_PATH)

    results = {}
    for key, info in GATE_VARIANTS.items():
        print(f"\n  Running variant: {key} — {info['desc']}")
        result = run_backtest_variant(key, info)
        if result:
            results[key] = result

    # Sort by return
    print("\n\n  === SWEEP RESULTS (sorted by return) ===")
    print(f"  {'Variant':<35} {'Return%':>10} {'Sharpe':>8} {'MaxDD%':>10} {'Trades':>8} {'WinRate':>8}")
    print("  " + "-" * 80)

    sorted_results = sorted(results.items(), key=lambda x: x[1]["ret"], reverse=True)
    for key, r in sorted_results:
        print(f"  {key:<35} {r['ret']:>+10.1f} {r['sharpe']:>8.2f} {r['maxdd']:>10.1f} {r['trades']:>8} {r['win_rate']:>8.1f}")

    # Clean up backup
    if os.path.exists(BACKUP_PATH):
        os.remove(BACKUP_PATH)

    return results


# ======================================================================
#  STEP 4 & 5: Analysis and Detection
# ======================================================================

def step4_5_analysis(btc_data, baseline_data, sweep_results):
    print("\n" + "=" * 80)
    print("  STEP 4: What's Different About 2025")
    print("=" * 80)

    # Best for 2025 from sweep
    if not sweep_results:
        print("  No sweep results to analyze!")
        return {}

    sorted_by_ret = sorted(sweep_results.items(), key=lambda x: x[1]["ret"], reverse=True)
    best_2025 = sorted_by_ret[0]

    print(f"\n  Best 2025 config: {best_2025[0]} ({best_2025[1]['ret']:+.1f}%)")
    print(f"  Previous best (1mo_red + RSI_always): variant l")
    if "l_1mo_red_rsi_always_no_bl" in sweep_results:
        l_ret = sweep_results["l_1mo_red_rsi_always_no_bl"]["ret"]
        print(f"    → 1mo_red + RSI_always: {l_ret:+.1f}% in this 15mo window")

    # Monthly PnL for best variant
    best_monthly = best_2025[1].get("monthly_pnl", {})
    if best_monthly:
        print(f"\n  Monthly PnL for best variant ({best_2025[0]}):")
        for mo in sorted(best_monthly.keys()):
            print(f"    {mo}: {best_monthly[mo]:+,.0f}")

    print("\n" + "=" * 80)
    print("  STEP 5: Can We Detect '2025-type' Market In Advance?")
    print("=" * 80)

    # Load BTC to check conditions at start of 2025 window
    df = pd.read_csv(BTC_PATH, parse_dates=["datetime"])
    df = df.set_index("datetime").sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    close = df["close"]
    check_dates = {
        "2024-01-05": "Start of 2024 window",
        "2025-01-05": "Start of 2025 window",
        "2025-04-05": "3 months into 2025 window",
    }

    sma200d = close.rolling(200 * 24, min_periods=100).mean()
    sma50w = close.rolling(50 * 7 * 24, min_periods=200).mean()
    sma20w = close.rolling(20 * 7 * 24, min_periods=100).mean()

    # 1mo, 3mo returns
    ret_1mo = close / close.shift(30 * 24) - 1
    ret_3mo = close / close.shift(90 * 24) - 1

    # EMA ribbon: 20d vs 50d vs 100d EMAs
    ema20 = close.ewm(span=20*24).mean()
    ema50 = close.ewm(span=50*24).mean()
    ema100 = close.ewm(span=100*24).mean()

    # Ribbon spread: (ema20 - ema100) / ema100
    ribbon_spread = (ema20 - ema100) / ema100 * 100

    # Vol
    log_ret = np.log(close / close.shift(1))
    vol_30d = log_ret.rolling(30 * 24).std() * np.sqrt(365 * 24) * 100

    print(f"\n  {'Date':<18} {'Price':>10} {'vsSMA200':>10} {'vsSMA50w':>10} {'Ret1mo':>10} {'Ret3mo':>10} {'Ribbon%':>10} {'Vol%':>8}")
    print("  " + "-" * 90)

    detection_data = {}
    for dt_str, label in check_dates.items():
        dt = pd.Timestamp(dt_str)
        price = close.asof(dt)
        s200 = sma200d.asof(dt)
        s50w = sma50w.asof(dt)
        r1m = ret_1mo.asof(dt)
        r3m = ret_3mo.asof(dt)
        ribbon = ribbon_spread.asof(dt)
        vol = vol_30d.asof(dt)

        vs200 = "ABOVE" if price > s200 else "BELOW"
        vs50w = "ABOVE" if price > s50w else "BELOW"

        print(f"  {dt_str:<18} {price:>10,.0f} {vs200:>10} {vs50w:>10} {r1m:>+10.1%} {r3m:>+10.1%} {ribbon:>+10.1f} {vol:>8.1f}")

        detection_data[dt_str] = {
            "price": float(price),
            "above_sma200d": vs200 == "ABOVE",
            "above_sma50w": vs50w == "ABOVE",
            "ret_1mo": float(r1m),
            "ret_3mo": float(r3m),
            "ribbon_spread_pct": float(ribbon),
            "vol_30d_ann": float(vol),
        }

    # Summary
    print("\n  === DETECTION SIGNALS ===")
    d2024 = detection_data.get("2024-01-05", {})
    d2025 = detection_data.get("2025-01-05", {})

    diffs = []
    if d2024 and d2025:
        if d2024.get("above_sma200d") != d2025.get("above_sma200d"):
            diffs.append(f"  SMA200d: 2024 start was {'ABOVE' if d2024['above_sma200d'] else 'BELOW'}, "
                        f"2025 start is {'ABOVE' if d2025['above_sma200d'] else 'BELOW'}")
        r1 = d2024.get("ribbon_spread_pct", 0)
        r2 = d2025.get("ribbon_spread_pct", 0)
        diffs.append(f"  EMA ribbon: 2024 start = {r1:+.1f}%, 2025 start = {r2:+.1f}%")
        diffs.append(f"  3mo return: 2024 start = {d2024.get('ret_3mo', 0):+.1%}, 2025 start = {d2025.get('ret_3mo', 0):+.1%}")

    for d in diffs:
        print(d)

    return {
        "best_2025_variant": best_2025[0],
        "best_2025_return": best_2025[1]["ret"],
        "detection_data": detection_data,
        "diffs": diffs,
    }


# ======================================================================
#  MAIN
# ======================================================================

def main():
    t_start = time.time()

    # Step 1
    btc_data = step1_btc_characterization()

    # Step 2
    baseline_data = step2_baseline_analysis()

    # Step 3
    sweep_results = step3_short_gate_sweep()

    # Step 4 & 5
    analysis = step4_5_analysis(btc_data, baseline_data, sweep_results)

    # Save all results
    output = {
        "btc_2025_regime": btc_data,
        "baseline_analysis": baseline_data,
        "sweep_results": sweep_results,
        "analysis": analysis,
        "elapsed_total_sec": time.time() - t_start,
    }

    with open(RESULTS_OUT, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n\n  Results saved to {RESULTS_OUT}")
    print(f"  Total elapsed: {time.time() - t_start:.0f}s")

    # Final summary
    print("\n" + "=" * 80)
    print("  FINAL SUMMARY")
    print("=" * 80)

    if sweep_results:
        sorted_by_ret = sorted(sweep_results.items(), key=lambda x: x[1]["ret"], reverse=True)
        print(f"\n  Top 5 variants for 2025:")
        for k, r in sorted_by_ret[:5]:
            desc = GATE_VARIANTS[k]["desc"]
            print(f"    {k}: {r['ret']:+.1f}% (sharpe={r['sharpe']:.2f}, maxdd={r['maxdd']:.1f}%) — {desc}")

        print(f"\n  Bottom 3 variants:")
        for k, r in sorted_by_ret[-3:]:
            desc = GATE_VARIANTS[k]["desc"]
            print(f"    {k}: {r['ret']:+.1f}% (sharpe={r['sharpe']:.2f}, maxdd={r['maxdd']:.1f}%) — {desc}")


if __name__ == "__main__":
    main()
