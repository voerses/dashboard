"""M5 — Rename sweep smoke (T-M5-16): native module rename, NO shim.

Under M5's design-over-code meta-rule, the v5/pending_entry.py module is
renamed to v5/orders.py natively — no back-compat import shim. After Phase 4
Task 21 (the rename sweep), importing ``v5.pending_entry`` must raise
ModuleNotFoundError. Importing ``v5.orders`` must succeed and expose the
full canonical API.

All tests MUST FAIL today — the rename has not yet landed.
"""
from __future__ import annotations

import pytest


class TestM4RenameSweepNative:
    """T-M5-16: native rename (no shim) — old module must be GONE."""

    def test_old_pending_entry_module_not_importable(self):
        """No shim: ``import v5.pending_entry`` must raise ModuleNotFoundError
        after M5 native rename. Verifies the design-over-code decision is
        implemented properly (not papered over with a shim)."""
        with pytest.raises(ModuleNotFoundError):
            import v5.pending_entry  # noqa: F401

    def test_new_orders_module_importable(self):
        """New canonical module exports Order + all M5 enums."""
        from v5.orders import (
            Order, Leg, OrderStatus, LegStatus,
            TriggerType, LegFillPolicy, ContingencyType, TimeInForce,
        )
        assert Order is not None
        assert Leg is not None
        assert OrderStatus is not None
        assert LegStatus is not None
        assert TriggerType is not None
        assert LegFillPolicy is not None
        assert ContingencyType is not None
        assert TimeInForce is not None
