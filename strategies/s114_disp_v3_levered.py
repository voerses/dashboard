"""
s114 Dispersion Momentum V3 Levered — Proven s100v3 params with higher leverage

s100 v3 achieved: +33.9%, -10.3% DD, Calmar 1.53 — excellent DD control
Problem: returns too low. But 10% DD means we have 10pp of DD budget to use.

Key insight: s100 v3 used 2-5x leverage (regime-dependent). If the signal
quality is good enough for 10% DD at 3x average leverage, then at 7x average
leverage, DD should scale to ~23% and returns should scale to ~80%.
At 6x: DD ~20%, returns ~68%.

This test validates whether the dispersion-filtered momentum signal
scales linearly with leverage.

Parameters: IDENTICAL to s100 v3 (proven), only leverage changed:
  - N_LONG = 5 (concentrated)
  - Rebalance = 168h (weekly)
  - No stops, hold to rebalance
  - Dispersion filter with 336h median window
  - Long-only
  - Leverage: 7x FLAT (not regime-dependent)

Target: 300%+ annual, <20% DD, Calmar >3
Market: PERP (bidirectional)
Status: EXPERIMENTAL (Gate 3)
"""

import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_mean)

STRATEGY_TYPE = "portfolio"

# ── Configuration (s100 v3 proven params) ─────────────────────────
LOOKBACK_BARS = 24              # 1-day trailing return
REBALANCE_BARS = 168            # Weekly (7-day) — proven from s93
N_LONG = 5                      # Concentrated — proven from s93
N_SHORT = 0                     # Long-only — proven
MIN_ADV_USD = 2_000_000
MIN_ELIGIBLE = 20

# Dispersion filter
DISPERSION_LOOKBACK = 24
DISPERSION_MEDIAN_WINDOW = 336  # 14-day median
DISPERSION_THRESHOLD_MULT = 1.0 # Only trade when dispersion > median

# LEVERAGE IS THE ONLY CHANGE FROM V3
LEVERAGE = 7.0                   # Was 2-5x regime-dependent, now flat 7x

# No stops — hold to rebalance (proven)
STOP_MULT = 99.0
TRAIL_MULT = 99.0
TARGET_MULT = 999.0
NO_STOP_BARS = 168
MIN_HOLD = 1
MAX_HOLD = 168
EDGE = 0.35
BREAKEVEN_ATR = 0.0

WARMUP = 400


def _trailing_return(close, lookback):
    n = len(close)
    ret = np.zeros(n, dtype=np.float64)
    if n > lookback:
        ret[lookback:] = (close[lookback:] - close[:-lookback]) / np.maximum(close[:-lookback], 1e-10)
    return ret


def _compute_dispersion(ret_matrix, lookback=DISPERSION_LOOKBACK):
    max_bars = ret_matrix.shape[0]
    dispersion = np.zeros(max_bars, dtype=np.float64)
    for i in range(lookback, max_bars):
        window = ret_matrix[i-lookback:i, :]
        valid_counts = np.sum(~np.isnan(window), axis=1)
        bar_stds = np.nanstd(window, axis=1)
        mask = valid_counts >= 10
        if np.any(mask):
            dispersion[i] = np.mean(bar_stds[mask])
    return dispersion


def _majority_regime(token_data, eligible_tokens, max_bars, bar_idx):
    regime_counts = np.zeros(5, dtype=int)
    for token in eligible_tokens:
        d = token_data[token]
        n = d['n']
        offset = max_bars - n
        local_bar = bar_idx - offset
        if 0 <= local_bar < n:
            regime_val = d['regime'][local_bar]
            regime_counts[np.clip(regime_val, 0, 4)] += 1
    return int(np.argmax(regime_counts))


