# On-Chain + Mempool Signal Discovery Results

## 1. Data Sources

### 1.1 Blockchain Info (blockchain_info_btc_1year.json)

| Metric Key | Description | Obs | Date Range |
|---|---|---|---|
| `estimated-transaction-volume-usd` | Estimated USD Transaction Value (USD) | 356 | 2025-03-25 to 2026-03-16 |
| `n-transactions` | Confirmed Transactions Per Day (Transactions) | 354 | 2025-03-25 to 2026-03-16 |
| `market-cap` | Market Capitalization (USD) | 358 | 2025-03-24 to 2026-03-16 |
| `total-bitcoins` | Bitcoins in circulation (BTC) | 359 | 2025-03-24 to 2026-03-17 |
| `n-unique-addresses` | Number Of Unique Addresses Used (Unique Addresses) | 354 | 2025-03-25 to 2026-03-16 |
| `n-transactions-per-block` | Average Number Of Transactions Per Block (Transactions Per Block) | 354 | 2025-03-25 to 2026-03-16 |
| `estimated-transaction-volume` | Estimated Transaction Value (BTC) | 356 | 2025-03-25 to 2026-03-16 |
| `output-volume` | Output Value (BTC) | 355 | 2025-03-25 to 2026-03-16 |
| `trade-volume` | USD Exchange Trade Volume (Trade Volume (USD)) | 358 | 2025-03-25 to 2026-03-17 |
| `nvt_ratio` | NVT Ratio (MarketCap / Est. TX Volume USD) | 356 | 2025-03-25 to 2026-03-16 |
| `active_addr_7d_chg` | Active Address 7d % Change | 354 | 2025-03-25 to 2026-03-16 |
| `tx_count_7d_chg` | TX Count 7d % Change | 354 | 2025-03-25 to 2026-03-16 |
| `value_per_tx_usd` | Avg TX Value in USD | 353 | 2025-03-25 to 2026-03-16 |
| `trade_vol_zscore` | Exchange Trade Volume Z-Score (30d) | 358 | 2025-03-25 to 2026-03-17 |

**Raw metrics:** estimated-transaction-volume-usd, n-transactions, market-cap, total-bitcoins, n-unique-addresses, n-transactions-per-block, estimated-transaction-volume, output-volume, trade-volume

**Derived signals:** nvt_ratio, active_addr_7d_chg, tx_count_7d_chg, value_per_tx_usd, trade_vol_zscore

### 1.2 Mempool Space (mempool_space_btc_data.json)

| Metric Key | Description | Obs | Date Range |
|---|---|---|---|
| `mp_hashrate` | Mempool Space Avg Hashrate (H/s) | 365 | 2025-03-25 to 2026-03-24 |
| `mp_difficulty` | Mining Difficulty (forward-filled) | 350 | 2025-04-05 to 2026-03-20 |
| `mp_diff_adjustment` | Difficulty Adjustment Multiplier (forward-filled) | 350 | 2025-04-05 to 2026-03-20 |
| `hashrate_7d_chg` | Hashrate 7d % Change | 365 | 2025-03-25 to 2026-03-24 |
| `hashrate_30d_chg` | Hashrate 30d % Change | 365 | 2025-03-25 to 2026-03-24 |

**Note:** `mempool_fees` contains only a snapshot of the most recent 8 blocks -- not a time series. Too few observations for IC analysis.

**Derived signals:** hashrate_7d_chg, hashrate_30d_chg

### 1.3 BTC Price Data

- Source: `data/spot/1h_cache/BTC_1h.parquet`, resampled to daily close
- Date range: 2020-01-01 to 2026-03-14
- Forward return horizons: 1d, 3d, 7d, 14d
- IS/OOS split: 2025-09-15

## 2. IC Results (Full Table)

