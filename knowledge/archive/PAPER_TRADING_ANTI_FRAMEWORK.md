# The Case Against Trading Frameworks: Build a Thin CCXT Wrapper

> **TL;DR — Why custom CCXT beats frameworks**
> - Jesse: Kraken NOT supported, $899-$1,599 license, complete strategy rewrite — disqualified
> - Freqtrade: Kraken painful (720-candle limit, 3100ms rate limit), 2-4 day rewrite per strategy, two codebases to maintain
> - OctoBot: quant scripting in "early alpha", async mismatch with our vectorized approach — not ready
> - Custom CCXT Pro wrapper: handles both exchanges, no rewrite tax, full control over execution
> **When to read full file:** Justifying infrastructure choices, evaluating framework adoption, troubleshooting execution
> **Sections:** 1-Summary, 2-Requirements, 3-Framework Demolition, 4-CCXT Capabilities, 5-Porting Tax, 6-Slippage, 7-Wrapper Arch, 8-Concessions, 9-Cost-Benefit, 10-Verdict

> **Position:** AGAINST using Jesse, Freqtrade, or OctoBot for paper trading.
> FOR building a thin custom wrapper using CCXT + CCXT Pro.
>
> Last updated: 2026-02-28. This is the SKEPTIC/CONTRARIAN position in an adversarial debate.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [What We Actually Need](#2-what-we-actually-need)
3. [Framework-by-Framework Demolition](#3-framework-by-framework-demolition)
4. [CCXT Capabilities Assessment](#4-ccxt-capabilities-assessment)
5. [The Strategy Porting Tax](#5-the-strategy-porting-tax)
6. [Order Book Slippage: How Much Do We Really Need?](#6-order-book-slippage-how-much-do-we-really-need)
7. [Architecture of the Thin Wrapper](#7-architecture-of-the-thin-wrapper)
8. [Honest Concessions: Where Frameworks Win](#8-honest-concessions-where-frameworks-win)
9. [Cost-Benefit Analysis](#9-cost-benefit-analysis)
10. [Verdict](#10-verdict)

---

## 1. Executive Summary

We have a working backtesting system. Our strategies (S11 Momentum Burst, S09 Optimized Trend) are already coded in Python with NumPy/Numba in `engine.py`. We need paper trading on Kraken AND Binance simultaneously. The question is: do we adopt a framework, or build a thin wrapper?

**The core argument:** For swing trading with ~2500 trades/year across 6-11 tokens, every existing framework imposes costs (financial, engineering, operational) that exceed their benefits. CCXT already provides 90% of what a framework gives us. The remaining 10% is trivial to build and gives us full control.

**Framework overhead summary:**

| Framework | Kraken Support | Cost | Strategy Rewrite | Deal-Breaker |
|-----------|---------------|------|-----------------|--------------|
| Jesse | NOT SUPPORTED for live/paper | $899-$1,599 | Full rewrite to class-based interface | No Kraken live trading driver. Closed-source live plugin. |
| Freqtrade | Supported but painful | Free | Full rewrite to `populate_indicators`/`populate_entry_trend` | 720-candle limit, 2GB+ RAM for data, 3100ms rate limit, broken Kraken API in 2025.9 |
| OctoBot | Supported | Free | Full rewrite to tentacles system | Quant scripting in "early alpha", not ready for advanced strategies |

**What CCXT gives us for free:** Exchange auth, order placement, balance queries, order book L2 data, WebSocket streaming (CCXT Pro), unified API across 100+ exchanges, sandbox/testnet switching, rate limiting, reconnection handling.

**What we need to build:** ~300-500 lines of Python glue code.

---

## 2. What We Actually Need

Let us be precise about requirements before evaluating solutions:

### Hard Requirements
1. **Dual-exchange paper trading** on Kraken AND Binance simultaneously
2. **Run our existing strategies** (S11 Momentum Burst, S09 Optimized Trend) without rewriting them
3. **Realistic fee modeling** per exchange (Kraken: 0.25%/0.40% base tier; Binance: 0.10%/0.10%)
4. **Some form of slippage modeling** that accounts for token liquidity differences
5. **Position tracking** across 6-11 tokens with portfolio-level risk management
6. **Logging** of all simulated trades for post-analysis

### Nice-to-Have
7. Real-time order book snapshots for slippage estimation
8. WebSocket data feeds for live signal generation
9. Alerting (Telegram/Discord) on signal triggers

### Explicitly NOT Needed
- Sub-second execution latency (our holds are 18-720 hours)
- High-frequency order management (we place ~7 trades/day across all tokens)
- Margin/leverage/futures support (we trade spot only)
- Backtesting infrastructure (we already have it, and it is fast: 49 tokens in 1.07s)
- Strategy optimization/hyperopt (we already do CPCV + walk-forward validation)

The question is: what is the minimum viable system that satisfies requirements 1-6?

---

## 3. Framework-by-Framework Demolition

### 3.1 Jesse: $899-$1,599 for Something That Does Not Support Kraken

**The fatal flaw: Kraken is NOT a supported exchange for live/paper trading.**

Jesse's confirmed live trading exchanges (as of February 2026):
- Apex Pro, Apex Omni, Hyperliquid
- Bybit (USDT Perpetual, USDC Perpetual, Spot)
- Binance (Perpetual Futures, Spot, US Spot)
- Coinbase Spot (Coinbase Advanced)
- Gate.io Perpetual Futures

Kraken is **not on this list**. This alone is disqualifying since our primary requirement is dual-exchange paper trading on both Kraken AND Binance.

**But suppose we could sponsor Kraken development.** Jesse quotes $5,000-$10,000 to develop a new exchange driver. Add that to the $999 license and we are looking at $6,000-$11,000 upfront for a closed-source dependency we cannot fix or extend ourselves.

**Additional problems:**
- The live trading plugin is **closed source**. If something breaks with the Kraken integration, we wait for Jesse's team to fix it. We cannot debug or patch it ourselves.
- Jesse's strategy interface requires class-based strategies with specific methods (`should_long()`, `go_long()`, `should_cancel_entry()`, `update_position()`, etc.). Our strategies are pure functions: `strategy(ctx: StrategyContext) -> StrategyResult`. Porting requires a complete rewrite of the strategy interface.
- The $899 plan limits you to 1 trading route and hourly/daily timeframes. For 6-11 tokens on two exchanges, we need the $999+ plan with 10+ trading routes.
- Jesse now has a token ($JESSE on MEXC) -- this is a red flag for long-term project sustainability. Trading framework projects should be funded by users, not token speculation.

**Verdict: Disqualified.** No Kraken support. Closed-source live plugin. Would cost $6K-$11K minimum with no guarantee of timeline.

Sources:
- [Jesse Supported Exchanges](https://docs.jesse.trade/docs/supported-exchanges/)
- [Jesse Pricing](https://jesse.trade/pricing)
- [Jesse FAQ: Is my exchange supported?](https://jesse.trade/help/faq/is-my-exchange-supported)

### 3.2 Freqtrade: Free but Kraken Is a Known Pain Point

Freqtrade is the strongest framework contender. It is free, open-source, actively maintained, and supports Kraken. But the Kraken integration has well-documented, long-standing problems:

**Problem 1: 720-Candle API Limit**

Kraken's REST API only returns 720 historical candles per request. This is sufficient for live trading (Freqtrade fetches new candles in real-time), but:
- Our S09 Optimized Trend requires EMA50 on daily timeframes plus ADX with 200+ bars of warmup. The 720-candle limit on 1H data gives us only 30 days of warmup. On 15m data, that is only 7.5 days.
- Backtesting requires downloading trade-by-trade data and converting to candles locally, which leads to Problem 2.
- Issue [#2134](https://github.com/freqtrade/freqtrade/issues/2134) (opened 2019, still relevant) and [#10407](https://github.com/freqtrade/freqtrade/issues/10407) document this.

**Problem 2: Memory Consumption**

Downloading Kraken historical data via `--dl-trades` requires loading every individual trade into memory for candle conversion. Users report:
- BTC/EUR: 55 million trades requiring ~30GB RAM ([Issue #4449](https://github.com/freqtrade/freqtrade/issues/4449))
- 2GB RAM systems get OOM-killed by the kernel
- The workaround is downloading Kraken's quarterly CSV trade archives manually

**Problem 3: Rate Limiting**

Kraken's API rate limits are aggressive. Freqtrade's recommended configuration uses `rateLimit: 3100` (3.1 seconds between API calls). With 6-11 tokens, each requiring OHLCV + order book + balance checks, a single polling cycle takes 20-35 seconds. In October 2025, users reported persistent `EAPI:Rate limit exceeded` errors even with doubled rate limits ([Issue #12326](https://github.com/freqtrade/freqtrade/issues/12326)).

**Problem 4: Strategy Rewrite**

Freqtrade requires strategies to implement three methods within a class inheriting `IStrategy`:
- `populate_indicators(self, dataframe, metadata)` -- add columns to a Pandas DataFrame
- `populate_entry_trend(self, dataframe, metadata)` -- set `enter_long = 1` where entry conditions are met
- `populate_exit_trend(self, dataframe, metadata)` -- set `exit_long = 1` where exit conditions are met

Our strategies are pure NumPy functions that return entry masks and trade parameters. The mapping is not trivial:

**Our interface:**
```python
def strategy(ctx: StrategyContext) -> StrategyResult:
    entry = ctx.ind_1h['adx'] > 30  # NumPy boolean array
    return StrategyResult(
        entry_mask=entry,
        direction=np.ones(n, dtype=np.int8),
        stop_mult=3.0, trail_mult=3.0, target_mult=999,
        no_stop_bars=24, min_hold=18, max_hold=720,
        edge=0.40, exit_regimes={CRISIS, DOWNTREND},
    )
```

**Freqtrade's interface:**
```python
class MyStrategy(IStrategy):
    INTERFACE_VERSION = 3
    minimal_roi = {"0": 999}  # How do we map max_hold=720?
    stoploss = -0.09  # Fixed %, but we use ATR-based stops
    trailing_stop = True  # But we need 3x ATR, not a fixed %

    def populate_indicators(self, dataframe, metadata):
        dataframe['adx'] = ta.ADX(dataframe)  # Must use ta-lib, not our Numba code
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe.loc[dataframe['adx'] > 30, 'enter_long'] = 1
        return dataframe
```

Key translation problems:
- **ATR-based stops vs fixed percentage stops:** Our `stop_mult=3.0` means 3x ATR. Freqtrade's `stoploss` is a fixed percentage. Custom stoploss callbacks exist but add complexity.
- **No-stop protection window:** Our `no_stop_bars=24` has no Freqtrade equivalent. We would need a custom stoploss callback that checks trade age.
- **Regime-based exits:** Our `exit_regimes={CRISIS, DOWNTREND}` uses a daily regime detector mapped to 1H. This requires passing custom data through Freqtrade's informative pairs feature.
- **Multi-timeframe alignment:** Our strategies use `ctx.align_daily_to_1h()` and `ctx.align_4h_to_1h()` helpers. Freqtrade has informative timeframes but the alignment semantics differ.
- **Position sizing:** Our Kelly-criterion position sizing uses tier, edge, and capital. Freqtrade has `custom_stake_amount` callback but the interface is different.
- **Numba JIT simulation:** Our `_simulate_core_jit` is a Numba-compiled simulation loop. Freqtrade's backtester uses its own simulation engine. We lose our simulation engine entirely.

**Estimated porting effort:** 2-4 days per strategy, plus debugging. And then we maintain TWO codebases -- our original for backtesting and the Freqtrade version for paper/live trading. Any strategy change must be made in both places.

**Problem 5: Framework Lock-in**

Once strategies are in Freqtrade format, we are locked into:
- Freqtrade's execution model (poll-based, not event-driven)
- Freqtrade's position management
- Freqtrade's backtesting engine (we would not use our own, losing CPCV + walk-forward validation)
- Freqtrade's configuration system (JSON config files with specific schema)
- Freqtrade's update cycle (breaking changes between versions, e.g., v2 to v3 interface migration)

**Verdict: Possible but expensive.** Kraken works but with significant pain (rate limits, memory, candle limits). Strategy rewrite is substantial and creates maintenance burden of two codebases.

Sources:
- [Freqtrade Exchange Notes - Kraken](https://www.freqtrade.io/en/stable/exchanges/)
- [Issue #2134: Kraken 720 candle limit](https://github.com/freqtrade/freqtrade/issues/2134)
- [Issue #4449: Data download requires 2GB+ RAM](https://github.com/freqtrade/freqtrade/issues/4449)
- [Issue #1982: Kraken rate limit exceeded](https://github.com/freqtrade/freqtrade/issues/1982)
- [Issue #12326: Kraken API broken in 2025.9](https://github.com/freqtrade/freqtrade/issues/12326)
- [Freqtrade Strategy Customization](https://www.freqtrade.io/en/stable/strategy-customization/)

### 3.3 OctoBot: Not Ready for Quant Strategies

OctoBot's "tentacles" architecture is designed for modular extensibility, but:

- **OctoBot-Script is in "early alpha."** Their quant scripting framework for custom strategies is explicitly described as alpha-stage. For production paper trading with real money at stake, this is unacceptable.
- **Strategy interface mismatch:** OctoBot strategies are async functions called on new price data. They use a completely different paradigm from our vectorized NumPy strategies.
- **No multi-timeframe support comparable to ours.** Our strategies use 1H/4H/Daily with alignment helpers. OctoBot's evaluator system does not natively support the kind of multi-timeframe regime detection we use.
- **Community size:** OctoBot has ~4K GitHub stars vs Freqtrade's ~30K+. Smaller community means fewer examples, less debugging help, slower issue resolution.
- **Built for retail, not quant.** OctoBot's strength is grid/DCA/TradingView strategies. Its AI integration is ChatGPT prompt-based, not quantitative. Our strategies use Numba-JIT simulations, CPCV validation, and walk-forward optimization -- none of which map to OctoBot's paradigm.

**Verdict: Not a serious contender.** Alpha-stage scripting, wrong paradigm, wrong target user.

Sources:
- [OctoBot GitHub](https://github.com/Drakkar-Software/OctoBot)
- [OctoBot Script Strategies](https://www.octobot.cloud/en/guides/octobot-script-docs/strategies)
- [AI-Integrated Crypto Trading Platforms Comparison](https://medium.com/@gwrx2005/ai-integrated-crypto-trading-platforms-a-comparative-analysis-of-octobot-jesse-b921458d9dd6)

---

## 4. CCXT Capabilities Assessment

### 4.1 What CCXT Provides Out of the Box

CCXT is a JavaScript/Python/PHP library for cryptocurrency trading. It supports 100+ exchanges with a unified API. Here is what it gives us for free:

**Exchange Connectivity:**
- Unified API for Kraken AND Binance (and 100+ others)
- API key authentication, signing, nonce management
- Rate limiting with exponential backoff (built-in)
- Sandbox/testnet mode: `exchange.set_sandbox_mode(True)`

**Market Data:**
- `fetch_ohlcv(symbol, timeframe, since, limit)` -- candle data
- `fetch_order_book(symbol, limit)` -- L2 order book snapshots
- `fetch_ticker(symbol)` -- current price, volume, bid/ask
- `fetch_tickers()` -- all tickers at once

**Trading:**
- `create_order(symbol, type, side, amount, price)` -- place orders
- `cancel_order(id, symbol)` -- cancel orders
- `fetch_order(id, symbol)` -- check order status
- `fetch_balance()` -- account balances
- `fetch_my_trades()` -- trade history

**CCXT Pro (WebSocket streaming):**
- `watch_order_book(symbol)` -- real-time L2 order book updates
- `watch_ticker(symbol)` -- real-time price updates
- `watch_ohlcv(symbol, timeframe)` -- real-time candle updates
- `watch_trades(symbol)` -- real-time trade feed
- Automatic reconnection with exponential backoff
- Connection multiplexing (one connection per exchange)

### 4.2 Multi-Exchange Simultaneous Operation

CCXT Pro explicitly supports running multiple exchanges simultaneously via asyncio. The official example `many-exchanges-many-streams.py` demonstrates exactly our use case:

```python
import ccxt.pro
from asyncio import gather, run

async def symbol_loop(exchange, symbol):
    while True:
        orderbook = await exchange.watch_order_book(symbol)
        # Process order book snapshot
        print(exchange.id, symbol, orderbook['asks'][0], orderbook['bids'][0])

async def exchange_loop(exchange_id, symbols):
    exchange = getattr(ccxt.pro, exchange_id)()
    loops = [symbol_loop(exchange, symbol) for symbol in symbols]
    await gather(*loops)
    await exchange.close()

async def main():
    exchanges = {
        'kraken':  ['SUI/USD', 'BONK/USD', 'FLOKI/USD', 'ZRO/USD'],
        'binance': ['SUI/USDT', 'BONK/USDT', 'FLOKI/USDT', 'ZRO/USDT'],
    }
    loops = [exchange_loop(id, syms) for id, syms in exchanges.items()]
    await gather(*loops)

run(main())
```

This is ~20 lines of code. No framework needed. Both exchanges run concurrently with independent rate limiting and reconnection handling.

### 4.3 Sandbox/Testnet Support

| Exchange | Sandbox Type | CCXT Support |
|----------|-------------|--------------|
| Binance Spot | Testnet (demo-api.binance.com) | Yes, `set_sandbox_mode(True)` -- note: URL recently changed, need CCXT >= late 2025 |
| Binance Futures | Testnet | Yes |
| Kraken Spot | No official sandbox | NO -- Kraken has no spot testnet |
| Kraken Futures | Demo environment | Yes |

**Critical finding: Kraken has no spot testnet.** This means for Kraken spot paper trading, we MUST simulate fills locally regardless of which approach we use. Neither Freqtrade nor Jesse can magically paper-trade on Kraken spot -- they all simulate fills locally. This removes a supposed advantage of frameworks.

### 4.4 Order Book L2 Access

Both Kraken and Binance provide L2 order book data through CCXT:

```python
# REST snapshot
ob = exchange.fetch_order_book('SUI/USD', limit=20)
# ob['bids'] = [[price, amount], [price, amount], ...]
# ob['asks'] = [[price, amount], [price, amount], ...]

# WebSocket streaming (CCXT Pro)
ob = await exchange.watch_order_book('SUI/USD')
# Same format, updated in real-time via incremental diffs
```

Kraken provides L2 and even L3 order book data for free via WebSocket. Binance provides L2 up to 5000 levels. For our slippage modeling, 20 levels is more than sufficient (see Section 6).

Sources:
- [CCXT GitHub](https://github.com/ccxt/ccxt)
- [CCXT Pro Manual](https://github.com/ccxt/ccxt/wiki/ccxt.pro.manual)
- [CCXT many-exchanges-many-streams example](https://github.com/ccxt/ccxt/blob/master/examples/ccxt.pro/py/many-exchanges-many-streams.py)
- [CCXT order book depth example](https://github.com/ccxt/ccxt/blob/master/examples/py/order-book-extra-level-depth-param.py)

---

## 5. The Strategy Porting Tax

This is the strongest argument against frameworks. Let us quantify it.

### 5.1 Our Current Strategy Interface

Our strategies are **pure functions** with a clean contract:

```
Input:  StrategyContext (indicators, regime, enriched data, all timeframes)
Output: StrategyResult (entry_mask, direction, stop/trail/target multipliers, hold limits, edge)
```

The Engine handles everything else: data loading, indicator computation, simulation, position sizing, portfolio aggregation, CPCV validation, walk-forward testing. A strategy is typically 30-60 lines of signal logic.

### 5.2 What Frameworks Require

Every framework requires strategies in a specific format:

**Freqtrade:** Class inheriting `IStrategy` with `populate_indicators()`, `populate_entry_trend()`, `populate_exit_trend()`, plus callbacks for custom stoploss, custom exit, custom stake amount. Pandas DataFrame-based. Must use their indicator library or ta-lib.

**Jesse:** Class inheriting `Strategy` with `should_long()`, `go_long()`, `should_cancel_entry()`, `update_position()`. Must use their indicator module. Must define routes (exchange + symbol + timeframe).

**OctoBot:** Async function called on new data. Must use their evaluator/tentacle system.

### 5.3 Translation Complexity for Our Strategies

Let us trace through S11 Momentum Burst specifically:

**Our version (55 lines, pure NumPy):**
- Reads `ctx.ind_1h['ret_1']`, `ctx.ind_1h['adx']`, `ctx.ind_1h['ema_20']`, `ctx.ind_1h['vol_ratio']`
- Combines four boolean conditions
- Returns StrategyResult with specific stop/trail/target/hold parameters

**What Freqtrade would need:**
1. Map our `compute_indicators_fast()` output to Freqtrade DataFrame columns (60+ indicators)
2. Implement multi-timeframe: our `align_daily_to_1h()` must become Freqtrade's `informative_pairs()` + merge logic
3. Implement regime detection: our `detect_daily_regime()` must feed into Freqtrade's data pipeline
4. Implement ATR-based stops: custom `custom_stoploss()` callback reading ATR from DataFrame
5. Implement no-stop protection: custom `custom_stoploss()` must check `trade.open_date` and disable stops for 24 bars
6. Implement regime-based exits: custom `custom_exit()` checking regime column
7. Implement Kelly position sizing: custom `custom_stake_amount()` callback
8. Maintain synchronization between our backtest code and Freqtrade code

**Estimated lines of code:** 200-300 per strategy in Freqtrade (vs 55 in our system)
**Estimated porting time:** 2-4 days per strategy (2 strategies = 4-8 days)
**Ongoing maintenance cost:** Every strategy change must be made in TWO places

### 5.4 The Thin Wrapper Alternative

With a thin wrapper, our strategies run **unchanged**:

```python
# Paper trading loop (simplified)
while True:
    # Fetch latest candle data via CCXT
    candles = await exchange.watch_ohlcv(symbol, '1h')
    df_1h = update_dataframe(candles)

    # Run our EXACT strategy code
    ctx = engine._build_context(ticker, df_1h)
    result = strategy_fn(ctx)

    # Check if new entry signal
    if result.entry_mask[-1] and not has_open_position(ticker):
        # Estimate slippage from order book
        ob = await exchange.watch_order_book(symbol)
        slippage = estimate_slippage(ob, position_size)

        # Simulate fill
        fill_price = candles[-1]['close'] * (1 + slippage)
        record_paper_trade(ticker, 'entry', fill_price, position_size)
```

The strategy function is called identically to how the backtester calls it. Zero porting effort. Zero maintenance divergence. One codebase.

---

## 6. Order Book Slippage: How Much Do We Really Need?

### 6.1 The Case That Order Book Data Is Overkill for Swing Trading

Our trading profile:
- **~2,500 trades/year** across 6-11 tokens = ~7 trades/day average
- **Hold periods: 18-720 hours** (0.75 to 30 days)
- **Position sizes: $4,000-$24,000** per trade ($200K capital, 2-12% per position)
- **Trade frequency per token: ~0.6/day** at most

For this profile, real-time order book modeling is significant overkill because:

1. **Entry timing is not critical.** A swing trade that holds for 3-30 days does not care whether it enters at $3.4521 or $3.4535. The difference is 0.04%. Our strategies have edge estimates of 35-40% on individual trades -- the entry price noise is irrelevant.

2. **Slippage is dominated by spread, not depth.** For $4K-$24K positions on tokens like SUI, BONK, FLOKI, the top 2-3 levels of the order book will absorb the entire order. We are not moving markets. The primary cost is the bid-ask spread, not walking the book.

3. **Historical spread data is a sufficient proxy.** Instead of live order book modeling, we can:
   - Sample the bid-ask spread periodically (every 5 minutes) for our tokens on each exchange
   - Build a statistical model: `slippage = f(spread, position_size, hour_of_day, volatility)`
   - Use this model in paper trading instead of real-time L2 data

4. **The academic literature agrees.** For positions below 1% of average daily volume (ours are typically 0.01-0.1% of ADV), market impact is negligible. The Almgren-Chriss model predicts near-zero permanent impact for our position sizes.

### 6.2 The Minimum Viable Slippage Model

Instead of full order book simulation, use a **three-tier statistical model** based on our existing KRAKEN_FEES.md research:

```python
SLIPPAGE_MODEL = {
    # Token tier -> (base_spread_bps, depth_impact_bps_per_1k_usd)
    'liquid':     (5, 0.5),   # BTC, ETH, SOL, AVAX -- 5bps spread + 0.5bps per $1K
    'mid':        (15, 2.0),  # SUI, TRX, FIL, ZRO -- 15bps spread + 2bps per $1K
    'low':        (40, 5.0),  # BONK, FLOKI, PENGU, DENT, OM -- 40bps + 5bps per $1K
}

def estimate_slippage(tier, position_usd):
    base, impact = SLIPPAGE_MODEL[tier]
    return (base + impact * position_usd / 1000) / 10000  # Convert bps to decimal
```

For a $10,000 BONK trade: `(40 + 5 * 10) / 10000 = 0.90%` slippage. This aligns with our KRAKEN_FEES.md research showing 0.20-1.0%+ slippage for low-liquidity tokens.

### 6.3 When Order Book Data IS Useful

Even though full L2 modeling is overkill for execution simulation, periodic order book snapshots are valuable for:

1. **Calibrating the statistical model.** Sample order books every 5 minutes, compute theoretical slippage for various position sizes, and update the tier-based model weekly.
2. **Detecting liquidity regime changes.** If BONK's order book depth drops 80% from its usual level, that is a signal to reduce position size or skip the trade.
3. **Comparing exchanges.** Snapshot Kraken vs Binance order books for the same token to measure which exchange offers better execution for each token. This is the core of our dual-exchange comparison.

The thin wrapper can do all of this with periodic `fetch_order_book()` calls (REST, not even WebSocket). CCXT Pro WebSocket streaming is available if we want real-time updates later, but it is not needed for MVP.

### 6.4 The Pragmatic Approach

**Phase 1 (MVP):** Statistical slippage model (the three-tier table above). No order book data needed. Get paper trading running in 1-2 days.

**Phase 2 (Calibration):** Add periodic order book snapshots (every 5 minutes) via REST. Compare theoretical slippage to statistical model. Adjust parameters.

**Phase 3 (Optional):** If Phase 2 reveals significant discrepancies, upgrade to CCXT Pro WebSocket order book streaming for real-time slippage estimation. This is ~30 lines of additional code.

Sources:
- [Kraken: What is slippage in crypto?](https://www.kraken.com/learn/what-is-slippage-in-crypto)
- [Identifying Crypto Market Trends Using Orderbook Slippage Metrics](https://blog.amberdata.io/identifying-crypto-market-trends-using-orderbook-slippage-metrics)
- [CoinAPI: Backtest Crypto Strategies with Real Market Data](https://www.coinapi.io/blog/backtest-crypto-strategies-with-real-market-data)
- [How to Backtest a Crypto Bot: Realistic Fees and Slippage](https://paybis.com/blog/how-to-backtest-crypto-bot/)

---

## 7. Architecture of the Thin Wrapper

### 7.1 Component Overview

```
+------------------+     +-----------------+     +------------------+
| Our Strategies   |     | Paper Trading   |     | CCXT / CCXT Pro  |
| (engine.py)      |<--->| Wrapper         |<--->| (exchange layer)  |
| S09, S11         |     | (~400 lines)    |     | Kraken + Binance  |
+------------------+     +-----------------+     +------------------+
                                |
                          +-----+------+
                          |            |
                    +-----------+ +-----------+
                    | Portfolio | | Trade Log |
                    | Tracker   | | (JSON/CSV)|
                    +-----------+ +-----------+
```

### 7.2 Core Components (~400 lines total)

**1. Data Fetcher (80 lines)**
```python
class LiveDataFetcher:
    """Fetch OHLCV data from exchanges, maintain rolling window."""
    def __init__(self, exchange_id, symbols):
        self.exchange = getattr(ccxt.pro, exchange_id)()
        self.buffers = {}  # symbol -> DataFrame (rolling 2000 bars)

    async def update(self, symbol, timeframe='1h'):
        candles = await self.exchange.watch_ohlcv(symbol, timeframe)
        self.buffers[symbol] = update_rolling_df(self.buffers[symbol], candles)
        return self.buffers[symbol]
```

**2. Signal Generator (60 lines)**
```python
class SignalGenerator:
    """Run our existing strategies on live data."""
    def __init__(self, engine, strategy_fn):
        self.engine = engine
        self.strategy_fn = strategy_fn

    def check_signals(self, ticker, df_1h):
        ctx = self.engine._build_context(ticker, df_1h)
        if ctx is None:
            return None
        result = self.strategy_fn(ctx)
        return result  # Contains entry_mask, stop/trail/target params
```

**3. Slippage Estimator (40 lines)**
```python
class SlippageEstimator:
    """Statistical + optional order book slippage estimation."""
    TIERS = {
        'liquid': (5, 0.5), 'mid': (15, 2.0), 'low': (40, 5.0),
    }

    def estimate(self, ticker, position_usd, orderbook=None):
        tier = get_slippage_tier(ticker)
        base, impact = self.TIERS[tier]
        statistical = (base + impact * position_usd / 1000) / 10000

        if orderbook:
            # Walk the order book for more precise estimate
            ob_slippage = walk_order_book(orderbook, position_usd)
            return max(statistical, ob_slippage)  # Conservative: use worse estimate
        return statistical
```

**4. Paper Portfolio (120 lines)**
```python
class PaperPortfolio:
    """Track simulated positions and PnL per exchange."""
    def __init__(self, capital, exchange_id, fee_maker, fee_taker):
        self.capital = capital
        self.exchange_id = exchange_id
        self.positions = {}  # ticker -> Position
        self.trades = []     # completed trade log
        self.fee_maker = fee_maker
        self.fee_taker = fee_taker

    def open_position(self, ticker, price, size_usd, slippage, direction=1):
        fill_price = price * (1 + slippage * direction)
        fee = size_usd * self.fee_taker
        # ... track position

    def check_exits(self, ticker, current_price, atr, regime, bar_count):
        # ATR-based stop, trailing stop, regime exit, max hold
        # Uses SAME logic as our backtester
        pass
```

**5. Main Loop (100 lines)**
```python
async def paper_trade_loop(config):
    """Main paper trading loop for one exchange."""
    engine = Engine()
    fetcher = LiveDataFetcher(config.exchange_id, config.symbols)
    signal_gen = SignalGenerator(engine, config.strategy_fn)
    portfolio = PaperPortfolio(config.capital, config.exchange_id,
                               config.fee_maker, config.fee_taker)
    slippage = SlippageEstimator()

    while True:
        for symbol, ticker in config.symbol_map.items():
            df_1h = await fetcher.update(symbol, '1h')
            result = signal_gen.check_signals(ticker, df_1h)
            if result is None:
                continue

            # Check exits for open positions
            if ticker in portfolio.positions:
                portfolio.check_exits(ticker, df_1h['close'].iloc[-1], ...)

            # Check entries
            if result.entry_mask[-1] and ticker not in portfolio.positions:
                ob = await fetcher.exchange.fetch_order_book(symbol, limit=20)
                slip = slippage.estimate(ticker, position_size, ob)
                portfolio.open_position(ticker, df_1h['close'].iloc[-1],
                                       position_size, slip)

        await asyncio.sleep(60)  # Check every minute (overkill for swing trading)
```

**6. Dual-Exchange Runner (40 lines)**
```python
async def main():
    kraken_config = ExchangeConfig(
        exchange_id='kraken',
        symbols={'SUI/USD': 'SUI', 'BONK/USD': 'BONK', ...},
        fee_maker=0.0025, fee_taker=0.0040,  # Base tier
        capital=100_000,  # Half of total
        strategy_fn=s11_momentum_burst,
    )
    binance_config = ExchangeConfig(
        exchange_id='binance',
        symbols={'SUI/USDT': 'SUI', 'BONK/USDT': 'BONK', ...},
        fee_maker=0.0010, fee_taker=0.0010,  # Base tier
        capital=100_000,
        strategy_fn=s11_momentum_burst,
    )
    await asyncio.gather(
        paper_trade_loop(kraken_config),
        paper_trade_loop(binance_config),
    )
```

### 7.3 What This Architecture Gives Us

1. **Zero strategy rewrite.** `signal_gen.check_signals()` calls the same `strategy_fn(ctx)` as our backtester.
2. **Dual exchange from day one.** Two `paper_trade_loop` coroutines running concurrently.
3. **Exchange-specific fees.** Each portfolio has its own fee schedule.
4. **Pluggable slippage.** Start with statistical, add order book later.
5. **Full trade logging.** Every simulated trade recorded for post-analysis.
6. **Trivially extensible.** Add a third exchange? Add another config. Add alerts? Add a callback. Add a new strategy? Pass a different `strategy_fn`.

### 7.4 Implementation Timeline

| Day | Deliverable |
|-----|------------|
| 1 | Data fetcher + signal generator. Verify strategies produce correct signals on live data. |
| 2 | Paper portfolio + slippage estimator. Simulate fills with statistical model. |
| 3 | Dual-exchange runner + trade logging. End-to-end paper trading on both exchanges. |
| 4 | Alerting (Telegram/Discord) + dashboard. Polish and deploy. |
| 5 | Order book calibration. Periodic snapshots to validate slippage model. |

Total: **5 days to full dual-exchange paper trading system.** Compare to 4-8 days just to port strategies to Freqtrade (before debugging the Kraken integration).

---

## 8. Honest Concessions: Where Frameworks Win

Intellectual honesty demands acknowledging where frameworks genuinely add value:

### 8.1 Freqtrade Advantages We Lose

1. **Battle-tested order management.** Freqtrade handles partial fills, order timeouts, order amendment, and exchange-specific quirks. Our thin wrapper must handle these for live trading (but for paper trading, we simulate fills, so this is irrelevant).

2. **Dry-run mode.** Freqtrade's dry-run is a complete paper trading system that has been tested by thousands of users. It handles edge cases we might miss. Counter-argument: dry-run still simulates fills locally -- it does not use exchange testnet for spot.

3. **Community and documentation.** If something goes wrong with Freqtrade, there are thousands of users, hundreds of strategy examples, and active Discord/Telegram support. Our thin wrapper has an audience of one.

4. **Hyperopt integration.** Freqtrade's hyperopt can optimize strategy parameters against historical data. We already have this (walk-forward + CPCV), but Freqtrade's is more convenient for quick iterations.

5. **Telegram bot integration.** Freqtrade has a mature Telegram bot for monitoring and controlling the bot. We would need to build this ourselves (~50 lines with python-telegram-bot).

### 8.2 Jesse Advantages We Lose

1. **Accurate backtesting engine.** Jesse's backtester is noted for accuracy and avoiding look-ahead bias. Our Numba JIT backtester is also accurate, so this is not a real loss.

2. **Optimization with AI.** Jesse offers AI-driven parameter optimization. Interesting but not needed -- we have CPCV which is more rigorous.

### 8.3 Risk Assessment: What Could Go Wrong With Our Thin Wrapper

1. **Edge cases in position tracking.** What if the exchange WebSocket disconnects mid-trade? What if candle data has gaps? We need to handle these. Mitigation: log everything, add heartbeat checks, use CCXT Pro's built-in reconnection.

2. **Clock drift and signal timing.** If our signal generator runs at :00:03 but the candle closed at :00:00, we might use stale data. Mitigation: wait for candle confirmation (CCXT Pro delivers candles when complete).

3. **Code quality.** A framework has been reviewed by hundreds of developers. Our wrapper is reviewed by us. Mitigation: keep it under 500 lines and write tests.

4. **Maintenance burden.** CCXT updates might break our wrapper. Exchange API changes might require adjustments. Mitigation: CCXT abstracts exchange-specific changes -- that is literally its purpose.

### 8.4 When a Framework WOULD Be the Right Choice

- If we were trading **50+ symbols** on **5+ exchanges** with **complex order types** (iceberg, TWAP, bracket orders), the framework overhead pays for itself.
- If we were doing **high-frequency trading** where execution speed and order management matter.
- If we had **no existing strategy codebase** and were starting from scratch.
- If we needed **margin/futures** with leverage management and funding rate tracking.

None of these apply to our situation.

---

## 9. Cost-Benefit Analysis

### 9.1 Quantified Comparison

| Factor | Thin Wrapper | Freqtrade | Jesse |
|--------|-------------|-----------|-------|
| **Upfront cost** | $0 | $0 | $899-$1,599 + $5K-$10K Kraken driver |
| **Strategy rewrite** | 0 hours | 30-60 hours | 30-60 hours + Kraken unavailable |
| **Time to paper trade** | 3-5 days | 8-15 days (port + debug) | N/A (no Kraken) |
| **Dual-exchange support** | Native | Possible (run 2 instances) | One exchange only per instance |
| **Ongoing maintenance** | 1 codebase | 2 codebases (backtest + live) | 2 codebases |
| **Kraken support** | Yes (CCXT handles it) | Yes (with pain) | No |
| **Order book access** | Yes (CCXT fetch/watch) | Limited (not built into strategy) | Limited |
| **Slippage model** | Fully custom | Fixed % or basic model | Fixed % |
| **Dependency risk** | CCXT (100+ contributors, 33K stars) | Freqtrade (30K stars, active) | Jesse (5K stars, paid plugin) |
| **Migration to live** | Add real order placement | Already handled | N/A |
| **Lines of code** | ~400 | ~300 (strategy port only) | N/A |

### 9.2 The Hidden Cost of Two Codebases

This is the clincher. With any framework, we maintain:
- **Codebase A:** Our `engine.py` + strategies for backtesting, CPCV, walk-forward
- **Codebase B:** Framework-format strategies for paper/live trading

Every strategy change, parameter tweak, or new indicator must be applied to both. This is not a one-time cost -- it is a permanent tax on every future change.

With the thin wrapper, there is **one codebase**. The paper trading wrapper calls the same strategy function as the backtester. Change the strategy once, it is live in both environments.

### 9.3 The "Framework Tax" in Developer Hours Per Year

Assuming 20 strategy iterations per year (parameter changes, new indicators, new exit logic):

| Activity | Thin Wrapper | Freqtrade |
|----------|-------------|-----------|
| Strategy change (backtest) | 1 hour | 1 hour |
| Strategy change (live) | 0 hours (same code) | 1-2 hours (port + test) |
| Framework version updates | 0 | 2-4 hours/quarter |
| Debugging framework-specific issues | 0 | 4-8 hours/quarter |
| **Annual overhead** | **0 hours** | **30-50 hours** |

---

## 10. Verdict

**Build the thin wrapper.**

The math is straightforward:
- Jesse is disqualified (no Kraken).
- OctoBot is disqualified (alpha-stage quant scripting).
- Freqtrade works but imposes a permanent strategy-porting tax and has documented Kraken pain points.
- A thin CCXT wrapper takes 3-5 days, reuses our existing strategies unchanged, supports dual-exchange natively, and has zero ongoing maintenance divergence.

The frameworks solve problems we do not have (complex order management, multi-strategy orchestration, high-frequency execution). They fail to solve the problem we do have (run our existing NumPy strategies against live data on two exchanges simultaneously with realistic fee/slippage modeling).

CCXT provides the hard parts (exchange connectivity, authentication, rate limiting, WebSocket streaming, order book data). We provide the easy part (~400 lines of glue code connecting CCXT to our engine).

**Recommended action:** Build the thin wrapper in 5 days. Use statistical slippage for MVP. Add order book calibration in week 2. If after 30 days of paper trading we find the wrapper inadequate, we will have learned exactly what we need -- and porting to Freqtrade at that point will be an informed decision, not a speculative one.

---

## Appendix A: CCXT Slippage Estimation Code

```python
def walk_order_book(orderbook, side, amount_usd):
    """
    Walk the order book to estimate slippage for a given USD trade size.

    Args:
        orderbook: CCXT order book dict with 'bids' and 'asks'
        side: 'buy' (walk asks) or 'sell' (walk bids)
        amount_usd: trade size in USD

    Returns:
        slippage as a decimal (e.g., 0.003 = 0.3%)
    """
    levels = orderbook['asks'] if side == 'buy' else orderbook['bids']
    if not levels:
        return 0.01  # 1% default if no order book

    best_price = levels[0][0]
    filled_usd = 0.0
    total_cost = 0.0

    for price, volume in levels:
        level_usd = price * volume
        fill = min(amount_usd - filled_usd, level_usd)
        total_cost += fill  # Already in USD terms at this price level
        filled_usd += fill

        if filled_usd >= amount_usd:
            break

    if filled_usd < amount_usd:
        # Not enough liquidity in the book
        return 0.02  # 2% penalty

    avg_price = (total_cost / filled_usd) * best_price / (levels[0][0] if levels[0][0] != 0 else 1)
    # Simplified: compute weighted average fill vs best price
    weighted_sum = 0
    filled = 0
    for price, volume in levels:
        level_usd = price * volume
        fill = min(amount_usd - filled, level_usd)
        weighted_sum += price * (fill / amount_usd)
        filled += fill
        if filled >= amount_usd:
            break

    avg_fill_price = weighted_sum / (filled / amount_usd) if filled > 0 else best_price
    slippage = abs(avg_fill_price - best_price) / best_price

    return slippage
```

## Appendix B: Freqtrade Strategy Port Complexity Example

To illustrate the porting tax, here is S11 Momentum Burst in Freqtrade format:

```python
# Freqtrade version -- 100+ lines vs 55 in our system
class S11MomentumBurst(IStrategy):
    INTERFACE_VERSION = 3
    minimal_roi = {"0": 999}
    stoploss = -0.99  # Disabled -- we use custom stoploss

    # These don't map cleanly to our ATR-based system
    trailing_stop = False  # Must use custom_stoploss instead
    use_custom_stoploss = True

    timeframe = '1h'

    # Multi-timeframe -- must define informative pairs
    def informative_pairs(self):
        pairs = self.dp.current_whitelist()
        informative_pairs = [(pair, '4h') for pair in pairs]
        informative_pairs += [(pair, '1d') for pair in pairs]
        return informative_pairs

    def populate_indicators(self, dataframe, metadata):
        # Must re-implement ALL our indicators
        # Our compute_indicators_fast() uses Numba -- can't use that here
        dataframe['adx'] = ta.ADX(dataframe)
        dataframe['plus_di'] = ta.PLUS_DI(dataframe)
        dataframe['minus_di'] = ta.MINUS_DI(dataframe)
        dataframe['ema_10'] = ta.EMA(dataframe, timeperiod=10)
        dataframe['ema_20'] = ta.EMA(dataframe, timeperiod=20)
        dataframe['ema_50'] = ta.EMA(dataframe, timeperiod=50)
        dataframe['rsi'] = ta.RSI(dataframe)
        dataframe['atr'] = ta.ATR(dataframe)
        # ... 20+ more indicators
        # Must merge informative timeframes
        # Must implement regime detection
        # Must align timeframes
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe.loc[
            (dataframe['ret_1'] > 0.03) &
            (dataframe['adx'] > 20) &
            (dataframe['close'] > dataframe['ema_20']) &
            (dataframe['vol_ratio'] > 1.0) &
            (dataframe.index > 200),
            'enter_long'] = 1
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        # Regime exits -- where is regime data?
        # Must implement regime detection in populate_indicators
        # and pass through dataframe
        return dataframe

    def custom_stoploss(self, pair, trade, current_time, current_rate,
                        current_profit, after_fill, **kwargs):
        # ATR-based stop with protection window
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return -0.99

        atr = dataframe['atr'].iloc[-1]
        trade_duration = (current_time - trade.open_date).total_seconds() / 3600

        # No-stop protection for first 24 bars
        if trade_duration < 24:
            return -0.99

        # 3x ATR stop
        stop_distance = 3.0 * atr / current_rate
        return -stop_distance

    def custom_stake_amount(self, current_time, current_rate, proposed_stake,
                           min_stake, max_stake, leverage, entry_tag,
                           wallet_balance, **kwargs):
        # Kelly sizing -- but we need tier info, edge, etc.
        # Where does tier come from? Must add to config somehow.
        return proposed_stake  # Punt on Kelly sizing
```

This is incomplete (no regime exits, no trailing stop implementation, no Kelly sizing), yet already 2x the lines. A complete port would be 3-4x.

---

## Appendix C: Key Reference Links

- [CCXT GitHub Repository](https://github.com/ccxt/ccxt)
- [CCXT Pro Manual (WebSocket)](https://github.com/ccxt/ccxt/wiki/ccxt.pro.manual)
- [CCXT Multi-Exchange Streaming Example](https://github.com/ccxt/ccxt/blob/master/examples/ccxt.pro/py/many-exchanges-many-streams.py)
- [CCXT Order Book Depth Example](https://github.com/ccxt/ccxt/blob/master/examples/py/order-book-extra-level-depth-param.py)
- [Jesse Supported Exchanges](https://docs.jesse.trade/docs/supported-exchanges/)
- [Jesse Pricing](https://jesse.trade/pricing)
- [Jesse FAQ: My exchange isn't supported](https://jesse.trade/help/faq/my-exchange-isnt-supported-can-i-code-it-myself)
- [Freqtrade Exchange Notes - Kraken](https://www.freqtrade.io/en/stable/exchanges/)
- [Freqtrade Strategy Customization](https://www.freqtrade.io/en/stable/strategy-customization/)
- [Freqtrade Strategy Migration v2 to v3](https://www.freqtrade.io/en/stable/strategy_migration/)
- [Freqtrade Issue #2134: Kraken 720-candle limit](https://github.com/freqtrade/freqtrade/issues/2134)
- [Freqtrade Issue #4449: Kraken data download 2GB+ RAM](https://github.com/freqtrade/freqtrade/issues/4449)
- [Freqtrade Issue #1982: Kraken rate limit exceeded](https://github.com/freqtrade/freqtrade/issues/1982)
- [Freqtrade Issue #12326: Kraken API broken in 2025.9](https://github.com/freqtrade/freqtrade/issues/12326)
- [OctoBot GitHub](https://github.com/Drakkar-Software/OctoBot)
- [OctoBot Script Strategies](https://www.octobot.cloud/en/guides/octobot-script-docs/strategies)
- [Amberdata: Orderbook Slippage Metrics](https://blog.amberdata.io/identifying-crypto-market-trends-using-orderbook-slippage-metrics)
- [CoinAPI: Backtest with Real Market Data](https://www.coinapi.io/blog/backtest-crypto-strategies-with-real-market-data)
- [Paybis: Realistic Fees and Slippage in Backtesting](https://paybis.com/blog/how-to-backtest-crypto-bot/)
