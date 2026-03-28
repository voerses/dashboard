#!/usr/bin/env python3
"""Verify strategy sizing parameters against research targets.

Simulates the Kelly sizing pipeline across volatility regimes to catch
parameter misconfiguration before backtesting. See AIPIP-0030.

Usage:
    python tools/verify_sizing.py s400
    python tools/verify_sizing.py s401
    python tools/verify_sizing.py --all          # verify all strategies with SIZING_OVERRIDES
    python tools/verify_sizing.py s400 --json    # machine-readable output
"""
import sys
import json
import argparse
import importlib
import importlib.util
from pathlib import Path

# Ensure project root is on path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "v4"))

from v4.config import SizingDefaults, resolve_sizing


# ---------------------------------------------------------------------------
# Volatility regimes to simulate
# ---------------------------------------------------------------------------

VOL_REGIMES = [
    ("floor",   0.005),
    ("calm",    0.010),
    ("normal",  0.020),
    ("high",    0.050),
    ("crisis",  0.100),
]


def load_strategy_module(strategy_id: str):
    """Import a strategy module by ID (e.g., 's400')."""
    # Find the strategy file
    strat_dir = ROOT / "strategies"
    matches = list(strat_dir.glob(f"{strategy_id}_*.py"))
    if not matches:
        raise FileNotFoundError(f"No strategy file found for '{strategy_id}' in {strat_dir}")
    if len(matches) > 1:
        raise ValueError(f"Multiple matches for '{strategy_id}': {[m.name for m in matches]}")

    mod_name = matches[0].stem
    spec = importlib.util.spec_from_file_location(mod_name, matches[0])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, matches[0]


def estimate_funding_cost(leverage: float, hold_hours: float, n_positions: float,
                          avg_funding_rate: float = 0.0001) -> float:
    """Rough funding drag as fraction of equity per rebalance cycle.

    Default 0.01%/8h is typical for crypto perps.
    """
    cycles = hold_hours / 8.0
    return avg_funding_rate * leverage * cycles * n_positions


def verify_strategy(strategy_id: str, verbose: bool = True) -> dict:
    """Run sizing verification for a single strategy. Returns results dict."""
    mod, filepath = load_strategy_module(strategy_id)

    sizing_overrides = getattr(mod, "SIZING_OVERRIDES", None)
    if sizing_overrides is None:
        return {"strategy": strategy_id, "status": "SKIP", "reason": "No SIZING_OVERRIDES"}

    research_targets = getattr(mod, "RESEARCH_TARGET_SIZES", None)
    leverage = getattr(mod, "LEVERAGE", 1.0)

    # Resolve sizing through the engine's validation pipeline
    try:
        resolved = resolve_sizing(SizingDefaults(), sizing_overrides)
    except ValueError as e:
        return {"strategy": strategy_id, "status": "FAIL", "reason": f"resolve_sizing failed: {e}"}

    # Extract key params
    target_vol = resolved.target_vol
    vol_floor = resolved.vol_floor
    kelly_mult = (resolved.kelly_mult_override if resolved.kelly_mult_override > 0
                  else resolved.kelly_mult_floor + resolved.kelly_mult_range)
    cap_pct = resolved.cap_pct_override if resolved.cap_pct_override > 0 else resolved.cap_pct_floor + resolved.cap_pct_range
    edge = 1.0  # worst-case (some strategies use edge=1.0 for portfolio sizing)

    # Check if strategy declares its edge
    # For per-token strategies, edge is typically in StrategyResult; use 0.40 as typical
    strategy_type = getattr(mod, "STRATEGY_TYPE", "per_token")
    if strategy_type == "portfolio":
        typical_edge = 1.0  # portfolio strategies often use edge=1.0 with size_multiplier
    else:
        typical_edge = 0.40  # typical per-token edge

    # Composite check
    max_vol_adj = target_vol / vol_floor
    worst_case_frac = kelly_mult * max_vol_adj
    composite_ok = worst_case_frac <= 8.0

    # Simulate across vol regimes
    regime_results = []
    issues = []
    for regime_name, vol in VOL_REGIMES:
        vol_adj = target_vol / max(vol, vol_floor) if vol > 0 else 1.0
        kelly_frac = kelly_mult * typical_edge
        raw_pct = kelly_frac * vol_adj  # as fraction of equity
        # Cap applies
        effective_pct = min(raw_pct, cap_pct)

        target_pct = research_targets.get("per_position_pct", None) if research_targets else None

        deviation = None
        status = "OK"
        if target_pct and regime_name == "floor":
            deviation = (effective_pct - target_pct) / target_pct
            if abs(deviation) > 0.30:
                status = "WARN"
                issues.append(f"Floor regime position {effective_pct:.1%} deviates {deviation:+.0%} from target {target_pct:.1%}")

        if effective_pct > 0.50:
            status = "FAIL"
            issues.append(f"{regime_name} regime: position = {effective_pct:.1%} of equity (>50% single name)")

        regime_results.append({
            "regime": regime_name,
            "volatility": vol,
            "vol_adj": round(vol_adj, 2),
            "raw_pct": round(raw_pct, 4),
            "effective_pct": round(effective_pct, 4),
            "binding_cap": "cap_pct" if raw_pct > cap_pct else "kelly",
            "deviation_from_target": round(deviation, 3) if deviation is not None else None,
            "status": status,
        })

    # Funding estimate (if research targets include hold duration)
    funding_estimate = None
    if research_targets and "hold_duration_hours" in research_targets:
        hold_h = research_targets["hold_duration_hours"]
        n_pos = research_targets.get("max_concurrent", 1)
        funding_pct = estimate_funding_cost(leverage, hold_h, n_pos)
        funding_estimate = {
            "per_cycle_pct": round(funding_pct, 4),
            "leverage": leverage,
            "hold_hours": hold_h,
            "n_positions": n_pos,
        }
        if funding_pct > 0.10:
            issues.append(f"Funding estimate {funding_pct:.1%} per cycle exceeds 10% threshold")

    # Overall status
    has_fail = any(r["status"] == "FAIL" for r in regime_results) or not composite_ok
    has_warn = any(r["status"] == "WARN" for r in regime_results) or (funding_estimate and funding_estimate["per_cycle_pct"] > 0.10)
    overall = "FAIL" if has_fail else ("WARN" if has_warn else "PASS")

    result = {
        "strategy": strategy_id,
        "file": str(filepath.relative_to(ROOT)),
        "status": overall,
        "sizing_overrides": sizing_overrides,
        "research_targets": research_targets,
        "resolved_params": {
            "target_vol": target_vol,
            "vol_floor": vol_floor,
            "kelly_mult": kelly_mult,
            "cap_pct": cap_pct,
        },
        "composite_check": {
            "max_vol_adj": round(max_vol_adj, 1),
            "worst_case_frac": round(worst_case_frac, 1),
            "limit": 8.0,
            "status": "OK" if composite_ok else "FAIL",
        },
        "regime_simulation": regime_results,
        "funding_estimate": funding_estimate,
        "issues": issues,
    }

    if verbose:
        _print_report(result)

    return result


