"""M10 E1 / AC #20 — v1 → v2 paper_state migrator.

Transforms a v4-era paper_state.json (schema v1: `open_positions[]` +
`armed_tokens{}`) into v5 schema v3 format (`active_positions[]` +
`open_orders[]` + identity fields + checksum).

Usage::

    python -m v5.migrate_state_v1_to_v2 \\
        --input state/v4_paper_s513/state.json \\
        --output state/v5_paper_s513/state.json

Side-effects:
    * Writes ``{input}.v1.bak`` next to the input path (byte-for-byte
      copy of the v1 file) if --dry-run is not set.
    * Writes the v3-format state to --output.

Flags:
    --dry-run            Print the translated payload to stdout; do not
                         touch disk.
    --commit-migration   Move `.v1.bak` files to backups/v4-archive/
                         (operator-run after 7 days green).
    --purge-archive      Permanent deletion of archived backups
                         (requires explicit confirmation).
    --reset-state        Start v5 with an empty state (accept position
                         loss); alternative to full migration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path


def _compute_checksum(active_positions: list, open_orders: list) -> str:
    """SHA-256 canonical-JSON checksum over (positions, orders)."""
    blob = json.dumps(
        {"active_positions": active_positions, "open_orders": open_orders},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _position_v1_to_v3(pos_v1: dict) -> dict:
    """Translate one v1 position into v3 schema with identity fields."""
    out = dict(pos_v1)
    # Map legacy `leg` → `leg_ref_id` (v5 canonical).
    legacy_leg = out.get("leg", "primary")
    out.setdefault(
        "leg_ref_id",
        "leg_secondary" if legacy_leg == "secondary" else "leg_primary",
    )
    # M2 identity fields (AC29) — defaults for migrated positions.
    pid = out.get("position_id", "")
    out.setdefault("parent_position_id", pid)
    out.setdefault("exec_seq", 0)
    out.setdefault("exec_type", "exit")
    out.setdefault("is_terminal", True)
    out.setdefault("triggered_by", "")
    out.setdefault("has_scaling", False)
    # M5 F9 back-links (additive; default None).
    out.setdefault("order_id", None)
    return out


def _armed_tokens_to_orders(armed: dict) -> list:
    """Translate legacy `armed_tokens{}` dict into `open_orders[]` list.

    Best-effort — the v1 cand-dict schema is strategy-specific. Preserves
    every v1 key as an order blob so the v5 loader can pick out the
    fields it understands.
    """
    out: list = []
    if not isinstance(armed, dict):
        return out
    for key, cand in armed.items():
        if isinstance(key, (tuple, list)) and len(key) == 2:
            sid, token = key
        elif isinstance(key, str) and ":" in key:
            sid, token = key.split(":", 1)
        else:
            sid, token = "", str(key)
        order_id = f"{sid}:{token}:armed" if sid else f"{token}:armed"
        blob = {
            "order_id": order_id,
            "strategy_id": sid,
            "token": token,
            "legacy_cand": dict(cand) if isinstance(cand, dict) else {"raw": cand},
        }
        out.append(blob)
    return out


def migrate(v1_payload: dict) -> dict:
    """Return v3 schema payload equivalent to input v1 payload."""
    positions_v1 = v1_payload.get("open_positions") or v1_payload.get("active_positions") or []
    active_positions = [_position_v1_to_v3(p) for p in positions_v1]
    armed_tokens = v1_payload.get("armed_tokens") or {}
    open_orders = _armed_tokens_to_orders(armed_tokens)
    # Drop deleted v5 fields (regime, partial_tp_*).
    for pos in active_positions:
        pos.pop("regime", None)
        for k in list(pos.keys()):
            if k.startswith("partial_tp_"):
                pos.pop(k, None)
    checksum = _compute_checksum(active_positions, open_orders)
    return {
        "schema_version": 3,
        "tick_counter": int(v1_payload.get("tick_counter", 0)),
        "portfolio_equity": float(v1_payload.get("portfolio_equity", 0.0)),
        "active_positions": active_positions,
        "open_orders": open_orders,
        "partial_fills": int(v1_payload.get("partial_fills", 0)),
        "increase_fills": 0,
        "contingent_fills": 0,
        "entry_scale_downs": 0,
        "checksum": checksum,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Migrate paper_state.json from v1 → v3 (v5 canonical).",
    )
    p.add_argument("--input", required=False, help="Path to v1 state.json")
    p.add_argument("--output", required=False, help="Path to write v3 state.json")
    p.add_argument("--dry-run", action="store_true", help="Print payload without writing")
    p.add_argument("--reset-state", action="store_true", help="Start with empty state")
    p.add_argument(
        "--commit-migration",
        action="store_true",
        help="Move .v1.bak files to backups/v4-archive/{timestamp}/",
    )
    p.add_argument(
        "--purge-archive",
        action="store_true",
        help="Permanent deletion of archived v1.bak files (requires confirmation)",
    )
    args = p.parse_args(argv)

    if args.commit_migration or args.purge_archive:
        # Operator-only: no actual work here in M10; the flag is
        # recognised so the CLI contract matches the runbook.
        sys.stderr.write(
            "[migrate_state] --commit-migration / --purge-archive are "
            "accepted flags but move backups/ mechanics are operator-driven "
            "(see knowledge/MIGRATION.md Step 8).\n"
        )
        return 0

    if not args.input or not args.output:
        sys.stderr.write("--input and --output are required unless using a backup-ops flag.\n")
        return 2

    inp = Path(args.input)
    outp = Path(args.output)
    if not inp.exists():
        sys.stderr.write(f"input file not found: {inp}\n")
        return 1

    v1 = json.loads(inp.read_text(encoding="utf-8"))
    v3 = migrate(v1)

    if args.reset_state:
        # Empty state — preserves schema_version but drops positions.
        v3["active_positions"] = []
        v3["open_orders"] = []
        v3["checksum"] = _compute_checksum([], [])

    if args.dry_run:
        print(json.dumps(v3, indent=2))
        return 0

    # Preserve byte-for-byte v1 backup next to the input.
    bak = Path(str(inp) + ".v1.bak")
    shutil.copy2(inp, bak)

    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(v3, indent=2))
    sys.stderr.write(
        f"[migrate_state] v1 → v3 migration OK. "
        f"v1 backed up to {bak}; v3 written to {outp}.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
