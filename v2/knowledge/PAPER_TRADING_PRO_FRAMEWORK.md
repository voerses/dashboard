# The Case FOR Using an Existing Trading Framework

## Advocate Position: Use Freqtrade for Paper Trading

> **TL;DR:** Freqtrade is the clear winner among existing frameworks for our use case. It is free, open-source, confirmed working on both Kraken and Binance, supports dry-run (paper trading) out of the box, handles multi-timeframe strategies natively, and our Python/NumPy strategies can be ported in 1-2 days per strategy. The alternative -- building a custom CCXT wrapper -- means spending 3-6 weeks reinventing what Freqtrade already provides, battle-tested by thousands of users.

---

## 1. Framework Comparison: Why Freqtrade Wins

### Jesse -- Eliminated

Jesse is an elegant framework with a clean strategy interface, but it fails our most critical requirement: **Kraken is NOT a supported exchange**. Jesse's confirmed exchanges are Binance, Bybit, Bitget, DYDX, Coinbase, and Apex Pro. Kraken is absent from their supported exchanges list, and adding it would require sponsoring development of a new exchange driver -- timeline unknown.

Additionally, Jesse requires a **$1,600 lifetime license** for live/paper trading (the open-source version is backtesting-only). Given that Freqtrade provides equivalent or superior functionality for free, Jesse offers no compelling advantage.

