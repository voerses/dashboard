"""
Fragility Overlay Evaluation (Gate 1)
--------------------------------------
Post-hoc sizing overlay on s513 / s523c trade logs.

Method:
  - Each trade has entry_bar (hourly bar index from backtest start).
  - Backtest runs 12 months ending at --end-date 2026-04-05T16:00:00.
  - Map entry_bar -> timestamp by taking (end_date - 12 months) + entry_bar*1h.
  - Look up fragility_index at that date; compute size multiplier.
  - Scale pnl and fees by multiplier; leave funding unchanged (it scales with
    notional so also scale it). Rebuild running equity as initial_capital +
    cumsum(scaled_net_pnl) in trade exit order -> compute metrics.

Note on scope: this is a coarse post-hoc proxy — it ignores that scaled trades
would have freed portfolio slots for other trades, and concurrent trade
equity interactions. Gate 1 is meant to be cheap; if the overlay clearly
improves metrics, a full re-simulation is Gate 2's job.

Rules tested:
  A. linear:     mult = 1 - frag
  B. threshold:  mult = 0 if frag > 0.7 else 1
  C. tiered:     mult = 1.0 if frag<=0.45, 0.5 if 0.45<frag<=0.65, 0.0 else
"""
import json
import os
import sys
from decimal import Decimal

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRAG_PATH = os.path.join(ROOT, "research/fragility_index.parquet")


def load_trades(trades_path):
    with open(trades_path) as f:
        raw = json.load(f)
    df = pd.DataFrame(raw)
    for col in ["pnl", "entry_price", "exit_price", "exit_fee", "funding_cost"]:
        if col in df.columns:
            df[col] = df[col].astype(float)
    df["entry_fee"] = df["entry_fee"].astype(float)
    df["margin_usd"] = df["margin_usd"].astype(float)
    return df


def load_equity_curve(path):
    with open(path) as f:
        eq = json.load(f)
    s = pd.Series({pd.Timestamp(k): float(v) for k, v in eq.items()}).sort_index()
    return s


def assign_timestamps(trades, end_date, months=12):
    # Backtest start = end_date - months*30.4375 days? Use MonthBegin offset
    end_ts = pd.Timestamp(end_date)
    start_ts = end_ts - pd.DateOffset(months=months)
    # Bars are hourly
    trades = trades.copy()
    trades["entry_ts"] = start_ts + pd.to_timedelta(trades["entry_bar"], unit="h")
    trades["exit_ts"] = start_ts + pd.to_timedelta(trades["exit_bar"], unit="h")
    return trades, start_ts, end_ts


def attach_fragility(trades, frag_df):
    frag = frag_df["frag_idx"].copy()
    frag.index = pd.to_datetime(frag.index).normalize()
    # reindex to daily full range with ffill so any trade date resolves
    full = pd.date_range(frag.index.min(), pd.Timestamp("2026-04-30"), freq="D")
    frag = frag.reindex(full).ffill()
    trades["entry_date"] = trades["entry_ts"].dt.normalize()
    trades["frag"] = trades["entry_date"].map(frag)
    # Any NaN (before fragility data starts) -> assume median 0.5
    trades["frag"] = trades["frag"].fillna(0.5)
    return trades


def rule_linear(frag):
    return np.clip(1.0 - frag, 0.0, 1.0)


def rule_threshold(frag, thresh=0.7):
    return np.where(frag > thresh, 0.0, 1.0)


def rule_tiered(frag):
    mult = np.ones_like(frag)
    mult[(frag > 0.45) & (frag <= 0.65)] = 0.5
    mult[frag > 0.65] = 0.0
    return mult


def compute_metrics(trades_sorted, initial_capital, start_ts, end_ts, pnl_col="net_pnl"):
    """Build a daily equity curve by summing pnl at trade exit dates.
    Returns dict of metrics.
    """
    trades_sorted = trades_sorted.copy()
    trades_sorted["exit_date"] = trades_sorted["exit_ts"].dt.normalize()
    # Sum pnl by exit date
    daily_pnl = trades_sorted.groupby("exit_date")[pnl_col].sum()
    # Build full daily index
    idx = pd.date_range(start_ts.normalize(), end_ts.normalize(), freq="D")
    daily_pnl = daily_pnl.reindex(idx, fill_value=0.0)
    equity = initial_capital + daily_pnl.cumsum()
    # Metrics
    rets = equity.pct_change().fillna(0.0)
    total_return = equity.iloc[-1] / initial_capital - 1.0
    # Annualized (daily rets)
    days = len(rets)
    ann_factor = 365.0
    mean = rets.mean() * ann_factor
    std = rets.std(ddof=0) * np.sqrt(ann_factor)
    sharpe = mean / std if std > 1e-12 else 0.0
    # Max drawdown
    peak = equity.cummax()
    dd = (equity - peak) / peak
    maxdd = dd.min()
    # annualized return
    years = days / 365.0
    ann_ret = (equity.iloc[-1] / initial_capital) ** (1.0 / max(years, 1e-9)) - 1.0
    calmar = ann_ret / abs(maxdd) if maxdd < 0 else np.inf
    return {
        "total_return_pct": total_return * 100,
        "annualized_return_pct": ann_ret * 100,
        "sharpe": sharpe,
        "max_drawdown_pct": maxdd * 100,
        "calmar": calmar,
        "final_equity": float(equity.iloc[-1]),
        "trades": int((trades_sorted[pnl_col].abs() > 1e-9).sum()),
    }


