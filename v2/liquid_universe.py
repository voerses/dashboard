"""
Liquid Token Universe — the ONLY tokens we trade.

Updated: 2026-02-28
Criteria: >= $5M average daily dollar volume (20-day)
Source: Binance spot market, live scan

Run `refresh_universe()` to update from live Binance data.
"""

# Tier 1: >$50M 24h vol — full position size, negligible market impact
TIER1 = [
    'BTC', 'ETH', 'SOL', 'XRP', 'PAXG', 'DOGE', 'BNB', 'SUI',
]

# Tier 2: $10M-$50M 24h vol — max 50% position, watch for slippage
TIER2 = [
    'ADA', 'ZEC', 'PEPE', 'TRX', 'LINK', 'AVAX', 'DOT', 'KITE',
    'ASTER', 'LTC', 'NEAR', 'TAO', 'BARD', 'BCH', 'UNI', 'ALICE',
    'APT', 'VIRTUAL', 'ICP', 'PUMP', 'ARB', 'FIL', 'ENA', 'ZRO',
    'ENSO', 'AAVE', 'HBAR', 'PENGU',
]

# Tier 3: $5M-$10M 24h vol — max 25% position, careful entry/exit
TIER3 = [
    'DASH', 'WLD', 'XPL', 'SHIB', 'WLFI', 'WIF', 'XLM', 'OM',
    'TRUMP', 'TON', 'PENDLE', 'SEI', 'BONK', 'FET', 'DENT', 'OP',
    'CHZ', 'FLOKI', 'INJ', 'CAKE', 'POL',
]

# Full liquid universe (all tiers)
LIQUID_TOKENS = TIER1 + TIER2 + TIER3

# Position size multipliers by tier
TIER_SIZE = {
    1: 1.0,   # Full position
    2: 0.5,   # Half position
    3: 0.25,  # Quarter position
}

# ADV threshold ($)
MIN_ADV = 5_000_000

# Max % of average daily volume to trade
MAX_ADV_PCT = 0.02  # 2%


def get_tier(ticker):
    """Get liquidity tier for a token. Returns (tier_number, size_multiplier)."""
    if ticker in TIER1:
        return 1, TIER_SIZE[1]
    elif ticker in TIER2:
        return 2, TIER_SIZE[2]
    elif ticker in TIER3:
        return 3, TIER_SIZE[3]
    else:
        return 0, 0  # Not in universe — do not trade


def max_position_usd(ticker, capital=200_000, adv=None):
    """Max position size in USD for a token, respecting liquidity constraints."""
    tier, size_mult = get_tier(ticker)
    if tier == 0:
        return 0

    # Base position = equal weight across portfolio * tier multiplier
    base = capital * size_mult / len(LIQUID_TOKENS)

    # Also cap at 2% of ADV if we know it
    if adv is not None and adv > 0:
        adv_cap = adv * MAX_ADV_PCT
        return min(base, adv_cap)

    return base


def refresh_universe():
    """Re-scan Binance and print updated tiers. Run periodically to catch liquidity changes."""
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    from live_scanner import get_top_symbols, fetch_recent_klines, compute_signals
    import numpy as np

    print("Refreshing liquid universe from Binance...")
    pairs = get_top_symbols(300)
    tiers = {1: [], 2: [], 3: []}

    for pair in pairs:
        ticker = pair['ticker']
        df = fetch_recent_klines(pair['symbol'], days=30)
        if df is None or len(df) < 20:
            continue
        close = df['close'].values
        vol = df['volume'].values
        adv = np.mean(close[-20:] * vol[-20:])

        if adv >= 50_000_000:
            tiers[1].append((ticker, adv))
        elif adv >= 10_000_000:
            tiers[2].append((ticker, adv))
        elif adv >= 5_000_000:
            tiers[3].append((ticker, adv))

    for t in [1, 2, 3]:
        tiers[t].sort(key=lambda x: -x[1])
        label = {1: '>$50M', 2: '$10-50M', 3: '$5-10M'}[t]
        print(f"\nTier {t} ({label} ADV): {len(tiers[t])} tokens")
        for tk, adv in tiers[t]:
            print(f"  {tk:10s} ADV=${adv/1e6:.1f}M")

    all_liquid = [t[0] for t in tiers[1] + tiers[2] + tiers[3]]
    print(f"\nTotal liquid: {len(all_liquid)} tokens")
    print(f"Update TIER1/TIER2/TIER3 lists in this file with the above.")


if __name__ == '__main__':
    refresh_universe()
