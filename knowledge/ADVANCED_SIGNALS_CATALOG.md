# Advanced Signals Catalog for Systematic Crypto Swing Trading

**Target**: 1H timeframe, 18-720 hour holding periods
**Universe**: 49 liquid tokens (Binance), Jan 2024 - Jan 2026
**Available data**: OHLCV (1H, 4H, daily), 1-minute microstructure features (VPIN, realized_vol, taker_buy_ratio, amihud, intraday_skew/kurtosis, parkinson_vol, etc.)

**Signal type legend**: ENTRY = trade initiation, EXIT = trade closure, FILTER = regime/condition gate, SIZING = position size adjustment, FEATURE = ML model input

---

## 1. STATISTICAL DISTRIBUTION SIGNALS

### Signal 1: Rolling Skewness (20-bar, 60-bar)

- **Formula**: skew(r_t, ..., r_{t-N}) = (1/N) * sum[((r_i - mu)/sigma)^3] over rolling window N
- **Complexity**: O(N) per bar, trivial
- **Causal mechanism**: Negative skewness indicates frequent small gains punctuated by large drops -- a fragile regime where mean-reversion longs are dangerous. Positive skewness suggests upside potential. Skewness predicts future return direction because it captures asymmetric positioning and stop-loss clustering. Empirically, negative skew in crypto precedes further drawdowns (momentum in higher moments).
- **Citation**: Harvey & Siddique (2000), "Conditional Skewness in Asset Pricing Tests", Journal of Finance. Amaya et al. (2015), "Does Realized Skewness and Kurtosis Predict the Cross-Section of Equity Returns?", Journal of Financial Economics.
- **Data requirement**: OHLCV (close-to-close returns). Already have intraday_skew from 1M cache for daily; need to compute on 1H returns for intrabar resolution.
- **Signal type**: FILTER, FEATURE
- **Difficulty**: Easy

### Signal 2: Rolling Kurtosis (20-bar, 60-bar)

- **Formula**: kurt(r_t, ..., r_{t-N}) = (1/N) * sum[((r_i - mu)/sigma)^4] - 3 (excess kurtosis)
- **Complexity**: O(N) per bar, trivial
- **Causal mechanism**: High kurtosis means fat tails are active -- extreme moves in either direction become more likely. Kurtosis spikes precede regime changes because they indicate instability in the return-generating process. Our own data confirms intraday_kurtosis is the best predictor in range markets (IC = -0.072). High kurtosis days predict mean-reversion (large moves overshoot and revert).
- **Citation**: Amaya et al. (2015), JFE. Cont (2001), "Empirical Properties of Asset Returns: Stylized Facts and Statistical Issues", Quantitative Finance.
- **Data requirement**: OHLCV. Already have intraday_kurtosis (daily). Compute 1H rolling version.
- **Signal type**: FILTER, FEATURE
- **Difficulty**: Easy

### Signal 3: Rolling Jarque-Bera Test Statistic

- **Formula**: JB = (N/6) * [S^2 + (1/4)(K-3)^2] where S = skewness, K = kurtosis, over rolling window
- **Complexity**: O(N) per bar (computed from skew + kurt already calculated)
- **Causal mechanism**: JB measures departure from Gaussian returns. High JB indicates the return distribution is non-normal, meaning standard mean-variance assumptions are violated. When JB rises sharply, traditional technical indicators (designed for approximately normal markets) become unreliable. This serves as a meta-signal to switch from standard indicators to fat-tail-aware strategies.
- **Citation**: Jarque & Bera (1980), "Efficient Tests for Normality, Homoscedasticity and Serial Independence of Regression Residuals", Economics Letters.
- **Data requirement**: OHLCV (derived from skew and kurtosis above)
- **Signal type**: FILTER
- **Difficulty**: Easy

### Signal 4: Hurst Exponent (R/S Analysis)

- **Formula**: H = log(R/S) / log(N) where R/S is the rescaled range statistic over window N. For DFA variant: compute fluctuation function F(n) at multiple scales, H = slope of log(F(n)) vs log(n).
- **Complexity**: O(N*log(N)) for R/S; O(N^2) for DFA. Medium computational cost.
- **Causal mechanism**: H < 0.5 indicates mean-reversion (anti-persistent), H = 0.5 is random walk, H > 0.5 indicates trending (persistent). This directly tells you whether momentum or mean-reversion strategies should be deployed. A token with H = 0.65 over the past 100 hours is in a trending regime; one with H = 0.35 is mean-reverting. The Hurst exponent changes over time, creating a dynamic regime indicator.
- **Citation**: Hurst (1951), original. Mandelbrot & Wallis (1969). Di Matteo et al. (2005), "Long-term memories of developed and emerging markets", Journal of Banking & Finance. Bariviera (2017), "The Inefficiency of Bitcoin Revisited", Economics Letters.
- **Data requirement**: OHLCV (close prices). Rolling window of 100-500 bars.
- **Signal type**: FILTER (regime classification)
- **Difficulty**: Medium

### Signal 5: Ornstein-Uhlenbeck Half-Life of Mean Reversion

- **Formula**: Estimate OU process dX = theta*(mu - X)*dt + sigma*dW. Regress X_{t} - X_{t-1} = a + b*X_{t-1} + e. Half-life = -ln(2)/ln(1+b). If b is not significantly negative, mean reversion is absent.
- **Complexity**: O(N) linear regression, trivial
- **Causal mechanism**: The half-life tells you how quickly a deviation from the mean is expected to revert. Short half-life (5-20 bars on 1H) means mean-reversion trades should have tight holding periods. Long half-life (100+ bars) means mean-reversion is too slow to be tradeable at the 1H timeframe. This is a direct estimate of the speed of mean reversion, which determines optimal holding period for MR strategies.
- **Citation**: Uhlenbeck & Ornstein (1930). Practical application: Chan (2013), "Algorithmic Trading: Winning Strategies and Their Rationale", Wiley.
- **Data requirement**: OHLCV (log prices or spread series). Rolling window 100-500 bars.
- **Signal type**: FILTER, SIZING (scale MR positions by inverse of half-life)
- **Difficulty**: Easy

### Signal 6: Hill Tail Index Estimator

- **Formula**: gamma_hat = (1/k) * sum_{i=1}^{k} [ln(X_{(n-i+1)}) - ln(X_{(n-k)})] where X_{(i)} are order statistics and k is the number of tail observations. Tail index alpha = 1/gamma_hat.
- **Complexity**: O(N*log(N)) for sorting, per window
- **Causal mechanism**: The tail index alpha measures how heavy the tails are. For crypto, alpha is typically 2-4 (much fatter than Gaussian alpha = infinity). When alpha drops (fatter tails), extreme events become more probable -- this is a risk signal. A declining Hill estimator over rolling windows warns of increasing tail risk. Combined with directional skew, it can indicate which tail is thickening.
- **Citation**: Hill (1975), "A Simple General Approach to Inference About the Tail of a Distribution", Annals of Statistics. Gabaix et al. (2006), "Institutional Investors and Stock Market Volatility", QJE.
- **Data requirement**: OHLCV (returns). Need 200+ observations for stable estimates.
- **Signal type**: FILTER, SIZING (reduce position when alpha is low)
- **Difficulty**: Medium

### Signal 7: Conditional Value-at-Risk (CVaR / Expected Shortfall)

- **Formula**: CVaR_alpha = E[X | X <= VaR_alpha] = (1/alpha) * integral_0^alpha VaR_u du. Empirical: average of returns below the alpha-quantile.
- **Complexity**: O(N*log(N)) for sorting, per window
- **Causal mechanism**: CVaR measures the expected loss given that you are already in the tail. Unlike VaR, it is coherent (subadditive) and captures tail severity. Rising CVaR (in absolute terms) indicates increasing downside risk and should trigger position reduction. The ratio CVaR/VaR measures tail shape -- if it is increasing, the tails are getting fatter even if VaR is stable.
- **Citation**: Rockafellar & Uryasev (2000), "Optimization of Conditional Value-at-Risk", Journal of Risk. Acerbi & Tasche (2002), "On the Coherence of Expected Shortfall", Journal of Banking & Finance.
- **Data requirement**: OHLCV (returns). Rolling window 50-200 bars.
- **Signal type**: SIZING, FILTER (risk overlay)
- **Difficulty**: Easy

### Signal 8: Maximum Drawdown Speed

- **Formula**: MDD_speed = max_drawdown(window) / duration_of_max_drawdown(window). Alternative: peak-to-trough return divided by number of bars from peak to trough.
- **Complexity**: O(N) per window
- **Causal mechanism**: Fast drawdowns (crash-like) tend to V-reverse because they are driven by forced liquidation and stop cascades rather than fundamental deterioration. Slow drawdowns (grinding bear) indicate fundamental selling and tend to continue. Drawdown speed distinguishes between the two. Fast MDD_speed suggests buying the dip; slow MDD_speed suggests staying out.
- **Citation**: Johansen & Sornette (2001), "Large Stock Market Price Drawdowns Are Outliers", Journal of Risk. Eling & Schuhmacher (2007), "Does the Choice of Performance Measure Influence the Evaluation of Hedge Funds?", JBF.
- **Data requirement**: OHLCV (high-water mark tracking).
- **Signal type**: ENTRY (fast crash = buy), FILTER (slow grind = avoid)
- **Difficulty**: Easy

### Signal 9: Drawdown Duration

- **Formula**: Duration = number of bars since last all-time-high (or rolling N-bar high). Can also compute average drawdown duration and compare current duration to historical.
- **Complexity**: O(N) per bar
- **Causal mechanism**: Extended drawdown durations are associated with regime shifts. Short drawdowns that quickly recover indicate a healthy trend. Prolonged drawdowns (beyond 2 standard deviations of historical duration) indicate structural bear market. This is a regime filter: if drawdown duration exceeds typical recovery time, the market has shifted.
- **Citation**: Magdon-Ismail & Atiya (2004), "Maximum Drawdown", Risk Magazine. Burghardt & Liu (2012), "It's the Autocorrelation, Stupid", Quantitative Finance.
- **Data requirement**: OHLCV
- **Signal type**: FILTER
- **Difficulty**: Easy

### Signal 10: Return Autocorrelation at Multiple Lags

- **Formula**: rho(k) = Corr(r_t, r_{t-k}) for k = 1, 2, 5, 10, 24 (hours). Computed over rolling windows of 100-500 bars.
- **Complexity**: O(N) per lag per window
- **Causal mechanism**: Positive autocorrelation at lag k means momentum at that horizon. Negative autocorrelation means mean reversion. Crypto typically shows positive autocorrelation at short lags (1-5 hours) that decays and sometimes turns negative at longer lags (24-72 hours). The autocorrelation profile changes with regime: trending markets have positive AC at all lags; ranging markets have negative AC at short lags. This is a direct measurement of which strategy type (momentum vs MR) is currently appropriate.
- **Citation**: Lo & MacKinlay (1988), "Stock Market Prices Do Not Follow Random Walks", Review of Financial Studies. Urquhart (2016), "The Inefficiency of Bitcoin", Economics Letters.
- **Data requirement**: OHLCV (returns)
- **Signal type**: FILTER, FEATURE
- **Difficulty**: Easy

### Signal 11: Variance Ratio Test (Lo-MacKinlay)

- **Formula**: VR(q) = Var(r_t(q)) / (q * Var(r_t)) where r_t(q) is the q-period return. Under random walk, VR(q) = 1. VR > 1 indicates momentum; VR < 1 indicates mean reversion.
- **Complexity**: O(N) per ratio
- **Causal mechanism**: Directly tests whether prices follow a random walk at a given horizon. If VR(24) > 1.2, the 24-hour horizon is exhibiting momentum -- deploy trend-following. If VR(24) < 0.8, that horizon is mean-reverting -- deploy MR strategies. Unlike simple autocorrelation, the variance ratio captures the cumulative effect of serial correlation across all intermediate lags.
- **Citation**: Lo & MacKinlay (1988), RFS. Chow & Denning (1993), "A Simple Multiple Variance Ratio Test", Journal of Econometrics.
- **Data requirement**: OHLCV (returns)
- **Signal type**: FILTER
- **Difficulty**: Easy

