# Jesse.trade Framework Research — Honest Assessment for Kraken Paper Trading

**Researched:** 2026-02-28
**Purpose:** Evaluate Jesse as a paper trading platform for 49–61 Kraken tokens using numpy/pandas-based strategies
**Verdict summary:** Jesse does NOT support Kraken for live/paper trading. The core framework is free but paper trading requires a paid plugin (~$1,600 list price). Freqtrade is a far better fit for this specific use case.

---

## 1. Maintenance Status (2025–2026)

**Verdict: Actively maintained, but be aware of what "active" means.**

Jesse is alive. Latest PyPI release: **v1.13.7, released February 25, 2026**. The jesse-ai GitHub organization had commits as recently as February 25, 2026, including auxiliary tooling (a Monaco Editor / Pyright language server bridge). The main repo has ~7,400 GitHub stars, 1,000+ forks, and a score of 9.6/10 on LibHunt.

However, "active" here means one primary developer (Saleh Mir) plus a small community. This is not a large open-source project with multiple maintainers. The project has had multiple monetization pivots (changed pricing models), which is a yellow flag for long-term sustainability. One dependency — **Tulipy** (Python bindings for Tulip Charts) — is explicitly marked as NOT actively maintained.

The framework added a Rust-accelerated indicator backend (`jesse-rust`) and distributed optimization via Ray+Optuna — these are legitimate improvements. The changelog reflects real ongoing work, not abandonment.

**Bottom line:** Maintained, but single-maintainer risk is real. Plan for occasional breaking changes between versions.

---

## 2. Kraken Exchange Support

**Verdict: NOT officially supported for live or paper trading. Backtesting via historical data possible but limited.**

This is the critical finding for your use case.

### What exists

- **PR #23 (May 2020):** A community contributor added a Kraken candle import driver. It was merged into the core framework.
- **PR #37 (also 2020):** Fixed a fundamental problem — Kraken's API only returns **720 OHLCV candles maximum** at any given interval. For 1-minute candles, that's only 12 hours of history. The fix switched to downloading raw trade data and building candles locally.
- **Candle import for backtesting:** You can download Kraken historical data using Jesse's import candles mode, but it is slower and more memory-intensive than other exchanges. Kraken uses non-standard tickers (`XBT` instead of `BTC`, so `XBTUSD` not `BTCUSD`).

### What does NOT exist

- **No official Kraken live trading driver.** The confirmed live trading exchanges as of early 2026 are: Binance (spot + futures), Binance US, Bybit (spot + futures), Bitget (spot + futures), and DYDX (DEX derivatives).
- **No official Kraken paper trading support.** Paper trading runs through Jesse's "Sandbox" exchange via the live plugin — which requires a supported exchange driver underneath.
- **No Kraken futures driver** of any kind.

Jesse's policy on new exchange drivers: "New exchange drivers are developed based on demand. If Jesse doesn't already support the one you need, you can sponsor its development to expedite the process." There is no current public timeline or roadmap entry for Kraken live trading.

### Kraken's data quirks that affect Jesse backtesting

- 720-candle API limit requires trade-level data download (slow, memory-heavy)
- Non-standard ticker symbols require careful mapping
- Kraken spot markets use different quote currencies (EUR, USD, BTC) that need explicit handling

**Bottom line:** Jesse cannot paper trade on Kraken. Full stop. You can use Jesse to backtest strategies using Kraken historical candle data (with the quirks above), but not to paper trade against live Kraken markets.

---

## 3. Multi-Token Performance: Can Jesse Handle 49–61 Tokens Simultaneously?

**Verdict: Functionally possible for backtesting; live trading at this scale is untested territory and architecturally sequential.**

### Architecture

Jesse uses a "route" model — each route is one (exchange, symbol, timeframe, strategy) combination. You can define 49–61 routes, but internally Jesse processes them **sequentially within its minute-by-minute execution model**, not in parallel.

