"""M9 C-10 — Arbitration telemetry analyzer CLI.

Reads `v5/logs/arbitration.jsonl` (or a supplied path) and produces a
starvation-report per strategy: admission rate, total candidates, total
admitted, displacement chains, tier-boundary stats.

Replaces the SQL-over-fills debug pattern most shops use for tracking
cross-strategy starvation.

USAGE:
  python -m v5.tools.arbitration_analyzer --log <path> --starvation-report
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import defaultdict
from pathlib import Path


def _iter_jsonl(path: Path):
    """Yield JSON records from a .jsonl or .jsonl.gz file."""
    if path.suffix == ".gz":
        opener = lambda p: gzip.open(p, "rt")
    else:
        opener = lambda p: p.open()
    with opener(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def starvation_report(log_path: Path) -> str:
    """Build per-strategy admission-rate report. Returns formatted
    string suitable for CLI stdout."""
    counts = defaultdict(lambda: {"total": 0, "admitted": 0})
    displacements = defaultdict(int)
    tier_distribution = defaultdict(lambda: defaultdict(int))

    for row in _iter_jsonl(log_path):
        sid = row.get("strategy_id", "<unknown>")
        counts[sid]["total"] += 1
        if row.get("admitted"):
            counts[sid]["admitted"] += 1
        displaced_by = row.get("displaced_by")
        if displaced_by:
            if isinstance(displaced_by, (list, tuple)) and displaced_by:
                displacements[displaced_by[0]] += 1
        tier = row.get("tier")
        if tier is not None:
            tier_distribution[sid][int(tier)] += 1

    lines = ["=== M9 Arbitration Starvation Report ==="]
    lines.append(f"Source: {log_path}")
    lines.append("")
    lines.append(f"{'strategy_id':<20} {'total':>8} {'admitted':>10} {'admission_rate':>16}")
    lines.append("-" * 56)
    for sid in sorted(counts):
        total = counts[sid]["total"]
        admitted = counts[sid]["admitted"]
        rate = (admitted / total) if total else 0.0
        lines.append(f"{sid:<20} {total:>8} {admitted:>10} {rate * 100:>15.2f}%")
    lines.append("")
    if displacements:
        lines.append("=== Displacement chains (top 5) ===")
        for sid, n in sorted(displacements.items(), key=lambda x: -x[1])[:5]:
            lines.append(f"  {sid}: {n} displacements")
        lines.append("")
    if any(tier_distribution.values()):
        lines.append("=== Tier distribution per strategy ===")
        for sid in sorted(tier_distribution):
            tiers = tier_distribution[sid]
            tier_str = ", ".join(f"tier{t}:{tiers[t]}" for t in sorted(tiers))
            lines.append(f"  {sid}: {tier_str}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(
        prog="v5.tools.arbitration_analyzer",
        description="M9 C-10 arbitration telemetry analyzer",
    )
    parser.add_argument(
        "--log",
        type=Path,
        required=True,
        help="Path to arbitration.jsonl (or .jsonl.gz for rotated segments)",
    )
    parser.add_argument(
        "--starvation-report",
        action="store_true",
        help="Emit per-strategy admission-rate + displacement report",
    )
    args = parser.parse_args()

    if not args.log.exists():
        print(f"ERROR: log file not found: {args.log}", file=sys.stderr)
        return 2

    if args.starvation_report:
        print(starvation_report(args.log))
    else:
        print("No report requested. Use --starvation-report.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
