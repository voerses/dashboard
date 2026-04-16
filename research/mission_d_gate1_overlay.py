"""Mission D Gate 1 — FIF × drift as a directional gate overlay on s523c.

The standalone Mission D strategy passed Gate 1 strongly (Sharpe 1.40,
alpha_t +3.58, MaxDD -4.1%). This script tests whether FIF × drift can also
ACT AS A FILTER on the existing s523c trade log:
  - For each s523c LONG entry: only keep it if FIF × drift z-score on the
    token is > +threshold at entry time (signal confirms upward direction)
  - For each s523c SHORT entry: only keep if z-score < -threshold (signal
    confirms downward direction)
  - Trades that fail the filter are SKIPPED (zero P&L for that trade)

Compares baseline s523c vs FIF-gated s523c on the clean 12-month backtest.

Reuses:
  - Mission D Gate 0 functions (compute_fif, log_returns, FIF_WINDOW)
  - s523c trade log: results/v4/s523c_growth_12mo_50k_trades.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/workspace/crypto_backtest")
sys.path.insert(0, str(REPO / "research"))

import mission_d_gate0 as mdg  # noqa: E402

OHLCV_DIR = REPO / "data/perp/binance/1h_ohlcv"
TRADES_PATH = REPO / "results/v4/s523c_growth_12mo_50k_trades.json"
OUT_REPORT = REPO / "research/mission_d_gate1_overlay_report.md"
OUT_RESULTS = REPO / "research/mission_d_gate1_overlay_results.json"

INITIAL_CAPITAL = 50000.0
BACKTEST_START = pd.Timestamp("2025-04-05 16:00")
BACKTEST_END = pd.Timestamp("2026-04-05 16:00")

SAMPLE_STEP_BARS = 24
ZSCORE_WINDOW = 200
INITIAL_BURN_IN = max(mdg.HURST_WINDOW + mdg.HURST_VEL_LAG, mdg.FIF_WINDOW, mdg.WRD_WINDOW + mdg.WRD_LAG)


def load_close(token: str) -> pd.Series | None:
    p = OHLCV_DIR / f"{token}_perp_1h.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, parse_dates=["datetime"]).set_index("datetime").sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    return df["close"].astype(np.float64)


def roll_fif_x_drift(close: pd.Series) -> pd.Series:
    """Roll FIF × drift z-score on the token, sampled every SAMPLE_STEP_BARS bars.
    Returns a Series of z-scores indexed by the sample timestamps.
    """
    rets = mdg.log_returns(close).dropna()
    n = len(rets)
    out_idx = []
    fxd_vals = []
    for end in range(INITIAL_BURN_IN, n, SAMPLE_STEP_BARS):
        slice_r = rets.iloc[max(0, end - mdg.FIF_WINDOW): end].to_numpy()
        if len(slice_r) < mdg.FIF_WINDOW:
            continue
        try:
            i_f, drift, _ = mdg.compute_fif(slice_r)
        except Exception:
            continue
        out_idx.append(rets.index[end - 1])
        fxd_vals.append(i_f * drift)
    s = pd.Series(fxd_vals, index=pd.DatetimeIndex(out_idx, name="datetime"), name="FIF_x_drift")
    if len(s) < ZSCORE_WINDOW:
        return s.iloc[:0]
    mu = s.rolling(ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW // 2).mean()
    sd = s.rolling(ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW // 2).std()
    z = (s - mu) / sd
    return z.dropna()


def lookup_z_at(z_series: pd.Series, ts: pd.Timestamp) -> float | None:
    """Forward-fill: use the most recent FIF×drift z-score that's <= ts."""
    if z_series.empty:
        return None
    try:
        ix = z_series.index.get_indexer([ts], method="pad")[0]
        if ix < 0:
            return None
        return float(z_series.iloc[ix])
    except Exception:
        return None


def compute_baseline_metrics(trades: list[dict]) -> dict:
    """Realized equity curve from a trade list (each with token, direction, entry_bar, exit_bar, pnl, margin_usd)."""
    days = pd.date_range(BACKTEST_START.normalize(), BACKTEST_END.normalize(), freq="D")
    realized = pd.Series(0.0, index=days)
    for tr in trades:
        exit_dt = (BACKTEST_START + pd.Timedelta(hours=int(tr["exit_bar"]))).normalize()
        if exit_dt in realized.index:
            realized.loc[exit_dt] += float(tr["pnl"])
        elif exit_dt > realized.index[-1]:
            realized.iloc[-1] += float(tr["pnl"])
        else:
            ix = realized.index.get_indexer([exit_dt], method="pad")[0]
            if ix >= 0:
                realized.iloc[ix] += float(tr["pnl"])
    equity = INITIAL_CAPITAL + realized.cumsum()
    return _eq_metrics(equity)


def _eq_metrics(equity: pd.Series) -> dict:
    final = float(equity.iloc[-1])
    total = (final / INITIAL_CAPITAL - 1) * 100
    rp = equity.cummax()
    dd = (equity - rp) / rp
    maxdd = float(dd.min() * 100)
    daily = equity.pct_change().dropna()
    sharpe = float(daily.mean() / daily.std() * np.sqrt(365)) if daily.std() > 0 else 0
    calmar = total / abs(maxdd) if maxdd < 0 else 0
    return {
        "final_equity": final,
        "total_return_pct": float(total),
        "max_drawdown_pct": maxdd,
        "sharpe": sharpe,
        "calmar": float(calmar),
    }