This means:
- **Backtesting 50 symbols:** Workable. Performance degrades roughly linearly. Expect longer runtimes but no hard failure. The DynamicNumpyArray storage and Rust-optimized indicators help here.
- **Live trading 50 symbols simultaneously:** Jesse has made improvements to support "more live tabs simultaneously without slowing down," but this is untested at 50+ symbols in production. The framework's architecture is not built for massively parallel symbol handling.
- **Memory:** No hard-coded limits on symbols, but each route holds its own candle buffer in memory. At 50 symbols with 1440 1-minute candles each, you're looking at ~50MB of candle data minimum before indicator computation.

### WebSocket limits

Jesse's WebSocket implementation handles connections per exchange, not per symbol. There is no published Jesse-side limit on symbol subscriptions, but:
- Kraken's WebSocket API has connection-level limits (not publicly specified but enforced)
- At 50+ symbols, WebSocket subscription management becomes a real concern
- Jesse has fixed several WebSocket reconnection issues in recent versions (heartbeat mechanism, Redis subscription failures)

**Bottom line:** Backtesting 50 tokens: yes, with patience. Live paper trading 50 tokens simultaneously: theoretically possible on supported exchanges, but Jesse was not designed for this scale and there are no documented real-world cases of it working reliably at 50+ symbols.

---

## 4. Paper Trading Capabilities

**Verdict: Paper trading exists and is reasonably realistic, but requires a paid plugin, and Kraken is not supported.**

### How Jesse paper trading works

Paper trading in Jesse is implemented via a "Sandbox" exchange — a simulated exchange environment that mirrors the behavior of real exchange drivers. It:
- Simulates market order execution at current prices
- Processes limit orders with realistic fills based on price crossing
- Tracks position management and PnL
- Applies fee structures matching the configured exchange

### Order types supported

Jesse supports **market, limit, and stop orders**. It "is smart enough to decide the type of orders by itself" based on how you set entry prices relative to current price. You define:
- `self.buy = qty, price` — limit or market depending on context
- `self.stop_loss = qty, price` — stop order
- `self.take_profit = qty, price` — limit order on the other side
- Multiple take-profit levels via list of tuples: `[(qty/2, price1), (qty/2, price2)]`
- Dynamic updates via `update_position()` method

### Slippage modeling

Jesse's documentation claims it "handles complex scenarios like slippage, fees, and realistic market conditions." However, the specifics of slippage modeling are not publicly documented in detail. Based on code architecture, the Sandbox exchange uses current price at candle close for fills — this is a simplified model. There is no documented order-book-depth-based slippage for high-volume orders.

**Known realism limitation:** Market orders fill at the current candle price, not at bid/ask spread. This will make paper trading results slightly more optimistic than reality, particularly for illiquid tokens.

### The paywall

Paper trading is NOT free. It requires the **jesse-live plugin**, which is a separate closed-source paid product:
- List price: approximately **$1,600 (lifetime license)**
- Black Friday pricing has been as low as **$640**
- A "free version" of the live plugin exists for trial purposes — scope is unclear
- Backtesting and strategy development are fully free

**Critical note from changelog:** A recent fix addressed "an issue where API keys were needed in paper trading mode" — suggesting paper trading previously required real exchange API keys even when simulated. This may still be partially true: paper trading connects to a live exchange for real-time price data while simulating order fills. Without Kraken live trading support, you cannot paper trade against live Kraken prices even with the plugin.

---

## 5. Local Setup Requirements

**Verdict: Moderately complex. Not plug-and-play, but manageable on Linux.**

### Requirements

| Component | Requirement |
|-----------|------------|
| Python | >=3.10, recommended 3.12 via Miniconda |
| PostgreSQL | >11.2 (mandatory, not optional) |
| Redis | Required for WebSocket/dashboard communication |
| OS | Linux, macOS, Windows (Memurai for Redis on Windows) |
| Install | `pip install jesse` |

### Storage and RAM

- PostgreSQL stores all candle data — a full Kraken dataset for 50 symbols across multiple timeframes can consume several GB
- Minimum RAM for a 50-symbol live session: 4–8GB based on community reports
- On a 4-core Linux machine, the optimization mode will use all cores via Ray (note: Ray is excluded for Python 3.13 due to compatibility issues; use 3.12)

