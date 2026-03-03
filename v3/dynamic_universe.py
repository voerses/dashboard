"""
V3 Dynamic Universe — Point-in-Time Token Eligibility
=======================================================

Fixes look-ahead bias in token selection: the static universe is locked at
backtest start using CURRENT ADV/quality. The dynamic universe re-evaluates
token eligibility at each walk-forward window boundary using only data
available at that point.

Three bias sources:
  1. Token LIST is static (FIXED here) — re-evaluate at each WF window
  2. Per-bar liquidity mask gates entries (ALREADY correct in engine.py)
  3. Delisted tokens missing from dataset (NOT fixable without new data — P8)

Usage:
    python v3/dynamic_universe.py --strategy s11 --workers 4
    python v3/dynamic_universe.py --strategy s11 --compare --workers 4
    python v3/dynamic_universe.py --timeline --workers 4
"""

import sys
import os
import time
import json
import argparse
import numpy as np
import pandas as pd
import importlib.util
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Tuple, Set
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
get_all_tradeable = _universe_mod.get_all_tradeable
aggregate_to_timeframe = _engine_mod.aggregate_to_timeframe
compute_portfolio_metrics = _portfolio_mod.compute_portfolio_metrics
simulate_portfolio = _portfolio_mod.simulate_portfolio
PortfolioConfig = _portfolio_mod.PortfolioConfig
PortfolioMetrics = _portfolio_mod.PortfolioMetrics

DEFAULT_MIN_ADV_USD = 500_000
DEFAULT_BURN_IN_DAYS = 90
DEFAULT_MIN_BARS_DAILY = 90  # ~3 months of daily data to be tradeable


# ---------------------------------------------------------------------------
# Dynamic Universe Computation
# ---------------------------------------------------------------------------

def _load_token_daily(args):
    """Load and resample one token to daily. Worker function."""
    token, data_dir, market = args
    cache_dir = os.path.join(data_dir, market, '1h_cache')
    path = os.path.join(cache_dir, f'{token}_1h.parquet')
    if not os.path.exists(path):
        return token, None, None
    try:
        df_1h = pd.read_parquet(path)
        if len(df_1h) < 48:
            return token, None, None
        df_daily = aggregate_to_timeframe(df_1h, hours=24)
        return token, df_daily['close'], df_daily['close'] * df_daily['volume']
    except Exception:
        return token, None, None


