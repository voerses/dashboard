# Project Status — crypto_backtest

> **Last updated:** 2026-03-14T13:15Z
> **Process mode:** strategy (paper trading monitoring — Gate 6)
> **Active:** V4 multi-portfolio paper trading: **21 pools**. Runner PID 381557.
> **Engine consolidation (v3→v4):** Completed 2026-03-14. Single simulation path via v4/simulator.py.
> **v3/ is FROZEN LEGACY — do NOT modify.** All imports point to v4/. v3/ exists only as historical reference.
> **Overlays deployed:** s69 (s56+time_trail), s72 (s65+time_trail), s75 (s63+fixed_tp=3.0), s76 (partial TP)
> **Dynamic weights deployed:** s80+s81-dyn (regime-weighted), super5-dyn (5-strategy dynamic)
> **Conviction scoring deployed:** 4-edge-conv (ranked mode), super5-conv (hybrid mode)
> **Breakeven ratchet (BE=0.5 ATR):** Deployed universally to ALL strategies on 2026-03-13. Tested 16/16 portfolios improved (median +82% PnL, 15/16 DD improved). s83-BE/s84-BE removed (redundant).
> **Missions:** Profit-taking (closed), dynamic weights (closed), conviction scoring (closed), data pipeline (closed), engine consolidation v3→v4 (closed). **Regime-adaptive exits (ACTIVE)** — breakeven deployed, Chandelier/volume/acceleration/triple barrier still to test. **Spot-long/perp-short (ACTIVE)** — s65/s62/s72 spot longs to eliminate funding drag (~0.4-0.7% notional). **Live trading infrastructure (PARKED)** — deferred until ready for real capital.
> **Data pipeline (COMPLETE):** kdb+-inspired RDB/HDB pattern. Live→`data/{market}/live/`, historical→`1h_cache/`. `load_token_data()` merges at read time. `promote_live.py` rolls with QC+manifests. `load_token_data_at(as_of)` for reproducible backtests.
> **Next step:** Monitor 21 pools for 50+ trades each. Comprehensive rankings saved to results/v4/portfolio_rankings.json.

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
| Gate 5.5 Portfolio Assembly | `results/correlation_*.json`, `results/regime_analysis_*.json` | **Updated with trail overlays.** Optimal 4-strat: s44(35%)+s29(30%)+s37(20%)+s32(15%). Sharpe 6.53, MaxDD -2.2%. Previous: s30(40%)+s32(25%)+s29(20%)+s11(15%), Sharpe ~4.4. |
| Data Infrastructure | `data/1h_cache/` | Spot: Binance 116 tokens. Perp: Binance 165, Kraken 314, Hyperliquid 52. 1H candles 2020-2026. |
| Data Pipeline Separation (v5.0) | `v4/data_loader.py`, `v4/live_fetcher.py`, `v4/manifest.py`, `v4/signals.py`, `v4/portfolio_signals.py`, `tools/promote_live.py`, `tools/build_parquet_cache.py` | kdb+-inspired RDB/HDB pattern. Live fetcher writes to `data/{market}/live/`, historical stays in `1h_cache/`. `load_token_data()` merges at read time with memory-efficient overlap handling (split into update/gap-fill/new). `promote_live.py` rolls live→historical with QC + SHA-256 manifests + atomic writes. `load_token_data_at(as_of, use_manifest)` enables reproducible backtests. 4 adversarial review rounds, 35 fixes (4 CRITICAL, 8 HIGH, 12 MEDIUM, 11 LOW). 627 tests pass. Tagged v5.0. |
| Dashboard (GitHub Pages) | `tools/generate_dashboard.py`, `simulations.json` | Self-contained HTML dashboard for monitoring simulation runs. Signal enrichment (indicators at entry), open positions panel, equity curve, daily P&L, expandable trade details. Deployed to `voerses.github.io/dashboard/`. |
| s33 Engine Support | `v3/engine.py` | Array support for `stop_mult`/`trail_mult` in StrategyResult + JIT. Enables per-bar dynamic stops (needed for leverage-scaled stops). |
| s33 Leveraged Conviction Perp | `strategies/s33_leveraged_conviction_perp.py` | Conviction-scored leverage (1-10x), Moreira-Muir inverse vol scaling, bidirectional. **Currently losing -15.1% in backtest — needs investigation before paper trading.** |
| `size_multiplier` Engine Support | `v3/engine.py`, `v3/validation.py` | Generic `size_multiplier` field in StrategyResult (float or np.ndarray, default 1.0). Applied in `_simulate` and `_simulate_combined`. Forwarded in WF masked result construction. Enables strategy-configured sizing overlays without engine-specific logic. |
| WF Validation Bug Fix | `v3/validation.py` | Fixed `_run_walk_forward` and `_run_walk_forward_combined` silently dropping `size_multiplier` when constructing masked StrategyResult. Both now forward the field. |
| `cap_multiplier` Engine Support | `v3/engine.py`, `v3/validation.py` | Scales ADV-based `cap_pct_arr` (default 2-12% of equity). Applied in `_simulate` and `_simulate_combined`. Forwarded in both WF functions. Unlocks aggressive sizing — `size_multiplier` alone was ineffective because `cap_pct` was the binding constraint. |
| Intra-Bar Liquidation Fix | `v3/engine.py` | Liquidation check now uses `low[i]` for longs, `high[i]` for shorts (worst-case intra-bar) instead of `close[i]`. Applied in all 3 locations: `_simulate_core_jit`, `_simulate_combined_jit` leg1, leg2. Minimal impact on delta-neutral carry; exposed -7pp MaxDD degradation on leveraged momentum (s55). |
| s34 Momentum Regime-Sized | `strategies/s34_momentum_regime_sized.py` | Wraps s11 with O2 (regime sizing) + O3 (weekend reduction). PASSED Gate 5O: Sharpe +0.27, PF +0.08, MaxDD +0.26pp. First overlay wrapper strategy. |
| AIPIP-0018 Process Update | `.claude/skills/strategy/SKILL.md`, knowledge files | Strategy Overlay Immutability: never modify base strategies, use wrapper files, generic engine changes only, delta-neutral exemption. |
| 15-min Data Fetcher (I1) | `tools/fetch_binance_15m.py` | Bulk download 15m candles from data.binance.vision. Parallel, resumable, 116 tokens. Output: `data/spot/15m_cache/`. |
| ETF Flow Pipeline (I2) | `tools/fetch_etf_flows.py` | Daily ETF inflow/outflow data from SoSoValue API + Farside Investors. BTC+ETH, Jan 2024+. Output: `data/etf_flows/`. |
| JIT Trail Progression (I3) | `v3/engine.py`, `v3/validation.py` | `trail_schedule` field in StrategyResult. Progressive trailing stop tightening by profit in ATR units. Applied in both JIT functions, forwarded in both WF functions. |
| Signal Discovery Engine | `tools/signal_discovery/` | Automated IC testing: 300+ features × 55 tokens × 5 horizons. Walk-forward IC, FDR correction, lead/lag causality, rolling IC health, IC decay curves. 252 significant signals found. Top: `ret_1_1h_vs_4h` (IC=-0.376, STABLE), regime-conditional EMAs (IC=-0.67). Outputs in `outputs/signal_discovery/`. |
| Temporal Analysis Layer | `tools/signal_discovery/analysis.py` | Rolling IC stability (STABLE/DECAYING/DEAD), lead/lag causality (57 LEADING, 16 HIGH confidence), IC decay curves (92% sign-consistent across horizons). |
| Signal-Enhanced Strategies | `strategies/s56-s58` | s56 signal_enhanced_momentum (V4 component), s57 signal_timed_turbo_carry (V4 component), s58 multi_strategy_portfolio (V4 production). s57/s58 use signal discovery outputs for timing. |
| V4 Portfolio Backtest Engine | `v4/` | Portfolio-level simulator: shared capital pool, concentration limits, ADV caps, partial fills, square-root slippage. Replaces V3 per-token validation with end-to-end simulation. See `knowledge/V4_ENGINE.md`. |
| V3→V4 Engine Consolidation | `v4/engine.py`, `v4/universe.py`, `v4/metrics.py`, `v4/cpcv.py`, `v4/validation.py` | **COMPLETE.** Single simulation path: all validation, backtesting, and paper trading now uses v4/simulator.py. Zero `from v3.` imports remain in v4/ or tools/. v3/ is frozen legacy (not imported by any production code). Eliminates 6 v3-only exit features divergence. Strategy files unchanged — `from engine import ...` resolves to v4/engine.py via sys.path. |
| V4 Paper Trading | `v4/paper_engine.py`, `v4/run_paper.py` | Same simulator code with live data via ccxt. Per-tick execution, state persistence, deterministic RNG, dashboard push to gh-pages. |
| Quant Review Fixes (Mar 10) | `v4/simulator.py`, `v4/paper_engine.py`, `strategies/s56,s57` | M2: symmetric RSI exit for shorts. M4: removed dead `_apply_walk_forward_mask_live()`. H3: fixed deprecated `reindex(method='ffill')`. C2/C3/H1/H2: verified as false positives. |
| V4 Strategy Sweep | `knowledge/STRATEGY_CATALOG.md` | All 37 strategies run through V4 12-month + Jan-Mar OOS backtests. Key finding: spot-only fails in sideways; perp/combined survive. |
| Signal Portfolio Module | `tools/signal_portfolio/` | Token clustering by signal profiles, IC-weighted composite signals, strategy generation, walk-forward optimization. Designed but not yet run end-to-end. |
| Paper Trading Engine Rewrite | `run_paper_live.py`, `v3/paper_engine.py` | Full parity with backtest engine: trade management (stop/trail/target/max_hold per tick), slippage model (3bps + sqrt(participation)), ADV-based Kelly sizing, funding sign fix, edge threshold, regime min hold, capital split, liquidation for all shorts. 10 tests passing. |
| Live Paper Trading (V3) | `state/paper_live/` | **Superseded by V4.** Previously: 4 strategies (s30, s32, s54, s58), 95 tokens, $800K. |
| Live Paper Trading (V4) | `state/v4_paper/` | V4 paper trading: s58 portfolio (s56+s57), $200K capital, Binance. Fresh start Mar 10. Dashboard auto-pushed to gh-pages. |
| Time-Trail Engine Support | `v4/simulator.py`, `v4/signals.py`, `v4/position.py`, `v4/paper_state.py`, `v3/engine.py` | `time_trail_schedule` field on Position, TokenSignals, StrategyResult. Applied as `min(profit_trail, time_trail)` in simulator trailing stop logic. Serialized/deserialized for paper trading state persistence. |
| Time-Trail Overlay Strategies | `strategies/s69_s56_time_trail.py`, `strategies/s70_s60_time_trail.py`, `strategies/s72_s65_time_trail.py` | Wrapper strategies adding aggressive time-based trail tightening to s56, s60, s65. Gate 5O validated: Calmar +13-148%, DD improved. Deployed to paper trading. |
| Fixed TP Overlay (s75) | `strategies/s75_s63_fixed_tp.py` | s63 counter-trend + target_mult=3.0. Locks in MR profits before trend resumes. Gate 5O: Calmar +17.6%, Return +18.5%, avg winner +22.7%. Deployed to paper trading. |
| Funding Exit Engine Support | `v4/simulator.py`, `v4/signals.py`, `v4/position.py`, `v4/paper_state.py`, `v3/engine.py` | `funding_exit_threshold` field (default 0.0 = disabled). Generic exit check if cumulative funding / margin exceeds threshold. s73/s74 KILLED but engine capability preserved. |
| Partial Profit-Taking | `v4/simulator.py`, `v4/signals.py`, `v3/engine.py` | `partial_tp_trail` field on StrategyResult/TokenSignals. Closes fraction of position at profit target, trails remainder. s76 overlay deployed. |
| Cross-Sectional Momentum V4 | `strategies/s80_xsec_momentum.py` | V4-native cross-sectional momentum: rank tokens by trailing return, long top quintile on perps. Regime-gated. |
| Sector Rotation V4 | `strategies/s81_sector_rotation.py` | V4-native sector rotation: 10 sectors, category-level momentum, top 2 sectors. Regime-gated. |
| Dynamic Weight Allocation | `v4/dynamic_weights.py` | Regime-aware strategy weighting. Computes per-strategy weights based on BTC regime + historical profit factors. Applied per-tick in paper trading. |
| Conviction-Based Entry Scoring | `v4/simulator.py`, `v4/signals.py`, `v4/config.py` | 3 modes: shuffle (random), ranked (conviction descending), hybrid (3 tiers). Auto-derives conviction from size_multiplier. Eliminates seed sensitivity (0% CV in ranked mode). |
| Dashboard V2 Redesign | `tools/generate_dashboard_v2.py` | Tabs now show % P/L, equity, days running per portfolio. Supports 21 tabs with wrapping layout. |
| Multi-Portfolio Expansion | `configs/multi_v4_paper.json` | 21 paper trading pools: 19 strategy combos + 2 conviction variants (4-edge-conv, super5-conv). |
| Live Price Refresh (SIGUSR1) | `v4/run_paper_multi.py`, `v4/paper_engine.py` | `--refresh` sends SIGUSR1 to running process. Fetches live ticker prices for open positions, updates MTM + heartbeat, pushes dashboard. No tick/trading — just price view. ~22s for 21 pools. Usage: `python -m v4.run_paper_multi --config configs/multi_v4_paper.json --refresh` |
| Breakeven Ratchet (universal) | `v3/engine.py`, `v4/signals.py`, `v4/position.py`, `v4/portfolio_signals.py`, `v4/paper_state.py` | `breakeven_atr` default changed from 0.0 to 0.5 across entire pipeline. After trade reaches +0.5 ATR, stop moves to entry price. Converts ~13.7% of losing trades to scratch. 16/16 portfolios improved (median PnL +82%, 15/16 DD improved). |
| Portfolio Rankings Script | `v4/rank_all_portfolios.py` | Runs 4 separate backtests per strategy (months=60/12/3/1), each starting fresh at $200K. Ranked by % return. Saves to `results/v4/portfolio_rankings.json`. 28 strategies × 4 periods = 112 backtests. |

