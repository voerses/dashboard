"""M10 E5 — state-corruption guard on ``paper_state.load()`` (AC #24).

AC #24 requires ``paper_state.load()`` to validate three invariants at
load time:

    1. ``schema_version == 3`` — otherwise raise
       ``StateSchemaMismatchError`` with a message pointing to the
       ``.v1.bak`` restore procedure.
    2. Top-level checksum over ``active_positions[]`` + ``open_orders[]``
       matches the in-file checksum — otherwise raise
       ``StateCorruptionError``.
    3. Every ``active_position.position_id`` appears at most once —
       otherwise raise ``ValueError`` (duplicate position_id).

MUST FAIL TODAY (RED):
    * ``StateSchemaMismatchError`` / ``StateCorruptionError`` classes
      do not exist in ``v5.paper_state`` — ImportError.
    * Current ``paper_state.load`` silently migrates v1/v2 payloads and
      does not compute a checksum or reject duplicates.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compute_checksum(positions: list[dict], orders: list[dict]) -> str:
    """SHA-256 over the canonical JSON encoding of positions+orders."""
    blob = json.dumps(
        {"active_positions": positions, "open_orders": orders},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(blob).hexdigest()


def _base_position(position_id: str, margin_usd: float = 10_000.0) -> dict:
    return {
        "position_id": position_id,
        "token": "BTC",
        "strategy_id": "s524m",
        "leg": "primary",
        "entry_bar": 42,
        "entry_price": 68_000.0,
        "direction": 1,
        "quantity": 0.5,
        "margin_usd": margin_usd,
        "leverage": 1.7,
        "is_perp": True,
        "cumulative_funding": 0.0,
    }


def _base_order(order_id: str) -> dict:
    return {
        "order_id": order_id,
        "token": "BTC",
        "strategy_id": "s524m",
        "order_type": "limit",
        "direction": 1,
        "limit_price": 67_500.0,
        "size": 0.5,
        "created_bar": 42,
    }


def _write_valid_v3(path: Path, positions: list[dict], orders: list[dict]) -> None:
    payload = {
        "schema_version": 3,
        "active_positions": positions,
        "open_orders": orders,
        "checksum": _compute_checksum(positions, orders),
    }
    path.write_text(json.dumps(payload, indent=2))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStateSchemaMismatch:
    """AC #24 — schema_version != 3 raises StateSchemaMismatchError."""

    def test_schema_version_2_raises_with_v1_bak_hint(
        self, tmp_path: Path
    ) -> None:
        from v5.paper_state import StateSchemaMismatchError, load

        path = tmp_path / "state.json"
        positions = [_base_position("BTC:s524m:1:primary")]
        payload = {
            "schema_version": 2,  # <-- wrong
            "active_positions": positions,
            "open_orders": [],
            "checksum": _compute_checksum(positions, []),
        }
        path.write_text(json.dumps(payload, indent=2))

        with pytest.raises(StateSchemaMismatchError) as excinfo:
            load(str(path))
        assert ".v1.bak" in str(excinfo.value), (
            "StateSchemaMismatchError message must mention `.v1.bak` "
            "restore procedure so the operator knows how to recover."
        )


class TestStateChecksumMismatch:
    """AC #24 — mutated active_position bytes raise StateCorruptionError."""

    def test_mutated_margin_without_checksum_update_raises(
        self, tmp_path: Path
    ) -> None:
        from v5.paper_state import StateCorruptionError, load

        path = tmp_path / "state.json"
        positions = [_base_position("BTC:s524m:1:primary", margin_usd=10_000.0)]
        _write_valid_v3(path, positions, [])

        # Load and mutate a byte inside active_positions[0].margin_usd,
        # but DO NOT update the top-level checksum — loader must detect it.
        raw = json.loads(path.read_text())
        raw["active_positions"][0]["margin_usd"] = 99_999.99
        path.write_text(json.dumps(raw, indent=2))

        with pytest.raises(StateCorruptionError):
            load(str(path))


class TestDuplicatePositionId:
    """AC #24 — duplicate active_position.position_id raises ValueError."""

    def test_duplicate_position_id_raises_value_error(
        self, tmp_path: Path
    ) -> None:
        from v5.paper_state import load

        path = tmp_path / "state.json"
        # Two entries share the same position_id — duplicate.
        positions = [
            _base_position("BTC:s30:1:primary"),
            _base_position("BTC:s30:1:primary"),
        ]
        _write_valid_v3(path, positions, [])

        with pytest.raises(ValueError) as excinfo:
            load(str(path))
        msg = str(excinfo.value).lower()
        assert "duplicate" in msg and "position_id" in msg, (
            "ValueError message must mention 'duplicate' + 'position_id' "
            f"for operator actionability; got: {excinfo.value!r}"
        )


class TestDuplicateOrderId:
    """AC #24 tightening (audit 2026-04-20) — duplicate
    ``open_orders[].order_id`` raises ValueError. Brief mandates
    uniqueness for BOTH position_id AND order_id; the position_id
    test alone was insufficient coverage."""

    def test_duplicate_order_id_raises_value_error(
        self, tmp_path: Path
    ) -> None:
        from v5.paper_state import load

        path = tmp_path / "state.json"
        positions = [_base_position("BTC:s524m:1:primary")]
        orders = [
            _base_order("ORD-001"),
            _base_order("ORD-001"),  # duplicate
        ]
        _write_valid_v3(path, positions, orders)

        with pytest.raises(ValueError) as excinfo:
            load(str(path))
        msg = str(excinfo.value).lower()
        assert "duplicate" in msg and "order_id" in msg, (
            "ValueError message must mention 'duplicate' + 'order_id' "
            f"for operator actionability; got: {excinfo.value!r}"
        )


class TestValidV3LoadsSuccessfully:
    """AC #24 anchor — a fully-valid v3 payload loads without raising."""

    def test_valid_v3_payload_loads_clean(self, tmp_path: Path) -> None:
        from v5.paper_state import load

        path = tmp_path / "state.json"
        positions = [
            _base_position("BTC:s524m:1:primary"),
            _base_position("ETH:s524m:2:primary"),
        ]
        _write_valid_v3(path, positions, [])

        # Must NOT raise any of the AC #24 errors.
        state = load(str(path))
        assert state is not None
