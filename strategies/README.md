# Strategy Library

Each strategy is a standalone module implementing the `strategy(ctx) -> StrategyResult` interface.
V4 validation engine validates strategies via walk-forward + CPCV dual gate.

## Before Adding a Strategy

**MANDATORY:** Complete the pre-development checklist in `TEMPLATE.py`:
1. Deduplication check — verify entry signal doesn't overlap with existing strategies
2. Signal Lab IC check — verify IC > +0.02 post-ETF
3. Knowledge base check — read PERFORMANCE_PATTERNS.md + SIGNAL_DEVELOPMENT.md
4. Performance check — must be < 1ms per call on 40K bars

See `knowledge/STRATEGY_LIFECYCLE.md` for the full research-to-production pipeline.

## Current Classification (2026-03-25, post-MTM validated)

> **IMPORTANT:** All returns below are from V4 portfolio backtester with post-MTM equity
> (mark-to-market, not realized-only) and $2M sizing cap. $200K starting capital, 12-month period.

### Tier A — Paper-Trading Confirmed Positive
| # | Strategy | 12mo Return | MaxDD | Paper Status |
|---|----------|-------------|-------|-------------|
| s62 | conservative_funding_carry_v4 | +9.6% | -29.7% | +1.06% in 7 days |
| s65 | funding_carry_v4 | +4.9% | -45.8% | +2.25% in 7 days |

### Tier B — Backtest Positive, Not Yet Paper-Validated
(None currently qualify)

### Tier C — Losing Money in Post-MTM Backtest
| # | Strategy | 12mo Return | MaxDD | Notes |
|---|----------|-------------|-------|-------|
| s56 | max_leverage_momentum | -27.4% | -38.5% | Production component, losing |
| s57 | signal_timed_turbo_carry | -29.3% | -29.2% | Production component, losing |
| s59 | funding_mean_rev_v4 | -52.7% | -58.8% | |
| s60 | momentum_burst_perp_v4 | -70.5% | -81.3% | Paper: -25.8% |
| s63 | vol_spike_reversal_v4 | -78.9% | -81.7% | Paper: 0% win rate |
| s69 | s56_time_trail | -27.4% | -38.5% | Overlay on s56 |
| s72 | s65_time_trail | +4.9% | -45.8% | Same as s65 (overlay neutral) |
| s75 | s63_fixed_tp | -83.9% | -85.7% | |
| s76 | s56_partial_tp | -27.9% | -38.4% | |
| s80 | xsec_momentum | N/A | N/A | Paper only, losing |
| s81 | sector_rotation | N/A | N/A | Paper only, losing |

### V3 Legacy — No Post-MTM V4 Validation
These were tested on V3 engine with uncapped compounding and realized-only equity.
All V3 performance numbers are unreliable. Do NOT cite V3 returns as realistic.
- s09, s11, s13, s17, s18, s21 (spot strategies — all lose money Jan-Mar 2026)
- s28, s29, s30, s32, s37, s39, s40, s41 (V3 perp — never re-validated in V4)
- s44, s49, s51, s54 (V3 carry/momentum — never re-validated)

### Tier D — Archived (< 20% validation rate)
Moved to `archive/`. See `archive/README.md` for details.
- s07_rsi_bounce (10.2%)
- s08_obv_divergence (6.1%)
- s16_composite_factor (0.0%)
- s19_mean_reversion_filtered (0.0%)

## Standard Interface

```python
def strategy(ctx: StrategyContext) -> StrategyResult:
    """Signal stack: Regime → Trend → Entry → Volume"""
```

## How to Add a New Strategy

1. Complete the pre-development checklist in `TEMPLATE.py`
2. Copy template: `cp TEMPLATE.py sNN_name.py`
3. Follow the gate process in `knowledge/STRATEGY_QUICK_REFERENCE.md`
4. Quick validate: `python v4/validation.py --strategy sNN --tokens BTC --workers 1`
5. Full validate: `python v4/validation.py --strategy sNN --workers 4`
6. Paper trade for 50+ trades before production

## Strategy Numbering

| Range | Category |
|-------|----------|
| s01–s06 | Legacy V1 (not in current system) |
| s07–s22 | V2/V3 signal lab batches |
| s25–s54 | V3 perp/carry strategies |
| s56–s81 | V4 production strategies |
| s85–s98 | V4 experimental (overlays, MACD, etc.) |
| s100–s120 | Auto-research batch (all killed) |
| s300+ | Research strategies (s316-s320) |

Never reuse an archived strategy number.
