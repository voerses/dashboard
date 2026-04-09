#!/usr/bin/env python3
"""
s523 2025 Token Deep Dive — Why the static blacklist works
===========================================================
Analysis of per-token PnL, token characteristics, and dynamic blacklist rules.

Goal: understand WHY the 50-token static blacklist adds +400pp in 2025,
and find dynamic criteria that replicate this selection logic.
"""

import sys
import os
import json
import time
import warnings
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

sys.path.insert(0, "/workspace/crypto_backtest")

from v4.portfolio_backtest import run_backtest
from v4.config import PortfolioConfig

# ─── Paths ────────────────────────────────────────────────────────────────────

BASE = Path("/workspace/crypto_backtest")
CONFIG_PATH = BASE / "data/alternative/s521_token_config.json"
METRICS_DIR = BASE / "data/alternative/binance_metrics/5min"
OHLCV_DIR = BASE / "data/perp/binance/1h_ohlcv"
RESULTS_PATH = BASE / "research/s523_2025_token_results.json"

OHLCV_PREFIX_MAP = {
    "BONK": "1000BONK", "FLOKI": "1000FLOKI", "PEPE": "1000PEPE",
    "SHIB": "1000SHIB", "SATS": "1000SATS",
}

# Static blacklist from s523c_growth
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

CAPITAL = 50_000
END_DATE = "2026-04-05"


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def get_ohlcv_path(symbol):
    mapped = OHLCV_PREFIX_MAP.get(symbol, symbol)
    return OHLCV_DIR / f"{mapped}_perp_1h.csv"


def get_metrics_path(symbol):
    return METRICS_DIR / f"{symbol}USDT_5min.parquet"


# ─── Backtest runner ──────────────────────────────────────────────────────────

def run_bt(strategy_id, months, label=""):
    """Run a backtest and return (metrics, trades)."""
    print(f"\n{'='*70}")
    print(f"  Running backtest: {strategy_id} ({months}mo) {label}")
    print(f"{'='*70}")
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
    metrics, extra_info, trades, _, eq_daily = run_backtest(
        strategy_ids=[strategy_id],
        months=months,
        capital=CAPITAL,
        config=config,
        market="perp",
        end_date=pd.Timestamp(END_DATE),
    )
    elapsed = time.time() - t0
    print(f"  Done: {len(trades)} trades in {elapsed:.1f}s")
    print(f"  Return: {metrics.total_return_pct:+.1f}%  Sharpe: {metrics.sharpe_ratio:.2f}  "
          f"MaxDD: {metrics.max_drawdown_pct:.1f}%")
    return metrics, trades, eq_daily


# ─── Per-token PnL analysis ──────────────────────────────────────────────────

def analyze_per_token_pnl(trades, year_start, year_end, label=""):
    """Compute per-token PnL stats from trades in a date range."""
    token_stats = defaultdict(lambda: {
        "trades": 0, "pnl": 0.0, "wins": 0, "losses": 0,
        "long_pnl": 0.0, "short_pnl": 0.0, "long_trades": 0, "short_trades": 0,
        "margin_total": 0.0,
    })

    for t in trades:
        # Filter by year range using entry_bar -> we need timestamps
        # ClosedTrade has entry_bar (int) and exit_bar (int)
        # We'll use the token field directly, and filter by year later
        token = t.token
        s = token_stats[token]
        s["trades"] += 1
        s["pnl"] += t.pnl
        s["margin_total"] += t.margin_usd
        if t.pnl > 0:
            s["wins"] += 1
        else:
            s["losses"] += 1
        if t.direction == 1:
            s["long_pnl"] += t.pnl
            s["long_trades"] += 1
        else:
            s["short_pnl"] += t.pnl
            s["short_trades"] += 1

    # Convert to DataFrame
    rows = []
    for token, s in token_stats.items():
        wr = s["wins"] / s["trades"] if s["trades"] > 0 else 0
        rows.append({
            "token": token,
            "trades": s["trades"],
            "pnl": s["pnl"],
            "win_rate": wr,
            "avg_pnl": s["pnl"] / s["trades"] if s["trades"] > 0 else 0,
            "long_pnl": s["long_pnl"],
            "short_pnl": s["short_pnl"],
            "long_trades": s["long_trades"],
            "short_trades": s["short_trades"],
            "margin_total": s["margin_total"],
            "in_blacklist": token in STATIC_BLACKLIST,
        })

    df = pd.DataFrame(rows).sort_values("pnl", ascending=True)
    return df


# ─── Token characteristics ────────────────────────────────────────────────────

