"""AC1: Freqtrade bridge at top-level.

Tests verify:
- freqtrade_bridge/ directory exists at /workspace/crypto_backtest/freqtrade_bridge/
- Contains: strategy_shell.py, exchange_registry.py, cost_model.py,
            parity_check.py, export_params.py, config_generator.py
- Imports work from the new location
"""

import importlib
import os
import pytest


BRIDGE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "freqtrade_bridge")

REQUIRED_MODULES = [
    "strategy_shell",
    "exchange_registry",
    "cost_model",
    "parity_check",
    "export_params",
    "config_generator",
]


class TestBridgeDirectoryExists:
    """freqtrade_bridge/ directory exists at the expected location."""

    def test_bridge_directory_exists(self):
        assert os.path.isdir(BRIDGE_DIR), (
            f"Expected freqtrade_bridge/ directory at {BRIDGE_DIR}"
        )

    def test_bridge_has_init(self):
        init_path = os.path.join(BRIDGE_DIR, "__init__.py")
        assert os.path.isfile(init_path), (
            f"Expected __init__.py in {BRIDGE_DIR}"
        )


class TestRequiredModulesExist:
    """All required module files exist in freqtrade_bridge/."""

    @pytest.mark.parametrize("module_name", REQUIRED_MODULES)
    def test_module_file_exists(self, module_name):
        module_path = os.path.join(BRIDGE_DIR, f"{module_name}.py")
        assert os.path.isfile(module_path), (
            f"Expected {module_name}.py in {BRIDGE_DIR}"
        )


class TestModuleImports:
    """Imports work from the new freqtrade_bridge location."""

    def test_import_strategy_shell(self):
        from freqtrade_bridge.strategy_shell import StrategyShell
        assert StrategyShell is not None

    def test_import_exchange_registry(self):
        from freqtrade_bridge.exchange_registry import ExchangeRegistry
        assert ExchangeRegistry is not None

    def test_import_cost_model(self):
        from freqtrade_bridge.cost_model import CostModel
        assert CostModel is not None

    def test_import_parity_check(self):
        from freqtrade_bridge.parity_check import ParityCheck
        assert ParityCheck is not None

    def test_import_export_params(self):
        from freqtrade_bridge.export_params import ExportParams
        assert ExportParams is not None

    def test_import_config_generator(self):
        from freqtrade_bridge.config_generator import ConfigGenerator
        assert ConfigGenerator is not None

    def test_import_freqtrade_bridge_package(self):
        import freqtrade_bridge
        assert freqtrade_bridge is not None
