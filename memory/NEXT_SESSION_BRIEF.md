# Next Session Brief — Mission J Gate 2 + Mission P Monitoring

**Session this brief was updated:** 2026-04-08
**Previous version:** 2026-04-07 (pre-Mission-P-deploy, pre-Mission-J-alpha-discovery)

## CRITICAL — what's live right now

- **Paper runner PID 323846** — live with new code (Position Management CLI Phase 1 + command queue hook)
- **Breadth cull sidecar PID 332240** — **EXECUTE MODE ACTIVE**, daily 01:30 UTC, will fire tomorrow on short basket if breadth holds
- **Backups at** `state/backups/breadth_cull_pre_execute/` (persistent) and `state/backups/pre_flip_insurance/` (pre-flip snapshot)
- **Mission F Phase 2** may or may not have completed overnight — check `data/perp/binance/{BNBUSDT,XRPUSDT,...,LTCUSDT}/microstructure_1min.parquet` existence
- **Mission D Gate 0** may or may not have completed — check `research/mission_d_gate0_report.md`

## First actions next session

1. **Check Mission P's first real fire** — did it happen? What did it close?
   ```bash
   tail -50 /tmp/breadth_cull_monitor.log 2>/dev/null || echo "tmp cleared on restart"
   tail -5 state/v4_paper_s523c/breadth_cull_shadow_log.jsonl
   tail -10 state/v4_paper_s523c/commands_processed.jsonl
   ls state/backups/breadth_cull_pre_execute/  # pre-fire snapshot
   /workspace/venv/bin/python -m v4.run_paper_multi --config configs/runner_pool_config.json --positions
   ```
   **Important:** if backup timestamps show a fire, compare pre-fire state vs current state to see exactly what closed.

2. **Check Mission F Phase 2 completion** — `ls data/perp/binance/{BNB,XRP,DOGE,ADA,AVAX,LINK,DOT,NEAR,ATOM,LTC}USDT/microstructure_1min.parquet`

3. **Check Mission D Gate 0 results** — `cat research/mission_d_gate0_report.md`

## Top-level state

- **Paper trading LIVE:** s513 ($150K pool) + s523c ($150K pool). Check `.claude/.strategy-mission` for full config and runner PID.
- **s523c is in its largest-ever drawdown** by absolute dollars: Nov 16 2025 → Jan 17 2026 peak-to-trough, -$151K / -38.9% over 62 days. Partly recovered by session end but this pain is reflected in your live paper account.
- **Engine code untouched this session.** Only tools/, research/, data/, and memory/ modified.
- **Mode state:** `.process-mode=strategy`, `.strategy-gate=gate0`

## What's new on disk (use this!)

### Orderbook data (Mission F)
```
data/perp/binance/{BTCUSDT,ETHUSDT,SOLUSDT}/bookdepth/{YYYY-MM-DD}.parquet    # 10-level depth curves, ~33s snapshots
data/perp/binance/{BTCUSDT,ETHUSDT,SOLUSDT}/trades_1s/{YYYY-MM-DD}.parquet    # 1s bars with buy/sell vol, notional, vwap
```
- 365 days each, 2025-04-10 → 2026-04-05
- 3.06 GB total
- Tools: `tools/fetch_binance_vision_bookdepth.py`, `tools/fetch_binance_vision_aggtrades.py` (for incremental updates or scaling to top 30 perps)
- README: `tools/README_binance_vision_orderbook.md`
- Binance Vision publishes 10 depth bands (±1/2/3/4/5%), cumulative notional — NOT 12. Don't expect ±0.2% level.

