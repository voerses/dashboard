"""
V3 Pairs Trading — Statistical Arbitrage on Perpetual Futures
==============================================================

Identifies cointegrated / correlated token pairs, trades the spread
when it diverges from equilibrium. Uses perp futures for both legs
(long underperformer, short outperformer).

Pair selection criteria (rolling window):
  1. Minimum correlation (stability gate)
  2. Cointegration test (ADF on spread residual)
  3. Half-life of mean reversion (speed filter)
  4. Both tokens meet ADV gate

Entry: z-score of spread > entry_z (2.0 default)
Exit:  z-score < exit_z (0.5) OR max hold exceeded OR stop hit

Costs: perp taker fee + slippage on both legs + funding rate

Usage:
    python v3/pairs_trading.py --workers 4
    python v3/pairs_trading.py --entry-z 1.5 2.0 2.5 --sweep
    python v3/pairs_trading.py --analyze   # show pair candidates
"""

import sys
import os
import time
import json
import argparse
import warnings
import numpy as np
import pandas as pd
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Tuple
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

warnings.filterwarnings('ignore', category=FutureWarning)
warnings.filterwarnings('ignore', category=RuntimeWarning)

# ---------------------------------------------------------------------------
# Import reusable V3 modules
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
_xsec_mod = _load_v3('cross_sectional')
_sector_mod = _load_v3('sector_rotation')

resolve_universe = _universe_mod.resolve_universe
get_fee_rate = _universe_mod.get_fee_rate
aggregate_to_timeframe = _engine_mod.aggregate_to_timeframe
compute_portfolio_metrics = _portfolio_mod.compute_portfolio_metrics
PortfolioMetrics = _portfolio_mod.PortfolioMetrics
load_universe_data = _xsec_mod.load_universe_data
build_daily_panel = _xsec_mod.build_daily_panel
compute_eligibility = _xsec_mod.compute_eligibility
_compute_slippage_bps = _xsec_mod._compute_slippage_bps
compute_btc_regime_daily = _sector_mod.compute_btc_regime_daily
get_token_sector = _sector_mod.get_token_sector


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class PairsConfig:
    capital: float = 200_000
    lookback_days: int = 60          # window for correlation/cointegration
    rebalance_days: int = 14         # re-evaluate pairs every N days
    zscore_window: int = 30          # rolling window for z-score computation
    max_pairs: int = 15              # max concurrent pairs
    entry_z: float = 2.0             # enter spread trade when |z| > entry_z
    exit_z: float = 0.5              # exit when |z| < exit_z (mean reversion)
    stop_z: float = 4.0              # stop loss: exit if |z| > stop_z
    max_hold_days: int = 30          # max holding period per trade
    min_correlation: float = 0.50    # minimum rolling correlation for pair
    max_correlation: float = 0.95    # skip near-perfect correlation (same token)
    min_half_life: int = 3           # min mean-reversion half-life (days)
    max_half_life: int = 60          # max half-life (too slow = not mean-reverting)
    same_sector_only: bool = False   # restrict pairs to same sector
    burn_in_days: int = 365          # OOS starts after 1yr
    min_adv_usd: float = 500_000    # ADV eligibility gate
    regime_filter: bool = True       # go flat in strong downtrends
    market: str = 'perp'             # perp data for both legs
    data_dir: str = 'data'
    exchange: str = 'binance'
    workers: int = 4


# ---------------------------------------------------------------------------
# Data Loading (perp-aware)
# ---------------------------------------------------------------------------

def _load_single_perp(args):
    """Load 1H parquet for a single perp token."""
    token, data_dir, market = args
    cache_dir = os.path.join(data_dir, market, '1h_cache')
    path = os.path.join(cache_dir, f'{token}_1h.parquet')
    if not os.path.exists(path):
        return token, None
    try:
        df = pd.read_parquet(path)
        if len(df) < 48:
            return token, None
        return token, df
    except Exception:
        return token, None


def load_perp_data(tokens: List[str], data_dir: str, market: str = 'perp',
                   workers: int = 4) -> Dict[str, pd.DataFrame]:
    """Load perp parquets (includes funding_rate, funding_1h columns)."""
    args_list = [(t, data_dir, market) for t in tokens]
    result = {}
    if workers <= 1:
        for a in args_list:
            token, df = _load_single_perp(a)
            if df is not None:
                result[token] = df
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_load_single_perp, a): a[0] for a in args_list}
            for future in as_completed(futures):
                token, df = future.result()
                if df is not None:
                    result[token] = df
    return result


