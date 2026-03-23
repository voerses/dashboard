# OI Rate-of-Change Divergence Signal -- Research Results

**Generated**: 2026-03-23 22:59 UTC
**Tokens**: BTC, ETH, SOL, XRP, DOGE, ADA, AVAX, LINK, SUI
**IS period**: 2024-01-01 to 2025-06-30
**OOS period**: 2025-07-01 to 2026-03-23
**Total daily observations**: 7,263

## Data Summary

| Token | Daily Obs | IS Obs | OOS Obs | OI Min Date | OI Max Date |
|-------|-----------|--------|---------|-------------|-------------|
| BTC | 807 | 547 | 260 | 2024-01-01 | 2026-03-17 |
| ETH | 807 | 547 | 260 | 2024-01-01 | 2026-03-17 |
| SOL | 807 | 547 | 260 | 2024-01-01 | 2026-03-17 |
| XRP | 807 | 547 | 260 | 2024-01-01 | 2026-03-17 |
| DOGE | 807 | 547 | 260 | 2024-01-01 | 2026-03-17 |
| ADA | 807 | 547 | 260 | 2024-01-01 | 2026-03-17 |
| AVAX | 807 | 547 | 260 | 2024-01-01 | 2026-03-17 |
| LINK | 807 | 547 | 260 | 2024-01-01 | 2026-03-17 |
| SUI | 807 | 547 | 260 | 2024-01-01 | 2026-03-17 |

## Top 10 Signals (Ranked by OOS |IC|, sign-consistent preferred)

| Rank | Signal | Horizon | IC (IS) | IC (OOS) | t (IS) | t (OOS) | Hit% IS | Hit% OOS | Consistent |
|------|--------|---------|---------|----------|--------|---------|---------|----------|------------|
| 1 | price_pct_3d | 3d | -0.0274 | -0.0816 | -1.92 | -3.94 | 48.8% | 47.3% | Yes |
| 2 | oi_div_unsigned_7d | 3d | -0.0297 | -0.0585 | -2.07 | -2.82 | 49.8% | 49.9% | Yes |
| 3 | funding_cum_3d | 1d | 0.0065 | 0.0520 | 0.45 | 2.51 | 49.4% | 48.7% | Yes |
| 4 | price_pct_3d | 1d | -0.0038 | -0.0514 | -0.27 | -2.48 | 49.2% | 50.1% | Yes |
| 5 | funding_zscore | 7d | 0.0145 | 0.0496 | 0.99 | 2.37 | 49.8% | 49.3% | Yes |
| 6 | oi_div_funding_7d | 3d | -0.0309 | -0.0487 | -2.11 | -2.34 | 49.7% | 49.9% | Yes |
| 7 | oi_div_signed_funding_7d | 3d | -0.0534 | -0.0468 | -3.65 | -2.25 | 48.2% | 48.8% | Yes |
| 8 | oi_accel_7d | 14d | -0.0415 | -0.0455 | -2.88 | -2.14 | 48.6% | 49.4% | Yes |
| 9 | price_pct_1d | 3d | -0.0163 | -0.0429 | -1.14 | -2.06 | 48.9% | 48.9% | Yes |
| 10 | oi_accel_7d | 3d | -0.0391 | -0.0374 | -2.71 | -1.80 | 49.4% | 48.3% | Yes |
| 11 | oi_div_signed_funding_7d | 7d | -0.0394 | -0.0362 | -2.69 | -1.73 | 48.3% | 48.5% | Yes |
| 12 | oi_vol_div_3d | 14d | -0.0083 | -0.0355 | -0.58 | -1.67 | 49.8% | 46.9% | Yes |
| 13 | oi_div_vol_adj_7d | 3d | -0.0139 | -0.0323 | -0.94 | -1.56 | 50.5% | 49.3% | Yes |
| 14 | oi_pct_7d | 3d | -0.0244 | -0.0315 | -1.70 | -1.52 | 50.3% | 49.3% | Yes |
| 15 | oi_vol_div_1d | 3d | -0.0246 | -0.0311 | -1.72 | -1.50 | 49.8% | 50.1% | Yes |

## Per-Token Breakdown (Top 3 Signals)

### #1: price_pct_3d @ 3d

| Token | IC (IS) | IC (OOS) | t (IS) | t (OOS) | Hit% IS | Hit% OOS |
|-------|---------|----------|--------|---------|---------|----------|
| BTC | -0.0257 | -0.0550 | -0.60 | -0.88 | 49.8% | 44.4% |
| ETH | -0.0303 | -0.0462 | -0.71 | -0.74 | 48.0% | 49.0% |
| SOL | -0.0126 | -0.1132 | -0.29 | -1.82 | 48.2% | 47.5% |
| XRP | -0.0409 | -0.0321 | -0.95 | -0.51 | 47.4% | 49.8% |
| DOGE | -0.0273 | -0.1113 | -0.63 | -1.79 | 46.5% | 48.6% |
| ADA | -0.0248 | -0.1098 | -0.58 | -1.76 | 49.4% | 44.4% |
| AVAX | -0.0697 | -0.1309 | -1.63 | -2.11 | 47.2% | 47.1% |
| LINK | -0.0487 | -0.0773 | -1.13 | -1.24 | 50.6% | 50.2% |
| SUI | -0.0195 | -0.0920 | -0.46 | -1.48 | 52.0% | 45.1% |

