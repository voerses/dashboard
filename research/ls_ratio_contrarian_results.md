# L/S Ratio Contrarian Signal — Research Results

Analysis date: 2026-03-23 22:55
Data source: Bybit daily L/S account ratio
Tokens: BTC, ETH, SOL, BNB, XRP, DOGE, ADA, AVAX, LINK, SUI
IS/OOS split: 2025-07-01

## Verdict
**FAIL (NOT ROBUST)** -- 6/436 combos passed thresholds, but 5 of 6 are AVAX-only (token-specific anomaly, not generalizable). The remaining pass (SOL raw L/S at 14d) has an IS backtest Sharpe of -0.88 (unimplementable). No signal variant shows consistent cross-token contrarian predictive power.

## Signal IC Summary (mean across tokens)

| Signal | Horizon | IC IS | t IS | IC OOS | t OOS | Sign% | Pass Rate |
|--------|---------|-------|------|--------|-------|-------|-----------|
| Cross-Token Divergence 30d | 1d | -0.0033 | -0.15 | +0.0208 | +0.33 | 44% | 0/9 |
| Cross-Token Divergence 30d | 3d | +0.0262 | +0.93 | +0.0653 | +1.05 | 56% | 1/9 |
| Cross-Token Divergence 30d | 7d | +0.0227 | +0.79 | +0.0880 | +1.41 | 44% | 0/9 |
| Cross-Token Divergence 30d | 14d | -0.0074 | -0.30 | +0.1888 | +3.07 | 33% | 0/9 |
| L/S + Funding Combo | 1d | +0.0077 | +0.33 | +0.0028 | +0.05 | 50% | 0/10 |
| L/S + Funding Combo | 3d | +0.0073 | +0.27 | -0.0084 | -0.13 | 60% | 0/10 |
| L/S + Funding Combo | 7d | -0.0160 | -0.63 | +0.0045 | +0.07 | 20% | 0/10 |
| L/S + Funding Combo | 14d | -0.0256 | -1.00 | +0.0232 | +0.37 | 40% | 0/10 |
| Rate of Change 10d | 1d | +0.0064 | +0.28 | -0.0114 | -0.18 | 50% | 0/10 |
| Rate of Change 10d | 3d | +0.0070 | +0.24 | -0.0008 | -0.01 | 70% | 0/10 |
| Rate of Change 10d | 7d | +0.0060 | +0.17 | +0.0087 | +0.14 | 60% | 1/10 |
| Rate of Change 10d | 14d | +0.0066 | +0.29 | +0.0168 | +0.28 | 20% | 1/10 |
| Rate of Change 20d | 1d | +0.0140 | +0.57 | +0.0186 | +0.30 | 50% | 0/10 |
| Rate of Change 20d | 3d | +0.0132 | +0.52 | +0.0304 | +0.49 | 40% | 0/10 |
| Rate of Change 20d | 7d | -0.0113 | -0.39 | +0.0577 | +0.93 | 20% | 0/10 |
| Rate of Change 20d | 14d | -0.0103 | -0.32 | +0.0897 | +1.44 | 20% | 0/10 |
| Rate of Change 5d | 1d | +0.0069 | +0.28 | -0.0129 | -0.21 | 10% | 0/10 |
| Rate of Change 5d | 3d | +0.0285 | +1.06 | +0.0413 | +0.66 | 80% | 0/10 |
| Rate of Change 5d | 7d | +0.0048 | +0.14 | +0.0036 | +0.06 | 60% | 0/10 |
| Rate of Change 5d | 14d | +0.0134 | +0.51 | +0.0300 | +0.47 | 30% | 1/10 |
| Raw L/S Contrarian | 1d | +0.0253 | +0.98 | -0.0359 | -0.58 | 0% | 0/10 |
| Raw L/S Contrarian | 3d | +0.0422 | +1.60 | -0.0131 | -0.21 | 40% | 0/10 |
| Raw L/S Contrarian | 7d | +0.0558 | +2.13 | -0.0281 | -0.47 | 50% | 0/10 |
| Raw L/S Contrarian | 14d | +0.0780 | +3.04 | -0.0262 | -0.44 | 40% | 1/10 |
| Regime Quartile 60d | 1d | -0.0014 | -0.04 | +0.0230 | +0.37 | 60% | 0/10 |
| Regime Quartile 60d | 3d | +0.0098 | +0.34 | +0.0476 | +0.77 | 60% | 0/10 |
| Regime Quartile 60d | 7d | +0.0054 | +0.17 | +0.0467 | +0.75 | 50% | 0/10 |
| Regime Quartile 60d | 14d | +0.0058 | +0.21 | +0.0428 | +0.68 | 20% | 0/10 |
| Regime Quartile 90d | 1d | +0.0030 | +0.12 | -0.0122 | -0.20 | 40% | 0/10 |
| Regime Quartile 90d | 3d | +0.0178 | +0.64 | -0.0013 | -0.02 | 30% | 0/10 |
| Regime Quartile 90d | 7d | +0.0078 | +0.25 | -0.0138 | -0.22 | 20% | 0/10 |
| Regime Quartile 90d | 14d | +0.0174 | +0.62 | -0.0149 | -0.24 | 10% | 0/10 |
| Z-Score 20d | 1d | +0.0168 | +0.66 | -0.0057 | -0.09 | 60% | 0/10 |
| Z-Score 20d | 3d | +0.0255 | +0.95 | +0.0255 | +0.41 | 60% | 0/10 |
| Z-Score 20d | 7d | +0.0147 | +0.54 | +0.0257 | +0.41 | 70% | 1/10 |
| Z-Score 20d | 14d | +0.0120 | +0.53 | +0.0609 | +0.98 | 20% | 0/10 |
| Z-Score 30d | 1d | +0.0111 | +0.46 | +0.0046 | +0.07 | 50% | 0/10 |
| Z-Score 30d | 3d | +0.0149 | +0.57 | +0.0397 | +0.64 | 60% | 0/10 |
| Z-Score 30d | 7d | +0.0009 | +0.04 | +0.0493 | +0.79 | 30% | 0/10 |
| Z-Score 30d | 14d | +0.0080 | +0.36 | +0.0808 | +1.30 | 20% | 0/10 |
| Z-Score 60d | 1d | +0.0026 | +0.13 | +0.0142 | +0.23 | 40% | 0/10 |
| Z-Score 60d | 3d | +0.0105 | +0.38 | +0.0441 | +0.71 | 50% | 0/10 |
| Z-Score 60d | 7d | +0.0020 | +0.06 | +0.0507 | +0.81 | 50% | 0/10 |
| Z-Score 60d | 14d | +0.0068 | +0.26 | +0.0571 | +0.92 | 30% | 0/10 |

