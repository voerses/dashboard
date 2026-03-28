# R164 -- THE FINAL Multi-Strategy Portfolio Results

**Generated:** 2026-03-28 18:32
**Period:** 2024-03-17 to 2026-03-17 (~2 years)
**Last 12 Months:** 2025-03-17 to 2026-03-17
**R162 tokens:** 66
**R160 tokens:** 105
**Strategy correlation:** 0.0244

---

## Base Strategy Performance (1x, No Leverage)

| Strategy | Period | Return | Ann Return | Sharpe | MaxDD | Calmar | Sortino |
|----------|--------|--------|------------|--------|-------|--------|---------|
| R162 (L7_K3_80/20_EMA10/30_VF) | Full | +542.2% | +153.4% | 1.39 | -70.7% | 2.17 | 3.04 |
| R162 (L7_K3_80/20_EMA10/30_VF) | 12M | +289.6% | +289.6% | 1.75 | -47.2% | 6.13 | 3.95 |
| R160 Variant B | Full | +768.3% | +41.6% | 0.77 | -25.1% | 1.66 | 0.88 |
| R160 Variant B | 12M | +29.9% | +29.9% | 0.90 | -7.5% | 4.01 | 1.30 |

---

## FINAL PORTFOLIO RECOMMENDATION

| Config | Description | 12M Return | Ann Return | Sharpe | MaxDD | Calmar | Sortino | Avg Leverage |
|--------|-------------|------------|------------|--------|-------|--------|---------|-------------|
| **A** | R162 + MoM Aggressive | **+64.9%** | +64.9% | 1.25 | -78.7% | 0.82 | 2.28 | 1.64x |
| **B** | 70/30 + 1.5x Static | **+346.3%** | +346.3% | 1.83 | -49.3% | 7.03 | 4.14 | 1.50x |
| **C** | 60/40 + MoM Aggressive | **+214.7%** | +214.7% | 1.42 | -53.7% | 4.00 | 2.59 | 1.67x |
| **D** | 50/50 + MoM Aggressive | **+248.9%** | +248.9% | 1.57 | -47.4% | 5.25 | 2.86 | 1.66x |
| **E** | R162 + 1.1x Static | **+321.8%** | +321.8% | 1.75 | -50.7% | 6.34 | 3.94 | 1.10x |
| **F** | R162 + MoM Conservative | **+187.8%** | +187.8% | 1.36 | -60.7% | 3.09 | 2.58 | 1.24x |

### Full Period Metrics

| Config | Description | Full Return | Ann Return | Sharpe | MaxDD | Calmar | Sortino | Avg Leverage |
|--------|-------------|-------------|------------|--------|-------|--------|---------|-------------|
| **A** | R162 + MoM Aggressive | +407.9% | +125.4% | 1.27 | -89.7% | 1.40 | 2.51 | 1.64x |
| **B** | 70/30 + 1.5x Static | +669.7% | +177.4% | 1.45 | -71.8% | 2.47 | 3.19 | 1.50x |
| **C** | 60/40 + MoM Aggressive | +774.8% | +195.8% | 1.37 | -62.2% | 3.15 | 2.71 | 1.67x |
| **D** | 50/50 + MoM Aggressive | +834.9% | +205.8% | 1.48 | -56.6% | 3.64 | 2.98 | 1.66x |
| **E** | R162 + 1.1x Static | +600.5% | +164.7% | 1.38 | -74.5% | 2.21 | 3.03 | 1.10x |
| **F** | R162 + MoM Conservative | +679.8% | +179.3% | 1.31 | -73.4% | 2.44 | 2.69 | 1.24x |

---

## Configuration Descriptions

| Config | Formula |
|--------|---------|
| A | R162 returns -> MoM leverage (>5%:3x, 0-5%:2x, -5-0%:1x, <-5%:0.5x) - 5% borrow |
| B | 0.70*1.5*R162 + 0.30*1.5*R160 - 0.5*5%/365 daily |
| C | (0.60*R162 + 0.40*R160) -> MoM leverage (same thresholds) |
| D | (0.50*R162 + 0.50*R160) -> MoM leverage (same thresholds) |
| E | R162 * 1.1x - 0.1*5%/365 daily |
| F | R162 returns -> Conservative MoM (>5%:2x, 0-5%:1.5x, -5-0%:1x, <-5%:0.5x) - 5% borrow |

---

## Winner: Config B (70/30 + 1.5x Static)

**12M Return:** +346.3%
**Sharpe:** 1.83
**MaxDD:** -49.3%
**Avg Leverage:** 1.50x

### Monthly Returns

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | Annual |
|------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|--------|
| 2024 |  --  |  --  |  --  | -29.5% | -13.4% | -27.3% | -5.2% | -22.4% | +42.6% | +7.3% | +196.9% | +6.8% | +58.3% |
| 2025 | +12.1% | -11.8% | -13.6% | +20.5% | +7.3% | -6.8% | +0.9% | -7.9% | +17.1% | +95.4% | -30.2% | +0.0% | +52.9% |
| 2026 | +207.5% | -14.6% | -1.6% |  --  |  --  |  --  |  --  |  --  |  --  |  --  |  --  |  --  | +158.5% |

### Average Leverage: 1.50x
- Min leverage: 1.5x
- Max leverage: 1.5x
- % time at max leverage: 100.0%
- % time at min leverage: 100.0%

### Worst Month: -30.2% (2025-11)
### Best Month: +207.5% (2026-01)

### Max Consecutive Losing Months: 5

### Quarterly Performance (Last 12 Months)

| Quarter | Return | MaxDD | Sharpe |
|---------|--------|-------|--------|
| Q2 2025 (Mar-Jun) | +19.9% | -19.4% | 1.37 |
| Q3 2025 (Jun-Sep) | +6.0% | -23.0% | 0.67 |
| Q4 2025 (Sep-Dec) | +28.8% | -46.2% | 1.49 |
| Q1 2026 (Dec-Mar) | +169.5% | -32.4% | 3.07 |

---

## Key Insights

1. **Strategy correlation is 0.024** -- R162 momentum and R160 vol breakout are nearly uncorrelated
2. **R162 base at 1x:** 12M = +289.6%, Sharpe = 1.75
3. **R160 base at 1x:** 12M = +29.9%, Sharpe = 0.90
4. **MoM leverage avg 12M return:** +179.1% vs **static leverage:** +334.1%
5. **MoM leverage avg Sharpe:** 1.40 vs **static leverage:** 1.79
6. **Config E (1.1x R162):** 12M = +321.8% -- CLEARS 300% target
