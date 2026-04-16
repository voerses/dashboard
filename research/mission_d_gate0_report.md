# Mission D Gate 0 — IC Test Report

Tier 1 indicators tested: HV, FIF, WRD, SRI
Tokens: BTC, ETH, SOL (single-asset); 17-token basket for SRI
Horizons: [1, 4, 24, 72, 168] hours
Sample step: every 24 bars (daily cadence)
Min paired observations: 500

## Verdicts

| Indicator | Verdict | Best |IC| | t-stat | Token | Horizon | Signal |
|---|---|---|---|---|---|---|
| HV | **MARGINAL** | 0.0617 | -2.77 | SOL | 1h | `HV_v_H` |
| FIF | **PASS** | 0.0975 | -4.39 | SOL | 168h | `FIF` |
| WRD | **PASS** | 0.0847 | -4.05 | ETH | 4h | `WRD_tail_shift` |
| SRI | **MARGINAL** | 0.0803 | 2.86 | SOL | 72h | `SRI` |

## Top 10 IC results overall

| Key | n | Pearson IC | t-stat | Spearman IC |
|---|---|---|---|---|
| `FIF|SOL|168h` | 2007 | -0.0975 | -4.39 | -0.0914 |
| `FIF_drift|SOL|168h` | 2007 | +0.0858 | +3.86 | +0.0505 |
| `WRD_tail_shift|ETH|4h` | 2271 | -0.0847 | -4.05 | -0.0230 |
| `WRD_tail_shift|BTC|4h` | 2271 | -0.0821 | -3.92 | -0.0270 |
| `WRD_tail_shift|SOL|1h` | 2014 | +0.0812 | +3.65 | -0.0195 |
| `SRI|SOL|72h` | 1260 | +0.0803 | +2.86 | +0.0466 |
| `WRD_W1|BTC|168h` | 2264 | +0.0767 | +3.66 | +0.0368 |
| `FIF_drift|SOL|1h` | 2014 | -0.0725 | -3.26 | -0.0463 |
| `FIF|SOL|72h` | 2011 | -0.0688 | -3.09 | -0.0726 |
| `FIF_drift|SOL|4h` | 2014 | -0.0624 | -2.80 | -0.0480 |

## Per-indicator best IC by token x horizon

### HV

| Signal | Token | Horizon | n | Pearson IC | t-stat | Spearman IC |
|---|---|---|---|---|---|---|
| `HV_v_H` | BTC | 1h | 2271 | -0.0122 | -0.58 | +0.0089 |
| `HV_v_H` | BTC | 4h | 2271 | -0.0013 | -0.06 | +0.0142 |
| `HV_v_H` | BTC | 24h | 2270 | -0.0094 | -0.45 | -0.0035 |
| `HV_v_H` | BTC | 72h | 2268 | +0.0078 | +0.37 | -0.0031 |
| `HV_v_H` | BTC | 168h | 2264 | +0.0120 | +0.57 | +0.0078 |
| `HV_v_H_signed` | BTC | 1h | 2271 | -0.0122 | -0.58 | +0.0089 |
| `HV_v_H_signed` | BTC | 4h | 2271 | -0.0013 | -0.06 | +0.0142 |
| `HV_v_H_signed` | BTC | 24h | 2270 | -0.0094 | -0.45 | -0.0035 |
| `HV_v_H_signed` | BTC | 72h | 2268 | +0.0078 | +0.37 | -0.0031 |
| `HV_v_H_signed` | BTC | 168h | 2264 | +0.0120 | +0.57 | +0.0078 |
| `HV_v_H` | ETH | 1h | 2271 | -0.0171 | -0.82 | -0.0028 |
| `HV_v_H` | ETH | 4h | 2271 | -0.0170 | -0.81 | -0.0338 |
| `HV_v_H` | ETH | 24h | 2270 | -0.0126 | -0.60 | +0.0016 |
| `HV_v_H` | ETH | 72h | 2268 | +0.0064 | +0.31 | +0.0100 |
| `HV_v_H` | ETH | 168h | 2264 | -0.0074 | -0.35 | -0.0093 |
| `HV_v_H_signed` | ETH | 1h | 2271 | -0.0171 | -0.82 | -0.0028 |
| `HV_v_H_signed` | ETH | 4h | 2271 | -0.0170 | -0.81 | -0.0338 |
| `HV_v_H_signed` | ETH | 24h | 2270 | -0.0126 | -0.60 | +0.0016 |
| `HV_v_H_signed` | ETH | 72h | 2268 | +0.0064 | +0.31 | +0.0100 |
| `HV_v_H_signed` | ETH | 168h | 2264 | -0.0074 | -0.35 | -0.0093 |
| `HV_v_H` | SOL | 1h | 2014 | -0.0617 | -2.77 | -0.0485 |
| `HV_v_H` | SOL | 4h | 2014 | -0.0403 | -1.81 | -0.0374 |
| `HV_v_H` | SOL | 24h | 2013 | -0.0373 | -1.67 | -0.0382 |
| `HV_v_H` | SOL | 72h | 2011 | -0.0041 | -0.18 | -0.0060 |
| `HV_v_H` | SOL | 168h | 2007 | -0.0228 | -1.02 | -0.0279 |
| `HV_v_H_signed` | SOL | 1h | 2014 | -0.0617 | -2.77 | -0.0485 |
| `HV_v_H_signed` | SOL | 4h | 2014 | -0.0403 | -1.81 | -0.0374 |
| `HV_v_H_signed` | SOL | 24h | 2013 | -0.0373 | -1.67 | -0.0382 |
| `HV_v_H_signed` | SOL | 72h | 2011 | -0.0041 | -0.18 | -0.0060 |
| `HV_v_H_signed` | SOL | 168h | 2007 | -0.0228 | -1.02 | -0.0279 |