### Signal 12: Phillips-Perron Test Statistic (Rolling)

- **Formula**: Modified Dickey-Fuller t-statistic with Newey-West HAC correction for serial correlation and heteroskedasticity. Test H0: unit root (non-stationary) vs H1: stationary.
- **Complexity**: O(N) per window (OLS regression + HAC correction)
- **Causal mechanism**: A strongly negative PP statistic (rejecting unit root) means the price series is mean-reverting at that window. A PP statistic near zero means the series is trending/non-stationary. Rolling PP statistics over 100-500 bar windows create a dynamic stationarity indicator that tracks regime transitions. When PP transitions from non-rejection to rejection, the market has shifted from trending to mean-reverting.
- **Citation**: Phillips & Perron (1988), "Testing for a Unit Root in Time Series Regression", Biometrika.
- **Data requirement**: OHLCV (log prices)
- **Signal type**: FILTER
- **Difficulty**: Medium

---

## 2. REGIME AND STRUCTURAL BREAK SIGNALS

### Signal 13: Hidden Markov Model State Probabilities (2-state, 3-state)

- **Formula**: Fit HMM with Gaussian emissions to return series. States represent latent regimes (e.g., bull/bear for 2-state; bull/bear/sideways for 3-state). Use Baum-Welch (EM) for parameter estimation, Viterbi for most likely state sequence, forward algorithm for filtered state probabilities P(S_t | r_1, ..., r_t).
- **Complexity**: O(T * K^2) per EM iteration where K = number of states. Training is expensive; inference is cheap.
- **Causal mechanism**: Markets alternate between regimes with distinct return/volatility characteristics. HMM captures this latent structure and estimates the probability of being in each regime at each point. The regime probability is a powerful filter: only take momentum signals in the "trending" state, only take MR signals in the "mean-reverting" state. The transition probabilities also provide information about regime persistence.
- **Citation**: Hamilton (1989), "A New Approach to the Economic Analysis of Nonstationary Time Series and the Business Cycle", Econometrica. Bulla et al. (2011), "Stylized Facts of Financial Time Series and Hidden Semi-Markov Models", Computational Statistics & Data Analysis.
- **Data requirement**: OHLCV (returns, optionally volume and volatility as additional observables)
- **Signal type**: FILTER (primary regime classifier)
- **Difficulty**: Hard

### Signal 14: Bayesian Online Changepoint Detection (BOCPD)

- **Formula**: At each time t, compute P(r_t | x_{t-r_t+1:t}) for each possible run length r_t (time since last changepoint). Update via: P(r_t | x_{1:t}) proportional to P(x_t | r_t) * [P(r_t | r_{t-1}) * P(r_{t-1} | x_{1:t-1})]. Changepoint probability = P(r_t = 0 | x_{1:t}).
- **Complexity**: O(T^2) naive, O(T) with pruning. Per-bar updates are O(T) worst case but pruning makes it practical.
- **Causal mechanism**: BOCPD provides a real-time probability that the data-generating process has changed. Unlike fixed-window approaches, it adapts the effective lookback dynamically. When changepoint probability spikes, all historical parameters (mean, variance, correlations) should be re-estimated from the new segment. This is a meta-signal that invalidates other indicators and triggers regime re-estimation.
- **Citation**: Adams & MacKay (2007), "Bayesian Online Changepoint Detection", arXiv:0710.3742. Applied to finance by Nystrup et al. (2017), "Long Memory of Financial Time Series and Hidden Markov Models with Time-Varying Parameters", Journal of Forecasting.
- **Data requirement**: OHLCV (returns). Works best with multiple features (returns + volume + volatility).
- **Signal type**: FILTER (regime change alarm)
- **Difficulty**: Hard

### Signal 15: Markov Switching Model (Hamilton)

- **Formula**: r_t = mu_{S_t} + sigma_{S_t} * e_t where S_t is Markov({1,...,K}) with transition matrix P. Estimated via EM with Hamilton filter. Unlike HMM, this is specifically parameterized for financial returns with state-dependent mean and variance.
- **Complexity**: O(T * K^2) per EM iteration
- **Causal mechanism**: Similar to HMM but with explicit economic interpretation: each state has a mean return and volatility. The "bull" state might have mu = +0.1%, sigma = 1.5%, while the "bear" state has mu = -0.3%, sigma = 3.0%. The filtered probability of being in each state directly informs position direction and sizing. Transition probabilities tell you about regime persistence.
- **Citation**: Hamilton (1989), Econometrica. Guidolin & Timmermann (2007), "Asset Allocation Under Multivariate Regime Switching", Journal of Economic Dynamics & Control.
- **Data requirement**: OHLCV (returns)
- **Signal type**: FILTER, SIZING
- **Difficulty**: Hard

### Signal 16: CUSUM Statistic

- **Formula**: S_t = max(0, S_{t-1} + (r_t - mu_0 - k)) where mu_0 is the target mean and k is the allowance. Signal when S_t > h (threshold). Two-sided version tracks both upward and downward shifts.
- **Complexity**: O(1) per bar (recursive)
- **Causal mechanism**: CUSUM detects persistent shifts in the mean return that are too small to detect on any single bar but accumulate over time. A rising CUSUM for upward shifts means the return process has shifted above its historical mean -- a new uptrend has begun. CUSUM is especially good at detecting gradual trend changes (as opposed to sudden breaks), which are common in crypto when institutional accumulation slowly shifts the return distribution.
- **Citation**: Page (1954), "Continuous Inspection Schemes", Biometrika. Applied to finance by Zeileis et al. (2002), "Testing and Dating of Structural Changes in Practice", Computational Statistics & Data Analysis.
- **Data requirement**: OHLCV (returns)
- **Signal type**: ENTRY (trend initiation), FILTER
- **Difficulty**: Easy

### Signal 17: Bai-Perron Structural Break Detection

- **Formula**: Minimize sum of squared residuals over all possible m breakpoints: min_{T_1,...,T_m} sum_{j=0}^{m} sum_{t=T_j+1}^{T_{j+1}} (r_t - mu_j)^2. Sequential testing determines optimal number of breaks. Sup-F test for significance.
- **Complexity**: O(T^2) with dynamic programming (Bai-Perron efficient algorithm)
- **Causal mechanism**: Identifies structural breaks in the return process -- points where the mean, variance, or both changed significantly. Unlike rolling-window approaches, Bai-Perron provides statistically validated break dates. These break dates define regime boundaries and tell you which historical data is relevant for current parameter estimation. Post-break parameters should be estimated only from data after the most recent break.
- **Citation**: Bai & Perron (1998), "Estimating and Testing Linear Models with Multiple Structural Changes", Econometrica. Bai & Perron (2003), "Computation and Analysis of Multiple Structural Change Models", Journal of Applied Econometrics.
- **Data requirement**: OHLCV (returns)
- **Signal type**: FILTER (data relevance boundary)
- **Difficulty**: Medium

### Signal 18: Rolling Composite Regime Classifier

- **Formula**: Regime = f(trend_score, vol_score, momentum_score) where: trend_score = sign(EMA20 - EMA50) * ADX/25, vol_score = realized_vol / median(realized_vol, 60d), momentum_score = sign(ret_20d) * |ret_20d| / std(ret_20d). Classify into: strong_trend_up, weak_trend_up, range, weak_trend_down, strong_trend_down, crisis.
- **Complexity**: O(1) per bar (inputs pre-computed)
- **Causal mechanism**: Simple composite that combines trend direction, trend strength, volatility regime, and momentum magnitude into a single classifier. Each regime gets a distinct strategy allocation. Our own data shows dramatically different indicator effectiveness across regimes (e.g., RSI is useless in range but strong in downtrends). This classifier gates which sub-strategies are active.
- **Citation**: Kritzman et al. (2012), "Regime Shifts: Implications for Dynamic Strategies", Financial Analysts Journal. Ang & Timmermann (2012), "Regime Changes and Financial Markets", Annual Review of Financial Economics.
- **Data requirement**: OHLCV + pre-computed indicators
- **Signal type**: FILTER (master regime gate)
- **Difficulty**: Easy

### Signal 19: BTC Correlation Regime (Rolling)

- **Formula**: rho_t = Corr(r_{token,t-N:t}, r_{BTC,t-N:t}) over rolling window N = 50-200 bars. Track level and rate of change. Define decorrelation events as rho dropping below 0.3 from above 0.6.
- **Complexity**: O(N) per bar per token
- **Causal mechanism**: Most altcoins are highly correlated with BTC (rho > 0.7 typically). When an altcoin decorrelates from BTC, it means token-specific information is driving its price -- either very bullish (breakout) or very bearish (collapse). Decorrelation events are opportunities because the token is no longer just a BTC beta play; it has its own signal. Rising correlation after decorrelation = re-convergence, a mean-reversion opportunity.
- **Citation**: Bouri et al. (2019), "On the Return-Volatility Relationship in the Bitcoin Market Around the Price Crash of 2013", Economics: The Open-Access, Open-Assessment E-Journal. Makarov & Schoar (2020), "Trading and Arbitrage in Cryptocurrency Markets", JFE.
- **Data requirement**: OHLCV (returns for token + BTC). Already have both.
- **Signal type**: FILTER, ENTRY (decorrelation breakout)
- **Difficulty**: Easy

### Signal 20: Cross-Sectional Return Dispersion

- **Formula**: Dispersion_t = std(r_{1,t}, r_{2,t}, ..., r_{N,t}) across all N tokens at time t. High dispersion = tokens moving independently; low dispersion = herd behavior.
- **Complexity**: O(N_tokens) per bar
- **Causal mechanism**: Low dispersion (all tokens moving together) indicates macro/systematic risk is dominant -- beta trades dominate alpha. High dispersion indicates idiosyncratic factors are active -- stock-picking (token-picking) adds value. Deploy cross-sectional strategies (momentum, mean-reversion) when dispersion is high. Use broad market exposure when dispersion is low. Dispersion also mean-reverts: extremely low dispersion often precedes a spike in dispersion (breakout).
- **Citation**: Solnik & Roulet (2000), "Dispersion as Cross-Sectional Correlation", Financial Analysts Journal. Stivers & Sun (2010), "Cross-Sectional Return Dispersion and Time Variation in Value and Momentum Premiums", JFQA.
- **Data requirement**: OHLCV (returns for all 49 tokens)
- **Signal type**: FILTER (alpha opportunity indicator)
- **Difficulty**: Easy

### Signal 21: Market Breadth (% Above Moving Average)

- **Formula**: Breadth_t = (1/N) * sum_{i=1}^{N} I(close_{i,t} > EMA_{k,i,t}) for k = 20, 50. Ranges from 0 to 1. Variants: advance-decline line = cumulative (advancers - decliners).
- **Complexity**: O(N_tokens) per bar
- **Causal mechanism**: When the market rallies but breadth narrows (fewer tokens participating), the rally is fragile and likely to reverse. When breadth is broad (>70% above 20d EMA), the trend is healthy and likely to continue. Breadth divergences (price making new highs but breadth declining) are classic top indicators. In crypto, breadth is especially useful because altcoin rallies often fail when BTC leads alone.
- **Citation**: Chordia et al. (2002), "Order Imbalance, Liquidity, and Market Returns", JFE. Applied to crypto by Lucey et al. (2022), "The Cryptocurrency Uncertainty Index", Finance Research Letters.
- **Data requirement**: OHLCV (close prices for all tokens)
- **Signal type**: FILTER (trend health)
- **Difficulty**: Easy

### Signal 22: Absorption Ratio (Kritzman)

