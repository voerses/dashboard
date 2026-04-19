"""M7 — Fill dataclass with FIX triple (AC-O5).

Covers:
  - AC-O5 — v5.fill.Fill is a frozen, slotted dataclass carrying:
      cl_ord_id: str               (FIX ClOrdID(11))
      venue_order_id: str | None   (FIX OrderID(37))
      exec_id: str | None          (FIX ExecID(17))
      exec_type: ExecType          (new M7 enum)
      transact_time: int           (FIX TransactTime(60), epoch ns)
      last_qty, last_px, cum_qty, leaves_qty: float

All tests MUST FAIL today — v5.fill does not exist and ExecType is unborn.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


REQUIRED_FIELDS = (
    "cl_ord_id",
    "venue_order_id",
    "exec_id",
    "exec_type",
    "transact_time",
    "last_qty",
    "last_px",
    "cum_qty",
    "leaves_qty",
    "avg_px",       # FIX AvgPx(6) — VWAP across partials (FIX reviewer M1)
)


class TestFillImport:
    """AC-O5 — Fill importable from v5.fill."""

    def test_fill_importable(self):
        from v5.fill import Fill  # noqa: F401

    def test_exec_type_importable(self):
        from v5.orders import ExecType  # noqa: F401


class TestFillDataclassShape:
    """AC-O5 — Fill is a frozen + slotted dataclass with the documented fields."""

    def test_fill_has_all_required_fields(self):
        from v5.fill import Fill
        ann = getattr(Fill, "__annotations__", {}) or {}
        for f in REQUIRED_FIELDS:
            assert f in ann, f"AC-O5: Fill.{f} missing from annotations"

    def test_fill_is_frozen(self):
        """AC-O5 — dataclass is frozen (immutability)."""
        import dataclasses
        from v5.fill import Fill
        params = getattr(Fill, "__dataclass_params__", None)
        assert params is not None
        assert params.frozen is True, "AC-O5: Fill must be frozen"

    def test_fill_uses_slots(self):
        """AC-O5 — dataclass uses slots."""
        from v5.fill import Fill
        # PEP 663 slots — __slots__ populated by @dataclass(slots=True)
        assert hasattr(Fill, "__slots__"), "AC-O5: Fill must use slots"

    def test_fill_immutable_rejects_mutation(self):
        import dataclasses
        from v5.fill import Fill
        from v5.orders import ExecType
        f = Fill(
            cl_ord_id="c-1", venue_order_id="v-1", exec_id="e-1",
            exec_type=ExecType.TRADE, transact_time=1_700_000_000_000_000_000,
            last_qty=1.0, last_px=50_000.0, cum_qty=1.0, leaves_qty=0.0,
            avg_px=50_000.0,
        )
        with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
            f.last_qty = 2.0  # type: ignore[misc]


class TestFillFieldTypes:
    """AC-O5 — field types match FIX semantics."""

    def test_cl_ord_id_is_required_str(self):
        """AC-O5 — cl_ord_id is REQUIRED (not Optional)."""
        from v5.fill import Fill
        ann = getattr(Fill, "__annotations__", {})
        hint = str(ann.get("cl_ord_id", ""))
        assert "str" in hint and "None" not in hint, (
            f"AC-O5: cl_ord_id must be 'str' (required, not Optional); got {hint!r}"
        )

    @pytest.mark.parametrize("field", ["venue_order_id", "exec_id"])
    def test_venue_id_and_exec_id_are_optional(self, field):
        """AC-O5 — venue_order_id + exec_id are 'str | None'."""
        from v5.fill import Fill
        ann = getattr(Fill, "__annotations__", {})
        hint = str(ann.get(field, ""))
        assert "None" in hint or "Optional" in hint, (
            f"AC-O5: {field} must be Optional; got {hint!r}"
        )

    def test_transact_time_is_int_epoch_ns(self):
        """AC-O5 — transact_time is int (epoch ns)."""
        from v5.fill import Fill
        ann = getattr(Fill, "__annotations__", {})
        assert "int" in str(ann.get("transact_time", ""))


class TestExecTypeEnum:
    """AC-O5 — ExecType enum covers all 9 FIX-standard ExecType(150) values
    per design §2.4 (FIX reviewer H1 fix — TRADE not FILL/PARTIAL_FILL)."""

    # FIX-standard 9-value set per design §2.4
    EXPECTED_MEMBERS = (
        ("NEW", "0"),
        ("TRADE", "F"),
        ("CANCELED", "4"),
        ("REJECTED", "8"),
        ("TRIGGERED", "L"),
        ("EXPIRED", "C"),
        ("TRADE_CANCEL", "H"),
        ("TRADE_CORRECT", "G"),
        ("ORDER_STATUS", "I"),
    )

    def test_exec_type_has_all_members(self):
        from v5.orders import ExecType
        for name, _ in self.EXPECTED_MEMBERS:
            assert hasattr(ExecType, name), (
                f"AC-O5: ExecType must declare member {name!r} per design §2.4"
            )

    def test_exec_type_values_match_fix_standard(self):
        from v5.orders import ExecType
        for name, expected_value in self.EXPECTED_MEMBERS:
            member = getattr(ExecType, name)
            assert member.value == expected_value, (
                f"AC-O5: ExecType.{name} must be {expected_value!r} per FIX ExecType(150); "
                f"got {member.value!r}"
            )

    def test_exec_type_does_not_have_fill_or_partial_fill(self):
        """Reviewer H1 — design previously had FILL/PARTIAL_FILL which is NOT
        FIX-standard. Use TRADE (ExecType=F) + OrdStatus to distinguish."""
        from v5.orders import ExecType
        assert not hasattr(ExecType, "FILL"), (
            "FIX-standard uses TRADE (F), not FILL — see design §2.4 H1 fix"
        )
        assert not hasattr(ExecType, "PARTIAL_FILL"), (
            "FIX-standard uses TRADE (F), not PARTIAL_FILL — see design §2.4 H1 fix"
        )


class TestFillAccountingInvariant:
    """AC-O5 — FIX accounting invariant: cum_qty + leaves_qty == order_qty.
    Reviewer H7 — real FIX systems use this invariant for reconstruction."""

    def test_cum_plus_leaves_equals_order_qty_after_partial(self):
        """Partial fill: cum_qty=0.5, leaves_qty=0.5, order_qty=1.0."""
        from v5.fill import Fill
        from v5.orders import ExecType
        f = Fill(
            cl_ord_id="c-1", venue_order_id="v-1", exec_id="e-1",
            exec_type=ExecType.TRADE, transact_time=1_700_000_000_000_000_000,
            last_qty=0.5, last_px=50_000.0, cum_qty=0.5, leaves_qty=0.5, avg_px=50_000.0,
        )
        order_qty = f.cum_qty + f.leaves_qty
        assert order_qty == 1.0, (
            f"AC-O5 FIX invariant: cum_qty + leaves_qty must reconstruct order_qty; "
            f"got {f.cum_qty} + {f.leaves_qty} = {order_qty}"
        )

    def test_cum_plus_leaves_equals_order_qty_after_final(self):
        """Final fill: cum_qty=1.0, leaves_qty=0.0, order_qty=1.0."""
        from v5.fill import Fill
        from v5.orders import ExecType
        f = Fill(
            cl_ord_id="c-1", venue_order_id="v-1", exec_id="e-2",
            exec_type=ExecType.TRADE, transact_time=1_700_000_000_000_000_000,
            last_qty=0.5, last_px=50_000.0, cum_qty=1.0, leaves_qty=0.0, avg_px=50_000.0,
        )
        assert f.cum_qty + f.leaves_qty == 1.0
        assert f.leaves_qty == 0.0, "final fill must have leaves_qty=0"

    def test_leaves_qty_non_negative(self):
        """Invariant: leaves_qty >= 0 (no negative outstanding)."""
        from v5.fill import Fill
        from v5.orders import ExecType
        with pytest.raises(ValueError):
            Fill(
                cl_ord_id="c-1", venue_order_id="v-1", exec_id="e-1",
                exec_type=ExecType.TRADE, transact_time=1_700_000_000_000_000_000,
                last_qty=0.5, last_px=50_000.0, cum_qty=1.5, leaves_qty=-0.5, avg_px=50_000.0,
            )


class TestFillAvgPx:
    """FIX reviewer M1 — AvgPx(6) VWAP field.

    Without avg_px, every strategy re-implements sum(last_qty*last_px)/cum_qty
    from partial-fill history. Adding as a required Fill field per FIX standard.
    """

    def test_avg_px_vwap_on_multi_partial(self):
        """Second partial after first: avg = (0.5*50000 + 0.5*50020) / 1.0 = 50010."""
        from v5.fill import Fill
        from v5.orders import ExecType
        f2 = Fill(
            cl_ord_id="c-1", venue_order_id="v-1", exec_id="e-2",
            exec_type=ExecType.TRADE, transact_time=1_700_000_000_000_000_000,
            last_qty=0.5, last_px=50_020.0, cum_qty=1.0, leaves_qty=0.0,
            avg_px=50_010.0,
        )
        assert f2.avg_px == 50_010.0

    def test_avg_px_equals_last_px_on_single_fill(self):
        """Single fill: avg_px == last_px (degenerate VWAP)."""
        from v5.fill import Fill
        from v5.orders import ExecType
        f = Fill(
            cl_ord_id="c-1", venue_order_id="v-1", exec_id="e-1",
            exec_type=ExecType.TRADE, transact_time=1_700_000_000_000_000_000,
            last_qty=1.0, last_px=50_000.0, cum_qty=1.0, leaves_qty=0.0,
            avg_px=50_000.0,
        )
        assert f.avg_px == f.last_px


class TestFillConstruction:
    """AC-O5 — Fill instances construct with all fields and round-trip values."""

    def test_construct_and_read_back(self):
        from v5.fill import Fill
        from v5.orders import ExecType
        f = Fill(
            cl_ord_id="c-abc", venue_order_id="v-123", exec_id="x-999",
            exec_type=ExecType.TRADE, transact_time=1_700_000_000_000_000_000,
            last_qty=0.5, last_px=50_000.0, cum_qty=1.5, leaves_qty=0.5, avg_px=49_990.0,
        )
        assert f.cl_ord_id == "c-abc"
        assert f.venue_order_id == "v-123"
        assert f.exec_id == "x-999"
        assert f.exec_type is ExecType.TRADE
        assert f.transact_time == 1_700_000_000_000_000_000
        assert f.avg_px == 49_990.0
        assert f.last_qty == 0.5
        assert f.last_px == 50_000.0
        assert f.cum_qty == 1.5
        assert f.leaves_qty == 0.5