### Infrastructure Roadmap (Next Wave)

| ID | Task | Unblocks | Dependency | Status |
|----|------|----------|------------|--------|
| I1 | 15-min Historical Data Fetcher | O4 defensive stop | None | **DONE** |
| I2 | ETF Flow Data Pipeline | N2 ETF flow overlay | None | **DONE** |
| I3 | JIT Trailing Stop Progression | O5 trailing stop overlay | None | **DONE** |
| I4 | O4 15-min Defensive Stop | Strategy value | I1 | **KILLED** at Gate 5O — zero marginal value on top of O5 |

**I1 details:** `tools/fetch_binance_15m.py` — bulk downloads from `data.binance.vision`. CLI: `--symbols`, `--start-year`, `--end-year`, `--workers 4`, `--force`. Output: `data/spot/15m_cache/{SYMBOL}_15m.parquet`. Smoke tested with BTC 2026 (5,664 rows, correct 15m intervals). Supports resume and parallel downloads.

**I2 details:** `tools/fetch_etf_flows.py` — SoSoValue API (working, 300 days/call) + Farside Investors HTML scrape (Cloudflare-blocked in this env, logic ready). Output: `data/etf_flows/{asset}_etf_flows.parquet` + `.csv`. Smoke tested: BTC 300 days (Dec 2024 – Mar 2026), ETH 300 days.

