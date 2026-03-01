# Automated Trading

Quantitative crypto trading system — swing and day trading strategies validated through a rigorous statistical pipeline.

## What's Here

```
strategies/          # Trading strategies (s09-s22), TEMPLATE.py for new ones
  archive/           # Tier C strategies (preserved, not active)
knowledge/           # Domain knowledge base (~900KB)
  process/           # Strategy development process (gates, kill criteria, research)
v3/                  # Validation engine (walk-forward + CPCV dual gate)
v2/                  # Signal lab, Freqtrade bridge, parameter export
v1/                  # Legacy engine (reference only)
data/                # Market data (1h/4h OHLCV, gitignored)
results/             # Sweep summaries (JSON)
indicators/          # Shared indicator library
```

## Strategy Tier System

Strategies are classified by V3 validation rate (% of 49 tokens passing dual WF+CPCV gate):

| Tier | Rate | Status | Count |
|------|------|--------|-------|
| **A** (production) | > 50% | Deploy | 6 strategies |
| **B** (experimental) | 20-50% | Iterate | 6 strategies |
| **C** (archived) | < 20% | Archive | 4 strategies |

**Current Tier A:** s11 (75.5%), s09 (73.5%), s13 (67.3%), s21 (63.3%), s17 (55.1%), s18 (51.0%)

## Quick Start

```bash
# Validate a strategy against all 49 tokens
python v3/validation.py --strategy s11 --workers 4

# Validate on BTC only (quick check)
python v3/validation.py --strategy s11 --tokens BTC

# Run full sweep (all strategies)
python v3/validation.py --strategy s09 s11 s13 s17 s18 s21 --workers 4

# Signal lab — test a new indicator's IC
python v2/signal_lab.py
```

## Strategy Development Pipeline

8-gate process — kill losers fast, advance winners. Return-first (Calmar > Sortino > Sharpe).

```
Gate 0: Idea Screen        (~5 min)    Kill ~50%
Gate 1: Signal Lab (IC)    (~30 min)   Kill ~90%
Gate 2: Knowledge + Dedup  (~15 min)   Kill ~30%
Gate 3: Prototype          (~30 min)   Kill ~10%
Gate 4: BTC Validation     (~5 min)    Kill ~50%
Gate 5: Full 49-Token      (~10 min)   Kill ~50%
Gate 6: Paper Trading      (~1-4 wks)  Kill ~50%
Gate 7: Production + Decay (ongoing)   ~30%/yr decay
```

~99.8% of ideas never reach production. This is normal. See `knowledge/process/STRATEGY_PIPELINE_GATES.md`.

## Knowledge Base

| File | What |
|------|------|
| `knowledge/STRATEGY_LIFECYCLE.md` | Tier system, sweep protocol, iteration rules |
| `knowledge/process/STRATEGY_PIPELINE_GATES.md` | Gate criteria and kill thresholds |
| `knowledge/process/SCOPE_AND_CONTEXT.md` | Trading style scope (swing/day, not HFT) |
| `knowledge/process/DATA_GAP_ANALYSIS.md` | Missing data sources and acquisition plan |
| `knowledge/KRAKEN_FEES.md` | Exchange fee tiers |
| `knowledge/SLIPPAGE_RESEARCH.md` | Slippage modeling by exchange and tier |

Full index: `knowledge/process/README.md`

## AI Development Process

This repo includes an AI-assisted development framework (via Claude Code):

| Command | Purpose |
|---------|---------|
| `/strategy` | 8-gate strategy development pipeline |
| `/dev` | Structured feature workflow (specify → design → implement) |
| `/review` | Independent code review |
| `/free` | Freeflow mode (no process enforcement) |

Process governance via AIPIPs — see `AIPIP/README.md`.
