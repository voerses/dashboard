# Project Status — crypto_backtest

> **Last updated:** 2026-03-05
> **Process mode:** strategy (gate5.5 — portfolio assembly complete, paused before Gate 6)
> **Next step:** Accumulate more strategies, then batch paper trade all candidates in parallel

---

## Infrastructure & Dev Work

### Done

| Item | Files | Summary |
|------|-------|---------|
| V3 Validation Engine | `v3/engine.py`, `v3/validation.py`, `v3/cpcv.py`, `v3/metrics.py` | Walk-forward + CPCV dual-gate validation across 49 tokens |
| Portfolio-Level Backtest (P1) | `v3/portfolio.py` | Shared cash pool, MTM equity, concentration caps. Enables portfolio-level Sharpe/DD measurement. |
| Strategy Correlation (P2) | `v3/correlation.py` | Pairwise correlation matrix, marginal Sharpe, greedy portfolio selection. **Finding:** all 6 Tier A are momentum variants, median r=+0.62, effective N=2.02. |
| Dynamic Universe (P3) | `v3/dynamic_universe.py` | Point-in-time token re-evaluation at each WF window. Bias is +0.2-2.7% Sharpe inflation (modest). |
| Token Selection Filters (P4) | `v3/universe.py` | Quality/bar-count/ADV filters, `--universe` CLI flag |
| Cross-Sectional Momentum (P5) | `v3/cross_sectional.py` | Rank all tokens by trailing return, long top quintile. Sharpe +1.54, corr +0.25 vs S11. **Tier A diversifier.** |
| Pairs Trading (P6) | `v3/pairs_trading.py` | Cointegration + z-score spread trading on perps. Sharpe +0.42, corr -0.06 vs S11. **Tier B diversifier** (portfolio value: 50/50 blend cuts DD -10pp). |
| Sector Rotation (P7) | `v3/sector_rotation.py` | 10 thematic sectors, category-level momentum. Sharpe +1.31, Calmar +1.73, corr +0.20 vs S11. **Tier A diversifier.** |
| Signal Agreement (P9) | `v3/signal_agreement.py` | AND/N-of-M/ANY gating across strategies. AND gate: -66% trades, +68% avg PnL, DD -6.7pp. **High-conviction overlay.** |
| Regime Analysis (P10) | `v3/regime_analysis.py` | Per-strategy performance by BTC regime. Regime weighting: Sharpe +1.67→+1.96, DD -22.2%→-17.7%. |
| Freqtrade Bridge | `freqtrade_bridge/` | strategy_shell, config_generator, cost_model, exchange_registry, export_params, parity_check |
| Paper Trading System | `paper_trading/` | instance_manager, equity_tracker, monitor, gate4_engine, compare_instances, setup |
| Paper Trade Launcher | `run_paper_trade.py` | CLI with 7 subcommands: setup/start/stop/status/list/monitor/compare. 51 tests passing. |
| Combined Engine Validation | `v3/engine.py`, `v3/validation.py`, `v3/universe.py` | WF+CPCV dual-gate validation for combined spot+perp strategies. `_simulate_combined` extracted as reusable method. Three patterns: simultaneous, conditional, alternating. |
| Combined Portfolio Tools | `v3/portfolio.py`, `v3/correlation.py`, `v3/regime_analysis.py` | All three tools updated to support combined market: detect 2-arg strategy signature, load both spot+perp data, align timeframes, mask both legs, call `_simulate_combined`. |
| Gate 5.5 Portfolio Assembly | `results/correlation_*.json`, `results/regime_analysis_*.json` | 3-strategy and 5-strategy correlation, marginal Sharpe, regime analysis. Recommended 4-strat allocation: s30(40%)+s32(25%)+s29(20%)+s11(15%). |
| Data Infrastructure | `data/1h_cache/` | Spot: Binance 116 tokens. Perp: Binance 165, Kraken 314, Hyperliquid 52. 1H candles 2020-2026. |

### Open / Outstanding

| Priority | Item | Blocker | Notes |
|----------|------|---------|-------|
| **URGENT** | AC8 fix: credentials written to disk in `run_paper_trade.py:141` | None | Strip creds before `json.dump()`, pass via env vars to subprocess |
| MINOR | Resource leak: unclosed log file handle (`run_paper_trade.py:146`) | None | Close fd after Popen |
| MINOR | Private API call: `InstanceManager._load_state()` | None | Expose public method |

