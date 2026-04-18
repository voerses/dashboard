"""Migration helper for the conviction_score → priority refactor.

Exposes `priority_from_conviction(score)` used when one-shot-migrating strategy
files from emitting `conviction_score=...` to emitting `priority=...` on their
StrategyResult.

CLI:
    python tools/migrate_conviction.py strategies/s523c_growth.py
      → prints suggested diff (grep-style) locating conviction_score= call sites.

Not a full AST rewriter — the strategy author reviews and applies manually.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np


def priority_from_conviction(score: float) -> int:
    """Map a conviction score in [0.0, 1.0] to an integer priority in [0, 1_000_000].

    Matches the migration shim in `v5.signals.TokenSignals.__post_init__`:
    values outside [0, 1] are clamped; 0.0 → 0, 0.5 → 500000, 1.0 → 1_000_000.
    """
    clipped = float(np.clip(score, 0.0, 1.0))
    return int(round(clipped * 1_000_000))


def _scan_strategy_file(path: Path) -> list[tuple[int, str]]:
    """Return (line_no, line_text) for every conviction_score= call site in `path`."""
    hits: list[tuple[int, str]] = []
    pattern = re.compile(r"\bconviction_score\s*=")
    for i, line in enumerate(path.read_text().splitlines(), 1):
        if pattern.search(line):
            hits.append((i, line.rstrip()))
    return hits


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("strategy_file", type=Path, help="Path to a strategy .py file")
    args = parser.parse_args()
    if not args.strategy_file.is_file():
        print(f"ERROR: not a file: {args.strategy_file}", file=sys.stderr)
        return 1
    hits = _scan_strategy_file(args.strategy_file)
    if not hits:
        print(f"{args.strategy_file}: no conviction_score= call sites found.")
        return 0
    print(f"{args.strategy_file}: {len(hits)} conviction_score= call site(s):")
    for lineno, text in hits:
        print(f"  line {lineno}: {text}")
    print()
    print("Suggested: replace `conviction_score=<arr>` with")
    print("  `priority=tools.migrate_conviction.priority_from_conviction_array(<arr>)`")
    print("or emit integer priority directly if your strategy already reasons in int scores.")
    return 0


def priority_from_conviction_array(scores) -> "np.ndarray":
    """Vectorized version: map a float array to int32 priorities with clip + round."""
    arr = np.asarray(scores, dtype=np.float64)
    return np.round(np.clip(arr, 0.0, 1.0) * 1_000_000).astype(np.int32)


if __name__ == "__main__":
    sys.exit(_main())
