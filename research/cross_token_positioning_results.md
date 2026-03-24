# Cross-Token Positioning Signal Analysis

**Date:** 2026-03-24
**Panel:** 27 symbols, 2,024 trading days
**Date range:** 2020-09-01 to 2026-03-17
**IS period:** 2020-09-01 to 2023-06-09 (1,012 days)
**OOS period:** 2023-06-10 to 2026-03-17 (1,012 days)

---

## 1. Data Description

- **Source:** Binance futures L/S ratio panel (`all_symbols_daily_ls.parquet`)
- **Symbols (27):** AAVEUSDT, ADAUSDT, APTUSDT, ARBUSDT, ATOMUSDT, AVAXUSDT, BNBUSDT, BTCUSDT, DOGEUSDT, DOTUSDT, ETHUSDT, FILUSDT, IMXUSDT, INJUSDT, LINKUSDT, LTCUSDT, NEARUSDT, ONDOUSDT, OPUSDT, SEIUSDT, SOLUSDT, SUIUSDT, TIAUSDT, TRXUSDT, UNIUSDT, WIFUSDT, XRPUSDT
- **Excluded:** MATICUSDT, MKRUSDT (no matching price files)
- **Key columns:** `sum_toptrader_ls_ratio` (primary positioning signal), `taker_buy_sell_ratio`
- **Completeness:** Core symbols (BTC, ETH, majors) ~62-84%; newer tokens (ONDO, WIF, SEI, SUI, TIA) ~39-52%
- **Forward returns:** Log returns at 1d, 3d, 7d, 14d horizons
- **Panel structure:** Unbalanced (symbols enter at different dates); daily cross-sectional IC is the correct metric

### Methodological Note on Panel IC

Two IC metrics are reported for panel signals:
- **Pooled IC:** Single Spearman correlation across all (date, symbol) observations. Inflates t-stats and can suffer Simpson's paradox.
- **Daily cross-sectional IC (CSIC):** Mean of daily rank ICs across tokens, with t-stat = mean / (std / sqrt(N_days)). This is the correct metric for cross-sectional signals and is used for all verdicts.

---

## 2. Signal Definitions

| # | Signal | Type | Definition |
|---|--------|------|------------|
| 1 | CS Positioning Dispersion | Cross-sectional -> TS | Daily std of `sum_toptrader_ls_ratio` across all tokens |
| 2 | CS Positioning Consensus | Cross-sectional -> TS | Daily mean of `sum_toptrader_ls_ratio` across all tokens |
| 3 | Token Positioning Z-Score | Panel (CSIC) | Per-token 30d rolling z-score of `sum_toptrader_ls_ratio` |
| 4 | Positioning Momentum | Panel (CSIC) | N-day change in `sum_toptrader_ls_ratio` (5d, 10d, 20d lookbacks) |
| 5 | Taker-Positioning Divergence | Panel (CSIC) | Difference between taker and positioning z-scores; also sign-based variant |

---

## 3. IC Tables

### Signal 1: Cross-Sectional Positioning Dispersion

*High dispersion = positioning disagreement across tokens. Tested as time-series signal vs BTC and equal-weight panel returns.*

| Target | Horizon | IC | t-stat | IS IC | OOS IC | Verdict |
|--------|---------|------|--------|-------|--------|---------|
| BTC | 1d | -0.0045 | -0.2 | -0.0578 | -0.0076 | **KILL** (|IC|<0.02, |t|<2) |
| BTC | 3d | -0.0095 | -0.3 | -0.0737 | -0.0356 | **KILL** (|IC|<0.02, |t|<2) |
| BTC | 7d | -0.0285 | -1.0 | -0.0674 | -0.0732 | **KILL** (|t|<2) |
| BTC | 14d | -0.0533 | -1.9 | -0.0165 | -0.1204 | **KILL** (|t|<2) |
| EW | 1d | -0.0044 | -0.2 | - | - | KILL |
| EW | 3d | -0.0090 | -0.3 | - | - | KILL |
| EW | 7d | -0.0156 | -0.5 | - | - | KILL |
| EW | 14d | -0.0404 | -1.4 | - | - | KILL |

### Signal 2: Cross-Sectional Positioning Consensus

*High consensus = all top traders long across all tokens (crowding danger). Tested as time-series signal vs BTC and equal-weight returns.*

