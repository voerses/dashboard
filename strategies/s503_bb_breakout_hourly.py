"""
s503 BB Breakout — Forward 1m Entry After 4H Signal
=====================================================

Forward-only BB breakout: NO retrospection, NO intra-bar lookback.

  SIGNAL: 4H BB level is known from prior 4H close. Momentum (7d) is known
          from prior 4H data. At each 4H boundary, if momentum confirms
          direction, ARM the BB level as a limit for the next 4H window.

  ENTRY:  Monitor 1m bars going FORWARD from the 4H boundary. When the
          first 1m close crosses the BB level → enter at the NEXT 1m bar
          (+1 minute honest delay). This is fully tradeable — equivalent
          to placing a stop-limit order at the BB level.

  EXIT:   Trail 2.0 ATR + partial TP at 1.5 ATR (50%), max hold 4h.

  Key differences from s501/s502:
  - Signal fires at 4H boundary (not on the crossing 1H bar)
  - 1m resolution is FORWARD-ONLY (never looks back into completed bars)
  - Vol_ratio filter dropped (can't know hourly volume before hour ends)
  - entry_resolution=1 with custom forward resolver
  - +1 minute honest delay on entry

Research: Derived from s502 (Gate 3) — honest forward entry variant
Status: GATE 3 — Prototype
Market: PERP (bidirectional)
"""

import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET)

# Module-level marker for V4 portfolio adapter dispatch
STRATEGY_TYPE = "portfolio"

PORTFOLIO_CONFIG = {
    "max_positions": 50,
    "conviction_mode": "ranked",
    "entry_resolution": 1,         # 1m resolution — but forward-only via custom resolver
    "max_portfolio_positions": 50,
}

# Lean indicator config
REQUIRED_PLUGINS = []
REQUIRED_INDICATOR_GROUPS = {'bb', 'volume'}

# ══════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════

W_BREAKOUT = 0.80
MOM_LOOKBACK_4H = 42
MIN_ADV_BREAKOUT = 50_000_000

LEVERAGE = 1.25
WARMUP_BARS = 500
BREAKOUT_POS_SIZE = 0.20

# How many 1H bars after the 4H boundary to search for a 1m cross
FORWARD_SEARCH_HOURS = 4  # entire next 4H window


# ══════════════════════════════════════════════════════════════════════
# FORWARD 1M RESOLVER
# ══════════════════════════════════════════════════════════════════════

def resolve_1m_forward(strategy_results, contexts, live_bar=-1):
    """Forward-only 1m entry resolution — no retrospection.

    For each armed signal (entry at 4H boundary), search FORWARD through
    subsequent 1m bars to find the first cross of the BB level. Enter at
    cross_idx + 1 (1-minute honest delay).

    Unlike resolve_1m_honest (s502), this NEVER looks back into the signal
    bar's hour. It only checks future hours' 1m data.
    """
    from v4.minute_exits import MinuteExitCache
    cache = MinuteExitCache(resolution=1, max_tokens=20)
    resolved = 0
    skipped_no_cross = 0
    skipped_no_data = 0

    for token, sr in strategy_results.items():
        if sr.entry_limit_price is None:
            continue

        ctx = contexts.get(token)
        if ctx is None:
            continue
        if isinstance(ctx, tuple):
            ctx = ctx[1] if ctx[1] is not None else ctx[0]

        ts_1h = ctx.idx_1h
        n_1h = len(ts_1h)
        entry_bars = np.where(sr.entry_mask)[0]

        for bar in entry_bars:
            lp = sr.entry_limit_price[bar]
            if np.isnan(lp):
                sr.entry_mask[bar] = False
                continue

            direction = int(sr.direction[bar])
            found = False

            # Search forward: bar+0 through bar+FORWARD_SEARCH_HOURS-1
            # bar is already the FIRST 1H bar of the new 4H window
            for offset in range(FORWARD_SEARCH_HOURS):
                check_bar = bar + offset
                if check_bar >= n_1h:
                    break

                hour_ts = np.datetime64(ts_1h[check_bar], 'ms')
                minute_data = cache.get_minute_bars(token, hour_ts)
                if minute_data is None:
                    continue

                _, _, closes_1m = minute_data

                # Find first 1m close crossing the BB level
                if direction == 1:
                    cross_mask = closes_1m > lp
                else:
                    cross_mask = closes_1m < lp

                cross_idx = np.where(cross_mask)[0]
                if len(cross_idx) == 0:
                    continue

                # HONEST: enter at NEXT minute after cross detection
                fill_idx = cross_idx[0] + 1
                if fill_idx >= len(closes_1m):
                    # Cross on last 1m bar — try next hour
                    continue

                # Fill found! Update entry to the actual fill bar and price
                sr.entry_limit_price[bar] = float(closes_1m[fill_idx])
                found = True
                resolved += 1
                break

            if not found:
                sr.entry_mask[bar] = False
                skipped_no_cross += 1

    print(f"  1m forward resolution: {resolved} resolved, "
          f"{skipped_no_cross} no cross in {FORWARD_SEARCH_HOURS}h window, "
          f"{skipped_no_data} no 1m data")


