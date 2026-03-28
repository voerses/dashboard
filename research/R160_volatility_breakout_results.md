# R160 -- Volatility Breakout Rotation Strategy Results

## Strategy Summary

**Core Idea**: Bollinger Band breakout + volume confirmation on 4H bars, rotated across all liquid tokens.

### Signal
- Bollinger Bands: 20-period SMA +/- 2.0 std on 4H bars
- Volume confirmation: volume > 1.5x 20-period avg volume
- BREAKOUT LONG: Close > BB_upper AND volume confirmed
- BREAKOUT SHORT: Close < BB_lower AND volume confirmed

### Entry & Exit
- Entry: At next 4H bar open after breakout signal
- Trail stop: Close below SMA(20) for longs, above for shorts
- Partial profit: At +3.0x ATR(20), close 50%, move stop to breakeven
- Max hold: 180 bars (30 days)
- Or: opposite breakout signal

### Portfolio Construction
- Scan all 105 qualified tokens every 4H
- Enter top 5 strongest breakouts (by distance from BB as % of price)
- Equal weight: 20% per position
- Max 10 positions total

### Costs & Data
- 7 bps per side (4 taker + 3 slippage) + funding from parquet
- Token universe: 105 tokens with >1yr data and $2M+ daily volume

### Variants
- **Variant A (Base)**: Breakout + volume confirmation only
- **Variant B (Momentum Filter)**: Same as A, but longs only if 14-day return > 0, shorts only if 14-day return < 0

## Performance Summary

| Variant | Leverage | Ann.Ret(Full) | Ann.Ret(12mo) | Sharpe(Full) | Sharpe(12mo) | MaxDD(Full) | MaxDD(12mo) | Calmar | Trades | WR | PF | AvgHold |
|---------|----------|---------------|---------------|-------------|-------------|------------|------------|--------|--------|----|----|---------|
| A | 1x | +32.7% | +28.7% | 1.31 | 1.40 | -40.1% | -23.5% | 0.81 | 8414 | 49% | 1.12 | 2.4d |
| A | 2x | +46.2% | +32.3% | 1.37 | 1.41 | -45.3% | -25.8% | 1.02 | 8414 | 49% | 1.12 | 2.4d |
| A | 3x | +55.2% | +33.7% | 1.40 | 1.41 | -47.3% | -26.6% | 1.17 | 8414 | 49% | 1.12 | 2.4d |
| B | 1x | +41.6% | +29.9% | 1.88 | 2.20 | -25.1% | -7.5% | 1.66 | 7336 | 51% | 1.22 | 2.5d |
| B | 2x | +56.8% | +32.3% | 1.87 | 2.20 | -35.3% | -8.0% | 1.61 | 7336 | 51% | 1.22 | 2.5d |
| B | 3x | +66.8% | +33.2% | 1.87 | 2.20 | -41.0% | -8.2% | 1.63 | 7336 | 51% | 1.22 | 2.5d |

## Trade Analysis (Variant A, 1x)

- **Total trades**: 8414 (5309 long, 3105 short)
- **Long win rate**: 48.2%
- **Short win rate**: 50.7%
- **Avg hold time**: 2.4 days
- **Median hold time**: 2.2 days

- **Avg winner**: +1.2542% (of portfolio equity)
- **Avg loser**: -1.0778% (of portfolio equity)

### Exit Reason Breakdown

| Exit Reason | Count | Avg PnL |
|-------------|-------|---------|
| trail_stop | 6625 | -0.2592% |
| partial | 1779 | +1.2834% |
| end_of_data | 10 | +0.6009% |

## Top Contributing Tokens (Variant A, 1x)

### Best performers

| Token | Trades | Total PnL | Avg PnL | Win Rate | Avg Hold (bars) |
|-------|--------|-----------|---------|----------|-----------------|
| DOGE | 165 | +69.0367% | +0.4184% | 48% | 14 |
| AVAX | 107 | +50.9643% | +0.4763% | 59% | 16 |
| ZEC | 200 | +42.0699% | +0.2103% | 58% | 15 |
| VVV | 34 | +39.6154% | +1.1652% | 68% | 17 |
| CRV | 141 | +36.5776% | +0.2594% | 57% | 16 |
| AXS | 135 | +36.3601% | +0.2693% | 52% | 14 |
| GALA | 111 | +34.0453% | +0.3067% | 51% | 15 |
| INJ | 82 | +33.4334% | +0.4077% | 59% | 16 |
| MOODENG | 35 | +31.9908% | +0.9140% | 60% | 13 |
| OP | 95 | +31.1997% | +0.3284% | 60% | 15 |

### Worst performers

| Token | Trades | Total PnL | Avg PnL | Win Rate | Avg Hold (bars) |
|-------|--------|-----------|---------|----------|-----------------|
| BAN | 42 | -17.2084% | -0.4097% | 36% | 12 |
| G | 19 | -18.0972% | -0.9525% | 32% | 11 |
| REZ | 38 | -20.7433% | -0.5459% | 29% | 11 |
| IMX | 80 | -20.9054% | -0.2613% | 38% | 15 |
| XMR | 163 | -21.5515% | -0.1322% | 39% | 14 |
| FARTCOIN | 31 | -26.5754% | -0.8573% | 32% | 13 |
| DYDX | 109 | -29.1036% | -0.2670% | 44% | 14 |
| UNI | 140 | -30.2357% | -0.2160% | 47% | 14 |
| OGN | 126 | -32.4908% | -0.2579% | 40% | 12 |
| SNX | 145 | -33.1167% | -0.2284% | 39% | 13 |

## Monthly Returns