- **Formula**: AR = sum_{i=1}^{k} sigma_i^2 / sum_{j=1}^{N} sigma_j^2 where sigma_i are eigenvalues from PCA on the cross-sectional return correlation matrix, and k is typically N/5 (top 20% of components). High AR = one factor (BTC) drives everything = systemic risk.
- **Complexity**: O(N^3) for eigendecomposition, but N = 49 tokens so this is cheap
- **Causal mechanism**: When a small number of factors explain most of the variance (high absorption ratio), the market is tightly coupled and vulnerable to systemic shocks. A sharp rise in AR often precedes market crashes by 1-2 weeks. Declining AR means diversification is working and individual token strategies can operate independently. AR > 0.8 is a risk-off signal; AR < 0.6 is risk-on.
- **Citation**: Kritzman et al. (2011), "Principal Components as a Measure of Systemic Risk", Journal of Portfolio Management.
- **Data requirement**: OHLCV (returns for all tokens, rolling correlation matrix)
- **Signal type**: FILTER (systemic risk), SIZING (reduce exposure when AR is high)
- **Difficulty**: Medium

---

## 3. INFORMATION THEORY SIGNALS

### Signal 23: Transfer Entropy (BTC -> Altcoin)

- **Formula**: TE_{X->Y} = sum p(y_{t+1}, y_t^k, x_t^l) * log[p(y_{t+1} | y_t^k, x_t^l) / p(y_{t+1} | y_t^k)] where x = BTC returns, y = altcoin returns, k,l = embedding dimensions. Estimated via KDE or k-nearest-neighbors.
- **Complexity**: O(N^2) for KDE, O(N*log(N)) for KNN estimator. Medium-high cost.
- **Causal mechanism**: Transfer entropy measures the directed flow of information from BTC to the altcoin. High TE(BTC->ALT) means BTC price movements predict future altcoin movements -- the altcoin is a lagging follower. Low TE means the altcoin has decoupled. When TE is high, you can use BTC movements as a leading indicator for altcoin entries. When TE drops, the altcoin is pricing in its own information and BTC signals are unreliable.
- **Citation**: Schreiber (2000), "Measuring Information Transfer", Physical Review Letters. Dimpfl & Peter (2013), "Using Transfer Entropy to Measure Information Flows Between Financial Markets", Studies in Nonlinear Dynamics & Econometrics.
- **Data requirement**: OHLCV (returns for BTC + each altcoin). Rolling windows of 200+ bars.
- **Signal type**: FILTER (leading indicator validity), ENTRY (use BTC signal when TE is high)
- **Difficulty**: Hard

### Signal 24: Transfer Entropy (ETH -> Altcoin)

- **Formula**: Same as Signal 23 but with ETH as the source. Compute TE_{ETH->ALT} alongside TE_{BTC->ALT} to determine whether ETH or BTC is the dominant leader for each altcoin.
- **Complexity**: Same as Signal 23
- **Causal mechanism**: DeFi and L2 tokens often follow ETH more closely than BTC. If TE(ETH->token) > TE(BTC->token), the token is in the "ETH ecosystem" and should be traded using ETH as the leading indicator rather than BTC. This is especially relevant for tokens like UNI, AAVE, OP, ARB, which have fundamental links to ETH.
- **Citation**: Same as Signal 23. Stosic et al. (2018), "Collective Behavior of Cryptocurrency Price Changes", Physica A.
- **Data requirement**: OHLCV (returns for ETH + each altcoin)
- **Signal type**: FILTER
- **Difficulty**: Hard

### Signal 25: Mutual Information Between Volume and Returns

- **Formula**: MI(X;Y) = sum sum p(x,y) * log[p(x,y) / (p(x)*p(y))] where X = discretized volume changes, Y = discretized returns. Estimated via histogram or KNN (Kraskov estimator).
- **Complexity**: O(N*log(N)) for KNN estimator
- **Causal mechanism**: Standard correlation only captures linear dependence. MI captures any functional dependence between volume and returns. High MI(volume, returns) means volume is informative about returns (linearly or non-linearly). Low MI means volume and returns are independent. MI rising from a low level suggests a new relationship is forming (often the start of a trend where volume confirms direction). Unlike price-volume correlation, MI detects asymmetric relationships like "high volume predicts large absolute returns but not direction."
- **Citation**: Kraskov et al. (2004), "Estimating Mutual Information", Physical Review E. Dionisio et al. (2006), "Mutual Information: A Measure of Dependency for Nonlinear Time Series", Physica A.
- **Data requirement**: OHLCV (volume + returns)
- **Signal type**: FEATURE
- **Difficulty**: Medium

### Signal 26: Conditional Mutual Information (Returns | Regime)

- **Formula**: CMI(X;Y|Z) = MI(X;Y,Z) - MI(X;Z) where Z is regime state. Measures how much extra predictability exists in a specific regime beyond what the regime label alone provides.
- **Complexity**: O(N*log(N)) per regime, with regime conditioning reducing effective sample size
- **Causal mechanism**: Our own analysis shows indicators work differently across regimes (RSI is useless aggregate but IC = -0.145 in downtrends). CMI formalizes this: it measures the information content of each indicator conditional on the current regime. High CMI for indicator X in regime Z means X is predictive specifically in that regime. This enables regime-adaptive indicator weighting.
- **Citation**: Cover & Thomas (2006), "Elements of Information Theory", Wiley. Frenzel & Pompe (2007), "Partial Mutual Information for Coupling Analysis of Multivariate Time Series", Physical Review Letters.
- **Data requirement**: OHLCV + regime labels (from Signal 13 or 18)
- **Signal type**: FEATURE (for ML models)
- **Difficulty**: Hard

### Signal 27: Shannon Entropy of Return Distribution (Rolling)

- **Formula**: H(X) = -sum p(x_i) * log(p(x_i)) where x_i are discretized return bins. Computed over rolling window. Max entropy = uniform distribution (max uncertainty); low entropy = concentrated distribution (predictable).
- **Complexity**: O(N) per window after discretization
- **Causal mechanism**: High entropy means the return distribution is spread across many outcomes -- the market is uncertain and difficult to predict. Low entropy means returns are concentrated in a few outcomes -- the market is predictable (either trending smoothly or range-bound). Changes in entropy precede regime shifts: entropy rising = increasing uncertainty = current regime breaking down. Entropy falling = market settling into a new regime = strategies become more reliable.
- **Citation**: Shannon (1948), "A Mathematical Theory of Communication". Bandt & Pompe (2002), "Permutation Entropy: A Natural Complexity Measure for Time Series", Physical Review Letters.
- **Data requirement**: OHLCV (returns)
- **Signal type**: FILTER (predictability gauge)
- **Difficulty**: Easy

### Signal 28: KL Divergence from Normal (Rolling)

- **Formula**: KL(P||Q) = sum P(x) * log[P(x)/Q(x)] where P is the empirical return distribution and Q is N(mu, sigma^2) fitted to the same data. Discretize returns into bins. KL = 0 means perfectly normal; KL >> 0 means highly non-normal.
- **Complexity**: O(N) per window
- **Causal mechanism**: Similar to Jarque-Bera but captures the full distributional departure, not just skewness and kurtosis. When KL divergence rises sharply, the return distribution has changed shape in ways that may not be captured by simple moment statistics. This is a more general normality departure measure. Increasing KL divergence warns that parametric models (which assume normality) are becoming unreliable.
- **Citation**: Kullback & Leibler (1951), "On Information and Sufficiency", Annals of Mathematical Statistics.
- **Data requirement**: OHLCV (returns)
- **Signal type**: FILTER
- **Difficulty**: Easy

### Signal 29: Normalized Compression Distance

- **Formula**: NCD(x,y) = [C(xy) - min(C(x), C(y))] / max(C(x), C(y)) where C(s) is the compressed length of string s. Apply by encoding quantized return series as strings, then computing NCD between current window and reference windows.
- **Complexity**: O(N) per compression (using zlib or lz4)
- **Causal mechanism**: NCD measures the algorithmic similarity between two sequences. If the current return series has low NCD with a historical "crash" sequence, the market may be replicating crash dynamics. If it has low NCD with a "rally" sequence, it may be replicating rally dynamics. This is a non-parametric pattern matching approach that does not assume any statistical model.
- **Citation**: Cilibrasi & Vitanyi (2005), "Clustering by Compression", IEEE Transactions on Information Theory. Brandmaier (2015), "pdc: An R Package for Complexity-Based Clustering of Time Series", Journal of Statistical Software.
- **Data requirement**: OHLCV (returns, quantized)
- **Signal type**: FEATURE
- **Difficulty**: Medium

### Signal 30: Approximate Entropy / Sample Entropy

- **Formula**: ApEn(m, r, N) = phi^m(r) - phi^{m+1}(r) where phi^m(r) = (1/(N-m+1)) * sum log(C_i^m(r)) and C_i^m(r) is the fraction of m-length template matches within tolerance r. SampEn is the bias-corrected version (avoids self-matches).
- **Complexity**: O(N^2 * m) -- quadratic in window length. Can be expensive for large windows.
- **Causal mechanism**: Low entropy = regular, predictable series (trending or periodic). High entropy = irregular, noisy series (random walk). Declining ApEn in a price series means the price is becoming more regular (entering a trend or a tight range). Rising ApEn means the series is becoming more chaotic (regime transition, increased noise). Predictability measured by ApEn can be used to scale confidence in other indicators.
- **Citation**: Pincus (1991), "Approximate Entropy as a Measure of System Complexity", PNAS. Richman & Moorman (2000), "Physiological Time-Series Analysis Using Approximate Entropy and Sample Entropy", AJP.
- **Data requirement**: OHLCV (returns or log prices)
- **Signal type**: FILTER (predictability/confidence scalar)
- **Difficulty**: Medium

---

## 4. CROSS-ASSET AND MACRO SIGNALS

### Signal 31: BTC Dominance Momentum (7d Change)

- **Formula**: BTC_dom_t = BTC_market_cap_t / total_crypto_market_cap_t. Signal = BTC_dom_t - BTC_dom_{t-168h} (7 days on 1H). Rising dominance = risk-off rotation into BTC; falling dominance = altcoin season.
- **Complexity**: O(1) per bar
- **Causal mechanism**: BTC dominance acts as a risk barometer within crypto. When dominance rises, capital flows from altcoins to BTC (flight to quality). When dominance falls, capital rotates into higher-beta altcoins. Rising dominance favors BTC-only positions; falling dominance favors alt-heavy portfolios. The 7d momentum captures the direction and speed of rotation.
- **Citation**: Corbet et al. (2018), "Exploring the Dynamic Relationships Between Cryptocurrencies and Other Financial Assets", Economics Letters. Bouri et al. (2020), "Quantile Connectedness in the Cryptocurrency Market", JIFMIM.
- **Data requirement**: Requires total crypto market cap data (external API). Not directly from our OHLCV but can approximate from our 49-token universe.
- **Signal type**: FILTER (rotation timing)
- **Difficulty**: Easy (if data available), Medium (if approximated)

### Signal 32: ETH/BTC Ratio Momentum

- **Formula**: ETHBTC_t = ETH_close_t / BTC_close_t. Signal = (ETHBTC_t / ETHBTC_{t-168h}) - 1. Also compute EMA(20) and EMA(50) of the ratio for trend.
- **Complexity**: O(1) per bar
- **Causal mechanism**: The ETH/BTC ratio is the most important rotation signal in crypto. Rising ETH/BTC indicates risk appetite for smart contract platforms and DeFi -- bullish for the broader altcoin ecosystem. Falling ETH/BTC means capital is consolidating into BTC -- bearish for alts. ETH/BTC breakdown below key support levels (0.03, 0.025) historically triggers severe altcoin drawdowns.
- **Data requirement**: OHLCV for ETH and BTC. Already have both.
- **Signal type**: FILTER (altcoin allocation), ENTRY (ETH vs BTC relative value)
- **Difficulty**: Easy

### Signal 33: DXY (US Dollar Index) 20d Returns

