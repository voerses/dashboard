"""Parameter sweep for anti-crash filters on S60 (24mo, 200K capital).

S60 is a dual-direction (long+short) strategy with different characteristics than s56.
Sweeps ADV sizing, DD filter, pump cooldown, and combinations.
All metrics computed from proper hourly MTM equity curves.
"""
import sys, os, time, json
import numpy as np
import pandas as pd

sys.path.insert(0, '/workspace/crypto_backtest')

from v4.config import StrategySpec, PortfolioConfig
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio, _process_entries as _orig_process_entries, SimulationState
from v4.report import compute_portfolio_metrics
import v4.simulator as sim_module

# ── Configuration ──
STRATEGY_ID = "s60"
MONTHS = 24  # Use 24mo for consistency with s56 (faster than 72mo, recent data more relevant)
CAPITAL = 200_000

# ── Filter parameter grids ──
ADV_BASES = [50_000_000, 75_000_000, 100_000_000, 150_000_000, 200_000_000]
ADV_CLAMP_MINS = [0.10, 0.15, 0.20, 0.25]
DD_THRESHOLDS = [-0.15, -0.20, -0.25, -0.30, -0.35, -0.40]
DD_WINDOWS = [72, 168, 336]  # 3d, 7d, 14d
PUMP_THRESHOLDS = [0.20, 0.30, 0.40, 0.50]
PUMP_WINDOWS = [24, 72, 168]  # 1d, 3d, 7d lookback

# ── Precompute signals once ──
print(f"[{STRATEGY_ID}] Loading signals...", flush=True)
t0 = time.time()
spec = StrategySpec(strategy_id=STRATEGY_ID, weight=1.0, max_positions=15, market="perp")
tokens = discover_tokens("perp")
end_date = infer_data_end_date("perp")
signals = precompute_strategy_signals(spec, tokens, PortfolioConfig(
    strategies=[spec], capital=CAPITAL, max_portfolio_positions=40
), months=MONTHS, end_date=end_date)
print(f"[{STRATEGY_ID}] Signals loaded in {time.time()-t0:.1f}s — {len(signals)} tokens", flush=True)

all_signals = {STRATEGY_ID: signals}
strategy_specs = {STRATEGY_ID: spec}

# ── Data cache for filter computations ──
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

# ── Filter parameters (set before each run) ──
FILTER_PARAMS = {
    'adv_enabled': False, 'adv_base': 100_000_000, 'adv_min': 0.15,
    'dd_enabled': False, 'dd_threshold': -0.30, 'dd_window': 168,
    'pump_enabled': False, 'pump_threshold': 0.30, 'pump_window': 168,
}

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
        
        # ADV sizing
        if FILTER_PARAMS['adv_enabled'] and bar >= 24:
            adv = float(np.sum(volume[bar-23:bar+1] * close[bar-23:bar+1]))
            sizing_mult = min(1.0, max(FILTER_PARAMS['adv_min'], np.sqrt(adv / FILTER_PARAMS['adv_base'])))
        
        # DD filter (LONG only — shorts benefit from declining prices)
        if FILTER_PARAMS['dd_enabled'] and direction == 1:
            w = FILTER_PARAMS['dd_window']
            if bar >= w:
                rolling_high = float(np.max(close[max(0,bar-w):bar+1]))
                dd_val = (close[bar] - rolling_high) / rolling_high
                if dd_val < FILTER_PARAMS['dd_threshold']:
                    should_block = True
        
        # Pump cooldown (LONG only)
        if FILTER_PARAMS['pump_enabled'] and direction == 1:
            pw = FILTER_PARAMS['pump_window']
            if bar >= pw:
                ret = (close[bar] - close[bar - pw]) / close[bar - pw]
                if ret > FILTER_PARAMS['pump_threshold']:
                    should_block = True
        
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

# Monkey-patch
sim_module._process_entries = _patched_process_entries

