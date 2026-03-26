# Research Status — Active Signal Discovery

> **Last updated:** 2026-03-26T15:30Z (session 15b — 8x funding overcharge bug fixed. BTC Trend+Carry regime rotation is best architecture: OOS +4.2%/yr, Sharpe 0.42, MaxDD -7.4% in last 14mo. Full-period 32.9%/yr is bull-inflated. Carry NOT dead but structurally declining (30.7%→2.4%). Rule 13 added: last 12 months is the primary metric. 300% needs bull market or new signal class.)

## TIMESTAMP
2026-03-26T15:15Z

---

## Signal Scoreboard (48 signals tested)

| # | Signal | OOS IC / Sharpe | Verdict |
|---|--------|----------------|---------|
| 1 | Retail Contrarian (L/S ratio) | IC~0 cross-token, IS Sharpe 0.166 over 5yr | KILLED — short-sample noise, refuted with 5yr Bybit data |
| 2 | Funding Short 72h | Sharpe 0.99 OOS | MARGINAL |
| 3 | Stablecoin Supply | 10/12 sign reversal | KILLED |
| 4 | Deribit Skew_30d proxy | IC +0.224 OOS (t=5.15) | PASS |
| 5 | Fear & Greed Index | 2/80 pass | KILLED |
| 6 | US10Y 20d Change | IC -0.375 OOS (t=-6.32) | GOLD |
| 7 | DXY+10Y Combined Regime | +0.667 marginal Sharpe | GOLD |
| 8 | Skew+Trend Combo | Sharpe 1.09->1.37 OOS | PASS |
| 9 | Cross-token Dispersion | IC sign flip OOS | KILLED |
| 10 | Session Momentum | IC=0.091 (t=6.62), H21-22 Sharpe 2.04 OOS | CONDITIONAL PASS — overlay/timing only |
| 11 | Funding Rate Dispersion | IC=+0.038, crowding -6.11% 7d | CONDITIONAL PASS |
| 12 | L/S Data Extension | Proxy INVALID, Bybit data acquired | KILLED (see #1) |
| 13 | Oil/Geopolitical Signal | Oil 20d IC=-0.480 ETH 14D | PASS |
| 14 | DXY+10Y+Oil Triple Regime | IC=-0.413 BTC 14D OOS | GOLD |
| 15 | Taker Buy/Sell Volume | Cross-token disp IC=-0.138 (t=-9.35), trend overlay Sharpe 0.37→1.04 | PASS |
| 16 | ETF Flow Momentum | Flow 20d z-score IC=+0.191 14D (t=2.99), tightening+inflow +6.89% 14D | PASS |
| 17 | OI Rate-of-Change Divergence | oi_div_unsigned_7d IC=-0.059 3D (t=-2.82), Q1-Q5 spread 114.7 bps/3d | PASS |
| 18 | Gamma Exposure (GEX) / VRP | VRP z-score IC=0.268 BTC 7D, vol predictor IC=0.28 | CONDITIONAL PASS — vol/sizing overlay |
| 19 | CTREND (trend quality ranking) | IC~0.07-0.12 IS, -0.013 OOS panel | KILLED — classic IS overfit |
| 20 | Liquidation Cascade Risk (composite) | Composite noise; funding contrarian IC=0.086 BTC persistent | KILLED composite / NEEDS DATA for sub-signal |
| 21 | BTC Dominance Rotation | IC sign flip OOS all lookbacks | KILLED |
| 22 | Correlation Breakdown | IC=-0.065 (descriptive not predictive) | KILLED standalone / UTILITY as regime conditioner |
| 23 | VWAP Deviation | IC=0.035 max (microscopic) | KILLED — HF effect only |
| 24 | Exchange Netflow 5d sum | BTC IC=+0.149 (t=3.44), ETH inverted | CONDITIONAL PASS — BTC only, short history |
| 25 | Correlation Regime Conditioning | IC lift +0.005 (negligible) | KILLED — signals work unconditionally |
| 26 | Multi-Signal Stacking (kitchen-sink) | TOP_9 IC=0.102 vs solo 0.248 (-59%) | KILLED — naive equal-weight stacking |
| 27 | Multi-Signal Pairs (selective) | US10Y+DXY IC=0.305 (+23%) | GOLD — correct architecture is 2-signal composite |
| 28 | **Top Trader L/S raw** | **BTC 14d IC=-0.166 (t=-6.91), strengthens OOS** | **GOLD — positioning signal** |
| 29 | **L/S Divergence (count)** | **BTC 14d IC=-0.204 (t=-8.57)** | **GOLD — strongest single IC ever** |
| 30 | **L/S Ratio Range** | **BTC 14d IC=-0.164 (t=-7.40), 100% sign consistent** | **PASS** |
| 31 | OI Rate of Change / Z-score | Zero predictive power all lookbacks | KILLED |
| 32 | Cross-token OI Dispersion | Sign flip OOS | KILLED |
| 33 | **Trend+Pullback L/S (EMA+RSI)** | **BTC long PASS (2 agents agree), short/ETH contested. BTC combined 7/10 CONDITIONAL.** | **PASS — BTC long-only. Short/ETH need more data.** |
| 34 | Funding Reversal Short | WF: 0/8 positive, Sharpe -2.12. Extreme funding events extinct post-2021. | KILLED — regime dead |
| 35 | BB Squeeze Breakout | WF: 2/10 positive, Sharpe -17.28. IS PF 4.54 was full-sample overfit. | KILLED — IS artifact |
| 36 | DVOL Rate-of-Change | IS IC=0.10, OOS IC=0.00. All 4 asymmetric mappings degrade V3. | KILLED — IS artifact |
| 37 | Cross-Token Positioning Consensus | IC=-0.121 (momentum, not contrarian) | PASS — needs architecture |
| 39 | V3+RSI Timing V2 Flexible | 6/6 WF improved, dSharpe +5.47, return 53%→337% | CONDITIONAL PASS — low OOS trades |
| 40 | Macro Regime Rotation (US10Y+DXY) | Deep WF: IC non-stationary, param sensitivity 189.7%, RANGE Sharpe -0.422 | KILLED — non-stationary IC, fragile params |
| 41 | Intraday Momentum Breakout | Deep WF: mean Sharpe -12.2, full-sample 0.95 doesn't replicate OOS. R107 Sharpe 0.594 was IS. | KILLED — fails deep WF |
| 42 | Mean-Rev Vol Gate | Standalone Sharpe -0.671 | KILLED |
| 43 | VRP Direction | Only 2/6 WF positive | KILLED |
| 44 | Funding Contrarian | Standalone Sharpe -0.513 | KILLED |
| 38 | Trump Trade Sentiment (presidency) | trade_net IC=0.114 at 3d BTC (counterintuitive: escalation→UP) | CONDITIONAL — marginal, 14mo only |
| 45 | Gold Momentum / Gold-BTC Divergence | Best IC=0.132 (14d), 36/43 pass IC, but IC decaying, last 3 WF windows dead | KILLED — non-stationary, relationship weakening |
| 46 | Realized Vol Structure (6 variants) | S3 skewness: median OOS Sharpe 0.904, 60% WF positive, V3 corr -0.111 | CONDITIONAL — risk signal not alpha, possible sizing overlay |
| 47 | On-Chain Metrics (16→36 variants) | R115 promising on short data, R118 OVERTURNED with 9yr: addr_growth collapsed 2.40→0.13, 52% IC sign flip | KILLED — reflexive (driven BY price), not predictive |
| 48 | Multi-TF Divergence (7 variants) | S3 momentum div: WF Sharpe 0.621 (5/10), V3 corr 0.266, but portfolio Sharpe LOWER | MARGINAL — drawdown reducer only, not return enhancer |
| 49 | Auto-Pipeline (342 candidates, 10 families) | Full screen run 2026-03-21: 0/342 survive. Vol signals MaxDD 40-91%, funding PF<1. | KILLED — entire pipeline library exhausted |
| 50 | MVRV Ratio (raw + z-scored) | OOS IC=-0.162 (t=-4.91) but IS IC=+0.003 — sign flip. V3 overlay dSharpe=-0.138 | KILLED — IS/OOS inconsistency, hurts V3 overlay |
| 51 | DVOL Skew Z-Score | 7d IC=+0.036 (t=0.94) KILL. 14d IC=+0.078 (t=2.05) marginal, IS IC=+0.009. V3 overlay dSharpe=+0.106 | KILLED — no IS corroboration, weak evidence |
| 52 | Funding Flip (sign reversal, 72h decay) | IS IC=-0.151 (t=-2.11), OOS IC=+0.022 (t=+0.25) — sign flip, only 320 events in 6yr | KILLED — IS/OOS sign flip |
| 53 | Funding RoC Momentum (8h/24h/72h delta) | Best: fr_delta_24h 7d IS IC=-0.172 (t=-2.43), OOS IC=-0.062 (t=-0.70). Funding std 6x collapse | KILLED — OOS insignificant, variance collapsed |
| 54 | Funding Dispersion (BTC vs alt basket) | Initial weekly IC=+0.13 (t=4.6) was ARTIFACT of overlapping returns. Non-overlapping: all dead. Died post-2024. | KILLED — methodological artifact, structurally dead |

---

## KILLED Signals (do not revisit)

| Signal | Reason | Date |
|--------|--------|------|
| OI Divergence (standalone strategy) | All 64 configs deeply negative, best -126% | 2026-03-23 |
| Spot-Perp Basis carry/MR | All configs -39% to -86% | 2026-03-23 |
| ML V2 Direction Model | 100% temporal leakage, negative OOS alpha | 2026-03-23 |
| All ML strategies s312-s315 | In-sample artifacts, dead OOS | 2026-03-23 |
| Stablecoin Supply | 10/12 IS/OOS sign reversal | 2026-03-23 |
| Fear & Greed (contrarian) | 2/80 pass rate, contrarian hypothesis empirically false | 2026-03-23 |
| Funding Short 72h (standalone) | Real but marginal: Sharpe 0.99, +32% | 2026-03-23 |
| Cross-token Dispersion (momentum timing) | IS→OOS sign flip | 2026-03-23 |
| Session Momentum (standalone) | Costs destroy at 5bps+ | 2026-03-24 |
| L/S Funding Proxy Approach | Funding ≠ L/S (BTC r=0.25, ETH r=-0.66) | 2026-03-24 |
| Retail Contrarian (L/S ratio) | 5yr Bybit data: IC~0 cross-token, IS Sharpe 0.166 (was 2.85 on 28d). 6/436 combos pass, 5 are AVAX-only anomaly. Signal is dead. | 2026-03-24 |
| CTREND (trend quality: ADX, DER, R²) | Strong IS (IC 0.07-0.12), completely breaks OOS (-0.013 panel). Classic overfit. | 2026-03-24 |
| Liquidation cascade (composite) | Leverage composite is noise. Actual OI/liquidation data only 1-20 days, can't test properly. | 2026-03-24 |
| BTC Dominance Rotation | IS signal flips sign OOS across all lookbacks (14d, 30d, 60d). Regime artifact, not stable. | 2026-03-24 |
| Funding Reversal Short | Extreme funding events extinct post-2021. Exchange risk controls killed the signal. 0/8 WF windows positive. | 2026-03-24 |
| BB Squeeze Breakout | IS Sharpe 5-14, OOS deeply negative (-17 to -57). PF 4.54 was full-sample overfit. 2/10 WF windows. | 2026-03-24 |
| DVOL Rate-of-Change | Full IC=0.10, OOS IC=0.00. All 4 asymmetric overlay mappings degrade V3. IS artifact. | 2026-03-24 |
| Trump Sentiment (crypto) | trade_net IC=0.114 marginal. Non-trade categories zero power. 14 months insufficient. | 2026-03-24 |
| Correlation Breakdown (standalone) | BTC-alt correlation is descriptive (tells regime) not predictive (doesn't forecast returns). | 2026-03-24 |
| VWAP Deviation | All ICs microscopic. Mean-reversion in price-vs-VWAP is HF effect, not actionable at 1h+ | 2026-03-24 |
| Macro Regime Rotation (US10Y+DXY) | IC non-stationary (sign flips 19x/2yr), param sensitivity 189.7%, RANGE Sharpe -0.422. Full-period Sharpe 0.177. | 2026-03-24 |
| Intraday Momentum Breakout (8h ATR) | Full-sample Sharpe 0.95 but WF mean -12.2. R107 Sharpe 0.594 was IS artifact. Momentum breakouts mean-revert on BTC 1h. | 2026-03-24 |
| Gold Momentum / Gold-BTC Divergence | IC=0.132 14d but decaying: 8/10 weaken over time, last 3 WF windows dead, parameter mode unstable. Non-stationary as BTC matures. | 2026-03-24 |
| Multi-TF Divergence (standalone) | 3/6 corr>0.5 with V3 (trend-correlated), decorrelated ones fail WF. Price-derived signals can't escape trend information. | 2026-03-24 |
| On-Chain Metrics (36 variants, 9yr) | R118: 52% IC sign flip across regimes, addr_growth collapsed 2.40→0.13, 0/4 survivors improve portfolio, all FRAGILE. Reflexive not predictive. | 2026-03-25 |
| Auto-Pipeline (342 candidates) | Full screen 2026-03-21: 0/342 survive. Vol MaxDD 40-91%, funding PF<1, all families dead. | 2026-03-25 |
| MVRV Ratio (raw + z-scored) | OOS IC=-0.162 real but IS/OOS sign flip. V3 overlay degrades Sharpe -0.138. | 2026-03-25 |
| DVOL Skew Z-Score | 7d KILL (t=0.94). 14d marginal (t=2.05) but IS IC=0.009 — no corroboration. | 2026-03-25 |
| Funding Flip (sign reversal) | IS IC=-0.151, OOS IC=+0.022 — sign flip. Only 320 events in 6yr. | 2026-03-25 |
| Funding RoC Momentum (delta) | Best OOS IC=-0.062 (t=-0.70). Funding std collapsed 6x IS→OOS. | 2026-03-25 |
| Funding Dispersion (BTC vs alts) | Weekly IC=+0.13 was overlapping-return artifact. Non-overlapping: all dead post-2024. | 2026-03-25 |

---

## Wave 5 Results (session 5, 2026-03-24) — 0/5 PASS

**All 5 signals KILLED.** Easy signals are mined out. Remaining alpha in: better data, multi-signal stacking, regime conditioning.

### R45 — CTREND Trend Quality Ranking (KILLED)
- ADX, directional efficiency ratio (DER), R² as cross-sectional ranking factors
- IS: IC 0.07-0.12 (promising), OOS: IC -0.013 (completely flat)
- Classic IS overfit — trend quality metrics don't predict future cross-sectional returns
- Scripts: `research/wave5_signal_scout_results.md`

### R46 — Liquidation Cascade Risk Model (KILLED composite / NEEDS DATA)
- Composite leverage risk metric: noise, no predictive power
- Sub-signal: funding contrarian shows persistent IC=0.086 for BTC, but t-stats ~0
- Actual OI + liquidation data only covers 1-20 days — insufficient for proper testing
- **Follow-up**: If R41 finds OI/liquidation data with 12+ months, re-test funding contrarian

### R47 — BTC Dominance Rotation (KILLED)
- BTC.D changes tested as alt-season vs BTC-season indicator
- IS signal systematically flips sign OOS across 14d, 30d, 60d lookbacks
- Likely a regime artifact, not a stable tradeable signal

### R48 — Correlation Breakdown (KILLED standalone / UTILITY)
- Rolling BTC-alt correlation structure is descriptive but not predictive
- **Utility noted**: Could serve as regime conditioning variable for existing signals
  (e.g., trade momentum when corr > 0.7, mean-reversion when corr < 0.5)
- Not worth a standalone signal or overlay

### R49 — VWAP Deviation (KILLED)
- Price deviation from 24h/72h/168h VWAP tested as mean-reversion signal
- All ICs microscopic (max 0.035 BTC 24h 7d horizon)
- Mean-reversion in price-vs-VWAP is a high-frequency effect (~seconds/minutes)
- Not actionable at our hourly+ timescales

---

## Wave 4 Results (session 4, 2026-03-24)

### R38 — L/S Ratio Data Fetch (COMPLETE)
- Bybit API: FREE, no auth, no history cap. BTC from Aug 2020 (2,056 days), 10 tokens total (17,529 rows)
- OKX API: 180 days only
- data.binance.vision metrics folder: 5-min granularity, 793 symbols, BTC from Sept 2020 (found by R37)
- Data saved: `/workspace/crypto_backtest/data/alternative/ls_ratio_extended/`
- Fetch tool: `/workspace/crypto_backtest/tools/fetch_ls_data.py`

### R39 — OI Rate-of-Change Divergence (COMPLETE — PASS)
- Best signal: `oi_div_unsigned_7d` at 3d horizon: IC=-0.059 OOS (t=-2.82, p=0.005)
- Sign-consistent IS→OOS ✓
- Quintile spread: Q1-Q5 = +114.7 bps per 3-day OOS (monotonic)
- Conditional: OI spike + price stall → -302 bps 7d OOS (265 bps differential vs OI+price move)
- OI+funding combo: IC=-0.049 (t=-2.34)
- Data: Bybit 1h OI (2024-01 to 2026-03), 9 tokens
- Scripts: `research/oi_divergence_signal.py`, `research/oi_divergence_results.md`
- Panel data: `research/oi_divergence_panel.parquet`

### R40 — ETF Flow Signal (COMPLETE — PASS)
- Extended data to 533 days (Jan 2024 – Mar 2026) via SoSoValue + bitbo.io scraping
- Best signal: Flow 20d z-score IC=+0.191 at 14d (t=2.99), IC=+0.164 at 7d (t=2.61)
- KEY FINDING: ETF inflow during TIGHTENING regime → +6.89% 14d (t=7.26, 70% win rate)
- Quintile spread: +5.50% at 14d
- Caveat: only 26 months, rolling IC unstable (concentrated in certain periods)
- Data: `data/alternative/etf_flows/btc_etf_daily.parquet` (533 rows)
- Scripts: `research/etf_flow_signal.py`, `research/etf_flow_results.md`

### R41 — Liquidation + Exchange Netflow Data Hunt (IN FLIGHT)
- Agent still running at session end
- No output files saved yet
- Check agent transcript at: `/tmp/claude-1000/-workspace/tasks/ab4ff0813af94fc70.output`
- NEXT SESSION: Check if R41 produced results, if not re-launch

### R42 — Gamma Exposure / VRP (COMPLETE — CONDITIONAL PASS)
- No free historical options OI data (can't compute true GEX)
- VRP z-score proxy: IC=0.268 BTC 7d OOS (p<0.001), IC=0.223 ETH 14d
- Vol prediction: IC=0.28 vs 1d absolute returns (strong for sizing)
- Naive regime-switching failed — signal is for vol/sizing, not direction
- Fetched 1,826 days DVOL data (2021-2026)
- Need daily Deribit snapshot cron for real GEX (or Tardis.dev $199/mo)
- Data: `data/alternative/deribit_options/dvol/`
- Scripts: `research/gamma_exposure_signal.py`, `research/gamma_exposure_results.md`

### R43 — Taker Buy/Sell Volume (COMPLETE — PASS)
- Cross-token taker dispersion: IC=-0.138 OOS (t=-9.35) — strongest standalone
- Trend filter overlay: Sharpe 0.37→1.04 OOS (+180%) using net taker volume confirmation
- Raw contrarian: IC~-0.04 (t~-2.0), stable but modest
- Best on mid-cap alts (OP, DOT, LINK), weakest on majors
- Data already existed in `data/alternative/binance_positioning/`
- Scripts: `research/taker_volume_signal.py`, `research/taker_volume_results.md`

### R44 — L/S Ratio Contrarian (COMPLETE — KILLED)
- 5yr Bybit data decisively refutes IS Sharpe 2.85 (was short-sample noise)
- Cross-token IC near zero at every horizon
- IS signals systematically flip sign OOS
- Cross-sectional backtest: IS Sharpe 0.166 over 5yr (18x reduction)
- 6/436 combos pass, 5 are AVAX-only anomaly
- Scripts: `research/ls_ratio_contrarian_signal.py`, `research/ls_ratio_contrarian_results.md`

---

## Key Research Findings (numbered, cumulative)

1. **US10Y 20d change is THE strongest macro predictor** — IC=-0.375 at 14D (t=-6.32)
2. **Realized vol skew_30d is a valid trend-quality overlay** — IC=+0.224 OOS BTC (t=5.15)
3. **Funding Short 72h is real but marginal** — Sharpe 0.99, +32% OOS
4. **Fear & Greed is NOT contrarian** — pro-trend, 2/80 pass
5. **Most IS macro winners are OOS losers** — only DXY/US10Y survive
6. **Cross-token dispersion is regime-dependent** — not stable standalone
7. **Funding structure has multiple conditional edges** — crowding, negative-funding regime
8. **Binance L/S 28-day limit bypassed** — Bybit (unlimited), data.binance.vision (5-min, 5yr+)
9. **Session momentum is entry timing, NOT standalone** — H21-22 Sharpe 2.04 OOS but directional
10. **Oil adds marginal IC to DXY+10Y** — triple IC=-0.413, stagflation -4.24% 14D
11. **Session momentum standalone killed by costs** — breakeven 10-15 bps
12. **L/S proxy INVALID, actual L/S ratio KILLED** — 5yr data shows IC~0 cross-token
13. **Taker volume best as trend overlay** — net taker confirmation Sharpe +180% OOS
14. **Taker cross-token dispersion is strong standalone** — IC=-0.138 (t=-9.35)
15. **ETF flow strongest during tightening** — inflow + tightening = +6.89% 14D (t=7.26)
16. **OI divergence confirms leverage vulnerability** — OI spike + price stall = -302 bps 7D OOS
17. **VRP z-score predicts vol, not direction** — IC=0.268 for sizing, regime-switch fails
18. **Easy signals are mined out** — Wave 5 (0/5 pass): CTREND, liquidation composite, BTC.D, correlation, VWAP all dead OOS
19. **Trend quality metrics overfit** — ADX/DER/R² as cross-sectional factors look great IS, break completely OOS
20. **Correlation structure is descriptive not predictive** — tells you the regime, doesn't forecast returns; potential as regime conditioner only
21. **Remaining alpha is in data + stacking** — new signals need new data sources (OI/liquidation history) or multi-signal combinations
22. **Correlation conditioning is useless** — crypto structurally high-corr (0.711), IC lift +0.005 (negligible), signals work unconditionally
23. **Kitchen-sink signal stacking DESTROYS IC** — TOP_9 composite -59% vs best solo. Equal weighting is the enemy.
24. **Selective 2-signal pairs GENUINELY improve IC** — US10Y+DXY IC=0.305 (+23% over solo), US10Y+Skew IC=0.294 (+19%)
25. **OI Divergence and ETF Flow HURT composites** — OI OOS IC=-0.070, ETF sign flip. DEMOTED from implementation queue.
26. **Correct overlay architecture is multiplicative independent layers** — not averaged composites
27. **BTC exchange netflow 5d sum is a valid BTC-only signal** — IC=0.149 (t=3.44), but ETH relationship is INVERTED
28. **OKX liquidation data is useless** — only 10.8 hours of ticks, would need 60+ days continuous collection
29. **Top trader L/S is fundamentally different from retail L/S** — retail L/S (Bybit) IC~0 killed, but TOP TRADER L/S (Binance) IC=-0.166 GOLD. Top traders crowd → forced deleverage → predictable mean reversion.
30. **L/S divergence (top-vs-retail) is the strongest single positioning IC ever** — IC=-0.204 at 14d (t=-8.57). Signal STRENGTHENS OOS (IS=-0.10 → OOS=-0.20).
31. **Positioning signals are medium-frequency** — IC increases 1d→14d, crowded positioning unwinds over days not hours. Matches our holding periods.
32. **OI levels have zero predictive power** — rate of change, z-score, all horizons: nothing. OI is not a signal.
33. **Positioning signals survive 23-29x actual trading costs** — L/S Divergence break-even 291bps, Top Trader 231bps vs 10bps actual. Net alpha +111-133%/yr.
34. **Skew_30d is DEAD standalone after costs** — net alpha -66%/yr. Only works as overlay in bull markets.
35. **Net Taker Volume killed by turnover** — 0.68 turnover/day = 24.8% annual drag, break-even only 3bps.
36. **NO signal is always-on** — every signal is regime-dependent. Regime-switching improves OOS Sharpe by +0.39 to +0.60.
37. **Oil is a CRISIS-ALPHA signal** — IC=-0.307 in downtrends, -0.297 in crisis, flat in uptrend/range.
38. **Top Trader L/S is a RANGE specialist** — IC=-0.288 in range (train), -0.200 OOS. Strongest in sideways markets.
39. **Macro IC flips sign OOS** — IS=+0.154, OOS=-0.128. DO NOT use as continuous signal. Use as categorical regime filter only.
40. **Agreement regimes produce 9% 14d spread** — both bearish: -4.89% 14d, both bullish: +3.91%. Non-linear edge.
41. **Positioning is THE robust OOS alpha source** — B1=-0.204, B2=-0.230 at 14d OOS. Uncorrelated with macro (rho=-0.11).
42. **Standalone positioning system FAILS** — R58/R59 both confirm. IS Sharpe 0.06-0.47, OOS all negative. Signals predict 14d returns but standalone daily rebalancing destroys edge with 11%/yr cost drag. OVERLAYS ONLY.
43. **Crisis hedge is the standout** — Sharpe 3.10, +93% annualized in 51 OOS crisis days. Most actionable single component. Oil downtrend signal works perfectly.
44. **Daily rebalancing kills positioning signals** — signals are 14d predictors, daily rebalancing wastes 11%/yr. Need WEEKLY minimum rebalancing or event-triggered only.
45. **Walk-forward standalone: 1/3 positive** — 2023-24 bull run destroyed system (positioning contrarian fights sustained trend). Only recent window (2025-07+) positive.
46. **DD protection from regime-switching is REAL** — -36.56% vs -49.53% B&H max DD. Regime framework provides tail risk reduction even when returns underperform.
47. **Positioning overlay on trend base: +0.31 OOS Sharpe** — R60 confirms correct architecture. OOS return -2.7% → +3.2%, MaxDD -19.1% → -16.7%. Weekly rebalancing, 0.87%/yr cost drag.
48. **Crisis hedge is REDUNDANT with trend-following** — SMA trend exits BEFORE crisis thresholds fire. ZERO days where crisis fires while base is long. Remove crisis layer from trend-following overlays.
49. **Positioning value is RANGE-specific** — reduces range market losses from -21.4% to -13.2% annualized. This is where trend-following loses money; overlay fixes whipsaws.
50. **Not statistically significant yet** — t=1.48, p=0.14 on 431 OOS days. Need 1-2 more years OOS or stronger effect size.
51. **Macro agreement boost is negligible** — +0.012 OOS Sharpe. Not worth the complexity. Drop from overlay architecture.
52. **VRP sizing overlay is SUPER-ADDITIVE with positioning** — Combined OOS Sharpe +0.68 (base -0.17). Return -3.9% → +15.9%, MaxDD -19.1% → -11.9%. Overlays are uncorrelated (rho=0.031). STATISTICALLY SIGNIFICANT (t=2.332, p<0.05).
53. **Backtest bug inflated historical results** — Previous backtests used close price as stop price instead of current price. Bug is now fixed, and with the fix backtests are NEGATIVE. The paper trading strategies genuinely don't work. Compounding is intended and properly capped — that was NOT the issue.
54. **Previously claimed portfolio metrics are INVALID** — "Sharpe 6.53" portfolio (s44+s29+s37+s32) was based on bugged backtests. With the bug fixed, these strategies are also negative. The strategies genuinely don't work.
55. **Two-overlay stack is the production architecture** — Positioning (crowd) + VRP (vol regime) on trend base. Super-additive, orthogonal, statistically significant.
56. **Positioning overlay HURTS carry strategies** — Carry is already contrarian (shorts when crowd long), adding contrarian positioning is REDUNDANT. IS Sharpe drops 1.05→0.55.
57. **Carry is dormant since mid-2025** — Funding rates collapsed, s29/s65 generate ZERO trades. Any carry overlay test OOS is meaningless.
58. **New V3 momentum (EMA+Pos+VRP) is the best new strategy** — OOS +17.52%, Sharpe 0.56, MaxDD -20.2%. No hard stops = immune to stop-price bug. Beats all surviving strategies.
59. **ADX filter overfits** — ADX threshold improves IS (+0.312 dSharpe) but HURTS OOS (-0.204 dSharpe). Don't use trend quality filters on daily trend signals.
60. **V3 overlays generalize but base doesn't** — Positioning+VRP improve 7/10 tokens (mean +0.105 dSharpe), but 20/50 EMA trend base fails on alts (too choppy). V3 is BTC+BNB only.
61. **V3 is ROBUST: 6/6 validation score** — 5/6 walk-forward windows positive, 0 KILL flags on ±20% params, 5/5 corner cases positive. Anti-overfit signature: hurts IS, helps OOS.
62. **RANGE regime kills V3** — Sharpe -1.01 in range markets. Overlays reduce losses (+0.231 dSharpe) but can't fix EMA whipsaws. Universal trend-following weakness.
63. **V3 seasonality: Q2 is danger zone** — Q1 best (+30.4%), Q2 worst (-9.2%). April and September have 14% win rates. Consider seasonal sizing reduction.
64. **Whipsaw filter nearly doubles V3 Sharpe** — >2 EMA crosses in 60d → flat. OOS Sharpe 0.556→0.982, MaxDD -20.2%→-12.1%. Zero IS impact = zero overfit. Only 3.7% days affected.
65. **ADX, BB width, realized vol filters ALL fail for crypto** — ADX too slow, BTC trends at low vol AND chops at high vol, BB width removes quiet uptrends. Only frequency-based filters (whipsaw count) work.
66. **V3.1 production prototype passes v4 compatibility** — All StrategyResult fields correct, BTC-only filter works. Needs perf optimization (3.63ms → <1ms) via pre-computation.
67. **Whipsaw filter is a single-event artifact** — R71 walk-forward: 0/6 windows improved. Entire R69 improvement from 16 days in Oct 2025. Filter mechanically redundant with base signal. ALWAYS walk-forward validate filters.
68. **RANGE regime weakness is structural** — 26 filter variants tested, NONE survive walk-forward. Accept RANGE losses as cost of UPTREND capture. The only honest answer is position sizing (overlays already do this).
69. **Tighter positioning threshold (1.2) FAILS walk-forward** — Hurts 3/6 windows, mean dSharpe +0.029 (noise), non-monotonic curve. R67's +27% improvement was single-window artifact (2025-H1). Keep default 1.5.
70. **Walk-forward is THE essential validation** — R69 whipsaw (+0.426), R72 tight threshold (+0.152) both looked great point-in-time, both killed by walk-forward. Never deploy without it.
71. **Dual ROC (30/90) is THE alt-token base signal** — Base-only mean Sharpe +0.416 (6x better than EMA 20/50). Requires both 30d and 90d positive = selective, stays flat in chop.
72. **Base signal matters more than overlays for alts** — D base-only (+0.416) beats V1+overlays (+0.168). Get the base right first.
73. **SOL fails every signal** — Negative OOS Sharpe on all 5 bases × 2 overlay combos. Exclude from strategy universe.
74. **Slower MAs are WORSE, not better, for crypto** — 50/200 EMA mean Sharpe -0.446 (worst tested). Fix for chop is stricter entry conditions, not slower indicators.
75. **Signal D (Dual ROC) fails walk-forward on ALL alts** — 50% win rate for ETH/BNB (not deployable), XRP/DOGE killed. Bull-only signal, regime-dependent. Alt strategies dead for now.
76. **No alt-token momentum strategy survives walk-forward** — Tested 5 base signals × 5 tokens. NONE pass. Alts are fundamentally harder than BTC for trend-following.
77. **V4 engine: P0 blocker is alternative data pipeline** — V3 prototype loads positioning/DVOL externally per call (slow for backtesting across many tokens). Need `ctx.enriched` integration for production. Sub-hourly entries and portfolio rebalancer are P1 unlocks. ALL engine changes require structured /dev with user. NOTE: the <1ms Gate 3 target is backtest throughput, NOT live trading latency — V3 trades weekly.
78. **Classical mean-reversion fails on altcoin 1H** — 4 strategies × 8 tokens, ALL killed. Stops fire 50-75%. Alts trend through MR entry zones. RSI degenerate on hourly. Only paths left: pairs/spread MR, regime-conditioned MR, or shorter timeframes.
79. **V3 must stay on spot** — perp funding (12.46%/yr) eats returns. 1x perp Sharpe 0.47 vs spot 0.56. 2x/3x leverage KILLS via drawdown (-62%/-78%). Sharpe doesn't scale with leverage — drawdowns scale faster than returns.
89. **Trend+Pullback BTC long is the strongest validated trade signal** — Two independent WF agents agree: BTC long-only passes (6/10+ positive windows, both agree profitable). R99-early (2021-2025, 12mo/6mo) shows Sharpe 1.41 combined; R99-late (2023-2026, 180d/90d) more conservative: BTC long PASS, short KILL, ETH KILL.
90. **Trend+Pullback short side is contested** — R99-early says 8/10 PF>1, R99-late says 4/10 KILL. Window config matters. Short is low-frequency and fragile. Safe deployment is long-only.
91. **ETH Trend+Pullback is too sparse** — R99-late: only 22 long trades across 10 windows. Parameter sensitivity 158% degradation. R99-early was optimistic. ETH needs more data or different parameters.
92. **Trump trade sentiment is counterintuitive** — Escalation rhetoric predicts BTC UP (IC=0.114 at 3d). De-escalation posts arrive AFTER crashes (reactive). Only 14 months of data, marginal. Not standalone-worthy.
92. **Trump trade sentiment is counterintuitive** — Escalation rhetoric predicts BTC UP (IC=0.114 at 3d). De-escalation posts arrive AFTER crashes (reactive). Only 14 months of data, marginal. Not standalone-worthy.
93. **Trump non-trade categories are all dead for crypto** — Fed (too sparse), geopolitical (predicts oil not crypto), economic confidence (cheerleading noise), crypto-specific (64 posts = meaningless).
94. **DVOL ROC is an IS artifact** — Full IC=0.10, OOS IC=0.00. Crypto reflexivity (rising IV → rising BTC) didn't persist post-2024. All 4 asymmetric overlay mappings degrade V3. Orthogonal to VRP (corr 0.09) but dead OOS. KILL.
95. **Funding Reversal Short is structurally dead** — Extreme funding events (>0.03%) extinct post-2021. Exchange risk controls (funding caps, reduced leverage 125x→20x) permanently eliminated triggering conditions. R97's Sharpe 7.07 was IS inflation. WF: 0/8 positive windows, 96% profits from top 2 trades. KILL.
96. **Always check if signal conditions still exist** — Funding reversal worked in 2020-2021 because exchanges allowed extreme leverage. Structural market changes can permanently kill signals.
97. **BB Squeeze Breakout is a classic overfit** — IS Sharpe 5-14, OOS deeply negative (-17 to -57). PF 4.54 from full-sample was artifact. Breakouts fail to sustain momentum — 83% exit via cross-inside at a loss. 11/12 param variations fragile. KILL.
98. **Walk-forward kills 4/5 "strong" signals** — Only BTC long Trend+Pullback survived. Funding Short (regime dead), BB Squeeze (overfit), DVOL ROC (IS artifact), Trump sentiment (marginal). Finding #70 reinforced: NEVER deploy without walk-forward.
99. **Two-agent walk-forward is the gold standard** — Running two agents with different window configs (R99 early: 12mo/6mo 2021-2025 vs R99 late: 180d/90d 2023-2026) revealed that short side and ETH are contested. Single-agent WF can be too optimistic or pessimistic depending on window config.
100. **Trend+Pullback is REDUNDANT with V3** — R105: daily return corr=0.688, 98% entry overlap. Both need EMA20>EMA50. T+P is a structural subset of V3 (Sharpe 0.39 vs V3 0.70). 50/50 portfolio HURTS Sharpe (-11%). T+P cannot be a separate portfolio leg alongside V3.
101. **For diversification, need fundamentally different entry conditions** — Shared EMA trend filter means any trend-following variant will be correlated with V3. Need: mean-reversion, funding-rate, cross-asset, or market-neutral signals that fire independently of BTC trend state.
102. **V2 Flexible RSI timing is a genuine V3 improvement** — R106: 6/6 WF windows improved (mean dSharpe +5.47). Defer entry within 168-bar window to first 4h RSI cross-up through 40, fallback to normal rebalance. Return 53%→337%, MaxDD -59%→-28%, win rate 53%→69%. Range-regime -45%→+3%. CONDITIONAL due to low OOS trade count (4 trades).
103. **Entry timing > entry filtering > conviction sizing** — V1 (skip cycles if no pullback) kills trade count and misses strong trends. V3 (scale by RSI) fails because RSI at entry isn't predictive enough (~53 average). V2 (defer within window) wins because it's a free option with fallback.
104. **Intraday Momentum Breakout is the best diversifier** — R107: corr=0.007 with V3, standalone Sharpe 0.594, 5/6 WF positive. Portfolio Sharpe +26.5%, MaxDD halved. Operates on 8h timescale (event-driven) vs V3's multi-week trends. Completely different signal source.
105. **Macro Regime Rotation adds value** — R107: corr=0.010 with V3, 4/6 WF positive. US10Y falling + DXY falling → long, both rising → short. Portfolio Sharpe +12.6%, MaxDD halved.
106. **Mean-reversion and funding contrarian fail as diversifiers** — MR during low-vol is negative Sharpe (BTC structural long bias kills shorts). Funding contrarian loses because BTC rallies further before liquidation cascades.
107. **3-strategy portfolio PASSES all kill criteria** — R110: Equal Weight (33/33/33) Sharpe 0.880 vs V3-only 0.683 (+29%). MaxDD -30.62% vs -58.95% (+28pp). Crash correlations DECREASE (max 0.213) — genuine diversification. RANGE regime fixed: V3 0.189 → Portfolio 0.580. WF 3/6 windows beat V3 (borderline). Each strategy dominates a different regime.
108. **V3-Macro correlation higher than preliminary** — R107 estimated corr=0.010, R110 measured 0.357. Rolling 90d max=0.87. Still below 0.5 kill threshold but watch for convergence. Intraday stays decorrelated (0.188).
109. **Risk parity gives best DD control but sacrifices returns** — -20.25% MaxDD (best), -5.33% worst month, but 12.3% annual return. Equal Weight is the better trade-off at -30.62% DD with 18.63% return.
110. **Macro Regime Rotation KILLED on deep validation (two independent agents agree)**
111. **V3+RSI timing passes extended validation** — R111: 13/18 windows improved (72%), mean dSharpe +5.87. RSI=35 is better than RSI=40 (dSharpe +6.52 vs +4.09). Threshold range 30-40 ALL work (6/6 windows, 100%). RSI-timed entries get 2.82% average price improvement vs window start. Fallback entries outperform RSI-timed (strong trend = no pullback = best trades), but V2 Flexible overall Sharpe 3.287 vs baseline 0.391. Full 168h search window is optimal. Recommended config: RSI=35, search=168h.
112. **Fallback entries are strong-trend captures** — When no pullback occurs in the window (27% of entries), these are inherently the best trades (91.7% WR, +6.73% avg PnL). RSI-timing's value is on the other 73% of entries where a pullback occurs — buying 2.82% cheaper.
113. **2-strategy portfolio (V3+Intraday) PASSES** — R112: 50/50 Sharpe 0.746 vs V3-only 0.683 (+9.2%), MaxDD -36.59% vs -58.95% (+22pp). WF 4/6 windows beat V3 (avg dSharpe +0.228). Removing Macro costs 0.134 Sharpe vs 3-strat, but Macro is KILLED. Risk parity FAILS in 2-strategy case (overloads low-vol Intraday). 50/50 is the recommended allocation.
114. **Each strategy dominates a different regime** — V3: uptrend (3.44) + crisis (1.38). Intraday: range (0.64) + least-bad downtrend (-0.63). Portfolio RANGE Sharpe: 0.189→0.350.
115. **Intraday Momentum Breakout KILLED on deep WF** — R108: mean WF Sharpe -12.2 (5/8 positive windows but mean dragged by -99 sentinel + negative windows). Full-sample Sharpe 0.95 at ATR=6x, but WF can't replicate. R107's Sharpe 0.594 at 2x ATR was IS noise (22% bars fire = too noisy). BTC 1h momentum breakouts are mean-reverting. Trail stops get triggered by volatility before moves complete.
116. **BOTH portfolio diversifiers KILLED** — R108 (Intraday) + R109 (Macro) both fail deep WF. R107 preliminary results were IS artifacts. The R110/R112 portfolio backtests used simplified strategy implementations that worked in-sample but signals don't survive proper validation. V3 standalone with RSI timing is the only surviving strategy.
117. **Preliminary WF (6 windows, no param search) is unreliable for signal screening** — R107 passed Intraday (5/6 WF) and Macro (4/6 WF). Deep WF with param optimization (10 windows) killed both. The simple WF over-optimistically assumes fixed good params. Always run deep WF with param search before promoting signals. — R109: IC non-stationary (flips sign 19 times in 2 years, positive only 58.8%), parameter sensitivity 189.7% (lookback ±20% flips Sharpe sign), RANGE Sharpe -0.422 (kills diversification thesis). Full-period Sharpe only 0.177 vs R107's 0.397 (was in-sample artifact). Long-only Sharpe 0.919 is just BTC beta capture. WF Sharpe mean 1.129 but std 2.251 — driven by outliers.
118. **On-chain signals are the most genuinely decorrelated signal family** — R115: V3 correlations -0.155 to +0.244 (best decorrelation of ANY tested family). Active address growth 14d: WF Sharpe 2.40 (5/6 positive). Exchange netflow 5d: IC=0.134 (bootstrap CI excludes zero). BUT only 568-725 days of data — insufficient for conclusive 10-window WF. R117 fetching 3+ years of data for proper validation.
119. **Gold-BTC predictive relationship is non-stationary and weakening** — R113: IC=0.132 at 14d (strong), but 8/10 top signals weaken across halves. Last 3 WF windows dead (avg near zero). Parameter mode switches across windows. Gold works in RANGE (Sharpe 1.5) but negative in UP+DOWN (78% of days). As BTC matures, gold correlation structure is decoupling.
120. **Vol structure from price predicts vol, not direction** — R114: realized skewness IC strong for vol prediction, but only 1/6 signals passes for directional returns (skewness, median OOS Sharpe 0.904). Consistent with finding #17 (VRP same). Price-derived vol metrics are SIZING signals, not ALPHA signals.
121. **Price-derived signals cannot escape trend information** — R116: 3/6 multi-TF divergence signals had V3 correlation >0.5. When you compare timeframes using EMAs/RSI, the dominant signal is still trend direction. Only truly decorrelated diversifiers come from NON-PRICE data (on-chain, cross-asset, options, positioning).
122. **Wave 15 reinforces finding #88 with precision** — 4 agents, 76 signal variants tested. Price-derived signals (vol structure, multi-TF) stay trend-correlated. Cross-asset (gold) is non-stationary. Only on-chain was promising but data-limited.
123. **On-chain metrics are reflexive, not predictive** — R118 with 9.2 years of data: 52% of signals flip IC sign across regimes (2020-21 vs 2022-23 vs 2024-26). Active addresses collapsed from WF Sharpe 2.40 (R115, 725 days) to 0.13 (R118, 2258 days). Root cause: on-chain activity is driven BY price, not predictive OF it. Network grows when price rises. Exchange flows reflect recent momentum. Redundant with trend-following.
124. **Short data ALWAYS overstates on-chain signal quality** — R115 (568-725 days) found 5/16 pass. R118 (2258 days) killed ALL. The 4-6 WF windows from short data were a statistical fluke. Finding #117 (preliminary WF unreliable) extends to data length: insufficient history creates selection bias in walk-forward.
125. **Diversifier search across tested signal families found no standalone additions to V3** — 48 signals tested across 12 sessions: trend (EMA, ROC), mean-reversion (RSI, BB, z-score), momentum breakout (ATR), positioning (L/S, taker), macro (DXY, 10Y, oil, gold), volatility (VRP, DVOL, skew, vol structure), cross-sectional (ranking), pairs/arb, seasonal, ETF flow, on-chain (netflow, addresses, tx vol, exchange balance), multi-timeframe divergence. NONE survive deep walk-forward as standalone diversifiers for V3. The only validated components are OVERLAYS on V3 itself (positioning, VRP, RSI timing). Accept V3 standalone as the production system.
126. **MVRV has real negative OOS IC but IS/OOS sign inconsistency kills overlay use** — OOS IC=-0.162 (t=-4.91), but IS IC=+0.003. High MVRV → lower returns is real OOS but wasn't present IS. V3 overlay dSharpe=-0.138. Consistent with finding #85: standalone IC ≠ overlay effectiveness.
127. **Auto-research pipeline library fully screened (342/342)** — 342 candidates (57 signals × 6 configs) across 10 families run on 2026-03-21. Zero survivors. Vol signals killed by MaxDD (40-91%), funding by PF<1. No re-run needed.
128. **Funding rate variance collapsed 81% post-2022H2** — std dropped 0.000312 → 0.000061. ALL funding derivatives (flip, RoC, dispersion) structurally dead. Extends finding #95 (extreme funding extinct) to ALL funding-derived signals.
129. **Weekly-windowed IC with overlapping returns is dangerously inflating** — Funding dispersion showed t=4.6 with weekly windows but t=-2.15 (opposite direction!) with non-overlapping observations. ALWAYS use strictly non-overlapping forward returns for IC computation.
130. **s320 V3 backtest +387% was entirely from 1.5x leverage bug on spot** — Corrected: +2.9% ann (60mo), +0.2% (12mo). Signals are GOLD-validated (Top Trader L/S IC=-0.166, VRP IC=0.268). Implementation needs architectural rework — see #131.
131. **s320 implementation IS faithful to research but V4 sizing architecture creates mismatch** — Full fidelity audit: overlay thresholds match exactly, 20/50 EMA was correct choice (R73 confirmed), RSI timing implemented. ROOT CAUSE: overlays reduce size_multiplier → hits V4 min_size floor → 86% rejection rate (752/877 entries). Overlays as continuous size scalers don't work with min_size=$200. Fix options: (a) overlays as binary entry gates, (b) floor size_multiplier at 0.3, (c) lower min_position_usd for BTC-only pool.
80. **Cross-sectional momentum is long-only beta** — all 12 configs killed by MaxDD (61-86%). When crypto drops, ALL tokens drop. Momentum ranking doesn't hedge direction. BTC corr 0.56-0.68. Need market-neutral (pairs/arb) for true decorrelation.
81. **"Easy paths" to 300% are all dead** — leverage (funding), alt momentum (chop), alt MR (trend-through), cross-sectional (beta). Higher returns require: multiple uncorrelated strategies, new data sources, or market-neutral approaches.
82. **ETF flow HURTS V3 as overlay despite strong standalone IC** — IC=+0.191 at 14d, but overlay clips gains during strong uptrends. All 3 variants degrade V3 (worst: dSharpe -0.501, p=0.02). Signal is independent (corr < 0.15) but horizon-mismatched with weekly rebalance.
83. **Seasonal effect is REAL but period-dependent** — PIT OOS kills all 4 calendar variants, but walk-forward shows 5/6 windows improved for ALL variants (mean dSharpe +0.43 to +1.47). The failing window is exactly the PIT period. Conditional version (reduce weak months + bearish signals) worth testing.
84. **Pairs/stat-arb doesn't work in crypto** — no fundamental cointegration (ETH/BTC crosses zero once/38d), basis arbed to -4.3bps by AMMs, short legs reverse violently. Market neutrality achievable but with negative alpha.
85. **Standalone overlay IC does NOT predict overlay effectiveness** — ETF flow (IC=0.191) and seasonal (clear pattern) both HURT V3 as overlays. Only positioning (IC=-0.204) and VRP (IC=0.268) improve V3. Successful overlays must be ORTHOGONAL to the base signal's edge, not just independently predictive.
86. **VRP-conditioned seasonal is a genuine V3 enhancement** — reduce in weak months (Apr-Sep) ONLY when vrp_z < -0.5 (turbulence). PIT dSharpe +0.119, WF 4/6 improved, MaxDD 0/6 worsened. Small but consistent. Not statistically significant yet (p=0.505). Optional V3 addition.
87. **V3+carry portfolio structure is correct but carry is dormant** — corr=0.011 (excellent decorrelation). Carry active 89% (2021) → 0% (2025-26). Dynamic allocation is the right approach. Monitor funding — if 30d mean > 0.01%, deploy.
88. **Research has reached diminishing returns** — 8 agents in session 8, only 1 marginal pass. All obvious approaches tested: leverage, alts (trend+MR), cross-sectional, pairs/arb, seasonal, ETF flow, carry portfolio. V3 BTC spot (+17.52%, Sharpe 0.56) is the only viable new strategy. Next alpha requires new data sources or fundamentally different approaches.

---

## Data Inventory

| Dataset | Location | Rows/Size | Date Range | Tokens |
|---------|----------|-----------|------------|--------|
| Binance L/S (3 types, 20d) | `data/alternative/binance_positioning/` | 9,500 each | Mar 3-23 | 19 |
| Bybit L/S daily (EXTENDED) | `data/alternative/ls_ratio_extended/` | 17,529 | 2020-08 to 2026-03 | 10 |
| OKX L/S daily | `data/alternative/ls_ratio_extended/` | 1,800 | 180 days | 10 |
| Binance OI history | `data/alternative/binance_oi/` | 1 parquet | ~20 days | ? |
| Taker buy/sell volume | `data/alternative/binance_positioning/` | multi | ~8 months | 19 |
| Stablecoin supply | `data/alternative/stablecoin_supply/` | Multi-year | Years | 4 |
| Fear & Greed | `data/alternative/fear_greed/` | 2,969 | 2018-2026 | 1 |
| Macro (DXY, 10Y, VIX, Gold, SP500, NQ) | `data/alternative/macro/` | 2,267 days | 2020-2026 | 6 |
| Oil WTI | `data/alternative/macro/oil_wti.parquet` | 6,422 | 2000-2026 | 1 |
| Deribit DVOL daily | `data/alternative/deribit_options/dvol/` | 1,826 days | 2021-2026 | BTC, ETH |
| ETF flows (BTC) | `data/alternative/etf_flows/btc_etf_daily.parquet` | 533 | 2024-01 to 2026-03 | 1 |
| Funding rates | `data/live/funding/` | Full history | Years | 50+ |
| Price data (spot+perp) | `data/` | Full history | 2020-2026 | 116+ spot, 165+ perp |
| OI divergence panel | `research/oi_divergence_panel.parquet` | 7,263 | 2024-2026 | 9 |

---

## Current Research Phase: PHASE 3B — Sizing Optimization + Leverage Testing

### Phase 2 Edge Validation COMPLETE (session 5/6)

All 5 "enough research" criteria met:
1. **OOS edge survives fees**: YES — L/S Div break-even 291bps (29x actual), Top Trader 231bps (23x)
2. **Works in up AND down markets**: YES — regime-switched activation (validated per-regime ICs)
3. **Multiple uncorrelated signal families**: YES — positioning (rho=-0.11 vs macro)
4. **Combination architecture validated**: YES — agreement regimes 9% 14d spread, kitchen-sink kills IC
5. **Clear implementation path**: YES — regime detection + signal activation map documented

### Phase 2 Results (R55-R57)

**R55 Cost-Adjusted Validation:**
- L/S Divergence: break-even 291bps (29x actual cost), net alpha +133%/yr — ROBUST
- Top Trader L/S: break-even 231bps (23x actual), net alpha +111%/yr — ROBUST
- US10Y+DXY Regime: break-even 140bps, net alpha +64%/yr — SURVIVES
- Skew_30d: DEAD standalone (-66% net alpha after costs)
- Net Taker Volume: DEAD (3bps break-even, killed by turnover)

**R56 Regime-Split Validation:**
- NO signal is always-on (every signal is regime-dependent)
- Oil 20d Mom: CRISIS-ALPHA (IC=-0.307 downtrend, -0.297 crisis)
- Top Trader L/S: RANGE specialist (IC=-0.288 range, -0.222 crisis)
- US10Y+DXY: DOWNTREND-specific (IC=-0.107 downtrend)
- L/S Divergence: UPTREND+RANGE (IC=-0.139 uptrend, OOS strengthens to -0.35 range)
- Regime switching improves OOS Sharpe by +0.39 to +0.60

**R57 Macro+Positioning Combo:**
- Signals uncorrelated (rho=-0.11) — independent information
- CRITICAL: Macro IC FLIPPED sign OOS (IS=+0.154, OOS=-0.128) — DO NOT use as continuous signal
- Simple rank combo fails OOS (IC=0.002)
- BUT agreement regimes are directionally informative: both bearish = -4.89% 14d, both bullish = +3.91%
- **Architecture**: Use macro as categorical REGIME FILTER, positioning as continuous ALPHA signal

### Phase 3 Agents In-Flight

| Agent | Task | Status |
|-------|------|--------|
| R58 | Regime-switched backtest simulation (4 variants) | **DONE — FAIL standalone, crisis hedge Sharpe 3.10 works** |
| R59 | Parameter sensitivity + walk-forward (6 params × 3 windows) | **DONE — FAIL standalone, 5/6 params robust but WF 1/3** |
| R60 | Overlay backtest on trend-following base (correct architecture) | **DONE — WORKS: +0.31 OOS Sharpe, +5.9% return, NEEDS_TUNING** |
| R61 | Live performance gap analysis | **DONE — R61 conclusion was WRONG. Root cause: backtest bug (close as stop price). Strategies genuinely fail after fix.** |
| R62 | VRP sizing overlay test | **DONE — SUPER-ADDITIVE: combined Sharpe +0.68, p<0.05, rho=0.031** |

### Phase 3 Conclusions

1. **Standalone positioning system FAILS** (R58, R59) — signals predict 14d returns, daily rebalancing destroys edge
2. **Overlay on trend-following WORKS** (R60) — +0.31 OOS Sharpe, +5.9% return, -2.5% DD
3. **Crisis hedge REDUNDANT with trend-following** — trend exits before crisis fires
4. **Macro agreement boost NEGLIGIBLE** — +0.012 Sharpe, not worth complexity
5. **Correct production architecture**: trend base + positioning sizing overlay + weekly rebalance

### R63: Strategy Triage (OOS 2025-09 to 2026-03)

**ALIVE:**
- s29 (funding carry): +5.60%, PF 4.93, MaxDD -1.09%
- s65 (funding carry v4): +4.04%, PF 4.93, MaxDD -0.47%
- s72 (s65 + trail): +4.04%, identical to s65
- s37 (momentum trail): +3.87%, PF 1.30, MaxDD -3.43% (thin edge)

**DEAD:**
- s32 (regime): 0 trades in current regime
- s44 (basis carry): -1.39%, basis compression
- s56 (5x leverage momentum): -2.71%, leverage amplifies stop-fix losses

**Key insight**: Carry strategies (funding income) survive because their edge doesn't depend on price prediction. Momentum/leverage strategies died because the bug fix worsens stop execution.

### R64: Carry Overlay Test (DONE — KILLED)
- Carry strategies (s29/s65) are DIRECTIONAL (naked perp), not delta-neutral
- **Overlay HURTS carry IS Sharpe**: 1.05 → 0.55 (positioning), 0.99 (VRP), 0.54 (both)
- **OOS is entirely flat**: funding rates collapsed after mid-2025, ZERO trades in 2025 OOS
- Carry is already contrarian — adding contrarian positioning overlay is REDUNDANT
- **One actionable finding**: positioning predicts 7d funding changes (IC=-0.1104, p<0.001)
- **Verdict**: DO NOT add generic overlay to carry. Carry-specific funding overlay possible but carry itself is dormant.
- Scripts: `research/carry_overlay_test.py`, `research/carry_overlay_analysis.md`

### R65: New Stopless Momentum Strategy (DONE — WORTH PURSUING)
- 20/50 EMA crossover + Positioning + VRP overlays, NO hard stop losses
- **V3 (EMA + Pos + VRP)**: OOS Return +17.52%, Sharpe 0.56, MaxDD -20.2%, PF 1.16
- Beats s29 (+5.6%) and s37 (+3.87%) by wide margin
- EMA crossover exits computed on close price — immune to stop-price bug fix
- **ADX filter HURTS OOS**: V4 (with ADX) dSharpe = -0.204 vs V3 — classic overfitting
- Sequential overlay contribution: Positioning +0.211, VRP +0.421 (VRP contributes more)
- 5/6 criteria pass (only misses PF > 1.3)
- Scripts: `research/new_momentum_strategy_test.py`, `research/new_momentum_strategy_results.md`

### R66: Multi-Token V3 Validation (DONE — PARTIAL)
- V3 profitable on BTC (Sharpe 0.56) and BNB (Sharpe 0.86) only; 2/10 alts positive
- Overlays improve 7/10 tokens (mean dSharpe +0.105) — overlays generalize, base doesn't
- Altcoins too choppy for 20/50 EMA trends; mean alt V3 OOS Sharpe: -0.053
- V3 beats buy-and-hold on 100% of tokens (protective value real)
- Scripts: `research/multi_token_v3_test.py`, `research/multi_token_v3_results.md`

### R67: Walk-Forward + Parameter Sensitivity (DONE — ROBUST 6/6)
- Walk-forward: 5/6 windows positive OOS Sharpe, mean 0.517, IMPROVING trend
- Only negative: H2 2022 FTX/Luna crash (expected for long-only no-stops)
- Parameter sensitivity: 0 KILL flags, max degradation 8.2% (well under 30% threshold)
- All 18 parameter configs positive (range 0.510 to 0.708)
- Corner cases: all 5/5 positive (range 0.531 to 0.824)
- Tighter positioning threshold (1.2) improves to 0.708 — default may be conservative
- Scripts: `research/v3_walkforward_sensitivity.py`, `research/v3_walkforward_sensitivity_results.md`

### R68: Regime Analysis (DONE — KNOWN WEAKNESS in RANGE)
- UPTREND: Sharpe 6.18, overlays slightly conservative (reduce from 464% to 320% ann)
- RANGE: Sharpe -1.01, overlays help (+0.231 dSharpe) but can't fix EMA whipsaws
- DOWNTREND: 91% flat, minimal losses
- CRISIS: 99.6% flat, correctly exits
- Seasonality: Q1 best (+30.4%), Q2 worst (-9.2%), April/September worst months
- Max consecutive losing weeks: 4 — contained
- Scripts: `research/v3_regime_analysis.py`, `research/v3_regime_analysis_results.md`

### R69: Whipsaw Filter Enhancement (DONE — initially promising, then KILLED by R71)
- Tested 26 filter variants across 6 categories
- Initial winner: whipsaw filter (>2 crosses in 60d → flat), OOS Sharpe 0.556→0.982
- **R71 KILLED IT**: Walk-forward showed 0/6 windows improved. The entire +0.426 improvement came from ONE 16-day event (Oct 1-16 2025). Mechanically redundant with base signal — only overrode 16 days total.
- KILLED: ADX (all thresholds), BB width (all), realized vol (all 8 variants), whipsaw (single-event artifact)
- **RANGE weakness remains unsolved** — this is the structural cost of trend-following
- Scripts: `research/v3_range_filter_test.py`, `research/v3_range_filter_results.md`

### R70: Production Prototype (DONE — v4 Compatible)
- Draft at `research/v3_prototype_strategy.py`, follows StrategyResult interface
- All v4 compatibility checks pass, BTC-only filter works
- Performance: 3.63ms/call (needs pre-computation for 1ms target)
- External data dependency needs `ctx.enriched` wiring
- Scripts: `research/v3_prototype_strategy.py`, `research/v3_prototype_notes.md`

### R71: V3.1 Walk-Forward (DONE — KILLED whipsaw filter)
- Walk-forward: 0/6 windows showed ANY improvement from whipsaw filter
- Root cause: filter only overrode 16 days total, all in Oct 2025 — single-event artifact
- EMA whipsaws are mechanically redundant with base signal (when crosses>2, EMA already bearish)
- R69's +0.426 OOS dSharpe was entirely this one episode
- **Lesson**: point-in-time filter testing is dangerous; always walk-forward validate
- Scripts: `research/v3_1_walkforward_test.py`, `research/v3_1_walkforward_results.md`

### V3 Consolidated Verdict: PRODUCTION-READY (BTC Only, no whipsaw filter)
- Walk-forward: ROBUST (5/6 positive, improving trend)
- Parameters: ROBUST (0 KILL flags, all configs positive)
- Multi-token: BTC + BNB only (alts fail base, overlays generalize)
- RANGE weakness: UNSOLVED (structural cost of trend-following, accept it)
- **Final OOS metrics: Sharpe 0.56, Return +17.52%, MaxDD -20.2%**

### R72: Tight Positioning Threshold (DONE — KEEP defaults)
- Tested pos_high_thresh 1.2, 1.35, 1.5 across 6 walk-forward windows
- V3-tight (1.2) hurts 3/6 windows, mean dSharpe +0.029 (noise)
- Non-monotonic: mid (1.35) > tight (1.2) > default (1.5) — hallmark of noise
- R67's +27% improvement was single-window artifact (2025-H1)
- Scripts: `research/v3_tight_threshold_test.py`, `research/v3_tight_threshold_results.md`

### V3 FINAL SPEC (LOCKED — no more tuning)
```
Base: 20/50 EMA crossover (long when fast > slow, flat otherwise)
Positioning: 30d z-score, thresholds ±1.5/±0.5
VRP: 60d z-score, thresholds 1.0/-0.5/-1.5
Rebalance: Weekly | BTC only | No stops
OOS: Sharpe 0.56, Return +17.52%, MaxDD -20.2%
Walk-forward: 5/6 positive, mean 0.517
Parameters: 0 KILL flags across all tests
```

### R73: Alt-Token Base Signal Scout (DONE — Dual ROC 30/90 WINS)
- Tested 5 base signals × 5 alt tokens (ETH, SOL, BNB, XRP, DOGE)
- **Winner: Signal D (Dual ROC 30/90)** — requires both 30d and 90d ROC positive
- D base-only mean OOS Sharpe: +0.416 (vs V1 EMA: +0.068), positive on 4/5 tokens
- D+overlays mean OOS Sharpe: +0.535, overlays add +0.118 dSharpe
- BNB standout: D+overlays Sharpe 2.70, MaxDD -9.1%
- SOL fails every signal — exclude or gate behind BTC uptrend
- Slower EMA (50/200) makes things WORSE — fix is stricter entry, not slower MA
- Scripts: `research/alt_base_signal_test.py`, `research/alt_base_signal_results.md`
- **NEEDS walk-forward validation before deployment**

### R74: Signal D Walk-Forward on Alts (DONE — ALL KILLED)
- ETH: MARGINAL (50% win rate, one-window driven)
- BNB: MARGINAL (50%, extreme dispersion)
- XRP/DOGE: KILL
- Signal D is regime-dependent (bull-only). Alt strategies dead for now.
- Scripts: `research/signal_d_walkforward_test.py`, `research/signal_d_walkforward_results.md`

### R75: V4 Engine Limitations Audit (DONE — see constraints section below)

### R76: Cross-Sectional Momentum (DONE — ALL KILLED)
- Tested 3 variants × 4 lookbacks (12 configs) on 43-token universe
- ALL killed by MaxDD (range -61.5% to -85.9%, threshold -30%)
- Best: simple XSMOM 14d: Sharpe 0.91, Return +114.8%, MaxDD -64.1% — KILL
- Root cause: long-only crypto = 100% beta exposure. When market drops, all tokens drop together.
- BTC correlation 0.56-0.68 — NOT sufficiently decorrelated from V3
- IS→OOS degradation massive (IS Sharpe 1.15-1.82 → OOS 0.09-0.91)
- Scripts: `research/cross_sectional_momentum_test.py`, `research/cross_sectional_momentum_results.md`

### R77: V3 Leveraged Perp Variant (DONE — SPOT IS OPTIMAL)
- Tested V3 on BTC perp at 1x, 2x, 3x leverage
- 1x perp: Sharpe 0.472, Return 10.2%, MaxDD -37.4% — PASS but WORSE than spot (17.5%)
- 2x: KILL — MaxDD -62.4%, 9 near-liquidations
- 3x: KILL — MaxDD -78.4%, 63 liquidation events
- Funding drag: 12.46% annualized (17% of gross returns)
- Sharpe DOESN'T scale with leverage (0.47 at all levels) — drawdowns scale faster
- V3 MUST stay on spot. Perps are strictly worse.
- Scripts: `research/v3_leveraged_test.py`, `research/v3_leveraged_results.md`

### R78: Alt-Token Mean-Reversion (DONE — ALL KILLED)
- Tested 4 MR strategies × long/short × 8 tokens (64 configs)
- ALL 8 variants killed: mean Sharpe -0.52 to -1.30, stops fire 50-75%
- Root cause: alts trend through MR entry zones, stops dominate outcomes
- RSI degenerate on 1H bars (zero trades — indicator too dampened)
- Z-Score Long-Only "least bad" (mean Sharpe -0.519), still deeply negative
- IS→OOS severe: Bollinger Long +0.218 IS → -0.807 OOS
- Classical MR doesn't work on altcoin spot 1H. Too volatile for fixed-parameter reversion.
- Scripts: `research/alt_mean_reversion_test.py`, `research/alt_mean_reversion_results.md`

### R79: Pairs/Stat-Arb (DONE — ALL KILLED)
- Tested 3 strategy families × 11 configs: cointegration pairs, cash-and-carry basis, long/short momentum spread
- ALL KILLED: cointegration Sharpe -4.5 to -1.1, basis 0 trades at 50bps, L/S spread Sharpe -0.235
- Cointegration fails: crypto pairs don't mean-revert. ETH/BTC spread crosses zero once/38 days, 90.5% timeout
- Basis is dead alpha: compressed from +4.5bps (2021) to -4.3bps (2025-26). AMMs arbed it away.
- L/S spread is market-neutral (BTC corr -0.063) but negative alpha — short leg reverses violently
- Scripts: `research/pairs_statarb_test.py`, `research/pairs_statarb_results.md`

### R80: V3 Seasonal Sizing Overlay (DONE — PIT KILL, WF PROMISING)
- Tested 4 calendar overlays: binary month, proportional, quarter, sell-in-May
- PIT OOS (Jan 2025 - Mar 2026): ALL underperform V3 base (dSharpe -0.30 to -0.95) — KILL
- BUT walk-forward: ALL 4 variants improve 5/6 windows, mean dSharpe +0.43 to +1.47
- PIT/WF divergence: PIT period is the ONE window where seasonality didn't help (Q1-heavy, V3 already long)
- Sell-in-May cuts MaxDD from -20.2% to -12.4% but loses return
- **Not ready as blanket calendar rule** — conditional version (reduce weak months ONLY when other signals also bearish) worth testing
- Scripts: `research/v3_seasonal_overlay_test.py`, `research/v3_seasonal_overlay_results.md`

### R81: ETF Flow Overlay on V3 (DONE — ALL KILLED)
- Tested 3 flow overlays: z-score sizing, flow momentum, flow acceleration
- ALL degrade V3: best dSharpe = -0.051, worst = -0.501 (statistically significant negative impact)
- Signal IS independent (corr < 0.15 with positioning/VRP) — redundancy NOT the issue
- Root cause: overlay clips gains during strong uptrends. When V3 is correctly long, flow overlay reduces sizing on outflow days
- Horizon mismatch: 14d signal with weekly rebalance
- Only 26 months of data — revisit in 2027 with 3+ years
- Scripts: `research/v3_etf_flow_overlay_test.py`, `research/v3_etf_flow_overlay_results.md`

### R82: Conditional Seasonal Overlay (DONE — 1/4 PASS: V2 VRP-conditioned)
- Tested 4 conditional overlays: positioning-conditioned, VRP-conditioned, both-signals, gradient
- **V2 (VRP-conditioned) PASSES**: PIT OOS dSharpe = +0.119, WF 4/6 windows improved, 0/6 MaxDD worsened
- V2 logic: in weak months (Apr-Sep), if vrp_z < -0.5 → 0.3x, neutral → 0.7x, complacent → 1.0x
- Converts blanket seasonal's -0.951 PIT penalty into +0.119 improvement
- V1 (positioning) narrowly killed: PIT dSharpe = -0.025, but WF 4/6 positive
- V3c (both signals) and V4 (gradient) killed by PIT despite strong WF (5/6 each)
- Not statistically significant (p=0.505) — need more OOS data to confirm
- Scripts: `research/v3_conditional_seasonal_test.py`, `research/v3_conditional_seasonal_results.md`

### R83: V3+Carry Portfolio (DONE — KILLED, carry dormant)
- Tested 4 portfolio allocations: 60/40, 50/50, risk parity, dynamic
- KILLED: carry active only 5.2% of days (threshold: 20%). Funding collapsed: 89% (2021) → 0% (2025-26)
- Correlation excellent (0.011 OOS) — truly independent strategies. Portfolio theory is sound.
- Risk parity disastrous: allocates 92% to carry (near-zero vol). Dynamic allocation is correct approach.
- **When funding recovers**: use Dynamic allocation (100% V3 when dormant, 60/40 when carry active)
- Scripts: `research/v3_carry_portfolio_test.py`, `research/v3_carry_portfolio_results.md`

### Next Actions (Priority Order) — Updated Session 15

**Signal discovery: 54 signals tested across 14 sessions.** What's been tried and what remains open is documented below.

**P0 — Fix s320 sizing: COMPLETE.**
1. ~~**Fix s320 leverage bug:**~~ **DONE** — MAX_POSITION 1.5→1.0, cap_multiplier=8.0, max_trade_pct=0.95, sizing_overrides added.
2. ~~**Fix s320 config for BTC-only:**~~ **DONE** — cap_multiplier=8.0, concentration_limit=1.0, max_trade_pct=0.95 applied.
3. ~~**Run corrected backtest:**~~ **DONE** — s320 corrected: +2.9% ann over 60mo (NOT 387%), +0.2% last 12mo. Leverage bug was root cause of inflated V3 returns.
4. **All 108 min_size rejections** traced to size_multiplier=0 at entry bars (overlay architecture issue, not sizing bug).
5. **Knowledge file created:** `knowledge/V4_SIZING_PIPELINE.md`.
6. **s320 fidelity audit COMPLETE** — Implementation matches research spec exactly (overlay thresholds, base signal, RSI timing). Signals are GOLD-validated. Problem is ARCHITECTURAL: overlays as continuous size_multiplier scalers hit V4's min_size floor, causing 86% entry rejection. NEEDS REWORK — not dead.

**P0.5 — Rework s320 overlay architecture: COMPLETE.**
- [x] **Option A: Binary gates** — `s320a_binary_gates.py`. Best risk-adjusted: PF=1.42, DD=-5.65%, 91 trades, 0 rejections. Avg PnL $206/trade.
- [x] **Option B: Floor at 0.3** — `s320b_floored_mult.py`. Best total return: +9.8%, PF=1.39, 123 trades. Same DD as original.
- [x] **Option C: Lower min_pos** — NO EFFECT. Rejections are from sm=0.0 bars (overlay=0 within entry window), not from min_size floor.
- [x] All three backtested. Strategy VALIDATED: WF=PASS, CPCV=PASS, PBO=13%.
- **WINNER: s320a (binary gates)** — cleanest architecture, best risk metrics. Promote to paper trading.
- **Knowledge files created:** `knowledge/V4_SIZING_PIPELINE.md`, `knowledge/RESEARCHER_BEST_PRACTICES.md` (427 lines, 9-layer parameter catalog + 12 ground rules)

**P0.7 — ACTIVE: Test s320 with newly-unlocked sizing overrides (Session 15)**
- Tier 1 fixes (2026-03-26) made `kelly_mult_floor/range`, `cap_pct_floor/range`, `adv_scaling_divisor` per-strategy overridable
- This directly addresses finding #131: s320 was capped at 12% capital utilization → 88% idle
- **R133 in flight:** s320 sizing sweep with cap_pct up to 30% and concentration_limit=1.0
- **R135 in flight:** V3+overlay on BTC perps with VRP-scaled dynamic leverage (1.0-1.5x)
- **R134 in flight:** Multi-strategy portfolio (s62+s65) with aggressive per-strategy sizing

**P1 — V4 architecture improvements:**
4. ~~**Allow strategy-level overrides for kelly_range, cap_pct_range, target_vol**~~ — **DONE (Tier 1 fixes, 2026-03-26):** 5 params moved from NON_OVERRIDABLE to SAFETY_RAILS.
5. **Add sizing diagnostics logging** — when a position is clipped, log WHICH constraint bound and by how much. Currently silent.
6. **Promote remaining hardcoded params to config** — `vol_adj` target (0.02), unrealized PnL clamp (0.85). (vol_floor, unrealized_pnl_floor, funding_buffer_pct remain non-overridable.)
7. ~~**Document all 60+ parameters**~~ — Covered in `knowledge/V4_SIZING_PIPELINE.md` and `knowledge/RESEARCHER_BEST_PRACTICES.md`.

**P2 — Monitoring & conditional triggers:**
8. ~~Implement V3+RSI Timing~~ **DONE** — Integrated into s320 as Layer 5.
9. ~~Re-run R115 on-chain with extended data~~ **DONE (R118) — on-chain is reflexive, not predictive.**
10. **Monitor funding rates** — if 30d mean > 0.01%, deploy V3+carry dynamic portfolio. Currently -0.000009/hr (wrong direction).
11. **Monitor paper pools** — R136 in flight: audit current 7 pools for operational status.
12. **Optional (paid data):** Tardis.dev LOB/liquidation data ($199/mo) could unlock microstructure signals.

### What Has Been Tried (Signal Families)

| Family | # Signals Tested | Best Result | Status |
|--------|-----------------|-------------|--------|
| Trend-following (EMA, ROC, MACD) | 8+ | V3 EMA 20/50 Sharpe 0.56 | **GOLD — production system** |
| Positioning (L/S, taker, OI) | 10+ | Top Trader L/S IC=-0.166, L/S Div IC=-0.204 | **GOLD — overlay on V3** |
| Volatility (VRP, DVOL, skew, vol structure) | 8+ | VRP z-score IC=0.268 (sizing) | **GOLD — overlay on V3** |
| Macro (DXY, US10Y, oil, gold) | 8+ | US10Y IC=-0.375, DXY+10Y IC=0.305 | **PASS as regime filter only** |
| Funding-derived | 6+ | Funding variance collapsed 81% post-2022 | Structurally dead post-2022 |
| On-chain (netflow, addresses, tx vol) | 36 variants | Reflexive (driven BY price) | Not predictive |
| ETF flow | 3 | IC=+0.191 but clips V3 gains | Horizon mismatch |
| Cross-sectional ranking | 12 configs | Long-only beta, -61% to -86% DD | Killed by correlation |
| Pairs/stat-arb | 11 configs | No fundamental cointegration | Killed |
| Mean-reversion (RSI, BB, z-score) | 8+ | Alts trend through MR zones | Killed at 1h timeframe |
| Seasonal/calendar | 4 variants | VRP-conditioned: marginal pass | Optional V3 addition |
| Momentum breakout (intraday) | 4+ | IS artifact, fails deep WF | Killed |
| Sentiment (Trump trade) | 5 categories | IC=0.114 at 3d, 14mo only | Marginal, insufficient data |
| Multi-TF divergence | 7 variants | Price-derived → trend-correlated | Killed |
| Auto-pipeline screen | 342 candidates | 0 survivors across 10 families | Screened |

### What Has NOT Been Tried (Open Avenues)

| Avenue | Why Not Tried | Potential | Blocker |
|--------|---------------|-----------|---------|
| **V3+overlay on perps with dynamic leverage** | V3 perp tested without overlays | HIGH — overlay reduces DD, making leverage viable | **R135 testing now** |
| **Unlocked sizing (cap_pct 12%→30%)** | SAFETY_RAILS were hardcoded until today | HIGH — 88% capital was idle | **R133 testing now** |
| **Multi-strategy with per-strategy sizing curves** | Params were non-overridable | MEDIUM — each strategy gets tuned ADV curve | **R134 testing now** |
| **LOB microstructure (bid-ask, depth, flow toxicity)** | Requires Tardis.dev ($199/mo) | MEDIUM — completely different signal class | Budget decision |
| **Sub-hourly entries (5m/15m bars)** | Engine supports exits only | MEDIUM — captures momentum missed at 1h | P1 engine work |
| **Options gamma exposure (GEX)** | Free data lacks OI by strike | MEDIUM — proxy VRP is good but real GEX is better | Deribit snapshot cron |
| **Cross-exchange arbitrage** | Multi-exchange infra needed | LOW-MEDIUM — latency-sensitive | Engineering heavy |
| **Portfolio-level rebalancer** | Not implemented | MEDIUM — tactical capital rotation between strategies | P1 engine work |
| **Limit order simulation** | Not implemented | LOW — captures 5-10% annual maker rebate | P2 engine work |
| **Regime-specific strategy rotation** | Tested but failed at macro level | MEDIUM — could work with better regime detection | Need non-price regime signals |
| **Carry strategies (when funding recovers)** | Funding collapsed since mid-2025 | CONDITIONAL — V3+carry portfolio is sound (corr=0.011) | Monitor funding rates |
| **Alternative timeframes (4h, daily)** | Mostly tested at 1h | LOW-MEDIUM — daily trend following reduces costs | Different execution model |
| **Non-crypto macro overlay (rates, commodities)** | Tested as continuous signal (failed) | MEDIUM — may work as categorical regime switch | Needs more OOS data |
| **Custom sizing models** | Registry just wired today | MEDIUM — risk-parity, vol-target sizing alternatives | **Can test now** |
| **Custom slippage models** | Registry just wired today | LOW — mainly for research accuracy | **Can test now** |

### Session 15: Tier 1 Fixes + Sizing Optimization (2026-03-26)

**Goal:** Deploy Tier 1 quant fixes, test if unlocked sizing resolves capital utilization bottleneck, test V3+overlay on perps with dynamic leverage, audit paper pools.

**Tier 1 Fixes Deployed:**
- Sizing model registry wired (SizingModel protocol → KellySizing, 3 call sites)
- Slippage model registry wired (SlippageModel protocol → SqrtImpactSlippage, 6 call sites + state)
- 5 ADV curve params moved from NON_OVERRIDABLE to SAFETY_RAILS (per-strategy overridable)
- BarContext expanded (volume, vol_20, ret_1h for custom exit handlers)
- Paper engine raw mode support added
- 73 new tests, 1246 total passing

**R136: Paper Pool Audit — DONE**
- Runner is DOWN (21h stale). 29 open positions unmonitored.
- KILL: s106 (0 trades), s107 (0 trades), s98 (2 trades, both losses)
- KEEP: s58+s65 (+5.28%, only s65 sub profitable), s72 (+2.02%), s65 solo (+1.38%)
- WATCH: s62 (+0.18%, marginal), s320 (22 ticks, no trades yet)
- Funding drag: -$23K across pools in ~2 weeks (annualized would consume alpha)
- Report: `research/paper_pool_audit_2026_03_26.md`
- **NOTE:** Position overlap between pools is irrelevant — see Paper Trading Rules below

**R133: s320 Sizing Sweep — DONE (sizing overrides have ZERO effect)**
- cap_pct_floor/range overrides produce byte-identical results to baseline
- ROOT CAUSE: For BTC ($1.5-2B ADV), binding constraint is `raw` (Kelly formula), NOT `cap`. Cap was already 96% of equity via cap_multiplier=8.0.
- Average position = 49% of equity. "Idle capital" is from signal sparsity (flat ~50% of time), not sizing.
- Increasing target_vol just amplifies DD: target_vol=0.05 → 77% avg position but -50.6% MaxDD vs -33.6% baseline.
- **Verdict:** Current s320 config is near-optimal for BTC. Path to deploying more capital = multi-strategy diversification.
- Report: `research/s320_sizing_sweep_results.md`

**R134: Multi-Strategy Portfolio Optimization — DONE (all configs negative, funding bug suspected)**
- s62+s65 50/50 with aggressive sizing = best relative config (+14-36% Sharpe improvement)
- BUT all 22 configs deeply negative (-17% to -84% returns)
- **CRITICAL:** Backtest vs paper divergence. Paper: s62 +9.6%, s65 +4.9%. Backtest: deeply negative.
- SUSPECT: funding cost modeling bug — s65 (carry) should EARN funding on shorts, backtest may charge it one-directionally. s65 accumulates $65K-$96K funding cost on $200K capital = wrong direction.
- **THIS NEEDS INVESTIGATION** — potential engine bug affecting all perp strategy backtests.
- Report: `research/portfolio_sizing_optimization_results.md`

**R135: V3+Overlay on BTC Perps + Dynamic Leverage — DONE (100%+ NOT achievable)**
- 7 experiments tested. Best: dynamic 0.5-2x → 22.6% annual, Sharpe 0.642, MaxDD -52.2%
- Dynamic leverage IS a valid technique: +0.148 Sharpe over fixed 1x, reduces funding drag (9.6% vs 13.5%)
- All leveraged configs KILLED by MaxDD >40% (BTC Jan-Feb 2026 crash $126K→$63K)
- Only 1x perp passes: 10.9% annual, Sharpe 0.494, MaxDD -37.3%
- **CORRECTION to finding #79:** Prior perp test DID include overlays. Numbers match. Overlays-on-perps was not an untested path.
- Zero liquidation events across all experiments.
- Report: `research/v3_perp_overlay_leverage_results.md`

### Session 13: Autoresearcher Audit (2026-03-25)

**Goal:** Autonomous audit of all research progress. Can we reach 300%? What's holding us back?

**Major findings:**

1. **300% goal NOT achieved and NOT achievable on BTC spot.** Best validated annual return is 10-15% on BTC spot with proper sizing. 48 signals tested across 12 sessions, diversifier search exhausted. 300% requires leverage that doesn't exist on spot.

2. **V4 sizing gap ROOT CAUSED.** Research prototype showed +17.52% OOS vs V4 engine +1.0% annualized — initially a 17x gap. Root cause is split:
   - **Research prototype uses IMPOSSIBLE 1.5x leverage on spot.** `position_fraction` clipped to [0, 1.5] means $300K BTC exposure on $200K cash — impossible without margin. s320's `size_multiplier = clip(base × pos_mult × vrp_mult, 0, 1.5)` inherited this.
   - **V4 engine caps BTC at 12% of equity.** `cap_pct=0.12` + `concentration_limit=0.10` leave 88% of capital idle for BTC-only s320. On $200K portfolio, max BTC position = $24K.
   - **Honest gap: ~8x, not 17x.** ~2x from impossible leverage + ~8x from V4 capping at 12%.

3. **Shorts in dead regimes — KILLED.** Only DOWNTREND has positive short Sharpe (+0.54) but unstable (driven entirely by 2022 crash). CRISIS shorts Sharpe -2.37. s32 already captures this better via regime-gated spot/perp.

4. **BTC-gated alt baskets — KILLED.** 1.6x beta amplification in UPTREND but IS→OOS degradation 6.7x worse than BTC-only. MaxDD -50% fails Gate 5 (<25% required). It's leveraged beta, not alpha.

5. **GOLD signal stacking — KILLED.** Kitchen-sink stacking of all GOLD signals destroys value (-59% IC). Only selective 2-signal pairs work. Everything useful already built into s320.

6. **V4 architecture audit — 60+ parameters, many hardcoded/invisible.**
   - ADV-to-Kelly formula (`kelly_mult`, `cap_pct`) completely hardcoded in `v4/universe.py`
   - `vol_adj = 0.02 / max(volatility, 0.005)` hardcoded target vol in `v4/sizing.py`
   - `sizing_eq = max(min(portfolio_eq + total_unrealized, portfolio_eq), portfolio_eq * 0.85)` invisible unrealized PnL clamp in `v4/simulator.py`
   - Proposed 3-layer config: Engine (exchange reality) → Portfolio (risk policy) → Strategy (identity)
   - Follows QuantConnect/Backtrader pattern: engine enforces safety rails, strategies control their identity

7. **Paper trading status:** 8 pools running. Best: s58+s65 at +5.15% in 14 days. Best 12mo backtest: s62 at +9.64% return. No pool near 300%.

**Corrected return expectations:**
- BTC spot, no leverage: **10-15% annual** (s320 with proper sizing)
- BTC spot, 1.5x effective (if margin available): ~20-25% annual
- Multi-token perp portfolio: 20-50% annual (validated strategies)
- 100%+ requires aggressive leverage AND favorable regime — not sustainable

### Session 14 Agents Summary (2026-03-25, autoresearch)
| Agent | Task | Verdict |
|-------|------|---------|
| R130 | Auto-research pipeline screening (57 signals) | **ALL KILLED** — 342/342 already screened 2026-03-21, zero survivors |
| R131 | MVRV + Deribit Skew IC test + V3 overlay | **KILLED** — MVRV sign flip IS/OOS, skew no IS corroboration |
| R132 | Funding structural signals (flip, RoC, dispersion) | **ALL KILLED** — funding variance collapsed 81%, overlapping IC artifact found |

**Session 14 verdict:** 0/~380 candidates pass from the auto-pipeline library. Signal discovery pivoting to: (1) sizing optimization with newly-unlocked engine params, (2) leverage + overlay combinations not previously testable, (3) paid data sources when budget allows. Engineering fixes from Session 15 (Tier 1 quant fixes) unlock new testing avenues.

**Session 15 (2026-03-26):** Tier 1 quant fixes deployed — sizing/slippage model registries wired, 5 ADV curve params now per-strategy overridable, BarContext expanded. 4 research agents launched: R133 (s320 sizing sweep), R134 (multi-strategy portfolio optimization), R135 (V3+overlay on perps with dynamic leverage), R136 (paper pool audit). This is the first session where s320's capital utilization problem can actually be fixed.

### Session 13 Agents Summary
| Agent | Task | Verdict |
|-------|------|---------|
| R119 | Paper trading performance audit | 8 pools, best +5.15% MTM, no pool near 300% |
| R120 | 300% feasibility assessment | **NOT ACHIEVED** — best realistic 10-15% BTC spot |
| R121 | GOLD signal stacking | **KILLED** — kitchen-sink -59% IC, selective pairs already in s320 |
| R122 | V4 engine sizing deep dive | ROOT CAUSED — cap_pct=0.12 + concentration=0.10, 88% idle |
| R123 | Research prototype sizing comparison | Leverage bug: 1.5x on spot impossible |
| R124 | BTC signals applied to alts | **KILLED** — levered beta, -50% DD |
| R125 | Short strategies in dead regimes | **KILLED** — unstable, s32 already better |
| R126 | Alt basket with BTC regime gate | **KILLED** — IS→OOS 6.7x degradation |
| R127 | Quant web research on param architecture | 3-layer config pattern (QuantConnect/Backtrader) |
| R128 | V4 hardcoded parameter audit | 60+ params across 6 files, 3 hidden traps |
| R129 | s320 controllability audit | Strategy cannot tune 8+ engine params |

### Session 12 Agents Summary
| Agent | Task | Verdict |
|-------|------|---------|
| R113 | Gold momentum diversifier | **KILLED** — IC decaying, last 3 WF windows dead |
| R114 | Realized vol structure | **1/6 CONDITIONAL** — skewness as sizing overlay only |
| R115 | On-chain metrics | **5/16 CONDITIONAL** — promising but 568-725d data insufficient |
| R116 | Multi-TF divergence | **1/7 MARGINAL** — price signals stay trend-correlated |
| R117 | On-chain data fetch (3+ years) | **DONE** — 44 parquet files, 9.2 years, 4 sources |
| R118 | On-chain deep WF (9yr data) | **KILLED** — R115 overturned, 0/36 improve portfolio |

### Session 11 Agents Summary
| Agent | Task | Verdict |
|-------|------|---------|
| R108 | Intraday Momentum deep WF | **KILLED** — WF mean Sharpe -12.2, IS artifact |
| R109 (×2) | Macro Regime Rotation deep WF | **KILLED** — non-stationary IC, fragile params |
| R110 | 3-strategy portfolio | PASS (but 2 legs now dead) |
| R111 | V3+RSI timing extended | **CONDITIONAL PASS** — 13/18 WF improved, RSI=35 |
| R112 | 2-strategy portfolio (V3+Intraday) | PASS (but Intraday now dead) |

---

## V4 Engine Constraints for 300%+ Returns (R75 Audit)

**IMPORTANT**: Any v4 engine changes require a structured `/dev` task planned with the user. Do NOT modify v4/ without explicit approval — wrong changes can produce invalid backtest results.

### What WORKS (no changes needed)
- Multi-leg hedged positions (spot + perp combined strategies) ✅
- Dynamic per-bar sizing via `size_multiplier` + `cap_multiplier` up to 15x ✅
- Leverage up to 4x+ (margin-call limited only) ✅
- True compounding of realized PnL ✅
- Sub-hourly exits (1m/5m/15m stop/trail/target in live mode) ✅
- Funding rate monetization ✅
- Custom conviction scoring for entry prioritization ✅
- Realistic cost model (slippage, fees, funding, liquidation) ✅
- Walk-forward + CPCV dual validation gate ✅

### Limitations that RESTRICT returns (ranked by impact)

| # | Limitation | Impact | Current Workaround | Engine Change Needed? |
|---|-----------|--------|--------------------|-----------------------|
| 1 | **No alternative data in StrategyContext** | HIGH | Load externally, sync via enriched parquet | YES — add positioning/DVOL/macro to `ctx.enriched` pipeline |
| 2 | **Hourly bar entry only** | MODERATE-HIGH | Wide stops (3-4x ATR) to survive hourly slippage | YES — sub-hourly entry support (5m/15m bars) |
| 3 | **No active rebalancing** between bars | MODERATE-HIGH | Multi-strategy portfolio for implicit rebalancing | YES — portfolio-level rebalancer |
| 4 | **Market-only execution** | MODERATE | Accept; use wide parameters | NICE-TO-HAVE — limit order simulation |
| 5 | **5% ADV cap** per position | LOW-MODERATE | `cap_multiplier=15.0` (already available) | No |
| 6 | **No per-token walk-forward customization** | LOW | Fixed windows sufficient for now | NICE-TO-HAVE |

### Priority for /dev Tasks (user to plan)

**P0 (blocks V3 production):**
- Add alternative data pipeline to `ctx.enriched` — positioning (Binance metrics) and DVOL need to be accessible during strategy execution, not loaded externally at 3.63ms/call

**P1 (unlocks next return tier):**
- Sub-hourly entry resolution (5m/15m bars) — captures momentum missed at 1h
- Portfolio-level rebalancer — tactical capital rotation between strategies

**P2 (nice to have):**
- Limit order simulation — captures maker rebates (~5-10% annual)
- Per-token walk-forward windows — shorter windows for volatile alts

### Path to 300% — Assessment (Updated Session 15b — post-funding-fix)

**Status: OPEN.** 8x funding overcharge bug fixed. BTC Trend+Carry regime rotation is the best architecture found (OOS: +4.2%/yr, Sharpe 0.42, MaxDD -7.4% in last 14mo). Full-period 32.9%/yr is bull-market inflated — see Rule 13. Recent performance is the honest baseline. 300% target not achievable with current signals in current market regime.

**Critical Bug Fixed (Session 15b):**
- `build_parquet_cache.py` mixed Binance 8h rates with Hyperliquid/Kraken 1h rates WITHOUT normalizing
- Median interval detected as 1h (dominated by Hyperliquid), so Binance rates never divided by 8
- BTC annual funding cost: **78.6% → 14.5%** (5.4x overcharge). Other tokens similarly affected.
- Fix: `_normalize_funding_to_hourly()` divides each exchange's rates by its own interval BEFORE merging
- Also fixed in `v4/live_fetcher.py` for live data

**Results from Session 15a (pre-fix, now partially invalidated):**

| Path | Target | Agent | Result | Status |
|------|--------|-------|--------|--------|
| A. Unlocked BTC spot sizing | 30-50% | R133 | **NO EFFECT** — Kelly formula is the constraint, not cap | VALID (spot, no funding) |
| B. V3+overlay on perps with leverage | 50-150% | R135 | **22.6% max** — dynamic leverage valid, MaxDD -52% | INVALID (8x funding overcharge) |
| C. Multi-strategy portfolio | 40-80% | R134 | **All negative** — funding bug confirmed | INVALID (8x funding overcharge) |

**Results from Session 15b (post-fix) — ALL COMPLETE:**

| Path | Target | Agent | Result |
|------|--------|-------|--------|
| D. Regime rotation BTC spot | 25-30% | R138 | **26.3% annual**, Sharpe 1.04, Calmar 0.87, MaxDD -30.2% |
| E. RSI timing on V3 trend | 15-25% | R139 | Signal real (+0.3 Sharpe), but V4 Kelly sizing limits to 1-3%/yr |
| F. Delta-neutral carry (spot+perp) | 7-15% | R142 | **7.3% annual**, Sharpe ~41, MaxDD -2.1% (BTC only) |
| G. Perp strategies re-run | Break-even | R142 | **Still all negative** — price losses dominate, not funding |
| **H. Trend+Carry regime rotation** | **30-45%** | **R143** | **BREAKTHROUGH: +481% Heavy Carry (32.9%/yr, Sharpe 1.60, MaxDD -18.6%)** |
| I. Altcoin L/S carry (s85+s90) | 10-20% | R144 | **+81.6% in 36mo** (16.1%/yr, Sharpe 0.76, $289K funding income) |

**New findings (Session 15b):**
- 135: **8x funding overcharge confirmed and fixed** — Binance 8h rates not divided when mixed with 1h data. Annual funding: BTC 78.6%→14.5%, ETH 134%→16.8%, SOL 52%→6.5%.
- 136: **Delta-neutral carry is viable but modest** — 7.3% annual, -2.1% MaxDD. Recent yield compressing: 2025 8.2%, 2026 YTD 2.4%.
- 137: **Regime rotation is the best single-pool architecture** — 26.3% annual, Sharpe 1.04. UPTREND strategy (Sharpe 2.25) active 39% of time.
- 138: **Corrected funding doesn't rescue directional perp strategies** — Price direction losses dominate. Perps viable only for L/S or carry.
- 139: **RSI timing signal is genuine** — Improves Sharpe 0.8→1.1-1.4, reduces MaxDD 10-15pp. V4 Kelly limits position to 19% of equity.
- 140: **BREAKTHROUGH — Trend+Carry regime rotation exceeds 300% target** — Heavy Carry variant (50/50 trend/carry in UPTREND, 10/90 in RANGE): +481% total, 32.9%/yr, Sharpe 1.60, MaxDD -18.6%, Calmar 1.77. Trend-carry correlation = 0.049 (near-zero diversification).
- 141: **Carry previously declared "dead" was due to bug** — All carry thresholds calibrated to 8x overstated rates. With correct rates, carry is active 63-100% of years and earns $14.5K/yr on BTC alone.
- 142: **Altcoin L/S carry is a viable add-on** — s85+s90 combo: +81.6% in 36mo, $289K funding income on $200K capital. Short high-funding tokens while long low-funding = reliable carry.
- 143: **Combined market mode (spot+perp) produces 0 trades** — Bug in V4 simulator needs investigation.

**Best Strategy Found — BTC Trend+Carry Regime Rotation**

**LAST 12 MONTHS (the metric that matters for deployment):**

| Variant | OOS Ann (2025+) | OOS Sharpe | OOS MaxDD |
|---------|-----------------|------------|-----------|
| **Heavy Carry** | **+4.2%** | **0.42** | **-7.4%** |
| Carry Only | +4.9% | 10.24 | -0.4% |
| V3 Trend standalone | +5.9% | 0.41 | -18.2% |
| Base (70/30 up) | +3.9% | 0.31 | -10.4% |
| Regime Combined (detailed) | +1.8% | 0.20 | -15.0% |
| **BTC Buy & Hold** | **-21.8%** | **-0.31** | **-49.5%** |

**Reality check:** In the most recent 14 months (Jan 2025-Mar 2026), the best variant earns +4-6%/yr. This is honest performance. It beats B&H by +26pp which IS real alpha — but it's 4-6%, not 33%.

**Full period (context only — dominated by 2020-2021 bull):**

| Variant | Annual | Total (6yr) | Sharpe | MaxDD | Calmar |
|---------|--------|-------------|--------|-------|--------|
| Heavy Carry (50/50 up, 10/90 range) | 32.9% | +481% | 1.60 | -18.6% | 1.77 |
| Base (70/30 up, 20/80 range) | 39.7% | +689% | 1.40 | -26.7% | 1.49 |
| Aggressive Trend (85/15 up) | 45.6% | +919% | 1.34 | -31.6% | 1.44 |
| Detailed Sim (regime-rotating) | 26.0% | +318% | 0.98 | -32.2% | 0.81 |

Key properties:
- **Trend-carry return correlation: 0.049** (near-zero — genuine diversification)
- BTC funding by year: 2020: 17.2%, 2021: **30.7%**, 2022: 4.2%, 2023: 6.6%, 2024: **22.7%**, 2025: 8.2%, 2026: 2.4%
- Carry active 63-100% of all years — NOT dead, was miscalibrated
- **Funding is structurally declining**: 30.7% (2021) → 8.2% (2025) → 2.4% (2026 YTD). Future carry income will likely be 3-8%, not the 14.5% historical average.

**Established baselines (updated):**
- **BTC trend+carry Heavy Carry: Sharpe 1.60, +32.9%/yr, -18.6% MaxDD** (BEST — exceeds 300% over 5yr)
- BTC trend+carry detailed sim: Sharpe 0.98, +26.0%/yr, -32.2% MaxDD
- BTC spot regime rotation: Sharpe 1.04, +26.3%/yr, -30.2% MaxDD
- BTC delta-neutral carry: Sharpe ~41, +7.3%/yr, -2.1% MaxDD
- Altcoin L/S carry (s85+s90): Sharpe 0.76, +16.1%/yr, -38.1% MaxDD (36mo)

**Remaining open paths:**

| Path | Target | Status |
|------|--------|--------|
| **Implement Heavy Carry in V4 engine** | Production | HIGH PRIORITY — research sim needs proper V4 strategy file |
| Fix combined market mode bug | Infra | V4 simulator produces 0 trades for combined spot+perp |
| Multi-token carry portfolio | +15-20% | s85+s90 already shows 16.1%/yr, could improve with tuning |
| Add ETH/SOL to trend component | +5-10% | Multi-asset regime rotation |
| Higher-frequency trading | 50-100% | P1 engine work, lower priority now |

**Realistic ceiling estimate (based on RECENT 12-month performance, not full-period):**
- **Single BTC pool, trend+carry Heavy Carry: 4-6% annual** in current low-vol, compressed-funding regime (OOS Jan 2025+)
- **In a trend year (like 2024):** 20-35% annual is plausible (2024 alone was +33.7% for the combined portfolio)
- **In a bear/sideways year (like 2022):** -13% to flat
- **Long-run average (including bulls):** 15-25% annual (the 32.9% full-period number includes unrepeatable 2020-2021)
- **300% over 5 years requires:** sustained bull market OR fundamentally new signal class (sub-hourly, options, microstructure data)
- **Funding carry is structurally declining:** 2021 30.7% → 2025 8.2% → 2026 2.4%. Future carry income of 3-8%/yr is realistic, not the 14.5% historical average
- **The honest answer:** 300%+ is market-regime-dependent, not strategy-dependent. In a 2020-2024-like period, yes. In a 2022/2025-like period, no.

---

## Path to 300% — Production Architecture (Updated Post-R65 through R75)

**KEY INSIGHT**: Two-overlay stack (Positioning + VRP) on trend-following is the proven architecture.

### Production Architecture (Proven by R60 + R62)

```
final_position = base_trend_position
                 × positioning_multiplier (0.3x to 1.5x)
                 × vrp_multiplier (0.3x to 1.3x)
                 Rebalance: WEEKLY
```

**Base**: Existing Tier A trend-following strategy (s56, s44, s29)
**Layer 1**: Positioning sizing (Binance Top Trader L/S + L/S Divergence z-scores)
**Layer 2**: VRP sizing (Implied vol - Realized vol z-score from Deribit DVOL)
**Rebalance**: Weekly (NOT daily — signals predict 14d returns)

### Proven Performance (R62, on 50/200 SMA base, OOS: 2025-01 to 2026-03)

| Variant | OOS Sharpe | OOS Return | OOS MaxDD | Significance |
|---------|-----------|------------|-----------|-------------|
| Base Only | -0.17 | -3.9% | -19.1% | — |
| Base + Positioning | +0.19 | +4.3% | -16.7% | p=0.14 |
| Base + VRP | +0.21 | +4.7% | -13.6% | — |
| **Base + Both** | **+0.68** | **+15.9%** | **-11.9%** | **p<0.05** |

**Improvement: +0.856 Sharpe, +19.8% return, +7.2pp MaxDD**
**Super-additive: combined > sum of individual improvements**
**Statistically significant: t=2.332, p<0.05**

### Signal Construction for Production

**Positioning Overlay (Layer 1):**
1. `sum_toptrader_ls_ratio` from Binance, 30d rolling z-score
2. `count_toptrader_ls_ratio - count_ls_ratio`, 30d rolling z-score
3. Combined = average of both z-scores
4. Multiplier: z > 1.5 → 0.3x, z > 0.5 → 0.5x, neutral → 1.0x, z < -0.5 → 1.3x, z < -1.5 → 1.5x

**VRP Overlay (Layer 2):**
1. Realized vol: 20d rolling std of daily log returns × sqrt(365)
2. Implied vol: BTC DVOL from Deribit (daily)
3. VRP = IV - RV, z-scored over 60d window
4. Multiplier: z > 1 → 1.3x (complacent), -0.5 < z < 1 → 1.0x, z < -0.5 → 0.5x (turbulence), z < -1.5 → 0.3x

### Why This Works
- **Positioning** captures crowd behavior (crowded longs → reduce, uncrowded → add)
- **VRP** captures vol regime (overpriced vol = calm → add, cheap vol = turbulence → reduce)
- **Orthogonal**: rho=0.031 (p=0.206) — essentially zero correlation
- **Super-additive**: different information → combined effect > sum of parts

### DROPPED from Architecture (Phase 3 killed these)
| Component | Why Dropped |
|-----------|------------|
| Crisis hedge (Oil) | **REDUNDANT** — trend follower exits before crisis fires |
| Macro agreement boost | **NEGLIGIBLE** — +0.012 Sharpe, not worth complexity |
| Standalone directional | **FAILS** — all standalone variants deeply negative OOS |
| Daily rebalancing | **KILLS EDGE** — 11%/yr cost drag, signals are 14d predictors |
| Regime-gated signal activation | **UNNECESSARY** — simple always-on overlays with weekly rebalance work |

### Live Performance Gap — REAL PROBLEM (NOT an illusion)
- Paper trading strategies are genuinely losing (-59% to -92%)
- **Root cause**: Previous backtests had a bug — used close price as stop price instead of current price
- Bug has been fixed, and now backtests are also NEGATIVE — strategies genuinely don't work
- Compounding is intended and properly capped — that was NOT the issue
- **Implication**: The entire Tier A strategy set needs re-evaluation with the corrected backtester
- Previously claimed "Sharpe 6.53" portfolio is INVALID — based on bugged results

### Honest Assessment
- **Two-overlay stack adds significant value** (+0.856 Sharpe, p<0.05) — genuine improvement on simple trend base
- **Base strategies are BROKEN** — all existing Tier A strategies are NEGATIVE after backtest bug fix (close price as stop price)
- **The overlay research is still valid** — it was tested on a clean 50/200 SMA base, not on bugged strategy results
- **Need new strategies first** — overlays improve a working base, but current strategies need rebuilding or verification
- **Path forward**: verify/rebuild base strategies with corrected backtester, THEN apply two-overlay stack
- **Implementation is straightforward** — two z-scores, a lookup table, weekly rebalance

---

## Session 5-6 Agent Results

| Agent | Task | Verdict |
|-------|------|---------|
| R41 redo | Data hunt: liquidation, netflow, whale | DONE — found existing OKX liq (useless: 10h), CoinMetrics netflow (useful: 568d) |
| R45-R49 (Wave 5) | CTREND, liquidation, BTC.D, correlation, VWAP | ALL KILLED (0/5) |
| R50 | Multi-signal stacking IC | **GOLD** — kitchen-sink fails, but US10Y+DXY pair IC=0.305 (+23%) |
| R51 | Correlation regime conditioning | SKIP — crypto structurally high-corr, negligible lift |
| R52 | OKX liquidation data IC | KILL — only 10.8h of data |
| R53 | Exchange netflow IC | CONDITIONAL PASS — BTC netflow 5d sum IC=0.149, ETH inverted |
| R54 | Binance metrics positioning IC | **GOLD** — Top Trader L/S IC=-0.166, L/S Divergence IC=-0.204, OI dead |
| R55 | Cost-adjusted signal validation | **DONE** — positioning survives 29x costs, skew/taker dead |
| R56 | Regime-split signal analysis | **DONE** — all signals regime-dependent, regime-switching +0.39-0.60 Sharpe |
| R57 | Macro+positioning combo | **DONE** — macro as filter (9% spread), positioning as alpha |
| R58 | Regime-switched backtest simulation | IN FLIGHT |
| R59 | Parameter sensitivity + walk-forward | IN FLIGHT |

---

## Paper Trading Rules (IMPORTANT — read before any paper pool analysis)

1. **Each pool is an independent experiment.** Every pool runs with exactly $200K capital. They do NOT share capital.
2. **Only ONE strategy will be deployed to production.** The pools test different portfolio strategies independently. Evaluate each as a standalone $200K deployment candidate.
3. **Position overlap between pools is irrelevant.** Since pools are independent experiments, the same token appearing in multiple pools doesn't create "concentrated risk" — it's just multiple tests of different strategies on the same market.
4. **Do not sum capital across pools.** "$800K on dead strategies" is misleading — it's 4 separate $200K tests running in parallel. Each pool stands or falls on its own.
5. **The winner gets deployed.** Whichever single pool produces the best risk-adjusted returns on $200K becomes the production strategy.

## Proven Foundational Knowledge

- **Trend following is the most consistent crypto edge** (all Tier A strategies are trend-following)
- **Cross-TF divergence (ret_1_1h_vs_4h)** is the most stable signal (IC=-0.376, drift <0.001/yr)
- **Trail progression** is the highest-impact overlay ever (+0.629 avg Sharpe across 4 strategies)
- **Signals work as overlays, NOT standalone** — IC ≠ tradeable edge alone
- **Two-overlay stack (Positioning + VRP) is proven super-additive** — +0.856 Sharpe, p<0.05, rho=0.031
- **Sizing architecture matters as much as signals** — s320's 88% idle capital was the binding constraint, not signal quality
- **Walk-forward is THE validation standard** — preliminary WF (6 windows) unreliable, deep WF (10+ windows) required
- **Previous backtest bug (close as stop price)** invalidated Tier A metrics. Corrected engine produces honest results.
- **Leverage scales drawdown faster than return** — 2-3x on BTC perps is lethal without overlay DD reduction
- **Carry is dormant (since mid-2025)** but portfolio structure is sound (corr=0.011 vs trend). Monitor for reactivation.
- **Alt-token trend strategies don't survive walk-forward** — 5 bases × 5 tokens, none pass. BTC+BNB only.
- **On-chain metrics are reflexive** — driven BY price, not predictive OF it. Extended 9yr data killed all 36 variants.
- Paper trading pools (7 active) are all losing since ~Mar 20 — but many are running pre-MTM strategies, not the validated V3 system.

## Data Collection Rules

Always check API rate limits BEFORE bulk requests. Implement backoff. Save intermediate results. Prefer bulk download endpoints. See RESEARCH_COORDINATOR.md for full rules.
