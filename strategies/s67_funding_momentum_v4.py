"""
Strategy S67: Funding Rate Momentum — V4 Portfolio Component
=============================================================
Class D (V4 Portfolio Strategy): gate path 0->2->V4-3->V4-4->V4-5->6->7

Hypothesis: When funding rate is accelerating (rate of change of 24h funding avg),
it signals increasing speculative conviction that drives price momentum. Enter in
the direction of funding acceleration during confirmed trends.

Different from s65 (funding carry): s65 FADES funding (enters opposite to collect
carry income). s67 FOLLOWS funding momentum (enters with the crowd when conviction
is building). These are diametrically opposed strategies that form a natural hedge
in the portfolio.

Different from s56 (momentum): s56 uses price returns as entry signal. s67 uses
funding rate acceleration — a different data source that captures POSITIONING changes
rather than price changes. Only 45% temporal overlap with s56.

Edge family: Funding dynamics / positioning momentum
Market: PERP (bidirectional — long in uptrends, short in downtrends)
Target regimes: UPTREND (longs), DOWNTREND (shorts)
Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, rolling_mean)

# Flat trail — exit ablation winner (trail_mult=1.5 across all strategies)
TRAIL_SCHEDULE = None

# Regime sizing — momentum strategies need trending markets
# Index:         CRISIS  QUIET  UPTREND  RANGE  DOWNTREND
REGIME_SIZE = np.array([0.0, 0.5, 1.5, 0.7, 1.5], dtype=np.float64)


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Funding rate momentum — follow accelerating funding during trends."""
    n = len(ctx.ind_1h['close'])
    adx = ctx.ind_1h['adx']
    regime = ctx.regime_1h
    funding = ctx.funding_1h

    # No funding data = no trades (spot context)
    if funding is None:
        return StrategyResult(
            entry_mask=np.zeros(n, dtype=bool),
            direction=np.ones(n, dtype=np.int8),
            market_type=MarketType.PERP,
            name='s67_funding_momentum_v4',
            breakeven_atr=0.5,
        )

    # ── LAYER 1: REGIME FILTER ──────────────────────────────────
    regime_ok = regime != 0  # exclude crisis

    # ── LAYER 2: FUNDING ACCELERATION ───────────────────────────
    # 24h rolling mean of funding rate
    funding_ma_24 = rolling_mean(funding, 24)

    # Acceleration = change in 24h funding avg over the last 24h
    # Using rolling_mean of (funding_ma - lagged_funding_ma) is equivalent
    # to just computing the difference with np.roll
    funding_ma_24_lag = np.empty_like(funding_ma_24)
    funding_ma_24_lag[:24] = 0.0
    funding_ma_24_lag[24:] = funding_ma_24[:-24]
    funding_accel = funding_ma_24 - funding_ma_24_lag

    # Threshold: meaningful acceleration
    accel_threshold = 0.00003  # ~3.6% annualized rate change

    # ── LAYER 3: DIRECTION + TREND CONFIRMATION ─────────────────
    # Long: funding accelerating up + UPTREND regime + trending (ADX > 20)
    long_entry = (funding_accel > accel_threshold) & (regime == 2) & (adx > 20)

    # Short: funding accelerating down + DOWNTREND regime + trending
    short_entry = (funding_accel < -accel_threshold) & (regime == 4) & (adx > 20)

    # ── LAYER 4: LIQUIDITY ──────────────────────────────────────
    liquid = ctx.liquidity_mask

    # ── COMPOSE ENTRY ───────────────────────────────────────────
    entry = (long_entry | short_entry) & regime_ok & liquid
    entry[:200] = False  # Warmup guard (need 24+24=48 bars for accel, plus indicator warmup)

    direction = np.where(long_entry, np.int8(1), np.where(short_entry, np.int8(-1), np.int8(0)))

    # ── REGIME SIZING ───────────────────────────────────────────
    size_mult = REGIME_SIZE[np.clip(regime, 0, 4)]

    # ── ADX STRENGTH SCALING ────────────────────────────────────
    # Stronger trends = more confident momentum = bigger position
    adx_scale = np.where(adx > 35, 1.5, np.where(adx > 25, 1.2, 1.0))
    size_mult = size_mult * adx_scale

    # ── FUNDING ACCEL MAGNITUDE SCALING ─────────────────────────
    # Bigger acceleration = stronger conviction building = bigger position
    accel_mag = np.abs(funding_accel)
    accel_scale = np.where(accel_mag > 0.0001, 1.5,
                  np.where(accel_mag > 0.00006, 1.2, 1.0))
    size_mult = size_mult * accel_scale

    # Cap at 4.0
    size_mult = np.minimum(size_mult, 4.0)

    return StrategyResult(
        entry_mask=entry,
        direction=direction,

        # Trade management — follow momentum with moderate stops
        stop_mult=3.5,        # 3.5x ATR (give trend room to work)
        trail_mult=1.5,       # 1.5x ATR flat trail (exit ablation winner)
        target_mult=999,      # Trail only (let momentum run)
        no_stop_bars=24,      # 24h protection
        min_hold=12,          # Min 12h
        max_hold=168,         # Max 7 days (momentum decays)
        edge=0.35,            # Moderate edge

        exit_regimes={CRISIS},  # Only exit on crisis (shorts need DOWNTREND)

        name='s67_funding_momentum_v4',

        # V4 perp settings
        market_type=MarketType.PERP,
        leverage=1.0,
        exchange='binance',
        size_multiplier=size_mult,
        cap_multiplier=4.0,
        trail_schedule=TRAIL_SCHEDULE,
        breakeven_atr=0.5,
    )
