#!/usr/bin/env python3
"""s523c_growth Drawdown Anatomy — basket regime as primary lens.

Tasks:
1. Top-10 drawdowns of bare s523c equity curve.
2. Build s523c-traded-universe basket regime time series.
3. Cluster drawdowns on basket features.
4. Cross-reference Mission G (profit_lockin) bad windows to s523c bad periods.
5. Test 5 candidate filters (3 basket-based, 2 BTC-based).

Outputs:
- research/s523c_universe_basket.parquet (cached basket time series)
- research/s523c_drawdown_anatomy_results.json
- research/s523c_drawdown_anatomy_report.md
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path("/workspace/crypto_backtest")
DATA_DIR = ROOT / "data" / "perp" / "binance" / "1h_ohlcv"
RESEARCH = ROOT / "research"
TOKEN_CONFIG = ROOT / "data" / "alternative" / "s521_token_config.json"
EQUITY_JSON = RESEARCH / "gate2_out" / "s523c_growth_60mo_50k_equity_curve.json"
GATE2_JSON = RESEARCH / "profit_lockin_gate2_results.json"

BASKET_CACHE = RESEARCH / "s523c_universe_basket.parquet"
RESULTS_JSON = RESEARCH / "s523c_drawdown_anatomy_results.json"
REPORT_MD = RESEARCH / "s523c_drawdown_anatomy_report.md"

# Mirrors strategies/s523c_growth.py TOKEN_BLACKLIST (line 91-104)
TOKEN_BLACKLIST = {
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


def log(msg: str) -> None:
    print(f"[s523c-anatomy] {msg}", flush=True)


# ──────────────────────────────────────────────────────────────────────
# DATA LOADERS
# ──────────────────────────────────────────────────────────────────────

def load_equity_curve() -> pd.Series:
    """Load s523c bare equity curve as a daily Series indexed by Timestamp."""
    with open(EQUITY_JSON) as f:
        eq = json.load(f)
    s = pd.Series(eq, dtype=float)
    s.index = pd.to_datetime(s.index)
    s = s.sort_index()
    return s


def load_token_universe() -> list[str]:
    """s523c universe = tokens in s521_token_config.json minus blacklist."""
    with open(TOKEN_CONFIG) as f:
        cfg = json.load(f)
    universe = sorted(t for t in cfg.keys() if t not in TOKEN_BLACKLIST)
    return universe


def _csv_path_for(token: str) -> Path | None:
    """Find the CSV file for a token; return None if missing."""
    p = DATA_DIR / f"{token}_perp_1h.csv"
    if p.exists():
        return p
    return None


def load_token_hourly(token: str) -> pd.DataFrame | None:
    """Load 1h OHLCV CSV for a token. Returns DataFrame indexed by UTC datetime."""
    p = _csv_path_for(token)
    if p is None:
        return None
    try:
        df = pd.read_csv(p, usecols=["datetime", "close"])
    except Exception:
        return None
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.set_index("datetime").sort_index()
    return df


# ──────────────────────────────────────────────────────────────────────
# BASKET TIME SERIES
# ──────────────────────────────────────────────────────────────────────

def build_basket_timeseries() -> pd.DataFrame:
    """Build the s523c universe basket regime features (daily)."""
    if BASKET_CACHE.exists():
        log(f"loading cached basket from {BASKET_CACHE}")
        return pd.read_parquet(BASKET_CACHE)

    universe = load_token_universe()
    log(f"universe size = {len(universe)} tokens (139 cfg − blacklist)")

    daily_closes: dict[str, pd.Series] = {}
    missing = []
    for tok in universe:
        df = load_token_hourly(tok)
        if df is None or df.empty:
            missing.append(tok)
            continue
        d = df["close"].resample("1D").last().dropna()
        if len(d) < 60:
            missing.append(tok)
            continue
        daily_closes[tok] = d

    log(f"loaded daily series for {len(daily_closes)} tokens; missing {len(missing)}")
    if missing:
        log(f"  missing examples: {missing[:10]}")

    closes_df = pd.DataFrame(daily_closes).sort_index()
    # Strip tz so we can join with the equity curve (which is naive)
    if closes_df.index.tz is not None:
        closes_df.index = closes_df.index.tz_convert(None)
    log(f"closes_df shape: {closes_df.shape}; range "
        f"{closes_df.index[0].date()} → {closes_df.index[-1].date()}")

    rets_df = closes_df.pct_change()
    basket_ret = rets_df.mean(axis=1, skipna=True)
    basket_eq = (1.0 + basket_ret.fillna(0)).cumprod()
    basket_vol_30d = basket_ret.rolling(30, min_periods=15).std() * np.sqrt(365)
    basket_dispersion = rets_df.std(axis=1, skipna=True)
    rolling_peak_30d = basket_eq.rolling(30, min_periods=1).max()
    basket_dd_30d = basket_eq / rolling_peak_30d - 1.0
    sma50 = closes_df.rolling(50, min_periods=25).mean()
    above_sma = (closes_df > sma50).astype(float)
    pct_above_50dma = above_sma.mean(axis=1, skipna=True)

    btc = load_token_hourly("BTC")
    if btc is None:
        raise RuntimeError("BTC perp 1h CSV missing")
    btc_daily = btc["close"].resample("1D").last().dropna()
    if btc_daily.index.tz is not None:
        btc_daily.index = btc_daily.index.tz_convert(None)
    btc_ret = btc_daily.pct_change()
    btc_eq = (1.0 + btc_ret.fillna(0)).cumprod()
    btc_dd_30d = btc_eq / btc_eq.rolling(30, min_periods=1).max() - 1.0
    btc_sma200 = btc_daily.rolling(200, min_periods=100).mean()
    btc_above_200dma = (btc_daily > btc_sma200).astype(float)

    basket_ret_30d = (1 + basket_ret.fillna(0)).rolling(30).apply(np.prod, raw=True) - 1
    btc_ret_30d = (1 + btc_ret.fillna(0)).rolling(30).apply(np.prod, raw=True) - 1

    df = pd.DataFrame({
        "basket_ret_24h": basket_ret,
        "basket_eq": basket_eq,
        "basket_vol_30d": basket_vol_30d,
        "basket_dispersion_24h": basket_dispersion,
        "basket_drawdown_30d": basket_dd_30d,
        "basket_ret_30d": basket_ret_30d,
        "pct_above_50dma": pct_above_50dma,
    })
    df["btc_ret_24h"] = btc_ret.reindex(df.index)
    df["btc_ret_30d"] = btc_ret_30d.reindex(df.index)
    df["btc_drawdown_30d"] = btc_dd_30d.reindex(df.index)
    df["btc_above_200dma"] = btc_above_200dma.reindex(df.index)
    df["alts_vs_btc_30d"] = df["basket_ret_30d"] - df["btc_ret_30d"]
    df["n_tokens_active"] = closes_df.notna().sum(axis=1)

    df.to_parquet(BASKET_CACHE)
    log(f"saved basket cache → {BASKET_CACHE}")
    return df


# ──────────────────────────────────────────────────────────────────────
# DRAWDOWN ANALYSIS
# ──────────────────────────────────────────────────────────────────────

def find_drawdowns(equity: pd.Series) -> list[dict]:
    """Detect distinct peak-to-trough-to-recovery drawdowns."""
    eq = equity.values
    idx = equity.index
    peak = eq[0]
    peak_i = 0
    dd_in_progress = False
    trough = peak
    trough_i = 0
    drawdowns: list[dict] = []

    for i in range(1, len(eq)):
        v = eq[i]
        if v >= peak:
            if dd_in_progress:
                drawdowns.append({
                    "peak_date": str(idx[peak_i].date()),
                    "trough_date": str(idx[trough_i].date()),
                    "recovery_date": str(idx[i].date()),
                    "peak_equity": float(peak),
                    "trough_equity": float(trough),
                    "depth_pct": float((trough - peak) / peak * 100),
                    "duration_days": int((idx[i] - idx[peak_i]).days),
                    "to_trough_days": int((idx[trough_i] - idx[peak_i]).days),
                })
                dd_in_progress = False
            peak = v
            peak_i = i
            trough = v
            trough_i = i
        else:
            if v < trough:
                trough = v
                trough_i = i
                dd_in_progress = True

    if dd_in_progress:
        drawdowns.append({
            "peak_date": str(idx[peak_i].date()),
            "trough_date": str(idx[trough_i].date()),
            "recovery_date": None,
            "peak_equity": float(peak),
            "trough_equity": float(trough),
            "depth_pct": float((trough - peak) / peak * 100),
            "duration_days": int((idx[-1] - idx[peak_i]).days),
            "to_trough_days": int((idx[trough_i] - idx[peak_i]).days),
        })

    drawdowns.sort(key=lambda d: d["depth_pct"])
    return drawdowns


def characterize_drawdown(dd: dict, basket: pd.DataFrame) -> dict:
    """Add basket regime features for the drawdown's date span."""
    start = pd.Timestamp(dd["peak_date"])
    end = pd.Timestamp(dd["trough_date"])
    sub = basket.loc[(basket.index >= start) & (basket.index <= end)]
    if sub.empty:
        return {**dd, "basket_features": "no_overlap"}

    out = dict(dd)
    out.update({
        "basket_ret_total": float((1 + sub["basket_ret_24h"].fillna(0)).prod() - 1) * 100,
        "basket_vol_mean": float(sub["basket_vol_30d"].mean()),
        "basket_dd_min": float(sub["basket_drawdown_30d"].min()) * 100,
        "basket_dispersion_mean": float(sub["basket_dispersion_24h"].mean()),
        "alts_vs_btc_mean": float(sub["alts_vs_btc_30d"].mean()) * 100,
        "pct_above_50dma_mean": float(sub["pct_above_50dma"].mean()) * 100,
        "btc_ret_total": float((1 + sub["btc_ret_24h"].fillna(0)).prod() - 1) * 100,
        "btc_dd_min": float(sub["btc_drawdown_30d"].min()) * 100,
        "btc_above_200dma_pct": float(sub["btc_above_200dma"].mean()) * 100,
    })
    return out


