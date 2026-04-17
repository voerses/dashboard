"""
Token Universe & Dynamic ADV-Based Sizing — V3
================================================

Position sizing and costs derived from actual trading volume, not static lists.
ANY token with data can trade — ADV determines sizing, costs, and tier classification.

ADV (Average Daily Volume in USD) drives everything:
  - Position sizing: continuous function of log(ADV)
  - Slippage: inversely proportional to sqrt(ADV)
  - Tier classification: derived from ADV for reporting only

Tier labels (for reporting):
  Tier 1 (>$50M ADV): full position, tight costs
  Tier 2 ($10-50M ADV): moderate position, moderate costs
  Tier 3 (<$10M ADV): small position, conservative costs
"""

import json
import math
import warnings
import numpy as np
import pandas as pd
from pathlib import Path as _Path


# ---------------------------------------------------------------------------
# ADV thresholds for tier classification (reporting only)
# ---------------------------------------------------------------------------
ADV_TIER1_THRESHOLD = 50_000_000   # $50M+
ADV_TIER2_THRESHOLD = 10_000_000   # $10M-$50M

# Hard cap: never take more than 2% of a token's daily volume
MAX_ADV_PCT = 0.02

# Default exchange for fee lookups
DEFAULT_EXCHANGE = 'binance'

# Fallback ADV when volume data is unavailable (conservative: $5M)
FALLBACK_ADV = 5_000_000


# ---------------------------------------------------------------------------
# Core: Compute ADV from OHLCV data
# ---------------------------------------------------------------------------

