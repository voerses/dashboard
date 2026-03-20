"""
Adaptive grace period sweep for any strategy.

Tests multiple grace period policies against the fixed no_stop_bars baseline.
Uses the standard v4 simulation pipeline (simulate_portfolio + compute_portfolio_metrics).

Approach: monkey-patches _process_entries to override no_stop_bars on each
newly created Position using token-level data available at entry time.

Usage:
    STRATEGY_ID=s72 MONTHS=12 python tools/adaptive_grace_sweep.py
    STRATEGY_ID=s65 MONTHS=12 python tools/adaptive_grace_sweep.py
"""
import sys, os, time, json
import numpy as np
import pandas as pd
from scipy.stats import percentileofscore

sys.path.insert(0, '/workspace/crypto_backtest')

from v4.config import StrategySpec, PortfolioConfig
from v4.signals import precompute_strategy_signals, discover_tokens, infer_data_end_date
from v4.simulator import simulate_portfolio, SimulationState
from v4.report import compute_portfolio_metrics
import v4.simulator as sim_module

# ── Configuration ──
STRATEGY_ID = os.environ.get("STRATEGY_ID", "s72")
MONTHS = int(os.environ.get("MONTHS", "12"))
CAPITAL = 200_000

# ── Token data cache for adaptive computations ──
_token_data_cache = {}

def _load_token_data(token):
    if token in _token_data_cache:
        return _token_data_cache[token]
    for subdir in ['perp', 'spot']:
        path = f'/workspace/crypto_backtest/data/{subdir}/1h_cache/{token}_1h.parquet'
        if os.path.exists(path):
            df = pd.read_parquet(path)
            # Precompute ATR
            h, l, c = df['high'].values, df['low'].values, df['close'].values
            tr = np.empty(len(df))
            tr[0] = h[0] - l[0]
            tr[1:] = np.maximum(h[1:] - l[1:],
                        np.maximum(np.abs(h[1:] - c[:-1]),
                                   np.abs(l[1:] - c[:-1])))
            atr = pd.Series(tr).rolling(14, min_periods=1).mean().values
            df['_atr14'] = atr
            _token_data_cache[token] = df
            return df
    _token_data_cache[token] = None
    return None


def atr_percentile(token, bar_idx, lookback=500):
    """ATR percentile of current bar vs trailing window."""
    df = _load_token_data(token)
    if df is None:
        return 50.0
    atr = df['_atr14'].values
    idx = min(bar_idx, len(atr) - 1)
    start = max(0, idx - lookback)
    window = atr[start:idx]
    if len(window) < 20:
        return 50.0
    return float(percentileofscore(window, atr[idx]))


def return_zscore(token, bar_idx, ret_window=168):
    """Z-score of recent return vs history."""
    df = _load_token_data(token)
    if df is None:
        return 0.0
    c = df['close'].values
    idx = min(bar_idx, len(c) - 1)
    if idx < ret_window:
        return 0.0
    ret = (c[idx] - c[idx - ret_window]) / max(c[idx - ret_window], 1e-10)
    # Historical returns
    lookback = min(2000, idx - ret_window)
    start = max(ret_window, idx - lookback)
    hist = [(c[i] - c[i - ret_window]) / max(c[i - ret_window], 1e-10) for i in range(start, idx, 4)]  # sample every 4th bar for speed
    if len(hist) < 20:
        return 0.0
    mu, sigma = np.mean(hist), np.std(hist)
    return (ret - mu) / max(sigma, 1e-10)


def token_adv(token, bar_idx, window=168):
    """Average daily volume (USD) over trailing window."""
    df = _load_token_data(token)
    if df is None:
        return 100_000_000
    c, v = df['close'].values, df['volume'].values
    idx = min(bar_idx, len(c) - 1)
    start = max(0, idx - window)
    usd_vol = c[start:idx+1] * v[start:idx+1]
    return float(np.mean(usd_vol)) * 24 if len(usd_vol) > 0 else 100_000_000