### Microstructure signals (Mission H Phase 1+2)
```
data/perp/binance/{BTCUSDT,ETHUSDT,SOLUSDT}/microstructure_1min.parquet    # 32 columns, 519,840 rows each
```
- Tool: `tools/build_microstructure_signals.py --phase 1|2|all`
- Signals: DtM (depth-to-move z-scores + asymmetry), OFI-T (taker flow 10s/1min/5min/30min/4h), OFI-M (maker flow with price-move adjustment), VDV (VWAP dislocation velocity 5min/30min/4h)
- Full spec: `research/mission_a_orderbook_signals.md`
- Sanity-checked lead/lag against 21 BTC cascade events — **OFI-M leads by ~60 min** (z=-0.43 at T-1h → +0.11 at T=0), **VDV_5min leads (-0.74) and flips to bounce signal (+1.92) at T+1h**
- Per-event |z| is modest (~0.4) — signals need ensembling or multi-symbol confirmation, not single-bar thresholding

### Diagnostic cache
```
research/s523c_universe_basket.parquet    # daily basket regime features, 89 tokens, 2020-01 → 2026-04
```
Columns: `basket_ret_24h, basket_eq, basket_vol_30d, basket_dispersion_24h, basket_drawdown_30d, basket_ret_30d, pct_above_50dma, btc_ret_24h, btc_ret_30d, btc_drawdown_30d, btc_above_200dma, alts_vs_btc_30d, n_tokens_active`

### Research reports (context for next agents)
- `research/mission_a_orderbook_signals.md` — full quant spec for DtM / OFI-T / OFI-M / VDV / LCP / CPI
- `research/s523c_drawdown_anatomy_report.md` — drawdown characterization
- `research/profit_lockin_final_validation_report.md` — full Mission G post-mortem
- `research/mission_h_sanity_phase2.py` — lead/lag cascade event analyzer (reusable template)

## Priority 1 — Mission J: Cascade Recovery strategy via Mission H signals

**This is the highest-value next mission.** Replaces the original Mission A (which was data-bound by Coinalyze). Uses infrastructure we already shipped.

### Hypothesis
Use OFI-M + VDV to detect cascades in real-time from orderbook flow (not from lagging hourly return triggers), then enter long alt basket on VDV mean-reversion crossover, exit on VDV return to zero. Avoids the Coinalyze free-tier 120-day history wall entirely.

### Gate 0 — Is there enough signal?
Already answered ✅. Sanity check in `research/mission_h_sanity.py` (Phase 1) and `research/mission_h_sanity_phase2.py` (Phase 2) showed OFI-M leads by ~60 min with z=-0.43, VDV leads and flips at T+1h.

### Gate 1 — Build and test the strategy
**Work for first agent of next session:**

1. Load BTCUSDT microstructure_1min parquet
2. Define a cascade event via composite signal (not the old BTC -2% trigger):
   - `cpi = 0.4*(-ofi_m_total_5min/std_all) + 0.4*(-vdv_5min/std_all) + 0.2*(-dtm_bid_5pct_z168h)` normalized
   - Cascade trigger: CPI crosses above +0.7 threshold AND stays there for >5 minutes
3. Define entry point: after cascade trigger, wait for VDV_5min to cross from negative back through zero (bounce signature)
4. Enter equal-weight long across top-10 liquid alts (for bootstrap, use the Mission F universe = BTC/ETH/SOL → use ETH + SOL as the "alts"; for a real test we need to extend Mission F to top-10 perps first)
5. Exit: VDV_1h returns to zero, OR 48h time stop, OR CPI drops below -0.3
6. Compute event-level P&L with realistic costs (4bps fee, 5bps slippage, no funding under 72h hold)
7. Gate 1 verdict thresholds: L12M Sharpe > 2.0, Calmar > 3.0, MaxDD > -25%, event count > 20, alpha vs BTC beta t-stat > 1.5

### Gate 1.5 — Scale Mission F if needed
If BTC/ETH/SOL isn't a wide enough alt basket for Gate 1 validation, the first sub-task is to extend Mission F to **top 30 perps × 365 days**. Estimate ~5.5 GB bookdepth + ~22 GB trades_1s. Use existing fetcher tools with wider --symbols arg. Budget: ~2h agent runtime.

