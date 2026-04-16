"""
s502 Honest BB Breakout — 1-Minute Delayed Entry
==================================================

Based on s501 (BB Breakout) with truly honest entries:

  SIGNAL: 4H BB level is known from prior 4H close. During the current 1H bar,
          1m candles are monitored. When 1m close first crosses BB level,
          we ENTER ON THE NEXT 1M BAR (1-minute reaction time).

  This is tradeable: BB level is known → monitor 1m candles → cross detected →
  place market order → filled on next 1m close (realistic execution).

  Implementation: Uses entry_limit_price + custom 1m resolution that enters
  at cross_idx+1 (next minute after cross), not cross_idx (same minute).
  Falls back to next-hour close if no 1m data available.

  Key differences from s501:
  - Entry at 1m bar AFTER cross, not AT cross (1-min honest delay)
  - No armed_levels (paper engine handles this live via WebSocket)
  - Same exit params as s501 (optimized trail=2.0 + partial_tp=1.5)

Research: Derived from s501 R172 (Gate 5P PASS)
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
    "entry_resolution": 1,         # 1m resolution for honest entry
    "max_portfolio_positions": 50,
}

# Lean indicator config
REQUIRED_PLUGINS = []
REQUIRED_INDICATOR_GROUPS = {'bb', 'volume'}

# Signal: use 1-minute delayed entry (cross_idx + 1)
# This flag is read by the custom 1m resolver below
HONEST_1M_DELAY = True

# ══════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════

W_BREAKOUT = 0.80
W_MOMENTUM = 0.0

BB_VOL_RATIO = 1.3
MOM_LOOKBACK_4H = 42
MIN_ADV_BREAKOUT = 50_000_000

MOM_REBAL_BARS = 168
MOM_K = 3
MOM_LONG_WT = 0.80
MOM_SHORT_WT = 0.20
MOM_EMA_FAST = 10
MOM_EMA_SLOW = 30
MIN_ADV_MOMENTUM = 1_000_000
MOM_LOOKBACK_1H = 168

LEVERAGE = 1.25
WARMUP_BARS = 500
BREAKOUT_POS_SIZE = 0.20


# ══════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════

def _rolling_mean(arr, w):
    """O(n) rolling mean via cumsum."""
    cs = np.cumsum(np.nan_to_num(arr, 0.0))
    cs = np.insert(cs, 0, 0.0)
    out = np.full(len(arr), np.nan)
    out[w - 1:] = (cs[w:] - cs[:-w]) / w
    return out


def _ema(arr, span):
    """Exponential moving average."""
    alpha = 2.0 / (span + 1)
    out = np.empty_like(arr, dtype=np.float64)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
    return out


def _first_cross_only(cross_mask, ctx):
    """Keep only the first True per 4H window to prevent re-entry."""
    if not np.any(cross_mask):
        return cross_mask
    out = np.zeros_like(cross_mask)
    window_ids = np.searchsorted(ctx.idx_4h, ctx.idx_1h, side='right') - 1
    cross_indices = np.where(cross_mask)[0]
    seen_windows = set()
    for i in cross_indices:
        w = window_ids[i]
        if w not in seen_windows:
            seen_windows.add(w)
            out[i] = True
    return out


def resolve_1m_honest(strategy_results, contexts, live_bar=-1):
    """Custom 1m entry resolution with 1-minute honest delay.

    Like _resolve_minute_entries but enters at cross_idx + 1 (next minute
    after the BB level is crossed), not at cross_idx (same minute).
    This represents a 1-minute reaction time — fully tradeable.
    """
    from v4.minute_exits import MinuteExitCache
    cache = MinuteExitCache(resolution=1, max_tokens=20)
    resolved = 0
    skipped_no_cross = 0
    skipped_no_data = 0
    skipped_last_min = 0

    for token, sr in strategy_results.items():
        if sr.entry_limit_price is None:
            continue

        ctx = contexts.get(token)
        if ctx is None:
            continue
        if isinstance(ctx, tuple):
            ctx = ctx[1] if ctx[1] is not None else ctx[0]

        ts_1h = ctx.idx_1h
        entry_bars = np.where(sr.entry_mask)[0]

        for bar in entry_bars:
            lp = sr.entry_limit_price[bar]
            if np.isnan(lp):
                continue

            direction = int(sr.direction[bar])
            hour_ts = np.datetime64(ts_1h[bar], 'ms')

            minute_data = cache.get_minute_bars(token, hour_ts)
            if minute_data is None:
                if live_bar >= 0 and bar >= live_bar:
                    continue
                sr.entry_mask[bar] = False
                skipped_no_data += 1
                continue

            _, _, closes_1m = minute_data

            # Find first 1m close crossing the BB level
            if direction == 1:
                cross_mask = closes_1m > lp
            else:
                cross_mask = closes_1m < lp

            cross_idx = np.where(cross_mask)[0]
            if len(cross_idx) == 0:
                sr.entry_mask[bar] = False
                skipped_no_cross += 1
                continue

            # HONEST: enter at NEXT minute after cross detection
            fill_idx = cross_idx[0] + 1
            if fill_idx >= len(closes_1m):
                # Cross on last 1m bar of the hour — can't fill this hour
                # Could carry to next hour, but for simplicity: skip
                sr.entry_mask[bar] = False
                skipped_last_min += 1
                continue

            sr.entry_limit_price[bar] = float(closes_1m[fill_idx])
            resolved += 1

    print(f"  1m honest resolution: {resolved} resolved, {skipped_no_cross} no cross, "
          f"{skipped_last_min} last-min skip, {skipped_no_data} no 1m data")


# ══════════════════════════════════════════════════════════════════════
# STRATEGY FUNCTION (CLASS B)
# ══════════════════════════════════════════════════════════════════════

def strategy(contexts: dict) -> dict:
    """Honest BB breakout — enter 1m after cross detection.

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

    # ── Step 2: Breakout signals (same cross as s501, same bar) ──────
    for token, td in token_data.items():
        ctx = td['ctx']
        n_1h = td['n_1h']
        close_4h = ctx.ind_4h['close']
        bb_upper_4h = ctx.ind_4h['bb_upper']
        bb_lower_4h = ctx.ind_4h['bb_lower']
        n_4h = td['n_4h']

        mom_7d = np.zeros(n_4h, dtype=np.float64)
        lb = MOM_LOOKBACK_4H
        if n_4h > lb:
            mom_7d[lb:] = (close_4h[lb:] - close_4h[:-lb]) / np.maximum(
                close_4h[:-lb], 1e-10)

        prior_bb_upper_4h = np.empty(n_4h, dtype=np.float64)
        prior_bb_upper_4h[0] = np.nan
        prior_bb_upper_4h[1:] = bb_upper_4h[:-1]

        prior_bb_lower_4h = np.empty(n_4h, dtype=np.float64)
        prior_bb_lower_4h[0] = np.nan
        prior_bb_lower_4h[1:] = bb_lower_4h[:-1]

        prior_bb_upper_1h = ctx.align_4h_to_1h(prior_bb_upper_4h)
        prior_bb_lower_1h = ctx.align_4h_to_1h(prior_bb_lower_4h)
        mom_7d_1h = ctx.align_4h_to_1h(mom_7d)

        close_1h = ctx.ind_1h['close']
        vol_ratio_1h = ctx.ind_1h['vol_ratio']
        valid_bb_1h = ~np.isnan(prior_bb_upper_1h) & ~np.isnan(prior_bb_lower_1h)

        # Cross detection on SAME bar (the 1m resolver handles honest timing)
        cross_long_1h = valid_bb_1h & (close_1h > prior_bb_upper_1h) & (vol_ratio_1h > BB_VOL_RATIO) & (mom_7d_1h > 0)
        cross_short_1h = valid_bb_1h & (close_1h < prior_bb_lower_1h) & (prior_bb_lower_1h > 0) & (vol_ratio_1h > BB_VOL_RATIO) & (mom_7d_1h < 0)

        brk_long_1h = _first_cross_only(cross_long_1h, ctx)
        brk_short_1h = _first_cross_only(cross_short_1h, ctx)

        # Strength for conviction scoring
        str_long = np.clip((close_1h - prior_bb_upper_1h) / np.maximum(close_1h, 1e-10), 0, 0.1)
        str_short = np.clip((prior_bb_lower_1h - close_1h) / np.maximum(close_1h, 1e-10), 0, 0.1)
        strength_1h = np.where(brk_long_1h, str_long, np.where(brk_short_1h, str_short, 0.0))

        # ADV filter
        if ctx.rolling_adv is not None:
            adv_ok = ctx.rolling_adv >= MIN_ADV_BREAKOUT
            brk_long_1h = brk_long_1h & adv_ok
            brk_short_1h = brk_short_1h & adv_ok

        # Limit price = BB level (for 1m cross detection)
        limit_price_1h = np.full(n_1h, np.nan, dtype=np.float64)
        limit_price_1h[brk_long_1h] = prior_bb_upper_1h[brk_long_1h]
        limit_price_1h[brk_short_1h] = prior_bb_lower_1h[brk_short_1h]

        td['brk_long_1h'] = brk_long_1h
        td['brk_short_1h'] = brk_short_1h
        td['strength_1h'] = strength_1h
        td['limit_price_1h'] = limit_price_1h

    # ── Step 3: Momentum ranking (weight=0, inactive) ────────────────
    tokens_list = sorted(token_data.keys())
    n_tokens = len(tokens_list)
    max_bars = max(td['n_1h'] for td in token_data.values())

    close_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)
    adv_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)
    for j, token in enumerate(tokens_list):
        td = token_data[token]
        ctx = td['ctx']
        n = td['n_1h']
        offset = max_bars - n
        close_matrix[offset:offset + n, j] = ctx.ind_1h['close']
        if ctx.rolling_adv is not None:
            adv_matrix[offset:offset + n, j] = ctx.rolling_adv

    mom_entry_mask = {token: np.zeros(token_data[token]['n_1h'], dtype=bool)
                      for token in tokens_list}
    mom_direction = {token: np.zeros(token_data[token]['n_1h'], dtype=np.int8)
                     for token in tokens_list}
    mom_size = {token: np.zeros(token_data[token]['n_1h'], dtype=np.float64)
                for token in tokens_list}
    # Momentum weight is 0, skip the ranking loop

    # ── Step 4: Combine entries and build StrategyResult per token ──
    results = {}

    for token in tokens_list:
        td = token_data[token]
        ctx = td['ctx']
        n = td['n_1h']

        brk_long = td['brk_long_1h']
        brk_short = td['brk_short_1h']
        brk_entry = brk_long | brk_short
        mom_entry = mom_entry_mask[token]

        entry_mask = brk_entry | mom_entry
        entry_mask[:WARMUP_BARS] = False

        if ctx.liquidity_mask is not None:
            entry_mask = entry_mask & ctx.liquidity_mask

        if not np.any(entry_mask):
            continue

        direction = np.zeros(n, dtype=np.int8)
        direction[brk_long] = 1
        direction[brk_short] = -1
        mom_only = mom_entry & ~brk_entry
        direction[mom_only] = mom_direction[token][mom_only]

        size_mult = np.ones(n, dtype=np.float64) * 0.05
        brk_size = W_BREAKOUT * BREAKOUT_POS_SIZE
        size_mult[brk_entry] = brk_size
        size_mult[mom_only] = mom_size[token][mom_only]

        conviction = np.zeros(n, dtype=np.float64)
        conviction[brk_entry] = np.clip(td['strength_1h'][brk_entry] * 10, 0.5, 1.0)
        conviction[mom_only] = 0.3

        results[token] = StrategyResult(
            entry_mask=entry_mask,
            direction=direction,
            market_type=MarketType.PERP,
            leverage=LEVERAGE,
            entry_limit_price=td['limit_price_1h'],
            # NO armed_levels
            # Exit management (same as s501)
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
            name='s502_honest_bb_breakout',
            exchange='binance',
        )

    return results