- **Formula**: DXY_ret_20d = (DXY_t / DXY_{t-480h}) - 1. Can also use rate of change relative to its own volatility: DXY_zscore = DXY_ret_20d / std(DXY_ret_20d, 60d).
- **Complexity**: O(1) per bar
- **Causal mechanism**: Bitcoin and crypto broadly have a negative correlation with the US dollar. When the dollar strengthens (DXY rising), global risk assets including crypto come under pressure. When the dollar weakens, crypto benefits from both the risk-on environment and the narrative of alternative stores of value. The 20d horizon captures the medium-term trend which is more relevant than intraday DXY noise.
- **Citation**: Corbet et al. (2020), "The Contagion Effects of the COVID-19 Pandemic: Evidence from Gold and Cryptocurrencies", Finance Research Letters. Baur & Hoang (2021), "A Crypto Safe Haven Against the US Dollar", Finance Research Letters.
- **Data requirement**: External data (DXY from FRED, TradingView API, or similar). Not in our current dataset.
- **Signal type**: FILTER (macro risk)
- **Difficulty**: Easy (if data sourced)

### Signal 34: US 10-Year Yield Change (5d, 20d)

- **Formula**: Yield_chg_5d = US10Y_t - US10Y_{t-120h}. Yield_chg_20d = US10Y_t - US10Y_{t-480h}. Also: real yield = US10Y - breakeven inflation.
- **Complexity**: O(1) per bar
- **Causal mechanism**: Rising real yields increase the opportunity cost of holding zero-yield assets like crypto. The 2022 crypto crash was fundamentally driven by the Fed hiking rates from 0% to 5%+. Rapid yield increases (>50bp in 20 days) are bearish for crypto; yield stability or decline is bullish. The mechanism is both direct (discount rates) and indirect (risk appetite).
- **Citation**: Conlon et al. (2024), "Are Cryptocurrencies a Safe Haven for Equity Investors?", Finance Research Letters. Choi & Shin (2022), "Bitcoin: An Inflation Hedge but Not a Safe Haven", Finance Research Letters.
- **Data requirement**: External data (FRED, Treasury.gov). Not in our dataset.
- **Signal type**: FILTER (macro risk)
- **Difficulty**: Easy (if data sourced)

### Signal 35: S&P 500 20d Returns

- **Formula**: SPX_ret_20d = (SPX_t / SPX_{t-20d}) - 1. Also: SPX vs its 200d EMA as a bull/bear market indicator.
- **Complexity**: O(1)
- **Causal mechanism**: Since 2020, BTC correlation with SPX has increased substantially (rho ~ 0.5-0.7 in risk-off periods, near 0 in crypto-specific rallies). A declining equity market creates broad risk-off sentiment that pressures crypto. A strong equity market provides a supportive macro backdrop. The correlation is asymmetric: crypto falls harder than equities in selloffs but can decouple to the upside.
- **Citation**: Bouri et al. (2020), "On the Hedge and Safe Haven Properties of Bitcoin", Finance Research Letters. Conlon & McGee (2020), "Safe Haven or Risky Hazard?", Finance Research Letters.
- **Data requirement**: External data (SPX daily close from Yahoo Finance or similar). Not in our dataset.
- **Signal type**: FILTER
- **Difficulty**: Easy (if data sourced)

### Signal 36: VIX Level and Momentum

- **Formula**: VIX_level = current VIX. VIX_momentum = VIX_t - VIX_{t-5d}. VIX_term_structure = VIX - VIX3M (contango vs backwardation). Regime: VIX < 15 = complacent, 15-25 = normal, 25-35 = elevated, >35 = crisis.
- **Complexity**: O(1)
- **Causal mechanism**: VIX measures implied volatility (fear) in equity options. VIX spikes coincide with crypto selloffs because they reflect sudden risk aversion. VIX > 30 is historically a poor environment for crypto longs. VIX backwardation (spot > 3-month) indicates acute panic -- often a contrarian buy signal for crypto after the initial crash. VIX trending lower supports risk-on positioning.
- **Citation**: Whaley (2000), "The Investor Fear Gauge", Journal of Portfolio Management. Adrian & Shin (2010), "Liquidity and Leverage", JFE.
- **Data requirement**: External data (CBOE VIX). Not in our dataset.
- **Signal type**: FILTER (risk-on/risk-off)
- **Difficulty**: Easy (if data sourced)

### Signal 37: Gold/BTC Ratio

- **Formula**: GoldBTC_t = Gold_price_t / BTC_price_t. Signal = (GoldBTC_t / GoldBTC_{t-20d}) - 1. Rising ratio = safe haven rotation into gold; falling ratio = risk appetite for crypto.
- **Complexity**: O(1)
- **Causal mechanism**: Gold and BTC compete for the "store of value" narrative. When gold outperforms BTC, the market is seeking traditional safety -- bearish for crypto risk. When BTC outperforms gold, the market is in a risk-on "digital gold" mode. The ratio also captures macro liquidity: both tend to rise when real yields fall, but BTC rises faster due to higher beta.
- **Citation**: Selmi et al. (2018), "Is Bitcoin a Hedge, a Safe Haven, or a Diversifier for Oil Price Movements?", Energy Economics. Shahzad et al. (2019), "Is Bitcoin a Better Safe-Haven Investment Than Gold and Commodities?", International Review of Financial Analysis.
- **Data requirement**: External data (Gold spot price). Not in our dataset.
- **Signal type**: FILTER
- **Difficulty**: Easy (if data sourced)

### Signal 38: MOVE Index (Bond Volatility)

- **Formula**: MOVE_level = current MOVE Index value. MOVE_chg_5d = MOVE_t - MOVE_{t-5d}. Regime: MOVE < 80 = calm, 80-120 = normal, 120-180 = stressed, >180 = crisis.
- **Complexity**: O(1)
- **Causal mechanism**: The MOVE Index measures implied volatility in Treasury bonds. It is a direct measure of liquidity stress in the largest and most important market in the world. When MOVE spikes, bond market volatility forces deleveraging across all risk assets including crypto. MOVE > 150 has historically coincided with severe crypto drawdowns (March 2023 banking crisis, Oct 2023 yield spike). MOVE is arguably a better risk indicator for crypto than VIX because it captures the interest rate risk channel directly.
- **Citation**: Adrian et al. (2019), "Vulnerable Growth", American Economic Review. Brunnermeier & Pedersen (2009), "Market Liquidity and Funding Liquidity", RFS.
- **Data requirement**: External data (ICE MOVE Index). Not in our dataset.
- **Signal type**: FILTER (liquidity stress)
- **Difficulty**: Easy (if data sourced)

### Signal 39: M2 Money Supply Growth Rate

- **Formula**: M2_growth = (M2_t / M2_{t-12months}) - 1. Can also use monthly change: M2_chg = (M2_t / M2_{t-1month}) - 1. Global M2 = sum of major central bank M2 equivalents in USD.
- **Complexity**: O(1) per data point (monthly frequency, interpolate to daily)
- **Causal mechanism**: Crypto is a liquidity-sensitive asset class. When global M2 is expanding, there is excess liquidity seeking returns, and a portion flows into crypto. When M2 contracts (quantitative tightening), liquidity dries up and speculative assets including crypto decline. The correlation between global M2 growth and BTC price is strong at 6-12 month horizons. This is a slow-moving but powerful macro filter.
- **Citation**: Bianchi (2020), "Cryptocurrencies as an Asset Class? An Empirical Assessment", Journal of Alternative Investments. Howell (2020), "Capital Wars: The Rise of Global Liquidity", Palgrave.
- **Data requirement**: External data (FRED M2, plus ECB, PBOC, BOJ equivalents for global M2). Not in our dataset. Monthly frequency.
- **Signal type**: FILTER (long-term macro backdrop)
- **Difficulty**: Easy (if data sourced)

### Signal 40: Net Stablecoin Supply Change

- **Formula**: Stablecoin_flow = total_stablecoin_supply_t - total_stablecoin_supply_{t-7d}. Decompose: USDT_flow + USDC_flow + DAI_flow. Positive = minting (new capital entering crypto); negative = redemptions (capital leaving).
- **Complexity**: O(1) per data point
- **Causal mechanism**: Stablecoin minting is the most direct measure of fiat capital entering the crypto ecosystem. When USDT/USDC supply is growing, there is new buying power available. When stablecoin supply contracts, capital is leaving crypto. This is a crypto-native liquidity indicator that is more timely than M2 (daily vs monthly) and more directly relevant. Large stablecoin inflows often precede rallies by 1-2 weeks.
- **Citation**: Lyons & Viswanath-Natraj (2023), "What Keeps Stablecoins Stable?", JFE. Griffin & Shams (2020), "Is Bitcoin Really Untethered?", Journal of Finance.
- **Data requirement**: External data (on-chain via Glassnode, DefiLlama, or CoinGecko API). Not in our dataset.
- **Signal type**: FILTER (crypto liquidity), ENTRY (inflow signal)
- **Difficulty**: Medium (requires on-chain data source)

### Signal 41: Total Crypto Market Cap Momentum

- **Formula**: Total_mcap_ret = (total_mcap_t / total_mcap_{t-20d}) - 1. Also: total_mcap vs its 50d and 200d EMA. Approximate from our 49-token universe: weighted sum of individual market caps.
- **Complexity**: O(N_tokens) per bar
- **Causal mechanism**: Total market cap trend provides the broadest measure of the crypto market's direction. Trading against the total market cap trend has negative expected value. This is the simplest and most important macro filter: only take longs when total market cap is above its 50d EMA, reduce exposure when it is below.
- **Citation**: Huang et al. (2024), "Cryptocurrency Momentum and Reversal", SSRN.
- **Data requirement**: Can approximate from OHLCV (close * circulating supply for each token). Or external API.
- **Signal type**: FILTER (broad market trend)
- **Difficulty**: Easy

### Signal 42: Altcoin Season Index

- **Formula**: Alt_season = fraction of top-N altcoins outperforming BTC over trailing 90d period. Alt_season > 0.75 = altcoin season. Alt_season < 0.25 = BTC season. Also compute rate of change.
- **Complexity**: O(N_tokens) per bar
- **Causal mechanism**: Crypto markets exhibit rotation between BTC-dominated rallies and altcoin-dominated rallies. During altcoin season, capital rotates from BTC into smaller tokens, creating momentum opportunities across the altcoin universe. During BTC season, altcoins underperform and capital concentrates. This signal determines portfolio allocation between BTC and altcoins.
- **Citation**: Sifat et al. (2019), "Lead-Lag Relationship Between Bitcoin and Ethereum", Research in International Business and Finance.
- **Data requirement**: OHLCV (returns for BTC + altcoins). Computable from our data.
- **Signal type**: FILTER (allocation rotation)
- **Difficulty**: Easy

---

## 5. MICROSTRUCTURE SIGNALS

### Signal 43: Corwin-Schultz Spread Estimator

- **Formula**: S = (2*(exp(alpha) - 1)) / (1 + exp(alpha)) where alpha = (sqrt(2*beta) - sqrt(beta)) / (3 - 2*sqrt(2)) - sqrt(gamma/(3 - 2*sqrt(2))). beta = sum[ln(H_{t+j}/L_{t+j})]^2 for j=0,1. gamma = [ln(H_{t,t+1}/L_{t,t+1})]^2 where H and L are high and low across two periods.
- **Complexity**: O(1) per bar
- **Causal mechanism**: The bid-ask spread is a direct measure of transaction costs and information asymmetry. Wide spreads indicate either low liquidity (risky to trade) or high information asymmetry (informed traders are active). The Corwin-Schultz estimator extracts spreads from high-low prices without needing order book data. Widening spreads precede large moves because market makers widen spreads when they sense informed flow. Narrowing spreads indicate safe, liquid trading conditions.
- **Citation**: Corwin & Schultz (2012), "A Simple Way to Estimate Bid-Ask Spreads from Daily High and Low Prices", Journal of Finance.
- **Data requirement**: OHLCV (high, low). Already have.
- **Signal type**: FILTER (liquidity/risk), SIZING (reduce size when spread is wide)
- **Difficulty**: Easy

