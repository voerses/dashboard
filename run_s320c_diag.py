#!/usr/bin/env python3
"""Diagnose s320 Option C: what are the pos_usd values at entry candidates?"""
import sys
import time
import json
import numpy as np
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from v4.config import PortfolioConfig, StrategySpec, SizingDefaults, resolve_sizing
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.sizing import compute_position_size


def main():
    config_path = PROJECT_ROOT / "configs/s320c_test.json"
    with open(config_path) as f:
        cfg_data = json.load(f)

    capital = cfg_data["capital"]
    min_pos = cfg_data.get("min_position_usd", 10)

    strategy_specs = {}
    for s in cfg_data["strategies"]:
        spec = StrategySpec.from_dict(s)
        strategy_specs[spec.strategy_id] = spec

    config = PortfolioConfig(
        strategies=list(strategy_specs.values()),
        capital=capital,
        min_position_usd=min_pos,
    )

    data_end = infer_data_end_date("spot")
    spec = strategy_specs["s320"]

    signals = precompute_strategy_signals(spec, ["BTC"], config, 12, end_date=data_end)
    sig = signals["BTC"]

    resolved = resolve_sizing(SizingDefaults(), spec.sizing_overrides)

    # Walk through entry bars
    entry_bars = np.flatnonzero(sig.entry_mask)
    print(f"Total entry_mask=True bars: {len(entry_bars)}")

    sizes = []
    sm_vals = []
    for bar in entry_bars:
        close_val = sig.close[bar]
        atr_val = sig.atr[bar]
        if np.isnan(atr_val):
            atr_val = close_val * 0.02
        vol = atr_val / max(close_val, 1e-10)
        adv_val = sig.rolling_adv[bar]
        sm_val = float(sig.size_multiplier[bar])
        lev_val = float(sig.leverage[bar])
        cap_mult = float(sig.cap_multiplier[bar])

        pos_usd = compute_position_size(
            strategy_equity=capital * spec.weight,
            rolling_adv=adv_val,
            volatility=vol,
            edge=sig.edge,
            size_multiplier=sm_val,
            cap_multiplier=cap_mult,
            max_trade_pct=sig.max_trade_pct,
            adv_cap_pct=config.adv_cap_pct,
            adv_sizing_enabled=spec.adv_sizing_enabled,
            adv_sizing_base=spec.adv_sizing_base,
            adv_sizing_floor=spec.adv_sizing_floor,
            edge_minimum=resolved.edge_minimum,
            target_vol=resolved.target_vol,
            vol_floor=resolved.vol_floor,
            spot_max_equity_pct=resolved.spot_max_equity_pct,
            leverage=lev_val,
            kelly_mult_override=resolved.kelly_mult_override,
            kelly_mult_scale=resolved.kelly_mult_scale,
            cap_pct_override=resolved.cap_pct_override,
            cap_pct_scale=resolved.cap_pct_scale,
            kelly_mult_floor=resolved.kelly_mult_floor,
            kelly_mult_range=resolved.kelly_mult_range,
            cap_pct_floor=resolved.cap_pct_floor,
            cap_pct_range=resolved.cap_pct_range,
            adv_scaling_divisor=resolved.adv_scaling_divisor,
        )
        sizes.append(pos_usd)
        sm_vals.append(sm_val)

    sizes = np.array(sizes)
    sm_vals = np.array(sm_vals)

    print(f"\nsize_multiplier distribution at entry bars:")
    print(f"  min:    {sm_vals.min():.4f}")
    print(f"  max:    {sm_vals.max():.4f}")
    print(f"  mean:   {sm_vals.mean():.4f}")
    print(f"  median: {np.median(sm_vals):.4f}")
    print(f"  zero:   {(sm_vals == 0).sum()} / {len(sm_vals)}")

    print(f"\npos_usd distribution at entry bars:")
    print(f"  min:    ${sizes.min():.2f}")
    print(f"  max:    ${sizes.max():.2f}")
    print(f"  mean:   ${sizes.mean():.2f}")
    print(f"  median: ${np.median(sizes):.2f}")
    print(f"  zero:   {(sizes == 0).sum()} / {len(sizes)}")
    print(f"  <$10:   {(sizes < 10).sum()} / {len(sizes)}")
    print(f"  <$50:   {(sizes < 50).sum()} / {len(sizes)}")
    print(f"  <$200:  {(sizes < 200).sum()} / {len(sizes)}")
    print(f"  >=$200: {(sizes >= 200).sum()} / {len(sizes)}")

    # Show a few examples
    print(f"\nSample entries (first 20):")
    print(f"  {'bar':>6s}  {'sm':>8s}  {'pos_usd':>12s}  {'vol':>8s}  {'close':>10s}")
    for i in range(min(20, len(entry_bars))):
        bar = entry_bars[i]
        close_val = sig.close[bar]
        atr_val = sig.atr[bar]
        if np.isnan(atr_val):
            atr_val = close_val * 0.02
        vol = atr_val / max(close_val, 1e-10)
        print(f"  {bar:>6d}  {sm_vals[i]:>8.4f}  ${sizes[i]:>11.2f}  {vol:>8.4f}  ${close_val:>9.0f}")

    # Histogram of pos_usd
    print(f"\npos_usd histogram (non-zero only):")
    nonzero = sizes[sizes > 0]
    if len(nonzero) > 0:
        bins = [0, 10, 50, 100, 200, 500, 1000, 5000, 10000, 50000, 100000, 500000]
        for i in range(len(bins)-1):
            count = ((nonzero >= bins[i]) & (nonzero < bins[i+1])).sum()
            if count > 0:
                print(f"  ${bins[i]:>7,} - ${bins[i+1]:>7,}: {count}")
        count = (nonzero >= bins[-1]).sum()
        if count > 0:
            print(f"  ${bins[-1]:>7,}+:           {count}")


if __name__ == "__main__":
    main()
