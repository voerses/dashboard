# Strategy Process Knowledge Base

> Research and frameworks for developing winning crypto swing trading strategies.
> Consolidated March 2026. Archived research available in `knowledge/archive/`.

## Quick Start
1. Read `../STRATEGY_QUICK_REFERENCE.md` — single-file gate reference (replaces reading multiple files)
2. Deep dive into files below only when investigating specifics

## Active Files

### Process & Gates
| File | Purpose | When to Read |
|------|---------|-------------|
| `SCOPE_AND_CONTEXT.md` | Trading style scope, what's relevant vs reference-only | First |
| `STRATEGY_PIPELINE_GATES.md` | Unified pipeline: 6 gates, kill criteria, metrics | Every strategy dev cycle |

### Signal Research
| File | Purpose | When to Read |
|------|---------|-------------|
| `SIGNAL_DISCOVERY_METHODS.md` | IC testing, causal analysis, factor engineering, overfitting prevention | Signal Lab phase |
| `CRYPTO_MICROSTRUCTURE_POST_ETF.md` | ETF impact, on-chain signals, regime changes, derivatives signals | New signal ideation |

### Validation & Risk
| File | Purpose | When to Read |
|------|---------|-------------|
| `BACKTESTING_VALIDATION_BEST_PRACTICES.md` | Walk-forward, CPCV, DSR, PBO, costs, bias audit | Validation phase |
| `RISK_PORTFOLIO_CONSTRUCTION.md` | Multi-strategy allocation, Kelly, drawdown management | Portfolio construction |

## Archived Files (in `../archive/process/`)
Moved during consolidation — preserved for reference but not needed for gate process:
- `BACKTESTING_VALIDATION_REFERENCE.md` — superseded by BEST_PRACTICES
- `FAST_ITERATION_FRAMEWORK.md` — superseded by STRATEGY_PIPELINE_GATES
- `PERFORMANT_BACKTESTING_INFRA.md` — superseded by PERFORMANCE_PATTERNS
- `QUANT_FUND_PROCESSES.md` — background research, not actionable
- `DATA_GAP_ANALYSIS.md` — future data sourcing reference
