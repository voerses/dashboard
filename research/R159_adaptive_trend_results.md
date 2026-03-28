# R159 -- Per-Token ADAPTIVE Trend-Following with Smart Exit Management

## Strategy Design

### Entry Signal (Per-Token Adaptive)
- 3 EMA cross timeframes: FAST(8, 21), MEDIUM(20, 50), SLOW(50, 200)
- Score: +1 per bullish cross (fast>slow), -1 per bearish => [-3, +3]
- Score >= 2: STRONG LONG (full size), == 1: MILD LONG (half size)
- Score <= -2: STRONG SHORT (full size), == -1: MILD SHORT (half size)
- Score == 0: FLAT

### Exit Strategy (Volatility-Calibrated)
- **Trailing stop**: 3.0x ATR(24) from peak/trough
- **Partial profit**: At +5.0x ATR, close 50%, tighten to 2.0x ATR
- **Time exit**: 240h (10d) without +2.0x ATR move
- **Regime exit**: Score flips to opposite direction => immediate close

### Portfolio Construction
- Max 10 longs + 5 shorts
- Target: 60% long, 30% short, 10% cash
- Recheck every 4h, equal weight within groups
- Costs: 7.0 bps/side + hourly funding

### Universe
- 107 tokens with >8760h data and >$5M daily dollar volume
- Data: 2020-01-01 to 2026-03-17 (54,425 hourly bars)

### Token Universe

AAVE, ADA, AGLD, AIXBT, AKT, ALGO, ANIME, APT, ARB, ARC, ATOM, AVAX, AXS, BAN, BCH
BERA, BIO, BNB, BTC, CAKE, CFX, CHZ, COS, CRV, DASH, DEGO, DENT, DEXE, DOGE, DOT
DUSK, DYDX, EIGEN, ENA, ENJ, ENS, ETC, ETH, ETHFI, FARTCOIN, FET, FIL, FLOW, G, GALA
GRASS, HBAR, ICP, IMX, INJ, IP, JUP, KAS, KAVA, LDO, LINK, LTC, ME, MOODENG, MORPHO
MOVE, NEAR, NEIRO, NEO, OGN, ONDO, OP, ORCA, PENDLE, PENGU, PHA, PIPPIN, PIXEL, POL, POLYX
RENDER, REZ, RVN, SAND, SEI, SHIB, SNX, SOL, SPX, STEEM, STRK, SUI, TAO, THE, TIA
TON, TRUMP, TRX, TURBO, UNI, VANRY, VIRTUAL, VVV, WIF, WLD, XLM, XMR, XRP, ZEC, ZEN
ZK, ZRO

**Total: 107 tokens**

## Results by Leverage

### Full Period

| Lev | Ann.Return | Total | Sharpe | Sortino | MaxDD | Calmar | Trades | WR | PF | Avg PnL |
|-----|-----------|-------|--------|---------|-------|--------|--------|----|----|---------|
| 1x | +13.2% | +115.7% | 0.83 | 0.82 | -41.9% | 0.31 | 39918 | 40% | 1.03 | +0.003% |
| 2x | +21.3% | +231.4% | 0.94 | 0.91 | -49.2% | 0.43 | 39918 | 40% | 1.03 | +0.006% |
| 3x | +27.3% | +347.1% | 1.00 | 0.96 | -52.3% | 0.52 | 39918 | 40% | 1.03 | +0.009% |

### Last 12 Months (since 2025-03-17)

| Lev | Ann.Return | Total | Sharpe | Sortino | MaxDD | Calmar | Trades | WR | PF |
|-----|-----------|-------|--------|---------|-------|--------|--------|----|----|
| 1x | -12.4% | -12.4% | -0.72 | -0.70 | -21.2% | -0.59 | 7059 | 39% | 0.96 |
| 2x | -15.6% | -15.6% | -0.68 | -0.66 | -26.6% | -0.59 | 7059 | 39% | 0.96 |
| 3x | -17.0% | -17.1% | -0.66 | -0.64 | -29.1% | -0.59 | 7059 | 39% | 0.96 |

