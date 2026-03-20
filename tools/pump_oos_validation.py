"""Out-of-sample validation: train on pre-2025, test on 2025+.
Addresses the key methodological gap: no OOS validation.

TRAIN: all data up to Dec 31 2024 (~54 months)
TEST:  Jan 1 2025 to Mar 18 2026 (~15 months, out-of-sample)
"""
import sys, os, time, json
import numpy as np
import pandas as pd
from datetime import datetime

sys.path.insert(0, '/workspace/crypto_backtest')

from v4.config import StrategySpec, PortfolioConfig
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio, _process_entries as _orig_process_entries
from v4.report import compute_portfolio_metrics
import v4.simulator as sim_module

CAPITAL = 200_000

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
TRIGGER_LOG = []

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
        adv = 0
        adv_base = FILTER_PARAMS.get('adv_base', 100_000_000)
        adv_min = FILTER_PARAMS.get('adv_min', 0.20)
        if FILTER_PARAMS.get('adv_enabled') and bar >= 24:
            adv = float(np.sum(volume[bar-23:bar+1] * close[bar-23:bar+1]))
            sizing_mult = min(1.0, max(adv_min, np.sqrt(adv / adv_base)))

        # Binary pump filter (longs only)
        if FILTER_PARAMS.get('pump_enabled') and direction == 1:
            pw = FILTER_PARAMS['pump_window']
            threshold = FILTER_PARAMS['pump_threshold']
            adv_cap = FILTER_PARAMS.get('pump_adv_cap', None)
            apply_pump = True
            if adv_cap is not None and adv > adv_cap:
                apply_pump = False
            if apply_pump and bar >= pw:
                ret = (close[bar] - close[bar - pw]) / close[bar - pw]
                if ret > threshold:
                    should_block = True
                    TRIGGER_LOG.append({
                        'token': pos.token, 'bar': bar, 'ret': float(ret),
                        'direction': direction, 'action': 'block',
                        'adv': adv, 'margin': pos.margin_usd,
                    })

        # Graduated pump filter (longs only)
        if FILTER_PARAMS.get('graduated_enabled') and direction == 1:
            pw = FILTER_PARAMS['graduated_window']
            if bar >= pw:
                ret = (close[bar] - close[bar - pw]) / close[bar - pw]
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
                        TRIGGER_LOG.append({
                            'token': pos.token, 'bar': bar, 'ret': float(ret),
                            'direction': direction, 'action': 'block_grad',
                            'adv': adv, 'margin': pos.margin_usd,
                        })
                    elif ret > t2:
                        sizing_mult *= 0.25
                        TRIGGER_LOG.append({
                            'token': pos.token, 'bar': bar, 'ret': float(ret),
                            'direction': direction, 'action': 'size_25pct',
                            'adv': adv, 'margin': pos.margin_usd,
                        })
                    elif ret > t1:
                        sizing_mult *= 0.50
                        TRIGGER_LOG.append({
                            'token': pos.token, 'bar': bar, 'ret': float(ret),
                            'direction': direction, 'action': 'size_50pct',
                            'adv': adv, 'margin': pos.margin_usd,
                        })

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

def run_sim(strategy_id, label, params, signals_cache, log_triggers=False):
    global FILTER_PARAMS, TRIGGER_LOG
    FILTER_PARAMS = params
    if log_triggers:
        TRIGGER_LOG = []

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
    for t in trades:
        token_pnl.setdefault(t.token, 0)
        token_pnl[t.token] += t.pnl
    worst_tokens = sorted(token_pnl.items(), key=lambda x: x[1])[:5]

    # Find MaxDD episode date
    worst_idx = np.argmin(dd_pct)
    timestamps = [ts for ts, _ in state.equity_snapshots]
    worst_date = pd.Timestamp(timestamps[worst_idx]).strftime('%Y-%m-%d %H:%M') if worst_idx < len(timestamps) else 'N/A'

    triggers = list(TRIGGER_LOG) if log_triggers else []

    result = {
        'strategy': strategy_id,
        'label': label,
        'sharpe': round(metrics.sharpe_ratio, 3),
        'sortino': round(metrics.sortino_ratio, 3),
        'calmar': round(metrics.calmar_ratio, 3),
        'max_dd_hourly_pct': round(max_dd_hourly, 2),
        'max_dd_date': worst_date,
        'total_return_pct': round(total_return_pct, 1),
        'n_trades': n_trades,
        'final_equity': round(final_eq, 0),
        'worst_tokens': [(t, round(float(p), 0)) for t, p in worst_tokens],
        'elapsed_s': round(elapsed, 1),
        'n_triggers': len(triggers),
        'triggers': triggers,
    }
    return result

