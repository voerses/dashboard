# Fast Iteration Framework for Trading Strategy Development

> **Goal:** Reject losers quickly, advance winners. Minimize the time and compute from idea to validated strategy or confident kill.

---

## Table of Contents

1. [Minimal Viable Strategy (MVS) Concept](#1-minimal-viable-strategy-mvs-concept)
2. [Stage-Gate Process with Kill Criteria](#2-stage-gate-process-with-kill-criteria)
3. [Sequential Testing (SPRT) for Early Decisions](#3-sequential-testing-sprt-for-early-decisions)
4. [Fail Fast Patterns](#4-fail-fast-patterns)
5. [Information Coefficient (IC) Screening](#5-information-coefficient-ic-screening)
6. [Validation Methods: Avoiding Overfitting](#6-validation-methods-avoiding-overfitting)
7. [Bayesian Optimization for Hyperparameters](#7-bayesian-optimization-for-hyperparameters)
8. [Automated Strategy Generation](#8-automated-strategy-generation)
9. [Strategy Templates and Factory Patterns](#9-strategy-templates-and-factory-patterns)
10. [Research Infrastructure for Speed](#10-research-infrastructure-for-speed)
11. [Parallelizing Research](#11-parallelizing-research)
12. [Research Sprints and Time-Boxing](#12-research-sprints-and-time-boxing)
13. [Idea Backlog and Prioritization](#13-idea-backlog-and-prioritization)
14. [Measuring and Minimizing Cycle Time](#14-measuring-and-minimizing-cycle-time)

---

## 1. Minimal Viable Strategy (MVS) Concept

The MVS is the simplest testable version of a trading idea -- enough to determine whether the core signal has any predictive power, but no more.

### What an MVS Includes

- **A single testable hypothesis:** e.g., "Price reverts when >2 standard deviations from the 20-period mean" or "Volume-weighted momentum over 4 hours predicts 1-hour returns."
- **Signal generation logic:** One function that takes market data and outputs a directional signal (long/short/flat).
- **Basic position sizing:** Equal-weight or fixed-size. No complex Kelly or risk-parity at this stage.
- **A vectorized backtest:** Fast, numpy/pandas-based. No event-driven simulation needed yet.
- **Three metrics:** Sharpe ratio, max drawdown, win rate. That is all.

### What an MVS Excludes

- Execution modeling (slippage, fees) -- add at Gate 2.
- Multi-instrument logic -- add at Gate 3.
- Risk management beyond basic stops -- add at Gate 3.
- Parameter optimization -- the MVS uses reasonable defaults from literature or intuition.

### MVS Time Budget

An MVS should take **2-4 hours** maximum from idea to first backtest result. If it takes longer, the research infrastructure needs improvement (see Section 10).

### MVS Implementation Pattern

```python
class MinimalViableStrategy:
    """Template for rapid hypothesis testing."""

    def __init__(self, params: dict):
        self.params = params

    def generate_signal(self, data: pd.DataFrame) -> pd.Series:
        """Core logic. Returns -1, 0, +1 signal series."""
        raise NotImplementedError

    def quick_backtest(self, data: pd.DataFrame) -> dict:
        """Vectorized backtest. Returns {sharpe, max_dd, win_rate}."""
        signals = self.generate_signal(data)
        returns = data['returns'] * signals.shift(1)
        sharpe = returns.mean() / returns.std() * np.sqrt(365 * 24)
        max_dd = (returns.cumsum() - returns.cumsum().cummax()).min()
        win_rate = (returns > 0).sum() / (returns != 0).sum()
        return {'sharpe': sharpe, 'max_dd': max_dd, 'win_rate': win_rate}
```

---

## 2. Stage-Gate Process with Kill Criteria

A disciplined stage-gate process ensures resources flow only to strategies that pass progressively harder validation. Each gate has quantitative kill criteria -- fail any gate and the strategy is killed or recycled.

### Gate 0: Hypothesis Sanity Check (5 minutes)

**Purpose:** Kill obviously flawed ideas before any compute is spent.

**Kill Criteria:**
- No clear economic rationale or behavioral explanation for why the signal should exist.
- Signal relies on data not available at decision time (lookahead bias by construction).
- Similar idea already tested and failed (check the idea graveyard).
- Signal frequency too low for statistical significance in available data (<30 expected trades).

**Decision:** PASS (proceed to Gate 1) or KILL (log to idea graveyard with reason).

### Gate 1: Signal Quality Screen (2-4 hours)

**Purpose:** Determine whether the raw signal has any predictive power.

**Deliverable:** MVS backtest on a single instrument, single timeframe.

**Kill Criteria (all must pass):**

| Metric | Threshold | Rationale |
|--------|-----------|-----------|
| Mean IC | > 0.02 | Below this, signal is indistinguishable from noise |
| IC t-stat | > 2.0 | Statistical significance at ~95% confidence |
| Sharpe ratio (gross) | > 1.0 | Must clear a high bar before costs |
| Max drawdown | < 30% | Unacceptable tail risk |
| Number of trades | > 50 | Insufficient sample for inference below this |
| Profit factor | > 1.3 | Edge must be material |

**Decision:**
- All pass: PROCEED to Gate 2.
- Sharpe > 0.7 but other metrics borderline: RECYCLE with modified parameters (one retry only).
- Any hard fail: KILL. Log results. Move on.

### Gate 2: Single-Instrument Deep Validation (1-2 days)

**Purpose:** Confirm the signal survives realistic conditions on one instrument.

**Deliverable:** Event-driven backtest with execution modeling.

**Additional Tests Applied:**
- Add realistic transaction costs (fees, slippage, funding rates).
- Walk-forward analysis with 5+ out-of-sample windows.
- Parameter sensitivity analysis (signal must survive +/- 20% parameter perturbation).
- Regime analysis: does the strategy work in trending AND ranging markets?
- Compute Deflated Sharpe Ratio (DSR) to correct for multiple testing.

**Kill Criteria:**

| Metric | Threshold | Rationale |
|--------|-----------|-----------|
| Net Sharpe (after costs) | > 0.8 | Must still be attractive after costs |
| DSR p-value | < 0.05 | Must survive multiple-testing correction |
| Walk-forward consistency | > 60% of windows profitable | Cannot rely on one regime |
| Parameter sensitivity | Sharpe degrades < 30% | Cannot be a knife-edge optimization |
| Calmar ratio | > 0.5 | Return must justify drawdown |

**Decision:** PROCEED to Gate 3 or KILL.

### Gate 3: Multi-Instrument / Portfolio Validation (3-5 days)

**Purpose:** Confirm the signal generalizes across instruments and adds value to a portfolio.

**Deliverable:** Portfolio-level backtest across multiple instruments.

**Tests Applied:**
- Cross-instrument validation: test on 5+ instruments (different liquidity profiles).
- Combinatorial Purged Cross-Validation (CPCV) for robust performance estimation.
- Correlation analysis with existing strategies (diversification value).
- Capacity analysis: can it handle target position sizes without excessive market impact?
- Stress testing against historical extreme events (March 2020, May 2021, FTX collapse, etc.).

**Kill Criteria:**

| Metric | Threshold | Rationale |
|--------|-----------|-----------|
| Cross-instrument hit rate | > 60% of instruments profitable | Must generalize |
| Portfolio Sharpe (incremental) | > 0.3 improvement | Must add value to existing book |
| Correlation with existing strategies | < 0.5 | Must provide diversification |
| Capacity | > $100K notional | Must be tradeable at meaningful size |
| Worst-case drawdown (stress) | < 40% | Must survive tail events |

**Decision:** PROCEED to Gate 4 or KILL.

### Gate 4: Paper Trading Confirmation (1-4 weeks)

**Purpose:** Confirm the strategy works in real-time with live data feeds.

**Deliverable:** Paper trading results with live market data.

**Tests Applied:**
- Run strategy on live data with simulated execution.
- Compare fills vs. backtest assumptions (slippage reality check).
- Monitor for data feed issues, edge cases, and operational failures.
- Sequential testing (SPRT) to confirm live performance matches backtest expectations.

**Kill Criteria:**

| Metric | Threshold | Rationale |
|--------|-----------|-----------|
| Live vs. backtest Sharpe ratio | > 0.6x backtest | Expected degradation, but not collapse |
| SPRT verdict | "Accept H1" (strategy works) | Sequential confirmation of edge |
| Operational failures | < 1 per week | Must be reliable |
| Fill quality | Slippage < 2x backtest assumption | Execution must be feasible |

**Decision:** GO LIVE (with small allocation) or KILL.

### Gate 5: Live Performance Review (ongoing, monthly)

**Purpose:** Confirm the strategy continues to perform.

**Kill Criteria:**
- Rolling 3-month Sharpe < 0.0 (strategy has lost its edge).
- Drawdown exceeds 1.5x worst historical drawdown.
- Strategy behavior diverges from its hypothesis (regime shift).
- Correlation with existing strategies increases above 0.7 (diversification lost).

---

## 3. Sequential Testing (SPRT) for Early Decisions

The Sequential Probability Ratio Test (SPRT), developed by Abraham Wald, is optimal for making accept/reject decisions with the minimum amount of data. It is the single most important statistical tool for a "fail fast" strategy research process.

### Why SPRT for Trading

- **Sample-size optimal:** SPRT minimizes the expected number of observations needed to make a decision, typically requiring 50% fewer observations than fixed-sample tests.
- **No peeking penalty:** Unlike fixed-sample hypothesis tests, SPRT is designed for continuous monitoring. There is no alpha-spending or p-value adjustment needed.
- **Natural fit for live monitoring:** As trade results come in sequentially, SPRT can determine whether the strategy is working as expected.

### How to Apply SPRT to Strategy Validation

**Setup:**
- H0 (null): Strategy has no edge. True Sharpe ratio = 0.
- H1 (alternative): Strategy has an edge. True Sharpe ratio = target (e.g., 1.0 annualized).
- Set acceptable error rates: alpha = 0.05 (false positive), beta = 0.10 (false negative).

**Boundaries:**
```
A = (1 - beta) / alpha       # Upper boundary (accept H1)
B = beta / (1 - alpha)       # Lower boundary (accept H0)

After each trade/period:
  - Compute cumulative log-likelihood ratio
  - If ratio >= ln(A): ACCEPT H1 (strategy works, proceed)
  - If ratio <= ln(B): ACCEPT H0 (strategy has no edge, KILL)
  - Otherwise: CONTINUE collecting data
```

**Practical Implementation:**
```python
import numpy as np

def sprt_trading(returns: np.ndarray, target_sharpe: float = 1.0,
                 alpha: float = 0.05, beta: float = 0.10) -> str:
    """
    Sequential test of whether a strategy has a meaningful Sharpe ratio.

    Returns: 'accept_edge', 'reject_edge', or 'continue'
    """
    A = np.log((1 - beta) / alpha)
    B = np.log(beta / (1 - alpha))

    # Convert target Sharpe to per-period mean return
    # Assuming daily returns, Sharpe = mean/std * sqrt(252)
    std = returns.std()
    if std == 0:
        return 'continue'
    target_mean = target_sharpe * std / np.sqrt(365 * 24)  # hourly for crypto

    # Cumulative log-likelihood ratio
    cumulative_llr = 0.0
    for r in returns:
        # Log-likelihood ratio for normal distribution
        llr = (r * target_mean / (std ** 2)) - (target_mean ** 2 / (2 * std ** 2))
        cumulative_llr += llr

        if cumulative_llr >= A:
            return 'accept_edge'
        elif cumulative_llr <= B:
            return 'reject_edge'

    return 'continue'
```

### SPRT Use Cases in the Pipeline

| Stage | SPRT Application |
|-------|-----------------|
| Gate 1 | Quick check: does the signal have any IC > 0? |
| Gate 4 | Paper trading: is live performance consistent with backtest? |
| Gate 5 | Live monitoring: has the strategy's edge decayed? |
| Regime detection | Has the market regime shifted (mean return changed)? |

### Limitations in Trading Context

- **Non-stationarity:** Financial returns are not i.i.d. SPRT boundaries are approximate.
- **Composite hypotheses:** The "true" Sharpe is unknown; SPRT is optimal only for simple hypotheses. Use mixture SPRT (mSPRT) for robustness.
- **Noisy data:** In highly noisy markets, SPRT may take a long time in the indifference zone. Set a maximum observation count as a timeout.

---

## 4. Fail Fast Patterns

### The Cost of Not Killing Early

Every hour spent on a losing strategy has two costs:
1. **Direct cost:** Compute, data, and researcher time.
2. **Opportunity cost:** Another idea in the backlog is not being tested.

The optimal research process is one that minimizes total cost across all ideas, which means killing losers as fast as possible.

### Pattern 1: Hypothesis-First Research

Never start coding before writing down:
1. **What** the signal is (precise mathematical definition).
2. **Why** it should work (economic/behavioral rationale).
3. **How** you will know it failed (specific kill criteria before running the test).

This prevents post-hoc rationalization of random results.

### Pattern 2: Progressive Fidelity

Start with the cheapest, fastest test. Only increase fidelity if the cheap test passes.

```
Napkin math (5 min) --> Vectorized backtest (2 hr) --> Event-driven backtest (1 day)
    --> Walk-forward (2 days) --> Paper trading (2 weeks) --> Live (ongoing)
```

Each stage is 5-10x more expensive than the previous one. Kill rates should be highest at the cheapest stages.

**Expected kill rates per stage:**
- Gate 0 (sanity): 30-40% of ideas killed
- Gate 1 (signal screen): 60-70% of survivors killed
- Gate 2 (deep validation): 40-50% of survivors killed
- Gate 3 (multi-instrument): 30-40% of survivors killed
- Gate 4 (paper trading): 10-20% of survivors killed

**Net survival rate:** ~3-8% of initial ideas reach live trading. This is normal and healthy.

### Pattern 3: Time-Box Everything

No research task is open-ended. Every task has a time budget:

| Task | Time Budget | Outcome if Budget Exceeded |
|------|------------|---------------------------|
| MVS backtest | 4 hours | Kill idea or simplify hypothesis |
| Parameter sensitivity | 4 hours | Accept current parameters |
| Deep validation | 2 days | Kill or escalate to review |
| Multi-instrument test | 5 days | Kill or reduce instrument scope |

If a time budget is exceeded, the default action is KILL, not "spend more time."

### Pattern 4: The Idea Graveyard

Maintain a structured log of every killed idea:

```json
{
  "id": "STRAT-042",
  "hypothesis": "BTC funding rate mean-reversion",
  "date_tested": "2026-02-15",
  "gate_killed_at": 1,
  "reason": "IC = 0.008, below 0.02 threshold",
  "time_spent": "3 hours",
  "data_used": "binance_perp_btc_1h_2023_2025",
  "notes": "Might work on shorter timeframe. Revisit if funding rate regime changes."
}
```

This prevents re-testing the same idea and enables pattern recognition across failures.

### Pattern 5: Pre-Mortem Analysis

Before starting any Gate 2+ work, ask: "What would make us kill this strategy?" Write down the specific scenarios. This combats sunk-cost bias and confirmation bias during the research process.

---

## 5. Information Coefficient (IC) Screening

The Information Coefficient is the rank correlation between predicted and actual returns. It is the fastest and most informative single metric for signal quality screening.

### IC Thresholds for Signal Screening

| Mean IC | Interpretation | Action |
|---------|---------------|--------|
| < 0.00 | Anti-predictive | Kill immediately (or reverse the signal) |
| 0.00 - 0.02 | Noise | Kill -- not enough edge to overcome costs |
| 0.02 - 0.05 | Weak but potentially tradeable | Proceed with caution; needs high capacity or low costs |
| 0.05 - 0.10 | Good signal | Strong proceed signal |
| > 0.10 | Excellent | Rare; verify it is not data-snooped |

### IC Stability Analysis

A high mean IC with high variance is worse than a moderate mean IC with low variance.

**Key metrics:**
- **IC mean:** Average predictive power.
- **IC std:** Stability of predictive power.
- **IC IR (Information Ratio):** IC_mean / IC_std. Target > 0.5.
- **IC hit rate:** Percentage of periods with positive IC. Target > 55%.

### Rolling IC Analysis

```python
def rolling_ic_analysis(predictions: pd.Series, actuals: pd.Series,
                        window: int = 60) -> pd.DataFrame:
    """Compute rolling IC and stability metrics."""
    rolling_ic = predictions.rolling(window).corr(actuals)
    return pd.DataFrame({
        'ic': rolling_ic,
        'ic_mean': rolling_ic.expanding().mean(),
        'ic_std': rolling_ic.expanding().std(),
        'ic_ir': rolling_ic.expanding().mean() / rolling_ic.expanding().std(),
        'ic_hit_rate': (rolling_ic > 0).expanding().mean()
    })
```

### Quantile Spread Analysis

Beyond IC, examine whether the signal creates a monotonic spread in returns:

1. Sort instruments/periods into quantiles by signal strength.
2. Compute mean return for each quantile.
3. The spread between top and bottom quantile should be positive, significant, and monotonic.

A signal with good IC but non-monotonic quantile returns is unreliable.

### IC Decay Analysis

Measure how quickly the signal's predictive power decays:
- Compute IC at multiple forward horizons (1h, 4h, 12h, 24h, 48h, 7d).
- A signal with rapid IC decay is best for short holding periods.
- A signal with slow IC decay supports longer holding periods.
- Mismatch between IC decay profile and intended holding period is a kill signal.

---

## 6. Validation Methods: Avoiding Overfitting

### The Deflated Sharpe Ratio (DSR)

The DSR, developed by Bailey and Lopez de Prado (2014), corrects the observed Sharpe ratio for:
1. **Selection bias:** When you test N strategies and pick the best, the winner's Sharpe is inflated.
2. **Non-normality:** Fat tails and skewness in returns inflate naive Sharpe estimates.

**Key insight:** After testing 1,000 independent strategies with zero true Sharpe, the expected best Sharpe is 3.26. The DSR deflates this back to reality.

**Application in the pipeline:**
- Track every backtest run during strategy development (N_trials).
- At Gate 2, compute DSR using the full trial history.
- Kill if DSR p-value > 0.05 (the observed Sharpe is not statistically significant after correcting for how many things you tried).

### Combinatorial Purged Cross-Validation (CPCV)

CPCV, also by Lopez de Prado (2017), is the gold standard for out-of-sample testing in finance:

1. **Purging:** Remove training observations whose label horizons overlap with the test period. This eliminates lookahead bias.
2. **Embargoing:** After each test period boundary, exclude a buffer of observations from training to prevent autocorrelation leakage.
3. **Combinatorial splitting:** Generate many train/test splits systematically, producing a distribution of out-of-sample performance metrics.

**Advantages over walk-forward analysis:**
- CPCV produces a distribution of Sharpe ratios, not a single point estimate.
- Empirically shown to have lower Probability of Backtest Overfitting (PBO) than walk-forward.
- More robust to specific market regime sequences.

**When to use:**
- Gate 2 and Gate 3 validation.
- Any time a strategy has more than 2-3 tunable parameters.

### Walk-Forward Analysis (WFA)

Still useful and simpler to implement than CPCV:

1. Optimize on training window (e.g., 12 months).
2. Test on subsequent out-of-sample window (e.g., 3 months).
3. Roll forward and repeat.
4. Aggregate out-of-sample results.

**Key rules:**
- Use at least 5 out-of-sample windows.
- Strategy must be profitable in > 60% of windows.
- Never optimize the walk-forward window sizes themselves (meta-overfitting).

### Hold-Out Set Discipline

Reserve the most recent 10-20% of data as a final hold-out set:
- Never touch it during development.
- Use it exactly once, at Gate 3, as the final confirmation.
- If it fails on the hold-out, the strategy is killed regardless of prior results.

---

## 7. Bayesian Optimization for Hyperparameters

### Why Not Grid Search

Grid search is exhaustive but wasteful:
- A strategy with 5 parameters, each with 10 values = 100,000 combinations.
- Each backtest takes 10 seconds = 11.5 days of compute.
- Most of those combinations are in unpromising regions of the parameter space.

### Bayesian Optimization Advantages

Bayesian optimization uses a probabilistic surrogate model (Gaussian Process or Tree-structured Parzen Estimator) to predict which parameter combinations are most likely to improve performance:

| Feature | Grid Search | Random Search | Bayesian Optimization |
|---------|------------|---------------|----------------------|
| Evaluations needed | Very high | Moderate | Low (10-50x fewer) |
| Informed by prior results | No | No | Yes |
| Handles high-dimensional spaces | Poorly | Moderately | Well |
| Risk of overfitting | High (exhaustive) | Moderate | Lower (targeted) |
| Implementation complexity | Low | Low | Moderate |

### Practical Implementation

```python
import optuna

def objective(trial):
    # Define parameter space
    lookback = trial.suggest_int('lookback', 10, 200)
    threshold = trial.suggest_float('threshold', 0.5, 3.0)
    stop_loss = trial.suggest_float('stop_loss', 0.01, 0.05)

    # Run backtest with these parameters
    strategy = MyStrategy(lookback=lookback, threshold=threshold, stop_loss=stop_loss)
    results = strategy.backtest(data)

    # Optimize for Sharpe ratio (or any metric)
    return results['sharpe']

study = optuna.create_study(direction='maximize')
study.optimize(objective, n_trials=100)  # 100 smart trials vs 100,000 grid trials
```

### Key Practices for Trading

1. **Optimize for robustness, not peak performance:** Use the median Sharpe across walk-forward windows as the objective, not the maximum in-sample Sharpe.
2. **Use early pruning:** Optuna supports pruning unpromising trials midway through backtesting, saving compute.
3. **Set parameter bounds from domain knowledge:** Narrow bounds reduce the search space and the risk of finding spurious optima.
4. **Track trial count for DSR:** Every Bayesian optimization trial counts toward the DSR correction.

---

## 8. Automated Strategy Generation

### Genetic Programming (GP) for Strategy Discovery

GP evolves populations of trading strategies through selection, crossover, and mutation. Each "individual" is a tree representing a trading rule built from technical indicators, mathematical operators, and logical conditions.

**Key frameworks:**
- **StrategyQuant X:** Commercial tool that uses GP to generate and screen thousands of trading strategies automatically. Includes multi-objective optimization (Sharpe, drawdown, trade count).
- **GeneTrader (open-source):** Generates strategy templates, extracts parameters, runs parallel backtests using a genetic algorithm. Saves the best individual from each generation.
- **QuantEvolve (2025):** Multi-agent evolutionary framework combining quality-diversity optimization with hypothesis-driven strategy generation. Uses a feature map aligned with investor preferences (strategy type, risk profile, turnover).

### LLM-Powered Strategy Generation

Emerging approach (2025-2026):
- **Seed Alpha Factory:** Uses LLMs to generate initial strategy hypotheses from financial literature, market microstructure theory, or pattern descriptions.
- **Multi-agent decision-making:** Multiple LLM agents propose, critique, and refine strategies.
- **Automated backtesting and scoring:** Generated strategies are automatically backtested and scored.

### AutoML for Trading

- **TPOT:** Uses GP to optimize entire ML pipelines (preprocessing, feature selection, model selection) for trading signal prediction.
- **EMADE:** Evolutionary Multi-objective Algorithm Design Engine extended with technical indicator primitives for automated strategy creation.

### Screening the Output

Automated generation produces many candidates. Apply the same stage-gate criteria:
1. Filter by Gate 1 thresholds (IC, Sharpe, drawdown).
2. Cluster similar strategies to estimate effective independent trials.
3. Apply DSR correction to the best candidates.
4. Only advance the top 5-10% to Gate 2 validation.

### Overfitting Risk

Automated generation amplifies the multiple-testing problem:
- If GP evaluates 10,000 strategies, the expected best Sharpe of a zero-edge population is ~4.0.
- Rigorous DSR correction and out-of-sample validation are non-negotiable.
- Human review of the top candidates' economic logic is essential (does the strategy make sense, or is it data-mined noise?).

---

## 9. Strategy Templates and Factory Patterns

### Why Templates Matter

Templates eliminate boilerplate and enforce consistency. A researcher should go from "I have an idea" to "I have a testable MVS" in under 30 minutes by filling in a template.

### Strategy Template Architecture

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Optional
import pandas as pd
import numpy as np

@dataclass
class StrategyConfig:
    """Configuration that every strategy must specify."""
    name: str
    version: str
    instruments: list[str]
    timeframe: str  # '1m', '5m', '1h', '4h', '1d'
    lookback_periods: int
    holding_period: int  # expected holding period in bars
    max_position_size: float
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None

class BaseStrategy(ABC):
    """Base class enforcing the strategy interface."""

    def __init__(self, config: StrategyConfig):
        self.config = config

    @abstractmethod
    def compute_features(self, data: pd.DataFrame) -> pd.DataFrame:
        """Compute strategy-specific features from raw OHLCV data."""
        pass

    @abstractmethod
    def generate_signal(self, features: pd.DataFrame) -> pd.Series:
        """Generate trading signal from features. Returns -1, 0, +1."""
        pass

    def backtest(self, data: pd.DataFrame, include_costs: bool = False) -> Dict:
        """Standard backtest pipeline."""
        features = self.compute_features(data)
        signals = self.generate_signal(features)
        returns = self._compute_returns(data, signals, include_costs)
        return self._compute_metrics(returns)

    def _compute_returns(self, data, signals, include_costs):
        raw_returns = data['close'].pct_change() * signals.shift(1)
        if include_costs:
            trades = signals.diff().abs()
            costs = trades * 0.001  # 10bps round-trip
            raw_returns -= costs
        return raw_returns.dropna()

    def _compute_metrics(self, returns: pd.Series) -> Dict:
        sharpe = returns.mean() / returns.std() * np.sqrt(365 * 24)
        cumulative = (1 + returns).cumprod()
        max_dd = (cumulative / cumulative.cummax() - 1).min()
        win_rate = (returns > 0).sum() / (returns != 0).sum()
        profit_factor = returns[returns > 0].sum() / abs(returns[returns < 0].sum())
        return {
            'sharpe': sharpe,
            'max_drawdown': max_dd,
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'n_trades': (signals.diff() != 0).sum(),
            'annual_return': returns.mean() * 365 * 24,
        }
```

### Strategy Factory

```python
class StrategyFactory:
    """Registry and instantiation of strategy classes."""

    _registry: Dict[str, type] = {}

    @classmethod
    def register(cls, name: str):
        def decorator(strategy_cls):
            cls._registry[name] = strategy_cls
            return strategy_cls
        return decorator

    @classmethod
    def create(cls, name: str, config: StrategyConfig) -> BaseStrategy:
        if name not in cls._registry:
            raise ValueError(f"Unknown strategy: {name}")
        return cls._registry[name](config)

    @classmethod
    def list_strategies(cls) -> list[str]:
        return list(cls._registry.keys())

# Usage:
@StrategyFactory.register('mean_reversion_zscore')
class ZScoreMeanReversion(BaseStrategy):
    def compute_features(self, data):
        data['zscore'] = (data['close'] - data['close'].rolling(self.config.lookback_periods).mean()) \
                         / data['close'].rolling(self.config.lookback_periods).std()
        return data

    def generate_signal(self, features):
        signal = pd.Series(0, index=features.index)
        signal[features['zscore'] < -2] = 1   # buy when oversold
        signal[features['zscore'] > 2] = -1   # sell when overbought
        return signal
```

### Reusable Component Library

Build a library of composable components that strategies can mix and match:

| Component | Purpose | Examples |
|-----------|---------|----------|
| **Signal generators** | Convert features to directional signals | Threshold crossover, z-score, percentile rank |
| **Feature extractors** | Compute predictive features from raw data | RSI, MACD, volume profile, funding rate, order book imbalance |
| **Position sizers** | Convert signals to position sizes | Fixed, Kelly, volatility-scaled, risk-parity |
| **Risk filters** | Override signals when risk is elevated | Drawdown circuit breaker, volatility filter, correlation filter |
| **Execution models** | Simulate realistic execution | Market order with slippage, TWAP, VWAP |
| **Performance analyzers** | Compute and visualize metrics | Sharpe, drawdown chart, monthly returns heatmap |

---

## 10. Research Infrastructure for Speed

### The Infrastructure Tax

A common finding: quant researchers spend 80% of their time on data plumbing and infrastructure, and only 20% on actual research. Inverting this ratio is the single biggest lever for faster iteration.

### Core Infrastructure Components

#### Data Layer

| Component | Purpose | Implementation |
|-----------|---------|----------------|
| **Data lake** | Central storage for all market data | Apache Iceberg on S3, Databricks Lakehouse, or TimescaleDB |
| **Data ingestion** | Automated collection from exchanges | CCXT-based collectors with deduplication and validation |
| **Data quality** | Detect gaps, outliers, and errors | Automated checks on ingestion: gap detection, OHLCV consistency, volume sanity |
| **Point-in-time database** | Prevent lookahead bias | Store data with ingestion timestamps; query as-of any historical point |

#### Feature Store

| Component | Purpose | Implementation |
|-----------|---------|----------------|
| **Feature registry** | Catalog of all computed features | Metadata store with feature definitions, dependencies, and freshness |
| **Feature computation** | Batch and streaming feature pipelines | Prefect/Dagster for batch; Flink/Kafka for streaming |
| **Feature serving** | Fast access for backtesting and live trading | Redis for live; Parquet files for batch backtesting |

A well-designed feature store means that when a new strategy idea needs "20-period rolling volatility for BTC," it is already computed, validated, and available -- not recomputed from scratch.

#### Backtesting Engine

Two modes are needed:

1. **Fast vectorized backtester** (Gate 1): Numpy/pandas-based. Processes years of hourly data in seconds. Sacrifices realism for speed.
2. **Event-driven backtester** (Gate 2+): Simulates order book interaction, slippage, partial fills, and latency. Slower but realistic.

Key performance targets:
- Vectorized backtest: < 5 seconds for 2 years of hourly data on one instrument.
- Event-driven backtest: < 5 minutes for 2 years of hourly data on one instrument.
- Backtesting-as-a-service: API endpoint that accepts a strategy config and returns results without the researcher managing infrastructure.

#### Experiment Tracking

Track every backtest run for DSR correction and reproducibility:

```python
experiment_log = {
    'strategy_id': 'STRAT-042',
    'timestamp': '2026-02-15T10:30:00Z',
    'parameters': {'lookback': 50, 'threshold': 2.0},
    'data_range': '2023-01-01 to 2025-12-31',
    'instrument': 'BTC-USDT',
    'results': {'sharpe': 1.4, 'max_dd': -0.18, 'n_trades': 142},
    'gate': 1,
    'verdict': 'PASS'
}
```

Use MLflow, Weights & Biases, or a simple JSONL file. The key is that nothing is lost.

---

## 11. Parallelizing Research

### Why Parallelize

If a single strategy takes 2 weeks from idea to Gate 4, and the kill rate is 95%, you need to test 20 ideas to find 1 winner. Sequential testing means 40 weeks to find one live strategy. Parallel testing with 4 threads means 10 weeks.

### Parallelization Strategies

#### Strategy-Level Parallelism

Run multiple independent strategy research tracks simultaneously:

```
Week 1:
  Thread A: MVS for momentum strategy       --> Gate 1
  Thread B: MVS for mean-reversion strategy  --> Gate 1
  Thread C: MVS for funding rate strategy    --> Gate 1
  Thread D: MVS for volume profile strategy  --> Gate 1

Week 2:
  Thread A: Gate 2 deep validation (momentum passed Gate 1)
  Thread B: [KILLED at Gate 1, start new idea]
  Thread C: Gate 2 deep validation (funding rate passed Gate 1)
  Thread D: [KILLED at Gate 1, start new idea]
```

#### Compute-Level Parallelism

- **Python multiprocessing** for running multiple backtests in parallel (bypass the GIL).
- **Cloud burst** for Gate 3 multi-instrument validation (spin up N workers, one per instrument).
- **GPU acceleration** for ML-heavy feature computation and Monte Carlo simulations.

#### Practical Limits

- Each parallel thread needs an independent researcher context (or well-automated tooling).
- Shared infrastructure (data layer, feature store) must handle concurrent access.
- Resource allocation: total compute budget should be allocated 70% to early gates (high throughput screening) and 30% to late gates (deep validation of survivors).

### Optimal Thread Count

The optimal number of parallel research threads depends on:
- Team size (1 researcher can manage 2-3 parallel tracks with good tooling).
- Compute budget (each track needs backtesting capacity).
- Data availability (are there enough uncorrelated ideas to parallelize?).

A solo researcher with good infrastructure should target 2-3 parallel tracks. A small team of 3-5 researchers should target 6-10 parallel tracks.

---

## 12. Research Sprints and Time-Boxing

### The Quant Research Sprint

Adapted from agile methodology to quant research:

**Sprint Duration:** 1 week (Mon-Fri).

**Sprint Structure:**
- **Monday AM (1 hour):** Sprint planning. Review idea backlog. Select 3-5 ideas for Gate 0/1 screening. Select 1-2 ideas for Gate 2+ advancement.
- **Monday PM - Thursday:** Execute. Each idea gets its time-boxed budget. Kill or advance at each gate.
- **Friday AM (2 hours):** Sprint review. Log all results (kills, advances, learnings). Update idea graveyard. Groom the backlog.
- **Friday PM:** Infrastructure improvement. Fix the bottleneck that slowed you down most this week.

**Sprint Metrics:**
- Ideas screened (Gate 0-1): target 3-5 per sprint.
- Ideas advanced (Gate 1 to Gate 2): target 0-2 per sprint.
- Ideas killed: target 2-4 per sprint (killing is progress).
- Average time per Gate 1 screen: target < 4 hours.
- Infrastructure improvement delivered: at least 1 per sprint.

### Time-Boxing Rules

1. **Hard time boxes are non-negotiable.** When the timer expires, the default is KILL, not "extend."
2. **Document why the time box was exceeded** (if it happens). This reveals infrastructure gaps.
3. **A time box that is consistently exceeded** means the gate is too expensive. Simplify the gate or improve the tooling.
4. **Never let sunk cost extend a time box.** "I already spent 3 hours" is not a reason to spend 3 more.

### Quant Fund Research Team Structure

Top quant funds organize research into small, independent teams:
- **Pod structure:** Small groups (2-5 people) of quant researchers, developers, and traders operating as semi-autonomous units.
- **Intrapreneurial model:** Each pod has its own P&L and research agenda.
- **Portfolio of strategies:** A successful fund needs strategies across different asset classes, timeframes, and style factors (momentum, mean-reversion, carry, statistical arbitrage).
- **Risk integration:** Risk management is embedded in the research process, not bolted on at the end.

---

## 13. Idea Backlog and Prioritization

### Backlog Structure

```
IDEA BACKLOG
============

[HIGH PRIORITY - Test this sprint]
- IDEA-047: Liquidation cascade momentum (Source: on-chain data analysis)
  Expected edge: High. Data available: Yes. Novelty: High.
  Priority score: 8.5/10

- IDEA-051: Cross-exchange basis convergence (Source: live observation)
  Expected edge: Medium. Data available: Yes. Novelty: Medium.
  Priority score: 7.0/10

[MEDIUM PRIORITY - Test next sprint]
- IDEA-044: Whale wallet tracking signal (Source: academic paper)
  ...

[LOW PRIORITY / PARKING LOT]
- IDEA-039: Sentiment NLP on crypto Twitter
  ...

[GRAVEYARD - Tested and killed]
- IDEA-042: Funding rate mean-reversion (killed Gate 1, IC = 0.008)
- IDEA-040: Open interest divergence (killed Gate 2, failed walk-forward)
  ...
```

### Prioritization Criteria

Use a weighted scoring model:

| Factor | Weight | Scale |
|--------|--------|-------|
| Expected edge strength | 30% | 1-10 based on economic rationale and analogous evidence |
| Data availability | 20% | 1-10 (10 = data ready now, 1 = need months to acquire) |
| Novelty / low crowding | 20% | 1-10 (10 = never seen before, 1 = well-known factor) |
| Implementation complexity | 15% | 1-10 (10 = trivial, 1 = requires major infrastructure) |
| Diversification value | 15% | 1-10 (10 = uncorrelated with everything, 1 = duplicate of existing) |

### Idea Sources

Maintain a diverse pipeline of idea sources:
- **Academic literature:** New papers on market microstructure, factor investing, statistical arbitrage.
- **Market observation:** Patterns noticed during live trading or market monitoring.
- **On-chain data:** Blockchain-specific signals (whale movements, DEX flows, liquidation levels).
- **Cross-market analogy:** Strategies that work in equities/FX adapted to crypto.
- **Automated generation:** GP/LLM-generated candidates (see Section 8).
- **Post-mortem analysis:** Insights from killed strategies ("the signal was there but on a different timeframe").

### Backlog Hygiene

- **Review weekly** during sprint planning.
- **Remove stale ideas** that have sat for > 4 weeks without being tested (if they were important, they would have been tested).
- **Re-prioritize after kills:** A killed strategy may reveal that a related idea is more or less promising.
- **Limit backlog size:** Maximum 20-30 ideas. Beyond this, ideas are too stale to be useful.

---

## 14. Measuring and Minimizing Cycle Time

### Key Cycle Time Metrics

| Metric | Definition | Target |
|--------|-----------|--------|
| Idea-to-Gate-1 | Time from idea entry to first backtest result | < 4 hours |
| Gate-1-to-Gate-2 | Time from signal screen to deep validation complete | < 2 days |
| Gate-2-to-Gate-3 | Time from single-instrument to multi-instrument validation | < 5 days |
| Gate-3-to-Gate-4 | Time from validation to paper trading start | < 1 day |
| Gate-4-to-Live | Paper trading duration | 1-4 weeks |
| Idea-to-Kill | Average time to reject a bad idea | < 4 hours (if killed at Gate 1) |
| Idea-to-Live | Total time for a successful strategy | 4-8 weeks |
| Research throughput | Ideas screened per week | 3-5 |
| Kill rate | Percentage of ideas killed | 90-97% |
| Win rate | Percentage of live strategies that remain profitable after 3 months | > 50% |

### Identifying Bottlenecks

Track where time is actually spent:

```
Typical time breakdown (before infrastructure investment):
  Data acquisition and cleaning:    40%  <-- THE BOTTLENECK
  Feature engineering:               20%
  Backtesting:                       15%
  Analysis and decision:             15%
  Documentation and logging:         10%

Target time breakdown (after infrastructure investment):
  Data acquisition and cleaning:     5%   (automated, feature store)
  Feature engineering:              15%   (reusable components)
  Backtesting:                       5%   (fast engine, templates)
  Analysis and decision:            50%   (THE ACTUAL RESEARCH)
  Documentation and logging:        10%   (automated experiment tracking)
  Infrastructure improvement:       15%   (continuous investment)
```

### Continuous Improvement Loop

1. **Measure** cycle times for every strategy that passes through the pipeline.
2. **Identify** the stage that took the longest (relative to its time budget).
3. **Invest** Friday PM infrastructure time in improving that stage.
4. **Repeat** weekly.

### The Speed Advantage

In quant research, speed compounds:
- Faster iteration = more ideas tested per unit time.
- More ideas tested = higher probability of finding an edge.
- Finding edges faster = deploying capital before the signal is crowded.
- The half-life of alpha is shrinking (signals get crowded faster). Speed is a moat.

---

## Appendix A: Quick Reference -- Kill Decision Checklist

```
GATE 0 (5 min):
  [ ] Economic rationale exists?
  [ ] No lookahead bias by construction?
  [ ] Not in the idea graveyard?
  [ ] Sufficient data for statistical significance?

GATE 1 (2-4 hours):
  [ ] Mean IC > 0.02?
  [ ] IC t-stat > 2.0?
  [ ] Gross Sharpe > 1.0?
  [ ] Max drawdown < 30%?
  [ ] Number of trades > 50?
  [ ] Profit factor > 1.3?

GATE 2 (1-2 days):
  [ ] Net Sharpe (after costs) > 0.8?
  [ ] DSR p-value < 0.05?
  [ ] Walk-forward consistency > 60%?
  [ ] Parameter sensitivity acceptable?
  [ ] Calmar ratio > 0.5?

GATE 3 (3-5 days):
  [ ] Cross-instrument hit rate > 60%?
  [ ] Portfolio Sharpe improvement > 0.3?
  [ ] Correlation with existing strategies < 0.5?
  [ ] Sufficient capacity?
  [ ] Survives stress tests?

GATE 4 (1-4 weeks):
  [ ] Live vs backtest Sharpe > 0.6x?
  [ ] SPRT accepts H1?
  [ ] Operationally reliable?
  [ ] Fill quality acceptable?
```

## Appendix B: Tools and Frameworks Reference

| Category | Tool | Use Case |
|----------|------|----------|
| Backtesting | Vectorbt, Backtrader, custom numpy | Fast vectorized and event-driven backtesting |
| Optimization | Optuna | Bayesian hyperparameter optimization |
| Experiment tracking | MLflow, W&B, JSONL logs | Track all trials for DSR correction |
| Data | CCXT, Databricks, TimescaleDB | Market data collection and storage |
| Feature store | Feast, custom Parquet store | Reusable computed features |
| Strategy generation | StrategyQuant, GeneTrader, DEAP | Genetic programming for automated strategy search |
| Statistical testing | scipy.stats, custom SPRT | Sequential testing and hypothesis testing |
| Validation | Custom CPCV, walk-forward | Out-of-sample validation with purging |
| Visualization | Plotly, matplotlib, Streamlit | Strategy performance dashboards |

## Appendix C: References and Sources

- Bailey, D. & Lopez de Prado, M. (2014). "The Deflated Sharpe Ratio." Journal of Portfolio Management.
- Lopez de Prado, M. (2018). "Advances in Financial Machine Learning." Wiley.
- Lopez de Prado, M. (2018). "A Data Science Solution to the Multiple-Testing Crisis in Financial Research." SSRN.
- Lopez de Prado, M. (2025). "Sharpe Ratio Inference: A New Standard." SSRN.
- Wald, A. "Sequential Analysis." Dover Publications.
- Pardo, R. (1992). "Design, Testing and Optimization of Trading Systems."
- Arian, H., Norouzi, D., & Seco, L. (2024). "Backtest Overfitting in the ML Era: A Comparison of OOS Testing Methods."
- QuantEvolve (2025): Multi-agent evolutionary framework for strategy generation. arXiv:2510.18569.
- Deutsche Bank (2014). "Seven Sins of Quantitative Investing." Global Quantitative Strategy.