### Blocked

| Item | Dependency |
|------|------------|
| Paper trading deployment (Gate 6) | AC8 credential fix + decision to batch paper trade when enough strategies accumulated |
| Survivorship-bias-free dataset (P8) | External data: CoinMarketCap/CoinGecko APIs for delisted tokens |

---

## Strategy Tiers (March 1, 2026 Sweep)

### Tier A (>50% validation rate — production candidates)

| Strategy | Rate | Sharpe | Type |
|----------|------|--------|------|
| s11 momentum_burst | 75.5% | 2.58 | Per-token momentum |
| s09 optimized_trend | 73.5% | 1.98 | Per-token dual momentum |
| s13 vol_weighted_tsmom | 67.3% | — | Per-token TSMOM |
| s21 skew_momentum | 63.3% | — | Per-token skew |
| s17 trend_strength_filter | 55.1% | 1.81 | Per-token trend |
| s18 momentum_accel | 51.0% | 0.63 | Per-token acceleration |

### Tier A Combined (spot+perp strategies — validated through Gate 5)

| Strategy | Rate | Sharpe | Calmar | MaxDD | Type |
|----------|------|--------|--------|-------|------|
| s30 basis_carry | 77.1% | 2.63 | 12.76 | -0.4% | Delta-neutral arb (long spot + short perp) |
| s32 regime_spot_perp | 78.9% | 1.37 | 2.86 | -0.8% | Regime-adaptive instrument selection |
| s31 funding_hedged_momentum | 55.0% | 0.64 | 0.94 | -1.7% | Momentum + funding hedge |

### Tier A Diversifiers (different strategy families — validated but outside gate system)

| Strategy | Sharpe | Corr vs S11 | Type | Module |
|----------|--------|-------------|------|--------|
| Cross-Sectional Momentum | +1.54 | +0.25 | Portfolio ranking | `v3/cross_sectional.py` |
| Sector Rotation | +1.31 | +0.20 | Category momentum | `v3/sector_rotation.py` |

### Tier B Diversifiers

| Strategy | Sharpe | Corr vs S11 | Type | Module |
|----------|--------|-------------|------|--------|
| Pairs Trading (z=3.0) | +0.42 | -0.06 | Stat arb (perp) | `v3/pairs_trading.py` |
| s29 Funding Carry | +0.81 | +0.002 | Carry (perp, market-neutral) | `strategies/s29_funding_carry.py` |

### Overlays (modify existing strategies, not standalone)

| Overlay | Effect | Module |
|---------|--------|--------|
| Regime Weighting | Sharpe +0.29, DD +4.5pp | `v3/regime_analysis.py` |
| Signal Agreement (AND) | Trades -66%, Calmar +0.46, DD +6.7pp | `v3/signal_agreement.py` |

### Tier B (20-50% — iteration candidates, max 3 cycles)

| Strategy | Rate |
|----------|------|
| s20 low_beta_quality | 46.9% |
| s22 supertrend_adx | 46.9% |
| s12 quality_breakout | 32.7% |
| s15 vol_regime_breakout | 30.6% |
| s14 microstructure_edge | 26.5% |
| s10 research_dip_buy | 24.5% |

### Graveyard (killed at gates)

| Date | Strategy | Gate | Reason |
|------|----------|------|--------|
| 2026-03-01 | s16 composite_factor | 5 | 0% validation |
| 2026-03-01 | s19 mean_reversion_filtered | 5 | 0% validation |
| 2026-03-01 | s08 obv_divergence | 5 | 6.1% rate |
| 2026-03-01 | s07 rsi_bounce | 5 | 10.2% rate |
| 2026-03-03 | s25 vol_spike_reversal | 5 | BTC failed Gate 4 3x; altcoins didn't save at Gate 5 |
| 2026-03-03 | s26 rsi_extreme_reversal | 5 | 14.3% rate |
| 2026-03-03 | s27 funding_mean_reversion | 5 | 11.6% rate |
| 2026-03-03 | s28 momentum_burst_perp | 5 | 6.7% rate |

---

## Key Findings

1. **Correlation problem:** All 6 Tier A strategies are momentum/trend variants (median pairwise r=+0.62). Running them together gives effective N=2 bets, not 6. S11 alone (Sharpe 1.74) beats any equal-weight combo (1.22).

