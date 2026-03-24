# Hourly Positioning Dynamics -- IC Analysis Results

Analysis date: 2026-03-24
Data sources: Binance Top Trader L/S Position (hourly), Global L/S (hourly), Top Trader Account (hourly)
Price: BTC perpetual 1h (Binance)

## 1. Data Description

| Dataset | Records | Date Range | L/S Ratio Range |
|---------|---------|------------|-----------------|
| Top Trader Position (1h) | 500 | 2026-03-03 15:00 to 2026-03-24 10:00 | 0.8985 -- 1.4431 |
| Global L/S (1h) | 500 | 2026-03-03 15:00 to 2026-03-24 10:00 | 0.8113 -- 2.6193 |
| Top Trader Account (1h) | 500 | 2026-03-03 15:00 to 2026-03-24 10:00 | 0.8608 -- 3.2409 |
| Top Trader Position (daily) | 28 | 2026-02-25 to 2026-03-24 | 1.0042 -- 1.5689 |
| Global L/S (daily) | 28 | 2026-02-25 to 2026-03-24 | 0.8505 -- 2.3795 |
| BTC Perp 1h | 54425 | 2020-01-01 to 2026-03-17 | price data |

**Overlap period:** 2026-03-03 15:00 to 2026-03-17 16:00 (338 hourly bars, ~14 days)
**IS/OOS split:** 60/40 = IS 202 bars / OOS 136 bars

### Critical Sample Size Limitation

The Binance positioning API returns a max of 1000 hourly records (~41 days), but BTC perp price data
only extends to 2026-03-17, yielding just **338 overlapping hourly bars (~14 days)**.

| Horizon | Independent Obs | Reliability |
|---------|----------------|-------------|
| 1h | ~338 | HIGH |
| 4h | ~84 | HIGH |
| 8h | ~42 | MODERATE |
| 24h | ~14 | LOW |
| 72h | ~4 | UNRELIABLE |
| 168h | ~2 | UNRELIABLE |

## 2. IC Table: Signal x Horizon x Split

### Top Trader ROC

| Signal | Hz | IC IS | t IS | n IS | IC OOS | t OOS | n OOS | Sign | Pass |
|--------|----|-------|------|------|--------|-------|-------|------|------|
| top_roc_4h | 1h | -0.0096 | -0.13 | 198 | -0.0629 | -0.73 | 135 | Y |  |
| top_roc_4h | 4h | 0.0766 | 1.08 | 198 | 0.1607 | 1.86 | 132 | Y |  |
| top_roc_4h | 8h | 0.0988 | 1.39 | 198 | 0.0365 | 0.41 | 128 | Y |  |
| top_roc_4h | 24h | -0.0231 | -0.32 | 198 | 0.0589 | 0.62 | 112 | N |  |
| top_roc_4h | 72h | 0.0073 | 0.10 | 198 | 0.1223 | 0.97 | 64 | Y |  |
| top_roc_4h | 168h | -0.0034 | -0.04 | 166 | N/A | N/A | 0 | N |  |
| top_roc_8h | 1h | 0.1541 | 2.16 | 194 | -0.0205 | -0.24 | 135 | N |  |
| top_roc_8h | 4h | 0.1013 | 1.41 | 194 | 0.0507 | 0.58 | 132 | Y |  |
| top_roc_8h | 8h | 0.0304 | 0.42 | 194 | 0.0062 | 0.07 | 128 | Y |  |
| top_roc_8h | 24h | -0.1182 | -1.65 | 194 | 0.0061 | 0.06 | 112 | N |  |
| top_roc_8h | 72h | 0.0224 | 0.31 | 194 | 0.2537 | 2.06 | 64 | Y |  |
| top_roc_8h | 168h | 0.0194 | 0.25 | 162 | N/A | N/A | 0 | N |  |
| top_roc_24h | 1h | -0.0375 | -0.50 | 178 | 0.0235 | 0.27 | 135 | N |  |
| top_roc_24h | 4h | -0.0377 | -0.50 | 178 | 0.1922 | 2.23 | 132 | N |  |
| top_roc_24h | 8h | -0.0562 | -0.75 | 178 | 0.0924 | 1.04 | 128 | N |  |
| top_roc_24h | 24h | -0.1776 | -2.39 | 178 | -0.1125 | -1.19 | 112 | Y |  |
| top_roc_24h | 72h | 0.0396 | 0.53 | 178 | 0.5043 | 4.60 | 64 | Y |  |
| top_roc_24h | 168h | -0.0733 | -0.88 | 146 | N/A | N/A | 0 | N |  |

### Global ROC

