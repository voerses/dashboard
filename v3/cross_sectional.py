"""
V3 Cross-Sectional Momentum — Rank-Based Portfolio Strategy
=============================================================

Ranks ALL tokens by trailing returns and goes long the top performers.
Structurally different from per-token time-series strategies (s01-s12).

Academically supported in crypto: cross-sectional outperforms time-series
momentum (Han, Kang, Ryu 2023). Provides diversification vs existing
Tier A strategies (median pairwise correlation +0.62).

Usage:
    python v3/cross_sectional.py --lookback 14 --workers 4
    python v3/cross_sectional.py --lookback 7 14 30 60 --sweep
    python v3/cross_sectional.py --universe liquid --top-pct 0.20
"""

import sys
import os
import time
import json
import argparse
import numpy as np
import pandas as pd
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Tuple
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

# ---------------------------------------------------------------------------
# Import reusable V3 modules (same pattern as portfolio.py / engine.py)
# ---------------------------------------------------------------------------
import importlib.util

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


_universe_mod = _load_v3('universe')
_engine_mod = _load_v3('engine')
_portfolio_mod = _load_v3('portfolio')

resolve_universe = _universe_mod.resolve_universe
get_fee_rate = _universe_mod.get_fee_rate
aggregate_to_timeframe = _engine_mod.aggregate_to_timeframe
compute_portfolio_metrics = _portfolio_mod.compute_portfolio_metrics
PortfolioMetrics = _portfolio_mod.PortfolioMetrics


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class CrossSectionalConfig:
    capital: float = 200_000
    lookback_days: int = 14        # trailing return window
    rebalance_days: int = 7        # weekly rebalance
    top_pct: float = 0.20          # long top quintile
    min_tokens: int = 5
    max_tokens: int = 30
    burn_in_days: int = 365        # OOS starts after 1yr
    min_adv_usd: float = 500_000   # ADV eligibility gate
    data_dir: str = 'data'
    market: str = 'spot'
    exchange: str = 'binance'
    workers: int = 4


# ---------------------------------------------------------------------------
# Data Loading
# ---------------------------------------------------------------------------

def _load_single_token(args):
    """Load 1H parquet for a single token. Worker function for parallel loading."""
    token, data_dir, market = args
    cache_dir = os.path.join(data_dir, market, '1h_cache')
    path = os.path.join(cache_dir, f'{token}_1h.parquet')
    if not os.path.exists(path):
        return token, None
    try:
        df = pd.read_parquet(path)
        if len(df) < 48:  # need at least 2 days
            return token, None
        return token, df
    except Exception:
        return token, None


def load_universe_data(tokens: List[str], data_dir: str, market: str,
                       workers: int = 4) -> Dict[str, pd.DataFrame]:
    """Load 1H parquets for all tokens in parallel."""
    args_list = [(t, data_dir, market) for t in tokens]
    result = {}

    if workers <= 1:
        for a in args_list:
            token, df = _load_single_token(a)
            if df is not None:
                result[token] = df
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_load_single_token, a): a[0] for a in args_list}
            for future in as_completed(futures):
                token, df = future.result()
                if df is not None:
                    result[token] = df

    return result


# ---------------------------------------------------------------------------
# Panel Construction
# ---------------------------------------------------------------------------

