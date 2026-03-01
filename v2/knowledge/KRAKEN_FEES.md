# Kraken Fee Structure — Research Report (February 2026)

> Last updated: 2026-02-28. Fees subject to change. Always verify at
> https://www.kraken.com/features/fee-schedule before going live.

---

## 1. Kraken Pro vs Regular Kraken — Which Interface to Use

**Use Kraken Pro exclusively for algorithmic / API trading.**

| Feature | Regular Kraken | Kraken Pro |
|---|---|---|
| Instant Buy fee | ~1%–1.5% flat + spread | N/A |
| Spot trading fee | 0.40% flat | 0.25% maker / 0.40% taker (base, volume-tiered) |
| Volume discounts | None | Yes — 11 tiers down to 0.00%/0.08% |
| Spread markup | Yes (Instant Buy only) | No spread on Pro limit orders |
| API/algo trading | Limited | Full REST + WebSocket |
| Order types | Basic | Limit, market, stop-loss, take-profit, trailing stops |
| Kraken+ subscription | Waives Instant Buy fees up to $10K/month ($4.99/mo) | No effect on Pro spot/API fees |

**Conclusion:** Regular Kraken's Instant Buy charges 1–1.5% + a non-transparent spread. Kraken Pro is the only viable path for systematic trading. API access is included for all Kraken account holders — no extra subscription required.

---

## 2. Kraken Pro Spot Crypto Fee Tiers (Standard Schedule)

Fees are determined by your **rolling 30-day USD trading volume** on Kraken Pro spot markets. Reassessed after every trade.

| 30-Day Volume | Maker Fee | Taker Fee |
|---|---|---|
| $0 – $9,999 | 0.25% | 0.40% |
| $10,000 – $49,999 | 0.20% | 0.35% |
| $50,000 – $99,999 | 0.14% | 0.24% |
| $100,000 – $249,999 | 0.12% | 0.22% |
| $250,000 – $499,999 | 0.10% | 0.20% |
| $500,000 – $999,999 | 0.08% | 0.18% |
| $1,000,000 – $2,499,999 | 0.06% | 0.16% |
| $2,500,000 – $4,999,999 | 0.04% | 0.14% |
| $5,000,000 – $9,999,999 | 0.02% | 0.12% |
| $10,000,000 – $99,999,999 | 0.00% | 0.10% |
| $100,000,000+ (Institutional) | 0.00% | 0.08% |