| Signal | Hz | IC IS | t IS | n IS | IC OOS | t OOS | n OOS | Sign | Pass |
|--------|----|-------|------|------|--------|-------|-------|------|------|
| glob_roc_4h | 1h | -0.1043 | -1.47 | 198 | 0.0453 | 0.52 | 135 | N |  |
| glob_roc_4h | 4h | -0.0539 | -0.76 | 198 | 0.0906 | 1.04 | 132 | N |  |
| glob_roc_4h | 8h | -0.0368 | -0.52 | 198 | -0.0680 | -0.76 | 128 | Y |  |
| glob_roc_4h | 24h | -0.1742 | -2.48 | 198 | 0.0695 | 0.73 | 112 | N |  |
| glob_roc_4h | 72h | 0.0094 | 0.13 | 198 | 0.2452 | 1.99 | 64 | Y |  |
| glob_roc_4h | 168h | -0.0131 | -0.17 | 166 | N/A | N/A | 0 | N |  |
| glob_roc_8h | 1h | 0.0359 | 0.50 | 194 | 0.0209 | 0.24 | 135 | Y |  |
| glob_roc_8h | 4h | 0.0021 | 0.03 | 194 | -0.0064 | -0.07 | 132 | N |  |
| glob_roc_8h | 8h | -0.1406 | -1.97 | 194 | -0.2029 | -2.33 | 128 | Y |  |
| glob_roc_8h | 24h | -0.2041 | -2.89 | 194 | 0.0640 | 0.67 | 112 | N |  |
| glob_roc_8h | 72h | 0.0184 | 0.26 | 194 | 0.3568 | 3.01 | 64 | Y |  |
| glob_roc_8h | 168h | -0.0357 | -0.45 | 162 | N/A | N/A | 0 | N |  |
| glob_roc_24h | 1h | -0.0783 | -1.04 | 178 | -0.0386 | -0.45 | 135 | Y |  |
| glob_roc_24h | 4h | -0.1448 | -1.94 | 178 | 0.0773 | 0.88 | 132 | N |  |
| glob_roc_24h | 8h | -0.1392 | -1.86 | 178 | 0.0490 | 0.55 | 128 | N |  |
| glob_roc_24h | 24h | -0.1074 | -1.43 | 178 | 0.1149 | 1.21 | 112 | N |  |
| glob_roc_24h | 72h | 0.1727 | 2.33 | 178 | 0.5854 | 5.69 | 64 | Y | **PASS** |
| glob_roc_24h | 168h | 0.0209 | 0.25 | 146 | N/A | N/A | 0 | N |  |

### Top-vs-Global Divergence ROC

| Signal | Hz | IC IS | t IS | n IS | IC OOS | t OOS | n OOS | Sign | Pass |
|--------|----|-------|------|------|--------|-------|-------|------|------|
| div_roc_4h | 1h | 0.1007 | 1.42 | 198 | -0.0976 | -1.13 | 135 | N |  |
| div_roc_4h | 4h | 0.0506 | 0.71 | 198 | 0.0339 | 0.39 | 132 | Y |  |
| div_roc_4h | 8h | 0.0862 | 1.21 | 198 | 0.1173 | 1.33 | 128 | Y |  |
| div_roc_4h | 24h | 0.1927 | 2.75 | 198 | 0.0053 | 0.06 | 112 | Y |  |
| div_roc_4h | 72h | 0.0160 | 0.22 | 198 | -0.3527 | -2.97 | 64 | N |  |
| div_roc_4h | 168h | 0.0505 | 0.65 | 166 | N/A | N/A | 0 | N |  |
| div_roc_8h | 1h | 0.0167 | 0.23 | 194 | 0.0143 | 0.16 | 135 | Y |  |
| div_roc_8h | 4h | 0.0467 | 0.65 | 194 | 0.1063 | 1.22 | 132 | Y |  |
| div_roc_8h | 8h | 0.1908 | 2.69 | 194 | 0.3338 | 3.98 | 128 | Y | **PASS** |
| div_roc_8h | 24h | 0.2191 | 3.11 | 194 | -0.0594 | -0.62 | 112 | N |  |
| div_roc_8h | 72h | 0.0199 | 0.28 | 194 | -0.4235 | -3.68 | 64 | N |  |
| div_roc_8h | 168h | 0.0835 | 1.06 | 162 | N/A | N/A | 0 | N |  |
| div_roc_24h | 1h | 0.0869 | 1.16 | 178 | 0.0604 | 0.70 | 135 | Y |  |
| div_roc_24h | 4h | 0.1490 | 2.00 | 178 | 0.0327 | 0.37 | 132 | Y |  |
| div_roc_24h | 8h | 0.1433 | 1.92 | 178 | 0.0022 | 0.03 | 128 | Y |  |
| div_roc_24h | 24h | 0.0986 | 1.31 | 178 | -0.1990 | -2.13 | 112 | N |  |
| div_roc_24h | 72h | -0.1564 | -2.10 | 178 | -0.5817 | -5.63 | 64 | Y | **PASS** |
| div_roc_24h | 168h | -0.0370 | -0.44 | 146 | N/A | N/A | 0 | N |  |

### Level Divergence

| Signal | Hz | IC IS | t IS | n IS | IC OOS | t OOS | n OOS | Sign | Pass |
|--------|----|-------|------|------|--------|-------|-------|------|------|
| level_div | 1h | -0.0618 | -0.88 | 202 | -0.0241 | -0.28 | 135 | Y |  |
| level_div | 4h | -0.1201 | -1.71 | 202 | -0.0648 | -0.74 | 132 | Y |  |
| level_div | 8h | -0.1944 | -2.80 | 202 | -0.0793 | -0.89 | 128 | Y |  |
| level_div | 24h | -0.4857 | -7.86 | 202 | -0.3894 | -4.43 | 112 | Y | **PASS** |
| level_div | 72h | -0.7082 | -14.19 | 202 | -0.3990 | -3.43 | 64 | Y | **PASS** |
| level_div | 168h | -0.6489 | -11.05 | 170 | N/A | N/A | 0 | N |  |
| level_div_z24 | 1h | 0.0808 | 1.08 | 179 | 0.0271 | 0.31 | 135 | Y |  |
| level_div_z24 | 4h | 0.1641 | 2.21 | 179 | 0.1347 | 1.55 | 132 | Y |  |
| level_div_z24 | 8h | 0.1803 | 2.44 | 179 | 0.2472 | 2.86 | 128 | Y | **PASS** |
| level_div_z24 | 24h | 0.2202 | 3.00 | 179 | -0.1077 | -1.14 | 112 | N |  |
| level_div_z24 | 72h | 0.0099 | 0.13 | 179 | -0.5808 | -5.62 | 64 | N |  |
| level_div_z24 | 168h | 0.1778 | 2.18 | 147 | N/A | N/A | 0 | N |  |
| level_div_z72 | 1h | 0.0497 | 0.57 | 131 | -0.0060 | -0.07 | 135 | N |  |
| level_div_z72 | 4h | 0.1751 | 2.02 | 131 | -0.0013 | -0.02 | 132 | N |  |
| level_div_z72 | 8h | 0.1644 | 1.89 | 131 | 0.0357 | 0.40 | 128 | Y |  |
| level_div_z72 | 24h | 0.1612 | 1.86 | 131 | -0.3787 | -4.29 | 112 | N |  |
| level_div_z72 | 72h | -0.3661 | -4.47 | 131 | -0.5471 | -5.15 | 64 | Y | **PASS** |
| level_div_z72 | 168h | 0.3865 | 4.13 | 99 | N/A | N/A | 0 | N |  |