### FIF

| Signal | Token | Horizon | n | Pearson IC | t-stat | Spearman IC |
|---|---|---|---|---|---|---|
| `FIF` | BTC | 1h | 2271 | -0.0251 | -1.20 | -0.0388 |
| `FIF` | BTC | 4h | 2271 | -0.0308 | -1.47 | -0.0496 |
| `FIF` | BTC | 24h | 2270 | +0.0060 | +0.29 | -0.0326 |
| `FIF` | BTC | 72h | 2268 | +0.0200 | +0.95 | -0.0438 |
| `FIF` | BTC | 168h | 2264 | +0.0425 | +2.02 | -0.0382 |
| `FIF_drift` | BTC | 1h | 2271 | +0.0473 | +2.25 | +0.0341 |
| `FIF_drift` | BTC | 4h | 2271 | +0.0045 | +0.22 | +0.0157 |
| `FIF_drift` | BTC | 24h | 2270 | -0.0096 | -0.46 | -0.0205 |
| `FIF_drift` | BTC | 72h | 2268 | +0.0155 | +0.74 | +0.0005 |
| `FIF_drift` | BTC | 168h | 2264 | +0.0231 | +1.10 | +0.0138 |
| `FIF_x_drift` | BTC | 1h | 2271 | +0.0270 | +1.28 | +0.0176 |
| `FIF_x_drift` | BTC | 4h | 2271 | -0.0017 | -0.08 | +0.0035 |
| `FIF_x_drift` | BTC | 24h | 2270 | +0.0269 | +1.28 | -0.0130 |
| `FIF_x_drift` | BTC | 72h | 2268 | +0.0486 | +2.31 | +0.0037 |
| `FIF_x_drift` | BTC | 168h | 2264 | +0.0464 | +2.21 | +0.0134 |
| `FIF` | ETH | 1h | 2271 | -0.0105 | -0.50 | -0.0212 |
| `FIF` | ETH | 4h | 2271 | -0.0107 | -0.51 | -0.0372 |
| `FIF` | ETH | 24h | 2270 | -0.0049 | -0.23 | -0.0273 |
| `FIF` | ETH | 72h | 2268 | -0.0022 | -0.10 | -0.0341 |
| `FIF` | ETH | 168h | 2264 | +0.0054 | +0.26 | -0.0434 |
| `FIF_drift` | ETH | 1h | 2271 | +0.0430 | +2.05 | +0.0304 |
| `FIF_drift` | ETH | 4h | 2271 | +0.0211 | +1.01 | +0.0058 |
| `FIF_drift` | ETH | 24h | 2270 | -0.0011 | -0.05 | -0.0094 |
| `FIF_drift` | ETH | 72h | 2268 | +0.0101 | +0.48 | -0.0066 |
| `FIF_drift` | ETH | 168h | 2264 | +0.0114 | +0.54 | +0.0077 |
| `FIF_x_drift` | ETH | 1h | 2271 | +0.0407 | +1.94 | +0.0221 |
| `FIF_x_drift` | ETH | 4h | 2271 | +0.0105 | +0.50 | -0.0034 |
| `FIF_x_drift` | ETH | 24h | 2270 | +0.0242 | +1.15 | -0.0116 |
| `FIF_x_drift` | ETH | 72h | 2268 | +0.0323 | +1.54 | -0.0098 |
| `FIF_x_drift` | ETH | 168h | 2264 | +0.0363 | +1.73 | -0.0030 |
| `FIF` | SOL | 1h | 2014 | -0.0383 | -1.72 | -0.0252 |
| `FIF` | SOL | 4h | 2014 | -0.0329 | -1.48 | -0.0268 |
| `FIF` | SOL | 24h | 2013 | -0.0382 | -1.72 | -0.0269 |
| `FIF` | SOL | 72h | 2011 | -0.0688 | -3.09 | -0.0726 |
| `FIF` | SOL | 168h | 2007 | -0.0975 | -4.39 | -0.0914 |
| `FIF_drift` | SOL | 1h | 2014 | -0.0725 | -3.26 | -0.0463 |
| `FIF_drift` | SOL | 4h | 2014 | -0.0624 | -2.80 | -0.0480 |
| `FIF_drift` | SOL | 24h | 2013 | +0.0102 | +0.46 | +0.0195 |
| `FIF_drift` | SOL | 72h | 2011 | +0.0567 | +2.54 | +0.0313 |
| `FIF_drift` | SOL | 168h | 2007 | +0.0858 | +3.86 | +0.0505 |
| `FIF_x_drift` | SOL | 1h | 2014 | -0.0451 | -2.03 | -0.0287 |
| `FIF_x_drift` | SOL | 4h | 2014 | -0.0348 | -1.56 | -0.0358 |
| `FIF_x_drift` | SOL | 24h | 2013 | +0.0345 | +1.55 | +0.0233 |
| `FIF_x_drift` | SOL | 72h | 2011 | +0.0481 | +2.16 | +0.0359 |
| `FIF_x_drift` | SOL | 168h | 2007 | +0.0432 | +1.94 | +0.0384 |

