"""M9 C-2 — Walk-Forward Finalization + CPCV (AC #6).

Acceptance criteria verified:
  - ValidationConfig(cpcv=CPCVSpec(n_groups=6, n_test_groups=2)) produces
    C(6,2)=15 paths.
  - WalkForwardResult.pbo < 0.40 on positive-alpha synthetic fixture.
  - deflated_sharpe > 0 on positive-alpha fixture.
  - Embargo + purge prevent leakage: bars in embargo window not in adjacent
    train/test.
  - ValidationConfig(cpcv=None) runs rolling-WF only (no CPCV paths).

All tests MUST FAIL today — the primitives referenced below
(`CPCVSpec`, new `ValidationConfig` shape, `WalkForwardRunner`,
`WalkForwardResult.pbo`, `WalkForwardResult.deflated_sharpe`) do not yet
exist in `v5/validation.py` as specified in M9 brief C-2.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


# ---------------------------------------------------------------------------
# Synthetic positive-alpha fixture
# ---------------------------------------------------------------------------

def _positive_alpha_returns(n_bars: int = 4000, seed: int = 7) -> np.ndarray:
    """Generate per-bar returns with persistent positive alpha.

    Used for PBO < 0.40 and deflated_sharpe > 0 assertions.
    """
    rng = np.random.default_rng(seed)
    noise = rng.normal(loc=0.0, scale=0.010, size=n_bars)
    alpha = np.full(n_bars, 0.0015)  # ~15 bps per bar expected alpha
    return alpha + noise


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCPCVPathCount:
    """AC #6 — CPCVSpec(n_groups=6, n_test_groups=2) produces C(6,2)=15 paths."""

    def test_cpcv_six_choose_two_produces_fifteen_paths(self):
        from v5.validation import CPCVSpec, ValidationConfig, WalkForwardRunner

        config = ValidationConfig(
            train_bars=500,
            recal_bars=100,
            cpcv=CPCVSpec(n_groups=6, n_test_groups=2,
                          purge_bars=10, embargo_bars=5),
        )
        runner = WalkForwardRunner(config=config)
        result = runner.run(returns=_positive_alpha_returns())

        expected_paths = math.comb(6, 2)
        assert expected_paths == 15
        assert len(result.per_path_metrics) == expected_paths, (
            f"CPCV(6,2) must yield C(6,2)=15 paths; got "
            f"{len(result.per_path_metrics)}"
        )


class TestCPCVPBOBelowThreshold:
    """AC #6 — PBO < 0.40 on positive-alpha synthetic fixture."""

    def test_pbo_below_0_40_on_positive_alpha(self):
        from v5.validation import CPCVSpec, ValidationConfig, WalkForwardRunner

        config = ValidationConfig(
            train_bars=500,
            recal_bars=100,
            cpcv=CPCVSpec(n_groups=6, n_test_groups=2,
                          purge_bars=10, embargo_bars=5),
            pbo_threshold=0.40,
        )
        runner = WalkForwardRunner(config=config)
        result = runner.run(returns=_positive_alpha_returns())

        assert result.pbo < 0.40, (
            f"Positive-alpha fixture should yield PBO < 0.40; got {result.pbo}"
        )


class TestCPCVDeflatedSharpePositive:
    """AC #6 — deflated_sharpe > 0 on positive-alpha fixture."""

    def test_deflated_sharpe_positive_on_positive_alpha(self):
        from v5.validation import CPCVSpec, ValidationConfig, WalkForwardRunner

        config = ValidationConfig(
            train_bars=500,
            recal_bars=100,
            cpcv=CPCVSpec(n_groups=6, n_test_groups=2,
                          purge_bars=10, embargo_bars=5),
            deflated_sharpe=True,
        )
        runner = WalkForwardRunner(config=config)
        result = runner.run(returns=_positive_alpha_returns())

        assert result.deflated_sharpe is not None, (
            "deflated_sharpe must be populated when ValidationConfig."
            "deflated_sharpe=True"
        )
        assert result.deflated_sharpe > 0.0, (
            f"Positive-alpha fixture should yield deflated_sharpe > 0; "
            f"got {result.deflated_sharpe}"
        )


class TestEmbargoPurgePreventLeakage:
    """AC #6 — Embargo + purge prevent leakage.

    For each CPCV fold, bars within `purge_bars` on either side of a test
    fold, and bars within `embargo_bars` after a test fold, MUST NOT appear
    in the train set of the same path.
    """

    def test_embargo_and_purge_bars_excluded_from_train(self):
        from v5.validation import CPCVSpec, ValidationConfig, WalkForwardRunner

        purge = 10
        embargo = 5
        spec = CPCVSpec(
            n_groups=6,
            n_test_groups=2,
            purge_bars=purge,
            embargo_bars=embargo,
        )
        config = ValidationConfig(
            train_bars=500,
            recal_bars=100,
            cpcv=spec,
        )
        runner = WalkForwardRunner(config=config)

        # The runner must expose its computed train/test index sets per path
        # so we can assert non-overlap including purge/embargo windows.
        splits = runner.compute_cpcv_splits(n_bars=3000)

        assert len(splits) == 15, (
            f"CPCV(6,2) must produce 15 splits; got {len(splits)}"
        )

        for path_idx, split in enumerate(splits):
            train_idx = set(split.train_indices)
            test_idx = set(split.test_indices)

            # Train and test never share a bar directly.
            assert train_idx.isdisjoint(test_idx), (
                f"Path {path_idx}: train and test indices overlap"
            )

            # For every bar in test, a purge_bars window BEFORE it and
            # embargo_bars window AFTER it must be absent from train.
            for t in test_idx:
                for offset in range(1, purge + 1):
                    assert (t - offset) not in train_idx, (
                        f"Path {path_idx}: purge violation — bar {t-offset} "
                        f"in train within purge window of test bar {t}"
                    )
                for offset in range(1, embargo + 1):
                    assert (t + offset) not in train_idx, (
                        f"Path {path_idx}: embargo violation — bar {t+offset} "
                        f"in train within embargo window of test bar {t}"
                    )


class TestRollingWalkForwardOnlyWhenCPCVNone:
    """AC #6 — ValidationConfig(cpcv=None) runs rolling-WF only."""

    def test_cpcv_none_yields_rolling_wf_only(self):
        from v5.validation import ValidationConfig, WalkForwardRunner

        config = ValidationConfig(
            train_bars=500,
            recal_bars=100,
            cpcv=None,
        )
        runner = WalkForwardRunner(config=config)
        result = runner.run(returns=_positive_alpha_returns())

        # When cpcv=None, there are no CPCV paths — only rolling-WF folds.
        assert result.per_path_metrics == [] or result.per_path_metrics is None, (
            "cpcv=None must produce no CPCV paths"
        )
        assert len(result.per_fold_metrics) > 0, (
            "Rolling WF folds must still be populated when cpcv=None"
        )
        # PBO is a CPCV-only statistic; absent in rolling-only mode.
        assert result.pbo is None, (
            "PBO must be None when cpcv=None (rolling-WF only)"
        )
