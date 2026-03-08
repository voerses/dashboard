"""
V3 Portfolio-Level Backtest — Trade-Level Simulation
=====================================================

Simulates a portfolio with a SHARED CASH POOL across all tokens.
Each trade checks capital availability before opening. When cash is
insufficient, the trade is skipped. This models the real constraint:
you can't open positions you can't afford.

Usage:
    python v3/portfolio.py --strategy s11 --workers 4
    python v3/portfolio.py --strategy s11 --capital 100000 --max-weight 0.10
    python v3/portfolio.py --result results/validation_s11_*.json

How it works:
    1. Run each token through the engine (walk-forward masked) to get trades
    2. Pool ALL trades across tokens, sorted chronologically
    3. Process events: check cash on entry, lock capital, release on exit
    4. Build portfolio equity = cash + locked capital (marked at cost)
    5. Compute portfolio-level metrics on the aggregate curve
"""

import sys
import os
import time
import json
import argparse
import importlib.util
import numpy as np
import pandas as pd
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Tuple
from datetime import datetime

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
_metrics_mod = _load_v3('metrics')
_universe_mod = _load_v3('universe')

Engine = _engine_mod.Engine
build_equity_curve = _metrics_mod.build_equity_curve
resolve_universe = _universe_mod.resolve_universe


# =============================================================================
# Configuration
# =============================================================================

@dataclass
class PortfolioConfig:
    capital: float = 200_000
    max_token_pct: float = 0.15       # Max fraction of capital in any single token
    min_position_usd: float = 1_000   # Skip trades smaller than this
    data_dir: str = 'data'
    market: str = 'spot'
    workers: int = 4
    exchange: str = 'binance'         # Fee structure to use (binance, kraken, hyperliquid)
    mtm: bool = True                  # Mark-to-market equity (vs mark-at-cost)
    dynamic_concentration: bool = True # Concentration cap scales with current equity


# =============================================================================
# Trade Extraction (Walk-Forward Masked)
# =============================================================================

def _is_combined_strategy(strategy_fn) -> bool:
    """Check if a strategy function expects two arguments (combined signature)."""
    import inspect
    try:
        sig = inspect.signature(strategy_fn)
        n_required = len([p for p in sig.parameters.values()
                         if p.default is inspect.Parameter.empty])
        return n_required >= 2
    except (ValueError, TypeError):
        return False


