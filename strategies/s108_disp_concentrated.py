"""
s108 Concentrated Dispersion Momentum — Fundamentally Different Architecture

Unlike s98 (per-token MACD signal) or s100 (dispersion as binary gate), this
strategy uses dispersion as a CONTINUOUS LEVERAGE SCALER combined with
concentrated high-conviction positions.

Key innovations:
  1. DISPERSION-SCALED LEVERAGE: leverage = base * (dispersion / median_dispersion)
     High dispersion → full 7x. Low dispersion → 2x minimum. No binary cutoff.
     This keeps capital deployed at ALL times but modulates risk.

  2. COMPOSITE RANKING: Not just momentum, but:
     50% trailing return (1-day) — raw momentum
     25% ADX — trend quality (higher ADX = more reliable trend)
     25% taker buy ratio — institutional flow direction
     This selects QUALITY momentum, not just raw price change.

  3. CONCENTRATED: 5 positions (not 8-10) — each winner matters more
     s93 proved 3-5 positions optimal for crypto momentum

  4. FAST REBALANCE: 48h (2-day) — crypto momentum decays in days

  5. NO STOPS, hold to rebalance — proven from s93 (stops kill momentum)

  6. LONG-ONLY: shorts hurt in current market structure

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

# ── Configuration ────────────────────────────────────────────────
LOOKBACK_BARS = 24              # 1-day trailing return
REBALANCE_BARS = 48             # 2-day rebalance (fast)
N_LONG = 5                      # Concentrated: 5 positions
MIN_ADV_USD = 5_000_000         # $5M ADV (broader universe than s98's $1B)
MIN_ELIGIBLE = 20

# Dispersion-scaled leverage
DISP_LOOKBACK = 24              # 1-day dispersion window
DISP_MEDIAN_WINDOW = 336        # 14-day rolling median
BASE_LEVERAGE = 7.0             # Max leverage at high dispersion
MIN_LEVERAGE = 2.0              # Floor leverage at low dispersion

# Composite ranking weights
MOM_WEIGHT = 0.50               # Trailing return
ADX_WEIGHT = 0.25               # Trend quality
TAKER_WEIGHT = 0.25             # Institutional flow

# Trade management
STOP_MULT = 99.0                # No hard stop
TRAIL_MULT = 99.0               # No trail (hold to rebalance)
TARGET_MULT = 999.0
NO_STOP_BARS = 48
MIN_HOLD = 1
MAX_HOLD = 48                   # Force exit at rebalance boundary
EDGE = 0.35
BREAKEVEN_ATR = 0.0             # No breakeven (hold to rebalance)

WARMUP = 400


def _trailing_return(close, lookback):
    """Compute trailing return over lookback bars."""
    n = len(close)
    ret = np.zeros(n, dtype=np.float64)
    if n > lookback:
        ret[lookback:] = (close[lookback:] - close[:-lookback]) / np.maximum(close[:-lookback], 1e-10)
    return ret


def _compute_dispersion(ret_matrix, lookback=DISP_LOOKBACK):
    """Cross-sectional return dispersion at each bar."""
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


def _rank_normalize(arr):
    """Rank-normalize an array to [0, 1]. NaNs stay NaN."""
    result = np.full_like(arr, np.nan)
    valid = ~np.isnan(arr)
    if np.sum(valid) < 2:
        return result
    vals = arr[valid]
    ranks = np.argsort(np.argsort(vals)).astype(np.float64)
    ranks /= (len(ranks) - 1)  # Normalize to [0, 1]
    result[valid] = ranks
    return result


def _majority_regime(token_data, eligible_tokens, max_bars, bar_idx):
    """Determine majority regime across universe at a given bar."""
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
    """Concentrated dispersion-scaled momentum.

    Args:
        contexts: {token: (ctx_spot, ctx_perp)} or {token: ctx}

    Returns:
        {token: StrategyResult} for selected positions
    """
    # ── Step 1: Build token data ────────────────────────────────
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

        adx = ctx.ind_1h['adx']
        taker = ctx.ind_1h['taker']
        ret_1d = _trailing_return(close, LOOKBACK_BARS)
        regime = ctx.regime_1h

        token_data[token] = {
            'close': close,
            'n': n,
            'ret_1d': ret_1d,
            'adx': adx,
            'taker': taker,
            'regime': regime,
            'ctx': ctx,
            'ctx_pair': ctx_pair,
        }

    eligible_tokens = sorted(token_data.keys())
    if len(eligible_tokens) < MIN_ELIGIBLE:
        return {}

    # ── Step 2: Build matrices ─────────────────────────────────
    max_bars = max(d['n'] for d in token_data.values())
    n_tokens = len(eligible_tokens)

    ret_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)
    adx_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)
    taker_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)

    for j, token in enumerate(eligible_tokens):
        d = token_data[token]
        n = d['n']
        offset = max_bars - n
        ret_matrix[offset:offset + n, j] = d['ret_1d']
        adx_matrix[offset:offset + n, j] = d['adx']
        taker_matrix[offset:offset + n, j] = d['taker']

    # ── Step 3: Compute dispersion ─────────────────────────────
    dispersion = _compute_dispersion(ret_matrix, lookback=DISP_LOOKBACK)
    disp_median = pd.Series(dispersion).rolling(
        DISP_MEDIAN_WINDOW, min_periods=DISP_MEDIAN_WINDOW // 2
    ).median().values

    # Continuous leverage scaling: leverage = BASE * (disp / median_disp)
    disp_ratio = np.where(
        disp_median > 0,
        dispersion / np.maximum(disp_median, 1e-10),
        1.0
    )
    leverage_schedule = np.clip(
        BASE_LEVERAGE * disp_ratio,
        MIN_LEVERAGE,
        BASE_LEVERAGE
    )

    # ── Step 4: Rebalance schedule ─────────────────────────────
    warmup = max(WARMUP, DISP_MEDIAN_WINDOW + 50)
    rebalance_bars = list(range(warmup, max_bars, REBALANCE_BARS))

    long_membership = np.zeros((max_bars, n_tokens), dtype=bool)
    lev_per_bar = np.full(max_bars, MIN_LEVERAGE, dtype=np.float64)

    for rb in rebalance_bars:
        majority_regime = _majority_regime(token_data, eligible_tokens, max_bars, rb)
        if majority_regime == CRISIS:
            continue

        lev = leverage_schedule[rb]
        if lev < 1.0:
            continue

        # Composite ranking at this rebalance bar
        mom_scores = ret_matrix[rb, :]
        adx_scores = adx_matrix[rb, :]
        taker_scores = taker_matrix[rb, :]

        # ADV filter
        valid = ~np.isnan(mom_scores) & (mom_scores != 0.0)
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

        # Rank-normalize each factor among eligible tokens
        mom_ranked = _rank_normalize(mom_scores[eligible_idx])
        adx_ranked = _rank_normalize(adx_scores[eligible_idx])
        taker_ranked = _rank_normalize(taker_scores[eligible_idx])

        # Handle NaN taker scores
        taker_ranked = np.nan_to_num(taker_ranked, nan=0.5)
        adx_ranked = np.nan_to_num(adx_ranked, nan=0.5)

        composite = (MOM_WEIGHT * mom_ranked +
                     ADX_WEIGHT * adx_ranked +
                     TAKER_WEIGHT * taker_ranked)

        sorted_idx = np.argsort(composite)
        n_long = min(N_LONG, max(1, n_eligible // 5))
        long_local = sorted_idx[-n_long:]
        long_indices = eligible_idx[long_local]

        next_rb = min(rb + REBALANCE_BARS, max_bars)
        long_membership[rb:next_rb, long_indices] = True
        lev_per_bar[rb:next_rb] = lev

    # ── Step 5: Generate per-token StrategyResult ───────────────
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

        # Per-bar leverage from dispersion scaling
        token_lev = lev_per_bar[offset:offset + n]
        avg_lev = float(np.mean(token_lev[entry_mask])) if np.any(entry_mask) else BASE_LEVERAGE

        results[token] = StrategyResult(
            entry_mask=entry_mask,
            direction=direction,
            market_type=MarketType.PERP,
            leverage=avg_lev,
            stop_mult=STOP_MULT,
            trail_mult=TRAIL_MULT,
            target_mult=TARGET_MULT,
            no_stop_bars=NO_STOP_BARS,
            min_hold=MIN_HOLD,
            max_hold=MAX_HOLD,
            edge=EDGE,
            exit_regimes={CRISIS},
            exchange='binance',
            name='s108_disp_concentrated',
            breakeven_atr=BREAKEVEN_ATR,
            cap_multiplier=6.0,
        )

    return results