| Metric | Horizon | IC_Full | t_Full | IC_IS | t_IS | n_IS | IC_OOS | t_OOS | n_OOS | Verdict | Reason |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `estimated-transaction-volume-usd` | 1d | -0.0790 | -1.47 | -0.1538 | -2.04 | 173 | -0.0108 | -0.14 | 173 | **KILL** | |IC_OOS|=0.0108 < 0.02; |t_OOS|=0.14 < 2.0 |
| `estimated-transaction-volume-usd` | 3d | -0.0773 | -1.43 | -0.1936 | -2.58 | 173 | +0.0016 | +0.02 | 171 | **KILL** | |IC_OOS|=0.0016 < 0.02; |t_OOS|=0.02 < 2.0 |
| `estimated-transaction-volume-usd` | 7d | -0.1013 | -1.87 | -0.1978 | -2.64 | 173 | -0.0107 | -0.14 | 167 | **KILL** | |IC_OOS|=0.0107 < 0.02; |t_OOS|=0.14 < 2.0 |
| `estimated-transaction-volume-usd` | 14d | -0.1550 | -2.85 | -0.2259 | -3.03 | 173 | -0.0136 | -0.17 | 160 | **KILL** | |IC_OOS|=0.0136 < 0.02; |t_OOS|=0.17 < 2.0 |
| `n-transactions` | 1d | +0.0124 | +0.23 | -0.0581 | -0.76 | 174 | +0.1245 | +1.63 | 170 | **KILL** | |t_IS|=0.76 < 2.0; |t_OOS|=1.63 < 2.0; Sign flip IS(-0.0581) -> OOS(+0.1245) |
| `n-transactions` | 3d | +0.0027 | +0.05 | -0.0423 | -0.56 | 174 | +0.1219 | +1.58 | 168 | **KILL** | |t_IS|=0.56 < 2.0; |t_OOS|=1.58 < 2.0; Sign flip IS(-0.0423) -> OOS(+0.1219) |
| `n-transactions` | 7d | -0.0383 | -0.70 | -0.0989 | -1.30 | 174 | +0.1490 | +1.92 | 164 | **KILL** | |t_IS|=1.30 < 2.0; |t_OOS|=1.92 < 2.0; Sign flip IS(-0.0989) -> OOS(+0.1490) |
| `n-transactions` | 14d | -0.0748 | -1.36 | -0.2079 | -2.79 | 174 | +0.2766 | +3.58 | 157 | **KILL** | Sign flip IS(-0.2079) -> OOS(+0.2766) |
| `market-cap` | 1d | -0.0134 | -0.25 | -0.1070 | -1.42 | 175 | -0.0464 | -0.61 | 173 | **KILL** | |t_IS|=1.42 < 2.0; |t_OOS|=0.61 < 2.0 |
| `market-cap` | 3d | -0.0742 | -1.38 | -0.1951 | -2.62 | 175 | -0.1508 | -1.98 | 171 | **KILL** | |t_OOS|=1.98 < 2.0 |
| `market-cap` | 7d | -0.1413 | -2.63 | -0.3525 | -4.95 | 175 | -0.2300 | -3.04 | 167 | **PASS** | IC_IS=-0.3525, IC_OOS=-0.2300 |
| `market-cap` | 14d | -0.1859 | -3.45 | -0.5628 | -8.96 | 175 | -0.2064 | -2.65 | 160 | **PASS** | IC_IS=-0.5628, IC_OOS=-0.2064 |
| `total-bitcoins` | 1d | -0.0975 | -1.82 | -0.0194 | -0.25 | 175 | -0.0567 | -0.74 | 173 | **KILL** | |IC_IS|=0.0194 < 0.02; |t_IS|=0.25 < 2.0; |t_OOS|=0.74 < 2.0 |
| `total-bitcoins` | 3d | -0.1419 | -2.66 | -0.0432 | -0.57 | 175 | -0.0050 | -0.07 | 171 | **KILL** | |t_IS|=0.57 < 2.0; |IC_OOS|=0.0050 < 0.02; |t_OOS|=0.07 < 2.0 |
| `total-bitcoins` | 7d | -0.2612 | -4.99 | -0.1481 | -1.97 | 175 | +0.0170 | +0.22 | 167 | **KILL** | |t_IS|=1.97 < 2.0; |IC_OOS|=0.0170 < 0.02; |t_OOS|=0.22 < 2.0; Sign flip IS(-0.1481) -> OOS(+0.0170) |
| `total-bitcoins` | 14d | -0.4333 | -8.77 | -0.3677 | -5.20 | 175 | -0.1034 | -1.31 | 160 | **KILL** | |t_OOS|=1.31 < 2.0 |
| `n-unique-addresses` | 1d | -0.0195 | -0.36 | -0.0653 | -0.86 | 174 | -0.0342 | -0.44 | 170 | **KILL** | |t_IS|=0.86 < 2.0; |t_OOS|=0.44 < 2.0 |
| `n-unique-addresses` | 3d | +0.0034 | +0.06 | -0.1188 | -1.57 | 174 | +0.0212 | +0.27 | 168 | **KILL** | |t_IS|=1.57 < 2.0; |t_OOS|=0.27 < 2.0; Sign flip IS(-0.1188) -> OOS(+0.0212) |
| `n-unique-addresses` | 7d | +0.0536 | +0.98 | -0.0518 | -0.68 | 174 | +0.0166 | +0.21 | 164 | **KILL** | |t_IS|=0.68 < 2.0; |IC_OOS|=0.0166 < 0.02; |t_OOS|=0.21 < 2.0; Sign flip IS(-0.0518) -> OOS(+0.0166) |
| `n-unique-addresses` | 14d | +0.0338 | +0.61 | -0.0857 | -1.13 | 174 | -0.0150 | -0.19 | 157 | **KILL** | |t_IS|=1.13 < 2.0; |IC_OOS|=0.0150 < 0.02; |t_OOS|=0.19 < 2.0 |
| `n-transactions-per-block` | 1d | +0.0381 | +0.71 | -0.0040 | -0.05 | 174 | +0.1187 | +1.55 | 170 | **KILL** | |IC_IS|=0.0040 < 0.02; |t_IS|=0.05 < 2.0; |t_OOS|=1.55 < 2.0; Sign flip IS(-0.0040) -> OOS(+0.1187) |
| `n-transactions-per-block` | 3d | -0.0244 | -0.45 | -0.0188 | -0.25 | 174 | +0.0149 | +0.19 | 168 | **KILL** | |IC_IS|=0.0188 < 0.02; |t_IS|=0.25 < 2.0; |IC_OOS|=0.0149 < 0.02; |t_OOS|=0.19 < 2.0; Sign flip IS(-0.0188) -> OOS(+0.0149) |
| `n-transactions-per-block` | 7d | -0.0894 | -1.65 | -0.0823 | -1.08 | 174 | +0.0243 | +0.31 | 164 | **KILL** | |t_IS|=1.08 < 2.0; |t_OOS|=0.31 < 2.0; Sign flip IS(-0.0823) -> OOS(+0.0243) |
| `n-transactions-per-block` | 14d | -0.1490 | -2.73 | -0.1991 | -2.66 | 174 | +0.1337 | +1.68 | 157 | **KILL** | |t_OOS|=1.68 < 2.0; Sign flip IS(-0.1991) -> OOS(+0.1337) |
| `estimated-transaction-volume` | 1d | -0.0669 | -1.24 | -0.1138 | -1.50 | 173 | +0.0053 | +0.07 | 173 | **KILL** | |t_IS|=1.50 < 2.0; |IC_OOS|=0.0053 < 0.02; |t_OOS|=0.07 < 2.0; Sign flip IS(-0.1138) -> OOS(+0.0053) |
| `estimated-transaction-volume` | 3d | -0.0578 | -1.07 | -0.1483 | -1.96 | 173 | +0.0298 | +0.39 | 171 | **KILL** | |t_IS|=1.96 < 2.0; |t_OOS|=0.39 < 2.0; Sign flip IS(-0.1483) -> OOS(+0.0298) |
| `estimated-transaction-volume` | 7d | -0.0754 | -1.39 | -0.1337 | -1.76 | 173 | +0.0388 | +0.50 | 167 | **KILL** | |t_IS|=1.76 < 2.0; |t_OOS|=0.50 < 2.0; Sign flip IS(-0.1337) -> OOS(+0.0388) |
| `estimated-transaction-volume` | 14d | -0.1295 | -2.38 | -0.0978 | -1.28 | 173 | +0.0111 | +0.14 | 160 | **KILL** | |t_IS|=1.28 < 2.0; |IC_OOS|=0.0111 < 0.02; |t_OOS|=0.14 < 2.0; Sign flip IS(-0.0978) -> OOS(+0.0111) |
| `output-volume` | 1d | -0.0837 | -1.56 | -0.0458 | -0.60 | 174 | -0.0575 | -0.75 | 171 | **KILL** | |t_IS|=0.60 < 2.0; |t_OOS|=0.75 < 2.0 |
| `output-volume` | 3d | -0.1195 | -2.22 | -0.0806 | -1.06 | 174 | -0.0683 | -0.89 | 169 | **KILL** | |t_IS|=1.06 < 2.0; |t_OOS|=0.89 < 2.0 |
| `output-volume` | 7d | -0.1221 | -2.26 | -0.0087 | -0.11 | 174 | -0.0015 | -0.02 | 165 | **KILL** | |IC_IS|=0.0087 < 0.02; |t_IS|=0.11 < 2.0; |IC_OOS|=0.0015 < 0.02; |t_OOS|=0.02 < 2.0 |
| `output-volume` | 14d | -0.2189 | -4.08 | -0.0653 | -0.86 | 174 | -0.0483 | -0.60 | 158 | **KILL** | |t_IS|=0.86 < 2.0; |t_OOS|=0.60 < 2.0 |
| `trade-volume` | 1d | -0.0116 | -0.22 | -0.0013 | -0.02 | 174 | +0.0409 | +0.54 | 173 | **KILL** | |IC_IS|=0.0013 < 0.02; |t_IS|=0.02 < 2.0; |t_OOS|=0.54 < 2.0; Sign flip IS(-0.0013) -> OOS(+0.0409) |
| `trade-volume` | 3d | -0.1496 | -2.80 | -0.2005 | -2.68 | 174 | -0.0493 | -0.64 | 171 | **KILL** | |t_OOS|=0.64 < 2.0 |
| `trade-volume` | 7d | -0.2061 | -3.88 | -0.2072 | -2.78 | 174 | -0.0661 | -0.85 | 167 | **KILL** | |t_OOS|=0.85 < 2.0 |
| `trade-volume` | 14d | -0.2599 | -4.91 | -0.1801 | -2.40 | 174 | -0.0985 | -1.24 | 160 | **KILL** | |t_OOS|=1.24 < 2.0 |
| `nvt_ratio` | 1d | +0.0662 | +1.23 | +0.1120 | +1.47 | 173 | -0.0046 | -0.06 | 173 | **KILL** | |t_IS|=1.47 < 2.0; |IC_OOS|=0.0046 < 0.02; |t_OOS|=0.06 < 2.0 |
| `nvt_ratio` | 3d | +0.0576 | +1.07 | +0.1489 | +1.97 | 173 | -0.0311 | -0.40 | 171 | **KILL** | |t_IS|=1.97 < 2.0; |t_OOS|=0.40 < 2.0; Sign flip IS(+0.1489) -> OOS(-0.0311) |
| `nvt_ratio` | 7d | +0.0717 | +1.32 | +0.1304 | +1.72 | 173 | -0.0408 | -0.52 | 167 | **KILL** | |t_IS|=1.72 < 2.0; |t_OOS|=0.52 < 2.0; Sign flip IS(+0.1304) -> OOS(-0.0408) |
| `nvt_ratio` | 14d | +0.1259 | +2.31 | +0.0967 | +1.27 | 173 | -0.0120 | -0.15 | 160 | **KILL** | |t_IS|=1.27 < 2.0; |IC_OOS|=0.0120 < 0.02; |t_OOS|=0.15 < 2.0; Sign flip IS(+0.0967) -> OOS(-0.0120) |
| `active_addr_7d_chg` | 1d | -0.0176 | -0.32 | -0.0537 | -0.69 | 167 | -0.0053 | -0.07 | 170 | **KILL** | |t_IS|=0.69 < 2.0; |IC_OOS|=0.0053 < 0.02; |t_OOS|=0.07 < 2.0 |
| `active_addr_7d_chg` | 3d | +0.0137 | +0.25 | -0.0790 | -1.02 | 167 | +0.0658 | +0.85 | 168 | **KILL** | |t_IS|=1.02 < 2.0; |t_OOS|=0.85 < 2.0; Sign flip IS(-0.0790) -> OOS(+0.0658) |
| `active_addr_7d_chg` | 7d | +0.0219 | +0.40 | -0.0898 | -1.16 | 167 | +0.0563 | +0.72 | 164 | **KILL** | |t_IS|=1.16 < 2.0; |t_OOS|=0.72 < 2.0; Sign flip IS(-0.0898) -> OOS(+0.0563) |
| `active_addr_7d_chg` | 14d | +0.0378 | +0.68 | +0.0633 | +0.81 | 167 | -0.0761 | -0.95 | 157 | **KILL** | |t_IS|=0.81 < 2.0; |t_OOS|=0.95 < 2.0; Sign flip IS(+0.0633) -> OOS(-0.0761) |
| `tx_count_7d_chg` | 1d | -0.0118 | -0.22 | -0.0205 | -0.26 | 167 | -0.0163 | -0.21 | 170 | **KILL** | |t_IS|=0.26 < 2.0; |IC_OOS|=0.0163 < 0.02; |t_OOS|=0.21 < 2.0 |
| `tx_count_7d_chg` | 3d | +0.0121 | +0.22 | +0.0770 | +0.99 | 167 | -0.0484 | -0.62 | 168 | **KILL** | |t_IS|=0.99 < 2.0; |t_OOS|=0.62 < 2.0; Sign flip IS(+0.0770) -> OOS(-0.0484) |
| `tx_count_7d_chg` | 7d | -0.0150 | -0.27 | -0.0130 | -0.17 | 167 | -0.0418 | -0.53 | 164 | **KILL** | |IC_IS|=0.0130 < 0.02; |t_IS|=0.17 < 2.0; |t_OOS|=0.53 < 2.0 |
| `tx_count_7d_chg` | 14d | -0.0145 | -0.26 | -0.0575 | -0.74 | 167 | +0.0139 | +0.17 | 157 | **KILL** | |t_IS|=0.74 < 2.0; |IC_OOS|=0.0139 < 0.02; |t_OOS|=0.17 < 2.0; Sign flip IS(-0.0575) -> OOS(+0.0139) |
| `value_per_tx_usd` | 1d | -0.0807 | -1.50 | -0.1283 | -1.69 | 173 | -0.0470 | -0.61 | 170 | **KILL** | |t_IS|=1.69 < 2.0; |t_OOS|=0.61 < 2.0 |
| `value_per_tx_usd` | 3d | -0.0582 | -1.07 | -0.1560 | -2.07 | 173 | -0.0132 | -0.17 | 168 | **KILL** | |IC_OOS|=0.0132 < 0.02; |t_OOS|=0.17 < 2.0 |
| `value_per_tx_usd` | 7d | -0.0500 | -0.92 | -0.1181 | -1.56 | 173 | -0.0201 | -0.26 | 164 | **KILL** | |t_IS|=1.56 < 2.0; |t_OOS|=0.26 < 2.0 |
| `value_per_tx_usd` | 14d | -0.0944 | -1.72 | -0.1063 | -1.40 | 173 | -0.0614 | -0.77 | 157 | **KILL** | |t_IS|=1.40 < 2.0; |t_OOS|=0.77 < 2.0 |
| `trade_vol_zscore` | 1d | +0.0471 | +0.84 | +0.0657 | +0.79 | 145 | +0.0390 | +0.51 | 173 | **KILL** | |t_IS|=0.79 < 2.0; |t_OOS|=0.51 < 2.0 |
| `trade_vol_zscore` | 3d | -0.0931 | -1.66 | -0.1572 | -1.90 | 145 | -0.0678 | -0.88 | 171 | **KILL** | |t_IS|=1.90 < 2.0; |t_OOS|=0.88 < 2.0 |
| `trade_vol_zscore` | 7d | -0.1218 | -2.16 | -0.1966 | -2.40 | 145 | -0.0978 | -1.26 | 167 | **KILL** | |t_OOS|=1.26 < 2.0 |
| `trade_vol_zscore` | 14d | -0.1510 | -2.66 | -0.1859 | -2.26 | 145 | -0.1186 | -1.50 | 160 | **KILL** | |t_OOS|=1.50 < 2.0 |
| `mp_hashrate` | 1d | -0.0564 | -1.05 | -0.0804 | -1.06 | 174 | +0.0508 | +0.67 | 173 | **KILL** | |t_IS|=1.06 < 2.0; |t_OOS|=0.67 < 2.0; Sign flip IS(-0.0804) -> OOS(+0.0508) |
| `mp_hashrate` | 3d | -0.0614 | -1.14 | -0.0219 | -0.29 | 174 | +0.0603 | +0.79 | 171 | **KILL** | |t_IS|=0.29 < 2.0; |t_OOS|=0.79 < 2.0; Sign flip IS(-0.0219) -> OOS(+0.0603) |
| `mp_hashrate` | 7d | -0.1151 | -2.13 | -0.1056 | -1.39 | 174 | +0.1559 | +2.03 | 167 | **KILL** | |t_IS|=1.39 < 2.0; Sign flip IS(-0.1056) -> OOS(+0.1559) |
| `mp_hashrate` | 14d | -0.2227 | -4.16 | -0.1549 | -2.06 | 174 | +0.1560 | +1.98 | 160 | **KILL** | |t_OOS|=1.98 < 2.0; Sign flip IS(-0.1549) -> OOS(+0.1560) |
| `mp_difficulty` | 1d | -0.0897 | -1.65 | -0.0713 | -0.91 | 163 | +0.0118 | +0.15 | 173 | **KILL** | |t_IS|=0.91 < 2.0; |IC_OOS|=0.0118 < 0.02; |t_OOS|=0.15 < 2.0; Sign flip IS(-0.0713) -> OOS(+0.0118) |
| `mp_difficulty` | 3d | -0.1945 | -3.61 | -0.2134 | -2.77 | 163 | -0.0367 | -0.48 | 171 | **KILL** | |t_OOS|=0.48 < 2.0 |
| `mp_difficulty` | 7d | -0.3435 | -6.62 | -0.3733 | -5.11 | 163 | -0.0753 | -0.97 | 167 | **KILL** | |t_OOS|=0.97 < 2.0 |
| `mp_difficulty` | 14d | -0.4746 | -9.66 | -0.5186 | -7.70 | 163 | -0.0819 | -1.03 | 160 | **KILL** | |t_OOS|=1.03 < 2.0 |
| `mp_diff_adjustment` | 1d | +0.0310 | +0.57 | -0.0146 | -0.19 | 163 | +0.0689 | +0.90 | 173 | **KILL** | |IC_IS|=0.0146 < 0.02; |t_IS|=0.19 < 2.0; |t_OOS|=0.90 < 2.0; Sign flip IS(-0.0146) -> OOS(+0.0689) |
| `mp_diff_adjustment` | 3d | +0.0268 | +0.49 | -0.0732 | -0.93 | 163 | +0.0969 | +1.27 | 171 | **KILL** | |t_IS|=0.93 < 2.0; |t_OOS|=1.27 < 2.0; Sign flip IS(-0.0732) -> OOS(+0.0969) |
| `mp_diff_adjustment` | 7d | +0.0038 | +0.07 | -0.2012 | -2.61 | 163 | +0.1235 | +1.60 | 167 | **KILL** | |t_OOS|=1.60 < 2.0; Sign flip IS(-0.2012) -> OOS(+0.1235) |
| `mp_diff_adjustment` | 14d | -0.0131 | -0.23 | -0.3364 | -4.53 | 163 | +0.0964 | +1.22 | 160 | **KILL** | |t_OOS|=1.22 < 2.0; Sign flip IS(-0.3364) -> OOS(+0.0964) |
| `hashrate_7d_chg` | 1d | -0.0190 | -0.35 | -0.0927 | -1.20 | 167 | +0.0462 | +0.61 | 173 | **KILL** | |t_IS|=1.20 < 2.0; |t_OOS|=0.61 < 2.0; Sign flip IS(-0.0927) -> OOS(+0.0462) |
| `hashrate_7d_chg` | 3d | -0.0258 | -0.47 | -0.0183 | -0.24 | 167 | -0.0128 | -0.17 | 171 | **KILL** | |IC_IS|=0.0183 < 0.02; |t_IS|=0.24 < 2.0; |IC_OOS|=0.0128 < 0.02; |t_OOS|=0.17 < 2.0 |
| `hashrate_7d_chg` | 7d | +0.0191 | +0.35 | -0.0578 | -0.74 | 167 | +0.0948 | +1.22 | 167 | **KILL** | |t_IS|=0.74 < 2.0; |t_OOS|=1.22 < 2.0; Sign flip IS(-0.0578) -> OOS(+0.0948) |
| `hashrate_7d_chg` | 14d | +0.0752 | +1.36 | +0.0064 | +0.08 | 167 | +0.1481 | +1.88 | 160 | **KILL** | |IC_IS|=0.0064 < 0.02; |t_IS|=0.08 < 2.0; |t_OOS|=1.88 < 2.0 |
| `hashrate_30d_chg` | 1d | +0.0221 | +0.39 | -0.0947 | -1.13 | 144 | +0.1112 | +1.46 | 173 | **KILL** | |t_IS|=1.13 < 2.0; |t_OOS|=1.46 < 2.0; Sign flip IS(-0.0947) -> OOS(+0.1112) |
| `hashrate_30d_chg` | 3d | +0.0124 | +0.22 | -0.0910 | -1.09 | 144 | +0.0724 | +0.94 | 171 | **KILL** | |t_IS|=1.09 < 2.0; |t_OOS|=0.94 < 2.0; Sign flip IS(-0.0910) -> OOS(+0.0724) |
| `hashrate_30d_chg` | 7d | +0.0256 | +0.45 | -0.1903 | -2.31 | 144 | +0.1661 | +2.16 | 167 | **KILL** | Sign flip IS(-0.1903) -> OOS(+0.1661) |
| `hashrate_30d_chg` | 14d | +0.0547 | +0.95 | -0.1718 | -2.08 | 144 | +0.1688 | +2.15 | 160 | **KILL** | Sign flip IS(-0.1718) -> OOS(+0.1688) |