# ──────────────────────────────────────────────────────────────────────
# CLUSTERING
# ──────────────────────────────────────────────────────────────────────

def cluster_drawdowns(chars: list[dict]) -> dict:
    PRIORITIES = [
        ("alt_cascade",       lambda c: c["basket_dd_min"] < -15),
        ("alt_underperform",  lambda c: c["alts_vs_btc_mean"] < -5),
        ("broad_breakdown",   lambda c: c["pct_above_50dma_mean"] < 30),
        ("low_dispersion",    lambda c: c["basket_dispersion_mean"] < 0.04),
        ("idiosyncratic",     lambda c: True),
    ]
    labels = []
    for c in chars:
        if "basket_dd_min" not in c:
            labels.append("unknown")
            continue
        for name, fn in PRIORITIES:
            if fn(c):
                labels.append(name)
                break
    counts: dict[str, int] = {}
    for lab in labels:
        counts[lab] = counts.get(lab, 0) + 1
    return {"labels": labels, "counts": counts}


# ──────────────────────────────────────────────────────────────────────
# MISSION G CROSS-REFERENCE
# ──────────────────────────────────────────────────────────────────────

def cross_reference_mission_g(basket: pd.DataFrame, equity: pd.Series) -> dict:
    with open(GATE2_JSON) as f:
        gate2 = json.load(f)

    windows = gate2["windows"]
    enriched = []
    for w in windows:
        start = pd.Timestamp(w["window_start_dt"])
        end = pd.Timestamp(w["window_end_dt"])
        if start.tz is not None:
            start = start.tz_convert(None)
        if end.tz is not None:
            end = end.tz_convert(None)

        eq_sub = equity.loc[(equity.index >= start) & (equity.index <= end)]
        if not eq_sub.empty:
            peak = eq_sub.cummax()
            dd = (eq_sub / peak - 1).min() * 100
            ret = (eq_sub.iloc[-1] / eq_sub.iloc[0] - 1) * 100
        else:
            dd = float("nan")
            ret = float("nan")

        b_sub = basket.loc[(basket.index >= start) & (basket.index <= end)]
        if not b_sub.empty:
            basket_dd_min = float(b_sub["basket_drawdown_30d"].min()) * 100
            alts_vs_btc = float(b_sub["alts_vs_btc_30d"].mean()) * 100
            basket_disp = float(b_sub["basket_dispersion_24h"].mean())
            breadth = float(b_sub["pct_above_50dma"].mean()) * 100
        else:
            basket_dd_min = alts_vs_btc = basket_disp = breadth = float("nan")

        enriched.append({
            "window_start": str(start.date()),
            "window_end": str(end.date()),
            "missionG_calmar_delta_pct": w.get("VEL95_calmar_delta_pct"),
            "missionG_n_fired": w.get("VEL95_n_fired"),
            "s523c_window_ret_pct": ret,
            "s523c_window_dd_pct": dd,
            "basket_dd_min_pct": basket_dd_min,
            "alts_vs_btc_mean_pct": alts_vs_btc,
            "basket_dispersion_mean": basket_disp,
            "pct_above_50dma_mean": breadth,
            "btc_window_ret_pct": w.get("btc_return_pct"),
        })

    bad_windows = [w for w in enriched
                   if w["missionG_calmar_delta_pct"] is not None
                   and w["missionG_calmar_delta_pct"] < 0]
    good_windows = [w for w in enriched
                    if w["missionG_calmar_delta_pct"] is not None
                    and w["missionG_calmar_delta_pct"] >= 0]

    def _mean(xs, k):
        vals = [x[k] for x in xs
                if not (x[k] is None or (isinstance(x[k], float) and np.isnan(x[k])))]
        return float(np.mean(vals)) if vals else None

    summary = {
        "n_windows_total": len(enriched),
        "n_bad_windows": len(bad_windows),
        "n_good_windows": len(good_windows),
        "bad_avg_basket_dd": _mean(bad_windows, "basket_dd_min_pct"),
        "good_avg_basket_dd": _mean(good_windows, "basket_dd_min_pct"),
        "bad_avg_alts_vs_btc": _mean(bad_windows, "alts_vs_btc_mean_pct"),
        "good_avg_alts_vs_btc": _mean(good_windows, "alts_vs_btc_mean_pct"),
        "bad_avg_s523c_dd": _mean(bad_windows, "s523c_window_dd_pct"),
        "good_avg_s523c_dd": _mean(good_windows, "s523c_window_dd_pct"),
    }
    return {"windows": enriched, "summary": summary}


