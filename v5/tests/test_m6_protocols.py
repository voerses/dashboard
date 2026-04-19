"""M6 — DataClient / LiveDataClient Protocol shapes (T-D3 / AC-D3).

All tests MUST FAIL today — v5.data.protocols does not exist.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestDataClientProtocolShape:
    """T-D3 / AC-D3."""

    @pytest.mark.parametrize("name", [
        "supports", "connect", "disconnect", "request", "replay",
    ])
    def test_data_client_declares_method(self, name):
        from v5.data.protocols import DataClient
        assert hasattr(DataClient, name)

    @pytest.mark.parametrize("attr", ["venue", "supported_modes"])
    def test_data_client_declares_attr(self, attr):
        from v5.data.protocols import DataClient
        ann = getattr(DataClient, "__annotations__", {}) or {}
        assert attr in ann or hasattr(DataClient, attr)

    @pytest.mark.parametrize("name", ["subscribe", "unsubscribe", "subscribe_scheduled"])
    def test_live_data_client_declares_method(self, name):
        from v5.data.protocols import LiveDataClient
        assert hasattr(LiveDataClient, name)

    def test_live_data_client_extends_data_client(self):
        from v5.data.protocols import LiveDataClient
        mro_names = {c.__name__ for c in inspect.getmro(LiveDataClient)}
        assert "DataClient" in mro_names
