"""
s90 Cross-Sectional Combined Factor Long/Short — V4 Portfolio Strategy (Class B)

Ranks ALL tokens by composite score = (14d_momentum_rank + inverse_funding_rank) / 2,
longs top N, shorts bottom N, rebalances weekly.

Key design choices:
- Two uncorrelated factors (momentum + funding) for robust ranking
- Long/short is self-hedging → 1.0x leverage, no regime exits
- No mechanical stops (s99/t99) — rebalance is the exit mechanism
- max_hold=168 (7d) forces exit at rebalance even if position missed rotation
- Liquidity + ADV filters only (ranking handles signal quality)

Research: Calmar 6.62 net (N=5), 5.32 net (N=8) with 40bps round-trip costs.

Market: PERP (bidirectional — long top, short bottom)
Status: EXPERIMENTAL
"""

import numpy as np
from scipy.stats import rankdata
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET, rolling_mean)

# Module-level marker for V4 portfolio adapter dispatch
STRATEGY_TYPE = "portfolio"

# ── Configuration ────────────────────────────────────────────────
LOOKBACK_BARS = 14 * 24        # 14 days in 1h bars for momentum
FUNDING_WINDOW = 72            # 72h = 3 day smoothing for funding
REBALANCE_BARS = 7 * 24        # 7 day rebalance frequency
N_LONG = 5                     # Fixed number of longs
N_SHORT = 5                    # Fixed number of shorts
MIN_ADV_USD = 5_000_000        # Liquid tokens only
MIN_ELIGIBLE = 20              # Need breadth for cross-sectional ranking
LEVERAGE = 1.0                 # L/S is self-hedging


def _compute_trailing_return(close: np.ndarray, lookback: int) -> np.ndarray:
    """Compute trailing return over lookback bars, vectorized."""
    n = len(close)
    ret = np.zeros(n, dtype=np.float64)
    if n > lookback:
        ret[lookback:] = (close[lookback:] - close[:-lookback]) / np.maximum(close[:-lookback], 1e-10)
    return ret