## Monthly Returns (1x Leverage)

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | YTD |
|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
| 2020 | - | +7.7% | +1.1% | +13.9% | -12.6% | +1.0% | +8.5% | +1.9% | -8.2% | +0.4% | +6.9% | +3.8% | +24.5% |
| 2021 | +27.4% | +28.5% | +16.6% | +14.0% | -6.2% | -7.5% | +4.1% | +6.7% | +5.9% | -1.7% | +11.3% | -5.1% | +94.0% |
| 2022 | -5.0% | -4.1% | +2.2% | -0.5% | +2.5% | -9.0% | +5.3% | -2.7% | -4.6% | -0.7% | +1.4% | -2.3% | -17.7% |
| 2023 | +8.9% | +7.2% | -2.2% | -2.6% | -0.0% | +1.0% | -1.8% | -4.0% | -1.7% | -1.2% | +0.7% | +2.6% | +6.8% |
| 2024 | -5.0% | +3.6% | +1.7% | -7.5% | -1.4% | +0.9% | -2.6% | -6.4% | +1.8% | -4.9% | +12.8% | +8.3% | +1.4% |
| 2025 | -4.1% | -1.9% | -4.8% | +1.1% | -2.7% | -2.7% | -4.4% | -3.1% | -3.1% | -5.6% | +9.3% | -4.9% | -26.9% |
| 2026 | +6.0% | -1.5% | +2.0% | - | - | - | - | - | - | - | - | - | +6.5% |

## Monthly Returns (2x Leverage)

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | YTD |
|------|------|------|------|------|------|------|------|------|------|------|------|------|------|
| 2020 | - | +14.4% | +2.0% | +24.4% | -20.1% | +1.8% | +14.7% | +3.1% | -13.3% | +0.7% | +11.9% | +6.2% | +45.7% |
| 2021 | +43.9% | +40.4% | +21.5% | +17.4% | -7.5% | -9.2% | +5.2% | +8.3% | +7.2% | -2.0% | +13.7% | -6.1% | +132.8% |
| 2022 | -5.9% | -4.9% | +2.6% | -0.7% | +3.0% | -10.9% | +6.5% | -3.3% | -5.7% | -0.8% | +1.7% | -2.9% | -21.3% |
| 2023 | +11.1% | +8.8% | -2.6% | -3.2% | -0.0% | +1.3% | -2.2% | -4.9% | -2.0% | -1.5% | +0.8% | +3.2% | +8.7% |
| 2024 | -6.1% | +4.5% | +2.1% | -9.2% | -1.7% | +1.2% | -3.2% | -8.1% | +2.3% | -6.2% | +16.6% | +10.4% | +2.4% |
| 2025 | -5.1% | -2.4% | -6.0% | +1.3% | -3.4% | -3.4% | -5.6% | -4.0% | -4.1% | -7.3% | +12.6% | -6.4% | -33.8% |
| 2026 | +7.9% | -2.0% | +2.7% | - | - | - | - | - | - | - | - | - | +8.6% |

## Exit Reason Breakdown (1x)

| Reason | Count | Pct |
|--------|-------|-----|
| trail_stop | 25117 | 62.9% |
| regime_flip | 12256 | 30.7% |
| partial_profit | 2531 | 6.3% |
| end_of_data | 14 | 0.0% |

## Token PnL Attribution (1x Leverage)

### Top 15 Contributors

| Token | PnL (% equity) | Hours Held |
|-------|---------------|------------|
| SOL | +35.97% | 6,264 |
| GALA | +31.76% | 3,606 |
| FET | +19.52% | 3,957 |
| AVAX | +19.23% | 20,530 |
| DOGE | +19.02% | 8,855 |
| ENS | +16.65% | 5,537 |
| CHZ | +13.97% | 11,791 |
| ICP | +12.58% | 3,281 |
| CRV | +11.32% | 13,688 |
| XRP | +11.18% | 7,908 |
| LINK | +10.00% | 8,483 |
| MOVE | +9.94% | 1,137 |
| FIL | +9.61% | 6,026 |
| ZEN | +9.38% | 4,934 |
| DEXE | +8.76% | 3,129 |

### Bottom 15 Contributors

| Token | PnL (% equity) | Hours Held |
|-------|---------------|------------|
| WLD | -5.43% | 1,412 |
| FLOW | -5.64% | 2,906 |
| SPX | -7.65% | 922 |
| NEO | -7.75% | 5,555 |
| WIF | -7.93% | 1,003 |
| MOODENG | -8.40% | 913 |
| APT | -9.03% | 14,050 |
| BCH | -10.47% | 23,084 |
| TRX | -10.65% | 12,926 |
| DOT | -10.88% | 7,196 |
| DASH | -11.13% | 13,098 |
| ALGO | -13.16% | 28,324 |
| OGN | -15.25% | 4,111 |
| CFX | -15.68% | 6,322 |
| XMR | -18.10% | 14,149 |

## Most Held Tokens (by hours in portfolio, 1x)