### Signal 44: Roll Spread Estimator

- **Formula**: Roll_spread = 2 * sqrt(-Cov(delta_p_t, delta_p_{t-1})) where delta_p_t = close_t - close_{t-1}. Only defined when Cov < 0 (bid-ask bounce induces negative autocovariance). Set to 0 when Cov >= 0.
- **Complexity**: O(N) per rolling window
- **Causal mechanism**: The Roll model assumes observed prices bounce between bid and ask, creating negative serial correlation in price changes. The magnitude of this bounce estimates the effective spread. This is a simpler estimator than Corwin-Schultz and works best at high frequencies (1H bars). When the Roll spread increases, market-making is becoming more expensive -- a sign of increasing adverse selection risk.
- **Citation**: Roll (1984), "A Simple Implicit Measure of the Effective Bid-Ask Spread in an Efficient Market", Journal of Finance.
- **Data requirement**: OHLCV (close prices). Already have.
- **Signal type**: FILTER, FEATURE
- **Difficulty**: Easy

### Signal 45: Abdi-Ranaldo Spread Estimator

- **Formula**: Spread^2 = 4 * E[(c_t - (h_t + l_t)/2) * (c_t - (h_{t+1} + l_{t+1})/2)] where c = close, h = high, l = low. Spread = 2 * sqrt(max(0, covariance_term)).
- **Complexity**: O(N) per rolling window
- **Causal mechanism**: Uses the relationship between closing prices and midpoints (approximated by (high+low)/2) to estimate the spread. More robust than Roll's estimator in the presence of volatility. The advantage over Corwin-Schultz is better performance in high-volatility environments typical of crypto. Provides a cleaner spread estimate for use in transaction cost modeling and liquidity filtering.
- **Citation**: Abdi & Ranaldo (2017), "A Simple Estimation of Bid-Ask Spreads from Daily Close, High, and Low Prices", Review of Financial Studies.
- **Data requirement**: OHLCV (close, high, low). Already have.
- **Signal type**: FILTER, SIZING
- **Difficulty**: Easy

### Signal 46: Kyle's Lambda (Price Impact Coefficient)

- **Formula**: delta_p_t = lambda * sqrt(V_t) * sign(r_t) + e_t. Estimate lambda via OLS regression of absolute returns on sqrt(volume) over rolling window. High lambda = high price impact = low liquidity.
- **Complexity**: O(N) per window (OLS regression)
- **Causal mechanism**: Kyle's lambda measures how much prices move per unit of volume (order flow). High lambda means the market is thin and each trade has a large price impact -- informed traders are more active relative to noise traders. Lambda increasing = deteriorating liquidity conditions = higher risk of being adversely selected. Low lambda = deep market = safer to trade larger sizes. Lambda should directly inform position sizing: trade smaller when lambda is high.
- **Citation**: Kyle (1985), "Continuous Auctions and Insider Trading", Econometrica. Hasbrouck (2009), "Trading Costs and Returns for US Equities", Journal of Financial Markets.
- **Data requirement**: OHLCV (returns + volume). Already have.
- **Signal type**: SIZING (inverse lambda sizing), FILTER
- **Difficulty**: Easy

### Signal 47: Amihud Illiquidity (Rolling Multi-Window)

- **Formula**: Amihud_t = (1/N) * sum |r_i| / Volume_i over rolling window N. Variants: use dollar volume (quote_volume) in denominator. Compute at multiple windows: 24h, 72h, 168h (1 week).
- **Complexity**: O(N) per window
- **Causal mechanism**: Amihud illiquidity measures the price impact per unit of trading volume. It is the most widely used liquidity measure in academic finance. In crypto, Amihud varies by orders of magnitude across tokens and time. Rising Amihud predicts wider spreads and larger adverse price moves. The multi-window approach captures different liquidity horizons: 24h captures intraday liquidity, 168h captures structural liquidity.
- **Citation**: Amihud (2002), "Illiquidity and Stock Returns: Cross-Section and Time-Series Effects", JFQA.
- **Data requirement**: OHLCV (returns + volume). Already have amihud_1m from enriched data (daily). Need to compute 1H versions.
- **Signal type**: SIZING, FILTER
- **Difficulty**: Easy

### Signal 48: VPIN (Volume-Synchronized Probability of Informed Trading)

- **Formula**: Classify volume into buy (V_B) and sell (V_S) using tick rule or bulk volume classification (BVC). VPIN = sum |V_B_tau - V_S_tau| / (n * V_bar) over n volume buckets of fixed size V_bar. High VPIN = high informed trading = adverse selection risk.
- **Complexity**: O(N) but requires volume bucketing
- **Causal mechanism**: VPIN estimates the fraction of trading that is information-driven rather than noise. High VPIN means informed traders (who know something the market does not yet reflect) are active. VPIN spikes preceded the 2010 Flash Crash by several hours. In crypto, VPIN spikes before major exchange announcements, regulatory news, and whale movements. It is a real-time early warning system for large impending moves.
- **Citation**: Easley, Lopez de Prado & O'Hara (2012), "Flow Toxicity and Liquidity in a High-Frequency World", Review of Financial Studies.
- **Data requirement**: OHLCV + taker_buy_base (have this in 1H data). Already have daily VPIN from enriched data. Can compute 1H VPIN.
- **Signal type**: FILTER (risk warning), EXIT (close positions when VPIN spikes)
- **Difficulty**: Medium

### Signal 49: Order Flow Imbalance (Taker Buy/Sell Asymmetry)

- **Formula**: OFI_t = (taker_buy_volume_t - taker_sell_volume_t) / total_volume_t. Variants: cumulative OFI over N bars, OFI momentum (delta over 24h). Already have taker_buy_base; taker_sell = total - taker_buy.
- **Complexity**: O(1) per bar
- **Causal mechanism**: Taker buys (market orders hitting the ask) indicate urgency to buy; taker sells indicate urgency to sell. Persistent positive OFI indicates demand exceeds supply at current prices -- bullish. Our data shows taker signal decays rapidly (useful at 1d IC = +0.035, noise by 5d). This confirms OFI is a short-term entry timing signal, not a medium-term predictor. Best used for entry timing within a broader directional framework.
- **Citation**: Chordia et al. (2004), "Order Imbalance and Individual Stock Returns: Theory and Evidence", JFE. Lee & Ready (1991), "Inferring Trade Direction from Intraday Data", Journal of Finance.
- **Data requirement**: OHLCV + taker_buy_base (already have in 1H data).
- **Signal type**: ENTRY (short-term timing)
- **Difficulty**: Easy

### Signal 50: Volume Clock vs Time Clock Acceleration

- **Formula**: VolumeTime_t = cumulative volume-bucketed bars elapsed vs calendar bars elapsed. Acceleration = d(VolumeTime)/d(CalendarTime). When VolumeTime accelerates, trading activity is intensifying.
- **Complexity**: O(N) for volume bucketing
- **Causal mechanism**: Markets are more active during informationally rich periods. When the volume clock runs faster than the calendar clock, more information is being processed per unit of time. Acceleration of the volume clock precedes breakouts and major moves because early-informed participants begin trading before the news is widely known. Deceleration (volume clock slowing) indicates a quiet period where signals are less reliable.
- **Citation**: Easley, Lopez de Prado & O'Hara (2012), "The Volume Clock: Insights into the High-Frequency Paradigm", Journal of Portfolio Management. Ane & Geman (2000), "Order Flow, Transaction Clock, and Normality of Asset Returns", Journal of Finance.
- **Data requirement**: OHLCV (volume). Already have.
- **Signal type**: FILTER (information intensity)
- **Difficulty**: Medium

### Signal 51: Large Trade Percentage (Trade Size Distribution)

- **Formula**: LargeTrade_pct = volume_from_large_trades / total_volume. Define "large" as trades > 2*median trade size. Approximate from OHLCV: LargeTrade_proxy = quote_volume / trades (average trade size), then compare to rolling median.
- **Complexity**: O(1) per bar
- **Causal mechanism**: Institutional and whale participants execute larger average trades. When the average trade size increases (or the fraction of large trades rises), institutional activity is increasing. Institutional flow is more informed and more persistent than retail flow. Rising institutional activity often precedes sustained directional moves. Our 1H data includes a trades count column, allowing computation of average trade size = quote_volume / trades.
- **Citation**: Barclay & Warner (1993), "Stealth Trading and Volatility: Which Trades Move Prices?", JFE. Chakrabarty et al. (2007), "Trade Size, Order Imbalance, and the Volatility-Volume Relation", JFE.
- **Data requirement**: OHLCV (quote_volume + trades count). Already have both.
- **Signal type**: FILTER (institutional activity), FEATURE
- **Difficulty**: Easy

### Signal 52: Hasbrouck Information Share (Cross-Exchange)

- **Formula**: Decompose the variance of the common efficient price innovations across multiple exchange price series using a VECM (Vector Error Correction Model). The information share for exchange j is the proportion of efficient price variance attributable to innovations from exchange j.
- **Complexity**: O(T * K^2) for VECM estimation where K = number of exchanges
- **Causal mechanism**: Different exchanges lead price discovery at different times. When a typically lagging exchange starts leading (its information share increases), it often signals a shift in the source of order flow -- possibly institutional entry on a regulated exchange or whale activity on a specific venue. Changes in information share can be early warnings of regime shifts.
- **Citation**: Hasbrouck (1995), "One Security, Many Markets: Determining the Contributions to Price Discovery", Journal of Finance.
- **Data requirement**: Requires multi-exchange data (Binance + Coinbase + Kraken at minimum). Not in our current single-exchange dataset. Would need additional data collection.
- **Signal type**: FEATURE
- **Difficulty**: Hard

### Signal 53: Realized Volatility Signature Plot

- **Formula**: Compute realized variance RV(delta) = sum r_{t,delta}^2 at multiple sampling frequencies delta (1m, 5m, 15m, 1H, 4H). Plot RV vs delta. The bias at high frequencies (microstructure noise) vs convergence at lower frequencies reveals noise-to-signal ratio.
- **Complexity**: O(N) per frequency, requires multi-frequency data
- **Causal mechanism**: The signature plot reveals how much microstructure noise contaminates volatility estimates at each frequency. When the noise component (gap between 1m RV and optimal RV) increases, the market is becoming noisier -- more noise traders, less informed trading, wider effective spreads. A stable signature plot (flat across frequencies) indicates clean price discovery. The optimal sampling frequency (where the plot flattens) also tells you the appropriate timeframe for trading decisions.
- **Citation**: Andersen et al. (2000), "The Distribution of Realized Stock Return Volatility", JFE. Bandi & Russell (2008), "Microstructure Noise, Realized Variance, and Optimal Sampling", Review of Economic Studies.
- **Data requirement**: Multi-frequency data (1m, 5m, 15m, 1H). We have 1m data in cache, and 1H data.
- **Signal type**: FILTER (noise level), FEATURE
- **Difficulty**: Medium

### Signal 54: Realized Kernel Estimator (Noise-Robust Volatility)

- **Formula**: RK = sum_{h=-H}^{H} k(h/H) * gamma_hat(h) where gamma_hat(h) is the h-th autocovariance of high-frequency returns and k() is a kernel function (Parzen, Tukey-Hanning). This debiases realized variance for microstructure noise.
- **Complexity**: O(N * H) where H is the bandwidth
- **Causal mechanism**: Standard realized volatility is biased upward at high frequencies due to bid-ask bounce and other microstructure effects. The realized kernel provides a noise-robust volatility estimate that is more accurate for risk management and volatility-based signals. When the gap between naive RV and kernel RV widens, microstructure noise is increasing (deteriorating market quality). The kernel estimate is also a better input for volatility-based signals (Bollinger Bands, ATR, position sizing).
- **Citation**: Barndorff-Nielsen et al. (2008), "Designing Realized Kernels to Measure the Ex Post Variation of Equity Prices in the Presence of Noise", Econometrica.
- **Data requirement**: High-frequency data (1m). We have 1m cache data.
- **Signal type**: SIZING (better vol estimate), FEATURE
- **Difficulty**: Medium

