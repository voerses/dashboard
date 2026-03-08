"""Generate v3-compatible strategy functions per group."""

import numpy as np
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .catalog_loader import SignalInfo
from .composite_signal import decorrelate_signals, build_composite_signal
from .config import CRISIS, QUIET, UPTREND, RANGE, DOWNTREND, REGIME_NAMES, HORIZON_DEFAULTS


@dataclass
class GroupSignalConfig:
    """Configuration for a group's strategy."""
    group_id: int
    tokens: List[str]
    signals: List[SignalInfo]  # filtered + decorrelated signals for this group
    dominant_horizon: int = 24  # peak horizon of dominant signal

    # Overridable trade parameters
    entry_threshold: float = 2.0
    min_vol_ratio: float = 1.0
    stop_mult: float = 3.0
    trail_mult: float = 2.5
    min_hold: int = 18
    max_hold: int = 168

    # Per-regime IC for sizing (from group's dominant signals)
    regime_ics: Optional[Dict[str, float]] = None


def _determine_dominant_horizon(signals: List[SignalInfo]) -> int:
    """Find the dominant signal horizon for trade management.

    Uses the signal's evaluation horizon (not peak_horizon from decay curves).
    Weights by |IC| so the strongest signal's horizon drives trade params.
    """
    if not signals:
        return 24

    horizon_ic = {}
    for s in signals:
        h = s.horizon
        horizon_ic[h] = horizon_ic.get(h, 0.0) + abs(s.mean_ic)

    return max(horizon_ic, key=horizon_ic.get)


def _compute_regime_ics(signals: List[SignalInfo]) -> Dict[str, float]:
    """Aggregate per-regime ICs across group signals (IC-weighted mean)."""
    regime_ic_sums = {}
    regime_weights = {}

    for s in signals:
        weight = abs(s.mean_ic)
        for regime_name, ic_val in s.per_regime_ic.items():
            if regime_name not in regime_ic_sums:
                regime_ic_sums[regime_name] = 0.0
                regime_weights[regime_name] = 0.0
            regime_ic_sums[regime_name] += abs(ic_val) * weight
            regime_weights[regime_name] += weight

    result = {}
    for regime_name in REGIME_NAMES.values():
        if regime_name in regime_ic_sums and regime_weights[regime_name] > 0:
            result[regime_name] = regime_ic_sums[regime_name] / regime_weights[regime_name]
        else:
            result[regime_name] = 0.0

    return result


def build_group_config(group_id: int,
                        tokens: List[str],
                        signals: List[SignalInfo]) -> GroupSignalConfig:
    """Build GroupSignalConfig from filtered signals."""
    horizon = _determine_dominant_horizon(signals)

    # Get default trade params for this horizon
    # Find closest matching horizon
    available = sorted(HORIZON_DEFAULTS.keys())
    closest = min(available, key=lambda h: abs(h - horizon))
    defaults = HORIZON_DEFAULTS[closest]

    regime_ics = _compute_regime_ics(signals)

    return GroupSignalConfig(
        group_id=group_id,
        tokens=tokens,
        signals=signals,
        dominant_horizon=horizon,
        stop_mult=defaults['stop_mult'],
        trail_mult=defaults['trail_mult'],
        min_hold=defaults['min_hold'],
        max_hold=defaults['max_hold'],
        regime_ics=regime_ics,
    )


def _build_regime_size_multiplier(regime_1h: np.ndarray,
                                    regime_ics: Dict[str, float]) -> np.ndarray:
    """Build per-bar size multiplier from regime-conditional ICs.

    CRISIS = 0.0 always. Other regimes scaled by |ic_regime| / max_regime_ic.
    """
    n = len(regime_1h)
    size_mult = np.ones(n, dtype=np.float64)

    # Build lookup table
    max_regime_ic = max(regime_ics.values()) if regime_ics else 1.0
    if max_regime_ic <= 0:
        max_regime_ic = 1.0

    lookup = np.ones(5, dtype=np.float64)
    lookup[CRISIS] = 0.0  # Always 0 for CRISIS

    for regime_id, regime_name in REGIME_NAMES.items():
        if regime_id == CRISIS:
            continue
        ic = regime_ics.get(regime_name, 0.0)
        lookup[regime_id] = abs(ic) / max_regime_ic

    # Clip regime values and apply lookup
    clipped = np.clip(regime_1h, 0, 4).astype(int)
    size_mult = lookup[clipped]

    return size_mult


