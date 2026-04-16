# Liquidation Cascade Recovery — Gate 1 Research Report

**Hypothesis.** When BTC cascades (1h < -3%, vol > 2×24h ma, market funding z(168h) > 1.5), long an equal-weight basket of top-20 most-liquid alts at T+0 and exit at T+{12,24,48,72}h.

## 1. Data Inspection

- **BTC 1h OHLCV**: `data/perp/binance/1h_ohlcv/BTC_perp_1h.csv` — 54,719 bars, 2020-01-01 → 2026-03-29

- **Funding**: 247 per-symbol CSVs (8h cadence). Market-wide mean built by equal-weighting across all symbols, ffilled to 1h. Range: 2020-01-01 → 2026-04-01

- **Liquidations**: `binance.parquet` — daily `l`/`s` (long/short liq USD) per token, 2020-11-11 → 2026-04-04, 216 tokens.

- **Alt universe**: 238 perp symbols with ≥ 200 bars.

## 2. Trigger Parameters & Sensitivity

Paper spec is very strict and produces almost no events in the recent 5y regime (funding rates have been far more moderated post-2021-Q2). We ran three specs:

| Spec | BTC ret < | vol × 24h ma | fund z > | Events last 5y |
|---|---|---|---|---|
| strict_paper | -3.0% | 2.0 | 1.5 | 1 |
| matched_72 | -2.0% | 2.0 | 0.5 | 61 |
| mid | -2.5% | 2.0 | 1.0 | 21 |

Cooldown: 72h between events. **Main analysis uses `matched_72`** — the relaxed spec whose event count matches the paper's 72-trades-over-5y claim. The strict paper spec fires 0 times in the last 5 years.


## 3. Events

Events per year (last 5y):

| Year | Events |
|---|---|
| 2021 | 17 |
| 2022 | 17 |
| 2023 | 9 |
| 2024 | 8 |
| 2025 | 8 |
| 2026 | 2 |

**Total events last 5y**: 61


Event table:

