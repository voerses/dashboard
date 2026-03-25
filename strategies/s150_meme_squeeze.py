"""
s150 Meme/Small-Cap Squeeze & Dump Predictor — V4 Portfolio Strategy (Class B)

V2: Fixed entry thresholds + wider stops for meme volatility.

HYPOTHESIS: Meme/small-cap tokens have extreme funding swings from retail crowding.
- Deeply negative funding = shorts crowded → squeeze coming → go long
- Extremely positive funding + price surge = longs crowded → dump coming → short 1x

Key design:
- Auto-selects volatile tokens by funding dispersion (no hardcoded token list)
- EMA for price trend (reacts faster than SMA on meme tokens)
- Wide stops (5 ATR) — meme tokens are volatile, need room to breathe
- No stop initially (12h grace) then trailing kicks in
- 1.5x longs, 1x shorts (asymmetric — squeeze upside > dump downside)
- Funding z-score entry (relative to each token's own volatility)

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
FUNDING_ZSCORE_WINDOW = 168   # 7d window for funding z-score
PRICE_EMA_SPAN = 48           # 48h EMA
SURGE_WINDOW = 168            # 7d return
ATR_WINDOW = 48               # Use 48h ATR for stop calculation

# Entry: use z-score relative to each token's own funding vol
LONG_FUND_Z = -2.0            # Funding z < -2 → shorts very crowded
SHORT_FUND_Z = 2.0            # Funding z > +2 → longs very crowded
SHORT_SURGE_MIN = 0.20        # Price must have surged >20% in 7d for short
MIN_ABS_FUNDING = 0.0002      # Minimum absolute funding (skip noise)

# Risk
LONG_LEVERAGE = 1.5
SHORT_LEVERAGE = 1.0
TRAIL_MULT = 5.0              # Wide: 5 ATR trail (memes need room)
STOP_MULT = 6.0               # Wide: 6 ATR stop 
MAX_HOLD = 120                # 5 days max (meme moves are fast but not instant)
NO_STOP_BARS = 12             # 12h grace period before stops activate

MIN_ADV_USD = 5_000_000
MIN_FUNDING_STD = 0.0002
WARMUP = 250


def strategy(contexts: dict) -> dict:
    """Meme/small-cap squeeze and dump prediction with z-score entries."""
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
        
        if n < WARMUP + FUNDING_ZSCORE_WINDOW:
            continue

        funding = ctx.funding_1h
        if funding is None:
            continue

        # Signals
        fund_avg = rolling_mean(funding, FUNDING_WINDOW)
        fund_z = np.full(n, 0.0, dtype=np.float64)
        fund_mu = rolling_mean(fund_avg, FUNDING_ZSCORE_WINDOW)
        fund_sigma = rolling_std(fund_avg, FUNDING_ZSCORE_WINDOW)
        valid = (fund_sigma > 1e-10) & ~np.isnan(fund_mu) & ~np.isnan(fund_avg)
        fund_z[valid] = (fund_avg[valid] - fund_mu[valid]) / fund_sigma[valid]
        
        price_ema = ema(close, PRICE_EMA_SPAN)
        
        # 7-day return (vectorized)
        surge_ret = np.full(n, np.nan, dtype=np.float64)
        surge_ret[SURGE_WINDOW:] = (close[SURGE_WINDOW:] / close[:-SURGE_WINDOW]) - 1.0
        
        # Funding dispersion check
        recent_funding = funding[-720:] if n > 720 else funding
        valid_f = recent_funding[~np.isnan(recent_funding)]
        if len(valid_f) < 50 or np.std(valid_f) < MIN_FUNDING_STD:
            continue
        
        # ── Build entry masks (vectorized) ──────────────────────────
        has_data = ~np.isnan(fund_avg) & ~np.isnan(price_ema) & ~np.isnan(atr) & (atr > 0)
        after_warmup = np.zeros(n, dtype=bool)
        after_warmup[WARMUP:] = True
        base_valid = has_data & after_warmup
        
        # ADV filter
        adv_ok = np.ones(n, dtype=bool)
        if ctx.rolling_adv is not None:
            adv_len = min(len(ctx.rolling_adv), n)
            adv_ok[:adv_len] = ctx.rolling_adv[:adv_len] >= MIN_ADV_USD
        
        base_valid = base_valid & adv_ok
        
        # LONG: Funding z < -2 AND |funding| significant AND price above EMA*0.95
        long_mask = (base_valid 
                     & (fund_z < LONG_FUND_Z) 
                     & (np.abs(fund_avg) > MIN_ABS_FUNDING)
                     & (close > price_ema * 0.95))
        
        # SHORT: Funding z > +2 AND |funding| significant AND price surged >20%
        short_mask = (base_valid 
                      & (fund_z > SHORT_FUND_Z) 
                      & (np.abs(fund_avg) > MIN_ABS_FUNDING)
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
            name='s150_meme_squeeze',
            breakeven_atr=0.0,
        )

    return results