def main() -> None:
    print("Loading s523c 12mo trade log…")
    with open(TRADES_PATH) as f:
        trades = json.load(f)
    print(f"  {len(trades)} trades")

    # Identify the unique tokens
    tokens = sorted({t["token"] for t in trades})
    print(f"  {len(tokens)} unique tokens")

    print("\nRolling FIF × drift z-score on each token…")
    z_series_per_token: dict[str, pd.Series] = {}
    skipped = 0
    for tok in tokens:
        c = load_close(tok)
        if c is None:
            skipped += 1
            continue
        z = roll_fif_x_drift(c)
        if z.empty:
            skipped += 1
            continue
        z_series_per_token[tok] = z
    print(f"  {len(z_series_per_token)}/{len(tokens)} tokens with valid FIF×drift")
    if skipped:
        print(f"  {skipped} tokens skipped (no OHLCV or insufficient history)")

    # ---- Baseline ----
    print("\nBaseline s523c metrics (no overlay)…")
    baseline = compute_baseline_metrics(trades)
    print(f"  Final equity: ${baseline['final_equity']:,.0f}")
    print(f"  Total return: {baseline['total_return_pct']:+.2f}%")
    print(f"  MaxDD: {baseline['max_drawdown_pct']:+.2f}%")
    print(f"  Sharpe: {baseline['sharpe']:+.2f}")
    print(f"  Calmar: {baseline['calmar']:+.2f}")

    # ---- Sweep over thresholds and modes ----
    configs = [
        # (z_threshold, mode_name)
        (0.0, "directional_sign"),    # any positive z for long, any negative z for short
        (0.5, "moderate"),
        (1.0, "strict"),
        (1.5, "very_strict"),
    ]

    all_results = []
    for thr, name in configs:
        # For each trade, look up z-score at entry; gate by direction
        kept = []
        skipped_counts = {"no_z": 0, "wrong_direction": 0}
        for tr in trades:
            tok = tr["token"]
            direction = int(tr["direction"])
            entry_dt = (BACKTEST_START + pd.Timedelta(hours=int(tr["entry_bar"])))
            z_series = z_series_per_token.get(tok)
            if z_series is None:
                # Token has no FIF data — keep trade as-is (don't penalize for missing data)
                kept.append(tr)
                continue
            z = lookup_z_at(z_series, entry_dt)
            if z is None or not np.isfinite(z):
                skipped_counts["no_z"] += 1
                # Keep trade — don't penalize
                kept.append(tr)
                continue
            # Direction gate
            if direction == 1 and z >= thr:
                kept.append(tr)
            elif direction == -1 and z <= -thr:
                kept.append(tr)
            else:
                skipped_counts["wrong_direction"] += 1
        # Compute metrics on kept trades only
        m = compute_baseline_metrics(kept)
        m["threshold"] = thr
        m["mode"] = name
        m["n_trades_kept"] = len(kept)
        m["n_trades_dropped"] = len(trades) - len(kept)
        m["skipped_no_z"] = skipped_counts["no_z"]
        m["skipped_wrong_dir"] = skipped_counts["wrong_direction"]
        all_results.append(m)
        print(f"\nthr={thr:>4.1f} ({name}):")
        print(f"  trades kept: {len(kept)}/{len(trades)} (dropped {skipped_counts['wrong_direction']} for wrong direction)")
        print(f"  Return: {m['total_return_pct']:+.1f}% (baseline {baseline['total_return_pct']:+.1f}%)")
        print(f"  MaxDD: {m['max_drawdown_pct']:+.1f}% (baseline {baseline['max_drawdown_pct']:+.1f}%)")
        print(f"  Sharpe: {m['sharpe']:+.2f} (baseline {baseline['sharpe']:+.2f})")
        print(f"  Calmar: {m['calmar']:+.2f} (baseline {baseline['calmar']:+.2f})")
        d_calmar_pct = ((m['calmar'] - baseline['calmar']) / baseline['calmar'] * 100) if baseline['calmar'] != 0 else 0
        print(f"  ΔCalmar: {d_calmar_pct:+.1f}%")

    # ---- Summary table ----
    print("\n" + "=" * 100)
    print("SUMMARY — FIF × drift directional gate on s523c 12mo trades")
    print("=" * 100)
    print(f"{'config':<22} {'kept':>8} {'Ret':>9} {'MaxDD':>9} {'Sharpe':>8} {'Calmar':>8} {'ΔCalmar':>10}")
    print(f"{'baseline':<22} {len(trades):>8} {baseline['total_return_pct']:>+8.1f}% {baseline['max_drawdown_pct']:>+8.1f}% "
          f"{baseline['sharpe']:>+8.2f} {baseline['calmar']:>+8.2f} {'—':>10}")
    for r in all_results:
        d_calmar_pct = ((r['calmar'] - baseline['calmar']) / baseline['calmar'] * 100) if baseline['calmar'] != 0 else 0
        cfg = f"thr={r['threshold']} ({r['mode']})"
        print(f"{cfg:<22} {r['n_trades_kept']:>8} {r['total_return_pct']:>+8.1f}% {r['max_drawdown_pct']:>+8.1f}% "
              f"{r['sharpe']:>+8.2f} {r['calmar']:>+8.2f} {d_calmar_pct:>+9.1f}%")

    OUT_RESULTS.write_text(json.dumps({"baseline": baseline, "configs": all_results}, indent=2, default=str))
    print(f"\nSaved → {OUT_RESULTS}")


if __name__ == "__main__":
    main()
