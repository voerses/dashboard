# The Case FOR Using an Existing Trading Framework

> **TL;DR -- Freqtrade for paper trading**
> - Freqtrade: free, open-source, Kraken + Binance confirmed, paper trading built-in, multi-timeframe native
> - Strategy porting: ~4-8 hours per strategy (NumPy -> Freqtrade Pandas); dual-exchange via two instances
> - Order book slippage estimation via `confirm_trade_entry()` + `fetch_order_book()`
> - Custom CCXT wrapper costs 3-5 weeks; Freqtrade gets to paper trading in 1-2 weeks
> **When to read full file:** Setting up paper trading, porting strategies to Freqtrade, dual-exchange config
> **Sections:** 1-Comparison, 2-Architecture, 3-Order Book, 4-Token Universe, 5-Case for FT, 6-Weaknesses, 7-Migration, 8-Timeline

---

## 1. Framework Comparison

| Framework | Verdict | Key Issue |
|-----------|---------|-----------|
| **Jesse** | Eliminated | No Kraken support; $1,600 license for live/paper; Binance/Bybit/Bitget only |
| **OctoBot** | Eliminated | Kraken balance tracking broken; strategy interface oriented toward pre-built (DCA/Grid), not custom Python |
| **Freqtrade** | Winner | All requirements met (see below) |

### Freqtrade Capabilities

| Requirement | Support |
|---|---|
| Kraken + Binance | Confirmed, exchange-specific docs |
| Paper trading (dry run) | Built-in, no API keys needed |
| Dual-exchange | Two bot instances with separate configs |
| Python strategy interface | Native Python with NumPy/Pandas/TA-Lib |
| Multi-timeframe (1H/4H/Daily) | `@informative` decorator or `informative_pairs()` |
| Custom stoploss (ATR-based) | `custom_stoploss()` + `stoploss_from_absolute()` |
| Custom exit (regime, max hold) | `custom_exit()` callback |
| Fee modeling per exchange | Configurable per instance |
| Order book access | `fetch_order_book()` via CCXT |
| Free & open-source | MIT license, 28K+ GitHub stars |

---

## 2. Freqtrade Architecture for Our Use Case

### Dual-Exchange Paper Trading

```bash
# Instance 1: Kraken
freqtrade trade --config config-kraken.json \
  --strategy MomentumBurstStrategy \
  --db-url sqlite:///kraken_trades.sqlite

# Instance 2: Binance
freqtrade trade --config config-binance.json \
  --strategy MomentumBurstStrategy \
  --db-url sqlite:///binance_trades.sqlite
```

Each instance: own `dry_run_wallet: 200000`, exchange-specific fees, separate DB, separate API port. Kraken uses `/USD` pairs, Binance uses `/USDT` pairs.

### Strategy Port Example (s11 Momentum Burst)

```python
from freqtrade.strategy import IStrategy, informative
import talib.abstract as ta

class MomentumBurstStrategy(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = '1h'
    startup_candle_count = 200
    minimal_roi = {"720": -1}  # Force exit after 30 days
    stoploss = -0.30
    use_custom_stoploss = True

    @informative('4h')
    def populate_indicators_4h(self, dataframe, metadata):
        dataframe['ema_20'] = ta.EMA(dataframe, timeperiod=20)
        return dataframe

    @informative('1d')
    def populate_indicators_1d(self, dataframe, metadata):
        dataframe['ema_50'] = ta.EMA(dataframe, timeperiod=50)
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)
        volatility = dataframe['close'].pct_change().rolling(20).std()
        dataframe['regime'] = np.where(volatility > 0.04, 0,
                              np.where(dataframe['adx'] > 25, 2,
                              np.where(dataframe['adx'] < 15, 1, 3)))
        return dataframe

    def populate_indicators(self, dataframe, metadata):
        """1H indicators -- matches our ind_1h dict."""
        dataframe['ret_1'] = dataframe['close'].pct_change()
        dataframe['adx'] = ta.ADX(dataframe, timeperiod=14)
        dataframe['ema_20'] = ta.EMA(dataframe, timeperiod=20)
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)
        dataframe['vol_ratio'] = dataframe['volume'] / dataframe['volume'].rolling(20).mean()
        return dataframe

    def populate_entry_trend(self, dataframe, metadata):
        dataframe.loc[
            (dataframe['ret_1'] > 0.03) &
            (dataframe['adx'] > 20) &
            (dataframe['close'] > dataframe['ema_20']) &
            (dataframe['vol_ratio'] > 1.0),
            'enter_long'] = 1
        return dataframe

    def custom_stoploss(self, pair, trade, current_time, current_rate,
                        current_profit, after_fill, **kwargs):
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        atr = dataframe.iloc[-1]['atr']
        hours = (current_time - trade.open_date_utc).total_seconds() / 3600
        if hours < 24:
            return -0.99  # No stop first 24h
        from freqtrade.strategy import stoploss_from_absolute
        return stoploss_from_absolute(current_rate - (atr * 3.0), current_rate, is_short=False)

    def custom_exit(self, pair, trade, current_time, current_rate,
                    current_profit, after_fill, **kwargs):
        hours = (current_time - trade.open_date_utc).total_seconds() / 3600
        if hours < 18: return None
        if hours >= 720: return 'max_hold_reached'
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) > 0:
            regime = dataframe.iloc[-1].get('regime_1d', 3)
            if regime in (0, 4): return 'regime_exit'
        return None
```

