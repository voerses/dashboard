"""M3 acceptance tests — signal_mode='full' bypass + tolerance constants.

Covers:
  - AC23: When `signal_mode == 'full'`, the indicator pipeline bypasses
           RollingCache entirely — no read, no write. Legacy pd.Series.ewm()
           path is used. RollingCache access is asserted zero via a mock.
  - AC15 (tolerance constants): Per-field float tolerance constants are
           present in the parity test support surface. We assert the tolerance
           constants exist with the brief-specified values:
             - float64 abs tol: 1e-10, rel tol: 1e-8
             - float32 abs tol: 1e-5, rel tol: 1e-4

All tests MUST FAIL today — there is no `signal_mode` config field, no
`compute_indicators_selective` dispatch, no tolerance constants module.

Seed: 42. TestClock epoch: 2026-03-01T00:00:00Z (not used in this file).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


# ===================================================================
# AC23 — signal_mode='full' bypasses RollingCache
# ===================================================================

class TestAC23SignalModeFullBypassesCache:
    """When signal_mode='full', RollingCache is not touched."""

    def test_portfolio_config_has_signal_mode_field(self):
        from v5.config import PortfolioConfig
        cfg = PortfolioConfig(capital=10_000.0, seed=42)
        assert hasattr(cfg, "signal_mode")
        # Default per brief is 'incremental'
        assert cfg.signal_mode == "incremental"

    def test_paper_config_has_signal_mode_field(self):
        from v5.paper_config import PaperConfig
        cfg = PaperConfig(strategies=[])
        assert hasattr(cfg, "signal_mode")
        assert cfg.signal_mode == "incremental"

    def test_signal_mode_accepts_full_and_incremental(self):
        from v5.config import PortfolioConfig
        cfg_full = PortfolioConfig(
            capital=10_000.0, seed=42, signal_mode="full",
        )
        assert cfg_full.signal_mode == "full"
        cfg_inc = PortfolioConfig(
            capital=10_000.0, seed=42, signal_mode="incremental",
        )
        assert cfg_inc.signal_mode == "incremental"

    def test_signal_mode_rejects_unknown_value(self):
        from v5.config import PortfolioConfig
        with pytest.raises(ValueError):
            cfg = PortfolioConfig(
                capital=10_000.0, seed=42, signal_mode="banana",
            )
            # Some configs defer validation; force it
            resolver = getattr(cfg, "resolve", None)
            if resolver is not None:
                resolver()

    def test_full_mode_does_not_touch_rolling_cache(self):
        """With signal_mode='full', compute_indicators_selective must not
        read from or write to a RollingCacheRegistry.

        We inject a MagicMock registry onto the engine state and assert
        zero calls after the indicator pipeline runs.
        """
        from v5.config import PortfolioConfig
        from v5.engine import compute_indicators_selective
        cfg = PortfolioConfig(
            capital=10_000.0, seed=42, signal_mode="full",
        )
        mock_registry = MagicMock(name="RollingCacheRegistry")
        # Patch both possible module-level dispatches — whichever exists.
        # The function must accept a `rolling_cache_registry` kwarg OR read
        # from cfg; we patch broadly.
        try:
            compute_indicators_selective(
                config=cfg,
                rolling_cache_registry=mock_registry,
            )
        except TypeError:
            # Accept alternate kwarg name
            compute_indicators_selective(
                config=cfg,
                registry=mock_registry,
            )
        # Not a single attribute access against the registry
        assert mock_registry.mock_calls == [], (
            f"signal_mode='full' must not touch RollingCacheRegistry; "
            f"got calls: {mock_registry.mock_calls}"
        )

    def test_incremental_mode_touches_rolling_cache(self):
        """Sanity counter-check: signal_mode='incremental' DOES use the registry.
        This ensures test_full_mode_does_not_touch_rolling_cache is meaningful
        (not passing vacuously because the registry is never used)."""
        from v5.config import PortfolioConfig
        from v5.engine import compute_indicators_selective
        cfg = PortfolioConfig(
            capital=10_000.0, seed=42, signal_mode="incremental",
        )
        mock_registry = MagicMock(name="RollingCacheRegistry")
        try:
            compute_indicators_selective(
                config=cfg, rolling_cache_registry=mock_registry,
            )
        except TypeError:
            compute_indicators_selective(
                config=cfg, registry=mock_registry,
            )
        # At least one read
        assert len(mock_registry.mock_calls) > 0, (
            "signal_mode='incremental' should read from RollingCacheRegistry"
        )


# ===================================================================
# AC15 — tolerance constants
# ===================================================================

class TestAC15ToleranceConstants:
    """Per-field tolerances are named constants with the brief-specified values."""

    def test_float64_abs_tolerance_present(self):
        """Either v5/testing.py or the parity test file exposes the float64 tols.

        Per brief: float64 abs tol 1e-10, rel tol 1e-8.
        """
        found = False
        for modname in ("v5.testing", "v5.parity_tolerances"):
            try:
                mod = __import__(modname, fromlist=["*"])
            except ImportError:
                continue
            for name in ("F64_ATOL", "FLOAT64_ATOL", "ATOL_F64"):
                if hasattr(mod, name):
                    assert getattr(mod, name) == pytest.approx(1e-10)
                    found = True
                    break
        assert found, (
            "Expected an F64_ATOL (=1e-10) constant in v5/testing.py"
            " or v5/parity_tolerances.py per AC15"
        )

    def test_float64_rel_tolerance_present(self):
        found = False
        for modname in ("v5.testing", "v5.parity_tolerances"):
            try:
                mod = __import__(modname, fromlist=["*"])
            except ImportError:
                continue
            for name in ("F64_RTOL", "FLOAT64_RTOL", "RTOL_F64"):
                if hasattr(mod, name):
                    assert getattr(mod, name) == pytest.approx(1e-8)
                    found = True
                    break
        assert found, "Expected F64_RTOL (=1e-8) constant per AC15"

    def test_float32_abs_tolerance_present(self):
        """Float32 abs tol: 1e-5."""
        found = False
        for modname in ("v5.testing", "v5.parity_tolerances"):
            try:
                mod = __import__(modname, fromlist=["*"])
            except ImportError:
                continue
            for name in ("F32_ATOL", "FLOAT32_ATOL", "ATOL_F32"):
                if hasattr(mod, name):
                    assert getattr(mod, name) == pytest.approx(1e-5)
                    found = True
                    break
        assert found, "Expected F32_ATOL (=1e-5) constant per AC15"

    def test_float32_rel_tolerance_present(self):
        """Float32 rel tol: 1e-4."""
        found = False
        for modname in ("v5.testing", "v5.parity_tolerances"):
            try:
                mod = __import__(modname, fromlist=["*"])
            except ImportError:
                continue
            for name in ("F32_RTOL", "FLOAT32_RTOL", "RTOL_F32"):
                if hasattr(mod, name):
                    assert getattr(mod, name) == pytest.approx(1e-4)
                    found = True
                    break
        assert found, "Expected F32_RTOL (=1e-4) constant per AC15"