def build_daily_panel(token_data: Dict[str, pd.DataFrame]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Resample each token to daily. Align to shared DatetimeIndex.

    Returns:
        close_panel: DataFrame (dates × tokens) of daily close prices
        volume_panel: DataFrame (dates × tokens) of daily USD volume
    """
    close_dict = {}
    volume_dict = {}

    for token, df_1h in token_data.items():
        df_daily = aggregate_to_timeframe(df_1h, hours=24)
        close_dict[token] = df_daily['close']
        # Dollar volume = close * base volume
        volume_dict[token] = df_daily['close'] * df_daily['volume']

    close_panel = pd.DataFrame(close_dict).sort_index()
    volume_panel = pd.DataFrame(volume_dict).sort_index()

    return close_panel, volume_panel


# ---------------------------------------------------------------------------
# Trailing Returns & Eligibility
# ---------------------------------------------------------------------------

def compute_trailing_returns(close_panel: pd.DataFrame, lookback_days: int) -> pd.DataFrame:
    """Vectorized trailing returns: close / close.shift(lookback) - 1."""
    return close_panel / close_panel.shift(lookback_days) - 1


def compute_eligibility(close_panel: pd.DataFrame, volume_panel: pd.DataFrame,
                        min_adv_usd: float, burn_in_days: int,
                        lookback_days: int) -> pd.DataFrame:
    """Token is eligible at date T if:
    - has non-NaN close for at least burn_in_days
    - rolling 30d median ADV >= min_adv_usd
    - has non-NaN trailing return at T
    """
    # History requirement: count non-NaN closes up to each date
    history_count = close_panel.expanding(min_periods=1).count()
    has_history = history_count >= burn_in_days

    # ADV: rolling 30-day median of daily dollar volume
    rolling_adv = volume_panel.rolling(window=30, min_periods=10).median()
    adv_ok = rolling_adv >= min_adv_usd

    # Trailing return is non-NaN (implicitly requires lookback_days of price data)
    trailing_ret = compute_trailing_returns(close_panel, lookback_days)
    has_return = trailing_ret.notna()

    return has_history & adv_ok & has_return


# ---------------------------------------------------------------------------
# Ranking & Selection
# ---------------------------------------------------------------------------

def rank_and_select(returns: pd.DataFrame, eligibility: pd.DataFrame,
                    rebalance_dates: pd.DatetimeIndex,
                    top_pct: float, min_tokens: int,
                    max_tokens: int) -> Dict[pd.Timestamp, List[str]]:
    """At each rebalance date, rank eligible tokens by return, select top quintile.

    Returns:
        {date: [token_list]} — tokens selected at each rebalance
    """
    selections = {}

    for date in rebalance_dates:
        if date not in returns.index:
            continue

        # Get eligible tokens at this date
        elig_mask = eligibility.loc[date]
        eligible_tokens = elig_mask[elig_mask].index.tolist()

        if len(eligible_tokens) < min_tokens:
            continue

        # Rank by trailing return (descending)
        ret_row = returns.loc[date, eligible_tokens].dropna()
        if len(ret_row) < min_tokens:
            continue

        ret_row = ret_row.sort_values(ascending=False)

        # Select top quintile
        n_select = max(min_tokens, int(len(ret_row) * top_pct))
        n_select = min(n_select, max_tokens, len(ret_row))

        selected = ret_row.iloc[:n_select].index.tolist()
        selections[date] = selected

    return selections


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def _compute_slippage_bps(trade_usd: float, adv: float) -> float:
    """Position-size-aware slippage (matches engine.py model).

    slip_bps = 3.0 + 0.03 * sqrt(trade_usd / adv) * 10000, cap 100bps
    """
    participation = trade_usd / max(adv, 1.0)
    slip_bps = 3.0 + 0.03 * np.sqrt(participation) * 10000.0
    return min(slip_bps, 100.0)


def simulate_cross_sectional(close_panel: pd.DataFrame, volume_panel: pd.DataFrame,
                             selections: Dict[pd.Timestamp, List[str]],
                             config: CrossSectionalConfig) -> Tuple[pd.Series, List[Dict], Dict]:
    """Core cross-sectional simulation.

    Between rebalances: portfolio return = equal-weighted mean of selected token daily returns.
    At each rebalance: compute turnover (entering/exiting tokens), apply slippage + fees.

    Returns:
        equity_curve: pd.Series (daily)
        trades: list of trade dicts (compatible with portfolio.py)
        info: dict with simulation metadata
    """
    if not selections:
        empty_eq = pd.Series(dtype=float)
        return empty_eq, [], {'error': 'no_selections'}

    fee_rate = get_fee_rate(config.exchange, config.market, 'taker')

    # Sort rebalance dates
    rebal_dates = sorted(selections.keys())
    first_rebal = rebal_dates[0]

    # Daily returns for all tokens
    daily_returns = close_panel.pct_change()

    # Build date range from first rebalance to end of data
    all_dates = close_panel.index
    sim_dates = all_dates[all_dates >= first_rebal]

    equity = config.capital
    equity_series = {}
    trades = []
    current_holdings = []
    prev_holdings = []
    total_turnover_cost = 0.0
    total_rebalances = 0

    # Rolling 30d ADV for slippage calculation
    rolling_adv = volume_panel.rolling(window=30, min_periods=10).median()

    # Track holding periods for trade generation
    holding_entries = {}  # token -> {'entry_date': date, 'entry_equity': float}

    for i, date in enumerate(sim_dates):
        # Check if this is a rebalance date
        if date in selections:
            new_holdings = selections[date]
            total_rebalances += 1

            # Compute turnover
            prev_set = set(prev_holdings)
            new_set = set(new_holdings)
            entering = new_set - prev_set
            exiting = prev_set - new_set

            # Generate trade records for exiting tokens
            for token in exiting:
                if token in holding_entries:
                    entry_info = holding_entries.pop(token)
                    # Approximate PnL: position was 1/n of equity at entry
                    n_held = len(prev_holdings) if prev_holdings else 1
                    position_usd = entry_info['entry_equity'] / n_held
                    # Get return over holding period
                    entry_date = entry_info['entry_date']
                    if token in close_panel.columns:
                        entry_price = close_panel.loc[entry_date, token] if entry_date in close_panel.index else np.nan
                        exit_price = close_panel.loc[date, token] if date in close_panel.index else np.nan
                        if pd.notna(entry_price) and pd.notna(exit_price) and entry_price > 0:
                            ret = (exit_price / entry_price) - 1
                            pnl = position_usd * ret
                        else:
                            pnl = 0.0
                            ret = 0.0
                    else:
                        pnl = 0.0
                        ret = 0.0

                    trades.append({
                        'token': token,
                        'entry_time': entry_info['entry_date'],
                        'exit_time': date,
                        'position_usd': position_usd,
                        'pnl': pnl,
                        'return_pct': ret * 100,
                        'hold_hours': int((date - entry_info['entry_date']).total_seconds() / 3600),
                        'exit_reason': 'rebalance',
                        'strategy': 'cross_sectional',
                        'portfolio_scale': 1.0,
                        'portfolio_position_usd': position_usd,
                    })

            # Record entries for new tokens
            for token in entering:
                holding_entries[token] = {
                    'entry_date': date,
                    'entry_equity': equity,
                }

            # Apply slippage + fees for turnover
            n_tokens_in_basket = len(new_holdings)
            if n_tokens_in_basket > 0:
                position_per_token = equity / n_tokens_in_basket

                for token in entering | exiting:
                    adv = rolling_adv.loc[date, token] if (date in rolling_adv.index and
                            token in rolling_adv.columns and
                            pd.notna(rolling_adv.loc[date, token])) else 5_000_000
                    slip_bps = _compute_slippage_bps(position_per_token, adv)
                    slip_cost = position_per_token * slip_bps / 10000.0
                    fee_cost = position_per_token * fee_rate
                    total_cost = slip_cost + fee_cost
                    equity -= total_cost
                    total_turnover_cost += total_cost

            prev_holdings = new_holdings
            current_holdings = new_holdings

        # Apply daily returns
        if current_holdings and date in daily_returns.index:
            day_rets = daily_returns.loc[date, current_holdings].dropna()
            if len(day_rets) > 0:
                portfolio_ret = day_rets.mean()
                equity *= (1 + portfolio_ret)

        equity_series[date] = equity

    # Close out remaining holdings at end
    final_date = sim_dates[-1] if len(sim_dates) > 0 else None
    if final_date is not None:
        for token in list(holding_entries.keys()):
            entry_info = holding_entries.pop(token)
            n_held = len(current_holdings) if current_holdings else 1
            position_usd = entry_info['entry_equity'] / n_held
            if token in close_panel.columns:
                entry_price = close_panel.loc[entry_info['entry_date'], token] if entry_info['entry_date'] in close_panel.index else np.nan
                exit_price = close_panel.loc[final_date, token] if final_date in close_panel.index else np.nan
                if pd.notna(entry_price) and pd.notna(exit_price) and entry_price > 0:
                    ret = (exit_price / entry_price) - 1
                    pnl = position_usd * ret
                else:
                    pnl = 0.0
                    ret = 0.0
            else:
                pnl = 0.0
                ret = 0.0

            trades.append({
                'token': token,
                'entry_time': entry_info['entry_date'],
                'exit_time': final_date,
                'position_usd': position_usd,
                'pnl': pnl,
                'return_pct': ret * 100,
                'hold_hours': int((final_date - entry_info['entry_date']).total_seconds() / 3600),
                'exit_reason': 'end_of_data',
                'strategy': 'cross_sectional',
                'portfolio_scale': 1.0,
                'portfolio_position_usd': position_usd,
            })

    equity_curve = pd.Series(equity_series).sort_index()

    info = {
        'total_rebalances': total_rebalances,
        'total_turnover_cost': total_turnover_cost,
        'avg_basket_size': np.mean([len(v) for v in selections.values()]),
        'peak_concurrent_positions': max((len(v) for v in selections.values()), default=0),
        'peak_exposure_pct': 100.0,  # fully invested
        'capital_utilization_pct': 100.0,
        'skip_rate_pct': 0.0,
        'n_rebalance_dates': len(selections),
    }

    return equity_curve, trades, info


# ---------------------------------------------------------------------------
# Report (cross-sectional specific)
# ---------------------------------------------------------------------------

def print_cross_sectional_report(metrics: PortfolioMetrics, info: Dict,
                                 config: CrossSectionalConfig):
    """Print formatted cross-sectional momentum report."""
    fee_rate = get_fee_rate(config.exchange, config.market, 'taker')

    print(f"\n{'='*80}")
    print(f"CROSS-SECTIONAL MOMENTUM BACKTEST")
    print(f"  Capital: ${config.capital:,.0f} | Lookback: {config.lookback_days}d | "
          f"Rebalance: {config.rebalance_days}d | Top: {config.top_pct*100:.0f}%")
    print(f"  Market: {config.market} | Exchange: {config.exchange} | "
          f"Fee: {fee_rate*100:.2f}% per side")
    print(f"  Min ADV: ${config.min_adv_usd:,.0f} | Burn-in: {config.burn_in_days}d | "
          f"Basket: {config.min_tokens}-{config.max_tokens} tokens")
    print(f"{'='*80}\n")

    print("Performance:")
    print(f"  Total Return:     {metrics.total_return_pct:+.1f}%")
    print(f"  Annualized Return:{metrics.annualized_return_pct:+.2f}%")
    print(f"  Sharpe Ratio:     {metrics.sharpe_ratio:+.2f}")
    print(f"  Sortino Ratio:    {metrics.sortino_ratio:+.2f}")
    print(f"  Calmar Ratio:     {metrics.calmar_ratio:+.2f}")

    print(f"\nRisk:")
    print(f"  Max Drawdown:     {metrics.max_drawdown_pct:.1f}%")
    print(f"  Max DD Duration:  {metrics.max_drawdown_duration_days} days")

    print(f"\nExecution:")
    print(f"  Rebalances:       {info.get('total_rebalances', 0)}")
    print(f"  Avg basket size:  {info.get('avg_basket_size', 0):.1f} tokens")
    print(f"  Turnover cost:    ${info.get('total_turnover_cost', 0):,.0f}")
    print(f"  Total trades:     {metrics.total_trades}")
    print(f"  Win rate:         {metrics.win_rate_pct:.1f}%")
    print(f"  Profit factor:    {metrics.profit_factor:.2f}")
    print(f"  Avg trade PnL:    ${metrics.avg_trade_pnl:,.0f}")

    print(f"\nDiversification:")
    print(f"  Tokens traded:    {metrics.n_tokens_traded}")
    print(f"  HHI (exposure):   {metrics.hhi_exposure:.4f}")

    if metrics.top_contributors:
        print(f"\nAttribution:")
        print(f"  Top contributors:   {metrics.top_contributors}")
        print(f"  Worst contributors: {metrics.worst_contributors}")
    print()


# ---------------------------------------------------------------------------
# Full Pipeline
# ---------------------------------------------------------------------------

def run_cross_sectional_backtest(tokens: List[str],
                                 config: Optional[CrossSectionalConfig] = None,
                                 verbose: bool = True) -> Dict:
    """Full pipeline: load -> panel -> rank -> simulate -> metrics -> report -> save."""
    if config is None:
        config = CrossSectionalConfig()

    t0 = time.time()

    # 1. Load data
    if verbose:
        print(f"Loading {len(tokens)} tokens from {config.data_dir}/{config.market}/1h_cache/ ...")
    token_data = load_universe_data(tokens, config.data_dir, config.market, config.workers)
    if verbose:
        print(f"  Loaded {len(token_data)} tokens ({time.time()-t0:.1f}s)")

    if len(token_data) < config.min_tokens:
        print(f"  ERROR: Only {len(token_data)} tokens loaded, need at least {config.min_tokens}")
        return {}

    # 2. Build daily panel
    t1 = time.time()
    close_panel, volume_panel = build_daily_panel(token_data)
    if verbose:
        print(f"  Daily panel: {close_panel.shape[0]} days × {close_panel.shape[1]} tokens ({time.time()-t1:.1f}s)")

    # 3. Compute trailing returns & eligibility
    trailing_returns = compute_trailing_returns(close_panel, config.lookback_days)
    eligibility = compute_eligibility(close_panel, volume_panel,
                                      config.min_adv_usd, config.burn_in_days,
                                      config.lookback_days)

    # 4. Generate rebalance dates (every N days from burn_in onward)
    all_dates = close_panel.index
    if len(all_dates) <= config.burn_in_days:
        print(f"  ERROR: Not enough data ({len(all_dates)} days) for burn-in ({config.burn_in_days} days)")
        return {}

    oos_start = all_dates[config.burn_in_days]
    oos_dates = all_dates[all_dates >= oos_start]
    rebalance_dates = oos_dates[::config.rebalance_days]
    if verbose:
        print(f"  OOS period: {oos_start.strftime('%Y-%m-%d')} to {all_dates[-1].strftime('%Y-%m-%d')}")
        print(f"  Rebalance dates: {len(rebalance_dates)}")

    # 5. Rank and select
    selections = rank_and_select(trailing_returns, eligibility, rebalance_dates,
                                 config.top_pct, config.min_tokens, config.max_tokens)
    if verbose:
        print(f"  Selection dates with valid baskets: {len(selections)}")

    if not selections:
        print("  ERROR: No valid baskets formed. Check eligibility criteria.")
        return {}

    # 6. Simulate
    t2 = time.time()
    equity_curve, trades, info = simulate_cross_sectional(
        close_panel, volume_panel, selections, config)
    if verbose:
        print(f"  Simulation done ({time.time()-t2:.1f}s)")

    # 7. Compute metrics (reuse portfolio.py)
    skipped = []  # cross-sectional doesn't skip trades
    metrics = compute_portfolio_metrics(equity_curve, trades, skipped, info)

    # 8. Report
    if verbose:
        print_cross_sectional_report(metrics, info, config)

    # 9. Save JSON
    results_dir = os.path.join(os.path.dirname(_v3_dir), 'results')
    os.makedirs(results_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_path = os.path.join(results_dir,
        f'xsec_lb{config.lookback_days}_rb{config.rebalance_days}_top{int(config.top_pct*100)}_{ts}.json')

    result_data = {
        'strategy': 'cross_sectional_momentum',
        'config': asdict(config),
        'metrics': asdict(metrics),
        'info': info,
        'equity_start': float(equity_curve.iloc[0]) if len(equity_curve) > 0 else 0,
        'equity_end': float(equity_curve.iloc[-1]) if len(equity_curve) > 0 else 0,
        'n_days': len(equity_curve),
        'timestamp': ts,
    }
    with open(out_path, 'w') as f:
        json.dump(result_data, f, indent=2, default=str)
    if verbose:
        print(f"  Results saved to {out_path}")
        print(f"  Total time: {time.time()-t0:.1f}s")

    return {
        'equity_curve': equity_curve,
        'trades': trades,
        'metrics': metrics,
        'info': info,
        'config': config,
        'result_path': out_path,
    }


# ---------------------------------------------------------------------------
# Parameter Sweep
# ---------------------------------------------------------------------------

def run_parameter_sweep(tokens: List[str],
                        lookback_list: List[int],
                        rebalance_list: Optional[List[int]] = None,
                        top_pct_list: Optional[List[float]] = None,
                        base_config: Optional[CrossSectionalConfig] = None,
                        verbose: bool = True) -> pd.DataFrame:
    """Sweep over lookback × rebalance × top_pct combinations.

    Returns DataFrame of results sorted by Sharpe ratio.
    """
    if base_config is None:
        base_config = CrossSectionalConfig()
    if rebalance_list is None:
        rebalance_list = [base_config.rebalance_days]
    if top_pct_list is None:
        top_pct_list = [base_config.top_pct]

    # Load data once
    print(f"Loading {len(tokens)} tokens for parameter sweep...")
    token_data = load_universe_data(tokens, base_config.data_dir,
                                    base_config.market, base_config.workers)
    print(f"  Loaded {len(token_data)} tokens")

    close_panel, volume_panel = build_daily_panel(token_data)
    print(f"  Daily panel: {close_panel.shape[0]} days × {close_panel.shape[1]} tokens")

    all_dates = close_panel.index
    daily_returns = close_panel.pct_change()

    results_rows = []
    total_combos = len(lookback_list) * len(rebalance_list) * len(top_pct_list)
    combo_idx = 0

    for lb in lookback_list:
        # Pre-compute trailing returns and eligibility for this lookback
        trailing_ret = compute_trailing_returns(close_panel, lb)
        eligibility = compute_eligibility(close_panel, volume_panel,
                                          base_config.min_adv_usd,
                                          base_config.burn_in_days, lb)

        for rb in rebalance_list:
            for tp in top_pct_list:
                combo_idx += 1
                cfg = CrossSectionalConfig(
                    capital=base_config.capital,
                    lookback_days=lb,
                    rebalance_days=rb,
                    top_pct=tp,
                    min_tokens=base_config.min_tokens,
                    max_tokens=base_config.max_tokens,
                    burn_in_days=base_config.burn_in_days,
                    min_adv_usd=base_config.min_adv_usd,
                    data_dir=base_config.data_dir,
                    market=base_config.market,
                    exchange=base_config.exchange,
                    workers=base_config.workers,
                )

                if len(all_dates) <= cfg.burn_in_days:
                    continue

                oos_start = all_dates[cfg.burn_in_days]
                oos_dates = all_dates[all_dates >= oos_start]
                rebal_dates = oos_dates[::rb]

                selections = rank_and_select(trailing_ret, eligibility,
                                             rebal_dates, tp,
                                             cfg.min_tokens, cfg.max_tokens)

                if not selections:
                    continue

                equity_curve, trades, info = simulate_cross_sectional(
                    close_panel, volume_panel, selections, cfg)

                if len(equity_curve) < 2:
                    continue

                metrics = compute_portfolio_metrics(equity_curve, trades, [], info)

                row = {
                    'lookback': lb,
                    'rebalance': rb,
                    'top_pct': tp,
                    'sharpe': metrics.sharpe_ratio,
                    'sortino': metrics.sortino_ratio,
                    'calmar': metrics.calmar_ratio,
                    'ann_return_pct': metrics.annualized_return_pct,
                    'max_dd_pct': metrics.max_drawdown_pct,
                    'max_dd_days': metrics.max_drawdown_duration_days,
                    'total_return_pct': metrics.total_return_pct,
                    'n_trades': metrics.total_trades,
                    'win_rate_pct': metrics.win_rate_pct,
                    'profit_factor': metrics.profit_factor,
                    'avg_basket': info.get('avg_basket_size', 0),
                    'turnover_cost': info.get('total_turnover_cost', 0),
                    'n_tokens_traded': metrics.n_tokens_traded,
                }
                results_rows.append(row)

                if verbose:
                    print(f"  [{combo_idx}/{total_combos}] lb={lb} rb={rb} top={tp:.0%} | "
                          f"Sharpe={metrics.sharpe_ratio:+.2f} Return={metrics.annualized_return_pct:+.1f}% "
                          f"DD={metrics.max_drawdown_pct:.1f}%")

    if not results_rows:
        print("  No valid results from sweep.")
        return pd.DataFrame()

    df = pd.DataFrame(results_rows).sort_values('sharpe', ascending=False)

    # Print summary
    print(f"\n{'='*90}")
    print(f"PARAMETER SWEEP RESULTS — {len(df)} combinations")
    print(f"{'='*90}")
    print(f"{'LB':>4} {'RB':>4} {'Top%':>5} {'Sharpe':>8} {'Sortino':>8} {'Ann%':>8} "
          f"{'MaxDD%':>8} {'Trades':>7} {'WinR%':>7} {'PF':>6}")
    print(f"{'-'*90}")
    for _, r in df.iterrows():
        print(f"{int(r['lookback']):4d} {int(r['rebalance']):4d} {r['top_pct']:5.0%} "
              f"{r['sharpe']:+8.2f} {r['sortino']:+8.2f} {r['ann_return_pct']:+8.1f} "
              f"{r['max_dd_pct']:8.1f} {int(r['n_trades']):7d} {r['win_rate_pct']:7.1f} "
              f"{r['profit_factor']:6.2f}")
    print()

    # Save sweep results
    results_dir = os.path.join(os.path.dirname(_v3_dir), 'results')
    os.makedirs(results_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    sweep_path = os.path.join(results_dir, f'xsec_sweep_{ts}.json')
    with open(sweep_path, 'w') as f:
        json.dump(results_rows, f, indent=2, default=str)
    print(f"  Sweep results saved to {sweep_path}")

    return df


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='V3 Cross-Sectional Momentum Backtest')
    parser.add_argument('--lookback', nargs='+', type=int, default=[14],
                        help='Trailing return lookback in days (multiple for sweep)')
    parser.add_argument('--rebalance', nargs='+', type=int, default=[7],
                        help='Rebalance frequency in days (multiple for sweep)')
    parser.add_argument('--top-pct', nargs='+', type=float, default=[0.20],
                        help='Top percentile to select (multiple for sweep)')
    parser.add_argument('--capital', type=float, default=200_000)
    parser.add_argument('--min-adv', type=float, default=500_000,
                        help='Minimum ADV in USD for eligibility')
    parser.add_argument('--burn-in', type=int, default=365,
                        help='Burn-in days before OOS starts')
    parser.add_argument('--min-tokens', type=int, default=5)
    parser.add_argument('--max-tokens', type=int, default=30)
    parser.add_argument('--universe', default='liquid',
                        choices=['all', 'filtered', 'liquid'],
                        help='Token universe mode')
    parser.add_argument('--tokens', nargs='+', help='Specific tokens (overrides --universe)')
    parser.add_argument('--market', default='spot', choices=['spot', 'perp'])
    parser.add_argument('--exchange', default='binance')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--sweep', action='store_true',
                        help='Run parameter sweep (use multiple --lookback/--rebalance/--top-pct)')
    args = parser.parse_args()

    # Resolve tokens
    if args.tokens:
        tokens = args.tokens
    else:
        tokens = resolve_universe(args.universe, market=args.market, verbose=True)
    print(f"Universe: {len(tokens)} tokens ({args.universe if not args.tokens else 'custom'})")

    config = CrossSectionalConfig(
        capital=args.capital,
        lookback_days=args.lookback[0],
        rebalance_days=args.rebalance[0],
        top_pct=args.top_pct[0],
        min_tokens=args.min_tokens,
        max_tokens=args.max_tokens,
        burn_in_days=args.burn_in,
        min_adv_usd=args.min_adv,
        data_dir='data',
        market=args.market,
        exchange=args.exchange,
        workers=args.workers,
    )

    if args.sweep or len(args.lookback) > 1 or len(args.rebalance) > 1 or len(args.top_pct) > 1:
        run_parameter_sweep(
            tokens,
            lookback_list=args.lookback,
            rebalance_list=args.rebalance if len(args.rebalance) > 1 else None,
            top_pct_list=args.top_pct if len(args.top_pct) > 1 else None,
            base_config=config,
        )
    else:
        run_cross_sectional_backtest(tokens, config)


if __name__ == '__main__':
    main()
