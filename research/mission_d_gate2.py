"""Mission D Gate 2 — Bootstrap Monte Carlo + dedup vs existing Tier A.

Re-runs the Gate 1 winning config (thr=2.0, hold=72h, long_only on BTC/ETH/SOL)
to regenerate the trade list, then:

  Part A: 1000-trial trade-level bootstrap (two variants: chronological reorder
          and random-order) over the resampled trades. Reports distribution of
          Sharpe, Calmar, total return, MaxDD, alpha_t. Optional 200-trial
          90-day block bootstrap.

  Part B: Dedup check — correlate Mission D daily P&L against s523c, s513,
          and Mission J (where available).

Outputs:
  research/mission_d_gate2_results.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path("/workspace/crypto_backtest")
sys.path.insert(0, str(REPO / "research"))

import mission_d_gate1 as mdg1  # noqa: E402

OUT_RESULTS = REPO / "research/mission_d_gate2_results.json"

TOKENS = ["BTC", "ETH", "SOL"]
CAPITAL = mdg1.INITIAL_CAPITAL
N_BOOT_TRADE = 1000
N_BOOT_BLOCK = 200
BLOCK_DAYS = 90
RNG = np.random.default_rng(20260408)

# Pass criteria
PASS = {
    "mean_sharpe_gt": 1.0,
    "mean_calmar_gt": 3.0,
    "p5_calmar_gt": 1.0,
    "pct_positive_gt": 0.75,
    "mean_alpha_t_gt": 1.5,
    "max_corr_lt": 0.5,
}


# ---------------------------------------------------------------------------
# Regenerate Gate 1 winning-config trade list
# ---------------------------------------------------------------------------
def build_trades() -> tuple[pd.DataFrame, dict, dict, dict]:
    closes = {tok: mdg1.load_close(tok) for tok in TOKENS}
    panels = {}
    for tok, c in closes.items():
        p = mdg1.roll_fif_x_drift(c)
        p = mdg1.add_zscore(p, "FIF_x_drift")
        panels[tok] = p
    trades, metrics = mdg1.backtest_strategy(
        panels, closes,
        threshold=2.0, hold_hours=72, universe=TOKENS,
        direction_mode="long_only",
    )
    return trades, metrics, panels, closes


# ---------------------------------------------------------------------------
# Metrics helpers
# ---------------------------------------------------------------------------
def compute_metrics_from_daily(equity: pd.Series, net_per_trade: np.ndarray,
                               btc_per_trade: np.ndarray) -> dict:
    """Given a daily equity series and per-trade (net, btc_ret) arrays, compute
    Sharpe, Calmar, total return, MaxDD, alpha_t.
    """
    cap = float(equity.iloc[0] - (equity.iloc[-1] - equity.iloc[0]) * 0) if False else CAPITAL
    final_eq = float(equity.iloc[-1])
    total_ret = (final_eq / CAPITAL - 1) * 100
    rp = equity.cummax()
    dd = (equity - rp) / rp
    maxdd = float(dd.min() * 100) if len(dd) else 0.0
    daily = equity.pct_change().dropna()
    sharpe = float(daily.mean() / daily.std() * np.sqrt(365)) if daily.std() > 0 else 0.0
    calmar = total_ret / abs(maxdd) if maxdd < 0 else 0.0

    # alpha_t via OLS against BTC trade-wise
    x = btc_per_trade[np.isfinite(btc_per_trade)]
    y = net_per_trade[np.isfinite(btc_per_trade)]
    if len(x) >= 10 and x.std() > 0:
        xm = x - x.mean()
        ym = y - y.mean()
        beta = (xm * ym).sum() / (xm * xm).sum()
        alpha = y.mean() - beta * x.mean()
        y_hat = alpha + beta * x
        resid = y - y_hat
        dof = len(x) - 2
        sigma2 = (resid ** 2).sum() / dof if dof > 0 else np.nan
        var_alpha = sigma2 * (1.0 / len(x) + (x.mean() ** 2) / (xm * xm).sum()) if dof > 0 else np.nan
        se_alpha = np.sqrt(var_alpha) if var_alpha and var_alpha > 0 else np.nan
        alpha_t = float(alpha / se_alpha) if se_alpha and se_alpha > 0 else float("nan")
    else:
        alpha_t = float("nan")

    return {
        "sharpe": sharpe,
        "calmar": float(calmar),
        "total_return_pct": float(total_ret),
        "maxdd_pct": maxdd,
        "alpha_t": alpha_t,
    }


def build_equity_chrono(sample_df: pd.DataFrame, n_uni: int) -> pd.Series:
    """Build a daily equity curve from a resampled trade dataframe using the
    resampled trades' original exit dates (chronological)."""
    exit_ts = pd.to_datetime(sample_df["exit_ts"]).dt.normalize()
    days_index = pd.date_range(exit_ts.min(), exit_ts.max(), freq="D")
    pnl = pd.Series(0.0, index=days_index)
    per_unit = CAPITAL / max(1, n_uni)
    for d, npct in zip(exit_ts.values, sample_df["net_pct"].values):
        if d in pnl.index:
            pnl.loc[d] += npct * per_unit
    return CAPITAL + pnl.cumsum()