**Summary:** 2 PASS / 74 KILL out of 76 metric-horizon combinations

### 2.1 Top 10 by |IC_Full|

| Metric | Horizon | IC_Full | t_Full | IC_IS | IC_OOS | Verdict |
|---|---|---|---|---|---|---|
| `mp_difficulty` | 14d | -0.4746 | -9.66 | -0.5186 | -0.0819 | **KILL** |
| `total-bitcoins` | 14d | -0.4333 | -8.77 | -0.3677 | -0.1034 | **KILL** |
| `mp_difficulty` | 7d | -0.3435 | -6.62 | -0.3733 | -0.0753 | **KILL** |
| `total-bitcoins` | 7d | -0.2612 | -4.99 | -0.1481 | +0.0170 | **KILL** |
| `trade-volume` | 14d | -0.2599 | -4.91 | -0.1801 | -0.0985 | **KILL** |
| `mp_hashrate` | 14d | -0.2227 | -4.16 | -0.1549 | +0.1560 | **KILL** |
| `output-volume` | 14d | -0.2189 | -4.08 | -0.0653 | -0.0483 | **KILL** |
| `trade-volume` | 7d | -0.2061 | -3.88 | -0.2072 | -0.0661 | **KILL** |
| `mp_difficulty` | 3d | -0.1945 | -3.61 | -0.2134 | -0.0367 | **KILL** |
| `market-cap` | 14d | -0.1859 | -3.45 | -0.5628 | -0.2064 | **PASS** |

