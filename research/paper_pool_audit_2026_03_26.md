# Paper Pool Audit — 2026-03-26

**Generated:** 2026-03-26 14:03 UTC
**Runner status:** DOWN (PID 183468 not running, last heartbeat 2026-03-25T17:04Z, ~21 hours stale)
**Config file:** `configs/multi_v4_paper.json` (8 pools configured, including s320)

---

## Executive Summary

The paper trading runner is **not running**. It stopped on 2026-03-24 at 22:16 UTC, was restarted (with s320 added), ran until 2026-03-25 ~17:05 UTC, and has been down since. All 8 pools are stale by ~21 hours.

Of the 8 configured pools:
- **2 pools are profitable and actively trading** (s58+s65, s72)
- **2 pools are marginally positive** (s65 solo, s62 solo)
- **1 pool is negative with only 2 trades** (s98) -- strategy appears broken for live conditions
- **3 pools have NEVER traded** (s106, s107, s320) -- dead weight consuming compute

Total paper capital: $1,600,000 (8 pools x $200K)
Total realized P&L across all pools: **+$4,318** (+0.27% on total capital)
Total mark-to-market P&L: **+$20,068** (+1.25% on total capital)

---

## Pool Performance Summary

| Pool | Equity | MTM Equity | Realized P&L% | MTM P&L% | Trades | Win Rate | Avg Win | Avg Loss | PF | Max DD% | Open Pos | Days Live | Status |
|------|--------|------------|----------------|----------|--------|----------|---------|----------|----|---------|----------|-----------|--------|
| **s58+s65** | $210,563 | $210,740 | **+5.28%** | +5.37% | 86 | 48.8% | $1,596 | -$1,304 | 1.10 | 18.72% | 11 | 14.3 | ACTIVE |
| **s72** | $204,041 | $204,881 | **+2.02%** | +2.44% | 59 | 47.5% | $1,991 | -$1,633 | 1.10 | 24.34% | 5 | 13.2 | ACTIVE |
| **s65** | $202,768 | $204,993 | **+1.38%** | +2.50% | 28 | 42.9% | $2,716 | -$2,019 | 1.01 | 9.47% | 6 | 7.0 | ACTIVE |
| **s62** | $200,363 | $201,468 | **+0.18%** | +0.73% | 33 | 33.3% | $1,255 | -$693 | 0.91 | 6.17% | 7 | 7.0 | MARGINAL |
| **s98** | $197,987 | $197,987 | **-1.01%** | -1.01% | 2 | 0.0% | N/A | -$998 | 0.00 | 1.31% | 0 | 4.8 | FAILING |
| **s106** | $200,000 | $200,000 | **0.00%** | 0.00% | 0 | N/A | N/A | N/A | N/A | 0.00% | 0 | 4.3 | DEAD |
| **s107** | $200,000 | $200,000 | **0.00%** | 0.00% | 0 | N/A | N/A | N/A | N/A | 0.00% | 0 | 4.3 | DEAD |
| **s320** | $200,000 | $200,000 | **0.00%** | 0.00% | 0 | N/A | N/A | N/A | N/A | 0.00% | 0 | 0.8 | DEAD |

**PF** = Profit Factor (gross wins / gross losses). Values above 1.0 are profitable.

---

## Detailed Pool Analysis

### Pool: s58+s65 (BEST PERFORMER)

**Config:** 3 strategies (s56, s57, s65) in perp+combined markets, 50 max positions
**State dir:** `state/v4_paper_s65/`
**Inception:** 2026-03-11T09:42Z (14.3 days)

| Sub-Strategy | Trades | Win Rate | Total PnL | Profit Factor | Notes |
|--------------|--------|----------|-----------|---------------|-------|
| **s65** | 55 | 52.7% | **+$12,168** | 1.27 | Workhorse. Clear alpha. |
| s56 | 19 | 47.4% | -$2,069 | 0.76 | Losing. 3 margin calls. |
| s57 | 12 | 33.3% | -$462 | 0.87 | Neutral/market-making. 4 data_end exits. |

**Key finding:** s65 is carrying this pool. s56 is a drag with 3 margin calls (dangerous). s57 is running basis-like positions (AGLD long+short, DYDX long+short) that are capital-intensive for minimal return.

**Open positions (11):** REZ, PHA, TURBO, ANIME, AGLD(x2), PIPPIN, DYDX(x2), TAO, IP
**Total margin deployed:** ~$187,112 (94% of equity)
**Realized PnL:** +$2,914 | Total fees: $1,842 | Total funding: -$9,490

