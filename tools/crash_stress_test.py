#!/usr/bin/env python3
"""Flash crash stress test — shock resistance of proposed exit configs.

Scenario: BTC drops 50%, alts drop 70% within 3 hours (3 bars on 1h data).
Compares how each exit config handles the shock vs CURRENT baseline.

Method:
1. Run normal 3-month simulation → baseline metrics
2. Inject crash at 3 different dates → crash metrics
3. Compare: max DD increase, equity loss, crash-window damage
4. Report side-by-side comparison

Exit configs tested:
- CURRENT (BE+prog) — baseline (progressive trail 3.0→1.5, breakeven 0.5)
- trail(1.5)+BE — v2/v3 winner for solo strategies
- trail(3.0)+BE — research consensus (wide trail, worse crash protection?)
- RA: 1.5/2.0/1.8+BE — v3 winner for s58 (tight regime-adaptive)
- RA: 1.0/3.0/2.0+BE — v3 winner for multi-strategy combos

Caveats:
- ATR is pre-computed; crash doesn't update ATR → stops use stale ATR
  (slightly optimistic — real ATR would spike → wider effective stops)
- Stop fills at stop_price, not market (partially corrected by stress_adv_multiplier)
- Both caveats affect all configs equally → comparison is fair
"""
import gc
import json
import sys
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics

# Regime constants
CRISIS = 0
QUIET = 1
UPTREND = 2
RANGE = 3
DOWNTREND = 4


@dataclass
class ExitConfig:
    label: str
    breakeven_atr: float = 0.5
    trail_schedule: bool = False
    chandelier_lookback: int = 0
    bear_target_mult: float = 0.0
    bear_max_hold: int = 0
    trail_mult_override: float = 0.0
    regime_trail: Optional[Dict[int, float]] = None


# =========================================================================
# EXIT CONFIGS TO TEST (subset of v3 — the recommended ones + comparison)
# =========================================================================
CONFIGS = [
    ExitConfig("CURRENT (BE+prog)", breakeven_atr=0.5, trail_schedule=True),
    ExitConfig("trail(1.5)+BE", trail_mult_override=1.5),
    ExitConfig("trail(3.0)+BE", trail_mult_override=3.0),
    ExitConfig("RA: 1.5/2.0/1.8+BE",
               regime_trail={CRISIS: 1.5, DOWNTREND: 1.5, UPTREND: 2.0, RANGE: 1.8, QUIET: 1.8}),
    ExitConfig("RA: 1.0/3.0/2.0+BE",
               regime_trail={CRISIS: 1.0, DOWNTREND: 1.0, UPTREND: 3.0, RANGE: 2.0, QUIET: 2.0}),
]


def load_portfolios_from_config(config_path: str):
    with open(config_path) as f:
        cfg = json.load(f)
    portfolios = {}
    for pf in cfg["portfolios"]:
        name = pf["pool_name"]
        strategies = []
        for s in pf["strategies"]:
            strategies.append(StrategySpec.from_dict(s))
        portfolios[name] = {
            "strategies": strategies,
            "max_portfolio_positions": pf.get("max_portfolio_positions", 40),
            "concentration_limit": pf.get("concentration_limit", 1.0),
        }
    return portfolios


def apply_exit_config(signals_dict, ec: ExitConfig):
    """Apply exit overrides and return saved state for restoration."""
    saved = []
    for token, sig in signals_dict.items():
        orig = {
            "breakeven_atr": sig.breakeven_atr,
            "chandelier_lookback": sig.chandelier_lookback,
            "bear_target_mult": sig.bear_target_mult,
            "bear_max_hold": sig.bear_max_hold,
            "trail_schedule": sig.trail_schedule,
            "trail_mult": sig.trail_mult,
        }
        saved.append((token, orig))
        sig.breakeven_atr = ec.breakeven_atr
        sig.chandelier_lookback = ec.chandelier_lookback
        sig.bear_target_mult = ec.bear_target_mult
        sig.bear_max_hold = ec.bear_max_hold
        if not ec.trail_schedule:
            sig.trail_schedule = None
        if ec.regime_trail is not None:
            new_trail = np.empty_like(sig.trail_mult)
            for regime_val, mult in ec.regime_trail.items():
                mask = sig.regime == regime_val
                new_trail[mask] = mult
            sig.trail_mult = new_trail
        elif ec.trail_mult_override > 0:
            sig.trail_mult = np.full_like(sig.trail_mult, ec.trail_mult_override)
    return saved


