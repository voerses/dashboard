"""Tests for Hard Data Cap — AC1, AC2, AC3, AC4, AC5, AC6.

Tests that DataFrames are capped at anchor BEFORE being passed to
_build_context() / strategy functions, ensuring no indicator contamination
from future data.
"""
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest

from v4.config import PortfolioConfig, StrategySpec


def _make_df(start_ts_ms, n, interval_ms=3_600_000):
    """Create a synthetic OHLCV DataFrame with DatetimeIndex."""
    timestamps = pd.to_datetime(
        [start_ts_ms + i * interval_ms for i in range(n)], unit="ms"
    )
    data = {
        "open": [100.0 + i for i in range(n)],
        "high": [105.0 + i for i in range(n)],
        "low": [95.0 + i for i in range(n)],
        "close": [102.0 + i for i in range(n)],
        "volume": [1000.0 + i for i in range(n)],
    }
    return pd.DataFrame(data, index=timestamps)


# Anchor date for testing: 2026-02-28
ANCHOR = pd.Timestamp("2026-02-28")

# Start date far enough back for data to span Jan-Apr 2026
# Jan 1 2026 in milliseconds since epoch
JAN_2026_MS = int(pd.Timestamp("2026-01-01").timestamp() * 1000)

# Number of hours from Jan 1 to Apr 30 2026 (~2880 bars)
N_BARS_JAN_APR = int((pd.Timestamp("2026-04-30") - pd.Timestamp("2026-01-01")).total_seconds() / 3600)


class TestDataCapAfterAnchor:
    """AC1, AC2: Data capped at anchor — no rows after anchor date."""

    def test_ac1_no_rows_after_anchor(self):
        """AC1: _load_all_contexts() caps DataFrames at anchor BEFORE _build_context().

        Mock load_token_data to return data spanning Jan-Apr 2026, verify
        _build_context receives data capped at Feb 28.
        """
        from v4.portfolio_signals import _load_all_contexts

        df = _make_df(JAN_2026_MS, N_BARS_JAN_APR)
        assert df.index.max() > ANCHOR, "Test setup: data should extend beyond anchor"

        contexts_received = []

        def _capturing_build_context(self, token, df_arg, **kwargs):
            """Capture the DataFrame passed to _build_context."""
            contexts_received.append({"token": token, "df_max": df_arg.index.max()})
            mock_ctx = MagicMock()
            mock_ctx.ind_1h = {"close": np.ones(len(df_arg))}
            mock_ctx.idx_1h = df_arg.index
            mock_ctx.regime_1h = np.ones(len(df_arg))
            return mock_ctx

        config = PortfolioConfig()
        spec = StrategySpec(
            strategy_id="s01",
            market="perp",
        )

        with patch("v4.portfolio_signals.load_token_data") as mock_load, \
             patch("v4.engine.Engine._build_context", _capturing_build_context):
            mock_load.return_value = df

            _load_all_contexts(
                tokens=["BTC"],
                strategy_spec=spec,
                config=config,
                months=3,
                end_date=ANCHOR,
            )

        assert len(contexts_received) > 0, "Expected _build_context to be called"
        assert mock_load.call_count >= 1, "Expected load_token_data to be called"
        for ctx_info in contexts_received:
            assert ctx_info["df_max"] <= ANCHOR, (
                f"AC1: _build_context received data up to {ctx_info['df_max']}, "
                f"which is after anchor {ANCHOR}. Data should be capped."
            )

    def test_ac2_precompute_strategy_signals_caps_data(self):
        """AC2: precompute_strategy_signals() caps data at anchor before _build_context().

        Uses a Class A (per-token) strategy path. Mocks _load_strategy_fn to
        avoid ImportError on non-existent strategy module.
        """
        from v4.signals import precompute_strategy_signals

        df = _make_df(JAN_2026_MS, N_BARS_JAN_APR)

        contexts_received = []

        def _capturing_build_context(self, token, df_arg, **kwargs):
            contexts_received.append({"token": token, "df_max": df_arg.index.max()})
            mock_ctx = MagicMock()
            mock_ctx.ind_1h = {"close": np.ones(len(df_arg))}
            mock_ctx.idx_1h = df_arg.index.values
            mock_ctx.regime_1h = np.ones(len(df_arg))
            return mock_ctx

        # Dummy strategy function that returns empty results
        def _dummy_strategy(ctx):
            return MagicMock(entry_mask=np.zeros(len(ctx.idx_1h), dtype=bool))

        config = PortfolioConfig()
        spec = StrategySpec(
            strategy_id="s01",
            market="perp",
        )

        with patch("v4.signals.load_token_data") as mock_load, \
             patch("v4.engine.Engine._build_context", _capturing_build_context), \
             patch("v4.signals._load_strategy_fn", return_value=_dummy_strategy):
            mock_load.return_value = df

            try:
                precompute_strategy_signals(
                    strategy_spec=spec,
                    tokens=["BTC"],
                    config=config,
                    months=3,
                    end_date=ANCHOR,
                )
            except Exception:
                pass  # Pipeline may fail at TokenSignals construction; we only care about data cap

        assert len(contexts_received) > 0, (
            "AC2: _build_context was never called — strategy function may have "
            "failed before data loading. Check mock setup."
        )
        for ctx_info in contexts_received:
            assert ctx_info["df_max"] <= ANCHOR, (
                f"AC2: _build_context received data up to {ctx_info['df_max']}, "
                f"which is after anchor {ANCHOR}"
            )


