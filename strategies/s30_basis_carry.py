"""
s30 Basis Carry — Cash-and-Carry Spot+Perp Arbitrage.

Hypothesis: "Elevated perp-over-spot basis predicts premium convergence.
Longing spot + shorting perp captures basis decay + funding payments
because leveraged demand for perp creates a structural premium."

Gate 0: PASS — new strategy family (basis arb, delta-neutral), score 6/10
Gate 2: PASS — 0% overlap with existing (no combined strategies exist)

Signal stack:
  Primary (spot long):
    Layer 1: Regime — exclude CRISIS only (basis exists in all regimes)
    Layer 2: Trend — none (delta-neutral, trend-agnostic)
    Layer 3: Entry — basis z-score > 1.5 (perp premium elevated)
    Layer 4: Volume — spot vol_ratio > 0.5 (relaxed, arb doesn't need burst)

  Secondary (perp short):
    Entry mirrors primary (simultaneous legs for delta neutrality)
    Direction: always short (selling the premium)

Combined engine test coverage:
  - Simultaneous dual-leg entry
  - Delta-neutral position (long spot + short perp)
  - Funding accumulation on short perp leg
  - Equal capital_split (0.5)
  - Different secondary_* trade params (tighter stops on hedge)

Risk: Basis collapsed post-ETF (Oct 2024). May have few trades in recent data.

Status: EXPERIMENTAL
"""

import numpy as np
from engine import (StrategyContext, StrategyResult, MarketType,
                    CRISIS)


def _fast_rolling_zscore(arr, window):
    """Pure numpy rolling z-score using cumulative sums. O(n)."""
    n = len(arr)
    out = np.zeros(n, dtype=np.float64)
    cs = np.cumsum(arr)
    cs2 = np.cumsum(arr * arr)
    for w in (window,):  # single-iteration loop for scoping
        mean = np.empty(n)
        var = np.empty(n)
        # First 'window' bars: expanding
        mean[:w] = cs[:w] / np.arange(1, w + 1)
        s2 = cs2[:w] / np.arange(1, w + 1)
        var[:w] = s2 - mean[:w] ** 2
        # Remaining bars: true rolling
        if n > w:
            cnt = float(w)
            mean[w:] = (cs[w:] - cs[:n - w]) / cnt
            s2 = (cs2[w:] - cs2[:n - w]) / cnt
            var[w:] = s2 - mean[w:] ** 2
        std = np.sqrt(np.maximum(var, 0))
        valid = std > 1e-10
        out[valid] = (arr[valid] - mean[valid]) / std[valid]
    return np.clip(out, -3.0, 3.0)


def strategy(ctx_spot: StrategyContext, ctx_perp: StrategyContext) -> StrategyResult:
    """Cash-and-carry: long spot + short perp when basis is elevated."""
    n = min(len(ctx_spot.ind_1h['close']), len(ctx_perp.ind_1h['close']))

    spot_close = ctx_spot.ind_1h['close'][:n]
    perp_close = ctx_perp.ind_1h['close'][:n]

    # ── BASIS COMPUTATION ──────────────────────────────────────
    # Basis = (perp - spot) / spot  (positive = perp trades at premium)
    basis = (perp_close - spot_close) / np.where(spot_close > 0, spot_close, 1.0)

    # Rolling z-score of basis (72h = 3 days lookback)
    basis_z = _fast_rolling_zscore(basis, 72)

    # ── LAYER 1: REGIME FILTER ─────────────────────────────────
    # Basis arb works in all regimes except CRISIS (liquidity dries up)
    regime_ok = ctx_spot.regime_1h[:n] != CRISIS

    # ── LAYER 2: BASIS PERSISTENCE ─────────────────────────────
    # Absolute basis must be meaningfully positive (>0.1%)
    basis_persistent = basis > 0.001

    # ── LAYER 3: ENTRY SIGNAL ──────────────────────────────────
    # Enter when basis z-score elevated (perp at premium) — long spot, short perp
    core_signal = basis_z > 1.5

    # ── LAYER 4: VOLUME ────────────────────────────────────────
    spot_vol_ratio = ctx_spot.ind_1h['vol_ratio'][:n]
    vol_ok = spot_vol_ratio > 0.5  # relaxed — arb doesn't need volume burst

    # ── COMPOSE ────────────────────────────────────────────────
    entry = regime_ok & basis_persistent & core_signal & vol_ok
    entry[:200] = False  # warmup guard

    # Both legs enter simultaneously for delta neutrality
    spot_entry = entry
    perp_entry = entry

    return StrategyResult(
        # Primary: long spot
        entry_mask=spot_entry,
        direction=np.ones(n, dtype=np.int8),  # long
        market_type=MarketType.COMBINED,

        # Secondary: short perp (sell the premium, collect funding)
        secondary_entry_mask=perp_entry,
        secondary_direction=-np.ones(n, dtype=np.int8),  # short
        secondary_market_type=MarketType.PERP,
        secondary_leverage=1.0,  # no leverage on arb

        capital_split=0.5,  # equal allocation to each leg

        # Primary trade management (spot long)
        stop_mult=4.0,       # wider stops — delta-neutral, price moves partially cancel
        trail_mult=3.5,
        target_mult=999,
        no_stop_bars=48,     # 48h protection (basis needs time to converge)
        min_hold=24,         # min 24h
        max_hold=504,        # max 21 days (basis should converge by then)
        edge=0.25,           # conservative — arb edge is small but steady

        # Secondary trade management (perp short — tighter)
        secondary_stop_mult=3.5,
        secondary_trail_mult=3.0,
        secondary_target_mult=999,
        secondary_no_stop_bars=48,
        secondary_min_hold=24,
        secondary_max_hold=504,
        secondary_edge=0.25,

        exit_regimes={CRISIS},  # only exit on CRISIS (arb works in trends)
        exchange='binance',
        name='s30_basis_carry',
        breakeven_atr=0.5,
    )