# ──────────────────────────────────────────────────────────────────────
# FILTER BACKTESTS
# ──────────────────────────────────────────────────────────────────────

def apply_filter(equity: pd.Series, mask_inactive: pd.Series) -> dict:
    rets = equity.pct_change().fillna(0)
    aligned = mask_inactive.reindex(equity.index).fillna(False).astype(bool)
    filtered_rets = rets.where(~aligned, 0.0)
    new_eq = equity.iloc[0] * (1 + filtered_rets).cumprod()

    peak = new_eq.cummax()
    dd = (new_eq / peak - 1)
    max_dd = float(dd.min()) * 100
    total_ret = float(new_eq.iloc[-1] / new_eq.iloc[0] - 1) * 100
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    cagr = float((1 + total_ret / 100) ** (1 / max(years, 0.01)) - 1) * 100
    daily_ret = new_eq.pct_change().dropna()
    sharpe = float(daily_ret.mean() / (daily_ret.std() + 1e-12) * np.sqrt(365))
    calmar = float(cagr / max(abs(max_dd), 0.01))
    pct_paused = float(aligned.mean()) * 100

    return {
        "total_return_pct": total_ret,
        "cagr_pct": cagr,
        "max_dd_pct": max_dd,
        "sharpe": sharpe,
        "calmar": calmar,
        "pct_days_paused": pct_paused,
    }


