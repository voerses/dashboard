# Session State — Crypto Swing Trading System
**Last updated**: 2026-02-28
**Purpose**: Complete state capture so we can resume without losing anything.

---

## 1. SYSTEM OVERVIEW

$200K crypto swing trading system. Hybrid architecture:
- **Research/Backtest**: Custom Numba/Polars engine (engine.py) — fast, unbiased, vectorized
- **Execution**: Freqtrade as thin shell — reads cpcv_params.json, trades on real exchanges
- **Validation**: CPCV + Walk-Forward dual gate — only deploys statistically robust tokens
- **Data**: 49 tokens, Binance Vision 1H/4H/Daily OHLCV, extended to 5 years (Jan 2021 - Feb 2026)

---

## 2. WHAT'S BUILT AND WORKING

### Core Engine (complete)
| File | Purpose | Status |
|------|---------|--------|
| `engine.py` | Plugin backtesting engine (StrategyContext, StrategyResult, Engine) | DONE |
| `cpcv.py` | Combinatorial Purged Cross-Validation | DONE |
| `walk_forward_fast.py` | Walk-forward optimization | DONE |
| `liquid_universe.py` | 49-token universe, tiers, position sizing | DONE |
| `regime_detector.py` | HMM + BOCPD + VPIN regime detection | DONE |
| `signal_lab.py` | 37-signal IC evaluation | DONE |
| `mtf_strategy_v2.py` | Core Numba JIT simulation engine (DO NOT MODIFY) | DONE |

### Strategies (complete)
| File | Strategy | Annual PnL | Validated Tokens |
|------|----------|-----------|-----------------|
| `strategies/s11_momentum_burst.py` | 3% burst + ADX>20 + EMA20 + volume | +$170K/yr (all), +$62K/yr (CPCV) | PENGU, SUI, AVAX, BONK, FLOKI, ZRO |
| `strategies/s09_optimized_trend.py` | EMA stack + ADX>30 + bullish DI | +$163K/yr (all), +$55K/yr (CPCV) | SUI, TRX, BONK, FLOKI |
| `strategies/s07_rsi_bounce.py` | RSI bounce | EXPERIMENTAL |
| `strategies/s08_obv_divergence.py` | OBV divergence | EXPERIMENTAL |

### Freqtrade Bridge (complete)
| File | Purpose | Status |
|------|---------|--------|
| `freqtrade_bridge/__init__.py` | Package docs | DONE |
| `freqtrade_bridge/exchange_registry.py` | Multi-exchange config (Kraken + Binance, extensible) | DONE |
| `freqtrade_bridge/strategy_shell.py` | Freqtrade IStrategy — reads cpcv_params.json | DONE |
| `freqtrade_bridge/parity_check.py` | Engine vs Freqtrade trade comparison | DONE |
| `freqtrade_bridge/config_kraken.json` | Example Kraken config | DONE |
| `export_params.py` | Engine → cpcv_params.json → Freqtrade bridge | DONE |

### Data (complete)
| Directory | Contents | Status |
|-----------|----------|--------|
| `real_data/1h_cache/` | 49 tokens × 5yr 1H OHLCV (Jan 2021 - Feb 2026) | DONE — extended from 2yr |
| `real_data/1h_cache_2yr_backup/` | Backup of original 2yr data | DONE |
| `real_data/4h_cache/` | 49 tokens × 4H OHLCV | DONE |
| `real_data/1m_cache/` | 49 tokens daily microstructure features | DONE |
| `real_data/all_tokens_enriched.parquet` | 57 tokens × 20 enriched features | DONE |

### Knowledge Base (21 docs, ~790KB total)
| File | Size | Contents |
|------|------|----------|
| `STRATEGY_RESULTS.md` | 10K | Master strategy ranking, findings, recommendations |
| `STRATEGY_CATALOG.md` | 95K | 40+ strategy blueprints from research |
| `INDICATOR_CATALOG.md` | 97K | 80+ indicator implementations |
| `ADVANCED_SIGNALS_CATALOG.md` | 92K | 78 advanced signals (ML, microstructure, cross-asset) |
| `RESEARCH_PAPERS_CATALOG.md` | 50K | 40+ academic papers with implementations |
| `ONCHAIN_DERIVATIVES_CATALOG.md` | 79K | 77 on-chain/derivatives indicators |
| `ONCHAIN_PRACTICAL_GUIDE.md` | 42K | Practical on-chain (MVRV, exchange flows) |
| `GEOPOLITICAL_MACRO_SIGNALS.md` | 42K | 15 macro event signals |
| `CAUSAL_ANALYSIS_RESEARCH.md` | 51K | Causal inference methods for trading |
| `SLIPPAGE_RESEARCH.md` | 34K | Slippage modeling and mitigation |
| `DATA_WEIGHTING_RESEARCH.md` | 23K | Recency weighting, regime-aware training |
| `QUANT_STRATEGIES_RESEARCH.md` | 33K | Quantitative strategy patterns |
| `PAPER_TRADING_PRO_FRAMEWORK.md` | 27K | Paper trading validation framework |
| `PAPER_TRADING_ANTI_FRAMEWORK.md` | 45K | Paper trading pitfalls and mitigations |
| `KRAKEN_FEES.md` | 18K | Kraken fee structure analysis |
| `FAT_TAIL_ANALYSIS.md` | 15K | Fat tail distribution analysis |
| `INDICATOR_ANALYSIS.md` | 13K | Signal IC and redundancy analysis |
| `JESSE_RESEARCH.md` | 22K | Jesse framework comparison |
| `VALIDATION_METHODS.md` | 14K | CPCV + WF validation methodology |
| `ARCHITECTURE.md` | 7K | System architecture and how-to |
| `DATA_MANIFEST.md` | 2K | Data file inventory |

