# Indicator Relationship and Causal Analysis

*Generated from 49 tokens of 1H data aggregated to daily timeframe*
*Date range: 2024-01-01 to 2026-01-31 (762 trading days)*

---

## 1. Cross-Correlation Matrix (BTC Daily)

**18 indicators analyzed** including base technical indicators and enriched microstructure signals.

### Redundant Pairs (|corr| > 0.7) -- Consider dropping one from each pair

| Indicator A | Indicator B | Correlation | Recommendation |
|---|---|---|---|
| RSI | BB_pct | **0.952** | Near-identical; keep RSI (more interpretable) |
| MACD_hist | BB_pct | 0.765 | Drop BB_pct (redundant with RSI) |
| RSI | MACD_hist | 0.752 | Keep both (different use cases) |
| vol_ratio | realized_vol | 0.747 | Drop one; vol_ratio is simpler |
| vol_ratio | parkinson_vol | 0.747 | parkinson_vol ~ realized_vol (0.992!) |
| realized_vol | parkinson_vol | **0.992** | Nearly identical; keep realized_vol |
| ATR_pct | vol_20 | 0.805 | Both measure volatility; keep ATR_pct (price-relative) |
| ret_1 | vwap_deviation | 0.827 | Both measure same-day price move; keep ret_1 |
| taker | taker_buy_ratio | **1.000** | Identical computation; drop one |

### Truly Independent Indicators (max |corr| < 0.5 with all others)

| Indicator | Max |corr| with any other |
|---|---|
| **VPIN** | 0.310 |
| **amihud_1m** | 0.337 |
| **intraday_skew** | 0.323 |
| **intraday_kurtosis** | 0.310 |

### Key Takeaway: Optimal Non-Redundant Indicator Set

After removing redundancies, the **independent information sources** are:
1. **RSI** (momentum/mean-reversion) -- subsumes BB_pct
2. **ADX** (trend strength) -- moderately correlated with BB_width (0.68)
3. **vol_ratio** (volume surge) -- subsumes realized_vol, parkinson_vol
4. **ATR_pct** (volatility relative to price) -- subsumes vol_20
5. **ret_1** (daily return) -- subsumes vwap_deviation
6. **taker** (order flow) -- taker_buy_ratio is identical
7. **VPIN** (informed trading probability) -- truly independent
8. **amihud_1m** (illiquidity) -- truly independent
9. **intraday_skew** (return distribution asymmetry) -- truly independent
10. **intraday_kurtosis** (tail thickness) -- truly independent

---

## 2. Lead-Lag Analysis (Forward Return Correlations)

**Question: Which indicators PREDICT future returns vs merely REACT to past prices?**

### Average Information Coefficient (IC) Across All 49 Tokens

Ranked by predictive power (average |IC| across 1-5 day forward returns):

| Rank | Indicator | Fwd 1d | Fwd 2d | Fwd 3d | Fwd 5d | Avg |IC| | Verdict |
|---|---|---|---|---|---|---|---|
| 1 | **ADX** | +0.050 | +0.066 | +0.072 | +0.079 | **0.067** | PREDICTIVE |
| 2 | **realized_vol** | +0.017 | +0.067 | +0.074 | +0.053 | **0.053** | PREDICTIVE |
| 3 | **parkinson_vol** | +0.017 | +0.062 | +0.070 | +0.046 | **0.049** | WEAK |
| 4 | **BB_width** | +0.032 | +0.040 | +0.043 | +0.042 | **0.039** | WEAK |
| 5 | BB_pct | -0.020 | -0.027 | -0.030 | -0.038 | 0.029 | WEAK |
| 6 | vol_ratio | -0.016 | -0.025 | -0.032 | -0.033 | 0.027 | WEAK |
| 7 | taker | +0.035 | +0.026 | +0.016 | +0.008 | 0.021 | WEAK |
| 8 | intraday_kurtosis | -0.041 | +0.017 | +0.011 | +0.004 | 0.018 | NOISE |
| 9 | MACD_hist | -0.012 | -0.016 | -0.015 | -0.012 | 0.014 | NOISE |
| 10 | intraday_skew | +0.041 | -0.002 | -0.002 | -0.005 | 0.013 | NOISE |
| 11 | VPIN | -0.026 | -0.015 | -0.006 | +0.001 | 0.012 | NOISE |
| 12 | ATR_pct | +0.004 | +0.005 | -0.007 | -0.030 | 0.012 | NOISE |
| 13 | ret_1 | +0.017 | -0.003 | -0.011 | -0.003 | 0.009 | NOISE |
| 14 | vwap_deviation | -0.007 | -0.008 | -0.009 | +0.010 | 0.008 | NOISE |
| 15 | vol_20 | +0.003 | +0.006 | -0.002 | -0.018 | 0.007 | NOISE |
| 16 | RSI | +0.009 | +0.000 | -0.005 | -0.003 | 0.004 | NOISE |

