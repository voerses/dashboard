"""
s159 Composite Signal L/S — V4 Portfolio Strategy (Class B)

Combines three signals into a composite ranking for volatile token L/S:
  composite = 0.5 * funding_zscore + 0.3 * volume_zscore + 0.2 * return_zscore

Each component z-scored cross-sectionally at each rebalance date.
Short the highest composite (crowded + blow-off + overbought),
long the lowest composite (undercrowded + coiling + oversold).

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

FUNDING_WINDOW = 24
VOL_LOOKBACK = 7 * 24          # 7-day volume window
RETURN_LOOKBACK = 7 * 24       # 7-day return window
REBALANCE_BARS = 7 * 24
N_LONG = 5
N_SHORT = 5
MIN_ADV_USD = 5_000_000
MIN_FUNDING_DISP = 0.0002      # Only trade high-dispersion (volatile/meme) tokens
MIN_ELIGIBLE = 12
LEVERAGE = 1.0
WARMUP = 250

# Composite weights
W_FUNDING = 0.5
W_VOLUME = 0.3
W_RETURN = 0.2


def _cross_sectional_zscore(values, valid_mask):
    """Z-score values cross-sectionally (across tokens at one point in time).

    Returns array of same shape with z-scores for valid entries, NaN for invalid.
    """
    result = np.full_like(values, np.nan)
    valid_vals = values[valid_mask]
    if len(valid_vals) < 3:
        return result
    mu = np.nanmean(valid_vals)
    sigma = np.nanstd(valid_vals)
    if sigma < 1e-10:
        result[valid_mask] = 0.0
        return result
    result[valid_mask] = np.clip((valid_vals - mu) / sigma, -3.0, 3.0)
    return result


def strategy(contexts: dict) -> dict:
    """Cross-sectional composite signal L/S on volatile tokens."""
    token_data = {}
    for token, ctx_pair in contexts.items():
        if isinstance(ctx_pair, tuple):
            ctx_spot, ctx_perp = ctx_pair
            ctx = ctx_perp if ctx_perp is not None else ctx_spot
        else:
            ctx = ctx_pair

        close = ctx.ind_1h['close']
        volume = ctx.ind_1h['volume']
        n = len(close)
        if n < WARMUP + max(VOL_LOOKBACK, RETURN_LOOKBACK, FUNDING_WINDOW):
            continue

        funding = ctx.funding_1h
        if funding is None:
            continue

        # Funding dispersion check — ONLY trade volatile tokens
        recent_funding = funding[-720:] if n > 720 else funding
        valid_f = recent_funding[~np.isnan(recent_funding)]
        if len(valid_f) < 50 or np.std(valid_f) < MIN_FUNDING_DISP:
            continue

        # Signal 1: 24h rolling mean funding rate
        funding_avg = rolling_mean(funding, FUNDING_WINDOW)

        # Signal 2: Volume momentum (vol_now / vol_7d_ago) — vectorized
        vol_ma = rolling_mean(volume, VOL_LOOKBACK)
        vol_mom = np.full(n, np.nan, dtype=np.float64)
        v_cur = vol_ma[VOL_LOOKBACK:]
        v_prev = vol_ma[:n - VOL_LOOKBACK]
        v_mask = (v_prev > 0) & ~np.isnan(v_prev) & ~np.isnan(v_cur)
        vol_mom[VOL_LOOKBACK:] = np.where(v_mask, v_cur / np.maximum(v_prev, 1e-20), np.nan)

        # Signal 3: 7-day return — vectorized
        ret_7d = np.full(n, np.nan, dtype=np.float64)
        r_cur = close[RETURN_LOOKBACK:]
        r_prev = close[:n - RETURN_LOOKBACK]
        r_mask = (r_prev > 0) & ~np.isnan(r_prev) & ~np.isnan(r_cur)
        ret_7d[RETURN_LOOKBACK:] = np.where(r_mask, r_cur / np.maximum(r_prev, 1e-20) - 1.0, np.nan)

        token_data[token] = {
            'close': close, 'n': n,
            'funding_avg': funding_avg,
            'vol_mom': vol_mom,
            'ret_7d': ret_7d,
            'ctx': ctx, 'ctx_pair': ctx_pair,
        }

    eligible_tokens = sorted(token_data.keys())
    if len(eligible_tokens) < MIN_ELIGIBLE:
        return {}

    max_bars = max(d['n'] for d in token_data.values())
    n_tokens = len(eligible_tokens)

    # Build signal matrices (right-aligned)
    fund_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)
    vol_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)
    ret_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)

    for j, token in enumerate(eligible_tokens):
        d = token_data[token]
        n = d['n']
        offset = max_bars - n
        fund_matrix[offset:offset + n, j] = d['funding_avg']
        vol_matrix[offset:offset + n, j] = d['vol_mom']
        ret_matrix[offset:offset + n, j] = d['ret_7d']

    rebalance_bars = list(range(WARMUP, max_bars, REBALANCE_BARS))
    long_membership = np.zeros((max_bars, n_tokens), dtype=bool)
    short_membership = np.zeros((max_bars, n_tokens), dtype=bool)

    for rb in rebalance_bars:
        fund_vals = fund_matrix[rb, :]
        vol_vals = vol_matrix[rb, :]
        ret_vals = ret_matrix[rb, :]

        # A token is valid only if all three signals are available
        valid = (~np.isnan(fund_vals)) & (~np.isnan(vol_vals)) & (~np.isnan(ret_vals))

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

        # Cross-sectional z-score each component
        z_fund = _cross_sectional_zscore(fund_vals, valid)
        z_vol = _cross_sectional_zscore(vol_vals, valid)
        z_ret = _cross_sectional_zscore(ret_vals, valid)

        # Composite score
        composite = (W_FUNDING * z_fund + W_VOLUME * z_vol + W_RETURN * z_ret)

        eligible_indices = np.where(valid)[0]
        eligible_signals = composite[eligible_indices]

        # Sort ascending: lowest composite = long, highest = short
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
            name='s159_composite_signal_ls',
            breakeven_atr=0.0,
        )

    return results