---

## 3. KEY FINDINGS (from 2yr data — needs re-validation on 5yr)

### Best Strategies
1. **S11 Momentum Burst**: +$170K/yr (all 49), +$62K/yr (CPCV 11) — BEST
2. **S09 Optimized Trend**: +$163K/yr (all 49), +$55K/yr (CPCV 11)
3. **Dual Momentum**: +$21.6K/yr (CPCV 11) — reliable baseline

### Dual-Validated Tokens (pass BOTH CPCV + Walk-Forward)
- **S11**: PENGU, SUI, AVAX, BONK, FLOKI, ZRO (6 tokens)
- **S09**: SUI, TRX, BONK, FLOKI (4 tokens)
- **Triple-validated core**: SUI, BONK, FLOKI

### Critical Parameters
- **24-bar no-stop window**: Biggest single improvement (+$9K/yr)
- **3x ATR stops**: Tighter stops transformed performance
- **ADX > 20-30**: #1 predictive indicator
- **Mean reversion**: NEVER works on swing timeframes in crypto

### What Failed
- Vol Breakout (-$2.8K), VPIN filter (hurt), V2 Daily Momentum (-$13.8K/yr)
- All mean reversion strategies lose (-$25K to -$185K/yr)

---

## 4. PAPER TRADING / DRY RUN ARCHITECTURE

**Both Kraken and Binance use Freqtrade's `dry_run: true` mode**:
- Connects to real exchange API for live market data (orderbook, candles)
- Simulates orders locally — no orders sent to exchange
- Tracks virtual wallet ($200K starting balance)
- Fills at realistic orderbook prices (not instant)
- Works identically for both exchanges (Kraken has no paper trading, Binance testnet is unreliable)

**Safety layers**:
1. `dry_run: true` hardcoded as default in config generator
2. Empty API keys in generated configs
3. `force_entry_enable: false`
4. Strategy blocks non-validated tokens in `confirm_trade_entry()`

**To go live**: Only requires config change (dry_run=false + API keys), no code changes.

---

## 5. EXCHANGE CONFIGURATION

### Kraken
- Maker: 0.16%, Taker: 0.26%
- Rate limit: 3000ms, Throttle: 5s
- Max pairs: 20
- `stoploss_on_exchange: true` (supports conditional/stop orders)
- All 49 tokens available

### Binance
- Maker: 0.10%, Taker: 0.10%
- Rate limit: 500ms, Throttle: 3s
- Max pairs: 50
- `stoploss_on_exchange: true`
- All 49 tokens available

### Adding New Exchange
1. Add `ExchangeConfig` entry to `EXCHANGES` dict in `exchange_registry.py`
2. Run: `python export_params.py --exchange <name>`
3. Auto-generates `config_<exchange>.json`

---

## 6. PENDING TASKS / NEXT STEPS

### Immediate (do next session)
1. **Re-run CPCV + Walk-Forward on 5yr data** — All existing results are from 2yr data only
   ```bash
   python3 export_params.py --strategy s11 --noise 0.003 --exchange kraken,binance
   ```
   This will:
   - Re-validate all tokens on 5yr data
   - Apply noise injection (±0.3%) for fragility test
   - Generate production `cpcv_params.json`
   - Generate per-exchange Freqtrade configs

2. **Also validate S09 on 5yr data**:
   ```bash
   python3 export_params.py --strategy s09 --noise 0.003 --exchange kraken,binance --out cpcv_params_s09.json
   ```

3. **Update STRATEGY_RESULTS.md** with 5yr results — the current findings may change significantly with more data

4. **Update ARCHITECTURE.md** — needs to reflect:
   - `freqtrade_bridge/` directory
   - `export_params.py`
   - 5yr data (was documented as 2yr)
   - `extend_data_5yr.py`

5. **Update DATA_MANIFEST.md** — needs 5yr data date ranges

### Medium-term (build next)
6. **Portfolio optimizer** — combine S11 + S09 with position limits on overlapping tokens (SUI, BONK, FLOKI)
7. **Cross-sectional momentum strategy** — long outperformers vs BTC (untested from catalogs)
8. **Adaptive position sizing** — scale position by CPCV confidence (lower PBO = larger position)
9. **Regime-conditional deployment** — only run certain strategies in matching regimes
10. **Live monitoring dashboard** — FreqUI setup + custom metrics

