# Kraken Fee Structure -- Research Report (February 2026)

> **TL;DR -- Exchange costs for backtesting**
> - At $200K AUM (~$200-500K monthly vol): 0.08-0.12% maker, 0.18-0.22% taker
> - Engine defaults: Tier 1 = 0.22% fee + 8bps slip, Tier 2 = 0.22% + 15bps, Tier 3 = 0.25% + 35bps
> - Old 0.10% + 5bps assumption was Binance base rate -- 2-3x too low for Kraken
> - Maker incentive on 425+ pairs (BONK, FLOKI, PENGU): potentially lower/negative maker fees
> **When to read full file:** Going live on Kraken, modeling slippage for small-caps, comparing Kraken vs Binance
> **Sections:** 1-Pro vs Regular, 2-Fee Tiers, 3-Stablecoin, 4-Maker Incentive, 5-Token Universe, 6-Withdrawals, 7-Hidden Fees, 8-Kraken vs Binance, 9-API, 10-Minimums, 11-Summary

> Last updated: 2026-02-28. Verify at https://www.kraken.com/features/fee-schedule before going live.

---

## 1. Kraken Pro vs Regular

**Use Kraken Pro exclusively.** Regular Kraken charges 1-1.5% + spread on Instant Buy. Kraken Pro: volume-tiered fees, full API, no spread markup on limit orders. API access free for all accounts.

---

## 2. Kraken Pro Spot Fee Tiers

Rolling 30-day USD volume, reassessed after every trade:

| 30-Day Volume | Maker | Taker |
|---|---|---|
| $0 - $9,999 | 0.25% | 0.40% |
| $10K - $49,999 | 0.20% | 0.35% |
| $50K - $99,999 | 0.14% | 0.24% |
| **$100K - $249,999** | **0.12%** | **0.22%** |
| **$250K - $499,999** | **0.10%** | **0.20%** |
| $500K - $999,999 | 0.08% | 0.18% |
| $1M - $2.5M | 0.06% | 0.16% |
| $2.5M - $5M | 0.04% | 0.14% |
| $5M - $10M | 0.02% | 0.12% |
| $10M - $100M | 0.00% | 0.10% |
| $100M+ | 0.00% | 0.08% |

**Our tier at $200K capital:** ~$200K-500K monthly volume = **0.08-0.12% maker / 0.18-0.22% taker**. Volume from Instant Buy does NOT count. Spot/futures volumes are independent.

---

## 3. Stablecoin & FX Pair Fees

Applies when stablecoin is the **base** currency (USDT/USD, DAI/USDT). Standard crypto schedule applies when stablecoin is only the quote (BTC/USDT).

| 30-Day Volume | Maker | Taker |
|---|---|---|
| $0 - $49,999 | 0.20% | 0.20% |
| Higher tiers | Down to 0.00% | Down to 0.001% |

---

## 4. Maker Fee Incentive Program

425+ lower-liquidity pairs eligible (as of Jan 2026). Maker fees lower than standard; at $10M+ tier, **negative maker fees** (rebates). All newly listed pairs automatically receive incentive schedule initially. Reassessed monthly.

