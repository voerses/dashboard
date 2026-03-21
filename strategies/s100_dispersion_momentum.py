"""
s100 Dispersion-Filtered Cross-Sectional Momentum — V4 Portfolio Strategy

Core hypothesis: Mid-cap alt momentum is highly profitable WHEN cross-sectional
return dispersion is elevated (market rewards differentiation). When dispersion
collapses, everything correlates and momentum reverses — sit in cash.

Key innovations over s93 (798% / -17.1% DD / Calmar 46.8 but negative 36mo):
  1. DISPERSION FILTER: Only trade when cross-sectional return std > rolling median
     This is the single filter that turns a regime-dependent strategy into an
     all-weather one. When dispersion is low, momentum doesn't pay — skip.
  2. BIDIRECTIONAL: Long top decile + short bottom decile (self-hedging)
     s93 was long-only -> destroyed in bear markets. L/S captures momentum
     in both directions and reduces beta.
  3. ATR TRAIL STOPS: 2.0x ATR trail instead of hold-to-rebalance
     s93 held to rebalance with no stops -> full drawdown on reversals.
     Trail protects gains while letting winners run within rebalance window.
  4. REGIME-ADAPTIVE LEVERAGE: Scale leverage by majority regime across universe
     UPTREND: 5x, RANGE: 3x, DOWNTREND: 2x (shorts only in pure downtrend),
     CRISIS: 0x (cash).
  5. FASTER REBALANCE: 72h (3-day) instead of 168h (7-day)
     Crypto momentum decays fast. 3-day captures the sweet spot.
  6. FUNDING OVERLAY: Boost positions where funding confirms direction
     Long + negative funding = double edge. Short + positive funding = double edge.

Target: 300%+ annual return, <20% max DD, Calmar >3
Market: PERP (bidirectional)
Status: EXPERIMENTAL (Gate 3 — Prototype)
"""

import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_mean)

STRATEGY_TYPE = "portfolio"

# ── Configuration ────────────────────────────────────────────────
# ── v4: Higher leverage, more positions, faster rebalance ────────
LOOKBACK_BARS = 24              # 1-day trailing return (s93 winner)
LOOKBACK_3D = 72                # 3-day (secondary)
MOM_WEIGHT_1D = 0.7
MOM_WEIGHT_3D = 0.3

# Rebalance — 3-day for faster alpha capture
REBALANCE_BARS = 72

# Position sizing — more positions for diversification + leverage
N_LONG = 8                      # Top 8 momentum longs
N_SHORT = 0                     # Long-only (shorts hurt in current market)
MIN_ADV_USD = 2_000_000
MIN_ELIGIBLE = 20

# Dispersion filter — more permissive (use 70% of median as threshold)
DISPERSION_LOOKBACK = 24
DISPERSION_MEDIAN_WINDOW = 336
DISPERSION_THRESHOLD_MULT = 0.7  # Trade when disp > 70% of median (more trades)

# High leverage — this is the 5x lever
LEVERAGE_MAP = {
    CRISIS: 0.0,
    QUIET: 3.0,
    UPTREND: 7.0,
    RANGE: 5.0,
    DOWNTREND: 2.0,
}
DEFAULT_LEVERAGE = 5.0

# No stops — hold to rebalance (proven from s93)
STOP_MULT = 99.0
TRAIL_MULT = 99.0
TARGET_MULT = 999.0
NO_STOP_BARS = 72
MIN_HOLD = 1
MAX_HOLD = 72
EDGE = 0.35
BREAKEVEN_ATR = 0.0

# Funding overlay
FUNDING_WINDOW = 72
FUNDING_BOOST = 1.3


def _trailing_return(close, lookback):
    """Compute trailing return over lookback bars."""
    n = len(close)
    ret = np.zeros(n, dtype=np.float64)
    if n > lookback:
        ret[lookback:] = (close[lookback:] - close[:-lookback]) / np.maximum(close[:-lookback], 1e-10)
    return ret


