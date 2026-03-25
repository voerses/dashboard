"""
s162 Volatile Funding L/S — Daily Rebalance + 7 Shorts — V4 Portfolio Strategy (Class B)

Recycle of s151 combining both improvements:
1. N_SHORT=7 (captures more short-side alpha, +26% Calmar in sensitivity test)
2. Daily rebalance (fixes the fragile 7-day REBALANCE_BARS dependency)

If both fixes are additive, this should be more robust AND higher-returning.
If they cancel out, the daily rebalance may eat fees from excess turnover.

Market: PERP only
Status: EXPERIMENTAL (Gate 3P — recycle of s151)
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_mean, rolling_std, ema)

STRATEGY_TYPE = "portfolio"

FUNDING_WINDOW = 24
REBALANCE_BARS = 24                  # Daily rebalance
N_LONG = 5
N_SHORT = 7                          # More shorts
MIN_ADV_USD = 5_000_000
MIN_FUNDING_DISP = 0.0002
MIN_ELIGIBLE = 12
LEVERAGE = 1.0
WARMUP = 250


def strategy(contexts: dict) -> dict:
    """Cross-sectional funding L/S — daily rebalance, more shorts."""
    token_data = {}
    for token, ctx_pair in contexts.items():
        if isinstance(ctx_pair, tuple):
            ctx_spot, ctx_perp = ctx_pair
            ctx = ctx_perp if ctx_perp is not None else ctx_spot
        else:
            ctx = ctx_pair

        close = ctx.ind_1h['close']
        n = len(close)
        if n < WARMUP + FUNDING_WINDOW:
            continue

        funding = ctx.funding_1h
        if funding is None:
            continue

        recent_funding = funding[-720:] if n > 720 else funding
        valid_f = recent_funding[~np.isnan(recent_funding)]
        if len(valid_f) < 50 or np.std(valid_f) < MIN_FUNDING_DISP:
            continue

        funding_avg = rolling_mean(funding, FUNDING_WINDOW)

        token_data[token] = {
            'close': close, 'n': n,
            'funding_avg': funding_avg,
            'ctx': ctx, 'ctx_pair': ctx_pair,
        }

    eligible_tokens = sorted(token_data.keys())
    if len(eligible_tokens) < MIN_ELIGIBLE:
        return {}

    max_bars = max(d['n'] for d in token_data.values())
    n_tokens = len(eligible_tokens)

    fund_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)
    for j, token in enumerate(eligible_tokens):
        d = token_data[token]
        n = d['n']
        offset = max_bars - n
        fund_matrix[offset:offset + n, j] = d['funding_avg']

    rebalance_bars = list(range(WARMUP, max_bars, REBALANCE_BARS))
    long_membership = np.zeros((max_bars, n_tokens), dtype=bool)
    short_membership = np.zeros((max_bars, n_tokens), dtype=bool)

    for rb in rebalance_bars:
        funds = fund_matrix[rb, :]
        valid = ~np.isnan(funds)

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
        eligible_funds = funds[eligible_indices]

        sorted_local = np.argsort(eligible_funds)
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
            name='s162_funding_ls_daily_7short',
            breakeven_atr=0.0,
        )

    return results