### Known risks / traps
- **Per-event |z|~0.4 may be too weak.** If a single-token composite doesn't fire enough, try multi-symbol confirmation (ETH + SOL + BTC all confirming) before entry.
- **Cascade frequency on just BTC.** 21 events in 1y. Top-10 alt basket will have more events per year.
- **Execution latency** — if trigger requires 1-min signal and entry requires VDV crossover, real-world execution lag of 5-30s on bounce entry may eat the edge. Must simulate with realistic fill delay.

## Priority 2 — Mission K: Mission H Phase 3 (LCP — Liquidation Cluster Proxy)

Speculative, highest "if it works" payoff. Adds a sizing input to Mission J (how big will the cascade be).

### Inputs
- OI time series: `data/alternative/binance_metrics/5min/{SYMBOL}_5min.parquet` (227 files, 2020-09 to 2026-04 for BTC)
- VWAP during accumulation: `data/perp/binance/{SYMBOL}/trades_1s/*.parquet`
- Leverage distribution estimate: 5× / 10× / 25× tranches weighted {0.40, 0.40, 0.20}

### Algorithm
Full spec in `research/mission_a_orderbook_signals.md` section "Signal 5".

### Validation
Align LCP cluster zones with the 21 BTC cascade events from the Mission H sanity check. If LCP clusters coincide with cascade magnitude, the signal is real. Otherwise KILL.

## Priority 3 — Mission D: Novel indicators from JSX (STILL UNREAD)

Path: `/workspace/novel-indicators-contrarian-math.jsx`

User wrote this as a framework of 8 math-from-physics indicators (LII, FIF, HV, WRD, SRI, TCB, TMP, ROFD). I never read the file this session. First task of Mission D is to read the JSX, classify each indicator by computability against the data we have:
- Group A: computable from OHLCV + funding + OI (test IC directly)
- Group B: needs bookdepth (use Mission F data)
- Group C: needs true tick-level or L2 (skip or defer)

Then IC test each computable indicator on BTC 1h returns and promote to Gate 1 backtest only the ones that pass IC |ρ| > 0.05 with t > 2.

## Priority 1 — Mission J Gate 2: Cascade recovery strategy (largest new-alpha opportunity)

**Mission J Gate 1 PASSED 4/5 checks on 2026-04-08 with real alpha (t-stat +3.54).** The single blocker is MaxDD from event clustering on the 2-symbol basket (ETH+SOL). Mission F Phase 2 is fetching 10 more alts to fix exactly this.

### Where Gate 1 landed
| Trigger | Events | Return | Sharpe | MaxDD | Calmar | alpha_t | Gate1 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.75 | 279 | +248.6% | +1.94 | -82.3% | 3.34 | **+3.54** | 4/5 |
| 0.80 | 153 | +162.9% | +1.96 | -44.5% | 4.00 | +2.37 | 4/5 |
| 0.85 | 64 | +82.3% | +1.87 | -17.9% | 5.11 | +1.08 | 4/5 |

### What worked (use as-is in Gate 2)
- CPI composite from OFI-M + VDV + DtM on BTC 1-minute microstructure
- Cascade event detection: CPI > trigger sustained ≥5 min, merged if <60 min gap
- Entry: first minute after peak when BTC vdv_5min crosses from negative back through zero
- **Pure time-48h exit** (any other exit creates a tautology)
- Cost model: 4 bps fee/side + 5 bps exit slippage, no leverage, equal-weight long basket

### What DOESN'T work (don't retry)
- ❌ vdv_30min or vdv_4h return-to-zero exit — tautology with entry lookback
- ❌ CPI < -0.3 exit — CPI includes -vdv, so this is BULLISH for longs (kills winners)
- ❌ Concurrency cap = 1 — skips 70% of events, destroys alpha

### Gate 2 plan