### Top Trader Z-Score

| Signal | Hz | IC IS | t IS | n IS | IC OOS | t OOS | n OOS | Sign | Pass |
|--------|----|-------|------|------|--------|-------|-------|------|------|
| top_z24 | 1h | 0.0443 | 0.59 | 179 | -0.0234 | -0.27 | 135 | N |  |
| top_z24 | 4h | 0.0047 | 0.06 | 179 | 0.1030 | 1.18 | 132 | Y |  |
| top_z24 | 8h | 0.0056 | 0.07 | 179 | 0.0176 | 0.20 | 128 | Y |  |
| top_z24 | 24h | -0.2184 | -2.98 | 179 | -0.0595 | -0.62 | 112 | Y |  |
| top_z24 | 72h | -0.0871 | -1.16 | 179 | 0.3513 | 2.95 | 64 | N |  |
| top_z24 | 168h | -0.1201 | -1.46 | 147 | N/A | N/A | 0 | N |  |
| top_z72 | 1h | 0.0364 | 0.41 | 131 | 0.0056 | 0.06 | 135 | Y |  |
| top_z72 | 4h | -0.0586 | -0.67 | 131 | 0.1045 | 1.20 | 132 | N |  |
| top_z72 | 8h | -0.0998 | -1.14 | 131 | 0.0308 | 0.35 | 128 | N |  |
| top_z72 | 24h | -0.4937 | -6.45 | 131 | -0.0110 | -0.12 | 112 | Y |  |
| top_z72 | 72h | -0.0708 | -0.81 | 131 | 0.4946 | 4.48 | 64 | N |  |
| top_z72 | 168h | -0.5657 | -6.76 | 99 | N/A | N/A | 0 | N |  |
| top_z168 | 1h | 0.1770 | 1.03 | 35 | 0.0143 | 0.16 | 135 | Y |  |
| top_z168 | 4h | 0.2541 | 1.51 | 35 | 0.1052 | 1.21 | 132 | Y |  |
| top_z168 | 8h | 0.1468 | 0.85 | 35 | -0.0002 | -0.00 | 128 | N |  |
| top_z168 | 24h | 0.1328 | 0.77 | 35 | 0.0433 | 0.46 | 112 | Y |  |
| top_z168 | 72h | 0.2387 | 1.41 | 35 | 0.4764 | 4.27 | 64 | Y |  |
| top_z168 | 168h | N/A | N/A | 3 | N/A | N/A | 0 | N |  |

### Global Z-Score

| Signal | Hz | IC IS | t IS | n IS | IC OOS | t OOS | n OOS | Sign | Pass |
|--------|----|-------|------|------|--------|-------|-------|------|------|
| glob_z24 | 1h | -0.0905 | -1.21 | 179 | 0.0071 | 0.08 | 135 | N |  |
| glob_z24 | 4h | -0.1655 | -2.23 | 179 | 0.0140 | 0.16 | 132 | N |  |
| glob_z24 | 8h | -0.2046 | -2.78 | 179 | -0.0954 | -1.08 | 128 | Y |  |
| glob_z24 | 24h | -0.2516 | -3.46 | 179 | 0.1371 | 1.45 | 112 | N |  |
| glob_z24 | 72h | -0.0099 | -0.13 | 179 | 0.5267 | 4.88 | 64 | N |  |
| glob_z24 | 168h | -0.1947 | -2.39 | 147 | N/A | N/A | 0 | N |  |
| glob_z72 | 1h | -0.0491 | -0.56 | 131 | 0.0118 | 0.14 | 135 | N |  |
| glob_z72 | 4h | -0.1568 | -1.80 | 131 | 0.0506 | 0.58 | 132 | N |  |
| glob_z72 | 8h | -0.1466 | -1.68 | 131 | -0.0151 | -0.17 | 128 | Y |  |
| glob_z72 | 24h | -0.1930 | -2.23 | 131 | 0.3326 | 3.70 | 112 | N |  |
| glob_z72 | 72h | 0.3803 | 4.67 | 131 | 0.5110 | 4.68 | 64 | Y | **PASS** |
| glob_z72 | 168h | -0.4113 | -4.44 | 99 | N/A | N/A | 0 | N |  |

### Account vs Position Divergence