### WRD

| Signal | Token | Horizon | n | Pearson IC | t-stat | Spearman IC |
|---|---|---|---|---|---|---|
| `WRD_W1` | BTC | 1h | 2271 | +0.0423 | +2.02 | +0.0320 |
| `WRD_W1` | BTC | 4h | 2271 | +0.0350 | +1.67 | +0.0429 |
| `WRD_W1` | BTC | 24h | 2270 | +0.0198 | +0.94 | +0.0310 |
| `WRD_W1` | BTC | 72h | 2268 | +0.0171 | +0.81 | +0.0069 |
| `WRD_W1` | BTC | 168h | 2264 | +0.0767 | +3.66 | +0.0368 |
| `WRD_tail_shift` | BTC | 1h | 2271 | -0.0610 | -2.91 | -0.0467 |
| `WRD_tail_shift` | BTC | 4h | 2271 | -0.0821 | -3.92 | -0.0270 |
| `WRD_tail_shift` | BTC | 24h | 2270 | -0.0289 | -1.38 | +0.0019 |
| `WRD_tail_shift` | BTC | 72h | 2268 | +0.0046 | +0.22 | +0.0366 |
| `WRD_tail_shift` | BTC | 168h | 2264 | +0.0319 | +1.52 | +0.0358 |
| `WRD_delta_skew` | BTC | 1h | 2271 | -0.0011 | -0.05 | -0.0012 |
| `WRD_delta_skew` | BTC | 4h | 2271 | +0.0201 | +0.96 | +0.0026 |
| `WRD_delta_skew` | BTC | 24h | 2270 | -0.0152 | -0.72 | -0.0122 |
| `WRD_delta_skew` | BTC | 72h | 2268 | +0.0179 | +0.85 | +0.0039 |
| `WRD_delta_skew` | BTC | 168h | 2264 | +0.0143 | +0.68 | -0.0210 |
| `WRD_W1` | ETH | 1h | 2271 | +0.0407 | +1.94 | +0.0173 |
| `WRD_W1` | ETH | 4h | 2271 | +0.0246 | +1.17 | +0.0183 |
| `WRD_W1` | ETH | 24h | 2270 | -0.0077 | -0.37 | +0.0184 |
| `WRD_W1` | ETH | 72h | 2268 | -0.0226 | -1.07 | +0.0101 |
| `WRD_W1` | ETH | 168h | 2264 | +0.0149 | +0.71 | +0.0209 |
| `WRD_tail_shift` | ETH | 1h | 2271 | -0.0442 | -2.11 | -0.0272 |
| `WRD_tail_shift` | ETH | 4h | 2271 | -0.0847 | -4.05 | -0.0230 |
| `WRD_tail_shift` | ETH | 24h | 2270 | -0.0355 | -1.69 | -0.0018 |
| `WRD_tail_shift` | ETH | 72h | 2268 | -0.0140 | -0.66 | +0.0254 |
| `WRD_tail_shift` | ETH | 168h | 2264 | -0.0008 | -0.04 | +0.0037 |
| `WRD_delta_skew` | ETH | 1h | 2271 | -0.0012 | -0.06 | -0.0101 |
| `WRD_delta_skew` | ETH | 4h | 2271 | +0.0012 | +0.05 | -0.0113 |
| `WRD_delta_skew` | ETH | 24h | 2270 | -0.0046 | -0.22 | -0.0124 |
| `WRD_delta_skew` | ETH | 72h | 2268 | +0.0088 | +0.42 | -0.0015 |
| `WRD_delta_skew` | ETH | 168h | 2264 | -0.0027 | -0.13 | -0.0208 |
| `WRD_W1` | SOL | 1h | 2014 | +0.0433 | +1.94 | +0.0251 |
| `WRD_W1` | SOL | 4h | 2014 | +0.0002 | +0.01 | +0.0003 |
| `WRD_W1` | SOL | 24h | 2013 | +0.0088 | +0.39 | +0.0280 |
| `WRD_W1` | SOL | 72h | 2011 | +0.0137 | +0.62 | +0.0545 |
| `WRD_W1` | SOL | 168h | 2007 | +0.0328 | +1.47 | +0.0665 |
| `WRD_tail_shift` | SOL | 1h | 2014 | +0.0812 | +3.65 | -0.0195 |
| `WRD_tail_shift` | SOL | 4h | 2014 | +0.0447 | +2.01 | +0.0208 |
| `WRD_tail_shift` | SOL | 24h | 2013 | +0.0088 | +0.40 | +0.0522 |
| `WRD_tail_shift` | SOL | 72h | 2011 | +0.0106 | +0.47 | +0.0347 |
| `WRD_tail_shift` | SOL | 168h | 2007 | +0.0169 | +0.76 | +0.0357 |
| `WRD_delta_skew` | SOL | 1h | 2014 | -0.0606 | -2.72 | -0.0192 |
| `WRD_delta_skew` | SOL | 4h | 2014 | -0.0423 | -1.90 | -0.0338 |
| `WRD_delta_skew` | SOL | 24h | 2013 | -0.0075 | -0.33 | -0.0134 |
| `WRD_delta_skew` | SOL | 72h | 2011 | +0.0023 | +0.10 | +0.0125 |
| `WRD_delta_skew` | SOL | 168h | 2007 | -0.0136 | -0.61 | -0.0153 |

