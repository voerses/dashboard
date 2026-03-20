"""Pump filter sensitivity sweep.
Tests threshold (15%-50%) x lookback (48h-240h) with ADV always on as base layer.
Also tests volume-qualified variant (pump filter only on tokens < $500M ADV).
"""
import sys, os, time, json
import numpy as np
import pandas as pd

sys.path.insert(0, '/workspace/crypto_backtest')

from v4.config import StrategySpec, PortfolioConfig
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio, _process_entries as _orig_process_entries
from v4.report import compute_portfolio_metrics
import v4.simulator as sim_module

CAPITAL = 200_000
MONTHS = 6  # Most relevant window for 12-month forward goal

_filter_cache = {}
def _get_filter_data(token):
    if token in _filter_cache:
        return _filter_cache[token]
    path = f'/workspace/crypto_backtest/data/perp/1h_cache/{token}_1h.parquet'
    if not os.path.exists(path):
        _filter_cache[token] = None
        return None
    df = pd.read_parquet(path)
    _filter_cache[token] = (df['close'].values.astype(np.float64), df['volume'].values.astype(np.float64))
    return _filter_cache[token]

FILTER_PARAMS = {}

def _patched_process_entries(state, all_signals, strategy_specs, bar_maps, global_bar, config, rng):
    positions_before = set(id(p) for p in state.position_manager.open_positions)
    _orig_process_entries(state, all_signals, strategy_specs, bar_maps, global_bar, config, rng)
    new_positions = [p for p in state.position_manager.open_positions if id(p) not in positions_before]

    to_remove = []
    for pos in new_positions:
        fdata = _get_filter_data(pos.token)
        if fdata is None or pos.entry_bar < 336:
            continue
        close, volume = fdata
        bar = pos.entry_bar
        if bar >= len(close):
            continue
        direction = 1 if pos.quantity > 0 else -1
        should_block = False
        sizing_mult = 1.0

        # ADV sizing — always on
        adv_base = FILTER_PARAMS.get('adv_base', 100_000_000)
        adv_min = FILTER_PARAMS.get('adv_min', 0.20)
        adv = 0
        if bar >= 24:
            adv = float(np.sum(volume[bar-23:bar+1] * close[bar-23:bar+1]))
            sizing_mult = min(1.0, max(adv_min, np.sqrt(adv / adv_base)))

        # Pump filter (longs only)
        if FILTER_PARAMS.get('pump_enabled') and direction == 1:
            pw = FILTER_PARAMS['pump_window']
            threshold = FILTER_PARAMS['pump_threshold']

            # Volume-qualified: only apply to tokens below ADV cap
            adv_cap = FILTER_PARAMS.get('pump_adv_cap', None)
            apply_pump = True
            if adv_cap is not None and adv > adv_cap:
                apply_pump = False

            if apply_pump and bar >= pw:
                ret = (close[bar] - close[bar - pw]) / close[bar - pw]
                if ret > threshold:
                    should_block = True

        # Graduated sizing variant
        if FILTER_PARAMS.get('graduated_enabled') and direction == 1:
            pw = FILTER_PARAMS['graduated_window']
            if bar >= pw:
                ret = (close[bar] - close[bar - pw]) / close[bar - pw]
                # Apply graduated reduction
                adv_cap = FILTER_PARAMS.get('graduated_adv_cap', None)
                apply_grad = True
                if adv_cap is not None and adv > adv_cap:
                    apply_grad = False
                if apply_grad:
                    t1 = FILTER_PARAMS.get('graduated_t1', 0.20)
                    t2 = FILTER_PARAMS.get('graduated_t2', 0.30)
                    t3 = FILTER_PARAMS.get('graduated_t3', 0.40)
                    if ret > t3:
                        should_block = True
                    elif ret > t2:
                        sizing_mult *= 0.25
                    elif ret > t1:
                        sizing_mult *= 0.50

        if should_block:
            to_remove.append(pos)
        elif sizing_mult < 1.0:
            pos.margin_usd *= sizing_mult
            pos.quantity *= sizing_mult
            if hasattr(pos, 'initial_risk'):
                pos.initial_risk *= sizing_mult

    for pos in to_remove:
        state.position_manager.open_positions.remove(pos)
        entry_fee = state._entry_fees_by_pos.pop(pos.position_id, 0)
        state.total_fees -= entry_fee

