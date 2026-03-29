#!/workspace/venv/bin/python
"""
R172 Gate 3P Validation Runner
================================
Runs s500_r172_portfolio through the raw backtest harness with:
  1. BTC only (quick sanity check)
  2. Top 20 by ADV (core universe)
  3. Full universe (all tokens with ADV > $2M)
  4. Parameter sensitivity (+/-20% on key params)

All P&L goes through tools/raw_backtest.py -- no standalone equity curves.
"""

import sys
import os
import time
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

sys.path.insert(0, "/workspace/crypto_backtest")
from tools.raw_backtest import Backtest
from strategies.s500_r172_portfolio import run as run_r172

# ══════════════════════════════════════════════════════════════════════
# COMMON HARNESS PARAMS
# ══════════════════════════════════════════════════════════════════════
CAPITAL = 100_000
FEE_BPS = 7
MARKET = "perp"
LEVERAGE_MAX = 2.5
START = "2024-01-01"
END = "2026-03-17"


def make_bt():
    """Create a fresh Backtest instance."""
    return Backtest(
        capital=CAPITAL,
        fee_bps=FEE_BPS,
        market=MARKET,
        leverage_max=LEVERAGE_MAX,
        start=START,
        end=END,
    )


def get_top_tokens_by_adv(n=20, min_adv=2_000_000):
    """Get top N tokens by ADV at backtest start date."""
    from tools.raw_backtest import DataCache
    dc = DataCache(MARKET)
    token_adv = {}
    start_ts = pd.Timestamp(START)
    for token in dc.available_tokens():
        try:
            adv = dc.adv_at(token, start_ts)
            if adv >= min_adv:
                token_adv[token] = adv
        except Exception:
            continue
    sorted_tokens = sorted(token_adv.items(), key=lambda x: -x[1])
    return {t[0] for t in sorted_tokens[:n]}


def get_full_universe(min_adv=2_000_000):
    """Get all tokens with ADV > threshold."""
    from tools.raw_backtest import DataCache
    dc = DataCache(MARKET)
    tokens = set()
    start_ts = pd.Timestamp(START)
    for token in dc.available_tokens():
        try:
            adv = dc.adv_at(token, start_ts)
            if adv >= min_adv:
                tokens.add(token)
        except Exception:
            continue
    return tokens


# ══════════════════════════════════════════════════════════════════════
# TEST 1: BTC Only (Quick Sanity Check)
# ══════════════════════════════════════════════════════════════════════
print("=" * 80)
print(" TEST 1: BTC ONLY (Quick Sanity Check)")
print("=" * 80)
print()

t0 = time.time()
bt1 = make_bt()
bt1 = run_r172(bt1, token_filter={"BTC"}, verbose=True)
r1 = bt1.report("R172 — BTC Only")
print(f"\nTime: {time.time() - t0:.1f}s")
print(f"Verdict: {r1['verdict']}")
if r1["kill_reasons"]:
    for kr in r1["kill_reasons"]:
        print(f"  KILL: {kr}")
print()


# ══════════════════════════════════════════════════════════════════════
# TEST 2: Top 20 by ADV (Core Universe)
# ══════════════════════════════════════════════════════════════════════
print("=" * 80)
print(" TEST 2: TOP 20 TOKENS BY ADV (Core Universe)")
print("=" * 80)
print()

top20 = get_top_tokens_by_adv(n=20)
print(f"Top 20 tokens: {sorted(top20)}")
print()

t0 = time.time()
bt2 = make_bt()
bt2 = run_r172(bt2, token_filter=top20, verbose=True)
r2 = bt2.report("R172 — Top 20 by ADV")
print(f"\nTime: {time.time() - t0:.1f}s")
print(f"Verdict: {r2['verdict']}")
if r2["kill_reasons"]:
    for kr in r2["kill_reasons"]:
        print(f"  KILL: {kr}")
print()


