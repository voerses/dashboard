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

import math
import numpy as np
from pathlib import Path as _Path


# ---------------------------------------------------------------------------
# ADV thresholds for tier classification (reporting only)
# ---------------------------------------------------------------------------
ADV_TIER1_THRESHOLD = 50_000_000   # $50M+
ADV_TIER2_THRESHOLD = 10_000_000   # $10M-$50M

# Hard cap: never take more than 2% of a token's daily volume
MAX_ADV_PCT = 0.02

# Exchange fee rate (fairly constant across major exchanges)
BASE_FEE_RATE = 0.0022

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
# ADV → Position Sizing (continuous)
# ---------------------------------------------------------------------------

def adv_to_sizing(adv):
    """Map ADV to (kelly_mult, cap_pct) — continuous functions.

    kelly_mult: 0.15 (micro) → 0.50 (mega liquid)
    cap_pct:    0.02 (micro) → 0.12 (mega liquid)

    Both scale with log10(ADV_millions), capped at $10B+ ADV.
    """
    adv_m = max(adv, 1.0) / 1_000_000  # ADV in millions
    # log10 scale: 0.1M → -1, 1M → 0, 10M → 1, 100M → 2, 10B → 4
    log_adv = math.log10(max(adv_m, 0.1))
    # Normalize to 0-1 range: -1 (0.1M) → 0, 4 (10B) → 1
    frac = max(0.0, min(1.0, (log_adv + 1) / 5.0))

    kelly_mult = 0.15 + 0.35 * frac
    cap_pct = 0.02 + 0.10 * frac
    return kelly_mult, cap_pct


# ---------------------------------------------------------------------------
# ADV → Trading Costs (continuous)
# ---------------------------------------------------------------------------

def adv_to_costs(adv):
    """Return fee_rate for a given ADV.

    fee_rate: ~0.0022 (constant, exchange-dependent not token-dependent)

    Slippage is no longer returned here — it's computed per-trade in the
    engine JIT as: slip_bps = base_spread + impact_coeff * sqrt(pos_usd / adv)
    This properly accounts for position size relative to liquidity.
    """
    return BASE_FEE_RATE


# ---------------------------------------------------------------------------
# Exchange-Specific Fee Model (for futures/perp support)
# ---------------------------------------------------------------------------

EXCHANGE_FEES = {
    # (maker, taker) fee rates per market type
    'binance':     {'spot': (0.0010, 0.0010), 'perp': (0.0002, 0.0005)},
    'kraken':      {'spot': (0.0012, 0.0022), 'perp': (0.000125, 0.000225)},
    'hyperliquid': {'spot': (0.0010, 0.0010), 'perp': (0.00015, 0.00035)},
}


def get_fee_rate(exchange='binance', market='spot', order_type='taker'):
    """Fee rate for exchange + market + order type.

    Args:
        exchange: Exchange name ('binance', 'kraken', 'hyperliquid')
        market: Market type ('spot' or 'perp')
        order_type: 'maker' or 'taker'

    Returns:
        Fee rate as a float (e.g. 0.0005 for Binance perp taker)
    """
    fees = EXCHANGE_FEES.get(exchange, EXCHANGE_FEES['binance'])
    market_fees = fees.get(market, fees['spot'])
    return market_fees[0 if order_type == 'maker' else 1]


# ---------------------------------------------------------------------------
# Token discovery — data-driven, no static lists
# ---------------------------------------------------------------------------

def get_all_tradeable(market='spot'):
    """Discover all tokens that have parquet data."""
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
    kelly_mult, cap_pct = adv_to_sizing(adv)
    base = capital * cap_pct
    adv_cap = adv * MAX_ADV_PCT
    return min(base, adv_cap)


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
    return BASE_FEE_RATE, 35.0