### Docker vs native

Jesse supports both. Docker is recommended for isolation but not required. Native setup on Linux requires PostgreSQL and Redis to be installed locally, then `POSTGRES_HOST=localhost` and `REDIS_HOST=localhost` in the `.env` config.

### Effort estimate

For a developer comfortable with Python and Linux: **2–4 hours** to get backtesting running. Add another 1–2 hours for data import and strategy setup. Live/paper trading setup requires the paid plugin and additional exchange API configuration.

### Known setup gotchas

- PostgreSQL version >= 15 requires `ALTER DATABASE jesse_db OWNER TO jesse_user` (easy to miss)
- Candle warmup: insufficient historical data throws `CandleNotFoundInDatabase` — you must pre-import enough candles to satisfy your longest indicator period
- All higher timeframes (4h, 1d) are auto-generated from 1-minute base candles — you must always import 1-minute data

---

## 6. Strategy Interface and numpy/pandas Integration

**Verdict: Integration is feasible but requires mapping your StrategyResult model to Jesse's event-driven interface. Not trivial but doable.**

### Jesse strategy interface

Jesse strategies inherit from a base class and implement these key methods:

```python
class MyStrategy(Strategy):

    def should_long(self) -> bool:
        # return True to signal long entry
        ...

    def should_short(self) -> bool:
        # return True to signal short entry (futures only)
        ...

    def go_long(self):
        qty = ...
        self.buy = qty, entry_price          # limit or market
        self.stop_loss = qty, stop_price     # stop order
        self.take_profit = qty, target_price # limit order

    def go_short(self):
        qty = ...
        self.sell = qty, entry_price
        self.stop_loss = qty, stop_price
        self.take_profit = qty, target_price

    def update_position(self):
        # Called on each candle while a position is open
        # Used for trailing stops, dynamic exits
        ...
```

### Candle data access

Candles are NumPy arrays accessible via `self.candles`:
- `self.candles[:, 0]` — timestamps
- `self.candles[:, 1]` — opens
- `self.candles[:, 2]` — closes
- `self.candles[:, 3]` — highs
- `self.candles[:, 4]` — lows
- `self.candles[:, 5]` — volumes

Custom indicators using numpy are supported — you implement them as Python functions that take `np.ndarray` and return scalar or array values.

### Mapping your StrategyResult to Jesse

Your existing model produces per-symbol signals like:
- `entry_mask` — which tokens to enter
- `stop_mult` — stop distance as ATR multiple
- `trail_mult` — trailing stop distance

**Mapping approach:**

```python
class KrakenMomentumStrategy(Strategy):

    @property
    def my_signals(self):
        # Run your numpy/pandas logic on self.candles
        # Return your StrategyResult equivalent
        closes = self.candles[:, 2]
        # ... your numpy code here ...
        return signals

    def should_long(self) -> bool:
        return bool(self.my_signals.entry_mask)

    def go_long(self):
        atr = ta.atr(self.candles, period=14)
        entry = self.price
        stop = entry - (atr * self.hp['stop_mult'])
        trail_distance = atr * self.hp['trail_mult']
        qty = utils.risk_to_qty(
            self.available_margin,
            self.hp['risk_pct'],
            entry,
            stop,
            fee_rate=self.fee_rate
        )
        self.buy = qty, entry
        self.stop_loss = qty, stop

    def update_position(self):
        # Implement trailing stop using trail_mult
        atr = ta.atr(self.candles, period=14)
        if self.is_long:
            new_stop = self.price - (atr * self.hp['trail_mult'])
            if new_stop > self.average_stop_loss:
                self.stop_loss = self.position.qty, new_stop

    def hyperparameters(self):
        return [
            {'name': 'stop_mult', 'type': float, 'min': 1.0, 'max': 4.0, 'default': 2.0},
            {'name': 'trail_mult', 'type': float, 'min': 1.0, 'max': 4.0, 'default': 2.5},
            {'name': 'risk_pct', 'type': float, 'min': 0.5, 'max': 3.0, 'default': 1.0},
        ]
```

