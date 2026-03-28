---
id: AIPIP-0030
title: Mandatory Sizing Verification Gate for Research-to-Engine Strategy Ports
status: accepted
author: @claude
created: 2026-03-28
---

## Problem

Research notebooks produce strategies with idealized position sizing — fixed fractions like "20% per position" or "80%/3 = 26.7% per long." When porting to the v4 engine, these ideal sizes must be translated into engine sizing parameters (`target_vol`, `kelly_mult_override`, `cap_pct_override`). This translation is currently done by intuition with no verification step.

### Evidence: s400/s401 Catastrophic Failure

| Strategy | Research Result | V4 Result | Root Cause |
|----------|----------------|-----------|------------|
| s401 | +74.2%, -10.9% MaxDD | -32,508%, 23,111 margin calls | `target_vol=0.05` / `vol_floor=0.005` = 10x vol_adj in calm markets |
| s400 | Sharpe 1.39, +289.6% | Sharpe 0.44 | `cap_pct_override=0.15` caps at 15% vs research's 26.7% |

The fix was trivial (parameter changes only), but the failure was undetected through the entire strategy development pipeline — Gates 0 through 5 all passed without catching that the sizing parameters would produce positions 10x larger than intended.

### Why This Happened

1. **No sizing simulation step.** Nobody asked "what position size does `target_vol=0.05` actually produce when realized vol hits the floor?"
2. **Research declares intent, not constraints.** Notebooks say "20% per position" in comments but don't encode this as a verifiable target.
3. **Parameter interactions are invisible.** `target_vol` alone looks fine. `vol_floor` alone looks fine. Together they create `vol_adj = target_vol / vol_floor = 10x`, which is catastrophic.
4. **DD scaling was boilerplate.** Both strategies got copy-pasted DD scaling thresholds without considering that weekly-rebalance strategies naturally draw down before diversifying.
5. **Funding costs absent from research.** s400's raw-mode run showed $57k funding drag on $200k capital (~29%). Research modeled zero funding.

## Proposal

### 1. Research Target Sizes — Mandatory Declaration

Every strategy file must declare its intended sizing as a `RESEARCH_TARGET_SIZES` dict, placed next to `SIZING_OVERRIDES`:

```python
# What the research actually intended (verification target, not used by engine)
RESEARCH_TARGET_SIZES = {
    "per_position_pct": 0.267,    # 80%/3 longs = 26.7% each
    "max_concurrent": 6,           # 3 long + 3 short at rebalance
    "max_gross_exposure": 1.0,     # 100% of equity
    "hold_duration_hours": 168,    # 7-day rebalance cycle
}
```

This is documentation, not engine config. Its purpose is to provide a verification target for the sizing parameters.

### 2. Sizing Verification Script

Add `tools/verify_sizing.py` that takes a strategy ID, reads its `SIZING_OVERRIDES` and `RESEARCH_TARGET_SIZES`, and runs the engine's sizing pipeline across volatility regimes:

```
$ python tools/verify_sizing.py s400

s400 Sizing Verification
========================
SIZING_OVERRIDES: kelly_mult=0.50, target_vol=0.02, cap_pct=0.27
RESEARCH_TARGET: 26.7% per position

Volatility Regime Simulation:
  vol=0.005 (floor)  → vol_adj=4.0x → pos=26.7% → vs target: +0.0%  OK
  vol=0.010 (calm)   → vol_adj=2.0x → pos=13.3% → vs target: -50.0% WARN (under-sized)
  vol=0.020 (normal) → vol_adj=1.0x → pos=6.7%  → vs target: -75.0% WARN (under-sized)
  vol=0.050 (high)   → vol_adj=0.4x → pos=2.7%  → vs target: -90.0% INFO (vol-scaled down)

Composite check: worst-case = 0.50 × 4.0 = 2.0x  OK (< 8x)
Funding estimate: 0.01%/8h × 2.5x lev × 168h × 6 pos = ~3.2% per cycle  WARN
```

The script flags:
- **FAIL**: Any regime where position > 50% of equity (single name)
- **FAIL**: Worst-case composite > 8x
- **WARN**: Floor-regime position deviates >30% from research target
- **WARN**: Estimated funding > 10% of research gross return
- **INFO**: Non-floor regimes (expected to be smaller)

### 3. Strategy Gate Integration

Add sizing verification as a **mandatory check at Gate 3 (Prototype)**. The strategy cannot proceed to Gate 4 (Backtest) without a passing `verify_sizing.py` output. This catches parameter misconfiguration before any capital is at risk.

Update the strategy gate guard to check for `sizing_verification.json` in the strategy's spec directory.

### 4. DD Scaling Defaults

Change the strategy template's DD_SCALING default from the current aggressive boilerplate to empty:

```python
# DD scaling disabled by default. Enable with strategy-specific thresholds
# only after baseline validation confirms the strategy's natural drawdown profile.
DD_SCALING = []
```

Strategies that want DD scaling must justify their thresholds based on observed baseline MaxDD.

### 5. Funding Cost Estimation in Research

Add a `estimate_funding_cost()` helper to the research toolkit that any notebook holding perp positions >24h should call:

```python
def estimate_funding_cost(leverage, hold_hours, n_positions, avg_funding_rate=0.0001):
    """Rough funding drag estimate. 0.01%/8h is typical for crypto perps."""
    cycles = hold_hours / 8
    return avg_funding_rate * leverage * cycles * n_positions
```

This is advisory, not blocking — but it should be printed in research results so the researcher sees the drag before declaring victory.

## Alternatives Considered

### A. Automated parameter search (rejected)

Could auto-tune `target_vol`/`kelly_mult` to match research targets. Rejected because:
- Hides the translation logic, making it harder to understand why a parameter was chosen
- Creates a dependency on the search algorithm's correctness
- Verification is simpler and more transparent than optimization

### B. Eliminate Kelly sizing for portfolio strategies (rejected)

Could use fixed-fraction sizing directly (skip Kelly entirely). Rejected because:
- Kelly provides vol-scaling that's valuable in high-vol regimes
- The issue isn't Kelly itself, it's the parameter translation
- Other strategies benefit from the Kelly pipeline

### C. Lower vol_floor globally (rejected)

Could lower `vol_floor` from 0.005 to 0.001 to reduce max vol_adj. Rejected because:
- `vol_floor` is a safety parameter protecting against division-by-near-zero
- The fix should be in the strategy parameters, not the engine safety rails
- Lowering vol_floor creates new risks for other strategies

## Impact

| File | Change |
|------|--------|
| `tools/verify_sizing.py` | New — sizing verification script |
| `strategies/TEMPLATE.py` | Add `RESEARCH_TARGET_SIZES` dict, empty `DD_SCALING` default |
| `.claude/skills/strategy/SKILL.md` | Add sizing verification to Gate 3 checklist |
| `.claude/hooks/strategy-gate-guard.sh` | Check for sizing_verification.json at Gate 3 |

## Risk

Low. All changes are additive:
- New verification script doesn't modify existing behavior
- `RESEARCH_TARGET_SIZES` is documentation only (not read by engine)
- DD scaling default change only affects new strategies (existing ones keep their config)
- Gate integration adds a check, doesn't remove any existing gate

The cost of NOT doing this is high — every future strategy port risks the same catastrophic parameter misconfiguration that destroyed s401.

## Change Log

- 2026-03-28: Initial proposal based on s400/s401 vol_adj blowup post-mortem