---

## 6. ML-DERIVED FEATURES

### Signal 55: PCA Factor Loadings (Top 3 Components)

- **Formula**: Compute the NxN correlation matrix of returns for all N tokens over a rolling window (500+ bars). Extract eigenvectors v_1, v_2, v_3 (top 3 principal components). Each token's loading on each component = its weight in that eigenvector. Component 1 is typically "market" (BTC beta), Component 2 is often "alt rotation", Component 3 is "sector."
- **Complexity**: O(N^3) eigendecomposition, but N=49 so trivial in practice. Rolling window is the main cost.
- **Causal mechanism**: PCA decomposes the cross-section of returns into orthogonal risk factors. A token whose loading on Component 1 (market) is increasing is becoming more correlated with BTC -- less alpha opportunity. A token whose loading shifts from Component 1 to Component 2 or 3 is developing idiosyncratic behavior -- more alpha opportunity. Factor loadings also reveal hidden sector rotations that are not visible from individual token analysis.
- **Citation**: Connor & Korajczyk (1988), "Risk and Return in an Equilibrium APT", JFE. Meucci (2009), "Managing Diversification", Risk Magazine.
- **Data requirement**: OHLCV (returns for all 49 tokens). Already have.
- **Signal type**: FEATURE (portfolio construction), FILTER (alpha opportunity)
- **Difficulty**: Medium

### Signal 56: Autoencoder Reconstruction Error (Anomaly Detection)

- **Formula**: Train a shallow autoencoder (encoder: D -> k -> D, where D = number of features, k << D) on a rolling window of multi-feature vectors (returns, volume, volatility, spread, etc.). Reconstruction error = ||x - decoder(encoder(x))||^2. High error = anomalous observation that does not fit learned patterns.
- **Complexity**: O(D^2 * N) per training epoch. Medium-high depending on retraining frequency.
- **Causal mechanism**: The autoencoder learns a compressed representation of "normal" market behavior. When reconstruction error spikes, the current market state is unlike anything in the training window -- a novel regime. Novel regimes are dangerous for systematic strategies that were calibrated on historical data. High reconstruction error should trigger risk reduction. It can also signal opportunity: if the anomaly is a dislocation rather than a regime change, prices may mean-revert once the anomaly resolves.
- **Citation**: Sakurada & Yairi (2014), "Anomaly Detection Using Autoencoders with Nonlinear Dimensionality Reduction", MLSDA. Zong et al. (2018), "Deep Autoencoding Gaussian Mixture Model for Unsupervised Anomaly Detection", ICLR.
- **Data requirement**: OHLCV + enriched features. Multi-dimensional input.
- **Signal type**: FILTER (anomaly alarm), SIZING (reduce on high error)
- **Difficulty**: Hard

### Signal 57: LSTM Prediction Confidence (Model Uncertainty)

- **Formula**: Train an LSTM to predict next-bar return direction (or magnitude). Use MC Dropout at inference: run N forward passes with dropout active, producing N predictions. Confidence = 1 - std(predictions)/mean(|predictions|). Low confidence = high uncertainty = unreliable signal.
- **Complexity**: O(sequence_length * hidden_dim^2 * N_passes) per prediction. High cost.
- **Causal mechanism**: The LSTM's confidence serves as a meta-signal: when the model is confident, its predictions are more reliable and positions can be sized up. When the model is uncertain (wide dispersion across MC dropout passes), the market is in a state the model has not seen or cannot decode -- positions should be small or flat. This is a principled way to combine ML predictions with risk management. The model itself does not need to be highly accurate; its uncertainty estimate is the valuable signal.
- **Citation**: Gal & Ghahramani (2016), "Dropout as a Bayesian Approximation: Representing Model Uncertainty in Deep Learning", ICML. Zhu & Shasha (2002), "StatStream: Statistical Monitoring of Thousands of Data Streams in Real Time", VLDB.
- **Data requirement**: OHLCV + enriched features (as LSTM inputs)
- **Signal type**: SIZING (confidence scalar), FILTER
- **Difficulty**: Hard

### Signal 58: Rolling Feature Importance (Random Forest)

- **Formula**: Train a Random Forest to predict sign(r_{t+N}) from a feature vector at each bar. Extract feature importance (mean decrease in impurity or permutation importance) over a rolling window (500-1000 bars). Track which features are most important over time.
- **Complexity**: O(N_trees * N_samples * D * log(N_samples)) per training. Medium-high.
- **Causal mechanism**: The relative importance of different features changes with market regime. In trending markets, momentum features dominate. In ranging markets, mean-reversion and microstructure features dominate. By tracking feature importance over time, you get a data-driven view of which signals are currently predictive. This enables adaptive signal weighting without hard-coded regime rules: weight each indicator by its recent feature importance.
- **Citation**: Breiman (2001), "Random Forests", Machine Learning. Gu et al. (2020), "Empirical Asset Pricing via Machine Learning", Review of Financial Studies.
- **Data requirement**: OHLCV + all computed features as inputs
- **Signal type**: FEATURE (meta-signal for adaptive weighting)
- **Difficulty**: Medium

### Signal 59: Kalman Filter Estimated Trend

- **Formula**: State-space model: x_t = F*x_{t-1} + w_t (state transition), y_t = H*x_t + v_t (observation). For trend extraction: state = [level, slope], observation = close price. Kalman gain K_t optimally weights new observation vs prior state estimate. Output: filtered trend estimate = H * x_{t|t}.
- **Complexity**: O(d^3) per step where d = state dimension (typically 2-4, so trivial)
- **Causal mechanism**: The Kalman filter provides an optimal (minimum variance) estimate of the underlying trend, separating signal from noise. Unlike moving averages, the Kalman filter adapts its smoothing based on the signal-to-noise ratio: when the market is noisy, it relies more on the prior (smooths more); when the market is trending cleanly, it follows the data more closely. The slope component directly estimates trend direction and magnitude. The innovation sequence (prediction error) is a measure of surprise that can trigger alerts.
- **Citation**: Kalman (1960), "A New Approach to Linear Filtering and Prediction Problems", ASME. Applied to finance by Wells (1996), "The Kalman Filter in Finance", Kluwer.
- **Data requirement**: OHLCV (close prices)
- **Signal type**: ENTRY (trend direction), FILTER (slope magnitude)
- **Difficulty**: Medium

### Signal 60: Wavelet Decomposition Energy at Multiple Scales

- **Formula**: Apply discrete wavelet transform (DWT) using Daubechies or Haar wavelet to the return series. Decompose into detail coefficients d_1, d_2, ..., d_J and approximation a_J. Energy at scale j = sum(d_j^2). Dominant scale = argmax_j Energy(j). Reconstruct trend at specific scales.
- **Complexity**: O(N) per DWT (fast wavelet transform)
- **Causal mechanism**: Different market participants operate at different timescales: HFT (minutes), day traders (hours), swing traders (days), investors (weeks/months). Wavelets decompose price action into these natural timescales. When energy at the swing-trade scale (4-72 hours for our 1H data) is high, there are tradeable patterns at our horizon. When energy is concentrated at very short (noise) or very long (macro trend) scales, our timeframe has less opportunity. The wavelet-reconstructed signal at our target scale is a denoised version of the tradeable component.
- **Citation**: Gencay et al. (2001), "An Introduction to Wavelets and Other Filtering Methods in Finance and Economics", Academic Press. In & Kim (2013), "An Introduction to Wavelet Theory in Finance", World Scientific.
- **Data requirement**: OHLCV (returns or prices)
- **Signal type**: FILTER (opportunity at target scale), FEATURE
- **Difficulty**: Medium

### Signal 61: Catch22 Features (Lubba et al.)

- **Formula**: A set of 22 canonical time series features including: distribution outlier measures, linear/nonlinear autocorrelation, successive differences, fluctuation analysis, transition matrix properties, and more. Specific features include: DN_HistogramMode_5, CO_f1ecac, CO_FirstMin_ac, SP_Summaries_welch_rect, SB_BinaryStats_mean_longstretch1, etc. Each is a scalar computed from a time series window.
- **Complexity**: O(N*log(N)) for most features (some involve FFT)
- **Causal mechanism**: Catch22 was designed as the minimal set of features that captures the most information about time series structure. It includes features for trend, periodicity, distribution shape, serial dependence, and complexity. Rather than hand-picking features, Catch22 provides a principled compressed representation that has been validated across thousands of time series datasets. These serve as a comprehensive feature vector for ML models, ensuring no major structural characteristic is missed.
- **Citation**: Lubba et al. (2019), "catch22: CAnonical Time-series CHaracteristics", Data Mining and Knowledge Discovery.
- **Data requirement**: OHLCV (returns or prices over rolling windows)
- **Signal type**: FEATURE (ML input vector)
- **Difficulty**: Medium (libraries available: pycatch22)

### Signal 62: ROCKET Features (Random Convolutional Kernels)

- **Formula**: Generate K random convolutional kernels with random length, weights, bias, dilation, and padding. Convolve each kernel with the time series. Extract two features per kernel: max value and proportion of positive values (ppv). Total features = 2K (typically K = 10,000).
- **Complexity**: O(K * N) where K = number of kernels. Fast due to random (no training) kernel generation.
- **Causal mechanism**: ROCKET achieves state-of-the-art time series classification accuracy with a fraction of the compute of deep learning approaches. The random kernels act as pattern detectors across multiple scales and shapes. Unlike hand-engineered features, ROCKET discovers patterns automatically. The output is a high-dimensional feature vector suitable for ridge regression or other linear classifiers, combining the expressiveness of convolutional neural networks with the simplicity and interpretability of linear models.
- **Citation**: Dempster et al. (2020), "ROCKET: Exceptionally Fast and Accurate Time Series Classification Using Random Convolutional Kernels", Data Mining and Knowledge Discovery.
- **Data requirement**: OHLCV (any univariate or multivariate time series)
- **Signal type**: FEATURE (ML classification input)
- **Difficulty**: Medium (libraries available: sktime, tsai)

---

## 7. ADDITIONAL ADVANCED SIGNALS

### Signal 63: Garman-Klass Volatility

- **Formula**: GK = 0.5 * ln(H/L)^2 - (2*ln(2) - 1) * ln(C/O)^2, computed per bar and averaged over rolling window.
- **Complexity**: O(1) per bar
- **Causal mechanism**: More efficient volatility estimator than close-to-close (uses full OHLC information). Uses ~7.4x less data than close-to-close to achieve the same estimation accuracy. Better volatility estimates improve every volatility-dependent signal (Bollinger Bands, ATR-based stops, position sizing). The ratio GK_vol / close_to_close_vol indicates intrabar information content: high ratio = lots of intrabar movement (noisy, wide ranges), low ratio = smooth moves.
- **Citation**: Garman & Klass (1980), "On the Estimation of Security Price Volatilities from Historical Data", Journal of Business.
- **Data requirement**: OHLCV (all four prices). Already have.
- **Signal type**: SIZING (better vol estimate), FEATURE
- **Difficulty**: Easy

### Signal 64: Yang-Zhang Volatility

