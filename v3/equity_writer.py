"""Append-mode equity CSV writer for the live paper trading engine.

Writes equity snapshots in append mode so that a crash loses at most
one bar of data.  Each write opens the file in ``'a'`` mode and flushes
immediately.
"""

from __future__ import annotations

import csv
import os


_HEADER = ["timestamp", "equity", "cash", "exposure"]


class EquityWriter:
    """Append-mode CSV writer for equity snapshots."""

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self._path = os.path.join(output_dir, "equity.csv")
        os.makedirs(output_dir, exist_ok=True)

    def append_equity(
        self,
        timestamp: str,
        equity: float,
        cash: float,
        exposure: float,
    ) -> None:
        """Append one equity row, creating the header if needed."""
        needs_header = not os.path.exists(self._path) or os.path.getsize(self._path) == 0

        with open(self._path, "a", newline="") as f:
            writer = csv.writer(f)
            if needs_header:
                writer.writerow(_HEADER)
            writer.writerow([timestamp, equity, cash, exposure])
            f.flush()
            os.fsync(f.fileno())

    def flush(self) -> None:
        """Explicit flush (no-op — each append already flushes)."""
        pass
