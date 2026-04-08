# Strategy Mission Backlog
## 2026-04-08 (late session) update — Mission P real value, 4 gate failures, s523c path-dependence discovery

**Mission P (live)**: real Calmar contribution on s523c 12mo measured at **+13.24 points** (Calmar 20.05 → 33.29, return +130pp, MaxDD shrink 11pp). Much bigger than the single-window headline suggested. Mission P is doing more than previously credited — it's also rescuing "post_cascade dead zone" trades through its portfolio-state lens.

**Mission C (Forward-Forward)**: KILLED definitively. Literature review: zero published wins for FF on financial time series in 3 years. Two empirical FF experiments on BTC (walk-forward and multi-class oracle) both tied or lost to trivial baselines. SCFF and other 2024-2025 variants benchmarked only on images/audio. Permanently in graveyard. DO NOT REVISIT. (Persistent memory: feedback_ff_dead_end_for_trading.md)

**Mission D (FIF×drift standalone)**: PARKED. Real alpha (Sharpe 1.40, Calmar 18.37, MaxDD -4.1%, alpha_t +3.58, 6yr). Max correlation vs Tier A book 0.089 (exceptional dedup). Gate 2 bootstrap 5/6 pass (6/6 block bootstrap). **Gate 3 raw_backtest fails AIPIP-0031 promote bar** (Sharpe 1.06 < 2.0) — structural archetype mismatch (low-frequency diversifier, not Sharpe-2 standalone). Gate 3.5 (lower threshold 2.0→1.5 + 9 tokens) regressed. Genuinely interesting finding: DOGE/XRP are the TOP alpha contributors, not BTC/ETH/SOL. Parked pending diversifier-class promote track.

**Mission J (cascade recovery standalone)**: PARKED after 4 Gate 2 attempts. Real alpha (alpha_t consistently +1.3 to +1.55). Gate 2.5 (5-symbol sub-basket NEAR/LTC/ETH/BNB/LINK + vol sizing) was best = 4/6 strict criteria, 83% bootstrap positive. Gate 2.6 (more leverage) WORSE. Gate 2.7 (bear-regime filter) CATASTROPHIC (halved alpha_t — the filter removed the CORE alpha events; capitulation cascades ARE the alpha). Basket beta to BTC = 1.36, structurally entangled. Needs BTC beta hedge layer as a structural fix — not attempted this session. PARKED.

**Missions Q / Q2 / Q3 (cascade timing overlays)**: all KILLED via artifact discovery. Three contamination sources identified:
1. Biased state classifier (30min pre-peak active, forward-looking pre_cascade check)
2. Repricing artifact (close.asof() systematically 0.2% better than engine's slippage-adjusted fills; shift=0 gave +$104k drift)
3. Path-dependent stops (small entry-price shifts re-roll stop-trigger outcomes for many trades)

When all three were controlled for, the causal cascade-timing effect on s523c was ~0. **The 1-hour-fill-delay in s523c is not waste** — it's load-bearing via stop triggers (see next finding).

**CRITICAL NEW FINDING — s523c path-dependence**: s523c uses ATR-based stops at entry_price ± 5×ATR. This makes the strategy SENSITIVE to entry price in a nonlinear way. A 0.2% shift in entry price moves the stop level by 0.2%, and for trades near their stop boundaries, this flips outcomes between "stopped out at loss" and "held to profitable max_hold exit". Measured: a 1-hour entry shift produces -38% per-trade PnL change with only 10 fewer trades. **Implication for future s523c overlay research**: price-based entry modifications are unreliable — any shift re-rolls the stop-trigger lottery and produces apparent "instability" that isn't real edge/loss. State-based overlays (like Mission P's breadth cull) are stable because they don't interact with individual entry prices.

**Three methodology lessons** (appended to findings/strategy-findings.jsonl):
1. shift=0 sanity check is MANDATORY on any replay experiment that recomputes PnL
2. Engine-level backtest is the only ground truth — replay tests miss state cascades
3. Path dependence via stops creates backtest metric instability that isn't random chaos but is still unreliable for optimization

**Next session priorities** (in order):
1. Monitor Mission P live fires
2. Live API polling PoC — switch from binance.vision batch (8h delay) to fapi.binance.com /futures/data/ live polling for fresh composite data
3. Mission J Gate 2.8 — BTC hedge layer (the one structural fix not yet attempted)
4. Diversifier-class promote track AIPIP for Mission D
5. Re-examine s523c Calmar variance across windows under the new "path-dependence" lens

---


Created: 2026-04-07
Source: External research dump (VPIN/TE/novel-indicators/Forward-Forward) + user request "create new ideas and missions"

Live missions (NOT in this file): see `.claude/.strategy-mission` for s513 + s523c paper deployment.

---

## Mission A — Liquidation Cascade Recovery (alt contagion bounce)
**Status:** PARKED (2026-04-07) — signal is real but blocked by data granularity
**Gate 1 outcome:** KILL on paper spec (funding-z > 1.5 fires only 1x in 5y post-2021; edge decayed to 6 L12M events, 3 losers)
**Rescue outcome:** KILL on L12M (data granularity), but **full-sample signal is strong**: liq-$ z-score gate at T+12h shows **t(α)=3.59, 100% win rate on 9 events, +5.25% alpha**. Blocker: Coinalyze parquets are daily-only; rescue had to use prior-day z-score → 9 valid hits in 5y, 1 in L12M.
**Unblock path:** Acquire hourly liquidation data via (a) Coinalyze API paid tier, (b) forward WS `@forceOrder` capture (T+0, no backtest), or (c) reconstruct from aggTrades once Mission F pipeline is live.

**2026-04-07 update — Rescue #2 (hourly Coinalyze) result:** DEFINITIVELY PARKED, data-bound. Coinalyze free tier caps hourly history at ~120 days per symbol; rescue got only 5 cooldown-separated events in the 113-day usable window. 4/5 winners (+2.36% mean) but t(α)=-0.32 — too thin to resolve alpha vs BTC beta. Edge is neither confirmed nor refuted. Tool changes shipped: `tools/fetch_coinalyze.py` now supports `--interval 1hour` flag, writes to `data/alternative/coinalyze/liquidations_1h_parquet/`. Hourly fetcher works and can be re-run anytime.

**Revival condition:** ~18 months of hourly cross-exchange liquidation data → ~20 events → statistical power. Cheapest path: turn on forward WS `@forceOrder` capture as background infra (like daily metrics loop). Re-run `research/cascade_recovery_gate1_rescue_hourly.py` unchanged once data accumulates.
**Reports:** `research/cascade_recovery_gate1_report.md` (+ rescue appendix), `research/cascade_recovery_gate1.py`, `research/cascade_recovery_gate1_rescue.py`
**Original hypothesis below ↓**
**Hypothesis:** When BTC cascades hard, high-leverage / lower-liquidity alts cascade harder and bounce harder. Trade the bounce, not the fall.
**Source claim:** Parameter-free strategy, +2.33%/trade, Sharpe 3.58, 72 OOS trades over 5y (Medium article, March 2026). Beta-decomposed: 54% BTC movement, alpha p=0.182 — so really a regime-conditional entry, not standalone alpha.
**Trigger (paper):** BTC 1h return < -3% AND volume > 2x 24h AND funding z-score (168h) > 1.5
**Holding window:** T+24h to T+48h post-cascade
**Data we have:** `data/alternative/coinalyze/liquidations_parquet/{binance,bybit,okx,bitmex,bitfinex,huobi}.parquet`, funding rates, 1h OHLCV across 226+ tokens
**Why it fits:** Event-driven, complementary to s513 (momentum) and s523c (positioning). If real, becomes a Tier A entry signal with ~30 trades/year.
**Kill criteria:** Gate 1 raw backtest L12M Sharpe < 2.0 OR Calmar < 3.0 OR returns indistinguishable from BTC beta.

