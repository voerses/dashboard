"""M10 E1 — ROLLBACK.md rehearsal drill (AC #20).

AC #20 mandates that `ROLLBACK.md` is REHEARSED, not just documented.
A rollback procedure that has never been executed is a design document,
not a rollback.

This drill runs a replay-based end-to-end rollback against a sandbox
state dir (``state/v5_paper_multi_rehearsal/``, realised inside
``tmp_path``):

    1. Seed a v4-format state file (schema v1: `open_positions[]` +
       `armed_tokens{}` — NO `open_orders[]`, NO identity fields).
    2. Run ``python -m v5.migrate_state_v1_to_v2 --input <v1> --output <v2>``
       via subprocess. Assert v2 state file created and `.v1.bak`
       backup preserved next to the input.
    3. Use ``ReplayFixtureBuilder`` (v5/tests/fixtures/_replay_builder.py)
       to inject 500 bars of paper activity through ``paper_engine``,
       writing additional trades into the v2 state.
    4. Execute ROLLBACK.md programmatically: stop v5 (delete pid),
       restore `.v1.bak` over the original path, restart v4 by loading
       state via ``v4.paper_state``.
    5. Assert: v4 loads successfully; `equity_after_rollback` within
       $0.01 of `equity_before_v5_start`; `active_positions` count
       preserved across the round-trip.

MUST FAIL TODAY (RED):
    * ``v5.migrate_state_v1_to_v2`` does not exist as a runnable module.
    * ``ReplayFixtureBuilder`` raises NotImplementedError in Phase 3.
    * No ROLLBACK.md rehearsal runner exists.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SEED_EQUITY_USD = 150_000.00


def _write_v1_state(path: Path) -> float:
    """Seed a synthetic v4-format paper_state with 2 open positions.

    Returns the equity value that callers should reconcile against after
    the rollback round-trip.
    """
    payload = {
        "version": 1,
        "schema_version": 1,
        "tick_counter": 4242,
        "portfolio_equity": _SEED_EQUITY_USD,
        "open_positions": [
            {
                "position_id": "BTC:s524m:4200:primary",
                "token": "BTC",
                "strategy_id": "s524m",
                "leg": "primary",
                "entry_bar": 4200,
                "entry_price": 68_000.0,
                "direction": 1,
                "quantity": 0.5,
                "margin_usd": 20_000.0,
                "leverage": 1.7,
                "is_perp": True,
                "cumulative_funding": -12.34,
            },
            {
                "position_id": "ETH:s524m:4210:primary",
                "token": "ETH",
                "strategy_id": "s524m",
                "leg": "primary",
                "entry_bar": 4210,
                "entry_price": 3_400.0,
                "direction": -1,
                "quantity": 2.0,
                "margin_usd": 10_000.0,
                "leverage": 1.0,
                "is_perp": True,
                "cumulative_funding": 3.21,
            },
        ],
        "armed_tokens": {
            "SOL:s524m": {
                "token": "SOL",
                "strategy_id": "s524m",
                "trigger_price": 142.0,
                "direction": 1,
            }
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    return _SEED_EQUITY_USD


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRollbackDrill:
    """AC #20 — ROLLBACK.md rehearsed end-to-end."""

    def test_migrator_subprocess_produces_v2_state_and_backup(
        self, tmp_path: Path
    ) -> None:
        """Step 2: migrator CLI creates v2 state + `.v1.bak` backup."""
        rehearsal_dir = tmp_path / "state" / "v5_paper_multi_rehearsal"
        v1_path = rehearsal_dir / "state.json"
        v2_path = rehearsal_dir / "state_v2.json"
        _write_v1_state(v1_path)

        cmd = [
            sys.executable,
            "-m",
            "v5.migrate_state_v1_to_v2",
            "--input",
            str(v1_path),
            "--output",
            str(v2_path),
        ]
        result = subprocess.run(
            cmd,
            cwd=_PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"v5.migrate_state_v1_to_v2 CLI failed.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert v2_path.exists(), (
            "Migrator must write the v2 state file at --output path."
        )
        backup_path = v1_path.with_suffix(v1_path.suffix + ".v1.bak")
        assert backup_path.exists(), (
            f"Migrator must preserve the v1 input as `.v1.bak` next to it "
            f"(expected {backup_path})."
        )

    def test_v2_state_receives_replayed_activity(self, tmp_path: Path) -> None:
        """Step 3: 500 bars of replay activity append trades to v2 state."""
        from v5.tests.fixtures._replay_builder import (  # noqa: F401 (RED import)
            ReplayFixtureBuilder,
            ScenarioSpec,
        )

        rehearsal_dir = tmp_path / "state" / "v5_paper_multi_rehearsal"
        v1_path = rehearsal_dir / "state.json"
        v2_path = rehearsal_dir / "state_v2.json"
        _write_v1_state(v1_path)

        subprocess.run(
            [
                sys.executable,
                "-m",
                "v5.migrate_state_v1_to_v2",
                "--input",
                str(v1_path),
                "--output",
                str(v2_path),
            ],
            cwd=_PROJECT_ROOT,
            check=True,
            timeout=60,
        )

        # Replay fixture drives paper activity into v2 state.
        builder = ReplayFixtureBuilder(
            tokens=["BTC", "ETH", "SOL"],
            n_bars=500,
            start_ts_utc=1_735_689_600 * 1_000_000_000,  # 2025-01-01 00:00:00Z
            seed=42,
            scenario=ScenarioSpec(random_walk_stddev=0.002, forced_trades=4),
        )
        from v5 import paper_engine  # noqa: F401 (sanity)

        # Phase 4 wires the replay driver; today this will raise because the
        # builder is a stub.
        trade_log = builder.drive_into_state(state_path=v2_path)
        assert trade_log is not None
        assert len(trade_log) >= 1, (
            "500-bar replay must produce at least one trade written to "
            "the v2 state."
        )

    def test_rollback_restores_equity_and_positions_within_one_cent(
        self, tmp_path: Path
    ) -> None:
        """Steps 4-5: rollback round-trip preserves equity + position count."""
        import v4.paper_state as v4_paper_state  # noqa: F401

        rehearsal_dir = tmp_path / "state" / "v5_paper_multi_rehearsal"
        v1_path = rehearsal_dir / "state.json"
        v2_path = rehearsal_dir / "state_v2.json"
        pid_path = rehearsal_dir / "paper.pid"

        equity_before = _write_v1_state(v1_path)

        # Record pre-migration position count.
        v1_payload = json.loads(v1_path.read_text())
        positions_before = len(v1_payload["open_positions"])

        # Migrate v1 -> v2.
        subprocess.run(
            [
                sys.executable,
                "-m",
                "v5.migrate_state_v1_to_v2",
                "--input",
                str(v1_path),
                "--output",
                str(v2_path),
            ],
            cwd=_PROJECT_ROOT,
            check=True,
            timeout=60,
        )
        backup_path = v1_path.with_suffix(v1_path.suffix + ".v1.bak")

        # Simulate v5 running: write a pid file.
        pid_path.write_text("12345")
        assert pid_path.exists()

        # --- ROLLBACK.md programmatic execution ---------------------------
        # 1. stop v5 (delete pid)
        pid_path.unlink()
        # 2. restore .v1.bak over original path
        v1_path.write_bytes(backup_path.read_bytes())
        # 3. restart v4: v4.paper_state loads the restored file
        restored_state = v4_paper_state.load(str(v1_path))

        # v4 loader returns either a dict-like mapping or an engine state —
        # both expose equity + positions. Test the observable invariants.
        equity_after = getattr(
            restored_state,
            "portfolio_equity",
            None,
        )
        if equity_after is None and hasattr(restored_state, "__getitem__"):
            equity_after = restored_state["portfolio_equity"]
        assert equity_after is not None, (
            "Restored v4 state must expose portfolio_equity."
        )
        assert abs(equity_after - equity_before) < 0.01, (
            f"Equity continuity violated: before=${equity_before:.2f}, "
            f"after=${equity_after:.2f} (tolerance $0.01)."
        )

        positions_after = getattr(restored_state, "open_positions", None)
        if positions_after is None and hasattr(restored_state, "__getitem__"):
            positions_after = restored_state["open_positions"]
        assert positions_after is not None, (
            "Restored v4 state must expose open_positions list."
        )
        assert len(positions_after) == positions_before, (
            f"Position count diverged across rollback: "
            f"before={positions_before}, after={len(positions_after)}."
        )