# ── Config set to test (comprehensive) ──
def build_configs(adv_base, adv_min):
    adv = {'adv_enabled': True, 'adv_base': adv_base, 'adv_min': adv_min}
    return [
        ('BASELINE', {}),
        ('ADV_ONLY', adv),
        # Binary pump: key thresholds at key lookbacks
        ('PUMP_15pct_48h', {**adv, 'pump_enabled': True, 'pump_threshold': 0.15, 'pump_window': 48}),
        ('PUMP_15pct_72h', {**adv, 'pump_enabled': True, 'pump_threshold': 0.15, 'pump_window': 72}),
        ('PUMP_20pct_72h', {**adv, 'pump_enabled': True, 'pump_threshold': 0.20, 'pump_window': 72}),
        ('PUMP_20pct_168h', {**adv, 'pump_enabled': True, 'pump_threshold': 0.20, 'pump_window': 168}),
        ('PUMP_25pct_72h', {**adv, 'pump_enabled': True, 'pump_threshold': 0.25, 'pump_window': 72}),
        ('PUMP_25pct_168h', {**adv, 'pump_enabled': True, 'pump_threshold': 0.25, 'pump_window': 168}),
        ('PUMP_30pct_72h', {**adv, 'pump_enabled': True, 'pump_threshold': 0.30, 'pump_window': 72}),
        ('PUMP_30pct_168h', {**adv, 'pump_enabled': True, 'pump_threshold': 0.30, 'pump_window': 168}),
        ('PUMP_35pct_72h', {**adv, 'pump_enabled': True, 'pump_threshold': 0.35, 'pump_window': 72}),
        ('PUMP_35pct_168h', {**adv, 'pump_enabled': True, 'pump_threshold': 0.35, 'pump_window': 168}),
        ('PUMP_40pct_168h', {**adv, 'pump_enabled': True, 'pump_threshold': 0.40, 'pump_window': 168}),
        ('PUMP_50pct_168h', {**adv, 'pump_enabled': True, 'pump_threshold': 0.50, 'pump_window': 168}),
        # Graduated variants
        ('GRAD_15/25/40_72h', {**adv, 'graduated_enabled': True, 'graduated_window': 72,
            'graduated_t1': 0.15, 'graduated_t2': 0.25, 'graduated_t3': 0.40}),
        ('GRAD_15/25/40_168h', {**adv, 'graduated_enabled': True, 'graduated_window': 168,
            'graduated_t1': 0.15, 'graduated_t2': 0.25, 'graduated_t3': 0.40}),
        ('GRAD_20/30/40_72h', {**adv, 'graduated_enabled': True, 'graduated_window': 72,
            'graduated_t1': 0.20, 'graduated_t2': 0.30, 'graduated_t3': 0.40}),
        ('GRAD_20/30/40_168h', {**adv, 'graduated_enabled': True, 'graduated_window': 168,
            'graduated_t1': 0.20, 'graduated_t2': 0.30, 'graduated_t3': 0.40}),
        ('GRAD_20/30/50_72h', {**adv, 'graduated_enabled': True, 'graduated_window': 72,
            'graduated_t1': 0.20, 'graduated_t2': 0.30, 'graduated_t3': 0.50}),
        ('GRAD_20/30/50_168h', {**adv, 'graduated_enabled': True, 'graduated_window': 168,
            'graduated_t1': 0.20, 'graduated_t2': 0.30, 'graduated_t3': 0.50}),
        ('GRAD_25/35/50_72h', {**adv, 'graduated_enabled': True, 'graduated_window': 72,
            'graduated_t1': 0.25, 'graduated_t2': 0.35, 'graduated_t3': 0.50}),
        ('GRAD_25/35/50_168h', {**adv, 'graduated_enabled': True, 'graduated_window': 168,
            'graduated_t1': 0.25, 'graduated_t2': 0.35, 'graduated_t3': 0.50}),
        # Extra graduated combos the reviewer asked for
        ('GRAD_15/25/35_168h', {**adv, 'graduated_enabled': True, 'graduated_window': 168,
            'graduated_t1': 0.15, 'graduated_t2': 0.25, 'graduated_t3': 0.35}),
        ('GRAD_20/35/50_168h', {**adv, 'graduated_enabled': True, 'graduated_window': 168,
            'graduated_t1': 0.20, 'graduated_t2': 0.35, 'graduated_t3': 0.50}),
        ('GRAD_25/40/60_168h', {**adv, 'graduated_enabled': True, 'graduated_window': 168,
            'graduated_t1': 0.25, 'graduated_t2': 0.40, 'graduated_t3': 0.60}),
        ('GRAD_30/45/60_168h', {**adv, 'graduated_enabled': True, 'graduated_window': 168,
            'graduated_t1': 0.30, 'graduated_t2': 0.45, 'graduated_t3': 0.60}),
    ]