## Mission B — Composite Fragility Index (regime overlay)
**Status:** KILLED 2026-04-07 (Gate 1). Best rule: s513 Calmar +2.5%, s523c +1.2% — within noise vs required +15%.
**Root cause:** Index as spec'd measures funding-long *euphoria*, not crash risk. Funding flips negative during crashes and the 168h z-score normalizes it fast, so index sits at/below median at LUNA/FTX/COVID.
**Future redesign path (if ever revisited):** asymmetric weighting toward negative funding, longer baseline window, add realized-vol term, longs-only overlay.
**Data flag discovered:** `data/alternative/binance_funding_rates_full.json` contains ONLY BTC+ETH despite the name. Use `funding_ls_proxy/` (149 symbols daily) instead.
**Reports:** `research/fragility_index_gate1_report.md`, `research/fragility_index.parquet`
**Original hypothesis below ↓**
**Hypothesis:** A funding-rate-based stress index can act as a regime overlay that improves DD on existing strategies (s513, s523c) without changing entries.
**Components:**
  1. Volume-weighted avg funding rate across top-50, z-scored over 168h
  2. Funding dispersion (cross-symbol stdev) z-scored
  3. Cross-exchange funding gap (Binance vs Bybit vs Hyperliquid where available)
**Output:** Single 0-1 fragility score; high = de-risk, low = full size
**Data we have:** Full funding rate history across exchanges
**Why it fits:** Doesn't require new strategies, improves existing ones. Cheap to test as a sizing multiplier.
**Kill criteria:** Doesn't materially improve s513/s523c Calmar (>15% lift required).

## Mission C — Forward-Forward Online Confidence Gate
**Status:** QUEUED
**Hypothesis:** FF goodness scores from `/workspace/ff_trading_v2.py` (your own experiment showed 57.3% online accuracy at 20ms latency) can act as a layer-wise confidence gate on s513 entries.
**Approach:** Wire FF model to s513's feature stack (MACD/RSI/Donchian + regime), use goodness score as size multiplier (Kelly-style), retrain layer-by-layer online.
**Why it fits:** You already built and validated the prototype. FF beats backprop in volatile regimes (53.2% vs 51.1%) — exactly when s513 has its worst DDs.
**Data we have:** Existing s513 feature pipeline + FF prototype.
**Kill criteria:** FF gate doesn't improve s513 OOS Sharpe by ≥0.3 OR doesn't reduce max DD by ≥20%.

## Mission D — FIF × drift Standalone Strategy
**Status:** Gate 1 STANDALONE PASS 2026-04-08 — promoted for Gate 2 + paper deployment
**Best variant (sweep over threshold ∈ {1.5, 2.0, 2.5}, hold ∈ {72h, 168h}, mode ∈ {long_only, long_short}):**

| Param | Value |
|---|---|
| Threshold | z-score ≥ 2.0 |
| Hold | 72 hours |
| Mode | long_only |
| Universe | BTC + ETH + SOL (1h returns, sample step 24 bars) |
| Cost | 4 bps fee/side + 5 bps slippage = 13 bps round-trip |
| Capital allocation | 1/3 of capital per token per trade |

**Standalone results (~6 years 1h data, BTC since 2020-01, SOL since 2020-09):**
- Trades: 101
- Total return: +74.6%
- **Sharpe: +1.40**
- **MaxDD: -4.1%** ⭐
- **Calmar: +18.37** ⭐
- **Alpha t-stat vs BTC beta: +3.58** ⭐
- Win rate: 65.3%
- Avg ~17 trades/year

**Key findings:**
1. **Long-only dominates long-short.** Every long-only variant beats its long-short counterpart on alpha_t. The signal is asymmetric — positive z-score works, negative does not.
2. **Higher threshold = higher win rate but fewer trades.** thr=2.5 gives 73% win rate with 41 trades. thr=2.0 is the sweet spot for total return + Sharpe.
3. **Yellow flag mitigated by threshold filter.** Spearman << Pearson on the IC test means outlier-driven; by acting only on |z|>2.0 we EXPLOIT this rather than fight it.
4. **Strongest alpha in entire sprint** alongside Mission J (+3.54 t-stat). Mission D has the cleaner risk profile.

**Cross-token consistency from Gate 0:**
- 1h, 4h horizons: signs flip across BTC/ETH/SOL → not robust
- 24h, 72h, 168h horizons: ALL POSITIVE on BTC/ETH/SOL → robust
- IC magnitudes 0.024-0.049 (modest but cross-token agreement is rare)

### s523c overlay test (RESULT: FAIL — keep as standalone only)

Tested FIF × drift as a directional confirmation gate on s523c trade log:
- For each LONG entry: keep only if FIF×drift z >= threshold
- For each SHORT entry: keep only if z <= -threshold

| Threshold | Trades kept | Return | MaxDD | Sharpe | Calmar | ΔCalmar |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 634 | +747.2% | -37.3% | +2.88 | +20.05 | — |
| 0.0 (sign) | 219 | +124.9% | -50.6% | +1.16 | +2.47 | **-87.7%** |
| 0.5 | 134 | -46.7% | -76.6% | -0.01 | -0.61 | -103% |
| 1.0 | 69 | -16.4% | -51.9% | -0.01 | -0.32 | -102% |
| 1.5 | 41 | +42.2% | -15.7% | +0.87 | +2.69 | -87% |

**Every variant catastrophic.** Confirms the session-wide pattern: **per-trade external signal filtering on s523c always fails**. Mission P was the only exception because it operates at portfolio level (breadth across many positions), not per-trade entry confirmation. s523c's own ranking is internally optimized — adding any external filter destroys edge.

### Gate 2 plan
1. **Bootstrap MC** on the standalone strategy — 100 trial bootstrap on the 101 trades, report Sharpe/Calmar/alpha_t distributions. Resolve the outlier-driven yellow flag.
2. **Walk-forward** validation: split 6 years into rolling windows, fit z-score window on H1, test on H2.
3. **Wider universe** when Mission F Phase 2 completes — add the 10 alts to the basket. Should produce more trades and lower per-trade concentration.
4. **Position sizing optimization** — currently 1/3 capital per trade. Test full Kelly vs fixed fraction.
5. **Try as Mission J cascade gate** instead of s523c filter — Mission J is event-driven on cascades; FIF × drift adds drift confirmation. Different mechanism than the failed s523c overlay.

### Gate 3 path (if Gate 2 passes)
- New strategy file `strategies/s550_fif_drift.py` (or similar v4-compatible naming)
- Single per-token signal computation (fits the v4 strategy interface naturally)
- Paper trade in a new pool alongside s513 + s523c
- ~$50K initial capital for 30-day observation period

### Files
- `research/mission_d_gate0.py` (Gate 0 IC test)
- `research/mission_d_gate0_report.md`, `research/mission_d_gate0_results.json`
- `research/mission_d_gate0_catalog.md` (full 8-indicator catalog)
- `research/mission_d_gate1.py` (standalone backtest)
- `research/mission_d_gate1_results.json` (sweep results)
- `research/mission_d_gate1_overlay.py` (s523c overlay test)
- `research/mission_d_gate1_overlay_results.json`

