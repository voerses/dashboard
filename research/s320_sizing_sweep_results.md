# s320 Sizing Override Sweep Results

**Date:** 2026-03-26
**Capital:** $200,000
**Periods tested:** 60mo, 12mo, 24mo

## Problem Statement

s320 is a BTC-only V3 spot strategy (EMA 20/50 + Positioning + VRP overlay, binary gates).
The V4 engine's default ADV-to-sizing curve capped BTC position at ~12% of equity
(cap_pct=0.12, concentration_limit=0.10), leaving 88% of capital idle.

Today, `cap_pct_floor`, `cap_pct_range`, `kelly_mult_floor`, `kelly_mult_range`,
and `adv_scaling_divisor` became per-strategy overridable via `sizing_overrides`.

## Binding Constraint Analysis

Position size = min(raw, cap, adv_cap, spot_cap, max_trade_pct)

For BTC with ADV ~ $1.5-2.0B:
- `raw = equity * kelly_mult * edge * size_mult * (target_vol / volatility)`
- `cap = equity * cap_pct * cap_multiplier` where cap_pct saturates at curve max (~0.12) for BTC's enormous ADV
- With cap_multiplier=8.0 from strategy: cap = equity * 0.12 * 8.0 = 96% of equity
- `raw` with kelly=0.50, edge=0.40, target_vol=0.02, vol~0.005: raw = equity * 0.20 * 4.0 = 80% of equity

**The binding constraint is `raw` (Kelly), NOT `cap`.** Configs A-C only relax the non-binding
`cap_pct` parameters and produce identical results to the baseline. To increase actual position
size, `target_vol` (which drives `vol_adj`) must be increased. Configs E-H test this.

## Configurations

### A: Cap Pct Conservative
- cap_pct_floor=0.08, cap_pct_range=0.22, kelly_mult=0.50, spot_max_equity=0.95, concentration=1.0
- Overrides: `{'cap_pct_floor': 0.08, 'cap_pct_range': 0.22, 'kelly_mult_override': 0.5, 'spot_max_equity_pct': 0.95}`

### B: Cap Pct Aggressive
- cap_pct_floor=0.08, cap_pct_range=0.30, kelly_mult=0.50, adv_scaling_div=2.0, spot_max_equity=0.95, concentration=1.0
- Overrides: `{'cap_pct_floor': 0.08, 'cap_pct_range': 0.3, 'kelly_mult_override': 0.5, 'adv_scaling_divisor': 2.0, 'spot_max_equity_pct': 0.95}`

### C: Cap Pct Override Max
- cap_pct_override=0.15, kelly_mult=0.50, spot_max_equity=0.95, concentration=1.0, cap_multiplier=8.0 (from strategy)
- Overrides: `{'cap_pct_override': 0.15, 'kelly_mult_override': 0.5, 'spot_max_equity_pct': 0.95}`

### D: Baseline (Current Prod)
- kelly_mult_override=0.50, spot_max_equity=0.95 (current multi_v4_paper.json config)
- Overrides: `{'kelly_mult_override': 0.5, 'spot_max_equity_pct': 0.95}`

### E: TargetVol 3% (1.5x vol_adj)
- target_vol=0.03, kelly_mult=0.50, spot_max_equity=0.95, concentration=1.0 — increases vol_adj from 1.0 to 1.5x
- Overrides: `{'kelly_mult_override': 0.5, 'target_vol': 0.03, 'spot_max_equity_pct': 0.95}`

### F: TargetVol 5% (2.5x vol_adj)
- target_vol=0.05, kelly_mult=0.50, spot_max_equity=0.95, concentration=1.0 — increases vol_adj up to 2.5x
- Overrides: `{'kelly_mult_override': 0.5, 'target_vol': 0.05, 'spot_max_equity_pct': 0.95}`

### G: No Kelly Override (ADV curve)
- No kelly_mult_override (use ADV curve: ~0.50 for BTC), spot_max_equity=0.95 — tests if ADV curve alone is sufficient
- Overrides: `{'spot_max_equity_pct': 0.95}`