**Sources:**
- [Jesse Supported Exchanges](https://docs.jesse.trade/docs/supported-exchanges/)
- [Jesse Exchange Limitations](https://docs.jesse.trade/docs/supported-exchanges/exchange-limitations.html)
- [Jesse Pricing](https://jesse.trade/pricing)

### OctoBot -- Eliminated

OctoBot supports both Kraken and Binance and can run multiple exchanges simultaneously. However, it has a critical limitation for Kraken: **"Kraken is not providing free and used data for account balance. OctoBot won't be able to manage a real portfolio correctly."** This makes Kraken paper trading unreliable in OctoBot.

More importantly, OctoBot's strategy interface is oriented toward pre-built strategies (DCA, Grid, TradingView signals) rather than custom Python indicator logic. Porting our NumPy-based ADX/EMA/momentum strategies into OctoBot's framework would require significantly more adaptation than Freqtrade.

**Sources:**
- [OctoBot Kraken Guide](https://www.octobot.cloud/en/guides/octobot-supported-exchanges/kraken)
- [OctoBot Exchanges Overview](https://www.octobot.cloud/en/guides/exchanges)
- [OctoBot GitHub](https://github.com/Drakkar-Software/OctoBot)

### Freqtrade -- The Winner

Freqtrade meets every requirement:

| Requirement | Freqtrade Support |
|---|---|
| Kraken exchange | Confirmed, with exchange-specific notes in docs |
| Binance exchange | Confirmed, first-class support |
| Paper trading (dry run) | Built-in, no API keys needed for simulation |
| Simultaneous dual-exchange | Run two bot instances with separate configs |
| Python strategy interface | Native Python with NumPy/Pandas/TA-Lib |
| Multi-timeframe (1H/4H/Daily) | `@informative` decorator or `informative_pairs()` |
| Custom stoploss (ATR-based) | `custom_stoploss()` callback with `stoploss_from_absolute()` |
| Custom exit logic (regime, max hold) | `custom_exit()` callback |
| Fee modeling per exchange | Configurable per instance |
| Order book data access | `fetch_order_book()` via CCXT integration |
| WebSocket data | Supported via CCXT Pro integration |
| Free & open-source | Yes, MIT license, 28K+ GitHub stars |

**Sources:**
- [Freqtrade Exchange Notes](https://www.freqtrade.io/en/stable/exchanges/)
- [Freqtrade Strategy Customization](https://www.freqtrade.io/en/stable/strategy-customization/)
- [Freqtrade Strategy Callbacks](https://www.freqtrade.io/en/stable/strategy-callbacks/)
- [Freqtrade Configuration](https://www.freqtrade.io/en/stable/configuration/)

---

## 2. Freqtrade Architecture for Our Use Case

### Dual-Exchange Paper Trading Setup

Run two independent Freqtrade instances, each with its own configuration:

```bash
# Instance 1: Kraken paper trading
freqtrade trade --config config-kraken.json \
  --strategy MomentumBurstStrategy \
  --db-url sqlite:///kraken_trades.sqlite

# Instance 2: Binance paper trading
freqtrade trade --config config-binance.json \
  --strategy MomentumBurstStrategy \
  --db-url sqlite:///binance_trades.sqlite
```

Each instance gets:
- Its own `dry_run_wallet: 200000` (matching our $200K capital)
- Exchange-specific fee configuration
- Separate database for trade logging
- Separate API port for web UI monitoring

This is a **documented, supported workflow** -- not a hack. Freqtrade's architecture explicitly supports multiple instances with different configs.

### Fee Configuration Per Exchange

```json
// config-kraken.json
{
  "exchange": {
    "name": "kraken",
    "pair_whitelist": ["SUI/USD", "BONK/USD", "FLOKI/USD", "PENGU/USD", "ZRO/USD",
                       "AVAX/USD", "DOT/USD", "TRX/USD", "FIL/USD", "OM/USD", "DENT/USD"]
  },
  "dry_run": true,
  "dry_run_wallet": 200000,
  "trading_mode": "spot"
}

// config-binance.json
{
  "exchange": {
    "name": "binance",
    "pair_whitelist": ["SUI/USDT", "BONK/USDT", "FLOKI/USDT", "PENGU/USDT", "ZRO/USDT",
                       "AVAX/USDT", "DOT/USDT", "TRX/USDT", "FIL/USDT", "OM/USDT", "DENT/USDT"]
  },
  "dry_run": true,
  "dry_run_wallet": 200000,
  "trading_mode": "spot"
}
```

Kraken uses `/USD` pairs, Binance uses `/USDT` pairs. Freqtrade handles this natively.

### Multi-Timeframe Strategy with @informative Decorator

Freqtrade's `@informative` decorator maps almost perfectly to our `StrategyContext` pattern:

```python
from freqtrade.strategy import IStrategy, informative
import numpy as np
import talib.abstract as ta

class MomentumBurstStrategy(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = '1h'
    startup_candle_count = 200

    # Maps to our min_hold=18, max_hold=720
    minimal_roi = {"720": -1}  # Force exit after 720 bars (30 days)
    stoploss = -0.30  # Fallback stoploss (overridden by custom)
    use_custom_stoploss = True

    @informative('4h')
    def populate_indicators_4h(self, dataframe, metadata):
        dataframe['ema_20'] = ta.EMA(dataframe, timeperiod=20)
        return dataframe

    @informative('1d')
    def populate_indicators_1d(self, dataframe, metadata):
        dataframe['ema_50'] = ta.EMA(dataframe, timeperiod=50)
        # Regime detection
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)
        volatility = dataframe['close'].pct_change().rolling(20).std()
        dataframe['regime'] = np.where(volatility > 0.04, 0,  # CRISIS
                              np.where(dataframe['adx'] > 25, 2,  # UPTREND
                              np.where(dataframe['adx'] < 15, 1,  # QUIET
                              3)))  # RANGE
        return dataframe

    def populate_indicators(self, dataframe, metadata):
        """1H indicators -- matches our ind_1h dict."""
        dataframe['ret_1'] = dataframe['close'].pct_change()
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)
        dataframe['plus_di'] = ta.PLUS_DI(dataframe, timeperiod=14)
        dataframe['minus_di'] = ta.MINUS_DI(dataframe, timeperiod=14)
        dataframe['ema_10'] = ta.EMA(dataframe, timeperiod=10)
        dataframe['ema_20'] = ta.EMA(dataframe, timeperiod=20)
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)
        dataframe['vol_ratio'] = dataframe['volume'] / dataframe['volume'].rolling(20).mean()
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        """Momentum Burst entry -- direct port from s11_momentum_burst.py"""
        dataframe.loc[
            (dataframe['ret_1'] > 0.03) &          # 3% hourly move
            (dataframe['adx'] > 20) &               # Trend present
            (dataframe['close'] > dataframe['ema_20']) &  # Above EMA20
            (dataframe['vol_ratio'] > 1.0),          # Above-average volume
            'enter_long'] = 1
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        """Basic exit signals (custom_exit handles the complex logic)."""
        return dataframe

    def custom_stoploss(self, pair, trade, current_time, current_rate,
                        current_profit, after_fill, **kwargs):
        """ATR-based trailing stop -- maps to our stop_mult=3.0, trail_mult=3.0"""
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        last_candle = dataframe.iloc[-1]
        atr = last_candle['atr']

        # No-stop protection for first 24 bars
        trade_duration_hours = (current_time - trade.open_date_utc).total_seconds() / 3600
        if trade_duration_hours < 24:
            return -0.99  # Effectively no stop

        # 3x ATR trailing stop
        from freqtrade.strategy import stoploss_from_absolute
        stop_price = current_rate - (atr * 3.0)
        return stoploss_from_absolute(stop_price, current_rate, is_short=False)

    def custom_exit(self, pair, trade, current_time, current_rate,
                    current_profit, after_fill, **kwargs):
        """Regime-based exit + min/max hold enforcement."""
        trade_duration_hours = (current_time - trade.open_date_utc).total_seconds() / 3600

        # Min hold: 18 hours
        if trade_duration_hours < 18:
            return None

        # Max hold: 720 hours (30 days)
        if trade_duration_hours >= 720:
            return 'max_hold_reached'

        # Regime exit: exit in CRISIS (0) or DOWNTREND (4)
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) > 0:
            last_candle = dataframe.iloc[-1]
            regime = last_candle.get('regime_1d', 3)  # Default to RANGE
            if regime in (0, 4):  # CRISIS or DOWNTREND
                return 'regime_exit'

        return None
```

### What This Demonstrates

The porting effort is **straightforward**. Our `s11_momentum_burst.py` is 60 lines. The Freqtrade equivalent above is ~100 lines and includes all the infrastructure (multi-timeframe, custom stop, regime exit, min/max hold) that Freqtrade provides as callbacks. The core signal logic (`ret_1 > 0.03 & adx > 20 & close > ema20 & vol_ratio > 1.0`) is **identical** -- just expressed as Pandas boolean conditions instead of NumPy boolean arrays. They are functionally equivalent.

**Estimated porting time: 4-8 hours per strategy.** We have 2 strategies to port (S11 Momentum Burst and S09 Optimized Trend). Total: 1-2 days.

---

## 3. Order Book Data and Slippage Modeling

### What Freqtrade Provides

Freqtrade accesses L2 order book data via CCXT's `fetch_order_book()`. This is available in:

1. **Entry pricing**: Configure `entry_pricing.price_side` to use bid/ask/same prices from the order book.
2. **Strategy callbacks**: Access order book data within `confirm_trade_entry()` to reject entries with insufficient liquidity.
3. **SlippageFilter**: A proposed (and partially implemented) feature that calculates estimated slippage against order book depth before allowing entries.

### Slippage Estimation in `confirm_trade_entry()`

```python
def confirm_trade_entry(self, pair, order_type, amount, rate, time_in_force,
                        current_time, entry_tag, side, **kwargs):
    """Reject entries where order book depth suggests excessive slippage."""
    order_book = self.dp.orderbook(pair, maximum=20)
    asks = order_book['asks']

    # Calculate how deep we need to go to fill our position
    remaining = amount
    total_cost = 0
    for price, qty in asks:
        fill = min(remaining, qty)
        total_cost += fill * price
        remaining -= fill
        if remaining <= 0:
            break

    if remaining > 0:
        return False  # Not enough liquidity in top 20 levels

    avg_fill_price = total_cost / amount
    slippage = (avg_fill_price - asks[0][0]) / asks[0][0]

    # Reject if slippage exceeds threshold (e.g., 0.5% for low-liq tokens)
    max_slippage = 0.005  # Configurable per pair
    return slippage <= max_slippage
```

This gives us **real-time order book-based slippage estimation** during paper trading -- something that would take days to build from scratch with CCXT.

### Honest Assessment of Limitations

Freqtrade's order book integration is not as deep as a fully custom system could provide. Specifically:

- **No continuous order book streaming**: Freqtrade fetches order book snapshots on demand, not via WebSocket subscription. For our 1H timeframe swing trading, this is perfectly adequate -- we don't need tick-level order book data.
- **No historical order book replay**: Slippage modeling in backtests uses a 5% cap, not actual order book history. This is a weakness, but it's a weakness shared by ALL frameworks -- historical order book data simply isn't available from exchange APIs.
- **The real slippage answer for paper trading**: During dry-run, Freqtrade simulates fills at the current price. To get realistic slippage in paper trading, we use `confirm_trade_entry()` to check order book depth at entry time. This is good enough for swing trading where we enter once per day, not hundreds of times per day.

**The custom CCXT wrapper would face the exact same limitations.** The order book snapshot approach is identical -- CCXT provides `fetch_order_book()` whether you use it directly or through Freqtrade.

---

## 4. Handling Our Specific Token Universe

### Kraken API Limitation: 720 Candles

Kraken's API returns a maximum of 720 candles per request. For our 1H timeframe, that is 30 days -- insufficient for strategy initialization (we need 200+ candles for EMA calculations).

**Freqtrade handles this.** The documentation explicitly addresses this limitation: use `--dl-trades` to download raw trade data and reconstruct candles locally. For live/dry-run mode, Freqtrade accumulates candles over time and stores them, so after the initial warm-up period, it has all the data it needs.

```bash
# Download Kraken data for backtesting (works around 720-candle limit)
freqtrade download-data --exchange kraken \
  --pairs SUI/USD BONK/USD FLOKI/USD PENGU/USD ZRO/USD \
  --timeframes 1h 4h 1d \
  --dl-trades \
  --days 365
```

### WebSocket Performance for 11 Pairs

Our validated token set is 11 pairs (SUI, BONK, FLOKI, PENGU, ZRO, AVAX, DOT, TRX, FIL, OM, DENT). Freqtrade handles this comfortably:

- **11 pairs is well within limits.** Users report issues above ~50-100 pairs. Our 11 pairs per instance is trivial.
- **Memory footprint**: With 11 pairs on a 1H timeframe plus 4H and daily informative pairs, expect ~200-500MB RAM per instance. Two instances (Kraken + Binance) = ~1GB total. Any modern machine handles this.
- **CPU**: 1H timeframe means indicator recalculation once per hour. Even on a low-power VPS, this is negligible.

### Token Availability

All 11 tokens in our validated set are available on both Kraken and Binance:

| Token | Kraken | Binance |
|---|---|---|
| SUI | SUI/USD | SUI/USDT |
| BONK | BONK/USD | BONK/USDT |
| FLOKI | FLOKI/USD (verify) | FLOKI/USDT |
| PENGU | PENGU/USD | PENGU/USDT |
| ZRO | ZRO/USD | ZRO/USDT |
| AVAX | AVAX/USD | AVAX/USDT |
| DOT | DOT/USD | DOT/USDT |
| TRX | TRX/USD | TRX/USDT |
| FIL | FIL/USD | FIL/USDT |
| OM | OM/USD | OM/USDT |
| DENT | DENT/USD | DENT/USDT |

---

## 5. The Strongest Case: Why Freqtrade Over Custom CCXT

### What You Get for Free with Freqtrade

Building a custom CCXT wrapper means reimplementing all of the following from scratch:

| Component | Freqtrade (Free) | Custom Build (Time Estimate) |
|---|---|---|
| Paper trading simulation | Built-in `dry_run` mode | 2-3 days |
| Trade persistence (database) | SQLite/PostgreSQL built-in | 1-2 days |
| Position sizing & management | Configurable, multiple methods | 1-2 days |
| Stoploss management (trailing, custom) | Callbacks + exchange stoploss | 2-3 days |
| Fee handling (maker/taker, per exchange) | Auto-fetched from exchange | 0.5 days |
| Order timeout management | `unfilledtimeout` config | 0.5 days |
| Multi-timeframe data alignment | `@informative` decorator | 1-2 days |
| Telegram notifications | Built-in integration | 1 day |
| Web UI dashboard | FreqUI built-in | 3-5 days |
| Trade logging & analysis | Built-in + export tools | 1-2 days |
| Backtesting engine | Built-in (but we have our own) | N/A |
| Hyperparameter optimization | Hyperopt built-in | N/A |
| Exchange error handling & reconnection | CCXT + Freqtrade retry logic | 1-2 days |
| Rate limiting compliance | Built-in per exchange | 0.5 days |
| Edge/risk management | Built-in Edge module | 1 day |
| **Total estimated custom build time** | **0 days** | **15-25 days** |

That is **3-5 weeks of development** before you can even start paper trading. With Freqtrade, you can start paper trading in **2-3 days** (time to port strategies + configure).

### The "Our Strategies Are Already in Python" Argument

The opposing argument will claim: "Our strategies are already written in Python with NumPy. Why not just wrap them with CCXT?"

The answer: **the strategies are the easy part.** Our `s11_momentum_burst.py` is 60 lines. The hard part is everything around the strategies:

1. **Reliable data feeds**: Handling WebSocket disconnections, API rate limits, candle gaps, exchange maintenance windows.
2. **Order lifecycle management**: Submitting orders, tracking partial fills, handling rejections, managing order timeouts.
3. **State persistence**: What happens when your process crashes? Custom code loses in-flight trades. Freqtrade recovers from its database.
4. **Multi-timeframe synchronization**: Our strategies use 1H, 4H, and daily data. Aligning these in real-time with proper forward-fill logic is non-trivial.
5. **Exchange-specific quirks**: Kraken and Binance have different API behaviors, rate limits, error codes, and pair naming conventions. Freqtrade has years of community knowledge baked into its exchange-specific handling.

### Battle-Tested at Scale

Freqtrade has 28,000+ GitHub stars, hundreds of active contributors, and has been used in production since 2017. The exchange integration layer has been debugged by thousands of users across dozens of exchanges over 8+ years.

A custom CCXT wrapper will encounter the same bugs -- WebSocket drops, API changes, rate limit edge cases -- but you will be the first person to discover and fix each one. Freqtrade has already found and fixed them.

### Risk Mitigation

We are deploying $200K of real capital (eventually). The paper trading phase exists to build confidence before going live. Using a framework that thousands of people have already used for live trading with real money reduces the risk that our paper trading results are artifacts of our own code bugs.

With a custom wrapper, every anomalous paper trading result raises the question: "Is this a real market phenomenon, or a bug in my custom code?" With Freqtrade, you can be confident the infrastructure is correct and focus on evaluating strategy performance.

---

## 6. Addressing Known Weaknesses (Honestly)

### Weakness 1: Freqtrade's Strategy Interface Is Opinionated

**True.** Freqtrade enforces a specific callback structure. Our vectorized NumPy approach needs to be adapted to Freqtrade's row-by-row callback pattern for exits.

**Why it's manageable:** Our entry logic is vectorized and maps directly to `populate_entry_trend()`. Only the exit logic (custom stoploss, regime detection) needs the callback pattern, and Freqtrade's `custom_stoploss()` and `custom_exit()` callbacks are specifically designed for this. The porting is mechanical, not conceptual.

### Weakness 2: Backtesting Behavior May Differ from Our Engine

**True.** Freqtrade's backtester processes candles differently from our Numba-JIT simulation. Results will not match exactly.

**Why it's manageable:** We are not replacing our backtesting engine. We keep our existing backtest for strategy development and validation. Freqtrade is used ONLY for paper trading and eventual live trading. The backtest engine and live engine are intentionally separate in any professional setup.

### Weakness 3: Two Bot Instances for Dual-Exchange

**True.** Running Kraken and Binance simultaneously requires two separate Freqtrade processes.

**Why it's manageable:** Two processes sharing the same strategy code is simpler than one process managing two exchange connections. Each instance is independent -- if the Kraken instance crashes, the Binance instance continues. This is actually a reliability advantage. Resource usage for two instances monitoring 11 pairs each on 1H timeframe is negligible (~1GB RAM, <1% CPU).

### Weakness 4: Kraken Data Download Requires --dl-trades

**True.** Due to the 720-candle API limit, downloading Kraken history for backtesting requires fetching individual trades and aggregating them, which is slower.

**Why it's manageable:** This only affects initial data download, not live/paper trading. Once running, Freqtrade accumulates candles normally. For our paper trading phase, we only need real-time data -- historical data comes from our existing backtest infrastructure.

### Weakness 5: Order Book Integration Is Snapshot-Based, Not Streaming

**True.** Freqtrade fetches order book snapshots on demand, not via continuous WebSocket streams.

**Why it's manageable:** We trade on a 1H timeframe. We enter maybe 5-10 positions per day across 11 tokens. Fetching an order book snapshot before each entry decision is perfectly adequate. Continuous order book streaming is useful for market-making or HFT, not for swing trading. A custom CCXT wrapper would face the exact same constraint unless we invest additional engineering in WebSocket order book streams -- which is overkill for our use case.

---

## 7. Migration Path: Paper Trading to Live

One of the strongest arguments for Freqtrade is the **zero-code migration from paper to live trading**:

```json
// Change one line to go live:
"dry_run": false
```

All the same strategies, configurations, stoploss logic, and exit conditions carry over. This is not true of a custom wrapper, where the paper trading simulation and live execution are necessarily different code paths that must be separately tested and validated.

### Production Deployment

Freqtrade is designed for long-running deployment:

```bash
# Docker deployment (recommended for production)
docker-compose up -d  # Runs in background, auto-restarts
```

Built-in monitoring via:
- **Telegram bot**: Real-time trade notifications, portfolio status, manual commands
- **FreqUI**: Web dashboard with trade history, profit charts, open position management
- **API endpoints**: Programmatic access for custom dashboards

---

## 8. Timeline Comparison

### Freqtrade Path
| Week | Activity |
|---|---|
| Week 1 | Port S11 + S09 strategies, configure dual-exchange, validate entries |
| Week 2 | Begin paper trading on both Kraken and Binance |
| Week 3-6 | Collect paper trading data, compare exchanges |
| Week 7-8 | Analyze results, decide Kraken vs Binance, prepare for live |

### Custom CCXT Path
| Week | Activity |
|---|---|
| Week 1-2 | Build core trading loop, order management, state persistence |
| Week 3 | Build multi-timeframe data pipeline, exchange-specific handlers |
| Week 4 | Build paper trading simulation, fee/slippage modeling |
| Week 5 | Build monitoring/alerting, Telegram/dashboard integration |
| Week 6 | Debug, test, fix exchange-specific edge cases |
| Week 7-8 | Begin paper trading (4-6 weeks later than Freqtrade) |
| Week 9-12 | Collect paper trading data |
| Week 13-14 | Analyze results, prepare for live |

**The Freqtrade path gets us to live trading 4-6 weeks sooner.**

---

## 9. Conclusion

Freqtrade is not a perfect framework. It is opinionated, its backtester is not as sophisticated as our custom engine, and running dual-exchange requires two instances. But these are minor inconveniences compared to the enormous value it provides:

1. **Proven infrastructure**: 8 years of production use, 28K+ stars, active maintenance
2. **Both exchanges confirmed**: Kraken and Binance with exchange-specific handling
3. **Zero cost**: Free, open-source, no license fees
4. **Fast time-to-paper-trading**: 1-2 weeks vs 5-6 weeks for custom
5. **Trivial strategy porting**: Our NumPy logic maps directly to Freqtrade's Pandas interface
6. **Zero-code paper-to-live migration**: Change one configuration flag
7. **Built-in monitoring**: Telegram, Web UI, API -- no custom dashboards needed
8. **Risk reduction**: Trust the infrastructure, focus on evaluating strategy performance

The question is not whether Freqtrade can do what we need -- it clearly can. The question is whether the marginal flexibility of a custom wrapper justifies 4-6 weeks of additional development time and the ongoing maintenance burden. For a $200K swing trading system that trades ~2,500 times per year on a 1H timeframe, it does not.

**Recommendation: Start with Freqtrade. If we hit a hard limitation during paper trading that cannot be worked around, THEN consider targeted custom code for that specific component. Don't pre-optimize for problems we haven't encountered yet.**

---

## Sources

### Freqtrade
- [Freqtrade GitHub](https://github.com/freqtrade/freqtrade)
- [Exchange-Specific Notes](https://www.freqtrade.io/en/stable/exchanges/)
- [Strategy Customization](https://www.freqtrade.io/en/stable/strategy-customization/)
- [Strategy Callbacks](https://www.freqtrade.io/en/stable/strategy-callbacks/)
- [Stoploss Documentation](https://www.freqtrade.io/en/stable/stoploss/)
- [Configuration](https://www.freqtrade.io/en/stable/configuration/)
- [Producer/Consumer Mode](https://www.freqtrade.io/en/stable/producer-consumer/)
- [Order Book Issue #11082](https://github.com/freqtrade/freqtrade/issues/11082)
- [SlippageFilter Issue #3524](https://github.com/freqtrade/freqtrade/issues/3524)
- [Multi-Timeframe Tutorial](https://dev.to/henry_lin_3ac6363747f45b4/lesson-27-freqtrade-multi-timeframe-strategies-n03)

### Jesse
- [Jesse Official Site](https://jesse.trade/)
- [Jesse Supported Exchanges](https://docs.jesse.trade/docs/supported-exchanges/)
- [Jesse Exchange Limitations](https://docs.jesse.trade/docs/supported-exchanges/exchange-limitations.html)
- [Jesse Custom Indicators](https://docs.jesse.trade/docs/indicators/custom-indicators.html)

### OctoBot
- [OctoBot GitHub](https://github.com/Drakkar-Software/OctoBot)
- [OctoBot Kraken Guide](https://www.octobot.cloud/en/guides/octobot-supported-exchanges/kraken)
- [OctoBot Binance Guide](https://www.octobot.cloud/en/guides/octobot-partner-exchanges/binance)

### Comparative Analysis
- [AI-Integrated Crypto Trading Platforms Comparison](https://medium.com/@gwrx2005/ai-integrated-crypto-trading-platforms-a-comparative-analysis-of-octobot-jesse-b921458d9dd6)
- [7 Best Crypto Trading Bots Compared 2025](https://www.quantoshi.com/reports/best-crypto-trading-bots-compared-2025-ultimate-guide)