**Funding drag is severe:** -$9,490 in funding costs over 14 days. At this rate, that is -$242K annualized, which would eventually overwhelm the +$12K in s65 trade PnL. This needs monitoring.

---

### Pool: s72

**Config:** Single strategy s72, perp market, 15 max positions
**State dir:** `state/v4_paper_s72/`
**Inception:** 2026-03-12T11:44Z (13.2 days)

| Metric | Value |
|--------|-------|
| Trades | 59 |
| Win Rate | 47.5% |
| Profit Factor | 1.10 |
| Max Drawdown | 24.34% (2026-03-24) |
| Funding Cost | -$7,887 |

**Open positions (5):** REZ, PIPPIN, IP, TURBO, ANIME
**Total margin deployed:** ~$201,818 (99% of equity -- nearly fully invested)

**Concerns:** 24.3% max drawdown is high. The pool recovered from it, but that level of drawdown in 13 days of live trading raises risk management questions. Funding drag of -$7,887 is also significant. The pool is currently profitable solely on unrealized gains in open positions. Realized PnL is actually -$4,349.

---

### Pool: s65 (Solo)

**Config:** Single strategy s65, perp market, 15 max positions
**State dir:** `state/v4_paper_s65_solo/`
**Inception:** 2026-03-18T17:52Z (7.0 days)

| Metric | Value |
|--------|-------|
| Trades | 28 |
| Win Rate | 42.9% |
| Profit Factor | 1.01 |
| Max Drawdown | 9.47% |
| Funding Cost | -$3,992 |

**Comparison with s65 inside s58+s65 pool:** The solo version has a lower win rate (42.9% vs 52.7%) and a barely-positive profit factor (1.01 vs 1.27). This is likely due to the shorter track record (7 days vs 14 days) and different position timing. The controlled drawdown (9.47%) is better than the multi-strategy pool.

**Open positions (6):** IP, ANIME, PHA, TURBO, PIPPIN, TAO

---

### Pool: s62 (Solo)

**Config:** Single strategy s62, perp market, 15 max positions
**State dir:** `state/v4_paper_s62_solo/`
**Inception:** 2026-03-18T17:52Z (7.0 days)

| Metric | Value |
|--------|-------|
| Trades | 33 |
| Win Rate | 33.3% |
| Profit Factor | 0.91 |
| Max Drawdown | 6.17% |
| Funding Cost | -$1,992 |

**Assessment:** 33.3% win rate with a sub-1.0 profit factor. This strategy is losing money on closed trades (-$1,428) and is only barely positive due to unrealized gains on 7 open positions. The low win rate combined with avg win ($1,255) that barely exceeds avg loss ($693) does not give confidence. One sentinel_stop exit suggests risk management kicked in.

**Open positions (7):** PHA, PIPPIN, AKT, ANIME, TURBO, IP, TAO
**Free capital:** $84,101 (42% of equity -- less deployed than other pools)

---

### Pool: s98

**Config:** Single strategy s98, perp market, 15 max positions, 20% concentration limit, 30min exit resolution
**State dir:** `state/v4_paper_s98/`
**Inception:** 2026-03-20T21:44Z (4.8 days)

| Metric | Value |
|--------|-------|
| Trades | 2 |
| Win Rate | 0.0% |
| Profit Factor | 0.00 |
| Realized PnL | -$1,990 |

**Assessment:** Only 2 trades in 4.8 days, both short positions (THE and PIPPIN), both stopped out for losses. The strategy has been completely flat since 2026-03-21 with zero positions and zero new signals for 4+ days. Either the signal generation is too restrictive for current market conditions, or there is a configuration issue. The 20% concentration limit may be throttling entries.

---

### Pool: s106

**Config:** Single strategy s106, perp market, 15 max positions, 20% concentration limit, 5min exit resolution
**State dir:** `state/v4_paper_s106/`
**Inception:** 2026-03-21T09:06Z (4.3 days)

**ZERO trades. ZERO positions. NEVER entered a single trade.**

After 114 hourly ticks (4.3 days), this strategy has generated no entry signals whatsoever. It is consuming compute resources (running PriceMonitor with 10-11 WebSocket streams per tick) for zero output.

---

### Pool: s107

**Config:** Single strategy s107, perp market, 15 max positions, 20% concentration limit, 5min exit resolution
**State dir:** `state/v4_paper_s107/`
**Inception:** 2026-03-21T09:06Z (4.3 days)

**ZERO trades. ZERO positions. NEVER entered a single trade.**