### H: Full Unlock (max everything)
- target_vol=0.05, kelly_mult=0.50, cap_pct_floor=0.08, cap_pct_range=0.30, spot_max_equity=0.95 — maximum allocation
- Overrides: `{'kelly_mult_override': 0.5, 'target_vol': 0.05, 'cap_pct_floor': 0.08, 'cap_pct_range': 0.3, 'spot_max_equity_pct': 0.95}`

## Results — 60 Months Lookback

| Config | Ann Return % | Total Return % | MaxDD % | Sharpe | Calmar | Sortino | Trades | Avg Pos % Eq | Cap Util % | Final Equity |
|--------|-------------|---------------|---------|--------|--------|---------|--------|-------------|-----------|-------------|
| A: Cap Pct Conservative | +3.8 | +24.7 | -33.6 | 0.31 | 0.11 | 0.21 | 123 | 49.1 | 39.4 | $249,400 |
| B: Cap Pct Aggressive | +3.8 | +24.7 | -33.6 | 0.31 | 0.11 | 0.21 | 123 | 49.1 | 39.4 | $249,400 |
| C: Cap Pct Override Max | +3.8 | +24.7 | -33.6 | 0.31 | 0.11 | 0.21 | 123 | 49.1 | 39.4 | $249,400 |
| D: Baseline (Current Prod) | +3.8 | +24.7 | -33.6 | 0.31 | 0.11 | 0.21 | 123 | 49.1 | 39.4 | $249,403 |
| E: TargetVol 3% (1.5x vol_adj) | +3.6 | +23.4 | -43.5 | 0.27 | 0.08 | 0.18 | 123 | 63.1 | 39.4 | $246,749 |
| F: TargetVol 5% (2.5x vol_adj) | +3.4 | +21.8 | -50.6 | 0.26 | 0.07 | 0.16 | 123 | 77.2 | 39.4 | $243,625 |
| G: No Kelly Override (ADV curve) | +3.4 | +22.4 | -31.9 | 0.31 | 0.11 | 0.20 | 123 | 43.9 | 39.4 | $244,819 |
| H: Full Unlock (max everything) | +3.9 | +25.5 | -52.6 | 0.28 | 0.07 | 0.18 | 123 | 85.4 | 39.4 | $250,920 |

## Results — 12 Months Lookback

| Config | Ann Return % | Total Return % | MaxDD % | Sharpe | Calmar | Sortino | Trades | Avg Pos % Eq | Cap Util % | Final Equity |
|--------|-------------|---------------|---------|--------|--------|---------|--------|-------------|-----------|-------------|
| A: Cap Pct Conservative | +0.1 | +0.2 | -11.1 | 0.06 | 0.01 | 0.03 | 20 | 59.3 | 19.3 | $200,400 |
| B: Cap Pct Aggressive | +0.1 | +0.2 | -11.1 | 0.06 | 0.01 | 0.03 | 20 | 59.3 | 19.3 | $200,400 |
| C: Cap Pct Override Max | +0.1 | +0.2 | -11.1 | 0.06 | 0.01 | 0.03 | 20 | 59.3 | 19.3 | $200,400 |
| D: Baseline (Current Prod) | +0.1 | +0.2 | -11.1 | 0.06 | 0.01 | 0.03 | 20 | 59.3 | 19.3 | $200,400 |
| E: TargetVol 3% (1.5x vol_adj) | -1.4 | -2.8 | -15.2 | -0.07 | -0.10 | -0.03 | 20 | 72.3 | 19.3 | $194,314 |
| F: TargetVol 5% (2.5x vol_adj) | -2.9 | -5.7 | -18.9 | -0.17 | -0.16 | -0.08 | 20 | 85.0 | 19.3 | $188,510 |
| G: No Kelly Override (ADV curve) | +0.2 | +0.4 | -9.9 | 0.07 | 0.02 | 0.03 | 20 | 52.5 | 19.3 | $200,818 |
| H: Full Unlock (max everything) | -3.6 | -7.1 | -21.0 | -0.19 | -0.17 | -0.09 | 20 | 95.5 | 19.3 | $185,840 |

## Results — 24 Months Lookback