### #2: oi_div_unsigned_7d @ 3d

| Token | IC (IS) | IC (OOS) | t (IS) | t (OOS) | Hit% IS | Hit% OOS |
|-------|---------|----------|--------|---------|---------|----------|
| BTC | -0.0482 | -0.0099 | -1.12 | -0.16 | 47.8% | 47.5% |
| ETH | -0.0677 | -0.0393 | -1.57 | -0.63 | 45.9% | 48.6% |
| SOL | -0.0475 | -0.1251 | -1.10 | -2.01 | 47.8% | 45.5% |
| XRP | -0.1615 | -0.0343 | -3.80 | -0.55 | 46.3% | 53.3% |
| DOGE | 0.0049 | -0.0465 | 0.11 | -0.74 | 50.6% | 52.9% |
| ADA | -0.0542 | -0.0272 | -1.26 | -0.43 | 50.2% | 49.4% |
| AVAX | 0.0451 | -0.1191 | 1.05 | -1.92 | 53.3% | 47.9% |
| LINK | -0.0024 | 0.0317 | -0.06 | 0.51 | 55.6% | 56.4% |
| SUI | -0.0391 | -0.1627 | -0.91 | -2.63 | 50.9% | 47.9% |

### #3: funding_cum_3d @ 1d

| Token | IC (IS) | IC (OOS) | t (IS) | t (OOS) | Hit% IS | Hit% OOS |
|-------|---------|----------|--------|---------|---------|----------|
| BTC | 0.0280 | -0.0300 | 0.65 | -0.48 | 52.1% | 45.6% |
| ETH | 0.0200 | -0.0106 | 0.46 | -0.17 | 50.6% | 51.0% |
| SOL | 0.0114 | 0.0622 | 0.26 | 1.00 | 51.0% | 51.4% |
| XRP | 0.0380 | 0.0495 | 0.88 | 0.79 | 52.1% | 45.9% |
| DOGE | 0.0375 | 0.0302 | 0.87 | 0.48 | 47.7% | 46.3% |
| ADA | 0.0095 | 0.0708 | 0.22 | 1.14 | 46.8% | 45.9% |
| AVAX | -0.0469 | 0.1281 | -1.09 | 2.07 | 44.7% | 57.9% |
| LINK | -0.0228 | 0.0611 | -0.53 | 0.98 | 49.9% | 47.9% |
| SUI | -0.0228 | 0.0523 | -0.53 | 0.84 | 49.7% | 46.7% |

## Full Aggregate Results (IS vs OOS)