## Backtest — Cross-Sectional Quartile Strategy
Cost: 5bps one-way

| Period | Sharpe | Total Return | Win Rate | Max DD | N Days |
|--------|--------|--------------|----------|--------|--------|
| IS | 0.166 | 6.96% | 49.52% | -31.87% | 1789 |
| OOS | 0.622 | 5.79% | 51.92% | -12.15% | 260 |

## Year-by-Year IC Stability (Z-Score 30d, 7d horizon)

| Year | IC Mean | IC Std | N Tokens |
|------|---------|--------|----------|
| 2020 | -0.0587 | 0.2749 | 3 |
| 2021 | +0.0110 | 0.1528 | 9 |
| 2022 | -0.0190 | 0.1013 | 9 |
| 2023 | +0.0213 | 0.0576 | 10 |
| 2024 | -0.0014 | 0.0494 | 10 |
| 2025 | +0.0323 | 0.0378 | 10 |
| 2026 | +0.2252 | 0.1727 | 10 |

## Per-Token L/S Ratio Statistics

| Token | Mean | Median | Std | Min | Max | Start Date | N Days |
|-------|------|--------|-----|-----|-----|------------|--------|
| BTC | 1.58 | 1.47 | 0.56 | 0.66 | 4.02 | 2020-08-05 | 2056 |
| ETH | 2.83 | 2.58 | 1.49 | 0.43 | 9.05 | 2020-10-22 | 1978 |
| SOL | 2.97 | 2.82 | 1.34 | 0.00 | 7.61 | 2021-06-30 | 1727 |
| BNB | 2.09 | 2.05 | 0.78 | 0.00 | 5.86 | 2021-06-30 | 1727 |
| XRP | 3.14 | 3.05 | 1.18 | 0.55 | 8.37 | 2021-05-14 | 1774 |
| DOGE | 3.46 | 3.21 | 1.38 | 0.63 | 9.10 | 2021-06-03 | 1754 |
| ADA | 3.31 | 2.86 | 1.60 | 0.83 | 12.16 | 2021-03-19 | 1830 |
| AVAX | 3.02 | 2.91 | 1.18 | 0.53 | 10.09 | 2021-09-16 | 1650 |
| LINK | 3.22 | 2.90 | 1.29 | 0.54 | 9.88 | 2020-10-22 | 1978 |
| SUI | 3.79 | 3.47 | 1.72 | 0.96 | 14.06 | 2023-05-04 | 1055 |