**Original hypothesis below ↓
**Winner:** `FIF × drift` composite (Fisher Information Flow × drift estimate)
  - **Cross-token consistent POSITIVE** sign on BTC/ETH/SOL at 24h/72h/168h horizons
  - IC 0.03-0.05 (modest but cross-token robust — rare)
  - Matches the paper's claimed structure: "meta-indicator × direction"
  - Interpretation: FIF tells you how estimable drift is; their product says "how confident should we be about the direction right now"
  - **Use case:** position sizing / regime gate on existing directional strategies, NOT standalone entry signal
**Marginal / killed:**
  - HV (Hurst Velocity): MARGINAL — only SOL 1h fluke, doesn't replicate BTC/ETH
  - SRI (Spectral Rotation Index): MARGINAL — only SOL 72h fluke
  - WRD tail_shift: PASS on BTC/ETH at 4h but flips on SOL; fold into existing 4h reversal research, don't dedicate Gate 1
  - FIF raw magnitude (without direction): sign flips across tokens, not robust
**Yellow flag:** Spearman ICs are 3-5x weaker than Pearson for winning signals → outlier-driven not monotonic. Edge from extreme events. Bootstrap MC required before Gate 1 capital commitment.
**Strongest single signal found:** FIF on SOL 168h, Pearson IC=-0.0975, t=-4.39, n=2007 — but SOL-specific (opposite sign on BTC/ETH).
**Gate 1 plan (next session):**
  1. Build FIF × drift composite as a per-token feature on BTC/ETH/SOL and the 10 alts Mission F Phase 2 is fetching
  2. Test as position sizing multiplier on s513 (momentum strategy). Hypothesis: reduce s513 position sizes when FIF × drift is weak/negative at entry
  3. Test as regime gate on Mission J cascade entries. Hypothesis: only take the long alt bounce if FIF × drift is positive at entry bar
  4. Optional: standalone small strategy (long when z-score > 2, short when < -2, 72-168h hold, 5-10% allocation)
  5. Bootstrap MC on best variant — yellow flag about outlier-driven nature must be resolved before capital
**Tier 2 (deferred):** TCB (topological crash barometer), LII (Lyapunov instability) — need ripser/persim install + more compute time
**Tier 3 (deferred):** TMP (thermodynamic market potential), ROFD (Rényi order flow divergence) — need orderbook adaptation from 10-band structure (Mission F Phase 2 data helps but formulas need rework)
**Synergies untested:** LII×FIF×HV predictability filter, SRI×HV×WRD rotation anticipator, TCB+TMP topology-thermodynamics gateway, ROFD+TMP+WRD informed flow microscope — worth exploring only if/when Tier 2/3 indicators are implemented
**Files:** research/mission_d_gate0.py, research/mission_d_gate0_report.md, research/mission_d_gate0_results.json, research/mission_d_gate0_catalog.md
**Original hypothesis below ↓
**Hypothesis:** 8 physics/info-theory indicators (LII, FIF, HV, WRD, SRI, TCB, TMP, ROFD) provide non-redundant signal vs RSI/MACD/ATR family.
**Action item:** Read `/workspace/novel-indicators-contrarian-math.jsx`, classify each indicator as (a) computable from OHLCV+funding+OI, (b) needs L2 orderbook (defer to Mission F), (c) too expensive. For (a), run IC test on BTC 1h returns.
**Layered framing from JSX:**
  - Layer 1 (Predictability gate): LII + FIF
  - Layer 2 (Regime): HV + WRD + SRI
  - Layer 3 (Stress): TCB + TMP
  - Layer 4 (Execution): ROFD (needs orderbook)
**Kill criteria:** No indicator from layers 1-3 shows IC |ρ| > 0.05 with t-stat > 2 across BTC/ETH/SOL.

## Mission E — VPIN on volume bars
**Status:** PARKED — needs Mission F first (orderbook/tick data)
**Reason parked:** VPIN requires either tick trades or aggressive 5-min approximation; better executed once we have a real orderbook/trade pipeline.

## Mission H — Microstructure Signal Library
**Status:** Phase 1 SHIPPED 2026-04-07 — DtM, OFI-T, VDV on BTC/ETH/SOL
**Tools:** `tools/build_microstructure_signals.py` (CLI, 392 lines)
**Data:** `data/perp/binance/{BTCUSDT,ETHUSDT,SOLUSDT}/microstructure_1min.parquet` — 166 MB total, 519,840 rows/symbol, 25 cols
**Signals (all 1min resolution):**
  - **DtM:** dtm_bid_1pct, dtm_bid_5pct, dtm_ask_1pct, dtm_ask_5pct, dtm_asymmetry_5pct, dtm_total_5pct, dtm_bid_5pct_z168h, dtm_total_5pct_z168h, dtm_bid_velocity_1h
  - **OFI-T:** ofi_t_1min, ofi_t_5min, ofi_t_30min, ofi_t_4h, ofi_t_notional_5min
  - **VDV:** vwap_5min, vwap_30min, vwap_4h, dev_5min, dev_30min, dev_4h, vdv_5min, vdv_30min, vdv_4h
**Sanity check verdict:** signals LEAD Mission A cascade events (21 found in BTC 1y data)
  - `dtm_bid_5pct_z168h` at -0.32σ at T-1h (market makers pull bids BEFORE the cascade prints)
  - `vdv_5min` at -0.74σ at T-1h, then flips to +1.92σ at T+1h (bounce signature)
  - `ofi_t_5min` is coincident (-0.30σ at T=0) not leading — need OFI-M (Phase 2) for earlier lead time
**Spec reference:** `research/mission_a_orderbook_signals.md`
**Phase 2 (queued):** OFI-M (maker-side), cheapest remaining signal, sharpens lead time further
**Phase 3 (speculative):** LCP (liquidation cluster proxy from OI history + leverage tranche model)
**Phase 4 (the strategy):** Mission A backtest using CPI composite trigger over 1y BTC orderbook data

## Mission F — Orderbook Data Infrastructure
**Status:** Phase 1 SHIPPED 2026-04-07 — BTC/ETH/SOL × 365d backfilled from Binance Vision
**Tools:** `tools/fetch_binance_vision_bookdepth.py`, `tools/fetch_binance_vision_aggtrades.py`, `tools/README_binance_vision_orderbook.md`
**Data layout:** `data/perp/binance/{BTCUSDT,ETHUSDT,SOLUSDT}/{bookdepth,trades_1s}/{YYYY-MM-DD}.parquet` — 3.06 GB total (467 MB bookdepth, 2.61 GB trades_1s)
**Verified:** VPIN computes correctly, volume classification balanced, depth curves realistic, matches $10B+/day BTC perp notional
**Schema note:** Binance Vision bookDepth publishes 10 levels (±1/2/3/4/5%) — NOT 12. The ±0.2% seen in one test file appears to be an anomaly or recent change. Downstream features should assume 10 levels.
**Phase 2 (queued):** Scale to top 30 perps once a microstructure strategy proves value. Est +5 GB for bookdepth, +40-60 GB for trades_1s.
**Phase 3 (deferred):** Daily incremental T+1 update via `tools/run_daily_metrics_loop.sh`. Live WS capture only when moving to paper trading.
**Original hypothesis below ↓**
**Goal:** Stand up an orderbook + trades data pipeline that unlocks the entire microstructure family: VPIN, ROFD, OFI, queue imbalance, micro-price, taker/maker flow, liquidation impact.
**Scope decisions needed:**
  1. **Source:** Binance WebSocket native (free, 4ms latency, `@depth` 100ms snapshots, `@trade` per-tick, `@forceOrder` liquidations) vs Tardis.dev historical (paid, ~$25/mo, full history) vs both
  2. **Universe:** Top-30 perps? Top-50? Just BTC/ETH/SOL initially?
  3. **Resolution:** Full L2 (10MB/symbol/day) vs L1 best bid/ask (200KB/symbol/day) vs aggregated 1-second snapshots
  4. **Storage:** Parquet rotation (daily files), compression (zstd), retention policy
  5. **Live vs historical:** Start a live capture immediately to build forward history, OR buy backfill from Tardis to skip the wait