### Key friction points

1. **One strategy instance per symbol per route.** Jesse does not natively run cross-symbol logic (e.g., a portfolio-level entry filter based on correlation across all 50 tokens). You'd need workarounds: shared state via a singleton, or pre-compute signals outside Jesse and load them as files.

2. **Single-timeframe per route.** Each route has one primary timeframe. Multi-timeframe access is possible via `self.get_candles('exchange', 'BTCUSD', '4h')` but adds complexity.

3. **No vectorized portfolio backtesting.** Jesse runs backtests symbol-by-symbol in sequence, not as a joint portfolio simulation. Capital allocation across 50 symbols simultaneously is not natively modeled — each route has its own `available_margin`.

4. **Pandas not used internally.** Jesse uses NumPy arrays throughout. If your existing strategy code is heavily Pandas-based, you will need to convert or wrap it. `pd.DataFrame(self.candles)` works but adds overhead.

---

## 7. Alternatives to Jesse for Local Kraken Paper Trading

### Freqtrade (Recommended for your use case)

**Why it wins for Kraken paper trading:**
- Native, officially supported Kraken integration via CCXT
- Paper trading ("dry-run") is **completely free** — no paid plugin
- Supports 50+ pairs simultaneously; tested at scale by the community
- Strategy interface uses pandas DataFrames natively (better fit for your existing code)
- Active maintenance with 2+ core maintainers; very large community
- Installation: `pip install freqtrade` or Docker, no PostgreSQL required (SQLite by default)
- Known Kraken limitation: 720-candle API limit means using `--dl-trades` for backtesting; RAM-intensive but manageable

**Kraken-specific notes in Freqtrade:**
- Set `rateLimit` to at least 3100ms to avoid Kraken rate limit errors
- Data download via `--dl-trades` is mandatory and RAM-heavy (>2GB for historical data)
- Full Kraken spot support; futures support is partial/experimental

**Strategy interface (pandas-native):**
```python
class MyFreqtradeStrategy(IStrategy):
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Your numpy/pandas code fits naturally here
        dataframe['signal'] = your_numpy_function(dataframe['close'].values)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe['signal'] > 0, 'enter_long'] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # stop loss handled by stoploss = -0.05 or custom_stoploss()
        return dataframe

    stoploss = -0.05  # 5% hard stop

    def custom_stoploss(self, pair, trade, current_time, current_rate,
                        current_profit, **kwargs) -> float:
        # Implement trail_mult logic here
        ...
```

### OctoBot

- Supports Kraken spot via CCXT
- Paper trading free and built-in
- More GUI-focused, less code-centric
- Active (20,000+ users)
- Less suitable for complex numpy/pandas strategies — more template-based

### VectorBT

- Excellent for fast vectorized backtesting of many parameter combinations
- **Not a paper trading bot** — backtesting only
- Needs CCXT layer for live/paper execution
- Good complement to Freqtrade: use VectorBT for research, Freqtrade for execution

### Custom CCXT-based paper trader

- `ccxt.kraken()` with sandbox mode or simulated fills
- Maximum flexibility to plug in your StrategyResult model directly
- Significant engineering effort: must implement position tracking, order simulation, fill logic
- No established community or debugging support
- Appropriate if your strategies are too non-standard for any framework

### Hummingbot

- Strong for market making strategies
- Supports Kraken
- Not well-suited for directional momentum strategies like yours

---

## 8. Known Limitations and Gotchas

### Jesse-specific

| Issue | Severity | Notes |
|-------|----------|-------|
| No Kraken live/paper trading | **Blocking** | The core reason Jesse doesn't fit this use case |
| Paid paper trading plugin | High | ~$1,600 list price; paper trading not free |
| PostgreSQL mandatory | Medium | Adds setup complexity; no SQLite option |
| Ray excluded on Python 3.13 | Low | Use Python 3.12 for full optimization support |
| Sequential multi-symbol processing | Medium | 50 routes means sequential not parallel execution |
| No portfolio-level capital management | Medium | Each route is financially isolated |
| Pandas not native | Low | NumPy-only; minor friction for pandas-heavy codebases |
| Single-maintainer project | Medium | Long-term sustainability risk |
| Warmup candle requirement | Low | Must pre-import sufficient history before running |