Same situation as s106. 114 ticks, zero signals, zero trades. Dead weight.

---

### Pool: s320

**Config:** Single strategy s320, spot market, 1 max position, kelly_mult_override=0.50, spot_max_equity_pct=0.95
**State dir:** `state/v4_paper_s320/`
**Inception:** 2026-03-24T22:21Z (0.8 days)

**ZERO trades. Only 22 ticks.** Added to the runner on the restart after 2026-03-24 22:16, and the runner died again ~19 hours later. Not enough time to evaluate, but after 22 hourly ticks with no entry, this may also have signal generation issues.

**Note:** The saved pool config (`state/v4_paper_s320/config.json`) does NOT include the `sizing_overrides` from the main config. The `kelly_mult_override` and `spot_max_equity_pct` may not be applied correctly. This needs investigation.

---

## Operational Issues

### 1. Runner is DOWN (CRITICAL)

The paper trading runner (PID 183468) is not running. Last log entry shows a clean shutdown at 2026-03-24T22:16Z. The heartbeat files indicate a restart occurred (last heartbeat 2026-03-25T17:04Z), but that instance also died without any visible log file. The current gap is ~21 hours with no new ticks.

**Impact:** All 8 pools are stale. Open positions in s58+s65 (11), s72 (5), s65 (6), and s62 (7) are unmonitored. Stop losses and price monitors are not running. If the underlying positions were real, this would be a significant risk event.

### 2. High Funding Costs

Across all active pools, total funding costs are:
- s58+s65: -$9,490 (14 days)
- s72: -$7,887 (13 days)
- s65: -$3,992 (7 days)
- s62: -$1,862 (7 days)
- **Total: -$23,231** across ~$800K deployed capital

Annualized, this is roughly **-$400K to -$600K** in funding drag. This is eating into alpha significantly. The strategies need to either turn over positions faster or incorporate funding cost awareness into entry decisions.

### 3. Position Overlap

Multiple pools hold the same tokens simultaneously:
- **TURBO:** held by s58+s65, s72, s65, s62 (4 pools, all LONG)
- **ANIME:** held by s58+s65, s72, s65, s62 (4 pools, all LONG)
- **IP:** held by s58+s65, s72, s65, s62 (4 pools, all LONG)
- **PIPPIN:** held by s58+s65, s72, s65, s62 (4 pools, SHORT in 3, SHORT in s72)
- **PHA:** held by s58+s65, s65, s62 (3 pools)
- **TAO:** held by s58+s65, s65, s62 (3 pools)
- **REZ:** held by s58+s65, s72 (2 pools)

This creates massive concentration risk. If TURBO or ANIME dumps, all 4 active pools take a hit simultaneously. The pools are not providing diversification -- they are amplifying the same bets.

### 4. s56 Margin Calls

Strategy s56 within the s58+s65 pool had 3 margin calls out of 19 trades (15.8%). This indicates the position sizing is too aggressive or stop losses are too loose for s56's signal quality.

### 5. Data Fetch Errors

Every hourly tick logs 65 fetch errors (out of 390 attempted). Three known missing spot symbols: MOODENG/USDT, ATH/USDT, GWEI/USDT. The remaining ~62 errors should be investigated to ensure they are not affecting signal generation for active strategies.

---

## Recommendations

### KILL (Remove from config)

| Pool | Reason |
|------|--------|
| **s106** | Zero trades in 4.3 days, 114 ticks. Strategy is non-functional in live conditions. Remove immediately. |
| **s107** | Zero trades in 4.3 days, 114 ticks. Same as s106. Remove immediately. |
| **s98** | 2 trades, both losses, zero activity for 4+ days. Strategy is non-functional or far too selective. Remove. |
| **s56** (within s58+s65) | Negative PnL, 3 margin calls, profit factor 0.76. Remove from the multi-strategy pool. |
| **s57** (within s58+s65) | Near-zero contribution, capital-intensive basis positions. Remove or reduce weight. |

### KEEP (Continue monitoring)

| Pool | Reason | Action Needed |
|------|--------|---------------|
| **s58+s65** | Best performer at +5.28%, but only because s65 carries it. | Remove s56/s57, run s65-only. Or keep as multi but acknowledge s65 is the alpha source. |
| **s72** | Positive at +2.02%, but 24% max drawdown is concerning. | Monitor drawdown. Consider tighter stops. |
| **s65 solo** | Marginal positive (+1.38%), clean drawdown (9.5%). | Keep running as controlled experiment. Compare with s65-in-pool. |