| Signal | Hz | IC IS | t IS | n IS | IC OOS | t OOS | n OOS | Sign | Pass |
|--------|----|-------|------|------|--------|-------|-------|------|------|
| acct_pos_div | 1h | 0.0733 | 1.04 | 202 | 0.0225 | 0.26 | 135 | Y |  |
| acct_pos_div | 4h | 0.1173 | 1.67 | 202 | 0.0686 | 0.78 | 132 | Y |  |
| acct_pos_div | 8h | 0.2047 | 2.96 | 202 | 0.1010 | 1.14 | 128 | Y |  |
| acct_pos_div | 24h | 0.4978 | 8.12 | 202 | 0.3920 | 4.47 | 112 | Y | **PASS** |
| acct_pos_div | 72h | 0.6527 | 12.18 | 202 | 0.3829 | 3.26 | 64 | Y | **PASS** |
| acct_pos_div | 168h | 0.6018 | 9.77 | 170 | N/A | N/A | 0 | N |  |
| acct_pos_div_roc4 | 1h | -0.0830 | -1.17 | 198 | 0.1212 | 1.41 | 135 | N |  |
| acct_pos_div_roc4 | 4h | -0.0849 | -1.19 | 198 | -0.0467 | -0.53 | 132 | Y |  |
| acct_pos_div_roc4 | 8h | -0.0452 | -0.63 | 198 | -0.0924 | -1.04 | 128 | Y |  |
| acct_pos_div_roc4 | 24h | -0.1652 | -2.34 | 198 | -0.0095 | -0.10 | 112 | Y |  |
| acct_pos_div_roc4 | 72h | 0.0016 | 0.02 | 198 | 0.3527 | 2.97 | 64 | Y |  |
| acct_pos_div_roc4 | 168h | -0.0159 | -0.20 | 166 | N/A | N/A | 0 | N |  |

### Account ROC

| Signal | Hz | IC IS | t IS | n IS | IC OOS | t OOS | n OOS | Sign | Pass |
|--------|----|-------|------|------|--------|-------|-------|------|------|
| acct_roc_4h | 1h | -0.0916 | -1.29 | 198 | 0.0754 | 0.87 | 135 | N |  |
| acct_roc_4h | 4h | -0.0657 | -0.92 | 198 | 0.0226 | 0.26 | 132 | N |  |
| acct_roc_4h | 8h | -0.0248 | -0.35 | 198 | -0.0853 | -0.96 | 128 | Y |  |
| acct_roc_4h | 24h | -0.1538 | -2.18 | 198 | 0.0620 | 0.65 | 112 | N |  |
| acct_roc_4h | 72h | 0.0086 | 0.12 | 198 | 0.3027 | 2.50 | 64 | Y |  |
| acct_roc_4h | 168h | -0.0064 | -0.08 | 166 | N/A | N/A | 0 | N |  |
| acct_roc_8h | 1h | 0.0269 | 0.37 | 194 | 0.0082 | 0.09 | 135 | Y |  |
| acct_roc_8h | 4h | -0.0081 | -0.11 | 194 | -0.0361 | -0.41 | 132 | Y |  |
| acct_roc_8h | 8h | -0.1422 | -1.99 | 194 | -0.2382 | -2.75 | 128 | Y |  |
| acct_roc_8h | 24h | -0.1995 | -2.82 | 194 | 0.0782 | 0.82 | 112 | N |  |
| acct_roc_8h | 72h | 0.0040 | 0.06 | 194 | 0.3821 | 3.26 | 64 | Y |  |
| acct_roc_8h | 168h | -0.0255 | -0.32 | 162 | N/A | N/A | 0 | N |  |
| acct_roc_24h | 1h | -0.0976 | -1.30 | 178 | -0.0372 | -0.43 | 135 | Y |  |
| acct_roc_24h | 4h | -0.1517 | -2.04 | 178 | 0.0524 | 0.60 | 132 | N |  |
| acct_roc_24h | 8h | -0.1186 | -1.58 | 178 | 0.0407 | 0.46 | 128 | N |  |
| acct_roc_24h | 24h | -0.1009 | -1.35 | 178 | 0.1468 | 1.56 | 112 | N |  |
| acct_roc_24h | 72h | 0.1429 | 1.92 | 178 | 0.5990 | 5.89 | 64 | Y |  |
| acct_roc_24h | 168h | 0.0330 | 0.40 | 146 | N/A | N/A | 0 | N |  |

### Passing Signals (strict: |IC|>=0.02, |t|>=2.0, same sign, both splits)

- **glob_roc_24h @ 72h**: IC_IS=0.1727 (t=2.33), IC_OOS=0.5854 (t=5.69)
- **div_roc_8h @ 8h**: IC_IS=0.1908 (t=2.69), IC_OOS=0.3338 (t=3.98)
- **div_roc_24h @ 72h**: IC_IS=-0.1564 (t=-2.10), IC_OOS=-0.5817 (t=-5.63)
- **level_div @ 24h**: IC_IS=-0.4857 (t=-7.86), IC_OOS=-0.3894 (t=-4.43)
- **level_div @ 72h**: IC_IS=-0.7082 (t=-14.19), IC_OOS=-0.3990 (t=-3.43)
- **level_div_z24 @ 8h**: IC_IS=0.1803 (t=2.44), IC_OOS=0.2472 (t=2.86)
- **level_div_z72 @ 72h**: IC_IS=-0.3661 (t=-4.47), IC_OOS=-0.5471 (t=-5.15)
- **glob_z72 @ 72h**: IC_IS=0.3803 (t=4.67), IC_OOS=0.5110 (t=4.68)
- **acct_pos_div @ 24h**: IC_IS=0.4978 (t=8.12), IC_OOS=0.3920 (t=4.47)
- **acct_pos_div @ 72h**: IC_IS=0.6527 (t=12.18), IC_OOS=0.3829 (t=3.26)

