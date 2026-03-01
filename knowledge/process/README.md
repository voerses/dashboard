# Strategy Process Knowledge Base

> Research and frameworks for developing winning crypto swing/day trading strategies.
> Compiled March 2026 from best-in-class quant research, academic papers, and top fund practices.

## Quick Start
1. Read `SCOPE_AND_CONTEXT.md` — what's relevant to our swing/day trading focus
2. Read `STRATEGY_PIPELINE_GATES.md` — the definitive gate/kill-criteria reference
3. Check `DATA_GAP_ANALYSIS.md` — what data we're missing

## File Index

### Process & Gates
| File | Purpose | When to Read |
|------|---------|-------------|
| `SCOPE_AND_CONTEXT.md` | Trading style scope, what's relevant vs reference-only | First |
| `STRATEGY_PIPELINE_GATES.md` | **Unified pipeline: 6 gates, kill criteria, metrics** | Every strategy dev cycle |
| `FAST_ITERATION_FRAMEWORK.md` | How to iterate fast, time-box research, kill losers early | Process design |

### Signal Research
| File | Purpose | When to Read |
|------|---------|-------------|
| `SIGNAL_DISCOVERY_METHODS.md` | IC testing, causal analysis, factor engineering, overfitting prevention | Signal Lab phase |
| `CRYPTO_MICROSTRUCTURE_POST_ETF.md` | ETF impact, on-chain signals, regime changes, derivatives signals | New signal ideation |
| `DATA_GAP_ANALYSIS.md` | What data we have vs what we need | Before starting new signal research |

### Validation & Risk
| File | Purpose | When to Read |
|------|---------|-------------|
| `BACKTESTING_VALIDATION_BEST_PRACTICES.md` | Walk-forward, CPCV, DSR, PBO, metrics hierarchy | Validation phase |
| `BACKTESTING_VALIDATION_REFERENCE.md` | Detailed formulas, thresholds, position sizing | Reference lookup |
| `RISK_PORTFOLIO_CONSTRUCTION.md` | Multi-strategy allocation, Kelly, drawdown management | Portfolio construction |

### Infrastructure
| File | Purpose | When to Read |
|------|---------|-------------|
| `QUANT_FUND_PROCESSES.md` | How RenTech, Two Sigma, etc. structure research pipelines | Process inspiration |
| `PERFORMANT_BACKTESTING_INFRA.md` | Vectorization, data pipelines, codebase structure | Infra improvements |

### Process Changes
| File | Purpose |
|------|---------|
| `../../AIPIP/AIPIP-0014-strategy-process-v2.md` | Proposed upgrade to /strategy workflow |

## Total Knowledge Base Size
~440KB across 11 files — comprehensive but navigable via this index.