def create_group_strategy(group_config: GroupSignalConfig):
    """Create a v3-compatible strategy function for a token group.

    Returns a closure: Callable[[StrategyContext], StrategyResult]
    that can be called with any token's StrategyContext.

    Entry logic (for negative IC / mean reversion):
        |composite z-score| > threshold -> entry
        composite > threshold -> SHORT (high feature -> low returns)
        composite < -threshold -> LONG (low feature -> high returns)
    """
    # Capture config in closure
    signals = group_config.signals
    threshold = group_config.entry_threshold
    min_vol = group_config.min_vol_ratio
    regime_ics = group_config.regime_ics or {}

    def strategy_fn(ctx):
        # Lazy import to avoid circular dependency
        import sys
        import os
        import importlib.util
        _v3_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'v3')
        _engine_path = os.path.join(_v3_dir, 'engine.py')
        if 'v3_engine' in sys.modules:
            _engine = sys.modules['v3_engine']
        else:
            spec = importlib.util.spec_from_file_location('v3_engine', _engine_path)
            _engine = importlib.util.module_from_spec(spec)
            sys.modules['v3_engine'] = _engine
            spec.loader.exec_module(_engine)
        StrategyResult = _engine.StrategyResult
        MarketType = _engine.MarketType

        n = len(ctx.ind_1h['close'])

        # Decorrelate signals for this specific context
        dedup_signals = decorrelate_signals(
            signals, ctx, max_corr=0.7
        )

        if not dedup_signals:
            # No valid signals — return empty result
            return StrategyResult(
                entry_mask=np.zeros(n, dtype=bool),
                direction=np.zeros(n, dtype=np.int8),
                name=f'signal_group_{group_config.group_id}',
            )

        # Build composite signal
        composite, used = build_composite_signal(dedup_signals, ctx)

        # Determine if signals are predominantly negative IC (mean-reversion)
        avg_ic = np.mean([s.mean_ic for s in used])

        # Entry logic
        entry_long = np.zeros(n, dtype=bool)
        entry_short = np.zeros(n, dtype=bool)

        if avg_ic < 0:
            # Negative IC: mean-reversion
            # composite > threshold -> SHORT (high feature -> low returns)
            # composite < -threshold -> LONG (low feature -> high returns)
            entry_short = composite > threshold
            entry_long = composite < -threshold
        else:
            # Positive IC: momentum-following
            # composite > threshold -> LONG
            # composite < -threshold -> SHORT
            entry_long = composite > threshold
            entry_short = composite < -threshold

        # Volume filter
        vol_ratio = ctx.ind_1h.get('vol_ratio')
        if vol_ratio is not None and min_vol > 0:
            vol_mask = vol_ratio > min_vol
            entry_long &= vol_mask
            entry_short &= vol_mask

        # Build entry mask and direction
        entry_mask = entry_long | entry_short
        direction = np.zeros(n, dtype=np.int8)
        direction[entry_long] = 1
        direction[entry_short] = -1

        # Regime-conditional sizing
        size_mult = _build_regime_size_multiplier(ctx.regime_1h, regime_ics)

        # Detect market type from context
        mtype = MarketType.SPOT
        if hasattr(ctx, 'market_type') and ctx.market_type == 'perp':
            mtype = MarketType.PERP

        return StrategyResult(
            entry_mask=entry_mask,
            direction=direction,
            stop_mult=group_config.stop_mult,
            trail_mult=group_config.trail_mult,
            min_hold=group_config.min_hold,
            max_hold=group_config.max_hold,
            exit_regimes={CRISIS},
            name=f'signal_group_{group_config.group_id}',
            size_multiplier=size_mult,
            market_type=mtype,
        )

    return strategy_fn