### Notable Candidates (relaxed: |IC|>=0.03, |t|>=1.5 in either split)

| Signal | Hz | IC IS | t IS | IC OOS | t OOS | Sign |
|--------|----|-------|------|--------|-------|------|
| top_roc_4h | 4h | 0.0766 | 1.08 | 0.1607 | 1.86 | Y |
| top_roc_8h | 1h | 0.1541 | 2.16 | -0.0205 | -0.24 | N |
| top_roc_8h | 24h | -0.1182 | -1.65 | 0.0061 | 0.06 | N |
| top_roc_8h | 72h | 0.0224 | 0.31 | 0.2537 | 2.06 | Y |
| top_roc_24h | 4h | -0.0377 | -0.50 | 0.1922 | 2.23 | N |
| top_roc_24h | 24h | -0.1776 | -2.39 | -0.1125 | -1.19 | Y |
| top_roc_24h | 72h | 0.0396 | 0.53 | 0.5043 | 4.60 | Y |
| glob_roc_4h | 24h | -0.1742 | -2.48 | 0.0695 | 0.73 | N |
| glob_roc_4h | 72h | 0.0094 | 0.13 | 0.2452 | 1.99 | Y |
| glob_roc_8h | 8h | -0.1406 | -1.97 | -0.2029 | -2.33 | Y |
| glob_roc_8h | 24h | -0.2041 | -2.89 | 0.0640 | 0.67 | N |
| glob_roc_8h | 72h | 0.0184 | 0.26 | 0.3568 | 3.01 | Y |
| glob_roc_24h | 4h | -0.1448 | -1.94 | 0.0773 | 0.88 | N |
| glob_roc_24h | 8h | -0.1392 | -1.86 | 0.0490 | 0.55 | N |
| glob_roc_24h | 72h | 0.1727 | 2.33 | 0.5854 | 5.69 | Y |
| div_roc_4h | 24h | 0.1927 | 2.75 | 0.0053 | 0.06 | Y |
| div_roc_4h | 72h | 0.0160 | 0.22 | -0.3527 | -2.97 | N |
| div_roc_8h | 8h | 0.1908 | 2.69 | 0.3338 | 3.98 | Y |
| div_roc_8h | 24h | 0.2191 | 3.11 | -0.0594 | -0.62 | N |
| div_roc_8h | 72h | 0.0199 | 0.28 | -0.4235 | -3.68 | N |
| div_roc_24h | 4h | 0.1490 | 2.00 | 0.0327 | 0.37 | Y |
| div_roc_24h | 8h | 0.1433 | 1.92 | 0.0022 | 0.03 | Y |
| div_roc_24h | 24h | 0.0986 | 1.31 | -0.1990 | -2.13 | N |
| div_roc_24h | 72h | -0.1564 | -2.10 | -0.5817 | -5.63 | Y |
| level_div | 4h | -0.1201 | -1.71 | -0.0648 | -0.74 | Y |
| level_div | 8h | -0.1944 | -2.80 | -0.0793 | -0.89 | Y |
| level_div | 24h | -0.4857 | -7.86 | -0.3894 | -4.43 | Y |
| level_div | 72h | -0.7082 | -14.19 | -0.3990 | -3.43 | Y |
| level_div | 168h | -0.6489 | -11.05 | nan | nan | N |
| level_div_z24 | 4h | 0.1641 | 2.21 | 0.1347 | 1.55 | Y |
| level_div_z24 | 8h | 0.1803 | 2.44 | 0.2472 | 2.86 | Y |
| level_div_z24 | 24h | 0.2202 | 3.00 | -0.1077 | -1.14 | N |
| level_div_z24 | 72h | 0.0099 | 0.13 | -0.5808 | -5.62 | N |
| level_div_z24 | 168h | 0.1778 | 2.18 | nan | nan | N |
| level_div_z72 | 4h | 0.1751 | 2.02 | -0.0013 | -0.02 | N |
| level_div_z72 | 8h | 0.1644 | 1.89 | 0.0357 | 0.40 | Y |
| level_div_z72 | 24h | 0.1612 | 1.86 | -0.3787 | -4.29 | N |
| level_div_z72 | 72h | -0.3661 | -4.47 | -0.5471 | -5.15 | Y |
| level_div_z72 | 168h | 0.3865 | 4.13 | nan | nan | N |
| top_z24 | 24h | -0.2184 | -2.98 | -0.0595 | -0.62 | Y |
| top_z24 | 72h | -0.0871 | -1.16 | 0.3513 | 2.95 | N |
| top_z72 | 24h | -0.4937 | -6.45 | -0.0110 | -0.12 | Y |
| top_z72 | 72h | -0.0708 | -0.81 | 0.4946 | 4.48 | N |
| top_z72 | 168h | -0.5657 | -6.76 | nan | nan | N |
| top_z168 | 4h | 0.2541 | 1.51 | 0.1052 | 1.21 | Y |
| top_z168 | 72h | 0.2387 | 1.41 | 0.4764 | 4.27 | Y |
| glob_z24 | 4h | -0.1655 | -2.23 | 0.0140 | 0.16 | N |
| glob_z24 | 8h | -0.2046 | -2.78 | -0.0954 | -1.08 | Y |
| glob_z24 | 24h | -0.2516 | -3.46 | 0.1371 | 1.45 | N |
| glob_z24 | 72h | -0.0099 | -0.13 | 0.5267 | 4.88 | N |
| glob_z24 | 168h | -0.1947 | -2.39 | nan | nan | N |
| glob_z72 | 4h | -0.1568 | -1.80 | 0.0506 | 0.58 | N |
| glob_z72 | 8h | -0.1466 | -1.68 | -0.0151 | -0.17 | Y |
| glob_z72 | 24h | -0.1930 | -2.23 | 0.3326 | 3.70 | N |
| glob_z72 | 72h | 0.3803 | 4.67 | 0.5110 | 4.68 | Y |
| glob_z72 | 168h | -0.4113 | -4.44 | nan | nan | N |
| acct_pos_div | 4h | 0.1173 | 1.67 | 0.0686 | 0.78 | Y |
| acct_pos_div | 8h | 0.2047 | 2.96 | 0.1010 | 1.14 | Y |
| acct_pos_div | 24h | 0.4978 | 8.12 | 0.3920 | 4.47 | Y |
| acct_pos_div | 72h | 0.6527 | 12.18 | 0.3829 | 3.26 | Y |
| acct_pos_div | 168h | 0.6018 | 9.77 | nan | nan | N |
| acct_pos_div_roc4 | 24h | -0.1652 | -2.34 | -0.0095 | -0.10 | Y |
| acct_pos_div_roc4 | 72h | 0.0016 | 0.02 | 0.3527 | 2.97 | Y |
| acct_roc_4h | 24h | -0.1538 | -2.18 | 0.0620 | 0.65 | N |
| acct_roc_4h | 72h | 0.0086 | 0.12 | 0.3027 | 2.50 | Y |
| acct_roc_8h | 8h | -0.1422 | -1.99 | -0.2382 | -2.75 | Y |
| acct_roc_8h | 24h | -0.1995 | -2.82 | 0.0782 | 0.82 | N |
| acct_roc_8h | 72h | 0.0040 | 0.06 | 0.3821 | 3.26 | Y |
| acct_roc_24h | 4h | -0.1517 | -2.04 | 0.0524 | 0.60 | N |
| acct_roc_24h | 8h | -0.1186 | -1.58 | 0.0407 | 0.46 | N |
| acct_roc_24h | 24h | -0.1009 | -1.35 | 0.1468 | 1.56 | N |
| acct_roc_24h | 72h | 0.1429 | 1.92 | 0.5990 | 5.89 | Y |

