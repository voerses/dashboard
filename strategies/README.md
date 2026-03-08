# Strategy Library

Each strategy is a standalone module implementing the `strategy(ctx) -> StrategyResult` interface.
The V3 validation engine validates strategies via walk-forward + CPCV dual gate.

## Before Adding a Strategy

**MANDATORY:** Complete the pre-development checklist in `TEMPLATE.py`:
1. Deduplication check — verify entry signal doesn't overlap with existing strategies
2. Signal Lab IC check — verify IC > +0.02 post-ETF
3. Knowledge base check — read PERFORMANCE_PATTERNS.md + SIGNAL_DEVELOPMENT.md
4. Performance check — must be < 1ms per call on 40K bars

See `knowledge/STRATEGY_LIFECYCLE.md` for the full research-to-production pipeline.

## Current Classification (2026-03-01 sweep)

### Tier A — Production (> 50% validation rate)
| # | Strategy | Rate | Core Edge |
|---|----------|------|-----------|
| s11 | momentum_burst | 75.5% | 3% hourly return + ADX + volume |
| s09 | optimized_trend | 73.5% | Multi-TF dual momentum |
| s13 | vol_weighted_tsmom | 67.3% | Volume-weighted time-series momentum |
| s21 | skew_momentum | 63.3% | Return skew + momentum acceleration |
| s17 | trend_strength_filter | 55.1% | ADX gradient + EMA alignment |
| s18 | momentum_accel | 51.0% | Momentum acceleration (change in momentum) |

### Tier B — Experimental (20-50% validation rate)
| # | Strategy | Rate | Status |
|---|----------|------|--------|
| s20 | low_beta_quality | 46.9% | Near promotion threshold |
| s22 | supertrend_adx | 46.9% | Supertrend flip timing noisy |
| s12 | quality_breakout | 32.7% | BB squeeze too rare on most tokens |
| s15 | vol_regime_breakout | 30.6% | Vol regime detection needs work |
| s14 | microstructure_edge | 26.5% | Spread estimator works on liquid only |
| s10 | research_dip_buy | 24.5% | Dip-buy timing inconsistent |

### Tier C — Archived (< 20% validation rate)
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
2. Copy template: `cp TEMPLATE.py sNN_name.py` (next available: s23)
3. Follow the 4-layer signal stack (Regime → Trend → Entry → Volume)
4. Profile: must be < 1ms per call
5. Quick validate: `python v3/validation.py --strategy sNN --tokens BTC --workers 1`
6. Full validate: `python v3/validation.py --strategy sNN --workers 4`
7. Classify into tier based on validation rate

## Strategy Numbering

| Range | Category |
|-------|----------|
| s01–s06 | Legacy V1 (not in current system) |
| s07–s10 | V2 initial batch |
| s11–s16 | Signal Lab batch 1 |
| s17–s22 | Signal Lab batch 2 (post-ETF) |
| s23+ | Future (next available: s23) |

Never reuse an archived strategy number.

## Future Ideas

Position sizing improvements (currently all strategies use flat equal-weight):
- **Volatility-scaled sizing** — scale position size inversely with realized vol (e.g. ATR-based)
- **Signal conviction sizing** — weight by signal strength (basis magnitude, IC score, regime confidence)
- **Risk parity across legs** — equalize risk contribution between spot and perp legs instead of equal USD
- **Kelly criterion sizing** — size based on estimated edge and variance from backtest statistics
- **Regime-adaptive sizing** — reduce exposure in CRISIS/DOWNTREND, increase in UPTREND/RANGE