def token_tier(adv_usd, vol_pct=None):
    """Classify token by ADV + volatility into tier with grace multiplier.

    vol_pct: annualized volatility %. High vol with high ADV = meme (e.g. PIPPIN).
    """
    # High-volume meme detection: if ADV > 5M but vol > 200% annualized
    if vol_pct is not None and vol_pct > 200 and adv_usd < 500_000_000:
        return 'meme_volatile', 0.4
    if adv_usd > 500_000_000:
        return 'btc_eth', 1.5
    elif adv_usd > 100_000_000:
        if vol_pct is not None and vol_pct > 150:
            return 'large_volatile', 0.6  # high-vol large cap (meme-like)
        return 'large', 1.0
    elif adv_usd > 30_000_000:
        return 'mid', 0.8
    elif adv_usd > 5_000_000:
        return 'small', 0.6
    else:
        return 'micro', 0.4


def _annualized_vol(token, bar_idx, lookback=168):
    """Annualized volatility % from hourly returns."""
    df = _load_token_data(token)
    if df is None:
        return 100.0
    c = df['close'].values
    idx = min(bar_idx, len(c) - 1)
    start = max(1, idx - lookback)
    if idx - start < 24:
        return 100.0
    rets = np.diff(np.log(c[start:idx+1]))
    return float(np.std(rets) * np.sqrt(8760) * 100)  # hourly -> annualized


# ── Grace Period Policies ──

def grace_fixed(base, token, bar_idx, **kw):
    return base

def grace_vol(base, token, bar_idx, **kw):
    """High vol -> shorter grace."""
    pct = atr_percentile(token, bar_idx) / 100.0
    return max(1, round(base * (1.5 - pct)))

def grace_tier(base, token, bar_idx, **kw):
    """Low-liquidity/meme/volatile tokens get shorter grace."""
    adv = token_adv(token, bar_idx)
    vol = _annualized_vol(token, bar_idx)
    _, mult = token_tier(adv, vol)
    return max(1, round(base * mult))

def grace_regime(base, token, bar_idx, regime=1, **kw):
    """Volatile/crisis regimes -> shorter grace."""
    mults = {0: 0.3, 1: 1.5, 2: 1.2, 3: 0.8, 4: 0.5}
    return max(1, round(base * mults.get(regime, 1.0)))

def grace_pump(base, token, bar_idx, **kw):
    """Recent pump/dump -> shorter grace."""
    z = return_zscore(token, bar_idx)
    return max(1, round(base / (1.0 + 0.3 * abs(z))))

def grace_composite(base, token, bar_idx, regime=1, **kw):
    """All factors combined with guardrails."""
    vol_f = 1.5 - atr_percentile(token, bar_idx) / 100.0
    adv = token_adv(token, bar_idx)
    ann_vol = _annualized_vol(token, bar_idx)
    _, tier_f = token_tier(adv, ann_vol)
    regime_mults = {0: 0.3, 1: 1.5, 2: 1.2, 3: 0.8, 4: 0.5}
    regime_f = regime_mults.get(regime, 1.0)
    z = return_zscore(token, bar_idx)
    pump_f = 1.0 / (1.0 + 0.3 * abs(z))

    grace = base * vol_f * tier_f * regime_f * pump_f

    tier_name, _ = token_tier(adv, ann_vol)
    bounds = {'btc_eth': (6, 72), 'large': (4, 48), 'mid': (3, 36), 'small': (2, 24), 'micro': (1, 12)}
    lo, hi = bounds.get(tier_name, (1, 48))
    return max(lo, min(hi, round(grace)))


# ── Monkey-patch infrastructure ──

_orig_process_entries = sim_module._process_entries
ACTIVE_POLICY = None
ACTIVE_BASE_NSB = 48
# We need access to signal regime at entry bar. Store a ref to all_signals.
_current_all_signals = None
_current_bar_maps = None

