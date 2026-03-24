# R118: Deep Walk-Forward Validation of On-Chain BTC Signals with Extended Data

**Date**: 2026-03-24
**Data**: Extended on-chain (R117) -- 9+ years, 2017-2026 (3,369 daily rows)
**BTC Spot**: 2020-01-01 to 2026-03-14 (2,258 daily rows)
**Cost assumption**: 10 bps round-trip
**WF config**: 180d train / 90d test / up to 10 windows

## Objective

Validate R115's promising on-chain signals using 9+ years of extended data from R117.
R115 was limited to 568-725 days (4-6 WF windows max). With 2,258 overlapping BTC
daily rows, we can now do proper 10-window deep walk-forward validation.

**Critical Investigation**: The subsample IC flip from R115 (netflow positive in 2nd
half, negative in 1st) -- with extended data, we test IC across 3 market regimes.

## Data Sources

| Source | File | Period | Rows |
|--------|------|--------|------|
| Exchange Netflow | coinmetrics_exchange_netflow_btc.parquet | 2017-01 to 2026-03 | 3,369 |
| Active Addresses | coinmetrics_cm_active_addresses.parquet | 2017-01 to 2026-03 | 3,369 |
| TX Volume USD | blockchain_com_tx_volume_usd.parquet | 2017-01 to 2026-03 | 3,354 |
| Exchange Balance | coinmetrics_exchange_balance_btc.parquet | 2017-01 to 2026-03 | 3,369 |
| BTC Spot 1h | data/spot/1h_cache/BTC_1h.parquet | 2020-01 to 2026-03 | 54,141 hourly |

## Signal Construction (Step 1)

For each of the 4 on-chain metrics, computed:
- **Rolling sum**: 5d, 10d, 20d
- **Momentum (% change)**: 7d, 14d, 30d
- **Z-score**: 30d rolling z-score
- **Level change**: 14d, 30d absolute change

**Total**: 36 raw signals constructed, all 36 aligned with BTC daily (2,250-2,258 obs each).

## IC Scan (Step 2)

Horizons: 1d, 3d, 7d, 14d forward returns. Kill threshold: |IC| < 0.02 across all horizons.

### Top 15 Signals by Max |IC|

| Signal | IC 1d | IC 3d | IC 7d | IC 14d | Max |IC| | N |
|--------|-------|-------|-------|--------|---------|---|
| txvol_20d_sum | -0.035 | -0.063 | -0.096 | **-0.138** | 0.138 | 2,249 |
| txvol_10d_sum | -0.036 | -0.062 | -0.089 | **-0.124** | 0.124 | 2,249 |
| netflow_change_30d | +0.025 | +0.049 | +0.074 | **+0.121** | 0.121 | 2,257 |
| txvol_5d_sum | -0.030 | -0.053 | -0.085 | **-0.116** | 0.116 | 2,249 |
| exbal_20d_sum | -0.025 | -0.046 | -0.069 | **-0.101** | 0.101 | 2,257 |
| exbal_5d_sum | -0.030 | -0.054 | -0.076 | **-0.099** | 0.099 | 2,257 |
| exbal_10d_sum | -0.027 | -0.049 | -0.071 | **-0.096** | 0.096 | 2,257 |
| netflow_20d_sum | +0.002 | +0.013 | +0.039 | **+0.091** | 0.091 | 2,257 |
| addr_growth_7d | **+0.052** | **+0.076** | +0.041 | +0.025 | 0.076 | 2,257 |
| addr_20d_sum | +0.016 | +0.029 | +0.047 | **+0.076** | 0.076 | 2,257 |
| addr_5d_sum | +0.026 | +0.040 | +0.044 | **+0.072** | 0.072 | 2,257 |
| exbal_mom_7d | -0.025 | -0.060 | **-0.071** | -0.053 | 0.071 | 2,257 |
| addr_10d_sum | +0.014 | +0.026 | +0.043 | **+0.071** | 0.071 | 2,257 |
| netflow_change_14d | -0.029 | **-0.057** | -0.056 | +0.007 | 0.057 | 2,257 |
| netflow_zscore_30d | +0.006 | -0.040 | **-0.056** | -0.037 | 0.056 | 2,257 |