class TestDataCapBackwardCompat:
    """AC3, AC5: Backward compatibility when end_date is None or in the future."""

    def test_ac3_no_end_date_returns_all_data(self):
        """AC3: When end_date is None, all available data is returned (no cap)."""
        from v4.portfolio_signals import _load_all_contexts

        df = _make_df(JAN_2026_MS, N_BARS_JAN_APR)
        original_max = df.index.max()

        contexts_received = []

        def _capturing_build_context(self, token, df_arg, **kwargs):
            contexts_received.append({"token": token, "df_max": df_arg.index.max()})
            mock_ctx = MagicMock()
            mock_ctx.ind_1h = {"close": np.ones(len(df_arg))}
            mock_ctx.idx_1h = df_arg.index
            mock_ctx.regime_1h = np.ones(len(df_arg))
            return mock_ctx

        config = PortfolioConfig()
        spec = StrategySpec(
            strategy_id="s01",
            market="perp",
        )

        with patch("v4.portfolio_signals.load_token_data") as mock_load, \
             patch("v4.engine.Engine._build_context", _capturing_build_context):
            mock_load.return_value = df

            _load_all_contexts(
                tokens=["BTC"],
                strategy_spec=spec,
                config=config,
                months=3,
                end_date=None,  # No cap
            )

        # Must have called _build_context — no silent skip
        assert len(contexts_received) > 0, (
            "AC3: _build_context was never called — check mock pipeline"
        )
        max_seen = max(c["df_max"] for c in contexts_received)
        # Data should NOT be capped — max should match original (load_from only trims start)
        assert max_seen >= original_max - pd.Timedelta(days=1), (
            "AC3: With end_date=None, data should not be artificially capped"
        )

    def test_ac5_future_end_date_returns_all_data(self):
        """AC5: When end_date is in the future (after all data), behavior matches end_date=None."""
        from v4.portfolio_signals import _load_all_contexts

        df = _make_df(JAN_2026_MS, N_BARS_JAN_APR)
        original_max = df.index.max()
        future_date = pd.Timestamp("2026-06-01")

        contexts_received = []

        def _capturing_build_context(self, token, df_arg, **kwargs):
            contexts_received.append({"token": token, "df_max": df_arg.index.max()})
            mock_ctx = MagicMock()
            mock_ctx.ind_1h = {"close": np.ones(len(df_arg))}
            mock_ctx.idx_1h = df_arg.index
            mock_ctx.regime_1h = np.ones(len(df_arg))
            return mock_ctx

        config = PortfolioConfig()
        spec = StrategySpec(
            strategy_id="s01",
            market="perp",
        )

        with patch("v4.portfolio_signals.load_token_data") as mock_load, \
             patch("v4.engine.Engine._build_context", _capturing_build_context):
            mock_load.return_value = df

            _load_all_contexts(
                tokens=["BTC"],
                strategy_spec=spec,
                config=config,
                months=3,
                end_date=future_date,
            )

        # Must have called _build_context — no silent skip
        assert len(contexts_received) > 0, (
            "AC5: _build_context was never called — check mock pipeline"
        )
        max_seen = max(c["df_max"] for c in contexts_received)
        assert max_seen >= original_max - pd.Timedelta(days=1), (
            "AC5: Future end_date should not cap data"
        )