def run_sim(label, params):
    """Run one simulation with given filter params, return metrics dict."""
    global FILTER_PARAMS
    FILTER_PARAMS = params
    
    config = PortfolioConfig(
        strategies=[spec], capital=CAPITAL, max_portfolio_positions=40
    )
    
    t0 = time.time()
    state = simulate_portfolio(all_signals, strategy_specs, config)
    elapsed = time.time() - t0
    
    metrics, extra, eq_daily = compute_portfolio_metrics(state, CAPITAL)
    
    # Compute hourly MTM DD from snapshots
    equities = np.array([eq for _, eq in state.equity_snapshots])
    running_max = np.maximum.accumulate(equities)
    dd_pct = (equities - running_max) / running_max
    max_dd_hourly = float(np.min(dd_pct)) * 100
    
    n_trades = len(state.position_manager.closed_trades)
    final_eq = equities[-1] if len(equities) > 0 else CAPITAL
    total_return_pct = (final_eq / CAPITAL - 1) * 100
    
    result = {
        'label': label,
        'sharpe': round(metrics.sharpe_ratio, 3),
        'sortino': round(metrics.sortino_ratio, 3),
        'calmar': round(metrics.calmar_ratio, 3),
        'max_dd_hourly_pct': round(max_dd_hourly, 2),
        'max_dd_daily_pct': round(metrics.max_drawdown_pct, 2),
        'total_return_pct': round(total_return_pct, 1),
        'n_trades': n_trades,
        'final_equity': round(final_eq, 0),
        'elapsed_s': round(elapsed, 1),
        'params': {k: v for k, v in params.items()},
    }
    print(f"  [{label}] Sharpe={result['sharpe']}, MaxDD_hourly={result['max_dd_hourly_pct']}%, "
          f"Return={result['total_return_pct']}%, Trades={n_trades}, {elapsed:.1f}s", flush=True)
    return result

results = []

# ── 1. Baseline ──
print(f"\n{'='*60}\n[{STRATEGY_ID}] BASELINE\n{'='*60}", flush=True)
r = run_sim('BASELINE', {
    'adv_enabled': False, 'adv_base': 100_000_000, 'adv_min': 0.15,
    'dd_enabled': False, 'dd_threshold': -0.30, 'dd_window': 168,
    'pump_enabled': False, 'pump_threshold': 0.30, 'pump_window': 168,
})
results.append(r)
baseline_dd = r['max_dd_hourly_pct']
baseline_sharpe = r['sharpe']
baseline_return = r['total_return_pct']

# ── 2. ADV Sizing Sweep ──
print(f"\n{'='*60}\n[{STRATEGY_ID}] ADV SIZING SWEEP\n{'='*60}", flush=True)
for adv_base in ADV_BASES:
    for adv_min in ADV_CLAMP_MINS:
        label = f"ADV_{adv_base//1_000_000}M_min{int(adv_min*100)}"
        r = run_sim(label, {
            'adv_enabled': True, 'adv_base': adv_base, 'adv_min': adv_min,
            'dd_enabled': False, 'dd_threshold': -0.30, 'dd_window': 168,
            'pump_enabled': False, 'pump_threshold': 0.30, 'pump_window': 168,
        })
        results.append(r)

# ── 3. DD Filter Sweep ──
print(f"\n{'='*60}\n[{STRATEGY_ID}] DD FILTER SWEEP\n{'='*60}", flush=True)
for dd_thresh in DD_THRESHOLDS:
    for dd_win in DD_WINDOWS:
        label = f"DD_{int(abs(dd_thresh)*100)}pct_{dd_win}h"
        r = run_sim(label, {
            'adv_enabled': False, 'adv_base': 100_000_000, 'adv_min': 0.15,
            'dd_enabled': True, 'dd_threshold': dd_thresh, 'dd_window': dd_win,
            'pump_enabled': False, 'pump_threshold': 0.30, 'pump_window': 168,
        })
        results.append(r)

# ── 4. Pump Cooldown Sweep ──
print(f"\n{'='*60}\n[{STRATEGY_ID}] PUMP COOLDOWN SWEEP\n{'='*60}", flush=True)
for pump_thresh in PUMP_THRESHOLDS:
    for pump_win in PUMP_WINDOWS:
        label = f"PUMP_{int(pump_thresh*100)}pct_{pump_win}h"
        r = run_sim(label, {
            'adv_enabled': False, 'adv_base': 100_000_000, 'adv_min': 0.15,
            'dd_enabled': False, 'dd_threshold': -0.30, 'dd_window': 168,
            'pump_enabled': True, 'pump_threshold': pump_thresh, 'pump_window': pump_win,
        })
        results.append(r)