- **Formula**: YZ = sigma_o^2 + k * sigma_c^2 + (1-k) * sigma_rs^2 where sigma_o = overnight (open-to-prev-close) variance, sigma_c = close-to-close variance, sigma_rs = Rogers-Satchell variance, k = 0.34/(1.34 + (n+1)/(n-1)).
- **Complexity**: O(N) per window
- **Causal mechanism**: The most efficient OHLC-based volatility estimator, combining overnight jumps, close-to-close moves, and intrabar ranges. Handles drift (trending markets) correctly, unlike Parkinson or Garman-Klass. The overnight component captures gap risk which is relevant in crypto even though markets trade 24/7 (exchange maintenance windows, cross-exchange arbitrage lags). In crypto, the "overnight" component can be interpreted as the open-to-prior-close gap, which captures information arriving between bars.
- **Citation**: Yang & Zhang (2000), "Drift Independent Volatility Estimation Based on High, Low, Open, and Close Prices", Journal of Business.
- **Data requirement**: OHLCV (full OHLC). Already have.
- **Signal type**: SIZING, FEATURE
- **Difficulty**: Easy

### Signal 65: Relative Volume Profile (RVOL)

- **Formula**: RVOL_t = volume_t / median(volume_{same_hour_of_day, past_N_days}). Captures intraday seasonality-adjusted volume. High RVOL = unusual activity for this time of day.
- **Complexity**: O(N_days * 24) setup, O(1) per bar
- **Causal mechanism**: Volume has strong intraday seasonality (higher during US/European market hours, lower during Asian hours). Raw volume comparisons are misleading because a "high volume" bar during Asian hours may be normal for US hours. RVOL normalizes for this pattern and reveals truly unusual activity. RVOL > 3x indicates extraordinary activity that is likely driven by news or informed trading. RVOL combined with return direction gives a cleaner volume confirmation signal.
- **Citation**: Chordia et al. (2001), "Trading Activity and Expected Stock Returns", JFE. Lo & Wang (2000), "Trading Volume: Definitions, Data Analysis, and Implications of Portfolio Theory", RFS.
- **Data requirement**: OHLCV (volume + timestamps). Already have open_time.
- **Signal type**: FILTER (unusual activity), ENTRY (RVOL > 3 + direction)
- **Difficulty**: Easy

### Signal 66: Volume-Weighted Price Elasticity

- **Formula**: Elasticity = %change_price / %change_volume = (delta_p / p) / (delta_v / v) over rolling window. Alternatively: regression coefficient of log(|return|) on log(volume).
- **Complexity**: O(N) per window
- **Causal mechanism**: Price elasticity with respect to volume measures market depth. Low elasticity (large volume causes small price changes) indicates a deep, resilient market. High elasticity (small volume causes large price changes) indicates a fragile market. Increasing elasticity over time warns that the market is becoming thinner and more vulnerable to liquidation cascades. This is a forward-looking fragility indicator.
- **Citation**: Kyle (1985), Econometrica. Amihud et al. (2005), "Liquidity and Asset Prices", Foundations and Trends in Finance.
- **Data requirement**: OHLCV (price + volume). Already have.
- **Signal type**: SIZING (reduce in fragile markets), FILTER
- **Difficulty**: Easy

### Signal 67: Intraday High-Low Range Ratio

- **Formula**: Range_ratio_t = (H_t - L_t) / (H_t - L_t)_{EMA_20}. Values > 1.5 indicate expansion; < 0.5 indicate compression. Also: directional range = (C_t - O_t) / (H_t - L_t) measures what fraction of the range was "used" directionally.
- **Complexity**: O(1) per bar
- **Causal mechanism**: Range expansion indicates increased participation and conviction. Range compression indicates indecision and accumulation. The directional range ratio tells you whether the range was consumed by a directional move (high ratio = strong conviction) or by whipsawing (low ratio = indecision). A bar with high total range but low directional ratio is a "rejection" bar indicating failed breakout. Consecutive compression bars followed by expansion is the classic volatility breakout pattern.
- **Citation**: Parkinson (1980), "The Extreme Value Method for Estimating the Variance of the Rate of Return", Journal of Business.
- **Data requirement**: OHLCV (OHLC). Already have.
- **Signal type**: ENTRY (breakout confirmation), FILTER
- **Difficulty**: Easy

### Signal 68: Frog-in-the-Pan (FIP) Indicator

- **Formula**: FIP = sign(r_{t,t-N}) * (% of same-sign returns in [t-N, t]) where r is the cumulative return over window N and % of same-sign returns measures discreteness. High FIP = continuous, gradual trend (many small same-sign returns). Low FIP = discrete, jumpy trend (large moves concentrated in few bars).
- **Complexity**: O(N) per window
- **Causal mechanism**: Da, Gurun & Warachka (2014) showed that gradual information diffusion (many small moves) leads to stronger momentum continuation than abrupt information arrival (few large moves). The "frog in the pan" slowly adjusts to gradual changes but reacts to sudden ones. In crypto, a steady 20% rise over 2 weeks (high FIP) has stronger forward momentum than a 20% jump in one day (low FIP) because the gradual trend indicates persistent buying pressure rather than a one-time event.
- **Citation**: Da, Gurun & Warachka (2014), "Frog in the Pan: Continuous Information and Momentum", Review of Financial Studies.
- **Data requirement**: OHLCV (returns). Already have.
- **Signal type**: ENTRY (momentum quality filter), FEATURE
- **Difficulty**: Easy

### Signal 69: Fractal Dimension (Box-Counting / Higuchi)

- **Formula**: Higuchi method: compute L(k) = (1/k) * sum |x(m+ik) - x(m+(i-1)k)| for multiple k values. FD = slope of log(L(k)) vs log(1/k). FD ~ 1.0 = smooth trend, FD ~ 1.5 = random walk, FD ~ 2.0 = space-filling (noisy/choppy).
- **Complexity**: O(N * K_max) where K_max is the maximum scale
- **Causal mechanism**: Fractal dimension characterizes the roughness of the price path. A trending market has low FD (smooth, nearly 1D path). A ranging/choppy market has high FD (rough, space-filling). FD is related to the Hurst exponent (FD = 2 - H) but computed differently and can disagree in non-ideal conditions. FD < 1.3 strongly favors trend-following strategies; FD > 1.7 strongly favors mean-reversion or staying flat.
- **Citation**: Higuchi (1988), "Approach to an Irregular Time Series on the Basis of the Fractal Theory", Physica D. Peters (1994), "Fractal Market Analysis", Wiley.
- **Data requirement**: OHLCV (close prices)
- **Signal type**: FILTER (strategy selection)
- **Difficulty**: Medium

### Signal 70: Detrended Fluctuation Analysis (DFA)

- **Formula**: 1) Compute cumulative sum of demeaned returns: Y(k) = sum_{i=1}^{k}(r_i - mean(r)). 2) Divide into non-overlapping segments of length n. 3) Fit linear trend in each segment, compute residuals. 4) F(n) = sqrt(mean(residuals^2)). 5) DFA exponent alpha = slope of log(F(n)) vs log(n). alpha < 0.5 = anti-correlated, alpha = 0.5 = random, alpha > 0.5 = long-range correlated.
- **Complexity**: O(N * S) where S is number of scales
- **Causal mechanism**: DFA is a more robust version of the Hurst exponent that handles non-stationarity. It directly measures long-range dependence in the return series. Unlike R/S analysis, DFA is not biased by short-term autocorrelation or trends. The DFA exponent changes over time and can be computed at multiple scales to reveal scale-dependent persistence. This is particularly useful for crypto where short-term and long-term dynamics can have opposite characteristics (short-term MR, long-term momentum or vice versa).
- **Citation**: Peng et al. (1994), "Mosaic Organization of DNA Nucleotides", Physical Review E. Kantelhardt et al. (2002), "Multifractal Detrended Fluctuation Analysis", Physica A.
- **Data requirement**: OHLCV (returns)
- **Signal type**: FILTER (persistence at target scale)
- **Difficulty**: Medium

### Signal 71: Copula-Based Tail Dependence (BTC-Altcoin)

- **Formula**: Fit a bivariate copula (Clayton, Gumbel, Student-t) to the joint distribution of (BTC returns, altcoin returns). Extract upper tail dependence lambda_U and lower tail dependence lambda_L. lambda_L > lambda_U means crashes are more correlated than rallies (asymmetric tail dependence).
- **Complexity**: O(N^2) for copula fitting via maximum likelihood
- **Causal mechanism**: Linear correlation misses the crucial question: are BTC and this altcoin more correlated in crashes or in rallies? Tail dependence answers this directly. If lower tail dependence is high (lambda_L > 0.5), the altcoin will crash WITH BTC during a BTC selloff -- no diversification when you need it most. If lambda_L is low, the altcoin provides crash protection. This should directly inform portfolio construction: avoid tokens with high lambda_L to BTC if you already hold BTC.
- **Citation**: Patton (2006), "Modelling Asymmetric Exchange Rate Dependence", International Economic Review. Bouri et al. (2019), "Modelling Long Memory Volatility in the Bitcoin Market", Economics Letters.
- **Data requirement**: OHLCV (returns for BTC + each token). Already have.
- **Signal type**: SIZING (portfolio construction), FILTER
- **Difficulty**: Hard

### Signal 72: Realized Semicovariance and Semivariance

- **Formula**: Semivariance_down = (1/N) * sum[min(r_t, 0)]^2. Semivariance_up = (1/N) * sum[max(r_t, 0)]^2. Semicovariance_down(X,Y) = (1/N) * sum[min(r_X,t, 0) * min(r_Y,t, 0)]. Ratio = semivar_down / semivar_up measures downside vs upside volatility.
- **Complexity**: O(N) per window
- **Causal mechanism**: Standard variance treats upside and downside moves symmetrically. Semivariance separates them. A high downside/upside semivariance ratio means the distribution of negative returns has higher variance than positive returns -- the asset is "riskier than it looks" from standard volatility. Rising downside semivariance warns of increasing left-tail risk even if total volatility is stable. This is a more nuanced risk measure than standard volatility for position sizing.
- **Citation**: Sortino & van der Meer (1991), "Downside Risk", Journal of Portfolio Management. Barndorff-Nielsen et al. (2010), "Measuring Downside Risk: Realised Semivariance", Volatility and Time Series Econometrics.
- **Data requirement**: OHLCV (returns). Already have.
- **Signal type**: SIZING (downside-aware risk scaling), FEATURE
- **Difficulty**: Easy

### Signal 73: Wold Decomposition / AR Residual Analysis

- **Formula**: Fit AR(p) model to returns: r_t = c + sum_{i=1}^{p} phi_i * r_{t-i} + e_t. The residuals e_t should be white noise. Compute: residual autocorrelation (Ljung-Box test), residual kurtosis, and residual ARCH effects (ARCH-LM test). Violations indicate model misspecification or regime change.
- **Complexity**: O(N * p) for AR fitting, O(N) for residual diagnostics
- **Causal mechanism**: The AR model captures the predictable (linear) component of returns. The residuals contain the unpredictable part. If residuals show structure (autocorrelation, ARCH effects), the AR model is missing something -- there is more predictability to extract. The ARCH-LM test specifically detects volatility clustering in residuals, indicating that a GARCH-type model would capture additional information. This is a model-diagnostic signal that identifies when a richer model is needed.
- **Citation**: Wold (1938), "A Study in the Analysis of Stationary Time Series". Box & Jenkins (1976), "Time Series Analysis: Forecasting and Control".
- **Data requirement**: OHLCV (returns). Already have.
- **Signal type**: FEATURE (model adequacy check)
- **Difficulty**: Easy

### Signal 74: GARCH(1,1) Conditional Volatility and Standardized Residuals