### SRI

| Signal | Token | Horizon | n | Pearson IC | t-stat | Spearman IC |
|---|---|---|---|---|---|---|
| `SRI` | BTC | 1h | 1260 | -0.0267 | -0.95 | -0.0333 |
| `SRI` | BTC | 4h | 1260 | -0.0377 | -1.34 | -0.0171 |
| `SRI` | BTC | 24h | 1260 | -0.0253 | -0.90 | -0.0109 |
| `SRI` | BTC | 72h | 1260 | +0.0077 | +0.27 | +0.0179 |
| `SRI` | BTC | 168h | 1260 | -0.0029 | -0.10 | -0.0030 |
| `absorption` | BTC | 1h | 1260 | +0.0266 | +0.94 | +0.0422 |
| `absorption` | BTC | 4h | 1260 | +0.0351 | +1.24 | +0.0396 |
| `absorption` | BTC | 24h | 1260 | +0.0011 | +0.04 | +0.0130 |
| `absorption` | BTC | 72h | 1260 | +0.0077 | +0.27 | +0.0032 |
| `absorption` | BTC | 168h | 1260 | -0.0067 | -0.24 | -0.0084 |
| `SRI` | ETH | 1h | 1260 | -0.0283 | -1.00 | -0.0629 |
| `SRI` | ETH | 4h | 1260 | -0.0272 | -0.96 | -0.0349 |
| `SRI` | ETH | 24h | 1260 | -0.0184 | -0.65 | -0.0146 |
| `SRI` | ETH | 72h | 1260 | +0.0118 | +0.42 | +0.0214 |
| `SRI` | ETH | 168h | 1260 | -0.0094 | -0.33 | -0.0037 |
| `absorption` | ETH | 1h | 1260 | +0.0487 | +1.73 | +0.0708 |
| `absorption` | ETH | 4h | 1260 | +0.0403 | +1.43 | +0.0489 |
| `absorption` | ETH | 24h | 1260 | -0.0027 | -0.10 | +0.0178 |
| `absorption` | ETH | 72h | 1260 | -0.0021 | -0.07 | -0.0129 |
| `absorption` | ETH | 168h | 1260 | -0.0102 | -0.36 | -0.0050 |
| `SRI` | SOL | 1h | 1260 | +0.0530 | +1.88 | +0.0147 |
| `SRI` | SOL | 4h | 1260 | +0.0126 | +0.45 | -0.0092 |
| `SRI` | SOL | 24h | 1260 | +0.0169 | +0.60 | +0.0106 |
| `SRI` | SOL | 72h | 1260 | +0.0803 | +2.86 | +0.0466 |
| `SRI` | SOL | 168h | 1260 | +0.0396 | +1.41 | +0.0399 |
| `absorption` | SOL | 1h | 1260 | +0.0103 | +0.36 | +0.0296 |
| `absorption` | SOL | 4h | 1260 | +0.0138 | +0.49 | +0.0492 |
| `absorption` | SOL | 24h | 1260 | -0.0309 | -1.10 | -0.0053 |
| `absorption` | SOL | 72h | 1260 | -0.0440 | -1.56 | -0.0234 |
| `absorption` | SOL | 168h | 1260 | -0.0608 | -2.16 | -0.0412 |
| `SRI` | BASKET_MEAN | 1h | 1260 | -0.0261 | -0.93 | -0.0342 |
| `SRI` | BASKET_MEAN | 4h | 1260 | -0.0370 | -1.31 | -0.0186 |
| `SRI` | BASKET_MEAN | 24h | 1259 | -0.0250 | -0.89 | -0.0100 |
| `SRI` | BASKET_MEAN | 72h | 1257 | +0.0094 | +0.33 | +0.0210 |
| `SRI` | BASKET_MEAN | 168h | 1253 | -0.0049 | -0.17 | -0.0053 |
| `absorption` | BASKET_MEAN | 1h | 1260 | +0.0274 | +0.97 | +0.0420 |
| `absorption` | BASKET_MEAN | 4h | 1260 | +0.0355 | +1.26 | +0.0398 |
| `absorption` | BASKET_MEAN | 24h | 1259 | +0.0007 | +0.03 | +0.0124 |
| `absorption` | BASKET_MEAN | 72h | 1257 | +0.0061 | +0.22 | -0.0002 |
| `absorption` | BASKET_MEAN | 168h | 1253 | -0.0081 | -0.29 | -0.0106 |

