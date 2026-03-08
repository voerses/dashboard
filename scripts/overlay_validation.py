#!/usr/bin/env python3
"""
Overlay Validation Backtest — Post-hoc trade scaling analysis.

For each overlay deployed to the paper trader, computes what the impact
would have been on s30 and s32 strategies by scaling per-trade P&L.

Overlays:
  O2 (Regime sizing):  Scale by regime weight (CRISIS=0, DOWNTREND=0.5, QUIET=0.75, RANGE=1.0, UPTREND=1.0)
  O3 (Weekend):        Scale by 0.5 if entry was Fri 20:00–Sun 20:00 UTC
  O6 (Funding carry):  s30 only — scale by funding rate magnitude (0.5–1.5x)
  S1 (Vol leverage):   s32 only — scale by min(0.30/vol, 2.0) where vol = 20-day BTC realized vol

Approach: mirrors v3/regime_analysis.py's post-hoc trade scaling pattern.
"""

import sys
import os
import time
import numpy as np
import pandas as pd
import importlib.util
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple
from datetime import datetime

# ---------------------------------------------------------------------------
# Module loading (same pattern as v3/regime_analysis.py)
# ---------------------------------------------------------------------------
_v3_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'v3')


def _load_v3(name):
    full_name = f'v3_{name}'
    if full_name in sys.modules:
        return sys.modules[full_name]
    spec = importlib.util.spec_from_file_location(full_name, os.path.join(_v3_dir, f'{name}.py'))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = mod
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Need v3 on sys.path for strategy imports
if _v3_dir not in sys.path:
    sys.path.insert(0, _v3_dir)

_engine_mod = _load_v3('engine')
_metrics_mod = _load_v3('metrics')
_universe_mod = _load_v3('universe')

Engine = _engine_mod.Engine
StrategyContext = _engine_mod.StrategyContext
StrategyResult = _engine_mod.StrategyResult
MarketType = _engine_mod.MarketType
CRISIS = _engine_mod.CRISIS
QUIET = _engine_mod.QUIET
UPTREND = _engine_mod.UPTREND
RANGE = _engine_mod.RANGE
DOWNTREND = _engine_mod.DOWNTREND

PerformanceMetrics = _metrics_mod.PerformanceMetrics
compute_metrics = _metrics_mod.compute_metrics
build_equity_curve = _metrics_mod.build_equity_curve
load_benchmark_returns = _metrics_mod.load_benchmark_returns

REGIME_NAMES = {0: 'CRISIS', 1: 'QUIET', 2: 'UPTREND', 3: 'RANGE', 4: 'DOWNTREND'}

# ---------------------------------------------------------------------------
# Overlay weights
# ---------------------------------------------------------------------------
O2_REGIME_WEIGHTS = {
    CRISIS: 0.0,
    DOWNTREND: 0.5,
    QUIET: 0.75,
    RANGE: 1.0,
    UPTREND: 1.0,
}


def is_weekend_entry(ts: pd.Timestamp) -> bool:
    """Check if timestamp falls in Fri 20:00 – Sun 20:00 UTC."""
    dow = ts.dayofweek  # Mon=0, Sun=6
    hour = ts.hour
    if dow == 4 and hour >= 20:  # Friday 20:00+
        return True
    if dow == 5:  # Saturday (all day)
        return True
    if dow == 6 and hour < 20:  # Sunday before 20:00
        return True
    return False


# ---------------------------------------------------------------------------
# Trade extraction for combined strategies (following regime_analysis.py pattern)
# ---------------------------------------------------------------------------