**Killed by IC < 0.02**: 5 signals (exbal_change_30d, exbal_mom_30d, netflow_mom_30d, txvol_change_14d, txvol_mom_7d)
**IC Scan survivors**: 31 signals

### Key R115 Comparison: IC Shrinkage

The ICs on extended data are **much smaller** than R115's short-sample estimates:

| Signal | R115 IC (14d, 568 days) | R118 IC (14d, 2,257 days) | Shrinkage |
|--------|------------------------|--------------------------|-----------|
| netflow_5d_sum | +0.134 | -0.007 | Collapsed |
| netflow_10d_sum | +0.140 | +0.036 | -74% |
| addr_growth_14d | -0.008 | +0.018 | Sign flip |
| txvol_mom_14d | -0.014 | +0.036 | Sign flip |

## Period-Specific IC (Step 2.5) -- STATIONARITY CHECK

**This is the critical test.** We split the 6-year BTC-overlapping period into 3 regimes
and check whether signal IC is consistent across market environments.

| Period | Date Range | Market Environment |
|--------|------------|-------------------|
| Period A | 2020-01 to 2021-12 | COVID crash + first bull run |
| Period B | 2022-01 to 2023-12 | Crypto winter + recovery |
| Period C | 2024-01 to 2026-03 | ETF era |

**Horizon used**: 7d forward returns.

### Non-Stationary Signals (IC flips sign across periods -- KILLED)

| Signal | IC Period A | IC Period B | IC Period C | Verdict |
|--------|-------------|-------------|-------------|---------|
| netflow_10d_sum | -0.030 | +0.045 | -0.134 | **NON-STATIONARY** |
| netflow_20d_sum | +0.022 | +0.103 | -0.046 | **NON-STATIONARY** |
| netflow_change_30d | +0.124 | +0.109 | -0.083 | **NON-STATIONARY** |
| netflow_mom_14d | -0.012 | -0.018 | +0.018 | **NON-STATIONARY** |
| netflow_mom_7d | -0.058 | -0.076 | +0.049 | **NON-STATIONARY** |
| addr_growth_7d | +0.073 | -0.015 | +0.037 | **NON-STATIONARY** |
| addr_growth_30d | +0.039 | -0.006 | +0.066 | **NON-STATIONARY** |
| addr_zscore_30d | +0.048 | -0.031 | +0.055 | **NON-STATIONARY** |
| addr_change_30d | +0.030 | -0.004 | +0.069 | **NON-STATIONARY** |
| addr_5d_sum | -0.026 | -0.001 | +0.153 | **NON-STATIONARY** |
| addr_10d_sum | -0.033 | +0.017 | +0.154 | **NON-STATIONARY** |
| addr_20d_sum | -0.029 | +0.047 | +0.157 | **NON-STATIONARY** |
| exbal_5d_sum | -0.195 | -0.179 | +0.024 | **NON-STATIONARY** |
| exbal_10d_sum | -0.192 | -0.172 | +0.037 | **NON-STATIONARY** |
| exbal_20d_sum | -0.184 | -0.177 | +0.043 | **NON-STATIONARY** |
| txvol_change_30d | -0.020 | +0.113 | +0.078 | **NON-STATIONARY** |

### Stationary Signals (same sign across all periods)

| Signal | IC Period A | IC Period B | IC Period C | Verdict |
|--------|-------------|-------------|-------------|---------|
| netflow_5d_sum | -0.055 | -0.018 | -0.122 | STABLE (negative) |
| netflow_change_14d | -0.057 | -0.020 | -0.163 | STABLE (negative) |
| netflow_zscore_30d | -0.058 | -0.047 | -0.073 | STABLE (negative) |
| exbal_change_14d | -0.108 | -0.018 | -0.054 | STABLE (negative) |
| exbal_mom_14d | -0.111 | -0.022 | -0.055 | STABLE (negative) |
| exbal_mom_7d | -0.060 | -0.096 | -0.081 | STABLE (negative) |
| exbal_zscore_30d | -0.023 | -0.032 | -0.072 | STABLE (negative) |
| addr_growth_14d | +0.034 | +0.008 | +0.033 | STABLE (positive) |
| addr_change_14d | +0.038 | +0.004 | +0.033 | STABLE (positive) |
| txvol_5d_sum | -0.132 | -0.057 | -0.085 | STABLE (negative) |
| txvol_10d_sum | -0.128 | -0.084 | -0.100 | STABLE (negative) |
| txvol_20d_sum | -0.141 | -0.078 | -0.126 | STABLE (negative) |
| txvol_mom_14d | +0.033 | +0.012 | +0.016 | STABLE (positive) |
| txvol_mom_30d | +0.006 | +0.062 | +0.049 | STABLE (positive) |
| txvol_zscore_30d | +0.035 | +0.048 | +0.011 | STABLE (positive) |