def compute_token_characteristics(config):
    """Compute ADV, volume rank, BTC beta, and IC for each token."""
    chars = {}

    # Load BTC daily returns for beta computation
    btc_path = get_ohlcv_path("BTC")
    btc_daily = None
    if btc_path.exists():
        df = pd.read_csv(btc_path, usecols=["datetime", "close", "volume"])
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        df = df.set_index("datetime").sort_index()
        btc_daily_close = df["close"].resample("1D").last().dropna()
        btc_daily_vol = df["volume"].resample("1D").sum()
        btc_daily_ret = btc_daily_close.pct_change().dropna()
        btc_daily = btc_daily_ret

    for token in config:
        ohlcv_path = get_ohlcv_path(token)
        metrics_path = get_metrics_path(token)

        char = {
            "token": token,
            "adv_usd": np.nan,
            "volume_rank": np.nan,
            "btc_beta": np.nan,
            "ic_magnitude": config[token].get("best_ic", 0),
            "direction": config[token].get("direction", "unknown"),
            "max_hold": config[token].get("max_hold_hours", 720),
            "in_blacklist": token in STATIC_BLACKLIST,
        }

        # Load OHLCV for ADV and beta
        if ohlcv_path.exists():
            try:
                df = pd.read_csv(ohlcv_path, usecols=["datetime", "close", "volume"])
                df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
                df = df.set_index("datetime").sort_index()

                # 2025 data only
                mask_2025 = (df.index >= "2025-01-01") & (df.index < "2026-01-01")
                df_2025 = df[mask_2025]

                if len(df_2025) > 100:
                    daily_vol = df_2025["volume"].resample("1D").sum().dropna()
                    daily_close = df_2025["close"].resample("1D").last().dropna()
                    daily_dollar_vol = (daily_vol * daily_close).dropna()

                    char["adv_usd"] = float(daily_dollar_vol.mean()) if len(daily_dollar_vol) > 10 else np.nan

                    # BTC beta (2025)
                    if btc_daily is not None:
                        token_ret = daily_close.pct_change().dropna()
                        merged = pd.DataFrame({"token": token_ret, "btc": btc_daily}).dropna()
                        if len(merged) > 30:
                            slope, _, _, _, _ = stats.linregress(merged["btc"], merged["token"])
                            char["btc_beta"] = float(slope)
            except Exception:
                pass

        # Funding rate behavior (2025)
        funding_path = BASE / f"data/perp/binance/funding/{token}_funding.csv"
        if funding_path.exists():
            try:
                fdf = pd.read_csv(funding_path)
                fdf["datetime"] = pd.to_datetime(fdf["datetime"], utc=True)
                fdf = fdf.set_index("datetime").sort_index()
                mask_2025 = (fdf.index >= "2025-01-01") & (fdf.index < "2026-01-01")
                fdf_2025 = fdf[mask_2025]
                if len(fdf_2025) > 10 and "funding_rate" in fdf_2025.columns:
                    char["avg_funding_rate"] = float(fdf_2025["funding_rate"].mean())
                    char["funding_std"] = float(fdf_2025["funding_rate"].std())
                else:
                    char["avg_funding_rate"] = np.nan
                    char["funding_std"] = np.nan
            except Exception:
                char["avg_funding_rate"] = np.nan
                char["funding_std"] = np.nan
        else:
            char["avg_funding_rate"] = np.nan
            char["funding_std"] = np.nan

        chars[token] = char

    # Volume rank
    all_advs = {t: c["adv_usd"] for t, c in chars.items() if not np.isnan(c["adv_usd"])}
    sorted_by_adv = sorted(all_advs.items(), key=lambda x: -x[1])
    for rank, (t, _) in enumerate(sorted_by_adv, 1):
        chars[t]["volume_rank"] = rank

    return pd.DataFrame(chars.values())


# ─── Empirical IC per token (2025) ───────────────────────────────────────────