## 3. Residual IC (After Removing Daily Signal)

Methodology: OLS-regress each hourly signal on [top_ls_daily, glob_ls_daily],
then compute Spearman IC of the residual against forward returns.

### Notable Residual ICs (|IC|>=0.03, |t|>=1.5)

| Signal | Hz | Split | Resid IC | t | n |
|--------|----|-------|----------|---|---|
| top_roc_4h | 4h | OOS | 0.1723 | 1.99 | 132 |
| top_roc_4h | 8h | IS | 0.1088 | 1.53 | 198 |
| top_roc_24h | 4h | OOS | 0.2113 | 2.47 | 132 |
| top_roc_24h | 8h | OOS | 0.1599 | 1.82 | 128 |
| top_roc_24h | 24h | IS | -0.1721 | -2.32 | 178 |
| glob_roc_4h | 72h | IS | 0.1202 | 1.70 | 198 |
| glob_roc_8h | 8h | OOS | -0.2000 | -2.29 | 128 |
| glob_roc_8h | 24h | IS | -0.1156 | -1.61 | 194 |
| glob_roc_8h | 72h | IS | 0.1566 | 2.20 | 194 |
| glob_roc_24h | 4h | IS | -0.1510 | -2.03 | 178 |
| glob_roc_24h | 8h | IS | -0.1388 | -1.86 | 178 |
| glob_roc_24h | 24h | OOS | 0.1655 | 1.76 | 112 |
| glob_roc_24h | 72h | IS | 0.1498 | 2.01 | 178 |
| div_roc_4h | 8h | OOS | 0.1360 | 1.54 | 128 |
| div_roc_4h | 24h | IS | 0.1122 | 1.58 | 198 |
| div_roc_4h | 72h | IS | -0.1156 | -1.63 | 198 |
| div_roc_4h | 72h | OOS | -0.2446 | -1.99 | 64 |
| div_roc_8h | 8h | IS | 0.1276 | 1.78 | 194 |
| div_roc_8h | 8h | OOS | 0.3397 | 4.05 | 128 |
| div_roc_8h | 72h | IS | -0.1741 | -2.45 | 194 |
| div_roc_24h | 4h | IS | 0.1580 | 2.12 | 178 |
| div_roc_24h | 8h | IS | 0.1287 | 1.72 | 178 |
| div_roc_24h | 72h | IS | -0.1445 | -1.94 | 178 |
| level_div | 8h | IS | -0.1160 | -1.65 | 202 |
| level_div | 8h | OOS | 0.1690 | 1.92 | 128 |
| level_div | 24h | IS | -0.4201 | -6.55 | 202 |
| level_div | 24h | OOS | -0.2049 | -2.20 | 112 |
| level_div | 72h | IS | -0.5565 | -9.47 | 202 |
| level_div_z24 | 8h | IS | 0.1157 | 1.55 | 179 |
| level_div_z24 | 8h | OOS | 0.2297 | 2.65 | 128 |
| level_div_z24 | 72h | IS | -0.1798 | -2.43 | 179 |
| level_div_z24 | 72h | OOS | -0.2270 | -1.84 | 64 |
| level_div_z72 | 4h | IS | 0.2310 | 2.70 | 131 |
| level_div_z72 | 8h | IS | 0.2759 | 3.26 | 131 |
| level_div_z72 | 8h | OOS | 0.1575 | 1.79 | 128 |
| level_div_z72 | 24h | IS | 0.3541 | 4.30 | 131 |
| level_div_z72 | 24h | OOS | -0.3209 | -3.55 | 112 |
| top_z24 | 24h | IS | -0.1480 | -1.99 | 179 |
| top_z72 | 4h | OOS | 0.1768 | 2.05 | 132 |
| top_z72 | 24h | IS | -0.4911 | -6.40 | 131 |
| top_z72 | 24h | OOS | 0.2071 | 2.22 | 112 |
| top_z72 | 72h | IS | -0.1388 | -1.59 | 131 |
| top_z168 | 4h | IS | 0.4370 | 2.79 | 35 |
| top_z168 | 4h | OOS | 0.2119 | 2.47 | 132 |
| top_z168 | 8h | OOS | 0.1390 | 1.58 | 128 |
| top_z168 | 24h | OOS | 0.2583 | 2.80 | 112 |
| glob_z24 | 4h | IS | -0.1140 | -1.53 | 179 |
| glob_z24 | 8h | IS | -0.1240 | -1.66 | 179 |
| glob_z24 | 72h | IS | 0.1752 | 2.37 | 179 |
| glob_z72 | 4h | IS | -0.2186 | -2.54 | 131 |
| glob_z72 | 8h | IS | -0.2662 | -3.14 | 131 |
| glob_z72 | 24h | IS | -0.3836 | -4.72 | 131 |
| glob_z72 | 24h | OOS | 0.3444 | 3.85 | 112 |
| acct_pos_div | 8h | IS | 0.1202 | 1.71 | 202 |
| acct_pos_div | 8h | OOS | -0.1677 | -1.91 | 128 |
| acct_pos_div | 24h | IS | 0.4093 | 6.34 | 202 |
| acct_pos_div | 72h | IS | 0.5197 | 8.60 | 202 |
| acct_pos_div_roc4 | 72h | OOS | 0.2484 | 2.02 | 64 |
| acct_roc_4h | 72h | IS | 0.1370 | 1.94 | 198 |
| acct_roc_4h | 72h | OOS | 0.2323 | 1.88 | 64 |
| acct_roc_8h | 8h | OOS | -0.2353 | -2.72 | 128 |
| acct_roc_8h | 72h | IS | 0.1953 | 2.76 | 194 |
| acct_roc_24h | 4h | IS | -0.1526 | -2.05 | 178 |
| acct_roc_24h | 8h | IS | -0.1156 | -1.54 | 178 |
| acct_roc_24h | 24h | OOS | 0.1763 | 1.88 | 112 |
| acct_roc_24h | 72h | IS | 0.1616 | 2.17 | 178 |