**I3 details:** `v3/engine.py` — `trail_schedule` field added to StrategyResult (Optional[np.ndarray], shape (N,2): [[profit_atr_threshold, trail_mult], ...]). Applied in `_simulate_core_jit` and `_simulate_combined_jit`. Forwarded in both WF functions. Backward compatible (None = old behavior). Verified: s11 BTC validation runs cleanly, progressive tightening confirmed working.

### Open / Outstanding

| Priority | Item | Blocker | Notes |
|----------|------|---------|-------|
| **HIGH** | Monitor 21 paper trading pools for 50+ trades | Time | 21 pools running (PID 341250). Oldest (s58) at tick 107, newest (super5-conv) at tick 32. Need 1-3 weeks for 50+ trades on newer pools. |
| **HIGH** | Evaluate conviction scoring paper results | Time + trades | 4-edge-conv (ranked) and super5-conv (hybrid) deployed. Compare vs shuffle counterparts (4-edge, super5-dyn). |
| **DONE** | Breakeven ratchet universal deployment | — | Deployed 2026-03-13. Default breakeven_atr=0.5 across all strategies. 16/16 portfolios improved. s83-BE/s84-BE removed (redundant). |
| **DONE** | Comprehensive portfolio rankings | — | 28 strategies ranked across 4 periods (all-time, 12mo, 3mo, 1mo). Each starts fresh at $200K. Saved to results/v4/portfolio_rankings.json. |
| **MED** | Dashboard GH Pages CDN stale cache | Mirror sync delay | Gitea→GitHub mirror not propagating gh-pages changes fast enough |
| **MED** | Hyperliquid data gap | No spot data | Only 23 perp tokens (Aug 2025-Mar 5), no spot. s57 can't run on HL. Not worth building fetcher yet. |
| **LOW** | Run signal portfolio pipeline end-to-end | None | Cluster 55 tokens by signal profile, generate IC-weighted composite strategies, optimize, backtest |

### Blocked

| Item | Dependency |
|------|------------|
| Paper trading deployment (Gate 6) | AC8 credential fix + batch paper trade when enough strategies accumulated |
| Survivorship-bias-free dataset (P8) | External data: CoinMarketCap/CoinGecko APIs for delisted tokens |

---

## Strategy Tiers (March 12, 2026)

### V4 Paper Trading — 21 Pools Active (PID 341250)

Updated 2026-03-13. Breakeven ratchet (BE=0.5) deployed universally.

