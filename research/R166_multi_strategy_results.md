# R166 -- Multi-Strategy Search Results

**Generated:** 2026-03-28 18:51
**Simulation Period:** 2024-03-17 to 2026-03-17 (~2 years)
**Last 12 Months:** 2025-03-17 to 2026-03-17
**Token Universe:** 66 tokens passing volume filter
**Costs:** 7 bps per side + hourly funding from parquet
**Borrow Cost:** 5% annual on leveraged portion

---

## Individual Strategy Performance (1x, no leverage)

| Strategy | Full Ann Ret | Full MaxDD | Full Sharpe | Full Calmar | Full Sortino | PF | 12M Ret | 12M Sharpe | 12M MaxDD |
|----------|-------------|-----------|------------|------------|-------------|----|---------|-----------|----------|
| R158: Momentum Rotation (L7_R7_K5_50/50_RFY) | 74.3% | -47.4% | **1.41** | 1.57 | 2.43 | 1.24 | 150.8% | 2.08 | -22.3% |
| R160: Volatility Breakout (Variant B, 1x) | 10.1% | -9.3% | **1.06** | 1.08 | 1.56 | 1.18 | 3.2% | 0.37 | -8.2% |
| S1: Mean-Reversion on Volatile Tokens (RSI-based) | -14.9% | -57.5% | **0.17** | -0.26 | 0.22 | 1.03 | 10.7% | 0.51 | -51.1% |
| S2: Funding Rate Carry (short high-fund, long low-fund) | 7.7% | -38.1% | **0.38** | 0.20 | 0.60 | 1.06 | 21.3% | 0.66 | -34.4% |
| S3: Volume Momentum (7d volume growth rank) | 167.4% | -29.0% | **1.94** | 5.76 | 3.51 | 1.40 | 148.7% | 1.64 | -29.0% |
| S4: Short-Term Reversal (3d, opposite of momentum) | 1.7% | -43.7% | **0.22** | 0.04 | 0.28 | 1.04 | -10.6% | -0.19 | -39.6% |
| S5: Low-Volatility Anomaly (long low-vol, short high-vol) | 4.2% | -69.6% | **0.37** | 0.06 | 0.39 | 1.06 | -28.3% | -0.19 | -69.6% |

---

## Daily Return Correlation Matrix

| | R158_Momentum | R160_VolBreakout | S1_MeanReversion | S2_FundingCarry | S3_VolumeMomentum | S4_Reversal | S5_LowVol |
|---|---|---|---|---|---|---|---|
| R158_Momentum | 1.00 | 0.05 | -0.13 | 0.20 | 0.66 | 0.14 | 0.12 |
| R160_VolBreakout | 0.05 | 1.00 | -0.06 | 0.04 | 0.02 | 0.10 | 0.02 |
| S1_MeanReversion | -0.13 | -0.06 | 1.00 | -0.01 | -0.21 | -0.26 | -0.24 |
| S2_FundingCarry | 0.20 | 0.04 | -0.01 | 1.00 | 0.31 | -0.06 | -0.05 |
| S3_VolumeMomentum | 0.66 | 0.02 | -0.21 | 0.31 | 1.00 | 0.14 | 0.19 |
| S4_Reversal | 0.14 | 0.10 | -0.26 | -0.06 | 0.14 | 1.00 | 0.23 |
| S5_LowVol | 0.12 | 0.02 | -0.24 | -0.05 | 0.19 | 0.23 | 1.00 |

---

## Uncorrelated Strategy Candidates

Criteria: Last-12M Sharpe > 0.3 AND correlation < 0.3 with both R158 and R160

- **S1_MeanReversion**: 12M Sharpe=0.51, corr(R158)=-0.13, corr(R160)=-0.06 -> **CANDIDATE**
- **S2_FundingCarry**: 12M Sharpe=0.66, corr(R158)=0.20, corr(R160)=0.04 -> **CANDIDATE**
- **S3_VolumeMomentum**: 12M Sharpe=1.64, corr(R158)=0.66, corr(R160)=0.02 -> not qualified
- **S4_Reversal**: 12M Sharpe=-0.19, corr(R158)=0.14, corr(R160)=0.10 -> not qualified
- **S5_LowVol**: 12M Sharpe=-0.19, corr(R158)=0.12, corr(R160)=0.02 -> not qualified

Relaxed criteria (Sharpe > 0.0 AND |corr| < 0.5):


---

## Combination Portfolio Tests (with Drawdown Control)

DD control bands: 0-5% DD=100%, 5-10%=75%, 10-15%=50%, 15-20%=25%, >20%=0%

Borrow cost: 5% annual on (leverage-1) portion