def evaluate_strategy(strategy_name, trades_path, equity_path, frag_df,
                      end_date, initial_capital=50000.0, months=12):
    trades = load_trades(trades_path)
    trades, start_ts, end_ts = assign_timestamps(trades, end_date, months)
    trades = attach_fragility(trades, frag_df)

    # Net pnl per trade (backtest book): pnl already nets fees? metrics file
    # shows total_fees & total_funding separately, and total_return implies
    # they ARE netted. We'll scale all of pnl.
    trades["net_pnl"] = trades["pnl"]

    # Sort by exit order
    trades = trades.sort_values("exit_ts").reset_index(drop=True)

    print(f"\n=== {strategy_name} ===")
    print(f"  trades: {len(trades)}  date range: {trades['entry_ts'].min()} -> {trades['exit_ts'].max()}")
    print(f"  frag at entries: mean={trades['frag'].mean():.3f} median={trades['frag'].median():.3f}")
    print(f"  trades with frag>0.7: {(trades['frag']>0.7).sum()}")

    # Baseline (no scaling): recompute with pnl as-is
    results = {}
    results["baseline"] = compute_metrics(trades, initial_capital, start_ts, end_ts, "net_pnl")

    # Rule A: linear
    for rule_name, rule_fn in [
        ("linear (1-frag)", rule_linear),
        ("threshold (frag>0.7 -> 0)", rule_threshold),
        ("tiered (0.45/0.65)", rule_tiered),
    ]:
        mult = rule_fn(trades["frag"].values)
        trades[f"pnl_{rule_name}"] = trades["net_pnl"] * mult
        m = compute_metrics(trades, initial_capital, start_ts, end_ts, f"pnl_{rule_name}")
        m["avg_mult"] = float(mult.mean())
        results[rule_name] = m

    return results, trades


def print_results_table(strategy, results):
    print(f"\n--- {strategy} overlay results ---")
    cols = ["rule", "Return%", "Sharpe", "MaxDD%", "Calmar", "FinalEquity", "AvgMult"]
    print(" | ".join(f"{c:>22}" for c in cols))
    print("-" * 170)
    for name, m in results.items():
        row = [
            name,
            f"{m['total_return_pct']:+.1f}",
            f"{m['sharpe']:.2f}",
            f"{m['max_drawdown_pct']:.1f}",
            f"{m['calmar']:.2f}",
            f"${m['final_equity']:,.0f}",
            f"{m.get('avg_mult', 1.0):.3f}",
        ]
        print(" | ".join(f"{x:>22}" for x in row))


def main():
    frag_df = pd.read_parquet(FRAG_PATH)
    end_date = "2026-04-05T16:00:00"

    s513_res, s513_trades = evaluate_strategy(
        "s513_triple_trigger_swing",
        "research/fragility_out/s513_baseline/s513_triple_trigger_swing_12mo_50k_trades.json",
        "research/fragility_out/s513_baseline/s513_triple_trigger_swing_12mo_50k_equity_curve.json",
        frag_df, end_date,
    )
    print_results_table("s513", s513_res)

    s523_res, s523_trades = evaluate_strategy(
        "s523c_growth",
        "research/fragility_out/s523c_baseline/s523c_growth_12mo_50k_trades.json",
        "research/fragility_out/s523c_baseline/s523c_growth_12mo_50k_equity_curve.json",
        frag_df, end_date,
    )
    print_results_table("s523c", s523_res)

    # Save results
    combined = {"s513": s513_res, "s523c": s523_res}
    with open("research/fragility_overlay_results.json", "w") as f:
        json.dump(combined, f, indent=2, default=str)
    print("\nSaved -> research/fragility_overlay_results.json")


if __name__ == "__main__":
    main()