| Pool | Strategies | Tick | Equity | MTM | Status |
|------|-----------|------|--------|-----|--------|
| s58 | s56+s57 | 107 | $201,203 | $200,109 | +0.6% realized |
| s60 | s60 | 102 | $201,021 | $201,297 | +0.5% realized |
| s58+s60 | s56+s57+s60 | 103 | $209,653 | $209,653 | **+4.8% realized** |
| s58+s62 | s56+s57+s62 | 87 | $209,746 | $220,184 | **+10.1% MTM** |
| s58+s63 | s56+s57+s63 | 80 | $204,027 | $201,741 | +2.0% realized |
| s58+s65 | s56+s57+s65 | 79 | $214,973 | $224,549 | **+12.3% MTM** |
| s58+s59 | s56+s57+s59 | 76 | $197,922 | $200,087 | -1.0% realized |
| 4-edge | s56+s57+s63+s65 | 76 | $215,280 | $214,120 | **+7.6% realized** |
| s69 | s56_time_trail | 48 | $202,443 | $202,124 | +1.2% realized |
| s58+s69 | s56+s57+s69 | 48 | $202,647 | $202,104 | +1.3% realized |
| s72 | s65_time_trail | 47 | $201,539 | $226,305 | **+13.2% MTM** |
| s58+s72 | s56+s57+s72 | 47 | $201,540 | $226,313 | **+13.2% MTM** |
| s58+s75 | s56+s57+s75 | 45 | $204,436 | $203,997 | +2.2% realized |
| s76 | partial_tp | 43 | $202,123 | $201,806 | +1.1% realized |
| s58+s76 | s56+s57+s76 | 43 | $202,012 | $200,714 | +1.0% realized |
| 4-edge+ptp | s56+s57+s63+s65+ptp | 41 | $203,727 | $219,240 | **+9.6% MTM** |
| s80+s81 | xsec_mom+sector_rot | 36 | $207,315 | $207,316 | +3.7% realized |
| s80+s81-dyn | s80+s81 dynamic weights | 34 | $208,609 | $208,931 | +4.3% realized, DOWNTREND |
| super5-dyn | s57+s60+s63+s80+s81 dyn | 34 | $208,200 | $211,820 | +4.1% realized |
| 4-edge-conv | 4-edge + conviction ranked | 32 | $201,283 | $213,800 | +6.9% MTM |
| super5-conv | super5 + conviction hybrid | 32 | $203,944 | $203,121 | +2.0% realized |

### V4 Production Components

| Strategy | V4 12mo Return | V4 Sharpe | OOS (Jan-Mar) | Role |
|----------|---------------|-----------|---------------|------|
| **s56** signal_enhanced_momentum | Component of s58 | — | +$72K | Momentum (spot+perp) |
| **s57** signal_timed_turbo_carry | Component of s58 | — | +$60K | Carry (combined) |
| **s60** momentum_burst_perp_v4 | +3157% | 5.2 | +157% | Perp momentum (bidirectional) |
| **s62** conservative_funding_carry | +269% | 3.1 | +50% | Funding carry |
| **s63** vol_spike_reversal_v4 | +441% solo | 3.94 | TBD | Counter-trend |
| **s65** funding_carry_v4 | +1217% (w/s58) | 8.52 | TBD | Funding carry complement |
| **s80** xsec_momentum | New | — | — | Cross-sectional ranking |
| **s81** sector_rotation | New | — | — | Sector momentum |

### V4 Overlays & Variants

| Strategy | Base | Overlay | Paper Pool |
|----------|------|---------|------------|
| s69 | s56 | time_trail | s69, s58+s69 |
| s72 | s65 | time_trail | s72, s58+s72 |
| s75 | s63 | fixed_tp=3.0 | s58+s75 |
| s76 | s56 | partial_tp | s76, s58+s76 |
| s80+s81-dyn | s80+s81 | dynamic regime weights | s80+s81-dyn |
| super5-dyn | s57+s60+s63+s80+s81 | dynamic regime weights | super5-dyn |
| 4-edge-conv | s56+s57+s63+s65 | conviction ranked | 4-edge-conv |
| super5-conv | s57+s60+s63+s80+s81 | conviction hybrid | super5-conv |

### V3 Tier A (>50% per-token validation rate)

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

### Overlay Wrappers (new strategy files wrapping base strategies)

