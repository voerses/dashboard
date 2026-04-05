#!/usr/bin/env python3
"""Build per-token config for s521_adaptive_per_token strategy.

Reads:
  /tmp/full_scan_results.parquet  — full IC scan results
  /tmp/token_signal_configs.json  — per-token family configs from prior analysis

Writes:
  data/alternative/s521_token_config.json
"""

import json
import os
import sys
from pathlib import Path

import pandas as pd
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SCAN_PATH = "/tmp/full_scan_results.parquet"
TOKEN_CFG_PATH = "/tmp/token_signal_configs.json"
OUTPUT_PATH = PROJECT_ROOT / "data" / "alternative" / "s521_token_config.json"

HORIZON_TO_HOURS = {
    "fwd_1d": 24,
    "fwd_2d": 48,
    "fwd_3d": 72,
    "fwd_5d": 120,
    "fwd_7d": 168,
    "fwd_10d": 240,
    "fwd_14d": 336,
    "fwd_21d": 504,
    "fwd_30d": 720,
}

MIN_OOS = 100  # minimum out-of-sample periods for a signal to be trusted


def main():
    # Load inputs
    df = pd.read_parquet(SCAN_PATH)
    with open(TOKEN_CFG_PATH) as f:
        token_configs = json.load(f)

    print(f"Scan results: {len(df)} rows, {df['token'].nunique()} tokens")
    print(f"Token configs: {len(token_configs)} tokens")

    # Filter to signals with sufficient OOS
    df_filt = df[df["n_oos"] >= MIN_OOS].copy()
    print(f"After n_oos >= {MIN_OOS} filter: {len(df_filt)} rows, {df_filt['token'].nunique()} tokens")

    output = {}

    for token, cfg in token_configs.items():
        families = cfg.get("families", {})
        if not families:
            continue

        # Get per-family IC values
        oi_info = families.get("OI", {})
        pos_info = families.get("positioning", {})
        flow_info = families.get("flow", {})

        oi_abs_ic = oi_info.get("abs_ic", 0.0)
        pos_abs_ic = pos_info.get("abs_ic", 0.0)
        flow_abs_ic = flow_info.get("abs_ic", 0.0)

        # Skip tokens where no family has sufficient n
        oi_n = oi_info.get("n", 0)
        pos_n = pos_info.get("n", 0)
        flow_n = flow_info.get("n", 0)

        if max(oi_n, pos_n, flow_n) < MIN_OOS:
            continue

        # Zero out families with insufficient OOS
        if oi_n < MIN_OOS:
            oi_abs_ic = 0.0
        if pos_n < MIN_OOS:
            pos_abs_ic = 0.0
        if flow_n < MIN_OOS:
            flow_abs_ic = 0.0

        total_ic = oi_abs_ic + pos_abs_ic + flow_abs_ic
        if total_ic < 0.01:
            continue

        # IC-proportional weights
        oi_weight = round(oi_abs_ic / total_ic, 4)
        pos_weight = round(pos_abs_ic / total_ic, 4)
        flow_weight = round(flow_abs_ic / total_ic, 4)

        # IC signs (contrarian = negative IC -> multiply signal by -1)
        oi_sign = int(np.sign(oi_info.get("ic", 0))) if oi_abs_ic > 0 else 0
        pos_sign = int(np.sign(pos_info.get("ic", 0))) if pos_abs_ic > 0 else 0
        flow_sign = int(np.sign(flow_info.get("ic", 0))) if flow_abs_ic > 0 else 0

        # Best horizon -> max_hold_hours
        # Use the horizon from the strongest family
        best_family = max(
            [("OI", oi_abs_ic, oi_info), ("positioning", pos_abs_ic, pos_info),
             ("flow", flow_abs_ic, flow_info)],
            key=lambda x: x[1],
        )
        best_horizon = best_family[2].get("horizon", "fwd_14d")
        max_hold_hours = HORIZON_TO_HOURS.get(best_horizon, 336)

        # Direction: majority of IC signs
        signs = [s for s in [oi_sign, pos_sign, flow_sign] if s != 0]
        if signs:
            direction = "contrarian" if sum(signs) < 0 else "momentum"
        else:
            direction = cfg.get("direction", "contrarian")

        # Best IC across families
        best_ic = round(max(oi_abs_ic, pos_abs_ic, flow_abs_ic), 5)

        output[token] = {
            "oi_weight": oi_weight,
            "pos_weight": pos_weight,
            "flow_weight": flow_weight,
            "oi_sign": oi_sign,
            "pos_sign": pos_sign,
            "flow_sign": flow_sign,
            "max_hold_hours": max_hold_hours,
            "best_ic": best_ic,
            "direction": direction,
        }

    # Sort by best_ic descending
    output = dict(sorted(output.items(), key=lambda x: -x[1]["best_ic"]))

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nWrote {len(output)} token configs to {OUTPUT_PATH}")
    print("\nTop 10 by best_ic:")
    for i, (tok, c) in enumerate(list(output.items())[:10]):
        print(f"  {tok:8s} IC={c['best_ic']:.3f}  w=[{c['oi_weight']:.2f},{c['pos_weight']:.2f},{c['flow_weight']:.2f}]  "
              f"signs=[{c['oi_sign']:+d},{c['pos_sign']:+d},{c['flow_sign']:+d}]  hold={c['max_hold_hours']}h  dir={c['direction']}")

    print("\nHorizon distribution:")
    from collections import Counter
    hdist = Counter(c["max_hold_hours"] for c in output.values())
    for h, cnt in sorted(hdist.items()):
        print(f"  {h}h: {cnt} tokens")


if __name__ == "__main__":
    main()
