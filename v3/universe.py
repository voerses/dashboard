"""
Token Universe & Tier System — V3 standalone copy.
===================================================

Copied from v2/liquid_universe.py. V2 is untouched.

Tiers control position sizing:
  Tier 1 (>$50M ADV): full position
  Tier 2 ($10-50M ADV): 50% position
  Tier 3 ($5-10M ADV): 25% position
"""

# Tier 1: >$50M 24h vol
TIER1 = [
    'BTC', 'ETH', 'SOL', 'XRP', 'PAXG', 'DOGE', 'BNB', 'SUI',
]

# Tier 2: $10M-$50M 24h vol
TIER2 = [
    'ADA', 'ZEC', 'PEPE', 'TRX', 'LINK', 'AVAX', 'DOT', 'KITE',
    'ASTER', 'LTC', 'NEAR', 'TAO', 'BARD', 'BCH', 'UNI', 'ALICE',
    'APT', 'VIRTUAL', 'ICP', 'PUMP', 'ARB', 'FIL', 'ENA', 'ZRO',
    'ENSO', 'AAVE', 'HBAR', 'PENGU',
]

# Tier 3: $5M-$10M 24h vol
TIER3 = [
    'DASH', 'WLD', 'XPL', 'SHIB', 'WLFI', 'WIF', 'XLM', 'OM',
    'TRUMP', 'TON', 'PENDLE', 'SEI', 'BONK', 'FET', 'DENT', 'OP',
    'CHZ', 'FLOKI', 'INJ', 'CAKE', 'POL',
]

LIQUID_TOKENS = TIER1 + TIER2 + TIER3

TIER_SIZE = {
    1: 1.0,
    2: 0.5,
    3: 0.25,
}

MAX_ADV_PCT = 0.02


def get_tier(ticker):
    """Get liquidity tier. Returns (tier_number, size_multiplier)."""
    if ticker in TIER1:
        return 1, TIER_SIZE[1]
    elif ticker in TIER2:
        return 2, TIER_SIZE[2]
    elif ticker in TIER3:
        return 3, TIER_SIZE[3]
    else:
        return 0, 0


def max_position_usd(ticker, capital=200_000, adv=None):
    """Max position size in USD, respecting liquidity constraints."""
    tier, size_mult = get_tier(ticker)
    if tier == 0:
        return 0
    base = capital * size_mult / len(LIQUID_TOKENS)
    if adv is not None and adv > 0:
        adv_cap = adv * MAX_ADV_PCT
        return min(base, adv_cap)
    return base