### 2.2 Verdict Summary Per Metric

| Metric | Description | Horizons PASS | Horizons KILL | Best IC | Best Horizon |
|---|---|---|---|---|---|
| `estimated-transaction-volume-usd` | Estimated USD Transaction Value (USD) | 0 | 4 | -0.1550 | 14d |
| `n-transactions` | Confirmed Transactions Per Day (Transactions) | 0 | 4 | -0.0748 | 14d |
| `market-cap` | Market Capitalization (USD) | 2 | 2 | -0.1859 | 14d |
| `total-bitcoins` | Bitcoins in circulation (BTC) | 0 | 4 | -0.4333 | 14d |
| `n-unique-addresses` | Number Of Unique Addresses Used (Unique Addresses) | 0 | 4 | +0.0536 | 7d |
| `n-transactions-per-block` | Average Number Of Transactions Per Block (Transactions Per Block) | 0 | 4 | -0.1490 | 14d |
| `estimated-transaction-volume` | Estimated Transaction Value (BTC) | 0 | 4 | -0.1295 | 14d |
| `output-volume` | Output Value (BTC) | 0 | 4 | -0.2189 | 14d |
| `trade-volume` | USD Exchange Trade Volume (Trade Volume (USD)) | 0 | 4 | -0.2599 | 14d |
| `nvt_ratio` | NVT Ratio (MarketCap / Est. TX Volume USD) | 0 | 4 | +0.1259 | 14d |
| `active_addr_7d_chg` | Active Address 7d % Change | 0 | 4 | +0.0378 | 14d |
| `tx_count_7d_chg` | TX Count 7d % Change | 0 | 4 | -0.0150 | 7d |
| `value_per_tx_usd` | Avg TX Value in USD | 0 | 4 | -0.0944 | 14d |
| `trade_vol_zscore` | Exchange Trade Volume Z-Score (30d) | 0 | 4 | -0.1510 | 14d |
| `mp_hashrate` | Mempool Space Avg Hashrate (H/s) | 0 | 4 | -0.2227 | 14d |
| `mp_difficulty` | Mining Difficulty (forward-filled) | 0 | 4 | -0.4746 | 14d |
| `mp_diff_adjustment` | Difficulty Adjustment Multiplier (forward-filled) | 0 | 4 | +0.0310 | 1d |
| `hashrate_7d_chg` | Hashrate 7d % Change | 0 | 4 | +0.0752 | 14d |
| `hashrate_30d_chg` | Hashrate 30d % Change | 0 | 4 | +0.0547 | 14d |