def strategy(contexts: dict) -> dict:
    """Dispersion-filtered momentum with higher leverage."""
    token_list = sorted(contexts.keys())
    if len(token_list) < MIN_ELIGIBLE:
        return {}

    token_data = {}
    for token in token_list:
        if token == "BTC":
            continue

        ctx_pair = contexts[token]
        if isinstance(ctx_pair, tuple):
            ctx_spot, ctx_perp = ctx_pair
            ctx = ctx_perp if ctx_perp is not None else ctx_spot
        else:
            ctx = ctx_pair

        close = ctx.ind_1h['close']
        n = len(close)
        if n < LOOKBACK_BARS + WARMUP:
            continue

        ret_1d = _trailing_return(close, LOOKBACK_BARS)
        regime = ctx.regime_1h

        token_data[token] = {
            'close': close,
            'n': n,
            'ret_1d': ret_1d,
            'regime': regime,
            'ctx': ctx,
            'ctx_pair': ctx_pair,
        }

    eligible_tokens = sorted(token_data.keys())
    if len(eligible_tokens) < MIN_ELIGIBLE:
        return {}

    # Build return matrix
    max_bars = max(d['n'] for d in token_data.values())
    n_tokens = len(eligible_tokens)

    ret_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)
    for j, token in enumerate(eligible_tokens):
        d = token_data[token]
        n = d['n']
        offset = max_bars - n
        ret_matrix[offset:offset + n, j] = d['ret_1d']

    # Compute dispersion
    dispersion = _compute_dispersion(ret_matrix, lookback=DISPERSION_LOOKBACK)
    disp_median = pd.Series(dispersion).rolling(
        DISPERSION_MEDIAN_WINDOW, min_periods=DISPERSION_MEDIAN_WINDOW // 2
    ).median().values
    threshold = np.nan_to_num(disp_median * DISPERSION_THRESHOLD_MULT, nan=0.0)
    risk_on = dispersion > threshold

    # Rebalance schedule
    warmup = max(WARMUP, DISPERSION_MEDIAN_WINDOW + 50)
    rebalance_bars = list(range(warmup, max_bars, REBALANCE_BARS))

    long_membership = np.zeros((max_bars, n_tokens), dtype=bool)

    for rb in rebalance_bars:
        if not risk_on[rb]:
            continue

        majority_regime = _majority_regime(token_data, eligible_tokens, max_bars, rb)
        if majority_regime == CRISIS:
            continue

        rets = ret_matrix[rb, :]
        valid = ~np.isnan(rets) & (rets != 0.0)

        # ADV filter
        adv_mask = np.zeros(n_tokens, dtype=bool)
        for j, token in enumerate(eligible_tokens):
            d = token_data[token]
            n = d['n']
            offset = max_bars - n
            local_bar = rb - offset
            if local_bar < 0 or local_bar >= n:
                continue
            ctx = d['ctx']
            if ctx.rolling_adv is not None and local_bar < len(ctx.rolling_adv):
                adv_mask[j] = ctx.rolling_adv[local_bar] >= MIN_ADV_USD
            else:
                adv_mask[j] = True

        eligible_mask = valid & adv_mask
        n_eligible = int(np.sum(eligible_mask))
        if n_eligible < MIN_ELIGIBLE:
            continue

        eligible_idx = np.where(eligible_mask)[0]
        eligible_rets = rets[eligible_idx]
        sorted_idx = np.argsort(eligible_rets)

        n_long = min(N_LONG, max(1, n_eligible // 5))
        long_indices = eligible_idx[sorted_idx[-n_long:]]

        next_rb = min(rb + REBALANCE_BARS, max_bars)
        long_membership[rb:next_rb, long_indices] = True

    # Generate per-token StrategyResult
    results = {}
    for j, token in enumerate(eligible_tokens):
        d = token_data[token]
        ctx = d['ctx']
        n = d['n']
        offset = max_bars - n

        is_long = long_membership[offset:offset + n, j]
        if not np.any(is_long):
            continue

        liq_mask = np.ones(n, dtype=bool)
        if ctx.liquidity_mask is not None:
            liq_mask = ctx.liquidity_mask

        burn_mask = np.ones(n, dtype=bool)
        burn_mask[:200] = False
        filter_mask = liq_mask & burn_mask

        entry_mask = is_long & filter_mask
        direction = np.ones(n, dtype=np.int8)

        if not np.any(entry_mask):
            continue

        results[token] = StrategyResult(
            entry_mask=entry_mask,
            direction=direction,
            market_type=MarketType.PERP,
            leverage=LEVERAGE,
            stop_mult=STOP_MULT,
            trail_mult=TRAIL_MULT,
            target_mult=TARGET_MULT,
            no_stop_bars=NO_STOP_BARS,
            min_hold=MIN_HOLD,
            max_hold=MAX_HOLD,
            edge=EDGE,
            exit_regimes={CRISIS},
            exchange='binance',
            name='s114_disp_v3_levered',
            breakeven_atr=BREAKEVEN_ATR,
            cap_multiplier=6.0,
        )

    return results