**Prerequisite check:** Mission F Phase 2 must have completed. Verify:
```bash
ls data/perp/binance/{BNB,XRP,DOGE,ADA,AVAX,LINK,DOT,NEAR,ATOM,LTC}USDT/microstructure_1min.parquet
```
If any missing, the fetch either failed or is still running. Must complete before Gate 2.

**Gate 2 tasks:**
1. **Build 12-symbol basket** — add BNB/XRP/DOGE/ADA/AVAX/LINK/DOT/NEAR/ATOM/LTC to ETH+SOL. Equal-weight long at each cascade event.
2. **Position-sized concurrency** — when multiple cascade events overlap, split capital. If 3 events active simultaneously, each gets 1/3 of normal position size. Aggregate exposure stays bounded at 1.0.
3. **Multi-year validation** — extend to 24 months of microstructure data if possible (BTC orderbook only goes back 365d currently — may need wider fetch)
4. **Bootstrap Monte Carlo** — 100-300 trial bootstrap like Mission P Gate 2, report honest expected alpha vs BTC beta
5. **Trigger sweep** — compare 0.70, 0.75, 0.80, 0.85 on the new basket
6. **Verdict thresholds:**
   - Sharpe > 1.5 AND
   - Calmar > 3 AND
   - **MaxDD > -30%** (relaxed from -25% because even 12-symbol basket may not fully eliminate clustering DD)
   - alpha_t > 1.5 (bootstrap mean, not single-window)

### Why this is priority 1
- **Bigger potential upside than Mission P** — new strategy class, not an overlay
- Alpha is strong (t=+3.54) and measured vs BTC beta, not disguised leverage
- Uncorrelated with s513 and s523c (event-driven on BTC microstructure, not alt ranking)
- If Gate 2 passes → Tier A candidate, worth dedicating a pool to in paper trading

### Scripts to reuse
- `research/mission_j_gate1.py` — original pipeline
- `research/mission_j_gate1_fix.py` — session's fix run with concurrency cap support (set to None for no cap)
- `research/mission_j_cpi_btc.parquet` — pre-computed BTC + CPI + VDV z-scores

### Files to produce in Gate 2
- `research/mission_j_gate2.py` — position-sized concurrency harness
- `research/mission_j_gate2_report.md` — verdict + metrics
- `research/mission_j_gate2_bootstrap.json` — 100-trial MC results

---

## Priority 2 — Mission P Live Monitoring (passive)

Mission P is deployed in execute mode. No active research needed — just monitoring.

**Observe:**
- Did the first real fire happen at 2026-04-09 01:30 UTC?
- Which tokens closed? Does the close price look reasonable vs what you'd expect?
- After 14-day throttle, did the next fire look sensible?
- Track live vs bootstrap prediction over 30 days

**Rollback procedure** (if something goes wrong):
```bash
# Stop the loop
kill $(cat /tmp/breadth_cull_loop.pid 2>/dev/null) || kill $(pgrep -f run_breadth_cull_loop)

# Stop the runner
kill $(pgrep -f 'run_paper_multi.*runner_pool_config')

# Restore state from latest pre-execute backup
BACKUP=$(ls -td state/backups/breadth_cull_pre_execute/v4_paper_s523c_* | head -1)
cp $BACKUP/state.json state/v4_paper_s523c/state.json
cp $BACKUP/trades.jsonl state/v4_paper_s523c/trades.jsonl
cp $BACKUP/equity.csv state/v4_paper_s523c/equity.csv

# Restart runner
nohup /workspace/venv/bin/python -u -m v4.run_paper_multi --config configs/runner_pool_config.json > /tmp/runner.log 2>&1 &
```

---

## Priority 3 — Mission D Gate 1: FIF × drift composite backtest

**Mission D Gate 0 completed 2026-04-08.** One signal survived cross-token consistency filter: **FIF × drift composite**. See `research/mission_d_gate0_report.md` for full details.