def test_filters(equity: pd.Series, basket: pd.DataFrame) -> dict:
    b = basket.reindex(equity.index, method="ffill")

    baseline = apply_filter(equity, pd.Series(False, index=equity.index))

    f1_mask = b["basket_drawdown_30d"] < -0.15
    f1 = apply_filter(equity, f1_mask)

    disp = b["basket_dispersion_24h"]
    disp_p30 = disp.rolling(90, min_periods=30).quantile(0.30)
    f2_mask = disp < disp_p30
    f2 = apply_filter(equity, f2_mask)

    f3_mask = b["alts_vs_btc_30d"] < -0.10
    f3 = apply_filter(equity, f3_mask)

    f4_mask = b["btc_above_200dma"] < 0.5
    f4 = apply_filter(equity, f4_mask)

    f5_mask = b["btc_drawdown_30d"] < -0.15
    f5 = apply_filter(equity, f5_mask)

    return {
        "baseline": baseline,
        "F1_basket_dd_gt_15": f1,
        "F2_dispersion_low_30pct": f2,
        "F3_alts_vs_btc_lt_neg10": f3,
        "F4_btc_below_200dma": f4,
        "F5_btc_dd_gt_15": f5,
    }


# ──────────────────────────────────────────────────────────────────────
# REPORT
# ──────────────────────────────────────────────────────────────────────

