"""M10 B5 — `armed_log.jsonl` dual-write DELETED (AC #13).

Test enforces:
    `v5/paper_engine.py` source contains ZERO references to
    `armed_log.jsonl`. `orders_log.jsonl` is the sole event sink.

M4 Task 16b flipped `Order` to the primary storage. The
`armed_log.jsonl` dual-write is M4-era back-compat that must be
dropped in M10 so the operator sees a single canonical event log.

Source-text grep is the ONLY reliable check here because the dual-write
is string-literal (file path) inside method bodies, not a structural
field. A false-positive inside an archived comment is acceptable — the
test's job is to force deletion of the live code path.

MUST FAIL TODAY — `paper_engine.py` still opens `armed_log.jsonl`
(`self._armed_log_path = os.path.join(config.state_dir, "armed_log.jsonl")`
at line 957).
"""
from __future__ import annotations

from pathlib import Path

_PAPER_ENGINE_SRC = (
    Path(__file__).resolve().parent.parent / "paper_engine.py"
)


class TestArmedLogDualWriteDeleted:
    """B5 — `armed_log.jsonl` string absent from `paper_engine.py`."""

    def test_source_has_no_armed_log_reference(self):
        """The substring `armed_log.jsonl` must not appear in paper_engine.py."""
        assert _PAPER_ENGINE_SRC.exists(), (
            f"paper_engine.py expected at {_PAPER_ENGINE_SRC}"
        )
        source = _PAPER_ENGINE_SRC.read_text()
        assert "armed_log.jsonl" not in source, (
            "paper_engine.py still references `armed_log.jsonl` — the "
            "dual-write must be DELETED in M10 (AC #13). "
            "`orders_log.jsonl` is the sole event sink."
        )

    def test_orders_log_still_present(self):
        """Sanity anchor: `orders_log.jsonl` remains the canonical sink."""
        source = _PAPER_ENGINE_SRC.read_text()
        assert "orders_log.jsonl" in source, (
            "orders_log.jsonl must remain as the canonical event sink "
            "after armed_log dual-write is removed."
        )
