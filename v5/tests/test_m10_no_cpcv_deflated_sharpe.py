"""M10 B9 — `v5.cpcv.deflated_sharpe` stub DELETED (AC #13, scope audit).

Test enforces:
    `v5.cpcv` module does NOT expose `deflated_sharpe`.

`v5/cpcv.py:57-82` is a DeprecationWarning-raising stub that redirects
callers to `v5.metrics.deflated_sharpe_ratio()`. M10 deletes (or
archives) the stub — the DSR API lives at `v5.metrics`.

MUST FAIL TODAY — `v5.cpcv.deflated_sharpe` is still importable.
"""
from __future__ import annotations

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestCpcvDeflatedSharpeDeleted:
    """B9 — `v5.cpcv.deflated_sharpe` absent."""

    def test_attribute_absent(self):
        """`hasattr(v5.cpcv, "deflated_sharpe")` is False."""
        import v5.cpcv
        assert hasattr(v5.cpcv, "deflated_sharpe") is False, (
            "v5.cpcv.deflated_sharpe (DeprecationWarning stub) must be "
            "DELETED in M10 (AC #13). Canonical API lives at "
            "v5.metrics.deflated_sharpe_ratio()."
        )

    def test_not_in_module_dir(self):
        """Secondary guard: `deflated_sharpe` not in `dir(v5.cpcv)`."""
        import v5.cpcv
        assert "deflated_sharpe" not in dir(v5.cpcv), (
            "deflated_sharpe still exported from v5.cpcv; delete the "
            "stub per AC #13."
        )