def build_equity_random_order(sample_df: pd.DataFrame, n_uni: int,
                              rng: np.random.Generator) -> pd.Series:
    """Build a synthetic equity curve by ordering resampled trades uniformly
    across the backtest window (random-order variant). Each trade gets a
    synthetic exit day drawn uniformly from the original date span."""
    # use a 1-day-per-trade cadence for simplicity; exit dates spread uniformly
    n = len(sample_df)
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    order = rng.permutation(n)
    pnl = pd.Series(0.0, index=idx)
    per_unit = CAPITAL / max(1, n_uni)
    pnl.iloc[:] = sample_df["net_pct"].values[order] * per_unit
    return CAPITAL + pnl.cumsum()


# ---------------------------------------------------------------------------
# Bootstrap runners
# ---------------------------------------------------------------------------
def trade_bootstrap(trades: pd.DataFrame, n_trials: int, mode: str,
                    rng: np.random.Generator) -> list[dict]:
    n = len(trades)
    results = []
    trades_r = trades.reset_index(drop=True)
    for t in range(n_trials):
        idx = rng.integers(0, n, size=n)
        sample = trades_r.iloc[idx].reset_index(drop=True)
        if mode == "chrono":
            eq = build_equity_chrono(sample, n_uni=3)
        else:  # "random"
            eq = build_equity_random_order(sample, n_uni=3, rng=rng)
        m = compute_metrics_from_daily(
            eq,
            sample["net_pct"].to_numpy(),
            sample["btc_ret"].to_numpy(),
        )
        results.append(m)
    return results


def block_bootstrap_daily(daily_pnl: pd.Series, n_trials: int, block_days: int,
                          rng: np.random.Generator) -> list[dict]:
    """90-day block bootstrap over the daily P&L series."""
    arr = daily_pnl.to_numpy()
    n = len(arr)
    if n < block_days * 2:
        return []
    n_blocks = int(np.ceil(n / block_days))
    results = []
    for _ in range(n_trials):
        starts = rng.integers(0, n - block_days + 1, size=n_blocks)
        boot = np.concatenate([arr[s:s + block_days] for s in starts])[:n]
        eq = pd.Series(CAPITAL + np.cumsum(boot),
                       index=pd.date_range("2020-01-01", periods=n, freq="D"))
        daily = eq.pct_change().dropna()
        sharpe = float(daily.mean() / daily.std() * np.sqrt(365)) if daily.std() > 0 else 0.0
        final_eq = float(eq.iloc[-1])
        total_ret = (final_eq / CAPITAL - 1) * 100
        rp = eq.cummax()
        dd = (eq - rp) / rp
        maxdd = float(dd.min() * 100)
        calmar = total_ret / abs(maxdd) if maxdd < 0 else 0.0
        results.append({
            "sharpe": sharpe,
            "calmar": float(calmar),
            "total_return_pct": float(total_ret),
            "maxdd_pct": maxdd,
            "alpha_t": float("nan"),  # not computable without trade reg
        })
    return results


# ---------------------------------------------------------------------------
# Distribution summary
# ---------------------------------------------------------------------------
def summarize(results: list[dict]) -> dict:
    if not results:
        return {}
    keys = ["sharpe", "calmar", "total_return_pct", "maxdd_pct", "alpha_t"]
    out = {}
    for k in keys:
        vals = np.array([r[k] for r in results if np.isfinite(r.get(k, np.nan))])
        if len(vals) == 0:
            out[k] = None
            continue
        out[k] = {
            "mean": float(np.mean(vals)),
            "std": float(np.std(vals)),
            "p5": float(np.percentile(vals, 5)),
            "p25": float(np.percentile(vals, 25)),
            "p50": float(np.percentile(vals, 50)),
            "p75": float(np.percentile(vals, 75)),
            "p95": float(np.percentile(vals, 95)),
            "n": int(len(vals)),
        }
    # Pass criterion counters
    out["pct_trials"] = {
        "sharpe_gt_1": float(np.mean([r["sharpe"] > 1.0 for r in results])),
        "sharpe_gt_1_5": float(np.mean([r["sharpe"] > 1.5 for r in results])),
        "calmar_gt_3": float(np.mean([r["calmar"] > 3.0 for r in results])),
        "alpha_t_gt_1_5": float(np.mean([
            r["alpha_t"] > 1.5 for r in results if np.isfinite(r["alpha_t"])
        ])) if any(np.isfinite(r["alpha_t"]) for r in results) else None,
        "total_return_gt_0": float(np.mean([r["total_return_pct"] > 0 for r in results])),
    }
    return out


