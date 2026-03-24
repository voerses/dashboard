# Binance Futures Daily Metrics — Signal Analysis Results

**Run date:** 2026-03-24 10:42:09
**Script:** `research/binance_metrics_signal_test.py`

## Hypothesis
Binance Futures positioning data (OI, top trader L/S ratios, taker buy/sell ratios)
contains information that predicts future crypto returns. Specifically:
- Rising OI may indicate crowded positioning, creating cascade risk (contrarian)
- Top trader positioning may lead general positioning (smart money signal)
- Divergence between top trader and general L/S may signal coming reversals
- Taker buy/sell extremes may indicate short-term mean reversion
- Cross-token OI dispersion may indicate crowding in specific names

## Data
- **Source:** Binance Futures daily metrics (all_symbols_daily_ls.parquet)
- **Symbols:** 3 scopes (BTC, ETH, full panel of 37917 obs)
- **Date range:** Dec 2021 to Mar 2026
- **IS period:** < 2025-01-01 (~3 years)
- **OOS period:** >= 2025-01-01 (~1+ year)

## Signal Definitions

| Signal | Description |
|--------|-------------|
| `oi_roc_5d` | OI rate of change (5d) |
| `oi_roc_10d` | OI rate of change (10d) |
| `oi_roc_20d` | OI rate of change (20d) |
| `oi_zscore_20d` | OI z-score (20d rolling) |
| `toptrader_ls_raw` | Top trader L/S ratio (raw) |
| `toptrader_ls_zscore` | Top trader L/S z-score (20d) |
| `toptrader_ls_roc_5d` | Top trader L/S rate of change (5d) |
| `ls_divergence_sum` | Top trader - general L/S (sum-based) |
| `ls_divergence_count` | Top trader - general L/S (count-based) |
| `ls_divergence_ratio` | Top trader / general L/S ratio |
| `ls_divergence_zscore` | L/S divergence z-score (20d) |
| `oi_price_div_5d` | OI-price divergence (5d) |
| `oi_price_div_10d` | OI-price divergence (10d) |
| `oi_price_div_20d` | OI-price divergence (20d) |
| `taker_ratio_zscore` | Taker buy/sell z-score (20d) |
| `taker_ratio_raw` | Taker buy/sell ratio (raw) |
| `taker_ratio_range` | Taker ratio intraday range |
| `ls_ratio_range` | L/S ratio intraday range |
| `oi_dispersion` | Cross-token OI z-score dispersion |
| `toptrader_ls_dispersion` | Cross-token top trader L/S dispersion |
| `taker_dispersion` | Cross-token taker ratio dispersion |

