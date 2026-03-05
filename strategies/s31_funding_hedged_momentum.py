"""
s31 Funding-Hedged Momentum — Momentum with Dynamic Perp Hedge.

Hypothesis: "Momentum (ret_1 > 0.03) predicts continued direction on spot.
Adding a short perp hedge when funding is extreme (crowded longs) reduces
drawdowns because extreme funding precedes liquidation cascades."

Gate 0: PASS — combines proven alpha (momentum) with novel risk management, score 7/10
Gate 2: PASS — no existing combined strategy; different from s11 (spot-only) and s28 (perp-only)

Signal stack:
  Primary (spot long — momentum):
    Layer 1: Regime — exclude CRISIS, DOWNTREND
    Layer 2: Trend — close > EMA20, ADX > 20
    Layer 3: Entry — ret_1 > 0.03 (momentum burst, proven in s11)
    Layer 4: Volume — vol_ratio > 1.0

  Secondary (perp short — dynamic hedge):
    Layer 1: Regime — exclude CRISIS only (hedge useful in all others)
    Layer 2: Funding extreme — rolling funding z-score > 2.0
    Layer 3: Entry — funding elevated + some price weakness signal
    Layer 4: Liquidity — perp must be liquid

Combined engine test coverage:
  - Conditional secondary leg (not always active)
  - Different entry conditions per leg
  - Partial hedge (capital_split 0.7/0.3)
  - Different secondary_* trade params (wider stops on hedge)

Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS, DOWNTREND)


def _fast_rolling_zscore(arr, window):
    """Pure numpy rolling z-score using cumulative sums. O(n)."""
    n = len(arr)
    out = np.zeros(n, dtype=np.float64)
    cs = np.cumsum(arr)
    cs2 = np.cumsum(arr * arr)
    cnt = float(window)
    mean = np.empty(n)
    var = np.empty(n)
    # First 'window' bars: expanding
    mean[:window] = cs[:window] / np.arange(1, window + 1)
    s2 = cs2[:window] / np.arange(1, window + 1)
    var[:window] = s2 - mean[:window] ** 2
    # Remaining bars: true rolling
    if n > window:
        mean[window:] = (cs[window:] - cs[:n - window]) / cnt
        s2r = (cs2[window:] - cs2[:n - window]) / cnt
        var[window:] = s2r - mean[window:] ** 2
    std = np.sqrt(np.maximum(var, 0))
    valid = std > 1e-10
    out[valid] = (arr[valid] - mean[valid]) / std[valid]
    return np.clip(out, -3.0, 3.0)


def strategy(ctx_spot: StrategyContext, ctx_perp: StrategyContext) -> StrategyResult:
    """Momentum burst on spot + short perp hedge when funding is extreme."""
    n = min(len(ctx_spot.ind_1h['close']), len(ctx_perp.ind_1h['close']))

    spot_close = ctx_spot.ind_1h['close'][:n]

    # ════════════════════════════════════════════════════════════
    # PRIMARY LEG: Spot Long Momentum (s11-derived)
    # ════════════════════════════════════════════════════════════

    # Layer 1: Regime
    regime_spot = ctx_spot.regime_1h[:n]
    regime_ok_spot = (regime_spot != CRISIS) & (regime_spot != DOWNTREND)

    # Layer 2: Trend alignment
    ema20 = ctx_spot.ind_1h['ema_20'][:n]
    adx = ctx_spot.ind_1h['adx'][:n]
    trend_ok = (spot_close > ema20) & (adx > 20)

    # Layer 3: Momentum burst (relaxed to 2% for more primary trades)
    ret_1 = ctx_spot.ind_1h['ret_1'][:n]
    momentum_signal = ret_1 > 0.02

    # Layer 4: Volume
    vol_ratio = ctx_spot.ind_1h['vol_ratio'][:n]
    vol_ok = vol_ratio > 1.0

    spot_entry = regime_ok_spot & trend_ok & momentum_signal & vol_ok
    spot_entry[:200] = False

    # ════════════════════════════════════════════════════════════
    # SECONDARY LEG: Perp Short Hedge (funding-driven)
    # ════════════════════════════════════════════════════════════

    funding = ctx_perp.funding_1h
    if funding is None:
        funding_arr = np.zeros(n, dtype=np.float64)
    else:
        funding_arr = funding[:n]

    # Layer 1: Regime — hedge useful everywhere except CRISIS
    regime_perp = ctx_perp.regime_1h[:n]
    regime_ok_perp = regime_perp != CRISIS

    # Layer 2: Funding extreme detection
    # Rolling z-score of funding rate (72h lookback)
    funding_z = _fast_rolling_zscore(funding_arr, 72)

    # Funding must be strongly positive (crowded longs paying shorts)
    funding_extreme = funding_z > 2.5

    # Layer 3: Confirmation — RSI elevated (overbought, likely to correct)
    rsi = ctx_perp.ind_1h['rsi'][:n]
    overbought = rsi > 70

    # Layer 4: Liquidity
    perp_liquid = ctx_perp.liquidity_mask[:n] if ctx_perp.liquidity_mask is not None else np.ones(n, dtype=bool)

    perp_entry = regime_ok_perp & funding_extreme & overbought & perp_liquid
    perp_entry[:200] = False

    return StrategyResult(
        # Primary: spot long momentum
        entry_mask=spot_entry,
        direction=np.ones(n, dtype=np.int8),
        market_type=MarketType.COMBINED,

        # Secondary: perp short hedge
        secondary_entry_mask=perp_entry,
        secondary_direction=-np.ones(n, dtype=np.int8),
        secondary_market_type=MarketType.PERP,
        secondary_leverage=1.0,

        capital_split=0.8,  # 80% to momentum, 20% to hedge

        # Primary trade management (momentum — s11-like)
        stop_mult=3.0,
        trail_mult=3.0,
        target_mult=999,
        no_stop_bars=24,
        min_hold=18,
        max_hold=720,
        edge=0.40,

        # Secondary trade management (hedge — tighter, faster capital recycling)
        secondary_stop_mult=3.0,
        secondary_trail_mult=2.5,
        secondary_target_mult=4.0,   # take profit at 4x ATR
        secondary_no_stop_bars=6,    # short protection
        secondary_min_hold=6,        # shorter min hold
        secondary_max_hold=120,      # max 5 days (hedge is temporary)
        secondary_edge=0.25,         # lower edge estimate

        exit_regimes={CRISIS, DOWNTREND},
        exchange='binance',
        name='s31_funding_hedged_momentum',
    )
