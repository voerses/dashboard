# BTC-Alt Rolling Correlation as Regime Conditioner for Macro Signals

**Date:** 2026-03-24
**Script:** `research/correlation_regime_conditioning.py`
**OOS cutoff:** 2025-07-01
**Verdict: SKIP -- no meaningful lift from regime conditioning**

---

## Hypothesis

When BTC-alt correlations are high (>0.7), the market moves as a bloc and macro signals (US10Y, DXY) should be stronger predictors. When correlations break down (<0.4), idiosyncratic factors dominate and macro signals lose power.

## Data

- **Price data:** BTC, ETH, SOL, DOGE, XRP, LINK (hourly, resampled to daily)
- **Macro:** US10Y yield, DXY (USD index)
- **Correlation window:** 30-day rolling BTC-alt pairwise correlations, averaged
- **IS period:** 2020-11-12 to 2025-06-30 (N=1,692)
- **OOS period:** 2025-07-01 to 2026-03-16 (N=259)

## Key Findings

### 1. Correlation Regime Distribution

The BTC-alt correlation ecosystem is **structurally high-correlation**:
- Mean avg_corr = 0.711, Median = 0.746
- 64.5% of days fall in HIGH (>0.7), 31.6% MEDIUM (0.4-0.7), only 3.9% LOW (<0.4)
- OOS is even more concentrated: **87.6% HIGH, 12.4% MEDIUM, 0% LOW**
- Trend is toward higher correlation over time (2022: 0.791, 2025: 0.793, 2026: 0.876)

**Implication:** The LOW regime barely exists. Any conditioner based on low-correlation is impractical -- it fires on <4% of days and has zero OOS coverage.

### 2. Signal IC by Correlation Regime

#### US10Y 20d Change (best macro signal)

| Regime | IS IC (14D) | IS t-stat | OOS IC (14D) | OOS t-stat |
|--------|-------------|-----------|--------------|------------|
| ALL    | -0.0521     | -2.14**   | -0.3749      | -6.32***   |
| HIGH   | -0.1132     | -3.66***  | -0.4092      | -6.53***   |
| MEDIUM | +0.0329     | +0.79     | -0.1599      | -0.89      |
| LOW    | +0.1171     | +1.01     | N/A (0 days)  | N/A        |

**Finding:** US10Y signal is indeed stronger in HIGH-corr regime IS (IC doubles from -0.052 to -0.113). OOS, the lift is smaller (+0.034 absolute IC) and the ALL baseline is already very strong (-0.375). The signal works well regardless of regime.

#### Skew 30d

| Regime | IS IC (14D) | IS t-stat | OOS IC (14D) | OOS t-stat |
|--------|-------------|-----------|--------------|------------|
| ALL    | +0.0780     | +3.22***  | +0.2258      | +3.62***   |
| HIGH   | +0.0620     | +1.99*    | +0.2310      | +3.46***   |
| MEDIUM | +0.1082     | +2.63**   | +0.5590      | +3.69***   |
| LOW    | -0.0409     | -0.35     | N/A           | N/A        |

**Finding:** Skew is actually *stronger* in MEDIUM regime than HIGH (IS: 0.108 vs 0.062). This contradicts the hypothesis. OOS, MEDIUM shows an extraordinary IC of 0.559 but on only 32 days.

#### DXY 20d Momentum

| Regime | IS IC (14D) | IS t-stat | OOS IC (14D) | OOS t-stat |
|--------|-------------|-----------|--------------|------------|
| ALL    | -0.0046     | -0.19     | -0.1705      | -2.70**    |
| HIGH   | +0.0098     | +0.31     | -0.1917      | -2.84**    |
| MEDIUM | -0.0498     | -1.20     | +0.3047      | +1.75*     |
| LOW    | +0.0663     | +0.57     | N/A           | N/A        |

**Finding:** DXY shows marginal improvement in HIGH regime OOS (+0.021 absolute IC), but the signal is weak overall and the MEDIUM regime flips sign between IS and OOS.

### 3. Mean IC Lift from Conditioning

| Metric                        | Value  |
|-------------------------------|--------|
| Mean absolute IC lift (IS)    | +0.005 |
| Mean absolute IC lift (OOS)   | +0.005 |
| Lift > 0.02 threshold?        | **NO** |

Average across all signal-target combinations, the HIGH-corr regime provides only +0.005 absolute IC improvement. This is statistically and practically negligible.

### 4. Correlation Regime as Standalone Signal

| Sample | IC (14D) | t-stat |
|--------|----------|--------|
| IS     | -0.0038  | -0.16  |
| OOS    | -0.0612  | -0.96  |

**Finding:** Average BTC-alt correlation has **no predictive power** for forward BTC returns. It is not a useful standalone alpha signal.

IS return patterns by regime:
- HIGH: +0.074%/day, Sharpe +0.46
- MEDIUM: +0.369%/day, Sharpe +2.24
- LOW: -0.254%/day, Sharpe -1.03

This suggests LOW-correlation periods were bearish IS, but with only 76 days and zero OOS days, this is not actionable.

### 5. Conditional Sharpe (Signal-Weighted Strategies)

| Signal        | IS Uncond Sharpe | IS HIGH Sharpe | OOS Uncond Sharpe | OOS HIGH Sharpe |
|---------------|------------------|----------------|-------------------|-----------------|
| US10Y 20d Chg | +1.06            | +0.53          | +1.10             | +1.12           |
| Skew 30d      | +1.32            | +0.92          | +1.16             | +0.72           |
| DXY 20d Mom   | +0.88            | +0.36          | -0.82             | -0.53           |

**Finding:** In IS, conditioning on HIGH regime actually *hurts* Sharpe (loses MEDIUM regime days which were profitable). In OOS, results are mixed with no consistent improvement.

### 6. Temporal Stability

- Correlation is trending higher over time (2021: 0.59 -> 2026: 0.88)
- The LOW regime has effectively disappeared since 2022
- Rolling 90d IC of US10Y signal conditioned on HIGH regime is unstable (same-sign agreement only 46.1% of the time)

## Why the Hypothesis Failed

1. **Structural high correlation:** The crypto market is nearly always in the HIGH regime (65% IS, 88% OOS). There is not enough variation to create a meaningful regime split.

2. **LOW regime is too rare:** Only 3.9% of days (76 total, 0 OOS). Any analysis of the LOW regime has extremely low statistical power.

3. **Signals work across regimes:** US10Y 20d change is already a strong unconditional signal (OOS IC = -0.375). The marginal lift from conditioning is tiny relative to the signal's base strength.

4. **MEDIUM regime is where diversity lives:** Interestingly, Skew works *better* in MEDIUM regime. This suggests the hypothesis direction may be inverted for some signals, but MEDIUM regime is also shrinking in prevalence.

## Recommendation

**SKIP** -- Do not use BTC-alt correlation as a regime conditioner.

- The mean IC lift is +0.005, far below the +0.02 threshold for practical value
- The correlation regime itself has no predictive power (IC = -0.06, t = -0.96 OOS)
- The LOW regime has zero OOS coverage, making the conditioner unusable
- Signals (especially US10Y 20d change) work well unconditionally

**Alternative directions worth exploring:**
- Cross-sectional *dispersion* (std of alt returns) rather than correlation -- may capture idiosyncratic risk more directly
- Regime shifts (change in correlation) rather than level -- transition periods may be more informative than sustained states
- Sector-specific correlation (DeFi vs L1 vs memes) rather than broad average