def _patched_process_entries(state, all_signals, strategy_specs, bar_maps, global_bar, config, rng):
    """Wrap original _process_entries, then override no_stop_bars on new positions."""
    global _current_all_signals, _current_bar_maps
    _current_all_signals = all_signals
    _current_bar_maps = bar_maps

    # Snapshot existing position IDs
    existing_ids = {p.position_id for p in state.position_manager.open_positions}

    # Run original
    _orig_process_entries(state, all_signals, strategy_specs, bar_maps, global_bar, config, rng)

    # Find newly created positions and override their no_stop_bars
    if ACTIVE_POLICY is not None and ACTIVE_POLICY != grace_fixed:
        for pos in state.position_manager.open_positions:
            if pos.position_id not in existing_ids:
                # Get regime at entry from signal data
                regime = 1  # default QUIET
                sigs = all_signals.get(pos.strategy_id, {})
                sig = sigs.get(pos.token)
                if sig is not None:
                    bm = bar_maps.get(pos.token)
                    if bm is not None and global_bar < len(bm):
                        local_bar = int(bm[global_bar])
                        if 0 <= local_bar < sig.n_bars and hasattr(sig, 'regime') and sig.regime is not None:
                            regime = int(sig.regime[local_bar])

                new_nsb = ACTIVE_POLICY(
                    ACTIVE_BASE_NSB, pos.token, pos.entry_bar,
                    regime=regime,
                )
                pos.no_stop_bars = new_nsb

# Install the patch
sim_module._process_entries = _patched_process_entries


# ── Simulation runner ──

def run_sim(label, policy_fn, base_nsb, strategy_id, signals, spec):
    global ACTIVE_POLICY, ACTIVE_BASE_NSB
    ACTIVE_POLICY = policy_fn
    ACTIVE_BASE_NSB = base_nsb

    # Set base no_stop_bars on all signals (policy_fixed uses this directly)
    for token_sig in signals.values():
        token_sig.no_stop_bars = base_nsb

    all_signals = {strategy_id: signals}
    strategy_specs = {strategy_id: spec}
    config = PortfolioConfig(
        capital=CAPITAL,
        max_portfolio_positions=15,
        concentration_limit=1.0,
        adv_cap_pct=0.05,
        seed=42,
        max_sizing_equity=2_000_000, stress_adv_multiplier=0.5,
        impact_coeff=0.01,
    )

    t0 = time.time()
    state = simulate_portfolio(all_signals, strategy_specs, config)
    elapsed = time.time() - t0

    perf, extra, eq_daily = compute_portfolio_metrics(state, CAPITAL)

    equities = np.array([eq for _, eq in state.equity_snapshots])
    if len(equities) == 0:
        return None
    n_trades = len(state.position_manager.closed_trades)
    final_eq = equities[-1]
    total_ret = (final_eq / CAPITAL - 1) * 100

    # Use DAILY MaxDD from metrics (same denominator Calmar uses)
    max_dd = perf.max_drawdown_pct  # daily-resampled, consistent with calmar_ratio

    # Avg grace actually used
    graces = [getattr(t, 'no_stop_bars', base_nsb) for t in state.position_manager.closed_trades]
    graces += [getattr(p, 'no_stop_bars', base_nsb) for p in state.position_manager.open_positions]
    avg_grace = np.mean(graces) if graces else base_nsb

    # Grace distribution
    if graces:
        g_arr = np.array(graces)
        grace_p10, grace_p50, grace_p90 = np.percentile(g_arr, [10, 50, 90])
    else:
        grace_p10 = grace_p50 = grace_p90 = base_nsb

    result = {
        'label': label,
        'strategy': strategy_id,
        'base_nsb': base_nsb,
        'total_return_pct': round(total_ret, 1),
        'max_dd_pct': round(max_dd, 2),
        'sharpe': round(perf.sharpe_ratio, 2),
        'calmar': round(perf.calmar_ratio, 1),
        'sortino': round(perf.sortino_ratio, 2),
        'trades': n_trades,
        'win_rate': round(perf.win_rate_pct, 1),
        'avg_grace': round(avg_grace, 1),
        'grace_p10': round(grace_p10, 0),
        'grace_p50': round(grace_p50, 0),
        'grace_p90': round(grace_p90, 0),
        'elapsed_s': round(elapsed, 1),
    }

    print(f"  {label:30s}: ret={total_ret:10.1f}%  DD={max_dd:7.2f}%  "
          f"Sh={perf.sharpe_ratio:5.2f}  Cal={perf.calmar_ratio:7.1f}  "
          f"trd={n_trades:4d}  win={perf.win_rate_pct:5.1f}%  "
          f"grace={avg_grace:4.1f} [{grace_p10:.0f}/{grace_p50:.0f}/{grace_p90:.0f}]  "
          f"{elapsed:.0f}s", flush=True)

    return result


