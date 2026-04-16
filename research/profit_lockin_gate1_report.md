# Profit Lock-in Overlay — Gate 1 Report

_Generated 2026-04-07T15:41:48.658415+00:00_

Post-hoc overlay study: does locking in profits when a trade becomes unusually
profitable unusually fast improve Calmar? No changes to v4/ or strategies/.
MFE reconstructed from OHLC + entry_bar/exit_bar; leverage inferred from
gross pnl / margin / realized_price_return per trade.

## Baselines

| Strategy | Total Return % | Sharpe | MaxDD % | Calmar | Trades (enriched) | Missing |
|----------|---------------:|-------:|--------:|-------:|------------------:|--------:|
| s523c | 662.20 | 2.89 | -35.61 | 18.60 | 575 | 59 |
| s513 | 208.29 | 2.76 | -12.47 | 16.71 | 179 | 16 |

Note: 'reconstructed' baselines are built by summing per-trade pnl into daily buckets,
so they differ modestly from the portfolio simulator's own equity/Sharpe/Calmar which
track margin utilisation intrabar. Absolute numbers: s523c official Calmar 19.4 (37% DD),
s513 official Calmar 12.9 (16.7% DD). Overlay *deltas* are the meaningful signal.

## Phase 1 Diagnostic — s523c

Total trades: 575, winners (positive MFE): 562

### Give-back ratio (max_unrealized − realized) / max_unrealized

| stat | value |
|------|------:|
| n | 562 |
| mean | 0.751 |
| p25 | 0.455 |
| p50 | 1.000 |
| p75 | 1.000 |
| p90 | 1.000 |

### Velocity (MFE price-move fraction per bar, winners only)

| stat | value |
|------|------:|
| mean | 0.00367 |
| p25 | 0.00060 |
| p50 | 0.00109 |
| p75 | 0.00281 |
| p90 | 0.00725 |
| p95 | 0.01330 |

### Time to peak (bars from entry to MFE)

| stat | bars | frac of hold |
|------|-----:|-------------:|
| mean | 214.2 | 0.491 |
| p25 | 39.2 | 0.194 |
| p50 | 151.0 | 0.490 |
| p75 | 333.2 | 0.779 |
| p90 | 566.9 | 0.946 |

### Outsized-move frequency (unlevered price-move MFE)

| threshold (price move) | % of trades reached | n | % of hit trades exit positive |
|-----------------------:|--------------------:|---:|------------------------------:|
| 2% | 88.7% | 510 | 47.5% |
| 5% | 76.9% | 442 | 53.6% |
| 8% | 66.3% | 381 | 60.4% |
| 10% | 60.2% | 346 | 65.6% |
| 15% | 49.4% | 284 | 74.3% |
| 20% | 41.0% | 236 | 79.7% |

## Phase 1 Diagnostic — s513

Total trades: 179, winners (positive MFE): 179

### Give-back ratio (max_unrealized − realized) / max_unrealized

| stat | value |
|------|------:|
| n | 179 |
| mean | 0.695 |
| p25 | 0.372 |
| p50 | 0.799 |
| p75 | 1.000 |
| p90 | 1.000 |

### Velocity (MFE price-move fraction per bar, winners only)

| stat | value |
|------|------:|
| mean | 0.00290 |
| p25 | 0.00090 |
| p50 | 0.00149 |
| p75 | 0.00374 |
| p90 | 0.00666 |
| p95 | 0.00878 |

### Time to peak (bars from entry to MFE)

| stat | bars | frac of hold |
|------|-----:|-------------:|
| mean | 43.5 | 0.563 |
| p25 | 15.5 | 0.212 |
| p50 | 47.0 | 0.644 |
| p75 | 67.5 | 0.903 |
| p90 | 77.0 | 0.951 |

### Outsized-move frequency (unlevered price-move MFE)

| threshold (price move) | % of trades reached | n | % of hit trades exit positive |
|-----------------------:|--------------------:|---:|------------------------------:|
| 2% | 74.9% | 134 | 75.4% |
| 5% | 57.0% | 102 | 86.3% |
| 8% | 35.8% | 64 | 92.2% |
| 10% | 27.4% | 49 | 93.9% |
| 15% | 15.6% | 28 | 96.4% |
| 20% | 11.2% | 20 | 95.0% |

## Phase 2 Rule Sweep — s523c

Top 10 (trigger × action) combos sorted by Calmar improvement % vs reconstructed baseline.
Calmar delta < 0 means worse than baseline. Return ratio = new_return / baseline_return.

