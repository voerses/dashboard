"""
s81 Sector Rotation — V4 Portfolio Strategy (Class B)

Ranks sectors by trailing momentum, selects top K sectors, goes long
the best-performing tokens within those sectors.

Portfolio strategy signature: receives dict of contexts, returns dict
of StrategyResult per token to trade.

Key improvements over V3 sector rotation:
- Progressive trailing stops (no stops in V3 → -58.7% DD)
- Regime-scaled sizing (concentrate in uptrend, zero in crisis)
- Per-token trend confirmation filter
- Perp market with moderate leverage

Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET)

# Module-level marker for V4 portfolio adapter dispatch
STRATEGY_TYPE = "portfolio"

# Exit ablation (v2/v3): flat 1.5 ATR trail + breakeven beats progressive schedule.
# bear_max_hold=12: force exit after 12 bars in DOWNTREND (halves max DD for s80+s81).
TRAIL_SCHEDULE = None

TIME_TRAIL_SCHEDULE = np.array([
    [48, 3.5],
    [120, 2.5],
    [240, 2.0],
    [480, 1.5],
], dtype=np.float64)

# ── Sector Map ───────────────────────────────────────────────────
SECTOR_MAP = {
    'L1': ['BTC', 'ETH', 'SOL', 'SUI', 'AVAX', 'ADA', 'DOT', 'NEAR', 'APT',
           'ATOM', 'TON', 'SEI', 'BERA', 'BNB', 'ICP', 'ETC', 'TRX', 'HBAR',
           'ALGO', 'VET', 'XTZ', 'ZIL', 'LUNC', 'ASTER'],
    'L2': ['ARB', 'OP', 'STRK', 'IMX', 'POL', 'ZKP', 'LAYER'],
    'DeFi': ['AAVE', 'UNI', 'CRV', 'DYDX', 'LDO', 'PENDLE', 'SNX', 'INJ',
             'JUP', 'ENA', 'ONDO', 'ETHFI', 'MORPHO', 'STG', 'KNC', 'CAKE',
             'ZRO', 'JTO', 'TRU', 'ENSO', 'WLFI', 'EIGEN', 'KAVA', 'OM'],
    'Meme': ['DOGE', 'SHIB', 'PEPE', 'BONK', 'FLOKI', 'WIF', 'NEIRO',
             'TRUMP', 'PENGU', 'BARD', 'GIGGLE', 'PUMP', 'KITE'],
    'Gaming': ['AXS', 'SAND', 'GALA', 'YGG', 'ALICE', 'AGLD', 'TLM',
               'CHZ', 'APE'],
    'AI': ['FET', 'RENDER', 'TAO', 'AIXBT', 'VIRTUAL', 'WLD', '0G', 'SAHARA'],
    'Infra': ['LINK', 'TRB', 'FIL', 'AR', 'STEEM', 'FIO', 'DENT',
              'BIO', 'SIGN', 'FORM', 'ORDI', 'TIA', 'PHA'],
    'Privacy': ['XMR', 'ZEC', 'ZEN', 'DASH', 'DUSK', 'ZAMA', 'LIT', 'SENT'],
    'Payments': ['XRP', 'XLM', 'LTC', 'BCH', 'PAXG'],
    'Emerging': ['AT', 'BREV', 'MIRA', 'XPL'],
}
TOKEN_TO_SECTOR = {}
for _s, _toks in SECTOR_MAP.items():
    for _t in _toks:
        TOKEN_TO_SECTOR[_t] = _s

# ── Configuration ────────────────────────────────────────────────
LOOKBACK_BARS = 14 * 24     # 14 days
REBALANCE_BARS = 7 * 24     # 7 day rebalance
TOP_SECTORS = 3              # Long top 3 sectors
MAX_TOKENS_PER_SECTOR = 8   # Limit to top 8 tokens per sector
MIN_SECTOR_TOKENS = 2       # Minimum tokens for a sector to qualify
MIN_ADV_USD = 500_000
LEVERAGE = 2.0


def _compute_trailing_return(close: np.ndarray, lookback: int) -> np.ndarray:
    n = len(close)
    ret = np.zeros(n, dtype=np.float64)
    if n > lookback:
        ret[lookback:] = (close[lookback:] - close[:-lookback]) / np.maximum(close[:-lookback], 1e-10)
    return ret


def strategy(contexts: dict) -> dict:
    """Sector rotation portfolio strategy.

    Args:
        contexts: {token: context} or {token: (ctx_spot, ctx_perp)}

    Returns:
        {token: StrategyResult} for tokens in selected sectors
    """
    # ── Step 1: Load token data and assign sectors ────────────────
    token_data = {}
    for token in sorted(contexts.keys()):
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

        sector = TOKEN_TO_SECTOR.get(token, 'Emerging')
        trail_ret = _compute_trailing_return(close, LOOKBACK_BARS)
        token_data[token] = {
            'close': close,
            'n': n,
            'trail_ret': trail_ret,
            'sector': sector,
            'ctx': ctx,
            'ctx_pair': ctx_pair,
        }

    if len(token_data) < MIN_SECTOR_TOKENS * TOP_SECTORS:
        return {}

    # ── Step 2: Build sector return matrix using max_bars alignment ──
    tokens_list = sorted(token_data.keys())
    max_bars = max(d['n'] for d in token_data.values())
    n_tokens = len(tokens_list)

    # Per-token return matrix (max_bars, n_tokens) — NaN-padded
    ret_matrix = np.full((max_bars, n_tokens), np.nan, dtype=np.float64)
    for j, token in enumerate(tokens_list):
        d = token_data[token]
        n = d['n']
        offset = max_bars - n
        ret_matrix[offset:offset + n, j] = d['trail_ret']

    # Map sectors to token indices
    sectors = sorted(set(d['sector'] for d in token_data.values()))
    sector_tokens = {s: [] for s in sectors}
    for j, token in enumerate(tokens_list):
        sector_tokens[token_data[token]['sector']].append(j)

    # ── Step 3: At each rebalance, rank sectors, select top tokens ──
    rebalance_bars = list(range(LOOKBACK_BARS, max_bars, REBALANCE_BARS))
    basket_membership = np.zeros((max_bars, n_tokens), dtype=bool)

    for rb in rebalance_bars:
        # Compute sector median returns (only from tokens with valid data)
        sector_returns = {}
        for sector, indices in sector_tokens.items():
            rets = [ret_matrix[rb, j] for j in indices if not np.isnan(ret_matrix[rb, j])]
            if len(rets) >= MIN_SECTOR_TOKENS:
                sector_returns[sector] = np.median(rets)

        if len(sector_returns) < TOP_SECTORS:
            continue

        # Rank sectors, select top K
        sorted_sectors = sorted(sector_returns.items(), key=lambda x: x[1], reverse=True)
        top_sector_names = [s for s, _ in sorted_sectors[:TOP_SECTORS]]

        # Within each top sector, select top tokens by return
        next_rb = rb + REBALANCE_BARS
        for sector_name in top_sector_names:
            indices = sector_tokens.get(sector_name, [])
            # Filter to tokens with valid data and ADV
            valid_indices = []
            for j in indices:
                if np.isnan(ret_matrix[rb, j]):
                    continue
                token = tokens_list[j]
                d = token_data[token]
                n_tok = d['n']
                offset_tok = max_bars - n_tok
                local_bar = rb - offset_tok
                if local_bar < 0 or local_bar >= n_tok:
                    continue
                ctx = d['ctx']
                if ctx.rolling_adv is not None and local_bar < len(ctx.rolling_adv):
                    if ctx.rolling_adv[local_bar] < MIN_ADV_USD:
                        continue
                valid_indices.append(j)

            if not valid_indices:
                continue

            # Rank within sector by return, take top N
            sector_rets = [(j, ret_matrix[rb, j]) for j in valid_indices]
            sector_rets.sort(key=lambda x: x[1], reverse=True)
            selected = [j for j, _ in sector_rets[:MAX_TOKENS_PER_SECTOR]]

            basket_membership[rb:next_rb, selected] = True

    # ── Step 4: Generate per-token StrategyResult ─────────────────
    results = {}

    for j, token in enumerate(tokens_list):
        d = token_data[token]
        ctx = d['ctx']
        n = d['n']
        offset = max_bars - n

        in_basket = basket_membership[offset:offset + n, j]
        if not np.any(in_basket):
            continue

        entry_mask = in_basket.copy()
        close = ctx.ind_1h['close']
        ema_20 = ctx.ind_1h['ema_20']
        regime = ctx.regime_1h

        # Per-token trend filter
        trend_ok = close > ema_20
        entry_mask = entry_mask & trend_ok

        # Regime filter
        regime_ok = (regime != CRISIS) & (regime != DOWNTREND)
        entry_mask = entry_mask & regime_ok

        entry_mask[:200] = False

        if ctx.liquidity_mask is not None:
            entry_mask = entry_mask & ctx.liquidity_mask

        if not np.any(entry_mask):
            continue

        # Regime-scaled sizing
        regime_size = np.where(regime == UPTREND, 2.5,
                     np.where(regime == RANGE, 1.5,
                     np.where(regime == QUIET, 1.0, 0.5)))

        direction = np.ones(n, dtype=np.int8)

        results[token] = StrategyResult(
            entry_mask=entry_mask,
            direction=direction,
            market_type=MarketType.PERP,
            leverage=LEVERAGE,
            stop_mult=3.0,
            trail_mult=1.5,
            target_mult=999,
            no_stop_bars=24,
            min_hold=12,
            max_hold=504,
            edge=0.35,
            exit_regimes={CRISIS},
            exchange='binance',
            name='s81_sector_rotation',
            trail_schedule=TRAIL_SCHEDULE,
            time_trail_schedule=TIME_TRAIL_SCHEDULE,
            bear_max_hold=12,
            size_multiplier=regime_size,
            cap_multiplier=2.5,
        )

    return results