| Portfolio | Leverage | Ann Ret | MaxDD | Sharpe | Calmar | Sortino | 12M Ret | 12M Sharpe | 12M MaxDD |
|-----------|---------|---------|-------|--------|--------|--------|---------|-----------|----------|
| ALL_7_EQUAL | 2x | 49.7% | -20.3% | **1.54** | 2.44 | 2.42 | 63.5% | 1.68 | -19.4% |
| ALL_7_EQUAL | 4x | 68.5% | -24.6% | **1.44** | 2.79 | 2.06 | 96.5% | 1.68 | -23.7% |
| R158+R160_ONLY | 3x | 63.7% | -24.5% | **1.36** | 2.60 | 2.02 | 109.7% | 1.71 | -21.0% |
| ALL_7_EQUAL | 3x | 48.8% | -22.8% | **1.30** | 2.14 | 1.84 | 44.2% | 1.18 | -22.8% |
| R158+R160_ONLY | 2x | 41.4% | -22.0% | **1.23** | 1.89 | 1.89 | 79.9% | 1.72 | -18.4% |
| R158+R160_ONLY | 4x | 51.6% | -26.6% | **1.10** | 1.94 | 1.47 | 76.1% | 1.29 | -22.5% |
| R158+R160+S2_FundingCarry | 2x | 29.5% | -21.7% | **1.00** | 1.36 | 1.56 | 75.6% | 1.68 | -20.1% |
| R158+R160+S2_FundingCarry | 4x | 29.3% | -26.5% | **0.82** | 1.11 | 1.17 | 73.9% | 1.33 | -24.6% |
| R158+R160+S2_FundingCarry | 3x | 21.6% | -24.1% | **0.72** | 0.90 | 1.03 | 67.9% | 1.36 | -22.1% |
| R158+R160+S1_MeanReversion+S2_FundingCarry | 3x | 12.8% | -25.6% | **0.54** | 0.50 | 0.67 | 53.9% | 1.32 | -20.7% |
| R158+R160+S1_MeanReversion+S2_FundingCarry | 4x | 6.1% | -29.1% | **0.35** | 0.21 | 0.37 | 43.3% | 1.11 | -24.0% |
| R158+R160+S1_MeanReversion | 2x | 4.7% | -21.9% | **0.31** | 0.22 | 0.36 | 18.6% | 0.70 | -18.8% |
| R158+R160+S1_MeanReversion+S2_FundingCarry | 2x | 3.5% | -23.5% | **0.26** | 0.15 | 0.32 | 25.9% | 0.93 | -17.2% |
| R158+R160+S1_MeanReversion | 3x | 0.3% | -24.5% | **0.16** | 0.01 | 0.18 | 21.3% | 0.72 | -20.6% |
| R158+R160+S1_MeanReversion | 4x | -3.3% | -28.0% | **0.03** | -0.12 | 0.03 | 13.7% | 0.56 | -25.3% |

### Monthly Returns: Best Combo (ALL_7_EQUAL @ 2x)

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | Annual |
|------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|--------|
| 2024 |  --  |  --  |  --  | -4.8% | -4.1% | -1.1% | -1.4% | -3.0% | -1.2% | +2.3% | +10.0% | +22.2% | +17.5% |
| 2025 | +8.3% | +20.7% | -2.6% | +0.6% | -5.7% | +35.6% | -10.6% | -5.4% | +1.2% | -3.0% | +1.6% | +2.9% | +42.2% |
| 2026 | +54.9% | -7.9% | +0.3% |  --  |  --  |  --  |  --  |  --  |  --  |  --  |  --  |  --  | +43.2% |

---

## Monthly Returns: Individual Strategies


### R158: Momentum Rotation (L7_R7_K5_50/50_RFY)

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | Annual |
|------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|--------|
| 2024 |  --  |  --  |  --  | -10.7% | -10.5% | -4.7% | -13.6% | -13.1% | +2.2% | +4.0% | +48.1% | +19.0% | +7.0% |
| 2025 | +2.9% | +6.2% | -3.7% | +12.6% | -9.5% | +37.8% | +4.3% | -11.9% | +13.0% | +19.1% | +12.9% | +13.3% | +134.0% |
| 2026 | +34.5% | -17.7% | +1.1% |  --  |  --  |  --  |  --  |  --  |  --  |  --  |  --  |  --  | +12.0% |