**Relevant tokens likely on incentive:** BONK, FLOKI, PENGU, DENT, ZRO, OM. Verify at [support.kraken.com/articles/pairs-eligible-for-maker-fee-rebates](https://support.kraken.com/articles/pairs-eligible-for-maker-fee-rebates).

---

## 5. Token Universe Fees

Same percentage fee across all spot crypto pairs. The real cost difference is **spread and slippage:**

| Token | Liquidity | Typical Spread | Slippage |
|---|---|---|---|
| BTC, ETH | High | 0.01-0.05% | 0.02-0.05% |
| SOL, AVAX, DOT | Good | 0.05-0.10% | 0.05-0.10% |
| SUI, TRX, FIL, ZRO | Moderate | 0.10-0.30% | 0.10-0.20% |
| BONK, FLOKI, PENGU, DENT, OM | Low | 0.5-2%+ (3%+ in volatility) | 0.20-0.50% |

---

## 6. Withdrawal Fees

| Asset / Method | Fee | Notes |
|---|---|---|
| USDT (ERC-20) | 0.6 USDT | Cheapest USDT route |
| USDT (TRC-20) | 4 USDT | Paradoxically more expensive on Kraken |
| ETH | ~0.01 ETH | Network-based |
| SOL | ~0.02 SOL | Network-based |
| BTC | Dynamic | Network-based |
| ACH (US) | ~$4 withdrawal | 1-5 business days |
| FedWire | ~$4 flat | 0-1 business days |
| Crypto deposits | Free | All cryptocurrencies |

---

## 7. Hidden and Non-Obvious Fees

| Fee | Where | Cost |
|---|---|---|
| Instant Buy spread | Instant Buy only | Variable, Kraken-retained |
| Margin open fee | Margin positions | ~0.02% (BTC: 0.01%) |
| Margin rollover | Every 4h on margin | ~0.02% per 4h = **43.8%/yr** |
| Small balance conversion | Dust below min order | 3% fixed |
| Card purchase | Debit/credit buys | ~3.75% + $0.25 |

**No fees for:** Spot holding (overnight), API/WebSocket access, Pro limit order spreads.

---

## 8. Kraken vs Binance

| Metric | Kraken Pro | Binance |
|---|---|---|
| Base maker / taker | 0.25% / 0.40% | 0.10% / 0.10% |
| Lowest maker / taker | 0.00% / 0.08% | 0.011% / 0.023% (with BNB) |
| Fee token discount | None | 25% with BNB |
| Futures maker / taker | 0.02% / 0.05% | 0.02% / 0.04% |

### Backtest Assumption Check

Old assumption (0.10% + 5bps = 0.15% per trade) is **Binance base rate, not Kraken.**

| Scenario | Fee | Slippage | Total/trade |
|---|---|---|---|
| Kraken worst (base tier, thin pair) | 0.40% | 0.50-2.0% | 0.90-2.40% |
| **Kraken realistic ($200-500K vol)** | **0.18-0.22%** | **0.10-0.30%** | **0.28-0.52%** |
| Kraken good ($1M+ vol, liquid pairs) | 0.12-0.16% | 0.05-0.10% | 0.17-0.26% |
| Binance base tier | 0.10% | 0.05-0.10% | 0.15-0.20% |

**Recommended backtest costs (conservative):**
- **Liquid (BTC, ETH, SOL, AVAX):** 0.20% fee + 0.05-0.10% slippage = 0.25-0.30%/trade
- **Mid (SUI, TRX, FIL, ZRO):** 0.22% fee + 0.10-0.20% slippage = 0.30-0.42%/trade
- **Low-liq (BONK, FLOKI, PENGU, DENT, OM):** 0.25% fee + 0.20-0.50% slippage = 0.42-0.75%/trade

---

## 9. API Access

| Resource | Cost | Limit |
|---|---|---|
| REST (public) | Free | ~1 req/sec per IP |
| REST (private) | Free | Counter-based per key (ledger=2pts, other=1pt) |
| WebSocket v2 | Free | 200 subs/sec (standard), 500/sec (Pro) |
| Level 3 order book | Free | Via WebSocket |
| Historical OHLC | Free | 720 candles/call |

Trading rate limits shared across REST + WebSocket + FIX per account per pair.

---

## 10. Minimum Order Sizes

With $200K / 49 tokens = ~$4,100/position, well above all minimums. Official source: [support.kraken.com/articles/205893708](https://support.kraken.com/articles/205893708). API: `GET /0/public/AssetPairs?pair=BONKUSD` -> `ordermin` field.

| Token | Approx. Min | ~USD Value |
|---|---|---|
| BTC | 0.0001 | ~$10 |
| ETH | 0.002 | ~$5 |
| SOL | 0.5 | ~$75-100 |
| AVAX/DOT/SUI/FIL | 0.1-1 | ~$3-7 |
| TRX | 500 | ~$70-100 |
| BONK | 500,000+ | ~$5 |
| DENT/FLOKI | 10,000+ | ~$3-10 |
| OM/ZRO/PENGU | Verify | ~$5-20 |

---

## 11. Summary: Key Numbers for Backtesting

| Parameter | Value |
|---|---|
| Base maker / taker | 0.25% / 0.40% |
| Realistic fee at $200K AUM | 0.08-0.12% maker / 0.18-0.22% taker |
| Slippage -- liquid (BTC, ETH, SOL) | 0.02-0.10% |
| Slippage -- mid (SUI, TRX, DOT, FIL) | 0.05-0.20% |
| Slippage -- low-liq (BONK, FLOKI, PENGU, DENT) | 0.20-1.0%+ |
| Spot holding / API fees | None |
| **Conservative backtest all-in** | **0.25-0.40% liquid; 0.50-0.75% low-liq** |

---

## Sources

- [Kraken Fee Schedule](https://www.kraken.com/features/fee-schedule) | [Fee Overview](https://support.kraken.com/articles/360030303832-overview-of-fees-on-kraken)
- [Kraken vs Pro](https://www.kraken.com/learn/kraken-vs-kraken-pro) | [Maker Rebates](https://support.kraken.com/articles/pairs-eligible-for-maker-fee-rebates)
- [API Rate Limits](https://support.kraken.com/articles/206548367-what-are-the-api-rate-limits-) | [API Docs](https://docs.kraken.com/)
- [Min Order Sizes](https://support.kraken.com/articles/205893708) | [Min/Deposit/Withdrawal CSV](https://support.kraken.com/articles/360042589912)
- [Kraken vs Binance](https://www.kraken.com/learn/kraken-vs-binance)
