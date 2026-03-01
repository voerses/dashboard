# Fat Tail Analysis — Proving Where the Money Is

Last updated: 2026-02-28

## Data
- **49 tokens**, 1H data aggregated to daily (Jan 2024 - Jan 2026, ~760 trading days)
- Source: `real_data/1h_cache/*.parquet`

---

## HEADLINE FINDINGS

### 1. Crypto Returns Are NOT Normal — Every Single Token Rejects Normality
- **49/49 tokens** reject the Jarque-Bera normality test at p<0.05
- Mean excess kurtosis: **17.6** (normal = 0, meaning crypto tails are ~18x fatter than Gaussian)
- Median excess kurtosis: **4.6** (even the "thinnest" crypto token has kurtosis of 1.6)
- **23 tokens** have kurtosis > 5 (very fat tails)
- **15 tokens** have kurtosis > 10 (extreme fat tails)

### 2. A Tiny Fraction of Days Produces Most of the Action
- **Top 5% of days** (38 days out of ~760) account for **21.0%** of total absolute PnL
  - Under normal distribution, top 5% would contribute only ~12-14%
- **Top 1% of days** (8 days) account for **7.1%** of total absolute PnL
  - Under normal distribution, top 1% would contribute only ~4-5%
- Concentration is ~1.5-1.8x higher than Gaussian predictions

### 3. Missing Just 5-10 Days Destroys Returns Completely
- Average token full return over 2 years: **-0.5%** (roughly flat)
- Miss the best 5 days: **-66.9%** (catastrophic)
- Miss the best 10 days: **-81.7%** (near total loss)
- Miss the worst 5 days: **+181.2%** (massive gain)
- **16/49 tokens** have positive 2-year returns. Miss the best 5 days and only **4/49** remain positive. Miss the best 10 and only **2/49**.

### 4. Fat Tails Correlate with Returns (Statistically Significant)
- Tail ratio vs total return: **Spearman r=0.464, p=0.0008** (strong)
- Kurtosis vs total return: **Spearman r=0.316, p=0.027** (moderate)
- Skewness vs total return: **Spearman r=0.291, p=0.043** (moderate)
- Tokens with positive skew and fat tails tend to have better buy-and-hold returns

### 5. CPCV Tokens vs Non-CPCV: Similar Tail Structure
- CPCV tokens kurtosis mean: 43.4 (inflated by TRX outlier at 359)
- Non-CPCV tokens kurtosis mean: 10.2
- **Mann-Whitney U test: p=0.57 — NOT significantly different** (TRX is an outlier)
- CPCV median kurtosis (4.19) vs non-CPCV median (4.79) — essentially the same
- **Conclusion: CPCV robustness is NOT primarily about fat tails** — it's about something else (likely mean-reversion characteristics, regime consistency, or microstructure)

---

## PART 1: DISTRIBUTION ANALYSIS

### Kurtosis Rankings (Top 15 — Fattest Tails)

| Token | CPCV? | Kurtosis | Skewness | Top 5% PnL% | Top 1% PnL% | Max Up% | Max Down% |
|-------|-------|----------|----------|-------------|-------------|---------|-----------|
| TRX | YES | 358.84 | 15.42 | 28.7% | 14.9% | +96.1% | -23.7% |
| FIL | YES | 50.91 | 3.68 | 21.6% | 8.2% | +79.2% | -26.7% |
| ADA | | 50.77 | 3.64 | 21.5% | 8.4% | +72.2% | -24.4% |
| HBAR | | 37.50 | 4.05 | 24.9% | 10.4% | +72.9% | -22.8% |
| XLM | | 35.08 | 3.83 | 25.3% | 10.9% | +53.5% | -16.8% |
| BCH | | 34.57 | 2.95 | 20.7% | 7.1% | +58.4% | -15.3% |
| SHIB | | 27.52 | 2.96 | 23.4% | 9.0% | +59.5% | -18.9% |
| OM | YES | 23.80 | 0.38 | 28.6% | 11.0% | +64.7% | -83.9% |
| FLOKI | YES | 18.81 | 2.49 | 21.8% | 8.6% | +66.4% | -27.5% |
| PAXG | | 16.59 | -1.01 | 24.2% | 8.6% | +6.7% | -10.1% |
| DASH | | 15.54 | 2.42 | 25.9% | 10.1% | +49.6% | -19.5% |
| UNI | | 15.12 | 2.04 | 22.5% | 8.4% | +54.9% | -26.3% |
| ZEC | | 14.85 | 1.93 | 21.9% | 7.5% | +62.5% | -21.2% |
| TRUMP | | 11.36 | 1.16 | 26.6% | 9.1% | +44.0% | -28.5% |
| FET | | 10.32 | 1.44 | 18.8% | 6.5% | +57.8% | -29.5% |