# ── Summary printer ──

def print_summary(results, strategy_id, period_label=""):
    """Print a sorted results table."""
    if not results:
        return
    title = f"SUMMARY — {strategy_id} {period_label}(sorted by Calmar ratio)"
    print(f"\n{'='*110}")
    print(title)
    print(f"Note: MaxDD is DAILY (same basis as Calmar denominator)")
    print(f"{'='*110}")
    results_sorted = sorted(results, key=lambda x: x['calmar'], reverse=True)

    baseline_label = f"FIXED_{results[0]['base_nsb']}" if results else ""

    print(f"{'Label':30s} {'Return':>10s} {'MaxDD':>8s} {'Sharpe':>7s} {'Calmar':>8s} "
          f"{'Trades':>6s} {'Win%':>6s} {'AvgGr':>6s} {'[p10/p50/p90]':>14s}")
    print("-" * 110)
    for r in results_sorted:
        marker = " ★ BASELINE" if r['label'] == baseline_label else ""
        print(f"{r['label']:30s} {r['total_return_pct']:9.1f}% {r['max_dd_pct']:7.2f}% "
              f"{r['sharpe']:7.2f} {r['calmar']:8.1f} {r['trades']:6d} {r['win_rate']:5.1f}% "
              f"{r['avg_grace']:6.1f} [{r['grace_p10']:.0f}/{r['grace_p50']:.0f}/{r['grace_p90']:.0f}]"
              f"{marker}")


def run_sweep(strategy_id, signals, spec, label_prefix=""):
    """Run the full policy sweep on precomputed signals. Returns list of results."""
    results = []

    # ── 1. Fixed baselines ──
    print(f"── {label_prefix}Fixed Baselines ──", flush=True)
    for nsb in [48, 36, 24, 12]:
        r = run_sim(f"FIXED_{nsb}", grace_fixed, nsb, strategy_id, signals, spec)
        if r: results.append(r)

    # ── 2. Single-factor adaptive (base=48) ──
    print(f"\n── {label_prefix}Single-Factor Adaptive (base=48) ──", flush=True)
    for name, fn in [
        ("VOL_ADAPT_48",    grace_vol),
        ("TIER_ADAPT_48",   grace_tier),
        ("REGIME_ADAPT_48", grace_regime),
        ("PUMP_ADAPT_48",   grace_pump),
    ]:
        r = run_sim(name, fn, 48, strategy_id, signals, spec)
        if r: results.append(r)

    # ── 3. Composite at different bases ──
    print(f"\n── {label_prefix}Composite Adaptive ──", flush=True)
    for base in [24, 36, 48, 60]:
        r = run_sim(f"COMPOSITE_{base}", grace_composite, base, strategy_id, signals, spec)
        if r: results.append(r)

    # Reset
    global ACTIVE_POLICY
    ACTIVE_POLICY = None
    return results


# ── Main ──

