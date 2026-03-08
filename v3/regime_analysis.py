"""
V3 Regime-Conditional Strategy Weighting
==========================================

Analyzes per-strategy performance by market regime and simulates
regime-weighted portfolio allocation.

Uses BTC daily bars as the global regime signal:
    CRISIS=0, QUIET=1, UPTREND=2, RANGE=3, DOWNTREND=4

Two modes:
  --analyze:  Empirical heatmap of strategy performance per regime
  --simulate: Regime-weighted portfolio vs equal-weight baseline

Usage:
    python v3/regime_analysis.py --strategies s11 s09 --analyze --workers 4
    python v3/regime_analysis.py --strategies s11 s09 --simulate --workers 4
    python v3/regime_analysis.py --strategies s11 s09 --analyze --simulate --workers 4
"""

import sys
import os
import time
import json
import argparse
import numpy as np
import pandas as pd
import importlib.util
from dataclasses import dataclass, asdict, field
from typing import Optional, List, Dict, Tuple
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

# ---------------------------------------------------------------------------
# Import V3 modules
# ---------------------------------------------------------------------------
_v3_dir = os.path.dirname(os.path.abspath(__file__))


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


_engine_mod = _load_v3('engine')
_universe_mod = _load_v3('universe')
_portfolio_mod = _load_v3('portfolio')

resolve_universe = _universe_mod.resolve_universe
get_fee_rate = _universe_mod.get_fee_rate
aggregate_to_timeframe = _engine_mod.aggregate_to_timeframe
compute_indicators_fast = _engine_mod.compute_indicators_fast
detect_daily_regime = _engine_mod.detect_daily_regime
compute_portfolio_metrics = _portfolio_mod.compute_portfolio_metrics
simulate_portfolio = _portfolio_mod.simulate_portfolio
extract_token_trades = _portfolio_mod.extract_token_trades
PortfolioConfig = _portfolio_mod.PortfolioConfig
PortfolioMetrics = _portfolio_mod.PortfolioMetrics

CRISIS, QUIET, UPTREND, RANGE, DOWNTREND = 0, 1, 2, 3, 4
REGIME_NAMES = {0: 'CRISIS', 1: 'QUIET', 2: 'UPTREND', 3: 'RANGE', 4: 'DOWNTREND'}


# ---------------------------------------------------------------------------
# Strategy Resolution
# ---------------------------------------------------------------------------

def resolve_strategy_path(name: str) -> Optional[str]:
    """Resolve strategy name to absolute file path."""
    import glob as _glob
    strat_dir = os.path.join(os.path.dirname(_v3_dir), 'strategies')
    candidates = [
        os.path.join(strat_dir, f'{name}.py'),
        os.path.join(strat_dir, f'{name}_*.py'),
    ]
    for c in candidates:
        matches = _glob.glob(c)
        if matches:
            return os.path.abspath(matches[0])
    return None


# ---------------------------------------------------------------------------
# Global Regime Detection (BTC-based)
# ---------------------------------------------------------------------------

def compute_global_regime(data_dir: str = 'data', market: str = 'spot') -> pd.Series:
    """Compute global market regime from BTC daily bars.

    Returns pd.Series with DatetimeIndex (daily) and regime values [0-4].
    """
    btc_path = os.path.join(data_dir, market, '1h_cache', 'BTC_1h.parquet')
    df_1h = pd.read_parquet(btc_path)
    df_daily = aggregate_to_timeframe(df_1h, hours=24)

    close = df_daily['close'].values.astype(np.float64)
    high = df_daily['high'].values.astype(np.float64)
    low = df_daily['low'].values.astype(np.float64)
    volume = df_daily['volume'].values.astype(np.float64)

    ind_d = compute_indicators_fast(close, high, low, volume)
    regimes = detect_daily_regime(ind_d)

    return pd.Series(regimes, index=df_daily.index, name='regime')


# ---------------------------------------------------------------------------
# Phase 1: Empirical Regime Performance Analysis
# ---------------------------------------------------------------------------

