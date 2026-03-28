# R163 -- Adaptive Leverage on Momentum Rotation

**Generated:** 2026-03-28 18:25
**Simulation Period:** 2024-03-17 to 2026-03-17 (~2 years)
**Last 12 Months:** 2025-03-17 to 2026-03-17
**Token Universe:** 66 tokens
**Base Strategy:** R158 L7_R7_K5_70/30_RFY
**Costs:** 7 bps per side + hourly funding + 5% annual borrow cost on leveraged portion
**Look-ahead protection:** All signals shifted by 1 day

---

## Base Strategy (1x, no leverage overlay)

| Metric | Value |
|--------|-------|
| Annual Return | 82.0% |
| Last 12M Return | 163.0% |
| Sharpe | 1.27 |
| MaxDD | -55.2% |
| Calmar | 1.49 |
| Avg Leverage | 1.00x |

---

## All Variants Comparison

| Variant | Description | Ann Ret | 12M Ret | Sharpe | MaxDD | Calmar | Avg Lev | Target Met? |
|---------|-------------|---------|---------|--------|-------|--------|---------|-------------|
| C | C: MoM (14d ret >5%→3x, 0-5%→2x, -5-0%→1x, <-5%→0.5x) | 1820.1% | **2094.5%** | 2.62 | -62.8% | 28.99 | 1.72x | YES |
| E-3.0x | E: Static 3.0x | 96.3% | **382.3%** | 1.22 | -93.4% | 1.03 | 3.00x | RET OK, DD HIGH |
| E-2.5x | E: Static 2.5x | 117.9% | **379.8%** | 1.23 | -89.0% | 1.32 | 2.50x | RET OK, DD HIGH |
| E-2.0x | E: Static 2.0x | 122.9% | **333.4%** | 1.23 | -82.0% | 1.50 | 2.00x | RET OK, DD HIGH |
| E-1.5x | E: Static 1.5x | 110.1% | **254.9%** | 1.25 | -71.3% | 1.54 | 1.50x | NO |
| E-1.0x | E: Static 1.0x | 82.0% | **163.0%** | 1.27 | -55.2% | 1.49 | 1.00x | DD OK, RET LOW |
| B | B: Drawdown (0%→2.5x, 10%→2.0x, 20%→1.5x, 30%→1.0x, >30%→0.5x) | 54.2% | **157.4%** | 0.95 | -47.0% | 1.15 | 0.97x | DD OK, RET LOW |
| A | A: Regime (BTC EMA20>50 → 2.5x, else 1.0x) | 41.1% | **91.6%** | 0.84 | -83.3% | 0.49 | 1.76x | NO |
| D | D: Combined (Regime * DD scale) | 9.0% | **9.2%** | 0.44 | -48.8% | 0.19 | 0.84x | DD OK, RET LOW |

---

## Target Analysis (300%+ 12M Return, MaxDD < 70%)

**1 variant(s) meet the target:**

- **C** (C: MoM (14d ret >5%→3x, 0-5%→2x, -5-0%→1x, <-5%→0.5x)): 12M Return = 2094.5%, MaxDD = -62.8%, Sharpe = 2.62, Avg Leverage = 1.72x

---

## Leverage Usage Analysis

| Variant | Avg Lev | Min Lev | Max Lev | % Days >2x | % Days >2.5x |
|---------|---------|---------|---------|------------|--------------|
| C | 1.72x | 0.5x | 3.0x | 32.6% | 32.6% |
| E-3.0x | 3.00x | 3.0x | 3.0x | 100.0% | 100.0% |
| E-2.5x | 2.50x | 2.5x | 2.5x | 100.0% | 0.0% |
| E-2.0x | 2.00x | 2.0x | 2.0x | 0.0% | 0.0% |
| E-1.5x | 1.50x | 1.5x | 1.5x | 0.0% | 0.0% |
| E-1.0x | 1.00x | 1.0x | 1.0x | 0.0% | 0.0% |
| B | 0.97x | 0.5x | 2.5x | 3.1% | 0.0% |
| A | 1.76x | 1.0x | 2.5x | 50.5% | 0.0% |
| D | 0.84x | 0.2x | 2.5x | 4.7% | 0.0% |

---

## Monthly Returns: Best by 12M Return (C)

**C: MoM (14d ret >5%→3x, 0-5%→2x, -5-0%→1x, <-5%→0.5x)**

| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | Annual |
|------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|-------|--------|
| 2024 |  --  |  --  |  --  | -33.6% | -7.1% | -9.0% | +0.7% | +11.7% | +44.5% | -0.3% | +473.0% | +75.2% | +812.5% |
| 2025 | +38.6% | +5.9% | -10.9% | +46.3% | +0.9% | +135.9% | +29.8% | -13.5% | +44.1% | +41.3% | +9.4% | +16.9% | +1233.0% |
| 2026 | +196.9% | -18.5% | -2.6% |  --  |  --  |  --  |  --  |  --  |  --  |  --  |  --  |  --  | +135.8% |

---

## Static Leverage Scaling Analysis

| Leverage | Ann Ret | 12M Ret | Sharpe | MaxDD | Calmar |
|----------|---------|---------|--------|-------|--------|
| 1.0x | 82.0% | 163.0% | 1.27 | -55.2% | 1.49 |
| 1.5x | 110.1% | 254.9% | 1.25 | -71.3% | 1.54 |
| 2.0x | 122.9% | 333.4% | 1.23 | -82.0% | 1.50 |
| 2.5x | 117.9% | 379.8% | 1.23 | -89.0% | 1.32 |
| 3.0x | 96.3% | 382.3% | 1.22 | -93.4% | 1.03 |

---

## Key Insight

Adaptive leverage aims to be 'in heavy' during favorable conditions 
and 'light' during drawdowns. The advantage over static leverage is 
reducing exposure during the worst periods while maintaining aggressive 
exposure during the best periods.

**Best adaptive (by Calmar):** C — Calmar=28.99, 12M=2094.5%, MaxDD=-62.8%, Avg Lev=1.72x

**Nearest static (1.5x):** Calmar=1.54, 12M=254.9%, MaxDD=-71.3%

Adaptive leverage improves risk-adjusted returns by 1777% (Calmar ratio) vs equivalent static leverage.