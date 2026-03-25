"""
s170 Smart Money Long/Short -- V4 Portfolio Strategy (Class B)

Uses smart money flow signals (GMGN.AI / DexScreener proxy) to rank
meme/volatile tokens cross-sectionally. Longs tokens with strongest
smart money accumulation, shorts tokens with strongest distribution.

Hypothesis: smart money wallets (tracked by GMGN) accumulate tokens
before pumps and distribute before dumps. Net buy/sell flow from these
wallets provides minutes-to-hours of lead time over price action.

Market: PERP only
Status: EXPERIMENTAL (Gate 3P -- prototype)
"""

import os
import numpy as np
import pandas as pd

from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_mean, rolling_std)

STRATEGY_TYPE = "portfolio"

# -- Parameters ---------------------------------------------------------------
SMART_MONEY_WINDOW = 72       # 3-day rolling net flow window
REBALANCE_BARS = 7 * 24      # weekly rebalance (matching s160)
N_LONG = 5
N_SHORT = 7                   # asymmetric: more shorts (proven in s160)
MIN_ELIGIBLE = 10             # need enough tokens with data
LEVERAGE = 1.0
WARMUP = 250
MIN_ADV_USD = 2_000_000       # lower than s160 since meme tokens have less volume

# -- Token universe (must match token_map.json keys) -------------------------
TOKENS = [
    "BONK", "WIF", "FARTCOIN", "MOODENG", "PENGU", "TRUMP",
    "NEIRO", "FLOKI", "PEPE", "TURBO", "VIRTUAL", "JUP",
    "ORCA", "DOGE", "SHIB", "DEXE", "SPX", "PIPPIN",
]

# -- Module-level cache (Pattern A from s120) ---------------------------------
_sm_cache = {}  # {token: pd.Series with DatetimeIndex -> smart_net_flow_usd}
_sm_loaded = False

# Per-call alignment cache: {(token, n_bars): np.ndarray}
_aligned_cache = {}

SM_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "alternative", "gmgn_ai",
)


def _load_smart_money():
    """Load all smart money parquet files into cache. No-op after first call."""
    global _sm_cache, _sm_loaded
    if _sm_loaded:
        return
    _sm_loaded = True

    for token in TOKENS:
        path = os.path.join(SM_DATA_DIR, f"{token}_smart_money.parquet")
        if not os.path.exists(path):
            continue
        try:
            df = pd.read_parquet(path)
            if "timestamp" not in df.columns or "smart_net_flow_usd" not in df.columns:
                continue
            df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
            df = df.set_index("timestamp").sort_index()
            _sm_cache[token] = df["smart_net_flow_usd"]
        except Exception:
            continue

    if _sm_cache:
        print(f"  [s170] Loaded smart money data for {len(_sm_cache)} tokens")


def _get_sm_aligned(token, idx_1h):
    """Get smart money net flow aligned to strategy's 1h index.

    Returns array of shape (n,) with 0.0 where data is unavailable.
    Uses alignment cache for fast repeat calls.
    """
    cache_key = (token, len(idx_1h))
    if cache_key in _aligned_cache:
        return _aligned_cache[cache_key]

    if token not in _sm_cache:
        result = np.zeros(len(idx_1h), dtype=np.float64)
    else:
        series = _sm_cache[token]
        aligned = series.reindex(idx_1h, method="ffill")
        result = np.nan_to_num(aligned.values.astype(np.float64), nan=0.0)

    _aligned_cache[cache_key] = result
    return result


def strategy(contexts):
    """Cross-sectional smart money L/S on meme/volatile tokens."""
    _load_smart_money()

    # If no smart money data loaded, return empty (graceful degradation)
    if not _sm_cache:
        return {}

    token_data = {}
    for token, ctx_pair in contexts.items():
        if isinstance(ctx_pair, tuple):
            ctx_spot, ctx_perp = ctx_pair
            ctx = ctx_perp if ctx_perp is not None else ctx_spot
        else:
            ctx = ctx_pair

        close = ctx.ind_1h['close']
        n = len(close)
        if n < WARMUP + SMART_MONEY_WINDOW:
            continue

        # Only include tokens that have smart money data
        ticker = ctx.ticker.replace("USDT", "")
        if ticker not in _sm_cache:
            continue

        # Get aligned smart money signal
        sm_flow = _get_sm_aligned(ticker, ctx.idx_1h)

        # Compute rolling smart money score (smoothed net flow)
        sm_score = rolling_mean(sm_flow, SMART_MONEY_WINDOW)

        token_data[token] = {
            'close': close, 'n': n,
            'sm_score': sm_score,
            'ctx': ctx, 'ctx_pair': ctx_pair,
            'ticker': ticker,
        }

    eligible_tokens = sorted(token_data.keys())
    if len(eligible_tokens) < MIN_ELIGIBLE:
        return {}

    max_bars = max(d['n'] for d in token_data.values())
    n_tokens = len(eligible_tokens)

    # Build score matrix for cross-sectional ranking
    score_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)
    for j, token in enumerate(eligible_tokens):
        d = token_data[token]
        n = d['n']
        offset = max_bars - n
        score_matrix[offset:offset + n, j] = d['sm_score']

    # Rebalance schedule
    rebalance_bars = list(range(WARMUP, max_bars, REBALANCE_BARS))
    long_membership = np.zeros((max_bars, n_tokens), dtype=bool)
    short_membership = np.zeros((max_bars, n_tokens), dtype=bool)

    for rb in rebalance_bars:
        scores = score_matrix[rb, :]
        valid = ~np.isnan(scores)

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

            # Regime guard: skip entries during CRISIS
            if hasattr(ctx, 'regime_1h') and ctx.regime_1h is not None:
                if local_bar < len(ctx.regime_1h) and ctx.regime_1h[local_bar] == CRISIS:
                    valid[j] = False

        n_valid = int(np.sum(valid))
        if n_valid < MIN_ELIGIBLE:
            continue

        eligible_indices = np.where(valid)[0]
        eligible_scores = scores[eligible_indices]

        # Z-score normalize cross-sectionally
        mu = np.mean(eligible_scores)
        sigma = np.std(eligible_scores)
        if sigma < 1e-10:
            continue
        z_scores = (eligible_scores - mu) / sigma

        # Rank: highest z-score = most accumulated (long), lowest = most distributed (short)
        sorted_local = np.argsort(z_scores)
        n_long = min(N_LONG, n_valid // 3)
        n_short = min(N_SHORT, n_valid // 3)
        if n_long == 0 or n_short == 0:
            continue

        # Long = top scores (most smart money accumulation)
        long_indices = eligible_indices[sorted_local[-n_long:]]
        # Short = bottom scores (most smart money distribution)
        short_indices = eligible_indices[sorted_local[:n_short]]

        next_rb = min(rb + REBALANCE_BARS, max_bars)
        long_membership[rb:next_rb, long_indices] = True
        short_membership[rb:next_rb, short_indices] = True

    # Build StrategyResult per token
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
            name='s170_smart_money_ls',
            breakeven_atr=0.0,
        )

    return results
