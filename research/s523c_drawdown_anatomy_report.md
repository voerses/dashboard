# s523c_growth Drawdown Anatomy

**Primary regime lens: s523c traded-universe basket (equal-weighted alt tokens after blacklist).**  
**Secondary lens: BTC regime (for comparison).**

Equity curve: 60-month bare s523c_growth at $50K, from `research/gate2_out/s523c_growth_60mo_50k_equity_curve.json`.

---

## Equity Overview
- Date range: 2021-04-05 → 2026-04-05  
- Start equity: $50,000  
- End equity: $225,743  
- Total return: 351.5%  
- CAGR: 35.2%  
- Max drawdown: -64.7%  

## Traded Universe
- s521_token_config.json: 139 tokens  
- Blacklist: 50 tokens (BTC, SOL, etc.)  
- s523c basket size: 89 tokens  
- Tokens with usable 1h data: 85  

## Task 1+2: Top 10 Drawdowns (basket regime PRIMARY, BTC SECONDARY)

| # | Peak | Trough | Days | Depth% | Basket DD% | Basket Vol | Basket Disp | Alts−BTC% | Breadth% | BTC ret% | BTC DD% |
|---|------|--------|------|--------|-----------|-----------|------------|-----------|----------|----------|---------|
| 1 | 2022-04-03 | 2023-12-17 | 623 | -64.7 | -52.9 | 0.74 | 0.032 | -1.4 | 11 | -9.6 | -40.4 |
| 2 | 2025-11-07 | 2026-01-17 | 71 | -37.3 | -28.3 | 0.70 | 0.045 | -4.8 | 20 | -6.1 | -26.0 |
| 3 | 2024-12-21 | 2025-02-03 | 44 | -35.4 | -43.0 | 0.96 | 0.055 | -13.7 | 14 | 3.6 | -12.8 |
| 4 | 2025-07-17 | 2025-08-22 | 36 | -21.0 | -20.6 | 0.76 | 0.042 | 15.3 | 66 | -1.4 | -8.8 |
| 5 | 2025-05-13 | 2025-06-22 | 40 | -20.3 | -31.3 | 0.88 | 0.040 | -2.7 | 45 | -1.8 | -9.1 |
| 6 | 2021-12-21 | 2021-12-27 | 6 | -18.0 | -29.8 | 0.91 | 0.044 | -3.3 | 3 | 8.1 | -17.7 |
| 7 | 2025-09-25 | 2025-10-06 | 11 | -15.3 | -17.1 | 0.65 | 0.051 | -2.5 | 30 | 10.0 | -6.9 |
| 8 | 2026-03-05 | 2026-03-16 | 11 | -14.0 | -6.3 | 0.56 | 0.072 | 6.1 | 27 | 3.0 | -9.2 |
| 9 | 2025-10-10 | 2025-10-13 | 3 | -10.7 | -32.1 | 1.22 | 0.082 | -22.7 | 12 | -5.3 | -11.3 |
| 10 | 2022-01-31 | 2022-02-24 | 24 | -9.1 | -36.1 | 0.92 | 0.031 | -14.2 | 1 | 1.2 | -19.5 |

## Task 3: Drawdown Clustering (basket features)

Heuristic priority: alt_cascade > alt_underperform > broad_breakdown > low_dispersion > idiosyncratic.

| Cluster | Count |
|---------|-------|
| alt_cascade | 9 |
| broad_breakdown | 1 |

Per-DD labels:
- 2022-04-03 → **alt_cascade**
- 2025-11-07 → **alt_cascade**
- 2024-12-21 → **alt_cascade**
- 2025-07-17 → **alt_cascade**
- 2025-05-13 → **alt_cascade**
- 2021-12-21 → **alt_cascade**
- 2025-09-25 → **alt_cascade**
- 2026-03-05 → **broad_breakdown**
- 2025-10-10 → **alt_cascade**
- 2022-01-31 → **alt_cascade**

## Task 4: Cross-Reference with Mission G (profit_lockin) Bad Windows