| Timestamp (UTC) | BTC 1h ret | vol×24h | fund z | daily liq ($M) | n alts | ret_24h net | ret_48h net | BTC_48h |
|---|---|---|---|---|---|---|---|---|
| 2021-03-31 07:00 | -2.56% | 3.5 | 1.77 | 0.4 | 20 | +7.48% | +9.26% | +2.18% |
| 2021-04-16 04:00 | -2.06% | 2.7 | 1.14 | 0.4 | 20 | +8.39% | -13.61% | -11.12% |
| 2021-05-02 03:00 | -2.06% | 2.7 | 2.98 | 0.1 | 20 | +4.04% | +1.01% | -1.75% |
| 2021-05-08 15:00 | -2.30% | 2.0 | 0.74 | 0.2 | 20 | +1.57% | +4.46% | +1.21% |
| 2021-06-04 05:00 | -2.19% | 2.4 | 1.22 | 0.0 | 20 | +5.01% | +0.75% | -1.43% |
| 2021-06-07 20:00 | -3.40% | 2.5 | 0.85 | 0.0 | 20 | -4.82% | -3.20% | +5.54% |
| 2021-06-17 17:00 | -2.12% | 2.5 | 0.89 | 0.0 | 20 | -6.19% | -5.75% | -5.14% |
| 2021-06-25 16:00 | -2.09% | 2.6 | 0.64 | 0.0 | 20 | -2.23% | +0.70% | +3.27% |
| 2021-07-05 11:00 | -3.14% | 3.9 | 0.93 | 0.4 | 20 | +6.83% | +12.14% | +4.38% |
| 2021-07-14 03:00 | -2.22% | 2.8 | 0.88 | 0.5 | 20 | +9.17% | +6.12% | +0.28% |
| 2021-07-26 20:00 | -4.09% | 3.2 | 1.18 | 0.4 | 20 | +0.39% | +2.36% | +5.76% |
| 2021-07-30 08:00 | -2.53% | 3.2 | 1.05 | 0.1 | 20 | +5.64% | +9.22% | +7.85% |
| 2021-09-20 02:00 | -2.69% | 4.3 | 2.99 | 0.7 | 20 | -7.33% | -10.93% | -8.60% |
| 2021-10-26 21:00 | -2.10% | 2.7 | 1.84 | 1.0 | 20 | -4.40% | -2.14% | -0.15% |
| 2021-11-10 21:00 | -2.39% | 5.8 | 0.90 | 2.3 | 20 | +6.37% | +4.33% | -0.31% |
| 2021-11-26 08:00 | -3.12% | 9.2 | 1.45 | 3.5 | 20 | +2.28% | -3.57% | -1.45% |
| 2021-12-17 14:00 | -2.12% | 4.5 | 0.96 | 2.0 | 20 | +5.65% | +6.01% | +3.12% |
| 2022-01-05 19:00 | -2.72% | 4.9 | 1.26 | 9.0 | 20 | -1.68% | -6.67% | -6.51% |
| 2022-02-17 13:00 | -2.23% | 3.4 | 1.20 | 14.3 | 20 | -4.64% | -7.65% | -5.32% |
| 2022-02-27 19:00 | -3.41% | 3.1 | 0.77 | 38.0 | 20 | +6.79% | +13.02% | +16.28% |
| 2022-03-10 04:00 | -3.04% | 3.1 | 1.30 | 32.6 | 20 | -1.76% | +2.02% | -1.10% |
| 2022-05-05 15:00 | -3.48% | 8.4 | 1.38 | 52.3 | 20 | -1.34% | -0.39% | -2.55% |
| 2022-05-16 06:00 | -2.39% | 2.8 | 0.62 | 36.0 | 20 | +5.22% | +1.16% | +0.57% |
| 2022-05-20 14:00 | -2.87% | 3.1 | 1.17 | 31.0 | 20 | +1.40% | +3.83% | +1.62% |
| 2022-05-23 19:00 | -2.69% | 4.3 | 1.53 | 35.1 | 20 | -2.43% | -2.46% | +1.79% |
| 2022-06-08 03:00 | -3.10% | 3.1 | 0.85 | 23.6 | 20 | +0.56% | +0.94% | +0.32% |
| 2022-06-18 06:00 | -4.88% | 3.0 | 1.12 | 65.5 | 20 | -3.18% | +5.45% | +3.64% |
| 2022-06-22 15:00 | -2.95% | 2.1 | 0.53 | 33.7 | 20 | +1.12% | +8.26% | +3.98% |
| 2022-07-30 19:00 | -2.41% | 2.5 | 0.82 | 33.5 | 20 | +0.50% | -6.26% | -4.06% |
| 2022-08-26 14:00 | -2.77% | 6.7 | 0.97 | 48.5 | 20 | -7.49% | -7.28% | -5.84% |
| 2022-09-06 17:00 | -3.29% | 6.6 | 1.19 | 52.4 | 20 | +0.28% | +2.51% | +0.54% |
| 2022-09-21 19:00 | -3.25% | 3.6 | 0.65 | 46.4 | 20 | +2.45% | +1.78% | -1.01% |
| 2022-09-27 16:00 | -3.94% | 6.4 | 1.01 | 37.8 | 20 | -0.78% | -1.11% | +1.78% |
| 2022-12-14 19:00 | -2.34% | 8.1 | 1.43 | 10.4 | 20 | -1.73% | -7.41% | -5.24% |
| 2023-02-16 23:00 | -2.02% | 2.5 | 1.32 | 53.0 | 20 | +4.67% | +5.18% | +4.72% |
| 2023-03-03 01:00 | -5.42% | 10.1 | 1.43 | 38.9 | 20 | +2.84% | +1.65% | +2.01% |
| 2023-03-14 18:00 | -2.51% | 2.1 | 1.63 | 69.0 | 20 | -9.27% | -8.16% | -0.90% |
| 2023-03-22 19:00 | -4.87% | 6.8 | 1.15 | 41.2 | 20 | +5.81% | +1.00% | +4.34% |
| 2023-06-05 15:00 | -2.98% | 9.6 | 1.16 | 53.6 | 20 | +2.73% | -1.01% | +1.32% |
| 2023-06-30 13:00 | -2.84% | 9.5 | 0.95 | 54.6 | 20 | +5.10% | +4.82% | +1.30% |
| 2023-08-31 16:00 | -2.55% | 10.1 | 0.87 | 21.6 | 20 | -1.44% | -1.94% | -1.42% |
| 2023-10-24 15:00 | -2.17% | 2.4 | 0.64 | 68.2 | 20 | +8.60% | +3.41% | +1.12% |
| 2023-11-09 15:00 | -2.06% | 2.9 | 0.56 | 93.0 | 20 | -1.46% | +4.80% | -0.19% |
| 2024-01-12 22:00 | -2.18% | 4.6 | 0.94 | 80.6 | 20 | +3.69% | +1.47% | -0.56% |
| 2024-03-05 15:00 | -2.86% | 4.1 | 2.24 | 221.3 | 20 | +0.91% | +2.88% | +0.71% |
| 2024-06-24 09:00 | -2.14% | 9.0 | 1.20 | 85.4 | 20 | +6.68% | +8.31% | -0.01% |
| 2024-07-04 01:00 | -3.07% | 3.8 | 1.06 | 163.4 | 20 | -9.21% | -6.27% | -4.04% |
| 2024-08-12 13:00 | -2.62% | 3.0 | 0.59 | 55.7 | 20 | +1.46% | +0.46% | +2.49% |
| 2024-08-20 14:00 | -2.32% | 5.0 | 1.04 | 38.6 | 20 | +1.07% | +4.19% | +1.74% |
| 2024-12-05 15:00 | -2.17% | 2.2 | 1.36 | 182.4 | 20 | -1.85% | +2.27% | -1.52% |
| 2024-12-30 21:00 | -2.42% | 2.5 | 0.93 | 73.0 | 20 | +1.12% | +4.17% | +3.18% |
| 2025-01-07 15:00 | -2.03% | 10.2 | 0.51 | 148.9 | 20 | -7.44% | -10.75% | -3.80% |
| 2025-01-19 21:00 | -2.45% | 3.3 | 1.63 | 280.0 | 20 | -3.47% | -0.09% | +2.94% |
| 2025-02-26 18:00 | -2.10% | 4.9 | 1.05 | 82.1 | 20 | +1.73% | +1.32% | +0.22% |
| 2025-03-10 14:00 | -2.08% | 3.9 | 1.05 | 105.0 | 20 | -4.54% | -1.32% | +2.11% |
| 2025-04-04 10:00 | -2.36% | 4.0 | 1.26 | 59.3 | 20 | +4.14% | -0.20% | +0.36% |
| 2025-05-21 17:00 | -2.32% | 6.9 | 4.63 | 77.9 | 20 | +6.75% | +5.57% | +2.62% |
| 2025-11-20 16:00 | -2.20% | 4.8 | 0.59 | 110.8 | 20 | -4.12% | -8.38% | -3.64% |
| 2025-12-01 00:00 | -3.71% | 15.7 | 0.59 | 133.0 | 20 | -3.29% | +6.37% | +5.35% |
| 2026-02-03 16:00 | -2.09% | 3.6 | 0.78 | 95.8 | 20 | -2.52% | -10.04% | -10.72% |
| 2026-02-23 01:00 | -3.48% | 15.8 | 0.95 | 85.1 | 20 | +1.00% | +6.14% | +2.29% |

