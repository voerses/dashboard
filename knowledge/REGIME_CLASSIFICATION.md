# Market Regime Classification System

Last updated: 2026-04-11

## 6-State Regime Detector

Classifies each day into one of 6 regimes using a priority cascade on causal signals.

### Input Signals (all from `regime_signals.parquet`, daily)
- `btc_ret_30d`: BTC 30-day rolling return
- `btc_above_sma50`: BTC above 350-day (50-week) SMA
- `alt_breadth_50d`: % of alts above their 50-day SMA (0-1)
- `alt_breadth_20d`: % of alts above their 20-day SMA (0-1)
- `alt_btc_spread_30d`: alt index 30d return - BTC 30d return

### Regime Definitions (checked in priority order)

| # | Regime | Conditions | Strategy Action |
|---|--------|-----------|-----------------|
| 1 | **BEAR_CRASH** | BTC below SMA50 AND btc_ret_30d < -10% AND alt_breadth < 25% | Shorts ungated, aggressive DB -10%, suppress longs |
| 2 | **ALT_BLEED** | alt_breadth < 35% AND alt_btc_spread < -5% | Shorts ungated, DB -10%, kill longs |
| 3 | **RECOVERY** | BTC below SMA50 AND btc_ret_30d > 0 AND breadth_20d > breadth_50d | Cautious longs, shorts gated by 30d return |
| 4 | **ALT_SEASON** | alt_breadth > 60% AND spread > +5% AND btc_ret > 0 | Gate shorts, boost longs 1.3x |
| 5 | **BULL_BTC** | BTC above SMA50 AND btc_ret > 0 AND spread < -3% | Selective longs, gate shorts by 45d return |
| 6 | **RANGING** | Default (everything else) | 45d return gate, standard sizing |

### 3-State Simplification

| State | Includes | Short Gate | Deep Bear | Sizing |
|-------|----------|-----------|-----------|--------|
| **RISK_OFF** | BEAR_CRASH + ALT_BLEED | Ungated | -10% | Shorts 1.15-1.3x, longs 0.3-0.5x |
| **NEUTRAL** | RANGING + RECOVERY | 45d/30d gate | -5% | Normal 1.0x |
| **RISK_ON** | ALT_SEASON + BULL_BTC | Fully gated | -3% | Longs 1.15x, shorts 0.85x |

### Validated Forward Returns (2022-2026)

| Regime | % of Days | Fwd 30d Alt Return | Fwd 30d Alt-BTC Spread |
|--------|-----------|-------------------|----------------------|
| BULL_BTC | 12.9% | +21.4% | +11.6% |
| ALT_SEASON | 9.9% | +9.1% | +5.6% |
| RANGING | 40.1% | -0.7% | — |
| RECOVERY | 5.2% | mixed | — |
| ALT_BLEED | 19.6% | negative | negative spread |
| BEAR_CRASH | 12.4% | -4.2% spread | — |

### Detection Method
- All signals are causal (backward-looking only)
- Uses `regime_signals.parquet` pre-computed by `tools/compute_regime_signals.py`
- 14-day smoothing via rolling majority prevents rapid flipping
- ~60 transitions/year raw, ~20 transitions/year with smoothing

### Signal Analysis per Regime (2026Q1 Deep Bear)

From `research/signal_distribution_2026q1.py`:
- **High conviction shorts (>0.66)**: 100% WR (8/8), avg +$1,199
- **Q2 IC (0.403-0.499) shorts**: PF 17.0, avg +$2,352
- **Contrarian token shorts**: PF 3.15 (vs momentum 1.37)
- **ALL longs**: 15.4% WR, -$14K total drag
- **Rising TOTAL2 shorts**: PF 3.43 (best sub-regime)
- **Liquidations**: 9 trades, -$32K (biggest leak)

### Sizing Profile per Regime

Based on signal distribution analysis:

**RISK_OFF (BEAR_CRASH + ALT_BLEED):**
- High conviction shorts: size_multiplier = 1.3
- Low conviction entries: size_multiplier = 0.7
- Contrarian token shorts: size_multiplier *= 1.15
- Momentum token shorts: size_multiplier *= 0.85
- Longs: size_multiplier = 0.3

**NEUTRAL (RANGING + RECOVERY):**
- All entries: size_multiplier = 1.0 (no change)

**RISK_ON (ALT_SEASON + BULL_BTC):**
- Longs: size_multiplier = 1.15
- Shorts: size_multiplier = 0.85

### Implementation
- Research script: `research/regime_multistate.py`
- Signal computation: `tools/compute_regime_signals.py`
- Data: `data/alternative/regime_signals.parquet`