# ── Load signals for TRAIN and TEST periods ──
tokens = discover_tokens("perp")
data_end = infer_data_end_date("perp")

TRAIN_END = pd.Timestamp('2024-12-31 23:00:00')
TRAIN_MONTHS = 54  # ~4.5 years back from end of 2024
TEST_END = data_end  # March 18 2026
TEST_MONTHS = 15   # Jan 2025 to Mar 2026

ADV_BASES = {'s56': 75_000_000, 's60': 100_000_000}
ADV_MIN = 0.20

signals_cache_train = {}
signals_cache_test = {}

for sid in ['s56', 's60']:
    # TRAIN signals
    print(f"[{sid}] Loading TRAIN signals (end={TRAIN_END.date()}, {TRAIN_MONTHS}mo)...", flush=True)
    t0 = time.time()
    spec = StrategySpec(strategy_id=sid, weight=1.0, max_positions=15, market="perp")
    cfg = PortfolioConfig(strategies=[spec], capital=CAPITAL, max_portfolio_positions=40)
    signals = precompute_strategy_signals(spec, tokens, cfg, months=TRAIN_MONTHS, end_date=TRAIN_END)
    signals_cache_train[sid] = ({sid: signals}, {sid: spec}, spec)
    print(f"[{sid}] TRAIN loaded in {time.time()-t0:.1f}s — {len(signals)} tokens", flush=True)

    # TEST signals
    print(f"[{sid}] Loading TEST signals (end={TEST_END}, {TEST_MONTHS}mo)...", flush=True)
    t0 = time.time()
    signals = precompute_strategy_signals(spec, tokens, cfg, months=TEST_MONTHS, end_date=TEST_END)
    signals_cache_test[sid] = ({sid: signals}, {sid: spec}, spec)
    print(f"[{sid}] TEST loaded in {time.time()-t0:.1f}s — {len(signals)} tokens", flush=True)

# ── Run all configs on both periods ──
all_results = {}