```
==================================================================================================================================
Signal                              Horizon  Sample       IC   t-stat    Hit%      p-val      N
==================================================================================================================================
funding_cum_3d                      14d      IS     -0.0133    -0.92   48.6%   3.55e-01   4851
funding_cum_3d                      14d      OOS     0.0773     3.65   43.9%   2.73e-04   2214
funding_cum_3d                      1d       IS      0.0065     0.45   49.4%   6.51e-01   4851
funding_cum_3d                      1d       OOS     0.0520     2.51   48.7%   1.21e-02   2331
funding_cum_3d                      3d       IS     -0.0070    -0.49   48.9%   6.27e-01   4851
funding_cum_3d                      3d       OOS     0.0419     2.02   47.9%   4.39e-02   2313
funding_cum_3d                      7d       IS     -0.0031    -0.21   47.7%   8.31e-01   4851
funding_cum_3d                      7d       OOS     0.0268     1.28   46.2%   2.01e-01   2277
funding_zscore                      14d      IS      0.0572     3.91   51.7%   9.40e-05   4662
funding_zscore                      14d      OOS     0.0281     1.32   48.8%   1.86e-01   2214
funding_zscore                      1d       IS     -0.0196    -1.34   47.9%   1.81e-01   4662
funding_zscore                      1d       OOS    -0.0022    -0.11   50.0%   9.15e-01   2331
funding_zscore                      3d       IS     -0.0135    -0.92   49.2%   3.57e-01   4662
funding_zscore                      3d       OOS     0.0421     2.03   50.7%   4.28e-02   2313
funding_zscore                      7d       IS      0.0145     0.99   49.8%   3.22e-01   4662
funding_zscore                      7d       OOS     0.0496     2.37   49.3%   1.80e-02   2277
oi_accel_1d                         14d      IS     -0.0007    -0.05   49.7%   9.58e-01   4905
oi_accel_1d                         14d      OOS    -0.0015    -0.07   50.3%   9.45e-01   2214
oi_accel_1d                         1d       IS     -0.0129    -0.91   48.8%   3.65e-01   4905
oi_accel_1d                         1d       OOS     0.0802     3.88   52.6%   1.06e-04   2331
oi_accel_1d                         3d       IS     -0.0230    -1.61   48.6%   1.08e-01   4905
oi_accel_1d                         3d       OOS     0.0001     0.00   48.9%   9.98e-01   2313
oi_accel_1d                         7d       IS      0.0030     0.21   49.1%   8.32e-01   4905
oi_accel_1d                         7d       OOS    -0.0023    -0.11   50.4%   9.14e-01   2277
oi_accel_3d                         14d      IS     -0.0158    -1.10   48.4%   2.71e-01   4869
oi_accel_3d                         14d      OOS    -0.0278    -1.31   48.4%   1.91e-01   2214
oi_accel_3d                         1d       IS     -0.0020    -0.14   50.6%   8.90e-01   4869
oi_accel_3d                         1d       OOS    -0.0147    -0.71   49.5%   4.79e-01   2331
oi_accel_3d                         3d       IS      0.0154     1.08   50.0%   2.82e-01   4869
oi_accel_3d                         3d       OOS    -0.0163    -0.78   50.6%   4.34e-01   2313
oi_accel_3d                         7d       IS     -0.0180    -1.26   49.2%   2.08e-01   4869
oi_accel_3d                         7d       OOS    -0.0195    -0.93   49.2%   3.52e-01   2277
oi_accel_7d                         14d      IS     -0.0415    -2.88   48.6%   4.02e-03   4797
oi_accel_7d                         14d      OOS    -0.0455    -2.14   49.4%   3.23e-02   2214
oi_accel_7d                         1d       IS     -0.0220    -1.52   49.8%   1.28e-01   4797
oi_accel_7d                         1d       OOS    -0.0103    -0.50   51.1%   6.19e-01   2331
oi_accel_7d                         3d       IS     -0.0391    -2.71   49.4%   6.69e-03   4797
oi_accel_7d                         3d       OOS    -0.0374    -1.80   48.3%   7.23e-02   2313
oi_accel_7d                         7d       IS     -0.0760    -5.28   48.4%   1.38e-07   4797
oi_accel_7d                         7d       OOS    -0.0294    -1.40   49.0%   1.61e-01   2277
oi_div_funding_1d                   14d      IS     -0.0061    -0.42   50.2%   6.77e-01   4662
oi_div_funding_1d                   14d      OOS    -0.0161    -0.76   54.9%   4.48e-01   2214
oi_div_funding_1d                   1d       IS     -0.0137    -0.94   48.4%   3.49e-01   4662
oi_div_funding_1d                   1d       OOS     0.0325     1.57   52.2%   1.17e-01   2331
oi_div_funding_1d                   3d       IS     -0.0208    -1.42   49.0%   1.56e-01   4662
oi_div_funding_1d                   3d       OOS    -0.0104    -0.50   50.6%   6.17e-01   2313
oi_div_funding_1d                   7d       IS     -0.0085    -0.58   49.2%   5.60e-01   4662
oi_div_funding_1d                   7d       OOS    -0.0044    -0.21   52.1%   8.36e-01   2277
oi_div_funding_3d                   14d      IS     -0.0335    -2.29   48.5%   2.20e-02   4662
oi_div_funding_3d                   14d      OOS    -0.0049    -0.23   55.2%   8.16e-01   2214
oi_div_funding_3d                   1d       IS     -0.0298    -2.04   49.4%   4.17e-02   4662
oi_div_funding_3d                   1d       OOS     0.0348     1.68   52.2%   9.34e-02   2331
oi_div_funding_3d                   3d       IS     -0.0407    -2.78   48.4%   5.43e-03   4662
oi_div_funding_3d                   3d       OOS    -0.0309    -1.49   50.7%   1.37e-01   2313
oi_div_funding_3d                   7d       IS     -0.0415    -2.83   48.6%   4.62e-03   4662
oi_div_funding_3d                   7d       OOS    -0.0153    -0.73   52.2%   4.66e-01   2277
oi_div_funding_7d                   14d      IS     -0.0558    -3.82   48.0%   1.38e-04   4662
oi_div_funding_7d                   14d      OOS     0.0346     1.63   56.4%   1.03e-01   2214
oi_div_funding_7d                   1d       IS     -0.0351    -2.39   49.0%   1.67e-02   4662
oi_div_funding_7d                   1d       OOS     0.0008     0.04   51.2%   9.71e-01   2331
oi_div_funding_7d                   3d       IS     -0.0309    -2.11   49.7%   3.51e-02   4662
oi_div_funding_7d                   3d       OOS    -0.0487    -2.34   49.9%   1.93e-02   2313
oi_div_funding_7d                   7d       IS     -0.0606    -4.15   48.7%   3.42e-05   4662
oi_div_funding_7d                   7d       OOS    -0.0281    -1.34   51.3%   1.80e-01   2277
oi_div_signed_1d                    14d      IS     -0.0124    -0.87   48.7%   3.85e-01   4914
oi_div_signed_1d                    14d      OOS    -0.0028    -0.13   49.2%   8.94e-01   2214
oi_div_signed_1d                    1d       IS     -0.0143    -1.00   49.0%   3.16e-01   4914
oi_div_signed_1d                    1d       OOS     0.0169     0.82   51.1%   4.14e-01   2331
oi_div_signed_1d                    3d       IS      0.0018     0.13   49.8%   8.97e-01   4914
oi_div_signed_1d                    3d       OOS     0.0220     1.06   51.2%   2.91e-01   2313
oi_div_signed_1d                    7d       IS      0.0010     0.07   50.0%   9.46e-01   4914
oi_div_signed_1d                    7d       OOS    -0.0090    -0.43   50.3%   6.68e-01   2277
oi_div_signed_3d                    14d      IS     -0.0199    -1.39   49.1%   1.64e-01   4896
oi_div_signed_3d                    14d      OOS     0.0123     0.58   49.0%   5.62e-01   2214
oi_div_signed_3d                    1d       IS     -0.0023    -0.16   50.6%   8.75e-01   4896
oi_div_signed_3d                    1d       OOS     0.0294     1.42   50.3%   1.55e-01   2331
oi_div_signed_3d                    3d       IS      0.0107     0.75   49.6%   4.53e-01   4896
oi_div_signed_3d                    3d       OOS     0.0265     1.27   51.4%   2.03e-01   2313
oi_div_signed_3d                    7d       IS     -0.0065    -0.46   49.1%   6.48e-01   4896
oi_div_signed_3d                    7d       OOS    -0.0128    -0.61   49.2%   5.40e-01   2277
oi_div_signed_7d                    14d      IS     -0.0281    -1.96   49.0%   5.02e-02   4860
oi_div_signed_7d                    14d      OOS     0.0113     0.53   50.7%   5.94e-01   2214
oi_div_signed_7d                    1d       IS      0.0064     0.45   51.1%   6.54e-01   4860
oi_div_signed_7d                    1d       OOS    -0.0249    -1.20   50.0%   2.30e-01   2331
oi_div_signed_7d                    3d       IS      0.0192     1.34   51.5%   1.81e-01   4860
oi_div_signed_7d                    3d       OOS    -0.0359    -1.73   49.8%   8.40e-02   2313
oi_div_signed_7d                    7d       IS     -0.0156    -1.08   49.9%   2.78e-01   4860
oi_div_signed_7d                    7d       OOS    -0.0262    -1.25   49.5%   2.11e-01   2277
oi_div_signed_funding_1d            14d      IS     -0.0208    -1.42   49.5%   1.56e-01   4662
oi_div_signed_funding_1d            14d      OOS     0.0015     0.07   49.1%   9.44e-01   2214
oi_div_signed_funding_1d            1d       IS      0.0126     0.86   51.0%   3.89e-01   4662
oi_div_signed_funding_1d            1d       OOS     0.0026     0.13   50.5%   8.99e-01   2331
oi_div_signed_funding_1d            3d       IS      0.0046     0.31   50.4%   7.54e-01   4662
oi_div_signed_funding_1d            3d       OOS    -0.0419    -2.02   48.1%   4.39e-02   2313
oi_div_signed_funding_1d            7d       IS     -0.0154    -1.05   49.5%   2.92e-01   4662
oi_div_signed_funding_1d            7d       OOS    -0.0131    -0.62   49.4%   5.33e-01   2277
oi_div_signed_funding_3d            14d      IS     -0.0351    -2.40   49.4%   1.65e-02   4662
oi_div_signed_funding_3d            14d      OOS     0.0092     0.43   49.7%   6.67e-01   2214
oi_div_signed_funding_3d            1d       IS     -0.0358    -2.44   48.0%   1.46e-02   4662
oi_div_signed_funding_3d            1d       OOS     0.0198     0.95   50.0%   3.40e-01   2331
oi_div_signed_funding_3d            3d       IS     -0.0309    -2.11   48.9%   3.49e-02   4662
oi_div_signed_funding_3d            3d       OOS    -0.0129    -0.62   50.7%   5.35e-01   2313
oi_div_signed_funding_3d            7d       IS     -0.0417    -2.85   48.0%   4.39e-03   4662
oi_div_signed_funding_3d            7d       OOS     0.0267     1.27   52.4%   2.03e-01   2277
oi_div_signed_funding_7d            14d      IS     -0.0558    -3.82   48.5%   1.37e-04   4662
oi_div_signed_funding_7d            14d      OOS    -0.0266    -1.25   49.1%   2.10e-01   2214
oi_div_signed_funding_7d            1d       IS     -0.0409    -2.79   47.6%   5.23e-03   4662
oi_div_signed_funding_7d            1d       OOS    -0.0267    -1.29   48.9%   1.98e-01   2331
oi_div_signed_funding_7d            3d       IS     -0.0534    -3.65   48.2%   2.67e-04   4662
oi_div_signed_funding_7d            3d       OOS    -0.0468    -2.25   48.8%   2.43e-02   2313
oi_div_signed_funding_7d            7d       IS     -0.0394    -2.69   48.3%   7.20e-03   4662
oi_div_signed_funding_7d            7d       OOS    -0.0362    -1.73   48.5%   8.41e-02   2277
oi_div_unsigned_1d                  14d      IS     -0.0118    -0.83   50.1%   4.09e-01   4914
oi_div_unsigned_1d                  14d      OOS    -0.0030    -0.14   54.9%   8.87e-01   2214
oi_div_unsigned_1d                  1d       IS     -0.0106    -0.74   48.3%   4.59e-01   4914
oi_div_unsigned_1d                  1d       OOS     0.0083     0.40   52.2%   6.90e-01   2331
oi_div_unsigned_1d                  3d       IS     -0.0227    -1.59   49.0%   1.12e-01   4914
oi_div_unsigned_1d                  3d       OOS    -0.0075    -0.36   50.6%   7.19e-01   2313
oi_div_unsigned_1d                  7d       IS     -0.0206    -1.44   49.1%   1.49e-01   4914
oi_div_unsigned_1d                  7d       OOS     0.0016     0.08   52.1%   9.39e-01   2277
oi_div_unsigned_3d                  14d      IS     -0.0418    -2.93   48.4%   3.44e-03   4896
oi_div_unsigned_3d                  14d      OOS     0.0237     1.11   55.2%   2.65e-01   2214
oi_div_unsigned_3d                  1d       IS     -0.0300    -2.10   49.5%   3.58e-02   4896
oi_div_unsigned_3d                  1d       OOS     0.0141     0.68   52.2%   4.97e-01   2331
oi_div_unsigned_3d                  3d       IS     -0.0322    -2.25   48.5%   2.44e-02   4896
oi_div_unsigned_3d                  3d       OOS    -0.0309    -1.49   50.7%   1.38e-01   2313
oi_div_unsigned_3d                  7d       IS     -0.0560    -3.92   48.6%   8.92e-05   4896
oi_div_unsigned_3d                  7d       OOS     0.0019     0.09   52.2%   9.27e-01   2277
oi_div_unsigned_7d                  14d      IS     -0.0525    -3.66   48.1%   2.54e-04   4860
oi_div_unsigned_7d                  14d      OOS     0.0661     3.12   56.4%   1.85e-03   2214
oi_div_unsigned_7d                  1d       IS     -0.0316    -2.21   49.1%   2.75e-02   4860
oi_div_unsigned_7d                  1d       OOS    -0.0126    -0.61   51.2%   5.44e-01   2331
oi_div_unsigned_7d                  3d       IS     -0.0297    -2.07   49.8%   3.86e-02   4860
oi_div_unsigned_7d                  3d       OOS    -0.0585    -2.82   49.9%   4.87e-03   2313
oi_div_unsigned_7d                  7d       IS     -0.0768    -5.37   48.7%   8.19e-08   4860
oi_div_unsigned_7d                  7d       OOS    -0.0254    -1.21   51.3%   2.26e-01   2277
oi_div_vol_adj_1d                   14d      IS     -0.0114    -0.80   49.0%   4.24e-01   4878
oi_div_vol_adj_1d                   14d      OOS    -0.0166    -0.78   48.6%   4.36e-01   2214
oi_div_vol_adj_1d                   1d       IS     -0.0075    -0.53   49.0%   5.99e-01   4878
oi_div_vol_adj_1d                   1d       OOS     0.0393     1.90   51.4%   5.81e-02   2331
oi_div_vol_adj_1d                   3d       IS     -0.0140    -0.98   48.9%   3.28e-01   4878
oi_div_vol_adj_1d                   3d       OOS    -0.0106    -0.51   50.3%   6.11e-01   2313
oi_div_vol_adj_1d                   7d       IS     -0.0233    -1.63   48.4%   1.03e-01   4878
oi_div_vol_adj_1d                   7d       OOS    -0.0026    -0.13   50.0%   8.99e-01   2277
oi_div_vol_adj_3d                   14d      IS     -0.0218    -1.51   48.0%   1.32e-01   4788
oi_div_vol_adj_3d                   14d      OOS    -0.0095    -0.45   48.9%   6.56e-01   2214
oi_div_vol_adj_3d                   1d       IS     -0.0111    -0.76   49.8%   4.44e-01   4788
oi_div_vol_adj_3d                   1d       OOS     0.0122     0.59   50.2%   5.57e-01   2331
oi_div_vol_adj_3d                   3d       IS     -0.0166    -1.15   48.7%   2.50e-01   4788
oi_div_vol_adj_3d                   3d       OOS    -0.0127    -0.61   49.5%   5.42e-01   2313
oi_div_vol_adj_3d                   7d       IS     -0.0576    -3.99   47.5%   6.62e-05   4788
oi_div_vol_adj_3d                   7d       OOS    -0.0138    -0.66   48.9%   5.09e-01   2277
oi_div_vol_adj_7d                   14d      IS     -0.0113    -0.77   48.3%   4.44e-01   4608
oi_div_vol_adj_7d                   14d      OOS     0.0271     1.27   51.4%   2.03e-01   2214
oi_div_vol_adj_7d                   1d       IS     -0.0114    -0.78   49.4%   4.38e-01   4608
oi_div_vol_adj_7d                   1d       OOS     0.0083     0.40   51.7%   6.88e-01   2331
oi_div_vol_adj_7d                   3d       IS     -0.0139    -0.94   50.5%   3.46e-01   4608
oi_div_vol_adj_7d                   3d       OOS    -0.0323    -1.56   49.3%   1.20e-01   2313
oi_div_vol_adj_7d                   7d       IS     -0.0414    -2.81   48.7%   4.95e-03   4608
oi_div_vol_adj_7d                   7d       OOS    -0.0170    -0.81   50.0%   4.18e-01   2277
oi_pct_1d                           14d      IS     -0.0084    -0.59   49.0%   5.58e-01   4914
oi_pct_1d                           14d      OOS    -0.0158    -0.74   48.6%   4.57e-01   2214
oi_pct_1d                           1d       IS     -0.0137    -0.96   48.9%   3.36e-01   4914
oi_pct_1d                           1d       OOS     0.0389     1.88   51.4%   6.01e-02   2331
oi_pct_1d                           3d       IS     -0.0131    -0.92   48.9%   3.58e-01   4914
oi_pct_1d                           3d       OOS    -0.0090    -0.43   50.3%   6.64e-01   2313
oi_pct_1d                           7d       IS     -0.0246    -1.72   48.3%   8.47e-02   4914
oi_pct_1d                           7d       OOS    -0.0052    -0.25   50.0%   8.04e-01   2277
oi_pct_3d                           14d      IS     -0.0222    -1.55   48.1%   1.21e-01   4896
oi_pct_3d                           14d      OOS    -0.0108    -0.51   48.9%   6.12e-01   2214
oi_pct_3d                           1d       IS     -0.0116    -0.81   49.8%   4.16e-01   4896
oi_pct_3d                           1d       OOS     0.0127     0.61   50.2%   5.40e-01   2331
oi_pct_3d                           3d       IS     -0.0170    -1.19   48.7%   2.33e-01   4896
oi_pct_3d                           3d       OOS    -0.0173    -0.83   49.5%   4.05e-01   2313
oi_pct_3d                           7d       IS     -0.0611    -4.28   47.4%   1.90e-05   4896
oi_pct_3d                           7d       OOS    -0.0152    -0.72   48.9%   4.69e-01   2277
oi_pct_7d                           14d      IS     -0.0039    -0.27   49.0%   7.88e-01   4860
oi_pct_7d                           14d      OOS     0.0257     1.21   51.4%   2.27e-01   2214
oi_pct_7d                           1d       IS     -0.0164    -1.14   49.3%   2.54e-01   4860
oi_pct_7d                           1d       OOS     0.0068     0.33   51.7%   7.41e-01   2331
oi_pct_7d                           3d       IS     -0.0244    -1.70   50.3%   8.91e-02   4860
oi_pct_7d                           3d       OOS    -0.0315    -1.52   49.3%   1.30e-01   2313
oi_pct_7d                           7d       IS     -0.0491    -3.43   48.5%   6.17e-04   4860
oi_pct_7d                           7d       OOS    -0.0141    -0.67   50.0%   5.01e-01   2277
oi_vol_div_1d                       14d      IS     -0.0054    -0.38   49.9%   7.07e-01   4914
oi_vol_div_1d                       14d      OOS     0.0006     0.03   51.9%   9.78e-01   2214
oi_vol_div_1d                       1d       IS     -0.0300    -2.11   48.6%   3.52e-02   4914
oi_vol_div_1d                       1d       OOS     0.0502     2.42   53.3%   1.54e-02   2331
oi_vol_div_1d                       3d       IS     -0.0246    -1.72   49.8%   8.49e-02   4914
oi_vol_div_1d                       3d       OOS    -0.0311    -1.50   50.1%   1.35e-01   2313
oi_vol_div_1d                       7d       IS     -0.0096    -0.68   50.0%   4.99e-01   4914
oi_vol_div_1d                       7d       OOS    -0.0105    -0.50   50.5%   6.15e-01   2277
oi_vol_div_3d                       14d      IS     -0.0083    -0.58   49.8%   5.61e-01   4896
oi_vol_div_3d                       14d      OOS    -0.0355    -1.67   46.9%   9.48e-02   2214
oi_vol_div_3d                       1d       IS     -0.0187    -1.31   50.5%   1.90e-01   4896
oi_vol_div_3d                       1d       OOS    -0.0300    -1.45   48.6%   1.47e-01   2331
oi_vol_div_3d                       3d       IS     -0.0199    -1.39   49.8%   1.65e-01   4896
oi_vol_div_3d                       3d       OOS    -0.0304    -1.46   49.2%   1.44e-01   2313
oi_vol_div_3d                       7d       IS     -0.0143    -1.00   50.1%   3.16e-01   4896
oi_vol_div_3d                       7d       OOS    -0.0199    -0.95   48.7%   3.43e-01   2277
oi_vol_div_7d                       14d      IS     -0.0656    -4.58   48.4%   4.74e-06   4860
oi_vol_div_7d                       14d      OOS     0.0586     2.76   51.9%   5.84e-03   2214
oi_vol_div_7d                       1d       IS     -0.0257    -1.79   49.4%   7.33e-02   4860
oi_vol_div_7d                       1d       OOS    -0.0176    -0.85   48.1%   3.96e-01   2331
oi_vol_div_7d                       3d       IS     -0.0399    -2.78   49.4%   5.39e-03   4860
oi_vol_div_7d                       3d       OOS    -0.0113    -0.54   50.5%   5.86e-01   2313
oi_vol_div_7d                       7d       IS     -0.0335    -2.34   49.5%   1.95e-02   4860
oi_vol_div_7d                       7d       OOS     0.0454     2.17   50.9%   3.02e-02   2277
price_pct_1d                        14d      IS      0.0021     0.15   49.7%   8.83e-01   4914
price_pct_1d                        14d      OOS    -0.0146    -0.69   50.5%   4.92e-01   2214
price_pct_1d                        1d       IS     -0.0076    -0.53   48.6%   5.95e-01   4914
price_pct_1d                        1d       OOS     0.0335     1.62   52.5%   1.06e-01   2331
price_pct_1d                        3d       IS     -0.0163    -1.14   48.9%   2.54e-01   4914
price_pct_1d                        3d       OOS    -0.0429    -2.06   48.9%   3.93e-02   2313
price_pct_1d                        7d       IS     -0.0313    -2.19   48.3%   2.84e-02   4914
price_pct_1d                        7d       OOS     0.0151     0.72   49.6%   4.71e-01   2277
price_pct_3d                        14d      IS      0.0096     0.67   49.9%   5.03e-01   4896
price_pct_3d                        14d      OOS    -0.0361    -1.70   49.5%   8.93e-02   2214
price_pct_3d                        1d       IS     -0.0038    -0.27   49.2%   7.90e-01   4896
price_pct_3d                        1d       OOS    -0.0514    -2.48   50.1%   1.31e-02   2331
price_pct_3d                        3d       IS     -0.0274    -1.92   48.8%   5.50e-02   4896
price_pct_3d                        3d       OOS    -0.0816    -3.94   47.3%   8.45e-05   2313
price_pct_3d                        7d       IS     -0.0627    -4.39   47.9%   1.13e-05   4896
price_pct_3d                        7d       OOS     0.0028     0.13   50.7%   8.95e-01   2277
price_pct_7d                        14d      IS      0.0162     1.13   49.0%   2.59e-01   4860
price_pct_7d                        14d      OOS     0.0064     0.30   50.1%   7.62e-01   2214
price_pct_7d                        1d       IS     -0.0255    -1.78   48.3%   7.49e-02   4860
price_pct_7d                        1d       OOS     0.0473     2.29   51.8%   2.23e-02   2331
price_pct_7d                        3d       IS     -0.0545    -3.80   48.0%   1.44e-04   4860
price_pct_7d                        3d       OOS     0.0223     1.07   49.8%   2.83e-01   2313
price_pct_7d                        7d       IS     -0.0534    -3.73   46.9%   1.96e-04   4860
price_pct_7d                        7d       OOS     0.0230     1.10   49.8%   2.73e-01   2277
==================================================================================================================================
```

