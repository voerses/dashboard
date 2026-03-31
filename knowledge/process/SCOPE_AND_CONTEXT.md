# Strategy Process — Scope & Context

## Trading Style
- **Primary focus: Swing trading and day trading**
- Holding periods: hours to days (occasionally weeks)
- Timeframes: 1h, 4h, daily OHLCV
- NOT high-frequency trading (HFT) — no sub-second execution, no co-location, no market-making
- NOT ultra-low-latency — millisecond execution speed is not a competitive advantage for us

## Objective Hierarchy (in priority order)
1. **Maximize returns** — this is the primary goal. We are here to make money.
2. **Don't lose big** — survive drawdowns, never blow up. Capital preservation is the constraint, not the objective.
3. **Everything else serves #1 and #2** — Sharpe, Sortino, etc. are diagnostic tools, not goals.

### Metric Implications
- **Sortino > Sharpe** as primary metric — Sharpe penalizes upside volatility, which we WANT
- **Total return and CAGR** matter — a strategy with Sharpe 0.8 and 80% annual return beats one with Sharpe 2.0 and 15% annual return
- **Max drawdown** is the key risk constraint — not volatility
- **Calmar ratio (return/maxDD)** is the best single metric for our goals
- **Profit factor** tells us if the edge is real
- Don't optimize for smooth equity curves at the expense of total return

## What This Means for the Knowledge Base
When reading the research files in this directory, apply this filter:

### Relevant to Us
- Signal discovery (IC testing, factor engineering, causal analysis)
- Backtesting validation (walk-forward, CPCV, deflated Sharpe ratio)
- Risk management and portfolio construction
- Regime detection and adaptive strategies
- On-chain signals and macro indicators
- ETF flow analysis (multi-day lag effects)
- Paper trading validation
- Strategy decay monitoring
- Vectorized backtesting (pandas/numpy — fast enough for our timeframes)
- Data quality and pipeline design

### Reference Only (not our edge)
- HFT infrastructure, co-location, FPGA
- Sub-millisecond execution optimization
- Tick-level microstructure (we use OHLCV)
- Market-making strategies
- GPU-accelerated backtesting (overkill for our scale)
- Low-latency data feeds (websocket speed is fine)
- Order book shape analysis at microsecond resolution

### Our Edge Is In
1. **Better signals** — finding novel alpha sources others miss
2. **Better validation** — rigorous statistical testing prevents overfitting
3. **Better risk management** — surviving drawdowns that kill others
4. **Faster iteration** — testing more ideas per week than competitors
5. **Crypto-native insights** — ETF flows, on-chain data, funding rates, liquidation cascades
6. **Multi-token universe** — 199 perp + 135 spot tokens gives us breadth for statistical validation

## Current Infrastructure
- Backtesting engine: custom Python v4 (vectorized, portfolio-level simulation)
- Validation: walk-forward + CPCV dual gate across full token universe
- Exchange: Binance (primary), Kraken, Hyperliquid (fee docs in KRAKEN_FEES.md)
- Data: Multi-exchange perp + spot OHLCV with funding rates (see DATA_MANIFEST.md for coverage)
- Strategies: Python files following TEMPLATE.py pattern