for sid in ['s56', 's60']:
    configs = build_configs(ADV_BASES[sid], ADV_MIN)
    all_results[sid] = {'train': [], 'test': []}

    for period, cache, label_prefix in [
        ('train', signals_cache_train, 'TRAIN'),
        ('test', signals_cache_test, 'TEST'),
    ]:
        print(f"\n{'='*110}")
        print(f"  {sid.upper()} — {label_prefix} PERIOD {'(pre-2025)' if period=='train' else '(2025+ OOS)'}")
        print(f"{'='*110}")
        print(f"  {'Config':<28} {'Sharpe':>7} {'Sortino':>8} {'Calmar':>8} {'MaxDD%':>8} {'Return%':>10} {'Trades':>6} {'MaxDD Date':<18} {'Triggers':>8}", flush=True)
        print(f"  {'-'*28} {'-'*7} {'-'*8} {'-'*8} {'-'*8} {'-'*10} {'-'*6} {'-'*18} {'-'*8}", flush=True)

        for config_label, params in configs:
            log_triggers = (period == 'test')  # Only log triggers for test period
            r = run_sim(sid, config_label, params, cache, log_triggers=log_triggers)
            all_results[sid][period].append(r)
            print(f"  {config_label:<28} {r['sharpe']:>7.3f} {r['sortino']:>8.3f} {r['calmar']:>8.1f} {r['max_dd_hourly_pct']:>7.2f}% {r['total_return_pct']:>9.1f}% {r['n_trades']:>6} {r['max_dd_date']:<18} {r['n_triggers']:>8}", flush=True)

# ── In-sample vs Out-of-sample comparison ──
print(f"\n{'='*130}")
print("  IN-SAMPLE vs OUT-OF-SAMPLE COMPARISON")
print(f"{'='*130}")

for sid in ['s56', 's60']:
    train_results = {r['label']: r for r in all_results[sid]['train']}
    test_results = {r['label']: r for r in all_results[sid]['test']}

    train_bl = train_results.get('ADV_ONLY', train_results['BASELINE'])
    test_bl = test_results.get('ADV_ONLY', test_results['BASELINE'])

    print(f"\n  {sid.upper()}")
    print(f"  {'Config':<28} {'TRAIN Sharpe':>12} {'TEST Sharpe':>12} {'TRAIN DD%':>10} {'TEST DD%':>10} {'TRAIN Ret%':>11} {'TEST Ret%':>11} {'Consistent?':>12}")
    print(f"  {'-'*28} {'-'*12} {'-'*12} {'-'*10} {'-'*10} {'-'*11} {'-'*11} {'-'*12}")

    for label in [r['label'] for r in all_results[sid]['train']]:
        tr = train_results[label]
        te = test_results[label]

        # "Consistent" = both improve on ADV-only baseline, or both don't
        train_dd_better = tr['max_dd_hourly_pct'] > train_bl['max_dd_hourly_pct']
        test_dd_better = te['max_dd_hourly_pct'] > test_bl['max_dd_hourly_pct']
        train_sharpe_better = tr['sharpe'] >= train_bl['sharpe'] - 0.05
        test_sharpe_better = te['sharpe'] >= test_bl['sharpe'] - 0.05

        if label in ('BASELINE', 'ADV_ONLY'):
            consistent = '—'
        elif train_dd_better == test_dd_better:
            consistent = 'YES' if train_dd_better else 'YES (none)'
        else:
            consistent = 'NO'

        print(f"  {label:<28} {tr['sharpe']:>12.3f} {te['sharpe']:>12.3f} {tr['max_dd_hourly_pct']:>9.2f}% {te['max_dd_hourly_pct']:>9.2f}% {tr['total_return_pct']:>10.1f}% {te['total_return_pct']:>10.1f}% {consistent:>12}")

# ── Trigger analysis for test period ──
print(f"\n{'='*130}")
print("  TRIGGER ANALYSIS — TEST PERIOD (2025+ OOS)")
print(f"{'='*130}")

for sid in ['s56', 's60']:
    test_results_list = all_results[sid]['test']

    # Find configs with triggers
    for r in test_results_list:
        if r['n_triggers'] > 0:
            print(f"\n  {sid.upper()} / {r['label']}: {r['n_triggers']} triggers")
            trigger_summary = {}
            for trig in r['triggers']:
                key = (trig['token'], trig['action'])
                trigger_summary.setdefault(key, []).append(trig)
            for (token, action), trigs in sorted(trigger_summary.items()):
                avg_ret = np.mean([t['ret'] for t in trigs])
                avg_margin = np.mean([t['margin'] for t in trigs])
                print(f"    {token:<12} {action:<12} ×{len(trigs):>3}  avg_ret={avg_ret:+.1%}  avg_margin=${avg_margin:,.0f}")

