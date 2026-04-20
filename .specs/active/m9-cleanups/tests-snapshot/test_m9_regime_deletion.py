"""M9 C-4 — Regime Fully Out of Engine (AC #1).

Acceptance criteria verified:
  - `grep -rn "RegimeConfig|regime_params|exit_regime|\\.regime\\[" v5/
    --exclude=regimes.py --exclude-dir=tests` returns 0 hits.
  - `grep -rn "conviction_score|conviction_array|min_conviction_threshold"
    v5/ --exclude-dir=tests` returns 0 hits.
  - `v5.regimes.detect_crisis(ctx, bar_idx)` returns bool based on
    BTC/TOTAL2 market indices.
  - `v5.regimes.detect_uptrend(ctx, bar_idx)` returns bool.
  - `v5.regimes.detect_dispersion(ctx, bar_idx)` returns bool.
  - Engine passes tests without any regime config (construct
    PortfolioConfig without RegimeConfig, verify no errors).
  - `ctx.market_indices` canonical keys present: BTC_CLOSE_1D,
    BTC_CLOSE_1H, TOTAL2, TOTAL3, DXY, BTC_DOMINANCE, REGIME_FLAG_1D.

All tests MUST FAIL today — `RegimeConfig` and conviction shims are
still live in v5/, regime detection helpers are not fully isolated in
`v5/regimes.py`, and `ctx.market_indices` canonical keys are not yet
populated.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

_V5_DIR = _project_root / "v5"


# ---------------------------------------------------------------------------
# Grep-based shim deletion tests
# ---------------------------------------------------------------------------

class TestRegimeFullyDeletedFromEngine:
    """AC #1 — regime artifacts absent from engine sources."""

    def test_no_regime_references_in_v5_engine(self):
        """grep returns 0 hits for regime patterns outside v5/regimes.py
        and v5/tests/."""
        result = subprocess.run(
            [
                "grep",
                "-rn",
                "-E",
                r"RegimeConfig|regime_params|exit_regime|\.regime\[",
                str(_V5_DIR),
                "--exclude=regimes.py",
                "--exclude-dir=tests",
            ],
            capture_output=True,
            text=True,
        )
        # grep returns 1 when no matches found, 0 when matches exist.
        assert result.returncode == 1, (
            f"Regime shim leakage: grep found matches.\n"
            f"stdout:\n{result.stdout}"
        )
        assert result.stdout.strip() == "", (
            f"Expected no matches but grep stdout was:\n{result.stdout}"
        )


class TestConvictionFullyDeleted:
    """AC #1 — conviction artifacts absent from engine sources."""

    def test_no_conviction_references_in_v5_non_tests(self):
        result = subprocess.run(
            [
                "grep",
                "-rn",
                "-E",
                r"conviction_score|conviction_array|min_conviction_threshold",
                str(_V5_DIR),
                "--exclude-dir=tests",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, (
            f"Conviction shim leakage: grep found matches.\n"
            f"stdout:\n{result.stdout}"
        )
        assert result.stdout.strip() == "", (
            f"Expected no matches but grep stdout was:\n{result.stdout}"
        )


# ---------------------------------------------------------------------------
# Regime helper utility tests
# ---------------------------------------------------------------------------

class TestRegimeHelpersReturnBool:
    """AC #1 — v5.regimes.* helpers return bool based on market indices."""

    def test_detect_crisis_returns_bool(self):
        from v5.regimes import detect_crisis
        from v5.universe_context import UniverseContext

        ctx = UniverseContext.build_test(
            tokens=["BTCUSDT"], bars=200, seed=0, timeframe="1h",
            with_market_indices=True,
        )
        result = detect_crisis(ctx, bar_idx=100)
        assert isinstance(result, bool), (
            f"detect_crisis must return bool; got {type(result).__name__}"
        )

    def test_detect_uptrend_returns_bool(self):
        from v5.regimes import detect_uptrend
        from v5.universe_context import UniverseContext

        ctx = UniverseContext.build_test(
            tokens=["BTCUSDT"], bars=200, seed=0, timeframe="1h",
            with_market_indices=True,
        )
        result = detect_uptrend(ctx, bar_idx=100)
        assert isinstance(result, bool), (
            f"detect_uptrend must return bool; got {type(result).__name__}"
        )

    def test_detect_dispersion_returns_bool(self):
        from v5.regimes import detect_dispersion
        from v5.universe_context import UniverseContext

        ctx = UniverseContext.build_test(
            tokens=["BTCUSDT", "ETHUSDT"], bars=200, seed=0, timeframe="1h",
            with_market_indices=True,
        )
        result = detect_dispersion(ctx, bar_idx=100)
        assert isinstance(result, bool), (
            f"detect_dispersion must return bool; got {type(result).__name__}"
        )


# ---------------------------------------------------------------------------
# Engine construction without regime config
# ---------------------------------------------------------------------------

class TestEngineHasNoRegimeConfig:
    """AC #1 — PortfolioConfig constructs without RegimeConfig; no errors."""

    def test_portfolio_config_no_regime_field(self):
        """Building a minimal PortfolioConfig must work and MUST NOT
        expose a RegimeConfig field."""
        from v5.config import PortfolioConfig, StrategySpec

        spec = StrategySpec(
            strategy_id="s_test",
            max_positions=3,
        )
        cfg = PortfolioConfig(strategies=[spec], capital=100_000.0)

        # There must be no regime_config-style attribute on PortfolioConfig
        # after C-4 deletion.
        forbidden_attrs = [
            "regime_config",
            "regime_params",
            "regime",
        ]
        for attr in forbidden_attrs:
            assert not hasattr(cfg, attr), (
                f"PortfolioConfig must not expose {attr!r} after C-4 "
                f"regime deletion"
            )

    def test_regime_config_symbol_not_importable(self):
        """`RegimeConfig` must not be importable from `v5.config`
        after C-4 deletion."""
        import v5.config as cfg_mod
        assert not hasattr(cfg_mod, "RegimeConfig"), (
            "v5.config.RegimeConfig must be deleted per M9 C-4 clean-cut"
        )


# ---------------------------------------------------------------------------
# Canonical market_indices keys
# ---------------------------------------------------------------------------

class TestMarketIndicesCanonicalKeys:
    """AC #1 — ctx.market_indices exposes canonical cross-sectional series."""

    def test_market_indices_canonical_keys_present(self):
        from v5.universe_context import UniverseContext

        ctx = UniverseContext.build_test(
            tokens=["BTCUSDT"], bars=200, seed=0, timeframe="1h",
            with_market_indices=True,
        )

        required_keys = {
            "BTC_CLOSE_1D",
            "BTC_CLOSE_1H",
            "TOTAL2",
            "TOTAL3",
            "DXY",
            "BTC_DOMINANCE",
            "REGIME_FLAG_1D",
        }
        present = set(ctx.market_indices.keys())
        missing = required_keys - present
        assert not missing, (
            f"ctx.market_indices missing canonical keys: {sorted(missing)}\n"
            f"present keys: {sorted(present)}"
        )