def tag_trades_with_regime(trades: List[Dict], regime_series: pd.Series) -> List[Dict]:
    """Tag each trade with the global regime at entry time."""
    # Build a fast lookup: forward-fill regime to cover all dates
    regime_daily = regime_series.reindex(
        pd.date_range(regime_series.index[0], regime_series.index[-1], freq='D')
    ).ffill().fillna(RANGE)

    for t in trades:
        entry_time = t.get('entry_time')
        if entry_time is not None:
            # Find the regime for the entry date
            entry_date = pd.Timestamp(entry_time).normalize()
            if entry_date in regime_daily.index:
                t['regime_at_entry'] = int(regime_daily.loc[entry_date])
            elif entry_date >= regime_daily.index[-1]:
                t['regime_at_entry'] = int(regime_daily.iloc[-1])
            else:
                t['regime_at_entry'] = RANGE
        else:
            t['regime_at_entry'] = RANGE

    return trades


def analyze_regime_performance(
    strategy_names: List[str],
    tokens: List[str],
    config: PortfolioConfig,
    regime_series: pd.Series,
    verbose: bool = True,
) -> Dict:
    """Compute per-strategy, per-regime performance metrics.

    Returns dict with heatmap data.
    """
    results = {}

    for strat_name in strategy_names:
        strat_path = resolve_strategy_path(strat_name)
        if strat_path is None:
            print(f"  [SKIP] Strategy not found: {strat_name}")
            continue

        if verbose:
            print(f"\n  Extracting trades for {strat_name}...")

        token_trades = extract_token_trades(strat_path, tokens, config)
        all_trades = []
        for tk_trades in token_trades.values():
            all_trades.extend(tk_trades)

        if not all_trades:
            print(f"  [SKIP] {strat_name}: no trades")
            continue

        # Tag with regime
        tagged = tag_trades_with_regime(all_trades, regime_series)

        if verbose:
            print(f"  {strat_name}: {len(tagged)} trades tagged")

        # Compute per-regime metrics
        regime_stats = {}
        for regime_id in range(5):
            regime_name = REGIME_NAMES[regime_id]
            regime_trades = [t for t in tagged if t.get('regime_at_entry') == regime_id]
            n = len(regime_trades)

            if n == 0:
                regime_stats[regime_name] = {
                    'n_trades': 0, 'win_rate': 0, 'avg_pnl': 0,
                    'total_pnl': 0, 'profit_factor': 0, 'avg_return_pct': 0,
                }
                continue

            pnls = [t['pnl'] for t in regime_trades]
            rets = [t.get('return_pct', 0) for t in regime_trades]
            wins = sum(1 for p in pnls if p > 0)
            gross_profit = sum(p for p in pnls if p > 0)
            gross_loss = abs(sum(p for p in pnls if p <= 0))

            regime_stats[regime_name] = {
                'n_trades': n,
                'win_rate': wins / n * 100 if n > 0 else 0,
                'avg_pnl': np.mean(pnls),
                'total_pnl': sum(pnls),
                'profit_factor': gross_profit / max(gross_loss, 1),
                'avg_return_pct': np.mean(rets),
            }

        # Overall stats
        total_pnls = [t['pnl'] for t in tagged]
        total_wins = sum(1 for p in total_pnls if p > 0)
        regime_stats['ALL'] = {
            'n_trades': len(tagged),
            'win_rate': total_wins / len(tagged) * 100,
            'avg_pnl': np.mean(total_pnls),
            'total_pnl': sum(total_pnls),
            'profit_factor': (sum(p for p in total_pnls if p > 0) /
                              max(abs(sum(p for p in total_pnls if p <= 0)), 1)),
            'avg_return_pct': np.mean([t.get('return_pct', 0) for t in tagged]),
        }

        results[strat_name] = regime_stats

    return results