### Kurtosis Rankings (Bottom 10 — Thinnest Tails)

| Token | CPCV? | Kurtosis | Skewness | Top 5% PnL% | Top 1% PnL% | Max Up% | Max Down% |
|-------|-------|----------|----------|-------------|-------------|---------|-----------|
| SOL | | 2.41 | 0.28 | 18.1% | 5.4% | +24.4% | -20.5% |
| BTC | | 2.41 | 0.37 | 20.2% | 5.7% | +11.9% | -8.5% |
| OP | | 2.39 | 0.11 | 18.4% | 5.3% | +26.3% | -29.6% |
| POL | | 2.36 | 0.00 | 19.3% | 6.0% | +15.3% | -22.3% |
| SEI | | 2.20 | 0.66 | 19.0% | 5.4% | +25.8% | -23.7% |
| WIF | | 2.13 | 0.65 | 18.7% | 5.2% | +36.4% | -34.4% |
| AVAX | YES | 2.13 | 0.05 | 18.0% | 5.1% | +20.5% | -27.1% |
| PENDLE | | 2.04 | 0.60 | 18.3% | 5.5% | +28.6% | -22.1% |
| INJ | | 1.88 | 0.21 | 17.5% | 5.3% | +23.0% | -29.2% |
| TAO | | 1.58 | 0.57 | 17.4% | 4.8% | +28.8% | -16.4% |

---

## PART 2: "MISS THE BEST DAYS" EFFECT

This proves that a small number of extreme days dominate total crypto returns.

### All 49 Tokens — Impact of Missing Best/Worst Days

| Token | CPCV? | Full 2Y Return | Miss Best 5 | Miss Best 10 | Miss Worst 5 |
|-------|-------|---------------|-------------|-------------|-------------|
| ZEC | | +991.0% | +143.5% | +2.7% | +2864.5% |
| PEPE | | +194.3% | -49.0% | -85.0% | +770.5% |
| TRX | YES | +166.1% | -7.6% | -33.8% | +446.9% |
| XRP | | +161.7% | -7.3% | -57.9% | +478.4% |
| BNB | | +149.2% | +34.7% | -12.4% | +308.4% |
| PAXG | | +138.5% | +90.5% | +64.3% | +220.1% |
| BCH | | +89.5% | -34.5% | -63.4% | +270.0% |
| BTC | | +78.2% | +10.0% | -25.6% | +167.6% |
| XLM | | +37.1% | -70.3% | -85.9% | +182.1% |
| SUI | YES | +36.7% | -58.1% | -82.5% | +265.7% |
| DASH | | +33.1% | -74.9% | -92.0% | +230.6% |
| PENDLE | | +27.5% | -60.1% | -84.4% | +259.8% |
| DOGE | | +13.3% | -59.0% | -81.0% | +175.7% |
| AAVE | | +11.1% | -58.8% | -79.5% | +181.9% |
| ETH | | +4.2% | -51.8% | -72.4% | +99.0% |
| HBAR | | +1.6% | -84.3% | -94.2% | +168.2% |
| SOL | | -3.9% | -55.0% | -73.9% | +119.8% |
| FLOKI | YES | -5.2% | -86.6% | -95.0% | +194.5% |
| OM | YES | -11.4% | -87.7% | -97.0% | +2114.9% |
| LTC | | -20.3% | -62.8% | -79.1% | +99.8% |
| LINK | | -35.7% | -74.7% | -86.0% | +54.2% |
| SHIB | | -36.2% | -85.7% | -93.0% | +50.5% |
| UNI | | -47.9% | -88.5% | -95.5% | +30.9% |
| ZRO | YES | -49.3% | -85.4% | -93.5% | +28.7% |
| CHZ | | -50.5% | -78.3% | -88.7% | +42.2% |
| BONK | YES | -52.0% | -88.8% | -96.4% | +60.5% |
| ADA | | -52.9% | -86.7% | -92.6% | +26.5% |
| CAKE | | -56.0% | -87.8% | -94.7% | +25.2% |
| NEAR | | -68.2% | -90.2% | -95.2% | -23.2% |
| TAO | | -68.2% | -87.7% | -94.0% | -24.8% |
| PENGU | YES | -72.8% | -92.9% | -97.5% | -8.6% |
| FET | | -74.4% | -93.8% | -97.3% | -25.5% |
| POL | | -74.7% | -87.2% | -92.8% | -40.0% |
| AVAX | YES | -75.9% | -88.7% | -94.0% | -35.7% |
| TON | | -78.3% | -88.4% | -91.7% | -49.3% |
| ICP | | -78.9% | -93.1% | -97.0% | -41.3% |
| DOT | YES | -82.0% | -92.0% | -95.4% | -52.5% |
| ENA | | -82.1% | -95.7% | -98.3% | -47.6% |
| WIF | | -83.5% | -95.5% | -98.5% | -37.9% |
| FIL | YES | -86.1% | -96.3% | -98.1% | -59.1% |
| APT | | -87.2% | -94.7% | -97.2% | -63.2% |
| SEI | | -87.6% | -95.7% | -98.1% | -67.7% |
| DENT | YES | -87.9% | -94.6% | -97.1% | -56.6% |
| WLD | | -88.6% | -97.7% | -99.2% | -62.0% |
| TRUMP | | -90.9% | -97.3% | -98.7% | -67.1% |
| INJ | | -90.9% | -96.5% | -98.3% | -73.9% |
| ALICE | | -91.2% | -98.0% | -99.1% | -64.8% |
| ARB | | -91.9% | -96.9% | -98.5% | -76.6% |
| OP | | -94.1% | -97.7% | -98.8% | -83.0% |