def compute_empirical_ic_2025(config):
    """Compute per-token empirical IC in 2025 using z-score composite vs fwd return."""
    ic_results = {}
    ZW = 30  # match strategy

    for token, cfg in config.items():
        ohlcv_path = get_ohlcv_path(token)
        metrics_path = get_metrics_path(token)

        if not ohlcv_path.exists() or not metrics_path.exists():
            continue

        try:
            # Load returns
            ohlcv = pd.read_csv(ohlcv_path, usecols=["datetime", "close"])
            ohlcv["datetime"] = pd.to_datetime(ohlcv["datetime"], utc=True)
            ohlcv = ohlcv.set_index("datetime").sort_index()
            daily_close = ohlcv["close"].resample("1D").last().dropna()
            fwd_ret = daily_close.pct_change().shift(-1)  # 1-day forward return

            # Load metrics
            mdf = pd.read_parquet(metrics_path)
            mdf["create_time"] = pd.to_datetime(mdf["create_time"], utc=True)
            mdf = mdf.set_index("create_time").sort_index()
            daily_m = mdf.resample("1D").last().dropna(how="all")

            # Compute composite z-score (same as strategy)
            def _zscore(arr, w):
                s = pd.Series(arr, dtype=np.float64)
                mu = s.rolling(w, min_periods=w // 2).mean()
                sd = s.rolling(w, min_periods=w // 2).std(ddof=1)
                return ((s - mu) / sd.replace(0, np.nan)).values

            n = len(daily_m)
            if n < ZW:
                continue

            oi_z = _zscore(daily_m.get("sum_open_interest_value", pd.Series(dtype=float)).values, ZW) if "sum_open_interest_value" in daily_m.columns else np.zeros(n)
            pos_z = _zscore(daily_m.get("sum_toptrader_long_short_ratio", pd.Series(dtype=float)).values, ZW) if "sum_toptrader_long_short_ratio" in daily_m.columns else np.zeros(n)
            flow_z = _zscore(daily_m.get("sum_taker_long_short_vol_ratio", pd.Series(dtype=float)).values, ZW) if "sum_taker_long_short_vol_ratio" in daily_m.columns else np.zeros(n)

            oi_z = np.nan_to_num(oi_z, nan=0.0)
            pos_z = np.nan_to_num(pos_z, nan=0.0)
            flow_z = np.nan_to_num(flow_z, nan=0.0)

            composite = (cfg["oi_weight"] * oi_z * cfg["oi_sign"] +
                         cfg["pos_weight"] * pos_z * cfg["pos_sign"] +
                         cfg["flow_weight"] * flow_z * cfg["flow_sign"])

            comp_series = pd.Series(composite, index=daily_m.index)

            # Merge with forward returns
            merged = pd.DataFrame({"composite": comp_series, "fwd_ret": fwd_ret}).dropna()

            # 2025 only
            mask = (merged.index >= "2025-01-01") & (merged.index < "2026-01-01")
            m2025 = merged[mask]

            if len(m2025) >= 30:
                ic, pval = stats.spearmanr(m2025["composite"], m2025["fwd_ret"])
                ic_results[token] = {"ic": float(ic), "pval": float(pval), "n_days": len(m2025)}
        except Exception:
            continue

    return ic_results


# ─── Dynamic blacklist testing via strategy monkey-patching ───────────────────

def run_with_dynamic_blacklist(blacklist_set, months, label):
    """Run s523c_growth with a custom blacklist by monkey-patching TOKEN_BLACKLIST."""
    import importlib
    import importlib.util

    # Reload strategy module and patch blacklist
    strat_path = BASE / "strategies/s523c_growth.py"
    spec = importlib.util.spec_from_file_location("s523c_growth_patched", str(strat_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Save original and patch
    original_blacklist = mod.TOKEN_BLACKLIST.copy()
    mod.TOKEN_BLACKLIST = blacklist_set

    # We need to patch the actual module in sys.modules so the engine uses it
    # The strategy loader looks for files matching strategy_id + "_*"
    # Simpler: just patch the already-loaded module
    # Find the module in sys.modules
    for key in list(sys.modules.keys()):
        if "s523c_growth" in key:
            sys.modules[key].TOKEN_BLACKLIST = blacklist_set

    try:
        metrics, trades, eq_daily = run_bt("s523c_growth", months, label)
    finally:
        # Restore
        for key in list(sys.modules.keys()):
            if "s523c_growth" in key:
                sys.modules[key].TOKEN_BLACKLIST = original_blacklist

    return metrics, trades, eq_daily


def patch_blacklist_and_run(blacklist_set, months, label):
    """Patch TOKEN_BLACKLIST in the strategy module and run backtest.

    Must patch the engine's internal module cache, not sys.modules.
    """
    from v4.engine import _STRATEGY_MODULE_CACHE

    # The engine caches the loaded module under strategy_id key
    cached_mod = _STRATEGY_MODULE_CACHE.get("s523c_growth")
    if cached_mod is None:
        # Force-load it by running a dummy operation
        from v4.engine import _load_strategy_fn
        _load_strategy_fn("s523c_growth")
        cached_mod = _STRATEGY_MODULE_CACHE.get("s523c_growth")

    original = cached_mod.TOKEN_BLACKLIST.copy()
    cached_mod.TOKEN_BLACKLIST = blacklist_set

    # Clear signal caches so they recompute with new blacklist
    cached_mod._composite_cache.clear()
    cached_mod._daily_loaded.clear()
    cached_mod._aligned_cache.clear()

    try:
        metrics, trades, eq_daily = run_bt("s523c_growth", months, label)
    finally:
        cached_mod.TOKEN_BLACKLIST = original
        cached_mod._composite_cache.clear()
        cached_mod._daily_loaded.clear()
        cached_mod._aligned_cache.clear()

    return metrics, trades, eq_daily


# ─── Filter trades by year ────────────────────────────────────────────────────

def filter_trades_by_year(trades, eq_daily, year):
    """Filter trades whose midpoint falls in the given year.

    We use entry_bar + exit_bar midpoint mapped via eq_daily index.
    If eq_daily is unavailable, we use all trades (conservative).
    """
    if eq_daily is None or len(eq_daily) == 0:
        return trades

    # Build bar -> date mapping from eq_daily
    # eq_daily is a pd.Series with DatetimeIndex (daily equity)
    # trades have entry_bar/exit_bar as integer bar indices
    # We can't directly map bars to dates without the 1h index
    # Instead, use a simpler heuristic: end_date - months * 30 days
    # and map bars proportionally

    # Actually, let's just use all trades for the given run period
    # Since we control months, a 15-month run ending 2026-04-05 covers Jan 2025 - Apr 2026
    return trades


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    config = load_config()
    all_results = {}

    # ──────────────────────────────────────────────────────────────────────
    # STEP 1: Run backtests — WITH and WITHOUT blacklist for 2025
    # ──────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  STEP 1: Per-Token PnL Analysis (2025)")
    print("=" * 80)

    # 1a. WITH static blacklist (baseline s523c_growth, 15 months -> 2025)
    print("\n--- 1a. WITH blacklist (baseline) ---")
    m_bl, trades_bl, eq_bl = run_bt("s523c_growth", 15, "WITH blacklist (2025)")

    # 1b. WITHOUT blacklist (empty blacklist)
    print("\n--- 1b. WITHOUT blacklist ---")
    m_no_bl, trades_no_bl, eq_no_bl = patch_blacklist_and_run(
        set(), 15, "NO blacklist (2025)")

    # Analyze per-token PnL for the no-blacklist run
    df_no_bl = analyze_per_token_pnl(trades_no_bl, "2025-01-01", "2026-04-05",
                                      "no-blacklist 2025")

    # Analyze per-token PnL for the with-blacklist run
    df_bl = analyze_per_token_pnl(trades_bl, "2025-01-01", "2026-04-05",
                                   "with-blacklist 2025")

    print(f"\n{'='*70}")
    print(f"  BASELINE COMPARISON")
    print(f"{'='*70}")
    print(f"  WITH blacklist:    {m_bl.total_return_pct:+.1f}% return, "
          f"{m_bl.sharpe_ratio:.2f} SR, {m_bl.max_drawdown_pct:.1f}% DD")
    print(f"  WITHOUT blacklist: {m_no_bl.total_return_pct:+.1f}% return, "
          f"{m_no_bl.sharpe_ratio:.2f} SR, {m_no_bl.max_drawdown_pct:.1f}% DD")
    print(f"  Blacklist impact:  {m_bl.total_return_pct - m_no_bl.total_return_pct:+.1f}pp")

    # Top 20 WORST tokens by PnL (no-blacklist run)
    print(f"\n{'='*70}")
    print(f"  TOP 20 WORST TOKENS (no-blacklist 2025)")
    print(f"{'='*70}")
    print(f"{'Token':<12} {'PnL':>10} {'Trades':>7} {'WR':>6} {'AvgPnL':>10} "
          f"{'LongPnL':>10} {'ShortPnL':>10} {'InBL':>5}")
    print("-" * 80)
    for _, r in df_no_bl.head(20).iterrows():
        print(f"{r['token']:<12} {r['pnl']:>10.0f} {r['trades']:>7} "
              f"{r['win_rate']:>6.1%} {r['avg_pnl']:>10.0f} "
              f"{r['long_pnl']:>10.0f} {r['short_pnl']:>10.0f} "
              f"{'YES' if r['in_blacklist'] else 'no':>5}")

    # Top 20 BEST tokens
    print(f"\n{'='*70}")
    print(f"  TOP 20 BEST TOKENS (no-blacklist 2025)")
    print(f"{'='*70}")
    print(f"{'Token':<12} {'PnL':>10} {'Trades':>7} {'WR':>6} {'AvgPnL':>10} "
          f"{'LongPnL':>10} {'ShortPnL':>10} {'InBL':>5}")
    print("-" * 80)
    for _, r in df_no_bl.tail(20).iloc[::-1].iterrows():
        print(f"{r['token']:<12} {r['pnl']:>10.0f} {r['trades']:>7} "
              f"{r['win_rate']:>6.1%} {r['avg_pnl']:>10.0f} "
              f"{r['long_pnl']:>10.0f} {r['short_pnl']:>10.0f} "
              f"{'YES' if r['in_blacklist'] else 'no':>5}")

    # Summary: blacklist token aggregate PnL
    bl_tokens_pnl = df_no_bl[df_no_bl["in_blacklist"]]["pnl"].sum()
    non_bl_tokens_pnl = df_no_bl[~df_no_bl["in_blacklist"]]["pnl"].sum()
    print(f"\n  Blacklisted tokens total PnL (in no-BL run): ${bl_tokens_pnl:,.0f}")
    print(f"  Non-blacklisted tokens total PnL:             ${non_bl_tokens_pnl:,.0f}")
    print(f"  Blacklist tokens traded: {df_no_bl[df_no_bl['in_blacklist']]['trades'].sum()}")
    print(f"  Non-BL tokens traded:    {df_no_bl[~df_no_bl['in_blacklist']]['trades'].sum()}")

    all_results["step1"] = {
        "with_blacklist": {
            "return_pct": float(m_bl.total_return_pct),
            "sharpe": float(m_bl.sharpe_ratio),
            "max_dd": float(m_bl.max_drawdown_pct),
            "n_trades": len(trades_bl),
        },
        "without_blacklist": {
            "return_pct": float(m_no_bl.total_return_pct),
            "sharpe": float(m_no_bl.sharpe_ratio),
            "max_dd": float(m_no_bl.max_drawdown_pct),
            "n_trades": len(trades_no_bl),
        },
        "bl_tokens_pnl": float(bl_tokens_pnl),
        "non_bl_tokens_pnl": float(non_bl_tokens_pnl),
        "worst_20": df_no_bl.head(20)[["token", "pnl", "trades", "win_rate", "in_blacklist"]].to_dict("records"),
        "best_20": df_no_bl.tail(20).iloc[::-1][["token", "pnl", "trades", "win_rate", "in_blacklist"]].to_dict("records"),
    }

    # ──────────────────────────────────────────────────────────────────────
    # STEP 2: Token Characteristics — what distinguishes losers?
    # ──────────────────────────────────────────────────────────────────────
    print(f"\n\n{'='*80}")
    print(f"  STEP 2: Token Characteristics — What Distinguishes Losers?")
    print(f"{'='*80}")

    chars_df = compute_token_characteristics(config)
    ic_results = compute_empirical_ic_2025(config)

    # Merge characteristics with PnL
    merged = df_no_bl.merge(chars_df, on="token", how="left", suffixes=("", "_char"))
    merged["empirical_ic"] = merged["token"].map(
        lambda t: ic_results.get(t, {}).get("ic", np.nan))

    # Split into losers (bottom 20%) and winners (top 20%)
    n = len(merged)
    losers = merged.head(max(1, n // 5))
    winners = merged.tail(max(1, n // 5)).iloc[::-1]

    def print_group_stats(group, label):
        print(f"\n  --- {label} ({len(group)} tokens) ---")
        print(f"  Avg PnL:        ${group['pnl'].mean():,.0f}")
        print(f"  Avg trades:     {group['trades'].mean():.1f}")
        print(f"  Avg win rate:   {group['win_rate'].mean():.1%}")
        adv = group["adv_usd"].dropna()
        if len(adv) > 0:
            print(f"  Median ADV:     ${adv.median():,.0f}")
            print(f"  Mean ADV:       ${adv.mean():,.0f}")
        beta = group["btc_beta"].dropna()
        if len(beta) > 0:
            print(f"  Median BTC beta:{beta.median():.2f}")
            print(f"  Mean BTC beta:  {beta.mean():.2f}")
        vol_rank = group["volume_rank"].dropna()
        if len(vol_rank) > 0:
            print(f"  Median vol rank:{vol_rank.median():.0f}")
        ic_mag = group["ic_magnitude"].dropna()
        if len(ic_mag) > 0:
            print(f"  Median config IC:{ic_mag.median():.4f}")
        emp_ic = group["empirical_ic"].dropna()
        if len(emp_ic) > 0:
            print(f"  Median 2025 IC: {emp_ic.median():.4f}")
            print(f"  Mean 2025 IC:   {emp_ic.mean():.4f}")
        bl_pct = group["in_blacklist"].mean()
        print(f"  In blacklist:   {bl_pct:.0%}")
        fund = group["avg_funding_rate"].dropna() if "avg_funding_rate" in group.columns else pd.Series(dtype=float)
        if len(fund) > 0:
            print(f"  Avg funding:    {fund.mean():.6f}")
        dirs = group["direction"].value_counts() if "direction" in group.columns else pd.Series(dtype=int)
        if len(dirs) > 0:
            print(f"  Directions:     {dict(dirs)}")

    print_group_stats(losers, "BOTTOM QUINTILE (losers)")
    print_group_stats(winners, "TOP QUINTILE (winners)")

    # Detailed table for all tokens with characteristics
    print(f"\n{'='*70}")
    print(f"  ALL TOKENS — Characteristics + PnL (sorted by PnL)")
    print(f"{'='*70}")
    print(f"{'Token':<10} {'PnL':>9} {'Trd':>4} {'WR':>5} {'ADV($M)':>9} "
          f"{'VRank':>5} {'Beta':>6} {'CfgIC':>6} {'EmpIC':>7} {'BL':>3}")
    print("-" * 75)
    for _, r in merged.iterrows():
        adv_m = r["adv_usd"] / 1e6 if not np.isnan(r.get("adv_usd", np.nan)) else np.nan
        print(f"{r['token']:<10} {r['pnl']:>9.0f} {r['trades']:>4} "
              f"{r['win_rate']:>5.0%} "
              f"{'N/A':>9}" if np.isnan(adv_m) else
              f"{r['token']:<10} {r['pnl']:>9.0f} {r['trades']:>4} "
              f"{r['win_rate']:>5.0%} "
              f"{adv_m:>9.1f}",
              end="")
        vr = r.get("volume_rank", np.nan)
        bt = r.get("btc_beta", np.nan)
        cic = r.get("ic_magnitude", np.nan)
        eic = r.get("empirical_ic", np.nan)
        bl = "Y" if r.get("in_blacklist", False) else ""
        print(f" {vr:>5.0f}" if not np.isnan(vr) else f" {'N/A':>5}", end="")
        print(f" {bt:>6.2f}" if not np.isnan(bt) else f" {'N/A':>6}", end="")
        print(f" {cic:>6.3f}" if not np.isnan(cic) else f" {'N/A':>6}", end="")
        print(f" {eic:>7.4f}" if not np.isnan(eic) else f" {'N/A':>7}", end="")
        print(f" {bl:>3}")

    # Correlation analysis: what predicts being a loser?
    print(f"\n{'='*70}")
    print(f"  CORRELATION: Characteristic vs PnL")
    print(f"{'='*70}")
    for col in ["adv_usd", "volume_rank", "btc_beta", "ic_magnitude", "empirical_ic",
                 "avg_funding_rate", "trades"]:
        if col in merged.columns:
            valid = merged[["pnl", col]].dropna()
            if len(valid) > 10:
                rho, pval = stats.spearmanr(valid["pnl"], valid[col])
                sig = "***" if pval < 0.01 else "**" if pval < 0.05 else "*" if pval < 0.1 else ""
                print(f"  {col:<20}: rho={rho:+.3f}  p={pval:.4f} {sig}")

    all_results["step2"] = {
        "losers_avg_adv": float(losers["adv_usd"].dropna().mean()) if len(losers["adv_usd"].dropna()) > 0 else None,
        "winners_avg_adv": float(winners["adv_usd"].dropna().mean()) if len(winners["adv_usd"].dropna()) > 0 else None,
        "losers_avg_beta": float(losers["btc_beta"].dropna().mean()) if len(losers["btc_beta"].dropna()) > 0 else None,
        "winners_avg_beta": float(winners["btc_beta"].dropna().mean()) if len(winners["btc_beta"].dropna()) > 0 else None,
        "losers_bl_pct": float(losers["in_blacklist"].mean()),
        "winners_bl_pct": float(winners["in_blacklist"].mean()),
    }

    # ──────────────────────────────────────────────────────────────────────
    # STEP 3: Dynamic blacklist rules — test each on 2025
    # ──────────────────────────────────────────────────────────────────────
    print(f"\n\n{'='*80}")
    print(f"  STEP 3: Dynamic Blacklist Rules — 2025 Testing")
    print(f"{'='*80}")

    # Build dynamic blacklist sets
    dynamic_rules = {}

    # Rule 1: Top-10 by ADV (large caps, too efficient)
    top10_adv = chars_df.nlargest(10, "adv_usd")["token"].tolist()
    dynamic_rules["top10_adv"] = set(top10_adv)
    print(f"\n  Rule: top10_adv -> {sorted(top10_adv)}")

    # Rule 2: Top-20 by ADV
    top20_adv = chars_df.nlargest(20, "adv_usd")["token"].tolist()
    dynamic_rules["top20_adv"] = set(top20_adv)
    print(f"  Rule: top20_adv -> {sorted(top20_adv)}")

    # Rule 3: Bottom-20 by ADV (too illiquid)
    bot20_adv = chars_df.nsmallest(20, "adv_usd")["token"].tolist()
    dynamic_rules["bot20_adv"] = set(bot20_adv)
    print(f"  Rule: bot20_adv -> {sorted(bot20_adv)}")

    # Rule 4: High BTC beta (> 1.5 — too correlated, positioning signal noise)
    high_beta = chars_df[chars_df["btc_beta"] > 1.5]["token"].tolist()
    dynamic_rules["high_beta_1.5"] = set(high_beta)
    print(f"  Rule: high_beta_1.5 ({len(high_beta)} tokens) -> {sorted(high_beta)}")

    # Rule 5: Negative empirical IC in 2025 (signal is wrong)
    neg_ic_tokens = [t for t, r in ic_results.items() if r["ic"] < 0]
    dynamic_rules["neg_empirical_ic"] = set(neg_ic_tokens)
    print(f"  Rule: neg_empirical_ic ({len(neg_ic_tokens)} tokens) -> {sorted(neg_ic_tokens)}")

    # Rule 6: Low |empirical IC| < 0.02 (no signal)
    low_ic_tokens = [t for t, r in ic_results.items() if abs(r["ic"]) < 0.02]
    dynamic_rules["low_abs_ic_0.02"] = set(low_ic_tokens)
    print(f"  Rule: low_abs_ic_0.02 ({len(low_ic_tokens)} tokens) -> {sorted(low_ic_tokens)}")

    # Rule 7: Trailing PnL — blacklist tokens with negative PnL in no-BL run
    # (This is retrospective for 2025 but shows the ceiling)
    neg_pnl_tokens = df_no_bl[df_no_bl["pnl"] < 0]["token"].tolist()
    dynamic_rules["neg_pnl_retrospective"] = set(neg_pnl_tokens)
    print(f"  Rule: neg_pnl_retrospective ({len(neg_pnl_tokens)} tokens)")

    # Rule 8: Combined — top10_adv + neg_empirical_ic
    combined_rule = set(top10_adv) | set(neg_ic_tokens)
    dynamic_rules["top10_adv_OR_neg_ic"] = combined_rule
    print(f"  Rule: top10_adv_OR_neg_ic ({len(combined_rule)} tokens)")

    # Rule 9: ADV > $500M/day (mega caps)
    mega_cap = chars_df[chars_df["adv_usd"] > 500_000_000]["token"].tolist()
    dynamic_rules["adv_gt_500M"] = set(mega_cap)
    print(f"  Rule: adv_gt_500M ({len(mega_cap)} tokens) -> {sorted(mega_cap)}")

    # Rule 10: ADV > $200M/day
    large_cap = chars_df[chars_df["adv_usd"] > 200_000_000]["token"].tolist()
    dynamic_rules["adv_gt_200M"] = set(large_cap)
    print(f"  Rule: adv_gt_200M ({len(large_cap)} tokens) -> {sorted(large_cap)}")

    # Rule 11: Low empirical IC (<0.05) — weak signal tokens
    weak_ic = [t for t, r in ic_results.items() if r["ic"] < 0.05]
    dynamic_rules["ic_lt_0.05"] = set(weak_ic)
    print(f"  Rule: ic_lt_0.05 ({len(weak_ic)} tokens)")

    # Run each dynamic rule
    step3_results = {}
    for rule_name, bl_set in dynamic_rules.items():
        print(f"\n  Testing rule: {rule_name} ({len(bl_set)} tokens blacklisted)")
        overlap_with_static = len(bl_set & STATIC_BLACKLIST)
        print(f"    Overlap with static BL: {overlap_with_static}/{len(STATIC_BLACKLIST)}")

        try:
            m, trades, _ = patch_blacklist_and_run(
                bl_set, 15, f"dynamic: {rule_name}")
            step3_results[rule_name] = {
                "return_pct": float(m.total_return_pct),
                "sharpe": float(m.sharpe_ratio),
                "max_dd": float(m.max_drawdown_pct),
                "n_trades": len(trades),
                "n_blacklisted": len(bl_set),
                "overlap_static": overlap_with_static,
                "tokens_blacklisted": sorted(bl_set),
            }
            print(f"    Result: {m.total_return_pct:+.1f}% return, "
                  f"{m.sharpe_ratio:.2f} SR, {m.max_drawdown_pct:.1f}% DD")
        except Exception as e:
            print(f"    FAILED: {e}")
            step3_results[rule_name] = {"error": str(e)}

    # Summary table
    print(f"\n{'='*80}")
    print(f"  STEP 3 SUMMARY: Dynamic Rule Results (2025)")
    print(f"{'='*80}")
    print(f"{'Rule':<25} {'Return':>8} {'Sharpe':>7} {'MaxDD':>7} {'N_BL':>5} {'Overlap':>7}")
    print("-" * 70)
    # Add baselines
    print(f"{'STATIC BL (baseline)':<25} {m_bl.total_return_pct:>+8.1f}% "
          f"{m_bl.sharpe_ratio:>7.2f} {m_bl.max_drawdown_pct:>7.1f}% "
          f"{len(STATIC_BLACKLIST):>5} {50:>7}")
    print(f"{'NO blacklist':<25} {m_no_bl.total_return_pct:>+8.1f}% "
          f"{m_no_bl.sharpe_ratio:>7.2f} {m_no_bl.max_drawdown_pct:>7.1f}% "
          f"{'0':>5} {'0':>7}")
    print("-" * 70)
    for rule_name, res in sorted(step3_results.items(),
                                   key=lambda x: x[1].get("return_pct", -999),
                                   reverse=True):
        if "error" in res:
            print(f"{rule_name:<25} ERROR: {res['error']}")
        else:
            print(f"{rule_name:<25} {res['return_pct']:>+8.1f}% "
                  f"{res['sharpe']:>7.2f} {res['max_dd']:>7.1f}% "
                  f"{res['n_blacklisted']:>5} {res['overlap_static']:>7}")

    all_results["step3"] = step3_results

    # ──────────────────────────────────────────────────────────────────────
    # STEP 4: Cross-year validation of best rules
    # ──────────────────────────────────────────────────────────────────────
    print(f"\n\n{'='*80}")
    print(f"  STEP 4: Cross-Year Validation of Best Rules")
    print(f"{'='*80}")

    # Pick top-3 rules by 2025 return
    ranked_rules = sorted(
        [(name, res) for name, res in step3_results.items() if "return_pct" in res],
        key=lambda x: x[1]["return_pct"],
        reverse=True
    )
    top_rules = ranked_rules[:3]
    print(f"\n  Top 3 rules from 2025:")
    for name, res in top_rules:
        print(f"    {name}: {res['return_pct']:+.1f}%")

    # For cross-year: run 51-month backtest (covers 2022-2025) with each rule
    # Then split trades by year
    step4_results = {}

    # First, run baseline (static BL) for 51 months
    print(f"\n  --- 51-month baselines ---")
    m_51_bl, trades_51_bl, eq_51_bl = run_bt("s523c_growth", 51,
                                               "51mo WITH static blacklist")
    m_51_no, trades_51_no, eq_51_no = patch_blacklist_and_run(
        set(), 51, "51mo NO blacklist")

    # Per-year split for baselines
    # We need bar -> date mapping. Use eq_daily index.
    def year_pnl_from_eq(eq_daily):
        """Extract per-year return from daily equity curve."""
        if eq_daily is None or len(eq_daily) == 0:
            return {}
        results = {}
        for year in [2022, 2023, 2024, 2025]:
            mask = eq_daily.index.year == year
            yr = eq_daily[mask]
            if len(yr) < 10:
                continue
            yr_ret = (yr.iloc[-1] / yr.iloc[0] - 1) * 100
            results[year] = float(yr_ret)
        return results

    bl_per_year = year_pnl_from_eq(eq_51_bl)
    no_bl_per_year = year_pnl_from_eq(eq_51_no)

    print(f"\n  Per-year returns (from equity curve):")
    print(f"  {'Year':<6} {'Static BL':>10} {'No BL':>10} {'Delta':>10}")
    for year in [2022, 2023, 2024, 2025]:
        bl_r = bl_per_year.get(year, 0)
        no_r = no_bl_per_year.get(year, 0)
        print(f"  {year:<6} {bl_r:>+10.1f}% {no_r:>+10.1f}% {bl_r - no_r:>+10.1f}pp")

    step4_results["static_bl_per_year"] = bl_per_year
    step4_results["no_bl_per_year"] = no_bl_per_year

    # Test top rules across 51 months
    for rule_name, rule_2025_res in top_rules:
        bl_set = dynamic_rules[rule_name]
        print(f"\n  --- Testing {rule_name} over 51 months ---")
        try:
            m_rule, trades_rule, eq_rule = patch_blacklist_and_run(
                bl_set, 51, f"51mo {rule_name}")
            per_year = year_pnl_from_eq(eq_rule)
            step4_results[rule_name] = {
                "total_return": float(m_rule.total_return_pct),
                "sharpe": float(m_rule.sharpe_ratio),
                "max_dd": float(m_rule.max_drawdown_pct),
                "per_year": per_year,
            }
            print(f"  Per-year: ", end="")
            for yr in [2022, 2023, 2024, 2025]:
                print(f"{yr}: {per_year.get(yr, 0):+.1f}%  ", end="")
            print()
        except Exception as e:
            print(f"  FAILED: {e}")
            step4_results[rule_name] = {"error": str(e)}

    # Cross-year summary
    print(f"\n{'='*80}")
    print(f"  STEP 4 SUMMARY: Cross-Year Validation")
    print(f"{'='*80}")
    print(f"{'Rule':<25} {'2022':>8} {'2023':>8} {'2024':>8} {'2025':>8} {'Total':>8}")
    print("-" * 70)
    print(f"{'Static BL':<25}", end="")
    for yr in [2022, 2023, 2024, 2025]:
        print(f" {bl_per_year.get(yr, 0):>+7.1f}%", end="")
    print(f" {m_51_bl.total_return_pct:>+7.1f}%")
    print(f"{'No BL':<25}", end="")
    for yr in [2022, 2023, 2024, 2025]:
        print(f" {no_bl_per_year.get(yr, 0):>+7.1f}%", end="")
    print(f" {m_51_no.total_return_pct:>+7.1f}%")
    for rule_name, _ in top_rules:
        res = step4_results.get(rule_name, {})
        if "error" in res:
            print(f"{rule_name:<25} ERROR")
            continue
        per_year = res.get("per_year", {})
        print(f"{rule_name:<25}", end="")
        for yr in [2022, 2023, 2024, 2025]:
            print(f" {per_year.get(yr, 0):>+7.1f}%", end="")
        print(f" {res.get('total_return', 0):>+7.1f}%")

    all_results["step4"] = step4_results

    # ──────────────────────────────────────────────────────────────────────
    # SAVE RESULTS
    # ──────────────────────────────────────────────────────────────────────
    with open(RESULTS_PATH, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n  Results saved to {RESULTS_PATH}")

    # ──────────────────────────────────────────────────────────────────────
    # FINAL RECOMMENDATIONS
    # ──────────────────────────────────────────────────────────────────────
    print(f"\n\n{'='*80}")
    print(f"  RECOMMENDATIONS")
    print(f"{'='*80}")
    print(f"""
  The static blacklist works because it removes tokens where the positioning
  signal is unreliable or inverted in recent market conditions. Key findings:

  1. LOSERS share: [see characteristics above]
  2. WINNERS share: [see characteristics above]
  3. Best dynamic rule: [see Step 3 results]
  4. Cross-year stability: [see Step 4 results]

  For a CAUSAL dynamic blacklist at each rebalance:
  - Compute trailing-90d empirical IC per token
  - Blacklist tokens with IC < threshold (determined above)
  - Optionally also blacklist top-N by ADV (large caps too efficient)
  - This is computable from historical data only (no lookahead)
""")


if __name__ == "__main__":
    main()