# ══════════════════════════════════════════════════════════════════════
# TEST 3: Full Universe (ADV > $2M)
# ══════════════════════════════════════════════════════════════════════
print("=" * 80)
print(" TEST 3: FULL UNIVERSE (ADV > $2M)")
print("=" * 80)
print()

full_univ = get_full_universe(min_adv=2_000_000)
print(f"Full universe: {len(full_univ)} tokens")
print()

t0 = time.time()
bt3 = make_bt()
bt3 = run_r172(bt3, token_filter=full_univ, verbose=True)
r3 = bt3.report("R172 — Full Universe (ADV > $2M)")
print(f"\nTime: {time.time() - t0:.1f}s")
print(f"Verdict: {r3['verdict']}")
if r3["kill_reasons"]:
    for kr in r3["kill_reasons"]:
        print(f"  KILL: {kr}")
print()


# ══════════════════════════════════════════════════════════════════════
# TEST 4: No Token Filter (Strategy's Own Universe Selection)
# ══════════════════════════════════════════════════════════════════════
print("=" * 80)
print(" TEST 4: DEFAULT UNIVERSE (Strategy's Own Selection)")
print("=" * 80)
print()

t0 = time.time()
bt4 = make_bt()
bt4 = run_r172(bt4, token_filter=None, verbose=True)
r4 = bt4.report("R172 — Default Universe")
print(f"\nTime: {time.time() - t0:.1f}s")
print(f"Verdict: {r4['verdict']}")
if r4["kill_reasons"]:
    for kr in r4["kill_reasons"]:
        print(f"  KILL: {kr}")
print()


# ══════════════════════════════════════════════════════════════════════
# TEST 5: Parameter Sensitivity
# ══════════════════════════════════════════════════════════════════════
print("=" * 80)
print(" TEST 5: PARAMETER SENSITIVITY (+/-20%)")
print("=" * 80)
print()

# Get the baseline Calmar from the default run
baseline_l12m = r4["windows"]["L12M"] if r4 and "windows" in r4 else {}
baseline_calmar = baseline_l12m.get("calmar", 0)
baseline_sharpe = baseline_l12m.get("sharpe", 0)
baseline_ret = baseline_l12m.get("total_ret", 0)

print(f"Baseline L12M: Calmar={baseline_calmar:.2f}, Sharpe={baseline_sharpe:.2f}, "
      f"Return={baseline_ret:+.1%}")
print()

sensitivity_results = []

# Define parameter variations
param_tests = [
    # (name, param_name, values)
    ("BB Period", "bb_period", [16, 20, 24]),
    ("BB Std", "bb_std", [1.6, 2.0, 2.4]),
    ("Vol Threshold", "vol_threshold", [1.04, 1.3, 1.56]),
    ("Mom Lookback 4H", "mom_lookback_4h", [34, 42, 50]),
    ("Weight Split", None, [(0.70, 0.30), (0.80, 0.20), (0.90, 0.10)]),
]

for test_name, param_name, values in param_tests:
    print(f"\n--- {test_name} ---")
    for val in values:
        kwargs = {}

        if test_name == "Weight Split":
            w_brk, w_mom = val
            kwargs["w_breakout"] = w_brk
            kwargs["w_momentum"] = w_mom
            label = f"{test_name}={w_brk:.0%}/{w_mom:.0%}"
        else:
            kwargs[param_name] = val
            label = f"{test_name}={val}"

        print(f"  Testing {label}...", end=" ", flush=True)
        t0 = time.time()
        bt_s = make_bt()
        bt_s = run_r172(bt_s, verbose=False, **kwargs)
        r_s = bt_s.report(f"Sensitivity: {label}")

        l12m = r_s["windows"]["L12M"] if r_s and "windows" in r_s else {}
        cal = l12m.get("calmar", 0)
        sha = l12m.get("sharpe", 0)
        ret = l12m.get("total_ret", 0)
        dd = l12m.get("maxdd", 0)

        delta_calmar = (cal - baseline_calmar) / baseline_calmar * 100 if baseline_calmar != 0 else 0
        elapsed = time.time() - t0

        sensitivity_results.append({
            "param": label,
            "calmar": cal,
            "sharpe": sha,
            "return": ret,
            "maxdd": dd,
            "delta_calmar_pct": delta_calmar,
            "verdict": r_s.get("verdict", "?"),
        })

        degraded = "DEGRADED >30%" if delta_calmar < -30 else "OK"
        print(f"Calmar={cal:.2f} ({delta_calmar:+.1f}%) Sharpe={sha:.2f} "
              f"Return={ret:+.1%} MaxDD={dd:+.1%} [{degraded}] ({elapsed:.0f}s)")

