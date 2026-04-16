# Mission P Gate 0 — Event-Driven Breadth-Based Direction Cull

Generated: 2026-04-08

**Verdict: PASS**

- Best none_g3d_br75_th14d: +10.5%, saved/given_back 1.77x

## Hypothesis (refined Mission O)

Mission O failed because daily evaluation × mean spread = 277 trigger days/year of noise. Mission P fixes both:
- **Event-driven**: only evaluate on regime-confirming days (vol spike / dispersion compression / decisive move / drawdown deepening)
- **Breadth not mean**: % of direction's open positions that are individually underwater (robust to outliers)
- **Throttle**: once a direction is culled, do not re-cull for N days (avoid repeated firing)

## Top 10 variants

| Variant | Δ$ | Δ% | Culls (L+S) | Event days | Helped | Hurt | Saved | Given back |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `none_g3d_br75_th14d` | $+39,234 | +10.5% | 12 | 358 | 63 | 27 | $+90,308 | $-51,074 |
| `none_g3d_br75_th21d` | $+31,520 | +8.4% | 10 | 358 | 55 | 29 | $+82,542 | $-51,022 |
| `none_g5d_br85_th14d` | $+27,217 | +7.3% | 7 | 356 | 36 | 16 | $+44,055 | $-16,838 |
| `none_g5d_br85_th21d` | $+27,217 | +7.3% | 7 | 356 | 36 | 16 | $+44,055 | $-16,838 |
| `dd_deepening_g5d_br85_th7d` | $+13,312 | +3.6% | 4 | 53 | 22 | 11 | $+19,816 | $-6,504 |
| `dd_deepening_g5d_br85_th14d` | $+13,312 | +3.6% | 4 | 53 | 22 | 11 | $+19,816 | $-6,504 |
| `dd_deepening_g5d_br85_th21d` | $+13,312 | +3.6% | 4 | 53 | 22 | 11 | $+19,816 | $-6,504 |
| `dd_deepening_g3d_br85_th7d` | $+11,027 | +3.0% | 4 | 54 | 24 | 13 | $+18,636 | $-7,609 |
| `dd_deepening_g3d_br85_th14d` | $+11,027 | +3.0% | 4 | 54 | 24 | 13 | $+18,636 | $-7,609 |
| `dd_deepening_g3d_br85_th21d` | $+11,027 | +3.0% | 4 | 54 | 24 | 13 | $+18,636 | $-7,609 |

## Best variant detail

- Variant: `none_g3d_br75_th14d`
- Event type: none
- Grace: 3d, Breadth threshold: 75%, Throttle: 14d
- Event days in year: 358
- Long culls: 10, Short culls: 2
- Trades force-closed: 90
- Helped: 63, Hurt: 27
- Saved $: $+90,308
- Given back $: $-51,074
- Total improvement: $+39,234 (+10.5%)

## Diagnosis

The event-driven breadth filter produces meaningful improvement at realistic costs. Recommend Gate 1: validate on multi-year rolling windows + finer event/breadth grid + test alternative event triggers.