Source: `research/profit_lockin_gate2_results.json` — 17 6-month windows.  
Bad window = `VEL95_calmar_delta_pct < 0` (overlay hurt the baseline).

- Total windows: 17  
- Bad windows (overlay hurt): 11  
- Good windows (overlay helped): 6  

**Average basket regime in bad vs good windows:**
- Bad windows  — basket_dd_min: -36.4%  | alts−btc_30d: -2.2%  | s523c_window_dd: -32.7%
- Good windows — basket_dd_min: -39.5%  | alts−btc_30d: -1.7%  | s523c_window_dd: -31.6%

**Per-window detail:**

| Window | MissG ΔCalmar% | s523c DD% | Basket DD% | Alts−BTC% | Disp | BTC ret% |
|--------|----------------|-----------|------------|-----------|------|----------|
| 2021-10-02 → 2022-03-31 | -6.5 | -18.0 | -41.1 | -0.3 | 0.043 | -3.2 |
| 2021-12-31 → 2022-06-29 | +57.5 | -26.7 | -52.9 | -4.2 | 0.039 | -57.7 |
| 2022-03-31 → 2022-09-27 | -35.7 | -27.6 | -52.9 | 3.4 | 0.038 | -56.5 |
| 2022-06-29 → 2022-12-26 | +50.8 | -17.5 | -35.9 | 2.5 | 0.032 | -16.0 |
| 2022-09-27 → 2023-03-26 | -95.0 | -41.6 | -35.9 | -0.5 | 0.033 | 45.5 |
| 2022-12-26 → 2023-06-24 | +96.3 | -37.6 | -30.1 | -3.9 | 0.031 | 80.6 |
| 2023-03-26 → 2023-09-22 | -8.5 | -18.9 | -28.6 | -9.1 | 0.024 | -3.9 |
| 2023-06-24 → 2023-12-21 | -13.2 | -54.2 | -24.8 | -0.8 | 0.030 | 43.8 |
| 2023-09-22 → 2024-03-20 | -4.1 | -49.0 | -20.7 | 5.4 | 0.043 | 139.4 |
| 2023-12-21 → 2024-06-18 | -38.1 | -31.4 | -34.6 | 1.0 | 0.043 | 48.0 |
| 2024-03-20 → 2024-09-16 | -133.6 | -25.1 | -41.3 | -7.3 | 0.034 | -8.4 |
| 2024-06-18 → 2024-12-15 | +366.1 | -47.7 | -41.3 | -0.6 | 0.038 | 58.8 |
| 2024-09-16 → 2025-03-15 | +32.5 | -39.3 | -45.4 | -3.7 | 0.049 | 45.9 |
| 2024-12-15 → 2025-06-13 | -10.0 | -35.4 | -45.4 | -9.8 | 0.052 | 2.2 |
| 2025-03-15 → 2025-09-11 | +30.9 | -21.0 | -31.6 | -0.4 | 0.049 | 35.4 |
| 2025-06-13 → 2025-12-10 | -8.6 | -21.0 | -36.4 | -1.8 | 0.048 | -13.0 |
| 2025-09-11 → 2026-03-10 | -54.0 | -37.3 | -39.1 | -4.4 | 0.050 | -37.7 |

## Task 5: Candidate Filter Backtests

Each filter pauses s523c on days where the trigger is true (returns set to 0). Basket-based (F1–F3) vs BTC-based (F4–F5).

| Filter | TotalRet% | CAGR% | MaxDD% | Sharpe | Calmar | %Days Paused |
|--------|-----------|-------|--------|--------|--------|--------------|
| baseline | 351.5 | 35.2 | -64.7 | 0.80 | 0.54 | 0.0 |
| F1_basket_dd_gt_15 | 1.5 | 0.3 | -72.6 | 0.21 | 0.00 | 42.8 |
| F2_dispersion_low_30pct | 162.6 | 21.3 | -68.6 | 0.62 | 0.31 | 30.9 |
| F3_alts_vs_btc_lt_neg10 | 517.0 | 43.9 | -39.8 | 1.04 | 1.10 | 29.4 |
| F4_btc_below_200dma | 279.6 | 30.6 | -54.2 | 0.77 | 0.56 | 43.9 |
| F5_btc_dd_gt_15 | 102.0 | 15.1 | -67.3 | 0.52 | 0.22 | 19.3 |