def export_strategy_file(group_config: GroupSignalConfig,
                          output_path: str):
    """Export a group strategy as a standalone .py file for the strategies/ dir.

    Generates a self-contained strategy module that can be loaded by the v3 engine.
    """
    signals_data = []
    for s in group_config.signals:
        signals_data.append({
            'feature': s.feature,
            'horizon': s.horizon,
            'mean_ic': s.mean_ic,
            'per_regime_ic': s.per_regime_ic,
        })

    regime_ics = group_config.regime_ics or {}

    code = f'''"""
Auto-generated signal group strategy: Group {group_config.group_id}
Tokens: {', '.join(group_config.tokens)}
Dominant horizon: {group_config.dominant_horizon}h

Generated by tools.signal_portfolio.strategy_generator
"""

import numpy as np
from engine import StrategyContext, StrategyResult, CRISIS

# Signal configuration (from signal discovery)
SIGNALS = {repr(signals_data)}

REGIME_ICS = {repr(regime_ics)}

# Trade parameters
ENTRY_THRESHOLD = {group_config.entry_threshold}
MIN_VOL_RATIO = {group_config.min_vol_ratio}
STOP_MULT = {group_config.stop_mult}
TRAIL_MULT = {group_config.trail_mult}
MIN_HOLD = {group_config.min_hold}
MAX_HOLD = {group_config.max_hold}


def strategy(ctx: StrategyContext) -> StrategyResult:
    """Signal-driven group strategy."""
    from tools.signal_portfolio.signal_converter import compute_feature
    from tools.signal_portfolio.signal_converter import _zscore

    n = len(ctx.ind_1h['close'])

    # Build composite signal
    weighted_sum = np.zeros(n)
    total_weight = 0.0

    for sig in SIGNALS:
        arr = compute_feature(ctx, sig['feature'])
        if arr is None:
            continue
        z = _zscore(arr)
        z_clean = np.nan_to_num(z, nan=0.0)
        ic = sig['mean_ic']
        weight = abs(ic)
        sign = np.sign(ic)
        weighted_sum += sign * weight * z_clean
        total_weight += weight

    composite = weighted_sum / total_weight if total_weight > 0 else np.zeros(n)

    # Entry logic (mean-reversion for negative IC)
    avg_ic = np.mean([s['mean_ic'] for s in SIGNALS])
    entry_long = np.zeros(n, dtype=bool)
    entry_short = np.zeros(n, dtype=bool)

    if avg_ic < 0:
        entry_short = composite > ENTRY_THRESHOLD
        entry_long = composite < -ENTRY_THRESHOLD
    else:
        entry_long = composite > ENTRY_THRESHOLD
        entry_short = composite < -ENTRY_THRESHOLD

    vol_ratio = ctx.ind_1h.get('vol_ratio')
    if vol_ratio is not None and MIN_VOL_RATIO > 0:
        vol_mask = vol_ratio > MIN_VOL_RATIO
        entry_long &= vol_mask
        entry_short &= vol_mask

    entry_mask = entry_long | entry_short
    direction = np.zeros(n, dtype=np.int8)
    direction[entry_long] = 1
    direction[entry_short] = -1

    # Regime sizing
    regime_lookup = np.ones(5, dtype=np.float64)
    regime_lookup[0] = 0.0  # CRISIS
    max_ric = max(REGIME_ICS.values()) if REGIME_ICS else 1.0
    if max_ric <= 0:
        max_ric = 1.0
    regime_map = {{'CRISIS': 0, 'QUIET': 1, 'UPTREND': 2, 'RANGE': 3, 'DOWNTREND': 4}}
    for rname, ric in REGIME_ICS.items():
        rid = regime_map.get(rname, -1)
        if rid > 0:
            regime_lookup[rid] = abs(ric) / max_ric
    size_mult = regime_lookup[np.clip(ctx.regime_1h, 0, 4).astype(int)]

    return StrategyResult(
        entry_mask=entry_mask,
        direction=direction,
        stop_mult=STOP_MULT,
        trail_mult=TRAIL_MULT,
        min_hold=MIN_HOLD,
        max_hold=MAX_HOLD,
        exit_regimes={{CRISIS}},
        name='signal_group_{group_config.group_id}',
        size_multiplier=size_mult,
    )
'''

    with open(output_path, 'w') as f:
        f.write(code)