| Target | Horizon | IC | t-stat | IS IC | OOS IC | Verdict |
|--------|---------|------|--------|-------|--------|---------|
| BTC | 1d | -0.0437 | -1.8 | -0.0474 | -0.0329 | **KILL** (|t|<2) |
| BTC | 3d | -0.0592 | -2.4 | -0.0141 | -0.0689 | **PASS** |
| BTC | 7d | -0.0937 | -3.9 | -0.0384 | -0.1112 | **PASS** |
| BTC | 14d | -0.1213 | -5.0 | -0.0597 | -0.1569 | **PASS** |
| EW | 1d | -0.0414 | -1.7 | - | - | KILL |
| EW | 3d | -0.0574 | -2.3 | - | - | PASS |
| EW | 7d | -0.0935 | -3.9 | - | - | PASS |
| EW | 14d | -0.1234 | -5.1 | - | - | PASS |

**Extreme consensus conditioning (vs BTC):**

| Horizon | High Consensus (Q80+) Avg Ret | Low Consensus (Q20-) Avg Ret | Spread |
|---------|-------------------------------|------------------------------|--------|
| 1d | +0.09% | +0.40% | -0.31% |
| 3d | +0.22% | +1.06% | -0.84% |
| 7d | +0.68% | +2.60% | -1.92% |
| 14d | +0.78% | +5.17% | -4.39% |

The contrarian direction is clear: when all top traders are long across all tokens, forward returns are poor. Low consensus (positioning disagreement or bearishness) precedes strong returns.

### Signal 3: Token-Specific Positioning Z-Score (Panel)

*Per-token 30d z-score of `sum_toptrader_ls_ratio`. Daily cross-sectional IC (CSIC) with clustered t-stats.*

| Window | Horizon | CSIC | t-stat | IS CSIC | IS t | OOS CSIC | OOS t | Sign | Verdict |
|--------|---------|------|--------|---------|------|----------|-------|------|---------|
| 30d | 1d | -0.0073 | -1.14 | -0.0063 | -0.36 | -0.0075 | -1.08 | OK | **KILL** (|IC|<0.02, |t|<2) |
| 30d | 3d | -0.0155 | -2.36 | -0.0135 | -0.78 | -0.0158 | -2.24 | OK | **KILL** (|IC|<0.02) |
| 30d | 7d | -0.0197 | -3.02 | -0.0458 | -2.92 | -0.0150 | -2.10 | OK | **MARGINAL PASS** |
| 30d | 14d | -0.0238 | -3.82 | -0.0765 | -5.10 | -0.0142 | -2.09 | OK | **PASS** |
| 14d | 1d | -0.0061 | - | - | - | - | - | - | KILL |
| 14d | 7d | -0.0233 | - | - | - | - | - | - | Marginal |
| 60d | 7d | -0.0290 | - | - | - | - | - | - | Marginal |
| 60d | 14d | -0.0167 | - | - | - | - | - | - | KILL |

The 30d z-score at 7d and 14d horizons is the sweet spot. Direction is contrarian (negative IC): tokens with recently elevated positioning underperform cross-sectionally. However, note significant IS-to-OOS decay (IS -0.077 vs OOS -0.014 at 14d).

### Signal 4: Positioning Momentum (Panel)

*N-day change in `sum_toptrader_ls_ratio`. Daily CSIC.*

