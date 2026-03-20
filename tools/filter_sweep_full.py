"""Full-history filter sweep for S56 and S60 (72 months).
Uses ALL available data for the most robust parameter selection.
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
MONTHS = 72

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
        
        if FILTER_PARAMS.get('adv_enabled') and bar >= 24:
            adv = float(np.sum(volume[bar-23:bar+1] * close[bar-23:bar+1]))
            sizing_mult = min(1.0, max(FILTER_PARAMS['adv_min'], np.sqrt(adv / FILTER_PARAMS['adv_base'])))
        
        if FILTER_PARAMS.get('dd_enabled') and direction == 1:
            w = FILTER_PARAMS['dd_window']
            if bar >= w:
                rolling_high = float(np.max(close[max(0,bar-w):bar+1]))
                dd_val = (close[bar] - rolling_high) / rolling_high
                if dd_val < FILTER_PARAMS['dd_threshold']:
                    should_block = True
        
        if FILTER_PARAMS.get('pump_enabled') and direction == 1:
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
    
    trades = state.position_manager.closed_trades
    token_pnl = {}
    token_long_pnl = {}
    token_short_pnl = {}
    for t in trades:
        token_pnl.setdefault(t.token, 0)
        token_pnl[t.token] += t.pnl
        if t.direction == 1:
            token_long_pnl.setdefault(t.token, 0)
            token_long_pnl[t.token] += t.pnl
        else:
            token_short_pnl.setdefault(t.token, 0)
            token_short_pnl[t.token] += t.pnl
    
    worst_tokens = sorted(token_pnl.items(), key=lambda x: x[1])[:5]
    ban_total = float(token_pnl.get('BAN', 0))
    ban_long = float(token_long_pnl.get('BAN', 0))
    pippin_total = float(token_pnl.get('PIPPIN', 0))
    pippin_long = float(token_long_pnl.get('PIPPIN', 0))
    pippin_short = float(token_short_pnl.get('PIPPIN', 0))
    
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
        'ban_total': round(ban_total, 0),
        'ban_long': round(ban_long, 0),
        'pippin_total': round(pippin_total, 0),
        'pippin_long': round(pippin_long, 0),
        'pippin_short': round(float(token_short_pnl.get('PIPPIN', 0)), 0),
        'worst_tokens': [(t, round(float(p), 0)) for t, p in worst_tokens],
        'elapsed_s': round(elapsed, 1),
    }
    
    print(f"  [{strategy_id}/{label}] Sharpe={result['sharpe']}, MaxDD_h={result['max_dd_hourly_pct']}%, "
          f"Return={result['total_return_pct']}%, Trades={n_trades}, {elapsed:.1f}s, "
          f"BAN=${ban_total:+,.0f} PIPPIN=${pippin_total:+,.0f}", flush=True)
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

# ── Test configs (focused set based on learnings) ──
CONFIGS = [
    ('BASELINE', {}),
    # ADV sizing — the universal winner
    ('ADV_50M_min20', {'adv_enabled': True, 'adv_base': 50_000_000, 'adv_min': 0.20}),
    ('ADV_75M_min20', {'adv_enabled': True, 'adv_base': 75_000_000, 'adv_min': 0.20}),
    ('ADV_100M_min20', {'adv_enabled': True, 'adv_base': 100_000_000, 'adv_min': 0.20}),
    ('ADV_100M_min25', {'adv_enabled': True, 'adv_base': 100_000_000, 'adv_min': 0.25}),
    ('ADV_150M_min20', {'adv_enabled': True, 'adv_base': 150_000_000, 'adv_min': 0.20}),
    # DD filter
    ('DD_15pct_72h', {'dd_enabled': True, 'dd_threshold': -0.15, 'dd_window': 72}),
    ('DD_20pct_72h', {'dd_enabled': True, 'dd_threshold': -0.20, 'dd_window': 72}),
    ('DD_25pct_168h', {'dd_enabled': True, 'dd_threshold': -0.25, 'dd_window': 168}),
    # Pump cooldown
    ('PUMP_20pct_168h', {'pump_enabled': True, 'pump_threshold': 0.20, 'pump_window': 168}),
    ('PUMP_30pct_168h', {'pump_enabled': True, 'pump_threshold': 0.30, 'pump_window': 168}),
    # Best combos from prior sweeps
    ('ADV75M+PUMP30_168h', {
        'adv_enabled': True, 'adv_base': 75_000_000, 'adv_min': 0.20,
        'pump_enabled': True, 'pump_threshold': 0.30, 'pump_window': 168,
    }),
    ('ADV100M+PUMP20_168h', {
        'adv_enabled': True, 'adv_base': 100_000_000, 'adv_min': 0.20,
        'pump_enabled': True, 'pump_threshold': 0.20, 'pump_window': 168,
    }),
    ('ADV100M+PUMP30_168h', {
        'adv_enabled': True, 'adv_base': 100_000_000, 'adv_min': 0.20,
        'pump_enabled': True, 'pump_threshold': 0.30, 'pump_window': 168,
    }),
    ('ADV75M+DD20_72h', {
        'adv_enabled': True, 'adv_base': 75_000_000, 'adv_min': 0.20,
        'dd_enabled': True, 'dd_threshold': -0.20, 'dd_window': 72,
    }),
    ('ADV100M+DD20_72h', {
        'adv_enabled': True, 'adv_base': 100_000_000, 'adv_min': 0.20,
        'dd_enabled': True, 'dd_threshold': -0.20, 'dd_window': 72,
    }),
    ('ADV75M+PUMP30+DD20_72h', {
        'adv_enabled': True, 'adv_base': 75_000_000, 'adv_min': 0.20,
        'pump_enabled': True, 'pump_threshold': 0.30, 'pump_window': 168,
        'dd_enabled': True, 'dd_threshold': -0.20, 'dd_window': 72,
    }),
]

results = {}
for sid in ['s56', 's60']:
    print(f"\n{'='*80}")
    print(f"  {sid.upper()} — FULL HISTORY ({MONTHS}mo)")
    print(f"{'='*80}")
    results[sid] = []
    for label, params in CONFIGS:
        r = run_sim(sid, label, params, signals_cache)
        results[sid].append(r)

# ── Save ──
out_path = '/workspace/crypto_backtest/results/v4/filter_sweep_72mo.json'
with open(out_path, 'w') as f:
    json.dump(results, f, indent=2, default=str)

# ── Summary ──
for sid in ['s56', 's60']:
    bl = results[sid][0]
    print(f"\n{'='*110}")
    print(f"  {sid.upper()} — 72mo FULL HISTORY (Baseline: Sharpe={bl['sharpe']}, MaxDD={bl['max_dd_hourly_pct']}%, Return={bl['total_return_pct']}%, Trades={bl['n_trades']})")
    print(f"{'='*110}")
    print(f"{'Label':<30} {'Sharpe':>7} {'Calmar':>7} {'MaxDD_h':>8} {'DD_chg':>7} {'Return':>12} {'Ret%':>6} {'Trades':>6} {'BAN':>10} {'PIPPIN':>12}")
    print(f"{'-'*30} {'-'*7} {'-'*7} {'-'*8} {'-'*7} {'-'*12} {'-'*6} {'-'*6} {'-'*10} {'-'*12}")
    
    for r in results[sid]:
        dd_chg = bl['max_dd_hourly_pct'] - r['max_dd_hourly_pct']
        ret_pct = (r['total_return_pct']/bl['total_return_pct']-1)*100 if bl['total_return_pct'] != 0 else 0
        ban_s = f"${r['ban_total']:+,.0f}" if r['ban_total'] != 0 else "—"
        pip_s = f"${r['pippin_total']:+,.0f}" if r['pippin_total'] != 0 else "—"
        print(f"{r['label']:<30} {r['sharpe']:>7.3f} {r['calmar']:>7.1f} {r['max_dd_hourly_pct']:>7.2f}% {dd_chg:>+6.2f}pp {r['total_return_pct']:>11.1f}% {ret_pct:>+5.0f}% {r['n_trades']:>6} {ban_s:>10} {pip_s:>12}")

# ── Cross-timeframe stability check ──
print(f"\n{'='*110}")
print("CROSS-TIMEFRAME STABILITY: DD improvement (pp) across windows")
print(f"{'='*110}")
print(f"{'Filter':<30} {'6mo':>10} {'24mo':>10} {'72mo':>10} {'Stable?':>10}")
print(f"{'-'*30} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")

# Load 6mo and 24mo results for comparison
try:
    r6 = json.load(open('/workspace/crypto_backtest/results/v4/filter_sweep_6mo.json'))
    r24_s56 = json.load(open('/workspace/crypto_backtest/results/v4/filter_sweep_s56.json'))
    r24_s60 = json.load(open('/workspace/crypto_backtest/results/v4/filter_sweep_s60.json'))
    
    for sid in ['s56', 's60']:
        print(f"\n  {sid.upper()}:")
        bl6 = [x for x in r6[sid] if x['label']=='BASELINE'][0]['max_dd_hourly_pct']
        bl72 = results[sid][0]['max_dd_hourly_pct']
        r24 = r24_s56 if sid == 's56' else r24_s60
        bl24 = r24[0]['max_dd_hourly_pct']
        
        for label in ['ADV_75M_min20', 'ADV_100M_min20', 'PUMP_20pct_168h', 'PUMP_30pct_168h', 'DD_20pct_72h']:
            dd6 = next((bl6 - x['max_dd_hourly_pct'] for x in r6[sid] if x['label']==label), None)
            dd24 = next((bl24 - x['max_dd_hourly_pct'] for x in r24 if x['label']==label), None)
            dd72 = next((bl72 - x['max_dd_hourly_pct'] for x in results[sid] if x['label']==label), None)
            
            vals = [v for v in [dd6, dd24, dd72] if v is not None]
            stable = "YES" if vals and all(v > 0 for v in vals) else "MIXED" if vals else "N/A"
            
            s6 = f"{dd6:>+.2f}pp" if dd6 is not None else "N/A"
            s24 = f"{dd24:>+.2f}pp" if dd24 is not None else "N/A"
            s72 = f"{dd72:>+.2f}pp" if dd72 is not None else "N/A"
            print(f"  {label:<28} {s6:>10} {s24:>10} {s72:>10} {stable:>10}")
except Exception as e:
    print(f"  Could not load comparison data: {e}")

print(f"\nSaved to {out_path}")
