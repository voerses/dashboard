"""
s155 Meme Squeeze — Volume Divergence Variant (v3)

HYPOTHESIS: Volume divergence from funding signals better entry timing.
- Enter LONG when funding deeply negative BUT volume is DECLINING (coiling)
- Enter SHORT when funding deeply positive AND volume is SPIKING (blow-off)

v3 changes from v2:
- Even tighter volume thresholds: long < 0.65 (was 0.75), short > 2.5 (was 2.0)
- Tighter funding z: -2.5 for longs (was -2.0), +2.5 for shorts (was +2.0)
- Added: regime filter — skip CRISIS regime for longs (everything drops in crisis)
- Max hold 72h (from 96h) to further cut funding drag

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

# Volume params — very tight for maximum selectivity
VOL_AVG_WINDOW = 168
VOL_SHORT_WINDOW = 12
LONG_VOL_RATIO_MAX = 0.65     # Volume < 65% of 7d avg = strongly declining
SHORT_VOL_RATIO_MIN = 2.5     # Volume > 250% of 7d avg = strongly spiking

# Entry thresholds — tighter z-scores
LONG_FUND_Z = -2.5
SHORT_FUND_Z = 2.5
SHORT_SURGE_MIN = 0.15
MIN_ABS_FUNDING = 0.0002

# Risk
LONG_LEVERAGE = 1.5
SHORT_LEVERAGE = 1.0
TRAIL_MULT = 5.0
STOP_MULT = 6.0
MAX_HOLD = 72                 # 3 days (aggressive funding drag reduction)
NO_STOP_BARS = 12

MIN_ADV_USD = 5_000_000
MIN_FUNDING_STD = 0.0002
WARMUP = 250


def strategy(contexts: dict) -> dict:
    """Meme squeeze with tight volume divergence confirmation."""
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

        # ── Funding signals ──────────────────────────────────────
        fund_avg = rolling_mean(funding, FUNDING_WINDOW)
        fund_z = np.full(n, 0.0, dtype=np.float64)
        fund_mu = rolling_mean(fund_avg, FUNDING_ZSCORE_WINDOW)
        fund_sigma = rolling_std(fund_avg, FUNDING_ZSCORE_WINDOW)
        valid = (fund_sigma > 1e-10) & ~np.isnan(fund_mu) & ~np.isnan(fund_avg)
        fund_z[valid] = (fund_avg[valid] - fund_mu[valid]) / fund_sigma[valid]

        # ── Volume signals ───────────────────────────────────────
        vol_avg_7d = rolling_mean(volume, VOL_AVG_WINDOW)
        vol_recent = rolling_mean(volume, VOL_SHORT_WINDOW)

        vol_ratio = np.full(n, 1.0, dtype=np.float64)
        valid_vol = (vol_avg_7d > 0) & ~np.isnan(vol_avg_7d) & ~np.isnan(vol_recent)
        vol_ratio[valid_vol] = vol_recent[valid_vol] / vol_avg_7d[valid_vol]

        vol_declining = vol_ratio < LONG_VOL_RATIO_MAX
        vol_spiking = vol_ratio > SHORT_VOL_RATIO_MIN

        # ── Price signals ────────────────────────────────────────
        price_ema = ema(close, PRICE_EMA_SPAN)

        surge_ret = np.full(n, np.nan, dtype=np.float64)
        surge_ret[SURGE_WINDOW:] = (close[SURGE_WINDOW:] / close[:-SURGE_WINDOW]) - 1.0

        # ── Regime filter ────────────────────────────────────────
        regime = ctx.regime_1h
        not_crisis = np.ones(n, dtype=bool)
        if regime is not None:
            regime_len = min(len(regime), n)
            not_crisis[:regime_len] = regime[:regime_len] != CRISIS

        # ── Token filtering ──────────────────────────────────────
        recent_funding = funding[-720:] if n > 720 else funding
        valid_f = recent_funding[~np.isnan(recent_funding)]
        if len(valid_f) < 50 or np.std(valid_f) < MIN_FUNDING_STD:
            continue

        # ── Entry masks ──────────────────────────────────────────
        has_data = (~np.isnan(fund_avg) & ~np.isnan(price_ema) 
                    & ~np.isnan(atr) & (atr > 0)
                    & ~np.isnan(volume) & (volume > 0))
        after_warmup = np.zeros(n, dtype=bool)
        after_warmup[WARMUP:] = True
        base_valid = has_data & after_warmup

        adv_ok = np.ones(n, dtype=bool)
        if ctx.rolling_adv is not None:
            adv_len = min(len(ctx.rolling_adv), n)
            adv_ok[:adv_len] = ctx.rolling_adv[:adv_len] >= MIN_ADV_USD
        base_valid = base_valid & adv_ok

        # LONG: extreme funding + volume declining + not crisis + price ok
        long_mask = (base_valid
                     & (fund_z < LONG_FUND_Z)
                     & (np.abs(fund_avg) > MIN_ABS_FUNDING)
                     & vol_declining
                     & not_crisis
                     & (close > price_ema * 0.95))

        # SHORT: extreme funding + volume spiking + price surged
        short_mask = (base_valid
                      & (fund_z > SHORT_FUND_Z)
                      & (np.abs(fund_avg) > MIN_ABS_FUNDING)
                      & vol_spiking
                      & ~np.isnan(surge_ret)
                      & (surge_ret > SHORT_SURGE_MIN))

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
            name='s155_volume_divergence',
            breakeven_atr=0.0,
        )

    return results