| Strategy | Base | Overlay | Tier | Gate 5O Result |
|----------|------|---------|------|----------------|
| s34 momentum_regime_sized | s11 | O2 regime sizing + O3 weekend reduction | A (overlay) | Sharpe +0.27, PF +0.08, MaxDD +0.26pp vs s11 |
| s37 momentum_trail_progression | s11 | O5 progressive trailing stop | A (overlay) | Sharpe +0.477, Calmar +0.709, MaxDD +0.93pp, 65/69 token wins vs s11 |
| s39 trend_trail_progression | s09 | O5 progressive trailing stop | A (overlay) | Sharpe +0.819, rate 36.3%→59.3% (+23.1pp), 64/69 token wins vs s09 |
| s40 tsmom_trail_progression | s13 | O5 progressive trailing stop | A (overlay) | Sharpe +0.752, rate 24.2%→51.6% (+27.5pp), 65/69 token wins vs s13 |
| s41 skew_trail_progression | s21 | O5 progressive trailing stop | A (overlay) | Sharpe +0.468, rate 24.2%→47.3% (+23.1pp), 63/69 token wins vs s21 |
| s44 basis_carry_trail_progression | s30 | O5 progressive trailing stop | A (overlay) | Sharpe +1.448, Calmar +44.35, MaxDD halved (-0.44%→-0.23%), AnnRet +65%, 63/0 wins/losses |
| s54 turbo_carry | s44 | 2x regime sizing + cap_multiplier=15 | A (overlay) | **Fixed capital: +160%/yr, +66.5%/yr last 12mo.** 22 elite tokens (3yr+, ADV>$50M). MaxDD -7.6%. Paper trading started. |

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
| 2026-03-08 | s35 regime_spot_perp_volsized (S1) | 5O | Calmar -29%, return halved vs s32. Vol-managed sizing too aggressive on regime-gated strategy. |
| 2026-03-08 | O3 weekend sizing on s30 | 5O | s30 is delta-neutral (long spot + short perp). Directional risk overlays have no effect. Calmar -9.5%. |
| 2026-03-08 | s36 basis carry funding-scaled (O6) | 5O | Neutral — Calmar -0.29, Sharpe -0.01. Funding rate colinear with basis premium; entry signal already captures carry richness. |
| 2026-03-08 | s38 momentum ETF flow (N2) | 5O | ETF data covers only 15mo (Dec 2024–Mar 2026); overlay=1.0 for 85% of backtest. Cannot validate. |
| 2026-03-08 | s50 momentum_extreme_leverage | 5 | 65% rate but only +2.7% mean return. 5x leverage amplified fees more than edge. |
| 2026-03-08 | s52 funding_extremes_leveraged | 5 | 40% rate, +2.4% mean return. Funding extremes too rare for consistent trades. |
| 2026-03-08 | s53 alt_momentum_breakout | 5 | 37% rate, +1.0% mean return. Breakout filters too strict, too few trades. |
| 2026-03-08 | s56 max_leverage_momentum | 5 | 14% rate, negative mean return. 5x leverage on tight filters amplifies losses. |
| 2026-03-08 | s42 momentum defensive trail (O4) | 5O | Zero marginal improvement on O5. Per-bar volatility ceiling too rare/small. O5 already captures value. |
| 2026-03-08 | s43 regime spot/perp trail (O5) | 5O | Rate -2.2pp (71.1→68.9%). Metrics improve (Sharpe +0.08) but lost 2 tokens from validation. Trail tightens short leg prematurely. |
| 2026-03-11 | s66 adx_breakout | 0 | Too few trades: only 38 entries on BTC. |
| 2026-03-11 | s68 band_walk | 2 | Momentum family saturated, 80% overlap with s56 within 24h. |
| 2026-03-11 | s67 funding_momentum_v4 | V4-5 | Sharpe 1.90 too low for portfolio. Decorrelated (all <0.2) but capital dilution hurts. Need Sharpe >3. |
| 2026-03-12 | s73 s56_funding_exit (Sub 1) | 5O | Calmar degrades at every threshold (7 tested). Funding is 1.2% of PnL — paper F31 was small-sample artifact. Can't discriminate winners from losers by funding. |
| 2026-03-12 | s74 s60_funding_exit (Sub 1) | 5O | Same. Calmar -34% at best Sharpe threshold. Funding exit cuts big winners alongside losers. |

---

## Key Findings

0. **V4 portfolio simulation is the new standard.** V4 replaces V3's per-token validation with portfolio-level simulation: shared capital, concentration limits, ADV caps, slippage model. Several strategies killed at V3's per-token gate (s27, s28, s25) are profitable in V4's portfolio context because diversification across many tokens compensates for individual weakness.

0a. **s58 production portfolio: +1717% (12mo), Sharpe 7.29, MaxDD -1.9%.** s56 (momentum) + s57 (carry) in V4 shared capital. OOS Jan-Mar 2026: +66% ($131K). March weakness ($833/day vs $2,394/day Feb) due to carry going flat in sideways market. Momentum carried the load in March.

0b. **Spot-only strategies fail in sideways markets (Jan-Mar 2026).** Every spot-only strategy in the sweep lost money during Jan-Mar. Only perp (bidirectional) and combined (carry) strategies survived. Implication: any all-weather portfolio MUST include perp/combined components.

0c. **Carry profits from basis convergence, not funding.** s57 generated $1.97M PnL but only $331 from funding payments. The edge is premium convergence (perp price → spot price), not yield harvesting.

1. **Correlation problem:** All 6 Tier A strategies are momentum/trend variants (median pairwise r=+0.62). Running them together gives effective N=2 bets, not 6. S11 alone (Sharpe 1.74) beats any equal-weight combo (1.22).

2. **Diversification unlocked:** Cross-sectional (+0.25 corr), sector rotation (+0.20), and pairs trading (-0.06) provide genuine diversification that per-token strategies cannot.

3. **Overlays work:** Regime weighting and signal agreement both improve risk-adjusted returns without requiring new signals — just smarter allocation and entry filtering.

4. **New capabilities not in process:** Portfolio strategies, overlays, and diversifiers exist as validated code with results but are **not integrated into the 8-gate pipeline**. The skill, quick reference, and sweep system don't know about them.

5. **Market-neutral carry unlocked:** s29 funding carry is the first genuinely market-neutral strategy (beta=0.0000, corr=+0.002). Harvests structural funding payments from retail long bias. 66/328 tokens validated (20.1%), but 91.2% pass rate among tokens with sufficient funding data. Mean MaxDD only -1.26%.

6. **Combined spot+perp engine works:** Three combined strategies validated end-to-end through Gate 5. s30 basis carry has highest Calmar ever (12.76 mean, max DD -0.4%). s32 regime selection has highest validation rate (78.9%) with negative beta. Combined WF+CPCV validation pipeline fully operational for all three engine patterns (simultaneous, conditional, alternating legs).

8. **Breakeven ratchet (BE=0.5 ATR) universally improves all strategies.** After trade reaches +0.5 ATR profit, stop moves to entry price. Converts ~13.7% of losing trades to scratch. Tested across 16/16 portfolios: median PnL +82%, 15/16 DD improved. Deployed as default on 2026-03-13. All strategies and paper trading pools now have breakeven enabled.