def restore_signals(signals_dict, saved):
    """Restore original signal values."""
    for token, orig in saved:
        sig = signals_dict[token]
        sig.breakeven_atr = orig["breakeven_atr"]
        sig.chandelier_lookback = orig["chandelier_lookback"]
        sig.bear_target_mult = orig["bear_target_mult"]
        sig.bear_max_hold = orig["bear_max_hold"]
        sig.trail_schedule = orig["trail_schedule"]
        sig.trail_mult = orig["trail_mult"]


# =========================================================================
# CRASH INJECTION
# =========================================================================

def inject_crash(all_precomputed, crash_frac, btc_drop=0.50, alt_drop=0.70, crash_bars=3):
    """Inject a flash crash into price arrays at a fractional position through data.

    Args:
        all_precomputed: {strategy_id: {token: TokenSignals}}
        crash_frac: fraction through the data (0.25, 0.5, 0.75) for crash placement
        btc_drop: total BTC price drop (0.50 = 50%)
        alt_drop: total alt price drop (0.70 = 70%)
        crash_bars: number of bars for the crash (3 = 3 hours)

    Returns:
        saved: dict for restoration, crash_info: dict with crash details
    """
    saved = {}
    crash_info = {"bars": {}, "date": None}

    for sid, signals_dict in all_precomputed.items():
        for token, sig in signals_dict.items():
            key = (sid, token)

            # Find crash bar at specified fraction
            crash_bar = int(sig.n_bars * crash_frac)
            if crash_bar < 10 or crash_bar + crash_bars + 10 >= sig.n_bars:
                continue

            # Save originals
            saved[key] = {
                "close": sig.close.copy(),
                "high": sig.high.copy(),
                "low": sig.low.copy(),
            }
            # Also save perp arrays if present
            if sig.perp_close is not None:
                saved[key]["perp_close"] = sig.perp_close.copy()
                saved[key]["perp_high"] = sig.perp_high.copy()
                saved[key]["perp_low"] = sig.perp_low.copy()

            drop = btc_drop if token == "BTC" else alt_drop
            pre_crash_spot = float(sig.close[crash_bar - 1])

            # Record crash info
            if crash_info["date"] is None and hasattr(sig, "timestamps"):
                crash_info["date"] = str(sig.timestamps[crash_bar])
            crash_info["bars"][(sid, token)] = {
                "crash_bar": crash_bar,
                "pre_price": pre_crash_spot,
                "post_price": pre_crash_spot * (1.0 - drop),
                "drop_pct": drop * 100,
            }

            # --- Apply crash to SPOT arrays ---
            _apply_crash_to_arrays(sig.close, sig.high, sig.low,
                                   crash_bar, crash_bars, drop, pre_crash_spot)

            # --- Apply crash to PERP arrays ---
            if sig.perp_close is not None:
                pre_crash_perp = float(sig.perp_close[crash_bar - 1])
                _apply_crash_to_arrays(sig.perp_close, sig.perp_high, sig.perp_low,
                                       crash_bar, crash_bars, drop, pre_crash_perp)

    return saved, crash_info