### Variant A (1x)

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | YTD |
|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
| 2020 | - | +10.5% | +34.4% | +7.0% | -16.1% | -5.3% | +7.1% | -4.1% | +14.4% | -14.3% | +18.0% | +1.1% | +51.4% |
| 2021 | +32.6% | +61.4% | +17.9% | +2.3% | -10.4% | -4.0% | +8.1% | +9.5% | +0.6% | -7.1% | +9.5% | -5.7% | +153.6% |
| 2022 | +6.6% | -5.2% | +0.5% | -6.9% | +2.3% | +3.9% | +11.5% | -4.9% | -13.6% | +2.0% | +1.6% | +2.9% | -1.7% |
| 2023 | +12.9% | -3.7% | -4.3% | -8.5% | -1.2% | +0.2% | -4.2% | -7.3% | -0.2% | +6.3% | -19.4% | +17.0% | -16.4% |
| 2024 | -2.2% | +6.7% | +11.3% | +2.7% | -3.8% | -4.6% | +1.8% | +5.4% | +1.8% | +3.4% | +20.3% | +2.8% | +52.9% |
| 2025 | -3.6% | -4.9% | +3.4% | +8.4% | +9.4% | -2.2% | +0.8% | -4.4% | -4.0% | -8.7% | -0.5% | -1.3% | -9.0% |
| 2026 | +8.6% | +14.7% | +5.9% | - | - | - | - | - | - | - | - | - | +31.9% |

### Variant B (1x)

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | YTD |
|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
| 2020 | - | +6.5% | +36.0% | +14.1% | -14.5% | -7.9% | +8.1% | +4.7% | +16.3% | -15.5% | +1.6% | +6.7% | +57.2% |
| 2021 | +31.2% | +55.7% | +18.9% | +0.7% | -5.8% | -9.8% | +7.2% | +14.2% | +4.8% | -5.1% | +10.6% | -1.6% | +175.2% |
| 2022 | +7.0% | -3.9% | -1.2% | -3.6% | +7.6% | +1.3% | +10.3% | +2.0% | -7.8% | +0.8% | +6.4% | +2.1% | +21.1% |
| 2023 | +12.2% | -2.9% | -4.8% | -5.3% | -0.9% | +1.0% | +0.6% | -2.0% | +1.2% | +3.4% | -8.3% | +11.2% | +3.5% |
| 2024 | +1.7% | +5.9% | +4.8% | +2.2% | -2.2% | +0.8% | +0.4% | +4.9% | +1.1% | -2.0% | +9.4% | -1.3% | +28.2% |
| 2025 | -1.9% | -2.8% | +0.3% | +3.7% | +7.0% | -1.3% | +2.1% | -2.3% | -0.7% | -0.5% | +2.0% | -0.7% | +4.6% |
| 2026 | +10.1% | +5.0% | +3.3% | - | - | - | - | - | - | - | - | - | +19.4% |

## Variant B vs A Comparison

Variant B adds a 14-day momentum filter: only take long breakouts when the token's 14-day return is positive, and short breakouts when negative. This filters out false breakouts against the prevailing trend.

| Metric | A (1x) | B (1x) | A (2x) | B (2x) | A (3x) | B (3x) |
|--------|--------|--------|--------|--------|--------|--------|
| Ann. Return | +32.7% | +41.6% | +46.2% | +56.8% | +55.2% | +66.8% |
| Sharpe | 1.31 | 1.88 | 1.37 | 1.87 | 1.40 | 1.87 |
| Max DD | -40.1% | -25.1% | -45.3% | -35.3% | -47.3% | -41.0% |
| Calmar | 0.81 | 1.66 | 1.02 | 1.61 | 1.17 | 1.63 |
| Trades | 8414 | 7336 | 8414 | 7336 | 8414 | 7336 |
| Win Rate | 49% | 51% | 49% | 51% | 49% | 51% |
| Profit Factor | 1.12 | 1.22 | 1.12 | 1.22 | 1.12 | 1.22 |

## Token Universe

**105 tokens** passed filters (>1yr data, $2M+ daily volume):

AAVE, ADA, AGLD, AIXBT, AKT, ALGO, ANIME, APT, ARB, ARC
ATOM, AVAX, AXS, BAN, BCH, BERA, BIO, BNB, BTC, CAKE
CFX, CHZ, COS, CRV, DASH, DEGO, DENT, DEXE, DOGE, DOT
DUSK, DYDX, EIGEN, ENA, ENS, ETC, ETH, ETHFI, FARTCOIN, FET
FIL, FLOW, G, GALA, GRASS, HBAR, ICP, IMX, INJ, IP
JUP, KAS, KAVA, LDO, LINK, LTC, ME, MOODENG, MORPHO, MOVE
NEAR, NEIRO, NEO, OGN, ONDO, OP, ORCA, PENDLE, PENGU, PHA
PIPPIN, PIXEL, POL, POLYX, RENDER, REZ, RVN, SAND, SEI, SNX
SOL, SPX, STEEM, STRK, SUI, TAO, THE, TIA, TON, TRUMP
TRX, TURBO, UNI, VANRY, VIRTUAL, VVV, WIF, WLD, XLM, XMR
XRP, ZEC, ZEN, ZK, ZRO

## Notes

- 4H bars resampled from 1H data (OHLCV aggregation)
- Signals generated on bar close, entry at NEXT bar open (no look-ahead)
- Volume confirmation is key: breakouts without volume are filtered out
- Funding applied per 4H bar: longs pay positive funding, shorts collect
- Warmup period: first 30 bars skipped for indicator convergence
- Portfolio rotation: every 4H bar, scan all tokens, rank breakouts, enter top 5
- Partial profit mechanism closes 50% at 3x ATR, moves stop to breakeven
- Trail stop uses SMA(20) cross (not ATR-based) for trend-following exits