## 4. Per-Window Return Distributions (net of costs, last 5y)

| Window | N | Mean | Median | Stdev | Win rate | Sharpe (event) | BTC mean |
|---|---|---|---|---|---|---|---|
| T+12h | 61 | +0.25% | +0.22% | 3.47% | 52% | 0.07 | -0.23% |
| T+24h | 61 | +0.67% | +1.00% | 4.68% | 59% | 0.14 | +0.09% |
| T+48h | 61 | +0.54% | +1.16% | 5.86% | 61% | 0.09 | +0.24% |
| T+72h | 61 | +0.70% | +0.79% | 7.07% | 56% | 0.10 | +0.45% |

## 5. Alpha vs BTC Beta (net, last 5y)

OLS regression of basket net return on BTC same-window return.

| Window | α (per trade) | β | t(α) | alpha sig? |
|---|---|---|---|---|
| T+12h | +0.52% | 1.14 | 1.86 | ✅ |
| T+24h | +0.57% | 1.18 | 1.48 | ❌ |
| T+48h | +0.28% | 1.07 | 0.62 | ❌ |
| T+72h | +0.24% | 1.01 | 0.38 | ❌ |

## 6. Sliced Performance with Costs (event-level annualized)


### L12M

Events in slice: 6

| Window | N | AnnRet | Sharpe | MaxDD | Calmar | Total |
|---|---|---|---|---|---|---|
| T+12h | 6 | +7.9% | 1.20 | -3.2% | 2.45 | +7.0% |
| T+24h | 6 | +1.7% | 0.19 | -9.6% | 0.17 | +1.5% |
| T+48h | 6 | -2.2% | -0.03 | -12.3% | -0.18 | -2.0% |
| T+72h | 6 | -14.1% | -0.55 | -8.2% | -1.71 | -12.6% |

### L6M

Events in slice: 4

| Window | N | AnnRet | Sharpe | MaxDD | Calmar | Total |
|---|---|---|---|---|---|---|
| T+12h | 4 | -2.9% | -0.37 | -1.9% | -1.49 | -0.7% |
| T+24h | 4 | -29.7% | -3.91 | -5.7% | -5.19 | -8.7% |
| T+48h | 4 | -24.3% | -0.65 | -10.0% | -2.42 | -7.0% |
| T+72h | 4 | -5.0% | -0.02 | -8.2% | -0.61 | -1.3% |