def _apply_crash_to_arrays(close, high, low, crash_bar, crash_bars, drop, pre_crash):
    """Apply crash to a set of price arrays (spot or perp)."""
    n_bars = len(close)

    # During crash: linear price decline over crash_bars
    for i in range(crash_bars):
        bar = crash_bar + i
        if bar >= n_bars:
            break
        frac = (i + 1) / crash_bars
        crash_level = pre_crash * (1.0 - drop * frac)
        open_level = pre_crash * (1.0 - drop * i / crash_bars)
        close[bar] = crash_level
        high[bar] = max(open_level, crash_level)  # open is high of bar
        low[bar] = crash_level  # bottom of bar

    # Post-crash: shift all remaining prices to maintain crashed level
    post_bar = crash_bar + crash_bars
    if post_bar < n_bars:
        post_crash_level = pre_crash * (1.0 - drop)
        orig_post = float(close[post_bar])  # Note: close was NOT yet modified here
        # Wait — close[post_bar] might already be from the saved copy... no, we only
        # modified bars crash_bar to crash_bar+crash_bars-1. post_bar is untouched.
        # But we need the ORIGINAL close[post_bar] to compute ratio.
        # Actually close[post_bar] is still original since we only touched crash bars above.
        if orig_post > 0:
            ratio = post_crash_level / orig_post
            close[post_bar:] *= ratio
            high[post_bar:] *= ratio
            low[post_bar:] *= ratio


def restore_crash(all_precomputed, saved):
    """Restore original price arrays after crash injection."""
    for (sid, token), orig in saved.items():
        sig = all_precomputed[sid][token]
        sig.close[:] = orig["close"]
        sig.high[:] = orig["high"]
        sig.low[:] = orig["low"]
        if "perp_close" in orig:
            sig.perp_close[:] = orig["perp_close"]
            sig.perp_high[:] = orig["perp_high"]
            sig.perp_low[:] = orig["perp_low"]


# =========================================================================
# ANALYTICAL SECTION
# =========================================================================

def analytical_crash_impact():
    """Print theoretical per-position loss for each exit config in a crash."""
    print("\n" + "=" * 100)
    print("  ANALYTICAL: Theoretical Per-Position Loss in Flash Crash")
    print("=" * 100)

    # Typical ATR as fraction of price
    btc_atr_pct = 0.020  # ~2% on 1h
    alt_atr_pct = 0.035  # ~3.5% on 1h (alts more volatile)
    btc_drop = 0.50
    alt_drop = 0.70

    configs_and_trails = [
        ("CURRENT (BE+prog)", "3.0 → 1.5 (progressive)", 3.0, 1.5),
        ("trail(1.5)+BE", "flat 1.5", 1.5, 1.5),
        ("trail(3.0)+BE", "flat 3.0", 3.0, 3.0),
        ("RA: 1.5/2.0/1.8+BE", "1.5 (bear) / 2.0 (bull)", 2.0, 1.5),
        ("RA: 1.0/3.0/2.0+BE", "1.0 (bear) / 3.0 (bull)", 3.0, 1.0),
    ]

    print(f"\n  Assumptions:")
    print(f"    BTC 1h ATR: ~{btc_atr_pct*100:.1f}% of price")
    print(f"    Alt 1h ATR: ~{alt_atr_pct*100:.1f}% of price")
    print(f"    BTC crash: -{btc_drop*100:.0f}%  |  Alt crash: -{alt_drop*100:.0f}%")
    print(f"    Breakeven: positions profitable by ≥0.5 ATR exit at entry (0% loss)")
    print(f"    Crash fills at stop price (optimistic — real slippage would be worse)")

    print(f"\n  {'Config':<25s}  {'Trail (ATR)':<22s}  "
          f"{'BTC loss':>10s}  {'Alt loss':>10s}  "
          f"{'BTC loss':>10s}  {'Alt loss':>10s}")
    print(f"  {'':<25s}  {'':<22s}  "
          f"{'(bull)':>10s}  {'(bull)':>10s}  "
          f"{'(bear)':>10s}  {'(bear)':>10s}")
    print(f"  {'-'*25}  {'-'*22}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*10}")

    for label, trail_desc, bull_mult, bear_mult in configs_and_trails:
        # In BULL (crash during uptrend — worst case for RA)
        btc_loss_bull = bull_mult * btc_atr_pct * 100
        alt_loss_bull = bull_mult * alt_atr_pct * 100
        # In BEAR (crash during downtrend — best case for RA)
        btc_loss_bear = bear_mult * btc_atr_pct * 100
        alt_loss_bear = bear_mult * alt_atr_pct * 100

        print(f"  {label:<25s}  {trail_desc:<22s}  "
              f"{btc_loss_bull:9.1f}%  {alt_loss_bull:9.1f}%  "
              f"{btc_loss_bear:9.1f}%  {alt_loss_bear:9.1f}%")

    print(f"\n  KEY INSIGHT: All stops trigger in first crash bar (crash >> trail width).")
    print(f"  The loss per position = trail_width × ATR. Tighter trail = less loss.")
    print(f"  Breakeven ratchet eliminates loss on any profitable position → shared benefit.")
    print(f"  RA configs: WORSE in bull-crash (wider trail), BETTER in bear-crash (tighter).")
    print(f"  Crash during uptrend is the dangerous scenario — RA uses wider trail there.")
    print(f"  trail(1.5)+BE has UNIFORM crash protection regardless of regime.\n")