### Summary Statistics

| Scenario | Avg Return | Tokens Positive |
|----------|-----------|-----------------|
| Full 2-year return | -0.5% | 16/49 |
| Miss best 5 days | **-66.9%** | **4/49** |
| Miss best 10 days | **-81.7%** | **2/49** |
| Miss worst 5 days | **+181.2%** | 37/49 |

**Key insight:** 5 days out of ~760 (0.7%) determine whether you make or lose money. 10 days (1.3%) account for an 81 percentage point swing. This is the defining characteristic of fat-tailed returns — returns are dominated by a handful of extreme observations.

---

## PART 3: TRADEABLE TAIL QUALITY — COMPOSITE RANKING

Score = 30% kurtosis rank + 30% top-5% PnL concentration rank + 20% tail ratio rank + 20% daily volatility rank.

Higher score = fatter, more asymmetric, more volatile tails.

| Rank | Token | CPCV? | Score | Kurtosis | Skew | Max 1-Day | Tail Ratio | Top 5% PnL |
|------|-------|-------|-------|----------|------|-----------|------------|------------|
| 1 | OM | YES | 0.939 | 23.80 | 0.38 | +64.7% | 1.56 | 28.6% |
| 2 | HBAR | | 0.914 | 37.50 | 4.05 | +72.9% | 1.77 | 24.9% |
| 3 | DASH | | 0.843 | 15.54 | 2.42 | +49.6% | 1.52 | 25.9% |
| 4 | XLM | | 0.816 | 35.08 | 3.83 | +53.5% | 1.65 | 25.3% |
| 5 | FLOKI | YES | 0.814 | 18.81 | 2.49 | +66.4% | 1.47 | 21.8% |
| 6 | ZEC | | 0.794 | 14.85 | 1.93 | +62.5% | 1.50 | 21.9% |
| 7 | TRX | YES | 0.763 | 358.84 | 15.42 | +96.1% | 1.36 | 28.7% |
| 8 | SHIB | | 0.763 | 27.52 | 2.96 | +59.5% | 1.44 | 23.4% |
| 9 | PEPE | | 0.757 | 7.85 | 1.64 | +47.5% | 1.61 | 21.5% |
| 10 | UNI | | 0.753 | 15.12 | 2.04 | +54.9% | 1.43 | 22.5% |
| 11 | FIL | YES | 0.698 | 50.91 | 3.68 | +79.2% | 1.17 | 21.6% |
| 12 | TRUMP | | 0.692 | 11.36 | 1.16 | +44.0% | 1.08 | 26.6% |
| 13 | WLD | | 0.688 | 9.16 | 1.54 | +55.4% | 1.40 | 20.9% |
| 14 | ADA | | 0.671 | 50.77 | 3.64 | +72.2% | 1.24 | 21.5% |
| 15 | CAKE | | 0.655 | 9.41 | 1.27 | +41.1% | 1.26 | 22.9% |
| 16 | XRP | | 0.653 | 9.01 | 1.41 | +34.1% | 1.44 | 24.7% |
| 17 | ALICE | | 0.631 | 10.00 | 0.97 | +44.9% | 1.14 | 21.6% |
| 18 | BCH | | 0.606 | 34.57 | 2.95 | +58.4% | 1.32 | 20.7% |
| 19 | PENGU | YES | 0.588 | 2.90 | 0.94 | +40.4% | 1.48 | 20.1% |
| 20 | FET | | 0.576 | 10.32 | 1.44 | +57.8% | 1.33 | 18.8% |
| 21 | SUI | YES | 0.576 | 4.19 | 1.02 | +38.2% | 1.48 | 19.8% |
| 22 | ICP | | 0.551 | 4.98 | 0.79 | +31.9% | 1.34 | 20.3% |
| 23 | PAXG | | 0.547 | 16.59 | -1.01 | +6.7% | 1.08 | 24.2% |
| 24 | BONK | YES | 0.545 | 3.49 | 0.96 | +39.4% | 1.44 | 19.3% |
| 25 | ZRO | YES | 0.482 | 4.05 | 0.77 | +33.5% | 1.20 | 19.9% |
| 26 | ENA | | 0.467 | 3.50 | 0.98 | +46.3% | 1.43 | 18.0% |
| 27 | NEAR | | 0.439 | 5.02 | 0.88 | +38.9% | 1.21 | 18.9% |
| 28 | DOGE | | 0.420 | 3.38 | 0.62 | +26.9% | 1.22 | 20.1% |
| 29 | LTC | | 0.418 | 4.27 | -0.02 | +18.8% | 1.04 | 21.9% |
| 30 | WIF | | 0.416 | 2.13 | 0.65 | +36.4% | 1.37 | 18.7% |
| 31 | BNB | | 0.410 | 4.17 | 0.50 | +17.3% | 1.16 | 21.1% |
| 32 | LINK | | 0.404 | 4.59 | 0.62 | +33.4% | 1.21 | 19.4% |
| 33 | TON | | 0.386 | 6.82 | -0.42 | +22.6% | 0.81 | 21.0% |
| 34 | ETH | | 0.386 | 4.07 | 0.59 | +21.9% | 1.16 | 20.8% |
| 35 | ARB | | 0.365 | 3.51 | 0.27 | +27.9% | 1.08 | 19.8% |
| 36 | SEI | | 0.361 | 2.20 | 0.66 | +25.8% | 1.31 | 19.0% |
| 37 | DOT | YES | 0.351 | 5.22 | 0.21 | +28.4% | 1.07 | 19.6% |
| 38 | PENDLE | | 0.329 | 2.04 | 0.60 | +28.6% | 1.29 | 18.3% |
| 39 | APT | | 0.318 | 3.30 | 0.22 | +25.9% | 1.09 | 19.6% |
| 40 | AAVE | | 0.318 | 2.75 | 0.47 | +27.9% | 1.20 | 19.3% |
| 41 | BTC | | 0.282 | 2.41 | 0.37 | +11.9% | 1.14 | 20.2% |
| 42 | DENT | YES | 0.241 | 3.25 | -0.32 | +22.7% | 0.92 | 18.8% |
| 43 | TAO | | 0.237 | 1.58 | 0.57 | +28.8% | 1.24 | 17.4% |
| 44 | OP | | 0.220 | 2.39 | 0.11 | +26.3% | 1.04 | 18.4% |
| 45 | CHZ | | 0.220 | 2.48 | 0.09 | +24.4% | 1.09 | 18.4% |
| 46 | POL | | 0.192 | 2.36 | 0.00 | +15.3% | 1.02 | 19.3% |
| 47 | INJ | | 0.192 | 1.88 | 0.21 | +23.0% | 1.10 | 17.5% |
| 48 | SOL | | 0.182 | 2.41 | 0.28 | +24.4% | 1.13 | 18.1% |
| 49 | AVAX | YES | 0.131 | 2.13 | 0.05 | +20.5% | 1.07 | 18.0% |