9. **Comprehensive rankings (March 13, 2026, $200K fresh start per period):**

   **ALL TIME (~5 years)** — Top 5:
   | # | Name | Return | MaxDD |
   |---|------|--------|-------|
   | 1 | 4-edge-conv | +1,901,656% | -5.7% |
   | 2 | 4-edge+ptp | +1,886,215% | -4.7% |
   | 3 | s58+s65 | +1,836,961% | -6.3% |
   | 4 | s58+s72 | +1,782,492% | -5.0% |
   | 5 | 4-edge | +1,738,115% | -5.6% |

   **LAST 12 MONTHS** — Top 5:
   | # | Name | Return | MaxDD |
   |---|------|--------|-------|
   | 1 | s60 | +16,013% | -7.2% |
   | 2 | s58+s60 | +12,956% | -4.2% |
   | 3 | super5-dyn | +11,536% | -34.5% |
   | 4 | super5-conv | +10,475% | -38.1% |
   | 5 | 4-edge+ptp | +7,281% | -10.3% |

   **LAST 3 MONTHS** — Top 5:
   | # | Name | Return | MaxDD |
   |---|------|--------|-------|
   | 1 | s60 | +659% | -2.8% |
   | 2 | super5-conv | +556% | -0.4% |
   | 3 | super5-dyn | +450% | -4.4% |
   | 4 | s58+s60 | +297% | -1.1% |
   | 5 | s58+s65 | +273% | -0.5% |

   **MARCH 2026** — Top 5:
   | # | Name | Return | MaxDD |
   |---|------|--------|-------|
   | 1 | s60 | +109% | -0.7% |
   | 2 | super5-dyn | +103% | -0.2% |
   | 3 | super5-conv | +100% | -2.1% |
   | 4 | s80+s81-dyn | +90% | -1.1% |
   | 5 | s80+s81 | +90% | -1.1% |

   Key insights: s60 dominates recent periods (1/3/12mo). 4-edge family leads all-time. super5 portfolios strong recently. All 28 strategies profitable across all 4 periods.
   Full rankings: `results/v4/portfolio_rankings.json`.

7. **Portfolio assembly (Gate 5.5) — UPDATED with trail progression overlays:** Previous allocation: s30(40%)+s32(25%)+s29(20%)+s11(15%), Sharpe ~4.4. **New optimal 4-strat portfolio:** s44(35%)+s29(30%)+s37(20%)+s32(15%). Combined Sharpe 6.53, MaxDD -2.2%. Trail overlays s44 and s37 replace base s30 and s11 respectively. Correlation structure: median pairwise +0.19, no pairs >0.7. s44 (delta-neutral carry + trail) is the anchor (Sharpe 5.80, final equity $8.5M on $200K). s29 has highest marginal Sharpe (+1.21) due to near-zero correlation with everything. Greedy forward selection adds all 4 strategies with positive cumulative improvement.

8. **Process decision: pause at Gate 5.5, batch paper trade later.** Paper trading (Gate 6) deferred until enough strategies accumulated. All validated strategies will be paper traded in parallel to maximize signal-to-wall-clock-time. Knowledge bases fully updated through Gate 5.5 — no information loss risk.

9. **Overlay wrapper pattern works (AIPIP-0018).** s34 wraps s11 with regime sizing + weekend reduction via `size_multiplier` field. Base strategy untouched. Enables clean A/B comparison at Gate 5O. s35 (S1 vol-managed on s32) killed — vol sizing too aggressive on a strategy that already handles regime risk via gated entries.

10. **Delta-neutral strategies immune to directional overlays.** s30 basis carry (long spot + short perp) showed -9.5% Calmar degradation with weekend sizing. Both legs hedge each other, so directional risk adjustments hurt rather than help. Formalized as Gate 0 kill criterion (AIPIP-0018 Rule 4).

11. **WF validation silently drops new StrategyResult fields.** Both `_run_walk_forward` and `_run_walk_forward_combined` construct masked results without forwarding new fields like `size_multiplier`. Fixed. Checklist added to Gate 3O to prevent recurrence.

12. **Overlay sweep complete (2026-03-08).** 10 ideas screened (O1-O6, S1-S2, N1-N2). Results: O1 deployed as engine parity fix, O2+O3 passed as s34, O6 killed at Gate 5O (funding colinear with basis), S1 killed at Gate 5O (vol-sizing too aggressive), O4/O5 killed as overlays (need infra: 15-min data and JIT changes respectively), S2 killed as overlay (needs permissive entries, not wrapper-compatible), N1 killed (engine already does ADV sizing), N2 blocked (needs ETF data). **Only 1 survivor out of 10 ideas = 10% hit rate.** Three infra tasks identified to unblock the next wave.

13. **Funding rate colinear with basis premium.** O6 (funding-scaled carry) tested on s30 basis carry showed Calmar -0.29 and Sharpe -0.01 — essentially neutral. The basis entry signal already captures funding richness because funding rate and basis premium are driven by the same market force (retail long demand). Overlaying one on the other adds noise, not signal.

14. **Progressive trailing stops are the strongest overlay found — universal across spot AND delta-neutral strategies.** Trail schedule `[[0,3.0],[1,2.5],[2,2.0],[3,1.5]]` improves ALL 4 top spot strategies: s09→s39 (+23.1pp, Sharpe +0.819), s11→s37 (+12.1pp, Sharpe +0.477), s13→s40 (+27.5pp, Sharpe +0.752), s21→s41 (+23.1pp, Sharpe +0.468). Plus s30→s44 basis carry: Sharpe +1.448, Calmar 15→59, MaxDD halved, 63/0 wins/losses — strongest single overlay result. Does NOT work on s32 regime spot/perp (-2.2pp rate, short legs need wide stops). Average spot improvement: +21.5pp, +0.629 Sharpe, 93% win rate.

16. **Per-bar volatility-based trail ceiling adds nothing on top of O5.** O4 defensive stop (tighten trail when previous bar range > 2.5x ATR) was KILLED at Gate 5O. Zero validation change at thresholds 2.0 and 1.5. O5 progressive trailing (profit-based) already captures the trailing stop optimization. The stop ratchet (`max(stop_price, trail)`) means once O5 has tightened, there's no room for further bar-level tightening. Engine `max_trail_mult` field preserved for potential future use.