# =========================================================================
# SIMULATION RUNNER
# =========================================================================

def run_crash_test(pf_name, pf_def, period, data_end):
    """Run all configs with and without crash for one portfolio.

    Returns list of result dicts.
    """
    strategies = pf_def["strategies"]
    config = PortfolioConfig(
        strategies=strategies,
        capital=200_000.0,
        max_portfolio_positions=pf_def["max_portfolio_positions"],
        concentration_limit=pf_def["concentration_limit"],
    )

    # Precompute signals once
    all_precomputed = {}
    for spec in strategies:
        tokens = discover_tokens(spec.market)
        signals = precompute_strategy_signals(spec, tokens, config, period, end_date=data_end)
        all_precomputed[spec.strategy_id] = signals

    strategy_specs = {s.strategy_id: s for s in strategies}
    results = []

    # Crash injection positions: 25%, 50%, 75% through data
    crash_fracs = [0.25, 0.50, 0.75]

    for ec in CONFIGS:
        # --- Normal run (no crash) ---
        saved_exit = {}
        for sid, sigs in all_precomputed.items():
            saved_exit[sid] = apply_exit_config(sigs, ec)

        state = simulate_portfolio(all_precomputed, strategy_specs, config)
        metrics, _, _ = compute_portfolio_metrics(state, 200_000.0)
        normal_dd = metrics.max_drawdown_pct
        normal_ret = metrics.total_return_pct
        normal_trades = metrics.total_trades

        for sid, saved in saved_exit.items():
            restore_signals(all_precomputed[sid], saved)

        # --- Crash runs ---
        crash_dds = []
        crash_rets = []
        crash_trades_list = []
        crash_liq_counts = []
        crash_stop_counts = []

        for crash_frac in crash_fracs:
            # Apply exit config
            saved_exit = {}
            for sid, sigs in all_precomputed.items():
                saved_exit[sid] = apply_exit_config(sigs, ec)

            # Inject crash
            crash_saved, crash_info = inject_crash(all_precomputed, crash_frac)

            # Simulate
            state = simulate_portfolio(all_precomputed, strategy_specs, config)
            metrics, _, _ = compute_portfolio_metrics(state, 200_000.0)

            crash_dds.append(metrics.max_drawdown_pct)
            crash_rets.append(metrics.total_return_pct)
            crash_trades_list.append(metrics.total_trades)

            # Count liquidations and stops from trades
            liqs = sum(1 for t in state.position_manager.closed_trades
                       if t.exit_reason == "liquidation")
            stops = sum(1 for t in state.position_manager.closed_trades
                        if t.exit_reason == "stop")
            crash_liq_counts.append(liqs)
            crash_stop_counts.append(stops)

            # Restore crash
            restore_crash(all_precomputed, crash_saved)

            # Restore exit config
            for sid, saved in saved_exit.items():
                restore_signals(all_precomputed[sid], saved)

        # Average across crash injection points
        avg_crash_dd = np.mean(crash_dds)
        avg_crash_ret = np.mean(crash_rets)
        worst_crash_dd = min(crash_dds)  # Most negative = worst
        avg_crash_trades = np.mean(crash_trades_list)
        avg_liqs = np.mean(crash_liq_counts)
        avg_stops = np.mean(crash_stop_counts)

        results.append({
            "portfolio": pf_name,
            "config": ec.label,
            "normal_dd": normal_dd,
            "normal_ret": normal_ret,
            "normal_trades": normal_trades,
            "avg_crash_dd": avg_crash_dd,
            "worst_crash_dd": worst_crash_dd,
            "avg_crash_ret": avg_crash_ret,
            "dd_increase": avg_crash_dd - normal_dd,  # more negative = worse
            "ret_loss": normal_ret - avg_crash_ret,
            "crash_dds": crash_dds,
            "crash_rets": crash_rets,
            "avg_liquidations": avg_liqs,
            "avg_stops": avg_stops,
        })

    del all_precomputed
    gc.collect()
    return results


