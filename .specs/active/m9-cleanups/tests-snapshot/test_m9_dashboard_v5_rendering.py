"""M9 C-6 — v5 dashboard deployment at /v5 URL, v4 untouched at /.

Covers AC #11:
- v4 dashboard at / unchanged (renders /data/state.json)
- v5 dashboard at /v5 reads /srv/data/state_v5.json
- state_v5.json has FIX-aligned counters
- binding_constraint column JOINs sizing_fills.jsonl by order_id
- start_all_services.sh file hash stable (never modified)

All tests MUST FAIL today — v5/dashboard_state.py, state_v5.json emitter,
path-aware JS, and the binding_constraint join helper do not exist yet.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


class TestV4DashboardUnchanged:
    """AC #11 — v4 dashboard at / still fetches /data/state.json after M9."""

    def test_v4_state_json_fetch_path_unchanged(self):
        """The v4 dashboard HTML must still reference /data/state.json for the '/' view."""
        index_html_candidates = [
            Path("/srv/dashboard/current/index.html"),
            _project_root / "dashboard" / "index.html",
            _project_root / "v4" / "dashboard" / "index.html",
        ]
        found = next((p for p in index_html_candidates if p.exists()), None)
        if found is None:
            pytest.fail("dashboard index.html not found in expected locations")

        html = found.read_text()
        # Path-aware fetcher from brief
        assert "/data/state.json" in html, (
            "v4 state.json fetch URL missing from dashboard HTML"
        )
        assert "location.pathname" in html or "pathname" in html, (
            "path-aware view detection missing"
        )


class TestV5DashboardUrl:
    """AC #11 — /v5 URL renders from state_v5.json."""

    def test_v5_state_json_served_at_v5_path(self):
        """Dashboard HTML routes /v5 path to /data/state_v5.json fetch."""
        index_html_candidates = [
            Path("/srv/dashboard/current/index.html"),
            _project_root / "dashboard" / "index.html",
            _project_root / "v4" / "dashboard" / "index.html",
        ]
        found = next((p for p in index_html_candidates if p.exists()), None)
        if found is None:
            pytest.fail("dashboard index.html not found in expected locations")

        html = found.read_text()
        assert "/data/state_v5.json" in html, (
            "v5 state.json fetch URL missing from dashboard HTML"
        )
        assert "/v5" in html, "v5 path prefix missing from view detection"


class TestStateV5JsonFixAlignedCounters:
    """AC #11 — state_v5.json contains FIX-aligned counters."""

    def test_state_v5_json_has_fix_aligned_counter_keys(self):
        from v5.dashboard_state import build_state_v5_json

        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "state_v5.json"
            build_state_v5_json(
                output_path=out_path,
                positions=[],
                orders=[],
                strategies=[],
            )
            state = json.loads(out_path.read_text())

            required_counters = {
                "partial_fills",      # FIX OrdStatus(39)=1
                "increase_fills",     # scale-up events
                "contingent_fills",   # FIX ContingencyType(1385) != 0
                "entry_scale_downs",  # M8 clamp scale-downs
            }
            missing = required_counters - set(state.get("counters", {}).keys())
            assert not missing, (
                f"state_v5.json missing FIX-aligned counters: {missing}"
            )


class TestBindingConstraintJoin:
    """AC #11 — binding_constraint column JOINs sizing_fills.jsonl by order_id."""

    def test_binding_constraint_joined_into_trade_row(self):
        from v5.dashboard_state import join_binding_constraint

        with tempfile.TemporaryDirectory() as tmp:
            sizing_log = Path(tmp) / "sizing_fills.jsonl"
            sizing_log.write_text(
                json.dumps({"order_id": "ord-42", "binding_constraint": "concentration"}) + "\n"
                + json.dumps({"order_id": "ord-99", "binding_constraint": "adv_cap"}) + "\n"
            )

            trades = [
                {"order_id": "ord-42", "symbol": "BTC"},
                {"order_id": "ord-99", "symbol": "ETH"},
                {"order_id": "ord-missing", "symbol": "SOL"},
            ]
            joined = join_binding_constraint(trades=trades, sizing_log=sizing_log)

            assert joined[0]["binding_constraint"] == "concentration"
            assert joined[1]["binding_constraint"] == "adv_cap"
            # Missing join entries render '-' not error
            assert joined[2]["binding_constraint"] == "-"


class TestStartAllServicesShUnchanged:
    """AC #11 — start_all_services.sh must be unchanged (file hash stable)."""

    def test_start_all_services_sh_file_hash_stable(self):
        """Ensure M9 work does not modify start_all_services.sh."""
        candidates = [
            _project_root / "tools" / "start_all_services.sh",
            _project_root / "scripts" / "start_all_services.sh",
        ]
        found = next((p for p in candidates if p.exists()), None)
        if found is None:
            pytest.fail("start_all_services.sh not found in expected locations")

        # Expected hash must be pinned in the M9 fixture; test fails until implementation
        # pins the correct hash.
        fixture_path = _project_root / "v5" / "tests" / "fixtures" / "m9_file_hashes" / "start_all_services.sh.sha256"
        if not fixture_path.exists():
            pytest.fail(
                f"Expected hash fixture missing at {fixture_path} "
                "(M9 must pin the pre-M9 hash)"
            )
        expected_hash = fixture_path.read_text().strip()
        actual_hash = hashlib.sha256(found.read_bytes()).hexdigest()
        assert actual_hash == expected_hash, (
            f"start_all_services.sh modified during M9 (hash drift): "
            f"expected {expected_hash}, got {actual_hash}"
        )