### Residual Signals Passing in Both Splits

- **acct_roc_4h @ 72h**: IS resid_IC=0.1370 (t=1.94), OOS resid_IC=0.2323 (t=1.88)
- **div_roc_4h @ 72h**: IS resid_IC=-0.1156 (t=-1.63), OOS resid_IC=-0.2446 (t=-1.99)
- **div_roc_8h @ 8h**: IS resid_IC=0.1276 (t=1.78), OOS resid_IC=0.3397 (t=4.05)
- **level_div @ 24h**: IS resid_IC=-0.4201 (t=-6.55), OOS resid_IC=-0.2049 (t=-2.20)
- **level_div_z24 @ 72h**: IS resid_IC=-0.1798 (t=-2.43), OOS resid_IC=-0.2270 (t=-1.84)
- **level_div_z24 @ 8h**: IS resid_IC=0.1157 (t=1.55), OOS resid_IC=0.2297 (t=2.65)
- **level_div_z72 @ 8h**: IS resid_IC=0.2759 (t=3.26), OOS resid_IC=0.1575 (t=1.79)
- **top_z168 @ 4h**: IS resid_IC=0.4370 (t=2.79), OOS resid_IC=0.2119 (t=2.47)

## 4. Intraday Patterns

IC of select signals vs 24h forward returns, conditioned on hour of day (UTC):

### top_roc_4h

| Hour (UTC) | IC | t | n |
|------------|-----|---|---|
| 00:00 | -0.0165 | -0.05 | 13 |
| 04:00 | -0.0165 | -0.05 | 13 |
| 08:00 | -0.0385 | -0.13 | 13 |
| 12:00 | 0.4505 | 1.67 | 13 |
| 16:00 | -0.3187 | -1.12 | 13 |
| 20:00 | -0.0275 | -0.09 | 13 |

### glob_roc_8h

| Hour (UTC) | IC | t | n |
|------------|-----|---|---|
| 00:00 | -0.6154 | -2.59 | 13 |
| 04:00 | 0.1538 | 0.52 | 13 |
| 08:00 | -0.3132 | -1.09 | 13 |
| 12:00 | 0.2033 | 0.69 | 13 |
| 16:00 | -0.3846 | -1.38 | 13 |
| 20:00 | -0.3147 | -1.05 | 12 |

### div_roc_8h

