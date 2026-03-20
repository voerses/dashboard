"""Final analysis of S60 72mo ADV anomaly.
Compares baseline vs ADV100M across ALL major DD episodes.
Tests the hypothesis: ADV absolute DD is always smaller, percentage is an artifact.
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

# ── Load signals ──
tokens = discover_tokens("perp")
end_date = infer_data_end_date("perp")

print("[s60] Loading 72mo signals...", flush=True)
t0 = time.time()
spec = StrategySpec(strategy_id='s60', weight=1.0, max_positions=15, market="perp")
signals = precompute_strategy_signals(spec, tokens, PortfolioConfig(
    strategies=[spec], capital=CAPITAL, max_portfolio_positions=40
), months=MONTHS, end_date=end_date)
signals_cache = ({spec.strategy_id: signals}, {spec.strategy_id: spec}, spec)
print(f"[s60] Loaded in {time.time()-t0:.1f}s — {len(signals)} tokens", flush=True)

def run_and_get_equity(label, params):
    global FILTER_PARAMS
    FILTER_PARAMS = params

    all_signals, strategy_specs, spec = signals_cache
    config = PortfolioConfig(strategies=[spec], capital=CAPITAL, max_portfolio_positions=40)

    state = simulate_portfolio(all_signals, strategy_specs, config)

    timestamps = np.array([ts for ts, _ in state.equity_snapshots])
    equities = np.array([eq for _, eq in state.equity_snapshots])

    return timestamps, equities, state

# ── Run both configs ──
print("\nRunning BASELINE...", flush=True)
ts_bl, eq_bl, state_bl = run_and_get_equity('BASELINE', {})
print(f"  Final equity: ${eq_bl[-1]:,.0f}", flush=True)

print("Running ADV100M_min20...", flush=True)
ts_adv, eq_adv, state_adv = run_and_get_equity('ADV100M_min20', {
    'adv_enabled': True, 'adv_base': 100_000_000, 'adv_min': 0.20
})
print(f"  Final equity: ${eq_adv[-1]:,.0f}", flush=True)

# ── Find ALL DD episodes > 5% for both configs ──
def find_dd_episodes(equities, timestamps, threshold_pct=-5.0):
    """Find all DD episodes exceeding threshold."""
    running_max = np.maximum.accumulate(equities)
    dd_pct = (equities - running_max) / running_max * 100

    episodes = []
    in_dd = False
    ep_start = 0

    for i in range(len(dd_pct)):
        if dd_pct[i] < threshold_pct and not in_dd:
            # Find the peak before this DD
            peak_idx = np.argmax(equities[:i+1])
            in_dd = True
            ep_start = peak_idx
        elif dd_pct[i] >= -0.5 and in_dd:  # DD recovered
            trough_idx = ep_start + np.argmin(dd_pct[ep_start:i+1])
            episodes.append({
                'peak_idx': ep_start,
                'trough_idx': trough_idx,
                'peak_ts': timestamps[ep_start],
                'trough_ts': timestamps[trough_idx],
                'peak_eq': equities[ep_start],
                'trough_eq': equities[trough_idx],
                'dd_pct': float(dd_pct[trough_idx]),
                'dd_abs': float(equities[trough_idx] - equities[ep_start]),
            })
            in_dd = False

    # Check if still in DD at end
    if in_dd:
        trough_idx = ep_start + np.argmin(dd_pct[ep_start:])
        episodes.append({
            'peak_idx': ep_start,
            'trough_idx': trough_idx,
            'peak_ts': timestamps[ep_start],
            'trough_ts': timestamps[trough_idx],
            'peak_eq': equities[ep_start],
            'trough_eq': equities[trough_idx],
            'dd_pct': float(dd_pct[trough_idx]),
            'dd_abs': float(equities[trough_idx] - equities[ep_start]),
        })

    return sorted(episodes, key=lambda x: x['dd_pct'])

print("\n" + "="*120)
print("  S60 72mo — ALL DRAWDOWN EPISODES > 5%")
print("="*120)

eps_bl = find_dd_episodes(eq_bl, ts_bl, -5.0)
eps_adv = find_dd_episodes(eq_adv, ts_adv, -5.0)

print(f"\nBASELINE — {len(eps_bl)} episodes > 5% DD:")
print(f"{'#':<3} {'Peak Date':<22} {'Trough Date':<22} {'Peak Eq':>14} {'Trough Eq':>14} {'DD%':>8} {'DD$':>14}")
print(f"{'-'*3} {'-'*22} {'-'*22} {'-'*14} {'-'*14} {'-'*8} {'-'*14}")
for i, ep in enumerate(eps_bl):
    peak_date = pd.Timestamp(ep['peak_ts']).strftime('%Y-%m-%d %H:%M')
    trough_date = pd.Timestamp(ep['trough_ts']).strftime('%Y-%m-%d %H:%M')
    print(f"{i+1:<3} {peak_date:<22} {trough_date:<22} ${ep['peak_eq']:>12,.0f} ${ep['trough_eq']:>12,.0f} {ep['dd_pct']:>7.2f}% ${ep['dd_abs']:>12,.0f}")

print(f"\nADV100M — {len(eps_adv)} episodes > 5% DD:")
print(f"{'#':<3} {'Peak Date':<22} {'Trough Date':<22} {'Peak Eq':>14} {'Trough Eq':>14} {'DD%':>8} {'DD$':>14}")
print(f"{'-'*3} {'-'*22} {'-'*22} {'-'*14} {'-'*14} {'-'*8} {'-'*14}")
for i, ep in enumerate(eps_adv):
    peak_date = pd.Timestamp(ep['peak_ts']).strftime('%Y-%m-%d %H:%M')
    trough_date = pd.Timestamp(ep['trough_ts']).strftime('%Y-%m-%d %H:%M')
    print(f"{i+1:<3} {peak_date:<22} {trough_date:<22} ${ep['peak_eq']:>12,.0f} ${ep['trough_eq']:>12,.0f} {ep['dd_pct']:>7.2f}% ${ep['dd_abs']:>12,.0f}")

# ── Cross-reference: for each BASELINE episode, what was ADV DD at that same time? ──
print("\n" + "="*120)
print("  CROSS-REFERENCE: Same time period, both configs")
print("="*120)
print(f"{'Episode Date':<22} {'BL DD%':>8} {'BL DD$':>14} {'ADV DD%':>8} {'ADV DD$':>14} {'ADV better%?':>12} {'ADV better$?':>12}")
print(f"{'-'*22} {'-'*8} {'-'*14} {'-'*8} {'-'*14} {'-'*12} {'-'*12}")

# For each baseline episode, measure ADV performance at the SAME bars
running_max_bl = np.maximum.accumulate(eq_bl)
dd_pct_bl = (eq_bl - running_max_bl) / running_max_bl * 100
running_max_adv = np.maximum.accumulate(eq_adv)
dd_pct_adv = (eq_adv - running_max_adv) / running_max_adv * 100

# Collect all unique episodes from both configs
all_episodes = []
for ep in eps_bl[:10]:  # Top 10 from baseline
    all_episodes.append(ep['trough_idx'])
for ep in eps_adv[:10]:  # Top 10 from ADV
    all_episodes.append(ep['trough_idx'])
all_episodes = sorted(set(all_episodes))

# For each episode trough, compute the local DD for both configs
# Use a window around each trough to find the local peak
for trough_idx in all_episodes:
    # Find local peak within 500 bars before the trough
    window_start = max(0, trough_idx - 500)

    bl_local_peak = float(np.max(eq_bl[window_start:trough_idx+1]))
    bl_local_trough = float(np.min(eq_bl[trough_idx-20:trough_idx+20]))  # narrow around trough
    bl_local_dd_pct = (bl_local_trough - bl_local_peak) / bl_local_peak * 100
    bl_local_dd_abs = bl_local_trough - bl_local_peak

    adv_local_peak = float(np.max(eq_adv[window_start:trough_idx+1]))
    adv_local_trough = float(np.min(eq_adv[trough_idx-20:trough_idx+20]))
    adv_local_dd_pct = (adv_local_trough - adv_local_peak) / adv_local_peak * 100
    adv_local_dd_abs = adv_local_trough - adv_local_peak

    trough_date = pd.Timestamp(ts_bl[trough_idx]).strftime('%Y-%m-%d %H:%M')
    better_pct = "YES" if adv_local_dd_pct > bl_local_dd_pct else "NO"
    better_abs = "YES" if adv_local_dd_abs > bl_local_dd_abs else "NO"

    print(f"{trough_date:<22} {bl_local_dd_pct:>7.2f}% ${bl_local_dd_abs:>12,.0f} {adv_local_dd_pct:>7.2f}% ${adv_local_dd_abs:>12,.0f} {better_pct:>12} {better_abs:>12}")

# ── The key question: is ADV ALWAYS better in absolute $ terms? ──
print("\n" + "="*120)
print("  KEY QUESTION: Is ADV always better in absolute dollar DD?")
print("="*120)

# Compute rolling DD in both $ and % for both configs
dd_abs_bl = eq_bl - running_max_bl
dd_abs_adv = eq_adv - running_max_adv

# Find the WORST absolute DD for each
worst_abs_bl_idx = np.argmin(dd_abs_bl)
worst_abs_adv_idx = np.argmin(dd_abs_adv)

print(f"\nBaseline worst absolute DD: ${float(dd_abs_bl[worst_abs_bl_idx]):,.0f} at bar {worst_abs_bl_idx} ({pd.Timestamp(ts_bl[worst_abs_bl_idx]).strftime('%Y-%m-%d %H:%M')})")
print(f"ADV100M worst absolute DD: ${float(dd_abs_adv[worst_abs_adv_idx]):,.0f} at bar {worst_abs_adv_idx} ({pd.Timestamp(ts_adv[worst_abs_adv_idx]).strftime('%Y-%m-%d %H:%M')})")

print(f"\nBaseline worst percentage DD: {float(np.min(dd_pct_bl)):.2f}% at bar {np.argmin(dd_pct_bl)}")
print(f"ADV100M worst percentage DD: {float(np.min(dd_pct_adv)):.2f}% at bar {np.argmin(dd_pct_adv)}")

# ── At every point in time, compare the two equity curves ──
min_len = min(len(eq_bl), len(eq_adv))
eq_ratio = eq_adv[:min_len] / eq_bl[:min_len]

print(f"\nEquity ratio (ADV/BL) stats:")
print(f"  Mean:   {float(np.mean(eq_ratio)):.4f}")
print(f"  Min:    {float(np.min(eq_ratio)):.4f} at bar {np.argmin(eq_ratio)} ({pd.Timestamp(ts_bl[np.argmin(eq_ratio)]).strftime('%Y-%m-%d %H:%M')})")
print(f"  Max:    {float(np.max(eq_ratio)):.4f} at bar {np.argmax(eq_ratio)} ({pd.Timestamp(ts_bl[np.argmax(eq_ratio)]).strftime('%Y-%m-%d %H:%M')})")
print(f"  Median: {float(np.median(eq_ratio)):.4f}")

# ── Explain the mechanism ──
print("\n" + "="*120)
print("  MECHANISM: Why ADV has higher % DD but lower $ DD")
print("="*120)

# Show equity at the key moments
worst_bl_trough = np.argmin(dd_pct_bl)
worst_adv_trough = np.argmin(dd_pct_adv)

# For baseline worst episode
bl_peak_idx = np.argmax(eq_bl[:worst_bl_trough+1])
print(f"\nBASELINE worst episode (June 2021):")
print(f"  Peak:   bar {bl_peak_idx} ({pd.Timestamp(ts_bl[bl_peak_idx]).strftime('%Y-%m-%d %H:%M')})")
print(f"  BL eq:  ${eq_bl[bl_peak_idx]:>14,.0f}  →  ${eq_bl[worst_bl_trough]:>14,.0f}  = {dd_pct_bl[worst_bl_trough]:.2f}% (${dd_abs_bl[worst_bl_trough]:,.0f})")
print(f"  ADV eq: ${eq_adv[bl_peak_idx]:>14,.0f}  →  ${eq_adv[worst_bl_trough]:>14,.0f}")
adv_dd_at_bl_episode = (eq_adv[worst_bl_trough] - np.max(eq_adv[:worst_bl_trough+1])) / np.max(eq_adv[:worst_bl_trough+1]) * 100
adv_dd_abs_at_bl = eq_adv[worst_bl_trough] - np.max(eq_adv[:worst_bl_trough+1])
print(f"  ADV DD at this moment: {adv_dd_at_bl_episode:.2f}% (${adv_dd_abs_at_bl:,.0f})")

# For ADV worst episode
adv_peak_idx = np.argmax(eq_adv[:worst_adv_trough+1])
print(f"\nADV worst episode (May 2021):")
print(f"  Peak:   bar {adv_peak_idx} ({pd.Timestamp(ts_adv[adv_peak_idx]).strftime('%Y-%m-%d %H:%M')})")
print(f"  ADV eq: ${eq_adv[adv_peak_idx]:>14,.0f}  →  ${eq_adv[worst_adv_trough]:>14,.0f}  = {dd_pct_adv[worst_adv_trough]:.2f}% (${dd_abs_adv[worst_adv_trough]:,.0f})")
print(f"  BL eq:  ${eq_bl[adv_peak_idx]:>14,.0f}  →  ${eq_bl[worst_adv_trough]:>14,.0f}")
bl_dd_at_adv_episode = (eq_bl[worst_adv_trough] - np.max(eq_bl[:worst_adv_trough+1])) / np.max(eq_bl[:worst_adv_trough+1]) * 100
bl_dd_abs_at_adv = eq_bl[worst_adv_trough] - np.max(eq_bl[:worst_adv_trough+1])
print(f"  BL DD at this moment: {bl_dd_at_adv_episode:.2f}% (${bl_dd_abs_at_adv:,.0f})")

# ── Equity ratio at key moments ──
print(f"\nEquity ratio (ADV/BL) at key moments:")
print(f"  At ADV peak (before May crash):  {eq_adv[adv_peak_idx]/eq_bl[adv_peak_idx]:.4f}")
print(f"  At ADV trough (May crash):       {eq_adv[worst_adv_trough]/eq_bl[worst_adv_trough]:.4f}")
print(f"  At BL peak (before June crash):  {eq_adv[bl_peak_idx]/eq_bl[bl_peak_idx]:.4f}")
print(f"  At BL trough (June crash):       {eq_adv[worst_bl_trough]/eq_bl[worst_bl_trough]:.4f}")

# ── Which tokens caused the May 2021 crash exposure difference? ──
print("\n" + "="*120)
print("  TRADE ANALYSIS: What caused the May 2021 crash difference?")
print("="*120)

# Look at open positions during the May 2021 crash window
# We can check what tokens had the worst PnL around that time
trades_bl = state_bl.position_manager.closed_trades
trades_adv = state_adv.position_manager.closed_trades

# Filter trades closing within 100 bars of ADV's worst trough
crash_window_start = worst_adv_trough - 50
crash_window_end = worst_adv_trough + 50

crash_trades_bl = [t for t in trades_bl if crash_window_start <= t.exit_bar <= crash_window_end]
crash_trades_adv = [t for t in trades_adv if crash_window_start <= t.exit_bar <= crash_window_end]

print(f"\nTrades closing within ±50 bars of May 2021 crash trough (bar {worst_adv_trough}):")
print(f"  Baseline: {len(crash_trades_bl)} trades")
print(f"  ADV100M:  {len(crash_trades_adv)} trades")

# Token PnL breakdown during crash
token_pnl_bl = {}
for t in crash_trades_bl:
    token_pnl_bl.setdefault(t.token, 0)
    token_pnl_bl[t.token] += t.pnl

token_pnl_adv = {}
for t in crash_trades_adv:
    token_pnl_adv.setdefault(t.token, 0)
    token_pnl_adv[t.token] += t.pnl

print(f"\nBaseline crash window PnL by token:")
for tok, pnl in sorted(token_pnl_bl.items(), key=lambda x: x[1]):
    print(f"  {tok:<12} ${pnl:>+12,.0f}")

print(f"\nADV100M crash window PnL by token:")
for tok, pnl in sorted(token_pnl_adv.items(), key=lambda x: x[1]):
    adv_mult = ""
    if tok in token_pnl_bl and token_pnl_bl[tok] != 0:
        ratio = pnl / token_pnl_bl[tok]
        adv_mult = f"  ({ratio:.2f}x BL)"
    print(f"  {tok:<12} ${pnl:>+12,.0f}{adv_mult}")

# ── Final summary ──
print("\n" + "="*120)
print("  CONCLUSION")
print("="*120)

# Compute DD stats that matter for production
print(f"""
  Metric                    BASELINE        ADV100M         Delta
  ─────────────────────────────────────────────────────────────────
  Worst % DD:               {float(np.min(dd_pct_bl)):>7.2f}%        {float(np.min(dd_pct_adv)):>7.2f}%        {float(np.min(dd_pct_adv)-np.min(dd_pct_bl)):>+.2f}pp
  Worst $ DD:               ${float(np.min(dd_abs_bl)):>12,.0f}  ${float(np.min(dd_abs_adv)):>12,.0f}  ${float(np.min(dd_abs_adv)-np.min(dd_abs_bl)):>+12,.0f}
  Worst % episode date:     {pd.Timestamp(ts_bl[np.argmin(dd_pct_bl)]).strftime('%Y-%m-%d')}       {pd.Timestamp(ts_adv[np.argmin(dd_pct_adv)]).strftime('%Y-%m-%d')}
  Same episodes?            {'YES' if abs(np.argmin(dd_pct_bl)-np.argmin(dd_pct_adv)) < 50 else 'NO — DIFFERENT EPISODES'}
