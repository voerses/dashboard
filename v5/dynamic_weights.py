"""V5 Dynamic Regime-Weighted Strategy Allocation.

Adjusts per-strategy weights based on the current BTC market regime.
Weights are derived empirically from regime-performance heatmaps
(profit factor per strategy per regime).

Two-level system:
  1. REGIME BUDGET: Total capital deployment scales with regime quality
  2. STRATEGY SHARE: Within-regime allocation proportional to relative PF

Usage (paper engine):
    from v5.dynamic_weights import DynamicWeightAllocator

    allocator = DynamicWeightAllocator.from_config(config)
    # Each tick:
    new_weights = allocator.get_weights(current_regime)
    for spec in config.strategies:
        spec.weight = new_weights[spec.strategy_id]
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)

CRISIS, QUIET, UPTREND, RANGE, DOWNTREND = 0, 1, 2, 3, 4
REGIME_NAMES = {0: 'CRISIS', 1: 'QUIET', 2: 'UPTREND', 3: 'RANGE', 4: 'DOWNTREND'}
REGIME_IDS = {'CRISIS': 0, 'QUIET': 1, 'UPTREND': 2, 'RANGE': 3, 'DOWNTREND': 4}


@dataclass
class DynamicWeightAllocator:
    """Computes per-strategy weights conditioned on the current regime.

    Attributes:
        weight_table: {strategy_id: {regime_id: weight}}
        base_weights: {strategy_id: static_weight} — fallback when regime unknown
        smoothing_alpha: EMA smoothing for regime transitions (0=instant, 0.9=slow)
        _current_weights: {strategy_id: current_smoothed_weight}
    """
    weight_table: Dict[str, Dict[int, float]] = field(default_factory=dict)
    base_weights: Dict[str, float] = field(default_factory=dict)
    smoothing_alpha: float = 0.3
    _current_weights: Dict[str, float] = field(default_factory=dict)
    _last_regime: int = -1

    @classmethod
    def from_heatmap(
        cls,
        heatmap: Dict[str, Dict[str, dict]],
        strategies: list[str],
        base_weights: Optional[Dict[str, float]] = None,
        min_trades: int = 15,
        smoothing_alpha: float = 0.3,
    ) -> DynamicWeightAllocator:
        """Derive weights from a regime-performance heatmap.

        heatmap format: {strategy_id: {regime_name: {n_trades, profit_factor, ...}}}
        """
        if base_weights is None:
            base_weights = {s: 1.0 for s in strategies}

        weight_table: Dict[str, Dict[int, float]] = {}

        for regime_id in range(5):
            rname = REGIME_NAMES[regime_id]

            # Collect PFs for strategies with enough trades
            pfs = {}
            for sid in strategies:
                stats = heatmap.get(sid, {}).get(rname, {})
                n = stats.get('n_trades', 0)
                pf = stats.get('profit_factor', 0.0)
                if n >= min_trades and pf > 0:
                    pfs[sid] = pf
                else:
                    pfs[sid] = 0.0

            # Total PF for normalization (excess above 0.5 baseline)
            total_pf = sum(max(pf, 0) for pf in pfs.values())

            for sid in strategies:
                pf = pfs[sid]
                if pf < 1.0:
                    w = 0.0  # Don't trade losing regime
                elif total_pf > 0:
                    # Proportional to PF, scaled so average = base_weight
                    w = (pf / total_pf) * len(strategies) * base_weights.get(sid, 1.0)
                    w = min(w, 2.0 * base_weights.get(sid, 1.0))  # Cap at 2x base
                else:
                    w = 0.0

                weight_table.setdefault(sid, {})[regime_id] = round(w, 4)

        return cls(
            weight_table=weight_table,
            base_weights=base_weights,
            smoothing_alpha=smoothing_alpha,
        )

    @classmethod
    def from_weight_file(
        cls,
        path: str,
        strategies: list[str],
        base_weights: Optional[Dict[str, float]] = None,
        smoothing_alpha: float = 0.3,
    ) -> DynamicWeightAllocator:
        """Load pre-computed weights from a JSON file.

        File format: {strategy_id: {regime_name: weight}}
        """
        if base_weights is None:
            base_weights = {s: 1.0 for s in strategies}

        with open(path) as f:
            data = json.load(f)

        raw_weights = data.get('weights', data)

        weight_table: Dict[str, Dict[int, float]] = {}
        for sid in strategies:
            if sid in raw_weights:
                for rname, w in raw_weights[sid].items():
                    rid = REGIME_IDS.get(rname)
                    if rid is not None:
                        weight_table.setdefault(sid, {})[rid] = float(w)
            else:
                # Strategy not in weight file → use base weight
                for rid in range(5):
                    weight_table.setdefault(sid, {})[rid] = base_weights.get(sid, 1.0)

        return cls(
            weight_table=weight_table,
            base_weights=base_weights,
            smoothing_alpha=smoothing_alpha,
        )

    @classmethod
    def from_config(
        cls,
        config,
        heatmap_path: str = "results/v4/regime_heatmap.json",
        smoothing_alpha: float = 0.3,
    ) -> Optional[DynamicWeightAllocator]:
        """Create allocator from a PaperConfig if dynamic_weights is enabled.

        Returns None if the config doesn't have dynamic weights enabled or
        the heatmap file doesn't exist.
        """
        if not getattr(config, 'dynamic_weights', False):
            return None

        if not os.path.exists(heatmap_path):
            logger.warning("Heatmap not found at %s — dynamic weights disabled", heatmap_path)
            return None

        with open(heatmap_path) as f:
            data = json.load(f)

        strategies = [s.strategy_id for s in config.strategies]
        base_weights = {s.strategy_id: s.weight for s in config.strategies}

        return cls.from_heatmap(
            heatmap=data['heatmap'],
            strategies=strategies,
            base_weights=base_weights,
            smoothing_alpha=smoothing_alpha,
        )

    def get_weights(self, regime: int) -> Dict[str, float]:
        """Get strategy weights for the given regime.

        Applies EMA smoothing on regime transitions to avoid whipsaw.
        Returns {strategy_id: weight}.
        """
        target_weights = {}
        for sid in self.weight_table:
            regime_w = self.weight_table[sid].get(regime)
            if regime_w is None:
                regime_w = self.base_weights.get(sid, 1.0)
            target_weights[sid] = regime_w

        # Apply EMA smoothing on transitions
        if not self._current_weights or self._last_regime == -1:
            # First call — snap to target
            self._current_weights = dict(target_weights)
        elif regime != self._last_regime:
            # Regime changed — blend toward new target
            alpha = self.smoothing_alpha
            for sid in target_weights:
                old = self._current_weights.get(sid, target_weights[sid])
                self._current_weights[sid] = round(
                    old * (1 - alpha) + target_weights[sid] * alpha, 4
                )
        # If regime unchanged, keep current weights (no smoothing needed)

        self._last_regime = regime
        return dict(self._current_weights)

    def get_weights_instant(self, regime: int) -> Dict[str, float]:
        """Get target weights without smoothing (for backtesting)."""
        result = {}
        for sid in self.weight_table:
            w = self.weight_table[sid].get(regime, self.base_weights.get(sid, 1.0))
            result[sid] = w
        return result

    def log_weights(self, regime: int, weights: Dict[str, float]) -> None:
        """Log current regime and weights for dashboard/monitoring."""
        rname = REGIME_NAMES.get(regime, f"UNKNOWN({regime})")
        parts = [f"{sid}={w:.2f}" for sid, w in sorted(weights.items())]
        logger.info("Dynamic weights [%s]: %s", rname, ", ".join(parts))
