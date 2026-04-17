"""M1 Fork Verification Tests -- all must PASS after M1 implementation.

These tests verify the mechanical correctness of the v4->v5 fork:
- v5/ directory exists with correct file count
- No v4 imports remain in v5/
- All dead code is removed
- All dead files are absent
- v4/ is untouched
- v5 modules are importable
- All v5 tests pass
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

V5_DIR = _project_root / "v5"
V4_DIR = _project_root / "v4"

# Dead files that must NOT exist in v5/
DEAD_FILES = [
    "run_sentinel.py",
    "sentinel_metrics.py",
    "breach_detector.py",
    "stop_store.py",
    "paper_shadow.py",
]

# Dead config fields (AC3) -- must not appear as definitions in v5/
AC3_DEAD_FIELDS = [
    "raw_mode",
    "dd_scaling",
    "pump_filter_funding_zscore",
    "pump_filter_range_threshold",
    "unrealized_pnl_floor",
    "exit_regimes",
    "exit_regimes_long",
    "exit_regimes_short",
    "regime_exit_min_bars",
]

# C-12 sizing purge config fields (AC11)
AC11_CONFIG_FIELDS = [
    "kelly_mult_floor",
    "kelly_mult_range",
    "kelly_mult_override",
    "kelly_mult_scale",
    "cap_pct_floor",
    "cap_pct_range",
    "cap_pct_override",
    "cap_pct_scale",
    "adv_scaling_divisor",
    "adv_sizing_enabled",
]

# C-12 sizing purge per-signal array fields (AC11)
AC11_SIGNAL_FIELDS = [
    "size_multiplier",
    "cap_multiplier",
    "max_trade_pct",
]

# Partial TP fields (AC12)
AC12_FIELDS = [
    "partial_tp_atr",
    "partial_tp_pct",
    "partial_tp_trail",
    "partial_closed",
]

# Walk-forward config fields (AC13)
AC13_CONFIG_FIELDS = [
    "train_bars",
    "recal_bars",
    "purge_bars",
    "skip_walk_forward",
    "true_walk_forward",
]

# Conviction knobs (AC14)
AC14_FIELDS = [
    "conviction_mode",
    "min_conviction_threshold",
    "strategy_type",
]

# Sentinel-related imports to strip from paper_engine (AC18)
AC18_SENTINEL_TERMS = [
    "sentinel",
    "breach_detector",
    "stop_store",
]

# Regime fields to strip from paper_state (AC19)
AC19_REGIME_FIELDS = [
    "exit_regimes",
    "exit_regimes_long",
    "exit_regimes_short",
    "regime_exit_min_bars",
    "last_known_regimes",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _grep_v5(pattern: str, path: str = "") -> subprocess.CompletedProcess:
    """Run grep -rn for pattern in v5/ (or a subpath). Returns CompletedProcess.

    When searching the whole v5/ tree, excludes the tests/ directory so that
    the test file itself does not produce false matches. Also excludes __pycache__.

    When targeting a specific file, asserts the file exists first so that
    tests fail properly before v5 is built (rather than passing vacuously).
    """
    if path:
        target = V5_DIR / path
        assert target.is_file(), (
            f"Target file {target} does not exist -- v5/ not yet built"
        )
        return subprocess.run(
            ["grep", "-rn", pattern, str(target)],
            capture_output=True, text=True,
        )
    else:
        # Must have engine .py files (not just tests/) to be a valid check
        engine_files = [
            f for f in V5_DIR.glob("*.py")
            if f.name != "__init__.py"
        ]
        assert len(engine_files) > 0, (
            "No engine .py files in v5/ -- v5/ not yet built"
        )
        return subprocess.run(
            ["grep", "-rn", "--exclude-dir=tests", "--exclude-dir=__pycache__",
             pattern, str(V5_DIR)],
            capture_output=True, text=True,
        )


def _count_py_files(directory: Path) -> int:
    """Count .py files directly in directory (non-recursive)."""
    return len(list(directory.glob("*.py")))


def _assert_file_exists(relpath: str) -> Path:
    """Assert a v5/ file exists and return its Path. Fails the test if missing."""
    p = V5_DIR / relpath
    assert p.is_file(), f"Required file {p} does not exist (v5 not yet built?)"
    return p


# ===================================================================
# AC1: v5/ directory exists with 42 .py files (41 engine + __init__)
# ===================================================================

class TestAC01DirectoryExists:
    """AC1: v5/ directory exists with all 41 engine .py files + __init__.py."""

    def test_ac01_v5_directory_with_engine_files(self):
        """v5/ directory must exist and contain engine .py files (not just tests/)."""
        assert V5_DIR.is_dir(), f"v5/ directory does not exist at {V5_DIR}"
        engine_files = [f for f in V5_DIR.glob("*.py") if f.name != "__init__.py"]
        assert len(engine_files) > 0, (
            "v5/ directory exists but contains no engine .py files"
        )

    def test_ac01_v5_file_count(self):
        """v5/ must contain exactly 42 .py files (41 engine + __init__.py)."""
        py_files = list(V5_DIR.glob("*.py"))
        assert len(py_files) == 42, (
            f"Expected 42 .py files in v5/, found {len(py_files)}: "
            f"{sorted(f.name for f in py_files)}"
        )

    def test_ac01_init_file_exists(self):
        """v5/__init__.py must exist."""
        assert (V5_DIR / "__init__.py").is_file(), "v5/__init__.py missing"


# ===================================================================
# AC2: Zero 'from v4.' imports in v5/
# ===================================================================

class TestAC02NoV4Imports:
    """AC2: No occurrences of 'from v4.' in any v5/ file."""

    def test_ac02_no_v4_imports(self):
        """grep -r 'from v4.' v5/ must return empty."""
        result = _grep_v5(r"from v4\.")
        assert result.returncode != 0, (
            f"Found 'from v4.' imports in v5/:\n{result.stdout}"
        )


# ===================================================================
# AC3: Dead code removed from v5 config/position/simulator
# ===================================================================

class TestAC03DeadCodeRemoved:
    """AC3: Dead config fields removed -- no definitions of removed fields in v5/."""

    @pytest.mark.parametrize("field", AC3_DEAD_FIELDS)
    def test_ac03_dead_field_absent(self, field: str):
        """Dead field '{field}' must not appear in v5/."""
        result = _grep_v5(field)
        assert result.returncode != 0, (
            f"Dead field '{field}' still present in v5/:\n{result.stdout}"
        )


# ===================================================================
# AC4: Dead files NOT present in v5
# ===================================================================

class TestAC04DeadFilesAbsent:
    """AC4: Dead/dormant files must not exist in v5/."""

    def test_ac04_v5_is_populated(self):
        """Precondition: v5/ must have engine files before checking dead files."""
        engine_files = [f for f in V5_DIR.glob("*.py") if f.name != "__init__.py"]
        assert len(engine_files) >= 30, (
            f"v5/ has only {len(engine_files)} engine files -- not yet built"
        )

    @pytest.mark.parametrize("filename", DEAD_FILES)
    def test_ac04_dead_file_absent(self, filename: str):
        """Dead file '{filename}' must not exist in v5/ (requires v5 to be built)."""
        engine_files = [f for f in V5_DIR.glob("*.py") if f.name != "__init__.py"]
        assert len(engine_files) >= 30, (
            f"v5/ has only {len(engine_files)} engine files -- not yet built"
        )
        path = V5_DIR / filename
        assert not path.exists(), f"Dead file present: {path}"


# ===================================================================
# AC5 + AC10: pytest v5/tests/ passes
# ===================================================================

class TestAC05And10TestSuitePass:
    """AC5+AC10: pytest v5/tests/ runs with 0 failures, 0 errors."""

    def test_ac05_v5_tests_pass(self):
        """pytest v5/tests/ -x -q must exit with code 0.

        Excludes THIS file to prevent recursive subprocess chains
        (this file runs pytest which would re-discover this file).
        """
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(V5_DIR / "tests"), "-x", "-q",
             "--tb=short", "--no-header",
             "--ignore=" + str(V5_DIR / "tests" / "test_m1_fork_verification.py")],
            capture_output=True, text=True,
            cwd=str(_project_root),
            timeout=600,
        )
        assert result.returncode == 0, (
            f"v5 test suite failed (exit code {result.returncode}):\n"
            f"STDOUT:\n{result.stdout[-2000:]}\n"
            f"STDERR:\n{result.stderr[-2000:]}"
        )


# ===================================================================
# AC6: v4/ byte-identical to before M1
# ===================================================================

class TestAC06V4Untouched:
    """AC6: v4/ directory is byte-identical to before M1 started."""

    def test_ac06_v4_no_git_diff(self):
        """git diff v4/ must show no changes. Requires v5/ to be populated first."""
        # Precondition: v5 must be built for this check to be meaningful
        engine_files = [f for f in V5_DIR.glob("*.py") if f.name != "__init__.py"]
        assert len(engine_files) >= 30, (
            f"v5/ has only {len(engine_files)} engine files -- not yet built"
        )
        result = subprocess.run(
            ["git", "diff", "--stat", "v4/"],
            capture_output=True, text=True,
            cwd=str(_project_root),
        )
        # git diff --stat outputs nothing when there are no changes
        assert result.stdout.strip() == "", (
            f"v4/ has been modified:\n{result.stdout}"
        )


# ===================================================================
# AC7: from v5.config import PortfolioConfig works
# ===================================================================

class TestAC07ConfigImport:
    """AC7: Basic import smoke test -- PortfolioConfig importable from v5."""

    def test_ac07_import_portfolio_config(self):
        """from v5.config import PortfolioConfig must succeed."""
        result = subprocess.run(
            [sys.executable, "-c",
             "from v5.config import PortfolioConfig; print('OK')"],
            capture_output=True, text=True,
            cwd=str(_project_root),
        )
        assert result.returncode == 0, (
            f"Failed to import v5.config.PortfolioConfig:\n"
            f"STDERR: {result.stderr}"
        )
        assert "OK" in result.stdout


# ===================================================================
# AC8: from v5.paper_engine import PaperEngine works
# ===================================================================

class TestAC08PaperEngineImport:
    """AC8: Module boot check -- PaperEngine importable from v5."""

    def test_ac08_import_paper_engine(self):
        """from v5.paper_engine import PaperEngine must succeed."""
        result = subprocess.run(
            [sys.executable, "-c",
             "from v5.paper_engine import PaperEngine; print('OK')"],
            capture_output=True, text=True,
            cwd=str(_project_root),
        )
        assert result.returncode == 0, (
            f"Failed to import v5.paper_engine.PaperEngine:\n"
            f"STDERR: {result.stderr}"
        )
        assert "OK" in result.stdout


# ===================================================================
# AC9: git diff shows only v5/ additions
# ===================================================================

class TestAC09OnlyV5Additions:
    """AC9: git diff --stat shows only additions in v5/ -- no v4 modifications."""

    def test_ac09_git_diff_only_v5(self):
        """All changed files in git diff must be under v5/. Requires v5/ to be populated."""
        # Precondition: v5 must be built for this check to be meaningful
        engine_files = [f for f in V5_DIR.glob("*.py") if f.name != "__init__.py"]
        assert len(engine_files) >= 30, (
            f"v5/ has only {len(engine_files)} engine files -- not yet built"
        )
        result = subprocess.run(
            ["git", "diff", "--stat", "--name-only"],
            capture_output=True, text=True,
            cwd=str(_project_root),
        )
        changed_files = [
            f for f in result.stdout.strip().split("\n") if f.strip()
        ]
        non_v5 = [f for f in changed_files if not f.startswith("v5/")]
        # Allow pytest.ini changes (testpaths update) but nothing in v4/
        non_v5_non_config = [
            f for f in non_v5
            if not f.startswith("pytest.ini") and not f.startswith(".specs/")
        ]
        v4_changes = [f for f in non_v5_non_config if f.startswith("v4/")]
        assert len(v4_changes) == 0, (
            f"v4/ files were modified:\n{v4_changes}"
        )


# ===================================================================
# AC11: C-12 sizing purge
# ===================================================================

class TestAC11SizingPurge:
    """AC11: C-12 sizing purge -- config fields, signal fields, adv_to_sizing, vol_adj."""

    @pytest.mark.parametrize("field", AC11_CONFIG_FIELDS)
    def test_ac11_config_field_absent(self, field: str):
        """Sizing config field '{field}' must not appear in v5/config.py."""
        result = _grep_v5(field, "config.py")
        assert result.returncode != 0, (
            f"Sizing config field '{field}' still in v5/config.py:\n{result.stdout}"
        )

    @pytest.mark.parametrize("field", AC11_SIGNAL_FIELDS)
    def test_ac11_signal_field_absent_in_sizing(self, field: str):
        """Per-signal field '{field}' must not appear in v5/sizing.py."""
        result = _grep_v5(field, "sizing.py")
        assert result.returncode != 0, (
            f"Signal field '{field}' still in v5/sizing.py:\n{result.stdout}"
        )

    @pytest.mark.parametrize("field", AC11_SIGNAL_FIELDS)
    def test_ac11_signal_field_absent_in_signals(self, field: str):
        """Per-signal field '{field}' must not appear in v5/signals.py."""
        result = _grep_v5(field, "signals.py")
        assert result.returncode != 0, (
            f"Signal field '{field}' still in v5/signals.py:\n{result.stdout}"
        )

    def test_ac11_adv_to_sizing_removed(self):
        """adv_to_sizing() function must not exist in v5/universe.py."""
        result = _grep_v5("adv_to_sizing", "universe.py")
        assert result.returncode != 0, (
            f"adv_to_sizing still in v5/universe.py:\n{result.stdout}"
        )

    def test_ac11_vol_adj_removed(self):
        """vol_adj computation must not exist in v5/sizing.py."""
        result = _grep_v5("vol_adj", "sizing.py")
        assert result.returncode != 0, (
            f"vol_adj still in v5/sizing.py:\n{result.stdout}"
        )


# ===================================================================
# AC12: Partial TP fields deleted
# ===================================================================

class TestAC12PartialTPRemoved:
    """AC12: Partial TP fields must not appear anywhere in v5/."""

    @pytest.mark.parametrize("field", AC12_FIELDS)
    def test_ac12_partial_tp_field_absent(self, field: str):
        """Partial TP field '{field}' must not appear in v5/."""
        result = _grep_v5(field)
        assert result.returncode != 0, (
            f"Partial TP field '{field}' still in v5/:\n{result.stdout}"
        )


# ===================================================================
# AC13: Walk-forward knobs deleted from config
# ===================================================================

class TestAC13WalkForwardRemoved:
    """AC13: Walk-forward knobs deleted from v5 config and signals."""

    @pytest.mark.parametrize("field", AC13_CONFIG_FIELDS)
    def test_ac13_config_field_absent(self, field: str):
        """Walk-forward config field '{field}' must not appear in v5/config.py."""
        result = _grep_v5(field, "config.py")
        assert result.returncode != 0, (
            f"Walk-forward field '{field}' still in v5/config.py:\n{result.stdout}"
        )

    def test_ac13_live_bar_absent_from_signals(self):
        """live_bar parameter must not appear in v5/signals.py."""
        result = _grep_v5("live_bar", "signals.py")
        assert result.returncode != 0, (
            f"live_bar still in v5/signals.py:\n{result.stdout}"
        )


# ===================================================================
# AC14: Conviction system knobs deleted
# ===================================================================

class TestAC14ConvictionRemoved:
    """AC14: Conviction system knobs must not appear in v5 config."""

    @pytest.mark.parametrize("field", AC14_FIELDS)
    def test_ac14_conviction_field_absent(self, field: str):
        """Conviction field '{field}' must not appear in v5/config.py."""
        result = _grep_v5(field, "config.py")
        assert result.returncode != 0, (
            f"Conviction field '{field}' still in v5/config.py:\n{result.stdout}"
        )


# ===================================================================
# AC15: max_concurrent_per_token renamed to max_positions_per_symbol
# ===================================================================

class TestAC15Rename:
    """AC15: max_concurrent_per_token renamed to max_positions_per_symbol."""

    def test_ac15_old_name_absent(self):
        """max_concurrent_per_token must not appear anywhere in v5/."""
        result = _grep_v5("max_concurrent_per_token")
        assert result.returncode != 0, (
            f"Old name 'max_concurrent_per_token' still in v5/:\n{result.stdout}"
        )

    def test_ac15_new_name_present(self):
        """max_positions_per_symbol must be defined in v5/config.py."""
        result = _grep_v5("max_positions_per_symbol", "config.py")
        assert result.returncode == 0, (
            "New name 'max_positions_per_symbol' not found in v5/config.py"
        )


# ===================================================================
# AC16: exit_resolution/entry_resolution replaced by bar_resolution
# ===================================================================

class TestAC16ResolutionMerge:
    """AC16: exit_resolution and entry_resolution replaced by bar_resolution."""

    def test_ac16_exit_resolution_absent(self):
        """exit_resolution must not appear in v5/config.py."""
        result = _grep_v5("exit_resolution", "config.py")
        assert result.returncode != 0, (
            f"exit_resolution still in v5/config.py:\n{result.stdout}"
        )

    def test_ac16_entry_resolution_absent(self):
        """entry_resolution must not appear in v5/config.py."""
        result = _grep_v5("entry_resolution", "config.py")
        assert result.returncode != 0, (
            f"entry_resolution still in v5/config.py:\n{result.stdout}"
        )

    def test_ac16_bar_resolution_present(self):
        """bar_resolution (or bar_spec) must be defined in v5/config.py."""
        result_res = _grep_v5("bar_resolution", "config.py")
        result_spec = _grep_v5("bar_spec", "config.py")
        assert result_res.returncode == 0 or result_spec.returncode == 0, (
            "Neither 'bar_resolution' nor 'bar_spec' found in v5/config.py"
        )


# ===================================================================
# AC17: regime_params absent
# ===================================================================

class TestAC17RegimeParamsRemoved:
    """AC17: regime_params config field removed from v5/."""

    def test_ac17_regime_params_absent(self):
        """regime_params must not appear anywhere in v5/."""
        result = _grep_v5("regime_params")
        assert result.returncode != 0, (
            f"regime_params still in v5/:\n{result.stdout}"
        )


# ===================================================================
# AC18: paper_engine.py sentinel imports stripped
# ===================================================================

class TestAC18SentinelImportsStripped:
    """AC18: paper_engine.py must have no sentinel-related imports."""

    @pytest.mark.parametrize("term", AC18_SENTINEL_TERMS)
    def test_ac18_sentinel_term_absent(self, term: str):
        """Sentinel term '{term}' must not appear in v5/paper_engine.py."""
        result = _grep_v5(term, "paper_engine.py")
        assert result.returncode != 0, (
            f"Sentinel term '{term}' still in v5/paper_engine.py:\n{result.stdout}"
        )


# ===================================================================
# AC19: paper_state.py regime fields stripped
# ===================================================================

class TestAC19PaperStateRegimeStripped:
    """AC19: paper_state.py regime fields stripped from serialization."""

    @pytest.mark.parametrize("field", AC19_REGIME_FIELDS)
    def test_ac19_regime_field_absent(self, field: str):
        """Regime field '{field}' must not appear in v5/paper_state.py."""
        result = _grep_v5(field, "paper_state.py")
        assert result.returncode != 0, (
            f"Regime field '{field}' still in v5/paper_state.py:\n{result.stdout}"
        )
