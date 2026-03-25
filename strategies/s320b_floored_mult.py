"""
V3 Momentum Strategy — Option B: Floored Multiplier (BTC Only, Spot)
=====================================================================

Identical to s320_v3_momentum_overlays except:
  The final size_multiplier is floored at 0.3 whenever the base signal is
  active (>0).  This prevents overlay scaling from pushing entries below
  V4's min_position_usd=$200 floor while preserving the continuous overlay
  signal structure.

Compose change (vs s320):
  OLD: clip(base * pos_mult * vrp_mult, 0.0, 1.0)
  NEW: where base > 0 → clip(base * pos_mult * vrp_mult, 0.3, 1.0)
       where base == 0 → 0.0

Everything else unchanged: 20/50 EMA base, RSI timing, weekly rebalance,
positioning overlay, VRP overlay.
"""

import numpy as np
from engine import StrategyContext, StrategyResult, MarketType, CRISIS

# =============================================================================
# Constants (locked from research validation)
# =============================================================================

REBALANCE_BARS = 168       # Weekly rebalance (7 * 24h)
WARMUP_BARS = 2160         # 90 days * 24 hours
MIN_POSITION = 0.3         # Floored minimum when signal is active
MAX_POSITION = 1.0
RSI_CROSS_THRESH = 35.0    # 4h RSI cross-up threshold (R111: optimal, 30-40 all work)


def strategy(ctx: StrategyContext) -> StrategyResult:
    """V3 Momentum: EMA base + Positioning overlay + VRP overlay. BTC only.
    Option B: Floor size_multiplier at 0.3 when signal is active."""
    n = len(ctx.ind_1h['close'])

    # ── BTC-ONLY FILTER ──────────────────────────────────────────────
    if ctx.ticker != 'BTC':
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.ones(n, dtype=np.int8),
            name='s320b_floored_mult',
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

    # ── OVERLAY MULTIPLIERS (pre-computed by engine plugins) ─────────
    pos_mult = ctx.custom.get('pos_mult', np.ones(n, dtype=np.float64))
    vrp_mult = ctx.custom.get('vrp_mult', np.ones(n, dtype=np.float64))

    # ── COMPOSE FINAL POSITION (daily) ───────────────────────────────
    # Align daily base signal to 1H, then multiply by 1H overlays
    base_1h = ctx.align_daily_to_1h(base_signal)
    raw_product = base_1h * pos_mult * vrp_mult

    # Option B: Floor at 0.3 when base signal is active, keep 0.0 when flat
    # Where base_1h > 0 (signal active): clip to [0.3, 1.0]
    # Where base_1h == 0 (signal flat): keep 0.0
    signal_active = base_1h > 0
    final_hourly = np.where(
        signal_active,
        np.clip(raw_product, MIN_POSITION, MAX_POSITION),
        0.0,
    )

    # ── REGIME FILTER ────────────────────────────────────────────────
    regime_ok = ctx.regime_1h != CRISIS
    final_hourly = np.where(regime_ok, final_hourly, 0.0)

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
    # For each rebalance window where signal is long, defer entry to
    # first 4h RSI cross-up through 35. Fallback to rebalance bar.
    # Uses searchsorted on cross indices for O(W * log(C)) lookup.
    cross_indices = np.flatnonzero(rsi_cross_pulse)
    entry_mask = np.zeros(n, dtype=bool)
    for rb in range(WARMUP_BARS, n, REBALANCE_BARS):
        if final_hourly[rb] > 0:
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

    # ── SIZE MULTIPLIER & CONVICTION ─────────────────────────────────
    size_mult = final_hourly.copy()
    conviction = np.clip(size_mult / MAX_POSITION, 0.0, 1.0)

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
        name='s320b_floored_mult',
        size_multiplier=size_mult,
        conviction_score=conviction,
        market_type=MarketType.SPOT,
        leverage=1.0,
        breakeven_atr=0.0,
        cap_multiplier=8.0,
        max_trade_pct=0.95,
    )