Source: [idatco.co/docs — Kraken Fees](https://idatco.co/docs/setting-up-trading-strategies/exchange-fees/kraken-fees/) and [tradingcritique.com — Kraken 2026 fee guide](https://tradingcritique.com/broker-review/kraken-broker-fees-us-traders-guide/)

### What Tier Does $200K Capital Put Us In?

This depends entirely on **monthly turnover, not AUM**. With $200K capital:

- **Low-frequency (e.g., 1-2 trades/position/month):** Monthly volume ~$200K–$400K
  - Tier: $100K–$249,999 = **0.12% maker / 0.22% taker**
  - Or $250K–$499,999 = **0.10% maker / 0.20% taker**
- **Medium-frequency (e.g., daily signals, 5x turnover):** Monthly volume ~$1M
  - Tier: $1,000,000 – $2,499,999 = **0.06% maker / 0.16% taker**
- **High-frequency (10x+ monthly turnover):** $2M+ volume
  - Tier: $2,500,000+ = **0.04% maker / 0.14% taker** and better

**Important caveats:**
- Volume from Instant Buy does NOT count toward 30-day tier calculation.
- Spot and futures volumes are calculated independently — no cross-pollination.
- The 30-day window is rolling; you lose your tier if volume drops.

---

## 3. Stablecoin & FX Pair Fees (Different Schedule)

Applies to: FX pairs (EUR/USD), stablecoins as the **base** currency (USDT/USD, DAI/USDT), and pegged tokens (WBTC/BTC, TBTC/BTC).

Note: If the stablecoin is the **quote** currency only (e.g., BTC/USDT), the standard Spot Crypto fee schedule applies above.

| 30-Day Volume | Maker Fee | Taker Fee |
|---|---|---|
| $0 – $49,999 | 0.20% | 0.20% |
| Higher tiers | Decreasing to 0.00% | Decreasing to 0.001% |
| $100,000,000+ | 0.00% | 0.001% |

This matters if you are routing through USDT/USD pairs for fiat conversion.

---

## 4. Maker Fee Incentive Program (Lower-Liquidity Pairs)

Kraken runs a **maker rebate program on 425+ trading pairs** (as of January 2026) to incentivize liquidity on thin markets. This is directly relevant to tokens like BONK, FLOKI, PENGU, DENT, ZRO, OM.

Key facts:
- Launched June 27, 2025 for select lower-liquidity spot pairs.
- From August 1, 2025 onwards, **all newly listed pairs automatically receive the maker fee incentive schedule** for an initial period.
- On the incentive schedule, maker fees are **lower than standard across all volume tiers**, and at the $10M+ tier, traders can earn **maker fee rebates** (negative maker fees — i.e., you are paid to make liquidity).
- January 2, 2026: 6 pairs that achieved sufficient volume/depth were graduated off the incentive schedule back to the standard one.
- The program is reassessed monthly.
- **BONK, FLOKI, PENGU, DENT, ZRO** are candidates to be on this schedule given their lower liquidity profiles. Verify at: [support.kraken.com/articles/pairs-eligible-for-maker-fee-rebates](https://support.kraken.com/articles/pairs-eligible-for-maker-fee-rebates)

**Practical implication for backtesting:** If your strategy uses limit orders (maker side) on these pairs, your actual maker fees could be lower than the standard schedule suggests — possibly even zero or negative for high-volume accounts.

---

## 5. Fees for Our Specific Token Universe

### Do Different Pairs Have Different Fee Schedules?

**For spot trading on Kraken Pro: No — the same tiered fee schedule applies uniformly across all spot crypto pairs.** BTC/USD and BONK/USD use the same percentage fee at any given volume tier.

The fee schedule differences are:
1. Standard spot crypto (all pairs) — the table in Section 2
2. Stablecoin/FX base pairs — the table in Section 3
3. Maker incentive pairs — lower maker fees for 425+ lower-liquidity pairs

| Token | Available on Kraken | Fee Schedule | Notes |
|---|---|---|---|
| BTC | Yes | Standard spot | High liquidity, tight spreads |
| ETH | Yes | Standard spot | High liquidity, tight spreads |
| SOL | Yes | Standard spot | Good liquidity |
| AVAX | Yes | Standard spot | Good liquidity |
| DOT | Yes | Standard spot | Moderate liquidity |
| SUI | Yes | Standard spot | Moderate liquidity |
| TRX | Yes | Standard spot | Moderate liquidity |
| FIL | Yes | Standard spot | Moderate liquidity |
| OM | Yes | Standard spot | Lower liquidity — likely on maker incentive schedule |
| ZRO | Yes | Standard spot | Lower liquidity — likely on maker incentive schedule |
| BONK | Yes | Standard spot | Low liquidity — likely on maker incentive schedule |
| FLOKI | Likely (confirm) | Standard spot | Low liquidity — Kraken listing needs verification |
| PENGU | Yes | Standard spot | Low liquidity — likely on maker incentive schedule |
| DENT | Yes | Standard spot | Low liquidity — likely on maker incentive schedule |

**The real fee difference between BTC/USD and BONK/USD is not in the fee rate — it is in bid-ask spread and slippage.** BTC spreads on Kraken are typically 0.01%–0.05%. BONK/FLOKI/PENGU spreads can be 0.5%–2%+ during normal conditions and 3%+ during volatility.

---

## 6. Withdrawal Fees

### Crypto Withdrawals
Fees are dynamic (pass-through of network costs). Values as of February 2026:

| Asset | Network | Approx. Withdrawal Fee |
|---|---|---|
| USDT | ERC-20 (Ethereum) | 0.6 USDT |
| USDT | TRC-20 (Tron) | 4 USDT |
| USDT | Solana | 0.9 USDT |
| USDT | Arbitrum One | 2 USDT |
| USDT | Polygon | 1 USDT |
| USDT | Optimism | 2 USDT |
| ETH | Ethereum | ~0.01 ETH |
| SOL | Solana | ~0.02 SOL |
| BTC | Bitcoin | Dynamic (network-based) |

Note: USDT-TRC20 is paradoxically more expensive than ERC-20 on Kraken as of 2026 — verify before routing withdrawals.

### Fiat (USD) Withdrawals

| Method | Fee | Processing Time |
|---|---|---|
| ACH (US) | Free deposit / ~$4 withdrawal | 1–5 business days |
| FedWire | ~$4 flat | 0–1 business days |
| SWIFT | Varies by corridor | 1–5 business days |

### Crypto Deposits
Free. No Kraken deposit fee for any cryptocurrency.

---

## 7. Hidden and Non-Obvious Fees

| Fee Type | Where It Applies | Cost |
|---|---|---|
| Bid-ask spread (Instant Buy) | Instant Buy/Sell only | Variable, Kraken-retained |
| Bid-ask spread (Pro limit orders) | Borne by order placement, not charged by Kraken | Market structure cost |
| Slippage (market orders) | All market/taker orders | Varies by liquidity |
| Margin open fee | Margin positions only | ~0.02% (BTC: 0.01%) |
| Margin rollover fee | Every 4 hours on open margin positions | ~0.02% per 4h (BTC: 0.01%) |
| Liquidation fee | Margin positions reaching liquidation price | Unspecified; avoid |
| Small balance conversion (3%) | Converting dust balances below min order size | 3% fixed |
| Card purchase fee | Debit/credit card buys | ~3.75% + $0.25 |
| Kraken+ subscription | Optional, covers only Instant Buy | $4.99/month or $49.99/year |

**Margin rollover math:** 0.02% every 4 hours = 0.12%/day = ~43.8%/year. Never hold margin positions overnight unless the strategy explicitly models this.

**No overnight/funding fees for spot positions.** Spot is spot — you own the asset, no financing cost from Kraken's side. (Compare: perpetual futures have funding rates.)

**No data/API fees.** WebSocket and REST API access is free for all account holders.

---

## 8. Kraken vs Binance Fee Comparison

| Metric | Kraken Pro | Binance |
|---|---|---|
| Base maker fee (spot) | 0.25% | 0.10% |
| Base taker fee (spot) | 0.40% | 0.10% |
| Lowest maker fee | 0.00% ($10M+ tier) | 0.011% (VIP 9, with BNB) |
| Lowest taker fee | 0.08% ($100M+ institutional) | 0.023% (VIP 9, with BNB) |
| Fee token discount | None | 25% discount with BNB |
| Volume pooling | No (spot/futures separate) | No (separate tier tracking) |
| Futures maker fee | 0.02% | 0.02% |
| Futures taker fee | 0.05% | 0.04% |
| API cost | Free | Free |

### Assessment of the 0.1% + 5bps Slippage Backtest Assumption

**Current backtest assumption:** 0.10% fee + 0.05% slippage = 0.15% total per trade (round-trip: 0.30%)

**Reality check for Kraken:**

| Scenario | Fee (taker) | Slippage | Total per trade |
|---|---|---|---|
| Worst case (base tier, market order, thin pair) | 0.40% | 0.50%–2.0% | 0.90%–2.40% |
| Realistic retail ($100K–$500K monthly vol, limit orders) | 0.18%–0.22% | 0.10%–0.30% | 0.28%–0.52% |
| Good case (limit orders, $1M+ monthly vol, liquid pairs) | 0.12%–0.16% | 0.05%–0.10% | 0.17%–0.26% |
| Binance equivalent (base tier, limit order) | 0.10% | 0.05%–0.10% | 0.15%–0.20% |
| Binance realistic (BNB discount, mid-tier) | 0.075% | 0.05% | 0.125% |

**Verdict:** The 0.10% + 5bps assumption is **calibrated for Binance at base rate, not Kraken.** On Kraken:
- If using **limit orders** and achieving $250K–$500K monthly volume (achievable with $200K capital at moderate turnover), taker fees drop to 0.18%–0.22% and maker fees to 0.08%–0.10%.
- For **small-cap/meme tokens** (BONK, FLOKI, PENGU, DENT), slippage is the dominant cost, not the fee percentage. Market orders on thin pairs can slip 0.5%–3%.
- The 5bps (0.05%) slippage assumption is **dangerously optimistic for small caps.** Conservative estimates: 0.20%–0.50% for BONK/FLOKI sized positions at $4,000/trade.

**Recommended backtest assumption for Kraken (conservative):**
- Liquid pairs (BTC, ETH, SOL, AVAX, DOT): 0.20% fee + 0.05%–0.10% slippage = **0.25%–0.30% per trade**
- Mid-liquidity (SUI, TRX, FIL, ZRO): 0.20%–0.22% fee + 0.10%–0.20% slippage = **0.30%–0.42% per trade**
- Low-liquidity (BONK, FLOKI, PENGU, DENT, OM): 0.22%–0.25% fee + 0.20%–0.50% slippage = **0.42%–0.75% per trade**

If the strategy is profitable at these costs, it is robust enough for live trading.

---

## 9. API and Data Access Costs

| Resource | Cost | Notes |
|---|---|---|
| REST API (public endpoints) | Free | Rate-limited by IP, ~1 req/sec for price data |
| REST API (private endpoints) | Free | Counter-based rate limit, per API key |
| WebSocket v2 (market data) | Free | 200 subscriptions/sec (standard), 500/sec (Pro) |
| WebSocket (order placement) | Free | Shared rate limit across REST + WS + FIX |
| Level 3 order book | Free | Available via WebSocket subscription |
| FIX API | Free (likely) | Enterprise arrangements may vary |
| Historical data | Free via REST | OHLC up to 720 candles per call |

**Rate limit summary:**
- Public endpoints: ~1 request/second per IP without triggering limits
- Private endpoints: Counter-based; Ledger/trades cost 2 points, other calls cost 1 point; counter decays over time
- WebSocket: 200 points/sec (standard user), 500 points/sec (Pro/high-volume user) for Level 3 order book subscriptions
- Trading rate limits are **shared across REST, WebSocket, and FIX** — a single counter per account per currency pair

---

## 10. Minimum Order Sizes

Kraken's minimum order sizes are denominated in the base asset of each pair. The official source is maintained at [support.kraken.com/articles/205893708](https://support.kraken.com/articles/205893708-minimum-order-size-volume-for-trading) and is updated regularly.

The Kraken support article also provides the data as downloadable CSV files for programmatic use (see [support.kraken.com/articles/360042589912](https://support.kraken.com/articles/360042589912-order-minimums-deposit-and-withdrawal-minimums-etc-)).

**Estimated typical minimums for our token universe** (approximate — verify before live trading):

| Token | Approx. Min Order | Approx. USD Value at ~Feb 2026 Prices | Notes |
|---|---|---|---|
| BTC | 0.0001 BTC | ~$10 | Very small minimum |
| ETH | 0.002 ETH | ~$5–6 | Very small minimum |
| SOL | 0.5 SOL | ~$75–100 | Moderate |
| AVAX | 0.1 AVAX | ~$3–5 | Small minimum |
| DOT | 1 DOT | ~$5–7 | Small minimum |
| SUI | 1 SUI | ~$3–5 | Small minimum |
| TRX | 500 TRX | ~$70–100 | Token denominated; check |
| FIL | 0.1 FIL | ~$5 | Small minimum |
| OM | Verify | ~$10–20 est. | Newer listing |
| ZRO | Verify | ~$10–20 est. | Newer listing |
| BONK | 500,000+ BONK | ~$5 est. | Token price ~$0.000006; large nominal qty |
| FLOKI | 10,000+ FLOKI | ~$3–5 est. | Token price ~$0.0002; verify listing availability |
| PENGU | Verify | ~$5–10 est. | Newer listing |
| DENT | 10,000+ DENT | ~$5–10 est. | Low unit price token |

**Important:** With $200K capital spread across 49 tokens, average position size is ~$4,100 per token. This is well above minimum order sizes for all listed tokens. However, for very small position sizes in low-price tokens (BONK, DENT), the minimum quantities may represent non-trivial constraints on position sizing precision.

Fetch live minimums via Kraken REST API:
```
GET https://api.kraken.com/0/public/AssetPairs?pair=BONKUSD,FLOKIUSD,PNGUUSD
```
The `ordermin` field in the response gives the current minimum order size for each pair.

---

## 11. Summary: Key Numbers for Backtesting

| Parameter | Value |
|---|---|
| Standard maker fee (base tier) | 0.25% |
| Standard taker fee (base tier) | 0.40% |
| Realistic maker fee at $200K AUM (~$200K–$500K monthly vol) | 0.08%–0.12% |
| Realistic taker fee at $200K AUM (~$200K–$500K monthly vol) | 0.18%–0.22% |
| Maker fee on incentive pairs (BONK, FLOKI etc.) | Potentially lower than standard, or negative at high volumes |
| Spread markup on Pro limit orders | 0% (Kraken does not markup Pro spot spreads) |
| Slippage — liquid pairs (BTC, ETH, SOL, AVAX) | 0.02%–0.10% typical |
| Slippage — mid-liquidity (SUI, TRX, DOT, FIL, ZRO) | 0.05%–0.20% typical |
| Slippage — low-liquidity (BONK, FLOKI, PENGU, DENT, OM) | 0.20%–1.0%+ (order-size dependent) |
| Overnight / holding fee (spot positions) | 0% (none for spot) |
| API / WebSocket access fee | 0% (free) |
| Recommended conservative backtest fee (all-in) | 0.25%–0.40% per trade for liquid; 0.50%–0.75% for low-liquidity |

**The current 0.10% + 5bps = 0.15% assumption is materially understated for Kraken.** It is approximately calibrated for Binance base tier, or a Kraken user with $10M+ monthly volume. Recommend updating the backtest to use:
- 0.22% taker fee (reflecting ~$200K–$500K monthly volume tier)
- 0.10%–0.30% slippage depending on token liquidity tier
- Separate slippage models for large-cap vs small-cap tokens

---

## Sources

- [Kraken Fee Schedule (official)](https://www.kraken.com/features/fee-schedule)
- [Kraken: Overview of fees](https://support.kraken.com/articles/360030303832-overview-of-fees-on-kraken)
- [Kraken vs Kraken Pro explained](https://www.kraken.com/learn/kraken-vs-kraken-pro)
- [idatco.co — Complete Kraken fee tier table](https://idatco.co/docs/setting-up-trading-strategies/exchange-fees/kraken-fees/)
- [tradingcritique.com — Kraken 2026 fee guide](https://tradingcritique.com/broker-review/kraken-broker-fees-us-traders-guide/)
- [Kraken maker fee program update (Jan 2026)](https://blog.kraken.com/news/maker-fee-program-update-6-pairs)
- [Kraken maker rebates on select spot pairs](https://blog.kraken.com/product/promotions/maker-rebates-select-spot-pairs)
- [Kraken: pairs eligible for maker fee rebates](https://support.kraken.com/articles/pairs-eligible-for-maker-fee-rebates)
- [Kraken minimum order sizes](https://support.kraken.com/articles/205893708-minimum-order-size-volume-for-trading)
- [Kraken order minimums CSV data](https://support.kraken.com/articles/360042589912-order-minimums-deposit-and-withdrawal-minimums-etc-)
- [Kraken cash withdrawal options](https://support.kraken.com/articles/360000423043-cash-withdrawal-options-fees-minimums-and-processing-times-)
- [Kraken API rate limits](https://support.kraken.com/articles/206548367-what-are-the-api-rate-limits-)
- [Kraken API documentation](https://docs.kraken.com/)
- [Kraken vs Binance comparison](https://www.kraken.com/learn/kraken-vs-binance)
- [Paybis — Backtesting with realistic fees and slippage](https://paybis.com/blog/how-to-backtest-crypto-bot/)
- [tradersunion.com — All Kraken Fees February 2026](https://tradersunion.com/brokers/crypto/view/kraken/fees/)
- [cryptoslate.com — Kraken Exchange Review 2026](https://cryptoslate.com/crypto-exchanges/kraken-exchange-review/)
