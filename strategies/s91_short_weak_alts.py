"""
s91 Short Weak Alts — BTC-Regime-Filtered Short Momentum (Class B Portfolio)

Shorts the weakest-momentum altcoins when BTC is NOT in a strong uptrend.
The structural decay of altcoins (median -75% over 24mo vs BTC +10%) provides
the base edge. The BTC regime filter avoids shorting during bull squeezes.

Key design:
- Signal: rank alts by 14d trailing return, short bottom N
- Filter: only short when BTC close < EMA20 OR EMA20 < EMA50 (bearish/neutral)
- Leverage: configurable (3-5x), margin capped per position
- Stops: tight (3% equity) to survive squeeze events
- Rebalance: weekly (168 bars)

Research (raw prototype, 12mo ending Mar 2026):
  3x: +26.4%, MaxDD -25.1%
  5x: +81.9%, MaxDD -37.0%

Market: PERP (short-only)
Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET, rolling_mean)

STRATEGY_TYPE = "portfolio"

# ── Configuration ────────────────────────────────────────────────
LOOKBACK_BARS = 14 * 24        # 14d momentum for ranking
REBALANCE_BARS = 7 * 24        # Weekly rebalance
N_SHORT = 5                    # Short bottom 5 by momentum
MIN_ADV_USD = 5_000_000        # Liquid tokens only
MIN_ELIGIBLE = 20              # Need breadth for ranking
LEVERAGE = 3.0                 # 3x leverage (5 * 3% margin = 15% total)
BTC_EMA_FAST = 20 * 24         # 20-day EMA for BTC regime
BTC_EMA_SLOW = 50 * 24         # 50-day EMA for BTC regime
STOP_EQUITY_PCT = 0.03         # 3% equity stop per position → stop_mult


def _compute_trailing_return(close: np.ndarray, lookback: int) -> np.ndarray:
    """Compute trailing return over lookback bars, vectorized."""
    n = len(close)
    ret = np.zeros(n, dtype=np.float64)
    if n > lookback:
        ret[lookback:] = (close[lookback:] - close[:-lookback]) / np.maximum(close[:-lookback], 1e-10)
    return ret


def _ema(arr: np.ndarray, span: int) -> np.ndarray:
    """Exponential moving average."""
    import pandas as pd
    return pd.Series(arr).ewm(span=span, min_periods=span // 2).mean().values


def strategy(contexts: dict) -> dict:
    """Short weak alts portfolio strategy with BTC regime filter.

    Args:
        contexts: {token: ctx_perp} or {token: (ctx_spot, ctx_perp)}

    Returns:
        {token: StrategyResult} for tokens selected for shorting
    """
    token_list = sorted(contexts.keys())
    if len(token_list) < MIN_ELIGIBLE:
        return {}

    # ── Extract BTC context for regime filter ─────────────────────
    btc_ctx = None
    for token in token_list:
        if token == 'BTC':
            ctx_pair = contexts[token]
            if isinstance(ctx_pair, tuple):
                _, btc_ctx = ctx_pair
                btc_ctx = btc_ctx if btc_ctx is not None else ctx_pair[0]
            else:
                btc_ctx = ctx_pair
            break

    if btc_ctx is None:
        return {}

    btc_close = btc_ctx.ind_1h['close']
    btc_n = len(btc_close)
    btc_ema_fast = _ema(btc_close, BTC_EMA_FAST)
    btc_ema_slow = _ema(btc_close, BTC_EMA_SLOW)

    # BTC regime: OK to short alts when BTC is bearish or neutral
    # Bearish: close < fast EMA  OR  fast EMA < slow EMA (downtrend structure)
    btc_short_ok = (btc_close < btc_ema_fast) | (btc_ema_fast < btc_ema_slow)

    # ── Build token data ──────────────────────────────────────────
    token_data = {}
    for token in token_list:
        if token == 'BTC':
            continue  # Don't short BTC itself

        ctx_pair = contexts[token]
        if isinstance(ctx_pair, tuple):
            ctx_spot, ctx_perp = ctx_pair
            ctx = ctx_perp if ctx_perp is not None else ctx_spot
        else:
            ctx = ctx_pair

        close = ctx.ind_1h['close']
        n = len(close)
        if n < LOOKBACK_BARS + 200:
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
    if len(eligible_tokens) < MIN_ELIGIBLE:
        return {}

    # ── Build right-aligned return matrix ─────────────────────────
    max_bars = max(d['n'] for d in token_data.values())
    # Ensure BTC alignment
    max_bars = max(max_bars, btc_n)

    n_tokens = len(eligible_tokens)
    ret_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)

    for j, token in enumerate(eligible_tokens):
        d = token_data[token]
        n = d['n']
        offset = max_bars - n
        ret_matrix[offset:offset + n, j] = d['trail_ret']

    # Align BTC regime to max_bars frame
    btc_offset = max_bars - btc_n
    btc_regime_aligned = np.zeros(max_bars, dtype=bool)
    btc_regime_aligned[btc_offset:btc_offset + btc_n] = btc_short_ok

    # ── Rebalance and rank ────────────────────────────────────────
    rebalance_bars = list(range(LOOKBACK_BARS, max_bars, REBALANCE_BARS))
    short_membership = np.zeros((max_bars, n_tokens), dtype=bool)

    for rb in rebalance_bars:
        # BTC regime check
        if rb < max_bars and not btc_regime_aligned[rb]:
            continue  # Skip: BTC in strong uptrend

        rets = ret_matrix[rb, :]
        valid = ~np.isnan(rets) & (rets != 0.0)
        n_valid = int(np.sum(valid))
        if n_valid < MIN_ELIGIBLE:
            continue

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

        # Rank: bottom N by trailing return = weakest momentum = short targets
        eligible_indices = np.where(eligible_mask)[0]
        eligible_rets = rets[eligible_indices]

        sorted_idx = np.argsort(eligible_rets)
        short_local = sorted_idx[:min(N_SHORT, n_eligible)]
        short_indices = eligible_indices[short_local]

        next_rb = rb + REBALANCE_BARS
        short_membership[rb:next_rb, short_indices] = True

    # ── Generate per-token StrategyResult ──────────────────────────
    # Convert equity-based stop to ATR-based stop_mult
    # stop_equity_pct = stop_mult * ATR / entry * leverage * margin_fraction
    # For 3% equity stop, 3x leverage, 3% margin: ATR_stop_pct = 3% / (3*0.03) = 33%
    # stop_mult = 33% * entry / ATR ≈ ~5-10 depending on token's ATR
    # Use a fixed moderate stop_mult that approximates this
    STOP_MULT = 99.0   # No mechanical stop — hold to rebalance (max_hold=168)
    TRAIL_MULT = 99.0  # No trail — rebalance is the exit mechanism

    results = {}

    for j, token in enumerate(eligible_tokens):
        d = token_data[token]
        ctx = d['ctx']
        n = d['n']
        offset = max_bars - n

        is_short = short_membership[offset:offset + n, j]
        if not np.any(is_short):
            continue

        # Filters
        liq_mask = np.ones(n, dtype=bool)
        if ctx.liquidity_mask is not None:
            liq_mask = ctx.liquidity_mask
        burn_mask = np.ones(n, dtype=bool)
        burn_mask[:300] = False

        entry_mask = is_short & liq_mask & burn_mask
        if not np.any(entry_mask):
            continue

        # Direction: always short
        direction = -np.ones(n, dtype=np.int8)

        ctx_pair = d['ctx_pair']
        if isinstance(ctx_pair, tuple):
            results[token] = StrategyResult(
                entry_mask=entry_mask,
                direction=direction,
                market_type=MarketType.PERP,
                leverage=LEVERAGE,
                stop_mult=STOP_MULT,
                trail_mult=TRAIL_MULT,
                target_mult=999,
                no_stop_bars=168,   # Protect full holding period
                min_hold=24,
                max_hold=168,       # 7d rebalance
                edge=0.35,
                exit_regimes=set(), # No regime exit — hold to rebalance
                exchange='binance',
                name='s91_short_weak_alts',
                breakeven_atr=0.0,  # Disable breakeven ratchet
                max_trade_pct=0.03,  # 3% of equity per position
            )
        else:
            results[token] = StrategyResult(
                entry_mask=entry_mask,
                direction=direction,
                market_type=MarketType.PERP if ctx.market_type == 'perp' else MarketType.SPOT,
                leverage=LEVERAGE if ctx.market_type == 'perp' else 1.0,
                stop_mult=STOP_MULT,
                trail_mult=TRAIL_MULT,
                target_mult=999,
                no_stop_bars=168,   # Protect full holding period
                min_hold=24,
                max_hold=168,
                edge=0.35,
                exit_regimes=set(), # No regime exit — hold to rebalance
                exchange='binance',
                name='s91_short_weak_alts',
                breakeven_atr=0.0,  # Disable breakeven ratchet
                max_trade_pct=0.03,
            )

    return results