# ---------------------------------------------------------------------------
# Dedup: Mission D daily P&L vs existing strategies
# ---------------------------------------------------------------------------
def mission_d_daily_pnl(trades: pd.DataFrame) -> pd.Series:
    exit_ts = pd.to_datetime(trades["exit_ts"]).dt.normalize()
    idx = pd.date_range(exit_ts.min(), exit_ts.max(), freq="D")
    pnl = pd.Series(0.0, index=idx)
    per_unit = CAPITAL / 3.0
    for d, n in zip(exit_ts.values, trades["net_pct"].values):
        if d in pnl.index:
            pnl.loc[d] += n * per_unit
    return pnl


def load_equity_curve_as_pnl(path: Path) -> pd.Series | None:
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text())
    except Exception:
        return None
    if isinstance(d, dict):
        s = pd.Series({pd.Timestamp(k): float(v) for k, v in d.items()}).sort_index()
        return s.diff().dropna()
    return None


def dedup_check(mission_d_pnl: pd.Series) -> dict:
    results_dir = REPO / "results/v4"
    candidates = {
        "s523c_growth_24mo_50k": results_dir / "s523c_growth_24mo_50k_equity_curve.json",
        "s523c_growth_12mo_50k": results_dir / "s523c_growth_12mo_50k_equity_curve.json",
        "s513_12mo_50k": results_dir / "s513_12mo_50k_equity_curve.json",
    }
    out = {}
    for name, p in candidates.items():
        other = load_equity_curve_as_pnl(p)
        if other is None or len(other) < 10:
            out[name] = {"status": "missing"}
            continue
        # align on intersection
        common = mission_d_pnl.index.intersection(other.index)
        if len(common) < 10:
            out[name] = {
                "status": "no_overlap",
                "md_range": f"{mission_d_pnl.index.min()}..{mission_d_pnl.index.max()}",
                "other_range": f"{other.index.min()}..{other.index.max()}",
                "overlap_days": int(len(common)),
            }
            continue
        a = mission_d_pnl.loc[common].to_numpy()
        b = other.loc[common].to_numpy()
        if np.std(a) == 0 or np.std(b) == 0:
            out[name] = {"status": "zero_variance", "overlap_days": int(len(common))}
            continue
        corr = float(np.corrcoef(a, b)[0, 1])
        out[name] = {
            "status": "ok",
            "overlap_days": int(len(common)),
            "pearson_corr": corr,
        }

    # Mission J — if trades csv exists, build daily P&L
    mj_trades = REPO / "research/mission_j_trades.csv"
    if mj_trades.exists():
        try:
            mj = pd.read_csv(mj_trades)
            # guess columns
            exit_col = next((c for c in mj.columns if "exit" in c.lower() and "ts" in c.lower() or c.lower() == "exit_date"), None)
            pnl_col = next((c for c in mj.columns if c.lower() in ("net_pct", "pnl_pct", "net_return", "pnl")), None)
            if exit_col and pnl_col:
                mj["_ex"] = pd.to_datetime(mj[exit_col]).dt.normalize()
                daily = mj.groupby("_ex")[pnl_col].sum()
                common = mission_d_pnl.index.intersection(daily.index)
                if len(common) >= 10 and mission_d_pnl.loc[common].std() > 0 and daily.loc[common].std() > 0:
                    corr = float(np.corrcoef(mission_d_pnl.loc[common], daily.loc[common])[0, 1])
                    out["mission_j"] = {"status": "ok", "overlap_days": int(len(common)), "pearson_corr": corr}
                else:
                    out["mission_j"] = {"status": "insufficient_overlap", "overlap_days": int(len(common))}
            else:
                out["mission_j"] = {"status": "unknown_columns", "columns": list(mj.columns)[:10]}
        except Exception as e:
            out["mission_j"] = {"status": f"error: {e}"}
    else:
        out["mission_j"] = {"status": "missing"}
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    print("[gate2] rebuilding Gate 1 winning-config trade list…")
    trades, single_window_metrics, _panels, _closes = build_trades()
    print(f"[gate2] {len(trades)} trades, "
          f"sharpe={single_window_metrics['sharpe']:.2f}, "
          f"calmar={single_window_metrics['calmar']:.2f}, "
          f"alpha_t={single_window_metrics['alpha_t_stat']:.2f}")

    print(f"[gate2] trade-bootstrap (chronological, N={N_BOOT_TRADE})…")
    boot_chrono = trade_bootstrap(trades, N_BOOT_TRADE, "chrono", RNG)
    print(f"[gate2] trade-bootstrap (random order, N={N_BOOT_TRADE})…")
    boot_random = trade_bootstrap(trades, N_BOOT_TRADE, "random", RNG)

    summ_chrono = summarize(boot_chrono)
    summ_random = summarize(boot_random)

    print("[gate2] block-bootstrap (90d, N=200) over daily P&L…")
    md_daily = mission_d_daily_pnl(trades)
    boot_block = block_bootstrap_daily(md_daily, N_BOOT_BLOCK, BLOCK_DAYS, RNG)
    summ_block = summarize(boot_block) if boot_block else {}

    print("[gate2] dedup vs existing Tier A strategies…")
    dedup = dedup_check(md_daily)

    # ---- verdict ----
    # Use chrono bootstrap as primary
    s = summ_chrono
    def _m(k, field):
        v = s.get(k)
        return v[field] if v else float("nan")

    mean_sharpe = _m("sharpe", "mean")
    mean_calmar = _m("calmar", "mean")
    p5_calmar = _m("calmar", "p5")
    mean_alpha_t = _m("alpha_t", "mean")
    pct_pos = s.get("pct_trials", {}).get("total_return_gt_0", 0.0)

    corrs = [d.get("pearson_corr") for d in dedup.values() if d.get("status") == "ok"]
    max_corr = max((abs(c) for c in corrs if c is not None), default=0.0)

    checks = {
        "mean_sharpe_gt_1.0": mean_sharpe > PASS["mean_sharpe_gt"],
        "mean_calmar_gt_3.0": mean_calmar > PASS["mean_calmar_gt"],
        "p5_calmar_gt_1.0": p5_calmar > PASS["p5_calmar_gt"],
        "pct_positive_gt_0.75": pct_pos > PASS["pct_positive_gt"],
        "mean_alpha_t_gt_1.5": mean_alpha_t > PASS["mean_alpha_t_gt"],
        "max_corr_lt_0.5": max_corr < PASS["max_corr_lt"],
    }
    n_pass = sum(checks.values())
    if n_pass == 6:
        verdict = "PROMOTE"
    elif n_pass in (4, 5):
        verdict = "NEEDS_TUNING"
    else:
        verdict = "KILL"

    output = {
        "config": {
            "threshold": 2.0, "hold_hours": 72, "direction_mode": "long_only",
            "tokens": TOKENS, "capital": CAPITAL,
            "n_boot_trade": N_BOOT_TRADE, "n_boot_block": N_BOOT_BLOCK,
            "block_days": BLOCK_DAYS,
        },
        "single_window": {
            "n_trades": int(len(trades)),
            "sharpe": single_window_metrics["sharpe"],
            "calmar": single_window_metrics["calmar"],
            "total_return_pct": single_window_metrics["total_return_pct"],
            "max_drawdown_pct": single_window_metrics["max_drawdown_pct"],
            "alpha_t_stat": single_window_metrics["alpha_t_stat"],
            "win_rate": single_window_metrics["win_rate"],
        },
        "bootstrap_trade_chrono": summ_chrono,
        "bootstrap_trade_random": summ_random,
        "bootstrap_block_90d": summ_block,
        "dedup": dedup,
        "max_abs_corr_existing": max_corr,
        "pass_criteria": PASS,
        "checks": checks,
        "n_checks_passed": n_pass,
        "verdict": verdict,
    }

    OUT_RESULTS.write_text(json.dumps(output, indent=2, default=str))
    print(f"\n[gate2] saved → {OUT_RESULTS}")
    print(f"[gate2] verdict: {verdict}  ({n_pass}/6 checks passed)")
    print(f"  mean sharpe     {mean_sharpe:+.2f}  (need >1.0)")
    print(f"  mean calmar     {mean_calmar:+.2f}  (need >3.0)")
    print(f"  p5 calmar       {p5_calmar:+.2f}  (need >1.0)")
    print(f"  mean alpha_t    {mean_alpha_t:+.2f}  (need >1.5)")
    print(f"  pct positive    {pct_pos*100:.1f}%  (need >75%)")
    print(f"  max abs corr    {max_corr:.3f}  (need <0.5)")


if __name__ == "__main__":
    main()