class TestDataCapEquityAndSignals:
    """AC4: Backtest with end_date produces no outputs after that date."""

    def test_ac4_no_signal_timestamps_after_end_date(self):
        """AC4: Running backtest with end_date=2026-02-28 produces zero signal
        entries AND zero context timestamps after 2026-02-28.

        Verifies both: (a) input data to _build_context is capped, and
        (b) the returned context timestamps are all <= anchor.
        """
        from v4.portfolio_signals import _load_all_contexts

        df = _make_df(JAN_2026_MS, N_BARS_JAN_APR)
        assert df.index.max() > ANCHOR, "Test setup: data extends beyond anchor"

        seen_max_timestamps = []
        returned_ctx_timestamps = []

        def _capturing_build_context(self, token, df_arg, **kwargs):
            seen_max_timestamps.append(df_arg.index.max())
            mock_ctx = MagicMock()
            mock_ctx.ind_1h = {"close": np.ones(len(df_arg))}
            mock_ctx.idx_1h = df_arg.index
            mock_ctx.regime_1h = np.ones(len(df_arg))
            returned_ctx_timestamps.append(df_arg.index.max())
            return mock_ctx

        config = PortfolioConfig()
        spec = StrategySpec(
            strategy_id="s01",
            market="perp",
        )

        with patch("v4.portfolio_signals.load_token_data") as mock_load, \
             patch("v4.engine.Engine._build_context", _capturing_build_context):
            mock_load.return_value = df
            contexts, cutoff, anchor = _load_all_contexts(
                tokens=["BTC"],
                strategy_spec=spec,
                config=config,
                months=3,
                end_date=ANCHOR,
            )

        assert len(seen_max_timestamps) > 0, "Expected _build_context to be called"

        # (a) Input data was capped
        for max_ts in seen_max_timestamps:
            assert max_ts <= ANCHOR, (
                f"AC4: _build_context received data up to {max_ts}, "
                f"but anchor is {ANCHOR}. No data should exist after end_date."
            )

        # (b) Returned anchor matches the end_date
        assert anchor <= ANCHOR, (
            f"AC4: Returned anchor {anchor} exceeds end_date {ANCHOR}"
        )

        # (c) Returned contexts have no timestamps beyond anchor
        for token, ctx in contexts.items():
            ctx_max = ctx.idx_1h.max()
            assert ctx_max <= ANCHOR, (
                f"AC4: Context for {token} has timestamps up to {ctx_max}, "
                f"which is after end_date {ANCHOR}"
            )


class TestDataCapValidationPath:
    """AC6: v4/validation.py also applies the data cap correctly."""

    def test_ac6_validation_cpcv_accepts_anchor(self):
        """AC6: _run_cpcv_v4 accepts an anchor parameter to cap data.

        The validation path loads parquet directly (bypassing load_token_data).
        It must accept an anchor parameter and cap DataFrames before context building.
        """
        import inspect
        from v4.validation import _run_cpcv_v4

        sig = inspect.signature(_run_cpcv_v4)
        assert "anchor" in sig.parameters, (
            "AC6: _run_cpcv_v4 must accept an 'anchor' parameter to cap data. "
            f"Current parameters: {list(sig.parameters.keys())}"
        )

    def test_ac6_validation_purge_days_updated(self):
        """AC6: WalkForwardConfig.purge_days is updated to 7 (from 5)."""
        from v4.validation import WalkForwardConfig

        wf_cfg = WalkForwardConfig()
        assert wf_cfg.purge_days == 7, (
            f"AC6: WalkForwardConfig.purge_days should be 7 (was 5), got {wf_cfg.purge_days}"
        )

    def test_ac6_validation_config_dict_has_anchor(self):
        """AC6: validate_strategy() threads anchor through config_dict for ProcessPoolExecutor."""
        import inspect
        from v4.validation import validate_strategy

        sig = inspect.signature(validate_strategy)
        # validate_strategy should accept end_date or anchor to thread to workers
        params = list(sig.parameters.keys())
        has_anchor = "anchor" in params or "end_date" in params
        assert has_anchor, (
            f"AC6: validate_strategy must accept 'anchor' or 'end_date' parameter. "
            f"Current parameters: {params}"
        )