sim_module._process_entries = _patched_process_entries

def run_sim(strategy_id, label, params, signals_cache):
    global FILTER_PARAMS
    FILTER_PARAMS = params

    all_signals, strategy_specs, spec = signals_cache[strategy_id]
    config = PortfolioConfig(strategies=[spec], capital=CAPITAL, max_portfolio_positions=40)

    t0 = time.time()
    state = simulate_portfolio(all_signals, strategy_specs, config)
    elapsed = time.time() - t0

    metrics, extra, eq_daily = compute_portfolio_metrics(state, CAPITAL)
    equities = np.array([eq for _, eq in state.equity_snapshots])
    running_max = np.maximum.accumulate(equities)
    dd_pct = (equities - running_max) / running_max
    max_dd_hourly = float(np.min(dd_pct)) * 100

    n_trades = len(state.position_manager.closed_trades)
    final_eq = equities[-1] if len(equities) > 0 else CAPITAL
    total_return_pct = (final_eq / CAPITAL - 1) * 100

    # Count blocked trades
    trades = state.position_manager.closed_trades
    token_pnl = {}
    for t in trades:
        token_pnl.setdefault(t.token, 0)
        token_pnl[t.token] += t.pnl
    worst_tokens = sorted(token_pnl.items(), key=lambda x: x[1])[:3]

    result = {
        'strategy': strategy_id,
        'label': label,
        'sharpe': round(metrics.sharpe_ratio, 3),
        'sortino': round(metrics.sortino_ratio, 3),
        'calmar': round(metrics.calmar_ratio, 3),
        'max_dd_hourly_pct': round(max_dd_hourly, 2),
        'total_return_pct': round(total_return_pct, 1),
        'n_trades': n_trades,
        'final_equity': round(final_eq, 0),
        'worst_tokens': [(t, round(float(p), 0)) for t, p in worst_tokens],
        'elapsed_s': round(elapsed, 1),
    }
    return result

# ── Precompute signals ──
signals_cache = {}
tokens = discover_tokens("perp")
end_date = infer_data_end_date("perp")

for sid in ['s56', 's60']:
    print(f"[{sid}] Loading {MONTHS}mo signals...", flush=True)
    t0 = time.time()
    spec = StrategySpec(strategy_id=sid, weight=1.0, max_positions=15, market="perp")
    signals = precompute_strategy_signals(spec, tokens, PortfolioConfig(
        strategies=[spec], capital=CAPITAL, max_portfolio_positions=40
    ), months=MONTHS, end_date=end_date)
    signals_cache[sid] = ({sid: signals}, {sid: spec}, spec)
    print(f"[{sid}] Loaded in {time.time()-t0:.1f}s — {len(signals)} tokens", flush=True)

# ── ADV base configs per strategy ──
ADV_CONFIGS = {
    's56': {'adv_base': 75_000_000, 'adv_min': 0.20},
    's60': {'adv_base': 100_000_000, 'adv_min': 0.20},
}

# ── Sweep: threshold x lookback ──
THRESHOLDS = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]
LOOKBACKS = [48, 72, 96, 120, 168, 240]