## Cross-token sign consistency (BTC/ETH/SOL)

`consistent=True` means all three tokens agree on IC sign (all +, all -).

### HV

| Signal x Horizon | Signs (BTC,ETH,SOL) | Consistent |
|---|---|---|
| `HV_v_H|1h` | (-1, -1, -1) | True |
| `HV_v_H|4h` | (-1, -1, -1) | True |
| `HV_v_H|24h` | (-1, -1, -1) | True |
| `HV_v_H|72h` | (1, 1, -1) | False |
| `HV_v_H|168h` | (1, -1, -1) | False |
| `HV_v_H_signed|1h` | (-1, -1, -1) | True |
| `HV_v_H_signed|4h` | (-1, -1, -1) | True |
| `HV_v_H_signed|24h` | (-1, -1, -1) | True |
| `HV_v_H_signed|72h` | (1, 1, -1) | False |
| `HV_v_H_signed|168h` | (1, -1, -1) | False |

### FIF

| Signal x Horizon | Signs (BTC,ETH,SOL) | Consistent |
|---|---|---|
| `FIF|1h` | (-1, -1, -1) | True |
| `FIF|4h` | (-1, -1, -1) | True |
| `FIF|24h` | (1, -1, -1) | False |
| `FIF|72h` | (1, -1, -1) | False |
| `FIF|168h` | (1, 1, -1) | False |
| `FIF_drift|1h` | (1, 1, -1) | False |
| `FIF_drift|4h` | (1, 1, -1) | False |
| `FIF_drift|24h` | (-1, -1, 1) | False |
| `FIF_drift|72h` | (1, 1, 1) | True |
| `FIF_drift|168h` | (1, 1, 1) | True |
| `FIF_x_drift|1h` | (1, 1, -1) | False |
| `FIF_x_drift|4h` | (-1, 1, -1) | False |
| `FIF_x_drift|24h` | (1, 1, 1) | True |
| `FIF_x_drift|72h` | (1, 1, 1) | True |
| `FIF_x_drift|168h` | (1, 1, 1) | True |

