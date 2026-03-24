# Regime-Split Signal Analysis

**Generated**: 2026-03-24 10:52
**OOS split**: Train <2025-01-01 | Test >=2025-01-01

## Regime Distribution

| Regime | Train Days | Train % | Test Days | Test % | All Days | All % |
|--------|-----------|---------|-----------|--------|----------|-------|
| UPTREND | 789 | 43.2% | 82 | 18.6% | 871 | 38.4% |
| DOWNTREND | 353 | 19.3% | 126 | 28.6% | 479 | 21.1% |
| RANGE | 599 | 32.8% | 216 | 49.0% | 815 | 35.9% |
| CRISIS | 86 | 4.7% | 17 | 3.9% | 103 | 4.5% |

## Signal Classification (Train, 7d horizon)

| Signal | Classification | Significant In | Details |
|--------|---------------|----------------|---------|
| US10Y+DXY Regime | **CRISIS-ALPHA (VALUABLE)** | DOWNTREND | UPT:-0.007 | DOW:-0.107* | RAN:-0.046 | CRI:+0.004 |
| Top Trader L/S Raw | **REGIME-SWITCHED** | RANGE | UPT:-0.041 | DOW:-0.001 | RAN:-0.288* | CRI:-0.222 |
| L/S Divergence | **BULL-ONLY (DANGEROUS)** | UPTREND | UPT:-0.139* | DOW:+0.018 | RAN:-0.026 | CRI:-0.130 |
| Skew 30d | **BULL-ONLY (DANGEROUS)** | UPTREND | UPT:+0.059* | DOW:-0.022 | RAN:+0.058 | CRI:-0.115 |
| Oil 20d Momentum | **CRISIS-ALPHA (VALUABLE)** | DOWNTREND, CRISIS | UPT:-0.004 | DOW:-0.307* | RAN:+0.026 | CRI:-0.297* |

## Detailed IC: TRAIN

### 7d Forward Return

| Signal | Regime | IC | t-stat | N | Direction |
|--------|--------|---:|-------:|--:|-----------|
| US10Y+DXY Regime | UPTREND | -0.0066 | -0.18 | 782 | FLAT |
| US10Y+DXY Regime | DOWNTREND |  **-0.1066** |  **-2.01** | 353 | SHORT |
| US10Y+DXY Regime | RANGE | -0.0462 | -1.07 | 539 | SHORT |
| US10Y+DXY Regime | CRISIS | 0.0036 | 0.03 | 74 | FLAT |
| US10Y+DXY Regime | ALL |  **-0.0360** |  **-1.51** | 1748 | SHORT |
| Top Trader L/S Raw | UPTREND | -0.0406 | -1.05 | 669 | SHORT |
| Top Trader L/S Raw | DOWNTREND | -0.0008 | -0.01 | 180 | FLAT |
| Top Trader L/S Raw | RANGE |  **-0.2882** |  **-5.87** | 382 | SHORT |
| Top Trader L/S Raw | CRISIS | -0.2217 | -1.34 | 37 | SHORT |
| Top Trader L/S Raw | ALL |  **-0.0721** |  **-2.57** | 1268 | SHORT |
| L/S Divergence | UPTREND |  **-0.1386** |  **-3.61** | 669 | SHORT |
| L/S Divergence | DOWNTREND | 0.0183 | 0.24 | 180 | FLAT |
| L/S Divergence | RANGE | -0.0261 | -0.51 | 382 | SHORT |
| L/S Divergence | CRISIS | -0.1304 | -0.78 | 37 | SHORT |
| L/S Divergence | ALL |  **-0.0802** |  **-2.86** | 1268 | SHORT |
| Skew 30d | UPTREND |  **0.0589** |  **1.65** | 789 | LONG |
| Skew 30d | DOWNTREND | -0.0219 | -0.41 | 353 | SHORT |
| Skew 30d | RANGE | 0.0578 | 1.38 | 569 | LONG |
| Skew 30d | CRISIS | -0.1148 | -1.06 | 86 | SHORT |
| Skew 30d | ALL |  **0.0649** |  **2.76** | 1797 | LONG |
| Oil 20d Momentum | UPTREND | -0.0038 | -0.11 | 789 | FLAT |
| Oil 20d Momentum | DOWNTREND |  **-0.3071** |  **-6.05** | 353 | SHORT |
| Oil 20d Momentum | RANGE | 0.0261 | 0.63 | 579 | LONG |
| Oil 20d Momentum | CRISIS |  **-0.2973** |  **-2.85** | 86 | SHORT |
| Oil 20d Momentum | ALL |  **-0.0690** |  **-2.94** | 1807 | SHORT |

