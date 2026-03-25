"""
Option A: Binary Gate Overlays — 20/50 EMA + Positioning/VRP Gates (BTC Only, Spot)
===================================================================================

Fork of s320 (v3_momentum_overlays) that converts overlays from continuous
size multipliers to binary entry gates. This fixes the V4 sizing rejection
problem where continuous scaling (0.3x-1.5x) multiplied into Kelly sizing
caused 86% of entries to fall below min_position_usd=$200.

Architecture change:
  OLD (s320): size_multiplier = base * pos_mult * vrp_mult  (0.0 to 1.0 continuous)
  NEW (s320a): overlays GATE entry (skip or enter), size_multiplier = 1.0 when entering

Gate logic:
  - Positioning z-score > 0.5 (crowd long = bearish) -> SKIP entry (entry_mask=False)
  - Positioning z-score <= 0.5 (neutral/short = bullish) -> ENTER at full size
  - VRP z-score < -0.5 (vol cheap = turbulence) -> SKIP entry
  - VRP z-score >= -0.5 (normal/expensive vol) -> ENTER at full size
  - When entering: size_multiplier = 1.0 (no continuous scaling)

Everything else is identical to s320:
  - 20/50 EMA base signal
  - RSI 35 entry timing within weekly rebalance windows
  - Weekly rebalance (168 bars), 90d warmup (2160 bars)
  - MarketType.SPOT, leverage=1.0
  - cap_multiplier=8.0, max_trade_pct=0.95
  - CRISIS regime exit
"""

import numpy as np
from engine import StrategyContext, StrategyResult, MarketType, CRISIS

# =============================================================================
# Constants (locked from research validation, same as s320)
# =============================================================================

REBALANCE_BARS = 168       # Weekly rebalance (7 * 24h)
WARMUP_BARS = 2160         # 90 days * 24 hours
RSI_CROSS_THRESH = 35.0    # 4h RSI cross-up threshold (R111: optimal, 30-40 all work)