| Token | Hours | Days | PnL |
|-------|-------|------|-----|
| ADA | 33,614 | 1401 | -5.37% |
| AAVE | 30,305 | 1263 | -3.85% |
| ALGO | 28,324 | 1180 | -13.16% |
| ATOM | 25,721 | 1072 | +0.28% |
| BCH | 23,084 | 962 | -10.47% |
| BNB | 21,890 | 912 | +4.04% |
| BTC | 20,978 | 874 | -0.78% |
| AVAX | 20,530 | 855 | +19.23% |
| AXS | 16,857 | 702 | +3.46% |
| XMR | 14,149 | 590 | -18.10% |
| APT | 14,050 | 585 | -9.03% |
| CRV | 13,688 | 570 | +11.32% |
| DASH | 13,098 | 546 | -11.13% |
| TRX | 12,926 | 539 | -10.65% |
| CHZ | 11,791 | 491 | +13.97% |
| ETH | 10,846 | 452 | +4.48% |
| ARB | 9,899 | 412 | -3.64% |
| ETC | 9,515 | 396 | -5.29% |
| AGLD | 9,495 | 396 | +6.62% |
| LTC | 9,183 | 383 | -2.33% |

## Performance by BTC Regime (1x)

BTC regime: EMA(20d) > EMA(50d) = UPTREND, else DOWNTREND

| Regime | Hours | % Time | Total Return | Ann.Return | Sharpe | Trades |
|--------|-------|--------|-------------|-----------|--------|--------|
| BTC_UP | 31,464 | 57.8% | +182.6% | +33.5% | 1.75 | 23449 |
| BTC_DOWN | 22,961 | 42.2% | -23.7% | -9.8% | -0.61 | 16469 |

## Trade Analysis (1x Leverage)

- **Total trades**: 39918
- **Long trades**: 26897
- **Short trades**: 13021
- **Long avg PnL**: +0.007%
- **Long win rate**: 40%
- **Short avg PnL**: -0.005%
- **Short win rate**: 40%
- **Avg holding period**: 17h (0.7d)
- **Median holding period**: 12h (0.5d)

### Top 10 Trades
| Token | Entry | Exit | Dir | PnL | Bars | Exit Reason |
|-------|-------|------|-----|-----|------|-------------|
| GALA | 2021-11-16 | 2021-11-17 | LONG | +19.32% | 20 | trail_stop |
| FET | 2023-02-06 | 2023-02-07 | LONG | +19.05% | 27 | trail_stop |
| MOVE | 2024-12-20 | 2024-12-20 | LONG | +11.89% | 12 | trail_stop |
| SOL | 2021-02-23 | 2021-02-24 | LONG | +8.99% | 31 | partial_profit |
| SOL | 2021-02-23 | 2021-02-25 | LONG | +8.76% | 44 | trail_stop |
| AGLD | 2024-11-03 | 2024-11-05 | LONG | +8.70% | 40 | trail_stop |
| ENS | 2022-05-01 | 2022-05-02 | LONG | +8.36% | 42 | partial_profit |
| FIL | 2021-03-25 | 2021-03-26 | LONG | +8.34% | 26 | trail_stop |
| ZEN | 2024-12-20 | 2024-12-20 | LONG | +8.27% | 1 | trail_stop |
| PIPPIN | 2025-12-01 | 2025-12-03 | LONG | +7.77% | 46 | trail_stop |

### Bottom 10 Trades
| Token | Entry | Exit | Dir | PnL | Bars | Exit Reason |
|-------|-------|------|-----|-----|------|-------------|
| DEGO | 2025-10-17 | 2025-10-19 | LONG | -3.91% | 35 | regime_flip |
| OGN | 2023-10-10 | 2023-10-10 | LONG | -4.34% | 7 | trail_stop |
| TRX | 2020-09-03 | 2020-09-04 | LONG | -4.54% | 2 | trail_stop |
| DASH | 2021-07-20 | 2021-07-20 | LONG | -4.88% | 14 | regime_flip |
| LINK | 2020-02-16 | 2020-02-16 | LONG | -5.00% | 2 | trail_stop |
| TRX | 2020-09-04 | 2020-09-04 | LONG | -5.01% | 11 | trail_stop |
| MOODENG | 2024-12-02 | 2024-12-02 | SHORT | -5.28% | 5 | trail_stop |
| ARC | 2026-02-04 | 2026-02-04 | LONG | -6.88% | 2 | trail_stop |
| DYDX | 2022-01-21 | 2022-01-22 | LONG | -8.08% | 4 | trail_stop |
| PIPPIN | 2025-10-30 | 2025-10-30 | LONG | -9.55% | 1 | trail_stop |

## Notes
- All signals use current bar's close (EMA is computed on close prices)
- EMAs are exponential moving averages (ewm, adjust=False)
- Volume filter is dynamic: token must pass 30-day trailing avg daily dvol > $5M at the bar
- Funding: longs pay positive funding, shorts collect
- No pyramiding: one position per token at a time
- Partial profit taking reduces position by 50% and tightens trail from 3x to 2x ATR
- Score flip exit: long closed when score goes to -1 or below; short closed when score goes to +1 or above
- First 250 bars skipped for EMA warm-up