def compute_dynamic_universe(
    all_tokens: List[str],
    data_dir: str = 'data',
    market: str = 'spot',
    eval_interval_days: int = 90,
    min_adv_usd: float = DEFAULT_MIN_ADV_USD,
    burn_in_days: int = DEFAULT_BURN_IN_DAYS,
    min_bars_daily: int = DEFAULT_MIN_BARS_DAILY,
    workers: int = 4,
    verbose: bool = True,
) -> Tuple[Dict[pd.Timestamp, List[str]], pd.DataFrame]:
    """Compute point-in-time token eligibility at periodic evaluation dates.

    At each evaluation date (every eval_interval_days), a token is eligible if:
      1. It has >= burn_in_days of non-NaN daily close data up to that date
      2. Its 30-day rolling median ADV >= min_adv_usd at that date
      3. It has >= min_bars_daily total daily bars up to that date

    Returns:
        timeline: {eval_date: [eligible_token_list]}
        summary_df: DataFrame with per-token first/last eligible dates
    """
    t0 = time.time()

    # Load all tokens to daily
    if verbose:
        print(f"  Loading {len(all_tokens)} tokens...")
    args_list = [(t, data_dir, market) for t in all_tokens]
    close_dict = {}
    volume_dict = {}

    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_load_token_daily, a): a[0] for a in args_list}
            for future in as_completed(futures):
                token, close_s, vol_s = future.result()
                if close_s is not None:
                    close_dict[token] = close_s
                    volume_dict[token] = vol_s
    else:
        for a in args_list:
            token, close_s, vol_s = _load_token_daily(a)
            if close_s is not None:
                close_dict[token] = close_s
                volume_dict[token] = vol_s

    if verbose:
        print(f"  Loaded {len(close_dict)} tokens ({time.time()-t0:.1f}s)")

    # Build panels
    close_panel = pd.DataFrame(close_dict).sort_index()
    volume_panel = pd.DataFrame(volume_dict).sort_index()
    all_dates = close_panel.index

    # Rolling 30d median ADV (point-in-time)
    rolling_adv = volume_panel.rolling(window=30, min_periods=10).median()

    # Cumulative non-NaN count (history depth at each date)
    history_count = close_panel.expanding(min_periods=1).count()

    # Generate evaluation dates
    eval_dates = all_dates[::eval_interval_days]
    if len(all_dates) > 0 and all_dates[-1] not in eval_dates:
        eval_dates = eval_dates.append(pd.DatetimeIndex([all_dates[-1]]))

    timeline = {}
    token_first_eligible = {}
    token_last_eligible = {}

    for eval_date in eval_dates:
        eligible = []
        for token in close_dict.keys():
            # Check history depth
            if eval_date not in history_count.index:
                continue
            hist = history_count.loc[eval_date, token]
            if pd.isna(hist) or hist < min_bars_daily:
                continue

            # Check burn-in
            if hist < burn_in_days:
                continue

            # Check ADV
            if eval_date not in rolling_adv.index:
                continue
            adv = rolling_adv.loc[eval_date, token]
            if pd.isna(adv) or adv < min_adv_usd:
                continue

            eligible.append(token)

            # Track first/last eligible
            if token not in token_first_eligible:
                token_first_eligible[token] = eval_date
            token_last_eligible[token] = eval_date

        timeline[eval_date] = sorted(eligible)

    # Build summary
    summary_rows = []
    for token in sorted(close_dict.keys()):
        first_date = close_panel[token].first_valid_index()
        last_date = close_panel[token].last_valid_index()
        n_eligible = sum(1 for d, toks in timeline.items() if token in toks)
        summary_rows.append({
            'token': token,
            'data_start': first_date,
            'data_end': last_date,
            'total_days': int(history_count.loc[all_dates[-1], token])
                          if token in history_count.columns and not pd.isna(
                              history_count.loc[all_dates[-1], token]) else 0,
            'first_eligible': token_first_eligible.get(token),
            'last_eligible': token_last_eligible.get(token),
            'n_eval_dates_eligible': n_eligible,
            'n_eval_dates_total': len(eval_dates),
            'always_eligible': n_eligible == len(eval_dates),
            'never_eligible': n_eligible == 0,
        })
    summary_df = pd.DataFrame(summary_rows)

    if verbose:
        n_always = summary_df['always_eligible'].sum()
        n_never = summary_df['never_eligible'].sum()
        n_partial = len(summary_df) - n_always - n_never
        print(f"  Evaluation dates: {len(eval_dates)} (every {eval_interval_days}d)")
        print(f"  Tokens: {n_always} always eligible, {n_partial} partial, {n_never} never eligible")
        if len(timeline) > 0:
            sizes = [len(v) for v in timeline.values()]
            print(f"  Universe size: min={min(sizes)}, max={max(sizes)}, "
                  f"mean={np.mean(sizes):.1f}")

    return timeline, summary_df