| Trigger | Action | Fired | Return % | Sharpe | MaxDD % | Calmar | ΔCalmar % | Ret Ratio |
|---------|--------|------:|---------:|-------:|--------:|-------:|----------:|----------:|
| VEL95 | FULL | 148 | 927.9 | 3.54 | -18.72 | 49.56 | +166.5 | 1.40 |
| VEL95 | 25_tight | 148 | 876.1 | 3.36 | -20.11 | 43.57 | +134.3 | 1.32 |
| VEL95 | 75_1ATR | 148 | 883.3 | 3.48 | -20.47 | 43.16 | +132.1 | 1.33 |
| VEL95 | 50BE | 148 | 795.8 | 3.25 | -24.05 | 33.08 | +77.9 | 1.20 |
| TIME12_8b | 75_1ATR | 97 | 747.0 | 3.16 | -28.86 | 25.88 | +39.2 | 1.13 |
| TIME12_8b | FULL | 97 | 736.4 | 3.14 | -28.75 | 25.61 | +37.7 | 1.11 |
| VEL95 | HALFLOCK | 148 | 682.9 | 3.06 | -26.72 | 25.56 | +37.4 | 1.03 |
| VEL90 | 75_1ATR | 257 | 636.0 | 2.76 | -24.92 | 25.52 | +37.2 | 0.96 |
| TIME12_8b | 50BE | 97 | 741.4 | 3.13 | -29.77 | 24.90 | +33.9 | 1.12 |
| VEL90 | FULL | 257 | 623.8 | 2.70 | -25.10 | 24.86 | +33.7 | 0.94 |

### Best combo for s523c: `VEL95 × FULL`

| Metric | Baseline | Overlay | Delta |
|--------|---------:|--------:|------:|
| Total Return % | 662.20 | 927.89 | +265.69 |
| Sharpe | 2.89 | 3.54 | +0.65 |
| MaxDD % | -35.61 | -18.72 | +16.89 |
| Calmar | 18.60 | 49.56 | +30.97 |
| Trades fired | — | 148 | — |

## Phase 2 Rule Sweep — s513

Top 10 (trigger × action) combos sorted by Calmar improvement % vs reconstructed baseline.
Calmar delta < 0 means worse than baseline. Return ratio = new_return / baseline_return.

| Trigger | Action | Fired | Return % | Sharpe | MaxDD % | Calmar | ΔCalmar % | Ret Ratio |
|---------|--------|------:|---------:|-------:|--------:|-------:|----------:|----------:|
| ABS20 | HALFLOCK | 86 | 188.0 | 2.74 | -12.41 | 15.14 | -9.4 | 0.90 |
| ATR4 | HALFLOCK | 116 | 178.4 | 3.22 | -11.79 | 15.13 | -9.5 | 0.86 |
| ATR4 | 50BE | 116 | 181.1 | 3.44 | -12.18 | 14.87 | -11.0 | 0.87 |
| ATR5 | 50BE | 95 | 175.0 | 3.03 | -13.05 | 13.41 | -19.7 | 0.84 |
| TIME8_8b | 50BE | 47 | 160.0 | 2.73 | -12.49 | 12.81 | -23.3 | 0.77 |
| ATR6 | 50BE | 83 | 176.9 | 2.98 | -13.83 | 12.79 | -23.4 | 0.85 |
| ATR3 | 50BE | 129 | 151.5 | 3.32 | -11.89 | 12.75 | -23.7 | 0.73 |
| ATR4 | 25_tight | 116 | 160.7 | 3.57 | -12.68 | 12.68 | -24.1 | 0.77 |
| ATR3 | HALFLOCK | 129 | 139.6 | 2.72 | -11.02 | 12.66 | -24.2 | 0.67 |
| ATR4 | 75_1ATR | 116 | 158.1 | 3.48 | -12.49 | 12.65 | -24.3 | 0.76 |

### Best combo for s513: `ABS20 × HALFLOCK`

| Metric | Baseline | Overlay | Delta |
|--------|---------:|--------:|------:|
| Total Return % | 208.29 | 187.97 | -20.32 |
| Sharpe | 2.76 | 2.74 | -0.02 |
| MaxDD % | -12.47 | -12.41 | +0.05 |
| Calmar | 16.71 | 15.14 | -1.56 |
| Trades fired | — | 86 | — |

## Verdict

**PASS**

- s523c best: `VEL95 × FULL` → Calmar Δ +166.5%, return ratio 1.40, DD Δ +16.89pp
- s513  best: `ABS20 × HALFLOCK` → Calmar Δ -9.4%, return ratio 0.90, DD Δ +0.05pp

### Gate 2 dedup check

Before promoting: verify the lock-in trigger does not double-fire with existing
exit handlers in `v4/`:

- `BreakevenRatchet`: moves stop to breakeven after X R multiple. A-50/A-HALFLOCK
  conflict directly — if ratchet is already active at X=1R and overlay fires on ABS5,
  the stop-to-BE would be applied twice.
- `Trailing` stop handler: A-25 (tighten trail to 1×ATR) would override the default
  5×ATR trail. Verify per-strategy whether overlay trail is strictly tighter.
- Max-hold exit: untouched (overlay fires earlier).

### Gate 3 prototype path

Add a new exit handler `v4/exits/profit_lockin.py` implementing the winning combo as a
per-position on_bar hook. Mount in the s523c_growth strategy config behind a feature
flag. Run full walk-forward before any paper-trade wiring.
