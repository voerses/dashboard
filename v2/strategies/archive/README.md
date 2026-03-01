# Archived Strategies (Tier C)

Strategies in this directory failed V3 dual-gate validation with < 20% rate.
They are preserved for reference but excluded from production sweeps.

## Archive Log

| Strategy | Rate | Archive Date | Reason |
|----------|------|-------------|--------|
| s07_rsi_bounce | 10.2% | 2026-03-01 | RSI alone insufficient post-ETF |
| s08_obv_divergence | 6.1% | 2026-03-01 | OBV divergence doesn't predict crypto moves |
| s16_composite_factor | 0.0% | 2026-03-01 | Too many weak signals combined = noise |
| s19_mean_reversion_filtered | 0.0% | 2026-03-01 | Mean reversion loses money even with heavy filters |

## Resurrection Criteria

An archived strategy can be resurrected if:
- New Signal Lab data shows the underlying signal has strengthened (IC improved)
- Market structure has changed (new ETF, regime shift)
- A fundamentally different composition approach is identified

To resurrect: copy back to `v2/strategies/`, re-validate with V3, classify into tier.
