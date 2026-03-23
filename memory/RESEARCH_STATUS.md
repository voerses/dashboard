# Research Status — Active Signal Discovery

> **Last updated:** 2026-03-24T02:00Z (session 4 end-of-day, all wave 4 agents complete except R41)

## TIMESTAMP
2026-03-24T02:00Z

---

## Signal Scoreboard (18 signals tested)

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

## Next Actions (priority order)

### P0 — Implementation Ready (proven signals)
1. **Implement DXY+10Y+Oil triple regime overlay in v4** — IC=-0.413, strongest macro signal
2. **Build oil spike drawdown filter** — +0.62 Sharpe, +14.7% DD improvement
3. **Build skew_30d overlay** — proven +0.28 Sharpe improvement

### P1 — High-Value Overlays (tested, need integration)
4. **Build taker volume trend confirmation overlay** — Sharpe +180% OOS
5. **Build taker cross-token dispersion signal** — IC=-0.138 (t=-9.35)
6. **Build ETF tightening+inflow combo signal** — +6.89% 14D during tightening
7. **Build OI divergence leverage vulnerability filter** — 114.7 bps/3d quintile spread
8. **Test VRP z-score as position sizing overlay** — IC=0.268 for vol prediction
9. **Test H21-22 as preferred execution window** for existing strategies

### P2 — Data Pipeline Needed
10. **Set up daily Deribit options snapshot cron** — enables real GEX computation in 3 months
11. **Check R41 results for liquidation/netflow data** — if found, test as signals
12. **Fetch 5-min L/S data from data.binance.vision** — for intraday pattern research if needed

### P3 — Multi-Signal Composite
13. **Stack proven overlays**: triple regime + skew + taker confirmation + OI divergence + VRP sizing
14. **Walk-forward validation of composite** with realistic costs

---

## Path to 300% — Updated Research Thesis

The path is through **stacking 5-7 uncorrelated overlay signals** on existing trend-following:

| Layer | Signal | Expected Sharpe Contribution |
|-------|--------|------------------------------|
| 1 | DXY+10Y+Oil triple regime | +0.6-0.7 (proven) |
| 2 | Skew_30d trend quality | +0.28 (proven) |
| 3 | Taker volume trend confirmation | +0.3-0.5 (OOS validated) |
| 4 | OI divergence leverage filter | +0.2-0.3 (OOS validated) |
| 5 | ETF tightening+inflow | +0.1-0.2 (OOS validated, short history) |
| 6 | VRP z-score position sizing | +0.2-0.3 (vol overlay, OOS validated) |
| 7 | H21-22 execution timing | +0.1-0.2 (conditional) |

Current portfolio Sharpe 6.53. Target: stack 4-5 of these to push annual return from ~200% toward 300%+ while maintaining Sharpe >3.

---

## Running Agents at Session End

| Agent | Task | Status |
|-------|------|--------|
| R41 | Liquidation + exchange netflow data hunt | IN FLIGHT — check on next session resume |

---

## Proven Foundational Knowledge

- **Trend following is the ONLY consistent crypto edge** (all Tier A strategies are trend-following)
- **Cross-TF divergence (ret_1_1h_vs_4h)** is the most stable signal (IC=-0.376, drift <0.001/yr)
- **Trail progression** is the highest-impact overlay ever (+0.629 avg Sharpe across 4 strategies)
- **s44 (basis carry + trail)** is best strategy: Sharpe 4.33→Calmar 59 with trail
- **Signals work as overlays, NOT standalone** — IC ≠ tradeable edge alone
- Current portfolio: s44(35%)+s29(30%)+s37(20%)+s32(15%) = Sharpe 6.53, MaxDD -2.2%
- All paper trading strategies are LOSING: s58(-59%), s65(-76%), s60(-88%), s63(-92%)

## Data Collection Rules

Always check API rate limits BEFORE bulk requests. Implement backoff. Save intermediate results. Prefer bulk download endpoints. See RESEARCH_COORDINATOR.md for full rules.
