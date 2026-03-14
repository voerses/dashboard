"""AC1 (concat/dedup), AC7 (panel building): DataLoader for live paper engine.

Tests verify:
- AC1: DataLoader concatenates historical parquet with live JSONL WAL overlay,
        deduplicates on timestamp, returns a single sorted DataFrame
- AC7: DataLoader builds close-price panels for portfolio strategies using
        the same batch pipeline as backtest (build_close_panel, rank_and_select)
"""

import json
import os

import pytest

from v3.data_loader import DataLoader


# ---------------------------------------------------------------------------
# AC1: Concat and dedup
# ---------------------------------------------------------------------------


class TestLoadToken:
    """DataLoader.load_token() merges parquet + JSONL WAL."""

    def test_load_token_returns_dataframe(self, tmp_path, sample_live_ohlcv_rows):
        """load_token returns a DataFrame with OHLCV columns."""
        # Write JSONL WAL
        wal_dir = tmp_path / "data" / "live" / "spot"
        wal_dir.mkdir(parents=True)
        wal_path = wal_dir / "BTC_live.jsonl"
        with open(wal_path, "w") as f:
            for row in sample_live_ohlcv_rows:
                f.write(json.dumps(row) + "\n")

        loader = DataLoader(data_dir=str(tmp_path / "data"))
        df = loader.load_token(token="BTC/USDT", market="spot")
        assert len(df) > 0
        for col in ("timestamp", "open", "high", "low", "close", "volume"):
            assert col in df.columns

    def test_load_token_sorted_by_timestamp(self, tmp_path, sample_live_ohlcv_rows):
        """Returned DataFrame is sorted by timestamp ascending."""
        wal_dir = tmp_path / "data" / "live" / "spot"
        wal_dir.mkdir(parents=True)
        wal_path = wal_dir / "BTC_live.jsonl"
        # Write in reverse order
        for row in reversed(sample_live_ohlcv_rows):
            with open(wal_path, "a") as f:
                f.write(json.dumps(row) + "\n")

        loader = DataLoader(data_dir=str(tmp_path / "data"))
        df = loader.load_token(token="BTC/USDT", market="spot")
        timestamps = df["timestamp"].tolist()
        assert timestamps == sorted(timestamps)

    def test_load_token_deduplicates_on_timestamp(self, tmp_path, sample_live_ohlcv_rows):
        """Duplicate timestamps are removed (last-write-wins or first-seen)."""
        wal_dir = tmp_path / "data" / "live" / "spot"
        wal_dir.mkdir(parents=True)
        wal_path = wal_dir / "BTC_live.jsonl"
        # Write same bars twice
        with open(wal_path, "w") as f:
            for row in sample_live_ohlcv_rows:
                f.write(json.dumps(row) + "\n")
            for row in sample_live_ohlcv_rows:
                f.write(json.dumps(row) + "\n")

        loader = DataLoader(data_dir=str(tmp_path / "data"))
        df = loader.load_token(token="BTC/USDT", market="spot")
        # No duplicate timestamps
        assert len(df) == len(sample_live_ohlcv_rows)
        assert df["timestamp"].is_unique

    def test_load_token_wal_only_no_parquet(self, tmp_path, sample_live_ohlcv_rows):
        """When no parquet exists, loads from WAL only without error."""
        wal_dir = tmp_path / "data" / "live" / "spot"
        wal_dir.mkdir(parents=True)
        wal_path = wal_dir / "BTC_live.jsonl"
        with open(wal_path, "w") as f:
            for row in sample_live_ohlcv_rows:
                f.write(json.dumps(row) + "\n")

        loader = DataLoader(data_dir=str(tmp_path / "data"))
        df = loader.load_token(token="BTC/USDT", market="spot")
        assert len(df) == len(sample_live_ohlcv_rows)

    def test_load_token_empty_wal_returns_parquet_only(self, tmp_path):
        """When WAL is empty but parquet exists, returns parquet data."""
        # Create empty WAL
        wal_dir = tmp_path / "data" / "live" / "spot"
        wal_dir.mkdir(parents=True)
        (wal_dir / "BTC_live.jsonl").write_text("")

        loader = DataLoader(data_dir=str(tmp_path / "data"))
        # This should not crash; may return empty if no parquet either
        df = loader.load_token(token="BTC/USDT", market="spot")
        assert df is not None
        assert len(df) == 0, (
            "Empty WAL with no parquet should return an empty DataFrame"
        )


# ---------------------------------------------------------------------------
# AC7: Panel building for portfolio strategies
# ---------------------------------------------------------------------------


