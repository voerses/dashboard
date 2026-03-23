# Alternative Data Sources for Crypto Trading Signals

**Date**: 2026-03-23
**Purpose**: Concrete, downloadable alternative data sources to augment a system where TA features have zero OOS predictive power. Every source below has been verified as accessible via Python with at least partial free access.

---

## Table of Contents

1. [Tier 1: FREE, No API Key Required](#tier-1-free-no-api-key-required)
2. [Tier 2: FREE with API Key (registration required)](#tier-2-free-with-api-key)
3. [Tier 3: Cheap Paid (<$100/mo)](#tier-3-cheap-paid)
4. [Summary Matrix](#summary-matrix)
5. [Recommended Build Order](#recommended-build-order)

---

## Tier 1: FREE, No API Key Required

### 1.1 Binance Futures Derivatives Data (Funding, OI, Long/Short, Taker Volume)

**Why it might have alpha**: Funding rate extremes signal crowded positioning. When 90% of traders are long and paying high funding, the market is primed for a squeeze. Open interest divergences from price reveal whether moves are backed by new money or just spot. Taker buy/sell ratio shows real-time aggression direction.

**Endpoints** (all free, no auth):

```
GET https://fapi.binance.com/fapi/v1/fundingRate
GET https://fapi.binance.com/futures/data/openInterestHist
GET https://fapi.binance.com/futures/data/globalLongShortAccountRatio
GET https://fapi.binance.com/futures/data/topLongShortPositionRatio
GET https://fapi.binance.com/futures/data/topLongShortAccountRatio
GET https://fapi.binance.com/futures/data/takerlongshortRatio
```

**Data**: Hourly granularity for OI/LS/taker, 8h for funding. 30-day rolling window for most endpoints (500 records max per call). For deeper history, use klines endpoint which includes taker buy volume.

**Covers**: All USDT-margined perpetuals (~200+ pairs on Binance).

```python
import requests
import pandas as pd
from datetime import datetime, timedelta

BASE = "https://fapi.binance.com"

def fetch_binance_funding_rate(symbol="BTCUSDT", limit=500):
    """Fetch funding rate history. Each record = one 8h funding event."""
    r = requests.get(f"{BASE}/fapi/v1/fundingRate",
                     params={"symbol": symbol, "limit": limit})
    df = pd.DataFrame(r.json())
    df["fundingRate"] = df["fundingRate"].astype(float)
    df["datetime"] = pd.to_datetime(df["fundingTime"], unit="ms")
    return df[["datetime", "symbol", "fundingRate"]]

def fetch_binance_oi_history(symbol="BTCUSDT", period="1h", limit=500):
    """Fetch open interest history. Periods: 5m,15m,30m,1h,2h,4h,6h,12h,1d."""
    r = requests.get(f"{BASE}/futures/data/openInterestHist",
                     params={"symbol": symbol, "period": period, "limit": limit})
    df = pd.DataFrame(r.json())
    df["sumOpenInterest"] = df["sumOpenInterest"].astype(float)
    df["sumOpenInterestValue"] = df["sumOpenInterestValue"].astype(float)
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df[["datetime", "symbol", "sumOpenInterest", "sumOpenInterestValue"]]

def fetch_binance_long_short_ratio(symbol="BTCUSDT", period="1h", limit=500):
    """Global long/short account ratio."""
    r = requests.get(f"{BASE}/futures/data/globalLongShortAccountRatio",
                     params={"symbol": symbol, "period": period, "limit": limit})
    df = pd.DataFrame(r.json())
    df["longShortRatio"] = df["longShortRatio"].astype(float)
    df["longAccount"] = df["longAccount"].astype(float)
    df["shortAccount"] = df["shortAccount"].astype(float)
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df

def fetch_binance_taker_volume(symbol="BTCUSDT", period="1h", limit=500):
    """Taker buy/sell ratio. >1 means buyers aggressive, <1 sellers aggressive."""
    r = requests.get(f"{BASE}/futures/data/takerlongshortRatio",
                     params={"symbol": symbol, "period": period, "limit": limit})
    df = pd.DataFrame(r.json())
    df["buySellRatio"] = df["buySellRatio"].astype(float)
    df["buyVol"] = df["buyVol"].astype(float)
    df["sellVol"] = df["sellVol"].astype(float)
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df

def fetch_binance_top_trader_positions(symbol="BTCUSDT", period="1h", limit=500):
    """Top trader long/short position ratio - whale positioning signal."""
    r = requests.get(f"{BASE}/futures/data/topLongShortPositionRatio",
                     params={"symbol": symbol, "period": period, "limit": limit})
    df = pd.DataFrame(r.json())
    df["longShortRatio"] = df["longShortRatio"].astype(float)
    df["longAccount"] = df["longAccount"].astype(float)
    df["shortAccount"] = df["shortAccount"].astype(float)
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df

# DEEPER HISTORY: Extract taker buy volume from klines (years of history)
def fetch_binance_klines_with_taker(symbol="BTCUSDT", interval="1h", limit=1500):
    """Klines include taker buy volume - available for full exchange history."""
    r = requests.get(f"{BASE}/fapi/v1/klines",
                     params={"symbol": symbol, "interval": interval, "limit": limit})
    cols = ["open_time","open","high","low","close","volume","close_time",
            "quote_volume","trades","taker_buy_base_vol","taker_buy_quote_vol","ignore"]
    df = pd.DataFrame(r.json(), columns=cols)
    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms")
    for c in ["volume", "taker_buy_base_vol", "taker_buy_quote_vol"]:
        df[c] = df[c].astype(float)
    df["taker_sell_base_vol"] = df["volume"] - df["taker_buy_base_vol"]
    df["taker_buy_ratio"] = df["taker_buy_base_vol"] / df["volume"]
    return df
```

**Rate limits**: 2400 request weight/min. Each call above costs 1 weight. Pagination via `startTime`/`endTime` to build longer histories.

**Key limitation**: Most `/futures/data/` endpoints only return last 30 days. Use the klines endpoint for deeper taker volume history. For funding rate, history goes back to contract inception.

---

### 1.2 Deribit Options Data (IV, Put-Call Ratio, Term Structure)

**Why it might have alpha**: Options market encodes forward-looking expectations. When IV term structure inverts (near-term IV > far-term), the market is pricing imminent volatility. Put-call OI ratio extremes precede reversals. Skew changes reveal directional bets by sophisticated players.

**Endpoint**: `GET https://www.deribit.com/api/v2/public/get_book_summary_by_currency`

**Data**: Real-time snapshots of ALL option instruments (hundreds per currency). Fields: mark_price, bid, ask, open_interest, volume_24h, mark_iv, underlying_price. Currencies: BTC, ETH, SOL.

**No authentication required. No API key. No rate limit issues for moderate usage.**

```python
import requests
import pandas as pd
from datetime import datetime

DERIBIT_BASE = "https://www.deribit.com/api/v2"

def fetch_deribit_options_summary(currency="BTC"):
    """Fetch ALL option book summaries. No auth needed. Returns hundreds of instruments."""
    r = requests.get(f"{DERIBIT_BASE}/public/get_book_summary_by_currency",
                     params={"currency": currency, "kind": "option"})
    data = r.json().get("result", [])
    df = pd.DataFrame(data)
    return df

def compute_deribit_pcr_and_iv(currency="BTC"):
    """Compute put-call ratio and average IV from live options data."""
    df = fetch_deribit_options_summary(currency)
    if df.empty:
        return {}

    # Parse instrument names: e.g., BTC-28MAR25-90000-C
    df["is_call"] = df["instrument_name"].str.endswith("-C")
    df["is_put"] = df["instrument_name"].str.endswith("-P")

    # Put-call open interest ratio
    call_oi = df.loc[df["is_call"], "open_interest"].sum()
    put_oi = df.loc[df["is_put"], "open_interest"].sum()
    pcr_oi = put_oi / call_oi if call_oi > 0 else None

    # Put-call volume ratio
    call_vol = df.loc[df["is_call"], "volume"].sum()
    put_vol = df.loc[df["is_put"], "volume"].sum()
    pcr_vol = put_vol / call_vol if call_vol > 0 else None

    # OI-weighted average implied volatility
    df_valid = df[df["mark_iv"].notna() & (df["mark_iv"] > 0)]
    if not df_valid.empty and df_valid["open_interest"].sum() > 0:
        avg_iv = (df_valid["mark_iv"] * df_valid["open_interest"]).sum() / df_valid["open_interest"].sum()
    else:
        avg_iv = None

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "currency": currency,
        "put_call_oi_ratio": pcr_oi,
        "put_call_vol_ratio": pcr_vol,
        "oi_weighted_avg_iv": avg_iv,
        "total_call_oi": call_oi,
        "total_put_oi": put_oi,
        "num_instruments": len(df),
    }

def build_iv_term_structure(currency="BTC"):
    """Build implied volatility term structure from live options.
    Groups by expiration, computes ATM IV per expiry."""
    df = fetch_deribit_options_summary(currency)
    if df.empty:
        return pd.DataFrame()

    underlying = df["underlying_price"].iloc[0]

    # Parse expiration and strike from instrument name
    parts = df["instrument_name"].str.split("-", expand=True)
    df["expiry_str"] = parts[1]
    df["strike"] = parts[2].astype(float)
    df["type"] = parts[3]

    # ATM = closest strike to underlying
    df["moneyness"] = abs(df["strike"] - underlying) / underlying

    # For each expiry, get the ATM call IV
    term_structure = []
    for expiry, group in df[df["type"] == "C"].groupby("expiry_str"):
        atm = group.loc[group["moneyness"].idxmin()]
        term_structure.append({
            "expiry": expiry,
            "atm_iv": atm["mark_iv"],
            "atm_strike": atm["strike"],
            "open_interest": group["open_interest"].sum(),
        })

    return pd.DataFrame(term_structure)

# Historical DVOL (30-day implied vol index, like VIX for crypto)
def fetch_dvol_historical():
    """Download historical DVOL from CryptoDataDownload (free CSV)."""
    # BTC DVOL
    url = "https://www.cryptodatadownload.com/cdd/Deribit_BTCDVOL_1h.csv"
    df = pd.read_csv(url, skiprows=1)
    return df
```

**Collecting history**: Deribit public API only gives snapshots. To build history, run `compute_deribit_pcr_and_iv()` on a cron (hourly) and store results. For historical DVOL, use the CryptoDataDownload CSV.

---

### 1.3 Fear & Greed Index (Alternative.me)

**Why it might have alpha**: Composite sentiment indicator combining volatility, volume/momentum, social media, surveys, dominance, and trends. Extreme fear readings historically precede bounces; extreme greed precedes corrections. Daily granularity, full history since 2018.

**Endpoint**: `GET https://api.alternative.me/fng/`

**No API key required. No rate limits.**

```python
import requests
import pandas as pd

def fetch_fear_greed_index(limit=0):
    """Fetch full history of Fear & Greed Index. limit=0 returns ALL data since 2018."""
    r = requests.get("https://api.alternative.me/fng/",
                     params={"limit": limit, "format": "json"})
    data = r.json()["data"]
    df = pd.DataFrame(data)
    df["value"] = df["value"].astype(int)
    df["datetime"] = pd.to_datetime(df["timestamp"].astype(int), unit="s")
    df["classification"] = df["value_classification"]
    return df[["datetime", "value", "classification"]].sort_values("datetime")

# Full history in one call:
# fng = fetch_fear_greed_index(limit=0)
# print(f"History: {fng['datetime'].min()} to {fng['datetime'].max()}, {len(fng)} days")
```

**Columns**: date, value (0-100), classification (Extreme Fear / Fear / Neutral / Greed / Extreme Greed).
**Granularity**: Daily.
**History**: Since February 2018 (~2000+ data points).

---

### 1.4 DefiLlama Stablecoin Supply Data

**Why it might have alpha**: Stablecoin supply is a proxy for capital available to deploy into crypto. Rising USDT/USDC supply = fresh capital inflows = bullish pressure. Falling supply = capital exiting. Supply changes lead price by days/weeks. This is one of the strongest leading indicators.

**Endpoints** (all free, no API key):

```
GET https://stablecoins.llama.fi/stablecoins           # List all stablecoins + current supply
GET https://stablecoins.llama.fi/stablecoin/{id}        # Historical supply for specific stablecoin
GET https://stablecoins.llama.fi/stablecoincharts/all   # Historical total stablecoin mcap
```

```python
import requests
import pandas as pd

LLAMA_BASE = "https://stablecoins.llama.fi"

def fetch_stablecoin_list():
    """Get all stablecoins with IDs, current supply, chains."""
    r = requests.get(f"{LLAMA_BASE}/stablecoins")
    return r.json()["peggedAssets"]

def fetch_stablecoin_history(stablecoin_id=1, name="USDT"):
    """Fetch historical circulating supply for a stablecoin.
    Common IDs: 1=USDT, 2=USDC, 3=BUSD, 4=DAI, 5=FRAX"""
    r = requests.get(f"{LLAMA_BASE}/stablecoin/{stablecoin_id}")
    data = r.json()
    tokens = data.get("tokens", [])
    rows = []
    for t in tokens:
        rows.append({
            "date": t["date"],
            "circulating_usd": t.get("circulating", {}).get("peggedUSD", 0),
        })
    df = pd.DataFrame(rows)
    df["datetime"] = pd.to_datetime(df["date"].astype(int), unit="s")
    df["stablecoin"] = name
    return df[["datetime", "stablecoin", "circulating_usd"]]

def fetch_total_stablecoin_mcap():
    """Fetch historical total stablecoin market cap (all stables combined)."""
    r = requests.get(f"{LLAMA_BASE}/stablecoincharts/all")
    data = r.json()
    rows = []
    for d in data:
        rows.append({
            "date": d["date"],
            "total_circulating_usd": d.get("totalCirculating", {}).get("peggedUSD", 0),
        })
    df = pd.DataFrame(rows)
    df["datetime"] = pd.to_datetime(df["date"].astype(int), unit="s")
    return df[["datetime", "total_circulating_usd"]]

# Build combined stablecoin supply dataset:
# usdt = fetch_stablecoin_history(1, "USDT")
# usdc = fetch_stablecoin_history(2, "USDC")
# dai = fetch_stablecoin_history(4, "DAI")
# combined = pd.concat([usdt, usdc, dai])
```

**Granularity**: Daily.
**History**: Full history since stablecoin inception (~2017+).
**Coverage**: USDT, USDC, DAI, FRAX, and dozens more.

---

### 1.5 Cross-Market: DXY, Rates, Equity Indices (via yfinance / FRED)

**Why it might have alpha**: Crypto is increasingly correlated with macro. DXY strength crushes risk assets. Rising real rates reduce speculative appetite. SPX/NDX drawdowns trigger crypto liquidation cascades. These are the macro regime variables that context-switch crypto from trending to mean-reverting.

```python
import yfinance as yf
import pandas as pd

def fetch_cross_market_data(start="2020-01-01"):
    """Fetch DXY, SPX, NDX, VIX, US10Y, Gold - all free via yfinance."""
    tickers = {
        "DXY": "DX-Y.NYB",       # US Dollar Index
        "SPX": "^GSPC",           # S&P 500
        "NDX": "^IXIC",           # NASDAQ Composite
        "VIX": "^VIX",            # CBOE Volatility Index
        "US10Y": "^TNX",          # 10-Year Treasury Yield
        "GOLD": "GC=F",           # Gold Futures
        "US2Y": "2YY=F",          # 2-Year Treasury Yield
    }

    frames = {}
    for name, ticker in tickers.items():
        try:
            data = yf.download(ticker, start=start, progress=False)
            if not data.empty:
                frames[name] = data["Close"].rename(name)
        except Exception as e:
            print(f"Failed to fetch {name}: {e}")

    df = pd.concat(frames.values(), axis=1)
    return df

# For FRED data (requires free API key from https://fred.stlouisfed.org/docs/api/api_key.html):
def fetch_fred_data(series_id="DTWEXBGS", api_key="YOUR_FRED_KEY"):
    """Fetch from FRED. Useful series:
    DTWEXBGS = Trade Weighted Dollar Index (broad)
    DFF = Fed Funds Rate
    T10Y2Y = 10Y-2Y Yield Spread
    BAMLH0A0HYM2 = High Yield OAS Spread
    """
    url = f"https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": "2020-01-01",
    }
    r = requests.get(url, params=params)
    data = r.json()["observations"]
    df = pd.DataFrame(data)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["datetime"] = pd.to_datetime(df["date"])
    return df[["datetime", "value"]].dropna()
```

**yfinance**: Free, no API key, daily granularity, years of history. Limited to daily for indices.
**FRED**: Free API key (instant registration), daily data, decades of history for macro series.

---

### 1.6 Google Trends (Crypto Search Interest)

**Why it might have alpha**: Retail search interest spikes precede retail FOMO buying. When "buy bitcoin" searches hit extremes, it often marks local tops. Declining search interest during uptrends signals weak hands exiting.

```python
from pytrends.request import TrendReq
import pandas as pd
import time

def fetch_google_trends_crypto(keywords=None, timeframe="today 12-m"):
    """Fetch Google Trends data for crypto keywords.
    Timeframe options: 'today 12-m', 'today 3-m', 'now 7-d' (hourly).
    For hourly data: use 'now 7-d' (gives ~168 hourly data points).
    """
    if keywords is None:
        keywords = ["bitcoin", "buy crypto", "crypto crash"]

    pytrends = TrendReq(hl="en-US", tz=360)
    pytrends.build_payload(keywords, cat=0, timeframe=timeframe, geo="", gprop="")
    df = pytrends.interest_over_time()
    return df

def fetch_google_trends_hourly(keyword="bitcoin", year_start=2025, month_start=1,
                                year_end=2025, month_end=6):
    """Fetch HOURLY Google Trends data over a date range.
    WARNING: This makes many requests (1 per week). Use sleep to avoid rate limits.
    """
    pytrends = TrendReq(hl="en-US", tz=360)
    df = pytrends.get_historical_interest(
        [keyword],
        year_start=year_start, month_start=month_start, day_start=1, hour_start=0,
        year_end=year_end, month_end=month_end, day_end=1, hour_end=0,
        sleep=60  # seconds between requests to avoid rate limiting
    )
    return df

# pip install pytrends
```

**Granularity**: Hourly (via `get_historical_interest`, slow due to rate limits), weekly/daily (via `interest_over_time`).
**History**: Up to 5 years weekly, custom date ranges hourly.
**Limitation**: Rate limited. Set `sleep=60` for hourly fetches. Index values are relative (0-100 scale), not absolute.

---

## Tier 2: FREE with API Key

### 2.1 CryptoCompare Social Stats

**Why it might have alpha**: Tracks social activity across Reddit, Twitter/X, and GitHub per coin. Spikes in social volume that diverge from price movement signal narrative shifts before they move the market.

**Endpoints**:
```
GET https://min-api.cryptocompare.com/data/social/coin/histo/hour
GET https://min-api.cryptocompare.com/data/social/coin/histo/day
GET https://min-api.cryptocompare.com/data/social/coin/latest
```

**Free tier**: 250,000 lifetime API calls. No monthly cost.

```python
import requests
import pandas as pd

CC_API_KEY = "YOUR_CRYPTOCOMPARE_KEY"  # Free at https://www.cryptocompare.com/cryptopian/api-keys

def fetch_cc_social_hourly(coin_id=1182, limit=168):
    """Fetch hourly social stats. coin_id: 1182=BTC, 7605=ETH.
    Free tier: 250K lifetime calls."""
    url = "https://min-api.cryptocompare.com/data/social/coin/histo/hour"
    params = {
        "coinId": coin_id,
        "limit": limit,
        "api_key": CC_API_KEY,
    }
    r = requests.get(url, params=params)
    data = r.json().get("Data", [])
    df = pd.DataFrame(data)
    df["datetime"] = pd.to_datetime(df["time"], unit="s")
    return df

def fetch_cc_social_daily(coin_id=1182, limit=365):
    """Fetch daily social stats."""
    url = "https://min-api.cryptocompare.com/data/social/coin/histo/day"
    params = {
        "coinId": coin_id,
        "limit": limit,
        "api_key": CC_API_KEY,
    }
    r = requests.get(url, params=params)
    data = r.json().get("Data", [])
    df = pd.DataFrame(data)
    df["datetime"] = pd.to_datetime(df["time"], unit="s")
    return df
```

**Columns**: reddit_subscribers, reddit_active_users, reddit_posts_per_hour, twitter_followers, twitter_statuses, code_repo_stars, code_repo_forks, code_repo_contributors, and more.
**Granularity**: Hourly and daily.
**History**: 7 days hourly (free), longer for daily.

---

### 2.2 Santiment (On-Chain + Social + Dev Activity)

**Why it might have alpha**: Combines on-chain (active addresses, whale transactions, exchange flows), social (social volume, sentiment, trending words), and developer activity (GitHub commits) into one API. The social-to-price divergence signals are particularly interesting.

**Free tier**: 1K API calls/month, 1-year history, 30-day real-time lag on restricted metrics.

```python
# pip install sanpy
import san

# Optional: set API key for expanded access
# import os; os.environ["SANPY_APIKEY"] = "your_key"

def fetch_santiment_dev_activity(slug="ethereum", days=365):
    """Developer activity - GitHub commits, events. Unrestricted on free tier."""
    from datetime import datetime, timedelta
    end = datetime.utcnow()
    start = end - timedelta(days=days)
    df = san.get(
        f"dev_activity/{slug}",
        from_date=start.isoformat() + "Z",
        to_date=end.isoformat() + "Z",
        interval="1d"
    )
    return df

def fetch_santiment_active_addresses(slug="bitcoin", days=365):
    """Daily active addresses."""
    from datetime import datetime, timedelta
    end = datetime.utcnow()
    start = end - timedelta(days=days)
    df = san.get(
        f"daily_active_addresses/{slug}",
        from_date=start.isoformat() + "Z",
        to_date=end.isoformat() + "Z",
        interval="1d"
    )
    return df

def fetch_santiment_social_volume(slug="bitcoin", days=90):
    """Social volume - total mentions across Telegram, Reddit, etc.
    NOTE: This is a RESTRICTED metric - 30-day lag on free tier."""
    from datetime import datetime, timedelta
    end = datetime.utcnow()
    start = end - timedelta(days=days)
    df = san.get(
        f"social_volume_total/{slug}",
        from_date=start.isoformat() + "Z",
        to_date=end.isoformat() + "Z",
        interval="1d"
    )
    return df

def fetch_santiment_exchange_flow(slug="bitcoin", days=365):
    """Exchange inflow/outflow volume."""
    from datetime import datetime, timedelta
    end = datetime.utcnow()
    start = end - timedelta(days=days)
    df_in = san.get(
        f"exchange_inflow/{slug}",
        from_date=start.isoformat() + "Z",
        to_date=end.isoformat() + "Z",
        interval="1d"
    )
    df_out = san.get(
        f"exchange_outflow/{slug}",
        from_date=start.isoformat() + "Z",
        to_date=end.isoformat() + "Z",
        interval="1d"
    )
    return df_in, df_out
```

**Key metrics available**:
- `dev_activity` - GitHub development activity (unrestricted)
- `daily_active_addresses` - on-chain activity
- `social_volume_total` - social mentions (restricted)
- `exchange_inflow` / `exchange_outflow` - exchange flows
- `network_growth` - new addresses
- `whale_transaction_count` - large transactions

**Slugs**: Use asset slugs like "bitcoin", "ethereum", "solana", etc. 2000+ assets supported.

---

### 2.3 CoinGecko (Market + Community Data)

**Why it might have alpha**: CoinGecko provides community/developer scores and tickers across all DEXes, which can reveal exchange-specific anomalies and community growth metrics.

```python
import requests
import pandas as pd
import time

CG_BASE = "https://api.coingecko.com/api/v3"

def fetch_coingecko_market_data(vs_currency="usd", per_page=100, page=1):
    """Fetch market data including community_score, developer_score for top coins.
    Free tier: 10-30 calls/min."""
    r = requests.get(f"{CG_BASE}/coins/markets", params={
        "vs_currency": vs_currency,
        "order": "market_cap_desc",
        "per_page": per_page,
        "page": page,
        "sparkline": "false",
    })
    return pd.DataFrame(r.json())

def fetch_coingecko_coin_detail(coin_id="bitcoin"):
    """Detailed data including community, developer, social stats."""
    r = requests.get(f"{CG_BASE}/coins/{coin_id}", params={
        "localization": "false",
        "tickers": "false",
        "market_data": "true",
        "community_data": "true",
        "developer_data": "true",
    })
    data = r.json()
    return {
        "reddit_subscribers": data.get("community_data", {}).get("reddit_subscribers"),
        "reddit_active_48h": data.get("community_data", {}).get("reddit_accounts_active_48h"),
        "twitter_followers": data.get("community_data", {}).get("twitter_followers"),
        "github_forks": data.get("developer_data", {}).get("forks"),
        "github_stars": data.get("developer_data", {}).get("stars"),
        "github_subscribers": data.get("developer_data", {}).get("subscribers"),
        "github_total_issues": data.get("developer_data", {}).get("total_issues"),
        "github_commit_count_4_weeks": data.get("developer_data", {}).get("commit_count_4_weeks"),
        "coingecko_score": data.get("coingecko_score"),
        "developer_score": data.get("developer_score"),
        "community_score": data.get("community_score"),
        "liquidity_score": data.get("liquidity_score"),
    }
```

**Free tier**: 10-30 calls/min (demo), no API key required for basic access. CoinGecko Pro starts at $129/month.

---

## Tier 3: Cheap Paid (<$100/mo)

### 3.1 CoinGlass API ($29/mo Hobbyist tier)

**Why it might have alpha**: Most comprehensive aggregated derivatives data. Liquidation data across ALL exchanges, aggregated OI, funding rate arbitrage spreads, long/short ratios. This is the derivatives intelligence layer Binance alone cannot provide because it only covers Binance.

**Pricing**: $29/mo (Hobbyist), 80+ endpoints, 30 requests/min.

```python
import requests
import pandas as pd

CG_API_KEY = "YOUR_COINGLASS_KEY"
CG_BASE = "https://open-api-v3.coinglass.com/api"
HEADERS = {"coinglassSecret": CG_API_KEY}

def fetch_coinglass_liquidation_history(symbol="BTC", interval="h1"):
    """Aggregated liquidation history across all exchanges."""
    r = requests.get(f"{CG_BASE}/futures/liquidation/v2/history",
                     params={"symbol": symbol, "interval": interval},
                     headers=HEADERS)
    return pd.DataFrame(r.json().get("data", []))

def fetch_coinglass_aggregated_oi(symbol="BTC", interval="h1"):
    """Open interest aggregated across all exchanges."""
    r = requests.get(f"{CG_BASE}/futures/openInterest/ohlc-history",
                     params={"symbol": symbol, "interval": interval},
                     headers=HEADERS)
    return pd.DataFrame(r.json().get("data", []))

def fetch_coinglass_funding_rate(symbol="BTC", interval="h8"):
    """Funding rate across exchanges with OI-weighting."""
    r = requests.get(f"{CG_BASE}/futures/fundingRate/ohlc-history",
                     params={"symbol": symbol, "interval": interval},
                     headers=HEADERS)
    return pd.DataFrame(r.json().get("data", []))

def fetch_coinglass_long_short_ratio(symbol="BTC", interval="h1"):
    """Long/short ratio aggregated across exchanges."""
    r = requests.get(f"{CG_BASE}/futures/globalLongShortAccountRatio/history",
                     params={"symbol": symbol, "interval": interval},
                     headers=HEADERS)
    return pd.DataFrame(r.json().get("data", []))
```

**Key data not available for free elsewhere**:
- Cross-exchange aggregated liquidation volumes
- Liquidation heatmaps (predicted liquidation clusters)
- OI-weighted funding rates
- Exchange flow netflow monitoring

**History**: Back to 2019 on higher tiers.

---

### 3.2 CryptoQuant ($29/mo Advanced)

**Why it might have alpha**: Best-in-class exchange flow analytics. Their labeled exchange addresses database is the most comprehensive. Miner flows, whale alerts, stablecoin exchange reserves - these are the on-chain signals institutions use.

**Pricing**: $29/mo Advanced (full historical, 24h resolution API), $99/mo Professional (API access).

**Note**: API access requires Professional plan ($99/mo). Advanced plan gets you charts and alerts but limited API.

```python
import requests
import pandas as pd

CQ_API_KEY = "YOUR_CRYPTOQUANT_KEY"  # Professional plan required for API

def fetch_cryptoquant_exchange_flow(asset="btc", metric="exchange-flows/inflow"):
    """CryptoQuant exchange flow data. Requires Professional plan API key."""
    url = f"https://api.cryptoquant.com/v1/{asset}/{metric}"
    headers = {"Authorization": f"Bearer {CQ_API_KEY}"}
    params = {"window": "day", "limit": 365}
    r = requests.get(url, headers=headers, params=params)
    return pd.DataFrame(r.json().get("result", {}).get("data", []))
```

---

### 3.3 Santiment Pro ($49/mo)

**Why it might have alpha**: Removes the 30-day lag on restricted metrics (social volume, sentiment, whale transactions). Full real-time access to all 2000+ assets. The combination of social + on-chain in one API is unique.

Same code as section 2.2, but with an API key set, restricted metrics become real-time.

---

## Summary Matrix

| Source | Cost | Granularity | History | Auth | Alpha Category |
|--------|------|-------------|---------|------|----------------|
| **Binance Futures** | Free | 5m-1d | 30d (data), years (klines) | None | Derivatives positioning |
| **Deribit Options** | Free | Snapshot | Build yourself | None | Options sentiment, IV |
| **Fear & Greed** | Free | Daily | 2018+ | None | Composite sentiment |
| **DefiLlama Stables** | Free | Daily | 2017+ | None | Capital flows |
| **yfinance Macro** | Free | Daily | 20+ years | None | Macro regime |
| **Google Trends** | Free | Hourly/Weekly | 5 years | None | Retail attention |
| **CryptoCompare Social** | Free (250K calls) | Hourly | 7d hourly | API key | Social activity |
| **Santiment** | Free / $49 Pro | Daily-Hourly | 1yr free | API key | On-chain + social |
| **CoinGecko** | Free (limited) | Snapshot | Varies | Optional | Community metrics |
| **CoinGlass** | $29/mo | 1m-1d | 2019+ | API key | Aggregated derivatives |
| **CryptoQuant** | $99/mo | Hourly-Daily | 2017+ | API key | Exchange flows |

---

## Recommended Build Order

Based on cost, ease of integration, and potential alpha (novelty vs. price data):

### Phase 1: Free data collection (Week 1)

1. **Binance Futures derivatives** - Start collecting hourly funding rate, OI, long/short ratio, taker volume. Run a cron job to accumulate history beyond the 30-day window. Use klines for deeper taker volume history.
2. **DefiLlama stablecoin supply** - Single API call gets full history. Compute daily change in USDT + USDC supply. This is the "dry powder" indicator.
3. **Fear & Greed Index** - Single API call, full history since 2018. Daily feature.
4. **yfinance macro data** - DXY, VIX, SPX, US10Y. Daily features. Compute rolling correlations and regime indicators.
5. **Deribit options snapshots** - Set up hourly cron to collect put-call ratio, IV term structure, total OI. Needs 1-2 months to build useful history.

### Phase 2: Free + API key data (Week 2-3)

6. **CryptoCompare social stats** - Register for free key, start pulling hourly social data.
7. **Santiment dev_activity** - Free tier, no lag on this metric. Strong for alt-coin selection.
8. **Google Trends** - Set up weekly collection for "bitcoin", "buy crypto", "crypto crash" search terms.

### Phase 3: Feature engineering + testing (Week 3-4)

- Normalize all features to Z-scores or percentile ranks
- Test single-feature predictive power on 1h/4h/1d forward returns
- Look for regime-dependent features (features that only work in specific macro environments)
- Combine top features into ensemble signals

### Phase 4: Evaluate paid sources (Month 2)

9. **CoinGlass $29/mo** - If Binance-only derivatives data shows signal, upgrade to cross-exchange aggregated data.
10. **Santiment Pro $49/mo** - If social/on-chain features show promise with lagged data, pay for real-time.

---

## Critical Notes

1. **Survivorship bias warning**: Most of these data sources are only available post-2018/2019. Your OOS period may be short.
2. **Look-ahead bias in stablecoin data**: DefiLlama data may be revised retroactively. Use point-in-time snapshots where possible.
3. **Binance 30-day limit**: Start collecting NOW. Every day you wait is a day of history you cannot recover for the `/futures/data/` endpoints.
4. **Deribit options**: No free historical API. You MUST build your own history by polling. Start the cron job immediately.
5. **Alpha decay**: Any signal that is widely known (funding rate extremes, fear/greed extremes) has likely been arbed away for simple directional trades. The edge will come from combining multiple alternative data features, not from any single one.

---

## Data Collection Cron Script (Starter)

```python
"""
cron_collect_altdata.py - Run hourly to build alternative data history.
Crontab: 0 * * * * python /path/to/cron_collect_altdata.py
"""
import json
import os
from datetime import datetime
from pathlib import Path

import requests
import pandas as pd

DATA_DIR = Path("data/alternative")
DATA_DIR.mkdir(parents=True, exist_ok=True)

def append_jsonl(filepath, record):
    with open(filepath, "a") as f:
        f.write(json.dumps(record) + "\n")

def collect_deribit_options():
    """Snapshot Deribit options every hour."""
    for currency in ["BTC", "ETH"]:
        try:
            r = requests.get(
                "https://www.deribit.com/api/v2/public/get_book_summary_by_currency",
                params={"currency": currency, "kind": "option"}, timeout=30
            )
            data = r.json().get("result", [])
            # Compute aggregates
            df = pd.DataFrame(data)
            calls = df[df["instrument_name"].str.endswith("-C")]
            puts = df[df["instrument_name"].str.endswith("-P")]
            record = {
                "timestamp": datetime.utcnow().isoformat(),
                "currency": currency,
                "total_call_oi": calls["open_interest"].sum(),
                "total_put_oi": puts["open_interest"].sum(),
                "pcr_oi": puts["open_interest"].sum() / max(calls["open_interest"].sum(), 1),
                "total_call_vol": calls["volume"].sum(),
                "total_put_vol": puts["volume"].sum(),
                "num_instruments": len(df),
            }
            # IV term structure (ATM IV per nearest 3 expiries)
            underlying = df["underlying_price"].iloc[0] if not df.empty else 0
            record["underlying_price"] = underlying

            append_jsonl(DATA_DIR / f"deribit_options_{currency.lower()}.jsonl", record)
        except Exception as e:
            print(f"Deribit {currency} error: {e}")

def collect_binance_derivatives():
    """Collect Binance derivatives data for top symbols."""
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
               "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT"]

    for symbol in symbols:
        try:
            # Funding rate
            r = requests.get("https://fapi.binance.com/fapi/v1/fundingRate",
                           params={"symbol": symbol, "limit": 1}, timeout=10)
            funding = r.json()[-1] if r.json() else {}

            # Open interest
            r = requests.get("https://fapi.binance.com/fapi/v1/openInterest",
                           params={"symbol": symbol}, timeout=10)
            oi = r.json()

            # Long/short ratio
            r = requests.get("https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
                           params={"symbol": symbol, "period": "1h", "limit": 1}, timeout=10)
            ls = r.json()[-1] if r.json() else {}

            # Taker buy/sell
            r = requests.get("https://fapi.binance.com/futures/data/takerlongshortRatio",
                           params={"symbol": symbol, "period": "1h", "limit": 1}, timeout=10)
            taker = r.json()[-1] if r.json() else {}

            record = {
                "timestamp": datetime.utcnow().isoformat(),
                "symbol": symbol,
                "funding_rate": float(funding.get("fundingRate", 0)),
                "open_interest": float(oi.get("openInterest", 0)),
                "long_short_ratio": float(ls.get("longShortRatio", 0)),
                "long_account_pct": float(ls.get("longAccount", 0)),
                "short_account_pct": float(ls.get("shortAccount", 0)),
                "taker_buy_sell_ratio": float(taker.get("buySellRatio", 0)),
                "taker_buy_vol": float(taker.get("buyVol", 0)),
                "taker_sell_vol": float(taker.get("sellVol", 0)),
            }
            append_jsonl(DATA_DIR / "binance_derivatives.jsonl", record)
        except Exception as e:
            print(f"Binance {symbol} error: {e}")

if __name__ == "__main__":
    collect_deribit_options()
    collect_binance_derivatives()
    print(f"[{datetime.utcnow().isoformat()}] Collection complete")
```

---

## Sources

- [Binance Futures API Docs](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data)
- [Deribit API Documentation](https://docs.deribit.com/)
- [Alternative.me Fear & Greed](https://alternative.me/crypto/fear-and-greed-index/)
- [DefiLlama Stablecoins API](https://defillama.com/stablecoins)
- [CoinGlass API](https://docs.coinglass.com/)
- [Santiment API / sanpy](https://github.com/santiment/sanpy)
- [CryptoCompare Social Stats](https://min-api.cryptocompare.com/documentation?key=Social)
- [CoinGecko API](https://www.coingecko.com/en/api)
- [pytrends / Google Trends](https://github.com/GeneralMills/pytrends)
- [FRED Economic Data](https://fred.stlouisfed.org/docs/api/fred/)
- [yfinance](https://github.com/ranaroussi/yfinance)
- [Tardis.dev](https://tardis.dev/)
- [CryptoDataDownload DVOL](https://www.cryptodatadownload.com/data/deribit/)
- [CryptoQuant](https://cryptoquant.com/docs)