# Binary gate thresholds
POS_ZSCORE_GATE = 0.5      # Skip entry if positioning z-score > this (crowd long)
VRP_ZSCORE_GATE = -0.5     # Skip entry if VRP z-score < this (vol cheap = turbulence)


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Option A: EMA base + binary positioning/VRP gates. BTC only, spot."""
    n = len(ctx.ind_1h['close'])

    # ── BTC-ONLY FILTER ──────────────────────────────────────────────
    if ctx.ticker != 'BTC':
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.ones(n, dtype=np.int8),
            name='s320a_binary_gates',
            stop_mult=99.0,
            trail_mult=99.0,
            target_mult=999.0,
            no_stop_bars=24,
            max_hold=REBALANCE_BARS,
            min_hold=24,
            edge=0.40,
            exit_regimes={CRISIS},
            market_type=MarketType.SPOT,
            leverage=1.0,
            size_multiplier=np.zeros(n, dtype=np.float64),
            conviction_score=np.zeros(n, dtype=np.float64),
            breakeven_atr=0.0,
            cap_multiplier=1.0,
            max_trade_pct=0.0,
        )

    # ── DAILY BAR SIGNALS ────────────────────────────────────────────
    # Use pre-computed EMAs from engine (adjust=False, matching prototype)
    ema_20 = ctx.ind_d['ema_20']
    ema_50 = ctx.ind_d['ema_50']

    # Base signal: long (1.0) when fast EMA > slow EMA, flat (0.0) otherwise
    base_signal = np.where(ema_20 > ema_50, 1.0, 0.0)

    # ── BINARY GATE: POSITIONING ──────────────────────────────────────
    # Instead of continuous pos_mult scaling, use z-score as binary gate.
    # pos_mult encodes positioning: high values = crowd long = bearish.
    # We extract the z-score from the raw overlay data if available,
    # otherwise derive the gate from pos_mult itself.
    #
    # In s320, pos_mult ranges 0.3-1.0 where:
    #   pos_mult=0.3 means crowd is very long (bearish, z-score high)
    #   pos_mult=1.0 means crowd is neutral/short (bullish, z-score low)
    # The gate: skip entry when pos_mult is low (crowd long).
    # pos_mult < 0.5 roughly corresponds to positioning z-score > 0.5.
    pos_mult = ctx.custom.get('pos_mult', np.ones(n, dtype=np.float64))
    # Gate: pos_mult >= 0.5 means positioning is acceptable (not too crowded long)
    pos_gate = pos_mult >= 0.5  # True = enter, False = skip

    # ── BINARY GATE: VRP ──────────────────────────────────────────────
    # In s320, vrp_mult ranges 0.3-1.3 where:
    #   vrp_mult=0.3 means vol is cheap / turbulence (vrp_z < -0.5)
    #   vrp_mult=1.0+ means vol is normal/expensive (safe to enter)
    # Gate: vrp_mult >= 0.7 means VRP is acceptable.
    vrp_mult = ctx.custom.get('vrp_mult', np.ones(n, dtype=np.float64))
    # Gate: vrp_mult >= 0.7 roughly corresponds to vrp_z >= -0.5
    vrp_gate = vrp_mult >= 0.7  # True = enter, False = skip

    # ── COMPOSE DAILY BASE → 1H ────────────────────────────────────
    base_1h = ctx.align_daily_to_1h(base_signal)

    # ── REGIME FILTER ────────────────────────────────────────────────
    regime_ok = ctx.regime_1h != CRISIS
    base_active = (base_1h > 0) & regime_ok

    # ── RSI CROSS DETECTION (4h → 1h) ────────────────────────────
    # Pre-compute 4h RSI cross-up through threshold, aligned to 1h grid
    rsi_4h = ctx.ind_4h['rsi']
    rsi_4h_prev = np.roll(rsi_4h, 1)
    rsi_4h_prev[0] = 50.0  # neutral default
    cross_up_4h = (rsi_4h_prev <= RSI_CROSS_THRESH) & (rsi_4h > RSI_CROSS_THRESH)
    cross_up_1h = ctx.align_4h_to_1h(cross_up_4h.astype(np.float64)) > 0.5
    # Pulse: only first 1h bar of each 4h cross (avoid 4 duplicate entries)
    cross_prev = np.roll(cross_up_1h, 1)
    cross_prev[0] = False
    rsi_cross_pulse = cross_up_1h & ~cross_prev

    # ── ENTRY MASK: RSI-timed weekly rebalance after warmup ────
    # For each rebalance window where base signal is active and gates pass,
    # defer entry to first 4h RSI cross-up through 35. Fallback to rebalance bar.
    cross_indices = np.flatnonzero(rsi_cross_pulse)
    entry_mask = np.zeros(n, dtype=bool)
    for rb in range(WARMUP_BARS, n, REBALANCE_BARS):
        if not base_active[rb]:
            continue

        # Binary gates: check at rebalance bar
        if not pos_gate[rb]:
            continue  # Positioning too crowded — skip entire window
        if not vrp_gate[rb]:
            continue  # VRP too cheap / turbulent — skip entire window

        window_end = min(rb + REBALANCE_BARS, n)
        # Find first cross index >= rb
        idx = np.searchsorted(cross_indices, rb)
        if idx < len(cross_indices) and cross_indices[idx] < window_end:
            entry_bar = cross_indices[idx]
        else:
            entry_bar = rb  # fallback
        end = min(entry_bar + (window_end - rb), n)
        entry_mask[entry_bar:end] = True

    # Warmup guard
    entry_mask[:WARMUP_BARS] = False

    # Liquidity filter
    if ctx.liquidity_mask is not None:
        entry_mask = entry_mask & ctx.liquidity_mask

    # ── SIZE MULTIPLIER: ALWAYS 1.0 WHEN ENTERING ──────────────────
    # This is the key change: no continuous scaling. Binary gates already
    # filtered out bad entries, so all remaining entries get full size.
    size_mult = np.where(entry_mask, 1.0, 0.0)
    conviction = np.where(entry_mask, 1.0, 0.0)

    return StrategyResult(
        entry_mask=entry_mask,
        direction=np.ones(n, dtype=np.int8),
        stop_mult=99.0,
        trail_mult=99.0,
        target_mult=999.0,
        no_stop_bars=24,
        min_hold=24,
        max_hold=REBALANCE_BARS,
        edge=0.40,
        exit_regimes={CRISIS},
        name='s320a_binary_gates',
        size_multiplier=size_mult,
        conviction_score=conviction,
        market_type=MarketType.SPOT,
        leverage=1.0,
        breakeven_atr=0.0,
        cap_multiplier=8.0,
        max_trade_pct=0.95,
    )