### Kraken API limitations (affects ALL frameworks)

| Issue | Severity | Notes |
|-------|----------|-------|
| 720-candle OHLCV API limit | High | Forces trade-level download for backtesting; slow and RAM-heavy |
| Non-standard tickers (XBT not BTC) | Low | Must map `XBTUSD` → `BTC/USD` in code |
| Rate limits (~1 req/sec public) | Medium | Set rate limiters carefully; 50 pairs requires care |
| WebSocket connection limits | Medium | Not published but enforced; 50 symbols is near the edge |
| Kraken data download memory usage | High | >2GB RAM reported for historical data conversion |

### Paper trading realism gap (industry-wide issue, not Jesse-specific)

- Market orders fill at last traded price, not bid/ask mid — results ~0.1–0.3% optimistic per trade
- No queue position modeling for limit orders — limit fills are idealized
- No market impact modeling — 50-token portfolio at realistic sizes would move thin markets
- Fees are modeled, which is good
- Slippage on stops is exchange-dependent — Jesse models it; exact methodology not documented

---

## Summary Recommendation

**Do not use Jesse for Kraken paper trading.** The blocker is fundamental: Kraken is not a supported live trading exchange in Jesse, and paper trading requires the live plugin. No workaround exists short of writing your own Kraken exchange driver (a significant engineering project).

**Use Freqtrade instead.** It natively supports Kraken, paper trading is completely free, it handles 50+ pairs, and its pandas-based strategy interface maps cleanly to your existing numpy/pandas code. The setup overhead is lower (no PostgreSQL required), and the community is large enough that Kraken-specific issues are documented and solved.

**Keep your existing backtest engine.** Your current numpy/pandas backtesting framework is already built for your StrategyResult model. Use it for research. Use Freqtrade only for paper/live execution against Kraken markets.

---

## Sources

- [Jesse GitHub repository](https://github.com/jesse-ai/jesse)
- [Jesse PyPI page](https://pypi.org/project/jesse/) — confirms v1.13.7, Feb 25 2026
- [Jesse supported exchanges docs](https://docs.jesse.trade/docs/supported-exchanges/)
- [Jesse exchange limitations docs](https://docs.jesse.trade/docs/supported-exchanges/exchange-limitations.html)
- [Jesse environment setup docs](https://docs.jesse.trade/docs/getting-started/environment-setup)
- [Jesse strategy API docs](https://docs.jesse.trade/docs/strategies/api.html)
- [Jesse entering/exiting trades docs](https://docs.jesse.trade/docs/strategies/entering-and-exiting.html)
- [Jesse custom indicators docs](https://docs.jesse.trade/docs/indicators/custom-indicators)
- [Jesse pricing page](https://jesse.trade/pricing)
- [Jesse PR #23 — Kraken candle import (merged May 2020)](https://github.com/jesse-ai/jesse/pull/23)
- [Jesse PR #37 — Kraken driver fix for 720-candle limit](https://github.com/jesse-ai/jesse/pull/37)
- [DeepWiki Jesse architecture summary](https://deepwiki.com/jesse-ai/jesse)
- [Gainium Jesse Review](https://gainium.io/blog/jesse)
- [Freqtrade installation docs](https://www.freqtrade.io/en/stable/installation/)
- [Freqtrade exchange-specific notes (Kraken)](https://www.freqtrade.io/en/stable/exchanges/)
- [Freqtrade Kraken data download RAM issue #4449](https://github.com/freqtrade/freqtrade/issues/4449)
- [Kraken WebSocket API FAQ](https://support.kraken.com/articles/360022326871-kraken-websocket-api-frequently-asked-questions)
- [AI comparison: OctoBot, Jesse, Freqtrade (Medium)](https://medium.com/@gwrx2005/ai-integrated-crypto-trading-platforms-a-comparative-analysis-of-octobot-jesse-b921458d9dd6)
