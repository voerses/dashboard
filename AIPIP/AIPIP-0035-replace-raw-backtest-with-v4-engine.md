---
id: AIPIP-0035
title: Replace raw_backtest.py References with V4 Portfolio Backtest CLI
status: accepted
author: @claude
created: 2026-04-03
---

## Problem

`tools/raw_backtest.py` is a standalone backtesting harness with a different cost model
than the v4 portfolio engine (`v4/portfolio_backtest.py`). Key differences:

- **Slippage:** raw uses daily ADV divisor; v4 uses hourly ADV (7-8x more conservative)
- **Fees:** raw charges once; v4 charges both entry and exit
- **Portfolio constraints:** raw has none; v4 enforces position limits, concentration, ADV caps
- **Walk-forward:** raw has no automatic burn; v4 has 365-day training window

This causes misleading Gate 1/3/4/5 results — strategies that look good in raw_backtest
fail in v4 (e.g., R162 showed +298% in research, -28% in v4). Gate verdicts should use
the same engine that production uses.

The v4 engine already supports `--raw --skip-wf` flags for pure signal quality testing,
making `tools/raw_backtest.py` redundant for gate validation.

## Proposal

### Already Done (knowledge files — not protected)
1. `knowledge/process/STRATEGY_PIPELINE_GATES.md` — all 9 `raw_backtest` references
   replaced with `v4/portfolio_backtest.py` CLI commands
2. `knowledge/V4_STRATEGY_COOKBOOK.md` — added research template section with CLI workflow
3. `strategies/RESEARCH_TEMPLATE.py` — new parameterizable template for Gate 1 testing

### Needs This AIPIP (protected files)

| File | Change |
|------|--------|
| `.claude/skills/strategy/SKILL.md` | Replace 3 `raw_backtest` references with v4 CLI instructions. Add reference to `RESEARCH_TEMPLATE.py` and `V4_STRATEGY_COOKBOOK.md`. |
| `.claude/hooks/strategy-gate-guard.sh` | Update 4 references from `results/raw_backtest/` directory checks to v4 output checks |

### SKILL.md Changes (3 lines)

Line 19: `Every strategy MUST be tested through tools/raw_backtest.py before advancing gates.`
→ `Every strategy MUST be tested through v4/portfolio_backtest.py before advancing gates.`

Line 31: `All P&L computation goes through from tools.raw_backtest import Backtest.`
→ `All P&L computation goes through v4/portfolio_backtest.py CLI (--raw --skip-wf for Gate 1).`

Line 164: `- Use tools/raw_backtest.py for any P&L validation (AIPIP-0031)`
→ `- Use v4/portfolio_backtest.py for any P&L validation (supersedes AIPIP-0031)`

### Hook Changes

`strategy-gate-guard.sh` lines 71-73, 113-118: Update directory check from
`results/raw_backtest/` to `results/v4/` (the v4 engine's default output directory).

## Rationale

- V4 engine is the source of truth for production
- Raw backtest gives false positives (missing costs, no constraints)
- V4 already has `--raw --skip-wf` for rapid prototyping (equivalent to raw_backtest use case)
- Research template + CLI workflow is simpler than importing a Python module

## Migration

`tools/raw_backtest.py` itself is NOT deleted — existing research scripts may reference it.
But no new gate validation should use it.
