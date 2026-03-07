"""DataLoader for live paper engine.

Concatenates historical parquet data with live JSONL WAL overlay,
deduplicates on timestamp, and returns sorted DataFrames.  Also
builds close-price panels for portfolio strategies.
"""

from __future__ import annotations

import json
import os
from typing import Sequence

import pandas as pd


def _token_prefix(token: str) -> str:
    """'BTC/USDT' -> 'BTC'."""
    return token.split("/")[0]


class DataLoader:
    """Merges parquet history + JSONL WAL into unified DataFrames."""

    def __init__(self, data_dir: str = "data", exchange: str = "binance"):
        self.data_dir = data_dir
        self.exchange = exchange

    # ------------------------------------------------------------------
    # AC1: load_token — concat parquet + JSONL, dedup, sort
    # ------------------------------------------------------------------

    def _parquet_path(self, token: str, market: str) -> str:
        prefix = _token_prefix(token)
        return os.path.join(
            self.data_dir, market, self.exchange,
            "1h_cache", f"{prefix}_1h.parquet",
        )

    def _wal_path(self, token: str, market: str) -> str:
        prefix = _token_prefix(token)
        return os.path.join(
            self.data_dir, "live", market, f"{prefix}_live.jsonl",
        )

    def _load_parquet(self, token: str, market: str) -> pd.DataFrame:
        path = self._parquet_path(token, market)
        if not os.path.exists(path):
            return pd.DataFrame()
        try:
            return pd.read_parquet(path)
        except Exception:
            return pd.DataFrame()

    def _load_wal(self, token: str, market: str) -> pd.DataFrame:
        path = self._wal_path(token, market)
        if not os.path.exists(path):
            return pd.DataFrame()
        rows: list[dict] = []
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)

    def load_token(
        self, token: str, market: str = "spot",
    ) -> pd.DataFrame:
        """Load and merge parquet + WAL data for a single token.

        Returns a DataFrame with columns: timestamp, open, high, low,
        close, volume — sorted by timestamp, deduplicated.
        """
        df_parquet = self._load_parquet(token, market)
        df_wal = self._load_wal(token, market)

        frames = [f for f in (df_parquet, df_wal) if len(f) > 0]
        if not frames:
            return pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"],
            )

        df = pd.concat(frames, ignore_index=True)

        # Ensure timestamp is numeric for dedup / sort.
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")

        df = df.drop_duplicates(subset=["timestamp"], keep="last")
        df = df.sort_values("timestamp").reset_index(drop=True)
        return df

    # ------------------------------------------------------------------
    # AC7: build_close_panel — token × time panel for portfolio strategies
    # ------------------------------------------------------------------

    def build_close_panel(
        self,
        tokens: Sequence[str],
        market: str = "spot",
        lookback_days: int = 30,
    ) -> pd.DataFrame:
        """Build a close-price panel with tokens as columns.

        Index is named 'timestamp'.  Only bars within `lookback_days`
        from the latest bar are included.
        """
        series: dict[str, pd.Series] = {}
        for token in tokens:
            df = self.load_token(token, market)
            if len(df) == 0:
                continue
            s = df.set_index("timestamp")["close"]
            s.name = token
            series[token] = s

        if not series:
            panel = pd.DataFrame()
            panel.index.name = "timestamp"
            return panel

        panel = pd.DataFrame(series)
        panel.index.name = "timestamp"
        panel = panel.sort_index()

        # Trim to lookback window.
        if len(panel) > 0 and lookback_days > 0:
            latest = panel.index.max()
            # Determine ms per day based on timestamp scale.
            if latest > 1e12:
                ms_per_day = 86_400_000
            else:
                ms_per_day = 86_400
            cutoff = latest - lookback_days * ms_per_day
            panel = panel.loc[panel.index >= cutoff]

        return panel

    # ------------------------------------------------------------------
    # Utility: trim WAL entries covered by refreshed parquets
    # ------------------------------------------------------------------

    def trim_live_overlay(self, token: str, market: str = "spot") -> int:
        """Remove WAL entries whose timestamps exist in parquet.

        Returns the number of lines removed.
        """
        df_parquet = self._load_parquet(token, market)
        if len(df_parquet) == 0:
            return 0

        parquet_ts = set(df_parquet["timestamp"].astype(int).tolist())
        wal_path = self._wal_path(token, market)
        if not os.path.exists(wal_path):
            return 0

        kept: list[str] = []
        removed = 0
        with open(wal_path, "r") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    row = json.loads(stripped)
                    if int(row["timestamp"]) in parquet_ts:
                        removed += 1
                        continue
                except (json.JSONDecodeError, KeyError):
                    pass
                kept.append(stripped)

        # Atomic write: tmp file then rename to avoid data loss on crash.
        tmp_path = wal_path + ".tmp"
        with open(tmp_path, "w") as f:
            for entry in kept:
                f.write(entry + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, wal_path)

        return removed