**Estimated storage (top-30, full L2, 1y):** ~100GB
**Estimated storage (top-30, L1 + trades, 1y):** ~5GB
**Why now:** Unlocks VPIN, ROFD, OFI strategies (Missions D-Layer4, E, and microstructure SHAP/GBM from research dump). Also lets us validate liquidation cascade timing (Mission A) at higher resolution.
**Deliverables:**
  - `tools/orderbook_capture.py` — async WS consumer writing to rotating parquet
  - `data/orderbook/{symbol}/{date}.parquet` layout
  - `tools/orderbook_loader.py` — backtest-friendly reader matching existing parquet conventions
  - Run as systemd / nohup background process alongside daily metrics loop
**Kill criteria:** None — this is infrastructure, not a strategy. Either we build it or we cap our microstructure ceiling.

## Mission G — Outsized Early-Profit Lock-In (s523c exit overlay)
**Status:** DEFINITIVELY KILLED 2026-04-07 after four methodology bugs unwound sequentially
**Scope:** s523c only (s513 was a negative-control that correctly failed — feature not bug)
**Final verdict:** at 50 bps slippage every variant (A-FULL, A-HALFLOCK, A-25, A-50, A-75) cuts Calmar by 60-75%. A-75 best at ΔCalmar -61%. Even at 0 bps before any friction, the edge dies once walk-forward stop semantics are correct.
**Root cause of the original false positive:**
  - Gate 1: intrabar HIGH fill (unattainable) inflated the headline by ~half
  - Verification: 1y window masked regime sensitivity
  - Gate 2: narrowed prematurely from 5 actions to 1, correctly killed A-FULL but missed A-HALFLOCK
  - Autopsy: A-HALFLOCK "rescue" was a recompute bug — math clamp `max(0, 0.5*lock - realized) * notional` instead of real walk-forward stop
  - Final validation (with correct `simulate_overlay` walk-forward): all variants fail at 0 bps
**Why s523c is structurally immune to early lock-in:**
  - The velocity trigger fires on continuation moves
  - s523c's Calmar is driven by the right tail of outsized winners (Pareto distribution)
  - Locking in early truncates that tail — trading load-bearing upside for noise reduction
**Valid diagnostic finding (keep for future):** median s523c winner gives back 100% of peak MFE. This is REAL but the simple velocity-lock is the wrong mechanism to exploit it.
**Correct future hypothesis (untested, would need fresh Gate 1):** "tighten the trail asymmetrically ONLY after a trade has banked enough margin to absorb a normal pullback". Different mechanism from early lock-in — doesn't truncate the right tail, just tightens friction in the body.
**Methodology lessons recorded:**
  1. Always test multi-year rolling windows before engine code (1y is not enough for exit overlays)
  2. When stdev >> mean, trust the median
  3. Never prematurely narrow the action/parameter grid between gates
  4. Always re-verify the "rescue" implementation against the ORIGINAL simulation semantics — recompute shortcuts hide bugs
**Reports:** `research/profit_lockin_gate1_report.md`, `research/profit_lockin_verification_report.md`, `research/profit_lockin_gate2_report.md`, `research/profit_lockin_autopsy_report.md`, `research/profit_lockin_final_validation_report.md`
**Why it died:** On 5-year rolling 6-month windows with walk-forward threshold + limit fills:
  - Mean ΔCalmar +13.3% (below OVERFIT floor), **median -8.5%** (typical window LOSES)
  - Stdev 106% (~8× mean) — pure noise
  - **11 of 17 windows were losses**, worst -133.6% (Mar-Sep 2024)
  - Entire positive mean driven by one +366% outlier window
  - No threshold (VEL90/95/98) is robust, regime correlation too weak to filter
**Gate 1 → Verification → Gate 2 arc:**
  - Gate 1 (1y, optimistic fill): +166% Calmar (claim)
  - Verification (1y, limit fill + walk-forward): +51% Calmar (degraded but looked real)
  - Gate 2 (5y rolling windows, limit fill): coin flip with fat left tail → KILL
**Lessons recorded:** (1) 1y verification is not enough for exit overlays — always multi-year rolling windows before engine code. (2) When stdev >> mean, trust the median, not the mean. (3) The pipeline worked — killed cheap (~3h of agent time) before days of engine work.
**Diagnostic finding still valuable:** median s523c winner gives back 100% of peak MFE. The edge diagnostic is real; the simple velocity-based lock-in rule just isn't the right way to capture it. Any future lock-in attempt must be tested on 5y rolling windows from day 1.
**Reports:** `research/profit_lockin_gate1_report.md`, `research/profit_lockin_verification_report.md`, `research/profit_lockin_gate2_report.md`
**Original hypothesis below ↓
**Best combo:** `VEL95 × A-FULL` on s523c
  - Calmar 18.6 → **49.6** (+166%)
  - Return +662% → **+928%** (+266pp)
  - MaxDD -35.6% → **-18.7%** (halved)
  - Sharpe 2.89 → 3.54
**Trigger definition:** velocity ≥ 95th percentile of winner velocity distribution = ~1.33% price move per hourly bar
**Action:** close 100% (partial closes lose because remainder give-back is high)
**Diagnostic gold:** On s523c the **median winner gives back 100% of peak MFE**. 20% of trades that hit +20% MFE still close as losers. The strategy is bleeding paper gains.
**s513 outcome:** KILL. Every combo neutral/negative. s513's tight default exits already capture peaks. Failure on s513 is confirmation the s523c result isn't random — if it worked on both we'd worry about overfitting.
**Caveats:**
  - Fire-bar=0 on ~90% of fires → intrabar fill assumption matters; real latency may shave 10-20% off the result
  - Funding/fees applied to original trade duration → slightly pessimistic
