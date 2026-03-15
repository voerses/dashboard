"""
s80 Cross-Sectional Momentum — V4 Portfolio Strategy (Class B)

Ranks ALL tokens by trailing returns, selects top quintile, enters long
with progressive trailing stops and regime-scaled sizing.

Portfolio strategy signature: receives dict of (ctx_spot, ctx_perp) tuples,
returns dict of StrategyResult per token to trade.

Key improvements over V3 cross-sectional:
- Progressive trailing stops (s56 pattern) instead of no stops
- Regime-scaled sizing (concentrate in uptrend, zero in crisis)
- Per-token trend + volume filters (not just ranking)
- Proper max_hold and min_hold for swing trades
- Perp market with leverage for higher capital efficiency

Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET)

# Module-level marker for V4 portfolio adapter dispatch
STRATEGY_TYPE = "portfolio"


# Exit ablation (v2/v3): flat 1.5 ATR trail + breakeven beats progressive schedule.
# bear_max_hold=12: force exit after 12 bars in DOWNTREND (halves max DD for s80+s81).
TRAIL_SCHEDULE = None

# Time-based trail — tightens as hold duration grows
TIME_TRAIL_SCHEDULE = np.array([
    [48, 3.5],    # 2 days: standard
    [120, 2.5],   # 5 days: tighten
    [240, 2.0],   # 10 days: aggressive
    [480, 1.5],   # 20 days: very tight
], dtype=np.float64)

# ── Configuration ────────────────────────────────────────────────
LOOKBACK_BARS = 14 * 24        # 14 days in 1h bars
REBALANCE_BARS = 7 * 24        # 7 day rebalance frequency
TOP_PCT = 0.20                 # Long top 20%
MIN_BASKET = 5
MAX_BASKET = 20
MIN_ADV_USD = 500_000
LEVERAGE = 2.0                 # Moderate leverage (not 5x — diversified portfolio)


def _compute_trailing_return(close: np.ndarray, lookback: int) -> np.ndarray:
    """Compute trailing return over lookback bars, vectorized."""
    n = len(close)
    ret = np.zeros(n, dtype=np.float64)
    if n > lookback:
        ret[lookback:] = (close[lookback:] - close[:-lookback]) / np.maximum(close[:-lookback], 1e-10)
    return ret


def strategy(contexts: dict) -> dict:
    """Cross-sectional momentum portfolio strategy.

    Args:
        contexts: {token: (ctx_spot, ctx_perp)} for combined market

    Returns:
        {token: StrategyResult} for tokens selected in the momentum basket
    """
    # ── Step 1: Compute trailing returns for ALL tokens at each bar ────
    # Build aligned return matrix for ranking
    token_list = sorted(contexts.keys())
    if len(token_list) < MIN_BASKET:
        return {}

    # Use perp context for close prices and indicators
    token_data = {}
    for token in token_list:
        ctx_pair = contexts[token]
        if isinstance(ctx_pair, tuple):
            ctx_spot, ctx_perp = ctx_pair
            ctx = ctx_perp if ctx_perp is not None else ctx_spot
        else:
            ctx = ctx_pair

        close = ctx.ind_1h['close']
        n = len(close)
        if n < LOOKBACK_BARS + 100:
            continue

        trail_ret = _compute_trailing_return(close, LOOKBACK_BARS)
        token_data[token] = {
            'close': close,
            'n': n,
            'trail_ret': trail_ret,
            'ctx': ctx,
            'ctx_pair': ctx_pair,
        }

    eligible_tokens = sorted(token_data.keys())
    if len(eligible_tokens) < MIN_BASKET:
        return {}

    # ── Step 2: For each rebalance point, rank tokens and build entry masks ──
    # Use max_bars as reference frame; NaN-fill shorter tokens (right-aligned)
    max_bars = max(d['n'] for d in token_data.values())

    # Build return matrix: (max_bars, n_tokens) — NaN where token has no data
    n_tokens = len(eligible_tokens)
    ret_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)
    for j, token in enumerate(eligible_tokens):
        d = token_data[token]
        n = d['n']
        # Right-align: token data fills end of matrix, NaN at start
        offset = max_bars - n
        ret_matrix[offset:offset + n, j] = d['trail_ret']

    # Determine rebalance bars
    rebalance_bars = list(range(LOOKBACK_BARS, max_bars, REBALANCE_BARS))

    # Build per-token entry masks based on ranking at rebalance points
    basket_membership = np.zeros((max_bars, n_tokens), dtype=bool)

    for rb in rebalance_bars:
        # Get returns at this bar
        rets = ret_matrix[rb, :]
        valid = ~np.isnan(rets) & (rets != 0.0)  # Exclude tokens with no data or zero return (pre-lookback)
        n_valid = int(np.sum(valid))
        if n_valid < MIN_BASKET:
            continue

        # ADV filter — only include tokens with sufficient liquidity
        adv_mask = np.zeros(n_tokens, dtype=bool)
        for j, token in enumerate(eligible_tokens):
            d = token_data[token]
            n = d['n']
            offset = max_bars - n
            local_bar = rb - offset
            if local_bar < 0 or local_bar >= n:
                adv_mask[j] = False
                continue
            ctx = d['ctx']
            if ctx.rolling_adv is not None and local_bar < len(ctx.rolling_adv):
                adv_mask[j] = ctx.rolling_adv[local_bar] >= MIN_ADV_USD
            else:
                adv_mask[j] = True

        eligible_mask = valid & adv_mask
        n_eligible = int(np.sum(eligible_mask))
        if n_eligible < MIN_BASKET:
            continue

        # Rank by return — select top quintile
        eligible_rets = np.where(eligible_mask, rets, -np.inf)
        n_select = max(MIN_BASKET, min(MAX_BASKET, int(n_eligible * TOP_PCT)))
        top_indices = np.argsort(eligible_rets)[-n_select:]

        # Mark these tokens as in-basket until next rebalance
        next_rb = rb + REBALANCE_BARS
        basket_membership[rb:next_rb, top_indices] = True

    # ── Step 3: Generate per-token StrategyResult for basket members ──────
    results = {}

    for j, token in enumerate(eligible_tokens):
        d = token_data[token]
        ctx = d['ctx']
        n = d['n']
        offset = max_bars - n

        # This token's basket membership over time
        in_basket = basket_membership[offset:offset + n, j]
        if not np.any(in_basket):
            continue

        # Build full-length entry mask (aligned to token's bars)
        entry_mask = in_basket.copy()

        # ── Per-token filters (beyond just ranking) ──────────────────
        close = ctx.ind_1h['close']
        ema_20 = ctx.ind_1h['ema_20']
        ema_50 = ctx.ind_1h['ema_50']
        regime = ctx.regime_1h

        # Only enter if token is trending up (EMA confirmation)
        trend_ok = close > ema_20
        entry_mask = entry_mask & trend_ok

        # Block crisis and downtrend entries
        regime_ok = (regime != CRISIS) & (regime != DOWNTREND)
        entry_mask = entry_mask & regime_ok

        # Burn-in: skip first 200 bars
        entry_mask[:200] = False

        # Apply liquidity mask
        if ctx.liquidity_mask is not None:
            entry_mask = entry_mask & ctx.liquidity_mask

        if not np.any(entry_mask):
            continue

        # ── Regime-scaled sizing (like s56) ──────────────────────────
        regime_size = np.where(regime == UPTREND, 2.5,
                     np.where(regime == RANGE, 1.5,
                     np.where(regime == QUIET, 1.0, 0.5)))

        # ── Direction: always long ───────────────────────────────────
        direction = np.ones(n, dtype=np.int8)

        # Build StrategyResult
        ctx_pair = d['ctx_pair']
        if isinstance(ctx_pair, tuple):
            # Combined: use perp for primary entry
            results[token] = StrategyResult(
                entry_mask=entry_mask,
                direction=direction,
                market_type=MarketType.PERP,
                leverage=LEVERAGE,
                stop_mult=3.0,
                trail_mult=1.5,
                target_mult=999,
                no_stop_bars=24,      # 1 day no-stop protection
                min_hold=12,          # 12h minimum hold
                max_hold=504,         # 21 days max hold (swing)
                edge=0.40,            # Moderate conviction
                exit_regimes={CRISIS},
                exchange='binance',
                name='s80_xsec_momentum',
                trail_schedule=TRAIL_SCHEDULE,
                time_trail_schedule=TIME_TRAIL_SCHEDULE,
                bear_max_hold=12,
                size_multiplier=regime_size,
                cap_multiplier=3.0,   # Moderate cap (diversified, not concentrated)
            )
        else:
            # Single market
            results[token] = StrategyResult(
                entry_mask=entry_mask,
                direction=direction,
                market_type=MarketType.PERP if ctx.market_type == 'perp' else MarketType.SPOT,
                leverage=LEVERAGE if ctx.market_type == 'perp' else 1.0,
                stop_mult=3.0,
                trail_mult=1.5,
                target_mult=999,
                no_stop_bars=24,
                min_hold=12,
                max_hold=504,
                edge=0.40,
                exit_regimes={CRISIS},
                exchange='binance',
                name='s80_xsec_momentum',
                trail_schedule=TRAIL_SCHEDULE,
                time_trail_schedule=TIME_TRAIL_SCHEDULE,
                bear_max_hold=12,
                size_multiplier=regime_size,
                cap_multiplier=3.0,
            )

    return results
