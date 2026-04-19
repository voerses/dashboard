"""M6 — Deferred event types raise clear error (T-D12 / AC-D7).

Covers:
  - AC-D7 T-D12: BookSnapshot / Liquidation / OpenInterest / Ticker / IndexPrice
    are explicitly deferred. Attempts to subscribe to them raise a clear
    "deferred to future milestone" error at engine construction — not silent.

All tests MUST FAIL today — v5.data.engine does not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


# The brief explicitly lists these 5 as deferred from M6.
DEFERRED_EVENT_TYPE_NAMES = (
    "BookSnapshot",
    "Liquidation",
    "OpenInterest",
    "Ticker",
    "IndexPrice",
)


class TestDeferredTypesNotInDataKind:
    """T-D12 — deferred types are not enumerated as DataKind values."""

    @pytest.mark.parametrize("name", DEFERRED_EVENT_TYPE_NAMES)
    def test_deferred_kind_not_in_enum(self, name):
        """Deferred types MUST NOT appear as DataKind members in M6."""
        from v5.data.streams import DataKind
        assert name.upper() not in DataKind.__members__, (
            f"{name} must not be a DataKind member in M6 (deferred per AC-D7)"
        )


class TestDeferredTypesRaiseClearError:
    """T-D12 — attempting to declare a deferred event type raises clearly."""

    @pytest.mark.parametrize("name", DEFERRED_EVENT_TYPE_NAMES)
    def test_deferred_type_import_raises_or_missing(self, name):
        """Deferred types MUST NOT be importable from v5.data.types in M6.
        # NOTE: brief ambiguous at AC-D7; strict interpretation — module does
        # not export the symbol at all.
        """
        import v5.data.types as types_mod
        assert not hasattr(types_mod, name), (
            f"{name} must not be exported from v5.data.types in M6"
        )

    @pytest.mark.parametrize("name", DEFERRED_EVENT_TYPE_NAMES)
    def test_explicit_deferred_construct_raises_clear_error(self, name):
        """T-D12 (brief line 881): attempts to wire a deferred event type raise a
        clear 'deferred to future milestone' error at engine construction — not silent.

        Exact API surface is implementer's choice; the engine exposes a
        `subscribe_deferred(name: str)` hook used by the strategy API as a
        fail-fast check when strategies declare deferred streams. The test
        asserts behavior (clear error mentioning 'deferred') across ALL 5
        deferred types, not just BookSnapshot (reviewer MED-finding).
        """
        from v5.data.engine import DataEngine
        engine = DataEngine()
        with pytest.raises(NotImplementedError) as excinfo:
            engine.subscribe_deferred(name)
        msg = str(excinfo.value).lower()
        assert "defer" in msg, (
            f"{name}: deferred-type error must mention 'deferred'; got {excinfo.value!r}"
        )
        assert name.lower() in msg, (
            f"{name}: error message should name the deferred type; got {excinfo.value!r}"
        )
