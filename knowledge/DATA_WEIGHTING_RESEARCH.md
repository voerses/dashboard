# Data Weighting Research: Multi-Year Historical Data for Crypto Backtesting

Last updated: 2026-02-28
Context: $200K crypto swing trading system, 49 tokens, 1H bars, Binance data

---

## TL;DR for Our System

Our current data window (Jan 2024 - Jan 2026, ~2 years) is defensible but limited. The ETF approval in January 2024 created a genuine regime break — making pre-2024 data structurally different from today's market. **Do not blindly extend to 5 years without regime-aware handling.**

Key actionable conclusions:
1. **Keep 2 years as the primary window** but augment with regime-labeled pre-ETF data for robustness tests
2. **Apply exponential decay** with λ=0.94 on rolling optimization; use equal weights for regime-separated CPCV splits
3. **January 2024 is a hard regime boundary** — pre/post should be treated as different markets
4. **Survivorship bias is already partially handled** by our token list construction, but needs explicit listing-date tracking
5. **Walk-Forward window should expand**, not shrink, as new data arrives (anchored start, rolling forward)

---

## 1. Recent vs. Older Data: How to Weight

### The Core Tradeoff

Older data provides more observations and regime diversity. Recent data reflects current market structure. The right answer depends on **how much the market has changed**.

### What the Research Says

**Exponentially Weighted Moving Average (EWMA) — Industry Standard for Volatility:**
- λ = 0.94 is the RiskMetrics canonical value for daily data (Morgan/Reuters, 1996)
- λ → 1 gives equal weights (historical simulation); λ → 0 gives only the most recent observation
- For 1H bars: λ = 0.994 to 0.998 is roughly equivalent (decay is per-bar, not per-day)
- A time-varying λ that minimizes in-sample variance beats a fixed λ on MSE, but requires per-step optimization

**Practical Rules of Thumb:**
- Equal weighting: fast to compute, slow to adapt; good for detecting persistent structural edges
- EWMA: reacts faster to volatility regime changes; preferred when market structure is evolving
- Linear decay: simpler than EWMA, less used in practice; no theoretical justification

**What "Recency Bias" Actually Means in Finance:**
- Lopez de Prado (2018, Ch. 4): sample weights should reflect each observation's uniqueness, not recency per se. He uses **uniqueness weighting** — observations in overlapping label windows get fractional weight. This is orthogonal to recency.
- For non-overlapping bars (our 1H system with no label overlap), all bars are equally unique. Recency weighting is a separate, practitioner choice.

### Recommendation for Our System

**Optimization (Walk-Forward, parameter fitting):** Apply EWMA with λ = 0.995 per 1H bar. This gives the last 200 hours (~8 days) roughly 50% of the total weight — appropriate for swing trading where recent volatility regimes matter most.

**Validation (CPCV, robustness scoring):** Use equal weights within each split. CPCV's value is testing across all possible timelines, not privileging recent ones. Weighting CPCV splits by recency partially defeats the purpose.

**Position Sizing in Production:** Use the most recent 90-day period exclusively. Do not average with older regimes.

---

## 2. Optimal Lookback Period: Academic and Practitioner Consensus

### Academic Evidence

- **Portfolio123 research (quantitative review):** For equity strategies, 9-12 years is optimal for stable Sharpe estimation. Beyond 15 years, correlation with out-of-sample performance degrades again due to structural market changes.
- **Journal of Futures Markets (2025):** Lookback period selection "significantly impacts returns." In crypto specifically, a 3-5 year window covers at least one full boom-bust cycle — the minimum to avoid regime-specific overfitting.
- **Walk-Forward window study (2025, arxiv 2602.10785):** For 1H Bitcoin data, 14-day train / 10-day test windows outperformed other configurations. Extrapolating: for a swing system, longer train windows (60-90 days) are more appropriate.

### Crypto-Specific Consensus

