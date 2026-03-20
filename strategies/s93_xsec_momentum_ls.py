"""
s93 Cross-Sectional Momentum — Long Top 3 Weekly

Ranks all alt-coin perps by 1-day trailing return, longs the top 3 with
weekly (168h) rebalancing. No mechanical stops — hold to rebalance.

Key design:
- 1d lookback momentum for ranking (24 bars)
- Weekly rebalance (168 bars) — minimizes cost drag
- Top 3 most liquid tokens (ADV > $2M, N_eligible > 20)
- 3x leverage on perps, 4% margin per position (12% total)
- No stops/trails — pure rebalance exit (max_hold=168)
- Entry only on rebalance bar (prevents re-entry after liquidation)

Performance (V4-cost standalone, 12mo):
  Return: +798%, MaxDD: -17.1%, Calmar: 46.8, Trades: 156

Market: PERP (long only)
Status: EXPERIMENTAL — signal works last 12mo, negative 36mo
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET)

STRATEGY_TYPE = "portfolio"
LOOKBACK_BARS = 24          # 1-day trailing return
REBALANCE_BARS = 168        # Weekly rebalance (7d)
N_LONG = 3                  # Top 3 momentum (concentrated)
N_SHORT = 0
MIN_ADV_USD = 2_000_000
MIN_ELIGIBLE = 20
LEVERAGE = 3.0


def _trailing_return(close, lookback):
    n = len(close)
    ret = np.zeros(n, dtype=np.float64)
    if n > lookback:
        ret[lookback:] = (close[lookback:] - close[:-lookback]) / np.maximum(close[:-lookback], 1e-10)
    return ret


def strategy(contexts):
    token_list = sorted(contexts.keys())
    if len(token_list) < MIN_ELIGIBLE:
        return {}

    # Build token data
    token_data = {}
    for token in token_list:
        if token == "BTC":
            continue
        ctx_pair = contexts[token]
        if isinstance(ctx_pair, tuple):
            ctx = ctx_pair[1] if ctx_pair[1] is not None else ctx_pair[0]
        else:
            ctx = ctx_pair
        close = ctx.ind_1h["close"]
        n = len(close)
        if n < LOOKBACK_BARS + 200:
            continue
        token_data[token] = {
            "close": close, "n": n,
            "ret": _trailing_return(close, LOOKBACK_BARS),
            "ctx": ctx, "ctx_pair": ctx_pair,
        }

    eligible_tokens = sorted(token_data.keys())
    if len(eligible_tokens) < MIN_ELIGIBLE:
        return {}

    # Right-align returns matrix
    max_bars = max(d["n"] for d in token_data.values())
    n_tokens = len(eligible_tokens)
    ret_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)
    for j, token in enumerate(eligible_tokens):
        d = token_data[token]
        n = d["n"]
        offset = max_bars - n
        ret_matrix[offset:offset + n, j] = d["ret"]

    # Compute rebalance schedule
    rebalance_bars = list(range(LOOKBACK_BARS + 200, max_bars, REBALANCE_BARS))

    # Entry only on rebalance bar — prevents re-entry after liquidation
    long_membership = np.zeros((max_bars, n_tokens), dtype=bool)

    for rb in rebalance_bars:
        rets = ret_matrix[rb, :]
        valid = ~np.isnan(rets) & (rets != 0.0)

        # ADV filter
        adv_mask = np.zeros(n_tokens, dtype=bool)
        for j, token in enumerate(eligible_tokens):
            d = token_data[token]
            n = d["n"]
            offset = max_bars - n
            local_bar = rb - offset
            if local_bar < 0 or local_bar >= n:
                continue
            ctx = d["ctx"]
            if ctx.rolling_adv is not None and local_bar < len(ctx.rolling_adv):
                adv_mask[j] = ctx.rolling_adv[local_bar] >= MIN_ADV_USD
            else:
                adv_mask[j] = True

        eligible_mask = valid & adv_mask
        n_eligible = int(np.sum(eligible_mask))
        if n_eligible < MIN_ELIGIBLE:
            continue

        eligible_idx = np.where(eligible_mask)[0]
        eligible_rets = rets[eligible_idx]
        sorted_idx = np.argsort(eligible_rets)
        n_long = min(N_LONG, n_eligible // 4)
        if n_long == 0:
            continue

        long_indices = eligible_idx[sorted_idx[-n_long:]]
        long_membership[rb, long_indices] = True

    # Build per-token StrategyResult
    results = {}
    for j, token in enumerate(eligible_tokens):
        d = token_data[token]
        ctx = d["ctx"]
        n = d["n"]
        offset = max_bars - n
        is_long = long_membership[offset:offset + n, j]
        if not np.any(is_long):
            continue

        liq_mask = np.ones(n, dtype=bool)
        if ctx.liquidity_mask is not None:
            liq_mask = ctx.liquidity_mask

        burn_mask = np.ones(n, dtype=bool)
        burn_mask[:50] = False

        entry_mask = is_long & liq_mask & burn_mask
        if not np.any(entry_mask):
            continue

        direction = np.ones(n, dtype=np.int8)
        results[token] = StrategyResult(
            entry_mask=entry_mask,
            direction=direction,
            market_type=MarketType.PERP,
            leverage=LEVERAGE,
            stop_mult=99.0,
            trail_mult=99.0,
            target_mult=999,
            no_stop_bars=REBALANCE_BARS,
            min_hold=1,
            max_hold=REBALANCE_BARS,
            edge=0.35,
            exit_regimes=set(),
            exchange="binance",
            name="s93_xsec_momentum_ls",
            breakeven_atr=0.0,
            max_trade_pct=0.04,
        )
    return results