### Key Per-Token Findings

**BTC:**
- vwap_deviation has strongest 1d IC (-0.107) -- mean-reversion signal
- BB_width is consistently predictive across all horizons (+0.07 to +0.10)

**ETH:**
- intraday_kurtosis is a strong 1d negative predictor (-0.099) -- fat tail days predict reversals
- ADX + BB_width are the dominant multi-day predictors

**SOL:**
- ADX has the strongest 5d signal (-0.113) -- trend strength predicts reversals in SOL
- parkinson_vol and realized_vol are leading indicators of forward returns

### Critical Insight

**ADX is the single most predictive indicator** across all tokens and horizons. Its IC *increases* with horizon (0.05 at 1d to 0.08 at 5d), suggesting it captures regime persistence. High ADX predicts positive forward returns -- trending markets tend to continue.

**RSI is nearly useless as a standalone predictor** (|IC| = 0.004). Despite being the most popular indicator in crypto, it has essentially zero forward-return predictability on aggregate.

**Taker buy ratio decays rapidly** -- useful at 1d (+0.035) but meaningless by 5d (+0.008), suggesting order flow is a very short-term signal.

---

## 3. Regime-Conditional Indicator Effectiveness

**Market regime classification** (based on BTC 20-day rolling returns and volatility):

| Regime | Days | % of Sample |
|---|---|---|
| Range | 395 | 51.8% |
| Uptrend | 148 | 19.4% |
| Quiet | 137 | 18.0% |
| Downtrend | 74 | 9.7% |
| Crisis | 8 | 1.0% |

### Best Indicators by Regime

#### UPTREND (148 days)
| Rank | Indicator | IC | Interpretation |
|---|---|---|---|
| 1 | ATR_pct | -0.072 | Low volatility predicts continuation |
| 2 | vol_ratio | +0.062 | Volume surges predict more upside |
| 3 | BB_pct | +0.061 | Overbought predicts MORE upside (momentum) |
| 4 | RSI | +0.058 | Same -- momentum dominates mean-reversion |
| 5 | vol_20 | -0.057 | Lower vol predicts further gains |

**Uptrend insight:** Momentum indicators (RSI, BB_pct) work WITH trend, not against it. Mean-reversion signals are contrarian losers in uptrends.

#### DOWNTREND (74 days)
| Rank | Indicator | IC | Interpretation |
|---|---|---|---|
| 1 | **RSI** | **-0.145** | Higher RSI predicts more downside (fading bounces) |
| 2 | vol_ratio | +0.134 | Volume spikes predict bottom/reversal |
| 3 | realized_vol | +0.133 | High realized vol signals capitulation/bounce |
| 4 | parkinson_vol | +0.129 | Same signal as realized_vol |
| 5 | MACD_hist | -0.124 | MACD divergence accelerates in downtrends |

**Downtrend insight:** RSI becomes the BEST indicator in downtrends (IC=-0.145), but in the OPPOSITE direction from textbook usage. High RSI bounces in downtrends are sell signals, not buy signals. Volume/volatility spikes signal bottoming.

