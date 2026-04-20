"""M8 AC-Sz4 — Strategy-side sizing helpers.

Pure functions — no engine dependency, no file I/O, no time reads.
Strategies compose these into `SizingRequest(FIXED_FRACTION, fraction=...)`
at `generate()` time; engine sees only the baked scalar.

Public:
  - vol_target_fraction(target_vol_annual, realized_vol, vol_cap=4.0)
  - kelly_fraction(edge, variance, kelly_mult=0.25)   # textbook
  - risk_budget_fraction(risk_usd, stop_distance_bps, equity, leverage)
  - composite_scaled_fraction(base_fraction, composite_score, adv,
                               config=V4_DEFAULTS)   # v4 curve
  - ADVScalingConfig (frozen dataclass) + V4_DEFAULTS instance
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def vol_target_fraction(
    target_vol_annual: float,
    realized_vol: float,
    vol_cap: float = 4.0,
) -> float:
    """Vol-target sizing: fraction = target_vol / realized_vol, capped at vol_cap.

    Bridgewater-style risk-budget primitive. If realized_vol is zero
    (or near-zero), returns `vol_cap` (hit the cap rather than blow up
    on division).

    Examples:
        target=20%, realized=40% → 0.50 (size down)
        target=20%, realized=10% → 2.00 (size up)
        target=20%, realized=1%  → hits 4.0 cap
    """
    if realized_vol <= 1e-12:
        return float(vol_cap)
    raw = float(target_vol_annual) / float(realized_vol)
    return min(raw, float(vol_cap))


def kelly_fraction(
    edge: float,
    variance: float,
    kelly_mult: float = 0.25,
) -> float:
    """Textbook Kelly sizing: fraction = edge / variance × kelly_mult.

    MacLean-Thorp-Ziemba default `kelly_mult=0.25` (fractional Kelly — full
    Kelly never used in practice; variance of outcomes dominates). Returns
    0.0 when variance is zero (not Inf).

    NOTE: this is the **textbook** formula. `composite_scaled_fraction`
    below uses v4's alternative `kelly_mult × edge` formula (no variance)
    which mirrors the v4 s524-family ADV curve for migration.
    """
    if variance <= 1e-12:
        return 0.0
    return (float(edge) / float(variance)) * float(kelly_mult)


def risk_budget_fraction(
    risk_usd: float,
    stop_distance_bps: float,
    equity: float,
    leverage: float = 1.0,
) -> float:
    """Convert "risk $X given stop at Y bps" into fraction-of-equity.

    Math: a position of notional N at leverage L, moving adversely by
    `stop_distance_bps` before stop fires, loses `N × stop_frac = N ×
    bps/10000`. Set that equal to risk_usd: `N = risk_usd / stop_frac`.
    As fraction-of-equity: `fraction = N / (equity × L) =
    risk_usd / (equity × L × stop_frac)`.

    Strategies that want risk-budget sizing compose this into a
    `SizingRequest(FIXED_FRACTION, fraction=risk_budget_fraction(...))`.
    Engine stays dumb about stop semantics — avoids coupling stop
    price into the RELEASE-time clamp pipeline.
    """
    stop_frac = float(stop_distance_bps) / 10_000.0
    denom = float(equity) * float(leverage) * stop_frac
    if denom <= 1e-12:
        return 0.0
    return float(risk_usd) / denom


@dataclass(frozen=True)
class ADVScalingConfig:
    """v4 ADV-scaling curve parameters. Frozen to prevent accidental
    mutation by strategies.

    Defaults mirror the v4 s524-family live-trading parameters for
    optional migration (`V4_DEFAULTS`). Strategies may override for
    research or calibration.
    """

    kelly_mult_floor: float = 0.15
    kelly_mult_range: float = 0.35
    cap_pct_floor: float = 0.02
    cap_pct_range: float = 0.10
    adv_scaling_divisor: float = 5.0


V4_DEFAULTS = ADVScalingConfig()


def composite_scaled_fraction(
    base_fraction: float,
    composite_score: float,
    adv: float,
    config: ADVScalingConfig = V4_DEFAULTS,
) -> float:
    """v4 s524-family ADV-scaling curve (for optional migration).

    Uses v4's `kelly_mult × edge` formula (NOT textbook edge/variance).
    Parameterized via `ADVScalingConfig` so research can sweep.

    Curve shape: scales base_fraction by (a) a kelly-multiplier
    derived from composite_score (interpolated between floor and
    floor+range), then (b) an ADV-dependent cap_pct that interpolates
    the same way. Result is the v4 sizing surface that s524m trained
    against.

    Intentionally lossy vs textbook Kelly — this is a pragmatic curve,
    not a variance-aware estimator. Strategies wanting rigorous sizing
    should use `kelly_fraction` or `vol_target_fraction` instead.
    """
    # Normalize composite score → [0, 1] (saturate at extremes)
    score_norm = max(0.0, min(1.0, abs(float(composite_score))))
    kelly_mult = float(config.kelly_mult_floor) + \
        score_norm * float(config.kelly_mult_range)
    # ADV scaling: larger ADV → larger cap_pct, saturating at
    # cap_pct_floor + cap_pct_range at adv = adv_scaling_divisor × 1e6.
    adv_norm = min(1.0, float(adv) / (float(config.adv_scaling_divisor) * 1e6))
    cap_pct = float(config.cap_pct_floor) + adv_norm * float(config.cap_pct_range)
    # v4 formula: base × kelly_mult × score_norm (edge), then capped at cap_pct
    scaled = float(base_fraction) * kelly_mult * score_norm
    # Safety: clamp at the cap_pct ceiling. cap_pct is a fraction of equity.
    return min(scaled, cap_pct)