# ── Sensitivity Summary ──
print("\n" + "=" * 80)
print(" PARAMETER SENSITIVITY SUMMARY")
print("=" * 80)
print()
print(f"{'Parameter':<30s}  {'Calmar':>8s}  {'Delta%':>8s}  {'Sharpe':>8s}  "
      f"{'Return':>10s}  {'MaxDD':>8s}  {'Status':>12s}")
print("-" * 95)
for row in sensitivity_results:
    status = "DEGRADED" if row["delta_calmar_pct"] < -30 else "OK"
    print(f"{row['param']:<30s}  {row['calmar']:>8.2f}  {row['delta_calmar_pct']:>+7.1f}%  "
          f"{row['sharpe']:>8.2f}  {row['return']:>+9.1%}  {row['maxdd']:>+7.1%}  "
          f"{status:>12s}")

any_degraded = any(r["delta_calmar_pct"] < -30 for r in sensitivity_results)
print()
if any_degraded:
    print("WARNING: Some parameters show >30% Calmar degradation at +/-20% variation.")
    print("Strategy may be overfit to specific parameter values.")
else:
    print("PASS: All parameter variations maintain Calmar within 30% of baseline.")
    print("Strategy shows robust parameter sensitivity.")


# ══════════════════════════════════════════════════════════════════════
# FINAL SUMMARY
# ══════════════════════════════════════════════════════════════════════
print("\n" + "=" * 80)
print(" GATE 3P FINAL SUMMARY")
print("=" * 80)
print()
print(f"{'Test':<40s}  {'Verdict':>8s}  {'L12M Ret':>10s}  {'Sharpe':>8s}  "
      f"{'Calmar':>8s}  {'MaxDD':>8s}")
print("-" * 90)

for test_name, result in [
    ("BTC Only", r1),
    ("Top 20 by ADV", r2),
    ("Full Universe (ADV>$2M)", r3),
    ("Default Universe", r4),
]:
    l12m = result["windows"]["L12M"] if result and "windows" in result else {}
    print(f"{test_name:<40s}  {result.get('verdict', '?'):>8s}  "
          f"{l12m.get('total_ret', 0):>+9.1%}  {l12m.get('sharpe', 0):>8.2f}  "
          f"{l12m.get('calmar', 0):>8.2f}  {l12m.get('maxdd', 0):>+7.1%}")

print()
print(f"Parameter robustness: {'FAIL' if any_degraded else 'PASS'} "
      f"(Calmar within 30% at +/-20% parameter variation)")
print()

# Overall Gate 3P verdict
default_verdict = r4.get("verdict", "KILL")
if default_verdict == "PASS" and not any_degraded:
    print("GATE 3P VERDICT: PASS")
    print("Strategy is ready for Gate 4 (Live Paper Trading).")
elif default_verdict == "PASS":
    print("GATE 3P VERDICT: CONDITIONAL PASS")
    print("Strategy passes core metrics but shows parameter sensitivity.")
else:
    print(f"GATE 3P VERDICT: FAIL")
    print(f"Default universe verdict: {default_verdict}")
    if r4.get("kill_reasons"):
        for kr in r4["kill_reasons"]:
            print(f"  - {kr}")

print("\n" + "=" * 80)
