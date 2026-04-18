"""M4 — BarSpec dataclass: canonical set, hashability, interning.

Covers:
  - AC22 T-B18 / AC28: BarSpec is frozen+slots; explicit __hash__; default
    normalization returns interned BarSpec.from_minutes(60).
  - Canonical resolution set enforcement (AC12): {1, 3, 5, 10, 15, 30, 60,
    120, 240, 360, 480, 720, 1440} accepted; others raise ValueError.

All tests MUST FAIL today — v5.bar_spec does not exist. Import errors
are valid RED states per Phase 3.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


CANONICAL_MINUTES = (1, 3, 5, 10, 15, 30, 60, 120, 240, 360, 480, 720, 1440)
NON_CANONICAL_MINUTES = (2, 4, 7, 45, 90, 180, 600, 2880)


class TestBarSpecCanonicalSet:
    """AC22 T-B18: canonical resolution set enforcement."""

    @pytest.mark.parametrize("n", CANONICAL_MINUTES)
    def test_canonical_minutes_accepted(self, n):
        """AC22 T-B18: all 13 canonical resolutions construct successfully."""
        from v5.bar_spec import BarSpec
        spec = BarSpec.from_minutes(n)
        assert spec.resolution_minutes == n

    @pytest.mark.parametrize("n", NON_CANONICAL_MINUTES)
    def test_non_canonical_minutes_raises(self, n):
        """AC22 T-B18: non-canonical resolution raises ValueError."""
        from v5.bar_spec import BarSpec
        with pytest.raises(ValueError):
            BarSpec.from_minutes(n)


class TestBarSpecHashabilityAndEquality:
    """AC22 T-B18: hashability, equality, frozen+slots invariants."""

    def test_equal_specs_equal(self):
        """AC22: BarSpec.from_minutes(60) == BarSpec.from_minutes(60)."""
        from v5.bar_spec import BarSpec
        assert BarSpec.from_minutes(60) == BarSpec.from_minutes(60)

    def test_hash_stable_across_runs(self):
        """AC22: hash stable across equivalent constructions."""
        from v5.bar_spec import BarSpec
        a = BarSpec.from_minutes(60)
        b = BarSpec.from_minutes(60)
        assert hash(a) == hash(b)

    def test_different_specs_different_hash(self):
        """AC22: distinct resolutions produce distinct hashes (in practice)."""
        from v5.bar_spec import BarSpec
        assert hash(BarSpec.from_minutes(60)) != hash(BarSpec.from_minutes(1))

    def test_usable_as_dict_key(self):
        """AC22: dict-key stability — same spec always maps to same slot."""
        from v5.bar_spec import BarSpec
        d = {BarSpec.from_minutes(60): "hourly"}
        assert d[BarSpec.from_minutes(60)] == "hourly"

    def test_frozen_cannot_mutate(self):
        """AC22: frozen=True — assignment to field raises."""
        from v5.bar_spec import BarSpec
        spec = BarSpec.from_minutes(60)
        with pytest.raises((AttributeError, Exception)):
            spec.resolution_minutes = 30  # type: ignore[misc]

    def test_slots_no_dict(self):
        """AC22: slots=True — no __dict__ on instances."""
        from v5.bar_spec import BarSpec
        spec = BarSpec.from_minutes(60)
        assert not hasattr(spec, "__dict__")


class TestBarSpecInterning:
    """AC28 T-B18: BarSpec.from_minutes(60) returns interned instance (identity)."""

    def test_default_60m_is_interned(self):
        """AC28 T-B18: BarSpec.from_minutes(60) is BarSpec.from_minutes(60)."""
        from v5.bar_spec import BarSpec
        assert BarSpec.from_minutes(60) is BarSpec.from_minutes(60)

    @pytest.mark.parametrize("n", CANONICAL_MINUTES)
    def test_all_canonical_interned(self, n):
        """AC28: interning applies to every canonical resolution."""
        from v5.bar_spec import BarSpec
        assert BarSpec.from_minutes(n) is BarSpec.from_minutes(n)


class TestBarSpecDerivedProperties:
    """AC22: period_ns and label derived properties."""

    def test_period_ns_hourly(self):
        """period_ns = resolution_minutes * 60 * 1e9."""
        from v5.bar_spec import BarSpec
        assert BarSpec.from_minutes(60).period_ns == 60 * 60 * 1_000_000_000

    def test_period_ns_minute(self):
        from v5.bar_spec import BarSpec
        assert BarSpec.from_minutes(1).period_ns == 60 * 1_000_000_000

    def test_label_minute(self):
        """AC20 sidecar-key: 1m label for sub-hour resolutions."""
        from v5.bar_spec import BarSpec
        assert BarSpec.from_minutes(1).label == "1m"

    def test_label_hourly(self):
        """AC20 sidecar-key: 1h for 60m."""
        from v5.bar_spec import BarSpec
        assert BarSpec.from_minutes(60).label == "1h"

    def test_label_daily(self):
        """AC20 sidecar-key: 1d for 1440m."""
        from v5.bar_spec import BarSpec
        assert BarSpec.from_minutes(1440).label == "1d"

    @pytest.mark.parametrize("n,expected", [
        (3, "3m"), (5, "5m"), (10, "10m"), (15, "15m"),
        (30, "30m"), (120, "2h"), (240, "4h"), (360, "6h"),
        (480, "8h"), (720, "12h"),
    ])
    def test_label_intermediate(self, n, expected):
        """AC20: label formatting for all canonical resolutions."""
        from v5.bar_spec import BarSpec
        assert BarSpec.from_minutes(n).label == expected


class TestBarSpecHashFields:
    """AC22: explicit __hash__ over (resolution_minutes, label_side, closed_side, anchor_utc)."""

    def test_hash_tuple_invariant(self):
        """AC22: hash is stable for equal specs and distinguishes different
        specs. Behavioral assertion only — no coupling to the internal
        tuple ordering."""
        from v5.bar_spec import BarSpec
        assert hash(BarSpec.from_minutes(60)) == hash(BarSpec.from_minutes(60))
        assert hash(BarSpec.from_minutes(60)) != hash(BarSpec.from_minutes(30))