- **Formula**: sigma_t^2 = omega + alpha * r_{t-1}^2 + beta * sigma_{t-1}^2. Conditional volatility = sigma_t. Standardized residual = r_t / sigma_t. If the GARCH model is correctly specified, standardized residuals should be iid. Alpha + beta = volatility persistence; alpha/(1-beta) = long-run variance contribution of shocks.
- **Complexity**: O(N) per estimation (quasi-ML), O(1) per step for rolling forward
- **Causal mechanism**: GARCH captures volatility clustering -- the single most robust stylized fact of financial returns. High conditional volatility from GARCH is a more accurate risk estimate than rolling window realized volatility because it adapts faster to new information (exponential weighting vs equal weighting). The standardized residuals reveal the "true" return after removing time-varying volatility -- these should be used for momentum/MR signals rather than raw returns. A -3 standardized residual is far more significant than a -3% raw return.
- **Citation**: Bollerslev (1986), "Generalized Autoregressive Conditional Heteroskedasticity", Journal of Econometrics. Engle (1982), "Autoregressive Conditional Heteroscedasticity with Estimates of the Variance of United Kingdom Inflation", Econometrica.
- **Data requirement**: OHLCV (returns). Already have.
- **Signal type**: SIZING (GARCH vol for position sizing), FEATURE (standardized residuals)
- **Difficulty**: Medium

### Signal 75: HAR (Heterogeneous Autoregressive) Realized Volatility Model

- **Formula**: RV_t^(d) = alpha + beta_d * RV_{t-1}^(d) + beta_w * RV_{t-1}^(w) + beta_m * RV_{t-1}^(m) + e_t where RV^(d) = daily RV, RV^(w) = weekly average RV, RV^(m) = monthly average RV. Uses 3 horizons to capture heterogeneous information processing speeds.
- **Complexity**: O(N) per OLS estimation
- **Causal mechanism**: Different participants react to volatility at different horizons. Day traders react to daily vol, swing traders to weekly, and institutional investors to monthly. HAR captures this hierarchy and produces superior volatility forecasts compared to GARCH or simple rolling windows. The forecast directly informs position sizing and stop-loss placement. HAR consistently outperforms GARCH for realized volatility forecasting in academic comparisons.
- **Citation**: Corsi (2009), "A Simple Approximate Long-Memory Model of Realized Volatility", Journal of Financial Econometrics. Andersen et al. (2007), "Roughing It Up: Including Jump Components in the Measurement, Modeling, and Forecasting of Return Volatility", RES.
- **Data requirement**: Realized volatility at multiple horizons (from 1H or 1M data). Already have realized_vol in enriched data.
- **Signal type**: SIZING (volatility forecast for position sizing)
- **Difficulty**: Easy

### Signal 76: Jump Detection (Barndorff-Nielsen & Shephard)

- **Formula**: Z_t = (RV_t - BV_t) / sqrt((pi^2/5 + pi - 5) * max(1, TQ_t/BV_t^2) * BV_t) where BV = bipower variation = (pi/2) * sum |r_t| * |r_{t-1}|, TQ = tripower quarticity. Z > 2.33 (5% level) indicates a jump.
- **Complexity**: O(N) per window
- **Causal mechanism**: Jumps are discontinuous price movements caused by news, liquidation cascades, or flash crashes. Distinguishing jumps from continuous volatility is crucial because: (1) jumps mean-revert faster than continuous moves, (2) jump risk requires different hedging than diffusion risk, (3) post-jump dynamics differ from post-trend dynamics. Detecting a jump in real-time triggers a different response (wait for mean-reversion) than detecting a trend continuation (follow the trend).
- **Citation**: Barndorff-Nielsen & Shephard (2006), "Econometrics of Testing for Jumps in Financial Economics Using Bipower Variation", JFE.
- **Data requirement**: High-frequency returns (1H or finer). Already have 1H data. Better with 1M data.
- **Signal type**: ENTRY (post-jump mean-reversion), FILTER (jump vs trend)
- **Difficulty**: Medium

### Signal 77: Realized Correlation (Rolling BTC-Alt Pairwise)

- **Formula**: RealizedCorr_t = RealizedCov(BTC, ALT)_t / sqrt(RV_BTC_t * RV_ALT_t) where RealizedCov = sum(r_BTC * r_ALT) over intraday returns. Compute at daily frequency using 1H returns within each day.
- **Complexity**: O(N_intraday) per day per pair
- **Causal mechanism**: Realized correlation (computed from intraday data) is more responsive than rolling daily correlation. It captures correlation changes within a single day. Sudden spikes in realized correlation indicate contagion or systemic stress. Sudden drops indicate idiosyncratic events. Tracking realized correlation in real-time allows portfolio hedging adjustments within the day rather than waiting for the daily close.
- **Citation**: Andersen et al. (2003), "Modeling and Forecasting Realized Volatility", Econometrica. Barndorff-Nielsen & Shephard (2004), "Econometric Analysis of Realized Covariation", Econometrica.
- **Data requirement**: OHLCV (returns for BTC + altcoin at 1H frequency). Already have.
- **Signal type**: FILTER (diversification monitoring), SIZING
- **Difficulty**: Easy

### Signal 78: Lyapunov Exponent (Chaos Detection)

- **Formula**: lambda = lim_{n->inf} (1/n) * sum_{i=0}^{n-1} ln|f'(x_i)| estimated via Rosenstein's algorithm: track divergence of initially close trajectories in phase space. Positive lambda = chaos (sensitive dependence on initial conditions); negative lambda = stable attractor; zero = periodic.
- **Complexity**: O(N^2) for nearest-neighbor search in phase space. High cost.
- **Causal mechanism**: A positive Lyapunov exponent means the price dynamics are chaotic -- small perturbations lead to exponentially different outcomes. In this regime, prediction horizon is fundamentally limited (by 1/lambda). If lambda is large, only very short-term predictions are possible. If lambda is small but positive, medium-term predictions may work. If lambda is near zero or negative, the system is more predictable. This directly determines the maximum useful forecast horizon for any model.
- **Citation**: Rosenstein et al. (1993), "A Practical Method for Calculating Largest Lyapunov Exponents from Small Data Sets", Physica D. BenSaida (2015), "A Practical Test for Noisy Chaotic Dynamics", SoftwareX.
- **Data requirement**: OHLCV (returns or log prices). Long windows (500+ bars).
- **Signal type**: FILTER (predictability horizon)
- **Difficulty**: Hard

---

## 8. SUMMARY TABLES

### Implementation Priority Matrix

| Priority | Signal IDs | Rationale |
|----------|-----------|-----------|
| **P0 — Compute immediately** | 1, 2, 4, 5, 7, 8, 9, 10, 11, 16, 18, 19, 20, 21, 32, 42, 43, 44, 45, 46, 47, 49, 51, 63, 64, 65, 67, 68, 72, 77 | Easy, OHLCV-only, high value |
| **P1 — Compute soon** | 3, 6, 12, 17, 22, 27, 28, 30, 50, 53, 54, 55, 58, 59, 60, 61, 66, 69, 70, 73, 74, 75, 76 | Medium difficulty, high value |
| **P2 — Compute when ML ready** | 13, 14, 15, 23, 24, 25, 26, 29, 48, 56, 57, 62, 71, 78 | Hard, require specialized libraries or training |
| **P3 — Requires external data** | 31, 33, 34, 35, 36, 37, 38, 39, 40, 41, 52 | Need APIs for macro/on-chain/multi-exchange data |

### Signal Type Distribution

| Type | Count | Signal IDs |
|------|-------|-----------|
| FILTER | 52 | 1-4, 6-9, 11-22, 26-28, 30-42, 46-48, 50, 55, 57, 59, 60, 65-67, 69, 71, 76-78 |
| FEATURE | 30 | 1, 2, 10, 19, 25, 26, 29, 30, 44, 51-56, 58, 60-63, 66, 68, 72-74 |
| SIZING | 19 | 5-7, 15, 22, 43, 45-47, 54, 56, 57, 63, 64, 66, 71, 72, 75, 77 |
| ENTRY | 12 | 5, 8, 16, 19, 23, 40, 49, 59, 65, 67, 68, 76 |
| EXIT | 2 | 48, 8 |

### Data Requirement Summary

| Data Source | Signals | Available? |
|------------|---------|-----------|
| OHLCV (1H) | 1-12, 16-22, 27-30, 42-51, 53, 55, 58-78 | Yes (49 tokens) |
| 1-minute microstructure | 48, 53, 54, 76 | Yes (1m_cache) |
| Enriched daily features | 47, 48, 75 | Yes (all_tokens_enriched.parquet) |
| External macro (DXY, yields, VIX, SPX) | 33-38 | No — need API |
| External crypto (dominance, stablecoin supply) | 31, 39-41 | No — need API |
| Multi-exchange | 52 | No — need additional exchange data |

### Computational Complexity Tiers

| Tier | Signals | Notes |
|------|---------|-------|
| O(1) per bar | 16, 31, 32, 34-38, 43, 49, 51, 63-65, 67 | Trivial, real-time capable |
| O(N) per window | 1-3, 5, 7-12, 17, 20, 21, 27, 28, 42, 44-47, 50, 59, 66, 68, 72-77 | Fast rolling computation |
| O(N*log(N)) | 4, 6, 29, 30, 60, 61 | Moderate, sorting or FFT |
| O(N^2) | 14, 23-25, 52, 71, 78 | Expensive, may need optimization |
| O(N^3) or ML training | 13, 15, 22, 55, 56-58, 62 | Expensive, offline/batch computation |

---

## 9. IMPLEMENTATION NOTES

### Library Dependencies

```python
# Core scientific
numpy, scipy, pandas, statsmodels

# Volatility & microstructure
arch  # GARCH models

# Information theory
scipy.stats  # entropy, KL divergence
dit  # advanced information theory (transfer entropy, mutual information)
# or: jpype + Java JIDT library for transfer entropy

# Regime detection
hmmlearn  # Hidden Markov Models
ruptures  # changepoint detection (BOCPD, CUSUM, Bai-Perron)
bayesian_changepoint_detection  # Adams-MacKay BOCPD

# Time series features
pycatch22  # Catch22 features
sktime  # ROCKET, various TS features
nolds  # Hurst exponent, Lyapunov, DFA, sample entropy

# ML
scikit-learn  # PCA, Random Forest, Ridge
torch  # LSTM, autoencoder (if using deep learning)

# Wavelets
pywt  # discrete wavelet transform

# Copulas
copulas  # or scipy for basic copula fitting
```

### Rolling Window Recommendations

| Signal Category | Minimum Window | Recommended Window | Update Frequency |
|----------------|---------------|-------------------|-----------------|
| Moments (skew, kurt) | 50 bars | 100-200 bars (1H) | Every bar |
| Hurst/DFA | 200 bars | 500 bars | Every 24 bars |
| HMM/Regime | 500 bars | 1000+ bars | Every 24-168 bars |
| Transfer Entropy | 200 bars | 500 bars | Every 24 bars |
| PCA | 200 bars | 500 bars | Every 24-168 bars |
| Volatility (GARCH) | 250 bars | 500+ bars | Every bar |
| Microstructure (spreads) | 20 bars | 50-100 bars | Every bar |
| Macro signals | N/A | N/A | Daily |

### Avoiding Lookahead Bias

For all signals, ensure:
1. Rolling windows use only data available at time t (no future data)
2. Model training windows end before the evaluation period
3. Parameters estimated on training data are frozen during walk-forward evaluation
4. External data timestamps are aligned to availability time (not event time) — e.g., M2 data is released with a 2-week lag
5. Signals requiring fitting (HMM, GARCH, PCA) use expanding or rolling windows with a gap between training and prediction

### Signal Combination Framework

Signals should be combined in a hierarchical structure:
1. **Layer 1 — Regime (FILTER)**: HMM state, Hurst exponent, composite regime classifier determine which sub-strategy is active
2. **Layer 2 — Opportunity (FILTER)**: Dispersion, breadth, absorption ratio determine whether alpha is available
3. **Layer 3 — Direction (ENTRY)**: Momentum/MR signals specific to the active regime generate trade direction
4. **Layer 4 — Timing (ENTRY)**: Order flow, VPIN, microstructure signals refine entry timing
5. **Layer 5 — Risk (SIZING)**: CVaR, GARCH vol, Amihud illiquidity, tail dependence determine position size
6. **Layer 6 — Monitoring (EXIT)**: Changepoint detection, autoencoder anomaly, drawdown speed trigger exits