### WRD

| Signal x Horizon | Signs (BTC,ETH,SOL) | Consistent |
|---|---|---|
| `WRD_W1|1h` | (1, 1, 1) | True |
| `WRD_W1|4h` | (1, 1, 1) | True |
| `WRD_W1|24h` | (1, -1, 1) | False |
| `WRD_W1|72h` | (1, -1, 1) | False |
| `WRD_W1|168h` | (1, 1, 1) | True |
| `WRD_tail_shift|1h` | (-1, -1, 1) | False |
| `WRD_tail_shift|4h` | (-1, -1, 1) | False |
| `WRD_tail_shift|24h` | (-1, -1, 1) | False |
| `WRD_tail_shift|72h` | (1, -1, 1) | False |
| `WRD_tail_shift|168h` | (1, -1, 1) | False |
| `WRD_delta_skew|1h` | (-1, -1, -1) | True |
| `WRD_delta_skew|4h` | (1, 1, -1) | False |
| `WRD_delta_skew|24h` | (-1, -1, -1) | True |
| `WRD_delta_skew|72h` | (1, 1, 1) | True |
| `WRD_delta_skew|168h` | (1, -1, -1) | False |

### SRI

| Signal x Horizon | Signs (BTC,ETH,SOL) | Consistent |
|---|---|---|
| `SRI|1h` | (-1, -1, 1) | False |
| `SRI|4h` | (-1, -1, 1) | False |
| `SRI|24h` | (-1, -1, 1) | False |
| `SRI|72h` | (1, 1, 1) | True |
| `SRI|168h` | (-1, -1, 1) | False |
| `absorption|1h` | (1, 1, 1) | True |
| `absorption|4h` | (1, 1, 1) | True |
| `absorption|24h` | (1, -1, -1) | False |
| `absorption|72h` | (1, -1, -1) | False |
| `absorption|168h` | (-1, -1, -1) | True |