## Passed Signal Details

| Signal | Token | Horizon | IC IS | t IS | IC OOS | t OOS |
|--------|-------|---------|-------|------|--------|-------|
| Rate of Change 10d | AVAX | 7d | +0.0785 | +2.92 | +0.1317 | +2.10 |
| Rate of Change 10d | AVAX | 14d | +0.0746 | +2.77 | +0.2637 | +4.27 |
| Z-Score 20d | AVAX | 7d | +0.0729 | +2.71 | +0.1797 | +2.89 |
| Raw L/S Contrarian | SOL | 14d | +0.0706 | +2.70 | +0.1350 | +2.13 |
| Rate of Change 5d | AVAX | 14d | +0.0620 | +2.30 | +0.1466 | +2.31 |
| Cross-Token Divergence 30d | AVAX | 3d | +0.0609 | +2.26 | +0.1790 | +2.90 |

## Critical Findings

### 1. The IS Sharpe 2.85 was a mirage
The original 28-day Binance result (IS Sharpe 2.85) is decisively refuted by 5+ years of Bybit data. With proper history, the cross-sectional L/S contrarian strategy produces IS Sharpe 0.166 -- an 18x reduction. The original result was pure short-sample noise.

### 2. Most IS signals flip sign OOS
The Raw L/S Contrarian signal shows the clearest pattern of **sign reversal**: positive IC in-sample for BTC (+0.072 at 7d), DOGE (+0.089), ADA (+0.085) -- but these all flip negative out-of-sample. This is the hallmark of overfitting to a specific market regime (2020-2025 bull runs).

### 3. AVAX is a token-specific anomaly
5 of 6 PASS signals are AVAX-only. This suggests something idiosyncratic about AVAX's L/S data -- possibly lower retail participation, different market microstructure, or data quality issues -- rather than a generalizable contrarian effect.

### 4. Year-by-year IC is inconsistent
The Z-Score 30d signal at 7d horizon shows IC flipping between years: -0.059 (2020), +0.011 (2021), -0.019 (2022), +0.021 (2023), -0.001 (2024). Only 2026 (partial year, 83 days) shows a strong IC of +0.225, which is likely recency bias.

### 5. The funding combo adds no value
Combining L/S ratio with funding rate (the theoretically strongest signal) produces IC near zero across all horizons when aggregated: IC_IS from -0.026 to +0.008, IC_OOS from -0.008 to +0.023. The two signals do not reinforce each other.

### 6. Contrarian hypothesis is weakly correct but not tradeable
The average IC direction is mildly correct (positive IC for contrarian at longer horizons), but the magnitude is too small (|IC| < 0.03 on average) and too inconsistent across tokens to produce a viable trading signal after costs.

## Methodology Notes
- **Signal direction**: All signals are CONTRARIAN — high L/S ratio (retail overly long) maps to negative signal (go short)
- **IC**: Spearman rank correlation between signal and forward returns
- **IS period**: All data before 2025-07-01
- **OOS period**: All data from 2025-07-01 onward (no parameter optimization)
- **PASS criteria**: |IC| > 0.05, |t-stat| > 2.0, same sign IS vs OOS
- **Backtest**: Long bottom quartile L/S tokens, short top quartile, daily rebalance, 5bps costs
- **Prior context**: IS Sharpe 2.85 on 28 days of Binance data was BLOCKED for insufficient history
