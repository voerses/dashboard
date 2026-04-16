# Composite Fragility Index — Gate 1 Report

**Date:** 2026-04-07
**Pipeline gate:** 0 + 1 (return-first overlay feasibility)
**Verdict: KILL** — no overlay rule meets the +15% Calmar bar on either strategy.

---

## 1. Data inspection

| Source | Symbols | Freq | Range | Rows |
|---|---|---|---|---|
| `data/alternative/binance_funding_rates_full.json` | **2** (BTC, ETH only) | 8h | 2019-09 → 2026-03 | 2,400 |
| `data/alternative/funding_ls_proxy/*_funding_proxy.parquet` | **149** | daily | 2020-01 → 2026-03 | 2,268/sym |
| `data/perp/binance/funding/*_funding.csv` | 247 | hourly | mostly ≥2025 | varies |
| `data/perp/binance/1h_ohlcv/*.csv` | 248 | 1h | 2020+ | ~49k/sym |

The headline `binance_funding_rates_full.json` file only contains BTCUSDT + ETHUSDT — unusable for a cross-sectional index. I used **`funding_ls_proxy`** (149 symbols, daily) as the funding panel and cross-matched 143 of them to `1h_ohlcv` for volume ranking. Hourly funding (`data/perp/binance/funding/`) exists but only covers the recent year consistently, so daily resolution is the correct common denominator.

## 2. Top-50 universe

7-day rolling dollar-volume rank from 1h OHLCV. Mean universe size = **42** (some dates have fewer than 50 symbols with enough volume history, especially pre-2021). Dynamic rebalancing every day.

## 3. Composite index

Formula: `sigmoid(0.4·avg_z + 0.3·disp_z + 0.3·level_z)` where z-scores are 7-day rolling.

| Quantile | Fragility |
|---|---|
| p05 | 0.245 |
| p25 | 0.412 |
| p50 | 0.490 |
| p75 | 0.592 |
| p95 | 0.790 |

Range [0.24, 0.91]. Roughly centered, fatter upper tail. Top-5 historical spike dates are all **early-2020 / early-2021** (funding-long euphoria).

### Sanity check at known stress events

| Event | Fragility (day) | 7d peak |
|---|---|---|
| COVID crash (2020-03-12) | 0.33 | 0.61 |
| LUNA (2022-05-10..13) | 0.53 → 0.60 | 0.62 |
| FTX (2022-11-08..10) | 0.35 → 0.60 | 0.69 |
| Aug 2024 yen carry (2024-08-05) | 0.31 | 0.55 |
| 2025-04 tariff shock | 0.47 | 0.62 |

**Critical finding:** the index **does not spike reliably at crash events**. It registers funding-long euphoria (top spikes are 2020/21 bull runs), but funding flips *negative* during crashes (short crowding), which gets suppressed by the 7-day z-score windows. The `level_z` component on `|avg|` helps a bit but does not dominate. This foreshadows the weak overlay performance.

## 4. s513 overlay results

Baseline (from reconstructed trade-log equity): Return **+220.4%**, Sharpe 2.77, MaxDD -13.1%, Calmar **16.75**. (True sim had Calmar 12.93 — the reconstruction sums trade PnL at exit dates, which optimistically smooths MDD; but *relative* overlay-vs-baseline on the same reconstruction is apples-to-apples.)

Entry fragility distribution: mean 0.489, median 0.487, only **3 / 195 trades** (1.5%) have frag > 0.7.

| Rule | Return % | Sharpe | MaxDD % | Calmar | ΔCalmar vs base |
|---|---|---|---|---|---|
| baseline | +220.4 | 2.77 | -13.1 | **16.75** | — |
| linear `1-frag` | +112.8 | 2.95 | -8.5 | 13.28 | -20.7% |
| threshold `frag>0.7 → 0` | +218.7 | 2.74 | -13.3 | 16.43 | -1.9% |
| tiered `0.45/0.65` | +137.1 | 3.03 | -9.3 | 14.64 | -12.6% |
| cut @0.55 | +195.0 | 2.80 | -11.4 | 17.09 | +2.0% |
| **soft Q4 → 0.5** | **+207.7** | 2.86 | -12.0 | **17.17** | **+2.5%** |
| top decile kill | +196.1 | 2.47 | -14.9 | 13.11 | -21.7% |

**Best s513 rule: "soft Q4 → 0.5"** → Calmar 17.17, a +2.5% improvement. **Bar for PASS was +15% → Calmar ≥ 19.26.** Fail.