### 14d Forward Return

| Signal | Regime | IC | t-stat | N | Direction |
|--------|--------|---:|-------:|--:|-----------|
| US10Y+DXY Regime | UPTREND | 0.0182 | 0.51 | 782 | FLAT |
| US10Y+DXY Regime | DOWNTREND |  **-0.1329** |  **-2.51** | 353 | SHORT |
| US10Y+DXY Regime | RANGE | 0.0071 | 0.16 | 539 | FLAT |
| US10Y+DXY Regime | CRISIS | -0.0541 | -0.46 | 74 | SHORT |
| US10Y+DXY Regime | ALL | -0.0098 | -0.41 | 1748 | FLAT |
| Top Trader L/S Raw | UPTREND |  **-0.0656** |  **-1.70** | 669 | SHORT |
| Top Trader L/S Raw | DOWNTREND | -0.0307 | -0.41 | 180 | SHORT |
| Top Trader L/S Raw | RANGE |  **-0.3744** |  **-7.87** | 382 | SHORT |
| Top Trader L/S Raw | CRISIS | -0.0953 | -0.57 | 37 | SHORT |
| Top Trader L/S Raw | ALL |  **-0.0994** |  **-3.55** | 1268 | SHORT |
| L/S Divergence | UPTREND |  **-0.1569** |  **-4.10** | 669 | SHORT |
| L/S Divergence | DOWNTREND | -0.0420 | -0.56 | 180 | SHORT |
| L/S Divergence | RANGE |  **-0.1132** |  **-2.22** | 382 | SHORT |
| L/S Divergence | CRISIS | -0.0820 | -0.49 | 37 | SHORT |
| L/S Divergence | ALL |  **-0.1187** |  **-4.25** | 1268 | SHORT |
| Skew 30d | UPTREND | 0.0451 | 1.27 | 789 | LONG |
| Skew 30d | DOWNTREND | 0.0284 | 0.53 | 353 | LONG |
| Skew 30d | RANGE | 0.0210 | 0.50 | 569 | LONG |
| Skew 30d | CRISIS | -0.0289 | -0.26 | 86 | SHORT |
| Skew 30d | ALL |  **0.0564** |  **2.40** | 1797 | LONG |
| Oil 20d Momentum | UPTREND | -0.0437 | -1.23 | 789 | SHORT |
| Oil 20d Momentum | DOWNTREND |  **-0.4028** |  **-8.24** | 353 | SHORT |
| Oil 20d Momentum | RANGE |  **0.1170** |  **2.83** | 579 | LONG |
| Oil 20d Momentum | CRISIS |  **-0.5112** |  **-5.45** | 86 | SHORT |
| Oil 20d Momentum | ALL |  **-0.0820** |  **-3.50** | 1807 | SHORT |

## Detailed IC: TEST

### 7d Forward Return