def print_regime_heatmap(results: Dict, verbose: bool = True):
    """Print formatted heatmap of strategy performance by regime."""
    if not results:
        return

    regimes_order = ['UPTREND', 'RANGE', 'QUIET', 'DOWNTREND', 'CRISIS', 'ALL']

    print(f"\n{'='*110}")
    print(f"REGIME PERFORMANCE HEATMAP — Per-Strategy, Per-Regime")
    print(f"{'='*110}")

    for strat_name, regime_stats in results.items():
        print(f"\n  Strategy: {strat_name}")
        print(f"  {'Regime':<12} {'Trades':>8} {'Win%':>8} {'AvgPnL$':>10} {'TotalPnL$':>12} "
              f"{'PF':>8} {'AvgRet%':>10}")
        print(f"  {'-'*70}")

        for regime in regimes_order:
            s = regime_stats.get(regime, {})
            n = s.get('n_trades', 0)
            if n == 0:
                print(f"  {regime:<12} {'0':>8} {'--':>8} {'--':>10} {'--':>12} {'--':>8} {'--':>10}")
            else:
                print(f"  {regime:<12} {n:>8} {s['win_rate']:>7.1f}% "
                      f"{s['avg_pnl']:>+10,.0f} {s['total_pnl']:>+12,.0f} "
                      f"{s['profit_factor']:>8.2f} {s['avg_return_pct']:>+9.2f}%")

    # Cross-strategy comparison per regime
    if len(results) > 1:
        print(f"\n{'='*110}")
        print(f"CROSS-STRATEGY COMPARISON BY REGIME (Profit Factor)")
        print(f"{'='*110}")
        strat_names = list(results.keys())
        col_w = max(12, max(len(s) for s in strat_names) + 2)
        header = f"  {'Regime':<12}" + "".join(f"{s:>{col_w}}" for s in strat_names)
        print(header)
        print(f"  {'-'*(12 + col_w * len(strat_names))}")
        for regime in regimes_order:
            vals = []
            for sn in strat_names:
                pf = results[sn].get(regime, {}).get('profit_factor', 0)
                n = results[sn].get(regime, {}).get('n_trades', 0)
                if n == 0:
                    vals.append("--")
                else:
                    vals.append(f"{pf:.2f}")
            print(f"  {regime:<12}" + "".join(f"{v:>{col_w}}" for v in vals))

    print()


# ---------------------------------------------------------------------------
# Phase 2: Derive Regime Weights from Empirical Data
# ---------------------------------------------------------------------------

@dataclass
class RegimeWeights:
    """Regime-conditional allocation weights."""
    weights: Dict[str, Dict[str, float]] = field(default_factory=dict)
    # {strategy_name: {regime_name: weight}}

    def get(self, strategy: str, regime: int) -> float:
        regime_name = REGIME_NAMES.get(regime, 'RANGE')
        return self.weights.get(strategy, {}).get(regime_name, 1.0)


def derive_weights_from_heatmap(
    heatmap: Dict,
    method: str = 'profit_factor',
) -> RegimeWeights:
    """Derive allocation weights from empirical regime performance.

    Method 'profit_factor': weight proportional to PF in each regime,
    normalized so best regime = 1.0. Regimes with PF < 1.0 get reduced weight.

    Method 'binary': full weight if PF >= 1.2, half weight if 1.0-1.2, zero if < 1.0.
    """
    weights = {}

    for strat_name, regime_stats in heatmap.items():
        strat_weights = {}

        if method == 'profit_factor':
            # Normalize PF: best regime = 1.0, scale others proportionally
            pfs = {}
            for regime in ['UPTREND', 'RANGE', 'QUIET', 'DOWNTREND', 'CRISIS']:
                s = regime_stats.get(regime, {})
                pf = s.get('profit_factor', 0)
                n = s.get('n_trades', 0)
                if n >= 10:  # need minimum sample
                    pfs[regime] = pf
                else:
                    pfs[regime] = 0.5  # conservative default for sparse regimes

            max_pf = max(pfs.values()) if pfs else 1.0
            for regime, pf in pfs.items():
                if pf <= 0.8:
                    strat_weights[regime] = 0.0  # don't trade if PF < 0.8
                elif pf <= 1.0:
                    strat_weights[regime] = 0.25  # minimal allocation
                elif pf <= 1.2:
                    strat_weights[regime] = 0.50  # reduced allocation
                else:
                    strat_weights[regime] = min(pf / max(max_pf, 1.0), 1.0)

        elif method == 'binary':
            for regime in ['UPTREND', 'RANGE', 'QUIET', 'DOWNTREND', 'CRISIS']:
                s = regime_stats.get(regime, {})
                pf = s.get('profit_factor', 0)
                n = s.get('n_trades', 0)
                if n < 10 or pf < 1.0:
                    strat_weights[regime] = 0.0
                elif pf < 1.2:
                    strat_weights[regime] = 0.50
                else:
                    strat_weights[regime] = 1.0

        weights[strat_name] = strat_weights

    return RegimeWeights(weights=weights)


