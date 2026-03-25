"""
s156 Meme Squeeze — Multi-Confirmation Variant (v3)

HYPOTHESIS: Require 3 out of 4 strong confirmation signals before entry.

v3: Replaced the weak "price above 7d low" condition (fired ~95% of the time)
with a stronger "funding acceleration" condition. Now all 4 conditions are
meaningfully selective.

For LONGS (squeeze setup):
  1. Funding z < -2 (shorts crowded)
  2. Volume declining < 85% of 7d avg (coiling before squeeze)  
  3. Funding accelerating negative (RoC < 0, shorts piling in faster)
  4. ATR expanding > 115% of 7d avg (volatility starting)
  + Price above 95% of EMA (base filter, not counted in conditions)

For SHORTS (dump setup):
  1. Funding z > +2 (longs crowded)
  2. Volume spiking > 150% of 7d avg (blow-off top)
  3. Funding accelerating positive (RoC > 0, longs piling in faster)
  4. ATR expanding > 115% of 7d avg (reversal starting)
  + Price surged >15% in 7d (base filter, not counted in conditions)

Require 3/4 of the numbered conditions.

Market: PERP only
Status: EXPERIMENTAL
Parent: s150_meme_squeeze
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
SURGE_WINDOW = 168

# Funding acceleration
ACCEL_FAST = 12
ACCEL_SLOW = 48
ACCEL_ROC_WINDOW = 12

# Volume params
VOL_AVG_WINDOW = 168
VOL_SHORT_WINDOW = 12
LONG_VOL_RATIO_MAX = 0.85
SHORT_VOL_RATIO_MIN = 1.5

# ATR expansion
ATR_AVG_WINDOW = 168
ATR_SHORT_WINDOW = 12
ATR_EXPANSION_MIN = 1.15

# Entry thresholds
LONG_FUND_Z = -2.0
SHORT_FUND_Z = 2.0
SHORT_SURGE_MIN = 0.15
MIN_ABS_FUNDING = 0.0002

# Multi-confirmation threshold
MIN_CONDITIONS = 3

# Risk
LONG_LEVERAGE = 1.5
SHORT_LEVERAGE = 1.0
TRAIL_MULT = 5.0
STOP_MULT = 6.0
MAX_HOLD = 96
NO_STOP_BARS = 12

MIN_ADV_USD = 5_000_000
MIN_FUNDING_STD = 0.0002
WARMUP = 250


def strategy(contexts: dict) -> dict:
    """Meme squeeze with 3/4 strong confirmation conditions."""
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

        if n < WARMUP + FUNDING_ZSCORE_WINDOW:
            continue

        funding = ctx.funding_1h
        if funding is None:
            continue

        # ── Cond 1: Funding z-score extreme ──────────────────────
        fund_avg = rolling_mean(funding, FUNDING_WINDOW)
        fund_z = np.full(n, 0.0, dtype=np.float64)
        fund_mu = rolling_mean(fund_avg, FUNDING_ZSCORE_WINDOW)
        fund_sigma = rolling_std(fund_avg, FUNDING_ZSCORE_WINDOW)
        valid = (fund_sigma > 1e-10) & ~np.isnan(fund_mu) & ~np.isnan(fund_avg)
        fund_z[valid] = (fund_avg[valid] - fund_mu[valid]) / fund_sigma[valid]

        long_c1 = (fund_z < LONG_FUND_Z) & (np.abs(fund_avg) > MIN_ABS_FUNDING)
        short_c1 = (fund_z > SHORT_FUND_Z) & (np.abs(fund_avg) > MIN_ABS_FUNDING)

        # ── Cond 2: Volume divergence ────────────────────────────
        vol_avg_7d = rolling_mean(volume, VOL_AVG_WINDOW)
        vol_recent = rolling_mean(volume, VOL_SHORT_WINDOW)

        vol_ratio = np.full(n, 1.0, dtype=np.float64)
        valid_vol = (vol_avg_7d > 0) & ~np.isnan(vol_avg_7d) & ~np.isnan(vol_recent)
        vol_ratio[valid_vol] = vol_recent[valid_vol] / vol_avg_7d[valid_vol]

        long_c2 = vol_ratio < LONG_VOL_RATIO_MAX
        short_c2 = vol_ratio > SHORT_VOL_RATIO_MIN

        # ── Cond 3: Funding acceleration ─────────────────────────
        fund_fast = ema(funding, ACCEL_FAST)
        fund_slow = ema(funding, ACCEL_SLOW)

        fund_roc = np.full(n, 0.0, dtype=np.float64)
        if n > ACCEL_ROC_WINDOW:
            fund_roc[ACCEL_ROC_WINDOW:] = fund_fast[ACCEL_ROC_WINDOW:] - fund_fast[:-ACCEL_ROC_WINDOW]

        long_c3 = (fund_roc < 0) & (fund_fast < fund_slow)
        short_c3 = (fund_roc > 0) & (fund_fast > fund_slow)

        # ── Cond 4: ATR expanding ────────────────────────────────
        atr_avg_7d = rolling_mean(atr, ATR_AVG_WINDOW)
        atr_recent = rolling_mean(atr, ATR_SHORT_WINDOW)

        atr_ratio = np.full(n, 1.0, dtype=np.float64)
        valid_atr = (atr_avg_7d > 0) & ~np.isnan(atr_avg_7d) & ~np.isnan(atr_recent)
        atr_ratio[valid_atr] = atr_recent[valid_atr] / atr_avg_7d[valid_atr]

        cond4 = atr_ratio > ATR_EXPANSION_MIN

        # ── Count conditions ─────────────────────────────────────
        long_count = (long_c1.astype(np.int8) + long_c2.astype(np.int8)
                      + long_c3.astype(np.int8) + cond4.astype(np.int8))
        short_count = (short_c1.astype(np.int8) + short_c2.astype(np.int8)
                       + short_c3.astype(np.int8) + cond4.astype(np.int8))

        # ── Base filters (not part of condition count) ───────────
        price_ema = ema(close, PRICE_EMA_SPAN)
        long_base = close > price_ema * 0.95

        surge_ret = np.full(n, np.nan, dtype=np.float64)
        surge_ret[SURGE_WINDOW:] = (close[SURGE_WINDOW:] / close[:-SURGE_WINDOW]) - 1.0
        short_base = ~np.isnan(surge_ret) & (surge_ret > SHORT_SURGE_MIN)

        # ── Token filtering ──────────────────────────────────────
        recent_funding = funding[-720:] if n > 720 else funding
        valid_f = recent_funding[~np.isnan(recent_funding)]
        if len(valid_f) < 50 or np.std(valid_f) < MIN_FUNDING_STD:
            continue

        # ── Entry masks ──────────────────────────────────────────
        has_data = (~np.isnan(fund_avg) & ~np.isnan(atr) & (atr > 0)
                    & ~np.isnan(volume) & (volume > 0))
        after_warmup = np.zeros(n, dtype=bool)
        after_warmup[WARMUP:] = True
        base_valid = has_data & after_warmup

        adv_ok = np.ones(n, dtype=bool)
        if ctx.rolling_adv is not None:
            adv_len = min(len(ctx.rolling_adv), n)
            adv_ok[:adv_len] = ctx.rolling_adv[:adv_len] >= MIN_ADV_USD
        base_valid = base_valid & adv_ok

        # 3/4 strong conditions + base filter
        long_mask = base_valid & (long_count >= MIN_CONDITIONS) & long_base
        short_mask = base_valid & (short_count >= MIN_CONDITIONS) & short_base

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
            name='s156_multi_confirm',
            breakeven_atr=0.0,
        )

    return results