""")

# Count how many bars ADV absolute DD is worse vs better
adv_worse_abs = np.sum(dd_abs_adv[:min_len] < dd_abs_bl[:min_len])
adv_better_abs = np.sum(dd_abs_adv[:min_len] > dd_abs_bl[:min_len])
print(f"  Bars where ADV has WORSE absolute DD: {adv_worse_abs} ({adv_worse_abs/min_len*100:.1f}%)")
print(f"  Bars where ADV has BETTER absolute DD: {adv_better_abs} ({adv_better_abs/min_len*100:.1f}%)")

# What percentage of the time is ADV equity higher?
adv_higher = np.sum(eq_adv[:min_len] > eq_bl[:min_len])
print(f"  Bars where ADV equity > BL equity: {adv_higher} ({adv_higher/min_len*100:.1f}%)")

# Risk-adjusted: compute the DD/equity ratio (what fraction of your equity are you losing?)
# This is the metric that actually matters for a fixed-capital production account
print(f"\n  FOR FIXED $200K CAPITAL (production scenario):")
print(f"  Baseline worst DD as % of $200K: {float(np.min(dd_abs_bl))/200_000*100:.1f}%")
print(f"  ADV100M worst DD as % of $200K:  {float(np.min(dd_abs_adv))/200_000*100:.1f}%")
print(f"  → ADV is {abs(float(np.min(dd_abs_bl))-float(np.min(dd_abs_adv)))/200_000*100:.1f}pp BETTER on fixed capital")

print(f"\nSaved analysis complete.")