### What the signal is
Fisher Information Flow (meta-indicator for drift estimability) multiplied by the drift estimate. When Fisher info is high AND drift positive → "confident upward direction". When Fisher info low OR drift small → "uninformative market, stand down".

### Gate 0 results
- Cross-token **positive** sign on BTC/ETH/SOL at 24h/72h/168h
- IC 0.03-0.05 (modest but robust — most novel indicator signals fail this step)
- Spearman ICs 3-5× weaker than Pearson → outlier-driven, not monotonic (yellow flag)

### Gate 1 plan
**Three candidate use cases, ordered by expected value:**

**Option A — Regime gate on Mission J cascade entries** (highest synergy):
- Only fire Mission J bounce entries when FIF × drift on the target basket is positive
- Hypothesis: FIF × drift confirms the bounce direction has measurable drift
- If this works, Mission J gets cleaner signal with fewer false positives
- Test in the same harness as Mission J Gate 2

**Option B — Position sizing multiplier on s513**:
- s513 is a momentum strategy with default 3× leverage, 15 positions
- Multiply each position's size by `max(0, FIF × drift_z_score)` normalized
- Hypothesis: reduces size when market is uninformative → lower DD without cutting much return
- Test as post-hoc overlay on s513 trade log (same methodology as Mission P backtest tool)

**Option C — Standalone small strategy**:
- Long when FIF × drift z-score > 2, short when < -2
- 72-168h hold, 5-10% portfolio allocation
- Last resort — IC is too small for standalone as primary driver

### Critical pre-Gate-1 step: bootstrap MC on the IC
The yellow flag is that Spearman IC << Pearson IC → outlier-driven. **Before committing capital via any Gate 1 backtest, run 100-trial bootstrap on the IC itself.** If bootstrap shows 60%+ positive trials, promote to Gate 1. If <50%, kill.

### Tier 2/3 deferred (only if FIF × drift passes Gate 1)
- TCB (topological) — needs `pip install ripser persim`
- LII (Lyapunov) — slow O(N²) but computable
- TMP (thermodynamic) — needs orderbook band-level adaptation
- ROFD (Rényi) — needs trade time-bin adaptation
- Synergies: LII×FIF×HV (predictability filter), SRI×HV×WRD (rotation anticipator), TCB+TMP (topology-thermodynamics gateway)

---

## [legacy] Priority 1.5 — Mission P Gate 3 (SUPERSEDED — already shipped)

**Mission P passed Gate 0 + Gate 1 at end of 2026-04-07/08 session.** First and only s523c exit modification to survive both gates. This is now a `/dev` workflow item, not research.