#### RANGE (395 days -- majority of time)
| Rank | Indicator | IC | Interpretation |
|---|---|---|---|
| 1 | **intraday_kurtosis** | -0.072 | Fat-tail days predict mean-reversion |
| 2 | **intraday_skew** | +0.070 | Positive skew predicts continuation |
| 3 | MACD_hist | -0.046 | MACD momentum mean-reverts in ranges |
| 4 | vwap_deviation | -0.038 | Deviation from VWAP mean-reverts |
| 5 | ADX | +0.033 | Modest trend strength persistence |

**Range insight:** Enriched microstructure signals (intraday_kurtosis, intraday_skew) are the BEST indicators in range markets, outperforming all traditional technical indicators.

#### QUIET (137 days)
| Rank | Indicator | IC | Interpretation |
|---|---|---|---|
| 1 | ret_1 | +0.074 | Short-term momentum continues in quiet markets |
| 2 | vwap_deviation | +0.052 | VWAP deviation predicts continuation |
| 3 | taker | +0.051 | Order flow matters more when vol is low |
| 4 | taker_buy_ratio | +0.051 | (identical to taker) |
| 5 | vol_ratio | +0.045 | Volume spikes in quiet markets are informative |

**Quiet insight:** In low-vol environments, short-term momentum and microstructure (order flow) become the dominant signals.

---

## 4. Indicator Combination Synergies

### Single Condition Forward 1d Returns

| Condition | Mean Return | Win Rate | Avg Occurrences | Tokens |
|---|---|---|---|---|
| ADX_weak (< 15) | **-0.758%** | 44.7% | 17.3 | 30 |
| taker_bear (< 0.45) | -0.495% | 47.9% | 47.2 | 45 |
| vol_ratio_lo (< 0.5) | -0.495% | 47.7% | 100.6 | 49 |
| taker_bull (> 0.55) | +0.419% | 53.3% | 33.0 | 19 |
| BB_squeeze | -0.415% | 46.4% | 160.3 | 49 |
| BB_pct_high (> 0.9) | +0.413% | 49.4% | 104.9 | 49 |
| RSI_high (> 70) | +0.396% | 49.3% | 84.9 | 49 |
| BB_pct_low (< 0.1) | +0.355% | 54.0% | 82.8 | 49 |
| vol_ratio_hi (> 2x) | +0.352% | 48.9% | 63.9 | 49 |
| RSI_low (< 30) | +0.352% | 54.0% | 88.4 | 49 |

### Top 2-Indicator Combinations

| Combo | Mean Ret | Win Rate | Avg Count | Tokens |
|---|---|---|---|---|
| **RSI_high + ret_neg** | **+1.606%** | 54.0% | 6.9 | 22 |
| **ADX_weak + MACD_neg** | **-1.582%** | 37.0% | 9.1 | 29 |
| RSI_low + BB_pct_high | -1.410% | 36.4% | 8.0 | 32 |
| **RSI_low + MACD_pos** | **+1.366%** | 58.3% | 10.6 | 27 |
| taker_bull + ret_pos | +1.183% | 52.1% | 11.9 | 15 |
| vol_ratio_lo + taker_bear | -1.156% | 42.3% | 11.0 | 35 |
| MACD_pos + taker_bull | +1.026% | 56.8% | 18.7 | 21 |
| taker_bear + BB_squeeze | -1.022% | 43.6% | 14.9 | 36 |
| **vol_ratio_hi + BB_pct_low** | **+0.988%** | **58.4%** | 11.2 | 45 |
| **vol_ratio_hi + ret_neg** | **+0.921%** | **56.2%** | 18.9 | 49 |

### Synergy Analysis -- Combos That Beat Both Singles