| Config | Ann Return % | Total Return % | MaxDD % | Sharpe | Calmar | Sortino | Trades | Avg Pos % Eq | Cap Util % | Final Equity |
|--------|-------------|---------------|---------|--------|--------|---------|--------|-------------|-----------|-------------|
| A: Cap Pct Conservative | -5.5 | -15.5 | -21.4 | -0.37 | -0.26 | -0.20 | 49 | 46.8 | 31.5 | $169,026 |
| B: Cap Pct Aggressive | -5.5 | -15.5 | -21.4 | -0.37 | -0.26 | -0.20 | 49 | 46.8 | 31.5 | $169,026 |
| C: Cap Pct Override Max | -5.5 | -15.5 | -21.4 | -0.37 | -0.26 | -0.20 | 49 | 46.8 | 31.5 | $169,026 |
| D: Baseline (Current Prod) | -5.5 | -15.5 | -21.4 | -0.37 | -0.26 | -0.20 | 49 | 46.8 | 31.5 | $169,026 |
| E: TargetVol 3% (1.5x vol_adj) | -6.5 | -18.1 | -28.8 | -0.32 | -0.22 | -0.18 | 49 | 59.2 | 31.5 | $163,871 |
| F: TargetVol 5% (2.5x vol_adj) | -5.9 | -16.5 | -32.9 | -0.22 | -0.18 | -0.12 | 49 | 70.4 | 31.5 | $166,954 |
| G: No Kelly Override (ADV curve) | -5.0 | -14.1 | -19.4 | -0.38 | -0.26 | -0.21 | 49 | 42.6 | 31.5 | $171,729 |
| H: Full Unlock (max everything) | -7.0 | -19.3 | -35.6 | -0.24 | -0.20 | -0.13 | 49 | 75.0 | 31.5 | $161,307 |

## Detailed Trade Statistics

| Config | Period | Win Rate % | Profit Factor | Avg PnL | Avg Hold (h) | Avg Pos USD | Rejections | Fees |
|--------|--------|-----------|--------------|---------|-------------|------------|-----------|------|
| A: Cap Pct Conservative | 60mo | 52.0 | 1.21 | $500 | 168 | $98,271 | 950 | $24,248 |
| B: Cap Pct Aggressive | 60mo | 52.0 | 1.21 | $500 | 168 | $98,271 | 950 | $24,248 |
| C: Cap Pct Override Max | 60mo | 52.0 | 1.21 | $500 | 168 | $98,271 | 950 | $24,248 |
| D: Baseline (Current Prod) | 60mo | 52.0 | 1.21 | $500 | 168 | $98,270 | 950 | $24,248 |
| E: TargetVol 3% (1.5x vol_adj) | 60mo | 52.0 | 1.16 | $506 | 168 | $126,184 | 950 | $31,119 |
| F: TargetVol 5% (2.5x vol_adj) | 60mo | 52.0 | 1.13 | $509 | 168 | $154,379 | 950 | $38,059 |
| G: No Kelly Override (ADV curve) | 60mo | 52.0 | 1.21 | $452 | 168 | $87,798 | 950 | $21,665 |
| H: Full Unlock (max everything) | 60mo | 52.0 | 1.13 | $585 | 168 | $170,850 | 950 | $42,122 |
| A: Cap Pct Conservative | 12mo | 50.0 | 1.05 | $139 | 168 | $118,620 | 108 | $4,750 |
| B: Cap Pct Aggressive | 12mo | 50.0 | 1.05 | $139 | 168 | $118,620 | 108 | $4,750 |
| C: Cap Pct Override Max | 12mo | 50.0 | 1.05 | $139 | 168 | $118,620 | 108 | $4,750 |
| D: Baseline (Current Prod) | 12mo | 50.0 | 1.05 | $139 | 168 | $118,620 | 108 | $4,750 |
| E: TargetVol 3% (1.5x vol_adj) | 12mo | 50.0 | 0.96 | $-140 | 168 | $144,644 | 108 | $5,786 |
| F: TargetVol 5% (2.5x vol_adj) | 12mo | 50.0 | 0.89 | $-404 | 168 | $170,065 | 108 | $6,798 |
| G: No Kelly Override (ADV curve) | 12mo | 50.0 | 1.06 | $146 | 168 | $105,002 | 108 | $4,205 |
| H: Full Unlock (max everything) | 12mo | 45.0 | 0.87 | $-517 | 168 | $190,968 | 108 | $7,632 |
| A: Cap Pct Conservative | 24mo | 53.1 | 0.76 | $-539 | 168 | $93,541 | 420 | $9,145 |
| B: Cap Pct Aggressive | 24mo | 53.1 | 0.76 | $-539 | 168 | $93,541 | 420 | $9,145 |
| C: Cap Pct Override Max | 24mo | 53.1 | 0.76 | $-539 | 168 | $93,541 | 420 | $9,145 |
| D: Baseline (Current Prod) | 24mo | 53.1 | 0.76 | $-539 | 168 | $93,541 | 420 | $9,145 |
| E: TargetVol 3% (1.5x vol_adj) | 24mo | 53.1 | 0.78 | $-619 | 168 | $118,415 | 420 | $11,580 |
| F: TargetVol 5% (2.5x vol_adj) | 24mo | 53.1 | 0.83 | $-534 | 168 | $140,780 | 420 | $13,777 |
| G: No Kelly Override (ADV curve) | 24mo | 53.1 | 0.76 | $-492 | 168 | $85,111 | 420 | $8,321 |
| H: Full Unlock (max everything) | 24mo | 53.1 | 0.81 | $-640 | 168 | $149,975 | 420 | $14,674 |