17. **Portfolio tools couldn't load wrapper strategies (bug fix).** `v3/portfolio.py`, `v3/correlation.py`, and `v3/regime_analysis.py` used `importlib.util.spec_from_file_location()` to load strategies, but the project root wasn't on `sys.path` in CLI context. Wrapper strategies (s37-s44) do `from strategies.sNN_base import strategy` which failed with `ModuleNotFoundError`, silently swallowed by `except Exception: pass`. Fixed by adding `project_root = os.path.dirname(v3_dir)` to sys.path in `_get_token_trades()` and `_detect_strategy_market()`. Also fixed auto-detect to use signature inspection (2-arg = combined) for wrappers that don't directly reference `MarketType.COMBINED`.

15. **External data overlays need 2+ years for validation.** s38 (ETF flow sizing) was identical to s11 because ETF data covers only 15 months (Dec 2024 – Mar 2026). Walk-forward validation spans 6 years, so the overlay is invisible. Infrastructure (tools/fetch_etf_flows.py) preserved for live signal enrichment. Lesson: don't prototype overlays on data shorter than 2x the WF training window.

18. **Signal discovery: cross-timeframe divergence is the strongest STABLE signal.** `ret_1_1h_vs_4h` (IC=-0.376) and `rsi_1h_vs_4h` (IC=-0.291) are both STABLE (drift < 0.001/yr) and LEADING (HIGH confidence). They work across ALL regimes. Mechanism: 1h indicator diverging from 4h predicts short-term reversion. This is micro mean-reversion (4h horizon) unlike the macro mean-reversion that consistently fails in crypto.

19. **Signal discovery: regime-conditional EMAs are powerful but DECAYING.** `ema_50_in_QUIET` (IC=-0.621 at 168h) is one of the strongest signals but decayed from -0.528 to -0.120 over the test period (+0.142/yr drift). Market structure changes erode regime-specific EMA edges. Cross-TF signals are more durable.

20. **Signal discovery: 92% of signals are sign-consistent across horizons.** A signal that predicts returns at 1h also predicts at 168h — just stronger/weaker. 168h is the most common peak horizon (28/73 features). This validates using the same signal with different hold periods.

21. **Signal discovery: only 23% of signals are genuinely LEADING (causal).** Of 252 FDR-passing signals, 57 are LEADING (forward IC >> reverse IC), 111 are LAGGING (describe past returns), 84 are SYMMETRIC. Only 16 have HIGH confidence. Strategy construction should prioritize these 16.

22. **Paper trading engine now matches backtest fidelity.** All 13 gaps between paper trading and backtest engine fixed: trade management (stop/trail/target/max_hold), slippage model (3bps + sqrt(participation)), ADV-based Kelly sizing, funding sign convention, edge threshold, regime exit min hold, capital split, liquidation for 1x shorts. Verified with 10-test suite.

23a. **V4-Gate 5 Sharpe/MaxDD delta tests break against near-riskless baselines.** s57 carry has 0.48% max DD and Sharpe 8.52 — near-arbitrage. Adding ANY directional strategy fails the Sharpe delta > 0 and MaxDD delta <= 2pp criteria because the baseline is already at the risk-free frontier. s59 has positive marginal Sharpe contribution (Grinold-Kahn: SR 3.83 > rho*8.52 = 2.42), confirming it adds portfolio value at optimal allocation (~5-8%). Future gate evaluations against extreme baselines should use marginal Sharpe contribution test, not simple delta comparison.

23. **Leveraged strategies fail at 5x.** s50 (5x leverage momentum), s52 (5x funding), s56 (5x max leverage) all killed. Fees are amplified more than edge. s57/s58 use 1x leverage with aggressive sizing (size_mult=3.0, cap_mult=15.0) instead — large positions without fee amplification.

24. **CRITICAL: Discovered signals only work as overlays, not standalone strategies.** Raw signal-based entries (e.g., enter when `ret_1_1h_vs_4h` z-score > 2) did not generate positive returns on their own. The signals have genuine IC (predictive power), but the IC translates to edge only when layered on top of existing well-performing strategies as timing/sizing overlays. Standalone signal strategies (s56) were killed. The successful approach is s57/s58: use the existing s44/s30 carry strategies as the base, and apply signal discovery outputs to improve entry timing, position sizing, and regime conditioning. **Lesson: IC != tradeable edge. Signals improve existing strategies, they don't replace them.**

25. **Counter-trend (s63) adds return but increases drawdown.** s63 vol spike reversal fades extreme vol spikes (vol_ratio > 3x). s58+s63: +1017% (+47.5% vs baseline), but MaxDD increases from 1.2% to 6.4%. Worth it in full portfolio but not as clean as s65. s63 collects positive funding on shorts, partially offsetting s58's negative funding.

26. **Funding carry (s65) is the best portfolio complement found.** s65 harvests structural funding rate imbalance (retail long bias). s58+s65: +1217% (+76.7% vs baseline), Sharpe INCREASES from 8.25 to 8.52, MaxDD barely changes (1.2% → 1.4%). Collects $240K in funding income. Different edge family than momentum or counter-trend.

27. **Four genuinely different edge families now validated in V4 portfolio.** (1) Momentum — s56, trend following. (2) Basis carry — s57, premium convergence. (3) Counter-trend — s63, fades extreme vol spikes. (4) Funding carry — s65, harvests structural funding payments. Full 4-strategy portfolio: +1684%, Sharpe 8.26, MaxDD -5.0%, $3.5M from $200K.

28. **V4 portfolio transforms weak V3 strategies into excellent complements.** s25 (killed V3) → s63 (+1017% in portfolio). s29 (Tier B, 20.1% V3 rate) → s65 (+1217% in portfolio, Sharpe increases). The shared capital + multi-token diversification effect is the key enabler.

29. **Portfolio alpha bar is now very high (Sharpe >3 to add value).** s67 funding momentum had excellent decorrelation (all <0.2 vs existing) and was profitable standalone (Sharpe 1.90, +94.6%), but failed V4-Gate 5 because adding it diluted capital from higher-performing s63 (3.95) and s65 (5.65). In the current 4-strategy portfolio, a new strategy needs standalone Sharpe > ~3.0 to overcome capital dilution. Funding momentum (following) is a weaker edge than funding carry (fading): s65 Sharpe 5.65 vs s67 Sharpe 1.90.