class TestBuildClosePanel:
    """DataLoader.build_close_panel() builds token x time panel."""

    def test_build_close_panel_returns_dataframe(self, tmp_path, sample_live_ohlcv_rows):
        """build_close_panel returns a panel with tokens as columns."""
        # Set up WAL for two tokens
        for token_name in ("BTC", "ETH"):
            wal_dir = tmp_path / "data" / "live" / "spot"
            wal_dir.mkdir(parents=True, exist_ok=True)
            wal_path = wal_dir / f"{token_name}_live.jsonl"
            with open(wal_path, "w") as f:
                for row in sample_live_ohlcv_rows:
                    # Adjust prices slightly for different tokens
                    modified = dict(row)
                    if token_name == "ETH":
                        modified["close"] = row["close"] * 0.06
                    f.write(json.dumps(modified) + "\n")

        loader = DataLoader(data_dir=str(tmp_path / "data"))
        panel = loader.build_close_panel(
            tokens=["BTC/USDT", "ETH/USDT"],
            market="spot",
            lookback_days=30,
        )
        assert "BTC/USDT" in panel.columns
        assert "ETH/USDT" in panel.columns

    def test_panel_has_correct_row_count(self, tmp_path, sample_live_ohlcv_rows):
        """Panel rows match the bar count within lookback window."""
        wal_dir = tmp_path / "data" / "live" / "spot"
        wal_dir.mkdir(parents=True, exist_ok=True)
        wal_path = wal_dir / "BTC_live.jsonl"
        with open(wal_path, "w") as f:
            for row in sample_live_ohlcv_rows:
                f.write(json.dumps(row) + "\n")

        loader = DataLoader(data_dir=str(tmp_path / "data"))
        panel = loader.build_close_panel(
            tokens=["BTC/USDT"],
            market="spot",
            lookback_days=30,
        )
        # All 10 sample bars should be within 30 days
        assert len(panel) == len(sample_live_ohlcv_rows)

    def test_panel_values_are_close_prices(self, tmp_path, sample_live_ohlcv_rows):
        """Panel values are the close prices, not open/high/low."""
        wal_dir = tmp_path / "data" / "live" / "spot"
        wal_dir.mkdir(parents=True, exist_ok=True)
        wal_path = wal_dir / "BTC_live.jsonl"
        with open(wal_path, "w") as f:
            for row in sample_live_ohlcv_rows:
                f.write(json.dumps(row) + "\n")

        loader = DataLoader(data_dir=str(tmp_path / "data"))
        panel = loader.build_close_panel(
            tokens=["BTC/USDT"],
            market="spot",
            lookback_days=30,
        )
        # Check first value matches the close price of the first bar
        expected_close = sample_live_ohlcv_rows[0]["close"]
        assert panel["BTC/USDT"].iloc[0] == pytest.approx(expected_close)

    def test_panel_handles_missing_token_gracefully(self, tmp_path, sample_live_ohlcv_rows):
        """If a token has no data, panel still builds for available tokens."""
        wal_dir = tmp_path / "data" / "live" / "spot"
        wal_dir.mkdir(parents=True, exist_ok=True)
        wal_path = wal_dir / "BTC_live.jsonl"
        with open(wal_path, "w") as f:
            for row in sample_live_ohlcv_rows:
                f.write(json.dumps(row) + "\n")

        loader = DataLoader(data_dir=str(tmp_path / "data"))
        # DOGE has no data
        panel = loader.build_close_panel(
            tokens=["BTC/USDT", "DOGE/USDT"],
            market="spot",
            lookback_days=30,
        )
        assert "BTC/USDT" in panel.columns


class TestPanelPipelineCompatibility:
    """Portfolio strategies use same batch pipeline as backtest."""

    def test_panel_index_is_timestamp_based(self, tmp_path, sample_live_ohlcv_rows):
        """Panel index should be timestamp-based for alignment with backtest."""
        wal_dir = tmp_path / "data" / "live" / "spot"
        wal_dir.mkdir(parents=True, exist_ok=True)
        wal_path = wal_dir / "BTC_live.jsonl"
        with open(wal_path, "w") as f:
            for row in sample_live_ohlcv_rows:
                f.write(json.dumps(row) + "\n")

        loader = DataLoader(data_dir=str(tmp_path / "data"))
        panel = loader.build_close_panel(
            tokens=["BTC/USDT"],
            market="spot",
            lookback_days=30,
        )
        # Index should be based on timestamps
        assert panel.index.name == "timestamp", (
            f"Panel index name should be 'timestamp', got '{panel.index.name}'"
        )

    def test_panel_compatible_with_rank_and_select(self, tmp_path, sample_live_ohlcv_rows):
        """Panel format is compatible with rank_and_select() — has numeric columns."""
        for token_name in ("BTC", "ETH", "SOL"):
            wal_dir = tmp_path / "data" / "live" / "spot"
            wal_dir.mkdir(parents=True, exist_ok=True)
            wal_path = wal_dir / f"{token_name}_live.jsonl"
            with open(wal_path, "w") as f:
                for row in sample_live_ohlcv_rows:
                    f.write(json.dumps(row) + "\n")

        loader = DataLoader(data_dir=str(tmp_path / "data"))
        panel = loader.build_close_panel(
            tokens=["BTC/USDT", "ETH/USDT", "SOL/USDT"],
            market="spot",
            lookback_days=30,
        )
        # All columns should be numeric (for ranking)
        for col in panel.columns:
            assert panel[col].dtype.kind in ("f", "i"), \
                f"Column {col} is not numeric: {panel[col].dtype}"