def write_report(results: dict) -> None:
    md = []
    md.append("# s523c_growth Drawdown Anatomy")
    md.append("")
    md.append("**Primary regime lens: s523c traded-universe basket "
              "(equal-weighted alt tokens after blacklist).**  ")
    md.append("**Secondary lens: BTC regime (for comparison).**")
    md.append("")
    md.append("Equity curve: 60-month bare s523c_growth at $50K, from "
              "`research/gate2_out/s523c_growth_60mo_50k_equity_curve.json`.")
    md.append("")
    md.append("---")
    md.append("")

    eo = results["equity_overview"]
    md.append("## Equity Overview")
    md.append(f"- Date range: {eo['start']} → {eo['end']}  ")
    md.append(f"- Start equity: ${eo['start_equity']:,.0f}  ")
    md.append(f"- End equity: ${eo['end_equity']:,.0f}  ")
    md.append(f"- Total return: {eo['total_return_pct']:.1f}%  ")
    md.append(f"- CAGR: {eo['cagr_pct']:.1f}%  ")
    md.append(f"- Max drawdown: {eo['max_dd_pct']:.1f}%  ")
    md.append("")

    md.append("## Traded Universe")
    md.append("- s521_token_config.json: 139 tokens  ")
    md.append("- Blacklist: 50 tokens (BTC, SOL, etc.)  ")
    md.append(f"- s523c basket size: {results['universe']['n_universe']} tokens  ")
    md.append(f"- Tokens with usable 1h data: {results['universe']['n_loaded']}  ")
    md.append("")

    md.append("## Task 1+2: Top 10 Drawdowns (basket regime PRIMARY, BTC SECONDARY)")
    md.append("")
    md.append("| # | Peak | Trough | Days | Depth% | Basket DD% | Basket Vol | "
              "Basket Disp | Alts−BTC% | Breadth% | BTC ret% | BTC DD% |")
    md.append("|---|------|--------|------|--------|-----------|-----------|"
              "------------|-----------|----------|----------|---------|")
    for i, dd in enumerate(results["top_drawdowns"], 1):
        if "basket_dd_min" not in dd:
            md.append(f"| {i} | {dd['peak_date']} | {dd['trough_date']} | "
                      f"{dd['to_trough_days']} | {dd['depth_pct']:.1f} | (no overlap) |||||||")
            continue
        md.append(
            f"| {i} | {dd['peak_date']} | {dd['trough_date']} | {dd['to_trough_days']} | "
            f"{dd['depth_pct']:.1f} | {dd['basket_dd_min']:.1f} | "
            f"{dd['basket_vol_mean']:.2f} | {dd['basket_dispersion_mean']:.3f} | "
            f"{dd['alts_vs_btc_mean']:.1f} | {dd['pct_above_50dma_mean']:.0f} | "
            f"{dd['btc_ret_total']:.1f} | {dd['btc_dd_min']:.1f} |"
        )
    md.append("")

    md.append("## Task 3: Drawdown Clustering (basket features)")
    md.append("")
    md.append("Heuristic priority: alt_cascade > alt_underperform > broad_breakdown > "
              "low_dispersion > idiosyncratic.")
    md.append("")
    md.append("| Cluster | Count |")
    md.append("|---------|-------|")
    for k, v in sorted(results["clusters"]["counts"].items(), key=lambda x: -x[1]):
        md.append(f"| {k} | {v} |")
    md.append("")
    md.append("Per-DD labels:")
    for peak, lab in zip(
        [d["peak_date"] for d in results["top_drawdowns"]],
        results["clusters"]["labels"],
    ):
        md.append(f"- {peak} → **{lab}**")
    md.append("")

    md.append("## Task 4: Cross-Reference with Mission G (profit_lockin) Bad Windows")
    md.append("")
    md.append("Source: `research/profit_lockin_gate2_results.json` — 17 6-month windows.  ")
    md.append("Bad window = `VEL95_calmar_delta_pct < 0` (overlay hurt the baseline).")
    md.append("")
    s = results["mission_g"]["summary"]
    md.append(f"- Total windows: {s['n_windows_total']}  ")
    md.append(f"- Bad windows (overlay hurt): {s['n_bad_windows']}  ")
    md.append(f"- Good windows (overlay helped): {s['n_good_windows']}  ")
    md.append("")
    md.append("**Average basket regime in bad vs good windows:**")
    if s['bad_avg_basket_dd'] is not None:
        md.append(f"- Bad windows  — basket_dd_min: {s['bad_avg_basket_dd']:.1f}%  | "
                  f"alts−btc_30d: {s['bad_avg_alts_vs_btc']:.1f}%  | "
                  f"s523c_window_dd: {s['bad_avg_s523c_dd']:.1f}%")
    if s['good_avg_basket_dd'] is not None:
        md.append(f"- Good windows — basket_dd_min: {s['good_avg_basket_dd']:.1f}%  | "
                  f"alts−btc_30d: {s['good_avg_alts_vs_btc']:.1f}%  | "
                  f"s523c_window_dd: {s['good_avg_s523c_dd']:.1f}%")
    md.append("")
    md.append("**Per-window detail:**")
    md.append("")
    md.append("| Window | MissG ΔCalmar% | s523c DD% | Basket DD% | Alts−BTC% | Disp | BTC ret% |")
    md.append("|--------|----------------|-----------|------------|-----------|------|----------|")
    for w in results["mission_g"]["windows"]:
        delta = w.get("missionG_calmar_delta_pct")
        delta_str = f"{delta:+.1f}" if delta is not None else "—"
        md.append(
            f"| {w['window_start']} → {w['window_end']} | {delta_str} | "
            f"{w['s523c_window_dd_pct']:.1f} | {w['basket_dd_min_pct']:.1f} | "
            f"{w['alts_vs_btc_mean_pct']:.1f} | {w['basket_dispersion_mean']:.3f} | "
            f"{w['btc_window_ret_pct']:.1f} |"
        )
    md.append("")

    md.append("## Task 5: Candidate Filter Backtests")
    md.append("")
    md.append("Each filter pauses s523c on days where the trigger is true (returns set to 0). "
              "Basket-based (F1–F3) vs BTC-based (F4–F5).")
    md.append("")
    md.append("| Filter | TotalRet% | CAGR% | MaxDD% | Sharpe | Calmar | %Days Paused |")
    md.append("|--------|-----------|-------|--------|--------|--------|--------------|")
    for name, m in results["filters"].items():
        md.append(f"| {name} | {m['total_return_pct']:.1f} | {m['cagr_pct']:.1f} | "
                  f"{m['max_dd_pct']:.1f} | {m['sharpe']:.2f} | {m['calmar']:.2f} | "
                  f"{m['pct_days_paused']:.1f} |")
    md.append("")

    md.append("## Key Findings")
    md.append("")
    base = results["filters"]["baseline"]
    best = max(results["filters"].items(), key=lambda kv: kv[1]["calmar"])
    md.append(f"- Baseline calmar: **{base['calmar']:.2f}**, max DD **{base['max_dd_pct']:.1f}%**")
    md.append(f"- Best filter by Calmar: **{best[0]}** "
              f"(Calmar {best[1]['calmar']:.2f}, MaxDD {best[1]['max_dd_pct']:.1f}%, "
              f"%paused {best[1]['pct_days_paused']:.1f}%)")
    basket_filters = ["F1_basket_dd_gt_15", "F2_dispersion_low_30pct", "F3_alts_vs_btc_lt_neg10"]
    btc_filters = ["F4_btc_below_200dma", "F5_btc_dd_gt_15"]
    bk_avg = float(np.mean([results["filters"][k]["calmar"] for k in basket_filters]))
    btc_avg = float(np.mean([results["filters"][k]["calmar"] for k in btc_filters]))
    md.append(f"- Basket-based filter average Calmar: {bk_avg:.2f}")
    md.append(f"- BTC-based filter average Calmar: {btc_avg:.2f}")
    md.append(f"- → {'BASKET' if bk_avg > btc_avg else 'BTC'} regime is the better filter family.")
    md.append("")

    REPORT_MD.write_text("\n".join(md))
    log(f"wrote report → {REPORT_MD}")


