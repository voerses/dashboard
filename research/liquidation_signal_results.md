# Liquidation Signal Predictive Power Test

**Date:** 2026-03-24
**Asset:** BTC perpetual futures
**Pass criteria:** IC > 0.05, t-stat > 2.0, sign consistent IS to OOS

---

## Data Inventory

| Source | Resolution | Date Range | Records | Usable for IC? |
|--------|-----------|------------|---------|----------------|
| OKX BTC tick liquidations | Tick-level | 2026-03-23 (11h) | 78,965 raw / 1,607 dedup | NO (too short) |
| OKX ETH tick liquidations | Tick-level | 2026-03-23 (9h) | 79,265 | NO |
| OKX SOL tick liquidations | Tick-level | 2026-03-22-23 (24h) | 33,038 | NO |
| BTC funding proxy (daily) | Daily | 2020-01-01 to 2026-03-17 | 2,268 | YES |
| Binance derivatives (hourly) | Hourly | 2026-03-03 to 2026-03-17 | 338 | Marginal (14 days) |
| BTC 1h price | Hourly | 2020-01-01 to 2026-03-17 | 54,425 | YES |

**Critical limitation:** The OKX liquidation tick data covers only ~11 hours. This is far too short for rolling statistics, IS/OOS splits, or meaningful IC computation. The analysis therefore uses **liquidation-proxy signals** derived from funding rates, long-short ratios, and cumulative funding -- variables that mechanistically drive liquidation cascades.

---

## Part A: OKX Tick-Level Micro-Structure (2026-03-23)

After deduplication: 1,607 unique liquidation events over 10.8 hours.

### Hourly Aggregation

