# Knowledge & Memory Index

> **Read this first on every session start.** Use it to decide what to load — never load everything at once.

## How to Use

1. Read this index (lightweight — ~100 lines)
2. Based on your current task, load only the relevant files
3. For `/strategy`: start with RESEARCH_STATUS.md + STRATEGY_QUICK_REFERENCE.md
4. For `/dev`: start with PROJECT_STATUS.md + ARCHITECTURE.md + RESEARCHER_BEST_PRACTICES.md
5. Load deeper files on-demand when you hit a specific question

---

## Memory Files (session state — changes frequently)

| File | Size | Audience | Summary |
|------|------|----------|---------|
| `PROJECT_STATUS.md` | 82K | both | Strategy tiers, capability inventory, open tasks, paper pool status. **The master state file.** |
| `RESEARCH_STATUS.md` | 97K | /strategy | Signal scoreboard (54 tested), GOLD/PASS/KILLED verdicts, next actions. |
| `RESEARCH_COORDINATOR.md` | 9K | /strategy | Coordinator role definition, operating model, anti-patterns, session resume checklist. |
| `ML_DIRECTION_MODEL_STATUS.md` | 10K | both | ML direction models: ALL DEAD. 7 experiments, root cause analysis. Do not pursue. |
| `NEXT_SESSION_BRIEF.md` | 1K | /dev | WebSocket consolidation task brief. |
| `SUB_HOURLY_EXIT_E2E_TESTING.md` | 15K | /dev | Sub-hourly exit resolution results. Deployed: s56→5m, s98→30m. |
| `DASHBOARD_AND_RUNNER_DEPLOYMENT.md` | 5K | /dev | HTTP server, state.json polling, deployment paths. |
| `DATA_INVENTORY.md` | 12K | /strategy | Complete data source inventory: 45+ sources, 7.8 GB. Price, positioning, OI, liquidations, macro, on-chain, sentiment. Includes gap analysis for future data acquisition. |

---

## Knowledge Files (domain reference — changes rarely)

### Core (load for most tasks)

| File | Size | Audience | Summary |
|------|------|----------|---------|
| `STRATEGY_QUICK_REFERENCE.md` | 40K | /strategy | Gate thresholds, kill criteria, dedup tables, cost tables, bias checklist. **The strategy bible.** |
| `RESEARCHER_BEST_PRACTICES.md` | 31K | both | Complete parameter catalog (9 layers), 13 ground rules, sizing pipeline. |
| `QUANT_METHODOLOGY.md` | 12K | both | Statistical foundations: backtesting (WFE, PBO, CPCV), OOS data cap methodology, signal evaluation (IC, DSR with Bailey & LdP formula, FDR), sizing (Kelly, vol-target), fees (3-layer model), regime detection, crypto-specific risks. |
| `ARCHITECTURE.md` | 15K | /dev | Directory layout, strategy templates, V4 engine overview, exit sentinel. |

### Strategy Research

| File | Size | Audience | Summary |
|------|------|----------|---------|
| `STRATEGY_CATALOG.md` | 23K | /strategy | Master registry: 85+ strategies, tier metadata, correlation matrix, portfolio blends. |
| `INDICATOR_CATALOG.md` | 13K | /strategy | 37 signals ranked by IC (ADX top at 0.067), redundant pairs, regime performance. |
| `SIGNAL_DEVELOPMENT.md` | 19K | /strategy | IC methodology, walk-forward validation, common pitfalls. |
| `STRATEGY_LIFECYCLE.md` | 10K | /strategy | Gate 0-6 lifecycle phases, per-gate deliverables, decision criteria. |
| `SHORT_STRATEGY_RESEARCH.md` | 9K | /strategy | Short-selling strategy research and findings. |

### Engine & Implementation

| File | Size | Audience | Summary |
|------|------|----------|---------|
| `V4_ENGINE.md` | 14K | /dev | V4 portfolio engine: signal pipeline, hard data cap, true walk-forward, OOS monthly runner, simulator, paper trading, state restoration. |
| `V4_EXPERIMENTATION_GUIDE.md` | 50K | both | Exit handlers, sizing models, regime customization, 20+ experimentation recipes. Deep dive only. |
| `V4_SIZING_PIPELINE.md` | 8K | /dev | Sizing formula, ADV curve, 3-layer config, validation. |
| `PERFORMANCE_PATTERNS.md` | 13K | /dev | Vectorization rules (46,600x speedup), caching, anti-patterns. |

### Operations & Data

| File | Size | Audience | Summary |
|------|------|----------|---------|
| `PAPER_TRADING_PRO_FRAMEWORK.md` | 16K | /dev | Paper trading setup, slippage, dual-exchange mode, go/no-go checklist. |
| `KRAKEN_FEES.md` | 7K | both | Fee tiers, maker rebates, withdrawal costs, Kraken vs Binance comparison. |
| `DATA_PIPELINE.md` | 5K | /dev | Directory structure, RDB/HDB merge strategy, funding normalization, maintenance wiring. |
| `DATA_MANIFEST.md` | 2K | /dev | 199 perp + 135 spot + 193 1m tokens, coverage dates, delisted tokens, integrity notes. Updated 2026-03-30 post-backfill. |
| `DATA_ACQUISITION_PLAYBOOK.md` | 6K | /dev | Proxy config, rate limiting, exchange API quirks, Cloudflare bypass. |

### Meta

| File | Size | Audience | Summary |
|------|------|----------|---------|
| `KNOWLEDGE_BASE_GUIDELINES.md` | 3K | both | File size rules, structure patterns, what to store vs skip. |

---

## Deep Dive Files (knowledge/process/)

Only load these when investigating a specific topic. Referenced by Quick Reference.

| File | Topic |
|------|-------|
| `BACKTESTING_VALIDATION_BEST_PRACTICES.md` | Walk-forward, CPCV, PBO deep dive |
| `CRYPTO_MICROSTRUCTURE_POST_ETF.md` | Post-ETF market structure, funding dynamics |
| `RISK_PORTFOLIO_CONSTRUCTION.md` | Portfolio optimization, risk parity, correlation |
| `SIGNAL_DISCOVERY_METHODS.md` | IC computation, feature engineering, FDR |
| `STRATEGY_PIPELINE_GATES.md` | Gate process deep dive |
| `SCOPE_AND_CONTEXT.md` | Project scope and boundaries |

---

## Archive (memory/archive/, knowledge/archive/)

Superseded or historical files. Do not load unless specifically investigating past decisions.

---

## Known Gaps

- ~~**Funding rate integration:** Downloaded but NOT in 1h parquets.~~ **RESOLVED 2026-03-26.** Funding rates now merged into all 1h parquets as `funding_rate` column (normalized to hourly). 8x overcharge bug fixed, all 195 perp parquets rebuilt.
- **Regime tuning guide:** 5 regimes defined but no parameter tuning documentation.
- **Market impact at scale:** sqrt(impact) model not validated against real orders.
