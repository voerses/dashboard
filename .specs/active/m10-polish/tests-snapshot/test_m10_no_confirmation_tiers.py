"""M10 B8 — `PaperConfig.confirmation_tiers` DELETED (AC #13, scope audit).

Test enforces:
    `PaperConfig` exposes no `confirmation_tiers` field or attribute.

`v5/paper_config.py:39` declares:

    confirmation_tiers: dict = field(default_factory=lambda: ...)  # deprecated

with a matching loader at `v5/paper_config.py:128`. The field is marked
deprecated but still present. M10 deletes it (brief scope-audit bullet).

MUST FAIL TODAY — `confirmation_tiers` is still declared.
"""
from __future__ import annotations

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestConfirmationTiersDeleted:
    """B8 — `PaperConfig.confirmation_tiers` absent."""

    def test_not_in_dataclass_fields(self):
        """`PaperConfig.__dataclass_fields__` must not contain it."""
        from v5.paper_config import PaperConfig
        fields = getattr(PaperConfig, "__dataclass_fields__", None)
        assert fields is not None, (
            "PaperConfig is expected to be a dataclass; __dataclass_fields__ missing."
        )
        assert "confirmation_tiers" not in fields, (
            f"PaperConfig.confirmation_tiers must be DELETED in M10 "
            f"(AC #13 / scope audit). Found fields: {list(fields.keys())}"
        )

    def test_not_an_attribute(self):
        """Defence-in-depth: class-level attribute also absent."""
        from v5.paper_config import PaperConfig
        assert not hasattr(PaperConfig, "confirmation_tiers"), (
            "PaperConfig.confirmation_tiers still accessible as attribute"
        )

    def test_not_accepted_by_loader(self):
        """`load_paper_config` must not read a `confirmation_tiers` key
        from its JSON input — the reader at line 128 must be gone too."""
        from pathlib import Path
        src = Path(__file__).resolve().parent.parent / "paper_config.py"
        text = src.read_text()
        assert "confirmation_tiers" not in text, (
            f"paper_config.py still references `confirmation_tiers` — "
            f"delete both the field and the loader line per AC #13."
        )