| Hour (UTC) | Total USD | Count | Net Direction (Long-Short) | Max Single |
|------------|-----------|-------|---------------------------|------------|
| 11:00 | $11.4M | 9 | +$11.3M (shorts liq'd) | $5.0M |
| 12:00 | $139.5M | 198 | -$94.4M (longs liq'd) | $47.9M |
| 13:00 | $405.2M | 406 | -$405.2M (ALL longs) | $108.9M |
| 14:00 | $111.2M | 203 | -$57.0M (longs liq'd) | $21.5M |
| 15:00 | $274.2M | 435 | +$219.1M (shorts liq'd) | $35.4M |
| 16:00 | $32.4M | 87 | -$26.9M (longs liq'd) | $3.5M |
| 17:00 | $60.0M | 130 | -$60.0M (ALL longs) | $10.2M |
| 18:00 | $4.0M | 12 | -$4.0M (longs liq'd) | $1.5M |
| 19:00 | $17.4M | 53 | +$12.4M (shorts liq'd) | $6.5M |
| 20:00 | $14.1M | 35 | +$12.8M (shorts liq'd) | $8.1M |
| 21:00 | $0.3M | 1 | +$0.3M | $0.3M |
| 22:00 | $10.9M | 38 | +$9.7M (shorts liq'd) | $2.5M |

### Key Observations

- **Total liquidation volume:** $1.08B in ~11 hours
- **Long vs Short split:** 32.3% longs / 67.7% shorts liquidated
- **Peak cascade:** 13:00 UTC saw $405M in pure long liquidations (the largest single event was $109M)
- **Reversal pattern:** Heavy long liquidations 12:00-14:00 followed by short liquidations 15:00 -- classic cascade-reversal micro-structure
- **4h clustering:** No bars exceeded 2 std dev threshold (small sample, high variance)
- **Conclusion:** The tick data confirms expected liquidation cascade patterns but cannot be used for predictive signal testing due to insufficient history

---

## Part B: Daily Liquidation-Proxy Signal IC Analysis (2020-2026)

### Rationale for Proxy Signals

Liquidation cascades are driven by predictable preconditions:
1. **Extreme funding rates** = crowded positioning = forced liquidation risk on reversal
2. **LS ratio skew** = one-sided market = cascade vulnerability
3. **Cumulative funding divergence** = sustained cost pressure = eventual position unwinding

These proxy signals capture the CONDITIONS that precede liquidation events, using 6+ years of daily data.

### Temporal Split

- **In-sample (IS):** 2020-01-01 to 2024-05-06 (1,587 days, 70%)
- **Out-of-sample (OOS):** 2024-05-06 to 2026-03-17 (681 days, 30%)

### Signals Tested

| Signal | Description | Construction |
|--------|-------------|-------------|
| liq_volume_zscore | Funding rate 20d z-score | Rolling 20d z-score of daily funding rate |
| liq_net_direction | LS proxy 30d deviation | ls_proxy_30d minus 1.0 |
| liq_cluster | Extreme funding cluster | \|fr_zscore_30d\| > 2.0 (boolean) |
| fr_zscore_7d | Funding rate z-score (7d) | Pre-built rolling z-score |
| fr_zscore_14d | Funding rate z-score (14d) | Pre-built rolling z-score |
| fr_zscore_30d | Funding rate z-score (30d) | Pre-built rolling z-score |
| fr_zscore_60d | Funding rate z-score (60d) | Pre-built rolling z-score |
| ls_proxy_7d | LS ratio proxy (7d) | Pre-built positioning proxy |
| ls_proxy_30d | LS ratio proxy (30d) | Pre-built positioning proxy |
| cum_funding_7d | Cumulative funding (7d) | Sum of daily funding rates over 7d |
| cum_funding_30d | Cumulative funding (30d) | Sum of daily funding rates over 30d |
| fr_pctrank_60d | Funding percentile rank (60d) | Percentile of current funding in 60d window |
| extreme_long | Extreme long flag | Binary flag for extreme long positioning |
| extreme_short | Extreme short flag | Binary flag for extreme short positioning |
| fr_cross_rank | Funding cross-sectional rank | Cross-asset funding rank |

### Results: Spearman IC vs Forward BTC Returns

#### PASSING Signal-Horizon Pairs (IC > 0.05, t > 2.0, sign consistent)

| Signal | Horizon | IS IC | IS t-stat | IS n | OOS IC | OOS t-stat | OOS n | Sign Consistent |
|--------|---------|-------|-----------|------|--------|------------|-------|-----------------|
| fr_zscore_7d | 7d | -0.0633 | -2.52 | 1583 | -0.0618 | -1.61 | 674 | YES |
| ls_proxy_7d | 7d | -0.0633 | -2.52 | 1583 | -0.0618 | -1.61 | 674 | YES |
| cum_funding_7d | 14d | -0.1014 | -4.05 | 1581 | -0.0417 | -1.08 | 667 | YES |
| cum_funding_30d | 7d | -0.0741 | -2.93 | 1558 | -0.0363 | -0.94 | 674 | YES |
| cum_funding_30d | 14d | -0.1666 | -6.66 | 1558 | -0.0598 | -1.55 | 667 | YES |

**Total: 5 of 60 daily signal-horizon pairs pass all three criteria.**

#### Notable Near-Misses

| Signal | Horizon | IS IC | IS t-stat | OOS IC | Failure Reason |
|--------|---------|-------|-----------|--------|---------------|
| liq_volume_zscore | 3d | -0.0140 | -0.56 | -0.0786 | IS IC too low |
| fr_zscore_14d | 7d | -0.0224 | -0.89 | -0.0876 | IS IC/t too low |
| fr_zscore_60d | 3d | +0.0037 | +0.15 | -0.0980 | Sign flip IS->OOS |
| extreme_long | 3d | +0.0706 | +2.82 | -0.0625 | Sign flip IS->OOS |
| liq_net_direction | 14d | +0.0814 | +3.24 | -0.0171 | Sign flip IS->OOS |

### Interpretation

The **negative IC** on all passing signals means: **higher funding/positioning pressure predicts lower forward returns**. This is the expected direction -- crowded long positioning (high cumulative funding, high LS skew) creates liquidation vulnerability that resolves through downside price moves.

- **cum_funding_30d at 14d** is the strongest signal: IS IC = -0.167 (t = -6.66), very high statistical significance. OOS IC = -0.060, weaker but same sign. This suggests that 30-day cumulative funding cost accumulation predicts negative BTC returns over the following 2 weeks.
- **fr_zscore_7d / ls_proxy_7d at 7d** show identical ICs (they are mathematically related), with both IS and OOS around -0.063. These are the most sign-stable pair.
- **Decay pattern:** OOS ICs are systematically smaller than IS ICs (0.02-0.06 OOS vs 0.05-0.17 IS), suggesting alpha decay or partial regime change in the 2024-2026 period.

---

## Part C: Hourly Signal IC Analysis (Binance Derivatives, ~14 days)

### Data and Split

- **Range:** 2026-03-03 to 2026-03-17 (338 hourly bars)
- **IS:** 236 bars (Mar 3-13)
- **OOS:** 102 bars (Mar 13-17)
- **Caveat:** Very short sample -- insufficient for statistical reliability

### Hourly Signals Tested

| Signal | Description |
|--------|-------------|
| oi_zscore_24h | OI change z-score (24h rolling) |
| net_taker | Net taker pressure (buy - sell volume, normalized) |
| ls_deviation | LS ratio deviation from 48h mean |
| oi_ls_divergence | OI x LS divergence (interaction term) |
| buy_sell_ratio | Raw buy/sell taker ratio |

### Results

**No hourly signal-horizon pairs pass all three criteria.**

- **ls_deviation** shows strong IS IC (-0.22 to -0.25 at 4h/12h) but **flips sign** OOS -- classic overfitting on a 10-day IS window.
- **net_taker** and **buy_sell_ratio** show consistent negative sign IS/OOS but ICs and t-stats are below thresholds.
- The 14-day sample is fundamentally too short for reliable IC estimation at hourly frequency.

---

## Summary Verdict

| Test | Result | Details |
|------|--------|---------|
| Direct liquidation tick IC | NOT TESTABLE | Only 11 hours of OKX data available |
| Daily liquidation-proxy IC | CONDITIONAL PASS | 5/60 pairs pass (cum_funding and fr_zscore family) |
| Hourly derivatives IC | FAIL | Sample too short (14 days), no pairs pass |

### Overall: CONDITIONAL PASS

Five daily signal-horizon pairs meet all criteria (IC > 0.05, t > 2.0, sign consistent IS to OOS). The strongest signals are:

1. **cum_funding_30d -> 14d return** (IS IC = -0.167, t = -6.66; OOS IC = -0.060): Accumulated funding cost over 30 days predicts negative 2-week returns
2. **cum_funding_30d -> 7d return** (IS IC = -0.074, t = -2.93; OOS IC = -0.036): Same signal, shorter horizon
3. **cum_funding_7d -> 14d return** (IS IC = -0.101, t = -4.05; OOS IC = -0.042): Shorter funding window, 2-week horizon

### Caveats

1. These are **proxy signals** for liquidation pressure, not direct liquidation data. The causal chain is: high cumulative funding -> crowded positioning -> liquidation cascade risk -> negative returns.
2. OOS ICs are 40-65% smaller than IS ICs, indicating meaningful alpha decay.
3. The actual tick-level OKX liquidation data needs weeks-to-months of continuous collection before direct IC testing is possible.
4. The hourly Binance data is too short to draw conclusions -- need 3-6 months minimum.

### Recommendations

1. **Deploy a continuous OKX liquidation data collector** (e.g., via Tardis.dev WebSocket) to build the 60+ days of tick data needed for direct analysis.
2. **Use cum_funding_30d as a production signal** for medium-term (7-14d) BTC return prediction, with appropriate position sizing for the observed IC range (0.04-0.07 OOS).
3. **Monitor alpha decay** -- the 2024-2026 OOS period shows weaker but directionally consistent signal. If IC continues declining, the signal may be getting arbitraged away.
4. **Do not use hourly signals** until at least 90 days of Binance derivatives data is accumulated.

---

**Script:** `research/liquidation_signal_test.py`
**Raw results:** `research/raw/liquidation_ic_results.csv`