def print_universe_timeline(timeline: Dict[pd.Timestamp, List[str]],
                            summary_df: pd.DataFrame):
    """Print the dynamic universe timeline."""
    print(f"\n{'='*90}")
    print(f"DYNAMIC UNIVERSE TIMELINE")
    print(f"{'='*90}\n")

    print(f"{'Date':<14} {'#Tokens':>8}  Entering / Exiting")
    print(f"{'-'*70}")

    prev_set = set()
    for date in sorted(timeline.keys()):
        curr_set = set(timeline[date])
        entering = curr_set - prev_set
        exiting = prev_set - curr_set

        enter_str = f"+{len(entering)}" if entering else ""
        exit_str = f"-{len(exiting)}" if exiting else ""
        changes = f"{enter_str} {exit_str}".strip()

        # Show token names for small changes
        detail = ""
        if 0 < len(entering) <= 5:
            detail += f"  +[{', '.join(sorted(entering))}]"
        if 0 < len(exiting) <= 5:
            detail += f"  -[{', '.join(sorted(exiting))}]"

        print(f"{date.strftime('%Y-%m-%d'):<14} {len(curr_set):>8}  {changes}{detail}")
        prev_set = curr_set

    # Tokens that lost eligibility mid-backtest
    partial = summary_df[~summary_df['always_eligible'] & ~summary_df['never_eligible']]
    if len(partial) > 0:
        print(f"\n  Tokens with partial eligibility ({len(partial)}):")
        for _, row in partial.sort_values('first_eligible').iterrows():
            first = row['first_eligible'].strftime('%Y-%m-%d') if pd.notna(row['first_eligible']) else '?'
            last = row['last_eligible'].strftime('%Y-%m-%d') if pd.notna(row['last_eligible']) else '?'
            print(f"    {row['token']:<8} eligible {first} → {last} "
                  f"({row['n_eval_dates_eligible']}/{row['n_eval_dates_total']} windows)")
    print()


# ---------------------------------------------------------------------------
# Dynamic Portfolio Simulation
# ---------------------------------------------------------------------------