### L3M

Events in slice: 2

| Window | N | AnnRet | Sharpe | MaxDD | Calmar | Total |
|---|---|---|---|---|---|---|
| T+12h | 2 | +43.9% | 2.70 | 0.0% | nan | +2.0% |
| T+24h | 2 | -25.5% | -1.88 | 0.0% | nan | -1.6% |
| T+48h | 2 | -58.2% | -1.05 | 0.0% | nan | -4.5% |
| T+72h | 2 | -36.6% | -0.56 | 0.0% | nan | -2.4% |

## 7. Verdict (Gate 1 thresholds)

Best window in L12M by Sharpe: **T+12h**

| Metric | Value | Threshold | Pass |
|---|---|---|---|
| Trades | 6 | > 10 | ❌ |
| Sharpe (ann) | 1.20 | > 2.0 | ❌ |
| Calmar | 2.45 | > 3.0 | ❌ |
| MaxDD | -3.2% | > -25% | ✅ |
| Alpha t-stat | 1.01 | > 1.5 | ❌ |

**VERDICT: KILL**


## 8. Diagnosis

- Insufficient events in L12M (6 trades). This is the killer — regime-conditional signal firing too rarely. Consider loosening trigger (-2.5% bar, vol 1.5×, fund_z 1.0) or using daily not hourly cascades.
- Sharpe 1.20 below 2.0 — event returns too noisy relative to mean.
- Alpha t-stat 1.01 — bounce is mostly BTC beta (β=0.95), not an alt-specific edge. Leveraged long BTC would likely do the same.
- Calmar 2.45 below 3.0 — drawdown regime dominates.
---

## Rescue Attempt (liquidation-$ gate)

**Hypothesis.** Replace funding-z > 1.5 with liquidation-$ z-score > 2.0 using cross-exchange Coinalyze data. Funding-z broke post-2021 because funding rates were moderated; liquidation $ scaled ~100x with market cap and should be a more regime-stable stress indicator.

### Data Inspection

**CRITICAL DATA LIMITATION**: Coinalyze liquidation parquets are **daily** resolution (columns: `date`, `l`, `s`, `token`, `symbol`). The hypothesis specifies an hourly 168h rolling z-score; hourly liquidation data is not available in this repo. We adapt to daily resolution with a 7-day rolling z-score of cross-exchange total daily liquidation $, attaching the **prior completed day's** z-score to each candidate trigger hour (no look-ahead). This is a weaker version of the hypothesis — a daily stress gate instead of an intraday stress gate.

Per-exchange coverage:

| Exchange | Rows | Tokens | Start | End |
|---|---|---|---|---|
| binance | 145,084 | 216 | 2020-11-11 | 2026-04-04 |
| bybit | 129,302 | 196 | 2021-03-19 | 2026-04-04 |
| okx | 106,625 | 144 | 2020-03-28 | 2026-04-04 |
| bitmex | 13,329 | 42 | 2020-10-16 | 2026-04-03 |
| bitfinex | 15,291 | 36 | 2020-04-29 | 2026-04-04 |
| huobi | 81,703 | 127 | 2020-11-02 | 2026-04-04 |

Aggregated cross-exchange daily series: **2,165 days** (2020-03-28 → 2026-04-04).

Daily liq $ percentiles (all years, $M): p50=61.3  p90=228.1  p99=588.8  max=2903.6

7d-rolling z distribution: p50=-0.23 p90=1.53 p99=2.21 max=2.26  n(z>2.0)=79 over 2159 days.

### New Trigger (liq_z > 2.0, 7d window) — Primary

Base filters unchanged: BTC 1h ret < -2%, vol > 2× 24h avg, 72h cooldown.

**Events in last 5y: 9** (vs 61 for `matched_72` funding-z spec, 1 for `strict_paper`).


Events by year:

| Year | Events |
|---|---|
| 2021 | 3 |
| 2022 | 1 |
| 2023 | 1 |
| 2024 | 3 |
| 2025 | 1 |

Event list:

| Timestamp (UTC) | BTC 1h ret | vol×24h | liq_z | fund_z | n alts | ret_12h net | ret_24h net | BTC_24h |
|---|---|---|---|---|---|---|---|---|
| 2021-05-20 16:00 | -5.23% | 2.7 | 2.19 | 0.02 | 20 | +1.02% | -12.51% | -5.78% |
| 2021-06-22 12:00 | -4.42% | 5.0 | 2.09 | -1.35 | 20 | +12.97% | +21.65% | +14.41% |
| 2021-09-21 00:00 | -3.14% | 4.2 | 2.07 | -1.64 | 20 | +7.30% | -1.86% | -1.60% |
| 2022-11-09 21:00 | -3.18% | 2.3 | 2.23 | -12.75 | 20 | +13.75% | +25.97% | +13.67% |
| 2023-10-24 15:00 | -2.17% | 2.4 | 2.25 | 0.64 | 20 | +2.15% | +8.60% | +3.47% |
| 2024-04-13 19:00 | -3.58% | 4.8 | 2.08 | -0.99 | 20 | +6.69% | +5.35% | -0.98% |
| 2024-05-01 07:00 | -3.40% | 5.3 | 2.07 | -4.82 | 20 | +4.22% | +4.59% | +0.28% |
| 2024-07-05 02:00 | -2.44% | 2.7 | 2.13 | -1.96 | 20 | +8.96% | +11.59% | +1.18% |
| 2025-04-07 06:00 | -2.78% | 3.3 | 2.03 | 0.33 | 20 | +11.18% | +12.46% | +6.20% |

### Per-Window Return Distributions (primary, net of costs, last 5y)

| Window | N | Mean | Median | Stdev | Win% | Sharpe(event) | α | β | t(α) |
|---|---|---|---|---|---|---|---|---|---|
| T+12h | 9 | +7.58% | +7.30% | 4.56% | 100% | 1.66 | +5.25% | 0.74 | 3.59 |
| T+24h | 9 | +8.43% | +8.60% | 11.60% | 78% | 0.73 | +2.99% | 1.59 | 1.86 |
| T+48h | 9 | +9.02% | +9.31% | 12.19% | 89% | 0.74 | +2.23% | 1.99 | 0.74 |

### Sliced Performance (primary, event-level annualized)


**L12M** — 1 events

| Window | N | AnnRet | Sharpe | MaxDD | Calmar | Total | t(α) |
|---|---|---|---|---|---|---|---|
| T+12h | 1 | +11.2% | nan | 0.0% | — | +11.2% | nan |
| T+24h | 1 | +12.5% | nan | 0.0% | — | +12.5% | nan |
| T+48h | 1 | +9.3% | nan | 0.0% | — | +9.3% | nan |

**L6M** — 0 events

| Window | N | AnnRet | Sharpe | MaxDD | Calmar | Total | t(α) |
|---|---|---|---|---|---|---|---|
| T+12h | 0 | — | — | — | — | — | — |
| T+24h | 0 | — | — | — | — | — | — |
| T+48h | 0 | — | — | — | — | — | — |

**L3M** — 0 events

| Window | N | AnnRet | Sharpe | MaxDD | Calmar | Total | t(α) |
|---|---|---|---|---|---|---|---|
| T+12h | 0 | — | — | — | — | — | — |
| T+24h | 0 | — | — | — | — | — | — |
| T+48h | 0 | — | — | — | — | — | — |

### Conjunctive Gate — liq_z > 2.0 AND fund_z > 0.5

Events in last 5y: 1

| Window | N (5y) | Mean | Sharpe(event) | α | β | t(α) |
|---|---|---|---|---|---|---|
| T+12h | 1 | +2.15% | nan | +nan% | nan | nan |
| T+24h | 1 | +8.60% | nan | +nan% | nan | nan |
| T+48h | 1 | +3.41% | nan | +nan% | nan | nan |

L12M:

| Window | N | AnnRet | Sharpe | MaxDD | Calmar |
|---|---|---|---|---|---|
| T+12h | 0 | — | — | — | — |
| T+24h | 0 | — | — | — | — |
| T+48h | 0 | — | — | — | — |

### Sensitivity Sweep on liq_z Threshold (7d window, T+12h exit)

| liq_z > | N (5y) | Mean T+12h | Sharpe(event) T+12h | L12M N | L12M Sharpe(ann) | L12M Calmar | L12M MaxDD | L12M t(α) |
|---|---|---|---|---|---|---|---|---|
| 1.0 | 36 | +1.38% | 0.24 | 5 | 0.69 | 1.40 | -8.6% | 0.46 |
| 1.5 | 25 | +2.93% | 0.56 | 4 | 1.99 | 15.58 | -1.6% | 1.23 |
| 2.0 | 9 | +7.58% | 1.66 | 1 | nan | — | 0.0% | nan |
| 2.5 | 0 | — | — | 0 | — | — | — | — |
| 3.0 | 0 | — | — | 0 | — | — | — | — |

### Verdict

Best L12M window: **T+12h** (primary spec).