| Hour (UTC) | IC | t | n |
|------------|-----|---|---|
| 00:00 | 0.6703 | 3.00 | 13 |
| 04:00 | -0.2088 | -0.71 | 13 |
| 08:00 | -0.1209 | -0.40 | 13 |
| 12:00 | -0.1868 | -0.63 | 13 |
| 16:00 | 0.4286 | 1.57 | 13 |
| 20:00 | 0.4196 | 1.46 | 12 |

### level_div_z24

| Hour (UTC) | IC | t | n |
|------------|-----|---|---|
| 00:00 | 0.4615 | 1.65 | 12 |
| 04:00 | 0.0490 | 0.15 | 12 |
| 08:00 | 0.0000 | 0.00 | 12 |
| 12:00 | -0.2098 | -0.68 | 12 |
| 16:00 | 0.3791 | 1.36 | 13 |
| 20:00 | 0.2378 | 0.77 | 12 |

### top_z72

| Hour (UTC) | IC | t | n |
|------------|-----|---|---|
| 00:00 | -0.0788 | -0.22 | 10 |
| 04:00 | -0.0667 | -0.19 | 10 |
| 08:00 | -0.3939 | -1.21 | 10 |
| 12:00 | -0.2848 | -0.84 | 10 |
| 16:00 | -0.5818 | -2.15 | 11 |
| 20:00 | -0.2121 | -0.61 | 10 |

**Note:** With ~14 days of data, each 4-hour bucket has ~14 observations. These results are directional only, not statistically reliable.

## 5. Comparison with Daily Signal Benchmarks

| Signal | Horizon | IC | t-stat | Data Period | Independent Obs |
|--------|---------|-----|--------|------------|-----------------|
| Daily Top Trader L/S (reference) | 14d | -0.166 | -6.91 | Multi-year | ~100+ |
| Daily L/S Divergence (reference) | 14d | -0.204 | -8.57 | Multi-year | ~100+ |
| Best hourly signal (this study) | varies | see above | see above | 14 days | <15 at 24h+ |

The daily signals were computed over years of data with hundreds of independent observations.
Direct IC magnitude comparison is not meaningful due to the sample size disparity.

## 6. Verdict

**CONDITIONAL PASS — NEEDS MORE DATA**

10 signal(s) passed strict thresholds, but 338 bars (~14 days) is critically small. Effective independent observations are insufficient. Re-test with 90+ days.

### Detailed Reasoning

1. **Insufficient overlap**: Only ~14 days of hourly positioning data overlaps with BTC price data.
   For meaningful IC testing, we need at minimum 90 days (2,160+ hourly bars), ideally 180+ days.

2. **Overlapping returns problem**: At 24h horizon, consecutive hourly observations share 23/24 of their
   forward return window. This inflates t-statistics by approximately sqrt(overlap_factor), making results
   appear ~5x more significant than they truly are. Effective independent N at 24h is ~14, not ~340.
   At 72h, effective independent N is ~4, making any result at that horizon essentially meaningless.

3. **10 signals passed strict thresholds, but nearly all at unreliable horizons**:
   - 6 of 10 passes are at the 72h horizon (effective N ~ 4 independent observations)
   - 3 of 10 passes are at the 24h horizon (effective N ~ 14)
   - Only 2 passes at 8h horizon (effective N ~ 42): `div_roc_8h` (IC=0.19/0.33) and `level_div_z24` (IC=0.18/0.25)
   - The 72h passes show absurdly high IC magnitudes (0.40-0.71) which are a classic sign of overfitting
     to a tiny effective sample. No legitimate positioning signal should have IC > 0.30.
   - The enormous ICs at 72h for `level_div` (IC=-0.71) and `acct_pos_div` (IC=0.65) are almost certainly
     capturing a single market regime move (the ~14-day trend), not a repeatable signal.

4. **Two 8h-horizon candidates deserve a watchlist**:
   - `div_roc_8h @ 8h`: IS IC=0.19 (t=2.69), OOS IC=0.33 (t=3.98) -- divergence in 8h ROC between
     top traders and global L/S predicts 8h forward returns. Plausible mechanism (smart vs dumb money
     flow divergence). Residual IC also positive in both splits (IS=0.13, OOS=0.34).
   - `level_div_z24 @ 8h`: IS IC=0.18 (t=2.44), OOS IC=0.25 (t=2.86) -- z-scored level divergence.
   - However, 42 effective independent observations is still marginal. These need 90+ days to confirm.

5. **Residual analysis**: 8 signals show residual IC passing relaxed thresholds in both splits after
   removing the daily signal effect. The strongest is `level_div @ 24h` (resid IC=-0.42/-0.20),
   suggesting the hourly-granularity divergence level contains information beyond what the daily
   snapshot captures. But again, effective N at 24h is ~14 -- far too few.

6. **Recommendation**: Set up a scheduled hourly fetch (cron or similar) to accumulate 90+ days of
   hourly positioning data. The daily signals remain the gold standard for positioning overlays.
   Do not add hourly positioning to any strategy based on this 14-day sample.
   Watchlist for re-evaluation at 90 days: `div_roc_8h`, `level_div_z24`, `level_div` (level).

### Effective Independent Observations (Newey-West corrected)

| Horizon | Raw N (IS) | Autocorr(1) | Effective N | Reliable? |
|---------|-----------|-------------|-------------|-----------|
| 1h | 337 | -0.019 | ~337 | Yes |
| 4h | 334 | 0.742 | ~83 | Yes |
| 8h | 330 | 0.880 | ~42 | Marginal |
| 24h | 314 | 0.962 | ~13 | No |
| 72h | 266 | 0.981 | ~4 | No |
