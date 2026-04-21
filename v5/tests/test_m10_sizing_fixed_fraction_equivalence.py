"""M10 B1 — FIXED_FRACTION clamp-pipeline ≡ legacy KellySizing (AC #13).

Test enforces:
    The M8 clamp pipeline applied to `SizingRequest(intent=FIXED_FRACTION)`
    produces byte-identical notional (float64 bitwise) to the pre-M8
    `_LegacyKellySizing.compute_size()` for 1000 seeded `(equity, adv,
    edge, leverage)` tuples.

This is the **Q3 prereq** for deleting `v5/sizing_legacy.py`: the legacy
path must be mathematically redundant with the clamp pipeline before the
shim can be removed in Phase 4.

MUST FAIL TODAY — the clamp pipeline wiring for FIXED_FRACTION against
the legacy ADV-cap math has not yet been reconciled.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _seeded_tuples(n: int = 1000, seed: int = 42):
    """Produce `n` deterministic `(equity, adv, edge, lev)` tuples.

    Ranges chosen to exercise the binding ADV cap, the equity floor, and
    the edge-minimum branch of `_LegacyKellySizing.compute_size()`.
    """
    rng = np.random.default_rng(seed)
    equities = rng.uniform(10_000.0, 1_000_000.0, n)
    advs = rng.uniform(1_000.0, 10_000_000.0, n)
    # Edge centered near 0.10 threshold so BOTH branches (below / above
    # edge_minimum) are exercised.
    edges = rng.uniform(0.0, 0.30, n)
    levs = rng.choice([1.0, 2.0, 3.0, 5.0, 10.0], size=n)
    return list(zip(equities, advs, edges, levs))


def _clamp_pipeline_fixed_fraction(
    equity: float, adv: float, edge: float, leverage: float,
) -> float:
    """Invoke the M8 clamp pipeline with a FIXED_FRACTION request and
    return the post-clamp notional (USD).

    The Phase-4 implementer chooses the exposure surface (a thin
    helper on `v5.sizing`, a method on `SizingRequest`, etc.). This
    wrapper will likely need to be updated by the implementer to point
    at the agreed-upon entry point — the test contract is the numeric
    equivalence, NOT the helper name."""
    # Import inside the function so test collection isn't blocked if the
    # helper isn't yet exposed (RED today). Phase 4 picks the name.
    from v5.sizing import compute_fixed_fraction_notional  # type: ignore[attr-defined]

    return float(
        compute_fixed_fraction_notional(
            equity=equity,
            adv=adv,
            edge=edge,
            leverage=leverage,
        )
    )


def _legacy_notional(equity: float, adv: float, edge: float, leverage: float) -> float:
    """Inline copy of `_LegacyKellySizing.compute_size()` math.

    Test-dispute 2026-04-21: the B1 test was originally importing
    `_LegacyKellySizing` from `v5/sizing_legacy.py` — but B2 deletes
    that file entirely. Per the cluster-B sequencing (B1 → B2), B1's
    equivalence gate is a one-shot check; after deletion, we preserve
    the legacy math here so this test continues to serve as a
    regression guard on `compute_fixed_fraction_notional`.
    """
    adv_cap_pct = 0.05
    edge_minimum = 0.10
    spot_max_equity_pct = 1.0
    if edge < edge_minimum:
        return 0.0
    pos_usd = adv * adv_cap_pct
    if leverage <= 1.0:
        pos_usd = min(pos_usd, equity * spot_max_equity_pct)
    return max(pos_usd, 0.0)


class TestFixedFractionEquivalence:
    """B1 — 1000 seeded tuples; FIXED_FRACTION pipeline == legacy KellySizing."""

    def test_byte_identical_notionals(self):
        """Every (equity, adv, edge, lev) tuple yields bitwise-equal notional."""
        tuples = _seeded_tuples(1000, seed=42)
        mismatches: list[tuple[int, float, float, float, float, float, float]] = []
        for i, (equity, adv, edge, lev) in enumerate(tuples):
            pipeline_n = _clamp_pipeline_fixed_fraction(equity, adv, edge, lev)
            legacy_n = _legacy_notional(equity, adv, edge, lev)
            # Bitwise float64 equality — no tolerance.
            if pipeline_n != legacy_n:
                # Accept NaN == NaN when both produced NaN.
                if not (math.isnan(pipeline_n) and math.isnan(legacy_n)):
                    mismatches.append(
                        (i, equity, adv, edge, lev, pipeline_n, legacy_n)
                    )
        assert not mismatches, (
            f"FIXED_FRACTION ≢ legacy KellySizing on "
            f"{len(mismatches)}/1000 tuples — first offender: "
            f"{mismatches[0] if mismatches else None}"
        )

    def test_seed_is_deterministic(self):
        """Two calls with the same seed produce the same tuples (guards
        against accidental RNG-state leak across the suite)."""
        a = _seeded_tuples(10, seed=42)
        b = _seeded_tuples(10, seed=42)
        assert a == b