**Killed by non-stationarity**: 16 signals (52% of IC survivors)
**Stationarity survivors**: 15 signals

**Key finding**: The R115 subsample IC flip for netflow is CONFIRMED. netflow_10d_sum
and netflow_20d_sum flip sign across periods. Only netflow_5d_sum and netflow_change_14d
maintain consistent (negative) sign direction.

## Deep Walk-Forward Validation (Step 3)

Configuration: 180d train / 90d test / up to 10 windows. Working backwards from data end.
Kill criteria: mean OOS Sharpe < 0.3 OR < 50% positive windows OR mean Sharpe excluding best window <= 0.

### WF Survivors (11 of 15 passed)

| Signal | Mean Sharpe | Median | Win/Total | Ex-Best Sharpe |
|--------|------------|--------|-----------|----------------|
| exbal_change_14d | 1.379 | 0.271 | 7/10 | 0.934 |
| exbal_mom_14d | 1.272 | 0.261 | 6/10 | 0.821 |
| txvol_mom_30d | 1.180 | 1.012 | 7/10 | 0.822 |
| txvol_20d_sum | 1.148 | 0.630 | 5/10 | 0.767 |
| netflow_change_14d | 1.017 | 1.175 | 7/10 | 0.542 |
| txvol_5d_sum | 0.995 | 0.158 | 5/10 | 0.601 |
| txvol_10d_sum | 0.957 | 1.040 | 7/10 | 0.573 |
| exbal_zscore_30d | 0.808 | 0.553 | 6/10 | 0.382 |
| netflow_5d_sum | 0.728 | 0.740 | 7/10 | 0.457 |
| netflow_zscore_30d | 0.484 | 0.599 | 6/10 | 0.264 |
| exbal_mom_7d | 0.304 | 0.291 | 5/10 | 0.060 |

### WF Killed (4 signals)

| Signal | Mean Sharpe | Win/Total | Kill Reason |
|--------|------------|-----------|-------------|
| addr_growth_14d | 0.130 | 4/10 | Sharpe < 0.3, < 50% positive, ex-best = -0.355 |
| addr_change_14d | 0.372 | 5/10 | Ex-best Sharpe = -0.027 (driven by single outlier window) |
| txvol_mom_14d | -0.162 | 3/10 | Sharpe < 0.3, < 50% positive, ex-best = -0.601 |
| txvol_zscore_30d | -0.111 | 4/10 | Sharpe < 0.3, ex-best = -0.599 |

**R115's star signal addr_growth_14d (WF Sharpe=2.40, 5/6 on 568 days) collapses to Sharpe=0.13, 4/10 on 2,258 days.**

## V3 Correlation Check (Step 4)

V3 Strategy: 20/50 EMA crossover, weekly rebalance. Full-period Sharpe: 0.978.
Kill threshold: Pearson correlation > 0.5.

| Signal | Pearson r | p-value | Verdict |
|--------|-----------|---------|---------|
| exbal_mom_7d | +0.439 | <0.001 | OK |
| netflow_change_14d | +0.435 | <0.001 | OK |
| txvol_10d_sum | +0.435 | <0.001 | OK |
| txvol_5d_sum | +0.393 | <0.001 | OK |
| exbal_change_14d | +0.544 | <0.001 | **KILLED** |
| exbal_mom_14d | +0.544 | <0.001 | **KILLED** |
| exbal_zscore_30d | +0.533 | <0.001 | **KILLED** |
| netflow_5d_sum | +0.579 | <0.001 | **KILLED** |
| netflow_zscore_30d | +0.501 | <0.001 | **KILLED** |
| txvol_20d_sum | +0.525 | <0.001 | **KILLED** |
| txvol_mom_30d | +0.646 | <0.001 | **KILLED** |