| Signal | Regime | IC | t-stat | N | Direction |
|--------|--------|---:|-------:|--:|-----------|
| US10Y+DXY Regime | UPTREND | -0.0694 | -0.62 | 82 | SHORT |
| US10Y+DXY Regime | DOWNTREND |  **-0.1776** |  **-1.95** | 119 | SHORT |
| US10Y+DXY Regime | RANGE | -0.0228 | -0.33 | 216 | SHORT |
| US10Y+DXY Regime | CRISIS | — | — | 17 | — |
| US10Y+DXY Regime | ALL |  **-0.0736** |  **-1.53** | 434 | SHORT |
| Top Trader L/S Raw | UPTREND |  **-0.4744** |  **-4.82** | 82 | SHORT |
| Top Trader L/S Raw | DOWNTREND | -0.0717 | -0.78 | 119 | SHORT |
| Top Trader L/S Raw | RANGE |  **-0.2004** |  **-2.99** | 216 | SHORT |
| Top Trader L/S Raw | CRISIS | — | — | 17 | — |
| Top Trader L/S Raw | ALL |  **-0.1683** |  **-3.55** | 434 | SHORT |
| L/S Divergence | UPTREND |  **-0.4031** |  **-3.94** | 82 | SHORT |
| L/S Divergence | DOWNTREND | -0.0261 | -0.28 | 119 | SHORT |
| L/S Divergence | RANGE |  **-0.3456** |  **-5.39** | 216 | SHORT |
| L/S Divergence | CRISIS | — | — | 17 | — |
| L/S Divergence | ALL |  **-0.2056** |  **-4.37** | 434 | SHORT |
| Skew 30d | UPTREND | 0.1209 | 1.09 | 82 | LONG |
| Skew 30d | DOWNTREND |  **0.1514** |  **1.66** | 119 | LONG |
| Skew 30d | RANGE | 0.0018 | 0.03 | 216 | FLAT |
| Skew 30d | CRISIS | — | — | 17 | — |
| Skew 30d | ALL | 0.0718 | 1.50 | 434 | LONG |
| Oil 20d Momentum | UPTREND |  **-0.3594** |  **-3.44** | 82 | SHORT |
| Oil 20d Momentum | DOWNTREND |  **-0.1907** |  **-2.10** | 119 | SHORT |
| Oil 20d Momentum | RANGE | -0.0520 | -0.76 | 216 | SHORT |
| Oil 20d Momentum | CRISIS | — | — | 17 | — |
| Oil 20d Momentum | ALL |  **-0.1339** |  **-2.81** | 434 | SHORT |

### 14d Forward Return

| Signal | Regime | IC | t-stat | N | Direction |
|--------|--------|---:|-------:|--:|-----------|
| US10Y+DXY Regime | UPTREND |  **-0.1848** |  **-1.68** | 82 | SHORT |
| US10Y+DXY Regime | DOWNTREND | -0.1245 | -1.32 | 112 | SHORT |
| US10Y+DXY Regime | RANGE |  **-0.1133** |  **-1.67** | 216 | SHORT |
| US10Y+DXY Regime | CRISIS | — | — | 17 | — |
| US10Y+DXY Regime | ALL |  **-0.1172** |  **-2.43** | 427 | SHORT |
| Top Trader L/S Raw | UPTREND |  **-0.5550** |  **-5.97** | 82 | SHORT |
| Top Trader L/S Raw | DOWNTREND | -0.0535 | -0.56 | 112 | SHORT |
| Top Trader L/S Raw | RANGE |  **-0.2456** |  **-3.71** | 216 | SHORT |
| Top Trader L/S Raw | CRISIS | — | — | 17 | — |
| Top Trader L/S Raw | ALL |  **-0.2042** |  **-4.30** | 427 | SHORT |
| L/S Divergence | UPTREND |  **-0.4982** |  **-5.14** | 82 | SHORT |
| L/S Divergence | DOWNTREND | -0.0021 | -0.02 | 112 | FLAT |
| L/S Divergence | RANGE |  **-0.4135** |  **-6.64** | 216 | SHORT |
| L/S Divergence | CRISIS | — | — | 17 | — |
| L/S Divergence | ALL |  **-0.2303** |  **-4.88** | 427 | SHORT |
| Skew 30d | UPTREND | -0.1304 | -1.18 | 82 | SHORT |
| Skew 30d | DOWNTREND |  **0.3348** |  **3.73** | 112 | LONG |
| Skew 30d | RANGE | 0.0026 | 0.04 | 216 | FLAT |
| Skew 30d | CRISIS | — | — | 17 | — |
| Skew 30d | ALL |  **0.0839** |  **1.74** | 427 | LONG |
| Oil 20d Momentum | UPTREND |  **-0.2395** |  **-2.21** | 82 | SHORT |
| Oil 20d Momentum | DOWNTREND |  **-0.3253** |  **-3.61** | 112 | SHORT |
| Oil 20d Momentum | RANGE | -0.0987 | -1.45 | 216 | SHORT |
| Oil 20d Momentum | CRISIS | — | — | 17 | — |
| Oil 20d Momentum | ALL |  **-0.1634** |  **-3.41** | 427 | SHORT |