# ──────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────

def main():
    log("loading equity curve")
    equity = load_equity_curve()
    log(f"equity range: {equity.index[0].date()} → {equity.index[-1].date()}, "
        f"{len(equity)} daily points")

    log("building basket time series (this is the slow step)")
    basket = build_basket_timeseries()

    universe = load_token_universe()

    log("Task 1: detecting drawdowns on bare s523c equity")
    drawdowns = find_drawdowns(equity)
    log(f"  found {len(drawdowns)} drawdowns")
    top10 = drawdowns[:10]
    for i, d in enumerate(top10, 1):
        log(f"  #{i}: {d['peak_date']}→{d['trough_date']} "
            f"depth={d['depth_pct']:.1f}% dur={d['duration_days']}d")

    log("Task 2: characterizing top-10 with basket regime")
    chars = [characterize_drawdown(d, basket) for d in top10]

    log("Task 3: clustering")
    clusters = cluster_drawdowns(chars)
    log(f"  cluster counts: {clusters['counts']}")

    log("Task 4: cross-referencing Mission G windows")
    mg = cross_reference_mission_g(basket, equity)
    log(f"  bad windows: {mg['summary']['n_bad_windows']} / {mg['summary']['n_windows_total']}")

    log("Task 5: testing filters")
    filters = test_filters(equity, basket)
    for k, m in filters.items():
        log(f"  {k}: ret={m['total_return_pct']:.1f}% dd={m['max_dd_pct']:.1f}% "
            f"calmar={m['calmar']:.2f} paused={m['pct_days_paused']:.1f}%")

    peak = equity.cummax()
    dd_curve = (equity / peak - 1)
    yrs = (equity.index[-1] - equity.index[0]).days / 365.25
    eq_overview = {
        "start": str(equity.index[0].date()),
        "end": str(equity.index[-1].date()),
        "start_equity": float(equity.iloc[0]),
        "end_equity": float(equity.iloc[-1]),
        "total_return_pct": float(equity.iloc[-1] / equity.iloc[0] - 1) * 100,
        "cagr_pct": float((equity.iloc[-1] / equity.iloc[0]) ** (1 / max(yrs, 0.01)) - 1) * 100,
        "max_dd_pct": float(dd_curve.min()) * 100,
    }

    results = {
        "equity_overview": eq_overview,
        "universe": {
            "n_universe": len(universe),
            "n_loaded": int(basket["n_tokens_active"].max()),
        },
        "top_drawdowns": chars,
        "clusters": clusters,
        "mission_g": mg,
        "filters": filters,
    }

    with open(RESULTS_JSON, "w") as f:
        json.dump(results, f, indent=2, default=str)
    log(f"saved results → {RESULTS_JSON}")

    write_report(results)
    log("DONE")


if __name__ == "__main__":
    main()