2. **Diversification unlocked:** Cross-sectional (+0.25 corr), sector rotation (+0.20), and pairs trading (-0.06) provide genuine diversification that per-token strategies cannot.

3. **Overlays work:** Regime weighting and signal agreement both improve risk-adjusted returns without requiring new signals — just smarter allocation and entry filtering.

4. **New capabilities not in process:** Portfolio strategies, overlays, and diversifiers exist as validated code with results but are **not integrated into the 8-gate pipeline**. The skill, quick reference, and sweep system don't know about them.

5. **Market-neutral carry unlocked:** s29 funding carry is the first genuinely market-neutral strategy (beta=0.0000, corr=+0.002). Harvests structural funding payments from retail long bias. 66/328 tokens validated (20.1%), but 91.2% pass rate among tokens with sufficient funding data. Mean MaxDD only -1.26%.

6. **Combined spot+perp engine works:** Three combined strategies validated end-to-end through Gate 5. s30 basis carry has highest Calmar ever (12.76 mean, max DD -0.4%). s32 regime selection has highest validation rate (78.9%) with negative beta. Combined WF+CPCV validation pipeline fully operational for all three engine patterns (simultaneous, conditional, alternating legs).

7. **Portfolio assembly (Gate 5.5):** 3-strategy combined portfolio (s30+s31+s32): Sharpe 4.41, Calmar 14.29, MaxDD -7.1%. s30 basis carry has highest marginal Sharpe (+0.95) and is regime-stable (PF 2.50-2.68 in all regimes). s31 redundant with s11 (r=+0.71). Recommended 4-strat allocation: s30(40%)+s32(25%)+s29(20%)+s11(15%). Effective N=3.03 with 5 strategies. Regime weighting unnecessary — s30 already works in all regimes.

8. **Process decision: pause at Gate 5.5, batch paper trade later.** Paper trading (Gate 6) deferred until enough strategies accumulated. All validated strategies will be paper traded in parallel to maximize signal-to-wall-clock-time. Knowledge bases fully updated through Gate 5.5 — no information loss risk.

---

## Capability Inventory (Available for Strategy Ideation)

These modules are built, tested, and have results. They expand what's possible beyond per-token time-series strategies:

| Capability | Module | What It Enables |
|-----------|--------|-----------------|
| **Cross-token ranking** | `v3/cross_sectional.py` | Long top-N tokens by trailing return (cross-sectional momentum) |
| **Sector classification** | `v3/sector_rotation.py` | 10 sectors (L1, L2, DeFi, Meme, AI, etc.), category-level momentum |
| **Pairs/stat arb** | `v3/pairs_trading.py` | Cointegration scanning, z-score spread trading, perp long/short |
| **Regime detection** | `v3/regime_analysis.py` | BTC-derived regime (CRISIS/QUIET/UPTREND/RANGE/DOWNTREND), per-strategy performance heatmaps |
| **Regime-conditional sizing** | `v3/regime_analysis.py` | Scale allocation by regime (0% crisis → 100% uptrend) |
| **Signal agreement gating** | `v3/signal_agreement.py` | Combine multiple strategy signals (AND/N-of-M) for high-conviction entries |
| **Portfolio simulation** | `v3/portfolio.py` | Shared cash pool, realistic capital constraints, concentration caps |
| **Correlation analysis** | `v3/correlation.py` | Pairwise strategy correlation, marginal Sharpe, greedy portfolio construction |
| **Dynamic universe** | `v3/dynamic_universe.py` | Point-in-time token eligibility at each WF window |
| **Perpetual futures** | `v3/engine.py` | Long and short, funding rates, leverage, perp-specific fees |
| **Combined spot+perp** | `v3/engine.py` (`_simulate_combined`) | Dual-leg strategies: simultaneous, conditional, alternating. Handles funding, fees, leverage per leg. |
| **Combined validation** | `v3/validation.py` | WF+CPCV for combined strategies. `_run_walk_forward_combined`, `_run_cpcv_combined`. |
| **Combined portfolio tools** | `v3/portfolio.py`, `v3/correlation.py`, `v3/regime_analysis.py` | All support `--market combined`: 2-arg detection, dual data loading, both-leg masking |
| **Multi-exchange** | `freqtrade_bridge/exchange_registry.py` | Binance, Kraken, Hyperliquid with exchange-specific fees/slippage |
| **Paper trading** | `paper_trading/` | Full infra: instance management, equity tracking, monitoring, Gate 4 SPRT |