| Combo | Combo % | Single 1 % | Single 2 % | Synergy | Win Rate |
|---|---|---|---|---|---|
| RSI_low + BB_pct_high | -1.410 | +0.352 | +0.413 | **+1.762** | 36.4% |
| RSI_low + ret_pos | -1.079 | +0.352 | +0.175 | +1.254 | 37.2% |
| RSI_high + ret_neg | +1.606 | +0.396 | -0.133 | +1.210 | 54.0% |
| RSI_low + MACD_pos | +1.366 | +0.352 | -0.072 | +1.014 | 58.3% |
| ADX_weak + MACD_neg | -1.582 | -0.758 | -0.146 | +0.824 | 37.0% |
| taker_bull + ret_pos | +1.183 | +0.419 | +0.175 | +0.765 | 52.1% |
| vol_ratio_hi + BB_pct_low | +0.988 | +0.352 | +0.355 | +0.633 | 58.4% |
| vol_ratio_hi + ret_neg | +0.921 | +0.352 | -0.133 | +0.570 | 56.2% |

### Best Actionable Combos (high synergy + sufficient sample + good win rate)

#### LONG Signals (buy):
1. **RSI_low + MACD_pos** -- Mean +1.37%, WR 58.3%, 10.6 avg occurrences
   - RSI oversold but MACD turning positive = dip buy with momentum confirmation
   - Synergy: +1.01% over best single

2. **vol_ratio_hi + BB_pct_low** -- Mean +0.99%, WR 58.4%, 11.2 avg occurrences (45 tokens)
   - Volume spike at bottom of Bollinger = capitulation buy
   - Most robust combo (seen in 45/49 tokens)

3. **vol_ratio_hi + ret_neg** -- Mean +0.92%, WR 56.2%, 18.9 avg occurrences (49 tokens)
   - Volume spike on a down day = reversal
   - Universal (all 49 tokens), most frequent

4. **RSI_high + ret_neg** -- Mean +1.61%, WR 54.0%, 6.9 avg occurrences
   - Highest return but lowest sample size -- may be overfit

#### SHORT/AVOID Signals:
1. **ADX_weak + MACD_neg** -- Mean -1.58%, WR 37.0%, 9.1 avg occurrences
   - No trend + negative momentum = continued drift down

2. **vol_ratio_lo + taker_bear** -- Mean -1.16%, WR 42.3%, 11.0 avg occurrences
   - Low volume + bearish order flow = weakness

3. **taker_bear + BB_squeeze** -- Mean -1.02%, WR 43.6%, 14.9 avg occurrences
   - Bearish flow during squeeze = downside breakout likely

---

## Summary of Key Findings

### 1. Indicator Redundancy
- **RSI and BB_pct are 95% correlated** -- using both wastes model capacity
- **realized_vol and parkinson_vol are 99% correlated** -- use one
- **taker and taker_buy_ratio are identical** -- same computation
- **4 truly independent signals**: VPIN, amihud_1m, intraday_skew, intraday_kurtosis

### 2. Predictive Power
- **ADX is the best predictor** (IC=0.067) and improves with horizon
- **RSI is the worst predictor** on aggregate (IC=0.004) -- massively overused
- Enriched signals (realized_vol, parkinson_vol) are the 2nd-3rd best predictors
- Order flow (taker) is only useful at 1-day horizon, decays rapidly

### 3. Regime-Dependent Usage
| Regime | Use These Indicators | Avoid These |
|---|---|---|
| Uptrend | vol_ratio, BB_pct (momentum) | Mean-reversion signals |
| Downtrend | RSI (contrarian), vol_ratio (capitulation) | MACD (lagging) |
| Range | intraday_kurtosis, intraday_skew | Most traditional indicators |
| Quiet | ret_1 (momentum), taker (order flow) | Volatility indicators |

### 4. Best Combinations
- **For buying dips**: RSI_low + MACD_pos (58% WR, +1.37% avg)
- **Most robust buy**: vol_ratio_hi + BB_pct_low (58% WR, +0.99%, 45 tokens)
- **For avoiding losses**: ADX_weak + MACD_neg (37% WR, -1.58%)
- **Combining indicators adds 0.5-1.5% synergy** over single conditions
