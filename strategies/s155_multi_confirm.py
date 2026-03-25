"""
s155 Multi-Confirmation Squeeze — V4 Portfolio Strategy (Class B)

Improvement on s150: require MULTIPLE confirmations before entry.
s150's problem was too many false positives (fires on non-events).

Confirmation requirements:
1. Funding z-score extreme (same as s150)
2. Volume surge (>2x recent average) — smart money positioning
3. Price breakout from recent range — confirming direction

All 3 must agree. This should dramatically reduce false positives.

Market: PERP only
Status: EXPERIMENTAL (Gate 3)
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_mean, rolling_std, ema)

STRATEGY_TYPE = "portfolio"

# ── Configuration ────────────────────────────────────────────────
FUNDING_WINDOW = 24
FUNDING_ZSCORE_WINDOW = 168
PRICE_EMA_SPAN = 48
VOL_WINDOW = 168            # 7d average volume
VOL_SURGE_MULT = 2.0        # Volume must be 2x recent average
RANGE_WINDOW = 72           # 3d price range for breakout

# Entry thresholds (slightly relaxed since multi-confirm provides safety)
LONG_FUND_Z = -1.8
SHORT_FUND_Z = 1.8
MIN_ABS_FUNDING = 0.0001

# Risk
LONG_LEVERAGE = 1.5
SHORT_LEVERAGE = 1.0
TRAIL_MULT = 4.0            # Tighter than s150 since better entries
STOP_MULT = 5.0
MAX_HOLD = 120
NO_STOP_BARS = 8

MIN_ADV_USD = 5_000_000
MIN_FUNDING_STD = 0.0002
WARMUP = 250


def strategy(contexts: dict) -> dict:
    """Squeeze prediction requiring funding + volume + price confirmation."""
    results = {}

    for token, ctx_pair in contexts.items():
        if isinstance(ctx_pair, tuple):
            ctx_spot, ctx_perp = ctx_pair
            ctx = ctx_perp if ctx_perp is not None else ctx_spot
        else:
            ctx = ctx_pair

        close = ctx.ind_1h['close']
        atr = ctx.ind_1h['atr']
        volume = ctx.ind_1h['volume']
        n = len(close)

        if n < WARMUP + VOL_WINDOW:
            continue

        funding = ctx.funding_1h
        if funding is None:
            continue

        # Funding dispersion check
        recent_funding = funding[-720:] if n > 720 else funding
        valid_f = recent_funding[~np.isnan(recent_funding)]
        if len(valid_f) < 50 or np.std(valid_f) < MIN_FUNDING_STD:
            continue

        # Signal 1: Funding z-score
        fund_avg = rolling_mean(funding, FUNDING_WINDOW)
        fund_mu = rolling_mean(fund_avg, FUNDING_ZSCORE_WINDOW)
        fund_sigma = rolling_std(fund_avg, FUNDING_ZSCORE_WINDOW)
        fund_z = np.full(n, 0.0, dtype=np.float64)
        valid = (fund_sigma > 1e-10) & ~np.isnan(fund_mu) & ~np.isnan(fund_avg)
        fund_z[valid] = (fund_avg[valid] - fund_mu[valid]) / fund_sigma[valid]

        # Signal 2: Volume surge
        vol_avg = rolling_mean(volume, VOL_WINDOW)
        vol_surge = np.full(n, 0.0, dtype=np.float64)
        vol_valid = (vol_avg > 0) & ~np.isnan(vol_avg) & ~np.isnan(volume)
        vol_surge[vol_valid] = volume[vol_valid] / vol_avg[vol_valid]

        # Signal 3: Price breakout from recent range
        range_high = np.full(n, np.nan, dtype=np.float64)
        range_low = np.full(n, np.nan, dtype=np.float64)
        for i in range(RANGE_WINDOW, n):
            range_high[i] = np.max(close[i-RANGE_WINDOW:i])
            range_low[i] = np.min(close[i-RANGE_WINDOW:i])

        price_ema = ema(close, PRICE_EMA_SPAN)

        # ── Build entry masks (vectorized) ──────────────────────────
        has_data = (~np.isnan(fund_avg) & ~np.isnan(price_ema) &
                    ~np.isnan(atr) & (atr > 0) &
                    ~np.isnan(range_high) & ~np.isnan(range_low))
        after_warmup = np.zeros(n, dtype=bool)
        after_warmup[WARMUP:] = True
        base_valid = has_data & after_warmup

        # ADV filter
        adv_ok = np.ones(n, dtype=bool)
        if ctx.rolling_adv is not None:
            adv_len = min(len(ctx.rolling_adv), n)
            adv_ok[:adv_len] = ctx.rolling_adv[:adv_len] >= MIN_ADV_USD

        base_valid = base_valid & adv_ok

        # Volume confirmation
        vol_confirm = vol_surge >= VOL_SURGE_MULT

        # LONG: Funding extreme negative + volume surge + price breaking above range high
        long_mask = (base_valid
                     & (fund_z < LONG_FUND_Z)
                     & (np.abs(fund_avg) > MIN_ABS_FUNDING)
                     & vol_confirm
                     & (close > range_high))  # Breakout above range

        # SHORT: Funding extreme positive + volume surge + price breaking below range low
        short_mask = (base_valid
                      & (fund_z > SHORT_FUND_Z)
                      & (np.abs(fund_avg) > MIN_ABS_FUNDING)
                      & vol_confirm
                      & (close < range_low))   # Breakdown below range

        if not np.any(long_mask) and not np.any(short_mask):
            continue

        # Liquidity + burn mask
        liq_mask = ctx.liquidity_mask if ctx.liquidity_mask is not None else np.ones(n, dtype=bool)
        burn_mask = np.zeros(n, dtype=bool)
        burn_mask[WARMUP:] = True
        filter_mask = liq_mask & burn_mask

        entry_mask = (long_mask | short_mask) & filter_mask
        direction = np.ones(n, dtype=np.int8)
        direction[short_mask & ~long_mask] = -1

        if not np.any(entry_mask):
            continue

        results[token] = StrategyResult(
            entry_mask=entry_mask,
            direction=direction,
            market_type=MarketType.PERP,
            leverage=LONG_LEVERAGE,
            stop_mult=STOP_MULT,
            trail_mult=TRAIL_MULT,
            target_mult=999,
            no_stop_bars=NO_STOP_BARS,
            min_hold=6,
            max_hold=MAX_HOLD,
            edge=0.25,
            exit_regimes=set(),
            exchange='binance',
            name='s155_multi_confirm',
            breakeven_atr=0.0,
        )

    return results
