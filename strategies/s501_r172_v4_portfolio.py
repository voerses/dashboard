"""
s501 R172 V4 Portfolio — BB Breakout + Cross-Sectional Momentum (V4 Engine)
===========================================================================

V4 Class B port of s500_r172_portfolio.py. Two-component portfolio combining
uncorrelated alpha sources via the v4 engine's portfolio strategy adapter.

  Component 1 (80% weight): R160 BB Volatility Breakout on 4H bars
    - Entry: close > upper BB(20,2) AND vol_ratio > 1.3 AND 7d return > 0
    - Exit: SMA(15) trailing stop on 4H, or max 30d hold
    - Partial profit: 50% at 3 ATR, set breakeven stop
    - Max 5 concurrent breakout positions

  Component 2 (20% weight): R162 Cross-Sectional Momentum Rotation
    - Rank tokens by 7-day lookback return across full universe
    - Go long top K=3, short bottom K=3
    - 80/20 long/short within momentum allocation
    - EMA(10/30) trend filter, ATR volatility filter
    - Weekly (168 bar) rebalance

Research: R172 Gate 5P PASS — Sharpe 2.85, Calmar 13.12, MaxDD -15.7%
Status: GATE 6 — V4 Engine Validation
Market: PERP (bidirectional)
"""

import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET)

# Module-level marker for V4 portfolio adapter dispatch
STRATEGY_TYPE = "portfolio"

# Optimized portfolio config (from mega sweep + validation)
# These are read by portfolio_backtest.py and paper_config.py
PORTFOLIO_CONFIG = {
    "max_positions": 50,
    "conviction_mode": "ranked",
    "entry_resolution": 1,
    "max_portfolio_positions": 50,
}

# ══════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════

# Component weights (for size_multiplier scaling)
W_BREAKOUT = 0.80
W_MOMENTUM = 0.0

# R160 Breakout params (R167 optimized)
BB_PERIOD = 20           # Pre-computed in ind_4h as bb_upper/bb_lower
BB_VOL_RATIO = 1.3       # vol_ratio threshold (ind_4h['vol_ratio'])
MOM_LOOKBACK_4H = 42     # 7 days * 6 bars/day
TRAIL_SMA_PERIOD = 15    # SMA for trailing stop on 4H
PARTIAL_ATR_MULT = 6.0   # Take partial at 6 ATR (doubled: 1H ATR ~0.6% vs 4H ~1.2%)
PARTIAL_CLOSE_PCT = 0.5  # Close 50% at partial target
MAX_HOLD_1H = 720        # 30 days in 1H bars
BREAKOUT_MAX_POS = 5     # Max concurrent breakout positions
BREAKOUT_POS_SIZE = 0.20 # 20% of breakout allocation per position
MIN_ADV_BREAKOUT = 50_000_000

# Hybrid entry params (limit + runner escalation)
PULLBACK_TOL = 0.002     # 0.2% tolerance for limit fill detection
RUNNER_THRESHOLD = 0.005  # 0.5% above/below BB confirms runner
ENTRY_SCAN_BARS = 3       # scan next 3 bars after cross for entry

# R162 Momentum params
MOM_LOOKBACK_DAYS = 7
MOM_REBAL_BARS = 168     # 7 days in 1H bars
MOM_K = 3                # Top K / bottom K tokens
MOM_LONG_WT = 0.80
MOM_SHORT_WT = 0.20
MOM_EMA_FAST = 10
MOM_EMA_SLOW = 30
MIN_ADV_MOMENTUM = 1_000_000
MOM_LOOKBACK_1H = 168    # 7 days in 1H bars

# Leverage
LEVERAGE = 1.25

# Warmup: skip entries in first N 1H bars
WARMUP_BARS = 500


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


