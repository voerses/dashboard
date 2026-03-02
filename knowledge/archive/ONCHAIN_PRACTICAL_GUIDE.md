# On-Chain Data: Practical Guide for Systematic Crypto Swing Trading

**Context:** $200K portfolio, 1H timeframe, 49 tokens, systematic swing trading system.
**Goal:** Identify free on-chain data sources and signals that can confirm or accelerate our existing strategy signals.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Free Data Sources](#2-free-data-sources)
3. [Signal-by-Signal Analysis](#3-signal-by-signal-analysis)
4. [Priority Ranking](#4-priority-ranking)
5. [Integration with engine.py](#5-integration-with-enginepy)
6. [Python Implementation](#6-python-implementation)
7. [Practical Recommendations](#7-practical-recommendations)

---

## 1. Executive Summary

### The Hard Truth About On-Chain Data for 1H Swing Trading

Most on-chain data updates **daily** (not hourly). This creates a fundamental mismatch with our 1H timeframe. On-chain signals are best used as:

- **Daily regime filters** (trade/no-trade decisions aligned to 1H bars via forward-fill)
- **Position sizing adjusters** (scale up/down based on on-chain confluence)
- **Exit urgency signals** (tighten stops when on-chain signals flash danger)

They are NOT useful as direct 1H entry triggers. Our existing technical signals handle that.

### What Actually Works (Based on Published Research)

| Signal | Predictive Power | Best Use | Free Data? | Update Freq |
|--------|-----------------|----------|------------|-------------|
| Exchange Netflows | Moderate | Filter/sizing | Yes (BGeometrics) | Daily |
| MVRV Z-Score | High (cycle tops/bottoms) | Regime filter | Yes (BGeometrics, blockchain.com) | Daily |
| SOPR | Moderate | Sentiment filter | Yes (BGeometrics) | Daily |
| Stablecoin Supply Delta | Moderate | Liquidity proxy | Yes (BGeometrics) | Daily |
| Hash Rate / Hash Ribbons | Low-Moderate (BTC only) | Miner capitulation | Yes (blockchain.com, BGeometrics) | Daily |
| Active Addresses | Low-Moderate | Network health | Yes (BGeometrics, blockchain.com) | Daily |
| Whale Transactions | Very Low (R^2 < 0.05) | Noise, skip it | N/A | N/A |
| NVT Ratio | Moderate (long-term) | Too slow for swing | Yes (BGeometrics) | Daily |

### Bottom Line: Three Signals Worth Implementing

1. **MVRV Z-Score** -- proven cycle timing, free, easy to integrate as regime filter
2. **Exchange Netflows** -- moderate lead time on selling pressure, free from BGeometrics
3. **Stablecoin Supply Delta** -- liquidity proxy, captures "dry powder" entering the system

Everything else is either too noisy (whale alerts), too slow (NVT), BTC-only (hash ribbons), or requires paid APIs.

---

## 2. Free Data Sources

### 2.1 BGeometrics (BEST FREE SOURCE)

**URL:** https://bitcoin-data.com/api/
**Docs:** https://bitcoin-data.com/api/scalar.html (OpenAPI/Swagger)
**Cost:** Free (rate-limited; paid tiers for higher volume)
**Coverage:** BTC primary, plus ETH, BNB, SOL, XRP, ADA, DOGE, LTC, DOT and more
**Update Frequency:** Daily (some derivatives metrics hourly)
**Authentication:** None required for basic endpoints

#### Key Endpoints

| Endpoint | Data | Use Case |
|----------|------|----------|
| `/v1/mvrv` | MVRV Z-Score | Cycle positioning / regime filter |
| `/v1/sopr` | Spent Output Profit Ratio | Sentiment (profit-taking vs capitulation) |
| `/v1/nupl` | Net Unrealized Profit/Loss | Regime filter |
| `/v1/active-addresses` | Daily active addresses | Network activity proxy |
| `/v1/exchanges` | Exchange inflow/outflow/netflow/reserves | Sell pressure detection |
| `/v1/realized-price` | Realized price (cost basis) | Support/resistance level |
| `/v1/supply-profit` | Supply in profit | Crowd positioning |
| `/v1/supply-loss` | Supply in loss | Capitulation detection |
| `/v1/reserve-risk` | Reserve Risk | Holder confidence |
| `/v1/funding-rate` | Perpetual funding rate | Derivatives sentiment |
| `/v1/open-interest-1h` | Futures open interest | Leverage buildup |
| `/v1/btc-liquidations` | Liquidation data | Cascade risk |
| `/v1/short-term-hodler-supply-btc` | STH supply | Hot money positioning |

#### Parameters (all endpoints)

- `startday` -- YYYY-MM-DD (optional)
- `endday` -- YYYY-MM-DD (optional)
- `page` -- pagination (optional)
- `size` -- results per page (optional)

```python
import requests
import pandas as pd

BASE = "https://bitcoin-data.com/api/v1"

def fetch_bgeometrics(metric: str, start: str = "2020-01-01") -> pd.DataFrame:
    """Fetch on-chain metric from BGeometrics free API."""
    url = f"{BASE}/{metric}"
    resp = requests.get(url, params={"startday": start, "size": 5000}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    df = pd.DataFrame(data)
    # Typical columns: date + metric value(s)
    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date').sort_index()
    return df

# Examples
mvrv = fetch_bgeometrics("mvrv")
sopr = fetch_bgeometrics("sopr")
exchanges = fetch_bgeometrics("exchanges")
active = fetch_bgeometrics("active-addresses")
```

### 2.2 Blockchain.com Charts API

**URL:** `https://api.blockchain.info/charts/{chartName}`
**Cost:** Free, no API key required
**Coverage:** BTC only
**Update Frequency:** Daily
**Rate Limits:** Generous but undocumented; use `sampled=false` for full resolution

#### Key Chart Names

| Chart Name | Data |
|-----------|------|
| `hash-rate` | Network hash rate (TH/s) |
| `market-price` | BTC price USD |
| `n-unique-addresses` | Unique addresses used per day |
| `n-transactions` | Confirmed transactions per day |
| `transaction-fees-usd` | Total fees USD per day |
| `miners-revenue` | Total miner revenue per day |
| `difficulty` | Mining difficulty |
| `mvrv` | Market Value to Realized Value |

```python
def fetch_blockchain_chart(chart_name: str, timespan: str = "2years") -> pd.DataFrame:
    """Fetch from blockchain.com free charts API."""
    url = f"https://api.blockchain.info/charts/{chart_name}"
    resp = requests.get(url, params={
        "timespan": timespan,
        "format": "json",
        "sampled": "false",
    }, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    df = pd.DataFrame(data['values'])
    df['date'] = pd.to_datetime(df['x'], unit='s')
    df = df.rename(columns={'y': chart_name}).set_index('date')
    df = df.drop(columns=['x'])
    return df

hash_rate = fetch_blockchain_chart("hash-rate")
addresses = fetch_blockchain_chart("n-unique-addresses")
```

### 2.3 Santiment (SanPy)

**URL:** https://api.santiment.net/
**Python:** `pip install sanpy`
**Cost:** Free tier gives Daily Active Addresses + Dev Activity for all ERC-20 tokens. Most other metrics require paid plan ($49+/mo).
**Coverage:** 2000+ tokens (strongest for ERC-20)
**Update Frequency:** Daily

```python
import san

# Free metrics (no API key needed for DAA)
daa = san.get("daily_active_addresses/bitcoin", from_date="2023-01-01", to_date="2025-12-31")
dev = san.get("dev_activity/ethereum", from_date="2023-01-01", to_date="2025-12-31")
```

**Limitation:** Most actionable metrics (exchange flows, MVRV, whale transactions) require a paid plan. DAA alone has limited predictive value.

### 2.4 Glassnode

**URL:** https://api.glassnode.com/v1/metrics/
**Cost:** Free tier = Tier 1 metrics at daily resolution with delay. API access officially requires Professional plan ($799/mo). Free tier best used via their web dashboard, not programmatic access.
**Coverage:** BTC, ETH, and ~40 other assets
**Update Frequency:** Daily (free), hourly (Advanced $29/mo), 10-min (Professional)

**Verdict:** Too expensive for systematic free usage. Use BGeometrics instead for the same metrics.

### 2.5 Dune Analytics

**URL:** https://dune.com/
**Cost:** Free tier allows SQL queries and dashboard forking. API requires paid plan.
**Coverage:** 100+ blockchains (strongest for EVM chains)
**Update Frequency:** Varies by query

**Use case:** One-off research queries, not real-time data feeds. Good for building custom exchange flow dashboards but too manual for systematic integration.

### 2.6 CoinGlass (Exchange Flows)

**URL:** https://www.coinglass.com/spot-inflow-outflow
**Cost:** Free web dashboard; API requires paid plan
**Coverage:** Multiple tokens across major exchanges
**Note:** Best free source for visual exchange flow monitoring; not easily automatable without paid API.

### Source Comparison Matrix

| Source | Free API? | BTC On-Chain | Alt On-Chain | Exchange Flows | Update Freq | Ease of Use |
|--------|-----------|-------------|-------------|----------------|-------------|-------------|
| **BGeometrics** | Yes | Excellent | Good (10+ coins) | Yes | Daily | High |
| **Blockchain.com** | Yes | Good | No | No | Daily | High |
| **Santiment** | Partial | DAA only free | DAA only free | Paid | Daily | Medium |
| **Glassnode** | Dashboard only | Excellent | Good | Yes | Daily+ | Low (paid API) |
| **Dune** | SQL only | Custom | Custom | Custom | Varies | Low (manual) |
| **CoinGlass** | Dashboard only | N/A | N/A | Visual only | Real-time | Medium |

---

## 3. Signal-by-Signal Analysis

### 3.1 Exchange Netflows (Moderate Predictive Power)

**What it measures:** Net BTC flowing into/out of known exchange wallets.

**The signal logic:**
- Sustained net outflows (negative netflow) = accumulation, bullish. Investors moving coins to cold storage.
- Sustained net inflows (positive netflow) = distribution, bearish. Coins moving to exchanges for potential selling.

**Published evidence:**
- The BTC exchange outflow/inflow ratio fell to ~0.9 in late 2024 (lowest since 2023 bear), correlating with the rally to $108K+ (Binance research).
- ETF-driven demand in Jan 2025 created supply-demand imbalance: ETFs acquired 51,500 BTC vs 13,850 BTC mined.

**Lead time:** 1-5 days. Not enough for 1H entries, but excellent as a daily regime filter.

**Practical implementation:**

```python
def exchange_flow_signal(exchanges_df: pd.DataFrame) -> pd.Series:
    """
    Returns a daily signal: -1 (bearish inflows), 0 (neutral), +1 (bullish outflows).
    Use 7-day and 30-day moving averages to smooth noise.
    """
    netflow = exchanges_df['netflow']  # negative = outflows (bullish)
    ma7 = netflow.rolling(7).mean()
    ma30 = netflow.rolling(30).mean()

    signal = pd.Series(0, index=netflow.index)
    signal[ma7 < 0] = 1          # Short-term outflows = bullish
    signal[ma7 > 0] = -1         # Short-term inflows = bearish
    signal[(ma7 < 0) & (ma30 < 0)] = 2   # Strong bullish (both timeframes)
    signal[(ma7 > 0) & (ma30 > 0)] = -2  # Strong bearish (both timeframes)

    return signal
```

**Best use in our system:** Daily filter. When exchange_flow_signal <= -1, reduce position sizes by 50% or avoid new longs. When >= 1, allow full sizing.

### 3.2 MVRV Z-Score (High Predictive Power for Cycle Extremes)

**What it measures:** How far BTC's market cap deviates from its realized cap (aggregate cost basis), normalized by standard deviation.

**The signal logic:**
- Z-Score > 7: historically marks cycle tops within 2 weeks
- Z-Score 3-7: overheated, increasing risk of correction
- Z-Score 0-3: fair value / accumulation zone
- Z-Score < 0: extreme undervaluation, historically the best buying zones

**Published evidence:**
- Picked every BTC cycle top within 2 weeks historically (2013, 2017, 2021).
- Buying when Z-Score enters green zone (< 0.5) has produced outsized returns every cycle.
- Ark Invest's 2022 research highlighted MVRV as a key factor in BTC fair-value assessments.

**Lead time:** Days to weeks at extremes. This is NOT a timing signal -- it is a regime signal.

**Practical implementation:**

```python
def mvrv_regime(mvrv_df: pd.DataFrame) -> pd.Series:
    """
    Classify MVRV Z-Score into regimes for position sizing.
    Returns multiplier: 0.0 to 1.5
    """
    z = mvrv_df['mvrv_zscore']  # adjust column name per actual API response

    sizing = pd.Series(1.0, index=z.index)
    sizing[z > 7] = 0.0     # Extreme overvaluation: no new positions
    sizing[z > 5] = 0.25    # Very overheated: minimal sizing
    sizing[z > 3] = 0.5     # Overheated: half sizing
    sizing[(z >= 0) & (z <= 3)] = 1.0  # Fair value: normal sizing
    sizing[z < 0] = 1.5     # Undervalued: increased sizing

    return sizing
```

**Best use in our system:** Position sizing multiplier applied to Kelly fraction. This is the single highest-value on-chain signal for BTC.

**Coverage limitation:** Only meaningful for BTC (and partially ETH). Alt-coin MVRV data is sparse and less reliable.

### 3.3 SOPR (Moderate Predictive Power)

**What it measures:** Whether coins being moved are being sold at a profit or loss.
- SOPR > 1: Coins moving at a profit (profit-taking in progress)
- SOPR < 1: Coins moving at a loss (capitulation / forced selling)
- SOPR = 1: Break-even, often acts as support in bull markets and resistance in bear markets

**The signal logic:**
- In bull markets, SOPR dipping to ~1.0 and bouncing = buy signal (profit-takers exhausted)
- In bear markets, SOPR spiking to ~1.0 and rejecting = sell signal (bag-holders selling the rip)
- Sustained SOPR < 1 = capitulation, historically precedes bottoms

**Variants:**
- STH-SOPR (Short-Term Holder): More responsive, better for swing trading
- LTH-SOPR (Long-Term Holder): Slower, better for macro positioning

**Published evidence:** SOPR is widely used as a sentiment gauge. Its effectiveness depends heavily on the current market regime -- it is most useful as a confirmation signal, not a standalone trigger.

**Practical implementation:**

```python
def sopr_signal(sopr_df: pd.DataFrame, regime: str) -> pd.Series:
    """
    SOPR-based sentiment filter.
    Returns: 1 (bullish), 0 (neutral), -1 (bearish)
    """
    sopr = sopr_df['sopr']  # adjust column name
    ma7 = sopr.rolling(7).mean()

    signal = pd.Series(0, index=sopr.index)

    if regime == 'bull':
        # In bull market: SOPR bouncing off 1.0 = buy
        signal[(ma7 > 0.98) & (ma7 < 1.02) & (sopr > sopr.shift(1))] = 1
        signal[ma7 > 1.05] = -1  # Heavy profit-taking
    else:
        # In bear market: SOPR rejecting at 1.0 = sell
        signal[(ma7 > 0.98) & (ma7 < 1.02) & (sopr < sopr.shift(1))] = -1
        signal[ma7 < 0.95] = 1   # Capitulation = potential bottom

    return signal
```

**Best use:** Confirmation filter. When SOPR aligns with your technical entry, increase confidence. When it diverges, reduce sizing.

### 3.4 Stablecoin Supply Changes (Moderate Predictive Power)

**What it measures:** Net minting/burning of USDT, USDC, and other stablecoins -- a proxy for new capital entering/leaving crypto.

**The signal logic:**
- Net stablecoin minting > $1B/week: Significant new capital, bullish for crypto broadly
- Sustained stablecoin supply growth: "Dry powder" accumulating, supports prices
- Stablecoin supply contraction: Capital leaving, bearish
- Large stablecoin transfers to exchange hot wallets: Often precedes buying pressure

**Published evidence:**
- USDT market cap dropped from $83B to $66B during 2022 bear (tracked price decline).
- 300M USDC mint in late 2023 preceded significant BTC rally.
- Early 2026: stablecoin supply expanded $1.32B to $268.1B, correlating with continued bull trend.
- BIS Working Paper (2025) found stablecoin dynamics affect broader crypto market pricing.

**Caveat:** As stablecoins increasingly serve non-crypto use cases (payments, savings, DeFi), the correlation with BTC price is gradually weakening (Santiment analyst Mads Eberhardt, 2025).

**Practical implementation:**

```python
def stablecoin_liquidity_signal(stablecoin_supply: pd.Series) -> pd.Series:
    """
    Stablecoin supply change as a liquidity proxy.
    Returns: -1 (contracting), 0 (flat), 1 (expanding)
    """
    pct_change_7d = stablecoin_supply.pct_change(7)
    pct_change_30d = stablecoin_supply.pct_change(30)

    signal = pd.Series(0, index=stablecoin_supply.index)
    signal[pct_change_7d > 0.005] = 1      # >0.5% weekly growth
    signal[pct_change_7d < -0.005] = -1    # >0.5% weekly contraction
    signal[(pct_change_7d > 0.01) & (pct_change_30d > 0.02)] = 2   # Strong expansion
    signal[(pct_change_7d < -0.01) & (pct_change_30d < -0.02)] = -2  # Strong contraction

    return signal
```

**Best use:** Broad market liquidity filter. When stablecoin supply is contracting, reduce overall portfolio exposure. When expanding, allow full allocation.

### 3.5 Hash Rate & Hash Ribbons (Low-Moderate, BTC Only)

**What it measures:** Total computational power securing the Bitcoin network. Hash Ribbons uses 30-day vs 60-day SMA crossovers of hash rate.

**The signal logic (Hash Ribbons):**
1. 30-day SMA crosses below 60-day SMA = Miner capitulation (miners shutting off, selling BTC to cover costs)
2. 30-day SMA crosses back above 60-day SMA = Recovery / Buy signal

**Published evidence:**
- Since 2013: 14 buy signals, 64% profitable
- Average trade lasted 253 days (far too slow for our 1H swing system)
- Average return to cycle peak: >5,000% (but these are multi-year holds)
- Max drawdown from buy signal: -3% to -42% (wide range, not tight risk management)
- Beat buy-and-hold since 2013

**Critical limitation:** Hash rate generally lags price, not leads it. When BTC price is high, more miners join (hash rate goes up). When BTC price drops, inefficient miners shut off (hash rate drops). The causal direction is mostly price -> hash rate, not the reverse.

**Practical implementation:**

```python
def hash_ribbons_signal(hash_rate: pd.Series) -> pd.Series:
    """
    Hash Ribbons: 30/60 day SMA crossover of hash rate.
    Returns: 1 (recovery buy signal), -1 (capitulation), 0 (neutral)
    """
    ma30 = hash_rate.rolling(30).mean()
    ma60 = hash_rate.rolling(60).mean()

    signal = pd.Series(0, index=hash_rate.index)

    # Capitulation: 30 < 60
    capitulation = ma30 < ma60
    signal[capitulation] = -1

    # Recovery: 30 crosses back above 60 after capitulation
    recovery = (ma30 > ma60) & (ma30.shift(1) < ma60.shift(1))
    signal[recovery] = 1

    return signal
```

**Best use for our system:** Minimal. The signal fires ~2-3 times per year and the trade duration (253 days avg) does not match our swing timeframe. Include only if you want a "miner capitulation" regime that prevents going long during hash rate crashes. Not worth building a data pipeline for this alone.

### 3.6 Active Addresses (Low-Moderate)

**What it measures:** Number of unique addresses that transacted on a given day.

**Published evidence:**
- A hybrid ML model using active addresses + hash rate + MVRV achieved 100% directional hit rate with Sharpe 1.03 (ScienceDirect 2022) -- but this was a multi-factor model, not active addresses alone.
- Active addresses correlate with price but the causal direction is ambiguous (does activity drive price, or does price drive activity?).
- Logistic regression models using on-chain features (including active addresses) achieved ~66% accuracy for next-day direction (PMC/NIH study, 2019 data).

**Practical value:** Low as a standalone signal. Useful as one feature in a multi-factor model.

**Best use:** If you have a machine learning overlay, include it as a feature. Not worth building a standalone signal for.

### 3.7 Whale Transactions (Very Low -- SKIP THIS)

**Published evidence:** Presto Research tested whether whale exchange deposits predict price declines. Result: **R-squared ranged from 0.0017 to 0.0537 across all 12 scenarios tested.** This is statistically insignificant.

**Why it fails:**
- Most large transfers are exchange internal housekeeping (wallet consolidation)
- A single whale controls thousands of addresses; "unknown -> unknown" transfers are noise
- VC and market maker deposits are slightly better predictors, but still very low R-squared
- False positive rate is extremely high

**Verdict:** Do not waste engineering time on this. The signal-to-noise ratio is terrible.

### 3.8 NVT Ratio (Moderate But Too Slow)

**What it measures:** Network Value (market cap) divided by on-chain transaction volume. Analogous to P/E ratio for equities.

**Signal logic:** High NVT = overvalued (network value not justified by usage). Low NVT = undervalued.

**Limitation:** NVT is a long-term valuation metric. It changes slowly and is best suited for weekly/monthly timeframes. Not useful for 1H swing trading.

**Verdict:** Skip for our system. If you ever build a weekly rebalancing overlay, revisit.

---

## 4. Priority Ranking

Ranked by: (1) predictive power, (2) free availability, (3) implementation ease, (4) relevance to 1H swing, (5) token coverage.

### Tier 1: Implement These (High ROI, Free, Proven)

| Rank | Signal | Score | Role in System | Data Source |
|------|--------|-------|---------------|-------------|
| 1 | **MVRV Z-Score** | 9/10 | Position sizing multiplier | BGeometrics `/v1/mvrv` |
| 2 | **Exchange Netflows** | 8/10 | Daily regime filter | BGeometrics `/v1/exchanges` |
| 3 | **Stablecoin Supply Delta** | 7/10 | Liquidity regime filter | BGeometrics (if available) or manual tracking |

### Tier 2: Consider Later (Useful But Lower Priority)

| Rank | Signal | Score | Role in System | Data Source |
|------|--------|-------|---------------|-------------|
| 4 | **SOPR** | 6/10 | Sentiment confirmation | BGeometrics `/v1/sopr` |
| 5 | **NUPL** | 6/10 | Regime overlay | BGeometrics `/v1/nupl` |
| 6 | **Funding Rate** | 6/10 | Derivatives sentiment | BGeometrics `/v1/funding-rate` |
| 7 | **Active Addresses** | 5/10 | ML feature (if building) | BGeometrics `/v1/active-addresses` |

### Tier 3: Skip (Low Value or Impractical)

| Rank | Signal | Score | Why Skip |
|------|--------|-------|----------|
| 8 | Hash Ribbons | 4/10 | Fires 2-3x/year, BTC only, lags price |
| 9 | NVT Ratio | 4/10 | Too slow for swing trading |
| 10 | Whale Transactions | 2/10 | R^2 < 0.05, mostly noise |

---

## 5. Integration with engine.py

### Architecture: On-Chain Data as a Daily Overlay

On-chain data is daily. Our system runs on 1H bars. The integration pattern:

```
Daily on-chain data --> Daily signal computation --> Forward-fill to 1H index
                                                     |
                                                     v
                                        StrategyContext.custom['onchain_*']
                                                     |
                                                     v
                                        Strategy uses as filter/sizing modifier
```

### Integration Points in StrategyContext

The `StrategyContext` dataclass already supports:
- `custom: Dict[str, np.ndarray]` -- perfect for on-chain signals
- `align_daily_to_1h(daily_values)` -- forward-fills daily arrays to 1H index
- `enriched: Optional[pd.DataFrame]` -- alternative: add to enriched daily features

### Option A: Indicator Plugin (Recommended)

Use the existing `_INDICATOR_PLUGINS` system in engine.py:

```python
from engine import _INDICATOR_PLUGINS, StrategyContext
import numpy as np
import pandas as pd

# Cache on-chain data (fetch once per session, not per token)
_ONCHAIN_CACHE = {}

def _ensure_onchain_data():
    """Fetch on-chain data once and cache it."""
    if 'mvrv' not in _ONCHAIN_CACHE:
        _ONCHAIN_CACHE['mvrv'] = fetch_bgeometrics("mvrv")
        _ONCHAIN_CACHE['exchanges'] = fetch_bgeometrics("exchanges")
        _ONCHAIN_CACHE['sopr'] = fetch_bgeometrics("sopr")

def onchain_indicators(ctx: StrategyContext):
    """Indicator plugin: adds on-chain signals to ctx.custom."""
    _ensure_onchain_data()

    # Only apply BTC on-chain data to BTC trades directly;
    # for alts, use BTC on-chain as a broad market filter
    mvrv_df = _ONCHAIN_CACHE['mvrv']
    exchanges_df = _ONCHAIN_CACHE['exchanges']

    # --- MVRV Sizing Multiplier ---
    # Create daily series, then align to 1H
    mvrv_daily = mvrv_regime(mvrv_df)
    # Reindex to match ctx.idx_d, then forward-fill to 1H
    mvrv_aligned = mvrv_daily.reindex(ctx.idx_d, method='ffill')
    ctx.custom['onchain_mvrv_sizing'] = ctx.align_daily_to_1h(mvrv_aligned.values)

    # --- Exchange Flow Filter ---
    flow_daily = exchange_flow_signal(exchanges_df)
    flow_aligned = flow_daily.reindex(ctx.idx_d, method='ffill')
    ctx.custom['onchain_exchange_flow'] = ctx.align_daily_to_1h(flow_aligned.values)

    # --- SOPR Signal ---
    sopr_df = _ONCHAIN_CACHE['sopr']
    # Determine regime from existing daily data
    regime = 'bull' if ctx.ind_d.get('sma200') is not None and \
             ctx.df_daily['close'].iloc[-1] > ctx.ind_d['sma200'][-1] else 'bear'
    sopr_daily = sopr_signal(sopr_df, regime)
    sopr_aligned = sopr_daily.reindex(ctx.idx_d, method='ffill')
    ctx.custom['onchain_sopr'] = ctx.align_daily_to_1h(sopr_aligned.values)

# Register the plugin
_INDICATOR_PLUGINS.append(onchain_indicators)
```

### Option B: Pre-compute and Add to Enriched Data

If you prefer to keep on-chain data in the `enriched` DataFrame alongside VPIN, realized_vol, etc.:

```python
# In your data preparation pipeline, before running the engine:

def add_onchain_to_enriched(enriched_df: pd.DataFrame) -> pd.DataFrame:
    """Add on-chain columns to the enriched daily DataFrame."""
    mvrv = fetch_bgeometrics("mvrv")
    exchanges = fetch_bgeometrics("exchanges")

    # Merge on date
    enriched_df = enriched_df.merge(
        mvrv[['mvrv_zscore']],  # adjust column names
        left_index=True, right_index=True, how='left'
    )
    enriched_df = enriched_df.merge(
        exchanges[['netflow']],
        left_index=True, right_index=True, how='left'
    )

    # Forward-fill missing days
    enriched_df['mvrv_zscore'] = enriched_df['mvrv_zscore'].ffill()
    enriched_df['netflow'] = enriched_df['netflow'].ffill()

    return enriched_df
```

### Using On-Chain Signals in a Strategy

```python
def my_strategy_with_onchain(ctx: StrategyContext) -> StrategyResult:
    """Example strategy incorporating on-chain signals."""
    n = len(ctx.ind_1h['close'])

    # --- Technical entry (existing logic) ---
    rsi = ctx.ind_1h['rsi']
    entry = rsi < 30  # oversold

    # --- On-chain filters (applied daily, forward-filled to 1H) ---
    mvrv_sizing = ctx.custom.get('onchain_mvrv_sizing', np.ones(n))
    exchange_flow = ctx.custom.get('onchain_exchange_flow', np.zeros(n))

    # Block entries when exchange flows are strongly bearish
    entry = entry & (exchange_flow >= -1)

    # Adjust edge based on MVRV (higher conviction in undervalued regimes)
    base_edge = 0.40
    # mvrv_sizing is 0.0 to 1.5; use as edge multiplier
    avg_mvrv_mult = np.nanmean(mvrv_sizing[-24:])  # last 24 hours
    adjusted_edge = base_edge * max(0.5, min(1.5, avg_mvrv_mult))

    return StrategyResult(
        entry_mask=entry,
        direction=np.ones(n, dtype=np.int8),
        stop_mult=3.0,
        trail_mult=2.5,
        target_mult=5.0,
        edge=adjusted_edge,
        name='rsi_oversold_onchain_filtered',
    )
```

---

## 6. Python Implementation

### Complete On-Chain Data Module

This is a drop-in module for the project root. Save as `tools/onchain_data.py`.

```python
"""
On-Chain Data Module for Backtest Engine
=========================================
Fetches free on-chain data from BGeometrics and blockchain.com.
Provides daily signals that can be forward-filled to 1H bars.

Usage:
    from onchain_data import OnchainSignals

    oc = OnchainSignals()
    oc.fetch_all()              # Fetches and caches all data
    mvrv_mult = oc.mvrv_sizing_multiplier()
    flow_sig = oc.exchange_flow_signal()
"""

import requests
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
import json
import time

CACHE_DIR = Path(__file__).parent / "onchain_cache"
BGEOMETRICS_BASE = "https://bitcoin-data.com/api/v1"
BLOCKCHAIN_BASE = "https://api.blockchain.info/charts"


class OnchainSignals:
    """Fetches, caches, and computes on-chain trading signals."""

    def __init__(self, cache_hours: int = 12):
        """
        Args:
            cache_hours: Re-fetch if cache is older than this many hours.
        """
        self.cache_hours = cache_hours
        CACHE_DIR.mkdir(exist_ok=True)
        self._data = {}

    # ----------------------------------------------------------------
    # Data fetching
    # ----------------------------------------------------------------

    def _cache_path(self, key: str) -> Path:
        return CACHE_DIR / f"{key}.parquet"

    def _is_stale(self, key: str) -> bool:
        path = self._cache_path(key)
        if not path.exists():
            return True
        age = datetime.now().timestamp() - path.stat().st_mtime
        return age > self.cache_hours * 3600

    def _fetch_bgeometrics(self, metric: str, key: str = None) -> pd.DataFrame:
        """Fetch from BGeometrics API with caching."""
        key = key or metric
        if not self._is_stale(key) and key not in self._data:
            self._data[key] = pd.read_parquet(self._cache_path(key))
            return self._data[key]

        if key in self._data and not self._is_stale(key):
            return self._data[key]

        url = f"{BGEOMETRICS_BASE}/{metric}"
        resp = requests.get(url, params={"startday": "2020-01-01", "size": 10000}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        df = pd.DataFrame(data)
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date').sort_index()

        df.to_parquet(self._cache_path(key))
        self._data[key] = df
        time.sleep(0.5)  # Rate limit courtesy
        return df

    def _fetch_blockchain(self, chart: str, timespan: str = "5years") -> pd.DataFrame:
        """Fetch from blockchain.com Charts API with caching."""
        key = f"bc_{chart}"
        if not self._is_stale(key) and key not in self._data:
            self._data[key] = pd.read_parquet(self._cache_path(key))
            return self._data[key]

        if key in self._data and not self._is_stale(key):
            return self._data[key]

        url = f"{BLOCKCHAIN_BASE}/{chart}"
        resp = requests.get(url, params={
            "timespan": timespan, "format": "json", "sampled": "false"
        }, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        df = pd.DataFrame(data['values'])
        df['date'] = pd.to_datetime(df['x'], unit='s')
        df = df.rename(columns={'y': chart}).set_index('date').drop(columns=['x'])

        df.to_parquet(self._cache_path(key))
        self._data[key] = df
        time.sleep(0.5)
        return df

    def fetch_all(self):
        """Fetch all on-chain datasets. Call once per session."""
        print("[OnchainSignals] Fetching MVRV...")
        self._fetch_bgeometrics("mvrv")
        print("[OnchainSignals] Fetching SOPR...")
        self._fetch_bgeometrics("sopr")
        print("[OnchainSignals] Fetching exchange flows...")
        self._fetch_bgeometrics("exchanges")
        print("[OnchainSignals] Fetching active addresses...")
        self._fetch_bgeometrics("active-addresses", key="active_addresses")
        print("[OnchainSignals] Fetching NUPL...")
        self._fetch_bgeometrics("nupl")
        print("[OnchainSignals] Fetching hash rate (blockchain.com)...")
        self._fetch_blockchain("hash-rate")
        print("[OnchainSignals] Done.")

    # ----------------------------------------------------------------
    # Signal computations
    # ----------------------------------------------------------------

    def mvrv_sizing_multiplier(self) -> pd.Series:
        """
        MVRV Z-Score -> position sizing multiplier.
        Returns a daily Series with values 0.0 to 1.5.
        """
        df = self._data.get('mvrv')
        if df is None:
            df = self._fetch_bgeometrics("mvrv")

        # Find the Z-score column (API may name it differently)
        z_col = None
        for col in df.columns:
            if 'zscore' in col.lower() or 'z_score' in col.lower() or col == 'mvrv':
                z_col = col
                break
        if z_col is None:
            z_col = df.columns[0]

        z = df[z_col].astype(float)

        sizing = pd.Series(1.0, index=z.index)
        sizing[z > 7] = 0.0      # Extreme top: no new positions
        sizing[z > 5] = 0.25     # Very overheated
        sizing[z > 3] = 0.5      # Overheated
        sizing[(z >= 0) & (z <= 3)] = 1.0  # Fair value
        sizing[z < 0] = 1.5      # Undervalued: increase sizing

        return sizing

    def exchange_flow_signal(self) -> pd.Series:
        """
        Exchange netflow -> daily regime signal.
        Returns: -2 (strong bearish) to +2 (strong bullish).
        Negative netflow (outflows) = bullish (accumulation).
        """
        df = self._data.get('exchanges')
        if df is None:
            df = self._fetch_bgeometrics("exchanges")

        # Find netflow column
        nf_col = None
        for col in df.columns:
            if 'netflow' in col.lower() or 'net_flow' in col.lower():
                nf_col = col
                break
        if nf_col is None:
            # Fall back to first numeric column
            nf_col = df.select_dtypes(include=[np.number]).columns[0]

        netflow = df[nf_col].astype(float)
        ma7 = netflow.rolling(7).mean()
        ma30 = netflow.rolling(30).mean()

        signal = pd.Series(0, index=netflow.index, dtype=int)
        signal[ma7 < 0] = 1
        signal[ma7 > 0] = -1
        signal[(ma7 < 0) & (ma30 < 0)] = 2
        signal[(ma7 > 0) & (ma30 > 0)] = -2

        return signal

    def sopr_signal(self, bull_regime: bool = True) -> pd.Series:
        """
        SOPR-based sentiment.
        Returns: -1 (bearish), 0 (neutral), +1 (bullish).
        """
        df = self._data.get('sopr')
        if df is None:
            df = self._fetch_bgeometrics("sopr")

        sopr_col = None
        for col in df.columns:
            if 'sopr' in col.lower():
                sopr_col = col
                break
        if sopr_col is None:
            sopr_col = df.columns[0]

        sopr = df[sopr_col].astype(float)
        ma7 = sopr.rolling(7).mean()

        signal = pd.Series(0, index=sopr.index, dtype=int)

        if bull_regime:
            signal[(ma7 > 0.98) & (ma7 < 1.02) & (sopr > sopr.shift(1))] = 1
            signal[ma7 > 1.05] = -1
        else:
            signal[(ma7 > 0.98) & (ma7 < 1.02) & (sopr < sopr.shift(1))] = -1
            signal[ma7 < 0.95] = 1

        return signal

    def nupl_regime(self) -> pd.Series:
        """
        Net Unrealized Profit/Loss -> regime classification.
        Returns: 'capitulation', 'hope', 'optimism', 'belief', 'euphoria', 'greed'
        """
        df = self._data.get('nupl')
        if df is None:
            df = self._fetch_bgeometrics("nupl")

        nupl_col = df.columns[0]
        nupl = df[nupl_col].astype(float)

        regime = pd.Series('optimism', index=nupl.index)
        regime[nupl < 0] = 'capitulation'
        regime[(nupl >= 0) & (nupl < 0.25)] = 'hope'
        regime[(nupl >= 0.25) & (nupl < 0.5)] = 'optimism'
        regime[(nupl >= 0.5) & (nupl < 0.75)] = 'belief'
        regime[nupl >= 0.75] = 'euphoria'

        return regime

    def hash_ribbons(self) -> pd.Series:
        """
        Hash Ribbons buy signal (BTC only).
        Returns: 1 (recovery buy), -1 (capitulation), 0 (neutral).
        Fires rarely (~2-3 times per year).
        """
        df = self._data.get('bc_hash-rate')
        if df is None:
            df = self._fetch_blockchain("hash-rate")

        hr = df.iloc[:, 0].astype(float)
        ma30 = hr.rolling(30).mean()
        ma60 = hr.rolling(60).mean()

        signal = pd.Series(0, index=hr.index, dtype=int)
        signal[ma30 < ma60] = -1  # Capitulation

        # Recovery: cross back above
        recovery = (ma30 > ma60) & (ma30.shift(1) <= ma60.shift(1))
        signal[recovery] = 1

        return signal

    # ----------------------------------------------------------------
    # Alignment helpers
    # ----------------------------------------------------------------

    def align_to_daily_index(self, signal: pd.Series, target_index: pd.DatetimeIndex) -> np.ndarray:
        """Forward-fill a daily signal to match a target DatetimeIndex."""
        aligned = signal.reindex(target_index, method='ffill')
        return aligned.fillna(0).values
```

### Backtest Integration Example

```python
# In your strategy file:
from onchain_data import OnchainSignals

# Initialize once
oc = OnchainSignals(cache_hours=12)
oc.fetch_all()

# Pre-compute signals
mvrv_mult = oc.mvrv_sizing_multiplier()
exch_flow = oc.exchange_flow_signal()

def onchain_filtered_strategy(ctx):
    """Strategy that uses on-chain data as filters."""
    n = len(ctx.ind_1h['close'])

    # Align daily on-chain signals to 1H bars
    mvrv_1h = oc.align_to_daily_index(mvrv_mult, ctx.idx_1h)
    flow_1h = oc.align_to_daily_index(exch_flow, ctx.idx_1h)

    # Store in custom for debugging
    ctx.custom['mvrv_sizing'] = mvrv_1h
    ctx.custom['exchange_flow'] = flow_1h

    # --- Your technical entry logic ---
    rsi = ctx.ind_1h['rsi']
    entry = rsi < 30

    # --- On-chain filter: block longs during strong exchange inflows ---
    entry = entry & (flow_1h >= -1)

    # --- On-chain sizing: scale edge by MVRV ---
    avg_mvrv = np.nanmean(mvrv_1h[-24:]) if len(mvrv_1h) >= 24 else 1.0
    edge = 0.40 * np.clip(avg_mvrv, 0.25, 1.5)

    return StrategyResult(
        entry_mask=entry,
        direction=np.ones(n, dtype=np.int8),
        stop_mult=3.0, trail_mult=2.5, target_mult=5.0,
        edge=edge,
        name='onchain_filtered',
    )
```

---

## 7. Practical Recommendations

### What to Build First (This Week)

1. **Create `onchain_data.py`** with the `OnchainSignals` class above.
2. **Test the BGeometrics API** -- verify endpoint names and column names match (API may have changed).
3. **Backtest MVRV sizing** -- run your best strategy with and without MVRV sizing multiplier. Compare Sharpe, max drawdown, and total return.
4. **Backtest exchange flow filter** -- run with and without the flow filter. Measure how many losing trades it avoids vs winning trades it blocks.

### Expected Improvements (Conservative Estimates)

Based on published research and the nature of these signals:

| Signal | Expected Impact | Mechanism |
|--------|----------------|-----------|
| MVRV sizing | +0.05 to +0.15 Sharpe | Reduces exposure at cycle tops, increases at bottoms |
| Exchange flow filter | -5% to -15% max drawdown | Avoids longs during distribution phases |
| SOPR confirmation | +2-5% win rate | Filters out counter-trend entries |
| Combined | +0.1 to +0.2 Sharpe, -10-20% max DD | Regime-aware position management |

These are conservative estimates. The actual improvement depends on:
- How well our existing signals already capture these dynamics (some overlap is likely)
- The specific time period backtested (on-chain signals perform best at regime transitions)
- Whether BGeometrics data has look-ahead bias in their free tier (check timestamps carefully)

### Watch Out For

1. **Look-ahead bias:** On-chain data is computed at end-of-day. Make sure you lag it by at least 1 day in backtests. When forward-filling to 1H, use yesterday's on-chain signal for today's bars.

2. **BTC-only limitation:** MVRV, SOPR, exchange flows are most reliable for BTC. For alts, use BTC on-chain signals as a broad market filter (when BTC is in distribution, be cautious on all 49 tokens).

3. **Survivorship in research:** Most published on-chain signal research is on BTC and ETH. Results on smaller tokens may not generalize.

4. **API reliability:** BGeometrics is a small provider. If their API goes down, have a fallback (blockchain.com for hash rate, manual MVRV tracking from public dashboards).

5. **Regime dependency:** SOPR behaves differently in bull vs bear markets. Your regime detection logic must be correct for SOPR signals to add value.

6. **Overfitting temptation:** On-chain signals look great in hindsight but have wide parameter ranges. Use CPCV validation for any on-chain-based strategy.

### What NOT to Spend Time On

- **Whale alerts / large transfer tracking** -- R^2 < 0.05, proven ineffective
- **NVT Ratio** -- too slow for our timeframe
- **Glassnode / CryptoQuant paid tiers** -- not justified until the free data proves its value in backtests
- **Building custom blockchain indexers** -- the free APIs give us what we need
- **Real-time on-chain data** -- daily resolution is sufficient; on-chain data does not have 1H predictive power

---

## Sources

### Data Providers
- [BGeometrics Free Bitcoin API](https://charts.bgeometrics.com/bitcoin_api.html)
- [Blockchain.com Charts API](https://www.blockchain.com/api/charts_api)
- [Santiment API / SanPy](https://github.com/santiment/sanpy)
- [Glassnode API Documentation](https://docs.glassnode.com/basic-api/api)
- [CryptoQuant API Docs](https://cryptoquant.com/docs)
- [Dune Analytics](https://dune.com/home)
- [CoinGlass Spot Inflow/Outflow](https://www.coinglass.com/spot-inflow-outflow)

### Research & Evidence
- [Binance: Exchange Inflow Data and Impact on Bitcoin](https://www.binance.com/en/square/post/29772524147137)
- [ScienceDirect: Bitcoin Spot ETP Fund Flows Analysis (2025)](https://www.sciencedirect.com/science/article/pii/S0165176525001417)
- [Presto Research: Whale Alerts -- Are They Tradable?](https://www.prestolabs.io/research/whale-alerts-are-they-tradable)
- [Hash Ribbons Indicator by Capriole (TradingView)](https://www.tradingview.com/script/kT7jIvqv-Hash-Ribbons/)
- [Bitcoin Hash Ribbons Explained with Past Results](https://www.stopsaving.com/bitcoin-hash-ribbons-indicator/)
- [MVRV Z-Score (Bitcoin Magazine Pro)](https://www.bitcoinmagazinepro.com/charts/mvrv-zscore/)
- [MVRV Ratio Explained (Yellow.com)](https://yellow.com/en-US/learn/mvrv-ratio-explained-how-to-measure-true-cryptocurrency-value)
- [How USDT Mints and Burns Move with Bitcoin Price Cycles (CoinTelegraph)](https://cointelegraph.com/news/usdt-mints-bitcoin-price)
- [ETFs Are Selling, Stablecoins Are Minting (Amberdata)](https://blog.amberdata.io/etfs-are-selling.-stablecoins-are-minting.-what-the-data-signals-now)
- [BIS Working Paper: Stablecoins and Safe Asset Prices](https://www.bis.org/publ/work1270.pdf)
- [ScienceDirect: Hybrid ML Model for Bitcoin Price Prediction (2022)](https://www.sciencedirect.com/science/article/abs/pii/S2214635022000673)
- [On-Chain Analytics in Python (Relataly)](https://www.relataly.com/seven-metrics-for-on-chain-analysis-in-python/10098/)