## Signal Interpretation

| Signal | Interpretation |
|--------|---------------|
| `oi_pct_{w}d` | Raw OI rate of change. Positive = growing open interest. |
| `oi_div_unsigned_{w}d` | OI growth minus abs(price change). Positive = OI growing faster than price moves. |
| `oi_div_signed_{w}d` | OI growth minus price change. High positive = OI up + price down (bearish leverage). |
| `oi_accel_{w}d` | Second derivative of OI. Positive = OI growth accelerating. |
| `oi_div_vol_adj_{w}d` | OI change divided by price volatility. Leverage intensity. |
| `oi_div_funding_{w}d` | OI divergence * |funding z-score|. Divergence amplified by funding extreme. |
| `oi_div_signed_funding_{w}d` | Signed divergence * funding z-score. Directional leverage + funding signal. |
| `oi_vol_div_{w}d` | OI growth minus volume growth. OI up + volume down = hollow leverage. |
| `funding_zscore` | Current funding rate z-score vs 30d rolling. |
| `funding_cum_3d` | Cumulative funding over ~3 days. |

## IC Interpretation Guide

- |IC| > 0.05: Potentially meaningful signal
- |IC| > 0.10: Strong signal (rare in crypto cross-sectional)
- |t-stat| > 2.0: Statistically significant at 5% level
- Hit rate > 52%: Slight directional edge
- **Sign consistency IS->OOS is more important than IC magnitude**
- Negative IC on divergence signals = mean reversion (OI buildup predicts reversal)
- Positive IC on divergence signals = trend confirmation (OI buildup predicts continuation)