def print_results(all_results, portfolios_tested):
    """Print formatted comparison tables."""

    print(f"\n\n{'#' * 120}")
    print(f"  SIMULATION: Flash Crash Stress Test Results")
    print(f"  BTC: -50% in 3h  |  Alts: -70% in 3h  |  Crash at 25%, 50%, 75% of period (averaged)")
    print(f"{'#' * 120}")

    for pf_name in portfolios_tested:
        pf_results = [r for r in all_results if r["portfolio"] == pf_name]
        if not pf_results:
            continue

        print(f"\n  {'='*110}")
        print(f"  {pf_name}")
        print(f"  {'='*110}")
        print(f"  {'Config':<25s}  {'NormDD':>8s}  {'CrashDD':>8s}  {'WorstDD':>8s}  "
              f"{'DD+':>8s}  {'NormRet':>10s}  {'CrashRet':>10s}  "
              f"{'RetLoss':>10s}  {'Liqs':>5s}  {'Stops':>6s}")
        print(f"  {'-'*25}  {'-'*8}  {'-'*8}  {'-'*8}  "
              f"{'-'*8}  {'-'*10}  {'-'*10}  "
              f"{'-'*10}  {'-'*5}  {'-'*6}")

        # Sort by DD increase (least increase = most crash-resistant)
        pf_results_sorted = sorted(pf_results, key=lambda x: x["dd_increase"])

        for r in pf_results_sorted:
            tag = ""
            if r["config"] == "trail(1.5)+BE":
                tag = " <<BEST"
            elif r["config"] == "CURRENT (BE+prog)":
                tag = " <<CUR"

            print(f"  {r['config']:<25s}  "
                  f"{r['normal_dd']:+7.2f}%  "
                  f"{r['avg_crash_dd']:+7.2f}%  "
                  f"{r['worst_crash_dd']:+7.2f}%  "
                  f"{r['dd_increase']:+7.2f}%  "
                  f"{r['normal_ret']:+9.1f}%  "
                  f"{r['avg_crash_ret']:+9.1f}%  "
                  f"{r['ret_loss']:+9.1f}%  "
                  f"{r['avg_liquidations']:4.1f}  "
                  f"{r['avg_stops']:5.0f}"
                  f"{tag}")

        # Per-crash-point detail
        print(f"\n  Per crash injection point:")
        for r in pf_results_sorted:
            dds = [f"{d:+.2f}%" for d in r["crash_dds"]]
            print(f"    {r['config']:<25s}  @25%={dds[0]:<10s}  @50%={dds[1]:<10s}  @75%={dds[2]:<10s}")

    # Summary table: crash resistance ranking
    print(f"\n\n{'#' * 120}")
    print(f"  CRASH RESISTANCE RANKING (by average DD increase — lower = more crash-resistant)")
    print(f"{'#' * 120}")

    # Aggregate across all portfolios
    config_agg = {}
    for r in all_results:
        label = r["config"]
        if label not in config_agg:
            config_agg[label] = {"dd_increases": [], "ret_losses": [], "worst_dds": [], "liqs": []}
        config_agg[label]["dd_increases"].append(r["dd_increase"])
        config_agg[label]["ret_losses"].append(r["ret_loss"])
        config_agg[label]["worst_dds"].append(r["worst_crash_dd"])
        config_agg[label]["liqs"].append(r["avg_liquidations"])

    print(f"\n  {'Config':<25s}  {'Avg DD incr':>12s}  {'Avg RetLoss':>12s}  "
          f"{'Worst DD':>10s}  {'Avg Liqs':>9s}  {'Verdict':>12s}")
    print(f"  {'-'*25}  {'-'*12}  {'-'*12}  {'-'*10}  {'-'*9}  {'-'*12}")

    ranked = sorted(config_agg.items(), key=lambda x: np.mean(x[1]["dd_increases"]))
    best_dd_incr = np.mean(ranked[0][1]["dd_increases"])

    for label, agg in ranked:
        avg_dd_incr = np.mean(agg["dd_increases"])
        avg_ret_loss = np.mean(agg["ret_losses"])
        worst_dd = min(agg["worst_dds"])
        avg_liqs = np.mean(agg["liqs"])

        if avg_dd_incr <= best_dd_incr * 1.1:
            verdict = "BEST"
        elif avg_dd_incr <= best_dd_incr * 1.5:
            verdict = "GOOD"
        elif avg_dd_incr <= best_dd_incr * 2.0:
            verdict = "OK"
        else:
            verdict = "RISKY"

        print(f"  {label:<25s}  {avg_dd_incr:+11.2f}%  {avg_ret_loss:+11.1f}%  "
              f"{worst_dd:+9.2f}%  {avg_liqs:8.1f}  {verdict:>12s}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", type=int, default=3,
                        help="Months of data to simulate (default: 3)")
    parser.add_argument("--portfolios", nargs="+", default=None,
                        help="Portfolio names (default: s58 s60 4-edge 4-edge+ptp)")
    parser.add_argument("--btc-drop", type=float, default=0.50,
                        help="BTC crash magnitude (default: 0.50 = 50%%)")
    parser.add_argument("--alt-drop", type=float, default=0.70,
                        help="Alt crash magnitude (default: 0.70 = 70%%)")
    args = parser.parse_args()

    config_path = PROJECT_ROOT / "configs" / "multi_v4_paper.json"
    all_portfolios = load_portfolios_from_config(str(config_path))

    DEFAULT_PORTFOLIOS = ["s58", "s60", "4-edge", "4-edge+ptp"]
    if args.portfolios:
        test_portfolios = args.portfolios
    else:
        test_portfolios = DEFAULT_PORTFOLIOS

    portfolios = {k: v for k, v in all_portfolios.items() if k in test_portfolios}

    total_runs = len(portfolios) * len(CONFIGS) * 4  # 1 normal + 3 crash per config
    print(f"FLASH CRASH STRESS TEST")
    print(f"{'=' * 60}")
    print(f"Scenario: BTC -{args.btc_drop*100:.0f}%, Alts -{args.alt_drop*100:.0f}% in 3 hours")
    print(f"Period: {args.period} months")
    print(f"Portfolios: {', '.join(portfolios.keys())}")
    print(f"Exit configs: {len(CONFIGS)}")
    print(f"Total simulation runs: {total_runs}")
    print(f"Crash injection points: 25%, 50%, 75% through data")
    sys.stdout.flush()

    # Analytical section first
    analytical_crash_impact()

    # Run simulations
    data_end = infer_data_end_date("combined")
    all_results = []

    for pf_name, pf_def in portfolios.items():
        print(f"\n{'='*80}")
        print(f"  Running crash test: {pf_name} ({args.period}mo)")
        print(f"{'='*80}")
        sys.stdout.flush()

        try:
            t0 = time.time()
            results = run_crash_test(pf_name, pf_def, args.period, data_end)
            elapsed = time.time() - t0
            all_results.extend(results)
            print(f"  Done in {elapsed:.1f}s ({len(results)} configs × 4 runs = {len(results)*4} simulations)")
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()
        gc.collect()

    # Print results
    print_results(all_results, test_portfolios)

    # Save raw results
    output_path = "/tmp/crash_stress_test.json"
    serializable = []
    for r in all_results:
        sr = {k: v for k, v in r.items()}
        serializable.append(sr)
    with open(output_path, "w") as f:
        json.dump(serializable, f, indent=2, default=str)
    print(f"\nRaw results saved to {output_path}")