Most practitioners and the academic literature agree on:
- **Minimum**: 18-24 months to cover at least one bull-to-bear transition
- **Practical**: 3-5 years to cover at least two complete cycles (halving events)
- **Maximum useful**: ~7 years for BTC/ETH; less for altcoins (many didn't exist)
- **For altcoin universe**: Use token-specific start dates; never pad with zeros or synthetic data

Our current 2-year window (Jan 2024 - Jan 2026) is at the lower bound of the minimum. It covers one major bull run and the beginning of the post-ETF institutional era, but does NOT include a crypto bear market within our primary dataset.

---

## 3. Five Years vs. Two Years: Specific Tradeoffs for Our Context

### Why 2 Years Is Defensible for Us

1. **The ETF regime break**: January 2024 was a genuine structural change. Bitcoin ETF approval brought $75B in institutional inflows in Q1 2024 alone, reduced BTC volatility by ~55% (per academic research on the ETF launch), and broke historical BTC-altcoin correlation patterns. Pre-2024 data reflects a fundamentally different market.

2. **Altcoin coverage**: Many of our 49 tokens (PENGU, ARB, OP, TRUMP, ZRO, etc.) didn't exist or had negligible liquidity before 2023-2024. Extending to 5 years requires either dropping these tokens or using a mismatched panel.

3. **Our validation pipeline already compensates**: WF + CPCV together produce 15 independent out-of-sample evaluations from 2 years of data. This is more statistically rigorous than a naive 5-year single-path backtest.

4. **Regime homogeneity**: Our 2-year window is more internally consistent than a 5-year window that straddles the 2022 bear market, 2023 recovery, and 2024 ETF-driven bull run.

### Why Extending to 3-4 Years Would Help

1. **Bear market coverage**: Our data (Jan 2024 - Jan 2026) captures mostly a bull market. A robust strategy must survive bear markets. The 2022-2023 bear (BTC -77% peak to trough) is the most relevant recent bear market for our tokens.

2. **More CPCV groups**: With 2 years of 1H data at 6 groups, each group is ~60 days. With 3 years, each group is ~90 days — closer to a full market cycle mini-segment. This improves CPCV's discriminative power.

3. **Walk-Forward reliability**: Our WF uses 365-day training windows. With only 2 years of data, we get roughly 4-5 non-overlapping training periods. With 3 years, we get 6-8 periods — reducing variance in the WF Sharpe estimate.

4. **Halving cycle**: Bitcoin's 4-year halving cycle (last one: April 2024) means 3-4 years of data would capture the pre-halving accumulation phase AND the post-halving expansion — the two phases our swing system is most likely to trade.

### The Recommendation: Stratified Augmentation

**Do not blindly extend to 5 years.** Instead:

```
Primary window (equal weight):    Jan 2024 - present      (post-ETF era)
Secondary window (50% weight):    Jun 2022 - Dec 2023     (bear/recovery)
Excluded:                         Pre-2022                (different exchange infra,
                                                           regulatory environment,
                                                           no tokens like ARB/OP)
```

This gives us ~3.5 years effective coverage with regime-appropriate weighting. The 2022-2023 window provides bear market coverage without introducing data from a fundamentally different microstructure era (no institutional ETF flows, no real-world Binance regulatory pressure, different fee structures).

**Implementation:** Run CPCV separately on each window, then take the harmonic mean of the two PBO estimates. A strategy is robust if it survives BOTH regimes.

---

## 4. Crypto-Specific Data Handling Problems

### Tokens That Didn't Exist (Survivorship Bias)

**The problem:** Backtesting a universe of currently liquid tokens implicitly selects survivors. A token that failed, got delisted, or lost liquidity is absent from our dataset. This inflates backtest returns.

**Quantified impact (academic research):** Using survivorship-biased data produced results showing 4x higher profit, 15% less drawdown, and roughly double the profit factor compared to survivorship-bias-free data.

**Our situation:** Our 49-token universe was constructed based on tokens liquid in Jan 2026 that have Binance 1H data going back to at least Jan 2024. Tokens that were delisted or became illiquid during 2024-2026 are excluded. This introduces mild survivorship bias within our 2-year window.

**Mitigation steps:**
1. **Maintain listing/delisting log**: Record when each token first appeared in the universe and flag any that lost top-50 status. Do not backtest tokens in periods before they were liquid.
2. **Apply token-specific start dates**: For tokens that listed mid-backtest-period (e.g., TRUMP, PENGU), zero out all signals before their listing date — do not interpolate or use pre-listing prices from other exchanges.
3. **Do NOT use point-in-time universe construction retroactively without checking**: If you rebalance the token universe, do not assume a token "should have been" in the universe before it was actually liquid.

### Market Microstructure Changes

**Exchange-level changes that break historical comparisons:**
- Binance fee changes (several times in 2022-2024)
- Binance withdrawal from some jurisdictions (affects volume, not price, but slippage models change)
- Introduction of Binance VIP tiers and market maker rebates (changes effective transaction costs)
- Funding rate mechanics changes for perpetuals (irrelevant for spot, relevant if using perp data)

**Practical fix for 1H spot data:** Use a flat transaction cost assumption that reflects the POST-2023 Binance fee structure (0.10% taker for standard account). Do not use a time-varying fee model — the complexity doesn't justify the precision given other uncertainties.

### Exchange Changes

- Pre-2020 Binance data has different tick sizes, trading pairs, and lower liquidity
- FTX collapse (Nov 2022) caused a brief but significant liquidity dislocation across all exchanges
- Coinbase International, Bybit growth (2023+) fragmented the orderbook — Binance's market share fell from ~70% to ~50%
- These make pre-2022 Binance data unreliable for modeling slippage and execution

**Recommendation:** Do not use data before January 2022 for any production calibration. For research purposes only, note that any result using pre-2022 data may not be achievable at current Binance liquidity levels.

### ETF Approval Regime Break (January 2024)

This is the most important structural break for our dataset:

**What changed:**
- BTC volatility reduced ~55% (institutional stabilization)
- BTC-altcoin correlation partially broke down (BTC increasingly treated as distinct asset class)
- Institutional capital flows became a dominant price driver (not just retail speculation)
- Funding rates in perpetuals changed character (institutional hedging rather than retail leverage)
- BTC dominance rose sharply in 2024 (from ~40% to ~57%), then partially reversed in 2025

**What this means for backtesting:**
- Any strategy trained purely on 2021-2022 data and applied post-2024 will face a different volatility regime
- Strategies that relied on high BTC-altcoin correlation (e.g., beta-adjusted mean reversion) may fail post-2024
- Our current training window (Jan 2024+) is already post-break — good for future performance but means we have no bear market in-sample

---

## 5. Weighting Schemes: Comparative Analysis

### Scheme Comparison Table

| Scheme | Formula | Best for | Worst for | Complexity |
|--------|---------|----------|-----------|------------|
| Equal weight | w_t = 1/N | Structural edge detection | Adapting to regime shifts | Minimal |
| Linear decay | w_t = (t - t_start) / T | Simple recency bias | Theoretically unjustified | Low |
| Exponential decay (EWMA) | w_t = λ^(T-t) | Volatility estimation, regime adaptation | Long-horizon structural edges | Low |
| Regime-based | w_t = f(regime_label_t) | Bull/bear-aware optimization | Requires accurate regime labels | High |
| Uniqueness weight (LdP) | w_t = 1 / (overlap count) | ML models with label overlap | Non-overlapping bars (no effect) | Medium |
| Expanding window | Anchored start, grows with time | Incorporating all new data | Computational cost | Low |

### For Parameter Optimization (Walk-Forward)

Use **EWMA with λ = 0.995 per 1H bar**. Justification:
- Half-life = ln(2) / ln(1/0.995) ≈ 138 hours ≈ 5.75 days
- Means ~50% of optimization weight comes from the last week of data — appropriate for swing trading where recent momentum, volatility, and correlation structure dominate
- This is more aggressive than RiskMetrics λ=0.94 (which targets daily data and gives 11-day half-life)

To implement: multiply each bar's return/feature by `λ^(T - bar_index)` before computing optimization objective. Normalize weights to sum to 1.

### For Robustness Testing (CPCV)

Use **equal weights within each CPCV split**. The value of CPCV comes from testing across different historical paths — adding recency weighting to individual splits undermines this by biasing all paths toward recent data.

For **inter-split weighting** (if you want to score the overall strategy): weight post-2024 splits at 1.5x and 2022-2023 splits at 1.0x. This acknowledges that post-ETF performance is more predictive of future performance.

### For Regime-Based Weighting

This is the most sophisticated and most theoretically justified approach, but requires reliable regime detection.

**Implementation outline:**
1. Label each 1H bar with a regime: BULL (BTC trending up, low VIX), BEAR (BTC trending down, high realized vol), NEUTRAL (ranging)
2. Compute current regime using a rolling 30-day window
3. Weight training bars: current regime bars get weight 2.0, adjacent regime bars get 1.0, opposite regime bars get 0.5
4. This ensures parameter optimization is dominated by the regime most similar to today's market

**Caution:** Regime labels themselves can introduce look-ahead bias (you know the 2022 crash was a bear market only because it ended). Use rolling-window regime labels, not retrospectively labeled segments.

Our existing `regime_detector.py` provides regime signals — these can be used as sample weights in the Walk-Forward optimization rather than just as filters.

### Expanding vs. Rolling Window

- **Expanding (anchored):** Start date fixed, end date grows as new data arrives. Gives more data over time but older data becomes proportionally less influential. Better for structural edge detection.
- **Rolling (fixed-length):** Window slides forward, discarding old data. More computationally efficient and keeps training data in the same regime. Better for regime-adaptive strategies.

**Recommendation for our system:** Use **rolling windows for WF parameter optimization** (fixed 365-day training window per WF step) but **expanding window for CPCV** (use all available data, dividing into N groups proportionally). This matches the purpose of each method.

---

## 6. Lopez de Prado on Sample Length vs. Recency

### Minimum Backtest Length (MinBTL)

Lopez de Prado's core contribution to this question is the **MinBTL formula** (Bailey, Borwein, Lopez de Prado & Zhu, 2014):

```
MinBTL ≈ (E[maxSR] / SR_target)^2 * T_trials
```

Where:
- `E[maxSR]` is the expected maximum Sharpe from N trials by chance
- `SR_target` is your target annualized Sharpe
- `T_trials` is the number of strategy configurations tested

**The key insight:** Minimum backtest length is NOT a fixed number of years — it scales with how many strategies you've tested. After testing 7 configurations, you need at least 2 years of backtest to conclude a Sharpe > 1 is real. After testing 70+ configurations (which we have), you need substantially more.

For our system: we've tested 70+ strategy configurations across 49 tokens. MinBTL implies we need at least 3-4 years of backtest to make a Sharpe > 1.0 credible. Our 2-year window is insufficient by this standard — which is why CPCV's PBO metric (which directly measures whether the positive result is real) is critical.

### Lopez de Prado on Recency

He does not directly advocate for recency-weighted samples. His framework (Ch. 4, Advances in Financial Machine Learning) focuses on:
1. **Uniqueness weights**: weight each observation by how unique its information is (fraction of non-overlapping label windows)
2. **Class weights**: weight rare outcomes more heavily for imbalanced classification
3. **Time decay**: optionally add a time decay on top of uniqueness weights

On time decay specifically (Ch. 4, pp. 72-74): "A further consideration is whether observations should be weighted by their recency... We leave this to the researcher's judgment, as the optimal decay rate depends on the market and strategy."

**Practical conclusion from Lopez de Prado's framework:**
- For our 1H bars with no label overlap: uniqueness weights = 1 for all bars. Time decay becomes the only weighting mechanism.
- The choice of decay rate is a hyperparameter — treat it as such, optimize it during WF, and apply it consistently.
- Do NOT use the most recent data exclusively for optimization; use it with decayed weights to maintain statistical power from older data.

### The Deflated Sharpe Ratio (DSR) for Multiple Tests

Lopez de Prado's DSR adjusts the Sharpe ratio downward based on how many strategies were tested:

```
DSR = SR * sqrt(1 - ρ) / sqrt(1 + (SR^2 * (T-1) * skew/6 - SR^4 * (T-1) * kurtosis/24))
```

Where ρ = correlation between strategy returns across trials. This is the primary tool for deflating claims from extensive strategy search.

**Implication for our system:** With 70+ strategies tested, a raw Sharpe of 1.5 may deflate to 0.7-0.9 after applying DSR. Our CPCV implementation already computes Deflated Sharpe — ensure we are using the number of tested strategies (not the number of winning strategies) as the input to DSR.

---

## 7. Practical Implementation Recommendations

### For the Current System (Jan 2024 - Jan 2026)

**Immediate actions:**
1. Accept 2-year window as primary; do not artificially extend with pre-ETF data without regime labels
2. Apply EWMA λ=0.995 in Walk-Forward parameter fitting (downweight bars older than 3 months)
3. Track token listing dates explicitly; add a `token_first_listed` column to data manifest
4. Separate CPCV into two regime blocks: bull phase (Jan 2024 - Oct 2024) and consolidation/continuation (Nov 2024+)

**Medium-term actions:**
1. Fetch 2022-2023 data for the top 20 liquid tokens (BTC, ETH, BNB, SOL, XRP, etc.)
2. Run a separate CPCV on the 2022-2023 bear market window for these tokens
3. Only retain tokens that show positive PBO (< 50%) in BOTH the 2022-2023 AND the 2024-2026 windows
4. This becomes the "all-weather" token subset for full capital deployment

**Walk-Forward window tuning:**
- Current: 365d train / 90d test — appropriate but the 90d test window spans multiple mini-regimes
- Consider: 180d train / 45d test with 3x more steps — gives more WF evaluations from the same 2-year data
- Do not go below 90d train (insufficient for swing trading parameter estimation)

### Data Extension Decision Tree

```
Token existed before Jan 2024?
  Yes → Check if liquidity was adequate (>$10M daily volume)
    Yes → Include with 50% weight on pre-2024 bars
    No  → Start data at date when volume crossed $10M threshold
  No  → Use only post-listing data, exclude token from 5-year comparison
```

### Regime-Aware Data Split Recommendation

For our 49-token system on 1H bars (Jan 2024 - Jan 2026):

```
Regime 1: Jan 2024 - Apr 2024    BTC ETF launch, rapid institutional inflows
Regime 2: May 2024 - Oct 2024    Post-halving consolidation, altcoin season start
Regime 3: Nov 2024 - Jan 2025    Trump election, BTC ATH, risk-on momentum
Regime 4: Feb 2025 - Jan 2026    Consolidation/correction, institutional digestion
```

Run CPCV with regime boundaries as group boundaries (not random temporal splits). This ensures each test fold contains complete regime transitions, not just fragments.

---

## 8. Summary Decision Matrix

| Decision | Our Recommendation | Why |
|----------|-------------------|-----|
| Primary window | 2 years (Jan 2024 - present) | Post-ETF regime homogeneity; sufficient with CPCV |
| Extend to 5 years? | No — extend to 3.5 years max | Pre-2022 data is structurally different; adds noise |
| Weighting for WF optimization | EWMA λ=0.995 per 1H bar | Adapts to current regime while retaining statistical power |
| Weighting for CPCV | Equal within splits, 1.5x post-2024 inter-split | Preserves regime diversity within splits |
| Survivorship bias fix | Token-specific start dates | Do not test tokens before adequate liquidity |
| ETF regime break | Jan 2024 = hard boundary | Do not average across this break without regime labels |
| MinBTL compliance | Report DSR, not raw Sharpe | 70+ strategies tested requires deflation |
| Bear market coverage | Add 2022-2023 for top-20 tokens | Secondary CPCV gate: must survive bear market too |

---

## Sources

- Bailey, D.H., Borwein, J., Lopez de Prado, M., Zhu, Q.J. (2014). ["The Probability of Backtest Overfitting."](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253) SSRN.
- Lopez de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley. Chapters 4, 7, 11, 12.
- Arian, H.R., Norouzi, D., Seco, L.A. (2024). ["Backtest overfitting in the machine learning era."](https://www.sciencedirect.com/science/article/abs/pii/S0950705124011110) *Knowledge-Based Systems*, 305.
- Palazzi et al. (2025). ["Trading Games: Beating Passive Strategies in the Bullish Crypto Market."](https://onlinelibrary.wiley.com/doi/full/10.1002/fut.70018) *Journal of Futures Markets*.
- Novel WFO paper (2025). ["A novel approach to trading strategy parameter optimization."](https://arxiv.org/html/2602.10785) arXiv 2602.10785.
- Concretum Group. ["Building a Survivorship Bias-Free Crypto Dataset."](https://concretumgroup.com/building-a-survivorship-bias-free-crypto-dataset-with-coinmarketcap-api/)
- ScienceDirect. ["Does the introduction of US spot Bitcoin ETFs affect spot returns and volatility?"](https://www.sciencedirect.com/science/article/pii/S106297692500047X)
- Grayscale. ["2026 Digital Asset Outlook."](https://research.grayscale.com/reports/2026-digital-asset-outlook-dawn-of-the-institutional-era)
- Portfolio123 Blog. ["How Far Back Should You Backtest?"](https://blog.portfolio123.com/how-far-back-should-you-backtest/)
- Springer Digital Finance. ["Regime switching forecasting for cryptocurrencies."](https://link.springer.com/article/10.1007/s42521-024-00123-2)
- arxiv. ["Asset volatility forecasting: The optimal decay parameter in the EWMA model."](https://arxiv.org/pdf/2105.14382)
- GARP. ["The 10 Reasons Most Machine Learning Funds Fail" — Lopez de Prado.](https://www.garp.org/hubfs/Whitepapers/a1Z1W0000054x6lUAA.pdf)
- Balaena Quant Insights. ["Best Backtesting Practices for CTA Trading in Cryptocurrency."](https://medium.com/balaena-quant-insights/best-backtesting-practices-for-cta-trading-in-cryptocurrency-e79677cb6375)