def print_weights(rw: RegimeWeights):
    """Print regime weight table."""
    print(f"\n{'='*80}")
    print(f"DERIVED REGIME WEIGHTS")
    print(f"{'='*80}")

    strats = list(rw.weights.keys())
    regimes = ['UPTREND', 'RANGE', 'QUIET', 'DOWNTREND', 'CRISIS']
    col_w = max(12, max(len(s) for s in strats) + 2)

    header = f"  {'Regime':<12}" + "".join(f"{s:>{col_w}}" for s in strats)
    print(header)
    print(f"  {'-'*(12 + col_w * len(strats))}")
    for regime in regimes:
        vals = []
        for sn in strats:
            w = rw.weights.get(sn, {}).get(regime, 1.0)
            vals.append(f"{w:.2f}")
        print(f"  {regime:<12}" + "".join(f"{v:>{col_w}}" for v in vals))
    print()


# ---------------------------------------------------------------------------
# Phase 3: Regime-Weighted Portfolio Simulation
# ---------------------------------------------------------------------------

def _is_combined_strategy_from_path(strategy_path):
    """Detect combined strategy by checking for MarketType.COMBINED in source."""
    try:
        with open(strategy_path, 'r') as f:
            source = f.read()
        return 'MarketType.COMBINED' in source
    except (OSError, IOError):
        return False


