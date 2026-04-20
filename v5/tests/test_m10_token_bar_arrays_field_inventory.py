"""M10 A1 — TokenBarArrays field-inventory diff (AC #10 Q2 gate).

Produces a documented markdown gap table at
``reviews/field_inventory.md`` comparing
``TokenBarArrays.__dataclass_fields__`` to the raw columns materialized
in ``ctx.data._arrays[tok]`` by ``v5.data.DataEngine`` for one token over
100 bars.

The gap set is ESSENTIAL to AC-S10 bridge wiring: every TokenBarArrays
field that has no direct DataEngine source must be explicitly derived
(ATR, ADV, RSI, z-scores, …) by the bridge — or documented as
strategy-provided scalar. This test locks that mapping BEFORE the
Phase-4 bridge edit so we catch silent drift.

Pass condition (Phase 4): the markdown gap file exists, is non-empty,
and documents every field class (data_engine_sourced / derived /
strategy_provided).

Fail today (Phase 3 RED): ``reviews/field_inventory.md`` does not yet
exist — this test fails at the file-existence assertion.

Reference: brief AC #10 + tasks.md A1 ("Q2 gate — must land BEFORE
bridge edits in Phase 4").
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


FIELD_INVENTORY_MD = _project_root / "reviews" / "field_inventory.md"
TOKEN = "BTC"
BARS = 100


@pytest.fixture(scope="module")
def token_bar_arrays_field_names() -> set[str]:
    """Full set of dataclass fields declared on TokenBarArrays."""
    from v5.signals import TokenBarArrays

    return set(TokenBarArrays.__dataclass_fields__.keys())


@pytest.fixture(scope="module")
def data_engine_array_keys() -> set[str]:
    """Raw per-column keys that DataEngine materializes in
    ``ctx.data._arrays[tok]`` for a single token over ``BARS`` bars.

    Uses ``UniverseContext.build_test`` seeded identically to the
    DataEngine path so the test is hermetic and does not depend on
    on-disk parquet data (live data is gitignored per CLAUDE.md).
    A separate integration test (Phase 4, C-cluster replay builder)
    will swap the build_test stub for a real DataEngine subscription.
    """
    from v5.universe_context import UniverseContext

    ctx = UniverseContext.build_test(
        tokens=[TOKEN], bars=BARS, seed=0, equity=100_000.0
    )
    # DataView._arrays is dict[token -> dict[col -> np.ndarray]]
    tok_cols = ctx.data._arrays.get(TOKEN)
    assert tok_cols is not None, (
        f"ctx.data._arrays has no entry for token {TOKEN!r}; got "
        f"keys={list(ctx.data._arrays.keys())}"
    )
    return set(tok_cols.keys())


class TestTokenBarArraysFieldInventory:
    """AC #10 Q2 gate — document the gap before Phase-4 bridge edits."""

    def test_field_inventory_markdown_exists(self):
        """reviews/field_inventory.md must exist once Phase-4 bridge work
        lands. Today (Phase 3 RED) it does not exist."""
        assert FIELD_INVENTORY_MD.exists(), (
            f"expected {FIELD_INVENTORY_MD} to exist; Phase-4 A1 produces "
            "it as part of bridge-wiring (per design §A1). Today this "
            "fails intentionally so the RED check passes."
        )

    def test_field_inventory_markdown_non_empty(self):
        """The gap table must be non-empty (at least a header + one
        row per field that DataEngine does NOT source directly)."""
        assert FIELD_INVENTORY_MD.exists(), (
            f"precondition: {FIELD_INVENTORY_MD} must exist first"
        )
        contents = FIELD_INVENTORY_MD.read_text(encoding="utf-8")
        assert len(contents.strip()) > 0, (
            f"{FIELD_INVENTORY_MD} is empty; Phase-4 must write the "
            "TokenBarArrays vs DataEngine gap table."
        )
        # Markdown table header sanity — defend against a blank
        # "only a title line" stub.
        assert "|" in contents, (
            f"{FIELD_INVENTORY_MD} has no markdown table pipes; expected "
            "a | field | source | classification | table per design §A1."
        )

    def test_field_inventory_documents_classification(
        self, token_bar_arrays_field_names, data_engine_array_keys
    ):
        """Every TokenBarArrays field absent from DataEngine keys must
        appear in the markdown table with a documented classification
        (data_engine_sourced / derived / strategy_provided).

        This prevents silent additions to TokenBarArrays from slipping
        past the bridge wiring review.
        """
        assert FIELD_INVENTORY_MD.exists(), (
            f"precondition: {FIELD_INVENTORY_MD} must exist first"
        )
        contents = FIELD_INVENTORY_MD.read_text(encoding="utf-8")
        gap = token_bar_arrays_field_names - data_engine_array_keys
        # Phase 4 will populate the gap file with a row per gap field.
        # Today both (a) the file is missing AND (b) the gap set is
        # non-empty — so this assertion fails RED.
        assert len(gap) > 0, (
            "expected at least one TokenBarArrays field to be absent from "
            "DataEngine raw columns (ATR / ADV / priority / stop_mult etc. "
            "are strategy-provided or derived); got empty gap — sanity "
            "failure."
        )
        missing_from_md: list[str] = [f for f in sorted(gap) if f not in contents]
        assert not missing_from_md, (
            f"fields present in the TokenBarArrays-vs-DataEngine gap but "
            f"NOT documented in {FIELD_INVENTORY_MD}: {missing_from_md}. "
            "Every gap field must appear in the markdown table with its "
            "source classification."
        )

    def test_field_inventory_declares_every_gap_classification(self):
        """The gap table rows must use one of the three canonical
        source-classification tokens defined by design §A1."""
        assert FIELD_INVENTORY_MD.exists(), (
            f"precondition: {FIELD_INVENTORY_MD} must exist first"
        )
        contents = FIELD_INVENTORY_MD.read_text(encoding="utf-8")
        required_tokens = (
            "data_engine_sourced",
            "derived",
            "strategy_provided",
        )
        missing = [t for t in required_tokens if t not in contents]
        assert not missing, (
            f"{FIELD_INVENTORY_MD} missing classification token(s): "
            f"{missing}. Design §A1 requires each gap row to use one of "
            f"{required_tokens}."
        )