### Research to explore (from catalogs)
11. **Better VPIN** — compute from 1-minute data (1m_cache) instead of daily aggregates
12. **Corwin-Schultz spread** per token for realistic slippage (already in export_params.py)
13. **On-chain signals** — MVRV, exchange net flows (requires data source)
14. **Macro event calendar** — Fed/CPI/employment as regime overlay
15. **Multi-strategy ensembles** — vote across 3+ strategies for higher confidence entries

### Verification (should do)
16. **Verify Kraken token list** — _KRAKEN_TOKENS set was approximated, should cross-check against Kraken API
17. **Parity check** — once Freqtrade is installed, run `parity_check.py` to compare engine vs Freqtrade trades
18. **Stress test** — run on deliberately bad market periods (2022 bear market in 5yr data)

---

## 7. FILE TREE SUMMARY

```
v2/
├── SESSION_STATE.md              ← THIS FILE (resume from here)
├── knowledge/                    ← 21 research docs (~790KB)
│   ├── STRATEGY_RESULTS.md       ← Master findings (needs 5yr update)
│   ├── ARCHITECTURE.md           ← System docs (needs update)
│   └── ... (19 more)
│
├── engine.py                     ← Core engine (PROTECTED)
├── cpcv.py                       ← CPCV validation (PROTECTED)
├── liquid_universe.py            ← Token universe (PROTECTED)
├── mtf_strategy_v2.py            ← Numba JIT engine (PROTECTED)
│
├── strategies/                   ← Strategy library
│   ├── s11_momentum_burst.py     ← BEST strategy
│   ├── s09_optimized_trend.py    ← 2nd best
│   └── ...
│
├── export_params.py              ← Engine → Freqtrade JSON bridge
│
├── freqtrade_bridge/             ← Freqtrade execution layer
│   ├── exchange_registry.py      ← Multi-exchange config
│   ├── strategy_shell.py         ← Freqtrade IStrategy
│   ├── parity_check.py           ← Engine vs Freqtrade comparison
│   └── config_kraken.json        ← Example config
│
├── real_data/
│   ├── 1h_cache/                 ← 49 tokens × 5yr (MAIN DATA)
│   ├── 1h_cache_2yr_backup/      ← Original 2yr backup
│   ├── 4h_cache/                 ← 49 tokens × 4H
│   └── 1m_cache/                 ← Microstructure features
│
├── results/                      ← Auto-logged JSON results
├── extend_data_5yr.py            ← Data extension script
└── ... (other scripts)
```

---

## 8. RUNNING AGENTS / BACKGROUND TASKS

**All agents completed.** No background tasks running.

Previous agents completed:
- Research papers catalog → RESEARCH_PAPERS_CATALOG.md
- Geopolitical/macro signals → GEOPOLITICAL_MACRO_SIGNALS.md
- On-chain practical guide → ONCHAIN_PRACTICAL_GUIDE.md
- Advanced signals catalog → ADVANCED_SIGNALS_CATALOG.md
- Strategy catalog → STRATEGY_CATALOG.md
- Indicator catalog → INDICATOR_CATALOG.md
- On-chain derivatives → ONCHAIN_DERIVATIVES_CATALOG.md
- Causal analysis → CAUSAL_ANALYSIS_RESEARCH.md
- 5yr data download → 49/49 tokens complete

---

## 9. CRITICAL DECISIONS MADE

1. **Hybrid architecture**: Engine for research, Freqtrade for execution only (not Jesse, not custom execution)
2. **Spot only**: No futures/margin for now (`can_short = False`)
3. **dry_run for both exchanges**: Freqtrade simulates locally using real data feed
4. **24-bar no-stop window**: Protection period before stoploss activates
5. **3x ATR stops**: Tighter than original (was 4x), major improvement
6. **CPCV PBO < 40% threshold**: Token must show alpha in >60% of timeline orderings
7. **Noise injection**: ±0.3% as default fragility test (matches Kraken vs Binance typical deviation)
8. **Kelly sizing**: Half-Kelly for T1 (BTC/ETH/SOL etc), Quarter-Kelly for T2/T3

---

## 10. HOW TO RESUME

```bash
cd /workspace/crypto_backtest/v2

# 1. Read this file first
cat SESSION_STATE.md

# 2. Run the 5yr validation (immediate priority)
python3 export_params.py --strategy s11 --noise 0.003 --exchange kraken,binance

# 3. Check results
cat cpcv_params.json | python3 -m json.tool | head -50

# 4. Update STRATEGY_RESULTS.md with new findings
```

---

## 11. ENVIRONMENT NOTES

- Python: `python3` (not `python`)
- Dependencies: numpy, pandas, numba, polars, talib (all installed in engine venv)
- No git repo initialized in v2/
- Working directory: `/workspace/crypto_backtest/v2`
- Freqtrade not installed yet (strategy_shell.py is ready but untested against Freqtrade)