def _get_token_trades(ticker, strategy_path, data_dir, market, capital, exchange='binance'):
    """Get timestamped trades for a single token via walk-forward simulation.

    Returns list of trade dicts with added fields:
        - token: ticker name
        - entry_time: datetime of entry
        - exit_time: datetime of exit
    Returns None if token has no valid trades.

    Supports combined (spot+perp) strategies that take two contexts.
    """
    v3_dir = os.path.dirname(os.path.abspath(__file__))
    if v3_dir not in sys.path:
        sys.path.insert(0, v3_dir)
    # Ensure project root is on path so wrapper strategies can import base strategies
    project_root = os.path.dirname(v3_dir)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    _eng = _load_v3('engine')
    EngineLocal = _eng.Engine

    # Load strategy
    spec = importlib.util.spec_from_file_location('strat', strategy_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    strategy_fn = mod.strategy

    is_combined = market == 'combined' and _is_combined_strategy(strategy_fn)

    if is_combined:
        return _get_token_trades_combined(
            ticker, strategy_fn, data_dir, capital, exchange)
    else:
        return _get_token_trades_single(
            ticker, strategy_fn, data_dir, market, capital, exchange)


def _get_token_trades_single(ticker, strategy_fn, data_dir, market, capital, exchange):
    """Single-market trade extraction (original path)."""
    _eng = _load_v3('engine')
    EngineLocal = _eng.Engine

    effective_market = market if market != 'combined' else 'spot'
    h1_path = os.path.join(data_dir, effective_market, f'1h_cache/{ticker}_1h.parquet')
    if not os.path.exists(h1_path):
        return None

    df_1h = pd.read_parquet(h1_path)
    if len(df_1h) < 2000:
        return None

    engine = EngineLocal(data_dir=data_dir, market=effective_market, capital=capital, exchange=exchange)
    ctx = engine._build_context(ticker, df_1h)
    if ctx is None:
        return None

    result = strategy_fn(ctx)
    n = len(ctx.ind_1h['close'])

    # Apply walk-forward masking (same as validation.py)
    train_bars = 365 * 24
    recal_bars = 90 * 24
    purge_bars = 5 * 24

    if n <= train_bars + purge_bars:
        return None

    masked_entry = result.entry_mask.copy()
    masked_entry[:train_bars] = False

    recal_point = train_bars
    while recal_point < n:
        purge_end = min(recal_point + purge_bars, n)
        masked_entry[recal_point:purge_end] = False
        recal_point += recal_bars

    result.entry_mask = masked_entry
    trades, final_equity = engine._simulate(ctx, result)

    if not trades:
        return None

    idx = ctx.idx_1h
    for t in trades:
        eb = t['entry_bar']
        xb = t['exit_bar']
        t['token'] = ticker
        t['entry_time'] = idx[min(eb, len(idx) - 1)]
        t['exit_time'] = idx[min(xb, len(idx) - 1)]

    return trades


def _get_token_trades_combined(ticker, strategy_fn, data_dir, capital, exchange):
    """Combined (spot+perp) trade extraction for dual-leg strategies."""
    _eng = _load_v3('engine')
    EngineLocal = _eng.Engine

    # Load both spot and perp data
    spot_path = os.path.join(data_dir, 'spot', f'1h_cache/{ticker}_1h.parquet')
    perp_path = os.path.join(data_dir, 'perp', f'1h_cache/{ticker}_1h.parquet')
    if not os.path.exists(spot_path) or not os.path.exists(perp_path):
        return None

    df_spot = pd.read_parquet(spot_path)
    df_perp = pd.read_parquet(perp_path)
    if len(df_spot) < 2000 or len(df_perp) < 2000:
        return None

    # Align to common time range
    common_start = max(df_spot.index.min(), df_perp.index.min())
    common_end = min(df_spot.index.max(), df_perp.index.max())
    df_spot = df_spot.loc[common_start:common_end]
    df_perp = df_perp.loc[common_start:common_end]
    if len(df_spot) < 2000 or len(df_perp) < 2000:
        return None

    engine_spot = EngineLocal(data_dir=data_dir, market='spot', capital=capital, exchange=exchange)
    engine_perp = EngineLocal(data_dir=data_dir, market='perp', capital=capital, exchange=exchange)

    ctx_spot = engine_spot._build_context(ticker, df_spot)
    ctx_perp = engine_perp._build_context(ticker, df_perp)
    if ctx_spot is None or ctx_perp is None:
        return None

    result = strategy_fn(ctx_spot, ctx_perp)
    n = min(len(ctx_spot.ind_1h['close']), len(ctx_perp.ind_1h['close']))

    # Apply walk-forward masking to both legs
    train_bars = 365 * 24
    recal_bars = 90 * 24
    purge_bars = 5 * 24

    if n <= train_bars + purge_bars:
        return None

    # Mask primary leg
    masked_entry = result.entry_mask.copy()
    masked_entry[:train_bars] = False
    recal_point = train_bars
    while recal_point < n:
        purge_end = min(recal_point + purge_bars, n)
        masked_entry[recal_point:purge_end] = False
        recal_point += recal_bars
    result.entry_mask = masked_entry

    # Mask secondary leg
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

    # Add timestamps and token name (use spot index as reference)
    idx = ctx_spot.idx_1h
    for t in trades:
        eb = t['entry_bar']
        xb = t['exit_bar']
        t['token'] = ticker
        t['entry_time'] = idx[min(eb, len(idx) - 1)]
        t['exit_time'] = idx[min(xb, len(idx) - 1)]

    return trades


def extract_token_trades(
    strategy_path: str,
    tokens: List[str],
    config: PortfolioConfig,
) -> Dict[str, List[Dict]]:
    """Extract timestamped trades for all tokens (parallel).

    Returns {token: [trade_dicts]} for tokens that produced trades.
    """
    from concurrent.futures import ProcessPoolExecutor, as_completed

    strategy_path = os.path.abspath(strategy_path)
    all_trades = {}

    if config.workers > 1 and len(tokens) > 1:
        with ProcessPoolExecutor(max_workers=config.workers) as executor:
            futures = {}
            for tk in tokens:
                f = executor.submit(
                    _get_token_trades, tk, strategy_path,
                    config.data_dir, config.market, config.capital, config.exchange)
                futures[f] = tk

            for future in as_completed(futures):
                tk = futures[future]
                try:
                    trades = future.result()
                    if trades:
                        all_trades[tk] = trades
                except Exception:
                    pass
    else:
        for tk in tokens:
            try:
                trades = _get_token_trades(
                    tk, strategy_path, config.data_dir, config.market, config.capital,
                    config.exchange)
                if trades:
                    all_trades[tk] = trades
            except Exception:
                pass

    return all_trades


# =============================================================================
# Core Portfolio Simulation (Shared Cash Pool)
# =============================================================================

def simulate_portfolio(
    all_token_trades: Dict[str, List[Dict]],
    capital: float = 200_000,
    max_token_pct: float = 0.15,
    min_position_usd: float = 1_000,
    mtm: bool = True,
    dynamic_concentration: bool = True,
    fee_rate: float = 0.0022,
) -> Tuple[pd.Series, List[Dict], List[Dict], Dict]:
    """Trade-level portfolio simulation with shared cash pool.

    Processes all trades across all tokens chronologically. For each entry,
    checks if the cash pool has enough capital. Skips trades when capital is
    insufficient or token concentration exceeds the cap.

    Equity modes:
        mtm=True:  Mark-to-market — unrealized PnL interpolated pro-rata over hold period
        mtm=False: Mark-at-cost — positions valued at entry cost until exit (conservative)

    Concentration modes:
        dynamic_concentration=True:  Cap scales with current equity (cash + locked)
        dynamic_concentration=False: Cap fixed at initial capital * max_token_pct

    Args:
        all_token_trades: {token: [trade_dicts with entry_time, exit_time]}
        capital: Total starting capital
        max_token_pct: Max fraction of capital/equity exposed to any single token
        min_position_usd: Minimum position size to accept
        mtm: Mark-to-market equity (default True)
        dynamic_concentration: Scale concentration cap with equity (default True)

    Returns:
        portfolio_equity: Daily equity series
        accepted_trades: Trades that were executed
        skipped_trades: Trades rejected due to capital/concentration constraints
        info: Simulation diagnostics
    """
    # Build event list: (time, event_type, token, trade)
    # event_type: 0=EXIT, 1=ENTRY (exits processed first at same timestamp)
    events = []
    backtest_start = None
    backtest_end = None
    for token, trades in all_token_trades.items():
        for t in trades:
            events.append((t['exit_time'], 0, token, t))   # EXIT
            events.append((t['entry_time'], 1, token, t))  # ENTRY
            # Track the full date range for equity curve (B4 fix)
            if backtest_start is None or t['entry_time'] < backtest_start:
                backtest_start = t['entry_time']
            if backtest_end is None or t['exit_time'] > backtest_end:
                backtest_end = t['exit_time']
    events.sort(key=lambda e: (e[0], e[1]))  # time, then exits before entries

    # State
    cash = float(capital)
    open_positions = {}  # id -> {token, locked, pnl, exit_time, entry_time}
    next_id = 0
    accepted = []
    skipped = []
    skip_reasons = {'no_cash': 0, 'concentration': 0, 'too_small': 0}

    # Track daily base equity snapshots (cash + locked, WITHOUT mtm)
    base_snapshots = {}  # date -> cash + total_locked (no MTM)
    peak_concurrent = 0
    peak_exposure = 0.0
    peak_exposure_equity = 0.0  # equity at time of peak exposure
    total_locked = 0.0  # Running sum of locked capital (avoids O(n) scans)
    token_exposure_map = {}  # token -> total locked capital (O(1) lookup)

    fixed_max_token_exposure = capital * max_token_pct
    total_entry_fees = 0.0

    def _current_equity():
        """Current portfolio equity (cash + locked capital)."""
        return cash + total_locked

    def _max_token_exposure():
        """Concentration cap — dynamic (scales with equity) or fixed."""
        if dynamic_concentration:
            return _current_equity() * max_token_pct
        return fixed_max_token_exposure

    def _snapshot(ts):
        date = ts.normalize()
        base_snapshots[date] = cash + total_locked

    for timestamp, event_type, token, trade in events:
        if event_type == 0:  # EXIT
            # Find matching open position for this token+exit_time
            matched_id = None
            for pid, pos in open_positions.items():
                if pos['token'] == token and pos['exit_time'] == timestamp:
                    matched_id = pid
                    break
            if matched_id is not None:
                pos = open_positions.pop(matched_id)
                cash += pos['locked'] + pos['pnl']
                total_locked -= pos['locked']
                token_exposure_map[pos['token']] = token_exposure_map.get(pos['token'], 0) - pos['locked']
            _snapshot(timestamp)

        else:  # ENTRY
            pos_usd = trade['position_usd']

            # Check 1: minimum position size
            if pos_usd < min_position_usd:
                skipped.append(trade)
                skip_reasons['too_small'] += 1
                continue

            # Check 2: concentration cap (O(1) via maintained map)
            max_exposure = _max_token_exposure()
            current_exposure = token_exposure_map.get(token, 0)
            if current_exposure + pos_usd > max_exposure:
                # Try to scale down to fit
                available_for_token = max(max_exposure - current_exposure, 0)
                if available_for_token < min_position_usd:
                    skipped.append(trade)
                    skip_reasons['concentration'] += 1
                    continue
                scale = available_for_token / pos_usd
                pos_usd = available_for_token
            else:
                scale = 1.0

            # Check 3: cash availability
            if cash < pos_usd:
                # Try to scale down to what we can afford
                if cash >= min_position_usd:
                    scale = min(scale, cash / trade['position_usd'])
                    pos_usd = cash
                else:
                    skipped.append(trade)
                    skip_reasons['no_cash'] += 1
                    continue

            # Accept the trade — deduct entry fee from cash
            entry_fee = pos_usd * fee_rate
            cash -= pos_usd + entry_fee
            total_entry_fees += entry_fee
            total_locked += pos_usd
            token_exposure_map[token] = token_exposure_map.get(token, 0) + pos_usd
            scaled_pnl = trade['pnl'] * scale
            open_positions[next_id] = {
                'token': token,
                'locked': pos_usd,
                'pnl': scaled_pnl,
                'exit_time': trade['exit_time'],
                'entry_time': trade['entry_time'],
                'scale': scale,
            }
            accepted_trade = dict(trade)
            accepted_trade['portfolio_scale'] = scale
            accepted_trade['portfolio_position_usd'] = pos_usd
            accepted.append(accepted_trade)
            next_id += 1

            # Track peaks
            n_open = len(open_positions)
            if n_open > peak_concurrent:
                peak_concurrent = n_open
            if total_locked > peak_exposure:
                peak_exposure = total_locked
                peak_exposure_equity = _current_equity()

            _snapshot(timestamp)

    # Build daily equity series for full backtest range (B4 + B5 fix)
    if backtest_start is None:
        return pd.Series(dtype=float), accepted, skipped, {}

    # B4: equity starts from backtest_start with initial capital
    # B5: daily MTM computed for every day, not just event days
    full_idx = pd.date_range(
        backtest_start.normalize(),
        (backtest_end if backtest_end is not None else backtest_start).normalize(),
        freq='D',
    )

    # Base equity (cash+locked) from event snapshots, ffilled between events
    base_snapshots[full_idx[0]] = base_snapshots.get(full_idx[0], capital)
    base_eq = pd.Series(base_snapshots).sort_index()
    base_eq = base_eq.reindex(full_idx).ffill().fillna(capital)

    # Daily MTM overlay: sum interpolated unrealized PnL for all open positions each day
    if mtm and accepted:
        daily_mtm = np.zeros(len(full_idx))
        full_idx_arr = full_idx.values  # numpy datetime64 for fast comparison
        for at in accepted:
            entry_t = at['entry_time']
            exit_t = at['exit_time']
            total_secs = (exit_t - entry_t).total_seconds()
            if total_secs <= 0:
                continue
            scale = at.get('portfolio_scale', 1.0)
            net_pnl = at['pnl'] * scale
            entry_np = np.datetime64(entry_t)
            exit_np = np.datetime64(exit_t)
            # Vectorized: find days where this trade is open
            mask = (full_idx_arr >= entry_np) & (full_idx_arr < exit_np)
            if not mask.any():
                continue
            days_open = full_idx_arr[mask]
            elapsed_secs = (days_open - entry_np) / np.timedelta64(1, 's')
            fracs = np.minimum(elapsed_secs / total_secs, 1.0)
            daily_mtm[mask] += net_pnl * fracs
        eq = base_eq.values + daily_mtm
        eq = pd.Series(eq, index=full_idx)
    else:
        eq = base_eq

    total_trades = len(accepted) + len(skipped)
    info = {
        'n_tokens': len(all_token_trades),
        'total_trades_available': total_trades,
        'n_accepted': len(accepted),
        'n_skipped': len(skipped),
        'skip_rate_pct': len(skipped) / max(total_trades, 1) * 100,
        'skip_reasons': skip_reasons,
        'peak_concurrent_positions': peak_concurrent,
        'peak_exposure_usd': peak_exposure,
        'peak_exposure_pct': peak_exposure / max(peak_exposure_equity, 1) * 100,
        'capital_utilization_pct': peak_exposure / capital * 100,
        'final_equity': float(eq.iloc[-1]),
        'final_cash': cash,
        'total_entry_fees': total_entry_fees,
        'mtm_mode': mtm,
        'dynamic_concentration': dynamic_concentration,
    }

    return eq, accepted, skipped, info


# =============================================================================
# Portfolio Metrics
# =============================================================================

@dataclass
class PortfolioMetrics:
    # Returns
    total_return_pct: float = 0.0
    annualized_return_pct: float = 0.0

    # Risk-adjusted
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0

    # Drawdown
    max_drawdown_pct: float = 0.0
    max_drawdown_duration_days: int = 0

    # Trade quality (portfolio level)
    total_trades: int = 0
    accepted_trades: int = 0
    skipped_trades: int = 0
    skip_rate_pct: float = 0.0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    avg_trade_pnl: float = 0.0

    # Capital utilization
    peak_concurrent_positions: int = 0
    peak_exposure_pct: float = 0.0       # % of equity at peak moment
    capital_utilization_pct: float = 0.0  # % of initial capital (shows compounding)

    # Diversification (from accepted trades)
    n_tokens_traded: int = 0
    median_pairwise_corr: float = 0.0
    hhi_exposure: float = 0.0

    # Per-token attribution
    top_contributors: str = ''
    worst_contributors: str = ''


def compute_portfolio_metrics(
    portfolio_equity: pd.Series,
    accepted_trades: List[Dict],
    skipped_trades: List[Dict],
    info: Dict,
) -> PortfolioMetrics:
    """Compute portfolio-level metrics from equity curve and trade data."""
    m = PortfolioMetrics()

    if len(portfolio_equity) < 2:
        return m

    # --- Returns ---
    total_return = (portfolio_equity.iloc[-1] / portfolio_equity.iloc[0]) - 1.0
    m.total_return_pct = total_return * 100

    n_days = len(portfolio_equity)
    years = max(n_days / 365.0, 0.01)
    m.annualized_return_pct = ((1 + total_return) ** (1.0 / years) - 1) * 100

    daily_returns = portfolio_equity.pct_change().dropna()
    daily_returns = daily_returns.replace([np.inf, -np.inf], 0).fillna(0)
    mean_daily = daily_returns.mean()
    std_daily = daily_returns.std()

    # --- Sharpe ---
    if std_daily > 1e-10:
        m.sharpe_ratio = (mean_daily / std_daily) * np.sqrt(365)

    # --- Sortino ---
    neg_returns = daily_returns[daily_returns < 0]
    if len(neg_returns) > 0:
        downside_std = np.sqrt(np.mean(neg_returns ** 2))
        if downside_std > 1e-10:
            m.sortino_ratio = (mean_daily / downside_std) * np.sqrt(365)

    # --- Drawdown ---
    cummax = portfolio_equity.cummax()
    drawdown = (portfolio_equity - cummax) / cummax
    m.max_drawdown_pct = float(drawdown.min()) * 100

    underwater = drawdown < 0
    if underwater.any():
        groups = (~underwater).cumsum()
        underwater_groups = underwater.groupby(groups).sum()
        m.max_drawdown_duration_days = int(underwater_groups.max())

    # --- Calmar ---
    if abs(m.max_drawdown_pct) > 0.01:
        m.calmar_ratio = m.annualized_return_pct / abs(m.max_drawdown_pct)

    # --- Trade quality (portfolio-level) ---
    m.total_trades = len(accepted_trades) + len(skipped_trades)
    m.accepted_trades = len(accepted_trades)
    m.skipped_trades = len(skipped_trades)
    m.skip_rate_pct = info.get('skip_rate_pct', 0)

    if accepted_trades:
        wins = [t for t in accepted_trades if t['pnl'] * t.get('portfolio_scale', 1.0) > 0]
        losers = [t for t in accepted_trades if t['pnl'] * t.get('portfolio_scale', 1.0) <= 0]
        m.win_rate_pct = len(wins) / len(accepted_trades) * 100

        scaled_pnls = [t['pnl'] * t.get('portfolio_scale', 1.0) for t in accepted_trades]
        gross_profit = sum(p for p in scaled_pnls if p > 0)
        gross_loss = abs(sum(p for p in scaled_pnls if p <= 0))
        m.profit_factor = gross_profit / max(gross_loss, 1)
        m.avg_trade_pnl = np.mean(scaled_pnls)

    # --- Capital utilization ---
    m.peak_concurrent_positions = info.get('peak_concurrent_positions', 0)
    m.peak_exposure_pct = info.get('peak_exposure_pct', 0)
    m.capital_utilization_pct = info.get('capital_utilization_pct', 0)

    # --- Diversification (from accepted trades) ---
    if accepted_trades:
        tokens_traded = set(t['token'] for t in accepted_trades)
        m.n_tokens_traded = len(tokens_traded)

        # HHI of capital exposure per token
        token_exposure = {}
        for t in accepted_trades:
            tk = t['token']
            token_exposure[tk] = token_exposure.get(tk, 0) + t.get('portfolio_position_usd', t['position_usd'])
        total_exp = sum(token_exposure.values())
        if total_exp > 0:
            shares = np.array([v / total_exp for v in token_exposure.values()])
            m.hhi_exposure = float(np.sum(shares ** 2))

        # Per-token PnL attribution
        token_pnl = {}
        for t in accepted_trades:
            tk = t['token']
            scaled_pnl = t['pnl'] * t.get('portfolio_scale', 1.0)
            token_pnl[tk] = token_pnl.get(tk, 0) + scaled_pnl

        sorted_pnl = sorted(token_pnl.items(), key=lambda x: -x[1])
        top3 = sorted_pnl[:3]
        worst3 = sorted_pnl[-3:]
        m.top_contributors = ', '.join(f'{tk}(${v:+,.0f})' for tk, v in top3)
        m.worst_contributors = ', '.join(f'{tk}(${v:+,.0f})' for tk, v in worst3)

    return m


# =============================================================================
# Portfolio Report
# =============================================================================

def print_portfolio_report(
    metrics: PortfolioMetrics,
    info: Dict,
    config: PortfolioConfig,
    strategy_name: str,
):
    """Print formatted portfolio report."""
    print(f"\n{'='*80}")
    print(f"PORTFOLIO BACKTEST: {strategy_name}")
    fee_rate = _universe_mod.get_fee_rate(config.exchange, config.market, 'taker')
    print(f"  Capital: ${config.capital:,.0f} | Max token: {config.max_token_pct*100:.0f}% | "
          f"Market: {config.market} | Exchange: {config.exchange}")
    print(f"  Fee: {fee_rate*100:.2f}% per side ({fee_rate*2*100:.2f}% round-trip)")
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

    print(f"\nTrade Execution:")
    print(f"  Total signals:    {metrics.total_trades}")
    print(f"  Accepted:         {metrics.accepted_trades}")
    print(f"  Skipped:          {metrics.skipped_trades} ({metrics.skip_rate_pct:.1f}%)")
    skip_reasons = info.get('skip_reasons', {})
    if skip_reasons:
        print(f"    No cash:        {skip_reasons.get('no_cash', 0)}")
        print(f"    Concentration:  {skip_reasons.get('concentration', 0)}")
        print(f"    Too small:      {skip_reasons.get('too_small', 0)}")
    print(f"  Win rate:         {metrics.win_rate_pct:.1f}%")
    print(f"  Profit factor:    {metrics.profit_factor:.2f}")
    print(f"  Avg trade PnL:    ${metrics.avg_trade_pnl:,.0f}")

    print(f"\nCapital Utilization:")
    print(f"  Peak concurrent:  {metrics.peak_concurrent_positions} positions")
    print(f"  Peak exposure:    {metrics.peak_exposure_pct:.1f}% of equity (at that moment)")
    print(f"  Capital util:     {metrics.capital_utilization_pct:.1f}% of initial capital")

    print(f"\nDiversification:")
    print(f"  Tokens traded:    {metrics.n_tokens_traded}")
    print(f"  HHI (exposure):   {metrics.hhi_exposure:.4f}")

    print(f"\nAttribution:")
    print(f"  Top contributors:   {metrics.top_contributors}")
    print(f"  Worst contributors: {metrics.worst_contributors}")

    if info.get('mtm_mode', False):
        print(f"\nNote: Mark-to-market equity (unrealized PnL interpolated pro-rata).")
        if info.get('dynamic_concentration', False):
            print(f"      Concentration cap scales with current equity.")
    else:
        print(f"\nNote: Positions marked at cost (conservative). Unrealized P&L not reflected")
        print(f"      in daily equity. P&L realized at trade exit only.")
    print()


# =============================================================================
# Main Pipeline
# =============================================================================

def run_portfolio_backtest(
    strategy_path: str,
    tokens: Optional[List[str]] = None,
    config: Optional[PortfolioConfig] = None,
    validated_tokens: Optional[List[str]] = None,
) -> Dict:
    """Run full portfolio backtest pipeline.

    Steps:
        1. Extract trades per token (walk-forward masked)
        2. Simulate portfolio with shared cash pool
        3. Compute metrics and report

    Returns:
        Dict with portfolio_equity, metrics, accepted_trades, skipped_trades, info
    """
    if config is None:
        config = PortfolioConfig()

    strategy_path = os.path.abspath(strategy_path)
    strategy_name = os.path.splitext(os.path.basename(strategy_path))[0]

    # Determine token list — default is ALL available tokens
    if tokens is not None:
        target_tokens = tokens
    elif validated_tokens is not None:
        target_tokens = validated_tokens
    else:
        target_tokens = _universe_mod.get_all_tradeable(config.market)
        print(f"Using full {config.market} universe: {len(target_tokens)} tokens")

    if not target_tokens:
        print("No tokens to process.")
        return {}

    print(f"\nPortfolio backtest: {strategy_name}")
    print(f"  Tokens: {len(target_tokens)} | Capital: ${config.capital:,.0f} | "
          f"Max per token: {config.max_token_pct*100:.0f}%")

    # Step 1: Extract trades per token
    t0 = time.time()
    print(f"  Extracting trades...", end='', flush=True)
    token_trades = extract_token_trades(strategy_path, target_tokens, config)
    total_trades = sum(len(t) for t in token_trades.values())
    print(f" {len(token_trades)} tokens, {total_trades} trades ({time.time()-t0:.1f}s)")

    if len(token_trades) < 1:
        print("  No tokens produced trades.")
        return {}

    # Step 2: Simulate portfolio
    print(f"  Simulating portfolio...", end='', flush=True)
    portfolio_eq, accepted, skipped_list, sim_info = simulate_portfolio(
        token_trades, config.capital, config.max_token_pct, config.min_position_usd,
        mtm=config.mtm, dynamic_concentration=config.dynamic_concentration,
        fee_rate=_universe_mod.get_fee_rate(config.exchange, config.market, 'taker'))
    print(f" {len(accepted)} accepted, {len(skipped_list)} skipped ({time.time()-t0:.1f}s)")

    if len(portfolio_eq) < 2:
        print("  Insufficient data for portfolio equity.")
        return {}

    # Step 3: Compute metrics
    metrics = compute_portfolio_metrics(portfolio_eq, accepted, skipped_list, sim_info)

    # Step 4: Report
    print_portfolio_report(metrics, sim_info, config, strategy_name)

    # Step 5: Save results
    result_data = {
        'strategy': strategy_name,
        'timestamp': datetime.now().isoformat(),
        'config': {
            'capital': config.capital,
            'max_token_pct': config.max_token_pct,
            'min_position_usd': config.min_position_usd,
            'market': config.market,
            'exchange': config.exchange,
            'mtm': config.mtm,
            'dynamic_concentration': config.dynamic_concentration,
        },
        'simulation': sim_info,
        'metrics': asdict(metrics),
        'tokens_traded': sorted(set(t['token'] for t in accepted)),
        'n_tokens': len(token_trades),
    }

    results_dir = os.path.join(os.path.dirname(_v3_dir), 'results')
    os.makedirs(results_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_path = os.path.join(results_dir, f'portfolio_{strategy_name}_{ts}.json')
    with open(out_path, 'w') as f:
        json.dump(result_data, f, indent=2, default=str)
    print(f"  Results saved to {out_path}")

    return {
        'portfolio_equity': portfolio_eq,
        'accepted_trades': accepted,
        'skipped_trades': skipped_list,
        'metrics': metrics,
        'info': sim_info,
    }


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description='V3 Portfolio Backtest (Trade-Level)')
    parser.add_argument('--strategy', required=True, help='Strategy name (e.g., s11)')
    parser.add_argument('--result', help='Path to validation result JSON (uses validated tokens)')
    parser.add_argument('--tokens', nargs='+', help='Specific tokens to include (overrides --universe)')
    parser.add_argument('--universe', choices=['all', 'filtered', 'liquid'],
                        default='filtered',
                        help='Token universe: all=every token with data, '
                             'filtered=quality-gated (default), liquid=filtered+ADV gate')
    parser.add_argument('--capital', type=float, default=200_000, help='Total capital (default: 200000)')
    parser.add_argument('--max-weight', type=float, default=0.15,
                        help='Max fraction per token (default: 0.15)')
    parser.add_argument('--market', default='spot', choices=['spot', 'perp'],
                        help='Market type')
    parser.add_argument('--exchange', default='binance',
                        choices=['binance', 'kraken', 'hyperliquid'],
                        help='Exchange fee structure (default: binance)')
    parser.add_argument('--workers', type=int, default=4, help='Parallel workers')
    parser.add_argument('--no-mtm', action='store_true', help='Disable mark-to-market (use mark-at-cost)')
    parser.add_argument('--no-dynamic-conc', action='store_true',
                        help='Disable dynamic concentration cap (use fixed initial capital)')

    args = parser.parse_args()

    # Resolve strategy path
    strat_dir = os.path.join(os.path.dirname(_v3_dir), 'strategies')
    candidates = [
        os.path.join(strat_dir, f'{args.strategy}.py'),
        os.path.join(strat_dir, f'{args.strategy}_*.py'),
        args.strategy,
    ]

    strategy_path = None
    for c in candidates:
        import glob
        matches = glob.glob(c)
        if matches:
            strategy_path = matches[0]
            break

    if strategy_path is None or not os.path.exists(strategy_path):
        print(f"Strategy not found: {args.strategy}")
        sys.exit(1)

    print(f"Strategy: {args.strategy} -> {strategy_path}")

    # Get validated tokens
    validated_tokens = None
    if args.result:
        with open(args.result) as f:
            val_data = json.load(f)
        validated_tokens = val_data.get('validated_tokens', [])
        if not validated_tokens:
            per_token = val_data.get('per_token', {})
            validated_tokens = [tk for tk, v in per_token.items()
                                if v.get('validated') in [True, 'True']]
        print(f"Using {len(validated_tokens)} validated tokens from {args.result}")
    elif args.tokens:
        validated_tokens = args.tokens
    else:
        # Neither --result nor --tokens: use --universe to resolve token list
        validated_tokens = resolve_universe(args.universe, market=args.market, verbose=True)
        print(f"Universe '{args.universe}': {len(validated_tokens)} tokens")

    config = PortfolioConfig(
        capital=args.capital,
        max_token_pct=args.max_weight,
        data_dir='data',
        market=args.market,
        exchange=args.exchange,
        workers=args.workers,
        mtm=not args.no_mtm,
        dynamic_concentration=not args.no_dynamic_conc,
    )

    run_portfolio_backtest(
        strategy_path, config=config,
        validated_tokens=validated_tokens)


if __name__ == '__main__':
    os.chdir(os.path.dirname(_v3_dir) or '.')
    main()