def _get_token_trades_dynamic(args):
    """Extract trades for one token, respecting dynamic universe windows.

    Masks entries outside windows where the token is eligible.
    """
    (ticker, strategy_path, data_dir, market, capital, exchange,
     eligible_windows) = args
    # eligible_windows: list of (start_date, end_date) where token is eligible

    v3_dir = os.path.dirname(os.path.abspath(__file__))
    if v3_dir not in sys.path:
        sys.path.insert(0, v3_dir)

    eng_mod = _load_v3('engine')
    EngineLocal = eng_mod.Engine

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

    spec = importlib.util.spec_from_file_location('strat', strategy_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    result = mod.strategy(ctx)

    n = len(ctx.ind_1h['close'])
    idx = ctx.idx_1h

    # Walk-forward masking (standard)
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

    # Dynamic universe masking: block entries outside eligible windows
    universe_mask = np.zeros(n, dtype=np.bool_)
    for start_dt, end_dt in eligible_windows:
        # Find bar indices within this window
        start_idx = idx.searchsorted(start_dt)
        end_idx = idx.searchsorted(end_dt, side='right')
        universe_mask[start_idx:end_idx] = True

    masked_entry = masked_entry & universe_mask

    result.entry_mask = masked_entry
    trades, final_equity = engine._simulate(ctx, result)

    if not trades:
        return ticker, None

    for t in trades:
        eb = t['entry_bar']
        xb = t['exit_bar']
        t['token'] = ticker
        t['entry_time'] = idx[min(eb, len(idx) - 1)]
        t['exit_time'] = idx[min(xb, len(idx) - 1)]

    return ticker, trades


def compute_eligible_windows(
    token: str,
    timeline: Dict[pd.Timestamp, List[str]],
) -> List[Tuple[pd.Timestamp, pd.Timestamp]]:
    """Convert timeline to list of (start, end) windows where token is eligible."""
    sorted_dates = sorted(timeline.keys())
    windows = []
    in_window = False
    window_start = None

    for i, date in enumerate(sorted_dates):
        is_eligible = token in timeline[date]

        if is_eligible and not in_window:
            window_start = date
            in_window = True
        elif not is_eligible and in_window:
            windows.append((window_start, date))
            in_window = False

    # Close final window
    if in_window and window_start is not None:
        # Extend to end of data
        windows.append((window_start, sorted_dates[-1] + pd.Timedelta(days=365)))

    return windows


def run_dynamic_portfolio(
    strategy_name: str,
    all_tokens: List[str],
    config: PortfolioConfig,
    timeline: Dict[pd.Timestamp, List[str]],
    verbose: bool = True,
) -> Optional[Dict]:
    """Run portfolio sim with dynamic universe gating."""
    strat_path = resolve_strategy_path(strategy_name)
    if strat_path is None:
        print(f"ERROR: Strategy not found: {strategy_name}")
        return None

    strat_path = os.path.abspath(strat_path)

    # Determine which tokens appear in any window
    all_eligible = set()
    for toks in timeline.values():
        all_eligible.update(toks)

    tokens_to_run = sorted(all_eligible & set(all_tokens))
    if verbose:
        print(f"  Tokens with any eligibility: {len(tokens_to_run)}")

    # Pre-compute eligible windows per token
    token_windows = {}
    for token in tokens_to_run:
        windows = compute_eligible_windows(token, timeline)
        if windows:
            token_windows[token] = windows

    # Extract trades with dynamic masking
    args_list = [
        (tk, strat_path, config.data_dir, config.market,
         config.capital, config.exchange, token_windows.get(tk, []))
        for tk in tokens_to_run
    ]

    all_trades = {}
    if config.workers > 1 and len(tokens_to_run) > 1:
        with ProcessPoolExecutor(max_workers=config.workers) as executor:
            futures = {executor.submit(_get_token_trades_dynamic, a): a[0]
                       for a in args_list}
            for future in as_completed(futures):
                try:
                    tk, trades = future.result()
                    if trades:
                        all_trades[tk] = trades
                except Exception:
                    pass
    else:
        for a in args_list:
            try:
                tk, trades = _get_token_trades_dynamic(a)
                if trades:
                    all_trades[tk] = trades
            except Exception:
                pass

    if not all_trades:
        return None

    total_trades = sum(len(t) for t in all_trades.values())
    if verbose:
        print(f"  Dynamic: {total_trades} trades from {len(all_trades)} tokens")

    fee_rate = get_fee_rate(config.exchange, config.market, 'taker')
    eq, accepted, skipped, info = simulate_portfolio(
        all_trades, config.capital, config.max_token_pct,
        config.min_position_usd, mtm=config.mtm,
        dynamic_concentration=config.dynamic_concentration,
        fee_rate=fee_rate,
    )
    metrics = compute_portfolio_metrics(eq, accepted, skipped, info)

    return {
        'label': f'{strategy_name} (dynamic)',
        'equity': eq,
        'metrics': metrics,
        'info': info,
        'accepted': accepted,
        'skipped': skipped,
        'n_tokens': len(all_trades),
    }


def run_static_portfolio(
    strategy_name: str,
    tokens: List[str],
    config: PortfolioConfig,
    verbose: bool = True,
) -> Optional[Dict]:
    """Run standard static-universe portfolio sim for comparison."""
    strat_path = resolve_strategy_path(strategy_name)
    if strat_path is None:
        return None

    strat_path = os.path.abspath(strat_path)
    token_trades = _portfolio_mod.extract_token_trades(strat_path, tokens, config)

    if not token_trades:
        return None

    total_trades = sum(len(t) for t in token_trades.values())
    if verbose:
        print(f"  Static: {total_trades} trades from {len(token_trades)} tokens")

    fee_rate = get_fee_rate(config.exchange, config.market, 'taker')
    eq, accepted, skipped, info = simulate_portfolio(
        token_trades, config.capital, config.max_token_pct,
        config.min_position_usd, mtm=config.mtm,
        dynamic_concentration=config.dynamic_concentration,
        fee_rate=fee_rate,
    )
    metrics = compute_portfolio_metrics(eq, accepted, skipped, info)

    return {
        'label': f'{strategy_name} (static)',
        'equity': eq,
        'metrics': metrics,
        'info': info,
        'accepted': accepted,
        'skipped': skipped,
        'n_tokens': len(token_trades),
    }


# ---------------------------------------------------------------------------
# Comparison Report
# ---------------------------------------------------------------------------

def print_comparison(static: Dict, dynamic: Dict, config: PortfolioConfig,
                     summary_df: pd.DataFrame):
    """Print static vs dynamic universe comparison."""
    fee_rate = get_fee_rate(config.exchange, config.market, 'taker')

    print(f"\n{'='*90}")
    print(f"STATIC vs DYNAMIC UNIVERSE — LOOK-AHEAD BIAS ANALYSIS")
    print(f"  Capital: ${config.capital:,.0f} | Market: {config.market} | "
          f"Fee: {fee_rate*100:.2f}%/side")
    print(f"{'='*90}\n")

    col_w = 25
    print(f"{'Metric':<28}{static['label']:>{col_w}}{dynamic['label']:>{col_w}}{'Delta':>{col_w}}")
    print(f"{'-'*(28 + col_w*3)}")

    def row(name, attr, fmt="+.2f"):
        vs = getattr(static['metrics'], attr, 0)
        vd = getattr(dynamic['metrics'], attr, 0)
        delta = vd - vs
        ss = f"{vs:{fmt}}"
        sd = f"{vd:{fmt}}"
        sde = f"{delta:{fmt}}"
        print(f"{name:<28}{ss:>{col_w}}{sd:>{col_w}}{sde:>{col_w}}")

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

    # Tokens unique to each
    static_tokens = set(t['token'] for t in static['accepted'])
    dynamic_tokens = set(t['token'] for t in dynamic['accepted'])
    only_static = static_tokens - dynamic_tokens
    only_dynamic = dynamic_tokens - static_tokens

    print(f"  Tokens in static only:  {len(only_static)}", end="")
    if only_static and len(only_static) <= 10:
        print(f"  [{', '.join(sorted(only_static))}]")
    else:
        print()
    print(f"  Tokens in dynamic only: {len(only_dynamic)}", end="")
    if only_dynamic and len(only_dynamic) <= 10:
        print(f"  [{', '.join(sorted(only_dynamic))}]")
    else:
        print()
    print(f"  Tokens in both:        {len(static_tokens & dynamic_tokens)}")

    # Bias quantification
    s_sharpe = static['metrics'].sharpe_ratio
    d_sharpe = dynamic['metrics'].sharpe_ratio
    if abs(s_sharpe) > 0.01:
        bias_pct = (s_sharpe - d_sharpe) / abs(s_sharpe) * 100
        print(f"\n  Look-ahead bias in Sharpe: {bias_pct:+.1f}%")
        if bias_pct > 5:
            print(f"  WARNING: Static universe inflates Sharpe by {bias_pct:.1f}%")
        elif bias_pct > 1:
            print(f"  NOTE: Modest bias — static overestimates by {bias_pct:.1f}%")
        else:
            print(f"  GOOD: Minimal bias — static and dynamic agree within {abs(bias_pct):.1f}%")

    # Trade count from tokens that lost eligibility
    partial = summary_df[~summary_df['always_eligible'] & ~summary_df['never_eligible']]
    partial_tokens = set(partial['token'].tolist())
    static_trades_partial = [t for t in static['accepted'] if t['token'] in partial_tokens]
    dynamic_trades_partial = [t for t in dynamic['accepted'] if t['token'] in partial_tokens]
    ghost_trades = len(static_trades_partial) - len(dynamic_trades_partial)
    if ghost_trades > 0:
        ghost_pnl = (sum(t['pnl'] for t in static_trades_partial) -
                     sum(t['pnl'] for t in dynamic_trades_partial))
        print(f"\n  Ghost trades (on tokens outside their eligible window): {ghost_trades}")
        print(f"  Ghost trade PnL impact: ${ghost_pnl:+,.0f}")

    print()


# ---------------------------------------------------------------------------
# Full Pipeline
# ---------------------------------------------------------------------------

def run_dynamic_universe_analysis(
    strategy_name: str,
    tokens: List[str],
    config: PortfolioConfig,
    show_timeline: bool = False,
    compare: bool = True,
    eval_interval_days: int = 90,
    min_adv_usd: float = DEFAULT_MIN_ADV_USD,
    verbose: bool = True,
) -> Dict:
    """Full dynamic universe analysis pipeline."""
    t0 = time.time()

    # Step 1: Compute dynamic universe timeline
    if verbose:
        print(f"Computing dynamic universe timeline...")
    timeline, summary_df = compute_dynamic_universe(
        tokens, config.data_dir, config.market,
        eval_interval_days=eval_interval_days,
        min_adv_usd=min_adv_usd,
        workers=config.workers,
        verbose=verbose,
    )

    if show_timeline:
        print_universe_timeline(timeline, summary_df)

    result = {
        'timeline_size': {str(k): len(v) for k, v in timeline.items()},
        'n_always_eligible': int(summary_df['always_eligible'].sum()),
        'n_never_eligible': int(summary_df['never_eligible'].sum()),
        'n_partial': int(len(summary_df) - summary_df['always_eligible'].sum()
                         - summary_df['never_eligible'].sum()),
    }

    if not compare:
        return result

    # Step 2: Run static portfolio
    if verbose:
        print(f"\nRunning static universe portfolio ({strategy_name})...")
    static_result = run_static_portfolio(strategy_name, tokens, config, verbose)

    # Step 3: Run dynamic portfolio
    if verbose:
        print(f"\nRunning dynamic universe portfolio ({strategy_name})...")
    dynamic_result = run_dynamic_portfolio(
        strategy_name, tokens, config, timeline, verbose)

    if static_result and dynamic_result:
        print_comparison(static_result, dynamic_result, config, summary_df)

        result['static_metrics'] = asdict(static_result['metrics'])
        result['dynamic_metrics'] = asdict(dynamic_result['metrics'])

    # Save
    results_dir = os.path.join(os.path.dirname(_v3_dir), 'results')
    os.makedirs(results_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_path = os.path.join(results_dir, f'dynamic_universe_{strategy_name}_{ts}.json')
    with open(out_path, 'w') as f:
        json.dump(result, f, indent=2, default=str)
    if verbose:
        print(f"Results saved to {out_path}")
        print(f"Total time: {time.time()-t0:.1f}s")

    return result


# ---------------------------------------------------------------------------
# CLI
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


def main():
    parser = argparse.ArgumentParser(
        description='V3 Dynamic Universe — Point-in-Time Token Eligibility')
    parser.add_argument('--strategy', required=True,
                        help='Strategy name (e.g. s11)')
    parser.add_argument('--timeline', action='store_true',
                        help='Print full universe timeline')
    parser.add_argument('--compare', action='store_true', default=True,
                        help='Compare static vs dynamic portfolio (default)')
    parser.add_argument('--no-compare', dest='compare', action='store_false')
    parser.add_argument('--eval-interval', type=int, default=90,
                        help='Re-evaluation interval in days (default: 90, matches WF windows)')
    parser.add_argument('--min-adv', type=float, default=DEFAULT_MIN_ADV_USD,
                        help='Minimum ADV for eligibility')
    parser.add_argument('--capital', type=float, default=200_000)
    parser.add_argument('--max-weight', type=float, default=0.15)
    parser.add_argument('--universe', default='liquid',
                        choices=['all', 'filtered', 'liquid'])
    parser.add_argument('--tokens', nargs='+')
    parser.add_argument('--market', default='spot', choices=['spot', 'perp'])
    parser.add_argument('--exchange', default='binance')
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()

    if args.tokens:
        tokens = args.tokens
    else:
        tokens = resolve_universe(args.universe, market=args.market, verbose=True)
    print(f"Universe: {len(tokens)} tokens (static starting point)")

    config = PortfolioConfig(
        capital=args.capital,
        max_token_pct=args.max_weight,
        data_dir='data',
        market=args.market,
        exchange=args.exchange,
        workers=args.workers,
    )

    run_dynamic_universe_analysis(
        args.strategy, tokens, config,
        show_timeline=args.timeline,
        compare=args.compare,
        eval_interval_days=args.eval_interval,
        min_adv_usd=args.min_adv,
    )


if __name__ == '__main__':
    main()
