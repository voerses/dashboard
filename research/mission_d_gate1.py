"""Mission D Gate 1 — FIF × drift standalone strategy backtest.

The Gate 0 IC test found FIF × drift is cross-token positive at 24h/72h/168h
horizons on BTC/ETH/SOL with IC 0.03-0.05. This script builds a small standalone
long-only strategy on top of that signal and runs a clean backtest.

Strategy:
  - Roll FIF × drift on each token (1h returns) at daily cadence (every 24 bars)
  - Compute z-score over rolling 200-sample window (~200 days)
  - Entry: when z-score crosses above threshold (test 1.5, 2.0, 2.5)
  - Hold: fixed time stop (test 72h, 168h)
  - Equal-weight long across simultaneous entries
  - Realistic costs: 4 bps fee/side + 5 bps slippage = 13 bps round-trip
  - No leverage
  - Universe: BTC, ETH, SOL

Reuses Mission D Gate 0 indicator functions.
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
OUT_REPORT = REPO / "research/mission_d_gate1_report.md"
OUT_RESULTS = REPO / "research/mission_d_gate1_results.json"

TOKENS = ["BTC", "ETH", "SOL"]
INITIAL_CAPITAL = 50000.0
FEE_BPS_SIDE = 4.0
SLIP_BPS = 5.0
ROUND_TRIP_COST = (FEE_BPS_SIDE * 2 + SLIP_BPS) / 10000  # 13 bps

# Strategy parameters
SAMPLE_STEP_BARS = 24  # daily cadence
ZSCORE_WINDOW = 200    # 200-sample rolling z-score baseline
INITIAL_BURN_IN = max(mdg.HURST_WINDOW + mdg.HURST_VEL_LAG, mdg.FIF_WINDOW, mdg.WRD_WINDOW + mdg.WRD_LAG)


def load_close(token: str) -> pd.Series:
    p = OHLCV_DIR / f"{token}_perp_1h.csv"
    if not p.exists():
        raise FileNotFoundError(p)
    df = pd.read_csv(p, parse_dates=["datetime"]).set_index("datetime").sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_convert(None)
    return df["close"].astype(np.float64)


def roll_fif_x_drift(close: pd.Series, sample_step: int = SAMPLE_STEP_BARS) -> pd.DataFrame:
    """Roll FIF × drift on the token, sampled every sample_step bars."""
    rets = mdg.log_returns(close).dropna()
    n = len(rets)
    out_idx = []
    fif_vals = []
    drift_vals = []
    fxd_vals = []
    for end in range(INITIAL_BURN_IN, n, sample_step):
        slice_r = rets.iloc[max(0, end - mdg.FIF_WINDOW): end].to_numpy()
        if len(slice_r) < mdg.FIF_WINDOW:
            continue
        try:
            i_f, drift, _ = mdg.compute_fif(slice_r)
        except Exception:
            continue
        out_idx.append(rets.index[end - 1])
        fif_vals.append(i_f)
        drift_vals.append(drift)
        fxd_vals.append(i_f * drift)
    return pd.DataFrame({
        "FIF": fif_vals,
        "drift": drift_vals,
        "FIF_x_drift": fxd_vals,
    }, index=pd.DatetimeIndex(out_idx, name="datetime"))


def add_zscore(panel: pd.DataFrame, col: str, window: int = ZSCORE_WINDOW) -> pd.DataFrame:
    """Add a rolling z-score column based on the trailing `window` samples."""
    s = panel[col]
    mu = s.rolling(window, min_periods=window // 2).mean()
    sd = s.rolling(window, min_periods=window // 2).std()
    panel[f"{col}_z"] = (s - mu) / sd
    return panel


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------
def backtest_strategy(
    panels: dict[str, pd.DataFrame],
    closes: dict[str, pd.Series],
    threshold: float,
    hold_hours: int,
    universe: list[str],
    direction_mode: str = "long_only",  # 'long_only' | 'long_short'
    capital: float = INITIAL_CAPITAL,
    cost_round_trip: float = ROUND_TRIP_COST,
) -> tuple[pd.DataFrame, dict]:
    """Generate trades from FIF×drift signal and compute equity curve.

    For each token, scan the panel for entry signals. An entry fires when the
    z-score at sample t crosses the threshold (z[t-1] < threshold AND z[t] >=
    threshold). Hold for hold_hours, then exit at the next available 1h close.
    """
    trades = []
    for tok in universe:
        panel = panels[tok]
        close = closes[tok]
        z = panel["FIF_x_drift_z"].to_numpy()
        sample_ts = panel.index.to_numpy()
        for i in range(1, len(z)):
            if not np.isfinite(z[i]) or not np.isfinite(z[i-1]):
                continue
            entry_signal = None
            if z[i-1] < threshold and z[i] >= threshold:
                entry_signal = 1
            elif direction_mode == "long_short" and z[i-1] > -threshold and z[i] <= -threshold:
                entry_signal = -1
            if entry_signal is None:
                continue
            entry_ts = sample_ts[i]
            entry_ts_pd = pd.Timestamp(entry_ts)
            try:
                ix = close.index.get_indexer([entry_ts_pd], method="bfill")[0]
                if ix < 0 or ix >= len(close) - hold_hours:
                    continue
            except Exception:
                continue
            entry_bar_ts = close.index[ix]
            entry_px = float(close.iloc[ix])
            exit_ix = min(ix + hold_hours, len(close) - 1)
            exit_bar_ts = close.index[exit_ix]
            exit_px = float(close.iloc[exit_ix])
            hold_h_actual = (exit_bar_ts - entry_bar_ts).total_seconds() / 3600
            gross = (exit_px - entry_px) / entry_px * entry_signal
            net = gross - cost_round_trip
            trades.append({
                "token": tok,
                "direction": entry_signal,
                "entry_ts": entry_bar_ts,
                "exit_ts": exit_bar_ts,
                "entry_px": entry_px,
                "exit_px": exit_px,
                "hold_h": hold_h_actual,
                "gross_pct": gross,
                "net_pct": net,
                "z_score": float(z[i]),
            })
    df = pd.DataFrame(trades)
    if df.empty:
        return df, {"n_trades": 0}
    df = df.sort_values("entry_ts").reset_index(drop=True)

    # Realized equity curve at trade close
    days_index = pd.date_range(
        start=df["entry_ts"].min().normalize(),
        end=(df["exit_ts"].max() + pd.Timedelta(days=1)).normalize(),
        freq="D",
    )
    pnl_per_day = pd.Series(0.0, index=days_index)
    for _, t in df.iterrows():
        d = t["exit_ts"].normalize()
        if d in pnl_per_day.index:
            pnl_per_day.loc[d] += t["net_pct"] * (capital / max(1, len(universe)))
    equity = capital + pnl_per_day.cumsum()

    final_eq = float(equity.iloc[-1])
    total_ret_pct = (final_eq / capital - 1) * 100
    rp = equity.cummax()
    dd = (equity - rp) / rp
    maxdd_pct = float(dd.min() * 100) if len(dd) else 0
    daily = equity.pct_change().dropna()
    sharpe = float(daily.mean() / daily.std() * np.sqrt(365)) if daily.std() > 0 else 0
    calmar = total_ret_pct / abs(maxdd_pct) if maxdd_pct < 0 else 0
    n_trades = int(len(df))
    win_rate = float((df["net_pct"] > 0).mean())
    mean_pnl = float(df["net_pct"].mean())
    median_pnl = float(df["net_pct"].median())

    # alpha vs BTC simple regression on per-trade vs BTC return
    # Compute BTC return over each trade's window
    btc = closes["BTC"]
    btc_rets = []
    for _, t in df.iterrows():
        try:
            ix_e = btc.index.get_indexer([t["entry_ts"]], method="pad")[0]
            ix_x = btc.index.get_indexer([t["exit_ts"]], method="pad")[0]
            if ix_e < 0 or ix_x < 0 or ix_x <= ix_e:
                btc_rets.append(np.nan)
                continue
            btc_rets.append((float(btc.iloc[ix_x]) - float(btc.iloc[ix_e])) / float(btc.iloc[ix_e]))
        except Exception:
            btc_rets.append(np.nan)
    df["btc_ret"] = btc_rets
    sub = df.dropna(subset=["btc_ret"])
    if len(sub) >= 10 and sub["btc_ret"].std() > 0:
        x = sub["btc_ret"].to_numpy()
        y = sub["net_pct"].to_numpy()
        xm = x - x.mean()
        ym = y - y.mean()
        beta = (xm * ym).sum() / (xm * xm).sum()
        alpha = y.mean() - beta * x.mean()
        y_hat = alpha + beta * x
        resid = y - y_hat
        sse = (resid ** 2).sum()
        dof = len(sub) - 2
        sigma2 = sse / dof if dof > 0 else np.nan
        var_alpha = sigma2 * (1.0 / len(sub) + (x.mean() ** 2) / (xm * xm).sum()) if dof > 0 else np.nan
        se_alpha = float(np.sqrt(var_alpha)) if var_alpha and var_alpha > 0 else np.nan
        alpha_t = float(alpha / se_alpha) if se_alpha and se_alpha > 0 else float("nan")
    else:
        alpha = beta = alpha_t = float("nan")

    metrics = {
        "n_trades": n_trades,
        "n_long": int((df["direction"] == 1).sum()),
        "n_short": int((df["direction"] == -1).sum()),
        "win_rate": win_rate,
        "mean_net_pct": mean_pnl,
        "median_net_pct": median_pnl,
        "total_return_pct": float(total_ret_pct),
        "final_equity": final_eq,
        "max_drawdown_pct": maxdd_pct,
        "sharpe": sharpe,
        "calmar": float(calmar),
        "alpha": float(alpha) if np.isfinite(alpha) else None,
        "beta_btc": float(beta) if np.isfinite(beta) else None,
        "alpha_t_stat": float(alpha_t) if np.isfinite(alpha_t) else None,
    }
    return df, metrics


def main() -> None:
    print("Loading 1h closes for", TOKENS)
    closes = {tok: load_close(tok) for tok in TOKENS}
    for tok, c in closes.items():
        print(f"  {tok}: {len(c)} bars, {c.index[0]} → {c.index[-1]}")

    print("\nRolling FIF × drift on each token (sample every 24 bars)…")
    panels = {}
    for tok, c in closes.items():
        p = roll_fif_x_drift(c)
        p = add_zscore(p, "FIF_x_drift")
        panels[tok] = p
        print(f"  {tok}: {len(p)} samples, z-score after {ZSCORE_WINDOW}-sample warmup")

    # ---- Sweep ----
    configs = []
    for thr in [1.5, 2.0, 2.5]:
        for hold in [72, 168]:
            for mode in ["long_only", "long_short"]:
                configs.append((thr, hold, mode))

    print(f"\nRunning sweep ({len(configs)} configs)…")
    all_results = []
    for thr, hold, mode in configs:
        trades, metrics = backtest_strategy(panels, closes, thr, hold, TOKENS, direction_mode=mode)
        metrics["threshold"] = thr
        metrics["hold_hours"] = hold
        metrics["direction_mode"] = mode
        all_results.append(metrics)
        print(f"  thr={thr} hold={hold}h {mode:<11} n={metrics['n_trades']:>3} "
              f"ret={metrics['total_return_pct']:>+7.1f}% "
              f"sharpe={metrics['sharpe']:>+5.2f} "
              f"maxdd={metrics['max_drawdown_pct']:>+6.1f}% "
              f"calmar={metrics['calmar']:>+5.2f} "
              f"alpha_t={metrics['alpha_t_stat'] if metrics['alpha_t_stat'] else 'n/a':>5}")

    # Sort by alpha_t (the cleanest metric)
    all_results.sort(key=lambda r: r["alpha_t_stat"] if r["alpha_t_stat"] else -99, reverse=True)

    print("\n" + "=" * 100)
    print("RANKED RESULTS (by alpha_t)")
    print("=" * 100)
    print(f"{'thr':>4} {'hold':>5} {'mode':>11} {'n':>4} {'ret':>9} {'sharpe':>8} {'maxdd':>8} "
          f"{'calmar':>7} {'alpha_t':>9} {'win%':>7}")
    for r in all_results:
        at = r['alpha_t_stat']
        at_s = f"{at:+.2f}" if at is not None else "n/a"
        print(f"{r['threshold']:>4.1f} {r['hold_hours']:>4}h {r['direction_mode']:>11} "
              f"{r['n_trades']:>4} {r['total_return_pct']:>+7.1f}% {r['sharpe']:>+7.2f} "
              f"{r['max_drawdown_pct']:>+7.1f}% {r['calmar']:>+6.2f} {at_s:>9} "
              f"{r['win_rate']*100:>5.1f}%")

    OUT_RESULTS.write_text(json.dumps(all_results, indent=2, default=str))
    print(f"\nSaved → {OUT_RESULTS}")


if __name__ == "__main__":
    main()