results = {}
for sid in ['s56', 's60']:
    adv = ADV_CONFIGS[sid]
    print(f"\n{'='*100}")
    print(f"  {sid.upper()} — PUMP FILTER SENSITIVITY (ADV always on: base=${adv['adv_base']/1e6:.0f}M)")
    print(f"{'='*100}")

    results[sid] = {'baseline': None, 'adv_only': None, 'sweep': [], 'volume_qualified': [], 'graduated': []}

    # Baseline (no filters)
    r = run_sim(sid, 'NO_FILTERS', {}, signals_cache)
    results[sid]['baseline'] = r
    print(f"  BASELINE: Sharpe={r['sharpe']}, MaxDD={r['max_dd_hourly_pct']}%, Return={r['total_return_pct']}%, Trades={r['n_trades']}", flush=True)

    # ADV only
    r = run_sim(sid, 'ADV_ONLY', adv, signals_cache)
    results[sid]['adv_only'] = r
    print(f"  ADV_ONLY: Sharpe={r['sharpe']}, MaxDD={r['max_dd_hourly_pct']}%, Return={r['total_return_pct']}%, Trades={r['n_trades']}", flush=True)

    # Full threshold x lookback sweep
    print(f"\n  {'Threshold':>10} {'Lookback':>8} {'Sharpe':>7} {'Sortino':>8} {'Calmar':>8} {'MaxDD%':>8} {'DD_chg':>7} {'Return%':>10} {'Ret_chg':>8} {'Trades':>6}", flush=True)
    print(f"  {'-'*10} {'-'*8} {'-'*7} {'-'*8} {'-'*8} {'-'*8} {'-'*7} {'-'*10} {'-'*8} {'-'*6}", flush=True)

    adv_dd = r['max_dd_hourly_pct']
    adv_ret = r['total_return_pct']

    for threshold in THRESHOLDS:
        for lookback in LOOKBACKS:
            label = f"PUMP_{int(threshold*100)}pct_{lookback}h"
            params = {
                **adv,
                'pump_enabled': True,
                'pump_threshold': threshold,
                'pump_window': lookback,
            }
            r = run_sim(sid, label, params, signals_cache)
            results[sid]['sweep'].append(r)
            dd_chg = adv_dd - r['max_dd_hourly_pct']
            ret_chg = (r['total_return_pct'] / adv_ret - 1) * 100 if adv_ret != 0 else 0
            print(f"  {threshold:>9.0%} {lookback:>7}h {r['sharpe']:>7.3f} {r['sortino']:>8.3f} {r['calmar']:>8.1f} {r['max_dd_hourly_pct']:>7.2f}% {dd_chg:>+6.2f}pp {r['total_return_pct']:>9.1f}% {ret_chg:>+7.1f}% {r['n_trades']:>6}", flush=True)

    # Volume-qualified variants (pump only on tokens < $500M ADV)
    print(f"\n  VOLUME-QUALIFIED (pump only on ADV < $500M):", flush=True)
    print(f"  {'Threshold':>10} {'Lookback':>8} {'Sharpe':>7} {'Sortino':>8} {'Calmar':>8} {'MaxDD%':>8} {'DD_chg':>7} {'Return%':>10} {'Ret_chg':>8} {'Trades':>6}", flush=True)
    print(f"  {'-'*10} {'-'*8} {'-'*7} {'-'*8} {'-'*8} {'-'*8} {'-'*7} {'-'*10} {'-'*8} {'-'*6}", flush=True)

    for threshold in [0.20, 0.25, 0.30, 0.35]:
        for lookback in [72, 120, 168]:
            label = f"VQ_PUMP_{int(threshold*100)}pct_{lookback}h"
            params = {
                **adv,
                'pump_enabled': True,
                'pump_threshold': threshold,
                'pump_window': lookback,
                'pump_adv_cap': 500_000_000,
            }
            r = run_sim(sid, label, params, signals_cache)
            results[sid]['volume_qualified'].append(r)
            dd_chg = adv_dd - r['max_dd_hourly_pct']
            ret_chg = (r['total_return_pct'] / adv_ret - 1) * 100 if adv_ret != 0 else 0
            print(f"  {threshold:>9.0%} {lookback:>7}h {r['sharpe']:>7.3f} {r['sortino']:>8.3f} {r['calmar']:>8.1f} {r['max_dd_hourly_pct']:>7.2f}% {dd_chg:>+6.2f}pp {r['total_return_pct']:>9.1f}% {ret_chg:>+7.1f}% {r['n_trades']:>6}", flush=True)

    # Graduated sizing variants
    print(f"\n  GRADUATED (>t1=50% size, >t2=25% size, >t3=block):", flush=True)
    print(f"  {'t1/t2/t3':>14} {'Lookback':>8} {'Sharpe':>7} {'Sortino':>8} {'Calmar':>8} {'MaxDD%':>8} {'DD_chg':>7} {'Return%':>10} {'Ret_chg':>8} {'Trades':>6}", flush=True)
    print(f"  {'-'*14} {'-'*8} {'-'*7} {'-'*8} {'-'*8} {'-'*8} {'-'*7} {'-'*10} {'-'*8} {'-'*6}", flush=True)

    graduated_configs = [
        (0.15, 0.25, 0.40, 72),
        (0.15, 0.25, 0.40, 168),
        (0.20, 0.30, 0.40, 72),
        (0.20, 0.30, 0.40, 168),
        (0.20, 0.30, 0.50, 72),
        (0.20, 0.30, 0.50, 168),
        (0.25, 0.35, 0.50, 72),
        (0.25, 0.35, 0.50, 168),
        # Volume-qualified graduated
        (0.20, 0.30, 0.40, 72, 500_000_000),
        (0.20, 0.30, 0.40, 168, 500_000_000),
        (0.25, 0.35, 0.50, 72, 500_000_000),
        (0.25, 0.35, 0.50, 168, 500_000_000),
    ]

    for cfg in graduated_configs:
        t1, t2, t3, lookback = cfg[:4]
        adv_cap = cfg[4] if len(cfg) > 4 else None
        vq_tag = "_VQ" if adv_cap else ""
        label = f"GRAD_{int(t1*100)}/{int(t2*100)}/{int(t3*100)}_{lookback}h{vq_tag}"
        params = {
            **adv,
            'graduated_enabled': True,
            'graduated_window': lookback,
            'graduated_t1': t1,
            'graduated_t2': t2,
            'graduated_t3': t3,
        }
        if adv_cap:
            params['graduated_adv_cap'] = adv_cap
        r = run_sim(sid, label, params, signals_cache)
        results[sid]['graduated'].append(r)
        dd_chg = adv_dd - r['max_dd_hourly_pct']
        ret_chg = (r['total_return_pct'] / adv_ret - 1) * 100 if adv_ret != 0 else 0
        tag = f"{int(t1*100)}/{int(t2*100)}/{int(t3*100)}{vq_tag}"
        print(f"  {tag:>14} {lookback:>7}h {r['sharpe']:>7.3f} {r['sortino']:>8.3f} {r['calmar']:>8.1f} {r['max_dd_hourly_pct']:>7.2f}% {dd_chg:>+6.2f}pp {r['total_return_pct']:>9.1f}% {ret_chg:>+7.1f}% {r['n_trades']:>6}", flush=True)