**Gate 2 dedup:** VEL95 × FULL pre-empts BreakevenRatchet/Trailing cleanly. No collision with existing exit chain. Partial-close variants WOULD collide (don't pursue).
**Gate 3 prototype path:** new exit handler in `v4/exits/` (or extend `v4/exit_handlers.py`), feature-flagged per-strategy. Only enable for s523c initially.
**Reports:** `research/profit_lockin_gate1.py`, `research/profit_lockin_gate1_report.md`
**Original hypothesis below ↓
**Hypothesis:** When a trade becomes unusually profitable unusually fast, locking in some or all of that profit immediately improves Calmar more than giving the trend room to run. The intuition: outsized early moves are often mean-reverting short-term (VPIN-style exhaustion) even if the longer-term trend continues.

### What "unusually large profit in a short period" might mean (parameters to sweep)
1. **Absolute %:** unrealized PnL ≥ X% where X ∈ {5, 8, 12, 15, 20}
2. **ATR-normalized:** unrealized PnL ≥ K × entry_ATR where K ∈ {2, 3, 4, 5, 6}
3. **Leverage-aware:** unrealized PnL as % of position notional (not account) ≥ X
4. **Time-conditioned:** condition above AND elapsed ≤ T bars where T ∈ {4h, 8h, 24h, 48h}
5. **Velocity-based:** PnL/hour z-score vs trailing typical trade velocity

### Lock-in actions to test
- **Full close** at trigger
- **Partial 50%** close, trail rest at breakeven
- **Partial 75%** close, trail rest at +1 ATR
- **Partial 25%** close, tighten trail to 1 ATR (instead of default 5 ATR on s523c)
- **Move stop to +50% of gained PnL** (lock in half the paper gain, let winner run)

### Test universe
- s513: +216% / 2.71 / -16.7% baseline (or current drift)
- s523c: ~+747% / 2.88 / -37.3% current baseline (2026-04-07)
- Both at the same `--skip-wf --end-date 2026-04-05T16:00:00` CLI used in other gates

### Verdict criteria (Gate 1)
**PASS** if any (trigger × action) combo improves Calmar by ≥15% on either strategy without losing more than 20% of total return.
**NEEDS_TUNING** if DD improves materially (≥25%) but return suffers more than 20%.
**KILL** if no combo beats baseline meaningfully.

### Additional output required
- **Per-trade analysis:** distribution of max unrealized PnL per trade vs realized PnL. How often do trades reach +10%/+20%/etc. that later close lower? This directly quantifies "money left on table that could be locked."
- **Velocity distribution:** histogram of max PnL velocity (PnL/hour) for winning trades — tells us what "unusually fast" objectively means
- **Give-back analysis:** for every trade that hit unrealized >X%, what % of that peak was actually captured? If the answer is "60% on average," the lock-in rule has obvious value.

### Why this is promising
- Pure post-hoc overlay — no engine changes, easy to test on existing trade logs
- The velocity/give-back analysis ALONE tells us whether the edge exists before we even test lock-in rules
- If it works, it directly reduces MaxDD without touching entry logic (cleaner than regime overlays like Mission B)
- Fits s523c particularly well — 30-position basket, lots of individual give-backs to measure

### Out of scope
- Not implementing in the live engine yet — this is research first
- Not touching stop/trail defaults for s513/s523c — only adding an overlay rule
- Not testing on paper trades — use full backtest trade logs

## Mission I — s523c Regime Gate (F3 filter)
**Status:** KILLED 2026-04-07 on Gate 2 validation with clean 12-month data
**Reason:** The Gate 1 +104% Calmar claim was a baseline-degradation artifact. On the 60-month backtest used for Gate 1, baseline Calmar was 0.54 (thin universe early years + 1002/1513 missing-OHLC trades). F3 "rescued" the garbage baseline. On the clean 12-month backtest (baseline Calmar 19.44), F3 only improved Calmar to 20.89 (+7%, within noise) and cost 41% of total return. Sharpe actively worsened 2.70→2.51.
**Composite filters tested:** F3 + ret_3d, F3 + ret_7d, F3 + basket_dd, F3 + combined. None beat baseline. Directional confirmation correctly cut April false positives but ALSO cut December true positives, because December had mixed declining days.
**Valid diagnostic finding that survives:**
  - s523c's biggest DD is Nov 16, 2025 → Jan 17, 2026 (62 days, -$151K, -38.9%) on clean 12-month
  - December 2025 alone was -30.3%, driven by alt cascade
  - s523c's DDs are alt-cascade events, not BTC regime events
  - Strategy design depends on right-tail winners — trimming via regime pauses or velocity locks destroys more than it saves
**Meta-lesson (session-wide):** When evaluating improvements on s523c, always use the CLEAN 12-month backtest with `--end-date 2026-04-05T16:00:00`, NEVER the synthetic 60-month backtest (which has degraded baseline due to thin universe + missing OHLC). The 60-month is useful for rolling-window regime tests but not for Gate 1 threshold-checking.
**Reports:** `research/s523c_drawdown_anatomy_report.md`, `research/s523c_universe_basket.parquet`
**Discovered via:** s523c drawdown anatomy investigation after user flagged BTC regime was the wrong lens (s523c excludes BTC, trades 89-token alt universe)
**The signal:** pause s523c (no new entries) when `alts_vs_btc_30d < -10%` (s523c universe basket 30d return minus BTC 30d return)
**Gate 1 result on 60-month backtest:**
  - Baseline: 351.5% return, -64.7% MaxDD, Sharpe 0.80, Calmar 0.54
  - F3 filtered: **517% return, -39.8% MaxDD, Sharpe 1.04, Calmar 1.10** (+104% Calmar, +47% return, DD reduced 38%)
  - Pauses 29.4% of time
  - Gold standard outcome: return UP, DD DOWN
**Why it works:** s523c's drawdowns are alt-cascade events (9/10 top DDs). The alts-vs-BTC rotation signal fires BEFORE alt cascades fully materialize. Cross-asset rotation leads the cascade.
**Why trailing-DD filters fail:** F1 (basket_dd > 15%) collapsed to +1.5% return. Trailing filters fire after damage. F3 is forward-looking.
**Caveats for Gate 2:**
  1. Potential minor look-ahead (end-of-day returns used intraday) — needs 1-day lag in production
  2. 60-month baseline is degraded (thin universe early years, 1002/1513 trades missing OHLC). Must validate on clean 12-month backtest.
  3. Filter applied as "set daily return to 0" on paused days — not full v4 simulator. Need to re-validate.
  4. Pause semantics unclear: no-new-entries vs force-close positions. Pick better one.
**Cache:** `research/s523c_universe_basket.parquet` — daily basket regime features (basket_ret_24h, basket_vol_30d, basket_dispersion_24h, basket_drawdown_30d, alts_vs_btc_30d, pct_above_50dma, BTC secondaries)
**Reports:** `research/s523c_drawdown_anatomy.py`, `research/s523c_drawdown_anatomy_report.md`, `research/s523c_drawdown_anatomy_results.json`
**Also important from the drawdown anatomy:**
  - s523c's biggest ABSOLUTE-$ drawdown is the MOST RECENT one: 2025-11-07 → 2026-01-17, peak $197K → trough $124K, **-$73K / -37.3% in 71 days**, NOT yet recovered. This is happening in the current paper-live deployment.
  - The older -64.7% percent DD (2022-04 → 2023-12) was only -$42K because of small base; % drawdowns in the early thin-universe period overstate pain
  - Absolute-$ lens is more reliable than % lens for this strategy
  - F3 filter, had it been active, would have caught the current live drawdown (alts_vs_btc was deeply negative through Nov-Jan)
**Mission G kill confirmed INDEPENDENTLY from regime angle:** correlation of VEL95 ΔCalmar with s523c in-window maxDD is -0.25 (wrong sign). Mission G cut winners during baseline-profitable windows (e.g. 2024-03→09 baseline +71% but overlay -0.19). Mission G's bad trade selection is independent of market regime.
**Gate 2 plan:** re-test F3 with (a) 1-day lag, (b) clean 12-month backtest, (c) threshold sensitivity sweep {-5%, -7.5%, -10%, -12.5%, -15%}, (d) temporal distribution of fires across 5 years, (e) no-new-entries vs force-close pause semantics

## Mission M — Signal Reevaluation Exit (Interpretation B: defensive)
**Status:** KILLED 2026-04-08 at Gate 0 (diagnostic-only)
**Hypothesis:** s523c's composite signal at +3d/+5d/+7d post-entry, combined with current P&L, predicts whether the trade will eventually lose. If signal degrades AND trade is underwater, close.
**Verdict:** Signal carries no forward information. Forward signal strength → eventual pnl Pearson r = -0.012 at +3d (essentially zero, t=-0.29). Signal flip at +3d → loser rate 47.8% vs 56.2% for intact-signal trades (flip is actually slightly LESS predictive of loss).
**4-bucket analysis at +3d (n=583):**
  - A (strong + winning): 55.96% wr, +0.350 mean pnl, n=193
  - B (strong + losing): 29.90% wr, -0.097 mean pnl, n=204 ← BIGGEST LEAK, but not a signal problem
  - **C (degraded + winning): 64.71% wr, +0.335 mean pnl, n=51 ← HIGHEST win rate of any bucket**
  - D (degraded + losing): 16.67% wr, -0.002 mean pnl, n=18 ← intervention target, only 12.8pp better than baseline (below 15pp threshold)
  - Baseline currently-underwater at +3d: 29.50% wr
**The crucial insight Bucket C reveals:** once a trade is profitable, signal degradation is NOT a reason to exit. Bucket C wins MORE than Bucket A. This is direct empirical proof that s523c's right-tail-dependent design is correct, and that the failed Mission G's velocity-lock approach was structurally wrong from first principles.
**Bigger leak surfaced (Bucket B):** trades where signal stayed strong AND trade went underwater are 124-204 per window with ~17-30% eventual win rates. These bleed money and the signal is no help — the existing stops aren't cutting losers fast enough when "signal right, market wrong". This is a STOP CALIBRATION problem, not a signal problem. Could become Mission N (adaptive stop tightening for losers based on time-since-entry).
**Reports:** `research/mission_m_gate0.py`, `research/mission_m_gate0_report.md`, `research/mission_m_gate0_results.json`, `research/mission_m_gate0_enriched.parquet`

## Mission P — Event-Free Breadth-Based Direction Cull (refined Mission O)
**Status:** Gate 0 + Gate 1 PASS 2026-04-08 — FIRST s523c exit modification to survive both. Ready for Gate 3 engine prototype.

### Gate 1 results (24-month validation, weighted to recent year per user constraint)
**Best variant (validated across H1 + H2):** `grace=3d, breadth=70%, throttle=10d`
- Walk-forward H1→H2: +18.9% at 8bps, +18.1% at 50bps
- 17 parameter combos positive in BOTH halves of the 24-month sample
- Best edge island: throttle=10d dominates across breadth 65-80%

### Equity curve metrics on H2 (recent year, $50K starting)
| Variant | Return | MaxDD | Sharpe | Calmar | ΔCalmar |
|---|---:|---:|---:|---:|---:|
| Baseline | +488% | -45.3% | 1.72 | 10.77 | — |
| **Mission P** | **+579%** | **-32.2%** | **1.99** | **17.95** | **+66.6%** |
| Variant B (lever winners +50%) | +565% | -44.9% | 1.59 | 12.59 | +16.8% |
| Variant B (+100%) | +642% | -54.0% | 1.45 | 11.88 | +10.2% |
| Variant C (close + lever +50%) | +666% | -44.6% | 1.74 | 14.92 | +38.5% |
| Variant C (+100%) | +750% | -53.7% | 1.56 | 13.97 | +29.7% |

### Leverage variants tested but REJECTED
**Mission P.2 tested deleveraging losers (Variant A) and leveraging winners (Variant B/C):**
- Variant A (soft deleverage): strictly worse than full close. Closing 25/50/75% gives proportionally smaller benefit. Confirms full close is optimal for losers.
- Variant B (lever winners +50% to +100%): boosts return BUT actively HURTS Calmar and Sharpe. Best lever-up alone: Calmar 12.59 vs Mission P 17.95. Lever-up fires on temporarily-winning positions, some of which flip; leveraged losses eat leveraged gains. helped/hurt count was 115/179 (~bad).
- Variant C (combined close + lever): Best raw return (+750%) but MaxDD actively WORSE than baseline (-53.7% vs -45.3%). Calmar 13.97 (vs Mission P 17.95). Adding lever to Mission P loses Calmar.
- **CRUCIAL: liquidation risk NOT modeled.** Lever-up at 2× boost → 5.2× total leverage → liquidation distance ~19%. Real execution would degrade lever variants further. Variant B/C numbers are upper bounds.

### Why Mission P beats lever variants
On risk-adjusted metrics (Sharpe, Calmar), the rule that ONLY closes losing-direction baskets dominates anything that touches winners. The s523c right-tail principle (Mission M Bucket C) holds: don't interfere with winners, even by amplifying them. The leveraged-up positions that subsequently flipped destroyed more value than the persistent winners added.

### Validated parameters for Gate 3
- Grace period: 3 days post-entry (matches s523c's no_stop_bars)
- Trigger: ≥70% of one direction's post-grace open positions individually underwater
- Action: close 100% of that direction's post-grace positions
- Throttle: 14 days between culls per direction (10d also works, 14d slightly safer)
- Cost assumption: 50 bps round-trip is realistic and the rule survives it

### Gate 3 engine implementation path
- New exit handler `BreadthDirectionCullHandler` in `v4/exit_handlers.py` or new module
- Hooks into hourly tick AFTER no_stop_bars grace period
- Aggregates per-direction underwater fraction across all open positions for the strategy
- Fires when breadth ≥ 70% AND throttle elapsed
- Closes all post-grace positions in losing direction
- Feature-flagged per-strategy (only enable for s523c initially)
- Consider blast radius: this is a NEW exit handler, requires Mission G's full /dev workflow

### Caveats remaining
1. 24-month sample is the limit per user constraint. Cannot validate beyond.
2. Realized equity curve approximation (not full MTM). Intra-trade DDs may differ from MTM curves.
3. Cull-during-stress execution slippage may exceed 50bps in extreme regimes (need conservative buffer).
4. Live paper monitoring before any live capital deployment.

**Reports:** `research/mission_p_gate0.py`, `research/mission_p_gate1.py`, `research/mission_p2_leverage.py`, `research/mission_p2_equity.py`, `research/mission_p_gate0_report.md`, `research/mission_p2_equity_results.json`

### s523d backtest tool (Gate 3 prototype, no engine changes) — 2026-04-08
**Tool:** `tools/s523d_run_with_cull.py` — runs s523c baseline backtest then applies Mission P breadth cull post-hoc, outputs in standard `results/v4/` format.

**Validated parameters (default):** grace=3d, breadth=80%, throttle=14d, cost=50bps

**Backtest results (50bps slippage):**
| Window | Metric | s523c baseline | s523d | Δ |
|---|---|---:|---:|---:|
| 12mo | Return | +747% | **+877%** | +130pp |
| 12mo | MaxDD | -37.3% | **-26.4%** | +10.9pp |
| 12mo | Sharpe | 2.88 | **3.03** | +0.15 |
| 12mo | **Calmar** | 20.05 | **33.29** | **+66.1%** |
| 12mo | Cull events | — | 11 | ~1/month |
| 24mo | Return | +517% | +598% | +81pp |
| 24mo | MaxDD | -56.9% | -50.8% | +6.1pp |
| 24mo | Sharpe | 1.54 | 1.74 | +0.20 |
| 24mo | Calmar | 9.08 | 11.77 | **+29.6%** |
| 24mo | Cull events | — | 25 | ~1/month |

**Note on baseline numbers:** the tool uses a REALIZED equity curve (running sum of trade pnls), not a true MTM curve. Baseline Calmar 20.05 in this tool vs 19.44 in `v4/portfolio_backtest.py` — slight difference due to intra-trade DD smoothing. The s523c↔s523d delta is methodologically consistent.

**Output files:**
- `results/v4/s523d_growth_12mo_50k_{trades,equity_curve,metrics,cull_meta}.json`
- `results/v4/s523d_growth_24mo_50k_{trades,equity_curve,metrics,cull_meta}.json`

### Path forward for live deployment (Gate 3 → Gate 4)
**Option A — Sidecar process (no engine change, lower blast radius):**
- Daily cron reads runner's state.json
- Computes breadth from open positions
- When rule fires, uses position management CLI to close losing-direction positions
- Aligns with the parked `position-management-cli` brief
- Estimated effort: 1-2 days work, no AIPIP needed

**Option B — Engine integration (cleaner long-term):**
- New exit handler with portfolio-state access
- Modifies `v4/simulator.py::_process_exits` to add a pre-pass
- HIGH blast radius — requires AIPIP per protected-files rule
- Estimated effort: 1 week work + AIPIP cycle

**Recommendation:** Option A first for paper trading validation (~30 days), then Option B if results confirm before live capital deployment.

### Shadow sidecar shipped 2026-04-08 — `tools/s523c_breadth_cull_monitor.py`
**What it does:** reads the live `state/v4_paper_s523c/state.json`, computes per-position MTM using `last_known_prices`, checks grace period (3 days), aggregates breadth per direction, evaluates the rule, logs decision to `state/v4_paper_s523c/breadth_cull_shadow_log.jsonl`. **Shadow only — does NOT execute closes.**

**First run (2026-04-08 09:00 UTC) on actual live state:**
- 12 open positions, all 2.3 days old or less (none past 3-day grace yet)
- LONG basket: 5/5 winning (0% breadth UW)
- SHORT basket: 6/7 losing (86% breadth UW) — **already meets the trigger condition**
- Rule correctly skips: no post-grace positions yet
- **Expected: rule will FIRE on SHORT basket within ~17h** when positions cross the 3-day grace period (~2026-04-09 01:00 UTC). This is a natural live validation moment.

**To enable execution (Gate 4):** Implement Phase 1 of `.specs/active/position-management-cli/brief.md` (just `--positions` and `--close TOKEN`, which doesn't conflict with engine per the brief). Then sidecar `--execute` mode calls `--close` for each action token.

**Recommended cron cadence:** hourly, throttle (14d) prevents repeated firing. Can co-locate with `tools/run_daily_metrics_loop.sh` or run as separate process.

**Original Gate 0 detail below ↓**

## Mission P — Event-Free Breadth-Based Direction Cull (refined Mission O) [Gate 0 detail]
**Status:** Gate 0 PASS 2026-04-08 (preserved for record)
**Hypothesis (user-refined from O):** evaluate at moments when the vast majority of one direction's open positions are individually losing — not based on basket mean (Mission O's failure). Don't evaluate daily. Use a throttle so we don't re-fire on the same regime episode.
**Best variant:** `g3d_br80_th14d` — grace 3d, breadth threshold 80% of direction underwater, 14-day throttle
  - **Total improvement: +$71,643 (+19.2%)** vs s523c clean 12-month baseline
  - Helped: 63 trades, Hurt: ~25 trades (~2.5x ratio)
  - Fires only ~7 times in the entire 365-day backtest
  - Saved/given-back ratio: ~1.7x
**Slippage robustness (validated):**
  - 5 bps: +10.6% (using br=75% — older variant)
  - 8 bps: +10.5%
  - 50 bps: +9.8% (only -0.7pp degradation)
  - 100 bps: +9.0%
  - Edge survives because rule fires rarely; slippage cost small vs saved P&L
**Parameter robustness (validated):**
  - There's a COHERENT EDGE ISLAND in (breadth 75-80%, throttle 10-21d): every combination in this region gives +3% to +19%
  - Outside the island (breadth ≤ 70% or ≥ 85%, throttle ≤ 7d): ALL negative
  - The sharpness of the boundary indicates real signal, not single overfit point
  - Breadth must be high enough to be specific (75-80%) but not so high that the rule arrives too late (85-90% = too late)
**Critical finding: NO event filter needed.** Adding vol/dispersion/move event triggers (the `any` variant) actively HURTS the result (bottom 5). Breadth + throttle alone does all the regime work. Simpler and better.
**Why this works where O/N/M/G failed:**
  - vs Mission O: O used basket mean (sensitive to outliers, fired 277/365 days). P uses breadth (count of underwater positions, robust). P fires ~7-12 days/year.
  - vs Mission N: N targeted individual underwater trades by time. P targets ENTIRE direction baskets only when overwhelmingly underwater. The breadth requirement is what filters out "loser that's about to bounce" — when 80% of the basket is underwater simultaneously, the regime is decisive.
  - vs Mission M: M tested per-trade signal. P tests aggregate position state (which incorporates all signals implicitly).
  - vs Mission G: G cut WINNERS. P cuts a LOSING basket, never touches winners. Bucket C from Mission M (winners are sacred) is preserved.
**Caveats — MUST address before any engine code:**
  1. Overfit risk: ~120 variants tested on single 12-month sample. Multiple-comparisons problem.
  2. No multi-year validation. Gate 2 must be: rolling 6mo windows over 5y, walk-forward parameters, median-positive verdict (not mean).
  3. Live execution complexity: simultaneously culling ~30 positions in a stress regime has additional slippage/liquidity risk.
  4. The 5y rolling test is critical — Mission G/I both passed 1y diagnostics and died on multi-year. P must clear that bar first.
**Gate 1 plan (next session):**
  1. Multi-year validation: rebuild snapshots over 5y backtest (using gate2_out trade log carefully — beware thin-universe early years), test best variant on 6-month rolling windows, require median improvement > +5% with stdev < 2× mean
  2. Walk-forward parameter selection: use first 50% to pick (breadth, throttle), test on second 50%
  3. Wider slippage stress test: 100bps, 150bps
  4. Sensitivity to grace period (test 2d, 4d, 6d in addition to 3d, 5d)
  5. ONLY if all 4 pass → Gate 3 (engine code: new exit handler that hooks into the post-grace evaluation)
**Reports:** `research/mission_p_gate0.py`, `research/mission_p_gate0_report.md`, `research/mission_p_gate0_results.json`

## Mission O — Portfolio-Level Direction Cull
**Status:** KILLED 2026-04-08 at Gate 0
**Hypothesis (user-proposed, more sophisticated than M/N):** after grace period, look at aggregate current P&L of all open LONGS vs all open SHORTS. If one direction's basket is decisively winning while the other is decisively losing, the regime favors one direction. Close all post-grace positions in the LOSING direction as a single action. This is portfolio-level, not per-trade.
**Verdict:** Catastrophically negative across ALL combinations. Best variant (grace 3d, threshold 18%): improvement -$245K (-65.8%), saved/given-back ratio 0.61x.
**Sweep matrix tested:** grace ∈ {2d, 3d, 5d} × threshold ∈ {3%, 5%, 8%, 12%, 18%}. **Every single combo net negative**, range -65.8% to -95.3%.
**Why it failed (the genuinely informative finding):**
  - 277 trigger days out of 365 (76% of days fire). Long-basket vs short-basket spread is daily NOISE, not regime structure
  - At any "long-bleeding" moment, ~50% of long positions are in tokens that will bounce — closing the basket cuts those bouncers along with genuine losers
  - Direction is too coarse a filter for a 30-position cross-sectional strategy. s523c's edge IS the token-level dispersion; aggregate filters destroy that
  - Helped/hurt ratio at best variant: 204/189 (essentially coin flip)
**THE FOURTH INDEPENDENT KILL** of s523c exit modifications. The unifying explanation across G/M/N/O is the Mission M Bucket C finding: once a token-direction position is in motion, macro/aggregate signals do NOT override the token-level signal. s523c's existing exit chain is well-calibrated to the strategy's actual behavior at the token level.
**Reports:** `research/mission_o_gate0.py`, `research/mission_o_gate0_report.md`, `research/mission_o_gate0_results.json`

## Mission N — Adaptive Stop Tightening
**Status:** KILLED 2026-04-08 at Gate 0
**Verdict:** Catastrophically net-negative at all tested offsets (+3d/+5d/+7d/+14d)
**Numbers (best/worst):**
  - +3d: cuts underwater trades → saves $199K of losers but gives back $390K of eventual winners → net **-$190K (-44% of baseline)**
  - +5d: -$112K (-25%)
  - +7d: -$105K (-21%)
  - +14d: +$9K (+2%, essentially zero — by then existing stops have done the work)
  - At 50bps slippage all variants degrade further by ~$10K-$20K
**Why it failed:** Underwater ≠ losing for s523c. Many trades that look underwater at +3d/+5d/+7d eventually recover into big winners (mean recovered pnl $3,800-$5,000). Cutting them early destroys the right tail. Only at +14d do most recoverable trades have time to recover, and by then the existing 5×ATR stop has cut the deepest losers — leaving a small pool with no real edge.
**THE THIRD INDEPENDENT KILL** of s523c exit modifications (after Mission G and Mission M). Pattern is now conclusive: s523c trades take time to develop in BOTH directions. Early action — targeting winners, signal-degraders, or apparent losers — destroys more value than it saves.
**Recommendation: STOP trying to improve s523c via exit overlays. Three independent kills with full diagnostics is enough evidence.** Path to portfolio improvement: better entries (Mission D), uncorrelated strategies (Mission J), or position sizing — NOT exits.
**Reports:** `research/mission_n_gate0.py`, `research/mission_n_gate0_report.md`, `research/mission_n_gate0_results.json`
**Hypothesis:** Bucket B from Mission M (s523c trades where signal stayed strong but trade went underwater) is the strategy's biggest leak. Existing static stops (5×ATR by default) don't cut these losers fast enough. A time-conditional stop tightening rule — "if a trade is still underwater after N days, tighten the stop from 5×ATR to 2×ATR" — might convert Bucket B's negative mean pnl (-0.097 to -0.187) into smaller losses without touching winners (Buckets A and C).
**Distinction from Mission G:** Mission G locked in winners early (truncated right tail). Mission N tightens losers' stops only after they've FAILED to recover. Asymmetric, defensive, and uses time-since-entry as the gate (not velocity, not signal degradation).
**Gate 0 plan:**
  1. Use the same enriched parquet from Mission M (`research/mission_m_gate0_enriched.parquet`)
  2. For each trade, classify as "still underwater at +3d/+5d/+7d" — these are the Bucket B candidates
  3. Compute: among these candidates, what % would be saved by exiting at +3d / +5d / +7d vs the actual exit? What % gives back additional gains (false positives)?
  4. If the saved-vs-given-back ratio is > 2x, advance to Gate 1 with rule sweeps
  5. Otherwise KILL — losers are losers, no time-based rule will fix them
**Apply ALL session meta-lessons:** clean 12-month backtest only, multi-year rolling windows before engine code, median over mean, no recompute shortcuts.

## Mission J — Cascade Recovery via Microstructure Signals
**Status:** Gate 1 NEEDS_TUNING 2026-04-08 — genuine alpha, fixable MaxDD
**Gate 1 results (pure time-48h exit on BTC + ETH/SOL basket, 1y):**
| Trigger | Events | Return | Sharpe | MaxDD | Calmar | alpha_t | Checks |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.75 | 279 | +248.6% | +1.94 | -82.3% | 3.34 | **+3.54** | 4/5 |
| 0.80 | 153 | +162.9% | +1.96 | -44.5% | 4.00 | +2.37 | 4/5 |
| 0.85 | 64 | +82.3% | +1.87 | -17.9% | 5.11 | +1.08 | 4/5 |

**Key finding:** The alpha is REAL and STRONG (t-stat +3.54 at trigger 0.75). The only failing check across all triggers is either MaxDD (clustering drawdown at lower triggers) or alpha_t (too few events at higher triggers).

**What the rule does:**
1. Computes CPI composite from OFI-M + VDV + DtM on BTC microstructure (1min)
2. Detects cascade events: CPI sustained >= threshold for 5+ min
3. Entry: first minute after cascade peak when BTC vdv_5min crosses from negative back through zero (bounce signal)
4. Long ETH+SOL basket equal-weight at entry hour close
5. Exit: PURE 48-hour time stop (no vdv/CPI-based exit — those created tautologies)
6. Realistic costs: 4bps fee/side + 5bps exit slippage, no leverage

**What killed my first exit rule attempts:**
- vdv_30min return-to-zero: tautology with vdv_5min entry → median hold 1h → costs dominate → -13 bps/event
- 4h min hold + vdv_4h > 0: tautology with 4h lookback → median hold 4h → still too short
- CPI < -0.3 exit: CPI includes -vdv, so CPI < -0.3 = squeeze-up = BULLISH for longs → closing winners at best moment
- **The only working exit is pure time-based (48h)** — let the bounce play out

**What killed my concurrency cap attempts:**
- concurrency_cap=1 skips 70%+ of events and destroys alpha (0.75: +3.54 → +1.07)
- Clustering IS the alpha — can't skip it
- The correct fix is POSITION SIZING across overlapping events, not event skipping

**Gate 2 plan:**
1. Wait for Mission F Phase 2 (top-10 alts fetch) — 12-symbol basket naturally reduces per-event concentration
2. Implement POSITION-SIZED concurrency (cap aggregate exposure at 1.0, split across overlapping trades)
3. Bootstrap Monte Carlo (like Mission P) to get honest expected alpha
4. Multi-year validation (24 months) with walk-forward H1→H2
5. Verdict thresholds: Sharpe > 1.5 AND Calmar > 3 AND MaxDD > -25% AND alpha_t > 1.5 on H2

**Session meta-lessons applied during Gate 1:**
- Beware exit rules that mechanically tie to entry signal lookback (tautology)
- Concurrency cap is a bad way to handle clustering drawdown (position sizing is the right way)
- Pure time-based exits work well when the signal is event-driven
- Verify the sign of composite signals before using them as exits (CPI < -0.3 being BULLISH for longs was counterintuitive)

**Files:**
- `research/mission_j_gate1.py` — original pipeline (agent-written)
- `research/mission_j_gate1_fix.py` — this session's fix run
- `research/mission_j_gate1_fix_results.json` — trigger sweep results
- `research/mission_j_cpi_btc.parquet` — BTC + CPI + VDV z-scores (reusable)
- `research/mission_j_trades.csv` — trade log from original agent run
- `research/mission_j_gate1_report.md` — agent's original Gate 1 report

---

## Decision priority

1. **Mission A** runs Gate 0 + Gate 1 NOW (data exists, fastest verdict)
2. **Mission F** scoping in parallel — needs user input on source/universe/resolution before any data fetching starts
3. **B/C** queued contingent on A outcome
4. **D** queued contingent on JSX review
5. **E** unblocked by F

## Coordinator notes
- Don't run all 6 in parallel — that wastes context and produces shallow results
- Kill A fast if Gate 1 fails; rotate to B
- Mission F is the long-pole infrastructure bet — get user sign-off on scope before kicking off any data download (storage/cost matters)