def _hybrid_entry_signals(cross_long, cross_short, close_1h, low_1h, high_1h,
                          prior_bb_upper_1h, prior_bb_lower_1h, ctx):
    """Hybrid limit+escalation entry for both long and short.

    After detecting a cross (bar T), scans bars T+1..T+ENTRY_SCAN_BARS:
      - Pullback entry: price returns to BB level (simulates limit fill)
      - Runner entry: price accelerates away (momentum escalation, T+1 only)
    One entry per 4H window. Pullback checked before runner on each bar.
    """
    n = len(cross_long)
    entry_long = np.zeros(n, dtype=bool)
    entry_short = np.zeros(n, dtype=bool)
    is_pullback = np.zeros(n, dtype=bool)

    window_ids = np.searchsorted(ctx.idx_4h, ctx.idx_1h, side='right') - 1

    # Process longs
    seen = set()
    for i in np.where(cross_long)[0]:
        w = window_ids[i]
        if w < 0 or w in seen:
            continue
        seen.add(w)
        bb = prior_bb_upper_1h[i]
        if np.isnan(bb):
            continue
        for offset in range(1, ENTRY_SCAN_BARS + 1):
            bar = i + offset
            if bar >= n:
                break
            # Pullback: low touched BB upper (limit fill)
            if low_1h[bar] <= bb * (1 + PULLBACK_TOL):
                entry_long[bar] = True
                is_pullback[bar] = True
                break
            # Runner: close well above BB upper (first bar only)
            if offset == 1 and close_1h[bar] > bb * (1 + RUNNER_THRESHOLD):
                entry_long[bar] = True
                break

    # Process shorts
    seen = set()
    for i in np.where(cross_short)[0]:
        w = window_ids[i]
        if w < 0 or w in seen:
            continue
        seen.add(w)
        bb = prior_bb_lower_1h[i]
        if np.isnan(bb):
            continue
        for offset in range(1, ENTRY_SCAN_BARS + 1):
            bar = i + offset
            if bar >= n:
                break
            # Pullback: high touched BB lower (limit fill)
            if high_1h[bar] >= bb * (1 - PULLBACK_TOL):
                entry_short[bar] = True
                is_pullback[bar] = True
                break
            # Runner: close well below BB lower (first bar only)
            if offset == 1 and close_1h[bar] < bb * (1 - RUNNER_THRESHOLD):
                entry_short[bar] = True
                break

    return entry_long, entry_short, is_pullback


# ══════════════════════════════════════════════════════════════════════
# STRATEGY FUNCTION (CLASS B)
# ══════════════════════════════════════════════════════════════════════