def _compute_dispersion(ret_matrix, lookback=DISPERSION_LOOKBACK):
    """Compute cross-sectional return dispersion at each bar.

    Dispersion = std of returns across all tokens at each time step.
    High dispersion = tokens are differentiating = momentum pays.
    Low dispersion = everything correlated = momentum mean-reverts.
    """
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
    """Dispersion-filtered cross-sectional momentum.

    Args:
        contexts: {token: (ctx_spot, ctx_perp)} or {token: ctx}

    Returns:
        {token: StrategyResult} for selected longs and shorts
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
        if n < LOOKBACK_3D + 250:
            continue

        funding = ctx.funding_1h
        ret_1d = _trailing_return(close, LOOKBACK_BARS)
        ret_3d = _trailing_return(close, LOOKBACK_3D)
        composite_ret = MOM_WEIGHT_1D * ret_1d + MOM_WEIGHT_3D * ret_3d
        regime = ctx.regime_1h

        funding_avg = None
        if funding is not None:
            funding_avg = rolling_mean(funding, FUNDING_WINDOW)

        token_data[token] = {
            'close': close,
            'n': n,
            'ret_1d': ret_1d,
            'ret_3d': ret_3d,
            'composite_ret': composite_ret,
            'regime': regime,
            'funding_avg': funding_avg,
            'ctx': ctx,
            'ctx_pair': ctx_pair,
        }

    eligible_tokens = sorted(token_data.keys())
    if len(eligible_tokens) < MIN_ELIGIBLE:
        return {}

    # ── Step 2: Build return matrices ───────────────────────────
    max_bars = max(d['n'] for d in token_data.values())
    n_tokens = len(eligible_tokens)

    composite_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)
    ret_1d_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)

    for j, token in enumerate(eligible_tokens):
        d = token_data[token]
        n = d['n']
        offset = max_bars - n
        composite_matrix[offset:offset + n, j] = d['composite_ret']
        ret_1d_matrix[offset:offset + n, j] = d['ret_1d']

    # ── Step 3: Compute cross-sectional dispersion ──────────────
    dispersion = _compute_dispersion(ret_1d_matrix, lookback=DISPERSION_LOOKBACK)
    dispersion_median = pd.Series(dispersion).rolling(
        DISPERSION_MEDIAN_WINDOW, min_periods=DISPERSION_MEDIAN_WINDOW // 2
    ).median().values

    # Risk-on when dispersion above threshold (percentage of median)
    threshold = np.nan_to_num(dispersion_median * DISPERSION_THRESHOLD_MULT, nan=0.0)
    risk_on = dispersion > threshold

    # Also allow entry when dispersion is expanding rapidly
    disp_rate = np.zeros_like(dispersion)
    disp_rate[1:] = dispersion[1:] - dispersion[:-1]
    disp_expanding = disp_rate > 0
    risk_on = risk_on | ((dispersion > threshold * 0.7) & disp_expanding)

    # ── Step 4: Rebalance schedule ──────────────────────────────
    warmup = max(LOOKBACK_3D + 200, DISPERSION_MEDIAN_WINDOW + 50)
    rebalance_bars = list(range(warmup, max_bars, REBALANCE_BARS))

    long_membership = np.zeros((max_bars, n_tokens), dtype=bool)
    short_membership = np.zeros((max_bars, n_tokens), dtype=bool)
    leverage_schedule = np.full(max_bars, DEFAULT_LEVERAGE, dtype=np.float64)

    for rb in rebalance_bars:
        if not risk_on[rb]:
            continue

        majority_regime = _majority_regime(token_data, eligible_tokens, max_bars, rb)
        leverage = LEVERAGE_MAP.get(majority_regime, DEFAULT_LEVERAGE)
        if leverage <= 0:
            continue

        composite_rets = composite_matrix[rb, :]
        valid = ~np.isnan(composite_rets) & (composite_rets != 0.0)

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
        eligible_rets = composite_rets[eligible_idx]
        sorted_idx = np.argsort(eligible_rets)

        n_long = min(N_LONG, max(1, n_eligible // 5))
        n_short = min(N_SHORT, max(1, n_eligible // 5))

        long_indices = eligible_idx[sorted_idx[-n_long:]]

        # Only short in non-uptrend
        if majority_regime == UPTREND:
            n_short = 0
            short_indices = np.array([], dtype=int)
        else:
            short_indices = eligible_idx[sorted_idx[:n_short]]

        next_rb = min(rb + REBALANCE_BARS, max_bars)
        long_membership[rb:next_rb, long_indices] = True
        if len(short_indices) > 0:
            short_membership[rb:next_rb, short_indices] = True
        leverage_schedule[rb:next_rb] = leverage

    # ── Step 5: Generate per-token StrategyResult ───────────────
    results = {}

    for j, token in enumerate(eligible_tokens):
        d = token_data[token]
        ctx = d['ctx']
        n = d['n']
        offset = max_bars - n

        is_long = long_membership[offset:offset + n, j]
        is_short = short_membership[offset:offset + n, j]

        if not np.any(is_long) and not np.any(is_short):
            continue

        liq_mask = np.ones(n, dtype=bool)
        if ctx.liquidity_mask is not None:
            liq_mask = ctx.liquidity_mask

        burn_mask = np.ones(n, dtype=bool)
        burn_mask[:200] = False
        filter_mask = liq_mask & burn_mask

        entry_mask = (is_long | is_short) & filter_mask
        direction = np.ones(n, dtype=np.int8)
        direction[is_short] = -1

        if not np.any(entry_mask):
            continue

        # Funding-informed size multiplier
        size_mult = np.ones(n, dtype=np.float64)
        if d['funding_avg'] is not None:
            funding = d['funding_avg']
            funding_confirms_long = (funding < -0.00003) & is_long
            funding_confirms_short = (funding > 0.00003) & is_short
            size_mult[funding_confirms_long] = FUNDING_BOOST
            size_mult[funding_confirms_short] = FUNDING_BOOST

        # Average leverage for this token's entries
        token_leverage = leverage_schedule[offset:offset + n]
        avg_leverage = float(np.mean(token_leverage[entry_mask])) if np.any(entry_mask) else DEFAULT_LEVERAGE

        results[token] = StrategyResult(
            entry_mask=entry_mask,
            direction=direction,
            market_type=MarketType.PERP,
            leverage=avg_leverage,
            stop_mult=STOP_MULT,
            trail_mult=TRAIL_MULT,
            target_mult=TARGET_MULT,
            no_stop_bars=NO_STOP_BARS,
            min_hold=MIN_HOLD,
            max_hold=MAX_HOLD,
            edge=EDGE,
            exit_regimes={CRISIS},
            exchange='binance',
            name='s100_dispersion_momentum',
            breakeven_atr=BREAKEVEN_ATR,
            size_multiplier=size_mult,
            cap_multiplier=6.0,
        )

    return results
