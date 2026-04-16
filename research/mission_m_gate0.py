"""
Mission M Gate 0 — Diagnostic only (no rule testing).

Question: does s523c's composite signal at +3d/+5d/+7d post-entry, combined with
current trade P&L, predict the eventual trade outcome?

Interpretation B (defensive): close trades only if BOTH signal degraded AND
trade currently underwater. This protects winners with weakened signals.

Reads clean 12-month s523c trade log + recomputes composite signals via
s523c._load_daily_signals(). Outputs correlations + 4-bucket conditional
analysis. PASS / KILL / INCONCLUSIVE verdict.
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# --- repo paths ---
sys.path.insert(0, "/workspace/crypto_backtest/v4")   # for engine.py
sys.path.insert(0, "/workspace/crypto_backtest")      # for strategies/

import strategies.s523c_growth as s523c  # noqa: E402

REPO = Path("/workspace/crypto_backtest")
TRADES_PATH = REPO / "results/v4/s523c_growth_12mo_50k_trades.json"
OHLCV_DIR = REPO / "data/perp/binance/1h_ohlcv"
OUT_REPORT = REPO / "research/mission_m_gate0_report.md"
OUT_RESULTS = REPO / "research/mission_m_gate0_results.json"

BACKTEST_END = pd.Timestamp("2026-04-05 16:00", tz="UTC")
BACKTEST_START = BACKTEST_END - pd.Timedelta(days=365)

OFFSETS_DAYS = [1, 3, 5, 7, 14]


def load_ohlcv(token: str) -> pd.DataFrame | None:
    path = OHLCV_DIR / f"{token}_perp_1h.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path, parse_dates=["datetime"]).set_index("datetime").sort_index()
    return df


def load_composite(token: str) -> pd.Series | None:
    symbol = f"{token}USDT"
    try:
        s523c._load_daily_signals(symbol, token)
    except Exception:
        return None
    s = s523c._composite_cache.get(symbol)
    if s is None or len(s) == 0:
        return None
    # Make tz-naive for date comparisons
    s = s.copy()
    s.index = pd.to_datetime(s.index).tz_localize(None) if s.index.tz is None else pd.to_datetime(s.index).tz_convert(None).tz_localize(None)
    return s


def main() -> None:
    print("Loading trade log…")
    with open(TRADES_PATH) as f:
        trades_raw = json.load(f)
    print(f"  {len(trades_raw)} trades")

    # caches
    ohlcv_cache: dict[str, pd.DataFrame | None] = {}
    composite_cache: dict[str, pd.Series | None] = {}

    rows: list[dict] = []
    skipped = 0
    skip_reasons: dict[str, int] = {}

    for tr in trades_raw:
        token = tr["token"]
        direction = int(tr["direction"])
        entry_bar = int(tr["entry_bar"])
        exit_bar = int(tr["exit_bar"])
        entry_price = float(tr["entry_price"])
        exit_price = float(tr["exit_price"])
        pnl_dollars = float(tr["pnl"])
        margin = float(tr["margin_usd"])

        entry_dt = BACKTEST_START + pd.Timedelta(hours=entry_bar)
        exit_dt = BACKTEST_START + pd.Timedelta(hours=exit_bar)

        # --- per-token data ---
        if token not in ohlcv_cache:
            ohlcv_cache[token] = load_ohlcv(token)
        ohlcv = ohlcv_cache[token]
        if ohlcv is None:
            skipped += 1
            skip_reasons["no_ohlcv"] = skip_reasons.get("no_ohlcv", 0) + 1
            continue

        if token not in composite_cache:
            composite_cache[token] = load_composite(token)
        comp = composite_cache[token]
        if comp is None:
            skipped += 1
            skip_reasons["no_composite"] = skip_reasons.get("no_composite", 0) + 1
            continue

        # --- normalize entry datetime to date for daily composite lookup ---
        entry_date = entry_dt.tz_localize(None).normalize()  # midnight UTC of entry day

        if entry_date not in comp.index:
            # try forward fill: take last available <= entry_date
            try:
                ix = comp.index.get_indexer([entry_date], method="pad")[0]
                if ix < 0:
                    skipped += 1
                    skip_reasons["entry_pre_history"] = skip_reasons.get("entry_pre_history", 0) + 1
                    continue
                entry_comp = float(comp.iloc[ix])
            except Exception:
                skipped += 1
                skip_reasons["entry_lookup_failed"] = skip_reasons.get("entry_lookup_failed", 0) + 1
                continue
        else:
            entry_comp = float(comp.loc[entry_date])

        if not np.isfinite(entry_comp):
            skipped += 1
            skip_reasons["entry_comp_nan"] = skip_reasons.get("entry_comp_nan", 0) + 1
            continue

        # --- direction-aware signal strength (positive = thesis intact) ---
        entry_strength = entry_comp * direction

        # --- eventual P&L pct (on margin, leverage applied) ---
        # use trade pnl on margin to be consistent with strategy book
        eventual_pnl_pct = pnl_dollars / margin if margin > 0 else 0.0
        eventual_loser = pnl_dollars < 0

        # --- forward signal lookups + current price at +Nd ---
        row: dict = {
            "token": token,
            "direction": direction,
            "entry_dt": entry_dt,
            "exit_dt": exit_dt,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "hold_hours": int(tr["hold_hours"]),
            "exit_reason": tr["exit_reason"],
            "pnl_dollars": pnl_dollars,
            "margin_usd": margin,
            "eventual_pnl_pct": eventual_pnl_pct,
            "eventual_loser": int(eventual_loser),
            "entry_comp": entry_comp,
            "entry_strength": entry_strength,
        }

        ohlcv_naive = ohlcv.copy()
        ohlcv_naive.index = ohlcv_naive.index.tz_convert(None) if ohlcv_naive.index.tz is not None else ohlcv_naive.index

        for d in OFFSETS_DAYS:
            target_dt = entry_dt + pd.Timedelta(days=d)
            # clamp to exit_dt — if trade closed before +Nd, this offset is N/A
            if target_dt > exit_dt:
                row[f"strength_+{d}d"] = np.nan
                row[f"current_pnl_pct_+{d}d"] = np.nan
                row[f"current_underwater_+{d}d"] = np.nan
                row[f"strength_change_+{d}d"] = np.nan
                row[f"strength_flipped_+{d}d"] = np.nan
                continue

            # composite at +Nd (use last available <= target_dt at daily granularity)
            target_date = target_dt.tz_localize(None).normalize()
            try:
                ix = comp.index.get_indexer([target_date], method="pad")[0]
                if ix < 0:
                    row[f"strength_+{d}d"] = np.nan
                else:
                    c = float(comp.iloc[ix])
                    row[f"strength_+{d}d"] = c * direction if np.isfinite(c) else np.nan
            except Exception:
                row[f"strength_+{d}d"] = np.nan

            # current price at +Nd
            try:
                ix = ohlcv_naive.index.get_indexer([target_dt.tz_localize(None)], method="pad")[0]
                if ix < 0:
                    row[f"current_pnl_pct_+{d}d"] = np.nan
                    row[f"current_underwater_+{d}d"] = np.nan
                else:
                    cur_price = float(ohlcv_naive.iloc[ix]["close"])
                    # unleveraged price return × direction (so positive = trade in profit)
                    cur_ret = (cur_price - entry_price) / entry_price * direction
                    row[f"current_pnl_pct_+{d}d"] = cur_ret
                    row[f"current_underwater_+{d}d"] = int(cur_ret < 0)
            except Exception:
                row[f"current_pnl_pct_+{d}d"] = np.nan
                row[f"current_underwater_+{d}d"] = np.nan

            # strength change & flip flag
            if np.isfinite(row[f"strength_+{d}d"]):
                row[f"strength_change_+{d}d"] = row[f"strength_+{d}d"] - entry_strength
                row[f"strength_flipped_+{d}d"] = int(row[f"strength_+{d}d"] < 0)
            else:
                row[f"strength_change_+{d}d"] = np.nan
                row[f"strength_flipped_+{d}d"] = np.nan

        rows.append(row)

    print(f"  enriched: {len(rows)}, skipped: {skipped} {skip_reasons}")

    df = pd.DataFrame(rows)
    df.to_parquet(REPO / "research/mission_m_gate0_enriched.parquet")
    print(f"  saved enriched parquet ({len(df)} rows)")

    # ============================================================
    # PRIMARY CORRELATIONS
    # ============================================================
    from scipy import stats as sst

    correlations: list[dict] = []
    for d in [3, 5, 7]:
        col = f"strength_+{d}d"
        sub = df[[col, "eventual_pnl_pct"]].dropna()
        if len(sub) >= 30:
            pearson_r, pearson_p = sst.pearsonr(sub[col], sub["eventual_pnl_pct"])
            spearman_r, spearman_p = sst.spearmanr(sub[col], sub["eventual_pnl_pct"])
            t_stat = pearson_r * np.sqrt(len(sub) - 2) / np.sqrt(1 - pearson_r ** 2) if abs(pearson_r) < 1 else float("nan")
            correlations.append({
                "metric": f"strength_+{d}d vs eventual_pnl_pct",
                "n": int(len(sub)),
                "pearson_r": float(pearson_r),
                "pearson_p": float(pearson_p),
                "spearman_r": float(spearman_r),
                "spearman_p": float(spearman_p),
                "t_stat": float(t_stat),
            })

    # change correlation
    for d in [3, 5, 7]:
        col = f"strength_change_+{d}d"
        sub = df[[col, "eventual_pnl_pct"]].dropna()
        if len(sub) >= 30:
            pearson_r, pearson_p = sst.pearsonr(sub[col], sub["eventual_pnl_pct"])
            t_stat = pearson_r * np.sqrt(len(sub) - 2) / np.sqrt(1 - pearson_r ** 2) if abs(pearson_r) < 1 else float("nan")
            correlations.append({
                "metric": f"strength_change_+{d}d vs eventual_pnl_pct",
                "n": int(len(sub)),
                "pearson_r": float(pearson_r),
                "pearson_p": float(pearson_p),
                "t_stat": float(t_stat),
            })

    # flip → loser binary correlation
    for d in [3, 5, 7]:
        flip_col = f"strength_flipped_+{d}d"
        sub = df[[flip_col, "eventual_loser"]].dropna()
        if len(sub) >= 30:
            tab = pd.crosstab(sub[flip_col].astype(int), sub["eventual_loser"].astype(int))
            try:
                chi2, p, _, _ = sst.chi2_contingency(tab)
            except Exception:
                chi2, p = 0.0, 1.0
            n_flipped = int((sub[flip_col] == 1).sum())
            losers_in_flipped = int(((sub[flip_col] == 1) & (sub["eventual_loser"] == 1)).sum())
            n_unflipped = int((sub[flip_col] == 0).sum())
            losers_in_unflipped = int(((sub[flip_col] == 0) & (sub["eventual_loser"] == 1)).sum())
            correlations.append({
                "metric": f"flip_+{d}d → eventual_loser",
                "n": int(len(sub)),
                "chi2": float(chi2),
                "p": float(p),
                "loser_rate_if_flipped": losers_in_flipped / n_flipped if n_flipped > 0 else None,
                "loser_rate_if_intact": losers_in_unflipped / n_unflipped if n_unflipped > 0 else None,
                "n_flipped": n_flipped,
                "n_intact": n_unflipped,
            })

    # ============================================================
    # 4-BUCKET CONDITIONAL ANALYSIS
    # ============================================================
    bucket_results: dict = {}
    overall_winrate = 1.0 - df["eventual_loser"].mean()
    overall_pnl_mean = df["eventual_pnl_pct"].mean()
    overall_pnl_median = df["eventual_pnl_pct"].median()
    bucket_results["overall"] = {
        "n": int(len(df)),
        "win_rate": float(overall_winrate),
        "mean_pnl_pct": float(overall_pnl_mean),
        "median_pnl_pct": float(overall_pnl_median),
    }

    for d in [3, 5, 7]:
        s_col = f"strength_+{d}d"
        u_col = f"current_underwater_+{d}d"
        sub = df.dropna(subset=[s_col, u_col]).copy()
        if len(sub) < 30:
            continue
        sub["bucket"] = "X"
        sub.loc[(sub[s_col] > 0.5) & (sub[u_col] == 0), "bucket"] = "A_strong_winning"
        sub.loc[(sub[s_col] > 0.5) & (sub[u_col] == 1), "bucket"] = "B_strong_losing"
        sub.loc[(sub[s_col] < 0) & (sub[u_col] == 0), "bucket"] = "C_degraded_winning"
        sub.loc[(sub[s_col] < 0) & (sub[u_col] == 1), "bucket"] = "D_degraded_losing"
        sub.loc[(sub[s_col] >= 0) & (sub[s_col] <= 0.5), "bucket"] = "E_neutral"

        bucket_table = []
        for b, g in sub.groupby("bucket"):
            wr = 1.0 - g["eventual_loser"].mean()
            bucket_table.append({
                "bucket": b,
                "n": int(len(g)),
                "win_rate": float(wr),
                "mean_pnl_pct": float(g["eventual_pnl_pct"].mean()),
                "median_pnl_pct": float(g["eventual_pnl_pct"].median()),
            })

        baseline_underwater = sub[sub[u_col] == 1]
        baseline_uw_winrate = 1.0 - baseline_underwater["eventual_loser"].mean() if len(baseline_underwater) > 0 else None
        baseline_uw_mean_pnl = baseline_underwater["eventual_pnl_pct"].mean() if len(baseline_underwater) > 0 else None

        bucket_results[f"+{d}d"] = {
            "buckets": bucket_table,
            "baseline_underwater": {
                "n": int(len(baseline_underwater)),
                "win_rate": float(baseline_uw_winrate) if baseline_uw_winrate is not None else None,
                "mean_pnl_pct": float(baseline_uw_mean_pnl) if baseline_uw_mean_pnl is not None else None,
            },
        }

    # ============================================================
    # VERDICT
    # ============================================================
    verdict = "KILL"
    verdict_reasons: list[str] = []

    # Criterion 1: Pearson at +3d > 0.20 with t > 3
    c1 = next((c for c in correlations if c["metric"] == "strength_+3d vs eventual_pnl_pct"), None)
    if c1 and c1["pearson_r"] > 0.20 and c1["t_stat"] > 3:
        verdict = "PASS"
        verdict_reasons.append(f"C1 PASS: pearson_r={c1['pearson_r']:.3f}, t={c1['t_stat']:.2f}")
    elif c1:
        verdict_reasons.append(f"C1 fail: pearson_r={c1['pearson_r']:.3f}, t={c1['t_stat']:.2f}")

    # Criterion 2: Bucket D win rate ≥15pp lower than baseline_underwater AND mean_pnl ≥2x more negative
    for d in [3, 5, 7]:
        key = f"+{d}d"
        if key not in bucket_results:
            continue
        bD = next((b for b in bucket_results[key]["buckets"] if b["bucket"] == "D_degraded_losing"), None)
        baseline = bucket_results[key]["baseline_underwater"]
        if bD and baseline["win_rate"] is not None:
            diff_wr_pp = (baseline["win_rate"] - bD["win_rate"]) * 100
            mean_pnl_ratio = bD["mean_pnl_pct"] / baseline["mean_pnl_pct"] if baseline["mean_pnl_pct"] != 0 else 0
            if diff_wr_pp >= 15 and mean_pnl_ratio >= 2.0:
                verdict = "PASS"
                verdict_reasons.append(
                    f"C2 PASS at +{d}d: bucket D wr {bD['win_rate']:.2f} vs baseline_uw {baseline['win_rate']:.2f} (Δ={diff_wr_pp:.1f}pp), pnl ratio={mean_pnl_ratio:.2f}x"
                )
            else:
                verdict_reasons.append(
                    f"C2 fail at +{d}d: bucket D wr {bD['win_rate']:.2f} vs baseline_uw {baseline['win_rate']:.2f} (Δ={diff_wr_pp:.1f}pp), pnl ratio={mean_pnl_ratio:.2f}x"
                )

    # Criterion 3: flip @ +3d → loser accuracy > 60%
    c3 = next((c for c in correlations if c["metric"] == "flip_+3d → eventual_loser"), None)
    if c3 and c3.get("loser_rate_if_flipped") is not None and c3["loser_rate_if_flipped"] > 0.60:
        verdict = "PASS"
        verdict_reasons.append(f"C3 PASS: flip@+3d → loser rate {c3['loser_rate_if_flipped']:.2%} (n_flipped={c3['n_flipped']})")
    elif c3:
        verdict_reasons.append(
            f"C3 fail: flip@+3d → loser rate {c3.get('loser_rate_if_flipped', 0) or 0:.2%} (n_flipped={c3['n_flipped']})"
        )

    results = {
        "verdict": verdict,
        "verdict_reasons": verdict_reasons,
        "trades_total": len(trades_raw),
        "trades_enriched": len(df),
        "skipped": skipped,
        "skip_reasons": skip_reasons,
        "correlations": correlations,
        "bucket_analysis": bucket_results,
    }

    OUT_RESULTS.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nVerdict: {verdict}")
    for r in verdict_reasons:
        print(f"  {r}")

    # ============================================================
    # MARKDOWN REPORT
    # ============================================================
    lines: list[str] = []
    lines.append("# Mission M Gate 0 — Diagnostic Report")
    lines.append(f"\nGenerated: 2026-04-08")
    lines.append(f"\n**Verdict: {verdict}**\n")
    for r in verdict_reasons:
        lines.append(f"- {r}")

    lines.append("\n## Trade log summary\n")
    lines.append(f"- Source: `results/v4/s523c_growth_12mo_50k_trades.json` (clean 12-month backtest)")
    lines.append(f"- Total trades: {len(trades_raw)}")
    lines.append(f"- Enriched (with valid signals + OHLCV): {len(df)}")
    lines.append(f"- Skipped: {skipped} {skip_reasons}")
    lines.append(f"- Overall win rate: {overall_winrate:.2%}")
    lines.append(f"- Overall mean pnl_pct: {overall_pnl_mean:+.3f}")
    lines.append(f"- Overall median pnl_pct: {overall_pnl_median:+.3f}")

    lines.append("\n## Primary correlations\n")
    lines.append("| Metric | N | Pearson r | p | t-stat |")
    lines.append("|---|---:|---:|---:|---:|")
    for c in correlations:
        if "pearson_r" in c:
            lines.append(f"| {c['metric']} | {c['n']} | {c['pearson_r']:+.3f} | {c['pearson_p']:.4f} | {c.get('t_stat', float('nan')):+.2f} |")

    lines.append("\n## Flip predictiveness (binary)\n")
    lines.append("| Window | N flipped | Loser rate if flipped | N intact | Loser rate if intact | chi2 p |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for c in correlations:
        if c["metric"].startswith("flip_"):
            lr_f = c.get("loser_rate_if_flipped")
            lr_i = c.get("loser_rate_if_intact")
            lines.append(
                f"| {c['metric']} | {c['n_flipped']} | {lr_f:.2%} | {c['n_intact']} | {lr_i:.2%} | {c['p']:.4f} |"
                if lr_f is not None and lr_i is not None
                else f"| {c['metric']} | — | — | — | — | — |"
            )

    lines.append("\n## 4-bucket conditional analysis (Interpretation B core)\n")
    for d in [3, 5, 7]:
        key = f"+{d}d"
        if key not in bucket_results:
            continue
        baseline = bucket_results[key]["baseline_underwater"]
        lines.append(f"\n### At {key} post-entry")
        lines.append("| Bucket | N | Win rate | Mean pnl_pct | Median pnl_pct |")
        lines.append("|---|---:|---:|---:|---:|")
        for b in bucket_results[key]["buckets"]:
            lines.append(
                f"| {b['bucket']} | {b['n']} | {b['win_rate']:.2%} | {b['mean_pnl_pct']:+.3f} | {b['median_pnl_pct']:+.3f} |"
            )
        if baseline["win_rate"] is not None:
            lines.append(
                f"\n*Baseline (all currently-underwater at {key}):* N={baseline['n']}, win rate={baseline['win_rate']:.2%}, mean pnl={baseline['mean_pnl_pct']:+.3f}"
            )

    lines.append("\n## Diagnosis\n")
    if verdict == "PASS":
        lines.append(
            "Signal degradation carries information about future P&L beyond what current P&L alone reveals. "
            "Mission M is worth promoting to Gate 1 (rule design + sweep)."
        )
    else:
        lines.append(
            "Signal degradation does NOT systematically predict eventual losers beyond what current P&L alone reveals. "
            "The s523c composite either mean-reverts on the same timescale as the strategy hold (so by the time we'd "
            "act, the signal has already moved through several states) or the entry-time z-score doesn't carry "
            "trade-level forward information. Either way, no reasonable Mission M rule will produce edge."
        )

    OUT_REPORT.write_text("\n".join(lines))
    print(f"\nReport: {OUT_REPORT}")
    print(f"Results: {OUT_RESULTS}")


if __name__ == "__main__":
    main()