### CPCV Tokens in the Tail Quality Ranking
- OM: Rank 1 (top tier tail quality)
- FLOKI: Rank 5
- TRX: Rank 7
- FIL: Rank 11
- PENGU: Rank 19
- SUI: Rank 21
- BONK: Rank 24
- ZRO: Rank 25
- DOT: Rank 37
- DENT: Rank 42
- AVAX: Rank 49

CPCV tokens span the FULL range of tail quality (rank 1 to rank 49). This confirms that **fat tails alone do not predict CPCV robustness**.

---

## PART 4: CPCV vs FAT TAILS — STATISTICAL COMPARISON

| Metric | CPCV Tokens (n=11) | Non-CPCV (n=38) | Difference |
|--------|-------------------|-----------------|------------|
| Kurtosis (mean) | 43.42 | 10.18 | +33.24 |
| Kurtosis (median) | 4.19 | 4.79 | -0.60 |
| Skewness | 2.33 | 1.09 | +1.24 |
| Top 5% PnL concentration | 21.5% | 20.9% | +0.6% |
| Top 1% PnL concentration | 7.7% | 6.9% | +0.8% |
| Tail ratio (up/down) | 1.29 | 1.26 | +0.03 |
| Max single-day up | 48.1% | 36.8% | +11.3% |
| Max single-day down | -31.9% | -22.8% | -9.1% |
| Daily volatility | 6.1% | 5.3% | +0.9% |

