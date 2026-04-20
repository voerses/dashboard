"""M8 AC-Sz5 — Binding-log JSONL writer for per-fill sizing transparency.

Every `run_clamp_pipeline` call writes exactly one JSONL entry to
`config.log_path` (or the default `v5/logs/sizing_fills.jsonl`) summarizing:
  - Request: intent, fraction/notional, leverage, reduce_only, margin_mode
  - Each clamp's computed ceiling value
  - `binding_constraint` (first clamp that reduced below requested, or
    "none" if unblocked, or "min_size"/"liquidation_distance" on reject,
    or "clamp_error_<name>" on exception)
  - Fill: filled_margin, filled_notional, fill_price, slippage_bps
  - `error` field populated only on clamp-error path

Schema uses FIX StrategyID(1098) convention (`strategy_id`), NOT
Party(448)/PartyRole(452)=53 — those are prime-broker give-up routing,
not intra-firm strategy identity (round-7 FIX reviewer correction).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_LOG_PATH = Path("v5/logs/sizing_fills.jsonl")


def write_sizing_fill_entry(
    order: Any,
    binding_log: Dict[str, Any],
    *,
    binding: Optional[str] = None,
    error: Optional[str] = None,
    path: Optional[Path] = None,
) -> None:
    """Append one AC-Sz5 binding-log entry to the JSONL sink.

    `binding_log` is the dict returned by `run_clamp_pipeline` containing
    the pre-baked per-fill fields. This function handles the I/O —
    appending one newline-delimited JSON object to the configured path.

    `binding` and `error` are optional overrides in case the caller has
    post-pipeline info to stamp (e.g., release_atomic catching an
    unexpected exception above the clamp layer).
    """
    # Allow binding_log to be the whole entry OR a partial — we merge
    # carefully below.
    entry: Dict[str, Any] = dict(binding_log) if binding_log else {}
    if binding is not None:
        entry["binding_constraint"] = binding
    if error is not None:
        entry["error"] = error
    # Fill in `order_id` / `symbol` / `strategy_id` if the caller passed
    # a partial (some clamp-error code paths do).
    entry.setdefault("order_id", getattr(order, "order_id", ""))
    entry.setdefault("symbol", getattr(order, "token", ""))
    entry.setdefault("strategy_id", getattr(order, "strategy_id", ""))

    target = Path(path) if path is not None else DEFAULT_LOG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")