def compute_adv(close, volume, lookback_days=30, hours_per_bar=1):
    """Compute Average Daily Volume (USD) from close/volume arrays.

    Args:
        close: array of close prices
        volume: array of base-currency volumes
        lookback_days: number of days to average over (default 30)
        hours_per_bar: timeframe (1 for 1h bars, 4 for 4h, 24 for daily)

    Returns:
        Median ADV in USD over the lookback period.
        Uses median (not mean) to be robust against volume spikes.
    """
    dollar_volume = close * volume
    bars_per_day = max(1, 24 // hours_per_bar)
    lookback_bars = lookback_days * bars_per_day

    if len(dollar_volume) < bars_per_day:
        return FALLBACK_ADV

    # Use the last `lookback_bars` of data
    recent = dollar_volume[-lookback_bars:]

    # Reshape into daily chunks and sum each day
    n_complete_days = len(recent) // bars_per_day
    if n_complete_days < 3:
        return FALLBACK_ADV

    trimmed = recent[-(n_complete_days * bars_per_day):]
    daily_volumes = trimmed.reshape(n_complete_days, bars_per_day).sum(axis=1)

    # Median is robust against single-day spikes
    return float(np.median(daily_volumes))


# ---------------------------------------------------------------------------
# ADV → Tier (for reporting only)
# ---------------------------------------------------------------------------

def adv_to_tier(adv):
    """Classify ADV into tier number (1, 2, or 3). For reporting only."""
    if adv >= ADV_TIER1_THRESHOLD:
        return 1
    elif adv >= ADV_TIER2_THRESHOLD:
        return 2
    else:
        return 3


# ---------------------------------------------------------------------------
# ADV → Trading Costs (continuous)
# ---------------------------------------------------------------------------

def adv_to_costs(adv, exchange=None, market='spot', order_type='taker'):
    """Return fee_rate for a given ADV.

    Delegates to get_fee_rate() for exchange-specific fees.
    Slippage is computed separately per-trade in the engine JIT.
    """
    if exchange is None:
        exchange = DEFAULT_EXCHANGE
    return get_fee_rate(exchange, market, order_type)


# ---------------------------------------------------------------------------
# Exchange-Specific Fee Model (for futures/perp support)
# ---------------------------------------------------------------------------

EXCHANGE_FEES = {
    # (maker, taker) fee rates per market type — verified 2025-2026
    # Sources:
    #   Binance:     binance.com/en/fee (base tier, no BNB discount)
    #   Kraken:      kraken.com/features/fee-schedule ($250k-500k/mo tier)
    #   Hyperliquid: hyperliquid.gitbook.io/hyperliquid-docs/trading/fees (Tier 0)
    'binance':     {'spot': (0.0010, 0.0010), 'perp': (0.0002, 0.0005)},
    'kraken':      {'spot': (0.0016, 0.0026), 'perp': (0.0002, 0.0005)},
    'hyperliquid': {'spot': (0.0004, 0.0007), 'perp': (0.00015, 0.00045)},
}

# Maintenance margin rates (MMR) per exchange — for liquidation threshold
# Position < $50K bracket for simplicity (vast majority of backtest positions)
# Sources:
#   Binance: binance.com/en/support/faq/leverage-and-margin-of-usd-m-futures (Tier 1: 0.40%)
#   Kraken:  support.kraken.com/hc/en-us/articles/margin-schedule (1.00% for <$2M)
#   Hyperliquid: conservative default (5.00%)
EXCHANGE_MMR = {
    'binance':     0.004,   # 0.40% — Tier 1 (<$50K notional)
    'kraken':      0.01,    # 1.00% — Tier 1 (<$2M notional)
    'hyperliquid': 0.05,    # 5.00% — conservative default
}

# Liquidation fee rates per exchange — charged on notional at liquidation
# Sources:
#   Binance: 1.5% of notional (liquidation insurance fund fee)
#   Kraken:  estimated similar
#   Hyperliquid: conservative estimate
EXCHANGE_LIQUIDATION_FEE = {
    'binance':     0.015,   # 1.5% of notional
    'kraken':      0.015,   # 1.5% estimated
    'hyperliquid': 0.015,   # 1.5% conservative
}


def get_maint_margin_rate(exchange='binance'):
    """Maintenance margin rate for an exchange.

    Returns the fraction of margin that must be maintained before liquidation.
    E.g. 0.004 means position is liquidated when remaining margin < 0.4% of notional.
    """
    return EXCHANGE_MMR.get(exchange, EXCHANGE_MMR['binance'])


def get_liquidation_fee_rate(exchange='binance'):
    """Liquidation fee rate for an exchange.

    Returns the fraction of notional charged as a liquidation fee.
    E.g. 0.015 means 1.5% of notional at liquidation.
    """
    return EXCHANGE_LIQUIDATION_FEE.get(exchange, EXCHANGE_LIQUIDATION_FEE['binance'])


def get_fee_rate(exchange='binance', market='spot', order_type='taker'):
    """Fee rate for exchange + market + order type.

    Args:
        exchange: Exchange name ('binance', 'kraken', 'hyperliquid')
        market: Market type ('spot' or 'perp')
        order_type: 'maker' or 'taker'

    Returns:
        Fee rate as a float (e.g. 0.0005 for Binance perp taker)
    """
    if market not in ('spot', 'perp'):
        warnings.warn(
            f"get_fee_rate: invalid market '{market}', falling back to 'spot'",
            stacklevel=2,
        )
        market = 'spot'
    fees = EXCHANGE_FEES.get(exchange, EXCHANGE_FEES['binance'])
    market_fees = fees.get(market, fees['spot'])
    return market_fees[0 if order_type == 'maker' else 1]


# ---------------------------------------------------------------------------
# Token discovery — data-driven, no static lists
# ---------------------------------------------------------------------------

# Tokens excluded from combined (spot+perp) strategies due to data gaps.
# LIT: spot delisted Feb 2025, perp re-listed Dec 2025 → zero overlap.
# XMR: spot delisted from Binance Feb 2024, perp continues → stale spot.
# PAXG: spot history much longer than perp → only 17% overlap.
NO_COMBINED = {'LIT', 'XMR', 'PAXG'}


def get_all_tradeable(market='spot'):
    """Discover all tokens that have parquet data."""
    if market == 'combined':
        # Combined requires both spot AND perp data
        spot_tokens = set(get_all_tradeable('spot'))
        perp_tokens = set(get_all_tradeable('perp'))
        return sorted((spot_tokens & perp_tokens) - NO_COMBINED)
    data_dir = _Path(__file__).resolve().parent.parent / 'data' / market / '1h_cache'
    if not data_dir.exists():
        return []
    tokens = sorted(
        f.stem.replace('_1h', '')
        for f in data_dir.glob('*_1h.parquet')
    )
    return tokens


def max_position_usd(capital=200_000, adv=None):
    """Max position size in USD, respecting liquidity constraints."""
    if adv is None or adv <= 0:
        adv = FALLBACK_ADV
    return adv * MAX_ADV_PCT


# ---------------------------------------------------------------------------
# Dynamic Liquidity Gating — point-in-time, no look-ahead
# ---------------------------------------------------------------------------

# Defaults for liquidity gating
DEFAULT_MIN_ADV_USD = 500_000   # $500k minimum ADV to be tradeable
DEFAULT_BURN_IN_DAYS = 90       # New listings need 90 days before trading
DEFAULT_ADV_LOOKBACK = 30       # Rolling 30-day ADV for evaluation


def compute_rolling_adv(close, volume, lookback_days=30, hours_per_bar=1):
    """Compute rolling ADV (point-in-time) for every bar in the series.

    Returns array same length as close, where each element is the median
    daily dollar volume over the trailing lookback window. NaN for bars
    with insufficient history.

    This is the time-series version of compute_adv() — used for
    liquidity gating at arbitrary points in time.

    Optimized: aggregates to daily, computes rolling median on daily series,
    then expands back to hourly via forward-fill. O(n_days * lookback_days).
    """
    close = np.asarray(close, dtype=np.float64)
    volume = np.asarray(volume, dtype=np.float64)
    dollar_volume = close * volume
    bars_per_day = max(1, 24 // hours_per_bar)
    n = len(dollar_volume)

    adv_hourly = np.full(n, np.nan)
    if n < bars_per_day * 3:
        return adv_hourly

    # Step 1: Aggregate hourly dollar volume to daily totals
    n_complete_days = n // bars_per_day
    if n_complete_days < 3:
        return adv_hourly

    # Reshape complete days
    trimmed = dollar_volume[:n_complete_days * bars_per_day]
    daily_dv = np.nan_to_num(trimmed, 0.0).reshape(n_complete_days, bars_per_day).sum(axis=1)

    # Step 2: Rolling median on daily series (O(n) via pandas)
    daily_adv = pd.Series(daily_dv).rolling(lookback_days, min_periods=3).median().values

    # Step 3: Expand daily ADV back to hourly, LAGGED BY ONE DAY
    # Day d's ADV uses day d's full volume, so it can only be known at
    # the end of day d. Apply it to day d+1's bars to avoid look-ahead.
    # Vectorized: repeat each daily value across bars_per_day, shifted by 1 day.
    if n_complete_days > 0:
        # Lag by 1 day: day d's ADV applies to day d+1's bars (and partial tail)
        expanded = np.repeat(daily_adv, bars_per_day)
        start = bars_per_day  # first bar of day 1
        end = min(start + len(expanded), n)
        adv_hourly[start:end] = expanded[:end - start]

    return adv_hourly


def compute_liquidity_mask(close, volume, min_adv_usd=DEFAULT_MIN_ADV_USD,
                           burn_in_days=DEFAULT_BURN_IN_DAYS,
                           adv_lookback_days=DEFAULT_ADV_LOOKBACK,
                           hours_per_bar=1):
    """Point-in-time liquidity gate for a single token.

    Returns a boolean array (same length as close) where True means
    the token is liquid enough to trade at that bar.

    Rules (all must be true):
        1. Token has >= burn_in_days of data before this bar
        2. Rolling ADV >= min_adv_usd at this bar
        3. No look-ahead: only uses data up to and including this bar
    """
    n = len(close)
    bars_per_day = max(1, 24 // hours_per_bar)
    burn_in_bars = burn_in_days * bars_per_day

    # Burn-in: first burn_in_days are always False
    if n <= burn_in_bars:
        return np.zeros(n, dtype=np.bool_)

    # Compute rolling ADV
    rolling_adv = compute_rolling_adv(close, volume, adv_lookback_days, hours_per_bar)

    # Vectorized: both gates at once
    mask = np.zeros(n, dtype=np.bool_)
    adv_ok = ~np.isnan(rolling_adv) & (rolling_adv >= min_adv_usd)
    mask[burn_in_bars:] = adv_ok[burn_in_bars:]

    return mask


def get_liquid_universe(market='spot', min_adv_usd=DEFAULT_MIN_ADV_USD,
                        burn_in_days=DEFAULT_BURN_IN_DAYS):
    """Get tokens that currently pass liquidity gate.

    Loads each token's data, computes ADV from the most recent 30 days,
    and filters by min_adv_usd. Also requires burn_in_days of history.

    Returns:
        List of (token, adv_usd) tuples, sorted by ADV descending.
    """
    data_dir = _Path(__file__).resolve().parent.parent / 'data' / market / '1h_cache'
    if not data_dir.exists():
        return []

    results = []
    for f in sorted(data_dir.glob('*_1h.parquet')):
        token = f.stem.replace('_1h', '')
        try:
            df = pd.read_parquet(f, columns=['close', 'volume'])
            n_days = len(df) / 24
            if n_days < burn_in_days:
                continue
            adv = compute_adv(
                df['close'].values.astype(np.float64),
                df['volume'].values.astype(np.float64),
                lookback_days=30, hours_per_bar=1)
            if adv >= min_adv_usd:
                results.append((token, adv))
        except Exception:
            continue

    results.sort(key=lambda x: -x[1])
    return results


# ---------------------------------------------------------------------------
# Quality-Based Filtering
# ---------------------------------------------------------------------------

_GRADE_ORDER = {'A': 0, 'B': 1, 'C': 2, 'D': 3, 'F': 4}


def load_quality_grades(quality_dir=None):
    """Load token quality grades from the latest quality report.

    Returns:
        Dict[str, dict] — {token: {grade, total_bars, completeness_pct, n_critical}}
        Empty dict if no quality reports found.
    """
    if quality_dir is None:
        quality_dir = _Path(__file__).resolve().parent.parent / 'data' / 'quality_reports'
    else:
        quality_dir = _Path(quality_dir)

    if not quality_dir.exists():
        return {}

    reports = sorted(quality_dir.glob('quality_*.json'))
    if not reports:
        return {}

    with open(reports[-1]) as f:
        raw = json.load(f)

    grades = {}
    for token, info in raw.items():
        summary = info.get('checks', {}).get('summary', {})
        grades[token] = {
            'grade': summary.get('grade', 'C'),
            'total_bars': info.get('total_bars', 0),
            'completeness_pct': summary.get('completeness_pct', 0.0),
            'n_critical': summary.get('n_critical', 0),
        }
    return grades


def get_filtered_universe(market='spot', min_bars=2000, min_grade='B',
                          min_adv_usd=None, burn_in_days=None,
                          quality_dir=None, verbose=False):
    """Get tokens filtered by data quality and optionally by liquidity.

    Filter layers (in order):
        1. Data existence — get_all_tradeable(market)
        2. Bar count — total_bars >= min_bars (from quality report)
        3. Quality grade — grade must meet min_grade threshold
        4. Liquidity (optional) — ADV check via get_liquid_universe()

    Tokens missing from the quality report are INCLUDED (don't penalize
    missing data). A warning is printed when verbose=True.

    Args:
        market: 'spot' or 'perp'
        min_bars: Minimum bar count to include (default 2000)
        min_grade: Minimum quality grade, 'A' or 'B' (default 'B')
        min_adv_usd: If set, apply liquidity gate (e.g. 500_000)
        burn_in_days: Burn-in requirement for liquidity gate
        quality_dir: Override quality report directory
        verbose: Print filter details

    Returns:
        List[str] — sorted token list passing all filters.
    """
    all_tokens = get_all_tradeable(market)
    if not all_tokens:
        return []

    grades = load_quality_grades(quality_dir)
    min_grade_rank = _GRADE_ORDER.get(min_grade, 1)

    # Filter by bar count and grade
    filtered = []
    excluded_bars = []
    excluded_grade = []
    missing_quality = []

    for token in all_tokens:
        if token not in grades:
            missing_quality.append(token)
            filtered.append(token)  # Include tokens missing from report
            continue

        info = grades[token]
        if info['total_bars'] < min_bars:
            excluded_bars.append((token, info['total_bars']))
            continue
        grade_rank = _GRADE_ORDER.get(info['grade'], 4)
        if grade_rank > min_grade_rank:
            excluded_grade.append((token, info['grade']))
            continue
        filtered.append(token)

    # Optional liquidity gate
    if min_adv_usd is not None:
        liq_kwargs = {'market': market, 'min_adv_usd': min_adv_usd}
        if burn_in_days is not None:
            liq_kwargs['burn_in_days'] = burn_in_days
        liquid_pairs = get_liquid_universe(**liq_kwargs)
        liquid_set = {tk for tk, _ in liquid_pairs}
        excluded_adv = [tk for tk in filtered if tk not in liquid_set]
        filtered = [tk for tk in filtered if tk in liquid_set]
    else:
        excluded_adv = []

    if verbose:
        print(f"Universe filter ({market}): {len(all_tokens)} total → {len(filtered)} passed")
        if excluded_bars:
            print(f"  Excluded (< {min_bars} bars): {', '.join(t for t,_ in excluded_bars)}")
        if excluded_grade:
            print(f"  Excluded (grade < {min_grade}): {', '.join(f'{t}({g})' for t,g in excluded_grade)}")
        if excluded_adv:
            print(f"  Excluded (low ADV): {', '.join(excluded_adv)}")
        if missing_quality:
            print(f"  Warning: no quality data for: {', '.join(missing_quality)} (included anyway)")

    return sorted(filtered)


def resolve_universe(universe_mode, market='spot', verbose=False):
    """Resolve a --universe flag value to a token list.

    Args:
        universe_mode: 'all', 'filtered', or 'liquid'
        market: 'spot' or 'perp'
        verbose: Print filter details

    Returns:
        List[str] — sorted token list.
    """
    if universe_mode == 'all':
        return get_all_tradeable(market)
    elif universe_mode == 'liquid':
        return get_filtered_universe(market=market, min_bars=2000, min_grade='B',
                                     min_adv_usd=500_000, verbose=verbose)
    else:  # 'filtered' (default)
        return get_filtered_universe(market=market, min_bars=2000, min_grade='B',
                                     verbose=verbose)


def print_universe_report(market='spot', min_adv_usd=DEFAULT_MIN_ADV_USD,
                          burn_in_days=DEFAULT_BURN_IN_DAYS):
    """Print a summary of the liquid universe."""
    all_tokens = get_all_tradeable(market)
    liquid = get_liquid_universe(market, min_adv_usd, burn_in_days)
    liquid_names = [t for t, _ in liquid]

    excluded_burn_in = []
    excluded_adv = []
    data_dir = _Path(__file__).resolve().parent.parent / 'data' / market / '1h_cache'

    for token in all_tokens:
        if token in liquid_names:
            continue
        try:
            f = data_dir / f'{token}_1h.parquet'
            df = pd.read_parquet(f, columns=['close', 'volume'])
            n_days = len(df) / 24
            if n_days < burn_in_days:
                excluded_burn_in.append((token, n_days))
            else:
                adv = compute_adv(
                    df['close'].values.astype(np.float64),
                    df['volume'].values.astype(np.float64))
                excluded_adv.append((token, adv))
        except Exception:
            excluded_adv.append((token, 0))

    print(f"\n{'='*70}")
    print(f"UNIVERSE REPORT — {market.upper()}")
    print(f"  Min ADV: ${min_adv_usd:,.0f} | Burn-in: {burn_in_days}d")
    print(f"{'='*70}\n")

    print(f"Total tokens with data: {len(all_tokens)}")
    print(f"Pass liquidity gate:    {len(liquid)}")
    print(f"Excluded (burn-in):     {len(excluded_burn_in)}")
    print(f"Excluded (low ADV):     {len(excluded_adv)}")

    print(f"\nLiquid tokens ({len(liquid)}):")
    for i, (tk, adv) in enumerate(liquid):
        tier = adv_to_tier(adv)
        print(f"  {i+1:3d}. {tk:>8s}  ADV=${adv/1e6:7.1f}M  Tier {tier}")

    if excluded_adv:
        excluded_adv.sort(key=lambda x: -x[1])
        print(f"\nExcluded — low ADV ({len(excluded_adv)}):")
        for tk, adv in excluded_adv[:10]:
            print(f"       {tk:>8s}  ADV=${adv/1e6:7.2f}M")
        if len(excluded_adv) > 10:
            print(f"       ... +{len(excluded_adv)-10} more")

    if excluded_burn_in:
        print(f"\nExcluded — insufficient history ({len(excluded_burn_in)}):")
        for tk, days in excluded_burn_in:
            print(f"       {tk:>8s}  {days:.0f} days (need {burn_in_days})")

    print()
    return liquid


# ---------------------------------------------------------------------------
# Legacy compatibility — kept so imports don't break, but not used for sizing
# ---------------------------------------------------------------------------
TIER1 = ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'BNB', 'SUI']
TIER2 = [
    'ADA', 'PEPE', 'TRX', 'LINK', 'AVAX', 'DOT', 'LTC', 'NEAR',
    'BCH', 'UNI', 'APT', 'ARB', 'FIL', 'AAVE', 'HBAR', 'XLM',
    'SHIB', 'TON', 'BONK', 'FET', 'OP', 'FLOKI', 'INJ',
    'ATOM', 'ETC', 'RENDER', 'SAND', 'AXS', 'ALGO', 'GALA',
    'DYDX', 'SNX', 'CRV', 'IMX', 'LDO', 'ONDO', 'TAO',
    'ENA', 'WIF', 'WLD', 'PENDLE', 'SEI', 'TIA', 'JUP', 'JTO',
    'STRK', 'ORDI', 'EIGEN', 'STG', 'NEIRO', 'TRUMP',
]
TIER3 = []
LIQUID_TOKENS = TIER1 + TIER2


def get_tier(ticker):
    """Legacy stub — returns tier 3 for everything. Use adv_to_tier(adv) instead."""
    return 3, 0.25


def get_tier_costs(tier):
    """Legacy stub — returns conservative costs. Use adv_to_costs(adv) instead."""
    return get_fee_rate(DEFAULT_EXCHANGE, 'spot', 'taker'), 35.0
