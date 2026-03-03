"""
V3 Sector / Narrative Rotation — Sector-Level Momentum Strategy
================================================================

Groups tokens into thematic sectors (L1, DeFi, Meme, AI, etc.) and
rotates capital into top-performing sectors. Structurally different
from both per-token time-series strategies AND cross-sectional
individual-token momentum.

Two modes:
  1. Sector-level ranking: rank sectors by average trailing return,
     go long all eligible tokens in top K sectors (equal weight)
  2. Hybrid: rank sectors first, then rank tokens within selected
     sectors by individual trailing return (top N per sector)

Academically supported: sector/narrative rotation captures thematic
momentum (e.g., "AI season", "meme season") that persists for weeks
to months in crypto.

Usage:
    python v3/sector_rotation.py --lookback 14 --workers 4
    python v3/sector_rotation.py --lookback 7 14 30 --sweep
    python v3/sector_rotation.py --top-sectors 3 --mode hybrid
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
# Import reusable V3 modules (same pattern as cross_sectional.py)
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

# Also reuse cross_sectional data loading & panel building
_xsec_mod = _load_v3('cross_sectional')
load_universe_data = _xsec_mod.load_universe_data
build_daily_panel = _xsec_mod.build_daily_panel
compute_trailing_returns = _xsec_mod.compute_trailing_returns
compute_eligibility = _xsec_mod.compute_eligibility
_compute_slippage_bps = _xsec_mod._compute_slippage_bps


# ---------------------------------------------------------------------------
# Sector Classification — All 116 tokens mapped to 10 sectors
# ---------------------------------------------------------------------------

SECTOR_MAP = {
    # L1 — Layer 1 blockchains
    'L1': ['BTC', 'ETH', 'SOL', 'SUI', 'AVAX', 'ADA', 'DOT', 'NEAR', 'APT',
           'ATOM', 'TON', 'SEI', 'BERA', 'BNB', 'ICP', 'ETC', 'TRX', 'HBAR',
           'ALGO', 'VET', 'XTZ', 'ZIL', 'LUNC', 'ASTER'],
    # L2 — Layer 2 / Scaling solutions
    'L2': ['ARB', 'OP', 'STRK', 'IMX', 'POL', 'ZKP', 'LAYER'],
    # DeFi — Decentralized finance protocols
    'DeFi': ['AAVE', 'UNI', 'CRV', 'DYDX', 'LDO', 'PENDLE', 'SNX', 'INJ',
             'JUP', 'ENA', 'ONDO', 'ETHFI', 'MORPHO', 'STG', 'KNC', 'CAKE',
             'ZRO', 'JTO', 'TRU', 'ENSO', 'WLFI', 'EIGEN', 'KAVA', 'OM'],
    # Meme — Meme / community tokens
    'Meme': ['DOGE', 'SHIB', 'PEPE', 'BONK', 'FLOKI', 'WIF', 'NEIRO',
             'TRUMP', 'PENGU', 'BARD', 'GIGGLE', 'PUMP', 'KITE'],
    # Gaming — Gaming, metaverse, NFTs, fan tokens
    'Gaming': ['AXS', 'SAND', 'GALA', 'YGG', 'ALICE', 'AGLD', 'TLM',
               'CHZ', 'APE'],
    # AI — AI, compute, data networks
    'AI': ['FET', 'RENDER', 'TAO', 'AIXBT', 'VIRTUAL', 'WLD', '0G', 'SAHARA'],
    # Infra — Oracles, storage, data, middleware
    'Infra': ['LINK', 'TRB', 'FIL', 'AR', 'STEEM', 'FIO', 'DENT',
              'BIO', 'SIGN', 'FORM', 'ORDI', 'TIA', 'PHA'],
    # Privacy — Privacy-focused chains and protocols
    'Privacy': ['XMR', 'ZEC', 'ZEN', 'DASH', 'DUSK', 'ZAMA', 'LIT', 'SENT'],
    # Payments — Payment networks, stablecoins, exchange tokens
    'Payments': ['XRP', 'XLM', 'LTC', 'BCH', 'PAXG'],
    # Emerging — New / uncategorized tokens
    'Emerging': ['AT', 'BREV', 'MIRA', 'XPL'],
}

# Build reverse map: token -> sector
TOKEN_TO_SECTOR = {}
for sector, tokens in SECTOR_MAP.items():
    for token in tokens:
        TOKEN_TO_SECTOR[token] = sector


def get_token_sector(token: str) -> str:
    """Get sector for a token. Returns 'Emerging' for unknown tokens."""
    return TOKEN_TO_SECTOR.get(token, 'Emerging')


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class SectorRotationConfig:
    capital: float = 200_000
    lookback_days: int = 14        # trailing return window for sector ranking
    rebalance_days: int = 7        # weekly rebalance
    top_sectors: int = 3           # number of sectors to go long
    mode: str = 'sector'           # 'sector' = equal-weight all tokens in top sectors
                                   # 'hybrid' = rank tokens within sectors, pick top N
    max_tokens_per_sector: int = 10  # for hybrid mode: max tokens per sector
    min_sector_tokens: int = 2     # sector needs this many eligible tokens to rank
    burn_in_days: int = 365        # OOS starts after 1yr
    min_adv_usd: float = 500_000   # ADV eligibility gate
    regime_filter: bool = True     # go to cash when BTC regime is DOWNTREND/CRISIS
    data_dir: str = 'data'
    market: str = 'spot'
    exchange: str = 'binance'
    workers: int = 4


# ---------------------------------------------------------------------------
# BTC Regime Filter — go to cash in downtrends
# ---------------------------------------------------------------------------

def compute_btc_regime_daily(close_panel: pd.DataFrame) -> pd.Series:
    """Compute daily BTC regime using EMA crossover + volatility.

    Returns a boolean Series: True = risk-on (ok to invest), False = risk-off (go to cash).

    Rules (same as engine.py detect_daily_regime):
    - Compute 20d and 50d EMA of BTC close
    - Compute 20d rolling volatility
    - RISK-OFF when: EMA20 < EMA50 AND vol is above expanding 75th percentile
      (i.e., strong downtrend with high volatility)
    - Also RISK-OFF when: vol > 2x expanding 75th percentile (CRISIS)
    """
    if 'BTC' not in close_panel.columns:
        # No BTC data — always risk-on
        return pd.Series(True, index=close_panel.index)

    btc = close_panel['BTC'].dropna()
    if len(btc) < 60:
        return pd.Series(True, index=close_panel.index)

    # EMAs
    ema_20 = btc.ewm(span=20, adjust=False).mean()
    ema_50 = btc.ewm(span=50, adjust=False).mean()

    # Daily returns and rolling volatility
    daily_ret = btc.pct_change()
    vol_20 = daily_ret.rolling(20).std()

    # Expanding percentiles (causal — no look-ahead)
    vol_p75 = vol_20.expanding(min_periods=60).quantile(0.75)

    # ADX proxy: use absolute EMA spread as trend strength indicator
    # (computing true ADX requires high/low which we don't have in the panel)
    ema_spread = (ema_20 - ema_50).abs() / ema_50
    trend_strong = ema_spread > 0.02  # 2% spread = strong trend

    # Regime classification
    crisis = vol_20 > vol_p75 * 2
    downtrend = trend_strong & (ema_20 < ema_50) & ~crisis

    # Risk-on = not in downtrend or crisis
    risk_on = ~(downtrend | crisis)
    risk_on[:60] = True  # insufficient data for regime detection

    # Reindex to full panel index
    risk_on = risk_on.reindex(close_panel.index).ffill().fillna(True)

    return risk_on


# ---------------------------------------------------------------------------
# Sector-Level Panel Construction
# ---------------------------------------------------------------------------

def build_sector_returns(close_panel: pd.DataFrame,
                         eligibility: pd.DataFrame,
                         lookback_days: int) -> Tuple[pd.DataFrame, Dict[str, List[str]]]:
    """Compute sector-level trailing returns.

    For each sector, the return is the equal-weighted average of eligible
    token trailing returns. NaN if fewer than 2 eligible tokens.

    Returns:
        sector_returns: DataFrame (dates x sectors) of trailing returns
        sector_tokens: {sector: [tokens_in_panel]} for reference
    """
    # Individual token trailing returns
    token_returns = compute_trailing_returns(close_panel, lookback_days)

    # Map each token in the panel to its sector
    panel_tokens = close_panel.columns.tolist()
    sector_tokens = {}
    for token in panel_tokens:
        sector = get_token_sector(token)
        if sector not in sector_tokens:
            sector_tokens[sector] = []
        sector_tokens[sector].append(token)

    # Compute sector-level returns: mean of eligible token returns per sector
    sector_return_dict = {}
    for sector, tokens in sector_tokens.items():
        if len(tokens) < 2:
            continue  # skip sectors with too few tokens in data

        sector_tok_returns = token_returns[tokens].copy()
        # Mask out ineligible tokens
        if eligibility is not None:
            elig_mask = eligibility[tokens] if all(t in eligibility.columns for t in tokens) else pd.DataFrame(True, index=eligibility.index, columns=tokens)
            sector_tok_returns = sector_tok_returns.where(elig_mask)

        # Count eligible tokens per date
        n_eligible = sector_tok_returns.notna().sum(axis=1)
        # Mean return where we have at least 2 eligible tokens
        avg_return = sector_tok_returns.mean(axis=1)
        avg_return[n_eligible < 2] = np.nan
        sector_return_dict[sector] = avg_return

    sector_returns = pd.DataFrame(sector_return_dict).sort_index()
    return sector_returns, sector_tokens


# ---------------------------------------------------------------------------
# Sector Ranking & Token Selection
# ---------------------------------------------------------------------------

def rank_sectors_and_select(
    sector_returns: pd.DataFrame,
    token_returns: pd.DataFrame,
    eligibility: pd.DataFrame,
    sector_tokens: Dict[str, List[str]],
    rebalance_dates: pd.DatetimeIndex,
    config: SectorRotationConfig,
    risk_on: Optional[pd.Series] = None,
) -> Dict[pd.Timestamp, List[str]]:
    """At each rebalance date, rank sectors by trailing return, select tokens.

    Mode 'sector': Equal-weight all eligible tokens in top K sectors.
    Mode 'hybrid': Within each top sector, rank tokens by individual return,
                   pick top N per sector.

    If risk_on is provided and False at a rebalance date, selects nothing
    (portfolio goes to cash).

    Returns:
        {date: [token_list]} — tokens selected at each rebalance
    """
    selections = {}

    for date in rebalance_dates:
        if date not in sector_returns.index:
            continue

        # Regime filter: go to cash when risk-off
        if risk_on is not None and date in risk_on.index and not risk_on.loc[date]:
            selections[date] = []  # empty = go to cash
            continue

        # Get sector returns at this date
        sec_ret = sector_returns.loc[date].dropna()
        if len(sec_ret) < 1:
            continue

        # Rank sectors (descending by trailing return)
        sec_ret = sec_ret.sort_values(ascending=False)
        top_sectors = sec_ret.index[:config.top_sectors].tolist()

        # Select tokens from top sectors
        selected_tokens = []
        for sector in top_sectors:
            if sector not in sector_tokens:
                continue
            tokens_in_sector = sector_tokens[sector]

            # Filter by eligibility at this date
            eligible = []
            for token in tokens_in_sector:
                if (token in eligibility.columns and
                        date in eligibility.index and
                        eligibility.loc[date, token]):
                    eligible.append(token)

            if len(eligible) < config.min_sector_tokens:
                continue

            if config.mode == 'hybrid' and date in token_returns.index:
                # Rank within sector by individual trailing return
                tok_ret = token_returns.loc[date, eligible].dropna()
                if len(tok_ret) > 0:
                    tok_ret = tok_ret.sort_values(ascending=False)
                    eligible = tok_ret.index[:config.max_tokens_per_sector].tolist()

            selected_tokens.extend(eligible)

        if len(selected_tokens) >= 2:
            selections[date] = selected_tokens

    return selections


# ---------------------------------------------------------------------------
# Simulation (reuse cross_sectional pattern with minor modifications)
# ---------------------------------------------------------------------------

def simulate_sector_rotation(
    close_panel: pd.DataFrame,
    volume_panel: pd.DataFrame,
    selections: Dict[pd.Timestamp, List[str]],
    config: SectorRotationConfig,
) -> Tuple[pd.Series, List[Dict], Dict]:
    """Core sector rotation simulation.

    Between rebalances: equal-weighted portfolio of selected tokens.
    At each rebalance: turnover cost for entering/exiting tokens.

    Returns:
        equity_curve: pd.Series (daily)
        trades: list of trade dicts
        info: simulation metadata
    """
    if not selections:
        return pd.Series(dtype=float), [], {'error': 'no_selections'}

    fee_rate = get_fee_rate(config.exchange, config.market, 'taker')

    rebal_dates = sorted(selections.keys())
    first_rebal = rebal_dates[0]

    daily_returns = close_panel.pct_change()
    all_dates = close_panel.index
    sim_dates = all_dates[all_dates >= first_rebal]

    equity = config.capital
    equity_series = {}
    trades = []
    current_holdings = []
    prev_holdings = []
    total_turnover_cost = 0.0
    total_rebalances = 0

    rolling_adv = volume_panel.rolling(window=30, min_periods=10).median()
    holding_entries = {}  # token -> {'entry_date': date, 'entry_equity': float}

    # Track sector distribution over time
    sector_history = []

    for i, date in enumerate(sim_dates):
        if date in selections:
            new_holdings = selections[date]
            total_rebalances += 1

            prev_set = set(prev_holdings)
            new_set = set(new_holdings)
            entering = new_set - prev_set
            exiting = prev_set - new_set

            # Track which sectors are active
            active_sectors = set(get_token_sector(t) for t in new_holdings)
            sector_history.append({
                'date': date,
                'sectors': sorted(active_sectors),
                'n_tokens': len(new_holdings),
            })

            # Generate trade records for exiting tokens
            for token in exiting:
                if token in holding_entries:
                    entry_info = holding_entries.pop(token)
                    n_held = len(prev_holdings) if prev_holdings else 1
                    position_usd = entry_info['entry_equity'] / n_held
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
                        'sector': get_token_sector(token),
                        'entry_time': entry_info['entry_date'],
                        'exit_time': date,
                        'position_usd': position_usd,
                        'pnl': pnl,
                        'return_pct': ret * 100,
                        'hold_hours': int((date - entry_info['entry_date']).total_seconds() / 3600),
                        'exit_reason': 'rebalance',
                        'strategy': 'sector_rotation',
                        'portfolio_scale': 1.0,
                        'portfolio_position_usd': position_usd,
                    })

            for token in entering:
                holding_entries[token] = {
                    'entry_date': date,
                    'entry_equity': equity,
                }

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

        if current_holdings and date in daily_returns.index:
            day_rets = daily_returns.loc[date, current_holdings].dropna()
            if len(day_rets) > 0:
                portfolio_ret = day_rets.mean()
                equity *= (1 + portfolio_ret)

        equity_series[date] = equity

    # Close remaining holdings
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
                'sector': get_token_sector(token),
                'entry_time': entry_info['entry_date'],
                'exit_time': final_date,
                'position_usd': position_usd,
                'pnl': pnl,
                'return_pct': ret * 100,
                'hold_hours': int((final_date - entry_info['entry_date']).total_seconds() / 3600),
                'exit_reason': 'end_of_data',
                'strategy': 'sector_rotation',
                'portfolio_scale': 1.0,
                'portfolio_position_usd': position_usd,
            })

    equity_curve = pd.Series(equity_series).sort_index()

    # Sector-level PnL attribution
    sector_pnl = {}
    for t in trades:
        sector = t.get('sector', 'Unknown')
        sector_pnl[sector] = sector_pnl.get(sector, 0) + t['pnl']

    info = {
        'total_rebalances': total_rebalances,
        'total_turnover_cost': total_turnover_cost,
        'avg_basket_size': np.mean([len(v) for v in selections.values()]),
        'peak_concurrent_positions': max((len(v) for v in selections.values()), default=0),
        'peak_exposure_pct': 100.0,
        'capital_utilization_pct': 100.0,
        'skip_rate_pct': 0.0,
        'n_rebalance_dates': len(selections),
        'sector_pnl': sector_pnl,
        'sector_history': sector_history[:5] + sector_history[-5:] if len(sector_history) > 10 else sector_history,
    }

    return equity_curve, trades, info


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_sector_rotation_report(metrics: PortfolioMetrics, info: Dict,
                                  config: SectorRotationConfig):
    """Print formatted sector rotation report."""
    fee_rate = get_fee_rate(config.exchange, config.market, 'taker')

    print(f"\n{'='*80}")
    print(f"SECTOR ROTATION BACKTEST ({config.mode.upper()} mode)")
    print(f"  Capital: ${config.capital:,.0f} | Lookback: {config.lookback_days}d | "
          f"Rebalance: {config.rebalance_days}d")
    print(f"  Top sectors: {config.top_sectors} | Min tokens/sector: {config.min_sector_tokens}")
    if config.mode == 'hybrid':
        print(f"  Max tokens/sector: {config.max_tokens_per_sector}")
    print(f"  Market: {config.market} | Exchange: {config.exchange} | "
          f"Fee: {fee_rate*100:.2f}% per side")
    print(f"  Min ADV: ${config.min_adv_usd:,.0f} | Burn-in: {config.burn_in_days}d | "
          f"Regime filter: {'ON' if config.regime_filter else 'OFF'}")
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

    # Sector PnL attribution
    sector_pnl = info.get('sector_pnl', {})
    if sector_pnl:
        print(f"\nSector Attribution:")
        sorted_sectors = sorted(sector_pnl.items(), key=lambda x: -x[1])
        for sector, pnl in sorted_sectors:
            print(f"  {sector:>10s}  ${pnl:>+12,.0f}")

    if metrics.top_contributors:
        print(f"\nToken Attribution:")
        print(f"  Top contributors:   {metrics.top_contributors}")
        print(f"  Worst contributors: {metrics.worst_contributors}")

    # Show sector rotation timeline (first/last few)
    sector_hist = info.get('sector_history', [])
    if sector_hist:
        print(f"\nSector Timeline (sample):")
        for sh in sector_hist[:3]:
            print(f"  {str(sh['date'])[:10]}  {sh['sectors']}  ({sh['n_tokens']} tokens)")
        if len(sector_hist) > 6:
            print(f"  ...")
        for sh in sector_hist[-3:]:
            print(f"  {str(sh['date'])[:10]}  {sh['sectors']}  ({sh['n_tokens']} tokens)")
    print()


# ---------------------------------------------------------------------------
# Full Pipeline
# ---------------------------------------------------------------------------

def run_sector_rotation_backtest(
    tokens: List[str],
    config: Optional[SectorRotationConfig] = None,
    verbose: bool = True,
) -> Dict:
    """Full pipeline: load -> panel -> sector returns -> rank -> simulate -> report."""
    if config is None:
        config = SectorRotationConfig()

    t0 = time.time()

    # 1. Load data
    if verbose:
        print(f"Loading {len(tokens)} tokens from {config.data_dir}/{config.market}/1h_cache/ ...")
    token_data = load_universe_data(tokens, config.data_dir, config.market, config.workers)
    if verbose:
        print(f"  Loaded {len(token_data)} tokens ({time.time()-t0:.1f}s)")

    if len(token_data) < 5:
        print(f"  ERROR: Only {len(token_data)} tokens loaded, need at least 5")
        return {}

    # 2. Build daily panel
    t1 = time.time()
    close_panel, volume_panel = build_daily_panel(token_data)
    if verbose:
        print(f"  Daily panel: {close_panel.shape[0]} days x {close_panel.shape[1]} tokens ({time.time()-t1:.1f}s)")

    # 3. Compute eligibility
    eligibility = compute_eligibility(close_panel, volume_panel,
                                      config.min_adv_usd, config.burn_in_days,
                                      config.lookback_days)

    # 4. Sector-level returns
    sector_returns, sector_tokens = build_sector_returns(
        close_panel, eligibility, config.lookback_days)
    if verbose:
        print(f"  Sectors: {list(sector_returns.columns)}")
        for sec, toks in sorted(sector_tokens.items()):
            print(f"    {sec:>10s}: {len(toks)} tokens")

    # 5. Generate rebalance dates
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

    # 6. Regime filter (optional)
    risk_on = None
    if config.regime_filter:
        risk_on = compute_btc_regime_daily(close_panel)
        oos_risk_off = (~risk_on.loc[oos_start:]).sum()
        oos_total = len(risk_on.loc[oos_start:])
        if verbose:
            print(f"  Regime filter: {oos_risk_off}/{oos_total} days risk-off "
                  f"({oos_risk_off/max(oos_total,1)*100:.0f}%)")

    # 7. Rank sectors and select tokens
    token_returns = compute_trailing_returns(close_panel, config.lookback_days)
    selections = rank_sectors_and_select(
        sector_returns, token_returns, eligibility,
        sector_tokens, rebalance_dates, config,
        risk_on=risk_on)
    if verbose:
        print(f"  Selection dates with valid baskets: {len(selections)}")

    if not selections:
        print("  ERROR: No valid baskets formed. Check eligibility criteria.")
        return {}

    # 7. Simulate
    t2 = time.time()
    equity_curve, trades, info = simulate_sector_rotation(
        close_panel, volume_panel, selections, config)
    if verbose:
        print(f"  Simulation done ({time.time()-t2:.1f}s)")

    # 8. Compute metrics
    skipped = []
    metrics = compute_portfolio_metrics(equity_curve, trades, skipped, info)

    # 9. Report
    if verbose:
        print_sector_rotation_report(metrics, info, config)

    # 10. Save JSON
    results_dir = os.path.join(os.path.dirname(_v3_dir), 'results')
    os.makedirs(results_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_path = os.path.join(results_dir,
        f'sector_rot_lb{config.lookback_days}_top{config.top_sectors}_{config.mode}_{ts}.json')

    # Serialize sector_pnl separately (it's not in PortfolioMetrics)
    result_data = {
        'strategy': 'sector_rotation',
        'config': asdict(config),
        'metrics': asdict(metrics),
        'info': {k: v for k, v in info.items() if k != 'sector_history'},
        'sector_pnl': info.get('sector_pnl', {}),
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
    lookback_list: List[int],
    top_sectors_list: Optional[List[int]] = None,
    rebalance_list: Optional[List[int]] = None,
    mode_list: Optional[List[str]] = None,
    base_config: Optional[SectorRotationConfig] = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """Sweep over lookback x top_sectors x rebalance x mode."""
    if base_config is None:
        base_config = SectorRotationConfig()
    if top_sectors_list is None:
        top_sectors_list = [base_config.top_sectors]
    if rebalance_list is None:
        rebalance_list = [base_config.rebalance_days]
    if mode_list is None:
        mode_list = [base_config.mode]

    # Load data once
    print(f"Loading {len(tokens)} tokens for parameter sweep...")
    token_data = load_universe_data(tokens, base_config.data_dir,
                                    base_config.market, base_config.workers)
    print(f"  Loaded {len(token_data)} tokens")

    close_panel, volume_panel = build_daily_panel(token_data)
    print(f"  Daily panel: {close_panel.shape[0]} days x {close_panel.shape[1]} tokens")

    # Compute regime filter once
    risk_on = None
    if base_config.regime_filter:
        risk_on = compute_btc_regime_daily(close_panel)
        print(f"  Regime filter: ON")

    all_dates = close_panel.index

    results_rows = []
    total_combos = len(lookback_list) * len(top_sectors_list) * len(rebalance_list) * len(mode_list)
    combo_idx = 0

    for lb in lookback_list:
        eligibility = compute_eligibility(close_panel, volume_panel,
                                          base_config.min_adv_usd,
                                          base_config.burn_in_days, lb)
        sector_returns, sector_tokens = build_sector_returns(
            close_panel, eligibility, lb)
        token_returns = compute_trailing_returns(close_panel, lb)

        for ts_count in top_sectors_list:
            for rb in rebalance_list:
                for mode in mode_list:
                    combo_idx += 1
                    cfg = SectorRotationConfig(
                        capital=base_config.capital,
                        lookback_days=lb,
                        rebalance_days=rb,
                        top_sectors=ts_count,
                        mode=mode,
                        max_tokens_per_sector=base_config.max_tokens_per_sector,
                        min_sector_tokens=base_config.min_sector_tokens,
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

                    selections = rank_sectors_and_select(
                        sector_returns, token_returns, eligibility,
                        sector_tokens, rebal_dates, cfg,
                        risk_on=risk_on)

                    if not selections:
                        continue

                    equity_curve, trades, info = simulate_sector_rotation(
                        close_panel, volume_panel, selections, cfg)

                    if len(equity_curve) < 2:
                        continue

                    metrics = compute_portfolio_metrics(equity_curve, trades, [], info)

                    row = {
                        'lookback': lb,
                        'top_sectors': ts_count,
                        'rebalance': rb,
                        'mode': mode,
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
                        print(f"  [{combo_idx}/{total_combos}] lb={lb} top={ts_count} rb={rb} mode={mode} | "
                              f"Sharpe={metrics.sharpe_ratio:+.2f} Return={metrics.annualized_return_pct:+.1f}% "
                              f"DD={metrics.max_drawdown_pct:.1f}%")

    if not results_rows:
        print("  No valid results from sweep.")
        return pd.DataFrame()

    df = pd.DataFrame(results_rows).sort_values('sharpe', ascending=False)

    # Print summary
    print(f"\n{'='*100}")
    print(f"SECTOR ROTATION SWEEP RESULTS - {len(df)} combinations")
    print(f"{'='*100}")
    print(f"{'LB':>4} {'Top':>4} {'RB':>4} {'Mode':>7} {'Sharpe':>8} {'Sortino':>8} "
          f"{'Ann%':>8} {'MaxDD%':>8} {'Trades':>7} {'WinR%':>7} {'PF':>6}")
    print(f"{'-'*100}")
    for _, r in df.iterrows():
        print(f"{int(r['lookback']):4d} {int(r['top_sectors']):4d} {int(r['rebalance']):4d} "
              f"{r['mode']:>7s} "
              f"{r['sharpe']:+8.2f} {r['sortino']:+8.2f} {r['ann_return_pct']:+8.1f} "
              f"{r['max_dd_pct']:8.1f} {int(r['n_trades']):7d} {r['win_rate_pct']:7.1f} "
              f"{r['profit_factor']:6.2f}")
    print()

    # Save
    results_dir = os.path.join(os.path.dirname(_v3_dir), 'results')
    os.makedirs(results_dir, exist_ok=True)
    ts_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    sweep_path = os.path.join(results_dir, f'sector_rot_sweep_{ts_str}.json')
    with open(sweep_path, 'w') as f:
        json.dump(results_rows, f, indent=2, default=str)
    print(f"  Sweep results saved to {sweep_path}")

    return df


# ---------------------------------------------------------------------------
# Sector Analysis (standalone — show sector performance without rotation)
# ---------------------------------------------------------------------------

def analyze_sectors(
    tokens: List[str],
    config: Optional[SectorRotationConfig] = None,
    verbose: bool = True,
) -> Dict:
    """Analyze sector-level performance (no rotation, just metrics per sector)."""
    if config is None:
        config = SectorRotationConfig()

    print(f"Loading {len(tokens)} tokens...")
    token_data = load_universe_data(tokens, config.data_dir, config.market, config.workers)
    print(f"  Loaded {len(token_data)} tokens")

    close_panel, volume_panel = build_daily_panel(token_data)

    # Map tokens to sectors
    panel_tokens = close_panel.columns.tolist()
    sector_tokens = {}
    for token in panel_tokens:
        sector = get_token_sector(token)
        if sector not in sector_tokens:
            sector_tokens[sector] = []
        sector_tokens[sector].append(token)

    # Use OOS period only
    all_dates = close_panel.index
    if len(all_dates) <= config.burn_in_days:
        print("  Not enough data for burn-in")
        return {}

    oos_start = all_dates[config.burn_in_days]
    oos_close = close_panel.loc[oos_start:]
    oos_returns = oos_close.pct_change()  # keep NaN per-token (different listing dates)

    print(f"\n{'='*90}")
    print(f"SECTOR ANALYSIS — OOS from {oos_start.strftime('%Y-%m-%d')}")
    print(f"{'='*90}")
    print(f"{'Sector':>10} {'#Tok':>5} {'AnnRet%':>8} {'Sharpe':>8} {'MaxDD%':>8} "
          f"{'Vol%':>7} {'Best':>8} {'Worst':>8}")
    print(f"{'-'*90}")

    sector_results = {}
    for sector in sorted(sector_tokens.keys()):
        toks = [t for t in sector_tokens[sector] if t in oos_returns.columns]
        if len(toks) < 2:
            continue

        # Equal-weighted sector return (NaN-safe: mean ignores NaN tokens)
        sec_daily = oos_returns[toks].mean(axis=1).dropna()
        if len(sec_daily) < 30:
            continue
        sec_cum = (1 + sec_daily).cumprod()
        total_ret = sec_cum.iloc[-1] - 1
        n_days = len(sec_daily)
        years = max(n_days / 365, 0.01)
        ann_ret = ((1 + total_ret) ** (1.0 / years) - 1) * 100
        std_daily = sec_daily.std()
        sharpe = (sec_daily.mean() / std_daily * np.sqrt(365)) if std_daily > 1e-10 else 0
        vol_ann = std_daily * np.sqrt(365) * 100
        max_dd = ((sec_cum / sec_cum.cummax()) - 1).min() * 100

        # Best/worst token in sector (by total return)
        tok_returns = {}
        for t in toks:
            tok_ret = oos_returns[t].dropna()
            if len(tok_ret) < 30:
                continue
            tok_cum = (1 + tok_ret).cumprod()
            tok_returns[t] = (tok_cum.iloc[-1] - 1) * 100
        if not tok_returns:
            continue
        best_tok = max(tok_returns, key=tok_returns.get)
        worst_tok = min(tok_returns, key=tok_returns.get)

        print(f"{sector:>10} {len(toks):5d} {ann_ret:+8.1f} {sharpe:+8.2f} {max_dd:8.1f} "
              f"{vol_ann:7.1f} {best_tok:>8s} {worst_tok:>8s}")

        sector_results[sector] = {
            'n_tokens': len(toks),
            'annualized_return_pct': ann_ret,
            'sharpe': sharpe,
            'max_dd_pct': max_dd,
            'vol_ann_pct': vol_ann,
            'best_token': best_tok,
            'worst_token': worst_tok,
            'tokens': toks,
        }

    print()
    return sector_results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='V3 Sector Rotation Backtest')
    parser.add_argument('--lookback', nargs='+', type=int, default=[14],
                        help='Trailing return lookback in days (multiple for sweep)')
    parser.add_argument('--rebalance', nargs='+', type=int, default=[7],
                        help='Rebalance frequency in days (multiple for sweep)')
    parser.add_argument('--top-sectors', nargs='+', type=int, default=[3],
                        help='Number of top sectors (multiple for sweep)')
    parser.add_argument('--mode', nargs='+', default=['sector'],
                        choices=['sector', 'hybrid'],
                        help='Selection mode (multiple for sweep)')
    parser.add_argument('--max-tokens-per-sector', type=int, default=10,
                        help='Max tokens per sector in hybrid mode')
    parser.add_argument('--min-sector-tokens', type=int, default=2,
                        help='Min eligible tokens for sector to rank')
    parser.add_argument('--capital', type=float, default=200_000)
    parser.add_argument('--min-adv', type=float, default=500_000)
    parser.add_argument('--burn-in', type=int, default=365)
    parser.add_argument('--universe', default='liquid',
                        choices=['all', 'filtered', 'liquid'])
    parser.add_argument('--tokens', nargs='+', help='Specific tokens')
    parser.add_argument('--market', default='spot', choices=['spot', 'perp'])
    parser.add_argument('--exchange', default='binance')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--sweep', action='store_true',
                        help='Run parameter sweep')
    parser.add_argument('--no-regime', action='store_true',
                        help='Disable BTC regime filter (always invested)')
    parser.add_argument('--analyze', action='store_true',
                        help='Analyze sector performance (no rotation)')
    args = parser.parse_args()

    # Resolve tokens
    if args.tokens:
        tokens = args.tokens
    else:
        tokens = resolve_universe(args.universe, market=args.market, verbose=True)
    print(f"Universe: {len(tokens)} tokens ({args.universe if not args.tokens else 'custom'})")

    config = SectorRotationConfig(
        capital=args.capital,
        lookback_days=args.lookback[0],
        rebalance_days=args.rebalance[0],
        top_sectors=args.top_sectors[0],
        mode=args.mode[0],
        max_tokens_per_sector=args.max_tokens_per_sector,
        min_sector_tokens=args.min_sector_tokens,
        burn_in_days=args.burn_in,
        min_adv_usd=args.min_adv,
        regime_filter=not args.no_regime,
        data_dir='data',
        market=args.market,
        exchange=args.exchange,
        workers=args.workers,
    )

    if args.analyze:
        analyze_sectors(tokens, config)
    elif args.sweep or len(args.lookback) > 1 or len(args.top_sectors) > 1 or len(args.rebalance) > 1 or len(args.mode) > 1:
        run_parameter_sweep(
            tokens,
            lookback_list=args.lookback,
            top_sectors_list=args.top_sectors if len(args.top_sectors) > 1 else None,
            rebalance_list=args.rebalance if len(args.rebalance) > 1 else None,
            mode_list=args.mode if len(args.mode) > 1 else None,
            base_config=config,
        )
    else:
        run_sector_rotation_backtest(tokens, config)


if __name__ == '__main__':
    main()