### WATCH (Needs changes before continuing)

| Pool | Reason | Action Needed |
|------|--------|---------------|
| **s62** | Barely positive (+0.18%), sub-1.0 profit factor, 33% win rate. | Give another 1-2 weeks. If PF stays below 1.0 on closed trades, kill. |
| **s320** | Only 22 ticks, too new to judge. But config may be broken. | Verify sizing_overrides are being applied. Restart runner and monitor for 1 week. |

### ADD / MODIFY

1. **Restart the runner immediately.** All pools are unmonitored with open positions.
2. **Streamline the config:** Remove s106, s107, s98. This saves ~20 seconds per tick cycle and reduces WebSocket connections.
3. **Fix s320 config:** Ensure `sizing_overrides` (kelly_mult_override, spot_max_equity_pct) are being passed through to the engine, not just sitting in the JSON config.
4. **Add funding-aware entry filter:** The cumulative -$23K in funding drag across pools is the single biggest P&L drag. Consider adding a funding rate check to avoid entering positions where funding is strongly against the direction.
5. **Address position overlap:** Consider a cross-pool deduplication layer or at minimum a dashboard alert when 3+ pools hold the same token.

---

## Capital Efficiency

| Category | Capital | % of Total | Annualized Return |
|----------|---------|------------|-------------------|
| Active & profitable (s58+s65, s72) | $400K | 25% | +95% (blended) |
| Marginal (s65 solo, s62 solo) | $400K | 25% | +41% (blended) |
| Dead/losing (s98, s106, s107, s320) | $800K | 50% | -0.3% |

**Half the paper capital is allocated to strategies that have generated zero or negative returns.** Reallocating the dead pool capital to the proven strategies (or just removing the dead pools) would significantly improve capital efficiency.

---

## Appendix: Open Positions Detail (as of last tick)

### s58+s65 (11 positions, ~$187K deployed)
| Token | Strategy | Direction | Entry Price | Margin |
|-------|----------|-----------|-------------|--------|
| REZ | s65 | LONG | $0.003618 | $25,884 |
| PHA | s65 | LONG | $0.038380 | $321 |
| TURBO | s65 | LONG | $0.001156 | $32,428 |
| ANIME | s65 | LONG | $0.005032 | $38,436 |
| AGLD | s57 | LONG | $0.247782 | $11,582 |
| AGLD | s57 | SHORT | $0.241965 | $11,582 |
| PIPPIN | s65 | SHORT | $0.080094 | $25,033 |
| DYDX | s57 | LONG | $0.085258 | $5,999 |
| DYDX | s57 | SHORT | $0.084668 | $5,999 |
| TAO | s56 | LONG | $364.850098 | $24,983 |
| IP | s65 | LONG | $0.637935 | $4,865 |

### s72 (5 positions, ~$202K deployed)
| Token | Strategy | Direction | Entry Price | Margin |
|-------|----------|-----------|-------------|--------|
| REZ | s72 | LONG | $0.003513 | $33,894 |
| PIPPIN | s72 | SHORT | $0.079679 | $27,428 |
| IP | s72 | LONG | $0.649393 | $53,808 |
| TURBO | s72 | LONG | $0.001117 | $46,335 |
| ANIME | s72 | LONG | $0.005095 | $40,353 |

### s65 solo (6 positions, ~$203K deployed)
| Token | Strategy | Direction | Entry Price | Margin |
|-------|----------|-----------|-------------|--------|
| IP | s65 | LONG | $0.643517 | $43,280 |
| ANIME | s65 | LONG | $0.005046 | $40,217 |
| PHA | s65 | LONG | $0.038126 | $24,897 |
| TURBO | s65 | LONG | $0.001107 | $45,265 |
| PIPPIN | s65 | SHORT | $0.077814 | $26,917 |
| TAO | s65 | LONG | $366.155853 | $22,028 |

### s62 solo (7 positions, ~$116K deployed)
| Token | Strategy | Direction | Entry Price | Margin |
|-------|----------|-----------|-------------|--------|
| PHA | s62 | LONG | $0.038172 | $15,004 |
| PIPPIN | s62 | SHORT | $0.079382 | $10,310 |
| AKT | s62 | LONG | $0.539667 | $12,495 |
| ANIME | s62 | LONG | $0.005021 | $19,859 |
| TURBO | s62 | LONG | $0.001102 | $18,472 |
| IP | s62 | LONG | $0.654344 | $25,974 |
| TAO | s62 | LONG | $366.031494 | $14,148 |