def _print_report(result: dict) -> None:
    """Print human-readable verification report."""
    sid = result["strategy"]
    status = result["status"]
    print(f"\n{'='*60}")
    print(f"  {sid.upper()} Sizing Verification — {status}")
    print(f"{'='*60}")

    # Params
    sp = result["resolved_params"]
    so = result["sizing_overrides"]
    print(f"\n  SIZING_OVERRIDES: {so}")
    print(f"  Resolved: target_vol={sp['target_vol']}, vol_floor={sp['vol_floor']}, "
          f"kelly_mult={sp['kelly_mult']:.2f}, cap_pct={sp['cap_pct']:.2f}")

    rt = result["research_targets"]
    if rt:
        print(f"  RESEARCH_TARGET: {rt}")
    else:
        print(f"  RESEARCH_TARGET: (not declared)")

    # Composite
    cc = result["composite_check"]
    print(f"\n  Composite check: worst-case = {sp['kelly_mult']:.2f} x {cc['max_vol_adj']:.1f} "
          f"= {cc['worst_case_frac']:.1f}x  {cc['status']} (< {cc['limit']:.0f}x)")

    # Regime table
    print(f"\n  {'Regime':<10} {'Vol':>6} {'vol_adj':>8} {'Raw%':>8} {'Eff%':>8} {'Binds':>8} {'Status':>8}")
    print(f"  {'-'*10} {'-'*6} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
    for r in result["regime_simulation"]:
        print(f"  {r['regime']:<10} {r['volatility']:>6.3f} {r['vol_adj']:>8.1f}x "
              f"{r['raw_pct']:>7.1%} {r['effective_pct']:>7.1%} {r['binding_cap']:>8} {r['status']:>8}")

    # Funding
    fe = result["funding_estimate"]
    if fe:
        print(f"\n  Funding estimate: {fe['per_cycle_pct']:.2%} per cycle "
              f"({fe['leverage']}x lev, {fe['hold_hours']}h hold, {fe['n_positions']} pos)")

    # Issues
    if result["issues"]:
        print(f"\n  Issues:")
        for issue in result["issues"]:
            print(f"    - {issue}")

    print()


def find_all_strategies_with_overrides():
    """Find all strategy files that have SIZING_OVERRIDES."""
    strat_dir = ROOT / "strategies"
    strategies = []
    for f in sorted(strat_dir.glob("s[0-9]*.py")):
        if f.name == "TEMPLATE.py":
            continue
        content = f.read_text()
        if "SIZING_OVERRIDES" in content:
            # Extract strategy ID from filename (e.g., s400 from s400_xsec_momentum_r162.py)
            sid = f.stem.split("_")[0]
            strategies.append(sid)
    return strategies


def main():
    parser = argparse.ArgumentParser(description="Verify strategy sizing parameters")
    parser.add_argument("strategy", nargs="?", help="Strategy ID (e.g., s400)")
    parser.add_argument("--all", action="store_true", help="Verify all strategies with SIZING_OVERRIDES")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of human-readable")
    parser.add_argument("--save", type=str, metavar="PATH",
                        help="Save verification result to JSON file (for gate integration)")
    args = parser.parse_args()

    if not args.strategy and not args.all:
        parser.error("Provide a strategy ID or use --all")

    if args.all:
        strategies = find_all_strategies_with_overrides()
        if not strategies:
            print("No strategies with SIZING_OVERRIDES found.")
            return
        results = []
        for sid in strategies:
            result = verify_strategy(sid, verbose=not args.json)
            results.append(result)
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            # Summary
            passed = sum(1 for r in results if r["status"] == "PASS")
            warned = sum(1 for r in results if r["status"] == "WARN")
            failed = sum(1 for r in results if r["status"] == "FAIL")
            skipped = sum(1 for r in results if r["status"] == "SKIP")
            print(f"\nSummary: {passed} PASS, {warned} WARN, {failed} FAIL, {skipped} SKIP")
    else:
        result = verify_strategy(args.strategy, verbose=not args.json)
        if args.json:
            print(json.dumps(result, indent=2))

        if args.save:
            save_path = Path(args.save)
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text(json.dumps(result, indent=2))
            if not args.json:
                print(f"  Saved to {save_path}")


if __name__ == "__main__":
    main()