### Validated parameters (don't tune further)
- Grace period: 3 days post-entry (matches s523c's `no_stop_bars`)
- Trigger: ≥70% of one direction's post-grace open positions individually underwater
- Action: close 100% of that direction's post-grace positions
- Throttle: 14 days between culls per direction

### Validated performance on H2 (recent year)
- Return: +488% → +579% (+91pp)
- MaxDD: -45.3% → -32.2% (13pp reduction)
- Sharpe: 1.72 → 1.99
- Calmar: 10.77 → 17.95 (+66.6%)
- Survives 50bps slippage (0.5pp degradation)
- Walk-forward validated H1 → H2

### Reports to read first
- `memory/STRATEGY_MISSION_BACKLOG.md` — Mission P entry has full Gate 1 detail
- `research/mission_p_gate0.py` — original sweep
- `research/mission_p_gate1.py` — 24-month validation harness with walk-forward
- `research/mission_p2_equity.py` — equity curve + MaxDD computation
- `research/mission_p2_equity_results.json` — final metrics

### Gate 3 implementation plan (use `/dev` workflow)

**Phase 1 — Specify** (Tier 2: 5-15 files, real engine work)
- Feature brief: BreadthDirectionCullHandler exit handler for s523c
- Acceptance criteria: rule fires when breadth ≥ 70% AND throttle elapsed; closes all post-grace positions in losing direction; does NOT touch positions still within grace period
- Edge cases: what if positions are exited mid-cull by other handlers? what if signal flickers across the threshold within the throttle window?

**Phase 2 — Design**
- Read `v4/exit_handlers.py` to understand the existing handler chain
- Trace where the new handler slots in: AFTER grace period check, BEFORE existing 5×ATR stop check
- Identify the data the handler needs: list of all open positions for the strategy with their current MTM and entry timestamps
- Check blast radius — `v4/exit_handlers.py` is in the HIGH caution list (5+ importers per blast-radius doc)
- Direction-aggregation logic: needs portfolio-wide visibility across all open positions, not just the current one being processed

**Phase 3 — Test (acceptance tests, frozen)**
- Test that rule fires correctly when 70%+ of longs are underwater after grace period
- Test that throttle prevents re-firing within 14 days
- Test that grace period excluded positions are NOT counted in breadth
- Test direction independence (long cull doesn't trigger short cull)
- Test that rule does NOT fire on insufficient sample (<5 positions per direction)

**Phase 4 — Implement**
- New handler module
- Integration into the chain
- Feature flag: only enable for s523c initially (other strategies untouched)
- Configuration: hardcode the validated parameters for v1; consider making them configurable for v2

**Phase 5 — Paper validation BEFORE live**
- Run alongside existing s523c pool without affecting it
- Use a separate equity tracking pool with the new handler enabled
- Compare side-by-side for ≥30 days of paper trading
- Only enable on the live pool after paper validation confirms the simulation behavior

### Critical constraints
- Mission P is the FIRST s523c improvement we've found. Don't rush.
- Do not pick up the leverage variants (P.2 Variants B/C) — they tested worse on Calmar despite higher return. The data is unambiguous.
- Don't add complexity beyond what was validated. The validated rule has 4 parameters: grace=3d, breadth=70%, action=close-100%, throttle=14d. Don't add event filters, don't add partial closes, don't add leverage adjustments.
- Bigger blast radius than originally planned. Confirm with user before touching `v4/exit_handlers.py`.

### What was killed and validated dead this session (don't revive)
- Mission B/G/I/M/N/O — see backlog. All exit-overlay attempts other than P.
- Mission P leverage variants A/B/C — tested in `research/mission_p2_*`, all worse than P baseline on Calmar.

## Priority 2 — Mission J: Cascade recovery via Mission H signals (still queued)
[unchanged, same as before — the new strategy from microstructure signals]

## OLD Priority 1.5 — Mission P Gate 1: Multi-year validation of breadth direction cull

**Mission P Gate 0 PASSED 2026-04-08** — first s523c exit modification to survive any diagnostic. User refined Mission O's hypothesis with two key changes: (1) use breadth (% of direction underwater) not mean spread, (2) hard throttle to prevent re-firing on the same regime episode. Result: +19.2% improvement on clean 12-month, robust to 50-100bps slippage, coherent edge island in (breadth 75-80%, throttle 10-21d).

### What's already done
- `research/mission_p_gate0.py` — full diagnostic with sweep
- Best variant: `grace_3d, breadth=80%, throttle=14d`
- 50bps stress test passed (+9.8%)
- Parameter robustness sweep shows coherent edge island, not single lucky combo
- NO event filter needed — breadth + throttle alone does the regime work

### Gate 1 plan (must pass before any engine code)
1. **Multi-year validation (the critical test):**
   - Use the 60-month s523c trade log from `research/gate2_out/s523c_growth_60mo_50k_*` BUT BEWARE the thin-universe early years that polluted Mission I
   - OR generate fresh 24-month and 36-month backtests if needed
   - Run rolling 6-month windows with the best variant
   - Walk-forward parameter selection: pick (breadth, throttle) on first half, test on second half
   - Verdict requires: **median improvement positive across windows, stdev < 2× mean, no window worse than -10%**
2. **Wider slippage stress:** test 100bps, 150bps, 200bps to see where it breaks
3. **Grace period sensitivity:** test grace 2d/4d/6d in addition to 3d/5d
4. **Live execution risk:** simulate the slippage degradation when culling 30 positions simultaneously in stress regimes (the 50bps assumption may be too optimistic for basket-level stress)

### Why this is priority 1.5 (not 1)
Mission J (cascade recovery via Mission H signals) is still priority 1 because it adds NEW alpha, while Mission P only improves existing s523c. But Mission P is now ahead of all the other s523c improvement queues because it actually has Gate 0 evidence.

### Critical: do NOT advance to Gate 3 without multi-year validation
Mission G also passed 1y diagnostic and died on 5y. Mission I had a +104% Calmar Gate 1 that was a baseline-degradation artifact. Mission P must clear the 5y bar before any engine work. No exceptions.

## Priority 4 — Position sizing / leverage exploration on s523c

**Mission N was killed at Gate 0 on 2026-04-08.** This is now the THIRD independent kill of s523c exit modifications (after G and M). Pattern is conclusive: **stop trying to improve s523c via exit overlays.** The strategy needs time to develop in both directions; early action destroys more than it saves regardless of trigger.

**Better avenue:** explore leverage / concentration / position-count tweaks. These are simple parameter sweeps, not engine changes:
- Current config: 30 max positions, 0.30 concentration, 2.6x leverage
- Sweep ranges: 20/25/30/35/40 positions × 0.20/0.25/0.30/0.35 concentration × 2.0/2.6/3.0/3.5 leverage
- Use clean 12-month backtest, test on multi-year rolling windows
- Pareto-frontier search for return vs MaxDD vs Sharpe vs Calmar

This is ~30 min of agent work using existing portfolio_backtest CLI. Probably the cheapest possible improvement attempt.

## STRONG META-CONSTRAINT for next session (record this)

**Three independent kills of s523c exit overlays prove that exit modifications are not the path.** Do NOT spawn another exit/stop/lock-in mission on s523c without explicit user override and a fundamentally new mechanism. The avenues that remain are:
1. **Better entries** — Mission D (novel indicators JSX, still unread) or new signal research
2. **Uncorrelated strategies** — Mission J (cascade recovery via Mission H signals) — adds new alpha to portfolio
3. **Position sizing** — leverage/concentration/count sweeps (Priority 4 above)
4. **Portfolio construction** — adding strategies with different correlation profiles

NOT:
- ❌ Velocity-based exits (Mission G — killed)
- ❌ Signal-degradation exits (Mission M — killed)
- ❌ Time-based stop tightening for losers (Mission N — killed)
- ❌ Portfolio-level direction cull on basket MEAN (Mission O — killed)
- ❌ Any other "react to in-trade behavior" overlay on s523c

EXCEPT:
- ✅ **Mission P (breadth-based direction cull) — Gate 0 PASSED**, advance to Gate 1 multi-year validation. This is the ONE refinement that survived a diagnostic. See Priority 1.5 above.

**The unifying explanation across G/M/N/O:** s523c's edge IS token-level dispersion. Any aggregate-level filter (per-trade signal, per-trade time, or per-direction basket using mean) destroys that dispersion because the tokens that LOOK bad in aggregate often include winners that haven't bounced yet. The Mission M Bucket C finding is the formal proof: signal-degraded winners had the HIGHEST win rate of any bucket.

**Why Mission P breaks the pattern:** it uses BREADTH (% of direction underwater), not mean. When 80% of one direction's positions are SIMULTANEOUSLY underwater AND we haven't culled in 14 days, the regime is unambiguous — even the would-be bouncers are bleeding. The breadth threshold acts as a confidence floor; the throttle prevents re-firing on the same episode. This sharpens the signal enough that it fires only ~7-12 times/year and the helped/hurt ratio is ~2.5x.

## Lower priority / parked

### Mission A (original framing) — parked
Revival requires: (a) Mission J succeeds OR (b) forward-capture `@forceOrder` WS for 18 months OR (c) Coinalyze paid tier. Mission J is the recommended path.

### Forward `@forceOrder` WS capture — cheap infrastructure bet
~80 lines of code, runs as nohup like daily metrics loop, builds forward liquidation history for free. 18-month payoff. If you want this running in the background, it's a 30-min task. Not urgent because Mission J doesn't need it.

## First-agent kickoff template for Mission J

```
Mission J Gate 1 — Cascade detection + bounce entry via microstructure signals.

Working dir: /workspace/crypto_backtest. Use /workspace/venv/bin/python.

READ FIRST:
- memory/NEXT_SESSION_BRIEF.md (this file)
- research/mission_a_orderbook_signals.md (signal spec)
- research/mission_h_sanity_phase2.py (cascade event detector template)

DATA:
- data/perp/binance/BTCUSDT/microstructure_1min.parquet (32 columns, Phase 1+2 signals)
- data/perp/binance/{ETHUSDT,SOLUSDT}/microstructure_1min.parquet (same)
- data/perp/binance/BTCUSDT/1h.parquet (OHLCV for P&L computation)

TASK:
1. Build CPI composite score from OFI-M + VDV + DtM (weights in spec)
2. Detect cascade events: CPI > 0.7 sustained > 5min
3. Entry: after cascade peaks, enter long when VDV_5min crosses from < 0 back to 0
4. Universe for bootstrap: ETH + SOL equal-weight long (only alts in our Mission F data)
5. Exit: VDV_1h back to 0, OR 48h stop, OR CPI < -0.3
6. Realistic costs: 4bps fee/side, 5bps slippage, no funding (<72h holds)
7. Report Gate 1 metrics: event count, Sharpe, Calmar, MaxDD, alpha vs BTC beta t-stat

OUTPUT:
- research/mission_j_gate1.py (strategy simulator)
- research/mission_j_gate1_report.md (verdict)

VERDICT THRESHOLDS:
PASS: event count > 15, Sharpe > 1.5 (relaxed from 2.0 due to 2-symbol universe), Calmar > 2.5, MaxDD > -25%, alpha t > 1.5
KILL: any of the above fails
SCALE: if PASS but universe is too narrow, recommend Mission F Phase 2 (top 30 perps)
```

## Tool cheatsheet

```bash
# Clean s523c 12-month backtest (source of truth for all Gate 1 checks on s523c)
cd /workspace/crypto_backtest && /workspace/venv/bin/python v4/portfolio_backtest.py \
  --strategy s523c_growth --months 12 --capital 50000 --market perp \
  --conviction-mode ranked --max-portfolio-positions 30 --concentration 0.30 \
  --skip-wf --end-date 2026-04-05T16:00:00

# Clean s513 12-month backtest
cd /workspace/crypto_backtest && /workspace/venv/bin/python v4/portfolio_backtest.py \
  --strategy s513_triple_trigger_swing --months 12 --capital 50000 --market perp \
  --conviction-mode ranked --max-portfolio-positions 15 --concentration 0.20 \
  --skip-wf --end-date 2026-04-05T16:00:00

# Re-fetch microstructure signals (incremental, idempotent)
/workspace/venv/bin/python tools/build_microstructure_signals.py --phase all \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT --start 2025-04-10 --end 2026-04-05

# Extend orderbook data to more symbols
/workspace/venv/bin/python tools/fetch_binance_vision_bookdepth.py \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT \
  --start 2025-04-10 --end 2026-04-05
```

## Mission backlog (full details)
See `memory/STRATEGY_MISSION_BACKLOG.md` for the complete mission history, including dead hypotheses to avoid retrying.