def extract_combined_trades(strategy_path: str, ticker: str, data_dir: str = 'data',
                            capital: float = 200_000) -> Optional[List[Dict]]:
    """Extract OOS trades for a combined (spot+perp) strategy on one token.

    Applies walk-forward OOS masking (same as validation.py).
    Returns list of trade dicts with entry_time, regime, funding_cost, etc.
    """
    # Load strategy
    spec = importlib.util.spec_from_file_location('strat', strategy_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    strategy_fn = mod.strategy

    # Load data
    spot_path = os.path.join(data_dir, 'spot', f'1h_cache/{ticker}_1h.parquet')
    perp_path = os.path.join(data_dir, 'perp', f'1h_cache/{ticker}_1h.parquet')
    if not os.path.exists(spot_path) or not os.path.exists(perp_path):
        return None

    df_spot = pd.read_parquet(spot_path)
    df_perp = pd.read_parquet(perp_path)
    if len(df_spot) < 2000 or len(df_perp) < 2000:
        return None

    common_start = max(df_spot.index.min(), df_perp.index.min())
    common_end = min(df_spot.index.max(), df_perp.index.max())
    df_spot = df_spot.loc[common_start:common_end]
    df_perp = df_perp.loc[common_start:common_end]
    if len(df_spot) < 2000 or len(df_perp) < 2000:
        return None

    engine_spot = Engine(data_dir=data_dir, market='spot', capital=capital)
    engine_perp = Engine(data_dir=data_dir, market='perp', capital=capital)
    ctx_spot = engine_spot._build_context(ticker, df_spot)
    ctx_perp = engine_perp._build_context(ticker, df_perp)
    if ctx_spot is None or ctx_perp is None:
        return None

    result = strategy_fn(ctx_spot, ctx_perp)
    n = min(len(ctx_spot.ind_1h['close']), len(ctx_perp.ind_1h['close']))

    # Walk-forward OOS masking
    train_bars = 365 * 24
    recal_bars = 90 * 24
    purge_bars = 5 * 24

    if n <= train_bars + purge_bars:
        return None

    # Mask primary
    masked_entry = result.entry_mask.copy()
    masked_entry[:train_bars] = False
    recal_point = train_bars
    while recal_point < n:
        purge_end = min(recal_point + purge_bars, n)
        masked_entry[recal_point:purge_end] = False
        recal_point += recal_bars
    result.entry_mask = masked_entry

    # Mask secondary
    if result.secondary_entry_mask is not None:
        masked_secondary = result.secondary_entry_mask.copy()
        masked_secondary[:train_bars] = False
        recal_point = train_bars
        while recal_point < n:
            purge_end = min(recal_point + purge_bars, n)
            masked_secondary[recal_point:purge_end] = False
            recal_point += recal_bars
        result.secondary_entry_mask = masked_secondary

    trades, final_equity = engine_perp._simulate_combined(ctx_spot, ctx_perp, result)

    if not trades:
        return None

    idx = ctx_spot.idx_1h
    for t in trades:
        eb = t['entry_bar']
        xb = t['exit_bar']
        t['token'] = ticker
        t['entry_time'] = idx[min(eb, len(idx) - 1)]
        t['exit_time'] = idx[min(xb, len(idx) - 1)]
        t['regime_at_entry'] = int(ctx_spot.regime_1h[min(eb, len(ctx_spot.regime_1h) - 1)])

    return trades


# ---------------------------------------------------------------------------
# BTC realized vol computation (for S1 overlay)
# ---------------------------------------------------------------------------

def compute_btc_realized_vol(data_dir: str = 'data') -> pd.Series:
    """Compute 20-day BTC realized vol from spot 1h bars.

    Returns hourly series of annualized realized vol, forward-filled from daily computation.
    """
    btc_path = os.path.join(data_dir, 'spot', '1h_cache', 'BTC_1h.parquet')
    df = pd.read_parquet(btc_path)
    # Daily close
    daily = df['close'].resample('1D').last().dropna()
    log_ret = np.log(daily / daily.shift(1)).dropna()
    # 20-day rolling std, annualized
    vol_20d = log_ret.rolling(20).std() * np.sqrt(365)
    # Reindex to hourly (forward-fill)
    vol_hourly = vol_20d.reindex(df.index, method='ffill')
    return vol_hourly


# ---------------------------------------------------------------------------
# Overlay application
# ---------------------------------------------------------------------------

def apply_overlays(trades: List[Dict], overlays: List[str],
                   btc_vol_series: Optional[pd.Series] = None,
                   funding_data: Optional[pd.Series] = None) -> List[Dict]:
    """Apply overlay scaling to trades (post-hoc P&L adjustment).

    Returns new list of scaled trade dicts (does not modify originals).
    """
    scaled = []
    for t in trades:
        scale = 1.0
        entry_time = t.get('entry_time')

        for ov in overlays:
            if ov == 'O2':
                regime = t.get('regime_at_entry', RANGE)
                scale *= O2_REGIME_WEIGHTS.get(regime, 1.0)

            elif ov == 'O3':
                if entry_time is not None and is_weekend_entry(pd.Timestamp(entry_time)):
                    scale *= 0.5

            elif ov == 'O6':
                # Funding carry: scale by funding rate magnitude
                # Positive funding -> short gets paid -> scale up for short trades
                # We use a simple model: map funding_cost per trade to a scale factor
                fc = t.get('funding_cost', 0)
                pos = t.get('position_usd', 1)
                hold = t.get('hold_hours', 1)
                if pos > 0 and hold > 0:
                    # Annualized funding rate from this trade
                    funding_rate_per_hour = fc / max(pos, 1)
                    # Map to scale: high magnitude funding = more attractive carry
                    # Typical funding: 0.01%/8h = 0.00000125/h
                    # If funding is favorable (positive for shorts), scale up
                    annual_funding = abs(funding_rate_per_hour) * 8760
                    # Map annual funding 0-10% to scale 0.5-1.5
                    scale *= np.clip(0.5 + annual_funding * 10.0, 0.5, 1.5)
                else:
                    scale *= 0.75  # default conservative

            elif ov == 'S1':
                # Vol leverage: scale by min(0.30 / vol, 2.0)
                if btc_vol_series is not None and entry_time is not None:
                    ts = pd.Timestamp(entry_time)
                    # Find nearest vol value
                    if ts in btc_vol_series.index:
                        vol = btc_vol_series.loc[ts]
                    else:
                        # Find closest before entry
                        mask = btc_vol_series.index <= ts
                        if mask.any():
                            vol = btc_vol_series.loc[mask].iloc[-1]
                        else:
                            vol = 0.5  # conservative default
                    if np.isnan(vol) or vol < 0.01:
                        vol = 0.5
                    scale *= min(0.30 / vol, 2.0)

        if scale < 0.01:
            continue  # skip effectively zero-sized trades

        st = dict(t)
        st['pnl'] = t['pnl'] * scale
        st['position_usd'] = t['position_usd'] * scale
        st['return_pct'] = t.get('return_pct', 0)  # return_pct stays the same (it's per-unit)
        st['overlay_scale'] = scale
        scaled.append(st)

    return scaled


# ---------------------------------------------------------------------------
# Metrics computation from trades
# ---------------------------------------------------------------------------

def compute_trade_metrics(trades: List[Dict], capital: float = 200_000) -> Dict:
    """Compute performance metrics from a trade list using daily equity curve from timestamps."""
    if not trades:
        return {
            'total_return_pct': 0, 'annualized_return_pct': 0,
            'sharpe': 0, 'sortino': 0, 'calmar': 0,
            'max_dd_pct': 0, 'win_rate_pct': 0, 'profit_factor': 0,
            'n_trades': 0, 'total_pnl': 0,
        }

    # Build daily equity curve from trade timestamps (more accurate than bar-based)
    sorted_trades = sorted(trades, key=lambda t: t.get('exit_time', pd.Timestamp.min))

    # Create daily PnL series
    daily_pnl = {}
    for t in sorted_trades:
        exit_time = t.get('exit_time')
        if exit_time is not None:
            day = pd.Timestamp(exit_time).normalize()
            daily_pnl[day] = daily_pnl.get(day, 0) + t['pnl']

    if not daily_pnl:
        return {
            'total_return_pct': 0, 'annualized_return_pct': 0,
            'sharpe': 0, 'sortino': 0, 'calmar': 0,
            'max_dd_pct': 0, 'win_rate_pct': 0, 'profit_factor': 0,
            'n_trades': len(trades), 'total_pnl': 0,
        }

    # Build continuous daily equity curve
    first_entry = min(pd.Timestamp(t.get('entry_time', pd.Timestamp.max))
                      for t in trades if t.get('entry_time') is not None)
    last_exit = max(pd.Timestamp(t.get('exit_time', pd.Timestamp.min))
                    for t in trades if t.get('exit_time') is not None)
    date_range = pd.date_range(first_entry.normalize(), last_exit.normalize(), freq='D')
    pnl_series = pd.Series(0.0, index=date_range)
    for day, pnl in daily_pnl.items():
        if day in pnl_series.index:
            pnl_series.loc[day] = pnl

    equity = capital + pnl_series.cumsum()

    # Compute metrics from equity curve
    m = PerformanceMetrics()
    n_days = len(equity)
    total_return = (equity.iloc[-1] / equity.iloc[0]) - 1.0
    m.total_return_pct = total_return * 100
    years = max(n_days / 365.0, 0.01)
    m.annualized_return_pct = ((1 + total_return) ** (1.0 / years) - 1) * 100

    daily_returns = equity.pct_change().dropna().replace([np.inf, -np.inf], 0.0).fillna(0.0)
    if len(daily_returns) >= 2:
        mean_daily = daily_returns.mean()
        std_daily = daily_returns.std()
        if std_daily > 1e-10:
            m.sharpe_ratio = (mean_daily / std_daily) * np.sqrt(365)
        neg_rets = daily_returns[daily_returns < 0]
        if len(neg_rets) > 0:
            downside_std = np.sqrt(np.mean(neg_rets ** 2))
            if downside_std > 1e-10:
                m.sortino_ratio = (mean_daily / downside_std) * np.sqrt(365)

    cummax = equity.cummax()
    drawdown = (equity - cummax) / cummax
    m.max_drawdown_pct = float(drawdown.min()) * 100
    if abs(m.max_drawdown_pct) > 0.01:
        m.calmar_ratio = m.annualized_return_pct / abs(m.max_drawdown_pct)

    # Trade quality
    m.total_trades = len(trades)
    if trades:
        wins = [t for t in trades if t['pnl'] > 0]
        losers = [t for t in trades if t['pnl'] <= 0]
        m.win_rate_pct = len(wins) / len(trades) * 100
        gross_profit = sum(t['pnl'] for t in wins)
        gross_loss = abs(sum(t['pnl'] for t in losers))
        m.profit_factor = gross_profit / max(gross_loss, 1)

    total_pnl = float(equity.iloc[-1] - capital)

    return {
        'total_return_pct': m.total_return_pct,
        'annualized_return_pct': m.annualized_return_pct,
        'sharpe': m.sharpe_ratio,
        'sortino': m.sortino_ratio,
        'calmar': m.calmar_ratio,
        'max_dd_pct': m.max_drawdown_pct,
        'win_rate_pct': m.win_rate_pct,
        'profit_factor': m.profit_factor,
        'n_trades': m.total_trades,
        'total_pnl': total_pnl,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_overlay_validation():
    t0 = time.time()

    data_dir = 'data'
    capital = 200_000
    tokens = ['BTC', 'ETH', 'SOL', 'ARB', 'DYDX']

    strat_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'strategies')
    s30_path = os.path.join(strat_dir, 's30_basis_carry.py')
    s32_path = os.path.join(strat_dir, 's32_regime_spot_perp.py')

    print("=" * 100)
    print("OVERLAY VALIDATION BACKTEST — s30 & s32 strategies")
    print("=" * 100)
    print(f"  Tokens: {', '.join(tokens)}")
    print(f"  Capital: ${capital:,.0f}")
    print(f"  Overlays: O2 (Regime sizing), O3 (Weekend), O6 (Funding carry), S1 (Vol leverage)")
    print()

    # Pre-compute BTC realized vol for S1 overlay
    print("Computing BTC 20-day realized vol for S1 overlay...")
    btc_vol = compute_btc_realized_vol(data_dir)
    vol_mean = btc_vol.dropna().mean()
    vol_std = btc_vol.dropna().std()
    print(f"  BTC vol: mean={vol_mean:.3f}, std={vol_std:.3f}")
    print()

    # ---------------------------------------------------------------------------
    # Extract trades
    # ---------------------------------------------------------------------------
    strategies = {
        's30': {'path': s30_path, 'overlays': ['O2', 'O3', 'O6', 'combined_s30']},
        's32': {'path': s32_path, 'overlays': ['O2', 'O3', 'S1', 'combined_s32']},
    }

    all_results = {}

    for strat_name, strat_info in strategies.items():
        strat_path = strat_info['path']
        print(f"\n{'='*100}")
        print(f"STRATEGY: {strat_name}")
        print(f"{'='*100}")

        # Extract trades across all tokens
        all_trades = []

        for tk in tokens:
            print(f"  Extracting trades for {tk}...", end=' ', flush=True)
            trades = extract_combined_trades(strat_path, tk, data_dir, capital)
            if trades:
                all_trades.extend(trades)
                print(f"{len(trades)} trades")
            else:
                print("no trades")

        if not all_trades:
            print(f"  No trades found for {strat_name}. Skipping.")
            continue

        # Sort by entry_time
        all_trades.sort(key=lambda t: t.get('entry_time', pd.Timestamp.min))

        print(f"\n  Total trades: {len(all_trades)}")
        print(f"  Date range: {all_trades[0].get('entry_time', '?')} to {all_trades[-1].get('entry_time', '?')}")

        # Regime distribution of trades
        regime_dist = {}
        for t in all_trades:
            r = t.get('regime_at_entry', RANGE)
            rn = REGIME_NAMES.get(r, 'RANGE')
            regime_dist[rn] = regime_dist.get(rn, 0) + 1
        print(f"  Regime distribution:")
        for rn in ['UPTREND', 'RANGE', 'QUIET', 'DOWNTREND', 'CRISIS']:
            c = regime_dist.get(rn, 0)
            pct = c / len(all_trades) * 100 if all_trades else 0
            print(f"    {rn:<12} {c:>5} ({pct:.1f}%)")

        # Weekend trades
        weekend_count = sum(1 for t in all_trades
                           if t.get('entry_time') is not None
                           and is_weekend_entry(pd.Timestamp(t['entry_time'])))
        print(f"  Weekend entries: {weekend_count} ({weekend_count/len(all_trades)*100:.1f}%)")

        # ---------------------------------------------------------------------------
        # Compute baseline and overlay metrics
        # ---------------------------------------------------------------------------
        scenarios = {'Baseline': all_trades}

        # O2: Regime sizing
        scenarios['O2 Regime'] = apply_overlays(all_trades, ['O2'])

        # O3: Weekend
        scenarios['O3 Weekend'] = apply_overlays(all_trades, ['O3'])

        if strat_name == 's30':
            # O6: Funding carry (s30 only)
            scenarios['O6 Funding'] = apply_overlays(all_trades, ['O6'])
            # Combined: O2 + O3 + O6
            scenarios['ALL Combined'] = apply_overlays(all_trades, ['O2', 'O3', 'O6'])
        elif strat_name == 's32':
            # S1: Vol leverage (s32 only)
            scenarios['S1 VolLev'] = apply_overlays(all_trades, ['S1'], btc_vol_series=btc_vol)
            # Combined: O2 + O3 + S1
            scenarios['ALL Combined'] = apply_overlays(all_trades, ['O2', 'O3', 'S1'], btc_vol_series=btc_vol)

        results = {}
        for scenario_name, scenario_trades in scenarios.items():
            m = compute_trade_metrics(scenario_trades, capital)
            results[scenario_name] = m

        all_results[strat_name] = results

        # ---------------------------------------------------------------------------
        # Print comparison table
        # ---------------------------------------------------------------------------
        print(f"\n  {'─'*90}")
        print(f"  OVERLAY IMPACT — {strat_name}")
        print(f"  {'─'*90}")

        headers = list(results.keys())
        metrics_to_show = [
            ('Ann. Return %', 'annualized_return_pct', '+.1f'),
            ('Sharpe', 'sharpe', '+.2f'),
            ('Sortino', 'sortino', '+.2f'),
            ('Calmar', 'calmar', '+.2f'),
            ('Max DD %', 'max_dd_pct', '.1f'),
            ('Win Rate %', 'win_rate_pct', '.1f'),
            ('Profit Factor', 'profit_factor', '.2f'),
            ('Trades', 'n_trades', 'd'),
            ('Total PnL $', 'total_pnl', ',.0f'),
        ]

        # Header row
        col_w = 14
        hdr = f"  {'Metric':<18}"
        for h in headers:
            hdr += f"  {h:>{col_w}}"
        # Delta column (vs baseline)
        hdr += f"  {'Best Delta':>{col_w}}"
        print(hdr)
        print(f"  {'─'*(18 + (col_w+2)*len(headers) + col_w + 2)}")

        baseline = results['Baseline']
        def _fmt(v, fmt):
            if fmt == 'd':
                return f"{int(v):>d}"
            elif fmt == ',.0f':
                return f"{v:>,.0f}"
            elif fmt == '+.1f':
                return f"{v:>+.1f}"
            elif fmt == '+.2f':
                return f"{v:>+.2f}"
            elif fmt == '.1f':
                return f"{v:>.1f}"
            elif fmt == '.2f':
                return f"{v:>.2f}"
            else:
                return f"{v}"

        for metric_name, metric_key, fmt in metrics_to_show:
            line = f"  {metric_name:<18}"
            vals = []
            for h in headers:
                v = results[h].get(metric_key, 0)
                vals.append(v)
                s = _fmt(v, fmt)
                line += f"  {s:>{col_w}}"

            # Best delta (excluding baseline)
            bv = baseline.get(metric_key, 0)
            if len(vals) > 1:
                if metric_key == 'max_dd_pct':
                    best_overlay = max(vals[1:])
                    delta = best_overlay - bv
                else:
                    best_overlay = max(vals[1:])
                    delta = best_overlay - bv
                s = _fmt(delta, fmt)
                line += f"  {s:>{col_w}}"
            print(line)

        print()

    # ---------------------------------------------------------------------------
    # Cross-strategy summary
    # ---------------------------------------------------------------------------
    if len(all_results) == 2:
        print(f"\n{'='*100}")
        print(f"CROSS-STRATEGY OVERLAY SUMMARY")
        print(f"{'='*100}")
        print()
        print(f"  {'Scenario':<20} {'Strategy':<8} {'AnnRet%':>10} {'Sharpe':>10} {'Calmar':>10} {'MaxDD%':>10} {'PF':>10}")
        print(f"  {'─'*80}")

        for strat_name, results in all_results.items():
            for scenario, m in results.items():
                print(f"  {scenario:<20} {strat_name:<8} {m['annualized_return_pct']:>+10.1f} "
                      f"{m['sharpe']:>+10.2f} {m['calmar']:>+10.2f} "
                      f"{m['max_dd_pct']:>10.1f} {m['profit_factor']:>10.2f}")
            print()

        # Improvement analysis
        print(f"\n  OVERLAY IMPROVEMENT ANALYSIS:")
        print(f"  {'─'*70}")
        for strat_name, results in all_results.items():
            baseline = results['Baseline']
            combined = results.get('ALL Combined', baseline)
            sharpe_delta = combined['sharpe'] - baseline['sharpe']
            calmar_delta = combined['calmar'] - baseline['calmar']
            dd_delta = combined['max_dd_pct'] - baseline['max_dd_pct']
            ret_delta = combined['annualized_return_pct'] - baseline['annualized_return_pct']

            print(f"  {strat_name} (ALL Combined vs Baseline):")
            print(f"    Ann Return:  {ret_delta:+.1f}% ({baseline['annualized_return_pct']:+.1f}% -> {combined['annualized_return_pct']:+.1f}%)")
            print(f"    Sharpe:      {sharpe_delta:+.2f} ({baseline['sharpe']:+.2f} -> {combined['sharpe']:+.2f})")
            print(f"    Calmar:      {calmar_delta:+.2f} ({baseline['calmar']:+.2f} -> {combined['calmar']:+.2f})")
            print(f"    Max DD:      {dd_delta:+.1f}% ({baseline['max_dd_pct']:.1f}% -> {combined['max_dd_pct']:.1f}%)")
            print()

    elapsed = time.time() - t0
    print(f"  Total time: {elapsed:.1f}s")


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    run_overlay_validation()
