"""
s163 Funding-Primary Filtered L/S — V4 Portfolio Strategy (Class B)

Recycle of s151/s160: funding stays the PRIMARY ranking signal,
but add confirmation filters to reduce noise and improve timing.

Key insight from s151 analysis:
  - Short side: 63% WR, $44K PnL → strong, keep ranking by funding
  - Long side: 46% WR, $10K PnL → weak, needs filtering

Approach: rank by funding (like s151), but FILTER candidates using:
  1. RSI confirmation: only short overbought (RSI>60), only long oversold (RSI<40)
  2. Volume confirmation: only trade when vol_ratio > 1.5 (active market)
  3. ADX trend filter: skip entries when ADX < 15 (no trend = no alpha)

N_SHORT=7 (from s160 finding: more shorts captures more edge).

Unlike s159 which diluted funding with equal-weight composite,
this keeps funding as the sole ranking criterion and uses other
signals as binary gates.

Market: PERP only
Status: EXPERIMENTAL (Gate 3P — recycle of s151)
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_mean, rolling_std, ema)

STRATEGY_TYPE = "portfolio"

FUNDING_WINDOW = 24
REBALANCE_BARS = 7 * 24
N_LONG = 5
N_SHORT = 7
MIN_ADV_USD = 5_000_000
MIN_FUNDING_DISP = 0.0002
MIN_ELIGIBLE = 10              # Lower than s151 (12) since filters reduce pool
LEVERAGE = 1.0
WARMUP = 250

# Confirmation filters
RSI_SHORT_MIN = 60             # Only short overbought tokens
RSI_LONG_MAX = 40              # Only long oversold tokens
VOL_RATIO_MIN = 1.5            # Only trade when volume is elevated
ADX_MIN = 15                   # Only trade when there's a trend


def strategy(contexts: dict) -> dict:
    """Funding-ranked L/S with RSI/volume/trend confirmation filters."""
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
            'rsi': ctx.ind_1h['rsi'],
            'vol_ratio': ctx.ind_1h['vol_ratio'],
            'adx': ctx.ind_1h['adx'],
            'ctx': ctx, 'ctx_pair': ctx_pair,
        }

    eligible_tokens = sorted(token_data.keys())
    if len(eligible_tokens) < MIN_ELIGIBLE:
        return {}

    max_bars = max(d['n'] for d in token_data.values())
    n_tokens = len(eligible_tokens)

    fund_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)
    rsi_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)
    vol_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)
    adx_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)

    for j, token in enumerate(eligible_tokens):
        d = token_data[token]
        n = d['n']
        offset = max_bars - n
        fund_matrix[offset:offset + n, j] = d['funding_avg']
        rsi_matrix[offset:offset + n, j] = d['rsi']
        vol_matrix[offset:offset + n, j] = d['vol_ratio']
        adx_matrix[offset:offset + n, j] = d['adx']

    rebalance_bars = list(range(WARMUP, max_bars, REBALANCE_BARS))
    long_membership = np.zeros((max_bars, n_tokens), dtype=bool)
    short_membership = np.zeros((max_bars, n_tokens), dtype=bool)

    for rb in rebalance_bars:
        funds = fund_matrix[rb, :]
        rsi_vals = rsi_matrix[rb, :]
        vol_vals = vol_matrix[rb, :]
        adx_vals = adx_matrix[rb, :]

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

        # Rank ALL valid tokens by funding (ascending: lowest funding = long candidate)
        eligible_indices = np.where(valid)[0]
        eligible_funds = funds[eligible_indices]
        sorted_local = np.argsort(eligible_funds)

        # --- SHORT CANDIDATES: highest funding, filtered ---
        n_short_candidates = min(N_SHORT * 3, n_valid // 2)  # wider pool to filter from
        short_pool = eligible_indices[sorted_local[-n_short_candidates:]][::-1]  # highest first
        shorts_selected = []
        for idx in short_pool:
            if len(shorts_selected) >= N_SHORT:
                break
            # Confirmation: RSI overbought + volume active + trend exists
            rsi_ok = np.isnan(rsi_vals[idx]) or rsi_vals[idx] >= RSI_SHORT_MIN
            vol_ok = np.isnan(vol_vals[idx]) or vol_vals[idx] >= VOL_RATIO_MIN
            adx_ok = np.isnan(adx_vals[idx]) or adx_vals[idx] >= ADX_MIN
            if rsi_ok and vol_ok and adx_ok:
                shorts_selected.append(idx)

        # --- LONG CANDIDATES: lowest funding, filtered ---
        n_long_candidates = min(N_LONG * 3, n_valid // 2)
        long_pool = eligible_indices[sorted_local[:n_long_candidates]]  # lowest first
        longs_selected = []
        for idx in long_pool:
            if len(longs_selected) >= N_LONG:
                break
            rsi_ok = np.isnan(rsi_vals[idx]) or rsi_vals[idx] <= RSI_LONG_MAX
            vol_ok = np.isnan(vol_vals[idx]) or vol_vals[idx] >= VOL_RATIO_MIN
            adx_ok = np.isnan(adx_vals[idx]) or adx_vals[idx] >= ADX_MIN
            if rsi_ok and vol_ok and adx_ok:
                longs_selected.append(idx)

        if len(shorts_selected) == 0 and len(longs_selected) == 0:
            continue

        next_rb = min(rb + REBALANCE_BARS, max_bars)
        for idx in longs_selected:
            long_membership[rb:next_rb, idx] = True
        for idx in shorts_selected:
            short_membership[rb:next_rb, idx] = True

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
            name='s163_funding_filtered_ls',
            breakeven_atr=0.0,
        )

    return results