## 3. Regime Analysis

Analyzed signals: `market-cap`

Regime defined by trailing 30d BTC return: >0 = uptrend, <=0 = downtrend

| Metric | Horizon | IC_Uptrend | t_Uptrend | n_Up | IC_Downtrend | t_Downtrend | n_Down |
|---|---|---|---|---|---|---|---|
| `market-cap` | 1d | -0.0396 | -0.50 | 160 | -0.0351 | -0.48 | 188 |
| `market-cap` | 3d | -0.1317 | -1.67 | 160 | -0.0739 | -1.01 | 186 |
| `market-cap` | 7d | -0.2829 | -3.71 | 160 | -0.0896 | -1.21 | 182 |
| `market-cap` | 14d | -0.3386 | -4.52 | 160 | -0.1229 | -1.63 | 175 |

## 4. Kill Criteria Applied

| Criterion | Threshold | Description |
|---|---|---|
| Low IC | abs(IC) < 0.02 | Signal has negligible rank correlation with forward returns |
| Low t-stat | abs(t) < 2.0 | IC not statistically significant at ~95% confidence |
| Sign flip | IS sign != OOS sign | Signal direction reverses out-of-sample (unreliable) |

## 5. Conclusions

**2 metric-horizon combinations passed kill criteria.**

Passing metrics:
- `market-cap`: PASS at 7d, 14d (best IC=-0.1859 at 14d)