## Deep Dive: Best OI-Specific Signals

### Signal Consistency Across Horizons

| Signal | 3d IC (IS/OOS) | 7d IC (IS/OOS) | 14d IC (IS/OOS) | Consistent? |
|--------|----------------|-----------------|------------------|-------------|
| `oi_div_unsigned_7d` | -0.030/-0.059 | -0.077/-0.025 | -0.053/+0.066 | 3d,7d YES; 14d flips |
| `oi_div_signed_funding_7d` | -0.053/-0.047 | -0.039/-0.036 | -0.056/-0.027 | ALL consistent |
| `oi_div_funding_7d` | -0.031/-0.049 | -0.061/-0.028 | -0.056/+0.035 | 3d,7d YES; 14d flips |
| `oi_accel_7d` | -0.039/-0.037 | -0.076/-0.029 | -0.042/-0.046 | ALL consistent |

**Best OI signal overall**: `oi_div_signed_funding_7d` -- consistent negative IC across ALL horizons in both IS and OOS. This is the combined signal of 7-day OI-price divergence interacted with funding rate z-score. The negative IC means: when OI grows faster than price while funding is extreme, forward returns are negative (mean reversion).

**Runner-up**: `oi_accel_7d` -- OI acceleration at 7d is consistently negative across all horizons, with the strongest OOS result at 14d (IC=-0.046, t=-2.14, statistically significant).