def main():
    print(f"\n{'='*110}")
    print(f"ADAPTIVE GRACE PERIOD SWEEP — {STRATEGY_ID}")
    print(f"Config: $200K, max_positions=15, concentration=1.0, adv_cap=5%, seed=42")
    print(f"Matches ranking pipeline (rank_all_portfolios.py)")
    print(f"{'='*110}\n", flush=True)

    spec = StrategySpec(
        strategy_id=STRATEGY_ID, weight=1.0, max_positions=15,
        market="perp", pump_filter_funding_zscore=3.0,
        adv_sizing_enabled=True, adv_sizing_base=75_000_000,
    )
    tokens = discover_tokens("perp")
    end_date = infer_data_end_date("perp")

    # ── Signal loading config (matches ranking exactly) ──
    sig_config = PortfolioConfig(
        capital=CAPITAL,
        max_portfolio_positions=15,
        concentration_limit=1.0,
        adv_cap_pct=0.05,
        seed=42,
        max_sizing_equity=2_000_000, stress_adv_multiplier=0.5,
        impact_coeff=0.01,
    )

    # ═══════════════════════════════════════════════════════════════
    #  FULL PERIOD (in-sample)
    # ═══════════════════════════════════════════════════════════════
    print(f"{'─'*110}")
    print(f"  IN-SAMPLE: {MONTHS} months ending {end_date.date()}")
    print(f"{'─'*110}\n", flush=True)

    print("Loading IS signals...", flush=True)
    t0 = time.time()
    signals_is = precompute_strategy_signals(spec, tokens, sig_config, months=MONTHS, end_date=end_date)
    print(f"Signals loaded in {time.time()-t0:.1f}s — {len(signals_is)} tokens\n", flush=True)

    results_is = run_sweep(STRATEGY_ID, signals_is, spec, label_prefix="IS: ")
    print_summary(results_is, STRATEGY_ID, f"IN-SAMPLE ({MONTHS}mo) ")

    # ═══════════════════════════════════════════════════════════════
    #  OUT-OF-SAMPLE (last 3 months)
    # ═══════════════════════════════════════════════════════════════
    OOS_MONTHS = 3
    print(f"\n\n{'─'*110}")
    print(f"  OUT-OF-SAMPLE: last {OOS_MONTHS} months ending {end_date.date()}")
    print(f"{'─'*110}\n", flush=True)

    print("Loading OOS signals...", flush=True)
    t0 = time.time()
    signals_oos = precompute_strategy_signals(spec, tokens, sig_config, months=OOS_MONTHS, end_date=end_date)
    print(f"Signals loaded in {time.time()-t0:.1f}s — {len(signals_oos)} tokens\n", flush=True)

    results_oos = run_sweep(STRATEGY_ID, signals_oos, spec, label_prefix="OOS: ")
    print_summary(results_oos, STRATEGY_ID, f"OUT-OF-SAMPLE ({OOS_MONTHS}mo) ")

    # ═══════════════════════════════════════════════════════════════
    #  COMPARISON: IS vs OOS for top policies
    # ═══════════════════════════════════════════════════════════════
    print(f"\n\n{'='*110}")
    print(f"IS vs OOS COMPARISON — {STRATEGY_ID}")
    print(f"{'='*110}")
    print(f"{'Label':30s} {'IS Calmar':>10s} {'OOS Calmar':>11s} {'IS Sharpe':>10s} {'OOS Sharpe':>11s} "
          f"{'IS DD':>8s} {'OOS DD':>8s} {'Degradation':>12s}")
    print("-" * 110)

    is_map = {r['label']: r for r in results_is}
    oos_map = {r['label']: r for r in results_oos}

    for r_is in sorted(results_is, key=lambda x: x['calmar'], reverse=True):
        label = r_is['label']
        r_oos = oos_map.get(label)
        if r_oos is None:
            continue
        is_cal = r_is['calmar']
        oos_cal = r_oos['calmar']
        degradation = ((oos_cal / is_cal) - 1) * 100 if is_cal > 0.01 else 0
        print(f"{label:30s} {is_cal:10.1f} {oos_cal:11.1f} {r_is['sharpe']:10.2f} {r_oos['sharpe']:11.2f} "
              f"{r_is['max_dd_pct']:7.2f}% {r_oos['max_dd_pct']:7.2f}% {degradation:+10.0f}%")

    # Save all results
    out = {
        'in_sample': results_is,
        'out_of_sample': results_oos,
        'config': {
            'strategy': STRATEGY_ID,
            'is_months': MONTHS,
            'oos_months': OOS_MONTHS,
            'capital': CAPITAL,
            'end_date': str(end_date),
        },
    }
    out_path = f'/workspace/crypto_backtest/results/v4/adaptive_grace_{STRATEGY_ID}.json'
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=2)
    print(f"\nResults saved to {out_path}")


if __name__ == '__main__':
    main()