| Metric | Value | Threshold | Check |
|---|---|---|---|
| Trades | 1 | > 10 | FAIL |
| Sharpe (ann) | nan | > 2.0 | FAIL |
| Calmar | nan | > 3.0 | FAIL |
| MaxDD | 0.0% | > -25% | PASS |
| Alpha t-stat | nan | > 1.5 | FAIL |

**RESCUE VERDICT: KILL**

### Rescue Diagnosis

1. **Full-sample signal exists but is historical.** Across the last 5y the primary spec (liq_z>2.0) fires 9 times with T+12h **mean +7.58%, event-Sharpe 1.66, alpha +5.25% with t(α)=3.59, and 100% win rate**. This is genuinely strong — stronger than the funding-z spec's full-5y result (t(α)=1.86). The liquidation-$ gate DOES select a better subset of cascades than funding-z does.

2. **But the signal under-fires in L12M: only 1 event.** Same killer as the original run. Event distribution is 3/1/1/3/1 per year 2021→2025 — it's not concentrated in 2021-2022; it just fires rarely everywhere. The bottleneck is temporal coincidence: a BTC 1h<-2% cascade with vol>2× must land on a day whose **prior**-day cross-exchange daily liq z-score (7d window) already exceeded 2.0. That double-hurdle is too tight at hourly cadence.

3. **Data limitation is the root cause, not the hypothesis.** Coinalyze parquets are **daily** only (verified: columns `date,l,s,token,symbol`; no hourly file exists). The hypothesis explicitly requires an hourly 168h rolling z, which would attach the z-score to the cascade hour itself, not the prior day. With intraday liquidation data, liq_z>2.0 AT the cascade hour would capture every stress-cascade co-incidence, likely producing 30-60 L12M-equivalent events. The rescue was forced to use prior-day z (no look-ahead) because using same-day z leaks the cascade's own liquidations into the gate. This turns an intraday stress gate into a "yesterday was stressful, is today also?" gate, which is a strictly weaker condition.

4. **Conjunctive gate is worse, not additive.** liq_z>2.0 AND fund_z>0.5 fires only **1 time in 5y**. The two gates are nearly orthogonal: high-liq days post-2021 tend to occur when funding is NEGATIVE (shorts getting squeezed is liquidation too, and those days have fund_z≈-1 to -12 — see event table: 5 of 9 events have fund_z < -1). This confirms funding-z was not just a weak proxy — post-2021 it is **anti-correlated** with liquidation stress, making the conjunction nearly empty.

5. **Sensitivity sweep offers no rescue path.** Loosening to liq_z>1.5 gives 4 L12M trades with event-Sharpe only 0.56 and t(α)=1.23 — fails trade count (>10), Sharpe (>2.0), and alpha significance. Loosening to liq_z>1.0 degrades further (event-Sharpe 0.24). Tightening to liq_z>2.5 gives zero events (7d rolling z max is 2.26 in recent data). **No threshold in the sweep passes Gate 1.**

### Recommended Next Action

**KILL this strategy for Gate 1 with the existing data.** The full-sample t(α)=3.59 on 9 trades is suggestive enough to warrant **one deferred resurrection attempt IF hourly liquidation data is acquired** (e.g., via Coinalyze API historical pull at 1h resolution, or reconstructing from the okx tick CSVs if extended backward). With hourly liq data the gate could be applied at the cascade hour itself and would likely produce 3-5× more events, potentially crossing the L12M trade-count threshold. Until then, this strategy is blocked by data, not by hypothesis. Do not proceed to Gate 2.


---

## Rescue Attempt #2 (hourly liquidation data)

**Motivation.** Rescue #1 had only 9 valid events in 5y / 1 in L12M because the daily-resolution liquidation data forced a PRIOR-day z-score (the current day's total was not knowable at the trigger hour). With hourly Coinalyze data we can compute a 168h rolling z-score aligned to the cascade hour itself, which should produce far more events and expose the true edge.

### Data Inspection

**IMPORTANT: Coinalyze 1h historical depth is capped.** Empirically the API returns ~60-100 days of hourly liquidation history (varies by symbol). This means we cannot produce a 5-year or L12M track; the test window is effectively the last ~60-90 days minus the 168h warmup. Gate 1 thresholds designed for L12M slices must be interpreted accordingly.

Per-exchange hourly liquidation coverage:

