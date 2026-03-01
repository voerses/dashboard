# On-Chain & Derivatives Indicator Catalog

*Exhaustive reference for systematic crypto swing trading*
*Timeframe: 1H candles, 18-720 hour holds, $200K portfolio*
*Date: 2026-02-28*

---

## Table of Contents

1. [On-Chain Indicators (35)](#1-on-chain-indicators)
   - [UTXO / Valuation Models](#11-utxo--valuation-models)
   - [Network Activity](#12-network-activity)
   - [Supply Distribution & Exchange Flows](#13-supply-distribution--exchange-flows)
   - [Miner Metrics](#14-miner-metrics)
   - [Stablecoin & DeFi On-Chain](#15-stablecoin--defi-on-chain)
2. [Derivatives Indicators (38)](#2-derivatives-indicators)
   - [Funding Rate](#21-funding-rate)
   - [Open Interest](#22-open-interest)
   - [Liquidations](#23-liquidations)
   - [Basis & Premium](#24-basis--premium)
   - [Options](#25-options)
   - [Sentiment from Derivatives](#26-sentiment-from-derivatives)
3. [Data Source Summary](#3-data-source-summary)
4. [Priority Ranking for Implementation](#4-priority-ranking-for-implementation)

---

## 1. On-Chain Indicators

### 1.1 UTXO / Valuation Models

---

#### OC-01: MVRV Ratio (Market Value / Realized Value)

**Formula:** `MVRV = Market Cap / Realized Cap`
where Realized Cap = sum of (each UTXO * price when it last moved).

**Data Source:** Glassnode (`/v1/metrics/market/mvrv`), CryptoQuant (`/v1/btc/market-data/mvrv`)

**Causal Mechanism:** When MVRV > 1, the average holder is in profit and has incentive to sell. When MVRV < 1, the average holder is underwater and unlikely to sell (capitulation zone). MVRV > 3.5 has historically marked cycle tops; MVRV < 1 has marked generational bottoms. This works because realized cap is a proxy for aggregate cost basis, making MVRV a market-wide P&L indicator.

**Academic Citation:** Ong, Kharif, "On-Chain Metrics as Predictors of Cryptocurrency Returns" (2020). Glassnode Academy original formulation by Murad Mahmudov & David Puell, 2018.

**Suitability for 1H Swing Trading:** This is a DAILY indicator updated once per block (~10 min for BTC). It changes slowly (weeks to months). Best used as a **regime filter** -- trade long-only when MVRV < 2.5, reduce size when MVRV > 3.0. Not useful for entry timing.

**Signal Type:** FILTER / SIZING

**Data Availability:** Glassnode (free tier: limited history, 24h delay; paid Tier 2: $29/mo for daily; Tier 3: $799/mo for real-time). CryptoQuant free tier: 1d resolution, limited history. **Effectively requires paid API for production use.**

---

#### OC-02: MVRV Z-Score

**Formula:** `MVRV_Z = (Market Cap - Realized Cap) / std(Market Cap)`
Standardizes MVRV into a z-score relative to its own history.

**Data Source:** Glassnode (`/v1/metrics/market/mvrv_z_score`), lookintobitcoin.com (free chart)

**Causal Mechanism:** Same as MVRV but normalized. Z > 7 has preceded every major top. Z < 0 has preceded every major bottom. The normalization accounts for Bitcoin's growing market cap over time, making cross-cycle comparisons valid.

**Academic Citation:** Same as MVRV. Z-score variant popularized by @100trillionUSD (PlanB).

**Suitability for 1H Swing Trading:** Daily regime filter. Even slower-moving than raw MVRV. Useful for position sizing across cycles.

**Signal Type:** FILTER / SIZING

**Data Availability:** Glassnode paid. Free charts at lookintobitcoin.com (no API). **Paid required for systematic use.**

---

#### OC-03: SOPR (Spent Output Profit Ratio)

**Formula:** `SOPR = sum(output_value_at_spending_price) / sum(output_value_at_creation_price)`
For all UTXOs spent in a period. SOPR > 1 means aggregate profit-taking; SOPR < 1 means aggregate loss realization.

**Data Source:** Glassnode (`/v1/metrics/indicators/sopr`), CryptoQuant (`/v1/btc/market-data/sopr`)

**Causal Mechanism:** SOPR = 1 acts as a support level in bull markets (holders refuse to sell at a loss) and resistance in bear markets (holders sell at breakeven to exit). This is a direct measure of realized profit/loss behavior. In bull markets, SOPR dipping to 1 and bouncing = buying opportunity. In bear markets, SOPR rising to 1 and rejecting = selling opportunity.

**Academic Citation:** Renato Shirakashi (2019), "Introducing SOPR." Empirical validation in Kalichkin (2019).

**Suitability for 1H Swing Trading:** Updated daily. SOPR bouncing off 1.0 in a bull regime is an actionable entry signal for swing trades. Combine with MVRV regime filter.

**Signal Type:** ENTRY / EXIT

**Data Availability:** Glassnode Tier 2+. CryptoQuant Professional. **Paid.**

---

#### OC-04: aSOPR (Adjusted SOPR)

**Formula:** Same as SOPR but excludes UTXOs with lifespan < 1 hour (removes noise from relay transactions, change outputs, and intra-exchange transfers).

**Data Source:** Glassnode (`/v1/metrics/indicators/sopr_adjusted`)

**Causal Mechanism:** By removing short-lived outputs, aSOPR isolates genuine economic transactions. More accurate than raw SOPR for detecting real profit-taking vs operational noise. The 1-hour filter eliminates approximately 40% of transactions that are economically meaningless.

**Academic Citation:** Glassnode (2020), "Introducing Adjusted SOPR."

**Suitability for 1H Swing Trading:** Preferred over raw SOPR for swing trading signals. Same entry logic (bouncing off 1.0 in bull regimes) but with fewer false signals.

**Signal Type:** ENTRY / EXIT

**Data Availability:** Glassnode Tier 2+. **Paid.**

---

#### OC-05: STH-SOPR (Short-Term Holder SOPR)

**Formula:** SOPR computed only for UTXOs aged < 155 days (short-term holders).

**Data Source:** Glassnode (`/v1/metrics/indicators/sopr_less_155`)

**Causal Mechanism:** Short-term holders are the "weak hands" of the market. Their SOPR reflects recent buyer behavior: panic selling (STH-SOPR << 1), profit-taking (STH-SOPR >> 1), or breakeven exits (STH-SOPR ~ 1). STH-SOPR < 0.95 in a bull market = capitulation by recent buyers = contrarian buy signal. STH-SOPR > 1.05 in late-stage bull = distribution beginning.

**Academic Citation:** Glassnode Insights (2021), "The Week On-Chain" series.

**Suitability for 1H Swing Trading:** More responsive than aSOPR (reacts within days). **Good for swing timing** -- STH capitulation events often mark 1-3 week swing lows.

**Signal Type:** ENTRY

**Data Availability:** Glassnode Tier 3. **Paid ($799/mo).**

---

#### OC-06: LTH-SOPR (Long-Term Holder SOPR)

**Formula:** SOPR computed only for UTXOs aged >= 155 days (long-term holders).

**Data Source:** Glassnode (`/v1/metrics/indicators/sopr_more_155`)

**Causal Mechanism:** Long-term holders selling at a loss (LTH-SOPR < 1) signals deep capitulation -- these are conviction holders who have been pushed to their pain threshold. Historically, LTH-SOPR < 1 has only occurred near bear market bottoms. LTH-SOPR rising sharply above 1 in late bull markets = smart money distribution.

**Academic Citation:** Glassnode Academy, "Understanding Long-Term Holder Behavior."

**Suitability for 1H Swing Trading:** Very slow-moving (changes over weeks/months). Purely a regime filter. LTH-SOPR < 1 = maximum bullish regime for swing longs.

**Signal Type:** FILTER

**Data Availability:** Glassnode Tier 3. **Paid.**

---

#### OC-07: NUPL (Net Unrealized Profit/Loss)

**Formula:** `NUPL = (Market Cap - Realized Cap) / Market Cap`
Equivalent to `1 - (1/MVRV)`.

**Data Source:** Glassnode (`/v1/metrics/indicators/net_unrealized_profit_loss`), lookintobitcoin.com

**Causal Mechanism:** Segments market into emotional zones: NUPL < 0 = Capitulation; 0-0.25 = Hope/Fear; 0.25-0.5 = Optimism; 0.5-0.75 = Belief/Greed; > 0.75 = Euphoria. These zones correspond to aggregate portfolio P&L and predict collective behavior (selling pressure increases with aggregate profit levels).

**Academic Citation:** Adamant Capital (Tuur Demeester, 2019), "Bitcoin in Heavy Accumulation."

**Suitability for 1H Swing Trading:** Daily regime filter. NUPL > 0.7 = reduce swing long sizing. NUPL < 0 = maximize long sizing.

**Signal Type:** FILTER / SIZING

**Data Availability:** Glassnode Tier 2. Free via lookintobitcoin charts (no API). **Paid for systematic use.**

---

#### OC-08: Puell Multiple

**Formula:** `Puell = Daily_Miner_Revenue_USD / 365d_MA(Daily_Miner_Revenue_USD)`

**Data Source:** Glassnode (`/v1/metrics/mining/puell_multiple`), lookintobitcoin.com

**Causal Mechanism:** Miners are forced sellers (they must pay electricity). When Puell is high (>4), miners earn much more than average and have high incentive/capacity to sell. When Puell is low (<0.5), miners earn far below average, leading to miner capitulation (hash rate drops, weak miners exit, selling pressure decreases = bullish). Post-halving, Puell drops mechanically as block rewards halve.

**Academic Citation:** David Puell (2019), original formulation.

**Suitability for 1H Swing Trading:** Daily/weekly filter. Puell < 0.5 historically marks generational buy zones. Not useful for entry timing.

**Signal Type:** FILTER

**Data Availability:** Glassnode Tier 2. Free chart at lookintobitcoin.com. **Paid for API.**

---

#### OC-09: Stock-to-Flow Deviation

**Formula:** `S2F = Current_Supply / Annual_Production`. `S2F_Price = exp(regression_coefficient * ln(S2F))`. `Deviation = Price / S2F_Price`.

**Data Source:** lookintobitcoin.com, glassnode (not directly -- compute from supply schedule)

**Causal Mechanism:** S2F models scarcity. Deviation > 1 = overvalued relative to scarcity model; < 1 = undervalued. **IMPORTANT CAVEAT:** The S2F model has been significantly criticized (Nuzzi, 2021) and broke down in the 2021-2022 cycle. Its predictive power is contested. Include only as a regime check, never as a primary signal.

**Academic Citation:** PlanB (2019), "Modeling Bitcoin Value with Scarcity." Critique: Nico Cordeiro (2020), "Why S2F is Flawed." Nuzzi (2021), "S2F Model Breakdown."

**Suitability for 1H Swing Trading:** Monthly regime check only. Low utility for swing trading.

**Signal Type:** FILTER (LOW PRIORITY)

**Data Availability:** Free via lookintobitcoin. Can compute from known supply schedule. **Free.**

---

#### OC-10: Realized Price

**Formula:** `Realized_Price = Realized_Cap / Circulating_Supply`
The average acquisition cost of all coins based on when each UTXO last moved.

**Data Source:** Glassnode (`/v1/metrics/market/price_realized_usd`), CryptoQuant

**Causal Mechanism:** Realized Price is the aggregate cost basis. Price falling below Realized Price means the average holder is at a loss -- historically a strong buy zone (occurred at every bear market bottom). Price far above Realized Price means large aggregate profits and distribution risk.

**Academic Citation:** Nic Carter & Antoine Le Calvez (2018), "Bitcoin as a Novel Asset."

**Suitability for 1H Swing Trading:** Daily. Acts as macro support/resistance. If BTC trades near Realized Price, use as swing long entry zone.

**Signal Type:** ENTRY (macro support level)

**Data Availability:** Glassnode Tier 2, CryptoQuant. **Paid.**

---

#### OC-11: Thermocap Multiple

**Formula:** `Thermocap = cumulative_sum(daily_miner_revenue_USD)`. `Multiple = Market_Cap / Thermocap`.

**Data Source:** Glassnode (derivable), lookintobitcoin.com

**Causal Mechanism:** Thermocap represents the total security spend (miners' cumulative revenue). It approximates the "energy cost" of the network. When Market Cap far exceeds Thermocap (Multiple > 32), the market is overheated relative to its security investment. This metric has marked every cycle top within a narrow band.

**Academic Citation:** Willy Woo (2019), "Introducing the Thermocap Multiple."

**Suitability for 1H Swing Trading:** Very slow (monthly regime). Low utility for swing timing.

**Signal Type:** FILTER (LOW PRIORITY)

**Data Availability:** Derivable from miner revenue data. **Paid source required.**

---

### 1.2 Network Activity

---

#### OC-12: Active Address Count (Daily Change Rate)

**Formula:** `Active_Addr_Mom = (Active_Addrs_Today - Active_Addrs_7d_MA) / Active_Addrs_7d_MA`

**Data Source:** Glassnode (`/v1/metrics/addresses/active_count`), CryptoQuant, Blockchain.com (free for BTC)

**Causal Mechanism:** Rising active addresses = growing network usage = organic demand. Falling active addresses during price rises = divergence warning (price driven by leverage, not adoption). Active addresses are a proxy for Metcalfe's Law (network value ~ n^2).

**Academic Citation:** Metcalfe's Law applied to Bitcoin: Peterson (2018), "Bitcoin Spreads Like a Virus." Empirical: Kalichkin (2018).

**Suitability for 1H Swing Trading:** Daily granularity. Useful as a confirmation filter: only take swing longs when active address momentum is positive.

**Signal Type:** FILTER

**Data Availability:** Blockchain.com API (free, BTC only). Glassnode free tier (24h delay). **Free for BTC.**

---

#### OC-13: New Address Momentum

**Formula:** `New_Addr_Mom = SMA(new_addrs, 7) / SMA(new_addrs, 30) - 1`

**Data Source:** Glassnode (`/v1/metrics/addresses/new_non_zero_count`), CryptoQuant

**Causal Mechanism:** New address creation reflects genuine new user onboarding. A surge in new addresses precedes price rallies (new demand entering). Divergence (price rising but new address growth stalling) signals distribution to existing holders, not fresh demand.

**Academic Citation:** Kalichkin (2018), "NVT and Network Activity Metrics."

**Suitability for 1H Swing Trading:** Daily. Good divergence signal. Works as filter/confirmation.

**Signal Type:** FILTER

**Data Availability:** Glassnode free tier (limited), Blockchain.com (free, BTC). **Free for BTC.**

---

#### OC-14: Transaction Count Momentum

**Formula:** `Tx_Mom = SMA(tx_count, 7) / SMA(tx_count, 30) - 1`

**Data Source:** Glassnode (`/v1/metrics/transactions/count`), Blockchain.com (free)

**Causal Mechanism:** Rising transaction volume confirms genuine economic activity backing a price move. Falling transactions during price rallies = hollow rally driven by derivatives. Similar logic to volume confirmation in traditional markets but measured on-chain rather than on exchanges (harder to fake).

**Academic Citation:** Bolt & van Oordt (2019), "On the Value of Virtual Currencies."

**Suitability for 1H Swing Trading:** Daily filter. Confirm swing entries only when tx momentum is positive.

**Signal Type:** FILTER

**Data Availability:** Blockchain.com API (free). **Free for BTC.**

---

#### OC-15: Entity-Adjusted Transfer Volume

**Formula:** Raw transfer volume adjusted to remove self-sends, change outputs, and intra-entity transfers. Glassnode uses entity clustering heuristics.

**Data Source:** Glassnode (`/v1/metrics/transactions/transfers_volume_entity_adjusted_sum`)

**Causal Mechanism:** Raw on-chain volume is inflated by ~30-50% due to change outputs and internal wallet reshuffling. Entity-adjusted volume isolates genuine economic transfers. Rising entity-adjusted volume during pullbacks = accumulation by real entities (bullish). Falling during rallies = distribution.

**Academic Citation:** Glassnode (2020), "Entity-Adjusted Metrics."

**Suitability for 1H Swing Trading:** Daily. Volume confirmation for swing entries.

**Signal Type:** FILTER

**Data Availability:** Glassnode Tier 2+. **Paid.**

---

#### OC-16: NVT Ratio

**Formula:** `NVT = Market_Cap / Daily_Transaction_Volume_USD`
Crypto equivalent of P/E ratio -- price relative to economic throughput.

**Data Source:** Glassnode (`/v1/metrics/indicators/nvt`), CryptoQuant, Blockchain.com (derivable)

**Causal Mechanism:** High NVT (>95th percentile) = network is overvalued relative to its utility (speculative premium). Low NVT (<5th percentile) = undervalued or high-utility period. NVT > 150 has preceded corrections; NVT < 30 has preceded rallies. This works because long-term, network value should track transaction throughput.

**Academic Citation:** Willy Woo (2017), "Introducing NVT Ratio." Kalichkin (2018), "NVT Signal."

**Suitability for 1H Swing Trading:** Daily. Slow-moving but useful as a regime filter. High NVT = avoid new longs.

**Signal Type:** FILTER

**Data Availability:** Derivable from free data (Blockchain.com market cap + tx volume). **Free.**

---

#### OC-17: NVT Signal (Kalichkin Smoothed)

**Formula:** `NVT_Signal = Market_Cap / SMA(Daily_Transaction_Volume_USD, 90)`
Uses 90-day smoothed volume to reduce noise.

**Data Source:** Glassnode (`/v1/metrics/indicators/nvts`), Woobull.com (free chart)

**Causal Mechanism:** Raw NVT is noisy due to daily volume spikes. NVT Signal smooths volume with a 90d MA, making it a forward-looking indicator (current price vs established throughput trend). NVT Signal >150 = overvalued, <40 = undervalued. More reliable than raw NVT for timing.

**Academic Citation:** Dimitry Kalichkin (2018), "Rethinking NVT Ratio."

**Suitability for 1H Swing Trading:** Daily. Better than raw NVT for regime detection. Useful as a top/bottom filter.

**Signal Type:** FILTER

**Data Availability:** Glassnode Tier 2. Woobull free chart (no API). **Paid for systematic use.**

---

#### OC-18: Velocity

**Formula:** `Velocity = Transaction_Volume_USD / Market_Cap`
Inverse of NVT.

**Data Source:** Derivable from any NVT source.

**Causal Mechanism:** High velocity = coins circulating rapidly (high utility or panic selling). Low velocity = coins dormant (HODLing or low interest). In context: rising velocity from a low base = new demand entering; falling velocity from a high base = speculative excess draining.

**Academic Citation:** Chris Burniske (2017), "Cryptoasset Valuations" (applied MV=PQ monetary equation).

**Suitability for 1H Swing Trading:** Daily. Marginal utility over NVT (inverse). Use one or the other.

**Signal Type:** FILTER

**Data Availability:** Derivable. **Free.**

---

#### OC-19: Mean Transaction Value (Change Rate)

**Formula:** `Mean_Tx_Change = (mean_tx_value_today / SMA(mean_tx_value, 14)) - 1`

**Data Source:** Glassnode (`/v1/metrics/transactions/transfers_volume_mean`), Blockchain.com

**Causal Mechanism:** Spikes in mean transaction value indicate whale activity (large transfers entering exchanges or moving between wallets). A sudden increase in mean tx value, especially combined with exchange inflow, suggests imminent large selling. Conversely, high mean tx value with exchange outflow = large accumulation.

**Academic Citation:** Empirical observation from CryptoQuant research.

**Suitability for 1H Swing Trading:** Daily. Use as a whale-activity confirmation signal.

**Signal Type:** FILTER

**Data Availability:** Blockchain.com (free, raw data). **Free for BTC.**

---

#### OC-20: Median Transfer Value Trend

**Formula:** `Median_Trend = SMA(median_transfer, 7) / SMA(median_transfer, 30) - 1`

**Data Source:** Glassnode (`/v1/metrics/transactions/transfers_volume_median`)

**Causal Mechanism:** Unlike mean (dominated by whales), median reflects the "typical" user. Rising median transfer value = retail engagement growing (bullish momentum confirmation). Falling median = retail disengaging (bearish for continuation).

**Academic Citation:** None specific. General network economics.

**Suitability for 1H Swing Trading:** Daily. Weak standalone signal; useful combined with exchange flow signals.

**Signal Type:** FILTER

**Data Availability:** Glassnode Tier 2. **Paid.**

---

### 1.3 Supply Distribution & Exchange Flows

---

#### OC-21: Exchange Balance (Net Change)

**Formula:** `Exchange_Balance = total_BTC_held_on_known_exchange_addresses`. Signal: `7d_change = balance_today - balance_7d_ago`.

**Data Source:** CryptoQuant (`/v1/btc/exchange-flows/reserve`), Glassnode (`/v1/metrics/distribution/balance_exchanges`), Coinglass

**Causal Mechanism:** Coins on exchanges are available for selling. Net exchange outflows (declining balance) = accumulation, coins moving to cold storage. Net inflows = preparation to sell. Exchange reserve dropping from 2.5M BTC (2020) to ~1.8M BTC (2025) has been the defining bullish narrative of recent cycles.

**Academic Citation:** Kharif (2020), "Exchange Reserves as a Leading Indicator." CryptoQuant Ki Young Ju research.

**Suitability for 1H Swing Trading:** Updated hourly by CryptoQuant. **One of the best on-chain signals for swing trading** -- large exchange inflows can precede sell-offs within hours/days.

**Signal Type:** ENTRY / EXIT

**Data Availability:** CryptoQuant free tier (delayed, limited). Coinglass (some free data). **Free tier available, paid for real-time.**

---

#### OC-22: Exchange Netflow

**Formula:** `Netflow = Exchange_Inflows - Exchange_Outflows` (in BTC or USD).

**Data Source:** CryptoQuant (`/v1/btc/exchange-flows/netflow`), Glassnode, Coinglass

**Causal Mechanism:** Netflow > 0 (inflows dominate) = sell pressure building. Netflow < 0 (outflows dominate) = accumulation. Extreme netflows (>2 std from mean) are the most actionable. A single-day netflow of +10,000 BTC has preceded corrections 70%+ of the time historically.

**Academic Citation:** CryptoQuant (2020), "Exchange Netflow as a Predictive Indicator."

**Suitability for 1H Swing Trading:** Hourly updates available. **Highly suitable for swing trading.** Large positive netflow = avoid longs or exit existing longs.

**Signal Type:** ENTRY / EXIT

**Data Availability:** CryptoQuant free (daily, delayed). Paid for hourly. **Paid for production quality.**

---

#### OC-23: Whale Transaction Count (>$1M, >$10M)

**Formula:** Count of on-chain transactions exceeding $1M or $10M in value per day.

**Data Source:** Glassnode (`/v1/metrics/transactions/transfers_count_greater_*`), Whale Alert API, CryptoQuant

**Causal Mechanism:** Whale transactions represent institutional or large player activity. A spike in >$10M transactions moving TO exchanges precedes selling. A spike moving FROM exchanges (or to fresh wallets) signals accumulation. Whale count rising without price movement = hidden accumulation/distribution.

**Academic Citation:** Empirical. Santiment research (2021).

**Suitability for 1H Swing Trading:** Daily. Useful for confirming whether a price move has institutional backing.

**Signal Type:** FILTER

**Data Availability:** Whale Alert (free API with rate limits). Glassnode Tier 2+. **Partially free.**

---

#### OC-24: Supply in Profit Percentage

**Formula:** `Supply_in_Profit = count(UTXOs where current_price > price_at_creation) / total_UTXOs`

**Data Source:** Glassnode (`/v1/metrics/supply/profit_relative`)

**Causal Mechanism:** When >95% of supply is in profit, nearly everyone has incentive to sell -- extreme greed. When <50% is in profit, nearly everyone is underwater -- capitulation zone (no one left to sell). Transition from >95% to <90% = distribution starting. Transition from <55% to >60% = accumulation ending.

**Academic Citation:** Glassnode Academy, "On-Chain Market Phases."

**Suitability for 1H Swing Trading:** Daily regime filter. >95% in profit = reduce long exposure. <55% = maximize long exposure.

**Signal Type:** FILTER / SIZING

**Data Availability:** Glassnode Tier 2+. **Paid.**

---

#### OC-25: Supply Held by Top 1% (Change Rate)

**Formula:** `Top1_Change = (top_1pct_supply_today / top_1pct_supply_30d_ago) - 1`

**Data Source:** Glassnode (`/v1/metrics/distribution/balance_1pct_holders`), Santiment

**Causal Mechanism:** Concentration increasing = whales accumulating (bullish if happening during drawdowns). Concentration decreasing = distribution to smaller holders (bearish if happening during rallies). This tracks the "smart money" flow.

**Academic Citation:** Santiment (2021), "Whale Accumulation Patterns." Makarov & Schoar (2021), "Blockchain Analysis of the Bitcoin Market."

**Suitability for 1H Swing Trading:** Daily/weekly. Slow-moving. Regime/confirmation filter.

**Signal Type:** FILTER

**Data Availability:** Santiment (limited free). Glassnode Tier 3. **Paid.**

---

#### OC-26: Coin Days Destroyed (CDD)

**Formula:** `CDD = sum(coins * days_since_last_moved)` for all coins spent in a day. Measures "dormancy destruction."

**Data Source:** Glassnode (`/v1/metrics/indicators/cdd`), CryptoQuant

**Causal Mechanism:** Spikes in CDD mean old coins are moving -- long-dormant holders are waking up to sell. CDD spikes at market tops (old holders distributing to new buyers). CDD staying low during rallies = "diamond hands" holding, rally is sustainable. Very high CDD + exchange inflow = major distribution event.

**Academic Citation:** ByteTree (2019), "Coin Days Destroyed." Original concept: Bitcoin Talk forum, 2011.

**Suitability for 1H Swing Trading:** Daily. CDD spike = exit/reduce signal for swing longs.

**Signal Type:** EXIT

**Data Availability:** Glassnode Tier 2. CryptoQuant Professional. **Paid.**

---

#### OC-27: HODL Waves (UTXO Age Distribution)

**Formula:** Percentage of UTXOs in each age band: <1d, 1d-1w, 1w-1m, 1m-3m, 3m-6m, 6m-12m, 1y-2y, 2y-3y, 3y-5y, 5y+.

**Data Source:** Glassnode (`/v1/metrics/supply/hodl_waves`), Unchained Capital (original visualization)

**Causal Mechanism:** Young coins (< 3 months) dominate at market tops (new buyers flooding in). Old coins (>1 year) dominate at market bottoms (only long-term holders remain). The "wave" pattern: old coins decrease as a cycle progresses (LTHs selling to STHs) and increase during bear markets (only HODLers left).

**Academic Citation:** Unchained Capital (2018), "Bitcoin HODL Waves."

**Suitability for 1H Swing Trading:** Weekly. Very slow. Only useful as multi-month regime context.

**Signal Type:** FILTER (LOW PRIORITY)

**Data Availability:** Glassnode Tier 3. **Paid.**

---

#### OC-28: Realized Cap HODL Waves

**Formula:** Like HODL Waves but weighted by realized cap instead of supply count. Each age band's share is weighted by the USD value at which coins last moved.

**Data Source:** Glassnode (`/v1/metrics/indicators/rcap_hodl_waves`)

**Causal Mechanism:** Superior to raw HODL Waves because it captures the economic weight of each age band, not just the count. A surge in the < 3-month band (by realized cap) means a massive amount of capital has recently entered, which is often the final stage of a bull market (late buyers).

**Academic Citation:** Glassnode (2020), "Realized Cap HODL Waves."

**Suitability for 1H Swing Trading:** Weekly. Regime context only.

**Signal Type:** FILTER (LOW PRIORITY)

**Data Availability:** Glassnode Tier 3. **Paid ($799/mo).**

---

#### OC-29: Supply Last Active 1y+ (Illiquid Supply)

**Formula:** Percentage of total supply that has not moved in over 1 year.

**Data Source:** Glassnode (`/v1/metrics/supply/active_more_1y_percent`)

**Causal Mechanism:** Illiquid supply = coins effectively removed from circulation. Higher illiquid supply = lower effective float = price more sensitive to demand changes. When illiquid supply is rising (more coins going dormant), supply squeeze dynamics amplify price moves. When it starts declining sharply, long-dormant holders are distributing.

**Academic Citation:** Glassnode (2021), "Illiquid Supply Shock."

**Suitability for 1H Swing Trading:** Daily/weekly. Slow-moving but informs volatility expectations. Rising illiquid supply = expect larger swing moves.

**Signal Type:** SIZING (volatility adjuster)

**Data Availability:** Glassnode Tier 2. **Paid.**

---

#### OC-30: Liveliness

**Formula:** `Liveliness = cumulative_CDD / cumulative_coin_days_created`
Ranges 0-1. Trends toward 1 when old coins move frequently; toward 0 when coins accumulate age.

**Data Source:** Glassnode (`/v1/metrics/indicators/liveliness`)

**Causal Mechanism:** Falling liveliness = net HODLing (accumulation phase, coins aging). Rising liveliness = net spending (distribution phase, old coins moving). Change in liveliness trend is a leading indicator of regime shifts. Liveliness momentum (7d change) can signal the start of distribution before price tops.

**Academic Citation:** Tamas Blummer (2018), "Liveliness of Bitcoin."

**Suitability for 1H Swing Trading:** Daily. Momentum of liveliness (not absolute level) is useful for detecting regime shifts.

**Signal Type:** FILTER

**Data Availability:** Glassnode Tier 2+. **Paid.**

---

### 1.4 Miner Metrics

---

#### OC-31: Hash Rate Momentum

**Formula:** `HR_Mom = SMA(hash_rate, 7) / SMA(hash_rate, 30) - 1`

**Data Source:** Blockchain.com (free), Glassnode (`/v1/metrics/mining/hash_rate_mean`), Braiins Pool

**Causal Mechanism:** Hash rate reflects miner commitment (capital expenditure on hardware). Rising hash rate = miners are profitable and expanding (bullish). Falling hash rate = miners are shutting down (capitulation, short-term bearish but often marks bottoms as selling pressure from capitulating miners ceases).

**Academic Citation:** Blockchain.com data. General network security economics.

**Suitability for 1H Swing Trading:** Daily. Hash rate drops of >10% in a week have preceded local bottoms (miner capitulation exhaustion). Useful as contrarian entry filter.

**Signal Type:** FILTER

**Data Availability:** Blockchain.com (free). **Free.**

---

#### OC-32: Hash Rate / Price Ratio

**Formula:** `HR_Price = Hash_Rate / BTC_Price_USD`. Rising = miners investing despite flat price (bullish conviction). Falling = price outrunning hash rate (speculative premium).

**Data Source:** Derivable from free data (Blockchain.com hash rate + price).

**Causal Mechanism:** Miners make long-term capital decisions (hardware purchases take months to deploy). Hash rate growing faster than price = miners see future profitability (bullish signal with 2-3 month lead). Price growing faster than hash rate = speculative excess.

**Academic Citation:** Empirical. Woo (2019).

**Suitability for 1H Swing Trading:** Weekly. Very slow indicator. Regime context only.

**Signal Type:** FILTER (LOW PRIORITY)

**Data Availability:** Derivable from free sources. **Free.**

---

#### OC-33: Miner Outflow (Exchange Deposits from Miners)

**Formula:** BTC transferred from known miner addresses to exchange addresses, daily.

**Data Source:** CryptoQuant (`/v1/btc/miner-flows/outflow`), Glassnode (`/v1/metrics/mining/miners_outflow`)

**Causal Mechanism:** Miners sell to cover operational costs. Abnormally high miner outflows (>2 std from mean) signal forced selling or profit-taking. Post-halving, miner outflows spike as marginal miners sell reserves to survive. This creates temporary but significant sell pressure.

**Academic Citation:** CryptoQuant (2020), "Miner Position Index."

**Suitability for 1H Swing Trading:** Daily. High miner outflows = avoid swing longs for next 1-3 days. Useful as a short-term filter.

**Signal Type:** FILTER / EXIT

**Data Availability:** CryptoQuant Professional. Glassnode Tier 2. **Paid.**

---

#### OC-34: Hash Ribbons

**Formula:** `Signal = SMA(hash_rate, 30) crosses above SMA(hash_rate, 60)` after a period where the 30d was below the 60d (miner capitulation followed by recovery).

**Data Source:** Derivable from hash rate data (Blockchain.com free). TradingView indicator available.

**Causal Mechanism:** When the 30d hash rate MA drops below the 60d MA, miners are capitulating (shutting off rigs). When it crosses back above, capitulation has ended and only efficient miners remain. This marks the end of forced selling pressure. Historically, hash ribbon buy signals have preceded major rallies (100%+ returns within 12 months).

**Academic Citation:** Charles Edwards (Capriole Investments, 2019), "Hash Ribbons & Bitcoin Bottoms."

**Suitability for 1H Swing Trading:** Signal fires once every 1-2 years. Not useful for regular swing trading but when it fires, it is one of the strongest long signals available. Use as a regime override: if hash ribbon fires, aggressively long.

**Signal Type:** ENTRY (rare, high-conviction)

**Data Availability:** Derivable from free hash rate data. **Free.**

---

#### OC-35: Difficulty Ribbon Compression

**Formula:** Standard deviation of difficulty over recent adjustment periods. When all difficulty MAs (9, 14, 25, 40, 60, 90, 128, 200 periods) compress toward each other, miner capitulation is occurring.

**Data Source:** Derivable from BTC difficulty data (Blockchain.com free). Woobull.com chart.

**Causal Mechanism:** Similar to hash ribbons. Difficulty ribbon compression = all difficulty moving averages converging = stable but stressed mining landscape. Extreme compression marks the nadir of miner capitulation. Expansion from compression = recovery underway.

**Academic Citation:** Willy Woo (2019), "Difficulty Ribbon."

**Suitability for 1H Swing Trading:** Monthly. Even slower than hash ribbons. Regime context only.

**Signal Type:** FILTER (LOW PRIORITY)

**Data Availability:** Free (difficulty is public blockchain data). **Free.**

---

### 1.5 Stablecoin & DeFi On-Chain

---

#### OC-36: Stablecoin Supply Ratio (SSR)

**Formula:** `SSR = BTC_Market_Cap / Total_Stablecoin_Market_Cap`
Low SSR = lots of stablecoin buying power relative to BTC (dry powder). High SSR = BTC expensive relative to available stablecoins.

**Data Source:** Glassnode (`/v1/metrics/indicators/ssr`), CryptoQuant, DefiLlama (stablecoin supply, free)

**Causal Mechanism:** Stablecoins are the "cash" of crypto. When SSR is low, there is abundant dry powder parked in stablecoins waiting to deploy. When SSR is high, most capital is already deployed (no marginal buyer). SSR drops of >10% in a week = significant new stablecoin inflows = bullish.

**Academic Citation:** CryptoQuant (2021), "Stablecoin Supply Ratio."

**Suitability for 1H Swing Trading:** Daily. SSR dropping = bullish regime. SSR oscillator (deviation from 30d MA) can time swing entries.

**Signal Type:** FILTER / ENTRY

**Data Availability:** DefiLlama API (free for stablecoin supply). CryptoQuant paid. **Partially free** (can compute SSR from free stablecoin data + BTC price).

---

#### OC-37: Stablecoin Exchange Netflow

**Formula:** `Stable_Netflow = stablecoin_inflow_to_exchanges - stablecoin_outflow_from_exchanges`

**Data Source:** CryptoQuant (`/v1/btc/stablecoin/exchange-netflow`), Glassnode

**Causal Mechanism:** Stablecoins flowing INTO exchanges = users depositing to buy crypto (bullish). Stablecoins flowing OUT of exchanges = users withdrawing cash (bearish or neutral -- DeFi yield farming). Net stablecoin inflow is one of the most directly actionable on-chain signals: it measures literal buy-side demand arriving.

**Academic Citation:** CryptoQuant (2021), "Stablecoin Exchange Flows."

**Suitability for 1H Swing Trading:** Hourly data available from CryptoQuant. **Excellent for swing timing.** Large stablecoin inflows to exchanges precede buying pressure within hours.

**Signal Type:** ENTRY

**Data Availability:** CryptoQuant Professional. **Paid.**

---

#### OC-38: DeFi TVL Momentum

**Formula:** `TVL_Mom = TVL_today / TVL_7d_ago - 1`
Total Value Locked across DeFi protocols.

**Data Source:** DefiLlama API (free, `https://api.llama.fi/v2/historicalChainTvl`)

**Causal Mechanism:** Rising TVL = capital entering DeFi ecosystem = risk-on sentiment. Falling TVL = capital exiting (risk-off or yield compression). TVL trends lead altcoin prices by 1-3 days because capital deployment precedes price impact. For BTC swing trading, TVL momentum serves as a broader crypto risk appetite gauge.

**Academic Citation:** DefiLlama research. Empirical correlation studies.

**Suitability for 1H Swing Trading:** Daily. Good risk appetite filter for the broader crypto market.

**Signal Type:** FILTER

**Data Availability:** DefiLlama API. **Free.**

---

#### OC-39: ETH Gas Price Momentum

**Formula:** `Gas_Mom = SMA(avg_gas_price, 7) / SMA(avg_gas_price, 30) - 1`

**Data Source:** Etherscan API (free), Glassnode (ETH), Dune Analytics (free)

**Causal Mechanism:** Rising gas prices = increased Ethereum usage = network congestion from demand. Gas spikes correlate with NFT mints, DeFi activity, and retail speculation. Sustained high gas = risk-on environment. Collapsing gas = activity dying (risk-off). Gas is a real-time thermometer of Ethereum ecosystem demand.

**Academic Citation:** Roughgarden (2021), "Transaction Fee Mechanism Design." Empirical.

**Suitability for 1H Swing Trading:** Hourly data available. **Good for timing ETH/altcoin swings.** Rising gas momentum = bullish ETH sentiment.

**Signal Type:** FILTER (ETH-specific)

**Data Availability:** Etherscan API (free). **Free.**

---

## 2. Derivatives Indicators

### 2.1 Funding Rate

---

#### DV-01: Funding Rate (8h, Raw)

**Formula:** `FR = Premium_Rate + clamp(Interest_Rate - Premium_Rate, -0.05%, 0.05%)`
Exchanges charge/pay funding every 8 hours to keep perp price anchored to spot. Positive FR = longs pay shorts.

**Data Source:** Binance API (free, `GET /fapi/v1/fundingRate`), Bybit API, OKX API, Coinglass (aggregated, free)

**Causal Mechanism:** Positive FR = market is net long (longs paying a cost to maintain positions). Extremely positive FR (>0.1% per 8h = ~0.3%/day = ~110%/year) signals euphoria and crowded longs. Negative FR = market is net short. FR acts as a cost of carry, so extreme FR creates natural mean-reversion pressure as the cost becomes prohibitive.

**Academic Citation:** Cong, Li, Wang (2022), "Token-Based Platform Finance." Empirical: Alexander & Heck (2020).

**Suitability for 1H Swing Trading:** Updated every 8 hours (can get predicted FR more frequently). **Core swing trading signal.** Extreme FR = contrarian signal. FR > 0.1% = fade longs. FR < -0.05% = fade shorts.

**Signal Type:** ENTRY / EXIT

**Data Availability:** All major exchange APIs (free, no API key required for public endpoints). Coinglass free tier. **Free.**

---

#### DV-02: Funding Rate 7d MA

**Formula:** `FR_7d = SMA(funding_rate, 21)` (21 funding periods = 7 days at 3/day).

**Data Source:** Computed from exchange APIs. Coinglass.

**Causal Mechanism:** Smooths out single-period noise. Persistent positive FR (7d MA > 0.03%) indicates a sustained leveraged long bias -- a regime condition, not a timing signal. Persistent negative FR = sustained short bias. Trend changes in FR_7d often precede price reversals by 24-72 hours.

**Academic Citation:** Empirical. Coinglass research.

**Suitability for 1H Swing Trading:** Computed from 8h data. **Good regime filter.** Rising FR_7d = increasingly crowded longs.

**Signal Type:** FILTER

**Data Availability:** Derivable from free data. **Free.**

---

#### DV-03: Funding Rate Z-Score

**Formula:** `FR_Z = (FR_current - mean(FR, 90d)) / std(FR, 90d)`

**Data Source:** Computed from exchange APIs.

**Causal Mechanism:** Normalizes FR to detect extremes relative to recent history. FR_Z > 2 = funding rate 2 standard deviations above the 90-day mean = extremely crowded positioning. FR_Z < -2 = extremely crowded short. More robust than absolute FR thresholds because FR "normal" levels change across market regimes.

**Academic Citation:** Standard statistical normalization. Applied in: Bitmex Research (2020).

**Suitability for 1H Swing Trading:** **Excellent contrarian entry signal.** FR_Z > 2 = short entry or exit longs. FR_Z < -2 = long entry or exit shorts.

**Signal Type:** ENTRY / EXIT

**Data Availability:** Derivable from free data. **Free.**

---

#### DV-04: Funding Rate Momentum (24h/72h Change)

**Formula:** `FR_Mom_24h = FR_current - FR_3periods_ago` (3 periods = 24h).

**Data Source:** Computed from exchange APIs.

**Causal Mechanism:** Rate of change matters more than level. Rapidly rising FR = leveraged longs piling in fast (potential blowoff). Rapidly falling FR = longs unwinding or shorts building (potential reversal). FR momentum captures the second derivative of market positioning.

**Academic Citation:** Empirical.

**Suitability for 1H Swing Trading:** **Highly actionable.** FR accelerating upward while price stagnates = bearish divergence (longs building but price not following).

**Signal Type:** ENTRY

**Data Availability:** Free. **Free.**

---

#### DV-05: Aggregated Funding Rate (Cross-Exchange)

**Formula:** `Agg_FR = weighted_mean(FR_binance, FR_bybit, FR_okx, FR_deribit, weights=OI_share)`
OI-weighted average funding rate across exchanges.

**Data Source:** Coinglass (pre-computed, free), or compute from individual exchange APIs.

**Causal Mechanism:** Any single exchange can have idiosyncratic FR (arbitrageurs, whale positions). Aggregated FR across all major venues removes exchange-specific noise and gives the true market-wide positioning signal. Divergence between exchange-specific FR and aggregated FR can identify arbitrage opportunities.

**Academic Citation:** Coinglass methodology documentation.

**Suitability for 1H Swing Trading:** Updated per funding period. Use this instead of single-exchange FR for systematic signals.

**Signal Type:** ENTRY / EXIT / FILTER

**Data Availability:** Coinglass free. **Free.**

---

#### DV-06: Funding Rate Divergence (Cross-Exchange Spread)

**Formula:** `FR_Div = max(FR_exchanges) - min(FR_exchanges)` or specific pair spreads (e.g., Binance FR - Bybit FR).

**Data Source:** Computed from exchange APIs, Coinglass.

**Causal Mechanism:** Large FR divergence between exchanges = segmented positioning (one exchange has much more leveraged longs/shorts than others). This creates cross-exchange arbitrage pressure that resolves via price convergence. Extreme divergence can also indicate exchange-specific liquidation cascades about to spill over.

**Academic Citation:** Empirical. Crypto market microstructure research.

**Suitability for 1H Swing Trading:** Marginal standalone value but useful as a regime instability indicator. High FR divergence = expect volatility.

**Signal Type:** FILTER (volatility warning)

**Data Availability:** Coinglass, exchange APIs. **Free.**

---

### 2.2 Open Interest

---

#### DV-07: Open Interest (Absolute, USD)

**Formula:** `OI = sum(all_open_perpetual_and_futures_contracts_USD)` across exchanges.

**Data Source:** Coinglass (free, `https://open-api.coinglass.com/public/v2/open_interest`), exchange APIs (free)

**Causal Mechanism:** OI measures the total amount of leveraged bets outstanding. Rising OI + rising price = new money entering longs (trend continuation). Rising OI + falling price = new money entering shorts (bearish). Falling OI + rising price = short covering (squeeze). Falling OI + falling price = long liquidation (capitulation). The OI-price relationship tells you WHO is driving the move.

**Academic Citation:** Bessembinder & Seguin (1993) applied to crypto by Alexander & Heck (2020), "The Role of Binance in Bitcoin Volatility Transmission."

**Suitability for 1H Swing Trading:** Updated every minute on Coinglass. **Essential for swing trading.** OI at all-time highs = liquidation cascade risk.

**Signal Type:** FILTER / SIZING

**Data Availability:** Coinglass free tier. Exchange APIs free. **Free.**

---

#### DV-08: OI Change Rate (24h/72h Momentum)

**Formula:** `OI_Change_24h = (OI_now - OI_24h_ago) / OI_24h_ago`

**Data Source:** Coinglass, exchange APIs.

**Causal Mechanism:** Rapid OI increase (>5% in 24h) = massive new leveraged positions opening. This increases the "fuel" for a liquidation cascade in either direction. OI increasing by >10% in 72h with price near a local high = extremely dangerous long positioning (expect a squeeze/cascade).

**Academic Citation:** Empirical. Multiple Coinglass and Deribit research reports.

**Suitability for 1H Swing Trading:** **Excellent for timing liquidation events.** Rapid OI buildup = reduce position size (expect explosive move).

**Signal Type:** SIZING / FILTER

**Data Availability:** Free. **Free.**

---

#### DV-09: OI/Volume Ratio (Leverage Buildup)

**Formula:** `OI_Vol = OI / 24h_Futures_Volume`
High ratio = lots of open positions relative to turnover = leverage building up without sufficient liquidity to absorb it.

**Data Source:** Coinglass, exchange APIs.

**Causal Mechanism:** When OI is high relative to volume, it means positions are NOT being turned over. Traders are holding leveraged bets and not closing them. This creates a "coiled spring" -- when the move comes, there's insufficient liquidity to absorb the closures, leading to cascading liquidations. OI/Volume > 2 std above mean = liquidation cascade imminent.

**Academic Citation:** Empirical analogy to futures basis in traditional markets. Biais, Foucault, Moinas (2015) principles applied.

**Suitability for 1H Swing Trading:** **Actionable.** High OI/Volume = expect a volatile move. Size down or position for mean reversion.

**Signal Type:** SIZING

**Data Availability:** Free. **Free.**

---

#### DV-10: OI-Weighted Funding Rate

**Formula:** `OI_FR = sum(FR_exchange * OI_exchange) / sum(OI_exchange)`
Same as DV-05 (Aggregated FR) but explicitly weighted by each exchange's OI contribution.

**Data Source:** Coinglass, exchange APIs.

**Causal Mechanism:** Gives more weight to exchanges where the most capital is at stake. If Binance has 40% of OI and FR = 0.05%, while smaller exchanges have FR = 0.01%, the OI-weighted FR will reflect the Binance dominance. More economically meaningful than simple average.

**Academic Citation:** Empirical.

**Suitability for 1H Swing Trading:** Use as the canonical FR signal for systematic trading.

**Signal Type:** ENTRY / EXIT

**Data Availability:** Free. **Free.**

---

#### DV-11: OI Concentration (Top Exchange Share)

**Formula:** `OI_Concentration = OI_largest_exchange / Total_OI`. Also: Herfindahl index across exchanges.

**Data Source:** Coinglass.

**Causal Mechanism:** If one exchange holds >50% of OI, that exchange's liquidation engine and insurance fund become the single point of failure for the entire market. High concentration = higher systemic risk and more correlated liquidation cascades. Diversified OI = more resilient market structure.

**Academic Citation:** Empirical. Market microstructure theory (concentration risk).

**Suitability for 1H Swing Trading:** Weekly check. Not a timing signal but informs where to watch for cascade risk.

**Signal Type:** FILTER (LOW PRIORITY)

**Data Availability:** Coinglass free. **Free.**

---

#### DV-12: Long/Short Ratio (Aggregated)

**Formula:** `LS_Ratio = (number_of_accounts_net_long) / (number_of_accounts_net_short)`
Or by position value: `LS_Value = total_long_value / total_short_value`.

**Data Source:** Binance API (`GET /futures/data/globalLongShortAccountRatio`), Coinglass, Bybit

**Causal Mechanism:** LS Ratio > 1 = more accounts are long than short (crowd is bullish). Extreme LS ratios (>2.5 or <0.7) are contrarian signals. The crowd is usually wrong at extremes. However, account-count-based ratios can be misleading because one whale short can offset thousands of retail longs in dollar terms.

**Academic Citation:** Empirical. Limited academic work; primarily industry research (Binance, Coinglass).

**Suitability for 1H Swing Trading:** Updated hourly by Binance. **Useful contrarian signal** at extremes. LS Ratio > 2.0 = crowded longs, fade.

**Signal Type:** ENTRY (contrarian)

**Data Availability:** Binance API (free), Coinglass (free). **Free.**

---

#### DV-13: Top Trader Long/Short Ratio

**Formula:** Same as DV-12 but filtered for top traders (top 20% by position size on Binance).

**Data Source:** Binance API (`GET /futures/data/topLongShortAccountRatio`), Coinglass

**Causal Mechanism:** Top traders are generally more informed than the average retail trader. When top traders are net long but retail is net short, the signal is more bullish (smart money vs dumb money divergence). When both are aligned in the same direction at extremes, the signal is weaker (genuine consensus).

**Academic Citation:** Empirical. Binance research.

**Suitability for 1H Swing Trading:** Updated hourly. **More reliable than aggregate LS ratio.** Smart money divergence from retail = strong signal.

**Signal Type:** ENTRY

**Data Availability:** Binance API (free). **Free.**

---

#### DV-14: Taker Buy/Sell Ratio (Futures)

**Formula:** `Taker_Ratio = taker_buy_volume / taker_sell_volume` for futures markets.

**Data Source:** Binance API (`GET /futures/data/takerlongshortRatio`), CryptoQuant, Coinglass

**Causal Mechanism:** Taker orders "cross the spread" and indicate urgency. Taker Buy Ratio > 1 = aggressive buying. < 1 = aggressive selling. Unlike maker orders (passive, less informative), taker orders reveal which side is more desperate to get filled. Extreme taker ratios (>1.3 or <0.7) precede short-term price moves.

**Academic Citation:** Kyle (1985) informed trading model applied to crypto. Easley, O'Hara, Basu (2019), "From Mining to Markets."

**Suitability for 1H Swing Trading:** **Available at sub-hourly frequency.** One of the most directly actionable derivatives signals for swing trading. Extreme taker buy ratio = momentum entry.

**Signal Type:** ENTRY

**Data Availability:** Binance free. CryptoQuant paid. **Free (Binance).**

---

### 2.3 Liquidations

---

#### DV-15: Liquidation Volume (Long/Short Separate)

**Formula:** `Liq_Long = sum(liquidated_long_positions_USD, 24h)`. `Liq_Short = sum(liquidated_short_positions_USD, 24h)`.

**Data Source:** Coinglass (free, `https://open-api.coinglass.com/public/v2/liquidation`), exchange WebSocket APIs (free)

**Causal Mechanism:** Liquidations are forced market orders that create cascading price pressure. A $100M long liquidation event forces $100M of market sell orders, pushing price down further, triggering more liquidations. Monitoring liquidation volume tells you whether a price move is driven by organic selling or forced selling (cascades). Large liquidation events create exhaustion points -- swing entry opportunities.

**Academic Citation:** Brunnermeier & Pedersen (2009), "Market Liquidity and Funding Liquidity" (margin spiral theory applied to crypto).

**Suitability for 1H Swing Trading:** **Real-time via WebSocket, hourly via API.** Post-liquidation-cascade entries are one of the highest-quality swing signals. After a $200M+ liquidation cascade, price often bounces 3-8% within 24-48 hours.

**Signal Type:** ENTRY

**Data Availability:** Coinglass free. Exchange WebSockets free. **Free.**

---

#### DV-16: Long Liquidation Dominance

**Formula:** `Long_Liq_Dom = Long_Liquidations / (Long_Liquidations + Short_Liquidations)`

**Data Source:** Coinglass, computed from exchange APIs.

**Causal Mechanism:** Long Liq Dominance > 70% = cascading long liquidations are driving the sell-off (forced sellers, not organic). This is typically the final phase of a correction -- after leveraged longs are flushed, the selling pressure exhausts. Long Liq Dom > 80% has historically marked local bottoms within 6-24 hours.

**Academic Citation:** Empirical. Coinglass analysis.

**Suitability for 1H Swing Trading:** **Highly actionable.** Long Liq Dom spike > 80% = swing long entry after the cascade stabilizes (wait for 1-2 hourly candles to confirm stabilization).

**Signal Type:** ENTRY

**Data Availability:** Free. **Free.**

---

#### DV-17: Liquidation Cascade Risk Score

**Formula:** `Cascade_Risk = (OI * avg_leverage * realized_vol) / liquidity_depth`
Composite score estimating the potential magnitude of a liquidation cascade.

**Data Source:** Computed from Coinglass (OI), exchange APIs (leverage, order book depth).

**Causal Mechanism:** When OI is high, leverage is high, volatility is elevated, AND order book liquidity is thin, the conditions are ripe for a liquidation cascade. This composite score tries to quantify "how much fuel is there for a cascade?" High cascade risk = reduce position size and tighten stops.

**Academic Citation:** Adapted from traditional VaR decomposition. Empirical calibration required.

**Suitability for 1H Swing Trading:** Composite, updated hourly. **Excellent for position sizing.** High cascade risk = 50% of normal position size.

**Signal Type:** SIZING

**Data Availability:** Requires combining multiple free sources. **Free (requires computation).**

---

#### DV-18: Estimated Liquidation Levels (Heatmap)

**Formula:** Estimate liquidation prices from known OI + assumed leverage distribution (5x, 10x, 25x, 50x, 100x). For each leverage tier: `liq_price_long = entry * (1 - 1/leverage)`, `liq_price_short = entry * (1 + 1/leverage)`.

**Data Source:** Coinglass liquidation heatmap (visual, limited API). Hyblock Capital (paid). Kingfisher (paid).

**Causal Mechanism:** Liquidation levels act as magnets -- when price approaches a cluster of liquidation levels, it tends to sweep through them as cascading liquidations create self-reinforcing momentum. Identifying where large liquidation clusters sit (e.g., $5B of 10x longs liquidated at $58K) allows positioning for the cascade or avoiding being caught in it.

**Academic Citation:** Theoretical: Brunnermeier (2009). Practical: Coinglass documentation.

**Suitability for 1H Swing Trading:** **Very useful for stop placement and target setting.** Place stops away from liquidation clusters. Target entries just after a cluster is swept.

**Signal Type:** ENTRY / EXIT (level-based)

**Data Availability:** Coinglass (limited free heatmap). Hyblock/Kingfisher paid ($50-200/mo). **Partially free.**

---

#### DV-19: Net Liquidation Flow

**Formula:** `Net_Liq = Short_Liquidations - Long_Liquidations` (positive = shorts being squeezed, negative = longs being flushed).

**Data Source:** Coinglass, computed from exchange APIs.

**Causal Mechanism:** Persistent net long liquidation (negative net liq flow) over multiple hours signals a cascading long flush that will create a capitulation bottom. Persistent net short liquidation (positive net liq flow) signals a short squeeze that will create an exhaustion top. The direction and magnitude inform whether the move is almost done.

**Academic Citation:** Empirical.

**Suitability for 1H Swing Trading:** Hourly. **Good for timing the end of cascade events.** When net liq flow reverses sign after a sustained cascade, the move is exhausting.

**Signal Type:** ENTRY (timing after cascades)

**Data Availability:** Free. **Free.**

---

### 2.4 Basis & Premium

---

#### DV-20: Futures Basis (Annualized)

**Formula:** `Basis = ((Futures_Price - Spot_Price) / Spot_Price) * (365 / days_to_expiry) * 100`
For quarterly futures (Binance, OKX, Deribit).

**Data Source:** Exchange APIs (free), Coinglass, Laevitas.ch

**Causal Mechanism:** Basis = cost of carry = implied yield from going long spot + short futures. High basis (>20% annualized) = extreme bullish positioning (longs willing to pay premium for leverage). Low basis (<5%) = neutral/bearish. Negative basis (backwardation) = extreme bearishness or forced selling in futures. Basis is the most direct measure of directional sentiment from sophisticated traders.

**Academic Citation:** Working (1949), "Theory of Basis." Applied to crypto: Alexander, Choi, Park, Sohn (2020), "BitMEX Bitcoin Derivatives."

**Suitability for 1H Swing Trading:** Updated continuously. **Strong regime signal.** Basis > 25% = overheated, contrarian short bias. Basis < 5% = underpositioned, contrarian long bias.

**Signal Type:** FILTER / ENTRY

**Data Availability:** Exchange APIs free. Coinglass free. **Free.**

---

#### DV-21: Perpetual Premium

**Formula:** `Perp_Premium = (Perp_Price - Spot_Price) / Spot_Price * 100`
Real-time divergence of perpetual price from spot.

**Data Source:** Exchange APIs (free), computed in real-time.

**Causal Mechanism:** Perp price > spot = leveraged longs pushing perp above spot (bullish pressure, but unsustainable if extreme). Perp price < spot = leveraged shorts pushing perp below spot (bearish pressure). Perp premium is closely related to funding rate but updates continuously rather than every 8 hours, making it a higher-frequency signal.

**Academic Citation:** Empirical. Related to basis literature.

**Suitability for 1H Swing Trading:** **Real-time.** Perp premium > 0.1% = longs crowded. < -0.1% = shorts crowded. Can compute on every 1H candle.

**Signal Type:** ENTRY

**Data Availability:** Free (order book data from exchange APIs). **Free.**

---

#### DV-22: Basis Momentum (Rate of Change)

**Formula:** `Basis_Mom = Basis_today - Basis_3d_ago`

**Data Source:** Computed from exchange APIs.

**Causal Mechanism:** Rising basis = increasing bullish conviction (more longs entering futures market). Falling basis = decreasing conviction or long unwinding. Basis momentum turning negative from a high level is an early warning of sentiment shift (longs starting to unwind).

**Academic Citation:** Empirical.

**Suitability for 1H Swing Trading:** Daily. Basis momentum turning sharply negative = early exit signal for swing longs.

**Signal Type:** EXIT

**Data Availability:** Free. **Free.**

---

#### DV-23: Cross-Exchange Basis Divergence

**Formula:** `Basis_Div = max(basis_exchange_i) - min(basis_exchange_i)` across major futures venues.

**Data Source:** Coinglass, exchange APIs.

**Causal Mechanism:** If Binance basis is 15% but OKX basis is 8%, it suggests localized demand imbalance (possibly specific to one exchange's user base or regulatory environment). Large divergence creates arbitrage opportunities and signals potential flow migration between exchanges. Also a stress indicator -- divergence widens during periods of market stress.

**Academic Citation:** Empirical. Cross-venue microstructure.

**Suitability for 1H Swing Trading:** Marginal for directional trading. Better for market structure awareness.

**Signal Type:** FILTER (LOW PRIORITY)

**Data Availability:** Free. **Free.**

---

#### DV-24: Options Implied Volatility (25-Delta ATM)

**Formula:** `IV = Black-Scholes implied volatility for 25-delta options` (roughly ATM). Term: 7d, 14d, 30d, 90d expirations.

**Data Source:** Deribit API (free, `GET /public/get_book_summary_by_currency`), Laevitas.ch, The Block (data page)

**Causal Mechanism:** IV reflects the market's expectation of future volatility. Rising IV = market expects a big move (direction unknown). Falling IV = market expects calm. IV relative to realized volatility (IV/RV spread) is more informative than absolute IV. The options market aggregates the views of sophisticated traders who put capital at risk on their volatility forecasts.

**Academic Citation:** Black & Scholes (1973). Applied to crypto: Hou, Xue, Zhang (2020), "Cryptocurrency Volatility Surface."

**Suitability for 1H Swing Trading:** Updated continuously. **Core signal.** High IV = wider stops, smaller positions. IV crush (sharp IV decline) after an event = mean-reversion opportunity.

**Signal Type:** SIZING / FILTER

**Data Availability:** Deribit API (free). **Free.**

---

#### DV-25: IV/RV Ratio (Implied vs Realized Volatility Spread)

**Formula:** `IV_RV = IV_30d / RV_30d` where RV = realized (historical) volatility over the same horizon.

**Data Source:** Deribit (IV), computed RV from price data.

**Causal Mechanism:** IV/RV > 1.3 = market is overpricing volatility (fear premium). This often occurs before or during corrections and marks the peak of fear. IV/RV < 0.8 = market is underpricing volatility (complacency). Historically, low IV/RV precedes volatility spikes. This is the crypto equivalent of VIX-based timing signals.

**Academic Citation:** Carr & Wu (2009), "Variance Risk Premiums." Applied to crypto: Deribit research.

**Suitability for 1H Swing Trading:** **Strong contrarian signal.** IV/RV > 1.5 = fear extreme, potential swing long entry. IV/RV < 0.8 = complacency, reduce long exposure.

**Signal Type:** ENTRY / SIZING

**Data Availability:** Deribit IV (free) + computed RV. **Free.**

---

#### DV-26: Put-Call Ratio

**Formula:** `PCR = Put_OI / Call_OI` or `PCR = Put_Volume / Call_Volume`

**Data Source:** Deribit API, Laevitas.ch, The Block, Coinglass

**Causal Mechanism:** PCR > 1 = more puts than calls outstanding (hedging/bearish positioning). PCR < 0.5 = very few puts (complacent/unhedged market). Extreme PCR is a classic contrarian indicator from traditional markets. In crypto, PCR > 1.2 has coincided with local bottoms (maximum hedging = maximum fear = contrarian buy).

**Academic Citation:** Bates (1991), "The Crash of '87." Applied to crypto: Hoang & Baur (2021).

**Suitability for 1H Swing Trading:** Daily. **Good contrarian entry filter.** PCR > 1.0 = fear, look for long entries. PCR < 0.4 = complacency, be cautious.

**Signal Type:** ENTRY (contrarian)

**Data Availability:** Deribit API (free). Laevitas free tier. **Free.**

---

#### DV-27: Options Skew (25-Delta Risk Reversal)

**Formula:** `Skew = IV(25d_put) - IV(25d_call)`
Positive skew = puts are more expensive than calls (bearish hedging demand). Negative skew = calls are more expensive (bullish speculation).

**Data Source:** Deribit API, Laevitas.ch, The Block

**Causal Mechanism:** Skew reflects the market's directional fear. When skew is very positive (>5%), it means institutional hedging demand for puts is extreme -- this is often the point of maximum fear and a contrarian buy signal. When skew is very negative (<-3%), speculative call buying is extreme -- often a top signal.

**Academic Citation:** Bollen & Whaley (2004), "Does Net Buying Pressure Affect the Shape of Implied Volatility Functions?" Applied to crypto: Winkel & Rubtsov (2021).

**Suitability for 1H Swing Trading:** Daily. **Excellent contrarian signal.** Extreme positive skew = buy. Extreme negative skew = sell/fade.

**Signal Type:** ENTRY / EXIT

**Data Availability:** Deribit API (free). **Free.**

---

#### DV-28: Options Term Structure Slope

**Formula:** `Term_Slope = IV_90d - IV_7d`
Positive slope (contango) = longer-term IV higher than short-term (normal). Negative slope (backwardation) = short-term IV exceeds long-term (fear/event pricing).

**Data Source:** Deribit API, Laevitas.ch

**Causal Mechanism:** Term structure inversion (short > long IV) signals the market is pricing imminent danger -- a specific event (regulatory announcement, ETF decision, earnings) or ongoing crisis. This is analogous to yield curve inversion in rates markets. Normalized term structure after inversion = event has passed, mean reversion opportunity.

**Academic Citation:** Mixon (2007), "The Implied Volatility Term Structure." Applied to crypto: Deribit blog research.

**Suitability for 1H Swing Trading:** Daily. Term structure inversion = high alert. Normalization after inversion = swing long entry.

**Signal Type:** FILTER / ENTRY

**Data Availability:** Deribit API (free). **Free.**

---

#### DV-29: Max Pain Price (Options)

**Formula:** `Max_Pain = price_at_which_total_option_value_expiring_worthless_is_maximized`
Computed by iterating over all strikes and finding where the combined put+call holders lose the most.

**Data Source:** Deribit API (compute from OI by strike), Coinglass, Laevitas

**Causal Mechanism:** "Max pain theory" posits that option sellers (dealers) have an incentive to pin price near max pain at expiry, because this minimizes their payout. While not guaranteed, BTC has gravitated toward max pain at monthly and quarterly expiries with ~60% historical accuracy. The mechanism: dealers hedge their gamma exposure by buying/selling spot, creating a magnetic effect.

**Academic Citation:** Ni, Pearson, Poteshman (2005), "Stock Price Clustering on Option Expiration Dates." Applied to crypto: empirical observation.

**Suitability for 1H Swing Trading:** **Useful for expiry-week trades.** 2-3 days before large monthly/quarterly expiry, position for price to drift toward max pain.

**Signal Type:** ENTRY (expiry-specific)

**Data Availability:** Deribit API (free, compute from strike-level OI). **Free.**

---

#### DV-30: Gamma Exposure (GEX)

**Formula:** `GEX = sum(gamma_per_strike * OI_per_strike * contract_multiplier * spot_price)`
Net gamma exposure of option dealers (market makers).

**Data Source:** Deribit API (compute from OI, strikes, prices), Laevitas (pre-computed)

**Causal Mechanism:** When dealers are net long gamma (positive GEX), they hedge by selling rallies and buying dips, SUPPRESSING volatility (market becomes "sticky"). When dealers are net short gamma (negative GEX), they hedge by buying rallies and selling dips, AMPLIFYING volatility. Negative GEX environments produce larger, faster moves -- critical for sizing swing trades.

**Academic Citation:** Bollen & Whaley (2004). Barbon & Buraschi (2021), "Gamma Fragility."

**Suitability for 1H Swing Trading:** **Excellent volatility regime indicator.** Negative GEX = expect 2-3x normal move sizes. Positive GEX = expect range-bound/mean-reversion. Size positions and choose strategies accordingly.

**Signal Type:** SIZING / FILTER

**Data Availability:** Deribit API (free, requires computation). Laevitas paid for pre-computed. **Free if computed.**

---

### 2.5 Options (Additional)

---

#### DV-31: Options Volume Momentum

**Formula:** `Opt_Vol_Mom = SMA(total_options_volume, 7) / SMA(total_options_volume, 30) - 1`

**Data Source:** Deribit API, Laevitas

**Causal Mechanism:** Rising options volume = increasing hedging/speculation demand. Spikes in options volume often precede major moves (informed traders buying options for leverage/protection before events). Volume surge in puts specifically = smart money hedging.

**Academic Citation:** Easley, O'Hara, Srinivas (1998), "Option Volume and Stock Prices."

**Suitability for 1H Swing Trading:** Daily. Volume spike = expect move. Check put/call breakdown for direction.

**Signal Type:** FILTER

**Data Availability:** Deribit API (free). **Free.**

---

#### DV-32: Deribit BTC Volatility Index (DVOL)

**Formula:** Deribit's proprietary VIX-equivalent for BTC. Computed from a strip of near-term option prices using VIX methodology adapted for BTC.

**Data Source:** Deribit API (`GET /public/get_volatility_index_data`), The Block

**Causal Mechanism:** DVOL is the crypto equivalent of the VIX. DVOL > 80 = extreme fear/vol. DVOL < 40 = complacency. Like VIX, it tends to spike on sell-offs and decline during rallies. DVOL > 90 has historically marked local bottoms in BTC (fear exhaustion). Mean-reversion trades on DVOL extremes are profitable.

**Academic Citation:** Deribit methodology paper. Analogy to VIX: Whaley (2000), "The Investor Fear Gauge."

**Suitability for 1H Swing Trading:** Updated continuously. **Strong fear/greed gauge.** DVOL > 80 = contrarian long entry. DVOL < 35 = reduce long exposure.

**Signal Type:** ENTRY / SIZING

**Data Availability:** Deribit API (free). **Free.**

---

#### DV-33: Large Options Block Trades

**Formula:** Count and directional analysis of options block trades > $1M notional.

**Data Source:** Deribit trade feed (WebSocket, free), Laevitas (block trade alerts)

**Causal Mechanism:** Large block trades in options represent institutional positioning. A $10M call purchase at a specific strike signals informed bullish conviction at that level. A $10M put purchase signals hedging or bearish positioning. Tracking the flow of large options trades reveals what sophisticated players expect.

**Academic Citation:** Hu (2014), "Does Option Trading Convey Stock Price Information?" Applied to crypto.

**Suitability for 1H Swing Trading:** Real-time. Useful for directional conviction. Large call blocks near support = bullish confirmation.

**Signal Type:** ENTRY (confirmation)

**Data Availability:** Deribit WebSocket (free). **Free.**

---

### 2.6 Sentiment from Derivatives

---

#### DV-34: Fear & Greed Index (Crypto)

**Formula:** Composite index (0-100) based on: volatility (25%), market momentum/volume (25%), social media (15%), dominance (10%), trends (10%), surveys (15%). Computed by Alternative.me.

**Data Source:** Alternative.me API (free, `https://api.alternative.me/fng/`), Coinglass

**Causal Mechanism:** Aggregates multiple sentiment dimensions into a single score. Fear (< 25) = contrarian buy zone. Extreme Greed (> 75) = contrarian sell zone. Works because crowds are systematically wrong at extremes. The composite nature makes it harder to game than any single component.

**Academic Citation:** Baker & Wurgler (2006), "Investor Sentiment and the Cross-Section of Stock Returns" (concept). Crypto-specific implementation by Alternative.me.

**Suitability for 1H Swing Trading:** Daily. **Solid regime filter.** Fear < 20 = aggressive long bias. Greed > 80 = aggressive short bias / no new longs.

**Signal Type:** FILTER / SIZING

**Data Availability:** Alternative.me API. **Free.**

---

#### DV-35: Crypto Volatility Index (CVI)

**Formula:** CVI is computed from the implied volatility of BTC and ETH options using a methodology similar to the CBOE VIX. Separate from Deribit's DVOL.

**Data Source:** CVI Finance (cvi.finance), on-chain (Ethereum-based oracle)

**Causal Mechanism:** Similar to DVOL (DV-32) but from a different methodology and source. CVI provides a decentralized volatility measure. Cross-referencing CVI with DVOL can identify discrepancies between decentralized and centralized volatility pricing.

**Academic Citation:** CVI Finance whitepaper.

**Suitability for 1H Swing Trading:** Daily. Secondary to DVOL. Use for cross-validation.

**Signal Type:** FILTER

**Data Availability:** CVI Finance (free oracle data on-chain). **Free.**

---

#### DV-36: Leveraged Longs vs Shorts Ratio (Bitfinex Margin)

**Formula:** `Bitfinex_LS = BTC_margin_longs / BTC_margin_shorts` from Bitfinex margin book.

**Data Source:** Bitfinex API (free, `GET /v2/stats1/pos.size:sym:tBTCUSD:long`), TradingView

**Causal Mechanism:** Bitfinex margin data is one of the oldest available datasets for crypto positioning. Unlike perpetual funding, margin positions have explicit borrow costs that change with demand. Extreme Bitfinex long/short imbalance has historically preceded corrections (when longs dominate) or rallies (when shorts dominate). The Bitfinex user base skews toward larger, more sophisticated traders.

**Academic Citation:** Empirical. Bitfinex/Tether ecosystem analysis by Griffin & Shams (2020).

**Suitability for 1H Swing Trading:** Updated in real-time. **Good contrarian signal.** Historical accuracy is decent but Bitfinex volume has declined relative to Binance/Bybit.

**Signal Type:** ENTRY (contrarian)

**Data Availability:** Bitfinex API (free). **Free.**

---

#### DV-37: Margin Lending Rate

**Formula:** Annualized borrow rate for USDT/USD or BTC on margin lending platforms.

**Data Source:** Bitfinex API (`GET /v2/stats1/credits.size.sym`), Aave/Compound (DeFi rates, on-chain)

**Causal Mechanism:** Rising borrow rates = increasing demand for leverage (traders want to borrow to go long/short). Spiking borrow rates (>50% annualized for USDT) indicate extreme leverage demand -- often near local tops. Very low borrow rates (<5%) indicate low demand for leverage -- often near bottoms when positioning is light.

**Academic Citation:** Brunnermeier & Pedersen (2009), "Market Liquidity and Funding Liquidity."

**Suitability for 1H Swing Trading:** Updated hourly. **Good leverage demand gauge.** Spiking rates = approaching a crowded positioning extreme.

**Signal Type:** FILTER

**Data Availability:** Bitfinex (free). Aave/Compound (free, on-chain). **Free.**

---

#### DV-38: Perpetual Volume / Spot Volume Ratio

**Formula:** `Perp_Spot = Perpetual_Futures_Volume / Spot_Volume`

**Data Source:** Coinglass, exchange APIs.

**Causal Mechanism:** When perpetual volume far exceeds spot volume (ratio > 5), the market is driven by leveraged speculation rather than organic spot demand. This creates fragile rallies/dumps that reverse quickly. When spot volume is relatively high (ratio < 2), moves are more organic and sustainable. This distinguishes between "leverage-driven" and "spot-driven" regimes.

**Academic Citation:** Empirical. Related to Shiller (2000) irrational exuberance concepts applied to crypto leverage.

**Suitability for 1H Swing Trading:** Hourly. **Regime indicator.** Perp/Spot > 5 = leverage-driven (mean-reversion strategies work better). Perp/Spot < 2 = spot-driven (trend-following works better).

**Signal Type:** FILTER (strategy selection)

**Data Availability:** Coinglass (free), exchange APIs. **Free.**

---

## 3. Data Source Summary

| Source | Free Tier | Paid Tier | Best For | API Quality |
|--------|-----------|-----------|----------|-------------|
| **Coinglass** | Aggregated OI, funding, liquidations (delayed) | $50/mo Pro | Derivatives data (OI, funding, liquidations) | Good REST API |
| **Binance API** | Full derivatives data (funding, LS ratio, taker ratio, OI) | N/A (free) | Primary exchange data | Excellent, well-documented |
| **Deribit API** | Full options data (IV, skew, OI by strike, DVOL) | N/A (free) | Options analytics | Good REST + WebSocket |
| **Glassnode** | Limited (24h delay, subset of metrics) | $29/mo (Tier 2), $799/mo (Tier 3) | On-chain (MVRV, SOPR, NUPL, supply, exchange flows) | Excellent REST API |
| **CryptoQuant** | Limited free | $39/mo Pro, $199/mo Premium | On-chain (exchange flows, miner flows, stablecoins) | Good REST API |
| **DefiLlama** | Full (all TVL/stablecoin data) | N/A (free) | DeFi TVL, stablecoin supply | Good REST API |
| **Blockchain.com** | Full BTC on-chain data | N/A (free) | BTC active addresses, tx count, hash rate | Basic REST API |
| **Alternative.me** | Full (Fear & Greed) | N/A (free) | Sentiment index | Simple REST API |
| **Etherscan** | Rate-limited free | Paid plans | ETH gas data | Good REST API |
| **Bitfinex** | Full margin data | N/A (free) | Margin positioning | Good REST API |
| **Laevitas** | Limited charts | Paid | Options analytics, GEX | Dashboard-focused |

### Cost-Optimized Data Strategy

**Free-only portfolio (28 indicators achievable):**
All derivatives indicators (DV-01 through DV-38) are available free from exchange APIs + Coinglass + Deribit + Alternative.me. On-chain: BTC active addresses, tx count, hash rate, NVT, velocity, S2F, gas prices, DeFi TVL, stablecoin supply from Blockchain.com + DefiLlama + Etherscan.

**Paid tier 1 ($70-90/mo for Glassnode Tier 2 + CryptoQuant Pro):**
Adds: MVRV, SOPR, NUPL, exchange netflow, CDD, realized price, NVT Signal. This is the sweet spot for cost/value.

**Paid tier 2 ($1000+/mo for Glassnode Tier 3 + CryptoQuant Premium):**
Adds: STH/LTH-SOPR, HODL Waves, hourly exchange flows, top 1% supply. Diminishing returns for swing trading.

**Recommendation: Start with free tier (derivatives-heavy), add Glassnode Tier 2 ($29/mo) when validating on-chain signals in backtest.**

---

## 4. Priority Ranking for Implementation

Ranked by (signal quality for 1H swing trading) * (data availability) * (uniqueness of information).

### Tier 1: Implement First (High impact, free data)

| # | Indicator | Signal Type | Why Priority |
|---|-----------|-------------|--------------|
| DV-01 | Funding Rate (raw) | Entry/Exit | Direct positioning measure, free, 8h update |
| DV-03 | Funding Rate Z-Score | Entry/Exit | Normalized extremes, free, high alpha |
| DV-07 | Open Interest (abs) | Filter/Sizing | Essential context for all other signals, free |
| DV-08 | OI Change Rate | Sizing/Filter | Cascade risk early warning, free |
| DV-15 | Liquidation Volume | Entry | Post-cascade entries, free, real-time |
| DV-16 | Long Liq Dominance | Entry | Cascade exhaustion signal, free |
| DV-20 | Futures Basis | Filter/Entry | Sentiment from sophisticated traders, free |
| DV-24 | Options IV (25d) | Sizing/Filter | Volatility expectation, free (Deribit) |
| DV-25 | IV/RV Ratio | Entry/Sizing | Fear/complacency gauge, free |
| DV-27 | Options Skew | Entry/Exit | Directional fear from options market, free |
| DV-34 | Fear & Greed Index | Filter/Sizing | Composite sentiment, free |
| DV-14 | Taker Buy/Sell Ratio | Entry | Direct order flow signal, free (Binance) |

### Tier 2: Implement Second (High impact, free or cheap data)

| # | Indicator | Signal Type | Why Priority |
|---|-----------|-------------|--------------|
| DV-02 | Funding Rate 7d MA | Filter | Regime context for FR signals |
| DV-04 | FR Momentum | Entry | Second derivative of positioning |
| DV-05 | Aggregated FR | Entry/Exit | Better than single-exchange FR |
| DV-09 | OI/Volume Ratio | Sizing | Leverage buildup detector |
| DV-12 | Long/Short Ratio | Entry | Contrarian crowd signal, free |
| DV-13 | Top Trader LS Ratio | Entry | Smart money signal, free |
| DV-21 | Perpetual Premium | Entry | Real-time perp vs spot, free |
| DV-26 | Put-Call Ratio | Entry | Classic contrarian indicator, free |
| DV-29 | Max Pain Price | Entry | Expiry-week magnet level, free |
| DV-30 | Gamma Exposure | Sizing | Volatility regime (amplify/suppress), computable |
| DV-32 | DVOL | Entry/Sizing | Crypto VIX equivalent, free |
| DV-38 | Perp/Spot Volume Ratio | Filter | Leverage vs organic demand regime, free |
| OC-36 | Stablecoin Supply Ratio | Filter/Entry | Dry powder gauge, partially free |
| OC-38 | DeFi TVL Momentum | Filter | Risk appetite gauge, free |

### Tier 3: Implement Third (Requires paid on-chain data)

| # | Indicator | Signal Type | Why Priority |
|---|-----------|-------------|--------------|
| OC-01 | MVRV Ratio | Filter/Sizing | Best on-chain valuation metric |
| OC-03 | SOPR / aSOPR | Entry/Exit | Profit-taking behavior |
| OC-07 | NUPL | Filter/Sizing | Market-wide P&L state |
| OC-21 | Exchange Balance | Entry/Exit | Supply available for selling |
| OC-22 | Exchange Netflow | Entry/Exit | Direct flow signal |
| OC-26 | CDD | Exit | Old coin movement detection |
| OC-12 | Active Address Count | Filter | Network health, free for BTC |
| OC-31 | Hash Rate Momentum | Filter | Miner capitulation signal, free |
| OC-34 | Hash Ribbons | Entry (rare) | Generational buy signal, free |

### Tier 4: Research / Low Priority

| # | Indicator | Reason for Lower Priority |
|---|-----------|--------------------------|
| OC-02 | MVRV Z-Score | Redundant with MVRV |
| OC-09 | S2F Deviation | Model validity questioned |
| OC-11 | Thermocap | Very slow, monthly regime only |
| OC-27 | HODL Waves | Weekly, slow for swing trading |
| OC-28 | RC HODL Waves | Same as above, more expensive data |
| OC-35 | Difficulty Ribbon | Monthly, very slow |
| DV-06 | FR Cross-Exchange Divergence | Low standalone signal value |
| DV-11 | OI Concentration | Structural, not directional |
| DV-23 | Cross-Exchange Basis Div | Low standalone signal value |
| DV-35 | CVI | Redundant with DVOL |

---

## Appendix: Signal Combination Framework

### Composite Signal Construction

The indicators above should NOT be used in isolation. Effective swing trading requires combining signals across categories:

**Entry Signal = Trigger + Confirmation + Regime Filter**

Example long entry:
- **Trigger:** FR Z-Score < -2 (shorts crowded) OR Long Liq Dominance > 80% (cascade exhaustion)
- **Confirmation:** Taker buy ratio > 1.1 AND OI declining (shorts closing, not new longs)
- **Regime Filter:** MVRV < 2.5 AND Fear & Greed < 40 AND Basis < 15%

Example short entry:
- **Trigger:** FR Z-Score > 2.5 (longs crowded) OR OI increase > 10% in 72h with price flat
- **Confirmation:** Taker buy ratio < 0.9 AND exchange netflow strongly positive
- **Regime Filter:** MVRV > 3.0 OR NUPL > 0.7 OR Fear & Greed > 75

### Position Sizing Modifiers

| Condition | Size Adjustment |
|-----------|----------------|
| GEX negative | 0.5x (expect amplified moves) |
| IV/RV > 1.3 | 0.7x (high vol premium, moves may reverse) |
| OI/Volume > 2 std | 0.5x (cascade risk) |
| Illiquid supply rising + low exchange balance | 1.3x (supply squeeze favorable) |
| Perp/Spot ratio > 5 | 0.7x (leverage-driven, fragile) |

---

*This catalog contains 39 on-chain indicators and 38 derivatives indicators (77 total). Each entry includes data source, causal mechanism, signal type, and data availability assessment. The priority ranking in Section 4 provides a clear implementation roadmap starting with free, high-impact derivatives signals and progressively adding paid on-chain data as the system matures.*