**V3 correlation survivors**: 4 signals | **Killed**: 7 signals

## Portfolio Test (Step 5)

For each surviving signal, built full-period strategy returns using 50th-percentile
threshold, then tested 50/50 and 70/30 (V3/signal) allocations.

### exbal_mom_7d

| Metric | V3 Only | Signal Only | 50/50 | 70/30 |
|--------|---------|-------------|-------|-------|
| Sharpe | 0.979 | 0.034 | 0.576 | 0.780 |
| Ann. Return | 43.2% | 1.6% | 22.4% | 30.7% |
| Max Drawdown | -55.9% | -76.7% | -61.4% | -58.6% |

Sharpe improvement: 50/50 = -0.403, 70/30 = -0.199
Range-period improvement: +0.689

### netflow_change_14d

| Metric | V3 Only | Signal Only | 50/50 | 70/30 |
|--------|---------|-------------|-------|-------|
| Sharpe | 0.979 | 0.043 | 0.594 | 0.794 |
| Ann. Return | 43.2% | 2.0% | 22.6% | 30.8% |
| Max Drawdown | -55.9% | -79.3% | -50.9% | -52.0% |

Sharpe improvement: 50/50 = -0.385, 70/30 = -0.184
Range-period improvement: +1.758

### txvol_10d_sum

| Metric | V3 Only | Signal Only | 50/50 | 70/30 |
|--------|---------|-------------|-------|-------|
| Sharpe | 0.999 | 0.234 | 0.704 | 0.855 |
| Ann. Return | 44.1% | 9.8% | 27.0% | 33.8% |
| Max Drawdown | -55.9% | -71.4% | -60.2% | -57.8% |

Sharpe improvement: 50/50 = -0.295, 70/30 = -0.144
Range-period improvement: +1.595

### txvol_5d_sum

| Metric | V3 Only | Signal Only | 50/50 | 70/30 |
|--------|---------|-------------|-------|-------|
| Sharpe | 0.999 | 0.365 | 0.780 | 0.899 |
| Ann. Return | 44.1% | 15.1% | 29.6% | 35.4% |
| Max Drawdown | -55.9% | -67.0% | -58.5% | -56.8% |

Sharpe improvement: 50/50 = -0.219, 70/30 = -0.099
Range-period improvement: +1.606

**Portfolio verdict**: No surviving signal improves portfolio Sharpe vs V3-only.
All combinations reduce Sharpe. The signals do show improvement during V3's weak
(range) periods (+0.7 to +1.8 Sharpe), but trending-period degradation outweighs the benefit.

## Parameter Sensitivity (Step 6)

All 4 surviving signals tested with threshold perturbation (+/-10%, +/-20%) and
direction flip. Base case: 50th percentile threshold, long_above direction.

| Signal | Base Sharpe | Verdict | Notes |
|--------|------------|---------|-------|
| exbal_mom_7d | 0.034 | **FRAGILE** | All perturbations > 30% degradation |
| netflow_change_14d | 0.043 | **FRAGILE** | All perturbations > 30% degradation |
| txvol_10d_sum | 0.234 | **FRAGILE** | Threshold shift +10%: -172% |
| txvol_5d_sum | 0.365 | **FRAGILE** | Threshold shift +10%: -125% |

**All 4 survivors are FRAGILE** -- Sharpe degrades >30% under parameter perturbation.

## Signal Pipeline Summary

| Stage | Count | Key Observation |
|-------|-------|-----------------|
| Raw signals constructed | 36 | 4 metrics x 9 signal types |
| IC scan survivors (|IC| >= 0.02) | 31 | Low bar -- most signals show some IC |
| Stationarity survivors | 15 | **16 killed** -- IC flips sign across market regimes |
| WF survivors | 11 | 4 killed -- addr_growth_14d collapses on extended data |
| V3 correlation survivors | 4 | **7 killed** -- most on-chain signals >0.5 corr with V3 |
| Final surviving signals | 4 | All marked FRAGILE, no portfolio improvement |