# ══════════════════════════════════════════════════════════════════════
# STRATEGY FUNCTION (CLASS B)
# ══════════════════════════════════════════════════════════════════════

def strategy(contexts: dict) -> dict:
    """BB breakout — forward 1m entry after 4H signal.

    Signal logic:
      1. At each 4H boundary, check prior 4H bar's BB and momentum
      2. If momentum > 0 and BB upper is valid → arm long entry
      3. If momentum < 0 and BB lower is valid → arm short entry
      4. Entry armed on first 1H bar of next 4H window
      5. Forward 1m resolver finds the cross

    Args:
        contexts: {token: ctx_perp} or {token: (ctx_spot, ctx_perp)}

    Returns:
        {token: StrategyResult} for tokens with entry signals
    """
    # ── Step 1: Extract token data ──────────────────────────────────
    token_data = {}
    for token in sorted(contexts.keys()):
        ctx_pair = contexts[token]
        if isinstance(ctx_pair, tuple):
            ctx_spot, ctx_perp = ctx_pair
            ctx = ctx_perp if ctx_perp is not None else ctx_spot
        else:
            ctx = ctx_pair

        n_1h = len(ctx.ind_1h['close'])
        n_4h = len(ctx.ind_4h['close'])

        if n_1h < WARMUP_BARS or n_4h < MOM_LOOKBACK_4H + 20:
            continue

        token_data[token] = {
            'ctx': ctx,
            'n_1h': n_1h,
            'n_4h': n_4h,
        }

    if len(token_data) < 6:
        return {}

    # ── Step 2: Compute 4H-level signals ────────────────────────────
    for token, td in token_data.items():
        ctx = td['ctx']
        n_1h = td['n_1h']
        n_4h = td['n_4h']
        close_4h = ctx.ind_4h['close']
        bb_upper_4h = ctx.ind_4h['bb_upper']
        bb_lower_4h = ctx.ind_4h['bb_lower']

        # 7-day momentum on 4H (known from prior bars)
        mom_7d = np.zeros(n_4h, dtype=np.float64)
        lb = MOM_LOOKBACK_4H
        if n_4h > lb:
            mom_7d[lb:] = (close_4h[lb:] - close_4h[:-lb]) / np.maximum(
                close_4h[:-lb], 1e-10)

        # Prior 4H bar's BB levels (known at 4H boundary)
        prior_bb_upper_4h = np.empty(n_4h, dtype=np.float64)
        prior_bb_upper_4h[0] = np.nan
        prior_bb_upper_4h[1:] = bb_upper_4h[:-1]

        prior_bb_lower_4h = np.empty(n_4h, dtype=np.float64)
        prior_bb_lower_4h[0] = np.nan
        prior_bb_lower_4h[1:] = bb_lower_4h[:-1]

        # Align 4H signals to 1H bars
        prior_bb_upper_1h = ctx.align_4h_to_1h(prior_bb_upper_4h)
        prior_bb_lower_1h = ctx.align_4h_to_1h(prior_bb_lower_4h)
        mom_7d_1h = ctx.align_4h_to_1h(mom_7d)

        valid_bb_1h = ~np.isnan(prior_bb_upper_1h) & ~np.isnan(prior_bb_lower_1h)

        # Identify 4H boundary bars (first 1H bar of each new 4H window)
        window_ids = np.searchsorted(ctx.idx_4h, ctx.idx_1h, side='right') - 1
        is_4h_boundary = np.zeros(n_1h, dtype=bool)
        is_4h_boundary[0] = True
        is_4h_boundary[1:] = window_ids[1:] != window_ids[:-1]

        # ARM entry at 4H boundary when:
        # - BB level is valid (known from prior 4H)
        # - Momentum confirms direction (known from prior 4H)
        # No vol_ratio check — that requires future 1H data
        boundary_long = is_4h_boundary & valid_bb_1h & (mom_7d_1h > 0)
        boundary_short = is_4h_boundary & valid_bb_1h & (mom_7d_1h < 0) & (prior_bb_lower_1h > 0)

        # Spread armed signal across ALL 4 bars of the 4H window.
        # Standard 1m resolver checks each hour independently.
        # First hour with a 1m cross → fill. Simulator deduplicates
        # (won't open second position for same token).
        arm_long = np.zeros(n_1h, dtype=bool)
        arm_short = np.zeros(n_1h, dtype=bool)
        for offset in range(FORWARD_SEARCH_HOURS):
            shifted_long = np.zeros(n_1h, dtype=bool)
            shifted_short = np.zeros(n_1h, dtype=bool)
            if offset < n_1h:
                end = n_1h - offset
                shifted_long[offset:n_1h] = boundary_long[:end]
                shifted_short[offset:n_1h] = boundary_short[:end]
            arm_long |= shifted_long
            arm_short |= shifted_short

        # ADV filter (rolling ADV is known from prior bars)
        if ctx.rolling_adv is not None:
            adv_ok = ctx.rolling_adv >= MIN_ADV_BREAKOUT
            arm_long = arm_long & adv_ok
            arm_short = arm_short & adv_ok

        # Strength for conviction — use boundary bar's momentum magnitude
        close_1h = ctx.ind_1h['close']
        mom_abs = np.abs(mom_7d_1h)
        strength_1h = np.clip(mom_abs * 5, 0.01, 0.1)

        td['arm_long'] = arm_long
        td['arm_short'] = arm_short
        td['strength_1h'] = strength_1h
        td['prior_bb_upper_1h'] = prior_bb_upper_1h
        td['prior_bb_lower_1h'] = prior_bb_lower_1h

    # ── Step 3: Build StrategyResult per token ──────────────────────
    results = {}
    tokens_list = sorted(token_data.keys())

    for token in tokens_list:
        td = token_data[token]
        ctx = td['ctx']
        n = td['n_1h']

        arm_long = td['arm_long']
        arm_short = td['arm_short']
        entry_mask = arm_long | arm_short
        entry_mask[:WARMUP_BARS] = False

        if ctx.liquidity_mask is not None:
            entry_mask = entry_mask & ctx.liquidity_mask

        if not np.any(entry_mask):
            continue

        direction = np.zeros(n, dtype=np.int8)
        direction[arm_long] = 1
        direction[arm_short] = -1

        # Limit price = BB level (for forward 1m cross detection)
        limit_price = np.full(n, np.nan, dtype=np.float64)
        limit_price[arm_long] = td['prior_bb_upper_1h'][arm_long]
        limit_price[arm_short] = td['prior_bb_lower_1h'][arm_short]

        brk_size = W_BREAKOUT * BREAKOUT_POS_SIZE
        size_mult = np.ones(n, dtype=np.float64) * 0.05
        size_mult[entry_mask] = brk_size

        conviction = np.zeros(n, dtype=np.float64)
        conviction[entry_mask] = np.clip(td['strength_1h'][entry_mask] * 10, 0.5, 1.0)

        results[token] = StrategyResult(
            entry_mask=entry_mask,
            direction=direction,
            market_type=MarketType.PERP,
            leverage=LEVERAGE,
            entry_limit_price=limit_price,
            # Exit management (same as s501/s502)
            stop_mult=999.0,
            trail_mult=2.0,
            sma_trail_vals=None,
            target_mult=999.0,
            partial_tp_atr=1.5,
            partial_tp_pct=0.5,
            partial_tp_trail=2.0,
            breakeven_atr=0.0,
            no_stop_bars=0,
            min_hold=1,
            max_hold=4,
            # Sizing
            edge=0.50,
            size_multiplier=size_mult,
            cap_multiplier=2.5,
            conviction_score=conviction,
            # Regime
            exit_regimes=set(),
            bear_max_hold=0,
            # Metadata
            name='s503_bb_breakout_hourly',
            exchange='binance',
        )

    return results