## Improvement Over Baseline (D)

| Config | Period | dReturn (pp) | dSharpe | dMaxDD (pp) | dCalmar | Pos Size Multiplier |
|--------|--------|-------------|---------|------------|---------|--------------------|
| A: Cap Pct Conservative | 60mo | -0.0 | -0.00 | +0.0 | -0.00 | 1.0x |
| B: Cap Pct Aggressive | 60mo | -0.0 | -0.00 | +0.0 | -0.00 | 1.0x |
| C: Cap Pct Override Max | 60mo | -0.0 | -0.00 | +0.0 | -0.00 | 1.0x |
| E: TargetVol 3% (1.5x vol_adj) | 60mo | -0.2 | -0.04 | -9.9 | -0.03 | 1.3x |
| F: TargetVol 5% (2.5x vol_adj) | 60mo | -0.4 | -0.06 | -17.0 | -0.05 | 1.6x |
| G: No Kelly Override (ADV curve) | 60mo | -0.3 | -0.01 | +1.7 | -0.00 | 0.9x |
| H: Full Unlock (max everything) | 60mo | +0.1 | -0.04 | -19.0 | -0.04 | 1.7x |
| A: Cap Pct Conservative | 12mo | +0.0 | +0.00 | +0.0 | +0.00 | 1.0x |
| B: Cap Pct Aggressive | 12mo | +0.0 | +0.00 | +0.0 | +0.00 | 1.0x |
| C: Cap Pct Override Max | 12mo | +0.0 | +0.00 | +0.0 | +0.00 | 1.0x |
| E: TargetVol 3% (1.5x vol_adj) | 12mo | -1.5 | -0.13 | -4.1 | -0.10 | 1.2x |
| F: TargetVol 5% (2.5x vol_adj) | 12mo | -3.0 | -0.23 | -7.8 | -0.16 | 1.4x |
| G: No Kelly Override (ADV curve) | 12mo | +0.1 | +0.01 | +1.2 | +0.01 | 0.9x |
| H: Full Unlock (max everything) | 12mo | -3.7 | -0.25 | -9.9 | -0.18 | 1.6x |
| A: Cap Pct Conservative | 24mo | +0.0 | +0.00 | +0.0 | +0.00 | 1.0x |
| B: Cap Pct Aggressive | 24mo | +0.0 | +0.00 | +0.0 | +0.00 | 1.0x |
| C: Cap Pct Override Max | 24mo | +0.0 | +0.00 | +0.0 | +0.00 | 1.0x |
| E: TargetVol 3% (1.5x vol_adj) | 24mo | -1.0 | +0.05 | -7.4 | +0.03 | 1.3x |
| F: TargetVol 5% (2.5x vol_adj) | 24mo | -0.4 | +0.15 | -11.5 | +0.08 | 1.5x |
| G: No Kelly Override (ADV curve) | 24mo | +0.5 | -0.01 | +1.9 | +0.00 | 0.9x |
| H: Full Unlock (max everything) | 24mo | -1.5 | +0.13 | -14.2 | +0.06 | 1.6x |

## Key Findings