## R115 Signal Comparison

| R115 Signal | R115 Verdict (568-725d) | R118 Verdict (2,258d) | Change |
|-------------|------------------------|----------------------|--------|
| addr_growth_14d | PASSED (Sharpe=2.40, 5/6) | WF_KILLED (Sharpe=0.13, 4/10) | **OVERTURNED** |
| netflow_5d_sum | PASSED (Sharpe=1.00, 3/4) | V3_KILLED (corr=0.58) | **OVERTURNED** |
| netflow_10d_sum | PASSED (Sharpe=0.49, 2/4) | PERIOD_KILLED (non-stationary) | **OVERTURNED** |
| netflow_usd_5d | PASSED (Sharpe=0.83, 3/4) | N/A (USD netflow not in extended set) | - |
| netflow_usd_10d | PASSED (Sharpe=0.47, 3/4) | N/A (USD netflow not in extended set) | - |
| addr_growth_7d | KILLED (2/6) | PERIOD_KILLED (non-stationary) | CONFIRMED DEAD |
| txvol_mom_14d | KILLED (2/6) | WF_KILLED (Sharpe=-0.16, 3/10) | CONFIRMED DEAD |
| exbal_change_14d | FLAGGED (1 window) | V3_KILLED (corr=0.54) | NOW TESTABLE |
| exbal_change_30d | FLAGGED (1 window) | IC_KILLED (|IC|=0.01) | NOW TESTABLE |

**All 5 signals that passed R115 are either killed or overturned by the extended data.**

## Conclusions

### The Verdict: On-Chain Signals as Standalone Alpha Are a Dead End

While 4 signals technically survive the full pipeline, they fail to add portfolio value:

1. **No portfolio Sharpe improvement**: All V3/on-chain combinations have LOWER Sharpe
   than V3-only. The range-period benefit does not compensate for trending-period drag.

2. **All survivors are FRAGILE**: Sharpe degrades >30% under any parameter perturbation.
   These are not robust signals.

3. **R115 results were regime-specific**: The addr_growth_14d "star signal" (Sharpe 2.40
   on 568 days) collapses to 0.13 on 2,258 days. This is classic short-sample overfitting.

4. **IC non-stationarity is pervasive**: 16 of 31 signals (52%) flip IC sign across
   market regimes. The subsample IC flip that R115 flagged for netflow is confirmed as a
   general problem across ALL on-chain signal categories.

5. **High V3 correlation kills most survivors**: 7 of 11 WF survivors are >0.5 correlated
   with V3. On-chain signals are NOT providing independent information -- they largely
   track the same price trends that V3 already captures.

### Root Cause Analysis

Why do on-chain signals fail as alpha:
- **Reflexivity**: On-chain activity (addresses, TX volume, exchange flows) is driven BY
  price, not predictive OF price. High prices cause more activity, not the reverse.
- **Regime dependence**: The relationship between on-chain and price changes across
  bull/bear/range markets. No single threshold works.
- **Redundancy with trend**: Most on-chain signals are proxies for momentum/trend,
  which V3 already captures better with direct price data.

### Next Steps

1. **On-chain as standalone alpha: DEAD END** for this portfolio
2. Consider on-chain as **risk signal** (position sizing/drawdown protection) rather than alpha
3. Revisit if new on-chain metrics become available (UTXO age, whale wallets, miner flows)
4. Focus diversification research on other data types (macro, cross-asset, funding rates)
5. The 4 surviving signals (txvol_5d_sum, txvol_10d_sum, netflow_change_14d, exbal_mom_7d)
   show range-period improvement -- could be useful as regime filters, not standalone signals

### Caveats

1. BTC spot data starts 2020-01 -- on-chain data before 2020 cannot be tested against returns
2. On-chain data quality may differ between providers (Coinmetrics vs Blockchain.com)
3. Exchange netflow definition may change over time as exchanges are added/removed
4. Parameter sensitivity test uses a fixed base (50th percentile) which may not be optimal
5. V3 correlation is computed over the full period, not walk-forward