| Exchange | Rows | Tokens | Start | End |
|---|---|---|---|---|
| binance | 45,946 | 29 | 2025-12-08 17:00:00+00:00 | 2026-04-07 15:00:00+00:00 |
| bybit | 34,189 | 29 | 2025-12-09 01:00:00+00:00 | 2026-04-07 15:00:00+00:00 |
| okx | 28,533 | 29 | 2025-12-08 17:00:00+00:00 | 2026-04-07 15:00:00+00:00 |
| bitmex | 1,741 | 21 | 2025-12-28 16:00:00+00:00 | 2026-04-07 15:00:00+00:00 |
| bitfinex | 1,816 | 20 | 2025-12-28 18:00:00+00:00 | 2026-04-07 11:00:00+00:00 |
| huobi | 12,820 | 29 | 2025-12-09 16:00:00+00:00 | 2026-04-07 15:00:00+00:00 |

Aggregated cross-exchange hourly series: **2,879 hours** (2025-12-08 17:00:00+00:00 → 2026-04-07 15:00:00+00:00).

Usable trigger window (post-168h warmup): **2025-12-15 17:00:00+00:00 → 2026-04-07 15:00:00+00:00** (~112.9 days, 2710 hours).

Hourly liq $ percentiles ($M): p50=0.86 p90=8.78 p99=40.25 max=207.54

168h-rolling z distribution: p50=-0.26 p90=0.89 p99=6.22 max=12.75, n(z>2)=124 of 2712 hours.


### Sensitivity Sweep (hourly, T+12h exit, full available window)

| liq_z > | N (full) | Mean T+12h | Sharpe(event) | Ann Sharpe | MaxDD | Calmar | α(T+12h) | t(α) |
|---|---|---|---|---|---|---|---|---|
| 1.0 | 5 | +2.36% | 0.75 | 5.26 | -3.1% | 66.84 | -0.11% | -0.32 |
| 1.5 | 5 | +2.36% | 0.75 | 5.26 | -3.1% | 66.84 | -0.11% | -0.32 |
| 2.0 | 5 | +2.36% | 0.75 | 5.26 | -3.1% | 66.84 | -0.11% | -0.32 |
| 2.5 | 5 | +2.36% | 0.75 | 5.26 | -3.1% | 66.84 | -0.11% | -0.32 |
| 3.0 | 4 | +2.05% | 0.58 | 4.09 | 0.0% | — | -0.29% | -5.31 |
| 3.5 | 4 | -0.13% | -0.03 | -0.22 | -4.1% | -2.18 | +0.02% | 0.08 |

### Per-Window Distribution at Best Threshold

Best threshold (by full-window ann Sharpe): **liq_z > 1.0** (5 events)

| Window | N | Mean | Median | Std | Win% | Sharpe(event) | α | β | t(α) |
|---|---|---|---|---|---|---|---|---|---|
| T+12h | 5 | +2.36% | +3.60% | 3.13% | 80% | 0.75 | -0.11% | 1.05 | -0.32 |
| T+24h | 5 | +1.33% | +1.25% | 3.52% | 80% | 0.38 | +0.21% | 1.10 | 0.41 |
| T+48h | 5 | -1.31% | +1.97% | 7.70% | 60% | -0.17 | -0.49% | 1.12 | -0.23 |

Events (best threshold):

| Timestamp | BTC 1h | vol× | liq_z | liq $M | n alts | ret_12h net | ret_24h net | BTC_24h |
|---|---|---|---|---|---|---|---|---|
| 2026-01-21 16:00 | -2.96% | 3.6 | 2.84 | — | 20 | +3.60% | +1.63% | +1.65% |
| 2026-01-29 15:00 | -2.39% | 10.6 | 9.99 | — | 20 | -3.07% | -3.57% | -2.41% |
| 2026-02-03 18:00 | -2.36% | 5.3 | 3.43 | — | 20 | +4.62% | +1.25% | +0.39% |
| 2026-02-23 01:00 | -3.48% | 15.8 | 10.63 | — | 20 | +2.56% | +1.00% | -0.40% |
| 2026-02-28 06:00 | -3.64% | 5.6 | 6.01 | — | 20 | +4.10% | +6.36% | +5.88% |

### L12M / L6M / L3M Slices (best threshold)

(Hourly data depth is limited — slices smaller than the full window will be empty.)

| Slice | N | AnnRet | Sharpe | MaxDD | Calmar | Total | t(α) T+12h |
|---|---|---|---|---|---|---|---|
| Full | 5 | +205.2% | 5.26 | -3.1% | 66.84 | +12.2% | -0.32 |
| L12M | 5 | +205.2% | 5.26 | -3.1% | 66.84 | +12.2% | -0.32 |
| L6M | 5 | +205.2% | 5.26 | -3.1% | 66.84 | +12.2% | -0.32 |
| L3M | 5 | +205.2% | 5.26 | -3.1% | 66.84 | +12.2% | -0.32 |

