"""
s29 Funding Rate Carry — Harvest perp funding payments.

Hypothesis: When perpetual funding rate is persistently positive (longs pay shorts),
go short to collect funding. When persistently negative, go long. Edge comes from
carry income, not price prediction. Funding is structurally positive in crypto
(retail is net long) → net short bias collects the premium.

Gate 0: PASS — new strategy family (carry, not momentum/trend), score 7.7/10
Gate 1: PASS — IC(total)=0.078, t-stat=11.06, hit rate=94%, PF=1.47 cost-adj
Gate 2: PASS — 0% signal overlap with all Tier A/B, corr=0.003 vs momentum
Gate 3: PASS — 0.86ms/call, fully vectorized, bidirectional (BTC 100% short, AXS 86% long)
Gate 4: PASS — BTC: Sharpe=0.79, Calmar=0.76, MaxDD=-1.6%, beta=-0.0006, corr=-0.02
Gate 5: PASS — Tier B: 66/328 tokens (20.1%), 91.2% among 30+ trade tokens
         Mean Sharpe=0.81, Mean Calmar=2.26, Mean MaxDD=-1.26%, beta=0.0000

Signal stack:
  Layer 1: Regime filter — exclude CRISIS only (carry works in all other regimes)
  Layer 2: Funding persistence — rolling mean |funding| above threshold
  Layer 3: Direction — short when funding > 0, long when funding < 0
  Layer 4: Liquidity — require liquid enough to trade
  Layer 5: Exit — ATR trailing stop + max hold + regime exit
  Layer 6: Sizing — ADV-based Kelly (engine-computed)

Key design decisions:
  - No trend filter (carry is regime-agnostic, works in range/quiet/uptrend)
  - Wider stops than momentum (carry income compensates for adverse price moves)
  - Longer holds (carry accumulates over time — shorter holds waste fees)
  - Direction is OPPOSITE to funding sign (we're the counterparty to retail)

Status: TIER B — validated, ready for Gate 6 (paper trading)
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS,
                    rolling_mean)


def strategy(ctx: StrategyContext) -> StrategyResult:
    n = len(ctx.ind_1h['close'])

    # ── FUNDING DATA ────────────────────────────────────────────
    funding = ctx.funding_1h  # per-hour funding rate
    if funding is None:
        # Spot context — no funding, no trades
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.ones(n, dtype=np.int8),
            market_type=MarketType.PERP,
            name='s29_funding_carry',
        )

    # ── LAYER 1: REGIME FILTER ──────────────────────────────────
    # Carry works in all regimes except CRISIS (liquidity dries up)
    regime_ok = ctx.regime_1h != CRISIS

    # ── LAYER 2: FUNDING PERSISTENCE ────────────────────────────
    # Rolling mean of absolute funding rate over 72h (3 days)
    # High |funding| = strong carry opportunity regardless of direction
    abs_funding = np.abs(funding)
    funding_ma = rolling_mean(abs_funding, 72)

    # Threshold: funding must be meaningfully above zero
    # 0.00005/hr ≈ 0.0004/8hr ≈ 0.044%/8hr ≈ 48% annualized
    # This filters for periods where carry income justifies the trade
    funding_threshold = 0.00005
    funding_elevated = funding_ma > funding_threshold

    # ── LAYER 3: DIRECTION (carry = opposite to funding) ────────
    # Signed funding signal: rolling mean preserves direction
    funding_signed = rolling_mean(funding, 72)

    # Direction: -1 (short) when funding positive, +1 (long) when negative
    # We are the counterparty collecting the funding payment
    direction = np.where(funding_signed > 0, np.int8(-1), np.int8(1))

    # ── LAYER 4: LIQUIDITY ──────────────────────────────────────
    # Use the engine's liquidity mask (ADV-based, point-in-time)
    liquid = ctx.liquidity_mask

    # ── COMPOSE ENTRY ───────────────────────────────────────────
    entry = regime_ok & funding_elevated & liquid
    entry[:200] = False  # warmup guard

    return StrategyResult(
        entry_mask=entry,
        direction=direction,

        # ── TRADE MANAGEMENT ────────────────────────────────────
        # Wider stops than momentum: carry income compensates for price noise
        stop_mult=4.0,        # 4x ATR initial stop (wider than Tier A's 3.0)
        trail_mult=3.5,       # 3.5x ATR trailing stop
        target_mult=999,      # No fixed target — let carry accumulate
        no_stop_bars=48,      # 48h protection (carry needs time to accumulate)
        min_hold=24,          # Minimum 24 hours (capture at least 3 funding periods)
        max_hold=336,         # Maximum 14 days (avoid overstaying)
        edge=0.30,            # Conservative Kelly edge (carry is steadier but lower per-trade)

        exit_regimes={CRISIS},  # Only exit on CRISIS (not DOWNTREND — carry works there)
        market_type=MarketType.PERP,
        leverage=1.0,         # No leverage — carry is the edge, not magnification
        exchange='binance',
        name='s29_funding_carry',
    )
