"""Acceptance tests for PaperConfig live extensions (AC11).

Tests verify:
  - AC11: PaperConfig has state_dir field with default "state/paper/"
  - AC11: PaperConfig has dashboard_push field with default False
  - AC11: load_paper_config reads state_dir from JSON
  - AC11: load_paper_config reads dashboard_push from JSON

All tests use synthetic data -- no real market data required.
These tests MUST FAIL until paper_config.py extensions are implemented (RED phase).
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "v4"))

import pytest

from v5.paper_config import PaperConfig, load_paper_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_config(data: dict) -> str:
    """Write a config dict to a temp JSON file and return the path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(data, f)
    f.close()
    return f.name


def _valid_config_dict(**overrides) -> dict:
    """Return a valid paper config dictionary with sensible defaults."""
    base = {
        "initial_capital": 200_000.0,
        "mode": "pool",
        "pool_name": "test_pool",
        "strategies": [
            {"strategy_id": "s56", "weight": 0.5, "market": "combined", "max_positions": 10},
        ],
        "max_portfolio_positions": 40,
        "concentration_limit": 0.10,
        "adv_cap_pct": 0.10,
        "min_position_usd": 200.0,
        "exchange": "binance",
        "seed": 42,
        "lookback_months": 12,
    }
    base.update(overrides)
    return base


# ===================================================================
# Test: AC11 — PaperConfig state_dir default
# ===================================================================

class TestPaperConfigStateDirDefault:
    """AC11: PaperConfig has state_dir field with default 'state/paper/'."""

    def test_state_dir_has_default(self):
        """PaperConfig.state_dir defaults to 'state/paper/' when not specified."""
        config_path = _write_config(_valid_config_dict())
        config = load_paper_config(config_path)

        assert hasattr(config, "state_dir")
        assert config.state_dir == "state/paper/"


# ===================================================================
# Test: AC11 — PaperConfig dashboard_push default
# ===================================================================

class TestPaperConfigDashboardPushDefault:
    """AC11: PaperConfig has dashboard_push field with default False."""

    def test_dashboard_push_has_default(self):
        """PaperConfig.dashboard_push defaults to False when not specified."""
        config_path = _write_config(_valid_config_dict())
        config = load_paper_config(config_path)

        assert hasattr(config, "dashboard_push")
        assert config.dashboard_push is False


# ===================================================================
# Test: AC11 — load_paper_config reads state_dir from JSON
# ===================================================================

class TestLoadStateDirFromJSON:
    """AC11: load_paper_config reads state_dir from JSON."""

    def test_state_dir_read_from_json(self):
        """When state_dir is present in JSON, load_paper_config reads it."""
        config_data = _valid_config_dict(state_dir="/custom/state/dir/")
        config_path = _write_config(config_data)
        config = load_paper_config(config_path)

        assert config.state_dir == "/custom/state/dir/"


# ===================================================================
# Test: AC11 — load_paper_config reads dashboard_push from JSON
# ===================================================================

class TestLoadDashboardPushFromJSON:
    """AC11: load_paper_config reads dashboard_push from JSON."""

    def test_dashboard_push_read_from_json(self):
        """When dashboard_push is present in JSON, load_paper_config reads it."""
        config_data = _valid_config_dict(dashboard_push=True)
        config_path = _write_config(config_data)
        config = load_paper_config(config_path)

        assert config.dashboard_push is True
