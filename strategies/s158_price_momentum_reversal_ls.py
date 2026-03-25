"""
s158 Price Momentum Reversal L/S — V4 Portfolio Strategy (Class B)

Alternative signal for volatile token cross-sectional L/S.
Rank by 7-day return. Short the biggest gainers (mean reversion after pump),
long the biggest losers (bounce after dump).

Classic short-term reversal on volatile/meme tokens.

Same universe filter as s151 (funding dispersion > 0.0002), same structure
(5L/5S, weekly rebalance, 1x leverage).

Market: PERP only
Status: EXPERIMENTAL (Gate 3)
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_mean, rolling_std, ema)

STRATEGY_TYPE = "portfolio"

RETURN_LOOKBACK = 7 * 24       # 7-day return window
REBALANCE_BARS = 7 * 24
N_LONG = 5
N_SHORT = 5
MIN_ADV_USD = 5_000_000
MIN_FUNDING_DISP = 0.0002      # Only trade high-dispersion (volatile/meme) tokens
MIN_ELIGIBLE = 12
LEVERAGE = 1.0
WARMUP = 250


def strategy(contexts: dict) -> dict:
    """Cross-sectional price momentum reversal L/S on volatile tokens."""
    token_data = {}
    for token, ctx_pair in contexts.items():
        if isinstance(ctx_pair, tuple):
            ctx_spot, ctx_perp = ctx_pair
            ctx = ctx_perp if ctx_perp is not None else ctx_spot
        else:
            ctx = ctx_pair

        close = ctx.ind_1h['close']
        n = len(close)
        if n < WARMUP + RETURN_LOOKBACK:
            continue

        funding = ctx.funding_1h
        if funding is None:
            continue

        # Funding dispersion check — ONLY trade volatile tokens
        recent_funding = funding[-720:] if n > 720 else funding
        valid_f = recent_funding[~np.isnan(recent_funding)]
        if len(valid_f) < 50 or np.std(valid_f) < MIN_FUNDING_DISP:
            continue

        # 7-day return: close[t] / close[t - 168] - 1 (vectorized)
        ret_7d = np.full(n, np.nan, dtype=np.float64)
        cur = close[RETURN_LOOKBACK:]
        prev = close[:n - RETURN_LOOKBACK]
        mask = (prev > 0) & ~np.isnan(prev) & ~np.isnan(cur)
        ret_7d[RETURN_LOOKBACK:] = np.where(mask, cur / np.maximum(prev, 1e-20) - 1.0, np.nan)

        token_data[token] = {
            'close': close, 'n': n,
            'ret_7d': ret_7d,
            'ctx': ctx, 'ctx_pair': ctx_pair,
        }

    eligible_tokens = sorted(token_data.keys())
    if len(eligible_tokens) < MIN_ELIGIBLE:
        return {}

    max_bars = max(d['n'] for d in token_data.values())
    n_tokens = len(eligible_tokens)

    ret_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)
    for j, token in enumerate(eligible_tokens):
        d = token_data[token]
        n = d['n']
        offset = max_bars - n
        ret_matrix[offset:offset + n, j] = d['ret_7d']

    rebalance_bars = list(range(WARMUP, max_bars, REBALANCE_BARS))
    long_membership = np.zeros((max_bars, n_tokens), dtype=bool)
    short_membership = np.zeros((max_bars, n_tokens), dtype=bool)

    for rb in rebalance_bars:
        signals = ret_matrix[rb, :]
        valid = ~np.isnan(signals)

        for j, token in enumerate(eligible_tokens):
            if not valid[j]:
                continue
            d = token_data[token]
            n = d['n']
            offset = max_bars - n
            local_bar = rb - offset
            if local_bar < 0 or local_bar >= n:
                valid[j] = False
                continue
            ctx = d['ctx']
            if ctx.rolling_adv is not None and local_bar < len(ctx.rolling_adv):
                if ctx.rolling_adv[local_bar] < MIN_ADV_USD:
                    valid[j] = False

        n_valid = int(np.sum(valid))
        if n_valid < MIN_ELIGIBLE:
            continue

        eligible_indices = np.where(valid)[0]
        eligible_signals = signals[eligible_indices]

        # Sort ascending by 7d return
        # Lowest return (biggest losers) = long (bounce after dump)
        # Highest return (biggest gainers) = short (mean reversion after pump)
        sorted_local = np.argsort(eligible_signals)
        n_long = min(N_LONG, n_valid // 3)
        n_short = min(N_SHORT, n_valid // 3)
        if n_long == 0 or n_short == 0:
            continue

        long_indices = eligible_indices[sorted_local[:n_long]]
        short_indices = eligible_indices[sorted_local[-n_short:]]

        next_rb = min(rb + REBALANCE_BARS, max_bars)
        long_membership[rb:next_rb, long_indices] = True
        short_membership[rb:next_rb, short_indices] = True

    results = {}
    for j, token in enumerate(eligible_tokens):
        d = token_data[token]
        n = d['n']
        offset = max_bars - n

        is_long = long_membership[offset:offset + n, j]
        is_short = short_membership[offset:offset + n, j]

        if not np.any(is_long) and not np.any(is_short):
            continue

        liq_mask = d['ctx'].liquidity_mask if d['ctx'].liquidity_mask is not None else np.ones(n, dtype=bool)
        burn_mask = np.zeros(n, dtype=bool)
        burn_mask[WARMUP:] = True

        entry_mask = (is_long | is_short) & liq_mask & burn_mask
        direction = np.ones(n, dtype=np.int8)
        direction[is_short] = -1

        if not np.any(entry_mask):
            continue

        results[token] = StrategyResult(
            entry_mask=entry_mask,
            direction=direction,
            market_type=MarketType.PERP,
            leverage=LEVERAGE,
            stop_mult=99.0,
            trail_mult=99.0,
            target_mult=999,
            no_stop_bars=168,
            min_hold=24,
            max_hold=168,
            edge=0.35,
            exit_regimes=set(),
            exchange='binance',
            name='s158_price_momentum_reversal_ls',
            breakeven_atr=0.0,
        )

    return results