# ── 5. Best combos ──
print(f"\n{'='*60}\n[{STRATEGY_ID}] COMBINATION SWEEP\n{'='*60}", flush=True)

adv_results = [r for r in results if r['label'].startswith('ADV_')]
dd_results = [r for r in results if r['label'].startswith('DD_')]
pump_results = [r for r in results if r['label'].startswith('PUMP_')]

def score(r):
    dd_improve = baseline_dd - r['max_dd_hourly_pct']
    ret_cost = max(0.01, baseline_return - r['total_return_pct'])
    return dd_improve / ret_cost if ret_cost > 0 else dd_improve * 100

best_adv = sorted(adv_results, key=score, reverse=True)[:3]
best_dd = sorted(dd_results, key=score, reverse=True)[:3]
best_pump = sorted(pump_results, key=score, reverse=True)[:3]

for adv_r in best_adv:
    ap = adv_r['params']
    for dd_r in best_dd:
        dp = dd_r['params']
        label = f"COMBO_{adv_r['label']}+{dd_r['label']}"
        r = run_sim(label, {
            'adv_enabled': True, 'adv_base': ap['adv_base'], 'adv_min': ap['adv_min'],
            'dd_enabled': True, 'dd_threshold': dp['dd_threshold'], 'dd_window': dp['dd_window'],
            'pump_enabled': False, 'pump_threshold': 0.30, 'pump_window': 168,
        })
        results.append(r)

for adv_r in best_adv[:2]:
    ap = adv_r['params']
    for pump_r in best_pump[:2]:
        pp = pump_r['params']
        label = f"COMBO_{adv_r['label']}+{pump_r['label']}"
        r = run_sim(label, {
            'adv_enabled': True, 'adv_base': ap['adv_base'], 'adv_min': ap['adv_min'],
            'dd_enabled': False, 'dd_threshold': -0.30, 'dd_window': 168,
            'pump_enabled': True, 'pump_threshold': pp['pump_threshold'], 'pump_window': pp['pump_window'],
        })
        results.append(r)

# Triple combo
if best_adv and best_dd and best_pump:
    ap = best_adv[0]['params']
    dp = best_dd[0]['params']
    pp = best_pump[0]['params']
    label = f"TRIPLE_{best_adv[0]['label']}+{best_dd[0]['label']}+{best_pump[0]['label']}"
    r = run_sim(label, {
        'adv_enabled': True, 'adv_base': ap['adv_base'], 'adv_min': ap['adv_min'],
        'dd_enabled': True, 'dd_threshold': dp['dd_threshold'], 'dd_window': dp['dd_window'],
        'pump_enabled': True, 'pump_threshold': pp['pump_threshold'], 'pump_window': pp['pump_window'],
    })
    results.append(r)

# ── Save results ──
out_path = f'/workspace/crypto_backtest/results/v4/filter_sweep_{STRATEGY_ID}.json'
with open(out_path, 'w') as f:
    json.dump(results, f, indent=2, default=str)
print(f"\n[{STRATEGY_ID}] Saved {len(results)} results to {out_path}")

# ── Summary table ──
print(f"\n{'='*60}")
print(f"[{STRATEGY_ID}] TOP 15 BY HOURLY MTM DD IMPROVEMENT (with return cost)")
print(f"{'='*60}")
print(f"{'Label':<45} {'Sharpe':>7} {'MaxDD_h':>8} {'DD_imp':>7} {'Return':>8} {'Ret_cost':>9} {'Trades':>7}")
print(f"{'-'*45} {'-'*7} {'-'*8} {'-'*7} {'-'*8} {'-'*9} {'-'*7}")

sorted_results = sorted(results[1:], key=lambda r: r['max_dd_hourly_pct'], reverse=True)
for r in sorted_results[:15]:
    dd_imp = baseline_dd - r['max_dd_hourly_pct']
    ret_cost = baseline_return - r['total_return_pct']
    print(f"{r['label']:<45} {r['sharpe']:>7.3f} {r['max_dd_hourly_pct']:>7.2f}% {dd_imp:>+6.2f}pp {r['total_return_pct']:>7.1f}% {ret_cost:>+8.1f}% {r['n_trades']:>7}")

print(f"\nBaseline: Sharpe={baseline_sharpe}, MaxDD_hourly={baseline_dd}%, Return={baseline_return}%")
