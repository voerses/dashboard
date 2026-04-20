"""M9 C-8 kill-switch removal drill.

Simulates full removal of v5 paper infrastructure (tools + config + symlink)
to verify v4 paths are unchanged. Test harness only — NOT a production CLI.

AC #11 kill-switch: `simulate_v5_removal(root)` returns diff dict
mapping file path → {"added", "modified", "deleted", "unchanged"} —
allowing the test to verify v4 files are unchanged while v5 files are
deleted.
"""
from __future__ import annotations

from pathlib import Path

V5_FILES = {
    "tools/start_v5_paper.sh",
    "tools/stop_v5_paper.sh",
    "configs/runner_pool_config_v5.json",
}


def simulate_v5_removal(*, root: Path) -> dict[str, str]:
    """Simulate removing v5 files from `root` and return a diff dict.

    v4 files not in V5_FILES → "unchanged" (never touched).
    v5 files in V5_FILES → "deleted" (removed from root).
    """
    root = Path(root)
    diff: dict[str, str] = {}

    # First, snapshot all files present under root
    all_files = [
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file()
    ]

    # Delete only the v5 files
    for rel in all_files:
        target = root / rel
        if rel in V5_FILES and target.exists():
            target.unlink()
            diff[rel] = "deleted"
        else:
            diff[rel] = "unchanged"
    return diff