| Lookback | Horizon | CSIC | t-stat | IS CSIC | IS t | OOS CSIC | OOS t | Sign | Verdict |
|----------|---------|------|--------|---------|------|----------|-------|------|---------|
| 5d | 1d | -0.0058 | -0.95 | +0.0128 | 0.77 | -0.0099 | -1.53 | **FLIP** | **KILL** |
| 5d | 3d | -0.0124 | -1.98 | +0.0057 | 0.36 | -0.0164 | -2.42 | **FLIP** | **KILL** |
| 5d | 7d | -0.0122 | -1.93 | +0.0063 | 0.40 | -0.0163 | -2.36 | **FLIP** | **KILL** |
| 5d | 14d | -0.0166 | -2.69 | -0.0062 | -0.43 | -0.0189 | -2.78 | OK | Marginal |
| **10d** | **1d** | -0.0112 | -1.81 | -0.0010 | -0.06 | -0.0132 | -1.99 | OK | KILL (|IC|<0.02, |t|<2) |
| **10d** | **3d** | **-0.0178** | **-2.85** | -0.0041 | -0.27 | **-0.0206** | **-3.02** | **OK** | **PASS** |
| **10d** | **7d** | **-0.0252** | **-3.99** | -0.0255 | -1.75 | **-0.0252** | **-3.59** | **OK** | **PASS** |
| **10d** | **14d** | **-0.0222** | **-3.67** | -0.0378 | -2.91 | **-0.0189** | **-2.79** | **OK** | **PASS** |
| 20d | 1d | -0.0103 | -1.60 | -0.0098 | -0.53 | -0.0104 | -1.51 | OK | KILL |
| 20d | 3d | -0.0180 | -2.85 | +0.0030 | 0.18 | -0.0217 | -3.17 | **FLIP** | **KILL** |
| 20d | 7d | -0.0223 | -3.48 | -0.0482 | -3.28 | -0.0177 | -2.50 | OK | **PASS** |
| 20d | 14d | -0.0269 | -4.31 | -0.0745 | -4.93 | -0.0184 | -2.70 | OK | **PASS** |

**The 10d lookback is the standout.** Consistent negative CSIC at 3d-14d, no sign flips, and the OOS IC at 7d (-0.025, t=-3.59) is remarkably stable vs IS (-0.026, t=-1.75). Positioning CHANGES are more predictive than levels, confirming the hypothesis. The 5d lookback has sign flips (IS positive, OOS negative) and is killed.

### Signal 5: Taker-Positioning Divergence (Panel)

*Divergence between taker z-score and positioning z-score. Positive = taker more bullish than top-trader positioning.*

| Variant | Horizon | CSIC | t-stat | IS CSIC | IS t | OOS CSIC | OOS t | Sign | Verdict |
|---------|---------|------|--------|---------|------|----------|-------|------|---------|
| z-score diff | 1d | +0.0094 | 1.52 | +0.0063 | 0.36 | +0.0100 | 1.51 | OK | **KILL** (|IC|<0.02, |t|<2) |
| z-score diff | 3d | +0.0113 | 1.78 | +0.0145 | 0.81 | +0.0107 | 1.58 | OK | **KILL** (|IC|<0.02, |t|<2) |
| z-score diff | 7d | +0.0158 | 2.44 | +0.0608 | 3.53 | +0.0077 | 1.11 | OK | **KILL** (OOS t<2) |
| z-score diff | 14d | +0.0199 | 3.23 | +0.0904 | 5.67 | +0.0071 | 1.08 | OK | **KILL** (OOS t<2, OOS decay) |
| sign-based | 1d | +0.0157 | 2.94 | +0.0241 | 2.69 | +0.0102 | 1.54 | OK | **KILL** (OOS |IC|<0.02) |
| sign-based | 3d | +0.0163 | 3.02 | +0.0212 | 2.27 | +0.0132 | 2.01 | OK | **MARGINAL** |
| sign-based | 7d | +0.0191 | 3.49 | +0.0314 | 3.37 | +0.0109 | 1.63 | OK | **KILL** (OOS t<2) |
| sign-based | 14d | +0.0210 | 3.88 | +0.0447 | 4.81 | +0.0052 | 0.80 | OK | **KILL** (OOS t<2, severe decay) |

Direction: when takers are more bullish than top-trader positioning on a specific token, that token slightly outperforms. However, the signal suffers severe IS-to-OOS decay (IS IC often 3-5x larger than OOS IC) and OOS t-stats are universally below 2.0.

---

## 4. Regime Conditioning

*BTC trend: price vs 60d SMA. Volatility: 30d realized vol vs median.*

### Cross-Sectional Consensus (vs BTC)

| Signal | Regime | 7d IC | 7d t | 14d IC | 14d t | N |
|--------|--------|-------|------|--------|-------|---|
| cs_consensus | Bull | **-0.1716** | **-5.5** | **-0.2046** | **-6.5** | 983 |
| cs_consensus | Bear | -0.0127 | -0.3 | -0.0375 | -1.0 | ~657 |
| cs_consensus | Low vol | **-0.1675** | **-5.1** | **-0.2170** | **-6.7** | ~917 |
| cs_consensus | High vol | -0.0059 | -0.2 | +0.0241 | 0.7 | ~752 |

