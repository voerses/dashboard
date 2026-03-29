---
id: AIPIP-0031
title: Mandatory Raw Backtest at Every Strategy Gate
status: accepted
author: @claude
created: 2026-03-29
---

## Problem

Strategies are developed blindly through the gate pipeline. We validate *signals* (IC, t-stats, regime splits) but never run a raw P&L backtest until very late — and when we do, implementation bugs (not dead signals) kill the results. The gap between research (+446% in R172) and v4 execution (~0%) has persisted across 3 porting attempts (s320, s400, s401).

### Root Cause

The gate pipeline validates signal *quality* (Gate 1: IC screen) but not signal *tradability* (does it make money after costs, liquidity caps, and realistic execution?). A signal with IC=0.20 can still lose money if:
- Fee drag exceeds the edge
- Position sizes exceed available liquidity (ADV)
- Compounding creates unrealistic position sizes in illiquid tokens
- Leverage causes margin calls
- Funding costs eat carry

### Evidence

| Strategy | Signal Quality | Raw Backtest | Problem |
|----------|---------------|-------------|---------|
| R162 momentum | IC valid, regime-robust | +298% standalone | Untested against ADV caps |
| s320 (V3 BTC) | GOLD signals | +2.9% after fix | 1.5x leverage bug, then 86% entry rejection |
| s400/s401 | Same GOLD signals | -32,508% | 10x vol_adj blowup, 23k margin calls |
| Momentum L3/S3 | Looks great IS | -31% OOS | IS=+2408%, OOS=-31%. Pure overfit. |

### What We Built

`tools/raw_backtest.py` — a strategy-agnostic backtest harness with non-bypassable guardrails:
- ADV cap (1% of 30d ADV per position)
- Equity-based limits (can't trade more than you have)
- Liquidation checks (5% maintenance margin)
- Slippage (sqrt market impact model)
- Fees + funding (baked into equity curve, all metrics are NET)
- Walk-forward split (automatic IS/OOS)
- OOS-only verdict (IS is diagnostic noise)

Kill criteria (OOS only):
- Sharpe < 2.0
- Calmar < 3.0
- MaxDD > -25%
- Annual return < 0%
- OOS trades < 30

## Proposal

### 1. Add raw backtest checkpoints to the gate pipeline

| Gate | Current | Proposed Addition |
|------|---------|-------------------|
| 0 (Idea Screen) | Hypothesis only | No change |
| 1 (Signal Lab) | IC, t-stat, hit rate | **+ Quick raw backtest on BTC** (single token, <2 min). KILL if negative net return after fees. |
| 2 (Dedup) | Compare vs existing | No change |
| 3/3P/3O (Prototype) | Write strategy file | **+ Full raw backtest through `tools/raw_backtest.py`** with all guardrails. Must produce OOS metrics. KILL if verdict is KILL. |
| 4 (Quick Validate) | BTC walk-forward | **Replace with raw backtest multi-window walk-forward** |
| 5/5P/5O (Full Validate) | 49-token validation | **+ Raw backtest on full universe** confirms Gate 3 results hold at scale |

### 2. Research Coordinator must use raw backtest

Update `memory/RESEARCH_COORDINATOR.md` to require:
- Every signal that passes Gate 1 IC screen gets an immediate raw backtest
- No signal advances past Gate 1 without a positive OOS net return
- Research scripts must use `from tools.raw_backtest import Backtest` for any P&L computation
- Ban standalone equity curve computations that skip fees/ADV/slippage

### 3. OOS-only decisions

- IS performance MUST NOT influence pass/kill decisions at any gate
- IS is diagnostic context only (shown in reports for debugging)
- All verdicts based exclusively on OOS metrics
- Walk-forward window: minimum 6 months OOS

### 4. Strategy skill gate guard update

Update `strategy-gate-guard.sh` to check for raw backtest output before allowing gate transitions past Gate 1.

## Files Changed

| File | Change |
|------|--------|
| `.claude/skills/strategy/SKILL.md` | Add raw backtest requirements at Gates 1, 3, 4, 5 |
| `memory/RESEARCH_COORDINATOR.md` | Add raw backtest mandate, ban standalone P&L scripts |
| `.claude/hooks/strategy-gate-guard.sh` | Check for backtest output file before gate advance |

## Risks

- **False kills:** Sharpe >2.0 and Calmar >3.0 are high bars. Legitimate strategies may get killed. Mitigation: these are OOS thresholds — if a strategy truly works OOS at these levels, it's real.
- **Speed:** Raw backtest adds ~2-5 min per gate. Mitigation: this replaces longer debugging cycles later (s320/s400/s401 each burned hours).

## Success Criteria

- Zero strategies reach Gate 5 with unmodeled friction costs
- Every gate transition has a raw backtest verdict attached
- No more "research shows +446% but v4 shows 0%" gaps

## Changelog

| Date | Change |
|------|--------|
| 2026-03-29 | Proposed |
| 2026-03-29 | Accepted by user |
| 2026-03-29 | Implemented: SKILL.md, RESEARCH_COORDINATOR.md, strategy-gate-guard.sh updated |