**Mann-Whitney U test** (CPCV vs non-CPCV kurtosis): U=233, **p=0.573** — NOT significant.

The mean kurtosis difference is entirely driven by TRX (kurtosis 359) — an extreme outlier. The medians are nearly identical.

### Correlations Between Tail Properties and Returns (Spearman)

| Metric | r | p-value | Interpretation |
|--------|---|---------|---------------|
| Tail Ratio vs Return | **0.464** | **0.0008** | Strong positive — tokens with fatter upside tails had better returns |
| Kurtosis vs Return | **0.316** | **0.027** | Moderate positive — fatter tails correlate with better returns |
| Skewness vs Return | **0.291** | **0.043** | Moderate positive — right-skewed tokens performed better |

---

## STRATEGIC IMPLICATIONS

### 1. Fat Tails Are Universal in Crypto
Every single token has fat-tailed returns. This is not a feature of specific tokens — it's a structural property of crypto markets. Any strategy that assumes normal returns will systematically misprice risk.

### 2. You MUST Be in the Market for the Fat Tail Days
Missing just 5 days (0.7% of the sample) turns the average token from flat to -67%. This means:
- **Time-in-market dominates timing.** Strategies that are frequently in cash miss fat tail days.
- **Our momentum strategy's 39% win rate with 2.37x payoff** is exactly the right profile: accept many small losses to capture the rare huge moves.
- **Stop losses on volatile tokens must be wide enough** to avoid being shaken out before the fat tail payoff arrives.

### 3. Tail Ratio Matters More Than Kurtosis for Strategy Selection
- Tail ratio (avg upside tail / avg downside tail) has the strongest correlation with returns (r=0.46)
- This means: tokens where extreme up-moves are bigger than extreme down-moves are better to be long
- Kurtosis alone does not distinguish good from bad tokens — it captures both upside and downside extremes equally

### 4. CPCV Robustness is NOT About Fat Tails
- CPCV tokens have essentially the same tail structure as non-CPCV tokens (p=0.57)
- CPCV robustness must come from other properties: temporal consistency of momentum signals, microstructure quality, or regime stability
- This is an important negative result: you cannot shortcut CPCV by screening on kurtosis

### 5. Token-Specific Fat Tail Properties
- **Extreme outliers** (TRX, FIL, ADA): kurtosis > 50. Returns are dominated by 1-2 massive days. Very hard to trade systematically.
- **High tail quality** (OM, HBAR, DASH, XLM, FLOKI): kurtosis 15-40, positive skew, good tail ratios. These have tradeable fat tails.
- **Moderate tails** (BTC, ETH, SOL): kurtosis 2-4. Still fat vs normal, but more Gaussian-like. Momentum strategies work differently here.
- **Thin(est) tails** (TAO, INJ, AVAX): kurtosis < 2.2. Closest to normal but still non-Gaussian.

### 6. The OM Anomaly
OM ranks #1 in tradeable tail quality AND is a CPCV token, but had -11.4% total return. Its max down day was -83.9% (likely a crash/delisting event). Missing worst 5 days would have given +2115% return. This is the quintessential fat-tail token: enormous upside potential destroyed by a single catastrophic event.
