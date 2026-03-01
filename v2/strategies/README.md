# Strategy Library

Each strategy is a standalone module that implements a standard interface.
The backtesting engine (`mtf_strategy_v2.py`) is NEVER modified — strategies are tested through wrappers.

## Directory Structure
```
strategies/
├── README.md                    # This file
├── __init__.py                  # Strategy registry
├── s01_dual_momentum.py         # Fat-tail dual momentum (PROFITABLE)
├── s02_mean_reversion.py        # Fat-tail mean reversion (MARGINAL)
├── s03_vol_breakout.py          # Fat-tail vol breakout (LOSING - archived)
├── s04_v3_contrarian.py         # V3 liquidity contrarian (PROFITABLE)
├── s05_vpin_enhanced.py         # VPIN-filtered DM+MR (LOSING - needs better data)
├── s06_v2_daily_momentum.py     # V2 daily EMA cross (TERRIBLE - archived)
```

## Standard Interface
Every strategy module exposes:
- `run(tokens, capital, **kwargs) -> dict[ticker, result]`
- `result` dict has: ticker, tier, total_return, n_trades, win_rate, payoff_ratio, equity, trades

## How to Add a New Strategy
1. Copy an existing strategy file as template
2. Implement the `run()` function
3. Register in `__init__.py`
4. Run `python run_test_suite.py --strategy sXX_name` to test
5. Results auto-logged to `results/` and `knowledge/STRATEGY_RESULTS.md` updated

## Status Tags
- **PROFITABLE**: Positive annual PnL, validated
- **MARGINAL**: Near breakeven, not enough alpha
- **LOSING**: Negative PnL, kept for reference
- **EXPERIMENTAL**: Untested or in development
