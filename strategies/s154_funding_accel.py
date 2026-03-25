"""
s154 Funding Acceleration Squeeze — V4 Portfolio Strategy (Class B)

Improvement on s150: instead of absolute funding z-score, use RATE OF CHANGE
of funding. When funding is rapidly changing (accelerating), it means new
positioning is being built quickly — this predicts imminent reversal better.

Key improvements over s150:
- Funding acceleration (2nd derivative) instead of level (1st derivative)
- Catches the VELOCITY of crowd positioning, not just the level
- Faster to fire (catches momentum shifts earlier)
- Combined with price confirmation (EMA trend)

Market: PERP only
Status: EXPERIMENTAL (Gate 3)
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND, UPTREND, RANGE, QUIET,
                    rolling_mean, rolling_std, ema)

STRATEGY_TYPE = "portfolio"

FUNDING_WINDOW = 24
ACCEL_WINDOW = 48
ACCEL_Z_WINDOW = 168
PRICE_EMA_SPAN = 48
ATR_WINDOW = 48

LONG_ACCEL_Z = -2.5
SHORT_ACCEL_Z = 2.5
MIN_ABS_FUNDING = 0.0001

LONG_LEVERAGE = 1.5
SHORT_LEVERAGE = 1.0
TRAIL_MULT = 5.0
STOP_MULT = 6.0
MAX_HOLD = 120
NO_STOP_BARS = 12

MIN_ADV_USD = 5_000_000
MIN_FUNDING_STD = 0.0002
WARMUP = 250


def strategy(contexts: dict) -> dict:
    """Squeeze prediction using funding acceleration (2nd derivative)."""
    results = {}

    for token, ctx_pair in contexts.items():
        if isinstance(ctx_pair, tuple):
            ctx_spot, ctx_perp = ctx_pair
            ctx = ctx_perp if ctx_perp is not None else ctx_spot
        else:
            ctx = ctx_pair

        close = ctx.ind_1h['close']
        atr = ctx.ind_1h['atr']
        n = len(close)

        if n < WARMUP + ACCEL_Z_WINDOW + ACCEL_WINDOW:
            continue

        funding = ctx.funding_1h
        if funding is None:
            continue

        recent_funding = funding[-720:] if n > 720 else funding
        valid_f = recent_funding[~np.isnan(recent_funding)]
        if len(valid_f) < 50 or np.std(valid_f) < MIN_FUNDING_STD:
            continue

        fund_avg = rolling_mean(funding, FUNDING_WINDOW)
        fund_avg_slow = rolling_mean(funding, ACCEL_WINDOW)
        accel = fund_avg - fund_avg_slow

        accel_mu = rolling_mean(accel, ACCEL_Z_WINDOW)
        accel_sigma = rolling_std(accel, ACCEL_Z_WINDOW)

        accel_z = np.full(n, 0.0, dtype=np.float64)
        valid = (accel_sigma > 1e-10) & ~np.isnan(accel_mu) & ~np.isnan(accel)
        accel_z[valid] = (accel[valid] - accel_mu[valid]) / accel_sigma[valid]

        price_ema = ema(close, PRICE_EMA_SPAN)

        has_data = ~np.isnan(fund_avg) & ~np.isnan(price_ema) & ~np.isnan(atr) & (atr > 0)
        after_warmup = np.zeros(n, dtype=bool)
        after_warmup[WARMUP:] = True
        base_valid = has_data & after_warmup

        adv_ok = np.ones(n, dtype=bool)
        if ctx.rolling_adv is not None:
            adv_len = min(len(ctx.rolling_adv), n)
            adv_ok[:adv_len] = ctx.rolling_adv[:adv_len] >= MIN_ADV_USD

        base_valid = base_valid & adv_ok

        long_mask = (base_valid
                     & (accel_z < LONG_ACCEL_Z)
                     & (np.abs(fund_avg) > MIN_ABS_FUNDING)
                     & (close > price_ema * 0.95))

        short_mask = (base_valid
                      & (accel_z > SHORT_ACCEL_Z)
                      & (np.abs(fund_avg) > MIN_ABS_FUNDING)
                      & (close > price_ema * 1.05))

        if not np.any(long_mask) and not np.any(short_mask):
            continue

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
            name='s154_funding_accel',
            breakeven_atr=0.0,
        )

    return results
