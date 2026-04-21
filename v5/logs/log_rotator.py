"""M10 E3 / AC #22 — size-based JSONL log rotation with gzip.

Applies to every JSONL sink under ``v5/logs/``. Rotates at ``max_bytes``
(default 500MB), gzips the rotated file as ``{sink_name}.N.gz`` (where
N is the next free integer), and re-opens the active sink fresh.

Keeps at most ``retain`` rotated archives (default 10); older archives
are deleted.
"""
from __future__ import annotations

import gzip
import io
import os
import shutil
from pathlib import Path
from typing import Union


_DEFAULT_MAX_BYTES = 500 * 1024 * 1024  # 500 MB
_DEFAULT_RETAIN = 10


class LogRotator:
    """Rotates a JSONL sink at ``max_bytes`` with gzip compression.

    Thread-safety: single-writer. If multiple threads need to share
    the same sink, wrap with an external ``threading.Lock``.
    """

    def __init__(
        self,
        path: Union[str, Path],
        *,
        max_bytes: int = _DEFAULT_MAX_BYTES,
        retain: int = _DEFAULT_RETAIN,
    ) -> None:
        self._path = Path(path)
        self._max_bytes = int(max_bytes)
        self._retain = int(retain)
        self._bytes_written = 0
        # Open in append binary for fast concurrent-safe writes; we
        # buffer text encoding ourselves.
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Honor existing file size on open.
        if self._path.exists():
            try:
                self._bytes_written = self._path.stat().st_size
            except OSError:
                self._bytes_written = 0
        self._fh: io.BufferedWriter = open(self._path, "ab", buffering=0)

    def write(self, line: str) -> None:
        """Append a single record to the sink.

        Rotates BEFORE the write if the incoming record would push the
        file past ``max_bytes``.
        """
        data = line.encode("utf-8") if isinstance(line, str) else line
        if self._bytes_written + len(data) > self._max_bytes:
            self._rotate()
        self._fh.write(data)
        self._bytes_written += len(data)

    def _rotate(self) -> None:
        """Close active file, gzip it as the next archive, re-open fresh."""
        try:
            self._fh.close()
        except Exception:
            pass

        if not self._path.exists() or self._path.stat().st_size == 0:
            # Nothing to rotate — re-open cleanly.
            self._fh = open(self._path, "ab", buffering=0)
            self._bytes_written = 0
            return

        # Find next free archive index.
        n = 1
        while True:
            archive = self._path.with_name(f"{self._path.name}.{n}.gz")
            if not archive.exists():
                break
            n += 1

        # Gzip the active file → archive.
        with open(self._path, "rb") as src, gzip.open(archive, "wb") as gz:
            shutil.copyfileobj(src, gz)

        # Truncate the active file + re-open.
        try:
            self._path.unlink()
        except OSError:
            pass
        self._fh = open(self._path, "ab", buffering=0)
        self._bytes_written = 0

        # Retention: delete archives beyond the last `retain`.
        self._prune_archives()

    def _prune_archives(self) -> None:
        """Delete archives beyond the configured retain count."""
        archives = sorted(
            self._path.parent.glob(f"{self._path.name}.*.gz"),
            key=lambda p: p.stat().st_mtime,
        )
        if len(archives) > self._retain:
            for stale in archives[: len(archives) - self._retain]:
                try:
                    stale.unlink()
                except OSError:
                    pass

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass

    def __enter__(self) -> "LogRotator":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __del__(self) -> None:  # noqa: D401 — best-effort finalizer
        try:
            self.close()
        except Exception:
            pass
