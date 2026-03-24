"""
V3 Momentum Strategy — 20/50 EMA + Positioning + VRP Overlays (BTC Only, Spot)
===============================================================================

Production implementation of the validated V3 momentum strategy.

Signal stack:
  Layer 1: Regime filter — exclude CRISIS
  Layer 2: EMA crossover base (long when 20d EMA > 50d EMA, pre-computed by engine)
  Layer 3: Positioning overlay (ctx.custom['pos_mult'], 0.3x-1.5x)
  Layer 4: VRP overlay (ctx.custom['vrp_mult'], 0.3x-1.3x)
  Compose: clip(base * pos_mult * vrp_mult, 0, 1.5)
  Weekly rebalance (168 bars), 90d warmup (2160 bars), no hard stops.

All overlay data is pre-computed by engine plugins (_compute_positioning_overlay,
_compute_vrp_overlay) and available in ctx.custom. This strategy does NO file I/O.

Validation (OOS): Return +17.52%, Sharpe 0.56, MaxDD -20.2%
"""

import numpy as np
from engine import StrategyContext, StrategyResult, MarketType, CRISIS

# =============================================================================
# Constants (locked from research validation)
# =============================================================================

REBALANCE_BARS = 168       # Weekly rebalance (7 * 24h)
WARMUP_BARS = 2160         # 90 days * 24 hours
MIN_POSITION = 0.0
MAX_POSITION = 1.5


def strategy(ctx: StrategyContext) -> StrategyResult:
    """V3 Momentum: EMA base + Positioning overlay + VRP overlay. BTC only."""
    n = len(ctx.ind_1h['close'])

    # ── BTC-ONLY FILTER ──────────────────────────────────────────────
    if ctx.ticker != 'BTC':
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.ones(n, dtype=np.int8),
            name='v3_momentum_overlays',
            stop_mult=99.0,
            trail_mult=99.0,
            target_mult=999.0,
            max_hold=REBALANCE_BARS,
            min_hold=24,
            exit_regimes={CRISIS},
            market_type=MarketType.SPOT,
            leverage=1.0,
            size_multiplier=np.zeros(n, dtype=np.float64),
            conviction_score=np.zeros(n, dtype=np.float64),
            breakeven_atr=0.0,
        )

    # ── DAILY BAR SIGNALS ────────────────────────────────────────────
    # Use pre-computed EMAs from engine (adjust=False, matching prototype)
    ema_20 = ctx.ind_d['ema_20']
    ema_50 = ctx.ind_d['ema_50']

    # Base signal: long (1.0) when fast EMA > slow EMA, flat (0.0) otherwise
    base_signal = np.where(ema_20 > ema_50, 1.0, 0.0)

    # ── OVERLAY MULTIPLIERS (pre-computed by engine plugins) ─────────
    pos_mult = ctx.custom.get('pos_mult', np.ones(n, dtype=np.float64))
    vrp_mult = ctx.custom.get('vrp_mult', np.ones(n, dtype=np.float64))

    # ── COMPOSE FINAL POSITION (daily) ───────────────────────────────
    # Align daily base signal to 1H, then multiply by 1H overlays
    base_1h = ctx.align_daily_to_1h(base_signal)
    final_hourly = np.clip(base_1h * pos_mult * vrp_mult, MIN_POSITION, MAX_POSITION)

    # ── REGIME FILTER ────────────────────────────────────────────────
    regime_ok = ctx.regime_1h != CRISIS
    final_hourly = np.where(regime_ok, final_hourly, 0.0)

    # ── ENTRY MASK: weekly rebalance after warmup ────────────────────
    entry_mask = np.zeros(n, dtype=bool)
    for rb in range(WARMUP_BARS, n, REBALANCE_BARS):
        if final_hourly[rb] > 0:
            end = min(rb + REBALANCE_BARS, n)
            entry_mask[rb:end] = True

    # Warmup guard
    entry_mask[:WARMUP_BARS] = False

    # Liquidity filter
    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    # ── SIZE MULTIPLIER & CONVICTION ─────────────────────────────────
    size_mult = final_hourly.copy()
    conviction = np.clip(size_mult / MAX_POSITION, 0.0, 1.0)

    return StrategyResult(
        entry_mask=entry_mask,
        direction=np.ones(n, dtype=np.int8),
        stop_mult=99.0,
        trail_mult=99.0,
        target_mult=999.0,
        min_hold=24,
        max_hold=REBALANCE_BARS,
        exit_regimes={CRISIS},
        name='v3_momentum_overlays',
        size_multiplier=size_mult,
        conviction_score=conviction,
        market_type=MarketType.SPOT,
        leverage=1.0,
        breakeven_atr=0.0,
    )