def build_daily_panel_with_funding(
    token_data: Dict[str, pd.DataFrame],
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build daily panels for close, volume, and cumulative funding.

    Returns:
        close_panel: DataFrame (dates x tokens)
        volume_panel: DataFrame (dates x tokens) — USD volume
        funding_panel: DataFrame (dates x tokens) — daily cumulative funding rate
    """
    close_dict = {}
    volume_dict = {}
    funding_dict = {}

    for token, df_1h in token_data.items():
        df_daily = aggregate_to_timeframe(df_1h, hours=24)
        close_dict[token] = df_daily['close']
        volume_dict[token] = df_daily['close'] * df_daily['volume']

        # Funding: sum hourly funding rates per day
        if 'funding_1h' in df_1h.columns:
            funding_daily = df_1h['funding_1h'].resample('D').sum()
            funding_dict[token] = funding_daily
        else:
            funding_dict[token] = pd.Series(0.0, index=df_daily.index)

    close_panel = pd.DataFrame(close_dict).sort_index()
    volume_panel = pd.DataFrame(volume_dict).sort_index()
    funding_panel = pd.DataFrame(funding_dict).sort_index().reindex(close_panel.index).fillna(0)

    return close_panel, volume_panel, funding_panel


# ---------------------------------------------------------------------------
# Pair Selection — Cointegration & Correlation
# ---------------------------------------------------------------------------

def compute_half_life(spread: np.ndarray) -> float:
    """Estimate half-life of mean reversion via OLS on lag-1 spread."""
    spread = spread[~np.isnan(spread)]
    if len(spread) < 20:
        return np.inf
    y = np.diff(spread)
    x = spread[:-1]
    x = x - np.mean(x)
    if np.std(x) < 1e-10:
        return np.inf
    # OLS: y = beta * x + epsilon
    beta = np.sum(x * y) / np.sum(x * x)
    if beta >= 0:
        return np.inf  # not mean-reverting
    half_life = -np.log(2) / beta
    return max(half_life, 0.1)


def compute_hedge_ratio(y: np.ndarray, x: np.ndarray) -> float:
    """OLS hedge ratio: y = beta * x + alpha."""
    mask = ~np.isnan(y) & ~np.isnan(x)
    y, x = y[mask], x[mask]
    if len(y) < 20:
        return 1.0
    x_dm = x - np.mean(x)
    denom = np.sum(x_dm * x_dm)
    if denom < 1e-10:
        return 1.0
    return np.sum(x_dm * (y - np.mean(y))) / denom


def evaluate_pair(
    log_price_a: np.ndarray,
    log_price_b: np.ndarray,
    min_corr: float,
    max_corr: float,
    min_hl: int,
    max_hl: int,
) -> Optional[Dict]:
    """Evaluate a single pair for cointegration + mean reversion.

    Returns dict with metrics if pair passes filters, None otherwise.
    """
    mask = ~np.isnan(log_price_a) & ~np.isnan(log_price_b)
    lpa, lpb = log_price_a[mask], log_price_b[mask]
    if len(lpa) < 60:
        return None

    # Correlation
    corr = np.corrcoef(lpa, lpb)[0, 1]
    if np.isnan(corr) or corr < min_corr or corr > max_corr:
        return None

    # Hedge ratio and spread
    beta = compute_hedge_ratio(lpa, lpb)
    spread = lpa - beta * lpb

    # Half-life
    hl = compute_half_life(spread)
    if hl < min_hl or hl > max_hl:
        return None

    # ADF test (simplified: use half-life as proxy)
    # True ADF is expensive for thousands of pairs; half-life < 60 implies stationarity
    spread_std = np.std(spread)
    if spread_std < 1e-10:
        return None

    return {
        'correlation': corr,
        'hedge_ratio': beta,
        'half_life': hl,
        'spread_std': spread_std,
        'n_obs': len(lpa),
    }


def select_pairs(
    close_panel: pd.DataFrame,
    volume_panel: pd.DataFrame,
    date: pd.Timestamp,
    config: PairsConfig,
    eligibility: Optional[pd.DataFrame] = None,
) -> List[Dict]:
    """Select valid pairs at a given date using trailing window.

    Returns list of pair dicts with {token_a, token_b, hedge_ratio, ...}
    """
    lookback = config.lookback_days

    # Get lookback window
    dates = close_panel.index
    date_idx = dates.get_loc(date) if date in dates else None
    if date_idx is None or date_idx < lookback:
        return []

    window_close = close_panel.iloc[date_idx - lookback:date_idx]

    # Eligible tokens at this date
    tokens = window_close.columns.tolist()
    if eligibility is not None and date in eligibility.index:
        tokens = [t for t in tokens if eligibility.loc[date, t]]

    if len(tokens) < 4:
        return []

    # Log prices
    log_prices = np.log(window_close[tokens].values + 1e-10)

    # Evaluate all pairs
    pairs = []
    token_idx = {t: i for i, t in enumerate(tokens)}

    for i, tok_a in enumerate(tokens):
        for j, tok_b in enumerate(tokens):
            if j <= i:
                continue  # avoid duplicates and self-pairs

            # Sector filter
            if config.same_sector_only:
                if get_token_sector(tok_a) != get_token_sector(tok_b):
                    continue

            result = evaluate_pair(
                log_prices[:, i], log_prices[:, j],
                config.min_correlation, config.max_correlation,
                config.min_half_life, config.max_half_life,
            )
            if result is not None:
                result['token_a'] = tok_a
                result['token_b'] = tok_b
                pairs.append(result)

    # Rank by quality: lower half-life (faster reversion) + higher correlation
    # Score = correlation / half_life (higher = better)
    for p in pairs:
        p['score'] = p['correlation'] / max(p['half_life'], 1)

    pairs.sort(key=lambda x: -x['score'])

    # Limit to max_pairs
    return pairs[:config.max_pairs]


# ---------------------------------------------------------------------------
# Z-Score Computation
# ---------------------------------------------------------------------------

def compute_spread_zscore(
    close_panel: pd.DataFrame,
    pairs: List[Dict],
    zscore_window: int = 30,
) -> Dict[Tuple[str, str], pd.DataFrame]:
    """Compute rolling z-score of spread for each pair.

    Returns {(token_a, token_b): DataFrame with columns [spread, zscore]}
    """
    result = {}
    log_close = np.log(close_panel + 1e-10)

    for pair in pairs:
        ta, tb = pair['token_a'], pair['token_b']
        beta = pair['hedge_ratio']

        if ta not in log_close.columns or tb not in log_close.columns:
            continue

        spread = log_close[ta] - beta * log_close[tb]
        spread_mean = spread.rolling(zscore_window, min_periods=10).mean()
        spread_std = spread.rolling(zscore_window, min_periods=10).std()
        zscore = (spread - spread_mean) / spread_std.replace(0, np.nan)

        result[(ta, tb)] = pd.DataFrame({
            'spread': spread,
            'zscore': zscore,
        })

    return result


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def simulate_pairs_trading(
    close_panel: pd.DataFrame,
    volume_panel: pd.DataFrame,
    funding_panel: pd.DataFrame,
    pair_selections: Dict[pd.Timestamp, List[Dict]],
    zscore_data: Dict[Tuple[str, str], pd.DataFrame],
    config: PairsConfig,
    risk_on: Optional[pd.Series] = None,
) -> Tuple[pd.Series, List[Dict], Dict]:
    """Core pairs trading simulation.

    At each bar:
    - Check existing positions for exit signals
    - Check new pairs for entry signals
    - Apply funding costs to open positions

    Returns:
        equity_curve: pd.Series (daily)
        trades: list of trade dicts
        info: simulation metadata
    """
    fee_rate = get_fee_rate(config.exchange, config.market, 'taker')

    # Find simulation start (first rebalance date)
    rebal_dates = sorted(pair_selections.keys())
    if not rebal_dates:
        return pd.Series(dtype=float), [], {'error': 'no_pairs'}

    first_rebal = rebal_dates[0]
    all_dates = close_panel.index
    sim_dates = all_dates[all_dates >= first_rebal]

    equity = config.capital
    cash = config.capital
    equity_series = {}
    trades = []
    total_costs = 0.0
    total_funding = 0.0
    n_entries = 0
    n_exits = 0
    n_stops = 0
    n_timeouts = 0

    rolling_adv = volume_panel.rolling(window=30, min_periods=10).median()

    # Active pairs for trading (updated at rebalance dates)
    active_pair_list = []
    # Open positions: list of {pair_key, token_a, token_b, hedge_ratio,
    #                          entry_date, entry_z, direction, position_usd, entry_prices}
    open_positions = []

    position_per_pair = config.capital / max(config.max_pairs, 1)

    for date in sim_dates:
        # Update active pairs at rebalance dates
        if date in pair_selections:
            active_pair_list = pair_selections[date]

            # If regime filter says risk-off, close all and skip
            if risk_on is not None and date in risk_on.index and not risk_on.loc[date]:
                # Close all open positions — return capital to cash
                for pos in list(open_positions):
                    pnl, cost = _close_position(pos, date, close_panel, trades, 'regime_off')
                    cash += pos['position_usd'] + pnl - cost
                    total_costs += cost
                    n_exits += 1
                open_positions = []
                active_pair_list = []

        # --- Check exits for open positions ---
        positions_to_close = []
        for pos in open_positions:
            pair_key = pos['pair_key']
            if pair_key not in zscore_data:
                positions_to_close.append((pos, 'data_loss'))
                continue

            zdf = zscore_data[pair_key]
            if date not in zdf.index:
                continue

            current_z = zdf.loc[date, 'zscore']
            if np.isnan(current_z):
                continue

            days_held = (date - pos['entry_date']).days

            # Exit conditions
            exit_reason = None
            if abs(current_z) < config.exit_z:
                exit_reason = 'mean_reversion'  # target hit
            elif abs(current_z) > config.stop_z:
                exit_reason = 'stop_loss'
                n_stops += 1
            elif days_held >= config.max_hold_days:
                exit_reason = 'timeout'
                n_timeouts += 1

            if exit_reason:
                positions_to_close.append((pos, exit_reason))

        for pos, reason in positions_to_close:
            pnl, cost = _close_position(pos, date, close_panel, trades, reason)
            cash += pos['position_usd'] + pnl - cost
            total_costs += cost
            open_positions.remove(pos)
            n_exits += 1

        # --- Apply funding to open positions ---
        for pos in open_positions:
            ta, tb = pos['token_a'], pos['token_b']
            fund_a = funding_panel.loc[date, ta] if (date in funding_panel.index and ta in funding_panel.columns) else 0
            fund_b = funding_panel.loc[date, tb] if (date in funding_panel.index and tb in funding_panel.columns) else 0
            # Long leg: pay funding if positive, receive if negative
            # Short leg: receive funding if positive, pay if negative
            d = pos['direction']  # +1 = long A / short B, -1 = long B / short A
            half_pos = pos['position_usd'] / 2
            # Net funding: long pays, short receives
            net_funding = half_pos * (d * fund_a - d * fund_b)
            cash -= net_funding
            total_funding += net_funding
            pos['cumulative_funding'] += net_funding

        # --- Check entries ---
        if len(open_positions) < config.max_pairs:
            for pair in active_pair_list:
                if len(open_positions) >= config.max_pairs:
                    break

                ta, tb = pair['token_a'], pair['token_b']
                pair_key = (ta, tb)

                # Skip if already have this pair open
                if any(p['pair_key'] == pair_key for p in open_positions):
                    continue

                if pair_key not in zscore_data:
                    continue

                zdf = zscore_data[pair_key]
                if date not in zdf.index:
                    continue

                current_z = zdf.loc[date, 'zscore']
                if np.isnan(current_z):
                    continue

                # Entry signal: z-score exceeds threshold
                if abs(current_z) < config.entry_z:
                    continue

                # Check we have cash
                position_per_pair = equity / max(config.max_pairs, 1)
                if cash < position_per_pair * 0.5:
                    continue

                # Direction: if z > 0, spread too high -> short A, long B
                # if z < 0, spread too low -> long A, short B
                direction = -1 if current_z > 0 else 1

                # Entry costs (both legs)
                half_pos = position_per_pair / 2
                for tok in [ta, tb]:
                    adv = rolling_adv.loc[date, tok] if (date in rolling_adv.index and
                            tok in rolling_adv.columns and
                            pd.notna(rolling_adv.loc[date, tok])) else 5_000_000
                    slip_bps = _compute_slippage_bps(half_pos, adv)
                    entry_cost = half_pos * (slip_bps / 10000.0 + fee_rate)
                    cash -= entry_cost
                    total_costs += entry_cost

                cash -= position_per_pair
                entry_prices = {}
                for tok in [ta, tb]:
                    if date in close_panel.index and tok in close_panel.columns:
                        entry_prices[tok] = close_panel.loc[date, tok]

                open_positions.append({
                    'pair_key': pair_key,
                    'token_a': ta,
                    'token_b': tb,
                    'hedge_ratio': pair['hedge_ratio'],
                    'entry_date': date,
                    'entry_z': current_z,
                    'direction': direction,
                    'position_usd': position_per_pair,
                    'entry_prices': entry_prices,
                    'cumulative_funding': 0.0,
                })
                n_entries += 1

        # --- Compute equity: cash + mark-to-market open positions ---
        mtm_value = 0.0
        for pos in open_positions:
            ta, tb = pos['token_a'], pos['token_b']
            d = pos['direction']
            half_pos = pos['position_usd'] / 2

            price_a = close_panel.loc[date, ta] if (date in close_panel.index and ta in close_panel.columns) else np.nan
            price_b = close_panel.loc[date, tb] if (date in close_panel.index and tb in close_panel.columns) else np.nan
            entry_a = pos['entry_prices'].get(ta, price_a)
            entry_b = pos['entry_prices'].get(tb, price_b)

            if pd.notna(price_a) and pd.notna(entry_a) and entry_a > 0:
                ret_a = (price_a / entry_a) - 1
            else:
                ret_a = 0
            if pd.notna(price_b) and pd.notna(entry_b) and entry_b > 0:
                ret_b = (price_b / entry_b) - 1
            else:
                ret_b = 0

            # d=+1: long A, short B -> PnL = half*(ret_a) + half*(-ret_b)
            # d=-1: short A, long B -> PnL = half*(-ret_a) + half*(ret_b)
            pair_pnl = half_pos * d * (ret_a - ret_b) - pos['cumulative_funding']
            mtm_value += pos['position_usd'] + pair_pnl

        equity = cash + mtm_value
        equity_series[date] = equity

    # Close remaining positions at end
    final_date = sim_dates[-1] if len(sim_dates) > 0 else None
    if final_date is not None:
        for pos in list(open_positions):
            _close_position(pos, final_date, close_panel, trades, 'end_of_data')

    equity_curve = pd.Series(equity_series).sort_index()

    # Pair-level attribution
    pair_pnl = {}
    for t in trades:
        pk = t.get('pair_key', '')
        pair_pnl[pk] = pair_pnl.get(pk, 0) + t['pnl']

    info = {
        'total_entries': n_entries,
        'total_exits': n_exits,
        'total_stops': n_stops,
        'total_timeouts': n_timeouts,
        'total_costs': total_costs,
        'total_funding': total_funding,
        'avg_pairs_open': np.mean([len(pair_selections.get(d, [])) for d in rebal_dates]),
        'peak_concurrent_positions': config.max_pairs,
        'peak_exposure_pct': 100.0,
        'capital_utilization_pct': 100.0,
        'skip_rate_pct': 0.0,
        'pair_pnl': pair_pnl,
    }

    return equity_curve, trades, info


def _close_position(pos, date, close_panel, trades, reason):
    """Close a pairs position and record the trade."""
    ta, tb = pos['token_a'], pos['token_b']
    d = pos['direction']
    half_pos = pos['position_usd'] / 2

    price_a = close_panel.loc[date, ta] if (date in close_panel.index and ta in close_panel.columns) else np.nan
    price_b = close_panel.loc[date, tb] if (date in close_panel.index and tb in close_panel.columns) else np.nan
    entry_a = pos['entry_prices'].get(ta, price_a)
    entry_b = pos['entry_prices'].get(tb, price_b)

    if pd.notna(price_a) and pd.notna(entry_a) and entry_a > 0:
        ret_a = (price_a / entry_a) - 1
    else:
        ret_a = 0
    if pd.notna(price_b) and pd.notna(entry_b) and entry_b > 0:
        ret_b = (price_b / entry_b) - 1
    else:
        ret_b = 0

    pair_pnl = half_pos * d * (ret_a - ret_b) - pos['cumulative_funding']
    pair_return = pair_pnl / pos['position_usd'] * 100

    # Exit costs (simplified — use fixed estimate)
    exit_cost = pos['position_usd'] * 0.001  # ~10bps total exit cost

    trades.append({
        'token': f"{ta}/{tb}",
        'pair_key': pos['pair_key'],
        'token_a': ta,
        'token_b': tb,
        'direction': d,
        'entry_time': pos['entry_date'],
        'exit_time': date,
        'position_usd': pos['position_usd'],
        'pnl': pair_pnl - exit_cost,
        'return_pct': pair_return,
        'hold_hours': int((date - pos['entry_date']).total_seconds() / 3600),
        'exit_reason': reason,
        'entry_z': pos['entry_z'],
        'funding_cost': pos['cumulative_funding'],
        'strategy': 'pairs_trading',
        'portfolio_scale': 1.0,
        'portfolio_position_usd': pos['position_usd'],
    })

    return pair_pnl, exit_cost


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_pairs_report(metrics: PortfolioMetrics, info: Dict, config: PairsConfig):
    """Print formatted pairs trading report."""
    fee_rate = get_fee_rate(config.exchange, config.market, 'taker')

    print(f"\n{'='*80}")
    print(f"PAIRS TRADING BACKTEST")
    print(f"  Capital: ${config.capital:,.0f} | Lookback: {config.lookback_days}d | "
          f"Rebal: {config.rebalance_days}d")
    print(f"  Entry z: {config.entry_z} | Exit z: {config.exit_z} | "
          f"Stop z: {config.stop_z} | Max hold: {config.max_hold_days}d")
    print(f"  Max pairs: {config.max_pairs} | Min corr: {config.min_correlation} | "
          f"Same sector: {config.same_sector_only}")
    print(f"  Market: {config.market} | Exchange: {config.exchange} | "
          f"Fee: {fee_rate*100:.2f}% per side | Regime: {'ON' if config.regime_filter else 'OFF'}")
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
    print(f"  Total entries:    {info.get('total_entries', 0)}")
    print(f"  Mean reversions:  {info.get('total_exits', 0) - info.get('total_stops', 0) - info.get('total_timeouts', 0)}")
    print(f"  Stop losses:      {info.get('total_stops', 0)}")
    print(f"  Timeouts:         {info.get('total_timeouts', 0)}")
    print(f"  Total costs:      ${info.get('total_costs', 0):,.0f}")
    print(f"  Total funding:    ${info.get('total_funding', 0):,.0f}")
    print(f"  Total trades:     {metrics.total_trades}")
    print(f"  Win rate:         {metrics.win_rate_pct:.1f}%")
    print(f"  Profit factor:    {metrics.profit_factor:.2f}")
    print(f"  Avg trade PnL:    ${metrics.avg_trade_pnl:,.0f}")

    print(f"\nDiversification:")
    print(f"  Tokens traded:    {metrics.n_tokens_traded}")
    print(f"  HHI (exposure):   {metrics.hhi_exposure:.4f}")

    # Top pairs
    pair_pnl = info.get('pair_pnl', {})
    if pair_pnl:
        sorted_pairs = sorted(pair_pnl.items(), key=lambda x: -x[1])
        print(f"\nTop Pairs:")
        for pk, pnl in sorted_pairs[:5]:
            print(f"  {str(pk):>30s}  ${pnl:>+10,.0f}")
        if len(sorted_pairs) > 5:
            print(f"  ...")
            for pk, pnl in sorted_pairs[-3:]:
                print(f"  {str(pk):>30s}  ${pnl:>+10,.0f}")

    if metrics.top_contributors:
        print(f"\nToken Attribution:")
        print(f"  Top:   {metrics.top_contributors}")
        print(f"  Worst: {metrics.worst_contributors}")
    print()


# ---------------------------------------------------------------------------
# Full Pipeline
# ---------------------------------------------------------------------------

def run_pairs_backtest(
    tokens: List[str],
    config: Optional[PairsConfig] = None,
    verbose: bool = True,
) -> Dict:
    """Full pipeline: load -> panel -> select pairs -> z-scores -> simulate."""
    if config is None:
        config = PairsConfig()

    t0 = time.time()

    # 1. Load perp data
    if verbose:
        print(f"Loading {len(tokens)} tokens from {config.data_dir}/{config.market}/1h_cache/ ...")
    token_data = load_perp_data(tokens, config.data_dir, config.market, config.workers)
    if verbose:
        print(f"  Loaded {len(token_data)} tokens ({time.time()-t0:.1f}s)")

    if len(token_data) < 4:
        print(f"  ERROR: Only {len(token_data)} tokens loaded, need at least 4")
        return {}

    # 2. Build daily panels
    t1 = time.time()
    close_panel, volume_panel, funding_panel = build_daily_panel_with_funding(token_data)
    if verbose:
        print(f"  Daily panel: {close_panel.shape[0]} days x {close_panel.shape[1]} tokens ({time.time()-t1:.1f}s)")

    # 3. Eligibility
    eligibility = compute_eligibility(close_panel, volume_panel,
                                      config.min_adv_usd, config.burn_in_days,
                                      config.lookback_days)

    # 4. Generate rebalance dates
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

    # 5. Regime filter
    risk_on = None
    if config.regime_filter:
        risk_on = compute_btc_regime_daily(close_panel)
        oos_risk_off = (~risk_on.loc[oos_start:]).sum()
        oos_total = len(risk_on.loc[oos_start:])
        if verbose:
            print(f"  Regime filter: {oos_risk_off}/{oos_total} days risk-off ({oos_risk_off/max(oos_total,1)*100:.0f}%)")

    # 6. Select pairs at each rebalance date
    t2 = time.time()
    pair_selections = {}
    all_pairs_ever = {}  # union of all pairs selected
    for date in rebalance_dates:
        # Regime filter
        if risk_on is not None and date in risk_on.index and not risk_on.loc[date]:
            pair_selections[date] = []
            continue

        pairs = select_pairs(close_panel, volume_panel, date, config, eligibility)
        pair_selections[date] = pairs
        for p in pairs:
            pk = (p['token_a'], p['token_b'])
            if pk not in all_pairs_ever:
                all_pairs_ever[pk] = p

    total_unique_pairs = len(all_pairs_ever)
    active_rebalances = sum(1 for v in pair_selections.values() if len(v) > 0)
    if verbose:
        print(f"  Pair selection: {total_unique_pairs} unique pairs across "
              f"{active_rebalances} active rebalances ({time.time()-t2:.1f}s)")

    if total_unique_pairs == 0:
        print("  ERROR: No valid pairs found. Check correlation/half-life thresholds.")
        return {}

    # 7. Compute z-scores for all pairs ever selected
    t3 = time.time()
    zscore_data = compute_spread_zscore(
        close_panel, list(all_pairs_ever.values()), config.zscore_window)
    if verbose:
        print(f"  Z-scores computed for {len(zscore_data)} pairs ({time.time()-t3:.1f}s)")

    # 8. Simulate
    t4 = time.time()
    equity_curve, trades, info = simulate_pairs_trading(
        close_panel, volume_panel, funding_panel,
        pair_selections, zscore_data, config, risk_on)
    if verbose:
        print(f"  Simulation done ({time.time()-t4:.1f}s)")

    # 9. Compute metrics
    metrics = compute_portfolio_metrics(equity_curve, trades, [], info)

    # 10. Report
    if verbose:
        print_pairs_report(metrics, info, config)

    # 11. Save
    results_dir = os.path.join(os.path.dirname(_v3_dir), 'results')
    os.makedirs(results_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_path = os.path.join(results_dir,
        f'pairs_ez{config.entry_z}_lb{config.lookback_days}_{ts}.json')

    result_data = {
        'strategy': 'pairs_trading',
        'config': asdict(config),
        'metrics': asdict(metrics),
        'info': {k: v for k, v in info.items() if k != 'pair_pnl'},
        'pair_pnl': {str(k): v for k, v in info.get('pair_pnl', {}).items()},
        'n_unique_pairs': total_unique_pairs,
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

def run_parameter_sweep(
    tokens: List[str],
    entry_z_list: List[float],
    lookback_list: Optional[List[int]] = None,
    rebalance_list: Optional[List[int]] = None,
    base_config: Optional[PairsConfig] = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """Sweep over entry_z x lookback x rebalance."""
    if base_config is None:
        base_config = PairsConfig()
    if lookback_list is None:
        lookback_list = [base_config.lookback_days]
    if rebalance_list is None:
        rebalance_list = [base_config.rebalance_days]

    # Load data once
    print(f"Loading {len(tokens)} tokens for parameter sweep...")
    token_data = load_perp_data(tokens, base_config.data_dir, base_config.market, base_config.workers)
    print(f"  Loaded {len(token_data)} tokens")

    close_panel, volume_panel, funding_panel = build_daily_panel_with_funding(token_data)
    print(f"  Daily panel: {close_panel.shape[0]} days x {close_panel.shape[1]} tokens")

    risk_on = None
    if base_config.regime_filter:
        risk_on = compute_btc_regime_daily(close_panel)
        print(f"  Regime filter: ON")

    all_dates = close_panel.index

    results_rows = []
    total_combos = len(entry_z_list) * len(lookback_list) * len(rebalance_list)
    combo_idx = 0

    for lb in lookback_list:
        eligibility = compute_eligibility(close_panel, volume_panel,
                                          base_config.min_adv_usd,
                                          base_config.burn_in_days, lb)

        for rb in rebalance_list:
            for ez in entry_z_list:
                combo_idx += 1
                cfg = PairsConfig(
                    capital=base_config.capital,
                    lookback_days=lb,
                    rebalance_days=rb,
                    zscore_window=base_config.zscore_window,
                    max_pairs=base_config.max_pairs,
                    entry_z=ez,
                    exit_z=base_config.exit_z,
                    stop_z=base_config.stop_z,
                    max_hold_days=base_config.max_hold_days,
                    min_correlation=base_config.min_correlation,
                    max_correlation=base_config.max_correlation,
                    min_half_life=base_config.min_half_life,
                    max_half_life=base_config.max_half_life,
                    same_sector_only=base_config.same_sector_only,
                    burn_in_days=base_config.burn_in_days,
                    min_adv_usd=base_config.min_adv_usd,
                    regime_filter=base_config.regime_filter,
                    market=base_config.market,
                    data_dir=base_config.data_dir,
                    exchange=base_config.exchange,
                    workers=base_config.workers,
                )

                if len(all_dates) <= cfg.burn_in_days:
                    continue

                oos_start = all_dates[cfg.burn_in_days]
                oos_dates = all_dates[all_dates >= oos_start]
                rebal_dates = oos_dates[::rb]

                # Select pairs
                pair_selections = {}
                all_pairs_ever = {}
                for date in rebal_dates:
                    if risk_on is not None and date in risk_on.index and not risk_on.loc[date]:
                        pair_selections[date] = []
                        continue
                    pairs = select_pairs(close_panel, volume_panel, date, cfg, eligibility)
                    pair_selections[date] = pairs
                    for p in pairs:
                        pk = (p['token_a'], p['token_b'])
                        if pk not in all_pairs_ever:
                            all_pairs_ever[pk] = p

                if not all_pairs_ever:
                    continue

                zscore_data = compute_spread_zscore(
                    close_panel, list(all_pairs_ever.values()), cfg.zscore_window)

                equity_curve, trades_list, info = simulate_pairs_trading(
                    close_panel, volume_panel, funding_panel,
                    pair_selections, zscore_data, cfg, risk_on)

                if len(equity_curve) < 2:
                    continue

                metrics = compute_portfolio_metrics(equity_curve, trades_list, [], info)

                row = {
                    'entry_z': ez,
                    'lookback': lb,
                    'rebalance': rb,
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
                    'n_pairs': len(all_pairs_ever),
                    'n_tokens_traded': metrics.n_tokens_traded,
                    'total_costs': info.get('total_costs', 0),
                    'total_funding': info.get('total_funding', 0),
                }
                results_rows.append(row)

                if verbose:
                    print(f"  [{combo_idx}/{total_combos}] ez={ez} lb={lb} rb={rb} | "
                          f"Sharpe={metrics.sharpe_ratio:+.2f} Return={metrics.annualized_return_pct:+.1f}% "
                          f"DD={metrics.max_drawdown_pct:.1f}% Trades={metrics.total_trades}")

    if not results_rows:
        print("  No valid results from sweep.")
        return pd.DataFrame()

    df = pd.DataFrame(results_rows).sort_values('sharpe', ascending=False)

    print(f"\n{'='*100}")
    print(f"PAIRS TRADING SWEEP RESULTS - {len(df)} combinations")
    print(f"{'='*100}")
    print(f"{'EZ':>5} {'LB':>4} {'RB':>4} {'Sharpe':>8} {'Sortino':>8} "
          f"{'Ann%':>8} {'MaxDD%':>8} {'Trades':>7} {'WinR%':>7} {'PF':>6} {'Pairs':>6}")
    print(f"{'-'*100}")
    for _, r in df.iterrows():
        print(f"{r['entry_z']:5.1f} {int(r['lookback']):4d} {int(r['rebalance']):4d} "
              f"{r['sharpe']:+8.2f} {r['sortino']:+8.2f} {r['ann_return_pct']:+8.1f} "
              f"{r['max_dd_pct']:8.1f} {int(r['n_trades']):7d} {r['win_rate_pct']:7.1f} "
              f"{r['profit_factor']:6.2f} {int(r['n_pairs']):6d}")
    print()

    # Save
    results_dir = os.path.join(os.path.dirname(_v3_dir), 'results')
    os.makedirs(results_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    sweep_path = os.path.join(results_dir, f'pairs_sweep_{ts}.json')
    with open(sweep_path, 'w') as f:
        json.dump(results_rows, f, indent=2, default=str)
    print(f"  Sweep results saved to {sweep_path}")

    return df


# ---------------------------------------------------------------------------
# Pair Analysis (show candidates without trading)
# ---------------------------------------------------------------------------

def analyze_pairs(
    tokens: List[str],
    config: Optional[PairsConfig] = None,
    verbose: bool = True,
) -> List[Dict]:
    """Analyze pair candidates at the latest available date."""
    if config is None:
        config = PairsConfig()

    print(f"Loading {len(tokens)} tokens...")
    token_data = load_perp_data(tokens, config.data_dir, config.market, config.workers)
    print(f"  Loaded {len(token_data)} tokens")

    close_panel, volume_panel, funding_panel = build_daily_panel_with_funding(token_data)

    eligibility = compute_eligibility(close_panel, volume_panel,
                                      config.min_adv_usd, config.burn_in_days,
                                      config.lookback_days)

    # Use latest date
    latest_date = close_panel.index[-1]
    print(f"  Analyzing pairs at {latest_date.strftime('%Y-%m-%d')}...")

    pairs = select_pairs(close_panel, volume_panel, latest_date, config, eligibility)

    print(f"\n{'='*90}")
    print(f"PAIR CANDIDATES — {len(pairs)} pairs (max {config.max_pairs})")
    print(f"  Lookback: {config.lookback_days}d | Min corr: {config.min_correlation} | "
          f"Half-life: {config.min_half_life}-{config.max_half_life}d")
    print(f"{'='*90}")
    print(f"{'#':>3} {'Token A':>8} {'Token B':>8} {'Sector A':>10} {'Sector B':>10} "
          f"{'Corr':>6} {'Beta':>6} {'HL(d)':>6} {'Score':>6}")
    print(f"{'-'*90}")

    for i, p in enumerate(pairs):
        sec_a = get_token_sector(p['token_a'])
        sec_b = get_token_sector(p['token_b'])
        print(f"{i+1:3d} {p['token_a']:>8s} {p['token_b']:>8s} "
              f"{sec_a:>10s} {sec_b:>10s} "
              f"{p['correlation']:6.3f} {p['hedge_ratio']:6.2f} "
              f"{p['half_life']:6.1f} {p['score']:6.3f}")

    # Also compute current z-scores
    if pairs:
        zscore_data = compute_spread_zscore(close_panel, pairs, config.zscore_window)
        print(f"\nCurrent Z-Scores:")
        for p in pairs[:10]:
            pk = (p['token_a'], p['token_b'])
            if pk in zscore_data:
                zdf = zscore_data[pk]
                if latest_date in zdf.index:
                    z = zdf.loc[latest_date, 'zscore']
                    signal = 'ENTRY' if abs(z) > config.entry_z else '-'
                    print(f"  {p['token_a']:>8s}/{p['token_b']:<8s}  z={z:+6.2f}  {signal}")

    print()
    return pairs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='V3 Pairs Trading Backtest')
    parser.add_argument('--entry-z', nargs='+', type=float, default=[2.0],
                        help='Z-score entry threshold (multiple for sweep)')
    parser.add_argument('--exit-z', type=float, default=0.5)
    parser.add_argument('--stop-z', type=float, default=4.0)
    parser.add_argument('--lookback', nargs='+', type=int, default=[60],
                        help='Lookback for pair selection (multiple for sweep)')
    parser.add_argument('--rebalance', nargs='+', type=int, default=[14])
    parser.add_argument('--zscore-window', type=int, default=30)
    parser.add_argument('--max-pairs', type=int, default=15)
    parser.add_argument('--max-hold', type=int, default=30)
    parser.add_argument('--min-corr', type=float, default=0.50)
    parser.add_argument('--max-corr', type=float, default=0.95)
    parser.add_argument('--min-half-life', type=int, default=3)
    parser.add_argument('--max-half-life', type=int, default=60)
    parser.add_argument('--same-sector', action='store_true',
                        help='Only pair tokens within the same sector')
    parser.add_argument('--capital', type=float, default=200_000)
    parser.add_argument('--min-adv', type=float, default=500_000)
    parser.add_argument('--burn-in', type=int, default=365)
    parser.add_argument('--universe', default='liquid',
                        choices=['all', 'filtered', 'liquid'])
    parser.add_argument('--tokens', nargs='+', help='Specific tokens')
    parser.add_argument('--market', default='perp', choices=['spot', 'perp'])
    parser.add_argument('--exchange', default='binance')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--no-regime', action='store_true')
    parser.add_argument('--sweep', action='store_true')
    parser.add_argument('--analyze', action='store_true',
                        help='Show pair candidates (no trading)')
    args = parser.parse_args()

    # Resolve tokens — use spot universe to determine which tokens to look for in perp
    if args.tokens:
        tokens = args.tokens
    else:
        tokens = resolve_universe(args.universe, market='spot', verbose=True)
    print(f"Universe: {len(tokens)} tokens ({args.universe if not args.tokens else 'custom'})")

    config = PairsConfig(
        capital=args.capital,
        lookback_days=args.lookback[0],
        rebalance_days=args.rebalance[0],
        zscore_window=args.zscore_window,
        max_pairs=args.max_pairs,
        entry_z=args.entry_z[0],
        exit_z=args.exit_z,
        stop_z=args.stop_z,
        max_hold_days=args.max_hold,
        min_correlation=args.min_corr,
        max_correlation=args.max_corr,
        min_half_life=args.min_half_life,
        max_half_life=args.max_half_life,
        same_sector_only=args.same_sector,
        burn_in_days=args.burn_in,
        min_adv_usd=args.min_adv,
        regime_filter=not args.no_regime,
        market=args.market,
        data_dir='data',
        exchange=args.exchange,
        workers=args.workers,
    )

    if args.analyze:
        analyze_pairs(tokens, config)
    elif args.sweep or len(args.entry_z) > 1 or len(args.lookback) > 1 or len(args.rebalance) > 1:
        run_parameter_sweep(
            tokens,
            entry_z_list=args.entry_z,
            lookback_list=args.lookback if len(args.lookback) > 1 else None,
            rebalance_list=args.rebalance if len(args.rebalance) > 1 else None,
            base_config=config,
        )
    else:
        run_pairs_backtest(tokens, config)


if __name__ == '__main__':
    main()