def strategy(contexts: dict) -> dict:
    """R172 combined portfolio strategy.

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

    # ── Step 2: Breakout signals (1H-resolution cross detection) ───
    for token, td in token_data.items():
        ctx = td['ctx']
        n_1h = td['n_1h']
        close_4h = ctx.ind_4h['close']
        bb_upper_4h = ctx.ind_4h['bb_upper']
        bb_lower_4h = ctx.ind_4h['bb_lower']
        n_4h = td['n_4h']

        # 7d momentum on 4H (42-bar return)
        mom_7d = np.zeros(n_4h, dtype=np.float64)
        lb = MOM_LOOKBACK_4H
        if n_4h > lb:
            mom_7d[lb:] = (close_4h[lb:] - close_4h[:-lb]) / np.maximum(
                close_4h[:-lb], 1e-10)

        # Prior 4H BB upper/lower, shifted by 1 on 4H grid
        # (known at start of current 4H window — point-in-time correct)
        prior_bb_upper_4h = np.empty(n_4h, dtype=np.float64)
        prior_bb_upper_4h[0] = np.nan
        prior_bb_upper_4h[1:] = bb_upper_4h[:-1]

        prior_bb_lower_4h = np.empty(n_4h, dtype=np.float64)
        prior_bb_lower_4h[0] = np.nan
        prior_bb_lower_4h[1:] = bb_lower_4h[:-1]

        # Align to 1H (ffill within each 4H window)
        prior_bb_upper_1h = ctx.align_4h_to_1h(prior_bb_upper_4h)
        prior_bb_lower_1h = ctx.align_4h_to_1h(prior_bb_lower_4h)

        # Align 7d momentum to 1H for 1H-resolution filtering
        mom_7d_1h = ctx.align_4h_to_1h(mom_7d)

        # 1H cross detection → direct entry on cross bar
        close_1h = ctx.ind_1h['close']
        low_1h = ctx.ind_1h['low']
        high_1h = ctx.ind_1h['high']
        vol_ratio_1h = ctx.ind_1h['vol_ratio']
        valid_bb_1h = ~np.isnan(prior_bb_upper_1h) & ~np.isnan(prior_bb_lower_1h)
        cross_long_1h = valid_bb_1h & (close_1h > prior_bb_upper_1h) & (vol_ratio_1h > BB_VOL_RATIO) & (mom_7d_1h > 0)
        cross_short_1h = valid_bb_1h & (close_1h < prior_bb_lower_1h) & (prior_bb_lower_1h > 0) & (vol_ratio_1h > BB_VOL_RATIO) & (mom_7d_1h < 0)

        # Deduplicate: first cross per 4H window (enter on cross bar itself)
        brk_long_1h = _first_cross_only(cross_long_1h, ctx)
        brk_short_1h = _first_cross_only(cross_short_1h, ctx)

        # Strength for conviction scoring
        strength_1h = np.zeros(n_1h, dtype=np.float64)
        str_long = np.clip((close_1h - prior_bb_upper_1h) / np.maximum(close_1h, 1e-10), 0, 0.1)
        str_short = np.clip((prior_bb_lower_1h - close_1h) / np.maximum(close_1h, 1e-10), 0, 0.1)
        strength_1h = np.where(brk_long_1h, str_long, np.where(brk_short_1h, str_short, 0.0))

        # ADV filter for breakout
        if ctx.rolling_adv is not None:
            adv_ok = ctx.rolling_adv >= MIN_ADV_BREAKOUT
            brk_long_1h = brk_long_1h & adv_ok
            brk_short_1h = brk_short_1h & adv_ok

        # SMA(15) trail on 4H → aligned to 1H
        sma_trail_4h = _rolling_mean(close_4h, TRAIL_SMA_PERIOD)
        sma_trail_1h = ctx.align_4h_to_1h(sma_trail_4h)

        # Limit entry price: BB level for limit fill, NaN for market
        limit_price_1h = np.full(n_1h, np.nan, dtype=np.float64)
        limit_price_1h[brk_long_1h] = prior_bb_upper_1h[brk_long_1h]
        limit_price_1h[brk_short_1h] = prior_bb_lower_1h[brk_short_1h]

        # Armed levels for live detection: BB levels for qualifying tokens BEFORE cross
        # (close hasn't crossed yet, but all other filters pass)
        arm_long = valid_bb_1h & (vol_ratio_1h > BB_VOL_RATIO) & (mom_7d_1h > 0) & (close_1h <= prior_bb_upper_1h)
        arm_short = valid_bb_1h & (vol_ratio_1h > BB_VOL_RATIO) & (mom_7d_1h < 0) & (close_1h >= prior_bb_lower_1h) & (prior_bb_lower_1h > 0)

        # Apply ADV filter
        if ctx.rolling_adv is not None:
            adv_ok_arm = ctx.rolling_adv >= MIN_ADV_BREAKOUT
            arm_long = arm_long & adv_ok_arm
            arm_short = arm_short & adv_ok_arm

        armed_level = np.full(n_1h, np.nan, dtype=np.float64)
        armed_level[arm_long] = prior_bb_upper_1h[arm_long]
        armed_level[arm_short] = prior_bb_lower_1h[arm_short]

        armed_dir = np.zeros(n_1h, dtype=np.int8)
        armed_dir[arm_long] = 1
        armed_dir[arm_short] = -1

        td['brk_long_1h'] = brk_long_1h
        td['brk_short_1h'] = brk_short_1h
        td['strength_1h'] = strength_1h
        td['sma_trail_1h'] = sma_trail_1h
        td['limit_price_1h'] = limit_price_1h
        td['armed_levels'] = armed_level
        td['armed_direction'] = armed_dir

    # ── Step 3: Momentum ranking (cross-sectional) ─────────────────
    tokens_list = sorted(token_data.keys())
    n_tokens = len(tokens_list)

    # Find max 1H bars for alignment
    max_bars = max(td['n_1h'] for td in token_data.values())

    # Build 1H close matrix (max_bars x n_tokens), right-aligned
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

    # Lookback returns (shifted by 1 bar to avoid look-ahead)
    lb = MOM_LOOKBACK_1H
    ret_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)
    if max_bars > lb + 1:
        # Return = close[t-1] / close[t-1-lb] - 1 (shifted, no look-ahead)
        past_close = close_matrix[:-1]  # shift by 1
        ret_matrix[lb + 1:] = (past_close[lb:] - past_close[:-lb]) / np.maximum(
            np.abs(past_close[:-lb]), 1e-10)

    # EMA trend filter per token (on 1H close)
    ema_signals = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)
    for j, token in enumerate(tokens_list):
        td = token_data[token]
        ctx = td['ctx']
        n = td['n_1h']
        offset = max_bars - n
        close_j = ctx.ind_1h['close']
        if n > MOM_EMA_SLOW + 10:
            ema_fast = _ema(close_j, MOM_EMA_FAST)
            ema_slow = _ema(close_j, MOM_EMA_SLOW)
            ema_diff = ema_fast - ema_slow
            # Shift by 1 bar (no look-ahead)
            ema_signals[offset + 1:offset + n, j] = ema_diff[:-1]

    # ATR ratio for volatility filter (from 1H indicators, shifted)
    atr_ratio_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)
    for j, token in enumerate(tokens_list):
        td = token_data[token]
        ctx = td['ctx']
        n = td['n_1h']
        offset = max_bars - n
        atr_j = ctx.ind_1h['atr']
        close_j = ctx.ind_1h['close']
        ratio = atr_j / np.maximum(close_j, 1e-10)
        # Shift by 1
        atr_ratio_matrix[offset + 1:offset + n, j] = ratio[:-1]

    # Rebalance schedule: every MOM_REBAL_BARS starting from warmup
    rebalance_bars = list(range(WARMUP_BARS, max_bars, MOM_REBAL_BARS))

    # Initialize per-token momentum entry arrays
    mom_entry_mask = {token: np.zeros(token_data[token]['n_1h'], dtype=bool)
                      for token in tokens_list}
    mom_direction = {token: np.zeros(token_data[token]['n_1h'], dtype=np.int8)
                     for token in tokens_list}
    mom_size = {token: np.zeros(token_data[token]['n_1h'], dtype=np.float64)
                for token in tokens_list}

    for rb in rebalance_bars:
        if rb >= max_bars:
            break

        # Get returns at this bar
        rets_at_rb = ret_matrix[rb]
        adv_at_rb = adv_matrix[rb]

        # Filter by valid return + ADV
        eligible = []
        for j in range(n_tokens):
            if np.isnan(rets_at_rb[j]):
                continue
            if np.isnan(adv_at_rb[j]) or adv_at_rb[j] < MIN_ADV_MOMENTUM:
                continue
            eligible.append(j)

        if len(eligible) < 2 * MOM_K:
            continue

        # ATR volatility filter: keep higher-ATR half
        atr_vals = atr_ratio_matrix[rb]
        eligible_atr = [(j, atr_vals[j]) for j in eligible if not np.isnan(atr_vals[j])]
        if len(eligible_atr) > 0:
            atr_arr = np.array([v for _, v in eligible_atr])
            med = np.median(atr_arr)
            eligible = [j for j, v in eligible_atr if v > med]

        if len(eligible) < 2 * MOM_K:
            continue

        # Rank by return
        eligible_rets = [(j, rets_at_rb[j]) for j in eligible]
        eligible_rets.sort(key=lambda x: x[1], reverse=True)

        top_k_indices = [j for j, _ in eligible_rets[:MOM_K]]
        bottom_k_indices = [j for j, _ in eligible_rets[-MOM_K:]]

        # EMA trend filter
        ema_at_rb = ema_signals[rb]
        top_k_indices = [j for j in top_k_indices
                         if not np.isnan(ema_at_rb[j]) and ema_at_rb[j] > 0]
        bottom_k_indices = [j for j in bottom_k_indices
                            if not np.isnan(ema_at_rb[j]) and ema_at_rb[j] < 0]

        # Set momentum entries for rebalance window
        next_rb = min(rb + MOM_REBAL_BARS, max_bars)
        n_long = max(len(top_k_indices), 1)
        n_short = max(len(bottom_k_indices), 1)

        for j in top_k_indices:
            token = tokens_list[j]
            td = token_data[token]
            n = td['n_1h']
            offset = max_bars - n
            local_start = max(rb - offset, 0)
            local_end = min(next_rb - offset, n)
            if local_start < local_end:
                # Only set entry on the first bar of the rebalance window
                mom_entry_mask[token][local_start] = True
                mom_direction[token][local_start] = 1
                mom_size[token][local_start] = (W_MOMENTUM * MOM_LONG_WT / n_long)

        for j in bottom_k_indices:
            token = tokens_list[j]
            td = token_data[token]
            n = td['n_1h']
            offset = max_bars - n
            local_start = max(rb - offset, 0)
            local_end = min(next_rb - offset, n)
            if local_start < local_end:
                mom_entry_mask[token][local_start] = True
                mom_direction[token][local_start] = -1
                mom_size[token][local_start] = (W_MOMENTUM * MOM_SHORT_WT / n_short)

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

        # Combine: breakout takes priority
        entry_mask = brk_entry | mom_entry

        # Warmup: suppress early entries
        entry_mask[:WARMUP_BARS] = False

        # Liquidity filter
        if ctx.liquidity_mask is not None:
            entry_mask = entry_mask & ctx.liquidity_mask

        if not np.any(entry_mask):
            continue

        # Direction: breakout direction takes priority over momentum
        direction = np.zeros(n, dtype=np.int8)
        direction[brk_long] = 1
        direction[brk_short] = -1
        mom_only = mom_entry & ~brk_entry
        direction[mom_only] = mom_direction[token][mom_only]

        # Size multiplier: breakout entries get larger size
        size_mult = np.ones(n, dtype=np.float64) * 0.05  # base
        brk_size = W_BREAKOUT * BREAKOUT_POS_SIZE  # 0.16
        size_mult[brk_entry] = brk_size
        size_mult[mom_only] = mom_size[token][mom_only]
        # Also set breakout size at armed bars (pre-cross) so paper engine
        # captures the correct size_multiplier before sub-hourly fill
        armed_levels_arr = td.get('armed_levels')
        if armed_levels_arr is not None:
            armed_mask = ~np.isnan(armed_levels_arr[:n])
            size_mult[armed_mask] = brk_size

        # Conviction score for entry prioritization
        conviction = np.zeros(n, dtype=np.float64)
        conviction[brk_entry] = np.clip(td['strength_1h'][brk_entry] * 10, 0.5, 1.0)
        conviction[mom_only] = 0.3

        # SMA trail values (4H SMA15 aligned to 1H)
        sma_trail = td['sma_trail_1h']

        # Limit entry prices (BB level for breakouts, NaN for momentum)
        limit_prices = td['limit_price_1h']

        results[token] = StrategyResult(
            entry_mask=entry_mask,
            direction=direction,
            market_type=MarketType.PERP,
            leverage=LEVERAGE,
            entry_limit_price=limit_prices,
            armed_levels=td.get('armed_levels'),
            armed_direction=td.get('armed_direction'),
            # Exit management — sweep-optimized (Calmar 121.26 vs 91.83 baseline)
            stop_mult=999.0,           # No fixed stop (sweep: stop has no effect)
            trail_mult=2.0,            # 2.0 ATR trailing stop (sweep: top Calmar lever)
            sma_trail_vals=None,       # Disabled
            target_mult=999.0,
            # Partial TP: close 50% at 1.5 ATR profit, trail remainder at 2.0 ATR
            partial_tp_atr=1.5,        # Take partial at 1.5 ATR profit
            partial_tp_pct=0.5,        # Close 50% of position
            partial_tp_trail=2.0,      # Trail remainder at 2.0 ATR (same as base trail)
            breakeven_atr=0.0,
            # Hold limits — research: peak EV at 4-8h
            no_stop_bars=0,
            min_hold=1,
            max_hold=4,               # 4h = peak Calmar window
            # Sizing
            edge=0.50,
            size_multiplier=size_mult,
            cap_multiplier=2.5,
            conviction_score=conviction,
            # Regime management
            exit_regimes=set(),        # No regime exits — time exit only
            bear_max_hold=0,           # 0 = disabled, use max_hold everywhere
            # Metadata
            name='s501_r172_v4_portfolio',
            exchange='binance',
        )

    return results
