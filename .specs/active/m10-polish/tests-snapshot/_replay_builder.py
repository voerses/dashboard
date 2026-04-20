"""M10 Cluster C0 — shared replay fixture primitive.

``ReplayFixtureBuilder`` synthesizes deterministic :class:`TokenBarArrays`
and a minimal :class:`StrategyContext` stub for scenario tests
(C1-C4, D1, E1, G3). No wall-clock, no on-disk data — everything is
numpy-seeded from ``ScenarioSpec``.

Implementation lives in Phase 4; this is the Phase-3 surface contract
the scenario acceptance tests bind against. Tests must FAIL today (RED)
because the builder does not yet exist in any form the sim loop can
consume.

Surface contract
----------------

``ScenarioSpec`` is a frozen dataclass describing what kind of synthetic
data the builder should produce:

    * ``random_walk_stddev``: per-bar log-return stddev (default 0.001)
    * ``forced_trades``: number of open/close cycles the synthetic
      ``entry_mask`` should stamp into the 1-token path (spaced evenly)
    * ``crash_bar``: optional integer bar index at which a uniform
      ``crash_pct`` (default -0.25 = -25%) price shock is applied to
      EVERY token — triggers simultaneous liquidation scenarios (C4)
    * ``funding_snaps``: optional sequence of ``(bar_idx, rate)`` pairs
      for perp fixtures; the builder writes the rate into
      ``funding_1h[bar_idx]`` and leaves zero elsewhere (C3)

``ReplayFixtureBuilder.build()`` returns
``dict[token, TokenBarArrays]`` — the shape ``simulate_portfolio``'s
``all_signals`` parameter expects.

``ReplayFixtureBuilder.ctx_stub()`` returns a minimal ``StrategyContext``
with ``ctx.data._arrays`` populated — matches the AC-S10 bridge-era
interface so bridge-mode tests can drive the inner loop without a real
UniverseContext.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence, Tuple


@dataclass
class ScenarioSpec:
    random_walk_stddev: float = 0.001
    forced_trades: int = 0
    crash_bar: Optional[int] = None
    crash_pct: float = -0.25
    funding_snaps: Optional[Sequence[Tuple[int, float]]] = None


class ReplayFixtureBuilder:
    """Deterministic TokenBarArrays + StrategyContext synthesizer.

    Constructor contract (Phase 4 implements)::

        __init__(
            self,
            tokens: list[str],
            n_bars: int,
            start_ts_utc: int,   # epoch nanoseconds, must be 00:00:00 UTC aligned
            seed: int,
            scenario: ScenarioSpec,
        )

    Behaviour (Phase 4 implements)::

        build()    -> dict[str, TokenBarArrays]
        ctx_stub() -> StrategyContext  # with ctx.data._arrays populated
    """

    def __init__(
        self,
        tokens,
        n_bars: int,
        start_ts_utc: int,
        seed: int,
        scenario: ScenarioSpec,
    ) -> None:
        raise NotImplementedError(
            "ReplayFixtureBuilder is a Phase-3 acceptance-test contract. "
            "Phase 4 task C0 lands the real implementation."
        )

    def build(self):
        raise NotImplementedError

    def ctx_stub(self):
        raise NotImplementedError