# ── Best config selection: what TRAIN data recommends vs TEST reality ──
print(f"\n{'='*130}")
print("  TRAIN-RECOMMENDED vs TEST-VALIDATED BEST CONFIGS")
print(f"{'='*130}")

for sid in ['s56', 's60']:
    train_results = all_results[sid]['train']
    test_results = {r['label']: r for r in all_results[sid]['test']}

    # Skip baseline and ADV_ONLY
    candidates = [r for r in train_results if r['label'] not in ('BASELINE', 'ADV_ONLY')]
    adv_only_train = next(r for r in train_results if r['label'] == 'ADV_ONLY')
    adv_only_test = test_results['ADV_ONLY']

    # Rank by: best DD improvement with Sharpe >= ADV_ONLY - 0.1
    valid_train = [r for r in candidates if r['sharpe'] >= adv_only_train['sharpe'] - 0.1]
    valid_train.sort(key=lambda r: r['max_dd_hourly_pct'], reverse=True)  # Less negative = better

    print(f"\n  {sid.upper()} — Top 5 by TRAIN DD (with Sharpe constraint):")
    print(f"  {'Rank':<5} {'Config':<28} {'TRAIN DD%':>10} {'TRAIN Sharpe':>13} {'→ TEST DD%':>11} {'TEST Sharpe':>12} {'TEST Ret%':>10} {'OOS valid?':>11}")
    print(f"  {'-'*5} {'-'*28} {'-'*10} {'-'*13} {'-'*11} {'-'*12} {'-'*10} {'-'*11}")

    for i, tr in enumerate(valid_train[:10]):
        te = test_results[tr['label']]
        train_dd_imp = tr['max_dd_hourly_pct'] - adv_only_train['max_dd_hourly_pct']
        test_dd_imp = te['max_dd_hourly_pct'] - adv_only_test['max_dd_hourly_pct']
        oos_valid = 'YES' if test_dd_imp > 0 else 'NO'
        print(f"  {i+1:<5} {tr['label']:<28} {tr['max_dd_hourly_pct']:>9.2f}% {tr['sharpe']:>13.3f} {te['max_dd_hourly_pct']:>10.2f}% {te['sharpe']:>12.3f} {te['total_return_pct']:>9.1f}% {oos_valid:>11}")

    # Also show: what TEST data says is best
    valid_test = [test_results[r['label']] for r in candidates]
    valid_test.sort(key=lambda r: r['max_dd_hourly_pct'], reverse=True)

    print(f"\n  {sid.upper()} — Top 5 by TEST DD (hindsight, for comparison):")
    print(f"  {'Rank':<5} {'Config':<28} {'TEST DD%':>10} {'TEST Sharpe':>12} {'TEST Ret%':>10}")
    print(f"  {'-'*5} {'-'*28} {'-'*10} {'-'*12} {'-'*10}")
    for i, te in enumerate(valid_test[:10]):
        print(f"  {i+1:<5} {te['label']:<28} {te['max_dd_hourly_pct']:>9.2f}% {te['sharpe']:>12.3f} {te['total_return_pct']:>9.1f}%")

# ── Save ──
# Strip triggers from saved results to keep file small
save_results = {}
for sid in ['s56', 's60']:
    save_results[sid] = {}
    for period in ['train', 'test']:
        save_results[sid][period] = []
        for r in all_results[sid][period]:
            r_copy = {k: v for k, v in r.items() if k != 'triggers'}
            save_results[sid][period].append(r_copy)

out_path = '/workspace/crypto_backtest/results/v4/pump_oos_validation.json'
with open(out_path, 'w') as f:
    json.dump(save_results, f, indent=2, default=str)

print(f"\nSaved to {out_path}")