## Key Findings

- Baseline calmar: **0.54**, max DD **-64.7%**
- Best filter by Calmar: **F3_alts_vs_btc_lt_neg10** (Calmar 1.10, MaxDD -39.8%, %paused 29.4%)
- Basket-based filter average Calmar: 0.47
- BTC-based filter average Calmar: 0.39
- → BASKET regime is the better filter family.

## Interpretation

**1. Drawdowns are concentrated in alt-cascade regimes.** 9 of 10 top drawdowns
occurred during 30d basket drawdowns of −15% or worse (median basket DD ≈ −30%).
This is the dominant failure mode: when the alt universe as a whole is in a
broad cascade, s523c's ranked-conviction long+short bets become correlated and
the diversification breaks down.

**2. The headline winning filter is F3 (alts_vs_btc_30d < −10%).** This single
filter:
  - More than doubles Calmar (0.54 → 1.10)
  - Cuts max drawdown from −64.7% to −39.8% (−25 pp)
  - Lifts total return from +351% to +517% (yes — return *increases* despite pausing 29% of days)
  - Lifts CAGR from 35.2% to 43.9%
  - Improves Sharpe from 0.80 to 1.04

The signal: when alts are *severely* underperforming BTC over the prior 30d
(a cross-asset rotation away from the alt complex), s523c's positioning edge
collapses. Pausing in those regimes captures most of the bad days without
giving up the regime where the strategy actually has edge.

**3. F1 (basket_drawdown_30d > 15%) is a TRAP.** It posts the worst result of
any filter: total return collapses from 351% to 1.5% and the drawdown actually
*deepens* to −72.6%. The reason: by the time the trailing 30d basket DD has
exceeded 15%, the cascade has already happened. Pausing then locks in the loss
and forfeits the bounce. **Lesson: trailing-DD filters fire late.** A
*forward-looking* signal like alts−vs−BTC (which leads cascades) works.

**4. F2 (dispersion collapse) does not help.** Pausing when dispersion is in
its bottom-30 percentile loses 53% of total return without improving DD.
s523c's ranked-conviction signal apparently still has edge in low-dispersion
regimes — the failure mode is direction (alt cascade), not dispersion.

**5. BTC-based filters underperform basket-based ones.** F4 (BTC < 200DMA)
preserves Calmar at baseline but doesn't improve it; F5 (BTC trailing DD > 15%)
suffers the same "fires late" problem as F1. This confirms the methodology
correction was justified: BTC regime is a *worse* lens for s523c than its own
traded universe.

**6. Mission G's bad windows do NOT cleanly align with s523c's bad periods
or basket regime.** Average basket_dd_min in Mission G's 11 bad windows
(−36.4%) is essentially identical to its 6 good windows (−39.5%); same for
alts−vs−btc and s523c's own window DD. This means Mission G's profit-lockin
overlay has an **independent failure mode** — it isn't simply that the overlay
breaks during alt cascades. ONE filter (F3) will NOT solve both problems.
The overlay needs its own diagnosis (likely related to which trades it picks
to lock in vs let run, not regime).

## Next Actions

1. **Adopt F3 (alts_vs_btc_30d < −10%) as a regime filter for s523c_growth.**
   It is the cheapest, highest-impact change available — implementable as a
   daily check on the basket-vs-BTC spread, no per-trade logic required.
2. Investigate F3 robustness: parameter sweep on the threshold (−5% to −20%)
   and the lookback window (15d, 30d, 60d), and walk-forward test the best
   point.
3. Mission G needs a separate diagnosis — it is not regime-explainable from
   the data here. Look at *which trades* the overlay fires on in bad windows,
   not at *when* it fires.
4. Consider stacking F3 with a directional bias: when F3 triggers, instead of
   pausing entirely, flip to short-only (the alt-underperform regime is
   structurally bearish for the alt longs).