def strategy(contexts: dict) -> dict:
    """Cross-sectional combined factor long/short portfolio strategy.

    Args:
        contexts: {token: (ctx_spot, ctx_perp)} for combined market

    Returns:
        {token: StrategyResult} for tokens selected as longs or shorts
    """
    # ── Step 1: Compute factors for ALL tokens ────────────────────
    token_list = sorted(contexts.keys())
    if len(token_list) < MIN_ELIGIBLE:
        return {}

    # Use perp context for close prices, funding, and indicators
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

        # Funding data required for composite factor
        funding = ctx.funding_1h
        if funding is None:
            continue

        trail_ret = _compute_trailing_return(close, LOOKBACK_BARS)
        funding_avg = rolling_mean(funding, FUNDING_WINDOW)

        token_data[token] = {
            'close': close,
            'n': n,
            'trail_ret': trail_ret,
            'funding_avg': funding_avg,
            'ctx': ctx,
            'ctx_pair': ctx_pair,
        }

    eligible_tokens = sorted(token_data.keys())
    if len(eligible_tokens) < MIN_ELIGIBLE:
        return {}

    # ── Step 2: For each rebalance point, rank tokens and build entry masks ──
    # Use max_bars as reference frame; NaN-fill shorter tokens (right-aligned)
    max_bars = max(d['n'] for d in token_data.values())

    n_tokens = len(eligible_tokens)
    # Return matrix: (max_bars, n_tokens)
    ret_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)
    # Funding matrix: (max_bars, n_tokens)
    fund_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)

    for j, token in enumerate(eligible_tokens):
        d = token_data[token]
        n = d['n']
        offset = max_bars - n
        ret_matrix[offset:offset + n, j] = d['trail_ret']
        fund_matrix[offset:offset + n, j] = d['funding_avg']

    # Determine rebalance bars
    rebalance_bars = list(range(LOOKBACK_BARS, max_bars, REBALANCE_BARS))

    # Build per-token long/short membership masks
    long_membership = np.zeros((max_bars, n_tokens), dtype=bool)
    short_membership = np.zeros((max_bars, n_tokens), dtype=bool)

    for rb in rebalance_bars:
        rets = ret_matrix[rb, :]
        funds = fund_matrix[rb, :]

        # Valid = has both factors and non-zero return (past lookback warmup)
        valid = ~np.isnan(rets) & (rets != 0.0) & ~np.isnan(funds)
        n_valid = int(np.sum(valid))
        if n_valid < MIN_ELIGIBLE:
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
        if n_eligible < MIN_ELIGIBLE:
            continue

        # ── Composite ranking ──────────────────────────────────────
        # Extract eligible values
        eligible_indices = np.where(eligible_mask)[0]
        eligible_rets = rets[eligible_indices]
        eligible_funds = funds[eligible_indices]

        # Momentum rank (ascending: highest return = highest rank)
        mom_rank = rankdata(eligible_rets, method='average')

        # Inverse funding rank (ascending: most negative funding = highest rank = best long)
        inv_fund_rank = rankdata(-eligible_funds, method='average')

        # Composite score
        composite = (mom_rank + inv_fund_rank) / 2.0

        # Long: top N_LONG by composite (highest composite = strongest long candidate)
        n_long = min(N_LONG, n_eligible // 2)
        n_short = min(N_SHORT, n_eligible // 2)
        if n_long == 0 or n_short == 0:
            continue

        sorted_idx = np.argsort(composite)
        long_local = sorted_idx[-n_long:]    # Highest composite
        short_local = sorted_idx[:n_short]   # Lowest composite

        # Map back to full token indices
        long_indices = eligible_indices[long_local]
        short_indices = eligible_indices[short_local]

        # Mark membership until next rebalance
        next_rb = rb + REBALANCE_BARS
        long_membership[rb:next_rb, long_indices] = True
        short_membership[rb:next_rb, short_indices] = True

    # ── Step 3: Generate per-token StrategyResult ─────────────────
    # Portfolio adapter expects {token: StrategyResult} where token matches context keys.
    # Combine long/short into one result per token: direction array encodes side per bar.
    # A token can be long in some rebalance windows and short in others (never both).
    results = {}

    for j, token in enumerate(eligible_tokens):
        d = token_data[token]
        ctx = d['ctx']
        n = d['n']
        offset = max_bars - n

        # This token's long/short membership over time
        is_long = long_membership[offset:offset + n, j]
        is_short = short_membership[offset:offset + n, j]

        has_long = np.any(is_long)
        has_short = np.any(is_short)
        if not has_long and not has_short:
            continue

        # ── Per-token filters (minimal — ranking handles signal quality) ──
        # Apply liquidity mask
        liq_mask = np.ones(n, dtype=bool)
        if ctx.liquidity_mask is not None:
            liq_mask = ctx.liquidity_mask

        # Burn-in: skip first 200 bars
        burn_mask = np.ones(n, dtype=bool)
        burn_mask[:200] = False

        filter_mask = liq_mask & burn_mask

        # Combined entry mask: enter when in long OR short basket
        entry_mask = (is_long | is_short) & filter_mask

        # Per-bar direction: +1 for long, -1 for short (default +1 where neither)
        direction = np.ones(n, dtype=np.int8)
        direction[is_short] = -1

        if not np.any(entry_mask):
            continue

        ctx_pair = d['ctx_pair']

        if isinstance(ctx_pair, tuple):
            results[token] = StrategyResult(
                entry_mask=entry_mask,
                direction=direction,
                market_type=MarketType.PERP,
                leverage=LEVERAGE,
                stop_mult=99.0,
                trail_mult=99.0,
                target_mult=999,
                no_stop_bars=168,   # Protect full holding period from stops
                min_hold=24,
                max_hold=168,       # 7d = rebalance period
                edge=0.35,
                exit_regimes=set(),  # No regime exits — rebalance handles it
                exchange='binance',
                name='s90_xsec_factor_ls',
                breakeven_atr=0.0,  # Disable breakeven ratchet — hold to rebalance
            )
        else:
            results[token] = StrategyResult(
                entry_mask=entry_mask,
                direction=direction,
                market_type=MarketType.PERP if ctx.market_type == 'perp' else MarketType.SPOT,
                leverage=LEVERAGE if ctx.market_type == 'perp' else 1.0,
                stop_mult=99.0,
                trail_mult=99.0,
                target_mult=999,
                no_stop_bars=168,
                min_hold=24,
                max_hold=168,
                edge=0.35,
                exit_regimes=set(),
                exchange='binance',
                name='s90_xsec_factor_ls',
                breakeven_atr=0.0,
            )

    return results