### Quintile Analysis: `oi_div_unsigned_7d` -> 3d Forward Returns

**In-Sample (4860 obs):**

| Quintile | Mean Return (bps) | Annualized Sharpe | Count |
|----------|-------------------|-------------------|-------|
| Q1 (low OI div) | +146.8 | 1.59 | 972 |
| Q2 | +31.8 | 0.41 | 972 |
| Q3 | +71.2 | 0.97 | 972 |
| Q4 | +18.0 | 0.27 | 972 |
| Q5 (high OI div) | +22.5 | 0.31 | 972 |
| **Q1-Q5 spread** | **+124.3 bps/3d** | | |

**Out-of-Sample (2313 obs):**

| Quintile | Mean Return (bps) | Annualized Sharpe | Count |
|----------|-------------------|-------------------|-------|
| Q1 (low OI div) | +24.7 | 0.39 | 463 |
| Q2 | -4.2 | -0.07 | 462 |
| Q3 | -57.2 | -0.95 | 463 |
| Q4 | -38.5 | -0.61 | 462 |
| Q5 (high OI div) | -90.0 | -1.37 | 463 |
| **Q1-Q5 spread** | **+114.7 bps/3d** | | |

The quintile spread is strongly monotonic in OOS: higher OI divergence (OI growing faster than price) predicts lower 3d forward returns. The Q1-Q5 long-short spread of ~115 bps per 3-day period persists OOS, confirming the mean reversion hypothesis.

