"""
s85 Cross-Sectional Funding Carry Long/Short — V4 Portfolio Strategy (Class B)

BREAKTHROUGH HYPOTHESIS: Rank all tokens by funding rate. Short the highest
funding (collect carry), long the lowest funding (pay minimal). Dollar-neutral
perp-only portfolio. Always active because funding DISPERSION exists even when
average funding is low.

Key design:
- Pure funding carry signal (no momentum component)
- Dollar-neutral: equal N longs and N shorts → hedged market exposure
- Weekly rebalance (minimize turnover/costs)
- 3x leverage (safe on hedged portfolio)
- No mechanical stops — rebalance is the exit mechanism
- 72h smoothed funding to avoid noise

Research basis:
- 1Token/Bybit 2025: Dollar Neutral strategy returned 66.69% with 7.72% MaxDD
- s30 basis carry: IS Sharpe 13.4, MaxDD -2.81% (delta-neutral proven edge)
- Funding dispersion exists even in low-average-funding environments (2025-26)

Market: PERP (bidirectional — long low-funding, short high-funding)
Status: EXPERIMENTAL (Gate 3)
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET, rolling_mean)

# Module-level marker for V4 portfolio adapter dispatch
STRATEGY_TYPE = "portfolio"

# ── Configuration ────────────────────────────────────────────────
FUNDING_WINDOW = 72            # 72h smoothing for funding rate
REBALANCE_BARS = 7 * 24        # Weekly rebalance
N_LONG = 5                     # Long bottom 5 (lowest funding → pay least)
N_SHORT = 5                    # Short top 5 (highest funding → collect most)
MIN_ADV_USD = 2_000_000        # Liquid tokens only
MIN_ELIGIBLE = 15              # Need breadth for cross-sectional
LEVERAGE = 3.0                 # 3x on hedged portfolio
WARMUP = 200


def strategy(contexts: dict) -> dict:
    """Cross-sectional funding carry long/short.

    Args:
        contexts: {token: StrategyContext} or {token: (ctx_spot, ctx_perp)}

    Returns:
        {token: StrategyResult} for tokens selected as longs or shorts
    """
    # ── Step 1: Extract funding data for all tokens ─────────────────
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

        funding_avg = rolling_mean(funding, FUNDING_WINDOW)

        token_data[token] = {
            'close': close,
            'n': n,
            'funding_avg': funding_avg,
            'ctx': ctx,
            'ctx_pair': ctx_pair,
        }

    eligible_tokens = sorted(token_data.keys())
    if len(eligible_tokens) < MIN_ELIGIBLE:
        return {}

    # ── Step 2: For each rebalance, rank by funding and assign L/S ──
    max_bars = max(d['n'] for d in token_data.values())
    n_tokens = len(eligible_tokens)

    # Funding matrix: (max_bars, n_tokens) — right-aligned
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

        # ADV filter
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

        # Sort by funding rate (ascending)
        sorted_local = np.argsort(eligible_funds)
        n_long = min(N_LONG, n_valid // 3)
        n_short = min(N_SHORT, n_valid // 3)
        if n_long == 0 or n_short == 0:
            continue

        # LONG: lowest funding tokens (bottom N — we pay least or receive on longs)
        long_local = sorted_local[:n_long]
        # SHORT: highest funding tokens (top N — we collect most carry)
        short_local = sorted_local[-n_short:]

        long_indices = eligible_indices[long_local]
        short_indices = eligible_indices[short_local]

        next_rb = min(rb + REBALANCE_BARS, max_bars)
        long_membership[rb:next_rb, long_indices] = True
        short_membership[rb:next_rb, short_indices] = True

    # ── Step 3: Generate per-token StrategyResult ───────────────────
    results = {}
    for j, token in enumerate(eligible_tokens):
        d = token_data[token]
        n = d['n']
        offset = max_bars - n

        is_long = long_membership[offset:offset + n, j]
        is_short = short_membership[offset:offset + n, j]

        if not np.any(is_long) and not np.any(is_short):
            continue

        liq_mask = np.ones(n, dtype=bool)
        ctx = d['ctx']
        if ctx.liquidity_mask is not None:
            liq_mask = ctx.liquidity_mask

        burn_mask = np.ones(n, dtype=bool)
        burn_mask[:WARMUP] = False

        filter_mask = liq_mask & burn_mask
        entry_mask = (is_long | is_short) & filter_mask

        direction = np.ones(n, dtype=np.int8)
        direction[is_short] = -1

        if not np.any(entry_mask):
            continue

        results[token] = StrategyResult(
            entry_mask=entry_mask,
            direction=direction,
            market_type=MarketType.PERP,
            leverage=LEVERAGE,
            stop_mult=99.0,        # No hard stop
            trail_mult=99.0,       # No trail
            target_mult=999,       # No TP
            no_stop_bars=168,      # Full rebalance protection
            min_hold=24,
            max_hold=168,          # 7d = rebalance
            edge=0.35,
            exit_regimes=set(),    # No regime exits
            exchange='binance',
            name='s85_funding_carry_ls',
            breakeven_atr=0.0,
        )

    return results
