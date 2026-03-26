# Range Regime Positioning Strategy -- Standalone Backtest Results

Analysis date: 2026-03-26
BTC perp data: 2020-01-01 to 2026-03-17 (2,268 daily bars)
IS/OOS split: 2024-12-31 / 2025-01-01

## Context

- V3 trend-following during RANGE: Sharpe -1.01, -21.4% annualized (finding #68)
- RANGE is ~35% of time -- the biggest drag on V3 returns
- Finding #42 said standalone positioning FAILS with DAILY rebalancing (11%/yr cost drag)
- This test uses WEEKLY rebalancing (findings #44, #47)
- Capital: $200K, position size 50%, fees 4bps + slippage 5bps per side

## Regime Distribution (V4 Causal Detection)

| Regime | Days | Pct |
|--------|------|-----|
| CRISIS (0) | 20 | 0.9% |
| QUIET (1) | 281 | 12.4% |
| UPTREND (2) | 879 | 38.8% |
| RANGE (3) | 467 | 20.6% |
| DOWNTREND (4) | 621 | 27.4% |

**NOTE:** RANGE here is 20.6%, not the ~35% cited in finding #68. The V4 causal regime
detection assigns many bars to DOWNTREND (27.4%) that may have been classified as RANGE
in the earlier analysis. This means the "RANGE drag" may be smaller than originally thought,
or partially captured under DOWNTREND.

## IC Analysis (Range Regime Only)

Spearman rank IC between positioning z-scores and forward BTC returns, computed only during
RANGE regime bars (n=325 days with valid signals).

| Signal | 7d IC | 7d t-stat | 7d p-value | 14d IC | 14d t-stat | 14d p-value |
|--------|-------|-----------|------------|--------|------------|-------------|
| top_trader_ls_z | -0.128 | -2.32 | 0.021 | -0.088 | -1.58 | 0.115 |
| divergence_z | +0.100 | +1.80 | 0.073 | +0.206 | +3.75 | 0.0002 |
| combined_z | -0.026 | -0.47 | 0.639 | +0.063 | +1.13 | 0.258 |

**Key observations:**
- **top_trader_ls_z** has significant negative IC at 7d (-0.128, p=0.021): when top traders
  are extremely long, BTC tends to fall over the next week. Contrarian signal WORKS.
- **divergence_z** has strong positive IC at 14d (+0.206, p=0.0002): when top traders diverge
  from retail (top > retail), BTC rises 14d later. This is NOT contrarian -- it is momentum
  (follow the smart money). The signal construction as "short when divergence is high" is
  inverted from what the IC says works.
- **combined_z** cancels out because the two signals point in opposite directions.

## Signal Diagnostics (Range Regime Only)

| Signal | Mean | Std | SHORT signals (z>1.5) | LONG signals (z<-1.5) | FLAT (abs(z)<0.5) |
|--------|------|-----|-----------------------|-----------------------|-------------------|
| top_trader_ls_z | -0.324 | 1.019 | 9 (2.7%) | 40 (12.2%) | 96 (29.2%) |
| divergence_z | -0.115 | 1.080 | 16 (4.9%) | 45 (13.7%) | 91 (27.7%) |
| combined_z | -0.220 | 0.749 | 1 (0.3%) | 14 (4.3%) | 160 (48.6%) |

The z=1.5 threshold produces very few signal days. During RANGE, the top_trader_ls_z
has a negative mean (-0.324), meaning top traders are biased slightly short during
range regimes on average. This biases toward LONG signals (40 days) vs SHORT (9 days).

## Strategy Results (Range Regime Only)

| Strategy | Range Ann Ret | Range Sharpe | Range MaxDD | Trades | Win Rate | Avg Hold | Cost/yr |
|----------|---------------|--------------|-------------|--------|----------|----------|---------|
| V3 BuyHold (Range) | +4.83% | 1.089 | -21.34% | --- | --- | --- | --- |
| A: Positioning Contrarian | +0.24% | 0.170 | -10.62% | 10 | 60.0% | 4.4d | 0.08% |
| B: L/S Divergence | +0.48% | 0.352 | -7.54% | 11 | 45.5% | 6.0d | 0.08% |
| C: Combined | +0.34% | 0.616 | -2.31% | 2 | 50.0% | 5.5d | 0.01% |
| D: Contrarian + VRP Filter | +0.85% | 0.761 | -0.80% | 2 | 100.0% | 2.0d | 0.01% |

**IMPORTANT: V3 BuyHold during RANGE shows +4.83% annualized, Sharpe 1.089.** This contradicts
finding #68 (Sharpe -1.01). The discrepancy likely arises because:
1. V3 actively trades (gets whipsawed), it does not just hold
2. The V4 regime detection may classify some V3-painful bars as DOWNTREND instead of RANGE
3. A passive long during RANGE is not harmful -- the active trend-following signals that
   generate false entries/exits are what cause losses

**All positioning strategies underperform passive RANGE buy-and-hold.**

## IS/OOS Breakdown

| Strategy | IS Range Sharpe | OOS Range Sharpe | IS Range Ann Ret | OOS Range Ann Ret |
|----------|-----------------|------------------|------------------|-------------------|
| A: Positioning Contrarian | +0.778 | -2.104 | +1.39% | -4.38% |
| B: L/S Divergence | +0.844 | -0.627 | +1.02% | -1.72% |
| C: Combined | +1.608 | -1.819 | +0.78% | -1.50% |
| D: Contrarian + VRP Filter | +0.889 | +0.000 | +1.06% | +0.00% |

**CRITICAL FINDING: Zero IS/OOS consistency.** Every strategy that is positive in IS turns
negative in OOS. Strategy D shows IS=+0.889, OOS=0.000 (no OOS trades due to VRP filter
never triggering during OOS RANGE periods).

## Year-by-Year Range Returns

### Strategy A: Positioning Contrarian
| Year | Range Return | Range Sharpe | Range Days |
|------|-------------|--------------|------------|
| 2020 | +1.60% | +2.56 | 118 |
| 2021 | +9.17% | +2.44 | 77 |
| 2022 | +0.00% | 0.00 | 42 |
| 2023 | -0.04% | -2.82 | 46 |
| 2024 | -3.36% | -1.51 | 59 |
| 2025 | -5.27% | -2.18 | 117 |

### Strategy B: L/S Divergence
| Year | Range Return | Range Sharpe | Range Days |
|------|-------------|--------------|------------|
| 2020 | +0.00% | 0.00 | 118 |
| 2021 | +8.73% | +3.04 | 77 |
| 2022 | +0.00% | 0.00 | 42 |
| 2023 | -1.11% | -3.73 | 46 |
| 2024 | -2.16% | -3.07 | 59 |
| 2025 | -2.07% | -0.65 | 117 |

**Pattern:** Both strategies profit in 2020-2021 (early data) and lose in 2023-2025 (recent).
This is regime decay, not edge.

## Trade Logs

### Strategy A: Positioning Contrarian (10 trades)
| Entry | Exit | Dir | Entry Price | Exit Price | PnL% | Reason |
|-------|------|-----|-------------|------------|------|--------|
| 2020-12-12 | 2020-12-16 | LONG | $18,800 | $21,347 | +13.55% | regime_change |
| 2021-03-04 | 2021-03-08 | LONG | $48,386 | $52,429 | +8.36% | signal_change |
| 2021-05-10 | 2021-05-13 | SHORT | $55,870 | $49,708 | +11.03% | regime_change |
| 2021-06-17 | 2021-06-20 | LONG | $38,061 | $35,589 | -6.50% | regime_change |
| 2021-12-27 | 2021-12-29 | SHORT | $50,708 | $46,437 | +8.42% | regime_change |
| 2023-11-30 | 2023-12-01 | LONG | $37,717 | $38,660 | +2.50% | regime_change |
| 2024-09-14 | 2024-09-20 | LONG | $59,964 | $63,176 | +5.36% | regime_change |
| 2024-11-04 | 2024-11-07 | SHORT | $67,834 | $75,859 | -11.83% | regime_change |
| 2025-03-28 | 2025-04-07 | LONG | $84,381 | $79,140 | -6.21% | regime_change |
| 2025-04-14 | 2025-04-22 | SHORT | $84,554 | $93,405 | -10.47% | regime_change |

### Strategy B: L/S Divergence (11 trades)
| Entry | Exit | Dir | Entry Price | Exit Price | PnL% | Reason |
|-------|------|-----|-------------|------------|------|--------|
| 2021-01-18 | 2021-01-21 | LONG | $36,687 | $30,880 | -15.83% | regime_change |
| 2021-03-04 | 2021-03-08 | LONG | $48,386 | $52,429 | +8.36% | signal_change |
| 2021-03-22 | 2021-04-01 | LONG | $54,130 | $58,794 | +8.62% | regime_change |
| 2021-11-01 | 2021-11-08 | LONG | $60,943 | $67,607 | +10.93% | regime_change |
| 2023-05-08 | 2023-05-11 | LONG | $27,660 | $26,956 | -2.54% | regime_change |
| 2023-06-05 | 2023-06-06 | LONG | $25,715 | $27,222 | +5.86% | regime_change |
| 2023-09-18 | 2023-09-20 | SHORT | $26,750 | $27,108 | -1.34% | regime_change |
| 2024-01-16 | 2024-01-19 | LONG | $43,133 | $41,662 | -3.41% | regime_change |
| 2025-01-13 | 2025-02-03 | LONG | $94,485 | $101,294 | +7.21% | regime_change |
| 2025-04-14 | 2025-04-22 | SHORT | $84,554 | $93,405 | -10.47% | regime_change |
| 2025-06-02 | 2025-06-06 | LONG | $105,814 | $104,240 | -1.49% | regime_change |

### Strategy C: Combined (2 trades)
| Entry | Exit | Dir | Entry Price | Exit Price | PnL% | Reason |
|-------|------|-----|-------------|------------|------|--------|
| 2021-03-04 | 2021-03-08 | LONG | $48,386 | $52,429 | +8.36% | signal_change |
| 2025-04-14 | 2025-04-21 | SHORT | $84,554 | $87,466 | -3.44% | signal_change |

### Strategy D: Contrarian + VRP Filter (2 trades)
| Entry | Exit | Dir | Entry Price | Exit Price | PnL% | Reason |
|-------|------|-----|-------------|------------|------|--------|
| 2021-05-10 | 2021-05-13 | SHORT | $55,870 | $49,708 | +11.03% | regime_change |
| 2023-11-30 | 2023-12-01 | LONG | $37,717 | $38,660 | +2.50% | regime_change |

## Comparison with Finding #42 (Daily Rebalance Cost Drag)

Finding #42 concluded standalone positioning FAILS with daily rebalancing due to 11%/yr cost drag.
Weekly rebalancing essentially eliminates cost drag:

| Strategy | Weekly Cost Drag/yr | vs Daily (~11%/yr) |
|----------|--------------------|--------------------|
| A: Positioning Contrarian | 0.08%/yr | 99.3% reduction |
| B: L/S Divergence | 0.08%/yr | 99.3% reduction |
| C: Combined | 0.01%/yr | 99.9% reduction |
| D: Contrarian + VRP Filter | 0.01%/yr | 99.9% reduction |

**Cost drag is eliminated, but the strategies still fail.** The problem was never just cost --
the underlying signal does not generate consistent standalone alpha during RANGE.

## Key Findings

### Finding 1: IC exists but does not translate to tradeable alpha
- top_trader_ls_z has significant IC=-0.128 at 7d during RANGE (p=0.021)
- divergence_z has strong IC=+0.206 at 14d during RANGE (p=0.0002)
- But the contrarian framework (short when z high, long when z low) only captures the
  top_trader_ls_z IC, and the strict z>1.5 threshold produces too few signals (9 SHORT days
  in 467 RANGE days) to build a tradeable strategy

### Finding 2: Divergence signal is misspecified
- divergence_z (top trader minus retail) has POSITIVE IC (+0.206), meaning when top traders
  are more long than retail, BTC RISES
- The strategy treats high divergence z as a SHORT signal (contrarian), but the IC says it
  should be a LONG signal (follow smart money)
- This explains why Strategy B underperforms despite divergence having the strongest IC

### Finding 3: IS/OOS breakdown confirms no edge
- ALL strategies: positive IS Sharpe, negative or zero OOS Sharpe
- Year-by-year: profitable only in 2020-2021, unprofitable 2023-2025
- This pattern is consistent with overfitting to early bull market conditions
- 2020-2021 (bull market + high vol) created favorable conditions for positioning signals
  that do not generalize to more recent RANGE regimes

### Finding 4: V3 RANGE drag may be overstated
- V4 causal regime detects RANGE as 20.6% of time (not ~35%)
- Passive buy-and-hold during RANGE: Sharpe +1.089, +4.83% annualized
- The "RANGE drag" comes from V3's active trend-following generating false signals,
  not from the market regime itself being hostile to long exposure
- This means the correct fix for RANGE drag is to REDUCE trading activity (go flat),
  not to trade a separate positioning strategy

### Finding 5: Trade count is too low for statistical confidence
- Strategy A: 10 trades in 5.5 years (no statistical significance)
- Strategy B: 11 trades in 5.5 years
- Strategies C and D: only 2 trades each
- Any Sharpe or win rate from 2-10 trades is noise, not signal

## Verdict: FAIL

**Standalone positioning strategies during RANGE regime cannot generate consistent positive
returns.** Despite weekly rebalancing solving the cost drag problem (from 11%/yr to <0.1%/yr),
the strategies fail because:

1. The underlying contrarian IC (-0.128) produces too few signal days at strict thresholds
2. The divergence signal is misspecified (should be momentum, not contrarian)
3. IS/OOS consistency is zero -- all strategies decay or reverse out of sample
4. Trade counts (2-11) are far too low for statistical reliability

**Implication for V3/V4 portfolio:**
The best approach to RANGE drag is NOT a standalone positioning strategy. Instead:
- **Use positioning as an OVERLAY** to reduce V3 trend-following position sizes during RANGE
  (finding #49: reduces losses from -21.4% to -13.2%)
- **Go flat during RANGE** (avoids false trend signals entirely)
- **Do not attempt standalone positioning alpha** during RANGE -- the signal density is too low

## Methodology
- **Regime detection**: V4 causal regime (ADX + expanding vol percentiles, no look-ahead)
- **Positioning data**: Binance daily top trader position L/S, global account L/S (2020-09 to 2026-03)
- **Z-score**: 30-day rolling z-score of positioning metrics
- **Entry**: z > 1.5 (crowd long) -> SHORT, z < -1.5 (crowd short) -> LONG
- **Exit**: z returns to within +/-0.5, or regime changes from RANGE
- **Rebalance**: Weekly (Monday close)
- **Position size**: 50% of equity, no leverage
- **Costs**: 4bps fees + 5bps slippage per side (9bps one-way)
- **IS/OOS**: 2020-09 to 2024-12 / 2025-01 to latest