def _get_token_trades_with_regime(args):
    """Extract trades for one token from one strategy, tagged with regime.

    Worker function for parallel execution. Supports combined strategies.
    """
    ticker, strategy_path, data_dir, market, capital, exchange = args

    v3_dir = os.path.dirname(os.path.abspath(__file__))
    if v3_dir not in sys.path:
        sys.path.insert(0, v3_dir)
    # Ensure project root is on path so wrapper strategies can import base strategies
    project_root = os.path.dirname(v3_dir)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    eng_mod = _load_v3('engine')
    EngineLocal = eng_mod.Engine

    # Load strategy
    spec = importlib.util.spec_from_file_location('strat', strategy_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    strategy_fn = mod.strategy

    is_combined = market == 'combined' or _is_combined_strategy_from_path(strategy_path)

    if is_combined:
        # Load both spot and perp data
        spot_path = os.path.join(data_dir, 'spot', f'1h_cache/{ticker}_1h.parquet')
        perp_path = os.path.join(data_dir, 'perp', f'1h_cache/{ticker}_1h.parquet')
        if not os.path.exists(spot_path) or not os.path.exists(perp_path):
            return ticker, None

        df_spot = pd.read_parquet(spot_path)
        df_perp = pd.read_parquet(perp_path)
        if len(df_spot) < 2000 or len(df_perp) < 2000:
            return ticker, None

        common_start = max(df_spot.index.min(), df_perp.index.min())
        common_end = min(df_spot.index.max(), df_perp.index.max())
        df_spot = df_spot.loc[common_start:common_end]
        df_perp = df_perp.loc[common_start:common_end]
        if len(df_spot) < 2000 or len(df_perp) < 2000:
            return ticker, None

        engine_spot = EngineLocal(data_dir=data_dir, market='spot', capital=capital, exchange=exchange)
        engine_perp = EngineLocal(data_dir=data_dir, market='perp', capital=capital, exchange=exchange)
        ctx_spot = engine_spot._build_context(ticker, df_spot)
        ctx_perp = engine_perp._build_context(ticker, df_perp)
        if ctx_spot is None or ctx_perp is None:
            return ticker, None

        result = strategy_fn(ctx_spot, ctx_perp)
        n = min(len(ctx_spot.ind_1h['close']), len(ctx_perp.ind_1h['close']))
        ctx_ref = ctx_spot  # reference context for regime/timestamps
    else:
        effective_market = market if market != 'combined' else 'spot'
        h1_path = os.path.join(data_dir, effective_market, f'1h_cache/{ticker}_1h.parquet')
        if not os.path.exists(h1_path):
            return ticker, None

        df_1h = pd.read_parquet(h1_path)
        if len(df_1h) < 2000:
            return ticker, None

        engine = EngineLocal(data_dir=data_dir, market=effective_market,
                             capital=capital, exchange=exchange)
        ctx = engine._build_context(ticker, df_1h)
        if ctx is None:
            return ticker, None

        result = strategy_fn(ctx)
        n = len(ctx.ind_1h['close'])
        ctx_ref = ctx

    # Walk-forward masking
    train_bars = 365 * 24
    recal_bars = 90 * 24
    purge_bars = 5 * 24

    if n <= train_bars + purge_bars:
        return ticker, None

    masked_entry = result.entry_mask.copy()
    masked_entry[:train_bars] = False
    recal_point = train_bars
    while recal_point < n:
        purge_end = min(recal_point + purge_bars, n)
        masked_entry[recal_point:purge_end] = False
        recal_point += recal_bars
    result.entry_mask = masked_entry

    # Mask secondary leg for combined
    if is_combined and result.secondary_entry_mask is not None:
        masked_secondary = result.secondary_entry_mask.copy()
        masked_secondary[:train_bars] = False
        recal_point = train_bars
        while recal_point < n:
            purge_end = min(recal_point + purge_bars, n)
            masked_secondary[recal_point:purge_end] = False
            recal_point += recal_bars
        result.secondary_entry_mask = masked_secondary

    if is_combined:
        trades, final_equity = engine_perp._simulate_combined(ctx_spot, ctx_perp, result)
    else:
        trades, final_equity = engine._simulate(ctx, result)

    if not trades:
        return ticker, None

    idx = ctx_ref.idx_1h
    for t in trades:
        eb = t['entry_bar']
        xb = t['exit_bar']
        t['token'] = ticker
        t['entry_time'] = idx[min(eb, len(idx) - 1)]
        t['exit_time'] = idx[min(xb, len(idx) - 1)]
        # Tag with per-token regime (from context)
        t['regime_at_entry'] = int(ctx_ref.regime_1h[min(eb, len(ctx_ref.regime_1h) - 1)])

    return ticker, trades


def simulate_regime_weighted(
    strategy_names: List[str],
    tokens: List[str],
    config: PortfolioConfig,
    regime_weights: RegimeWeights,
    regime_series: pd.Series,
    verbose: bool = True,
) -> Tuple[Optional[Dict], Optional[Dict]]:
    """Run portfolio sim twice: baseline (equal weight) and regime-weighted.

    Regime weighting is applied by scaling trade position_usd by the
    regime-conditional weight at entry time.

    Returns (baseline_result, weighted_result).
    """
    # Collect all trades from all strategies
    all_token_trades_base = {}  # for baseline
    all_token_trades_weighted = {}  # for regime-weighted

    for strat_name in strategy_names:
        strat_path = resolve_strategy_path(strat_name)
        if strat_path is None:
            continue

        if verbose:
            print(f"  Extracting trades for {strat_name}...")

        args_list = [
            (tk, strat_path, config.data_dir, config.market, config.capital, config.exchange)
            for tk in tokens
        ]

        strat_trades = {}
        if config.workers > 1:
            with ProcessPoolExecutor(max_workers=config.workers) as executor:
                futures = {executor.submit(_get_token_trades_with_regime, a): a[0]
                           for a in args_list}
                for future in as_completed(futures):
                    try:
                        tk, trades = future.result()
                        if trades:
                            strat_trades[tk] = trades
                    except Exception:
                        pass
        else:
            for a in args_list:
                try:
                    tk, trades = _get_token_trades_with_regime(a)
                    if trades:
                        strat_trades[tk] = trades
                except Exception:
                    pass

        if verbose:
            total = sum(len(t) for t in strat_trades.values())
            print(f"    {strat_name}: {total} trades from {len(strat_trades)} tokens")

        # Tag trades with strategy name and merge
        for tk, trades in strat_trades.items():
            for t in trades:
                t['strategy'] = strat_name

            # Baseline: add all trades as-is
            if tk not in all_token_trades_base:
                all_token_trades_base[tk] = []
            all_token_trades_base[tk].extend(trades)

            # Weighted: scale position_usd by regime weight
            weighted_trades = []
            for t in trades:
                regime = t.get('regime_at_entry', RANGE)
                w = regime_weights.get(strat_name, regime)
                if w <= 0.01:
                    continue  # skip trade entirely

                tw = dict(t)
                tw['position_usd'] = t['position_usd'] * w
                tw['pnl'] = t['pnl'] * w
                tw['portfolio_scale'] = t.get('portfolio_scale', 1.0) * w
                tw['portfolio_position_usd'] = t.get('portfolio_position_usd',
                                                      t['position_usd']) * w
                weighted_trades.append(tw)

            if weighted_trades:
                if tk not in all_token_trades_weighted:
                    all_token_trades_weighted[tk] = []
                all_token_trades_weighted[tk].extend(weighted_trades)

    if not all_token_trades_base:
        return None, None

    fee_rate = get_fee_rate(config.exchange, config.market, 'taker')

    # Baseline simulation
    if verbose:
        print(f"\n  Simulating baseline (equal weight)...")
    eq_base, acc_base, skip_base, info_base = simulate_portfolio(
        all_token_trades_base, config.capital, config.max_token_pct,
        config.min_position_usd, mtm=config.mtm,
        dynamic_concentration=config.dynamic_concentration,
        fee_rate=fee_rate,
    )
    metrics_base = compute_portfolio_metrics(eq_base, acc_base, skip_base, info_base)

    # Weighted simulation
    if verbose:
        print(f"  Simulating regime-weighted...")
    eq_wt, acc_wt, skip_wt, info_wt = simulate_portfolio(
        all_token_trades_weighted, config.capital, config.max_token_pct,
        config.min_position_usd, mtm=config.mtm,
        dynamic_concentration=config.dynamic_concentration,
        fee_rate=fee_rate,
    )
    metrics_wt = compute_portfolio_metrics(eq_wt, acc_wt, skip_wt, info_wt)

    base_result = {
        'label': 'baseline',
        'equity': eq_base,
        'metrics': metrics_base,
        'info': info_base,
        'accepted': acc_base,
        'skipped': skip_base,
    }
    weighted_result = {
        'label': 'regime-weighted',
        'equity': eq_wt,
        'metrics': metrics_wt,
        'info': info_wt,
        'accepted': acc_wt,
        'skipped': skip_wt,
    }

    return base_result, weighted_result


def print_simulation_comparison(base: Dict, weighted: Dict, config: PortfolioConfig):
    """Print side-by-side baseline vs regime-weighted results."""
    fee_rate = get_fee_rate(config.exchange, config.market, 'taker')

    print(f"\n{'='*90}")
    print(f"REGIME-WEIGHTED vs BASELINE PORTFOLIO COMPARISON")
    print(f"  Capital: ${config.capital:,.0f} | Market: {config.market} | "
          f"Exchange: {config.exchange} | Fee: {fee_rate*100:.2f}%/side")
    print(f"{'='*90}\n")

    col_w = 22
    print(f"{'Metric':<28}{'Baseline':>{col_w}}{'Regime-Weighted':>{col_w}}{'Delta':>{col_w}}")
    print(f"{'-'*(28 + col_w*3)}")

    def row(name, attr, fmt="+.2f"):
        vb = getattr(base['metrics'], attr, 0)
        vw = getattr(weighted['metrics'], attr, 0)
        delta = vw - vb
        sb = f"{vb:{fmt}}"
        sw = f"{vw:{fmt}}"
        sd = f"{delta:{fmt}}"
        print(f"{name:<28}{sb:>{col_w}}{sw:>{col_w}}{sd:>{col_w}}")

    row("Total Return %", "total_return_pct", "+.1f")
    row("Ann. Return %", "annualized_return_pct", "+.1f")
    row("Sharpe", "sharpe_ratio", "+.2f")
    row("Sortino", "sortino_ratio", "+.2f")
    row("Calmar", "calmar_ratio", "+.2f")
    print()
    row("Max Drawdown %", "max_drawdown_pct", ".1f")
    row("Max DD Days", "max_drawdown_duration_days", "d")
    print()
    row("Total Trades", "accepted_trades", "d")
    row("Win Rate %", "win_rate_pct", ".1f")
    row("Profit Factor", "profit_factor", ".2f")
    row("Avg Trade PnL $", "avg_trade_pnl", ",.0f")
    print()
    row("Skipped", "skipped_trades", "d")
    row("Skip Rate %", "skip_rate_pct", ".1f")

    # Correlation between the two equity curves
    combined = pd.DataFrame({
        'baseline': base['equity'],
        'regime_weighted': weighted['equity'],
    }).dropna(how='all').ffill().dropna()
    if len(combined) > 30:
        rets = combined.pct_change().dropna().replace([np.inf, -np.inf], 0)
        corr = rets['baseline'].corr(rets['regime_weighted'])
        print(f"\n  Equity curve correlation: {corr:+.4f}")

    # Regime breakdown of weighted trades
    regime_counts = {}
    for t in weighted['accepted']:
        r = t.get('regime_at_entry', RANGE)
        rn = REGIME_NAMES.get(r, 'RANGE')
        regime_counts[rn] = regime_counts.get(rn, 0) + 1

    if regime_counts:
        print(f"\n  Regime-weighted trade distribution:")
        for rn in ['UPTREND', 'RANGE', 'QUIET', 'DOWNTREND', 'CRISIS']:
            c = regime_counts.get(rn, 0)
            total = weighted['metrics'].accepted_trades
            pct = c / max(total, 1) * 100
            print(f"    {rn:<12} {c:>6} trades ({pct:.1f}%)")

    print()


# ---------------------------------------------------------------------------
# Full Pipeline
# ---------------------------------------------------------------------------

def run_regime_analysis(
    strategy_names: List[str],
    tokens: List[str],
    config: PortfolioConfig,
    do_analyze: bool = True,
    do_simulate: bool = True,
    weight_method: str = 'profit_factor',
    custom_weights: Optional[Dict] = None,
    verbose: bool = True,
) -> Dict:
    """Full regime analysis pipeline."""
    t0 = time.time()

    # Step 1: Compute global regime from BTC
    if verbose:
        print("Computing global regime from BTC daily bars...")
    regime_market = 'spot' if config.market == 'combined' else config.market
    regime_series = compute_global_regime(config.data_dir, regime_market)

    # Print regime distribution
    regime_dist = regime_series.value_counts().sort_index()
    if verbose:
        print(f"  Regime distribution ({len(regime_series)} days):")
        for rid, count in regime_dist.items():
            rname = REGIME_NAMES.get(int(rid), '?')
            pct = count / len(regime_series) * 100
            print(f"    {rname:<12} {count:>5} days ({pct:.1f}%)")

    result = {'regime_distribution': {REGIME_NAMES[int(k)]: int(v)
              for k, v in regime_dist.items()}}

    # Step 2: Empirical analysis
    heatmap = None
    if do_analyze:
        if verbose:
            print(f"\n{'='*80}")
            print(f"PHASE 1: EMPIRICAL REGIME PERFORMANCE ANALYSIS")
            print(f"{'='*80}")

        heatmap = analyze_regime_performance(
            strategy_names, tokens, config, regime_series, verbose)
        print_regime_heatmap(heatmap)
        result['heatmap'] = heatmap

    # Step 3: Derive weights
    if do_simulate:
        if custom_weights:
            regime_weights = RegimeWeights(weights=custom_weights)
        elif heatmap:
            regime_weights = derive_weights_from_heatmap(heatmap, method=weight_method)
        else:
            # Run analysis to get heatmap first
            heatmap = analyze_regime_performance(
                strategy_names, tokens, config, regime_series, verbose=False)
            regime_weights = derive_weights_from_heatmap(heatmap, method=weight_method)

        if verbose:
            print_weights(regime_weights)

        # Step 4: Simulate
        if verbose:
            print(f"{'='*80}")
            print(f"PHASE 2: REGIME-WEIGHTED PORTFOLIO SIMULATION")
            print(f"{'='*80}")

        base_result, weighted_result = simulate_regime_weighted(
            strategy_names, tokens, config, regime_weights, regime_series, verbose)

        if base_result and weighted_result:
            print_simulation_comparison(base_result, weighted_result, config)
            result['baseline_metrics'] = asdict(base_result['metrics'])
            result['weighted_metrics'] = asdict(weighted_result['metrics'])
            result['weights'] = regime_weights.weights

    # Save results
    results_dir = os.path.join(os.path.dirname(_v3_dir), 'results')
    os.makedirs(results_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    strat_str = '_'.join(strategy_names)
    out_path = os.path.join(results_dir, f'regime_analysis_{strat_str}_{ts}.json')

    # Serialize heatmap
    save_data = {
        'strategy': 'regime_analysis',
        'strategies': strategy_names,
        'timestamp': ts,
        'elapsed': time.time() - t0,
    }
    save_data.update({k: v for k, v in result.items()
                      if not isinstance(v, (pd.Series, pd.DataFrame))})

    with open(out_path, 'w') as f:
        json.dump(save_data, f, indent=2, default=str)
    if verbose:
        print(f"Results saved to {out_path}")
        print(f"Total time: {time.time()-t0:.1f}s")

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='V3 Regime-Conditional Strategy Weighting')
    parser.add_argument('--strategies', nargs='+', required=True,
                        help='Strategy names (e.g. s11 s09)')
    parser.add_argument('--analyze', action='store_true',
                        help='Run empirical regime performance analysis')
    parser.add_argument('--simulate', action='store_true',
                        help='Run regime-weighted portfolio simulation')
    parser.add_argument('--weight-method', default='profit_factor',
                        choices=['profit_factor', 'binary'],
                        help='Weight derivation method')
    parser.add_argument('--capital', type=float, default=200_000)
    parser.add_argument('--max-weight', type=float, default=0.15)
    parser.add_argument('--universe', default='liquid',
                        choices=['all', 'filtered', 'liquid'])
    parser.add_argument('--tokens', nargs='+', help='Specific tokens')
    parser.add_argument('--market', default='spot', choices=['spot', 'perp', 'combined'])
    parser.add_argument('--exchange', default='binance')
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()

    if not args.analyze and not args.simulate:
        args.analyze = True
        args.simulate = True

    if args.tokens:
        tokens = args.tokens
    else:
        tokens = resolve_universe(args.universe, market=args.market, verbose=True)
    print(f"Universe: {len(tokens)} tokens")

    config = PortfolioConfig(
        capital=args.capital,
        max_token_pct=args.max_weight,
        data_dir='data',
        market=args.market,
        exchange=args.exchange,
        workers=args.workers,
    )

    run_regime_analysis(
        args.strategies, tokens, config,
        do_analyze=args.analyze,
        do_simulate=args.simulate,
        weight_method=args.weight_method,
    )


if __name__ == '__main__':
    main()
