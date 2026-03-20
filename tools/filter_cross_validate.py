"""Cross-validation: test the best settings from each strategy's sweep,
plus BAN/PIPPIN-specific targeted combos.
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
MONTHS = 24

# ── Data cache ──
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
        
        # LOW-ADV pump combo: only block pump on low-ADV tokens
        if FILTER_PARAMS.get('low_adv_pump_enabled') and direction == 1 and bar >= 168:
            adv = float(np.sum(volume[bar-23:bar+1] * close[bar-23:bar+1]))
            adv_threshold = FILTER_PARAMS.get('low_adv_pump_adv_threshold', 50_000_000)
            if adv < adv_threshold:
                pw = FILTER_PARAMS.get('low_adv_pump_window', 168)
                if bar >= pw:
                    ret = (close[bar] - close[bar - pw]) / close[bar - pw]
                    if ret > FILTER_PARAMS.get('low_adv_pump_ret_threshold', 0.30):
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

def run_sim(strategy_id, label, params):
    global FILTER_PARAMS
    FILTER_PARAMS = params
    
    spec = StrategySpec(strategy_id=strategy_id, weight=1.0, max_positions=15, market="perp")
    tokens = discover_tokens("perp")
    end_date = infer_data_end_date("perp")
    
    signals = precompute_strategy_signals(spec, tokens, PortfolioConfig(
        strategies=[spec], capital=CAPITAL, max_portfolio_positions=40
    ), months=MONTHS, end_date=end_date)
    
    all_signals = {strategy_id: signals}
    strategy_specs = {strategy_id: spec}
    config = PortfolioConfig(strategies=[spec], capital=CAPITAL, max_portfolio_positions=40)
    
    t0 = time.time()
    state = simulate_portfolio(all_signals, strategy_specs, config)
    elapsed = time.time() - t0
    
    metrics, extra, eq_daily = compute_portfolio_metrics(state, CAPITAL)
    equities = np.array([eq for _, eq in state.equity_snapshots])
    running_max = np.maximum.accumulate(equities)
    dd_pct = (equities - running_max) / running_max
    max_dd_hourly = float(np.min(dd_pct)) * 100
    
    # Find worst DD episode details
    worst_idx = np.argmin(dd_pct)
    peak_idx = np.argmax(equities[:worst_idx+1]) if worst_idx > 0 else 0
    
    n_trades = len(state.position_manager.closed_trades)
    final_eq = equities[-1] if len(equities) > 0 else CAPITAL
    total_return_pct = (final_eq / CAPITAL - 1) * 100
    
    # Token-level worst trade analysis
    trades = state.position_manager.closed_trades
    token_pnl = {}
    for t in trades:
        token_pnl.setdefault(t.token, 0)
        token_pnl[t.token] += t.pnl
    worst_tokens = sorted(token_pnl.items(), key=lambda x: x[1])[:5]
    
    result = {
        'strategy': strategy_id,
        'label': label,
        'sharpe': round(metrics.sharpe_ratio, 3),
        'sortino': round(metrics.sortino_ratio, 3),
        'calmar': round(metrics.calmar_ratio, 3),
        'max_dd_hourly_pct': round(max_dd_hourly, 2),
        'max_dd_daily_pct': round(metrics.max_drawdown_pct, 2),
        'total_return_pct': round(total_return_pct, 1),
        'n_trades': n_trades,
        'final_equity': round(final_eq, 0),
        'worst_tokens': worst_tokens,
    }
    
    print(f"  [{strategy_id}/{label}] Sharpe={result['sharpe']}, Calmar={result['calmar']}, "
          f"MaxDD_h={result['max_dd_hourly_pct']}%, Return={result['total_return_pct']}%, "
          f"Trades={n_trades}", flush=True)
    print(f"    Worst tokens: {worst_tokens}", flush=True)
    return result

results = []

# ═══════════════════════════════════════════════════
# S56 Cross-validation
# ═══════════════════════════════════════════════════
print("\n" + "="*70)
print("S56 CROSS-VALIDATION")
print("="*70)

# S56 Baseline
r = run_sim('s56', 'BASELINE', {})
results.append(r)

# S56 Winner: PUMP_20pct_168h
r = run_sim('s56', 'PUMP_20pct_168h', {
    'pump_enabled': True, 'pump_threshold': 0.20, 'pump_window': 168,
})
results.append(r)

# S56: ADV_100M + PUMP_20pct_168h (not tested in original sweep)
r = run_sim('s56', 'ADV100M+PUMP20_168h', {
    'adv_enabled': True, 'adv_base': 100_000_000, 'adv_min': 0.20,
    'pump_enabled': True, 'pump_threshold': 0.20, 'pump_window': 168,
})
results.append(r)

# S56: Low-ADV pump combo (BAN-specific: block pump only on low-ADV tokens)
r = run_sim('s56', 'LOW_ADV_PUMP_50M_30pct_168h', {
    'low_adv_pump_enabled': True, 'low_adv_pump_adv_threshold': 50_000_000,
    'low_adv_pump_ret_threshold': 0.30, 'low_adv_pump_window': 168,
})
results.append(r)

# S56: Low-ADV pump + DD filter (BAN + PIPPIN specific)  
r = run_sim('s56', 'LOW_ADV_PUMP+DD15_72h', {
    'low_adv_pump_enabled': True, 'low_adv_pump_adv_threshold': 50_000_000,
    'low_adv_pump_ret_threshold': 0.30, 'low_adv_pump_window': 168,
    'dd_enabled': True, 'dd_threshold': -0.15, 'dd_window': 72,
})
results.append(r)

# S56: ADV_100M + Low-ADV pump + DD15_72h (kitchen sink)
r = run_sim('s56', 'ADV100M+LOWPUMP+DD15_72h', {
    'adv_enabled': True, 'adv_base': 100_000_000, 'adv_min': 0.20,
    'low_adv_pump_enabled': True, 'low_adv_pump_adv_threshold': 50_000_000,
    'low_adv_pump_ret_threshold': 0.30, 'low_adv_pump_window': 168,
    'dd_enabled': True, 'dd_threshold': -0.15, 'dd_window': 72,
})
results.append(r)

# S56: PUMP_20pct_168h + DD_15pct_72h (both best single filters)
r = run_sim('s56', 'PUMP20_168h+DD15_72h', {
    'pump_enabled': True, 'pump_threshold': 0.20, 'pump_window': 168,
    'dd_enabled': True, 'dd_threshold': -0.15, 'dd_window': 72,
})
results.append(r)

# S56: Moderate settings (less aggressive)
r = run_sim('s56', 'PUMP30_168h+DD20_72h', {
    'pump_enabled': True, 'pump_threshold': 0.30, 'pump_window': 168,
    'dd_enabled': True, 'dd_threshold': -0.20, 'dd_window': 72,
})
results.append(r)

# ═══════════════════════════════════════════════════
# S60 Cross-validation  
# ═══════════════════════════════════════════════════
print("\n" + "="*70)
print("S60 CROSS-VALIDATION")
print("="*70)

# S60 Baseline
r = run_sim('s60', 'BASELINE', {})
results.append(r)

# S60 Winner: ADV_100M_min20
r = run_sim('s60', 'ADV_100M_min20', {
    'adv_enabled': True, 'adv_base': 100_000_000, 'adv_min': 0.20,
})
results.append(r)

# S60: ADV_75M_min20 (nearly as good, less return cost)
r = run_sim('s60', 'ADV_75M_min20', {
    'adv_enabled': True, 'adv_base': 75_000_000, 'adv_min': 0.20,
})
results.append(r)

# S60: ADV_100M + Low-ADV pump (targeted combo)
r = run_sim('s60', 'ADV100M+LOWPUMP', {
    'adv_enabled': True, 'adv_base': 100_000_000, 'adv_min': 0.20,
    'low_adv_pump_enabled': True, 'low_adv_pump_adv_threshold': 50_000_000,
    'low_adv_pump_ret_threshold': 0.30, 'low_adv_pump_window': 168,
})
results.append(r)

# S60: ADV_100M + DD_15pct_72h (cross-check)
r = run_sim('s60', 'ADV100M+DD15_72h', {
    'adv_enabled': True, 'adv_base': 100_000_000, 'adv_min': 0.20,
    'dd_enabled': True, 'dd_threshold': -0.15, 'dd_window': 72,
})
results.append(r)

# S60: ADV only with different clamps to find sweet spot
for clamp in [0.10, 0.15, 0.25, 0.30]:
    r = run_sim('s60', f'ADV_100M_min{int(clamp*100)}', {
        'adv_enabled': True, 'adv_base': 100_000_000, 'adv_min': clamp,
    })
    results.append(r)

# ═══════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════
out_path = '/workspace/crypto_backtest/results/v4/filter_cross_validate.json'
with open(out_path, 'w') as f:
    json.dump(results, f, indent=2, default=str)

for strat in ['s56', 's60']:
    strat_results = [r for r in results if r['strategy'] == strat]
    baseline = [r for r in strat_results if r['label'] == 'BASELINE'][0]
    
    print(f"\n{'='*80}")
    print(f"{strat.upper()} FINAL COMPARISON")
    print(f"{'='*80}")
    print(f"{'Label':<35} {'Sharpe':>7} {'Calmar':>7} {'MaxDD_h':>8} {'DD_chg':>7} {'Return':>10} {'Ret%chg':>8} {'Trades':>7}")
    print(f"{'-'*35} {'-'*7} {'-'*7} {'-'*8} {'-'*7} {'-'*10} {'-'*8} {'-'*7}")
    
    for r in strat_results:
        dd_chg = baseline['max_dd_hourly_pct'] - r['max_dd_hourly_pct']
        ret_chg = (r['total_return_pct'] / baseline['total_return_pct'] - 1) * 100 if baseline['total_return_pct'] != 0 else 0
        print(f"{r['label']:<35} {r['sharpe']:>7.3f} {r['calmar']:>7.2f} {r['max_dd_hourly_pct']:>7.2f}% {dd_chg:>+6.2f}pp {r['total_return_pct']:>9.1f}% {ret_chg:>+7.1f}% {r['n_trades']:>7}")
    
    print(f"\n  BAN/PIPPIN protection check:")
    for r in strat_results:
        tokens = dict(r['worst_tokens'])
        ban_loss = tokens.get('BAN', 0)
        pippin_loss = tokens.get('PIPPIN', 0)
        print(f"    [{r['label']:<33}] BAN: ${ban_loss:>+10,.0f}  PIPPIN: ${pippin_loss:>+10,.0f}")

print(f"\nResults saved to {out_path}")