# ── Save ──
out_path = '/workspace/crypto_backtest/results/v4/pump_sensitivity_sweep.json'
with open(out_path, 'w') as f:
    json.dump(results, f, indent=2, default=str)

# ── Summary: Find Pareto-optimal configs ──
print(f"\n{'='*120}")
print("  PARETO FRONTIER: Best DD improvement per unit of return cost")
print(f"{'='*120}")

for sid in ['s56', 's60']:
    adv_only = results[sid]['adv_only']
    all_configs = results[sid]['sweep'] + results[sid]['volume_qualified'] + results[sid]['graduated']

    print(f"\n  {sid.upper()} (ADV-only baseline: Sharpe={adv_only['sharpe']}, MaxDD={adv_only['max_dd_hourly_pct']}%, Return={adv_only['total_return_pct']}%)")
    print(f"  {'Label':<32} {'Sharpe':>7} {'Sortino':>8} {'Calmar':>8} {'MaxDD%':>8} {'DD_imp':>7} {'Ret%':>10} {'RetCost':>8} {'Efficiency':>10}")
    print(f"  {'-'*32} {'-'*7} {'-'*8} {'-'*8} {'-'*8} {'-'*7} {'-'*10} {'-'*8} {'-'*10}")

    scored = []
    for r in all_configs:
        dd_imp = adv_only['max_dd_hourly_pct'] - r['max_dd_hourly_pct']
        ret_cost = (r['total_return_pct'] / adv_only['total_return_pct'] - 1) * 100 if adv_only['total_return_pct'] != 0 else 0
        # Efficiency = DD improvement (pp) per 1% return cost (higher = better)
        efficiency = dd_imp / abs(ret_cost) if ret_cost != 0 else (float('inf') if dd_imp > 0 else 0)
        scored.append((r, dd_imp, ret_cost, efficiency))

    # Sort by efficiency (DD improvement per return cost), filter to those with positive DD improvement
    scored = [s for s in scored if s[1] > 0.1]  # At least 0.1pp DD improvement
    scored.sort(key=lambda x: -x[3])  # Best efficiency first

    for r, dd_imp, ret_cost, eff in scored[:20]:
        eff_str = f"{eff:.2f}" if eff != float('inf') else "INF"
        print(f"  {r['label']:<32} {r['sharpe']:>7.3f} {r['sortino']:>8.3f} {r['calmar']:>8.1f} {r['max_dd_hourly_pct']:>7.2f}% {dd_imp:>+6.2f}pp {r['total_return_pct']:>9.1f}% {ret_cost:>+7.1f}% {eff_str:>10}")

print(f"\nSaved to {out_path}")
