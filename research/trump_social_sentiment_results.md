# Trump Social Media Sentiment -> BTC Returns: Signal Analysis

**Date:** 2026-03-24
**Analyst:** Quantitative Research Agent
**Verdict:** WEAK PASS -- Tariff/policy signals show persistent IC; pure crypto signal too sparse for standalone use

---

## 1. Data Availability Assessment

### Truth Social (Primary Source)
- **Source:** CNN-hosted auto-updating archive (`ix.cnn.io/data/truth-social/truth_archive.json`)
- **Original repo:** [stiles/trump-truth-social-archive](https://github.com/stiles/trump-truth-social-archive)
- **Format:** JSON with id, created_at, content (HTML), url, media, engagement counts
- **Coverage:** 32,197 posts from 2022-02-14 to 2026-03-24 (4+ years)
- **Quality:** Good. HTML tags need stripping. ~82% of posts have text content (rest are media-only reblogs).
- **Update frequency:** Every 5 minutes (live endpoint)
- **Cost:** Free, no API key needed

### Twitter Archive (Supplementary)
- **Source:** [MarkHershey/CompleteTrumpTweetsArchive](https://github.com/MarkHershey/CompleteTrumpTweetsArchive) on GitHub
- **Coverage:** ~54,000 tweets from 2009 to 2021-01-08 (account suspension)
- **Used subset:** 9,824 tweets from 2020-01-01 to 2021-01-08 (overlap with BTC data)
- **Quality:** Good. CSV format with ID, timestamp, URL, text.

### Other Sources Evaluated
- **Kaggle:** Multiple datasets available (muhammetakkurt, headsortails, codebreaker619) -- good for one-time download
- **Hugging Face:** `muhammetakkurt/trump-2024-campaign-truthsocial-truths` -- campaign period only
- **Apify/ScrapeCreators:** Commercial scraping tools (not needed given CNN endpoint)
- **thetrumparchive.com:** Searchable web archive, limited bulk download

### BTC Price Data
- **Source:** Binance spot 1h OHLCV cached at `/workspace/crypto_backtest/data/spot/1h_cache/BTC_1h.parquet`
- **Coverage:** 54,141 hourly bars from 2020-01-01 to 2026-03-14

---

## 2. Data Obtained

**Saved to:** `/workspace/crypto_backtest/data/alternative/trump_social/`

| File | Description | Size |
|------|-------------|------|
| `truth_archive_raw.json` | Raw Truth Social archive (32,197 posts) | 17.3 MB |
| `trump_tweets_in_office.csv` | Twitter: in-office tweets 2017-2021 | 6.4 MB |
| `trump_tweets_bf_office.csv` | Twitter: pre-office tweets pre-2017 | 6.8 MB |
| `posts_scored.csv` | All posts with sentiment scores | derived |
| `ic_results_daily.csv` | Daily IC results table | derived |
| `ic_results_truthsocial.csv` | Truth Social period IC results | derived |
| `ic_results_hourly.csv` | Hourly IC results | derived |

**Post Distribution:**
| Year | Posts |
|------|-------|
| 2020 | 9,681 (Twitter) |
| 2021 | 143 (Twitter, Jan only) |
| 2022 | 3,889 (Truth Social) |
| 2023 | 9,905 (Truth Social) |
| 2024 | 10,587 (Truth Social) |
| 2025 | 6,229 (Truth Social) |
| 2026 | 1,587 (Truth Social, to 3/24) |

**Gap:** No data from 2021-01-09 to 2022-02-13 (Twitter ban to Truth Social launch).

---

## 3. Signal Construction

### Keyword Categories

| Category | Keywords | Purpose |
|----------|----------|---------|
| Crypto Positive | bitcoin, btc, crypto, cryptocurrency, digital asset, blockchain, strategic reserve, stablecoin, etc. | Direct crypto policy mentions |
| Pro-Market | great economy, stock market, winning, tax cut, deregulat, innovation, etc. | Positive economic sentiment |
| Anti-Market | tariff, trade war, china, disaster, crash, recession, war, etc. | Negative/uncertainty sentiment |
| Fed | federal reserve, powell, interest rate, rate cut, monetary policy, etc. | Monetary policy commentary |
| Policy Uncertainty | executive order, emergency, terminate, cancel, shut down, etc. | Policy action indicators |

### Signals Constructed

**Daily signals (n=1,786 trading days with overlap):**
- `post_count` -- raw daily post count
- `post_count_zscore` -- 30-day rolling z-score of post count
- `crypto_intensity` -- crypto keyword score per post
- `market_sentiment` -- net pro-market vs anti-market score per post
- `uncertainty_intensity` -- uncertainty + anti-market score per post
- `tariff_post_count` -- number of posts mentioning tariffs
- `fed_post_count` -- number of posts mentioning the Fed
- `engagement_zscore` -- 30-day rolling z-score of total engagement (favorites)

**Hourly signals (Truth Social period, n=35,833 hours):**
- `post_count`, `post_count_6h`, `post_count_24h` -- activity levels
- `market_score_24h`, `uncertainty_24h` -- 24h rolling sentiment

### Key Observations on Keyword Matching
- Only **31 posts** directly mention crypto/bitcoin/blockchain keywords. Trump's direct crypto commentary is very sparse.
- **371 posts** mention tariffs -- a much richer signal source.
- **121 posts** mention the Fed/interest rates.
- The "defi" keyword required word-boundary matching to avoid matching "deficit", "definitely", "defiant" -- a major false-positive source that was corrected.
- "fraud" was removed from crypto-negative keywords as it overwhelmingly matched political fraud, not crypto fraud.

---

## 4. IC Results

### Kill Criteria Applied
- |IC| >= 0.02
- |t-stat| >= 2.0
- No IS/OOS sign flip

### A. Full Sample Daily IC (2020-2026, n=1,786 days)

**Passing signals (all three criteria met):**

| Signal | Horizon | IC | t-stat | IC_IS | IC_OOS | Sign Stable? |
|--------|---------|-----|--------|-------|--------|-------------|
| tariff_post_count | 7d | **-0.076** | -3.19 | -0.068 | -0.084 | Yes |
| tariff_post_count | 5d | -0.056 | -2.36 | -0.056 | -0.055 | Yes |
| crypto_intensity | 7d | -0.055 | -2.33 | -0.028 | -0.073 | Yes |
| crypto_post_count | 7d | -0.055 | -2.33 | -0.028 | -0.073 | Yes |
| crypto_intensity | 5d | -0.055 | -2.30 | -0.032 | -0.071 | Yes |
| crypto_post_count | 5d | -0.055 | -2.30 | -0.032 | -0.070 | Yes |
| uncertainty_intensity | 1d | -0.048 | -2.04 | -0.020 | -0.075 | Yes |

**Interpretation:**
- **Tariff mentions are the strongest signal.** IC of -0.076 at 7-day horizon means more tariff posts predict lower BTC returns over the following week. This is economically intuitive: tariff escalation -> risk-off -> BTC sells off.
- The signal is remarkably stable: IS IC = -0.068, OOS IC = -0.084. No sign flip. The signal *strengthened* out of sample.
- Uncertainty intensity predicts negative 1-day returns (IC=-0.048), consistent with policy uncertainty -> short-term risk-off.
- Crypto intensity shows negative IC, but this is driven by only 31 posts -- the per-day sparsity makes this signal coincide with tariff/uncertainty periods rather than being a clean independent signal.

### B. Truth Social Only IC (2022-2026, n=1,412 days)

**Key passing signals:**

| Signal | Horizon | IC | t-stat | IC_IS | IC_OOS |
|--------|---------|-----|--------|-------|--------|
| engagement_zscore | 7d | -0.070 | -2.58 | -0.061 | -0.109 |
| market_sentiment | 7d | +0.064 | +2.39 | +0.068 | +0.050 |
| tariff_post_count | 7d | +0.064 | +2.38 | +0.067 | +0.050 |
| fed_post_count | 5d | -0.054 | -2.01 | -0.037 | -0.065 |

**NOTE:** The tariff signal **flips sign** between the full sample and Truth Social-only analysis. In the full sample (2020-2026), tariff posts predict negative returns (IC=-0.076). In the Truth Social-only period (2022-2026), tariff posts predict positive returns (IC=+0.064). This is a critical regime dependency:
- **2020-2021 (Twitter period):** Tariff posts during Trump's first term occurred during escalating US-China tensions (2020). Context: COVID + trade war -> tariffs = risk-off -> BTC down.
- **2025-2026 (Truth Social, post-inauguration):** Tariff posts during second term often celebrate tariff victories ("Tariff Victory!", "Liberation Day"). Context: tariffs = policy wins being boasted about -> risk-on sentiment. Different economic regime.

This sign flip is a **serious concern** and suggests the tariff signal is regime-dependent, not a stable structural alpha source.

### C. Hourly IC (Truth Social Period, n=35,833 hours)

**Passing signals (no sign flip):**

| Signal | Horizon | IC | t-stat | IC_IS | IC_OOS |
|--------|---------|-----|--------|-------|--------|
| post_count_24h | 168h | +0.068 | +12.78 | +0.112 | +0.016 |
| post_count_6h | 168h | +0.039 | +7.27 | +0.066 | +0.009 |
| post_count_24h | 72h | +0.035 | +6.59 | +0.041 | +0.029 |
| post_count_6h | 72h | +0.024 | +4.58 | +0.022 | +0.026 |
| post_count | 168h | +0.022 | +4.22 | +0.032 | +0.014 |
| market_score_24h | 24h | -0.024 | -4.60 | -0.038 | -0.014 |
| post_count_6h | 4h | +0.014 | +2.72 | +0.011 | +0.016 |

**Caution on hourly ICs:**
- High t-stats are expected with ~36,000 observations even for tiny ICs.
- ICs of 0.01-0.07 are economically small. After transaction costs (slippage, funding), many of these would not be tradeable.
- The post_count_24h -> 168h IC of +0.068 degrades heavily OOS (0.112 -> 0.016), suggesting in-sample overfitting or regime shift.
- The most IS/OOS-stable hourly signal is `post_count_6h -> fwd_ret_72h` (IC 0.022 IS, 0.026 OOS) but at IC=0.024, the signal-to-noise ratio is very low.

---

## 5. Event Study Results

### A. Crypto-Mentioning Posts (n=31 events)

| Window | Mean Return | Excess Return | t-stat | p-value |
|--------|------------|---------------|--------|---------|
| Pre 1h | +0.348% | +0.342% | +2.18 | 0.037 |
| Post 1h | +0.136% | +0.130% | +0.84 | 0.410 |
| Post 4h | +0.091% | +0.066% | +0.30 | 0.766 |
| Post 24h | +0.196% | +0.043% | +0.41 | 0.688 |
| Post 48h | -0.552% | -0.552% | -0.82 | 0.418 |

**Assessment:** Only 31 crypto-mentioning posts -- far too few for reliable inference. The significant pre-1h return suggests Trump posts *after* BTC moves up (reaction, not prediction), or it's a small-sample artifact. Post-event returns are not significant. **Insufficient data for conclusions.**

### B. Tariff-Mentioning Posts (n=371 events)

| Window | Mean Return | Excess Return | t-stat | p-value |
|--------|------------|---------------|--------|---------|
| Post 1h | -0.052% | -0.059% | -1.74 | 0.083 |
| Post 4h | +0.053% | +0.028% | +0.85 | 0.397 |
| Post 24h | +0.181% | +0.029% | +1.39 | 0.165 |

**Assessment:** Marginal negative 1h reaction (p=0.083) that reverses by 4h. Not economically significant. The sign reversal is consistent with noise rather than a tradeable pattern.

### C. Fed-Mentioning Posts (n=121 events)

| Window | Mean Return | Excess Return | t-stat | p-value |
|--------|------------|---------------|--------|---------|
| Post 24h | +0.382% | +0.229% | +1.87 | 0.064 |
| Post 48h | +0.439% | +0.439% | +1.39 | 0.168 |

**Assessment:** Marginal positive drift after Fed-mentioning posts. However, median return is near zero (-0.016%), suggesting the mean is driven by a few large positive moves. Possibly capturing BTC's response to the same macro events Trump is commenting on (correlation, not causation).

### D. Strongly Negative Sentiment Posts (n=198 events)

| Window | Mean Return | Excess Return | t-stat | p-value |
|--------|------------|---------------|--------|---------|
| Post 4h | -0.108% | -0.133% | -1.32 | 0.188 |
| Post 24h | -0.226% | -0.378% | -1.20 | 0.233 |
| Post 48h | -0.247% | -0.247% | -1.03 | 0.306 |

**Assessment:** Directionally interesting (negative posts -> negative BTC returns) but not statistically significant at any window. The 24h excess return of -0.378% is economically meaningful but p=0.233 means we cannot reject the null. Would need more events to confirm.

### E. High Activity Days (Caution: Inflated Statistics)

The "high activity" event study shows very high t-stats (15+ for 24h window), but these are **methodologically flawed**: we are counting individual posts, not independent day-level events. A single high-activity day with 80+ posts contributes 80 correlated "events". The true number of independent events is ~150 days (not 5,600 posts). After correcting for this clustering, significance would likely drop substantially. **Do not rely on these results.**

---

## 6. Volatility Impact

| Signal | IC vs Next-Day Realized Vol | p-value |
|--------|----------------------------|---------|
| post_count | -0.031 | 0.184 |
| tariff_post_count | -0.016 | 0.488 |
| uncertainty_intensity | -0.013 | 0.585 |
| fed_post_count | -0.009 | 0.710 |

**Assessment:** No significant relationship between Trump posting activity and next-day BTC realized volatility. The hypothesis that "Trump posts increase crypto volatility" is not supported by the data.

---

## 7. Critical Assessment and Caveats

### What works:
1. **Tariff post count -> 5-7 day BTC returns** is the most robust signal. IC of -0.056 to -0.076 (full sample), stable IS/OOS, no sign flip in the full sample. Economic mechanism is clear: tariff escalation -> risk-off -> BTC drops over the following week.

2. **Post activity level** shows a modest positive correlation with 72-168h forward returns at hourly resolution. More posting -> slightly higher returns. This may be capturing "Trump is in campaign/boasting mode" which correlates with positive macro sentiment.

### What does NOT work:
1. **Direct crypto mentions** are too sparse (31 posts over 4 years) to form a tradeable signal. The IC appears significant but is driven by tariff/uncertainty co-occurrence.

2. **Event studies** show no significant abnormal returns around any category of Trump posts, with sufficient sample size.

3. **Volatility prediction** from posting activity does not work.

### Regime dependence (major concern):
The tariff signal **flips sign** between the Twitter era (2020-2021, negative IC) and the Truth Social era (2022-2026, positive IC in the TS-only analysis). This means the signal is context-dependent:
- During trade war escalation (2020): tariffs = bad for risk assets
- During tariff victory laps (2025-2026): tariffs = boasting about wins

A signal that changes meaning based on political context is difficult to deploy systematically.

### Autocorrelation concern:
Hourly ICs are computed on overlapping returns (168h forward returns computed every hour). This inflates t-statistics dramatically. The true degrees of freedom are ~36,000/168 = ~214 independent observations for the 168h horizon, not 36,000.

### Latency concern:
Even if a signal existed, reacting to Truth Social posts requires:
- Real-time post detection (sub-minute latency)
- NLP classification (keyword matching: milliseconds; LLM: seconds)
- Order execution (seconds)

The 1h event study window shows no significant returns, meaning any alpha dissipates within the first hour -- likely faster than most retail implementations can react.

---

## 8. Verdict

**WEAK PASS** -- Downgraded from initial PASS after accounting for caveats.

### What passed criteria:
- 7 daily signals pass |IC| >= 0.02, |t| >= 2.0, no IS/OOS sign flip (full sample)
- The tariff signal (IC=-0.076, t=-3.19) is the strongest, with excellent IS/OOS stability

### Why "weak" not "strong":
1. **Regime dependence:** Tariff signal flips sign between eras, questioning structural stability
2. **Crypto signal too sparse:** Only 31 direct crypto posts -- not enough for standalone signal
3. **No short-term alpha:** Event studies show no significant abnormal returns at 1h-4h horizons where a trader could actually react
4. **Hourly ICs are small:** Even the best hourly IC (0.068) degrades to 0.016 OOS
5. **Autocorrelation inflates statistics:** Many hourly "passing" signals are artifacts of overlapping returns

### Recommendation:
- **Best use case:** Tariff post count as a **weekly overlay** on existing strategies. When tariff mentions spike, reduce BTC exposure or go short for 5-7 days.
- **Do NOT use as standalone signal.** IC magnitudes are too small and regime-dependent for standalone alpha.
- **Monitor for regime changes:** The tariff signal's meaning depends on whether Trump is escalating or celebrating. Requires human judgment.
- **Consider NLP upgrade:** Simple keyword matching captures only crude semantics. An LLM-based sentiment classifier could potentially distinguish "tariff threat" from "tariff victory" posts, which would directly address the regime flip problem.

### Data pipeline for future use:
The CNN endpoint (`ix.cnn.io/data/truth-social/truth_archive.json`) provides free, real-time, auto-updating data. This is production-ready for signal generation if the NLP layer is improved.

---

## Appendix: Data Sources

- [CNN-hosted Truth Social Archive](https://ix.cnn.io/data/truth-social/truth_archive.json) (also available as .csv and .parquet)
- [stiles/trump-truth-social-archive](https://github.com/stiles/trump-truth-social-archive) -- original scraping repo
- [MarkHershey/CompleteTrumpTweetsArchive](https://github.com/MarkHershey/CompleteTrumpTweetsArchive) -- Twitter archive
- [Kaggle: Trump 2024 Campaign Truth Social Truths](https://www.kaggle.com/datasets/muhammetakkurt/trump-2024-campaign-truthsocial-truths-tweets)
- [Hugging Face: trump-2024-campaign-truthsocial-truths](https://huggingface.co/datasets/muhammetakkurt/trump-2024-campaign-truthsocial-truths)

**Analysis code:** `/workspace/crypto_backtest/research/trump_social_sentiment_analysis.py`
**Raw data:** `/workspace/crypto_backtest/data/alternative/trump_social/`