### 60-Month Period
- **Best Sharpe:** D: Baseline (Current Prod) (0.31)
- **Best Return:** H: Full Unlock (max everything) (+3.9%)
- **Best Calmar:** D: Baseline (Current Prod) (0.11)
- **Baseline:** Sharpe=0.31, Return=+3.8%, MaxDD=-33.6%, AvgPos=49.1% equity

### 12-Month Period
- **Best Sharpe:** G: No Kelly Override (ADV curve) (0.07)
- **Best Return:** G: No Kelly Override (ADV curve) (+0.2%)
- **Best Calmar:** G: No Kelly Override (ADV curve) (0.02)
- **Baseline:** Sharpe=0.06, Return=+0.1%, MaxDD=-11.1%, AvgPos=59.3% equity

### 24-Month Period
- **Best Sharpe:** F: TargetVol 5% (2.5x vol_adj) (-0.22)
- **Best Return:** G: No Kelly Override (ADV curve) (-5.0%)
- **Best Calmar:** F: TargetVol 5% (2.5x vol_adj) (-0.18)
- **Baseline:** Sharpe=-0.37, Return=-5.5%, MaxDD=-21.4%, AvgPos=46.8% equity

## Recommendations

### 1. The requested cap_pct overrides (A/B/C) have ZERO effect on s320

Configs A, B, and C produce byte-identical results to the baseline D across all periods.
This is because for BTC (ADV ~ $1.5-2.0B), the ADV-to-sizing curve already saturates
`cap_pct` to its maximum value (0.12). Combined with `cap_multiplier=8.0` from the strategy,
the cap = 96% of equity -- far above the binding constraint (`raw` Kelly size at ~49% of
equity for typical overlays). Relaxing `cap_pct_floor`, `cap_pct_range`, or setting
`cap_pct_override` has no practical effect.

**Conclusion: Do NOT ship A/B/C overrides. They add config complexity for zero benefit.**

### 2. Increasing target_vol (E/F) increases position size but DEGRADES risk-adjusted returns

- Config E (target_vol=0.03): Position size up 28% (49% -> 63% equity), but Sharpe drops
  0.31 -> 0.27 (60mo) and MaxDD worsens -33.6% -> -43.5%.
- Config F (target_vol=0.05): Position size up 57% (49% -> 77% equity), but Sharpe drops
  to 0.26 and MaxDD to -50.6%.
- Config H (full unlock): Position at 85% equity, tiny return boost (+0.1 pp ann) but
  MaxDD blows out to -52.6%.

The additional capital deployed does not generate proportionally more return -- it just
amplifies drawdowns. This is consistent with the strategy's moderate edge (0.40) being
well-sized at the current Kelly fraction.

**Conclusion: Do NOT increase target_vol. The current default (0.02) is correctly calibrated
for s320's edge/volatility profile.**

### 3. The current production config (D) is near-optimal for risk-adjusted performance

- 60mo: Sharpe 0.31, Calmar 0.11, MaxDD -33.6% -- tied for best risk-adjusted with G
- The ADV curve without kelly override (G) produces slightly smaller positions (44% vs 49%)
  with marginally better MaxDD (-31.9% vs -33.6%) and identical Sharpe

**Conclusion: Keep the current config. The kelly_mult_override=0.50 is validated. The idle
capital problem (~50% average, ~60% time-weighted) is inherent to the strategy's weekly
rebalance + overlay structure, not a sizing constraint issue.**

### 4. The real idle capital problem is signal sparsity, not sizing caps

- All configs produce identical trade counts (123 trades / 60mo, 20 trades / 12mo)
- Capital utilization is ~39% (60mo) regardless of position size
- The strategy is flat (size_multiplier=0) ~50% of the time due to EMA crossover +
  crisis regime filter + positioning/VRP overlays

**The path to deploying more capital is not bigger positions but rather combining s320 with
uncorrelated strategies (as in the multi-strategy portfolio pools) or shortening the
rebalance window.**

### 5. If the 24-month drawdown (-21.4%) is a concern

Config G (pure ADV curve, no kelly override) showed -19.4% MaxDD vs -21.4% for the baseline --
a 2 pp improvement from slightly smaller positions. This is a minor improvement and may be
within noise given the small sample (49 trades).

---
*Generated by s320_sizing_sweep.py on 2026-03-26*