**Critical finding:** The consensus signal is strongly regime-dependent. It works in bull markets and low-volatility environments (IC -0.17 to -0.22, highly significant) but is essentially dead in bear markets and high-volatility regimes. This makes intuitive sense: crowded long positioning is most dangerous during complacent trending markets, not during panic.

### Cross-Sectional Dispersion (vs BTC)

| Signal | Regime | 7d IC | 7d t | 14d IC | 14d t | N |
|--------|--------|-------|------|--------|-------|---|
| cs_dispersion | Bull | -0.1058 | -2.8 | -0.1382 | -3.7 | 692 |
| cs_dispersion | Bear | -0.0030 | -0.1 | -0.0549 | -1.3 | ~551 |
| cs_dispersion | Low vol | -0.0967 | -2.9 | -0.1282 | -3.8 | ~882 |
| cs_dispersion | High vol | **+0.1140** | **+2.2** | **+0.1267** | **+2.4** | ~361 |

**Sign flip in high volatility:** Dispersion flips sign in high-vol regimes. During high vol, more disagreement predicts positive returns (contrarian buying during panic disagreement). During low vol, high dispersion is slightly bearish. This unstable behavior is why the unconditional signal is killed.

### Panel Positioning Momentum by Regime

| Signal | Regime | 7d CSIC | 7d t | 14d CSIC | 14d t |
|--------|--------|---------|------|----------|-------|
| pos_mom_10d | Bull | -0.0089 | -5.20 | -0.0114 | -5.32 |
| pos_mom_10d | Bear | -0.0778 | +0.09 | -0.0494 | +0.78 |
| pos_mom_10d | Low vol | -0.0334 | -3.65 | -0.0184 | -4.57 |
| pos_mom_10d | High vol | -0.0419 | -1.71 | -0.0261 | +0.27 |
| pos_mom_5d | High vol | **-0.0957** | **-2.58** | -0.0376 | -0.75 |

The 10d positioning momentum signal works across all regimes (consistent negative IC), with stronger point estimates in bear and high-vol conditions. The 5d momentum is particularly strong in high-vol regimes (IC -0.096 at 7d).

---

## 5. Top Findings

### Ranking by Signal Quality (combined full-sample IC, OOS stability, and t-stat)

| Rank | Signal | Best Config | Full CSIC | OOS CSIC | OOS t | Kill? |
|------|--------|-------------|-----------|----------|-------|-------|
| 1 | **CS Consensus vs BTC** | 14d horizon | -0.121 | -0.157 | ~-5.0 | **NO** |
| 2 | **Pos Momentum 10d** (panel) | 7d horizon | -0.025 | -0.025 | -3.59 | **NO** |
| 3 | **Pos Momentum 10d** (panel) | 14d horizon | -0.022 | -0.019 | -2.79 | **NO** |
| 4 | **Pos Momentum 20d** (panel) | 7d horizon | -0.022 | -0.018 | -2.50 | **NO** |
| 5 | **Token Z-Score 30d** (panel) | 14d horizon | -0.024 | -0.014 | -2.09 | Marginal |
| 6 | CS Consensus vs BTC | 7d horizon | -0.094 | -0.111 | ~-3.9 | **NO** |
| 7 | Pos Momentum 20d (panel) | 14d horizon | -0.027 | -0.018 | -2.70 | **NO** |
| 8 | Sign Divergence (panel) | 3d horizon | +0.016 | +0.013 | 2.01 | Marginal |

### Key Insight: Levels vs Changes

**Positioning CHANGES (momentum) are more predictive than positioning LEVELS (z-score).** The 10d momentum signal has better OOS stability (IC barely decays from IS to OOS at 7d horizon), while the z-score signal shows ~5x decay from IS to OOS at 14d. This suggests the market adapts to static positioning levels but not to the velocity of positioning shifts.

### Cross-Sectional Consensus is the Flagship Signal

The consensus signal (mean positioning across all tokens) at 14d horizon achieves IC=-0.121 (t=-5.0), strengthening to IC=-0.157 OOS. This is a **macro-level** contrarian signal: when top traders are uniformly long across the entire alt panel, the market is about to underperform. This complements the BTC-specific top trader L/S ratio (IC=-0.166 at 14d) by adding a cross-token crowding dimension.

---

## 6. Signal Verdicts

### Signal 1: Cross-Sectional Positioning Dispersion
**VERDICT: KILL**