30. **PAPER TRADING: 80% of exits cluster at the no_stop_bars boundary.** 12 of 15 closed trades held exactly 24 bars (= no_stop_bars for s56/s60/s59). The trailing stop activates and fires immediately when protection expires. Trades never actually run with an active progressive trail. This suggests the 24h protection period is either too long (positions have already moved past stop) or the initial stop is too tight relative to realized volatility.

31. **PAPER TRADING: Funding costs consume 67% of gross PnL.** Total funding drag: -$1,232 out of +$1,852 gross realized. PIPPIN alone paid $1,071 in funding across 3 trades for just $389 net. For perp longs in tokens with strong retail long bias, funding is the dominant cost — exceeding both entry and exit fees combined. Funding-aware exit logic is a high-priority optimization.

32. **PAPER TRADING: Zero profit-taking exits used.** All strategies have target_mult=999 (disabled). The only exit paths triggered in live trading are trailing stop (93%) and regime exit (7%). No RSI, mean-target, or max-hold exits observed. Win/loss ratio is 0.96x with +$41 expectancy — razor-thin. The exit architecture relies entirely on trailing stops, which fire at the no_stop_bars boundary (finding #30).

33. **PAPER TRADING: s65 funding carry validating as best complement.** s58+s65 pool leads at +4.12% MTM return. The 4-edge portfolio (s56+s57+s63+s65) matches at +4.07% despite s63 being underwater. s65's 48h no_stop_bars means very few closed trades — mostly unrealized gains. Need 50+ closed trades to confirm.

34. **PAPER TRADING: s63 counter-trend struggling (0% win rate).** 2 closed shorts (BERA -$1,149 at 12 bars, RENDER -$441 at 18 bars) both stopped out. Counter-trend entries may be premature — the vol spike signal fires but the reversal hasn't completed before the stop activates. s63's 12h no_stop_bars may be too short for mean reversion to play out.

35. **PAPER TRADING: VVV concentration risk.** VVV accounts for 5 of 15 closed trades and +$6,356 of the +$620 net realized PnL. Without VVV, the portfolio would be -$5,736 on realized trades. Single-token dependency is a concern — need to monitor whether this is VVV-specific alpha or broad strategy alpha.

36. **Fixed TP at 3x ATR is optimal for counter-trend (s63).** Submission 4 (s75) passed Gate 5O: Calmar +17.6%, Return +18.5%, Sharpe +7.8% solo; +31.4% return in s58 combo. Mean reversion trades have a natural profit cap — price reverts to the mean but rarely overshoots. Trail-only exits (target_mult=999) give back gains when the original trend resumes. TP at 3x ATR captures 264 trades (11.8%) that would otherwise trail back to loss. Avg winner INCREASES +22.7% because TP locks in gains the trail would return. Deployed to paper trading as s58+s75 pool.

38. **Conviction-based entry scoring eliminates seed sensitivity.** Ranked mode (pure conviction ordering) produces 0% return variation across 10 seeds — fully deterministic. Hybrid mode (3 tiers, shuffle within) has ~3% variation. On diverse multi-strategy portfolios (4-edge, super5), conviction scoring improves Calmar +59-281% and DD by up to -67%. However, results are NOT universal: solo strategies and some 2-strategy combos regress. Deployed selectively: 4-edge-conv (ranked), super5-conv (hybrid).

39. **Dynamic regime-weighted allocation works for multi-strategy portfolios.** super5-dyn (5-strategy dynamic) improved return +28% over static allocation in backtest. Regime weights shift capital toward strategies that perform well in current BTC regime (DOWNTREND currently: s80=0.79, s81=0.80, s57=1.20, s60=1.44, s63=0.77). s80+s81-dyn uses equal weights (1.0/1.0) since both perform similarly across regimes.

40. **s80 cross-sectional momentum + s81 sector rotation add genuine diversification.** Both are V4-native strategies using perps. Different edge families (cross-token ranking, sector narrative) vs per-token signals. Paper trading started Mar 12.

41. **Regime blocks explain non-redeployment of older strategies.** s58 and older pools showing 0 entries is correct behavior: 29 tokens in DOWNTREND, s56's REGIME_SIZE=0.0 completely blocks entries. s57 carry can still enter but needs specific basis conditions. Not a bug.

37. **Funding-aware exit overlay KILLED — funding drag is not addressable via exit timing.** Submission 1 (s73/s74) tested 7 thresholds (0.01%–0.5%) on s56 and s60. Calmar degrades at EVERY threshold. Root cause: funding costs are only 1.2% of total PnL in 12-month backtests. The paper trading finding F31 (67% of PnL consumed by funding) was a small-sample artifact (15 trades, sideways March). More fundamentally, high-funding tokens (ARC, PIPPIN) produce the biggest winners AND losers — a funding-based exit can't discriminate direction. The engine capability (`funding_exit_threshold`) is preserved for potential future use but has no viable threshold on current strategies.

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
| **Strategy-configured sizing** | `v3/engine.py` (`size_multiplier`) | Generic `size_multiplier` field (float or np.ndarray). Applied in `_simulate` + `_simulate_combined`. Enables overlays like regime sizing, weekend reduction, vol-managed leverage without engine-specific logic. |
| **Multi-exchange** | `freqtrade_bridge/exchange_registry.py` | Binance, Kraken, Hyperliquid with exchange-specific fees/slippage |
| **Paper trading** | `paper_trading/` | Full infra: instance management, equity tracking, monitoring, Gate 4 SPRT |
| **15-min data** | `tools/fetch_binance_15m.py` | Bulk 15m candle download for all spot tokens. Enables sub-hourly analysis and defensive stops. |
| **ETF flow data** | `tools/fetch_etf_flows.py` | Daily BTC/ETH ETF inflows from SoSoValue + Farside. Enables ETF flow overlay strategies. |
| **Progressive trailing stops** | `v3/engine.py` (`trail_schedule`) | Dynamic trail tightening by profit level. Strategies define schedule as [[atr_profit, trail_mult], ...]. |