### R160: Volatility Breakout (Variant B, 1x)

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | Annual |
|------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|--------|
| 2024 |  --  |  --  |  --  | +2.3% | -1.4% | -0.9% | -0.4% | +4.7% | +0.4% | -2.8% | +8.5% | +4.1% | +15.0% |
| 2025 | -0.8% | +0.2% | -2.0% | +2.1% | -0.6% | -1.1% | -0.3% | -4.0% | +2.9% | -4.1% | +4.8% | -2.7% | -5.8% |
| 2026 | +7.5% | +2.0% | -2.8% |  --  |  --  |  --  |  --  |  --  |  --  |  --  |  --  |  --  | +6.6% |

### S1: Mean-Reversion on Volatile Tokens (RSI-based)

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | Annual |
|------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|--------|
| 2024 |  --  |  --  |  --  | -18.3% | +11.7% | -1.3% | -9.1% | -27.5% | -7.1% | +28.4% | -10.1% | +17.4% | -25.4% |
| 2025 | +22.1% | +1.2% | -5.1% | -21.5% | +9.5% | +44.0% | +0.8% | -5.8% | +3.6% | -16.3% | +4.4% | -7.4% | +15.6% |
| 2026 | -15.0% | +16.5% | +5.1% |  --  |  --  |  --  |  --  |  --  |  --  |  --  |  --  |  --  | +4.0% |

### S2: Funding Rate Carry (short high-fund, long low-fund)

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | Annual |
|------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|--------|
| 2024 |  --  |  --  |  --  | +7.4% | -2.4% | -13.8% | -0.7% | +4.3% | -0.2% | +4.9% | +21.2% | -8.6% | +8.5% |
| 2025 | +7.1% | +13.8% | -5.1% | +3.7% | +9.0% | +11.5% | +1.6% | -2.4% | +2.0% | -5.7% | -16.0% | +4.5% | +22.2% |
| 2026 | +34.8% | -23.4% | +8.5% |  --  |  --  |  --  |  --  |  --  |  --  |  --  |  --  |  --  | +12.0% |

### S3: Volume Momentum (7d volume growth rank)

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | Annual |
|------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|--------|
| 2024 |  --  |  --  |  --  | -6.2% | -4.9% | +10.1% | +4.3% | +10.8% | +6.0% | +12.0% | +36.5% | +10.3% | +102.9% |
| 2025 | +3.9% | +18.1% | -2.0% | +3.2% | -20.7% | +35.4% | -4.6% | -10.2% | -0.9% | +22.9% | +1.6% | +18.5% | +67.2% |
| 2026 | +130.0% | -18.3% | -3.4% |  --  |  --  |  --  |  --  |  --  |  --  |  --  |  --  |  --  | +81.7% |

### S4: Short-Term Reversal (3d, opposite of momentum)

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | Annual |
|------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|--------|
| 2024 |  --  |  --  |  --  | -0.2% | -1.8% | +5.7% | +4.9% | -13.9% | +1.2% | -1.9% | -13.8% | +31.2% | +5.0% |
| 2025 | -5.0% | -3.3% | +3.0% | +3.6% | -7.9% | -2.0% | -17.2% | -5.8% | -0.6% | +0.7% | -4.1% | +1.0% | -33.1% |
| 2026 | +23.1% | +0.6% | +0.9% |  --  |  --  |  --  |  --  |  --  |  --  |  --  |  --  |  --  | +24.9% |

### S5: Low-Volatility Anomaly (long low-vol, short high-vol)

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | Annual |
|------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|--------|
| 2024 |  --  |  --  |  --  | +10.7% | -15.5% | +8.3% | +0.9% | -6.9% | -16.4% | +1.1% | +19.5% | +17.6% | +13.0% |
| 2025 | +9.6% | +36.4% | +5.1% | +2.9% | +0.2% | +6.0% | -23.9% | -0.1% | -0.8% | -51.2% | +15.0% | +11.2% | -19.1% |
| 2026 | +31.6% | +3.5% | +3.7% |  --  |  --  |  --  |  --  |  --  |  --  |  --  |  --  |  --  | +41.2% |

---

## Token Universe (66 tokens)

AAVE, ADA, AGLD, ALGO, APT, ARB, ATOM, AVAX, AXS, BCH, BNB, BTC, CAKE, CFX, CHZ, CRV, DASH, DENT, DOGE, DOT, DUSK, DYDX, ENJ, ENS, ETC, ETH, FET, FIL, FLOW, GALA, HBAR, ICP, IMX, INJ, JUP, KAS, KAVA, LDO, LINK, LTC, NEAR, NEO, OGN, ONDO, OP, PENDLE, PEPE, POLYX, RVN, SAND, SEI, SHIB, SNX, SOL, STEEM, SUI, TIA, TRX, UNI, WIF, WLD, XLM, XMR, XRP, ZEC, ZEN