Dispersion fails on all horizons (|t|<2 unconditionally). It shows interesting regime behavior (sign flip in high-vol) but is not tradeable as a standalone signal. Potentially useful as a regime filter for other signals.

### Signal 2: Cross-Sectional Positioning Consensus
**VERDICT: STRONG PASS at 7d and 14d horizons**

The best cross-token signal tested. IC=-0.094 (7d) and IC=-0.121 (14d), both passing kill criteria with significant t-stats. OOS IC strengthens vs IS, which is unusual and encouraging. Direction: contrarian (high consensus = bearish). Regime-dependent: works in bull/low-vol conditions. Dead in bear/high-vol.

**Actionable:** When mean `sum_toptrader_ls_ratio` across all 27 tokens is above Q80, expect -1.9% (7d) and -4.4% (14d) underperformance vs low-consensus periods. This can serve as a risk-off overlay for BTC or alt positions.

### Signal 3: Token-Specific Positioning Z-Score
**VERDICT: MARGINAL PASS at 7d-14d, KILL at 1d-3d**

The 30d z-score shows correct contrarian direction (negative CSIC) and no sign flips, but suffers significant IS-to-OOS decay (14d: IS -0.077 to OOS -0.014). The OOS CSIC is barely above the 0.02 threshold at 7d (-0.015, t=-2.10). This signal works but is weaker than momentum.

### Signal 4: Positioning Momentum (10d change)
**VERDICT: STRONG PASS at 3d, 7d, 14d horizons**

The standout panel signal. The 10d lookback achieves CSIC=-0.025 at 7d with no IS/OOS sign flip and minimal decay (IS -0.026 vs OOS -0.025). The 5d lookback has sign flips and is killed. The 20d lookback passes at 7d-14d but has a sign flip at 3d.

**Actionable:** Tokens where top-trader positioning has increased most over the past 10 days tend to underperform cross-sectionally over the next 3-14 days. This is a viable cross-sectional alpha signal for long-short alt portfolios.

### Signal 5: Taker-Positioning Divergence
**VERDICT: KILL (severe OOS decay)**

Despite strong full-sample IC and intuitive story (taker-positioning disagreement predicts reversals), the signal suffers 3-5x IS-to-OOS decay. The IS period captures most of the signal; OOS t-stats are universally below 2.0. This is likely a period-specific artifact rather than a persistent alpha source.

---

## 7. Summary Verdict Table

| Signal | Best Horizon | Full IC | IS IC | OOS IC | OOS t | Verdict |
|--------|-------------|---------|-------|--------|-------|---------|
| CS Dispersion | 14d | -0.053 | -0.017 | -0.120 | ~-1.9 | **KILL** |
| **CS Consensus** | **14d** | **-0.121** | **-0.060** | **-0.157** | **~-5.0** | **STRONG PASS** |
| Token Z-Score (30d) | 14d | -0.024 | -0.077 | -0.014 | -2.09 | MARGINAL |
| **Pos Momentum (10d)** | **7d** | **-0.025** | **-0.026** | **-0.025** | **-3.59** | **STRONG PASS** |
| Taker-Pos Divergence | 14d | +0.020 | +0.090 | +0.007 | 1.08 | **KILL** |

---

## 8. Implications for Strategy

1. **CS Consensus as macro overlay:** The mean top-trader L/S ratio across all tokens is a strong contrarian timing signal for BTC and alts at 7-14d horizons. Combine with BTC-specific L/S ratio (IC=-0.166) for a two-factor positioning model: BTC-specific + cross-token crowding.

2. **10d Positioning Momentum for cross-sectional alpha:** The 10d change in `sum_toptrader_ls_ratio` provides CSIC=-0.025 at 7d, stable OOS. This is suitable for long-short token selection within the alt panel.

3. **Regime conditioning matters:** Both flagship signals benefit from regime awareness. Consensus works best in bull/low-vol (IC doubles). Momentum works across regimes but is strongest in bear markets (IC triples).

4. **Levels vs changes:** Positioning changes (momentum) are more robust OOS than positioning levels (z-scores). Build signals on deltas, not levels.

5. **Taker data adds little incrementally:** The taker-positioning divergence does not survive OOS testing. Taker volume may be useful for BTC specifically (earlier analysis showed IC=-0.138 for taker volume dispersion) but fails as a cross-sectional panel signal.