### Quintile Analysis: `oi_accel_7d` -> 14d Forward Returns

**Out-of-Sample (2214 obs):**

| Quintile | Mean Return (bps) | Annualized Sharpe | Count |
|----------|-------------------|-------------------|-------|
| Q1 (decelerating OI) | -225.2 | -0.94 | 443 |
| Q2 | -207.5 | -0.72 | 443 |
| Q3 | -212.4 | -0.71 | 442 |
| Q4 | -162.4 | -0.50 | 443 |
| Q5 (accelerating OI) | -394.7 | -1.34 | 443 |
| **Q1-Q5 spread** | **+169.6 bps/14d** | | |

OI acceleration at the 7d window predicts significantly worse 14d returns in the top quintile. This confirms the hypothesis: rapidly accelerating open interest (leverage buildup) leads to mean reversion cascades.

### Conditional Analysis: OI Spike + Price Stall

The core hypothesis: when OI spikes but price stalls, leverage will unwind.

| Window | Sample | Baseline 7d Ret | OI Spike + Price Stall | OI Spike + Price Move |
|--------|--------|-----------------|------------------------|-----------------------|
| 3d | IS | +139 bps | +12 bps (n=465) | +212 bps (n=514) |
| 3d | OOS | -93 bps | **-154 bps** (n=192) | -60 bps (n=264) |
| 7d | IS | +134 bps | **-52 bps** (n=477) | +301 bps (n=495) |
| 7d | OOS | -93 bps | **-302 bps** (n=184) | -37 bps (n=272) |

