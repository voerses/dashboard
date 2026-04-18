"""M4 AC41 — Shadow Replay Harness (T20 deliverable).

Replays pre-M4 and post-M4 engine trade lists through a structured diff:
  - 6-dim trade-sequence identity: (strategy_id, token, entry_ts, direction,
    leg_index, exit_reason)
  - Price tolerance: entry/exit within PRICE_ULP_TOLERANCE ULP (float32)
  - Per-trade PnL tolerance: ≤ PER_TRADE_PNL_BPS_TOLERANCE of notional
  - Aggregate fleet tolerance: ≤ AGGREGATE_PNL_BPS_TOLERANCE of total notional
  - Optional markdown report with `## Per-strategy breakdown`

Blocking gate for any PR that changes BarProcessor semantics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence

import numpy as np

from v5.tests.shadow_replay_tolerances import (
    AGGREGATE_PNL_BPS_TOLERANCE,
    PER_TRADE_PNL_BPS_TOLERANCE,
    PRICE_ULP_TOLERANCE,
)

# The 6-dimension identity tuple per AC41.
_IDENTITY_DIMS: tuple[str, ...] = (
    "strategy_id", "token", "entry_ts", "direction", "leg_index", "exit_reason",
)


@dataclass(frozen=True)
class TradeDiff:
    """Structured diff for one trade-pair failure."""
    index: int
    dimension: str        # which of the 6 identity dims OR "entry_price"/"exit_price"/"pnl"
    pre_value: object
    post_value: object
    detail: str = ""

    def __str__(self) -> str:
        return (
            f"trade[{self.index}] {self.dimension}: "
            f"pre={self.pre_value!r} vs post={self.post_value!r} {self.detail}"
        )


@dataclass
class ShadowReplayReport:
    status: str                                # "PASS" | "FAIL"
    diffs: list[TradeDiff] = field(default_factory=list)
    failing_trades: list[int] = field(default_factory=list)
    aggregate_pnl_delta_bps: float = 0.0
    per_strategy: dict[str, dict] = field(default_factory=dict)


def _price_within_ulp(a: float, b: float, ulp: int = PRICE_ULP_TOLERANCE) -> bool:
    if a == b:
        return True
    a32 = np.float32(a)
    b32 = np.float32(b)
    if np.isnan(a32) or np.isnan(b32):
        return False
    # Count ULPs between the two float32 values.
    ai = np.frombuffer(np.float32(a32).tobytes(), dtype=np.int32)[0]
    bi = np.frombuffer(np.float32(b32).tobytes(), dtype=np.int32)[0]
    return abs(int(ai) - int(bi)) <= ulp


def _per_trade_pnl_bps(pre: Mapping, post: Mapping) -> float:
    notional = abs(float(post.get("notional", 0.0))) or 1.0
    delta = abs(float(post.get("pnl", 0.0)) - float(pre.get("pnl", 0.0)))
    return 1e4 * delta / notional


def run_shadow_replay(
    pre_trades: Sequence[Mapping],
    post_trades: Sequence[Mapping],
    report_path: Optional[Path] = None,
) -> ShadowReplayReport:
    """Execute shadow replay comparison. Returns structured report.

    If report_path is provided, writes a markdown report with a single
    `## Per-strategy breakdown` heading and a 4-column table.
    """
    report = ShadowReplayReport(status="PASS")

    # Length check
    if len(pre_trades) != len(post_trades):
        report.status = "FAIL"
        report.diffs.append(TradeDiff(
            index=-1, dimension="trade_count",
            pre_value=len(pre_trades), post_value=len(post_trades),
        ))
        if report_path is not None:
            _emit_markdown(report, report_path)
        return report

    per_strategy: dict[str, dict] = {}
    total_notional = 0.0
    total_pnl_delta = 0.0
    # Aggregate metric per AC41: sum of per-trade |bps| drifts (captures systemic
    # same-direction bias across a fleet). For uniform-notional trades,
    # equals trade_count * per_trade_bps.
    aggregate_bps_sum = 0.0

    for i, (pre, post) in enumerate(zip(pre_trades, post_trades)):
        failed_this_trade = False

        # 6-dim identity check
        for dim in _IDENTITY_DIMS:
            if pre.get(dim) != post.get(dim):
                report.diffs.append(TradeDiff(
                    index=i, dimension=dim,
                    pre_value=pre.get(dim), post_value=post.get(dim),
                ))
                failed_this_trade = True

        # Price ULP tolerance (only check if identity passed)
        if not failed_this_trade:
            for pdim in ("entry_price", "exit_price"):
                if not _price_within_ulp(float(pre.get(pdim, 0.0)), float(post.get(pdim, 0.0))):
                    report.diffs.append(TradeDiff(
                        index=i, dimension=pdim,
                        pre_value=pre.get(pdim), post_value=post.get(pdim),
                        detail=f"exceeds {PRICE_ULP_TOLERANCE} ULP",
                    ))
                    failed_this_trade = True

        # Per-trade PnL tolerance
        if not failed_this_trade:
            bps = _per_trade_pnl_bps(pre, post)
            if bps > PER_TRADE_PNL_BPS_TOLERANCE:
                report.diffs.append(TradeDiff(
                    index=i, dimension="pnl",
                    pre_value=pre.get("pnl"), post_value=post.get("pnl"),
                    detail=f"delta {bps:.2f} bps > {PER_TRADE_PNL_BPS_TOLERANCE} bps",
                ))
                failed_this_trade = True

        if failed_this_trade:
            report.failing_trades.append(i)

        # Aggregate accounting (always accumulate — even if trade failed — to detect drift)
        notional = abs(float(post.get("notional", 0.0)))
        pnl_delta = abs(float(post.get("pnl", 0.0)) - float(pre.get("pnl", 0.0)))
        total_notional += notional
        total_pnl_delta += pnl_delta
        if notional > 0:
            aggregate_bps_sum += 1e4 * pnl_delta / notional

        # Per-strategy bookkeeping
        sid = str(post.get("strategy_id", "UNKNOWN"))
        strat = per_strategy.setdefault(
            sid, {"pass_count": 0, "fail_count": 0, "pnl_delta": 0.0, "notional": 0.0, "trade_count": 0}
        )
        strat["trade_count"] += 1
        strat["pnl_delta"] += pnl_delta
        strat["notional"] += notional
        if failed_this_trade:
            strat["fail_count"] += 1
        else:
            strat["pass_count"] += 1

    # Aggregate fleet-wide tolerance — sum of per-trade |bps|, captures systemic drift
    report.aggregate_pnl_delta_bps = aggregate_bps_sum
    if aggregate_bps_sum > AGGREGATE_PNL_BPS_TOLERANCE:
        report.diffs.append(TradeDiff(
            index=-1, dimension="aggregate_pnl",
            pre_value=0.0, post_value=aggregate_bps_sum,
            detail=f"aggregate delta {aggregate_bps_sum:.2f} bps > {AGGREGATE_PNL_BPS_TOLERANCE} bps",
        ))
        # Mark as fail even if no individual trade failed.
        if report.status == "PASS":
            report.status = "FAIL"

    if report.diffs:
        report.status = "FAIL"

    report.per_strategy = per_strategy

    if report_path is not None:
        _emit_markdown(report, report_path)

    return report


def _emit_markdown(report: ShadowReplayReport, path: Path) -> None:
    lines: list[str] = []
    lines.append("# Shadow Replay Report")
    lines.append("")
    lines.append(f"**Status**: {report.status}")
    lines.append(f"**Aggregate PnL delta**: {report.aggregate_pnl_delta_bps:.2f} bps")
    lines.append(f"**Failing trades**: {len(report.failing_trades)}")
    lines.append("")
    lines.append("## Per-strategy breakdown")
    lines.append("")
    lines.append("| Strategy | Pass | PnL delta (bps) | Trade count |")
    lines.append("|---|---|---|---|")
    for sid, s in sorted(report.per_strategy.items()):
        notional = s["notional"] or 1.0
        bps = 1e4 * s["pnl_delta"] / notional
        pass_tag = "yes" if s["fail_count"] == 0 else "no"
        lines.append(f"| {sid} | {pass_tag} | {bps:.2f} | {s['trade_count']} |")
    if not report.per_strategy:
        lines.append("| — | — | — | — |")
    lines.append("")

    if report.diffs:
        lines.append("## Diffs")
        lines.append("")
        for d in report.diffs:
            lines.append(f"- {d}")
        lines.append("")

    Path(path).write_text("\n".join(lines))