## OOS Validation: Classification Stability

| Signal | Train Class. | Test Class. | Stable? |
|--------|-------------|------------|---------|
| US10Y+DXY Regime | CRISIS-ALPHA | CRISIS-ALPHA | YES |
| Top Trader L/S Raw | REGIME-SWITCHED | REGIME-SWITCHED | YES |
| L/S Divergence | BULL-ONLY | REGIME-SWITCHED | NO - REGIME SHIFT |
| Skew 30d | BULL-ONLY | CRISIS-ALPHA | NO - REGIME SHIFT |
| Oil 20d Momentum | CRISIS-ALPHA | REGIME-SWITCHED | NO - REGIME SHIFT |

## Sharpe Improvement from Regime Switching

For regime-switched signals: compare always-on vs regime-filtered trading.

| Signal | Period | Sharpe (Always-On) | Sharpe (Regime-Switched) | Improvement | % Time Traded |
|--------|--------|-------------------:|------------------------:|------------:|--------------:|
| US10Y+DXY Regime | TRAIN | 1.036 | 0.550 | -0.486 (-46.9%) | 20.2% |
| US10Y+DXY Regime | TEST | -0.181 | 0.414 | +0.595 (+328.0%) | 27.4% |
| Top Trader L/S Raw | TRAIN | 1.103 | 1.035 | -0.068 (-6.2%) | 30.1% |
| Top Trader L/S Raw | TEST | 0.487 | 0.085 | -0.402 (-82.5%) | 49.8% |
| L/S Divergence | TRAIN | 0.766 | 0.867 | +0.100 (+13.1%) | 52.8% |
| L/S Divergence | TEST | 1.113 | 0.870 | -0.242 (-21.8%) | 18.9% |
| Skew 30d | TRAIN | 1.272 | 1.292 | +0.020 (+1.6%) | 43.9% |
| Skew 30d | TEST | 0.374 | 0.478 | +0.103 (+27.7%) | 18.9% |
| Oil 20d Momentum | TRAIN | 0.925 | 0.723 | -0.203 (-21.9%) | 24.3% |
| Oil 20d Momentum | TEST | 0.013 | 0.399 | +0.386 (+3032.7%) | 31.3% |

## Key Findings & Recommendations

### REGIME-SWITCHED Signals (need regime overlay)
- **Top Trader L/S Raw**: ON in [RANGE], OFF in [UPTREND, DOWNTREND, CRISIS]. Sharpe improvement from switching: -0.068

### BULL-ONLY Signals (DANGEROUS - flag for review)
- **L/S Divergence**: Only significant in UPTREND. Will give false signals in bear markets. Either add regime gate or remove from signal stack.
- **Skew 30d**: Only significant in UPTREND. Will give false signals in bear markets. Either add regime gate or remove from signal stack.

### CRISIS-ALPHA Signals (valuable for hedging)
- **US10Y+DXY Regime**: Works in CRISIS/DOWNTREND. Activate during stress periods for tail hedging.
- **Oil 20d Momentum**: Works in CRISIS/DOWNTREND. Activate during stress periods for tail hedging.

## Action Items

1. **Always-on signals**: Deploy without regime filter
2. **Regime-switched signals**: Implement BTC 50d return regime detector; gate signal ON/OFF by regime
3. **Bull-only signals**: Add mandatory regime gate or remove from production signal set
4. **Crisis-alpha signals**: Keep as overlay; activate when crisis detector fires
5. **Insignificant signals**: Review for data quality issues or drop from stack
