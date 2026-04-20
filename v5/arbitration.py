"""M9 C-1 — SignalArbitrationPolicy Protocol + 4 impls + apply_arbitration.

Resolves contention when emitted signals exceed available slots.
Within-strategy ranking is strategy-owned via TokenSignal.priority;
this Protocol is the engine's arbiter across strategies
(scope="portfolio") or within a strategy's slot limit (scope="strategy").

Engine-internal; NOT FIX-serializable — priority has no FIX tag analog.
No mainstream open-source backtester ships an explicit analog;
prop-shop-internal pattern made explicit.

Phase ordering (pinned):
  dedup (Phase 3.05) → min_slot_guarantee → arbitration → per-order clamps
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional, Protocol, runtime_checkable

import numpy as np


# ============================================================
# EntryCandidate + SimulationState dataclasses
# ============================================================


@dataclass
class EntryCandidate:
    """Candidate entry going into arbitration. Strategy-emitted."""

    strategy_id: str
    token: str
    priority: float = 0.0
    # Populated by TieredPriority; 0-indexed tier (0=bottom, n-1=top).
    tier: Optional[int] = None
    # M9: optional per-candidate SizingRequest (used by risk components
    # to throttle fraction_of_equity when DrawdownThrottle triggers).
    sizing: Optional[object] = None
    # M9: direction (1=long, -1=short, 0=flat) for net-exposure checks.
    direction: int = 1
    # M9: notional (USD) for gross-exposure checks. Attached by strategies
    # post-sizing; may also be set by tests directly as a dynamic attribute.
    notional_usd: Optional[float] = None


@dataclass
class SimulationState:
    """Minimal state bundle passed to arbitration policy — seeded RNG
    for fairness-preserving policies (RandomShuffle, TieredPriority).

    M9 C-5: additional aggregate-state fields consumed by RiskComponents
    (DrawdownThrottle, MaxGrossExposure, etc.). Narrow read surface —
    components should not peek at individual position data."""

    rng: np.random.Generator = field(
        default_factory=lambda: np.random.default_rng(0)
    )
    # --- M9 C-5 aggregate state fields for risk components ---
    equity: float = 0.0
    peak_equity: float = 0.0
    open_positions_notional_usd: dict = field(default_factory=dict)
    open_orders_count: int = 0
    realized_pnl_today_usd: float = 0.0
    funding_rate_bps: dict = field(default_factory=dict)
    trading_state: str = "ACTIVE"
    per_symbol_strategies: dict = field(default_factory=dict)
    correlation_matrix: dict = field(default_factory=dict)
    close_arrays: dict = field(default_factory=dict)
    bar_idx: int = 0


# ============================================================
# SignalArbitrationPolicy Protocol + 4 impls
# ============================================================


@runtime_checkable
class SignalArbitrationPolicy(Protocol):
    """Resolves contention among entry candidates. Returns a reordered
    list (not indices — returning indices couples policy to caller's
    list ordering). Strategy priority is strategy-owned; this Protocol
    arbitrates across strategies under resource contention.

    NOT FIX-serializable."""

    def rank(
        self,
        candidates: list[EntryCandidate],
        state: SimulationState,
        scope: Literal["portfolio", "strategy"] = "portfolio",
    ) -> list[EntryCandidate]: ...


class RandomShuffle:
    """DEFAULT. Seeded via state.rng. Fairness-first — prevents
    systematic starvation when cross-strategy priorities are
    uncalibrated (e.g. momentum z-scores vs mean-rev normalized
    [0,1]). Ignores priority; emits shuffled order."""

    def rank(
        self,
        candidates: list[EntryCandidate],
        state: SimulationState,
        scope: Literal["portfolio", "strategy"] = "portfolio",
    ) -> list[EntryCandidate]:
        out = list(candidates)
        state.rng.shuffle(out)
        return out


class PriorityDesc:
    """Descending priority; ties broken by (strategy_id, token) lex.
    Opt-in after priority calibration confirmed across strategies."""

    def rank(
        self,
        candidates: list[EntryCandidate],
        state: SimulationState,
        scope: Literal["portfolio", "strategy"] = "portfolio",
    ) -> list[EntryCandidate]:
        def key(c: EntryCandidate):
            return (-(c.priority or 0.0), c.strategy_id, c.token)

        return sorted(candidates, key=key)


class TieredPriority:
    """Rank-space quantile buckets with intra-tier shuffle. Default
    tier_quantiles=[0.33, 0.66] → 3 tiers. Top tier contains highest
    priorities. Tier membership recorded on each EntryCandidate.tier."""

    def __init__(self, tier_quantiles: Optional[list[float]] = None):
        self.tier_quantiles = tier_quantiles or [0.33, 0.66]

    def rank(
        self,
        candidates: list[EntryCandidate],
        state: SimulationState,
        scope: Literal["portfolio", "strategy"] = "portfolio",
    ) -> list[EntryCandidate]:
        if not candidates:
            return []
        # Sort ascending by priority; quantile-split into n_tiers.
        sorted_by_prio = sorted(candidates, key=lambda c: (c.priority or 0.0))
        n = len(sorted_by_prio)
        n_tiers = len(self.tier_quantiles) + 1
        # Compute tier boundaries in rank-space (indices in sorted list).
        # Use round() so boundaries produce symmetric equal-size tiers
        # when n is divisible by tier count (e.g., n=9, quantiles=[0.33,0.66]
        # → boundaries [3, 6] → 3/3/3 split).
        boundaries = [round(q * n) for q in self.tier_quantiles]

        tiered: list[list[EntryCandidate]] = [[] for _ in range(n_tiers)]
        for idx, c in enumerate(sorted_by_prio):
            # Determine tier index: 0 = bottom, n_tiers-1 = top.
            tier = 0
            for b in boundaries:
                if idx >= b:
                    tier += 1
            c.tier = tier
            tiered[tier].append(c)

        # Shuffle within each tier using seeded rng for determinism.
        for tier_bucket in tiered:
            state.rng.shuffle(tier_bucket)

        # Flatten top-to-bottom: top tier first (reversed order).
        out: list[EntryCandidate] = []
        for tier_bucket in reversed(tiered):
            out.extend(tier_bucket)
        return out


class RoundRobin:
    """Weighted round-robin across strategies. Each strategy picks its
    next-best (by priority) in rotation. weights=None means equal rotation."""

    def __init__(self, weights: Optional[dict[str, float]] = None):
        self.weights = weights

    def rank(
        self,
        candidates: list[EntryCandidate],
        state: SimulationState,
        scope: Literal["portfolio", "strategy"] = "portfolio",
    ) -> list[EntryCandidate]:
        if not candidates:
            return []
        # Group by strategy; within each strategy sort by descending priority.
        by_strategy: dict[str, list[EntryCandidate]] = {}
        for c in candidates:
            by_strategy.setdefault(c.strategy_id, []).append(c)
        for sid in by_strategy:
            by_strategy[sid].sort(key=lambda c: -(c.priority or 0.0))

        # Round-robin rotation — strategies in insertion order (stable).
        strategy_ids = list(by_strategy.keys())
        out: list[EntryCandidate] = []
        while any(by_strategy[sid] for sid in strategy_ids):
            for sid in strategy_ids:
                if by_strategy[sid]:
                    out.append(by_strategy[sid].pop(0))
        return out


# ============================================================
# apply_arbitration — top-level pipeline (dedup → guarantee → rank)
# ============================================================


def apply_arbitration(
    candidates: list[EntryCandidate],
    *,
    strategies: list,
    max_portfolio_positions: int,
    policy: SignalArbitrationPolicy,
    state: SimulationState,
) -> list[EntryCandidate]:
    """Top-level arbitration pipeline per AC #3 Phase ordering:

      1. Phase 3.05: dedup by `max_positions_per_symbol` per strategy
      2. Pre-fill min_slot_guarantee slots per strategy (highest-priority
         candidates per strategy, up to effective guarantee =
         min(spec.min_slot_guarantee, spec.max_positions, deduped_count))
      3. Arbitrate remaining candidates for residual slots via policy.rank()
      4. Clamp to max_portfolio_positions

    Returns the admitted candidates (subset of input, reordered).
    """
    # Map strategy_id → spec
    spec_map = {_name_of(s): s for s in strategies}

    # Phase 3.05: dedup
    deduped = _dedup_by_symbol(candidates, spec_map)

    # Bucket by strategy post-dedup
    by_strategy: dict[str, list[EntryCandidate]] = {}
    for c in deduped:
        by_strategy.setdefault(c.strategy_id, []).append(c)

    # Sort each strategy's candidates by desc priority for guarantee picks
    for sid in by_strategy:
        by_strategy[sid].sort(key=lambda c: -(c.priority or 0.0))

    # Phase: guarantee reservation.
    #   - Strategy that emitted NO signals this bar → reserve full guarantee
    #     (slot unused, NOT released to cross-strategy pool — AC #3(h))
    #   - Strategy that emitted signals but post-dedup clipped to < guarantee
    #     → reserve effective = min(guarantee, max_positions, len(avail))
    #     which is the actual admitted count (AC #3(j))
    admitted: list[EntryCandidate] = []
    reserved_slots = 0
    for spec in strategies:
        sid = _name_of(spec)
        g = int(getattr(spec, "min_slot_guarantee", 0) or 0)
        if g <= 0:
            continue
        max_pos = int(getattr(spec, "max_positions", 10**9) or 10**9)
        avail = by_strategy.get(sid, [])
        if not avail:
            # AC #3(h): unemitted strategy reserves full raw guarantee
            reserved_slots += min(g, max_pos)
            continue
        # AC #3(j): emitted but possibly dedup-clipped — reserve effective only
        effective_g = min(g, max_pos, len(avail))
        reserved_slots += effective_g
        for c in avail[:effective_g]:
            admitted.append(c)
        by_strategy[sid] = avail[effective_g:]

    # Residual slot budget after guarantees
    residual_budget = max_portfolio_positions - reserved_slots
    residual_budget = max(0, residual_budget)

    # Arbitrate remaining candidates
    remaining = [c for sid_lst in by_strategy.values() for c in sid_lst]
    if residual_budget > 0 and remaining:
        ranked = policy.rank(remaining, state, scope="portfolio")
        admitted.extend(ranked[:residual_budget])

    return admitted


def _name_of(spec) -> str:
    """StrategySpec identity — accepts either `name` or `strategy_id`."""
    return getattr(spec, "name", None) or getattr(spec, "strategy_id", "")


def _dedup_by_symbol(
    candidates: list[EntryCandidate],
    spec_map: dict,
) -> list[EntryCandidate]:
    """Phase 3.05: within each strategy, cap concurrent candidates per
    symbol by spec.max_positions_per_symbol (default 1). Highest-priority
    wins on duplicates."""
    by_key: dict[tuple[str, str], list[EntryCandidate]] = {}
    for c in candidates:
        by_key.setdefault((c.strategy_id, c.token), []).append(c)

    out: list[EntryCandidate] = []
    for (sid, tok), group in by_key.items():
        spec = spec_map.get(sid)
        cap = int(getattr(spec, "max_positions_per_symbol", 1) or 1) if spec else 1
        # Sort by descending priority and take top `cap`
        group.sort(key=lambda c: -(c.priority or 0.0))
        out.extend(group[:cap])
    return out


# ============================================================
# M9 C-1 / C-10 — ArbitrationLogWriter with 500MB gzip rotation
# ============================================================


class ArbitrationLogWriter:
    """M9 telemetry sink for `arbitration.jsonl`. Rotates at 500MB
    default with gzip compression on rotated segments. 1-year 3-strategy
    × 200-token backtest stays <2GB total under this config.

    Schema per row:
      {bar_idx, strategy_id, token, rank_in, rank_out, tier,
       admitted, displaced_by}
    """

    def __init__(
        self,
        *,
        log_dir,
        base_name: str = "arbitration.jsonl",
        rotate_size_bytes: int = 500 * 1024 * 1024,
    ):
        from pathlib import Path
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.base_name = base_name
        self.rotate_size_bytes = int(rotate_size_bytes)
        self._current_path = self.log_dir / base_name
        self._fh = open(self._current_path, "a", encoding="utf-8")
        self._seq = 0

    def write(self, record: dict) -> None:
        """Append one JSONL row; rotate when current segment > threshold."""
        import json as _json
        self._fh.write(_json.dumps(record) + "\n")
        self._fh.flush()
        # Rotation check — cheap O(1) via tell()
        if self._fh.tell() > self.rotate_size_bytes:
            self._rotate()

    def _rotate(self) -> None:
        """Close current segment, gzip it, start a new segment."""
        import gzip as _gz
        self._fh.close()
        self._seq += 1
        archive_path = self.log_dir / f"{self.base_name}.{self._seq}.gz"
        # Compress current segment -> .gz
        with open(self._current_path, "rb") as src:
            with _gz.open(archive_path, "wb", compresslevel=6) as dst:
                dst.write(src.read())
        # Truncate current segment for next writes
        self._current_path.unlink()
        self._fh = open(self._current_path, "a", encoding="utf-8")

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