## 5. s523c overlay results

Baseline (reconstructed): Return **+747.2%**, Sharpe 2.88, MaxDD -37.3%, Calmar **19.92**.
(Note: spec stated 528% / MDD -45.9% / Calmar 11.5. The current run produces better baseline because data has moved forward; I compare against the *current* baseline per meta-rule "code over specs.")

Entry fragility distribution: mean 0.492, median 0.474, **16 / 634** trades (2.5%) have frag > 0.7.

**Cross-sectional sanity:** s523c trades binned by fragility quartile at entry show a monotonic decay — Q1 avg $724 → Q4 avg $267, win rate 48% → 40%. The signal exists but is weak and slow — not enough concentration of losses in the top quartile to make cutting them accretive.

| Rule | Return % | Sharpe | MaxDD % | Calmar | ΔCalmar vs base |
|---|---|---|---|---|---|
| baseline | +747.2 | 2.88 | -37.3 | **19.92** | — |
| linear `1-frag` | +384.0 | 2.72 | -32.5 | 11.74 | -41.1% |
| threshold `frag>0.7 → 0` | +730.0 | 2.72 | -38.2 | 18.96 | -4.8% |
| tiered `0.45/0.65` | +469.0 | 2.33 | -43.7 | 10.66 | -46.5% |
| cut @0.55 | +666.0 | 2.58 | -33.0 | 20.08 | +0.8% |
| **soft Q4 → 0.5** | **+704.7** | 2.77 | -34.8 | **20.15** | **+1.2%** |
| top decile kill | +727.9 | 2.71 | -36.4 | 19.87 | -0.3% |

**Best s523c rule: "soft Q4 → 0.5"** → Calmar 20.15, a +1.2% improvement. **Bar for PASS was Calmar ≥ 22.90.** Fail.

## 6. Verdict: **KILL**

Neither strategy hits the +15% Calmar bar with any rule tested. The best-case improvement is ~2.5% on s513 and ~1.2% on s523c — both well within noise.

### Diagnosis (one paragraph)

The composite fragility index as specified measures **funding-long euphoria**, not crash risk. Its top-5 historical spike dates are all 2020-2021 bull runs; at the three largest modern crash events (COVID 2020-03, LUNA 2022-05, FTX 2022-11) the index sits at or below the median, because during crashes funding *inverts* (short crowding) and the 7-day rolling z-scoring quickly normalizes the new regime. The z-score weighting scheme also washes out persistent regime shifts because normalization is backward-looking. On the trade side, s513 has essentially no fragility signal — its Q3 trades outperform Q1 — so any culling destroys returns without reducing risk. s523c does show a real Q1→Q4 PnL decay (+$724 → +$267 avg, win rate 48→40%), but the decay is gradual across quartiles rather than concentrated in an extreme, so any rule that trims aggressively enough to move MDD also trims too deeply into the profitable middle. Only very conservative rules (cut @0.55, soft Q4→0.5) stay approximately neutral; none achieve the 15% Calmar lift.

### What would need to be true for NEEDS_TUNING
For this line of work to be worth a Gate 2, we would need to redesign the index to actually fire during crashes. Candidates:
1. **Asymmetric level component:** weight *negative* (short-crowded) funding extremes ≥ positive, since short-crowding characterizes crash onset.
2. **Longer baseline z-window** (30-90d) to preserve regime-shift signal that the 7d window washes out.
3. **Add a realized-vol dispersion term** — fragility = funding stress + price stress, not funding alone.
4. **Directional overlay** — only cut longs when frag-long is high; don't cut shorts symmetrically (s513 has meaningful short exposure).
5. **Target the right strategy:** s513 shows no pnl-vs-frag relationship at all. This overlay concept, if revived, should be tested only on long-biased strategies where the Q1→Q4 decay exists.

Given the +1-3% Calmar improvements at best, I do not recommend Gate 2 without a fundamental index redesign. **KILL.**

## Artifacts

- `research/fragility_index_gate1.py` — index builder
- `research/fragility_index.parquet` — daily fragility time series (2020-01 → 2026-03)
- `research/fragility_overlay_eval.py` — overlay evaluator
- `research/fragility_overlay_results.json` — full results dump
- `research/fragility_out/s513_baseline/` — s513 baseline backtest artifacts
- `research/fragility_out/s523c_baseline/` — s523c baseline backtest artifacts
