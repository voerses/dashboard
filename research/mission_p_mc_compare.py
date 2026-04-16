"""Monte Carlo comparison: original Gate 1 params vs sweep-picked variants."""
import sys, json
sys.path.insert(0, '/workspace/crypto_backtest')
sys.path.insert(0, '/workspace/crypto_backtest/research')
import numpy as np
from mission_p_full_sweep import (
    load_trades, load_ohlcv, build_snapshots, simulate_with_equity,
    compute_equity_metrics, BACKTEST_START_12, BACKTEST_END_12, COST_50BPS, TRADES_12
)

print("Loading...")
trades = load_trades(TRADES_12, BACKTEST_START_12)
tokens = sorted({t["token"] for t in trades})
ohlcv = {tok: load_ohlcv(tok) for tok in tokens}

def mc_trial(trades, ohlcv, bs, be, params, n_trials=80, seed=42):
    rng = np.random.default_rng(seed)
    n = len(trades)
    deltas = []
    for trial in range(n_trials):
        idx = rng.integers(0, n, size=n)
        sampled = sorted([trades[i] for i in idx], key=lambda t: t["entry_dt"])
        snaps = build_snapshots(sampled, ohlcv, bs, be, params["grace"])
        base = compute_equity_metrics(sampled, {}, COST_50BPS, bs, be)
        r = simulate_with_equity(sampled, snaps, params["breadth"], params["throttle"],
                                  params["min_basket"], COST_50BPS, bs, be,
                                  close_mode=params["close_mode"])
        cd = ((r["calmar"] - base["calmar"]) / base["calmar"] * 100) if base["calmar"] != 0 else 0
        deltas.append(cd)
        if (trial+1) % 20 == 0:
            print(f"    {trial+1}/{n_trials}, mean={np.mean(deltas):+.1f}%", flush=True)
    a = np.array(deltas)
    return {
        "mean": float(a.mean()),
        "median": float(np.median(a)),
        "stdev": float(a.std()),
        "p05": float(np.percentile(a, 5)),
        "p95": float(np.percentile(a, 95)),
        "pct_positive": float((a > 0).sum() / len(a) * 100),
    }

variants = [
    ("Gate1 validated", {"grace": 3, "breadth": 0.80, "throttle": 14, "min_basket": 5, "close_mode": "whole_basket"}),
    ("Sweep H2 best", {"grace": 2, "breadth": 0.80, "throttle": 21, "min_basket": 3, "close_mode": "whole_basket"}),
    ("Sweep underwater", {"grace": 2, "breadth": 0.75, "throttle": 21, "min_basket": 5, "close_mode": "underwater_only"}),
]

results = {}
for name, params in variants:
    print(f"\n=== {name} ===")
    print(f"    {params}")
    results[name] = mc_trial(trades, ohlcv, BACKTEST_START_12, BACKTEST_END_12, params, n_trials=80)

print("\n" + "="*80)
print("Comparison (80-trial Monte Carlo)")
print("="*80)
print(f"{'Variant':<20} {'Mean':>10} {'Median':>10} {'Stdev':>10} {'P5':>10} {'P95':>10} {'%pos':>8}")
for name, r in results.items():
    print(f"{name:<20} {r['mean']:>+9.1f}% {r['median']:>+9.1f}% {r['stdev']:>9.1f}% "
          f"{r['p05']:>+9.1f}% {r['p95']:>+9.1f}% {r['pct_positive']:>7.1f}%")

# Aggressive sweep best from earlier run
print(f"{'Aggressive (br65 mb3)':<20} {-22.3:>+9.1f}% {-29.6:>+9.1f}% {45.6:>9.1f}% "
      f"{-83.7:>+9.1f}% {42.0:>+9.1f}% {28.0:>7.1f}%  [from previous run]")

with open('/workspace/crypto_backtest/research/mission_p_mc_comparison.json', 'w') as f:
    json.dump(results, f, indent=2)
print("\nSaved → research/mission_p_mc_comparison.json")