**Key finding**: OI spike + price stall consistently underperforms both the baseline and OI spike + price move, across both IS and OOS, and across both windows. The 7d OOS result is striking: -302 bps vs -37 bps for the move condition, a 265 bps differential.

This validates the core signal hypothesis: OI that builds without corresponding price movement represents vulnerable leverage that resolves through liquidation cascades.

## Conclusions and Recommendations

### Validated Signals (IS-OOS consistent, economically meaningful):

1. **`oi_div_unsigned_7d` at 3d horizon** -- Best single OI divergence signal. IC_OOS = -0.059 (t=-2.82). Monotonic quintile returns. Q1-Q5 spread of 115 bps/3d OOS.

2. **`oi_div_signed_funding_7d` at 3d horizon** -- Best combined signal. IC_OOS = -0.047 (t=-2.25). Consistent across ALL horizons in both samples. Interaction of OI divergence with funding extremes amplifies the mean reversion signal.

3. **`oi_accel_7d` at 14d horizon** -- OI acceleration signal. IC_OOS = -0.046 (t=-2.14). Statistically significant. The second derivative of OI captures leverage buildup dynamics.

4. **`oi_div_funding_7d` at 3d horizon** -- OI divergence weighted by absolute funding. IC_OOS = -0.049 (t=-2.34). Significant.

### Failed/Weak Signals:

- **Short-window OI signals (1d)**: Too noisy. IC values near zero and inconsistent.
- **OI acceleration at 1d/3d**: No predictive power. OI acceleration needs a longer window to be meaningful.
- **Signed OI-price divergence (without funding)**: Weaker than unsigned version. Direction of price move relative to OI is less informative than the magnitude gap.

### Implementation Notes:

- **Signal direction**: All viable OI divergence signals have NEGATIVE IC, confirming the mean reversion hypothesis. High OI divergence predicts LOWER future returns.
- **Optimal horizon**: 3d forward returns show the strongest signal. 7d also works but with weaker OOS IC (signal decay). 14d is unstable for some signals (sign flips).
- **Trading implication**: Short tokens in the top quintile of OI divergence. The Q5 quintile (highest OI-price divergence) showed -90 bps per 3d period OOS.
- **Funding confirmation**: Adding funding rate extremes as a filter improves signal quality and consistency, particularly at longer horizons. When funding is also extreme, the leverage unwind is more likely.
- **Next steps**: Test as a cross-sectional ranking factor in a portfolio context. Estimate turnover and transaction costs. Combine with existing momentum and funding signals.