## Pass Criteria
- |IC| > 0.05 (economically meaningful rank correlation)
- |t-stat| > 2.0 (statistically significant)
- Sign consistent IS -> OOS (signal doesn't flip direction)

## Full-Sample IC Results

| Scope | Signal | Horizon | IC | t-stat | p-value | N | Hit Rate |
|-------|--------|---------|---:|-------:|--------:|--:|---------:|
| BTC | ls_divergence_count | 14d | **-0.2040** | **-8.57** | 0.0000 | 1695 | 48.2% |
| BTC | ls_divergence_count | 1d | -0.0469 | -1.94 | 0.0526 | 1708 | 49.5% |
| BTC | ls_divergence_count | 3d | **-0.0749** | **-3.10** | 0.0020 | 1706 | 50.1% |
| BTC | ls_divergence_count | 7d | **-0.1570** | **-6.56** | 0.0000 | 1702 | 48.5% |
| BTC | ls_divergence_ratio | 14d | **-0.1771** | **-7.41** | 0.0000 | 1695 | 56.0% |
| BTC | ls_divergence_ratio | 1d | -0.0410 | -1.70 | 0.0902 | 1708 | 51.6% |
| BTC | ls_divergence_ratio | 3d | **-0.0765** | **-3.17** | 0.0016 | 1706 | 54.2% |
| BTC | ls_divergence_ratio | 7d | **-0.1508** | **-6.29** | 0.0000 | 1702 | 55.1% |
| BTC | ls_divergence_sum | 14d | **0.0728** | **3.00** | 0.0027 | 1695 | 51.4% |
| BTC | ls_divergence_sum | 1d | 0.0160 | 0.66 | 0.5078 | 1708 | 49.6% |
| BTC | ls_divergence_sum | 3d | 0.0159 | 0.66 | 0.5119 | 1706 | 49.1% |
| BTC | ls_divergence_sum | 7d | 0.0175 | 0.72 | 0.4715 | 1702 | 49.1% |
| BTC | ls_divergence_zscore | 14d | -0.0170 | -0.69 | 0.4918 | 1634 | 51.0% |
| BTC | ls_divergence_zscore | 1d | 0.0292 | 1.18 | 0.2370 | 1647 | 51.9% |
| BTC | ls_divergence_zscore | 3d | 0.0398 | 1.61 | 0.1067 | 1645 | 50.8% |
| BTC | ls_divergence_zscore | 7d | -0.0187 | -0.76 | 0.4487 | 1641 | 49.4% |
| BTC | ls_ratio_range | 14d | **-0.1637** | **-7.40** | 0.0000 | 1991 | 54.7% |
| BTC | ls_ratio_range | 1d | -0.0331 | -1.48 | 0.1382 | 2004 | 50.7% |
| BTC | ls_ratio_range | 3d | **-0.0688** | **-3.09** | 0.0021 | 2002 | 53.4% |
| BTC | ls_ratio_range | 7d | **-0.0939** | **-4.21** | 0.0000 | 1998 | 53.6% |
| BTC | oi_dispersion | 14d | 0.0199 | 0.78 | 0.4369 | 1535 | 52.4% |
| BTC | oi_dispersion | 1d | -0.0036 | -0.14 | 0.8865 | 1548 | 49.6% |
| BTC | oi_dispersion | 3d | -0.0314 | -1.23 | 0.2172 | 1546 | 52.6% |
| BTC | oi_dispersion | 7d | 0.0116 | 0.45 | 0.6501 | 1542 | 51.4% |
| BTC | oi_price_div_10d | 14d | -0.0415 | -1.86 | 0.0634 | 2000 | 47.6% |
| BTC | oi_price_div_10d | 1d | -0.0251 | -1.13 | 0.2600 | 2013 | 49.2% |
| BTC | oi_price_div_10d | 3d | -0.0187 | -0.84 | 0.4013 | 2011 | 48.0% |
| BTC | oi_price_div_10d | 7d | -0.0357 | -1.60 | 0.1098 | 2007 | 47.6% |
| BTC | oi_price_div_20d | 14d | -0.0022 | -0.10 | 0.9221 | 1990 | 47.8% |
| BTC | oi_price_div_20d | 1d | -0.0246 | -1.10 | 0.2704 | 2003 | 49.9% |
| BTC | oi_price_div_20d | 3d | -0.0470 | -2.10 | 0.0357 | 2001 | 47.2% |
| BTC | oi_price_div_20d | 7d | -0.0336 | -1.50 | 0.1335 | 1997 | 47.0% |
| BTC | oi_price_div_5d | 14d | -0.0365 | -1.63 | 0.1025 | 2005 | 47.0% |
| BTC | oi_price_div_5d | 1d | -0.0303 | -1.36 | 0.1738 | 2018 | 49.5% |
| BTC | oi_price_div_5d | 3d | -0.0373 | -1.67 | 0.0942 | 2016 | 46.6% |
| BTC | oi_price_div_5d | 7d | -0.0411 | -1.84 | 0.0654 | 2012 | 47.1% |
| BTC | oi_roc_10d | 14d | -0.0081 | -0.36 | 0.7188 | 2000 | 51.5% |
| BTC | oi_roc_10d | 1d | -0.0133 | -0.59 | 0.5524 | 2013 | 49.7% |
| BTC | oi_roc_10d | 3d | -0.0032 | -0.14 | 0.8875 | 2011 | 49.3% |
| BTC | oi_roc_10d | 7d | 0.0102 | 0.46 | 0.6479 | 2007 | 51.5% |
| BTC | oi_roc_20d | 14d | 0.0418 | 1.86 | 0.0624 | 1990 | 52.5% |
| BTC | oi_roc_20d | 1d | -0.0137 | -0.61 | 0.5409 | 2003 | 50.2% |
| BTC | oi_roc_20d | 3d | -0.0248 | -1.11 | 0.2679 | 2001 | 49.0% |
| BTC | oi_roc_20d | 7d | -0.0044 | -0.20 | 0.8445 | 1997 | 49.9% |
| BTC | oi_roc_5d | 14d | 0.0017 | 0.08 | 0.9384 | 2005 | 50.1% |
| BTC | oi_roc_5d | 1d | -0.0192 | -0.86 | 0.3878 | 2018 | 49.8% |
| BTC | oi_roc_5d | 3d | -0.0216 | -0.97 | 0.3334 | 2016 | 47.1% |
| BTC | oi_roc_5d | 7d | -0.0159 | -0.71 | 0.4751 | 2012 | 48.2% |
| BTC | oi_zscore_20d | 14d | 0.0236 | 1.05 | 0.2931 | 1991 | 51.7% |
| BTC | oi_zscore_20d | 1d | -0.0151 | -0.68 | 0.4987 | 2004 | 50.2% |
| BTC | oi_zscore_20d | 3d | -0.0036 | -0.16 | 0.8737 | 2002 | 49.8% |
| BTC | oi_zscore_20d | 7d | -0.0005 | -0.02 | 0.9821 | 1998 | 50.3% |
| BTC | taker_dispersion | 14d | -0.0498 | -1.86 | 0.0638 | 1387 | 53.9% |
| BTC | taker_dispersion | 1d | 0.0063 | 0.24 | 0.8135 | 1400 | 49.6% |
| BTC | taker_dispersion | 3d | 0.0281 | 1.05 | 0.2931 | 1398 | 53.1% |
| BTC | taker_dispersion | 7d | -0.0103 | -0.39 | 0.6997 | 1394 | 52.6% |
| BTC | taker_ratio_range | 14d | 0.0361 | 1.57 | 0.1175 | 1882 | 55.6% |
| BTC | taker_ratio_range | 1d | 0.0082 | 0.36 | 0.7225 | 1895 | 50.8% |
| BTC | taker_ratio_range | 3d | 0.0134 | 0.58 | 0.5598 | 1893 | 53.7% |
| BTC | taker_ratio_range | 7d | 0.0187 | 0.81 | 0.4164 | 1889 | 54.3% |
| BTC | taker_ratio_raw | 14d | **0.0609** | **2.65** | 0.0082 | 1882 | 55.6% |
| BTC | taker_ratio_raw | 1d | 0.0143 | 0.62 | 0.5343 | 1895 | 50.8% |
| BTC | taker_ratio_raw | 3d | 0.0264 | 1.15 | 0.2515 | 1893 | 53.7% |
| BTC | taker_ratio_raw | 7d | 0.0213 | 0.93 | 0.3548 | 1889 | 54.3% |
| BTC | taker_ratio_zscore | 14d | -0.0322 | -1.38 | 0.1666 | 1843 | 48.3% |
| BTC | taker_ratio_zscore | 1d | -0.0091 | -0.39 | 0.6946 | 1856 | 49.6% |
| BTC | taker_ratio_zscore | 3d | -0.0074 | -0.32 | 0.7500 | 1854 | 49.0% |
| BTC | taker_ratio_zscore | 7d | -0.0450 | -1.94 | 0.0531 | 1850 | 46.4% |
| BTC | toptrader_ls_dispersion | 14d | **0.1061** | **3.66** | 0.0003 | 1178 | 55.0% |
| BTC | toptrader_ls_dispersion | 1d | -0.0031 | -0.11 | 0.9153 | 1191 | 50.8% |
| BTC | toptrader_ls_dispersion | 3d | 0.0126 | 0.44 | 0.6630 | 1189 | 54.1% |
| BTC | toptrader_ls_dispersion | 7d | 0.0386 | 1.33 | 0.1844 | 1185 | 53.9% |
| BTC | toptrader_ls_raw | 14d | **-0.1656** | **-6.91** | 0.0000 | 1695 | 56.0% |
| BTC | toptrader_ls_raw | 1d | **-0.0609** | **-2.52** | 0.0118 | 1708 | 51.6% |
| BTC | toptrader_ls_raw | 3d | **-0.0937** | **-3.88** | 0.0001 | 1706 | 54.2% |
| BTC | toptrader_ls_raw | 7d | **-0.1373** | **-5.72** | 0.0000 | 1702 | 55.1% |
| BTC | toptrader_ls_roc_5d | 14d | 0.0003 | 0.01 | 0.9917 | 1677 | 49.1% |
| BTC | toptrader_ls_roc_5d | 1d | -0.0020 | -0.08 | 0.9355 | 1690 | 50.4% |
| BTC | toptrader_ls_roc_5d | 3d | -0.0051 | -0.21 | 0.8355 | 1688 | 48.9% |
| BTC | toptrader_ls_roc_5d | 7d | -0.0346 | -1.42 | 0.1556 | 1684 | 48.5% |
| BTC | toptrader_ls_zscore | 14d | -0.0385 | -1.56 | 0.1199 | 1634 | 47.4% |
| BTC | toptrader_ls_zscore | 1d | -0.0005 | -0.02 | 0.9826 | 1647 | 49.8% |
| BTC | toptrader_ls_zscore | 3d | -0.0007 | -0.03 | 0.9759 | 1645 | 49.4% |
| BTC | toptrader_ls_zscore | 7d | -0.0275 | -1.11 | 0.2660 | 1641 | 49.8% |
| ETH | ls_divergence_count | 14d | **-0.0700** | **-2.47** | 0.0137 | 1239 | 49.7% |
| ETH | ls_divergence_count | 1d | -0.0058 | -0.21 | 0.8373 | 1252 | 51.8% |
| ETH | ls_divergence_count | 3d | -0.0091 | -0.32 | 0.7469 | 1250 | 51.0% |
| ETH | ls_divergence_count | 7d | -0.0554 | -1.96 | 0.0504 | 1246 | 50.0% |
| ETH | ls_divergence_ratio | 14d | -0.0099 | -0.35 | 0.7271 | 1239 | 48.2% |
| ETH | ls_divergence_ratio | 1d | 0.0011 | 0.04 | 0.9681 | 1252 | 50.3% |
| ETH | ls_divergence_ratio | 3d | 0.0052 | 0.18 | 0.8548 | 1250 | 51.3% |
| ETH | ls_divergence_ratio | 7d | -0.0163 | -0.58 | 0.5644 | 1246 | 50.1% |
| ETH | ls_divergence_sum | 14d | **0.0772** | **2.72** | 0.0066 | 1239 | 52.3% |
| ETH | ls_divergence_sum | 1d | 0.0041 | 0.15 | 0.8847 | 1252 | 49.8% |
| ETH | ls_divergence_sum | 3d | -0.0058 | -0.21 | 0.8373 | 1250 | 49.0% |
| ETH | ls_divergence_sum | 7d | 0.0331 | 1.17 | 0.2434 | 1246 | 50.4% |
| ETH | ls_divergence_zscore | 14d | -0.0264 | -0.91 | 0.3647 | 1178 | 50.0% |
| ETH | ls_divergence_zscore | 1d | 0.0440 | 1.52 | 0.1290 | 1191 | 51.4% |
| ETH | ls_divergence_zscore | 3d | **0.0675** | **2.33** | 0.0200 | 1189 | 50.9% |
| ETH | ls_divergence_zscore | 7d | -0.0129 | -0.44 | 0.6584 | 1185 | 49.5% |
| ETH | ls_ratio_range | 14d | **-0.1034** | **-4.07** | 0.0000 | 1535 | 46.8% |
| ETH | ls_ratio_range | 1d | -0.0226 | -0.89 | 0.3741 | 1548 | 50.0% |
| ETH | ls_ratio_range | 3d | -0.0333 | -1.31 | 0.1902 | 1546 | 51.3% |
| ETH | ls_ratio_range | 7d | **-0.0600** | **-2.36** | 0.0184 | 1542 | 49.4% |
| ETH | oi_dispersion | 14d | 0.0023 | 0.09 | 0.9272 | 1535 | 46.5% |
| ETH | oi_dispersion | 1d | -0.0028 | -0.11 | 0.9112 | 1548 | 50.0% |
| ETH | oi_dispersion | 3d | -0.0414 | -1.63 | 0.1036 | 1546 | 51.2% |
| ETH | oi_dispersion | 7d | -0.0242 | -0.95 | 0.3424 | 1542 | 49.2% |
| ETH | oi_price_div_10d | 14d | 0.0156 | 0.61 | 0.5405 | 1544 | 52.4% |
| ETH | oi_price_div_10d | 1d | -0.0270 | -1.06 | 0.2876 | 1557 | 49.3% |
| ETH | oi_price_div_10d | 3d | -0.0227 | -0.89 | 0.3715 | 1555 | 49.0% |
| ETH | oi_price_div_10d | 7d | 0.0127 | 0.50 | 0.6176 | 1551 | 51.0% |
| ETH | oi_price_div_20d | 14d | **0.0655** | **2.57** | 0.0103 | 1534 | 53.0% |
| ETH | oi_price_div_20d | 1d | -0.0114 | -0.45 | 0.6546 | 1547 | 49.9% |
| ETH | oi_price_div_20d | 3d | -0.0158 | -0.62 | 0.5344 | 1545 | 49.4% |
| ETH | oi_price_div_20d | 7d | 0.0008 | 0.03 | 0.9762 | 1541 | 51.6% |
| ETH | oi_price_div_5d | 14d | 0.0278 | 1.09 | 0.2742 | 1549 | 52.9% |
| ETH | oi_price_div_5d | 1d | -0.0380 | -1.50 | 0.1336 | 1562 | 49.3% |
| ETH | oi_price_div_5d | 3d | -0.0215 | -0.85 | 0.3952 | 1560 | 49.3% |
| ETH | oi_price_div_5d | 7d | -0.0118 | -0.46 | 0.6432 | 1556 | 50.1% |
| ETH | oi_roc_10d | 14d | 0.0141 | 0.55 | 0.5805 | 1544 | 52.1% |
| ETH | oi_roc_10d | 1d | -0.0221 | -0.87 | 0.3846 | 1557 | 50.0% |
| ETH | oi_roc_10d | 3d | -0.0117 | -0.46 | 0.6445 | 1555 | 51.1% |
| ETH | oi_roc_10d | 7d | 0.0206 | 0.81 | 0.4165 | 1551 | 50.4% |
| ETH | oi_roc_20d | 14d | **0.0677** | **2.65** | 0.0080 | 1534 | 52.9% |
| ETH | oi_roc_20d | 1d | -0.0143 | -0.56 | 0.5734 | 1547 | 49.1% |
| ETH | oi_roc_20d | 3d | -0.0117 | -0.46 | 0.6461 | 1545 | 48.5% |
| ETH | oi_roc_20d | 7d | 0.0067 | 0.26 | 0.7942 | 1541 | 49.8% |
| ETH | oi_roc_5d | 14d | 0.0268 | 1.05 | 0.2925 | 1549 | 51.1% |
| ETH | oi_roc_5d | 1d | -0.0327 | -1.29 | 0.1970 | 1562 | 49.0% |
| ETH | oi_roc_5d | 3d | -0.0130 | -0.51 | 0.6067 | 1560 | 48.8% |
| ETH | oi_roc_5d | 7d | -0.0074 | -0.29 | 0.7710 | 1556 | 49.5% |
| ETH | oi_zscore_20d | 14d | 0.0487 | 1.91 | 0.0567 | 1535 | 52.9% |
| ETH | oi_zscore_20d | 1d | -0.0250 | -0.98 | 0.3259 | 1548 | 49.7% |
| ETH | oi_zscore_20d | 3d | -0.0059 | -0.23 | 0.8163 | 1546 | 49.2% |
| ETH | oi_zscore_20d | 7d | 0.0071 | 0.28 | 0.7813 | 1542 | 49.5% |
| ETH | taker_dispersion | 14d | -0.0373 | -1.39 | 0.1647 | 1387 | 48.6% |
| ETH | taker_dispersion | 1d | 0.0033 | 0.12 | 0.9008 | 1400 | 49.9% |
| ETH | taker_dispersion | 3d | 0.0366 | 1.37 | 0.1710 | 1398 | 51.4% |
| ETH | taker_dispersion | 7d | 0.0108 | 0.40 | 0.6880 | 1394 | 50.6% |
| ETH | taker_ratio_range | 14d | -0.0437 | -1.65 | 0.0991 | 1426 | 47.6% |
| ETH | taker_ratio_range | 1d | -0.0300 | -1.14 | 0.2555 | 1439 | 50.0% |
| ETH | taker_ratio_range | 3d | -0.0269 | -1.02 | 0.3081 | 1437 | 51.3% |
| ETH | taker_ratio_range | 7d | -0.0355 | -1.34 | 0.1797 | 1433 | 50.0% |
| ETH | taker_ratio_raw | 14d | -0.0187 | -0.71 | 0.4799 | 1426 | 47.6% |
| ETH | taker_ratio_raw | 1d | -0.0292 | -1.11 | 0.2680 | 1439 | 50.0% |
| ETH | taker_ratio_raw | 3d | -0.0153 | -0.58 | 0.5634 | 1437 | 51.3% |
| ETH | taker_ratio_raw | 7d | -0.0292 | -1.11 | 0.2692 | 1433 | 50.0% |
| ETH | taker_ratio_zscore | 14d | -0.0349 | -1.30 | 0.1942 | 1387 | 47.9% |
| ETH | taker_ratio_zscore | 1d | -0.0300 | -1.12 | 0.2616 | 1400 | 49.2% |
| ETH | taker_ratio_zscore | 3d | 0.0030 | 0.11 | 0.9096 | 1398 | 49.6% |
| ETH | taker_ratio_zscore | 7d | -0.0256 | -0.96 | 0.3394 | 1394 | 47.3% |
| ETH | toptrader_ls_dispersion | 14d | **0.0764** | **2.63** | 0.0087 | 1178 | 48.9% |
| ETH | toptrader_ls_dispersion | 1d | -0.0012 | -0.04 | 0.9660 | 1191 | 50.5% |
| ETH | toptrader_ls_dispersion | 3d | -0.0063 | -0.22 | 0.8288 | 1189 | 51.5% |
| ETH | toptrader_ls_dispersion | 7d | -0.0061 | -0.21 | 0.8347 | 1185 | 51.0% |
| ETH | toptrader_ls_raw | 14d | **-0.0828** | **-2.92** | 0.0035 | 1239 | 48.2% |
| ETH | toptrader_ls_raw | 1d | -0.0320 | -1.13 | 0.2583 | 1252 | 50.3% |
| ETH | toptrader_ls_raw | 3d | -0.0445 | -1.57 | 0.1160 | 1250 | 51.3% |
| ETH | toptrader_ls_raw | 7d | **-0.0631** | **-2.23** | 0.0260 | 1246 | 50.1% |
| ETH | toptrader_ls_roc_5d | 14d | **-0.0864** | **-3.03** | 0.0025 | 1221 | 46.3% |
| ETH | toptrader_ls_roc_5d | 1d | -0.0366 | -1.28 | 0.1991 | 1234 | 48.1% |
| ETH | toptrader_ls_roc_5d | 3d | **-0.0722** | **-2.54** | 0.0113 | 1232 | 47.2% |
| ETH | toptrader_ls_roc_5d | 7d | **-0.1017** | **-3.58** | 0.0004 | 1228 | 46.7% |
| ETH | toptrader_ls_zscore | 14d | -0.0435 | -1.49 | 0.1359 | 1178 | 48.8% |
| ETH | toptrader_ls_zscore | 1d | -0.0267 | -0.92 | 0.3573 | 1191 | 48.4% |
| ETH | toptrader_ls_zscore | 3d | -0.0522 | -1.80 | 0.0721 | 1189 | 47.8% |
| ETH | toptrader_ls_zscore | 7d | **-0.0905** | **-3.13** | 0.0018 | 1185 | 47.8% |
| PANEL | ls_divergence_count | 14d | **-0.1191** | **-21.36** | 0.0000 | 31730 | 45.2% |
| PANEL | ls_divergence_count | 1d | -0.0263 | -4.71 | 0.0000 | 32081 | 48.6% |
| PANEL | ls_divergence_count | 3d | -0.0455 | -8.16 | 0.0000 | 32027 | 48.3% |
| PANEL | ls_divergence_count | 7d | **-0.0957** | **-17.18** | 0.0000 | 31919 | 46.4% |
| PANEL | ls_divergence_ratio | 14d | **-0.1215** | **-21.81** | 0.0000 | 31730 | 46.2% |
| PANEL | ls_divergence_ratio | 1d | -0.0291 | -5.21 | 0.0000 | 32081 | 48.8% |
| PANEL | ls_divergence_ratio | 3d | **-0.0501** | **-8.98** | 0.0000 | 32027 | 48.8% |
| PANEL | ls_divergence_ratio | 7d | **-0.1016** | **-18.25** | 0.0000 | 31919 | 47.5% |
| PANEL | ls_divergence_sum | 14d | -0.0029 | -0.51 | 0.6084 | 31730 | 51.3% |
| PANEL | ls_divergence_sum | 1d | -0.0042 | -0.75 | 0.4548 | 32081 | 49.6% |
| PANEL | ls_divergence_sum | 3d | -0.0038 | -0.68 | 0.4950 | 32027 | 49.6% |
| PANEL | ls_divergence_sum | 7d | -0.0130 | -2.32 | 0.0203 | 31919 | 50.0% |
| PANEL | ls_divergence_zscore | 14d | -0.0178 | -3.10 | 0.0019 | 30457 | 49.6% |
| PANEL | ls_divergence_zscore | 1d | -0.0050 | -0.88 | 0.3803 | 30808 | 49.5% |
| PANEL | ls_divergence_zscore | 3d | -0.0060 | -1.06 | 0.2909 | 30754 | 49.7% |
| PANEL | ls_divergence_zscore | 7d | -0.0376 | -6.58 | 0.0000 | 30646 | 48.7% |
| PANEL | ls_ratio_range | 14d | **-0.0574** | **-11.11** | 0.0000 | 37378 | 45.6% |
| PANEL | ls_ratio_range | 1d | -0.0205 | -3.98 | 0.0001 | 37729 | 48.7% |
| PANEL | ls_ratio_range | 3d | -0.0244 | -4.73 | 0.0000 | 37675 | 48.4% |
| PANEL | ls_ratio_range | 7d | -0.0452 | -8.77 | 0.0000 | 37567 | 47.0% |
| PANEL | oi_dispersion | 14d | 0.0222 | 4.26 | 0.0000 | 36922 | 45.0% |
| PANEL | oi_dispersion | 1d | -0.0034 | -0.67 | 0.5055 | 37273 | 48.6% |
| PANEL | oi_dispersion | 3d | -0.0221 | -4.26 | 0.0000 | 37219 | 48.3% |
| PANEL | oi_dispersion | 7d | 0.0039 | 0.76 | 0.4482 | 37111 | 46.7% |
| PANEL | oi_price_div_10d | 14d | -0.0090 | -1.75 | 0.0806 | 37431 | 50.4% |
| PANEL | oi_price_div_10d | 1d | -0.0194 | -3.77 | 0.0002 | 37782 | 49.6% |
| PANEL | oi_price_div_10d | 3d | -0.0185 | -3.60 | 0.0003 | 37728 | 49.6% |
| PANEL | oi_price_div_10d | 7d | -0.0208 | -4.04 | 0.0001 | 37620 | 49.9% |
| PANEL | oi_price_div_20d | 14d | 0.0100 | 1.93 | 0.0536 | 37161 | 51.5% |
| PANEL | oi_price_div_20d | 1d | -0.0125 | -2.42 | 0.0154 | 37512 | 50.0% |
| PANEL | oi_price_div_20d | 3d | -0.0182 | -3.53 | 0.0004 | 37458 | 49.9% |
| PANEL | oi_price_div_20d | 7d | -0.0157 | -3.03 | 0.0024 | 37350 | 49.9% |
| PANEL | oi_price_div_5d | 14d | -0.0043 | -0.83 | 0.4042 | 37566 | 51.0% |
| PANEL | oi_price_div_5d | 1d | -0.0220 | -4.29 | 0.0000 | 37917 | 49.8% |
| PANEL | oi_price_div_5d | 3d | -0.0194 | -3.78 | 0.0002 | 37863 | 49.7% |
| PANEL | oi_price_div_5d | 7d | -0.0211 | -4.09 | 0.0000 | 37755 | 49.7% |
| PANEL | oi_roc_10d | 14d | 0.0017 | 0.34 | 0.7353 | 37431 | 49.8% |
| PANEL | oi_roc_10d | 1d | -0.0192 | -3.74 | 0.0002 | 37782 | 49.1% |
| PANEL | oi_roc_10d | 3d | -0.0092 | -1.78 | 0.0749 | 37728 | 49.8% |
| PANEL | oi_roc_10d | 7d | -0.0060 | -1.15 | 0.2481 | 37620 | 49.4% |
| PANEL | oi_roc_20d | 14d | 0.0190 | 3.67 | 0.0002 | 37161 | 50.6% |
| PANEL | oi_roc_20d | 1d | -0.0139 | -2.69 | 0.0072 | 37512 | 49.2% |
| PANEL | oi_roc_20d | 3d | -0.0113 | -2.20 | 0.0281 | 37458 | 49.3% |
| PANEL | oi_roc_20d | 7d | -0.0073 | -1.41 | 0.1575 | 37350 | 49.0% |
| PANEL | oi_roc_5d | 14d | -0.0009 | -0.17 | 0.8656 | 37566 | 49.8% |
| PANEL | oi_roc_5d | 1d | -0.0251 | -4.89 | 0.0000 | 37917 | 49.1% |
| PANEL | oi_roc_5d | 3d | -0.0166 | -3.22 | 0.0013 | 37863 | 49.3% |
| PANEL | oi_roc_5d | 7d | -0.0205 | -3.98 | 0.0001 | 37755 | 48.7% |
| PANEL | oi_zscore_20d | 14d | 0.0068 | 1.32 | 0.1870 | 37188 | 50.7% |
| PANEL | oi_zscore_20d | 1d | -0.0198 | -3.84 | 0.0001 | 37539 | 49.4% |
| PANEL | oi_zscore_20d | 3d | -0.0102 | -1.98 | 0.0481 | 37485 | 49.9% |
| PANEL | oi_zscore_20d | 7d | -0.0085 | -1.65 | 0.0991 | 37377 | 49.7% |
| PANEL | taker_dispersion | 14d | **-0.0766** | **-14.23** | 0.0000 | 34300 | 46.0% |
| PANEL | taker_dispersion | 1d | -0.0322 | -6.00 | 0.0000 | 34651 | 48.7% |
| PANEL | taker_dispersion | 3d | -0.0196 | -3.65 | 0.0003 | 34597 | 48.7% |
| PANEL | taker_dispersion | 7d | -0.0497 | -9.23 | 0.0000 | 34489 | 47.3% |
| PANEL | taker_ratio_range | 14d | -0.0170 | -3.20 | 0.0014 | 35440 | 46.1% |
| PANEL | taker_ratio_range | 1d | -0.0191 | -3.61 | 0.0003 | 35791 | 48.8% |
| PANEL | taker_ratio_range | 3d | -0.0210 | -3.97 | 0.0001 | 35737 | 48.6% |
| PANEL | taker_ratio_range | 7d | -0.0184 | -3.46 | 0.0005 | 35629 | 47.4% |
| PANEL | taker_ratio_raw | 14d | -0.0028 | -0.52 | 0.6033 | 35440 | 46.1% |
| PANEL | taker_ratio_raw | 1d | -0.0152 | -2.88 | 0.0039 | 35791 | 48.8% |
| PANEL | taker_ratio_raw | 3d | -0.0181 | -3.42 | 0.0006 | 35737 | 48.6% |
| PANEL | taker_ratio_raw | 7d | -0.0132 | -2.50 | 0.0126 | 35629 | 47.4% |
| PANEL | taker_ratio_zscore | 14d | **-0.0525** | **-9.77** | 0.0000 | 34585 | 48.5% |
| PANEL | taker_ratio_zscore | 1d | -0.0297 | -5.56 | 0.0000 | 34936 | 48.8% |
| PANEL | taker_ratio_zscore | 3d | -0.0392 | -7.33 | 0.0000 | 34882 | 48.8% |
| PANEL | taker_ratio_zscore | 7d | -0.0429 | -8.00 | 0.0000 | 34774 | 48.8% |
| PANEL | toptrader_ls_dispersion | 14d | 0.0467 | 8.12 | 0.0000 | 30120 | 46.1% |
| PANEL | toptrader_ls_dispersion | 1d | 0.0032 | 0.55 | 0.5793 | 30471 | 48.9% |
| PANEL | toptrader_ls_dispersion | 3d | -0.0072 | -1.26 | 0.2067 | 30417 | 49.1% |
| PANEL | toptrader_ls_dispersion | 7d | -0.0196 | -3.42 | 0.0006 | 30309 | 47.6% |
| PANEL | toptrader_ls_raw | 14d | **-0.1342** | **-24.13** | 0.0000 | 31730 | 46.2% |
| PANEL | toptrader_ls_raw | 1d | -0.0315 | -5.64 | 0.0000 | 32081 | 48.8% |
| PANEL | toptrader_ls_raw | 3d | **-0.0531** | **-9.51** | 0.0000 | 32027 | 48.8% |
| PANEL | toptrader_ls_raw | 7d | **-0.0957** | **-17.17** | 0.0000 | 31919 | 47.5% |
| PANEL | toptrader_ls_roc_5d | 14d | -0.0175 | -3.11 | 0.0019 | 31358 | 49.1% |
| PANEL | toptrader_ls_roc_5d | 1d | -0.0044 | -0.78 | 0.4340 | 31709 | 49.8% |
| PANEL | toptrader_ls_roc_5d | 3d | -0.0028 | -0.49 | 0.6226 | 31655 | 49.9% |
| PANEL | toptrader_ls_roc_5d | 7d | -0.0284 | -5.05 | 0.0000 | 31547 | 49.1% |
| PANEL | toptrader_ls_zscore | 14d | -0.0117 | -2.05 | 0.0404 | 30457 | 49.6% |
| PANEL | toptrader_ls_zscore | 1d | -0.0077 | -1.35 | 0.1755 | 30808 | 49.6% |
| PANEL | toptrader_ls_zscore | 3d | -0.0096 | -1.69 | 0.0908 | 30754 | 49.5% |
| PANEL | toptrader_ls_zscore | 7d | -0.0286 | -5.01 | 0.0000 | 30646 | 48.9% |

## IS / OOS Split Results

| Scope | Signal | Horizon | IS IC | IS t-stat | OOS IC | OOS t-stat | Consistent |
|-------|--------|---------|------:|----------:|-------:|-----------:|:----------:|
| BTC | ls_divergence_count | 14d | -0.1187 | -4.25 | -0.2303 | -4.88 | YES |
| BTC | ls_divergence_count | 1d | -0.0087 | -0.31 | -0.0780 | -1.64 | YES |
| BTC | ls_divergence_count | 3d | -0.0166 | -0.59 | -0.1159 | -2.44 | YES |
| BTC | ls_divergence_count | 7d | -0.0802 | -2.86 | -0.2056 | -4.37 | YES |
| BTC | ls_divergence_ratio | 14d | -0.1251 | -4.49 | -0.0146 | -0.30 | YES |
| BTC | ls_divergence_ratio | 1d | -0.0108 | -0.38 | -0.0272 | -0.57 | YES |
| BTC | ls_divergence_ratio | 3d | -0.0294 | -1.05 | -0.0880 | -1.84 | YES |
| BTC | ls_divergence_ratio | 7d | -0.0944 | -3.38 | -0.1057 | -2.21 | YES |
| BTC | ls_divergence_sum | 14d | 0.1500 | 5.40 | 0.0962 | 1.99 | YES |
| BTC | ls_divergence_sum | 1d | 0.0442 | 1.58 | 0.0054 | 0.11 | YES |
| BTC | ls_divergence_sum | 3d | 0.0742 | 2.65 | -0.0593 | -1.24 | NO |
| BTC | ls_divergence_sum | 7d | 0.0846 | 3.02 | -0.0182 | -0.38 | NO |
| BTC | ls_divergence_zscore | 14d | 0.0197 | 0.68 | -0.1364 | -2.84 | NO |
| BTC | ls_divergence_zscore | 1d | 0.0398 | 1.38 | -0.0046 | -0.10 | NO |
| BTC | ls_divergence_zscore | 3d | 0.0636 | 2.21 | -0.0358 | -0.75 | NO |
| BTC | ls_divergence_zscore | 7d | 0.0109 | 0.38 | -0.1059 | -2.21 | NO |
| BTC | ls_ratio_range | 14d | -0.1909 | -7.69 | -0.1147 | -2.38 | YES |
| BTC | ls_ratio_range | 1d | -0.0434 | -1.72 | -0.0119 | -0.25 | YES |
| BTC | ls_ratio_range | 3d | -0.0854 | -3.39 | -0.0284 | -0.59 | YES |
| BTC | ls_ratio_range | 7d | -0.1140 | -4.54 | -0.0529 | -1.10 | YES |
| BTC | oi_dispersion | 14d | 0.0269 | 0.90 | -0.0161 | -0.33 | NO |
| BTC | oi_dispersion | 1d | 0.0098 | 0.33 | -0.0436 | -0.91 | NO |
| BTC | oi_dispersion | 3d | -0.0073 | -0.24 | -0.1064 | -2.24 | YES |
| BTC | oi_dispersion | 7d | 0.0255 | 0.85 | -0.0488 | -1.02 | NO |
| BTC | oi_price_div_10d | 14d | -0.0602 | -2.39 | 0.0204 | 0.42 | NO |
| BTC | oi_price_div_10d | 1d | -0.0218 | -0.86 | -0.0447 | -0.94 | YES |
| BTC | oi_price_div_10d | 3d | -0.0195 | -0.77 | -0.0331 | -0.69 | YES |
| BTC | oi_price_div_10d | 7d | -0.0419 | -1.66 | -0.0279 | -0.58 | YES |
| BTC | oi_price_div_20d | 14d | -0.0249 | -0.99 | 0.0761 | 1.57 | NO |
| BTC | oi_price_div_20d | 1d | -0.0310 | -1.23 | -0.0090 | -0.19 | YES |
| BTC | oi_price_div_20d | 3d | -0.0572 | -2.26 | -0.0224 | -0.47 | YES |
| BTC | oi_price_div_20d | 7d | -0.0564 | -2.23 | 0.0481 | 1.00 | NO |
| BTC | oi_price_div_5d | 14d | -0.0403 | -1.60 | -0.0357 | -0.74 | YES |
| BTC | oi_price_div_5d | 1d | -0.0251 | -1.00 | -0.0630 | -1.32 | YES |
| BTC | oi_price_div_5d | 3d | -0.0311 | -1.23 | -0.0879 | -1.84 | YES |
| BTC | oi_price_div_5d | 7d | -0.0414 | -1.65 | -0.0504 | -1.05 | YES |
| BTC | oi_roc_10d | 14d | -0.0200 | -0.79 | -0.0264 | -0.54 | YES |
| BTC | oi_roc_10d | 1d | -0.0069 | -0.27 | -0.0603 | -1.26 | YES |
| BTC | oi_roc_10d | 3d | 0.0038 | 0.15 | -0.0709 | -1.48 | NO |
| BTC | oi_roc_10d | 7d | 0.0113 | 0.45 | -0.0500 | -1.04 | NO |
| BTC | oi_roc_20d | 14d | 0.0155 | 0.61 | 0.0653 | 1.35 | YES |
| BTC | oi_roc_20d | 1d | -0.0227 | -0.90 | -0.0096 | -0.20 | YES |
| BTC | oi_roc_20d | 3d | -0.0356 | -1.41 | -0.0295 | -0.62 | YES |
| BTC | oi_roc_20d | 7d | -0.0278 | -1.10 | 0.0313 | 0.65 | NO |
| BTC | oi_roc_5d | 14d | 0.0024 | 0.09 | -0.0518 | -1.07 | NO |
| BTC | oi_roc_5d | 1d | -0.0126 | -0.50 | -0.0662 | -1.39 | YES |
| BTC | oi_roc_5d | 3d | -0.0115 | -0.46 | -0.1036 | -2.18 | YES |
| BTC | oi_roc_5d | 7d | -0.0098 | -0.39 | -0.0843 | -1.76 | YES |
| BTC | oi_zscore_20d | 14d | 0.0229 | 0.91 | -0.0336 | -0.69 | NO |
| BTC | oi_zscore_20d | 1d | -0.0038 | -0.15 | -0.0692 | -1.45 | YES |
| BTC | oi_zscore_20d | 3d | 0.0195 | 0.77 | -0.1159 | -2.44 | NO |
| BTC | oi_zscore_20d | 7d | 0.0088 | 0.35 | -0.0848 | -1.77 | NO |
| BTC | taker_dispersion | 14d | -0.0810 | -2.52 | 0.0569 | 1.18 | NO |
| BTC | taker_dispersion | 1d | -0.0020 | -0.06 | 0.0296 | 0.62 | NO |
| BTC | taker_dispersion | 3d | 0.0199 | 0.62 | 0.0549 | 1.15 | YES |
| BTC | taker_dispersion | 7d | -0.0364 | -1.13 | 0.0691 | 1.44 | NO |
| BTC | taker_ratio_range | 14d | 0.0904 | 3.46 | 0.0310 | 0.64 | YES |
| BTC | taker_ratio_range | 1d | 0.0145 | 0.55 | 0.0352 | 0.74 | YES |
| BTC | taker_ratio_range | 3d | 0.0240 | 0.92 | 0.0566 | 1.18 | YES |
| BTC | taker_ratio_range | 7d | 0.0544 | 2.08 | 0.0363 | 0.75 | YES |
| BTC | taker_ratio_raw | 14d | 0.0865 | 3.31 | 0.0955 | 1.98 | YES |
| BTC | taker_ratio_raw | 1d | 0.0145 | 0.55 | 0.0452 | 0.95 | YES |
| BTC | taker_ratio_raw | 3d | 0.0240 | 0.91 | 0.0868 | 1.82 | YES |
| BTC | taker_ratio_raw | 7d | 0.0235 | 0.90 | 0.1081 | 2.26 | YES |
| BTC | taker_ratio_zscore | 14d | -0.0503 | -1.89 | 0.0452 | 0.93 | NO |
| BTC | taker_ratio_zscore | 1d | -0.0114 | -0.43 | 0.0043 | 0.09 | NO |
| BTC | taker_ratio_zscore | 3d | -0.0228 | -0.86 | 0.0546 | 1.14 | NO |
| BTC | taker_ratio_zscore | 7d | -0.0696 | -2.62 | 0.0579 | 1.21 | NO |
| BTC | toptrader_ls_dispersion | 14d | 0.1078 | 2.97 | 0.0073 | 0.15 | YES |
| BTC | toptrader_ls_dispersion | 1d | -0.0176 | -0.48 | -0.0121 | -0.25 | YES |
| BTC | toptrader_ls_dispersion | 3d | 0.0167 | 0.46 | -0.0397 | -0.83 | NO |
| BTC | toptrader_ls_dispersion | 7d | 0.0508 | 1.39 | -0.0459 | -0.95 | NO |
| BTC | toptrader_ls_raw | 14d | -0.0994 | -3.55 | -0.2042 | -4.30 | YES |
| BTC | toptrader_ls_raw | 1d | -0.0429 | -1.53 | -0.0314 | -0.66 | YES |
| BTC | toptrader_ls_raw | 3d | -0.0479 | -1.71 | -0.1193 | -2.51 | YES |
| BTC | toptrader_ls_raw | 7d | -0.0721 | -2.57 | -0.1683 | -3.55 | YES |
| BTC | toptrader_ls_roc_5d | 14d | 0.0192 | 0.68 | -0.0778 | -1.61 | NO |
| BTC | toptrader_ls_roc_5d | 1d | -0.0062 | -0.22 | 0.0076 | 0.16 | NO |
| BTC | toptrader_ls_roc_5d | 3d | 0.0240 | 0.85 | -0.0938 | -1.97 | NO |
| BTC | toptrader_ls_roc_5d | 7d | -0.0164 | -0.58 | -0.0996 | -2.08 | YES |
| BTC | toptrader_ls_zscore | 14d | -0.0442 | -1.54 | -0.0563 | -1.16 | YES |
| BTC | toptrader_ls_zscore | 1d | -0.0062 | -0.22 | 0.0071 | 0.15 | NO |
| BTC | toptrader_ls_zscore | 3d | 0.0061 | 0.21 | -0.0497 | -1.04 | NO |
| BTC | toptrader_ls_zscore | 7d | -0.0278 | -0.96 | -0.0592 | -1.23 | YES |
| ETH | ls_divergence_count | 14d | 0.0277 | 0.79 | -0.1659 | -3.47 | NO |
| ETH | ls_divergence_count | 1d | 0.0195 | 0.55 | -0.0224 | -0.47 | NO |
| ETH | ls_divergence_count | 3d | 0.0431 | 1.23 | -0.0691 | -1.45 | NO |
| ETH | ls_divergence_count | 7d | 0.0126 | 0.36 | -0.1579 | -3.32 | NO |
| ETH | ls_divergence_ratio | 14d | 0.0610 | 1.74 | 0.0775 | 1.60 | YES |
| ETH | ls_divergence_ratio | 1d | 0.0316 | 0.90 | -0.0089 | -0.19 | NO |
| ETH | ls_divergence_ratio | 3d | 0.0645 | 1.84 | -0.0233 | -0.49 | NO |
| ETH | ls_divergence_ratio | 7d | 0.0419 | 1.19 | 0.0003 | 0.01 | YES |
| ETH | ls_divergence_sum | 14d | 0.1085 | 3.11 | 0.1898 | 3.98 | YES |
| ETH | ls_divergence_sum | 1d | 0.0144 | 0.41 | 0.0164 | 0.34 | YES |
| ETH | ls_divergence_sum | 3d | 0.0221 | 0.63 | 0.0087 | 0.18 | YES |
| ETH | ls_divergence_sum | 7d | 0.0444 | 1.27 | 0.1085 | 2.27 | YES |
| ETH | ls_divergence_zscore | 14d | 0.0644 | 1.77 | -0.1702 | -3.56 | NO |
| ETH | ls_divergence_zscore | 1d | 0.0534 | 1.46 | 0.0274 | 0.57 | YES |
| ETH | ls_divergence_zscore | 3d | 0.0977 | 2.69 | 0.0167 | 0.35 | YES |
| ETH | ls_divergence_zscore | 7d | 0.0455 | 1.25 | -0.0995 | -2.08 | NO |
| ETH | ls_ratio_range | 14d | -0.0566 | -1.89 | -0.1775 | -3.72 | YES |
| ETH | ls_ratio_range | 1d | -0.0083 | -0.28 | -0.0436 | -0.91 | YES |
| ETH | ls_ratio_range | 3d | -0.0045 | -0.15 | -0.0926 | -1.94 | YES |
| ETH | ls_ratio_range | 7d | -0.0217 | -0.72 | -0.1269 | -2.66 | YES |
| ETH | oi_dispersion | 14d | 0.0333 | 1.11 | -0.0701 | -1.45 | NO |
| ETH | oi_dispersion | 1d | 0.0269 | 0.90 | -0.0713 | -1.50 | NO |
| ETH | oi_dispersion | 3d | -0.0085 | -0.28 | -0.1307 | -2.75 | YES |
| ETH | oi_dispersion | 7d | 0.0077 | 0.26 | -0.0971 | -2.03 | NO |
| ETH | oi_price_div_10d | 14d | 0.0035 | 0.12 | 0.0264 | 0.54 | YES |
| ETH | oi_price_div_10d | 1d | -0.0212 | -0.71 | -0.0385 | -0.81 | YES |
| ETH | oi_price_div_10d | 3d | -0.0162 | -0.54 | -0.0413 | -0.86 | YES |
| ETH | oi_price_div_10d | 7d | 0.0103 | 0.34 | 0.0159 | 0.33 | YES |
| ETH | oi_price_div_20d | 14d | 0.0367 | 1.22 | 0.1081 | 2.24 | YES |
| ETH | oi_price_div_20d | 1d | -0.0163 | -0.54 | -0.0044 | -0.09 | YES |
| ETH | oi_price_div_20d | 3d | -0.0368 | -1.22 | 0.0290 | 0.60 | NO |
| ETH | oi_price_div_20d | 7d | -0.0438 | -1.46 | 0.0972 | 2.03 | NO |
| ETH | oi_price_div_5d | 14d | 0.0258 | 0.86 | 0.0188 | 0.39 | YES |
| ETH | oi_price_div_5d | 1d | -0.0272 | -0.91 | -0.0638 | -1.34 | YES |
| ETH | oi_price_div_5d | 3d | -0.0193 | -0.65 | -0.0362 | -0.76 | YES |
| ETH | oi_price_div_5d | 7d | -0.0112 | -0.37 | -0.0225 | -0.47 | YES |
| ETH | oi_roc_10d | 14d | -0.0002 | -0.01 | 0.0293 | 0.60 | NO |
| ETH | oi_roc_10d | 1d | -0.0211 | -0.71 | -0.0232 | -0.49 | YES |
| ETH | oi_roc_10d | 3d | -0.0042 | -0.14 | -0.0285 | -0.60 | YES |
| ETH | oi_roc_10d | 7d | 0.0214 | 0.72 | 0.0137 | 0.28 | YES |
| ETH | oi_roc_20d | 14d | 0.0207 | 0.69 | 0.1559 | 3.25 | YES |
| ETH | oi_roc_20d | 1d | -0.0230 | -0.76 | -0.0010 | -0.02 | YES |
| ETH | oi_roc_20d | 3d | -0.0337 | -1.12 | 0.0333 | 0.70 | NO |
| ETH | oi_roc_20d | 7d | -0.0365 | -1.21 | 0.1018 | 2.13 | NO |
| ETH | oi_roc_5d | 14d | 0.0284 | 0.95 | 0.0103 | 0.21 | YES |
| ETH | oi_roc_5d | 1d | -0.0234 | -0.78 | -0.0583 | -1.22 | YES |
| ETH | oi_roc_5d | 3d | -0.0069 | -0.23 | -0.0325 | -0.68 | YES |
| ETH | oi_roc_5d | 7d | -0.0054 | -0.18 | -0.0164 | -0.34 | YES |
| ETH | oi_zscore_20d | 14d | 0.0305 | 1.01 | 0.0753 | 1.56 | YES |
| ETH | oi_zscore_20d | 1d | -0.0218 | -0.73 | -0.0319 | -0.67 | YES |
| ETH | oi_zscore_20d | 3d | 0.0016 | 0.05 | -0.0282 | -0.59 | NO |
| ETH | oi_zscore_20d | 7d | -0.0099 | -0.33 | 0.0429 | 0.89 | NO |
| ETH | taker_dispersion | 14d | -0.0547 | -1.70 | 0.0230 | 0.48 | NO |
| ETH | taker_dispersion | 1d | 0.0016 | 0.05 | 0.0079 | 0.16 | YES |
| ETH | taker_dispersion | 3d | 0.0341 | 1.06 | 0.0479 | 1.00 | YES |
| ETH | taker_dispersion | 7d | -0.0132 | -0.41 | 0.0701 | 1.46 | NO |
| ETH | taker_ratio_range | 14d | -0.0223 | -0.70 | -0.1048 | -2.17 | YES |
| ETH | taker_ratio_range | 1d | -0.0472 | -1.49 | 0.0116 | 0.24 | NO |
| ETH | taker_ratio_range | 3d | -0.0427 | -1.35 | 0.0020 | 0.04 | NO |
| ETH | taker_ratio_range | 7d | -0.0288 | -0.91 | -0.0573 | -1.19 | YES |
| ETH | taker_ratio_raw | 14d | -0.0163 | -0.51 | -0.0551 | -1.14 | YES |
| ETH | taker_ratio_raw | 1d | -0.0444 | -1.40 | -0.0032 | -0.07 | YES |
| ETH | taker_ratio_raw | 3d | -0.0391 | -1.24 | 0.0206 | 0.43 | NO |
| ETH | taker_ratio_raw | 7d | -0.0479 | -1.51 | -0.0136 | -0.28 | YES |
| ETH | taker_ratio_zscore | 14d | -0.0392 | -1.21 | -0.0389 | -0.80 | YES |
| ETH | taker_ratio_zscore | 1d | -0.0545 | -1.69 | 0.0175 | 0.37 | NO |
| ETH | taker_ratio_zscore | 3d | -0.0184 | -0.57 | 0.0403 | 0.84 | NO |
| ETH | taker_ratio_zscore | 7d | -0.0511 | -1.58 | 0.0127 | 0.26 | NO |
| ETH | toptrader_ls_dispersion | 14d | 0.1155 | 3.18 | -0.0633 | -1.31 | NO |
| ETH | toptrader_ls_dispersion | 1d | 0.0095 | 0.26 | -0.0439 | -0.92 | NO |
| ETH | toptrader_ls_dispersion | 3d | 0.0040 | 0.11 | -0.0659 | -1.38 | NO |
| ETH | toptrader_ls_dispersion | 7d | 0.0245 | 0.67 | -0.0939 | -1.96 | NO |
| ETH | toptrader_ls_raw | 14d | 0.0356 | 1.01 | -0.1081 | -2.24 | NO |
| ETH | toptrader_ls_raw | 1d | -0.0166 | -0.47 | -0.0063 | -0.13 | YES |
| ETH | toptrader_ls_raw | 3d | 0.0049 | 0.14 | -0.0444 | -0.93 | NO |
| ETH | toptrader_ls_raw | 7d | 0.0184 | 0.52 | -0.0863 | -1.80 | NO |
| ETH | toptrader_ls_roc_5d | 14d | -0.1438 | -4.09 | -0.0196 | -0.41 | YES |
| ETH | toptrader_ls_roc_5d | 1d | -0.0752 | -2.12 | 0.0192 | 0.40 | NO |
| ETH | toptrader_ls_roc_5d | 3d | -0.1552 | -4.42 | 0.0374 | 0.78 | NO |
| ETH | toptrader_ls_roc_5d | 7d | -0.2160 | -6.23 | 0.0570 | 1.19 | NO |
| ETH | toptrader_ls_zscore | 14d | -0.0822 | -2.26 | -0.0172 | -0.35 | YES |
| ETH | toptrader_ls_zscore | 1d | -0.0686 | -1.88 | 0.0455 | 0.95 | NO |
| ETH | toptrader_ls_zscore | 3d | -0.1286 | -3.55 | 0.0546 | 1.14 | NO |
| ETH | toptrader_ls_zscore | 7d | -0.1697 | -4.71 | 0.0182 | 0.38 | NO |
| PANEL | ls_divergence_count | 14d | 0.0300 | 4.27 | -0.1533 | -16.65 | NO |
| PANEL | ls_divergence_count | 1d | 0.0136 | 1.93 | -0.0438 | -4.78 | NO |
| PANEL | ls_divergence_count | 3d | 0.0294 | 4.18 | -0.0790 | -8.61 | NO |
| PANEL | ls_divergence_count | 7d | 0.0130 | 1.85 | -0.1356 | -14.81 | NO |
| PANEL | ls_divergence_ratio | 14d | 0.0092 | 1.31 | -0.1234 | -13.36 | NO |
| PANEL | ls_divergence_ratio | 1d | 0.0080 | 1.14 | -0.0455 | -4.96 | NO |
| PANEL | ls_divergence_ratio | 3d | 0.0160 | 2.28 | -0.0703 | -7.66 | NO |
| PANEL | ls_divergence_ratio | 7d | -0.0077 | -1.10 | -0.1125 | -12.25 | YES |
| PANEL | ls_divergence_sum | 14d | 0.0903 | 12.88 | -0.0058 | -0.62 | NO |
| PANEL | ls_divergence_sum | 1d | 0.0231 | 3.29 | -0.0161 | -1.76 | NO |
| PANEL | ls_divergence_sum | 3d | 0.0468 | 6.65 | -0.0220 | -2.39 | NO |
| PANEL | ls_divergence_sum | 7d | 0.0569 | 8.10 | -0.0175 | -1.90 | NO |
| PANEL | ls_divergence_zscore | 14d | 0.0176 | 2.42 | -0.0828 | -8.92 | NO |
| PANEL | ls_divergence_zscore | 1d | 0.0140 | 1.93 | -0.0376 | -4.10 | NO |
| PANEL | ls_divergence_zscore | 3d | 0.0248 | 3.41 | -0.0565 | -6.15 | NO |
| PANEL | ls_divergence_zscore | 7d | -0.0070 | -0.97 | -0.0934 | -10.16 | YES |
| PANEL | ls_ratio_range | 14d | -0.1133 | -18.33 | -0.0385 | -4.14 | YES |
| PANEL | ls_ratio_range | 1d | -0.0307 | -4.94 | -0.0219 | -2.39 | YES |
| PANEL | ls_ratio_range | 3d | -0.0466 | -7.50 | -0.0219 | -2.38 | YES |
| PANEL | ls_ratio_range | 7d | -0.0740 | -11.93 | -0.0583 | -6.33 | YES |
| PANEL | oi_dispersion | 14d | 0.0290 | 4.62 | -0.0525 | -5.65 | NO |
| PANEL | oi_dispersion | 1d | 0.0178 | 2.84 | -0.0614 | -6.71 | NO |
| PANEL | oi_dispersion | 3d | 0.0022 | 0.35 | -0.1039 | -11.36 | NO |
| PANEL | oi_dispersion | 7d | 0.0085 | 1.36 | -0.0492 | -5.33 | NO |
| PANEL | oi_price_div_10d | 14d | -0.0146 | -2.35 | -0.0106 | -1.13 | YES |
| PANEL | oi_price_div_10d | 1d | -0.0091 | -1.46 | -0.0492 | -5.36 | YES |
| PANEL | oi_price_div_10d | 3d | 0.0018 | 0.28 | -0.0738 | -8.05 | NO |
| PANEL | oi_price_div_10d | 7d | -0.0039 | -0.62 | -0.0750 | -8.14 | YES |
| PANEL | oi_price_div_20d | 14d | -0.0079 | -1.26 | 0.0267 | 2.86 | NO |
| PANEL | oi_price_div_20d | 1d | -0.0184 | -2.95 | -0.0062 | -0.67 | YES |
| PANEL | oi_price_div_20d | 3d | -0.0236 | -3.78 | -0.0173 | -1.88 | YES |
| PANEL | oi_price_div_20d | 7d | -0.0332 | -5.32 | 0.0079 | 0.86 | NO |
| PANEL | oi_price_div_5d | 14d | -0.0039 | -0.62 | -0.0224 | -2.41 | YES |
| PANEL | oi_price_div_5d | 1d | -0.0158 | -2.55 | -0.0417 | -4.54 | YES |
| PANEL | oi_price_div_5d | 3d | -0.0159 | -2.57 | -0.0346 | -3.77 | YES |
| PANEL | oi_price_div_5d | 7d | -0.0125 | -2.02 | -0.0539 | -5.84 | YES |
| PANEL | oi_roc_10d | 14d | 0.0060 | 0.97 | -0.0195 | -2.09 | NO |
| PANEL | oi_roc_10d | 1d | -0.0104 | -1.68 | -0.0464 | -5.06 | YES |
| PANEL | oi_roc_10d | 3d | 0.0113 | 1.82 | -0.0660 | -7.19 | NO |
| PANEL | oi_roc_10d | 7d | 0.0170 | 2.73 | -0.0732 | -7.95 | NO |
| PANEL | oi_roc_20d | 14d | 0.0048 | 0.77 | 0.0283 | 3.04 | YES |
| PANEL | oi_roc_20d | 1d | -0.0178 | -2.85 | -0.0112 | -1.22 | YES |
| PANEL | oi_roc_20d | 3d | -0.0133 | -2.13 | -0.0166 | -1.81 | YES |
| PANEL | oi_roc_20d | 7d | -0.0158 | -2.52 | -0.0018 | -0.20 | YES |
| PANEL | oi_roc_5d | 14d | 0.0110 | 1.77 | -0.0406 | -4.36 | NO |
| PANEL | oi_roc_5d | 1d | -0.0215 | -3.46 | -0.0381 | -4.15 | YES |
| PANEL | oi_roc_5d | 3d | -0.0081 | -1.31 | -0.0408 | -4.44 | YES |
| PANEL | oi_roc_5d | 7d | -0.0013 | -0.21 | -0.0756 | -8.21 | YES |
| PANEL | oi_zscore_20d | 14d | 0.0075 | 1.20 | -0.0169 | -1.81 | NO |
| PANEL | oi_zscore_20d | 1d | -0.0104 | -1.66 | -0.0465 | -5.08 | YES |
| PANEL | oi_zscore_20d | 3d | 0.0048 | 0.77 | -0.0533 | -5.80 | NO |
| PANEL | oi_zscore_20d | 7d | 0.0022 | 0.35 | -0.0512 | -5.54 | NO |
| PANEL | taker_dispersion | 14d | -0.1101 | -16.72 | 0.0227 | 2.44 | NO |
| PANEL | taker_dispersion | 1d | -0.0361 | -5.45 | -0.0220 | -2.40 | YES |
| PANEL | taker_dispersion | 3d | -0.0336 | -5.07 | 0.0180 | 1.96 | NO |
| PANEL | taker_dispersion | 7d | -0.0903 | -13.69 | 0.0557 | 6.03 | NO |
| PANEL | taker_ratio_range | 14d | -0.0008 | -0.13 | 0.0229 | 2.46 | NO |
| PANEL | taker_ratio_range | 1d | -0.0166 | -2.56 | -0.0086 | -0.93 | YES |
| PANEL | taker_ratio_range | 3d | -0.0221 | -3.42 | 0.0140 | 1.52 | NO |
| PANEL | taker_ratio_range | 7d | -0.0146 | -2.26 | 0.0324 | 3.51 | NO |
| PANEL | taker_ratio_raw | 14d | 0.0134 | 2.07 | 0.0329 | 3.54 | YES |
| PANEL | taker_ratio_raw | 1d | -0.0190 | -2.94 | 0.0080 | 0.87 | NO |
| PANEL | taker_ratio_raw | 3d | -0.0145 | -2.24 | 0.0059 | 0.64 | NO |
| PANEL | taker_ratio_raw | 7d | -0.0125 | -1.93 | 0.0410 | 4.44 | NO |
| PANEL | taker_ratio_zscore | 14d | -0.0900 | -13.72 | 0.0162 | 1.74 | NO |
| PANEL | taker_ratio_zscore | 1d | -0.0457 | -6.94 | 0.0012 | 0.13 | NO |
| PANEL | taker_ratio_zscore | 3d | -0.0547 | -8.32 | -0.0093 | -1.01 | YES |
| PANEL | taker_ratio_zscore | 7d | -0.0791 | -12.05 | 0.0284 | 3.08 | NO |
| PANEL | toptrader_ls_dispersion | 14d | 0.0438 | 5.98 | -0.0461 | -4.95 | NO |
| PANEL | toptrader_ls_dispersion | 1d | -0.0006 | -0.08 | -0.0190 | -2.07 | YES |
| PANEL | toptrader_ls_dispersion | 3d | -0.0123 | -1.68 | -0.0428 | -4.66 | YES |
| PANEL | toptrader_ls_dispersion | 7d | -0.0226 | -3.08 | -0.0785 | -8.52 | YES |
| PANEL | toptrader_ls_raw | 14d | -0.0610 | -8.69 | -0.1069 | -11.54 | YES |
| PANEL | toptrader_ls_raw | 1d | -0.0174 | -2.48 | -0.0202 | -2.21 | YES |
| PANEL | toptrader_ls_raw | 3d | -0.0184 | -2.61 | -0.0466 | -5.07 | YES |
| PANEL | toptrader_ls_raw | 7d | -0.0365 | -5.19 | -0.0890 | -9.68 | YES |
| PANEL | toptrader_ls_roc_5d | 14d | -0.0231 | -3.25 | -0.0219 | -2.35 | YES |
| PANEL | toptrader_ls_roc_5d | 1d | -0.0100 | -1.41 | 0.0018 | 0.19 | NO |
| PANEL | toptrader_ls_roc_5d | 3d | -0.0231 | -3.26 | 0.0222 | 2.42 | NO |
| PANEL | toptrader_ls_roc_5d | 7d | -0.0335 | -4.72 | -0.0305 | -3.31 | YES |
| PANEL | toptrader_ls_zscore | 14d | -0.0150 | -2.07 | -0.0371 | -3.98 | YES |
| PANEL | toptrader_ls_zscore | 1d | -0.0090 | -1.24 | -0.0137 | -1.49 | YES |
| PANEL | toptrader_ls_zscore | 3d | -0.0153 | -2.10 | -0.0166 | -1.81 | YES |
| PANEL | toptrader_ls_zscore | 7d | -0.0361 | -4.96 | -0.0438 | -4.75 | YES |

## Verdict

**34 signal-scope-horizon combination(s) PASS all criteria:**

| Scope | Signal | Horizon | Full IC | Full t-stat | IS IC | OOS IC | Hit Rate |
|-------|--------|---------|--------:|------------:|------:|-------:|---------:|
| BTC | ls_divergence_count | 14d | -0.2040 | -8.57 | -0.1187 | -0.2303 | 48.2% |
| BTC | ls_divergence_ratio | 14d | -0.1771 | -7.41 | -0.1251 | -0.0146 | 56.0% |
| BTC | toptrader_ls_raw | 14d | -0.1656 | -6.91 | -0.0994 | -0.2042 | 56.0% |
| BTC | ls_ratio_range | 14d | -0.1637 | -7.40 | -0.1909 | -0.1147 | 54.7% |
| BTC | ls_divergence_count | 7d | -0.1570 | -6.56 | -0.0802 | -0.2056 | 48.5% |
| BTC | ls_divergence_ratio | 7d | -0.1508 | -6.29 | -0.0944 | -0.1057 | 55.1% |
| BTC | toptrader_ls_raw | 7d | -0.1373 | -5.72 | -0.0721 | -0.1683 | 55.1% |
| PANEL | toptrader_ls_raw | 14d | -0.1342 | -24.13 | -0.0610 | -0.1069 | 46.2% |
| BTC | toptrader_ls_dispersion | 14d | 0.1061 | 3.66 | 0.1078 | 0.0073 | 55.0% |
| ETH | ls_ratio_range | 14d | -0.1034 | -4.07 | -0.0566 | -0.1775 | 46.8% |
| PANEL | ls_divergence_ratio | 7d | -0.1016 | -18.25 | -0.0077 | -0.1125 | 47.5% |
| PANEL | toptrader_ls_raw | 7d | -0.0957 | -17.17 | -0.0365 | -0.0890 | 47.5% |
| BTC | ls_ratio_range | 7d | -0.0939 | -4.21 | -0.1140 | -0.0529 | 53.6% |
| BTC | toptrader_ls_raw | 3d | -0.0937 | -3.88 | -0.0479 | -0.1193 | 54.2% |
| ETH | toptrader_ls_roc_5d | 14d | -0.0864 | -3.03 | -0.1438 | -0.0196 | 46.3% |
| ETH | ls_divergence_sum | 14d | 0.0772 | 2.72 | 0.1085 | 0.1898 | 52.3% |
| BTC | ls_divergence_ratio | 3d | -0.0765 | -3.17 | -0.0294 | -0.0880 | 54.2% |
| BTC | ls_divergence_count | 3d | -0.0749 | -3.10 | -0.0166 | -0.1159 | 50.1% |
| BTC | ls_divergence_sum | 14d | 0.0728 | 3.00 | 0.1500 | 0.0962 | 51.4% |
| BTC | ls_ratio_range | 3d | -0.0688 | -3.09 | -0.0854 | -0.0284 | 53.4% |
| ETH | oi_roc_20d | 14d | 0.0677 | 2.65 | 0.0207 | 0.1559 | 52.9% |
| ETH | ls_divergence_zscore | 3d | 0.0675 | 2.33 | 0.0977 | 0.0167 | 50.9% |
| ETH | oi_price_div_20d | 14d | 0.0655 | 2.57 | 0.0367 | 0.1081 | 53.0% |
| BTC | toptrader_ls_raw | 1d | -0.0609 | -2.52 | -0.0429 | -0.0314 | 51.6% |
| BTC | taker_ratio_raw | 14d | 0.0609 | 2.65 | 0.0865 | 0.0955 | 55.6% |
| ETH | ls_ratio_range | 7d | -0.0600 | -2.36 | -0.0217 | -0.1269 | 49.4% |
| PANEL | ls_ratio_range | 14d | -0.0574 | -11.11 | -0.1133 | -0.0385 | 45.6% |
| PANEL | toptrader_ls_raw | 3d | -0.0531 | -9.51 | -0.0184 | -0.0466 | 48.8% |
| BTC | oi_price_div_20d | 3d | -0.0470 | -2.10 | -0.0572 | -0.0224 | 47.2% |
| PANEL | ls_ratio_range | 7d | -0.0452 | -8.77 | -0.0740 | -0.0583 | 47.0% |
| ETH | toptrader_ls_zscore | 14d | -0.0435 | -1.49 | -0.0822 | -0.0172 | 48.8% |
| PANEL | taker_ratio_zscore | 3d | -0.0392 | -7.33 | -0.0547 | -0.0093 | 48.8% |
| BTC | taker_ratio_range | 14d | 0.0361 | 1.57 | 0.0904 | 0.0310 | 55.6% |
| BTC | taker_ratio_range | 7d | 0.0187 | 0.81 | 0.0544 | 0.0363 | 54.3% |

### Near-Miss Signals (|IC| > 0.03, |t-stat| > 1.5)

41 near-miss combinations found.

| Scope | Signal | Horizon | IC | t-stat | Sign Consistent |
|-------|--------|---------|---:|-------:|:---------------:|
| PANEL | ls_divergence_ratio | 14d | -0.1215 | -21.81 | NO |
| PANEL | ls_divergence_count | 14d | -0.1191 | -21.36 | NO |
| ETH | toptrader_ls_roc_5d | 7d | -0.1017 | -3.58 | NO |
| PANEL | ls_divergence_count | 7d | -0.0957 | -17.18 | NO |
| ETH | toptrader_ls_zscore | 7d | -0.0905 | -3.13 | NO |
| ETH | toptrader_ls_raw | 14d | -0.0828 | -2.92 | NO |
| PANEL | taker_dispersion | 14d | -0.0766 | -14.23 | NO |
| ETH | toptrader_ls_dispersion | 14d | 0.0764 | 2.63 | NO |
| ETH | toptrader_ls_roc_5d | 3d | -0.0722 | -2.54 | NO |
| ETH | ls_divergence_count | 14d | -0.0700 | -2.47 | NO |
| ETH | toptrader_ls_raw | 7d | -0.0631 | -2.23 | NO |
| ETH | ls_divergence_count | 7d | -0.0554 | -1.96 | NO |
| PANEL | taker_ratio_zscore | 14d | -0.0525 | -9.77 | NO |
| ETH | toptrader_ls_zscore | 3d | -0.0522 | -1.80 | NO |
| PANEL | ls_divergence_ratio | 3d | -0.0501 | -8.98 | NO |
| BTC | taker_dispersion | 14d | -0.0498 | -1.86 | NO |
| PANEL | taker_dispersion | 7d | -0.0497 | -9.23 | NO |
| ETH | oi_zscore_20d | 14d | 0.0487 | 1.91 | YES |
| BTC | ls_divergence_count | 1d | -0.0469 | -1.94 | YES |
| PANEL | toptrader_ls_dispersion | 14d | 0.0467 | 8.12 | NO |

## Summary by Signal Family

### OI Signals
- Mean IC: -0.0033, Mean |IC|: 0.0157
- % statistically significant: 2%
- % sign consistent IS->OOS: 60%
- Passing combinations: 1

### Top Trader L/S
- Mean IC: -0.0479, Mean |IC|: 0.0479
- % statistically significant: 36%
- % sign consistent IS->OOS: 56%
- Passing combinations: 9

### L/S Divergence
- Mean IC: -0.0298, Mean |IC|: 0.0475
- % statistically significant: 31%
- % sign consistent IS->OOS: 42%
- Passing combinations: 10

### OI-Price Divergence
- Mean IC: -0.0158, Mean |IC|: 0.0232
- % statistically significant: 3%
- % sign consistent IS->OOS: 78%
- Passing combinations: 2

### Taker Ratio
- Mean IC: -0.0139, Mean |IC|: 0.0251
- % statistically significant: 6%
- % sign consistent IS->OOS: 47%
- Passing combinations: 4

### Cross-Token Dispersion
- Mean IC: -0.0005, Mean |IC|: 0.0244
- % statistically significant: 8%
- % sign consistent IS->OOS: 31%
- Passing combinations: 1

## Conclusions

Binance Futures metrics show predictive signal in 6 signal family/families:
- **L/S Divergence**: 10 passing combination(s)
- **Cross-Token Dispersion**: 1 passing combination(s)
- **Taker Ratio**: 4 passing combination(s)
- **OI-Price Divergence**: 2 passing combination(s)
- **OI Signals**: 1 passing combination(s)
- **Top Trader L/S**: 9 passing combination(s)

Recommended next steps:
1. Integrate passing signals into the multi-factor model
2. Test interaction effects between passing signals
3. Evaluate portfolio-level performance with position sizing
4. Test robustness across different market regimes (trending vs ranging)