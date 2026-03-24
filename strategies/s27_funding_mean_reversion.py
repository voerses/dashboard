"""
Strategy S27: Funding Rate Mean Reversion (Perp)
=================================================
Gate 1 IC: Signal uses funding rate extremes — perp-native signal.

Hypothesis: Extreme funding rates indicate crowded positioning.
High positive funding = longs overcrowded, paying shorts → SHORT (fade longs).
High negative funding = shorts overcrowded, paying longs → LONG (fade shorts).

Causal chain: funding → basis → ret_1 (3-8h lag from Granger analysis).
Funding extremes are a leading signal for position unwinds.

Signal: When funding rate z-score exceeds +/-2.0, fade the crowd.
        Requires trend confirmation (ADX > 20) and vol confirmation.

Market: PERP (leveraged, both directions)
Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND,
                    rolling_mean, rolling_std)


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Funding rate mean reversion — fade crowded positioning on perp."""
    n = len(ctx.ind_1h['close'])
    close = ctx.ind_1h['close']
    adx = ctx.ind_1h['adx']
    vol_ratio = ctx.ind_1h['vol_ratio']
    regime = ctx.regime_1h

    # Get funding rate (perp-only signal)
    funding = ctx.funding_1h
    if funding is None:
        # No funding data — return empty
        entry = np.zeros(n, dtype=bool)
        return StrategyResult(
            entry_mask=entry,
            direction=np.zeros(n, dtype=np.int8),
            stop_mult=3.0, trail_mult=2.5, target_mult=999,
            no_stop_bars=8, min_hold=8, max_hold=168, edge=0.25,
            exit_regimes={CRISIS}, name='funding_mean_reversion',
            market_type=MarketType.PERP, leverage=2.0, exchange='binance',
            breakeven_atr=0.5,
        )

    # ── LAYER 1: REGIME FILTER ──
    regime_ok = regime != 0  # exclude CRISIS

    # ── LAYER 2: FUNDING EXTREME DETECTION ──
    # Per-hour funding: typical ~0.0000125 (0.01%/8h), extreme >0.0001 (0.08%/8h)
    # Absolute thresholds at ~P95/P5 levels across tokens
    fund_high = funding > 0.00006    # extreme positive → longs overcrowded → short
    fund_low = funding < -0.00002    # extreme negative → shorts overcrowded → long

    # ── LAYER 3: TREND CONFIRMATION ──
    trend_present = adx > 20

    # ── LAYER 4: VOLUME CONFIRMATION ──
    vol_elevated = vol_ratio > 1.2  # at least some volume

    # Combined entry
    entry_short = regime_ok & fund_high & trend_present & vol_elevated
    entry_long = regime_ok & fund_low & trend_present & vol_elevated

    # Direction
    direction = np.where(entry_long, 1,
                np.where(entry_short, -1, 0)).astype(np.int8)

    entry = entry_long | entry_short
    entry[:200] = False

    return StrategyResult(
        entry_mask=entry,
        direction=direction,

        # Funding MR parameters
        stop_mult=3.5,       # 3.5x ATR (wide — funding unwinds take time)
        trail_mult=2.5,      # 2.5x ATR trailing
        target_mult=999,     # Trail only
        no_stop_bars=12,     # 12h protection (funding takes time to normalize)
        min_hold=12,         # Min 12h
        max_hold=168,        # Max 7 days
        edge=0.25,           # Conservative edge estimate

        exit_regimes={CRISIS},
        name='funding_mean_reversion',

        # PERP settings
        market_type=MarketType.PERP,
        leverage=2.0,
        exchange='binance',
        breakeven_atr=0.5,
    )
