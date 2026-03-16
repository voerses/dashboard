"""
s57 Signal-Timed Turbo Carry — s44 basis carry + signal-informed entry timing + turbo sizing

Enhancement over s54 turbo carry:
  1. Uses cross-TF divergence signal to TIME carry entries better
  2. When ret_1_1h_vs_4h z-score < -1.0 AND basis elevated: enter
     (mean-reversion signal says price will rise → long spot benefits)
  3. When rsi_1h_vs_4h z-score < -1.0: additional confirmation
  4. Turbo regime sizing (from s54): 6x in UPTREND, 3x in RANGE/QUIET
  5. Funding boost from s54: 1.5x when funding z-score > 1.5

Key difference from s54: the signal filter should improve entry TIMING,
entering carry when the directional wind is favorable even though the
strategy is delta-neutral. Better timing → shorter hold → less exposure.

Status: EXPERIMENTAL
"""

import numpy as np
import pandas as pd
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, rolling_mean, rolling_std)

from strategies.s30_basis_carry import _fast_rolling_zscore


# Exit ablation (v2/v3): flat 1.5 ATR trail + breakeven beats progressive schedule.
TRAIL_SCHEDULE = None

# Turbo regime sizing (from s54)
REGIME_SIZE = np.array([0.0, 3.0, 6.0, 3.0, 2.0], dtype=np.float64)
# Index:                CRISIS QUIET UPTREND RANGE DOWNTREND


def _zscore_fast(arr, window=72):
    """Rolling z-score clipped to [-3, 3]."""
    s = pd.Series(arr)
    mu = s.rolling(window, min_periods=window).mean()
    sigma = s.rolling(window, min_periods=window).std()
    z = ((s - mu) / sigma.clip(lower=1e-10)).values
    return np.clip(np.nan_to_num(z, nan=0.0), -3.0, 3.0)


def strategy(ctx_spot: StrategyContext, ctx_perp: StrategyContext) -> StrategyResult:
    """Signal-timed turbo carry: basis arb with signal-informed entry timing."""
    n = min(len(ctx_spot.ind_1h['close']), len(ctx_perp.ind_1h['close']))

    spot_close = ctx_spot.ind_1h['close'][:n]
    perp_close = ctx_perp.ind_1h['close'][:n]

    # ── BASIS COMPUTATION (from s30) ─────────────────────────────
    basis = (perp_close - spot_close) / np.where(spot_close > 0, spot_close, 1.0)
    basis_z = _fast_rolling_zscore(basis, 72)

    # ── LAYER 1: REGIME FILTER ───────────────────────────────────
    regime_ok = ctx_spot.regime_1h[:n] != CRISIS

    # ── LAYER 2: BASIS PERSISTENCE ───────────────────────────────
    basis_persistent = basis > 0.001

    # ── LAYER 3: CORE BASIS SIGNAL ───────────────────────────────
    basis_elevated = basis_z > 1.5

    # ── LAYER 4: VOLUME ──────────────────────────────────────────
    spot_vol_ratio = ctx_spot.ind_1h['vol_ratio'][:n]
    vol_ok = spot_vol_ratio > 0.5

    # ── LAYER 5: SIGNAL-INFORMED TIMING ──────────────────────────
    # Cross-TF divergence: when 1h returns are LOW relative to 4h,
    # mean-reversion says price will rise → favorable for long spot leg.
    # This is an OPTIONAL boost, not a hard filter — carry works without it.
    ret_1h = ctx_spot.ind_1h.get('ret_1')
    ret_4h = ctx_spot.ind_4h.get('ret_1')

    signal_favorable = np.ones(n, dtype=bool)  # default: always OK
    if ret_1h is not None and ret_4h is not None:
        # Align 4h to 1h (shift by 1 to prevent look-ahead: the 4h
        # candle close isn't known until hour 4, so use the previous 4h bar)
        aligned_ret_4h = pd.Series(
            ret_4h, index=ctx_spot.idx_4h
        ).shift(1).reindex(ctx_spot.idx_1h[:n]).ffill().values.copy()

        z1 = _zscore_fast(ret_1h[:n])
        z4 = _zscore_fast(aligned_ret_4h)
        cross_tf = z1 - z4

        # Enter when cross-TF is low (mean-reversion UP expected)
        # OR when basis is very elevated (>2.0 z) regardless of signal
        signal_favorable = (cross_tf < 0.5) | (basis_z > 2.0)

    # ── COMPOSE ENTRY ────────────────────────────────────────────
    entry = regime_ok & basis_persistent & basis_elevated & vol_ok & signal_favorable
    entry[:200] = False

    # ── SIZING ───────────────────────────────────────────────────
    regime = ctx_spot.regime_1h[:n]
    size_mult = REGIME_SIZE[np.clip(regime, 0, 4)]

    # ADX confidence scaling
    adx = ctx_spot.ind_1h['adx'][:n]
    adx_scale = np.where(adx > 30, 1.5, np.where(adx > 20, 1.0, 0.7))
    size_mult = size_mult * adx_scale

    # Funding rate boost (from s54): high funding = extra carry income
    funding = ctx_perp.funding_1h
    if funding is not None:
        fund_abs = np.abs(funding[:n])
        fund_ma = rolling_mean(fund_abs, 72)
        fund_std = rolling_std(fund_abs, 72)
        fund_std_safe = np.maximum(fund_std, 1e-10)
        fund_z = (fund_abs - fund_ma) / fund_std_safe
        funding_boost = np.where(fund_z > 1.5, 1.5, 1.0)
        size_mult = size_mult * funding_boost

    # Signal quality boost: when cross-TF is strongly favorable, size up
    if ret_1h is not None and ret_4h is not None:
        signal_boost = np.where(cross_tf < -1.0, 1.3, 1.0)
        size_mult = size_mult * signal_boost

    # Cap at 10.0
    size_mult = np.minimum(size_mult, 10.0)

    return StrategyResult(
        # Primary: long spot
        entry_mask=entry,
        direction=np.ones(n, dtype=np.int8),
        market_type=MarketType.COMBINED,

        # Secondary: short perp
        secondary_entry_mask=entry,
        secondary_direction=-np.ones(n, dtype=np.int8),
        secondary_market_type=MarketType.PERP,
        secondary_leverage=1.0,

        capital_split=0.5,

        # Primary trade management (spot long)
        stop_mult=4.0,
        trail_mult=1.5,
        target_mult=999,
        no_stop_bars=48,
        min_hold=24,
        max_hold=504,
        edge=0.30,

        # Secondary trade management (perp short)
        secondary_stop_mult=3.5,
        secondary_trail_mult=1.5,  # exit ablation: flat 1.5 ATR trail
        secondary_target_mult=999,
        secondary_no_stop_bars=48,
        secondary_min_hold=24,
        secondary_max_hold=504,
        secondary_edge=0.30,

        exit_regimes={CRISIS},
        exchange='binance',
        name='s57_signal_timed_turbo_carry',
        trail_schedule=TRAIL_SCHEDULE,
        size_multiplier=size_mult,
        cap_multiplier=15.0,  # carry is safe, push sizing hard
    )