**Porting effort:** ~4-8 hours per strategy. Core signal logic identical -- just Pandas booleans instead of NumPy arrays. 2 strategies to port = 1-2 days total.

---

## 3. Order Book Data and Slippage Modeling

**Freqtrade provides** L2 order book via CCXT `fetch_order_book()`:
- Entry pricing: configurable `entry_pricing.price_side` (bid/ask/same)
- Strategy callbacks: `confirm_trade_entry()` for liquidity checks
- SlippageFilter: partially implemented feature for pre-entry slippage estimation

```python
def confirm_trade_entry(self, pair, order_type, amount, rate, time_in_force,
                        current_time, entry_tag, side, **kwargs):
    order_book = self.dp.orderbook(pair, maximum=20)
    asks = order_book['asks']
    remaining = amount
    total_cost = 0
    for price, qty in asks:
        fill = min(remaining, qty)
        total_cost += fill * price
        remaining -= fill
        if remaining <= 0: break
    if remaining > 0: return False  # Insufficient liquidity
    slippage = (total_cost / amount - asks[0][0]) / asks[0][0]
    return slippage <= 0.005  # 0.5% max slippage
```

**Limitations (honest):**
- Snapshot-based, not streaming -- adequate for 1H swing trading
- No historical order book replay (same limitation as custom CCXT)
- Dry-run simulates at current price; `confirm_trade_entry()` adds realism

---

## 4. Token Universe

**Kraken 720-candle limit:** Use `--dl-trades` for history download. Live mode accumulates candles normally.

```bash
freqtrade download-data --exchange kraken \
  --pairs SUI/USD BONK/USD FLOKI/USD PENGU/USD ZRO/USD \
  --timeframes 1h 4h 1d --dl-trades --days 365
```

**11 pairs per instance:** Well within limits (issues start at 50-100). ~500MB RAM per instance, negligible CPU at 1H timeframe.

All 11 tokens available on both exchanges (Kraken: `/USD`, Binance: `/USDT`): SUI, BONK, FLOKI, PENGU, ZRO, AVAX, DOT, TRX, FIL, OM, DENT.

---

## 5. Freqtrade vs Custom CCXT

### What Freqtrade Provides Free

| Component | Custom Build Time |
|---|---|
| Paper trading simulation | 2-3 days |
| Trade persistence (DB) | 1-2 days |
| Position sizing & management | 1-2 days |
| Stoploss management (trailing, custom) | 2-3 days |
| Fee handling (auto-fetched) | 0.5 days |
| Multi-timeframe alignment | 1-2 days |
| Telegram + Web UI | 4-6 days |
| Exchange error handling & reconnection | 1-2 days |
| Rate limiting compliance | 0.5 days |
| **Total custom build** | **15-25 days** |

**Key arguments:**
- Strategies are the easy part (60 lines). Infrastructure is the hard part: data feeds, order lifecycle, state persistence, multi-TF sync, exchange quirks.
- 28K+ stars, 8 years of production use, hundreds of contributors. Exchange bugs already found and fixed.
- $200K capital deployment -- trust proven infrastructure, focus on evaluating strategy performance.

### Known Weaknesses

| Weakness | Severity | Mitigation |
|---|---|---|
| Opinionated callback interface | Low | Entry logic maps directly; only exits need callbacks |
| Backtester differs from our engine | N/A | We keep our engine for backtesting; FT for live only |
| Two instances for dual-exchange | Low | Independent processes = reliability advantage; ~1GB RAM |
| Kraken needs `--dl-trades` | Low | Initial download only; live mode works normally |
| Order book snapshot-based | Low | 1H swing trading doesn't need streaming |

---

## 6. Paper to Live Migration

```json
// Change one line to go live:
"dry_run": false
```

Zero-code migration. Production deployment via Docker with auto-restart. Built-in monitoring: Telegram bot, FreqUI web dashboard, API endpoints.

---

## 7. Timeline Comparison

| Path | Paper Trading Starts | Live Ready |
|---|---|---|
| **Freqtrade** | Week 2 | Week 7-8 |
| **Custom CCXT** | Week 7-8 | Week 13-14 |

**Freqtrade gets to live trading 4-6 weeks sooner.**

---

## 8. Conclusion

**Recommendation: Start with Freqtrade.** If we hit a hard limitation during paper trading, build targeted custom code for that specific component. Don't pre-optimize for problems we haven't encountered.

---

## Sources

### Freqtrade
- [GitHub](https://github.com/freqtrade/freqtrade) | [Exchanges](https://www.freqtrade.io/en/stable/exchanges/) | [Strategy Customization](https://www.freqtrade.io/en/stable/strategy-customization/)
- [Callbacks](https://www.freqtrade.io/en/stable/strategy-callbacks/) | [Stoploss](https://www.freqtrade.io/en/stable/stoploss/) | [Config](https://www.freqtrade.io/en/stable/configuration/)
- [Producer/Consumer](https://www.freqtrade.io/en/stable/producer-consumer/) | [Order Book #11082](https://github.com/freqtrade/freqtrade/issues/11082) | [Slippage #3524](https://github.com/freqtrade/freqtrade/issues/3524)

### Alternatives (Eliminated)
- Jesse: [Exchanges](https://docs.jesse.trade/docs/supported-exchanges/) | [Pricing](https://jesse.trade/pricing)
- OctoBot: [Kraken Guide](https://www.octobot.cloud/en/guides/octobot-supported-exchanges/kraken) | [GitHub](https://github.com/Drakkar-Software/OctoBot)