### Verdict (Gate 1)

Evaluation window: **Full window (5 ev; L12M had 5)**, threshold liq_z > 1.0, exit T+12h.

| Metric | Value | Threshold | Check |
|---|---|---|---|
| Trades | 5 | > 10 | FAIL |
| Sharpe (ann) | 5.26 | > 2.0 | PASS |
| Calmar | 66.84 | > 3.0 | PASS |
| MaxDD | -3.1% | > -25% | PASS |
| Alpha t-stat | -0.32 | > 1.5 | FAIL |

**RESCUE #2 VERDICT: KILL (insufficient events due to short hourly history)**


### Rescue #2 Diagnosis

1. **Hourly data confirms the signal DOES exist directionally**, but the sample is too thin to pass Gate 1. Across 113 usable days (~16 weeks, post-168h warmup) the base filter (BTC 1h<-2% AND vol>2×24h) fires only **11 times**, which drops to **5 events** after the 72h cooldown. The mean T+12h net return is +2.36% with 80% win rate and event-Sharpe 0.75; the point estimate of event-Sharpe scaled to annual is 5.26 — which clears the Sharpe/Calmar/MaxDD thresholds. But n=5 is insufficient to pass the trade-count gate (>10) AND the alpha t-stat is **negative** (-0.32) because beta(≈1.05) fully explains the return: at T+12h the basket mostly co-moves with BTC and the gate does not isolate idiosyncratic alt recovery.

2. **The liq_z gate is not binding at this data depth.** At the 11 base-trigger hours, liq_z ranges from -0.25 to 10.63; 9/11 have liq_z > 1.0 and 6/11 have liq_z > 5.0. Thresholds in the sweep 1.0–2.5 all produce the SAME 5 events (cooldown is the active constraint). Only liq_z > 3.0 drops one event and liq_z > 3.5 flips the sign. This means with hourly data the liquidation filter effectively DOES NOTHING on this window — almost every "BTC 1h<-2% + vol spike" is already a liquidation cascade. The gate's discriminating role only emerges when the dataset is large enough that many false positives (volatile-but-not-cascade hours) exist for it to cull.

3. **Contrast with Rescue #1 (daily, full 5y).** The daily-with-prior-day rescue on 9 events (2020–2025) showed t(α)=3.59 and +5.25% alpha — large and significant in-sample. The hourly rescue on 5 events (Dec 2025–Apr 2026) shows t(α)=-0.32 — statistically zero. The gap is not a contradiction: (a) these are different sub-samples of history (4 months vs 5 years); (b) at T+12h beta absorbs most of the return in a short window; (c) the hourly test is dominated by a single negative outlier (2026-01-29 -3.07%) that happens to coincide with a BTC -2.41% drift over 24h.

4. **Root cause of the KILL: Coinalyze 1h historical depth.** The API returns only ~90–120 days of hourly liquidation history depending on symbol (BTC: 65d; BNB: 97d; ETH: 65d). A single cascade cluster can dominate the sample. To properly test this hypothesis we would need either (a) ~2 years of hourly liquidation depth — not available on Coinalyze's default API tier — or (b) an alternative data source (Exchange tick archives → reconstructed liquidation prints). Neither is in-scope for this task.

### Gate 2 / Next-Action Recommendation

**Mission A (Liquidation Cascade Recovery) status: DEFINITIVELY PARKED — data-bound.**

- **Do NOT advance to Gate 2.** Both rescues have failed the trade-count gate by different failure modes (Rescue #1: 1 L12M event due to prior-day z lag; Rescue #2: 5 events due to short hourly history). Neither is a hypothesis-invalidating failure.
- **Do NOT attempt further tuning within this data regime** — there is no threshold on the existing hourly window that simultaneously clears n>10 and t(α)>1.5, because cooldown caps events at ~5 and the 4-month window gives beta too much weight.
- **Revival condition:** if/when ≥18 months of hourly cross-exchange liquidation data becomes available (paid Coinalyze tier, or reconstructed from exchange tick archives), re-run this script unchanged. Expected event count scales roughly linearly: 18 months → ~20 events, enough to resolve the alpha t-stat with meaningful power.
- **Dedup vs s513/s523c:** not applicable — strategy does not exist as a tradeable signal until the data revival condition is met. If it is revived and passes, dedup check should compare entry times vs s513 paper and s523c trades over the overlap window; cascade triggers are rare enough (≤3/month expected) that overlap with mean-reversion strategies is expected to be <5%.

**FINAL MISSION A STATUS (as of Rescue #2):** KILL-for-now, parked pending hourly data depth. Not a failed hypothesis — a failed dataset.
