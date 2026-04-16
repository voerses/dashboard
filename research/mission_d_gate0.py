"""
Mission D Gate 0 — IC test for 4 Tier-1 novel indicators from the
contrarian-math catalog.

Indicators:
  HV  — Hurst Velocity (DFA-based, derivative of Hurst exponent)
  FIF — Fisher Information Flow (KDE-based generalized Fisher info)
  WRD — Wasserstein Regime Detector (W1 between rolling return windows)
  SRI — Spectral Rotation Index (eigenvector rotation of corr matrix)

All indicator values at time t use ONLY data through and including bar t.
Forward returns at time t use data from t+1 onward.

Research-only. Outputs:
  research/mission_d_gate0_results.json
  research/mission_d_gate0_report.md
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import (
    gaussian_kde,
    pearsonr,
    spearmanr,
    wasserstein_distance,
)

DATA_DIR = Path("/workspace/crypto_backtest/data/perp/binance/1h_ohlcv")
OUT_DIR = Path("/workspace/crypto_backtest/research")
RESULTS_JSON = OUT_DIR / "mission_d_gate0_results.json"
REPORT_MD = OUT_DIR / "mission_d_gate0_report.md"

PRIMARY_TOKENS = ["BTC", "ETH", "SOL"]
BASKET = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX",
    "LINK", "DOT", "POL", "NEAR", "ATOM", "LTC", "UNI", "ICP", "FIL",
]

HORIZONS_HOURS = [1, 4, 24, 72, 168]
SAMPLE_STEP = 24  # evaluate indicators every 24 bars (daily cadence)

# Indicator window parameters
HURST_WINDOW = 200
HURST_VEL_LAG = 20
FIF_WINDOW = 200
WRD_WINDOW = 100
WRD_LAG = 100
SRI_WINDOW = 168  # 7 days
SRI_LAG = 24  # 1 day

MIN_PAIRED_OBS = 500


# ----------------------------------------------------------------------
# Data loading
# ----------------------------------------------------------------------
def load_close(token: str) -> pd.Series:
    fp = DATA_DIR / f"{token}_perp_1h.csv"
    df = pd.read_csv(fp, usecols=["timestamp", "close"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df["close"].astype(float)


def log_returns(close: pd.Series) -> pd.Series:
    return np.log(close).diff()


# ----------------------------------------------------------------------
# Indicator math
# ----------------------------------------------------------------------
def compute_hurst_dfa(series: np.ndarray, min_w: int = 10, max_w: int | None = None) -> float:
    """Detrended Fluctuation Analysis estimate of Hurst exponent."""
    s = np.asarray(series, dtype=float)
    s = s[np.isfinite(s)]
    n = len(s)
    if n < 30:
        return np.nan
    if max_w is None:
        max_w = n // 4
    if max_w <= min_w:
        return np.nan
    Y = np.cumsum(s - s.mean())
    scales = np.unique(np.logspace(np.log10(min_w), np.log10(max_w), 12).astype(int))
    scales = scales[scales >= min_w]
    if len(scales) < 4:
        return np.nan
    F = []
    for w in scales:
        segs = n // w
        if segs < 1:
            F.append(np.nan)
            continue
        Y_trim = Y[: segs * w].reshape(segs, w)
        x = np.arange(w)
        # vectorised linear detrend per segment
        x_mean = x.mean()
        denom = ((x - x_mean) ** 2).sum()
        y_mean = Y_trim.mean(axis=1, keepdims=True)
        cov = ((Y_trim - y_mean) * (x - x_mean)).sum(axis=1) / denom
        intercept = y_mean.flatten() - cov * x_mean
        trend = intercept[:, None] + cov[:, None] * x[None, :]
        F2 = np.mean((Y_trim - trend) ** 2, axis=1)
        F.append(np.sqrt(F2.mean()))
    F = np.array(F)
    mask = np.isfinite(F) & (F > 0)
    if mask.sum() < 4:
        return np.nan
    coeffs = np.polyfit(np.log(scales[mask]), np.log(F[mask]), 1)
    return float(coeffs[0])


def compute_hv(returns: np.ndarray, hurst_window: int = HURST_WINDOW,
               velocity_lag: int = HURST_VEL_LAG) -> tuple[float, float]:
    """Returns (H_current, v_H)."""
    r = np.asarray(returns)
    if len(r) < hurst_window + velocity_lag:
        return (np.nan, np.nan)
    H_cur = compute_hurst_dfa(r[-hurst_window:])
    H_prev = compute_hurst_dfa(r[-(hurst_window + velocity_lag):-velocity_lag])
    v_H = (H_cur - H_prev) / velocity_lag
    return (H_cur, v_H)


def compute_fif(returns: np.ndarray, window: int = FIF_WINDOW) -> tuple[float, float, float]:
    """Returns (I_fisher, drift, drift_t_stat)."""
    r = np.asarray(returns)[-window:]
    r = r[np.isfinite(r)]
    if len(r) < 30 or r.std() == 0:
        return (np.nan, np.nan, np.nan)
    try:
        kde = gaussian_kde(r, bw_method="silverman")
    except Exception:
        return (np.nan, np.nan, np.nan)
    x_grid = np.linspace(r.min() - 3 * r.std(), r.max() + 3 * r.std(), 400)
    f = kde(x_grid)
    f_prime = np.gradient(f, x_grid)
    integrand = np.where(f > 1e-10, f_prime ** 2 / f, 0.0)
    I_fisher = float(np.trapezoid(integrand, x_grid))
    drift = float(np.mean(r))
    drift_t = float(drift / (r.std(ddof=1) / np.sqrt(len(r))))
    return (I_fisher, drift, drift_t)


def compute_wrd(returns: np.ndarray, window: int = WRD_WINDOW,
                lag: int = WRD_LAG) -> tuple[float, float, float]:
    """Returns (W1, delta_skew, tail_shift) where tail_shift = right_shift - left_shift."""
    r = np.asarray(returns)
    if len(r) < window + lag:
        return (np.nan, np.nan, np.nan)
    cur = r[-window:]
    prev = r[-(window + lag):-lag]
    cur = cur[np.isfinite(cur)]
    prev = prev[np.isfinite(prev)]
    if len(cur) < 10 or len(prev) < 10:
        return (np.nan, np.nan, np.nan)
    W1 = float(wasserstein_distance(cur, prev))
    if cur.std() == 0 or prev.std() == 0:
        delta_skew = np.nan
    else:
        skew_c = float(np.mean(((cur - cur.mean()) / cur.std()) ** 3))
        skew_p = float(np.mean(((prev - prev.mean()) / prev.std()) ** 3))
        delta_skew = skew_c - skew_p
    left_shift = float(np.percentile(cur, 5) - np.percentile(prev, 5))
    right_shift = float(np.percentile(cur, 95) - np.percentile(prev, 95))
    tail_shift = right_shift - left_shift  # positive = right tail extended more
    return (W1, delta_skew, tail_shift)


def compute_sri(returns_matrix: np.ndarray, window: int = SRI_WINDOW,
                lag: int = SRI_LAG) -> tuple[float, float, np.ndarray]:
    """
    returns_matrix: (T, N) array.
    Returns (SRI_degrees, absorption_ratio, v1_current).
    """
    R = np.asarray(returns_matrix)
    if R.shape[0] < window + lag:
        return (np.nan, np.nan, np.zeros(R.shape[1] if R.ndim == 2 else 0))
    R_cur = R[-window:]
    R_prev = R[-(window + lag):-lag]
    # mask cols with no variance
    if np.any(R_cur.std(axis=0) == 0) or np.any(R_prev.std(axis=0) == 0):
        return (np.nan, np.nan, np.zeros(R.shape[1]))
    C_cur = np.corrcoef(R_cur.T)
    C_prev = np.corrcoef(R_prev.T)
    if not (np.isfinite(C_cur).all() and np.isfinite(C_prev).all()):
        return (np.nan, np.nan, np.zeros(R.shape[1]))
    vals_c, vecs_c = np.linalg.eigh(C_cur)
    vals_p, vecs_p = np.linalg.eigh(C_prev)
    v1_c = vecs_c[:, -1]
    v1_p = vecs_p[:, -1]
    cos_a = float(np.clip(np.abs(np.dot(v1_c, v1_p)), -1.0, 1.0))
    sri_deg = float(np.degrees(np.arccos(cos_a)))
    absorption = float(vals_c[-1] / np.sum(vals_c))
    return (sri_deg, absorption, v1_c)


# ----------------------------------------------------------------------
# Rolling computation
# ----------------------------------------------------------------------
def roll_single_asset_indicators(returns: pd.Series, sample_step: int = SAMPLE_STEP) -> pd.DataFrame:
    """Compute HV, FIF, WRD on a single-asset return series at every sample_step bars."""
    r = returns.values
    idx = returns.index
    n = len(r)

    burn_in = max(HURST_WINDOW + HURST_VEL_LAG, FIF_WINDOW, WRD_WINDOW + WRD_LAG)
    rows = []
    for t in range(burn_in, n, sample_step):
        slice_r = r[: t + 1]
        H_cur, v_H = compute_hv(slice_r)
        I_f, drift, drift_t = compute_fif(slice_r)
        W1, dskew, tail = compute_wrd(slice_r)
        rows.append({
            "ts": idx[t],
            "H": H_cur,
            "v_H": v_H,
            "FIF": I_f,
            "FIF_drift": drift,
            "FIF_drift_t": drift_t,
            "W1": W1,
            "delta_skew": dskew,
            "tail_shift": tail,
        })
    return pd.DataFrame(rows).set_index("ts")


def roll_sri(returns_matrix: pd.DataFrame, sample_step: int = SAMPLE_STEP) -> pd.DataFrame:
    arr = returns_matrix.values
    idx = returns_matrix.index
    n = len(idx)
    burn_in = SRI_WINDOW + SRI_LAG
    rows = []
    for t in range(burn_in, n, sample_step):
        sri, absorp, _ = compute_sri(arr[: t + 1])
        rows.append({"ts": idx[t], "SRI": sri, "absorption": absorp})
    return pd.DataFrame(rows).set_index("ts")


# ----------------------------------------------------------------------
# Forward returns + IC test
# ----------------------------------------------------------------------
def forward_log_returns(close: pd.Series, horizon_hours: int) -> pd.Series:
    """Forward log return: log(close[t+h] / close[t]). Index aligned to t."""
    return np.log(close.shift(-horizon_hours) / close)


def ic_test(x: pd.Series, y: pd.Series) -> dict:
    df = pd.concat([x, y], axis=1).dropna()
    if len(df) < MIN_PAIRED_OBS:
        return {"n": int(len(df)), "skip": True}
    a = df.iloc[:, 0].values
    b = df.iloc[:, 1].values
    if np.std(a) == 0 or np.std(b) == 0:
        return {"n": int(len(df)), "skip": True}
    pr, pp = pearsonr(a, b)
    sr, sp = spearmanr(a, b)
    n = len(df)
    # t-stat from pearson
    if abs(pr) >= 1.0:
        tstat = np.nan
    else:
        tstat = pr * np.sqrt(n - 2) / np.sqrt(1 - pr ** 2)
    return {
        "n": int(n),
        "pearson_ic": float(pr),
        "pearson_p": float(pp),
        "spearman_ic": float(sr),
        "spearman_p": float(sp),
        "t_stat": float(tstat),
    }


# ----------------------------------------------------------------------
# Main pipeline
# ----------------------------------------------------------------------
def main():
    t0 = time.time()
    print("[load] reading 1h closes for primary tokens...")
    closes = {tok: load_close(tok) for tok in PRIMARY_TOKENS}
    rets = {tok: log_returns(c) for tok, c in closes.items()}

    # Compute single-asset indicators per primary token
    indicator_panels = {}
    for tok in PRIMARY_TOKENS:
        print(f"[indicator] rolling HV/FIF/WRD on {tok} (n={len(rets[tok])})...")
        ts0 = time.time()
        panel = roll_single_asset_indicators(rets[tok].dropna())
        indicator_panels[tok] = panel
        print(f"  done in {time.time() - ts0:.1f}s, {len(panel)} sample points")

    # Build multi-asset returns matrix for SRI
    print("[load] building basket returns matrix for SRI...")
    basket_closes = {}
    for tok in BASKET:
        try:
            basket_closes[tok] = load_close(tok)
        except FileNotFoundError:
            print(f"  skip {tok}: missing")
    basket_df = pd.DataFrame(basket_closes)
    # Drop tokens with < 2 years of history
    min_bars = 24 * 365 * 2
    keep = [c for c in basket_df.columns if basket_df[c].notna().sum() >= min_bars]
    basket_df = basket_df[keep]
    print(f"  basket tokens kept: {keep}")
    basket_rets = np.log(basket_df).diff().dropna(how="all")
    # Drop rows that are missing too many cols (require all cols valid)
    basket_rets = basket_rets.dropna()
    print(f"  basket aligned rows: {len(basket_rets)}")

    print("[indicator] rolling SRI on basket...")
    ts0 = time.time()
    sri_panel = roll_sri(basket_rets)
    print(f"  done in {time.time() - ts0:.1f}s, {len(sri_panel)} sample points")

    # IC test
    print("[ic] running IC tests...")
    results: dict = {"horizons_hours": HORIZONS_HOURS, "indicators": {}}

    indicator_signals = {
        "HV_v_H": ("v_H", "magnitude"),
        "HV_v_H_signed": ("v_H", "signed"),  # raw, captures direction
        "FIF": ("FIF", "magnitude"),
        "FIF_drift": ("FIF_drift", "signed"),
        "FIF_x_drift": ("FIF_x_drift", "signed"),  # composite below
        "WRD_W1": ("W1", "magnitude"),
        "WRD_tail_shift": ("tail_shift", "signed"),
        "WRD_delta_skew": ("delta_skew", "signed"),
    }

    for tok in PRIMARY_TOKENS:
        panel = indicator_panels[tok].copy()
        # composite signal: FIF * sign(drift) * drift magnitude
        panel["FIF_x_drift"] = panel["FIF"] * panel["FIF_drift"]
        close = closes[tok]
        for sig_name, (col, _) in indicator_signals.items():
            if col not in panel.columns:
                continue
            x = panel[col]
            for h in HORIZONS_HOURS:
                fr = forward_log_returns(close, h)
                fr_aligned = fr.reindex(x.index)
                res = ic_test(x, fr_aligned)
                key = f"{sig_name}|{tok}|{h}h"
                results.setdefault("ic", {})[key] = res

    # SRI vs forward returns of BTC, ETH, SOL (and basket-mean)
    sri_signals = {
        "SRI": "SRI",
        "absorption": "absorption",
    }
    for tok in PRIMARY_TOKENS + ["BASKET_MEAN"]:
        if tok == "BASKET_MEAN":
            close_eq = basket_df.dropna().mean(axis=1)
        else:
            close_eq = closes[tok]
        for sig_name, col in sri_signals.items():
            x = sri_panel[col]
            for h in HORIZONS_HOURS:
                fr = forward_log_returns(close_eq, h)
                fr_aligned = fr.reindex(x.index)
                res = ic_test(x, fr_aligned)
                key = f"{sig_name}|{tok}|{h}h"
                results.setdefault("ic", {})[key] = res

    # ------------------------------------------------------------------
    # Verdicts
    # ------------------------------------------------------------------
    def verdict_for(indicator_signal_names: list[str]) -> dict:
        """Aggregate IC across signal variants/tokens to derive PASS/MARGINAL/KILL."""
        rows = []
        for key, res in results["ic"].items():
            sig, tok, h = key.split("|")
            if sig not in indicator_signal_names:
                continue
            if res.get("skip"):
                continue
            rows.append({
                "sig": sig,
                "token": tok,
                "horizon": h,
                "ic": res["pearson_ic"],
                "t": res["t_stat"],
                "n": res["n"],
                "spearman": res["spearman_ic"],
            })
        if not rows:
            return {"verdict": "NO_DATA", "evidence": []}
        # PASS rules
        # (a) |IC|>0.05 and |t|>2 on >=2 horizons (any single signal/token combo qualifying counts each horizon once)
        # (b) |IC|>0.08 and |t|>3 on any single horizon
        any_strong = any(abs(r["ic"]) > 0.08 and abs(r["t"]) > 3 for r in rows)
        # robust: count horizons that have at least one (sig,tok) hitting threshold
        robust_horizons = set()
        for r in rows:
            if abs(r["ic"]) > 0.05 and abs(r["t"]) > 2:
                robust_horizons.add((r["sig"], r["token"], r["horizon"]))
        # collapse to (sig,tok) -> # horizons
        from collections import defaultdict
        per_sigtok = defaultdict(set)
        for s, tk, h in robust_horizons:
            per_sigtok[(s, tk)].add(h)
        any_robust = any(len(v) >= 2 for v in per_sigtok.values())

        # marginal: at least one (|IC|>0.04 and |t|>2)
        any_marginal = any(abs(r["ic"]) > 0.04 and abs(r["t"]) > 2 for r in rows)

        if any_strong or any_robust:
            verdict = "PASS"
        elif any_marginal:
            verdict = "MARGINAL"
        else:
            verdict = "KILL"

        # best signal
        best = max(rows, key=lambda r: abs(r["ic"]))
        return {
            "verdict": verdict,
            "best_signal": best,
            "n_rows": len(rows),
            "any_strong_single_horizon": any_strong,
            "any_robust_2horizons": any_robust,
        }

    indicator_groups = {
        "HV": ["HV_v_H", "HV_v_H_signed"],
        "FIF": ["FIF", "FIF_drift", "FIF_x_drift"],
        "WRD": ["WRD_W1", "WRD_tail_shift", "WRD_delta_skew"],
        "SRI": ["SRI", "absorption"],
    }
    summary = {ind: verdict_for(sigs) for ind, sigs in indicator_groups.items()}
    results["verdicts"] = summary

    # Cross-token sign consistency for primary tokens
    consistency: dict = {}
    for ind, sigs in indicator_groups.items():
        ind_consist = {}
        for sig in sigs:
            for h in HORIZONS_HOURS:
                signs = []
                for tok in PRIMARY_TOKENS:
                    key = f"{sig}|{tok}|{h}h"
                    res = results["ic"].get(key)
                    if res and not res.get("skip"):
                        signs.append(np.sign(res["pearson_ic"]))
                if len(signs) == 3:
                    ind_consist[f"{sig}|{h}h"] = {
                        "signs": [int(s) for s in signs],
                        "consistent": int(abs(sum(signs))) == 3,
                    }
        consistency[ind] = ind_consist
    results["cross_token_consistency"] = consistency

    # Strongest single signal across everything
    all_rows = []
    for key, res in results["ic"].items():
        if res.get("skip"):
            continue
        all_rows.append((abs(res["pearson_ic"]), key, res))
    all_rows.sort(reverse=True)
    results["top_10_by_abs_ic"] = [
        {"key": k, **r} for _, k, r in all_rows[:10]
    ]

    # Save
    OUT_DIR.mkdir(exist_ok=True)
    with RESULTS_JSON.open("w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"[save] wrote {RESULTS_JSON}")

    # Build report
    write_report(results)
    print(f"[save] wrote {REPORT_MD}")
    print(f"[done] total {time.time() - t0:.1f}s")


def write_report(results: dict) -> None:
    lines = []
    lines.append("# Mission D Gate 0 — IC Test Report")
    lines.append("")
    lines.append("Tier 1 indicators tested: HV, FIF, WRD, SRI")
    lines.append("Tokens: BTC, ETH, SOL (single-asset); 17-token basket for SRI")
    lines.append(f"Horizons: {HORIZONS_HOURS} hours")
    lines.append(f"Sample step: every {SAMPLE_STEP} bars (daily cadence)")
    lines.append(f"Min paired observations: {MIN_PAIRED_OBS}")
    lines.append("")
    lines.append("## Verdicts")
    lines.append("")
    lines.append("| Indicator | Verdict | Best |IC| | t-stat | Token | Horizon | Signal |")
    lines.append("|---|---|---|---|---|---|---|")
    for ind, v in results["verdicts"].items():
        if v.get("best_signal"):
            b = v["best_signal"]
            lines.append(
                f"| {ind} | **{v['verdict']}** | {abs(b['ic']):.4f} | {b['t']:.2f} | "
                f"{b['token']} | {b['horizon']} | `{b['sig']}` |"
            )
        else:
            lines.append(f"| {ind} | {v['verdict']} | - | - | - | - | - |")
    lines.append("")
    lines.append("## Top 10 IC results overall")
    lines.append("")
    lines.append("| Key | n | Pearson IC | t-stat | Spearman IC |")
    lines.append("|---|---|---|---|---|")
    for row in results["top_10_by_abs_ic"]:
        lines.append(
            f"| `{row['key']}` | {row['n']} | {row['pearson_ic']:+.4f} | "
            f"{row['t_stat']:+.2f} | {row['spearman_ic']:+.4f} |"
        )
    lines.append("")
    lines.append("## Per-indicator best IC by token x horizon")
    lines.append("")
    for ind, sigs in [
        ("HV", ["HV_v_H", "HV_v_H_signed"]),
        ("FIF", ["FIF", "FIF_drift", "FIF_x_drift"]),
        ("WRD", ["WRD_W1", "WRD_tail_shift", "WRD_delta_skew"]),
        ("SRI", ["SRI", "absorption"]),
    ]:
        lines.append(f"### {ind}")
        lines.append("")
        lines.append("| Signal | Token | Horizon | n | Pearson IC | t-stat | Spearman IC |")
        lines.append("|---|---|---|---|---|---|---|")
        for key, res in results["ic"].items():
            sig, tok, h = key.split("|")
            if sig not in sigs:
                continue
            if res.get("skip"):
                continue
            lines.append(
                f"| `{sig}` | {tok} | {h} | {res['n']} | {res['pearson_ic']:+.4f} | "
                f"{res['t_stat']:+.2f} | {res['spearman_ic']:+.4f} |"
            )
        lines.append("")

    lines.append("## Cross-token sign consistency (BTC/ETH/SOL)")
    lines.append("")
    lines.append("`consistent=True` means all three tokens agree on IC sign (all +, all -).")
    lines.append("")
    for ind, items in results["cross_token_consistency"].items():
        if not items:
            continue
        lines.append(f"### {ind}")
        lines.append("")
        lines.append("| Signal x Horizon | Signs (BTC,ETH,SOL) | Consistent |")
        lines.append("|---|---|---|")
        for k, v in items.items():
            lines.append(f"| `{k}` | {tuple(v['signs'])} | {v['consistent']} |")
        lines.append("")

    lines.append("## Recommendation for Gate 1")
    lines.append("")
    pass_inds = [i for i, v in results["verdicts"].items() if v["verdict"] == "PASS"]
    marginal_inds = [i for i, v in results["verdicts"].items() if v["verdict"] == "MARGINAL"]
    kill_inds = [i for i, v in results["verdicts"].items() if v["verdict"] == "KILL"]
    if pass_inds:
        lines.append(f"- **Promote to Gate 1:** {', '.join(pass_inds)}")
    if marginal_inds:
        lines.append(f"- **Hold for monitoring (do not promote yet):** {', '.join(marginal_inds)}")
    if kill_inds:
        lines.append(f"- **Kill:** {', '.join(kill_inds)}")
    lines.append("")
    lines.append("PASS rule: |Pearson IC| > 0.05 with |t| > 2 on >=2 of 5 horizons")
    lines.append("for at least one (signal,token) pair, OR |IC| > 0.08 with |t| > 3 on a single horizon.")
    lines.append("")

    REPORT_MD.write_text("\n".join(lines))


if __name__ == "__main__":
    main()