## Recommendation for Gate 1

- **Promote to Gate 1:** FIF, WRD
- **Hold for monitoring (do not promote yet):** HV, SRI

PASS rule: |Pearson IC| > 0.05 with |t| > 2 on >=2 of 5 horizons
for at least one (signal,token) pair, OR |IC| > 0.08 with |t| > 3 on a single horizon.

## Honest assessment (post-verdict)

The automated PASS for FIF and WRD requires important caveats:

- **FIF PASS is SOL-only.** Best result `FIF|SOL|168h` has IC=-0.0975 (t=-4.39) and is
  robust on SOL across 72h+168h horizons. BUT cross-token signs are inconsistent: FIF on
  BTC/ETH at 168h are weakly POSITIVE (+0.04, +0.005) while SOL is strongly NEGATIVE.
  This is the classic regime-specific / single-asset overfit pattern. The
  `FIF_drift` and `FIF_x_drift` directional composites DO show consistent positive
  signs across BTC/ETH/SOL at 72h and 168h, but magnitudes are modest (max IC ~0.086 on
  SOL, ~0.05 on BTC/ETH). This is the most interesting FIF angle.

- **WRD PASS is short-horizon BTC/ETH only and the sign on SOL flips.**
  `WRD_tail_shift` is the strongest variant: BTC 4h IC=-0.082 (t=-3.92), ETH 4h
  IC=-0.085 (t=-4.05). Sign is robust across BTC and ETH (both negative — i.e. when the
  right tail extends faster than the left tail, forward 4h returns are slightly
  negative — looks like a short-horizon mean-reversion / blow-off signal). On SOL the
  sign FLIPS to positive (+0.081 at 1h). Not cross-asset robust; likely captures
  BTC/ETH-specific blow-off exhaustion.
  Spearman ICs are an order of magnitude weaker than Pearson — the relationship is
  outlier-driven, not monotonic.

- **HV (Hurst Velocity) is essentially noise on BTC/ETH.** All 1h-168h ICs are
  |IC|<0.02 with |t|<1. Only SOL produces |IC|=0.062 at 1h. Verdict: weak / regime
  specific. The mathematical "leading indicator" claim does not survive 6 years of
  hourly BTC data.

- **SRI is borderline and inconsistent.** SOL at 72h hits IC=0.080 t=2.86, but the same
  signal on BTC/ETH/BASKET_MEAN is essentially zero or negative. SRI may have value as
  a regime filter but not as a forward-return predictor in raw form.

## Strongest single signal found

`FIF | SOL | 168h`: Pearson IC = -0.0975, t = -4.39, n = 2007, Spearman = -0.0914.
Interpretation: when Fisher information about SOL hourly returns is high (the return
distribution is "peaked" enough to permit drift estimation), the next 7 days of SOL
returns tend to be NEGATIVE. This is consistent with high-information periods being
late-stage trends near exhaustion. Replicates weakly on BTC/ETH but with opposite sign,
so SOL-specific.

## Final recommendation for Gate 1

**Promote to Gate 1 with caveats:** ONE indicator family deserves Gate 1 promotion —
the **FIF directional composite** (`FIF * sign(drift) * |drift|`, i.e. `FIF_x_drift`).
It is the only signal in this study with cross-token sign consistency across BTC/ETH/SOL
on the 24h/72h/168h horizons (all positive, indicating informed-direction days predict
forward returns) AND a non-trivial magnitude (~0.03-0.05 IC). That's exactly the
"meta-indicator times direction" structure the paper claims and it's the cleanest result
in the file.

WRD `tail_shift` is interesting as a SHORT-HORIZON BTC/ETH-only contrarian filter
(possible add-on to existing 4h reversal strategies) but should NOT get its own Gate 1
slot. HV and SRI should be killed as standalone signals — they may still have value
inside the catalogued synergies (SRI+HV+WRD rotation anticipator), but on their own they
do not survive a 2000+ observation IC test.

KILL: HV, SRI (as standalone). HOLD: WRD (as filter overlay only). PROMOTE: FIF
directional composite.