### CRITICAL CAVEAT: Market-Cap Signal is Mean-Reversion, Not On-Chain Alpha

The `market-cap` signal passing is a **false positive** for on-chain alpha discovery. Here is why:

1. **Market cap IS price.** BTC market cap = BTC price x BTC supply. Since supply changes <0.01% daily, market cap is essentially a monotonic transformation of price. A negative IC between current market cap (=price level) and forward returns simply measures **mean reversion** -- when price is high relative to its recent history, forward returns tend to be negative, and vice versa.

2. **This is not a new signal.** It is equivalent to a contrarian level signal (short when high, long when low), which is well-known and already captured by any momentum/mean-reversion framework.

3. **Regime dependency confirms this.** The regime split shows IC is much stronger during uptrends (-0.34 at 14d) than downtrends (-0.12 at 14d), consistent with mean-reversion being more predictable at trend extremes.

4. **Not actionable as an on-chain signal.** You do not need blockchain data to know the BTC price level -- exchange data suffices.

**Effective verdict: KILL (not genuine on-chain alpha).**

### Genuine On-Chain Signal Assessment

After removing market-cap as a price proxy, **zero on-chain metrics from either data source produced actionable predictive signal** for BTC forward returns at any horizon tested.

The most common failure modes:
- **IS signal that vanishes OOS:** estimated-transaction-volume-usd, trade-volume, mp_difficulty all showed IS IC > 0.15 but collapsed to near-zero OOS. Classic overfitting to the sample period.
- **Sign flips IS to OOS:** n-transactions, mp_hashrate, hashrate_30d_chg all reversed direction. The signal was noise, not signal.
- **Near-zero IC everywhere:** active_addr_7d_chg, tx_count_7d_chg, n-unique-addresses showed no predictive power in any sample.

### Limitations

1. **Short sample:** On-chain data covers ~1 year (2025-03-25 to 2026-03-16), yielding ~170 IS + ~180 OOS daily observations. This is marginal for robust IC estimation.
2. **Mempool fees:** Only a block-level snapshot (8 blocks) was available, not historical time series. Fee pressure signals could not be tested.
3. **Single asset:** All analysis is BTC-only. Cross-sectional IC (across multiple assets) would be a stronger test.
4. **No transaction costs:** IC measures predictive rank correlation only, not tradeable alpha after costs.
