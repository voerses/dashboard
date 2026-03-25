"""
s146 Cross-Sectional Funding Sentiment L/S — Quality-Filtered

Same signal as s85/s144 but with strict token quality filters:
- ADV >= $20M (real liquidity, executable)
- History >= 1 year (established tokens only)
- No meme tokens (extreme noise, manipulation risk)
- Funding rate capped at |0.5%/hr| (ignore noise/manipulation)
- 24h funding window (fastest useful signal)
- 1x leverage (pure signal quality)

Market: PERP (dollar-neutral L/S)
Status: EXPERIMENTAL (Gate 3)
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET, rolling_mean)

STRATEGY_TYPE = "portfolio"

# ── Configuration ────────────────────────────────────────────────
FUNDING_WINDOW = 24            # 24h smoothing (fast signal)
REBALANCE_BARS = 7 * 24        # Weekly rebalance
N_LONG = 5
N_SHORT = 5
MIN_ADV_USD = 20_000_000       # $20M minimum ADV
MIN_HISTORY_BARS = 8760        # >= 1 year of hourly data
MIN_ELIGIBLE = 15
LEVERAGE = 1.0                 # 1x — pure signal quality
MAX_ABS_FUNDING = 0.005        # Cap: ignore |funding| > 0.5%/hr (noise)
WARMUP = 200

# Tokens to exclude — memes, joke coins, extreme speculation
EXCLUDED_TOKENS = {
    # Classic memes
    'DOGE', 'SHIB', 'PEPE', 'BONK', 'FLOKI', 'WIF', 'NEIRO',
    'TRUMP', 'PENGU', 'BARD', 'TURBO', 'MEME', 'BABYDOGE',
    # Degen/meme-adjacent
    'FARTCOIN', 'MOODENG', 'GIGGLE', 'PUMP', 'KITE', 'PIPPIN',
    'SIREN', 'RIVER', 'POWER', 'NIGHT', 'BEAT', 'RAVE',
    'USELESS', 'GWEI', 'WET',
    # Non-crypto (stock tokens)
    'TSLA', 'PLTR', 'COIN', 'MSTR', 'HOOD', 'INTC',
    # Commodities
    'XAU', 'XAG', 'XPT',
}


def strategy(contexts: dict) -> dict:
    """Cross-sectional funding sentiment long/short with quality filters."""
    token_data = {}
    for token, ctx_pair in contexts.items():
        # Exclude garbage tokens
        if token in EXCLUDED_TOKENS:
            continue

        if isinstance(ctx_pair, tuple):
            ctx_spot, ctx_perp = ctx_pair
            ctx = ctx_perp if ctx_perp is not None else ctx_spot
        else:
            ctx = ctx_pair

        close = ctx.ind_1h['close']
        n = len(close)

        # Quality filter: minimum history
        if n < max(WARMUP + FUNDING_WINDOW, MIN_HISTORY_BARS):
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

            # ADV filter
            ctx = d['ctx']
            if ctx.rolling_adv is not None and local_bar < len(ctx.rolling_adv):
                if ctx.rolling_adv[local_bar] < MIN_ADV_USD:
                    valid[j] = False
                    continue

            # Funding rate cap — ignore extreme outliers
            if abs(funds[j]) > MAX_ABS_FUNDING:
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

        long_local = sorted_local[:n_long]
        short_local = sorted_local[-n_short:]

        long_indices = eligible_indices[long_local]
        short_indices = eligible_indices[short_local]

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
            stop_mult=99.0,
            trail_mult=99.0,
            target_mult=999,
            no_stop_bars=168,
            min_hold=24,
            max_hold=168,
            edge=0.35,
            exit_regimes=set(),
            exchange='binance',
            name='s146_quality_funding_ls',
            breakeven_atr=0.0,
        )

    return results
