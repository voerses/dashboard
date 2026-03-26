# Funding Direction Investigation — R134 Bug Report

## Executive Summary

**Is there a bug? YES -- but it is a MAGNITUDE bug (8x), not a SIGN (direction) bug.**

The V4 backtest engine's funding direction logic is correct: short positions with positive funding rates correctly earn funding income. However, the on-disk parquet data has `funding_1h` equal to the raw 8h settlement rate instead of the per-hour rate (funding_rate / 8). Since the simulator applies `funding_1h` every hour, all funding charges/income are 8x the correct value.

This affects 120 out of 195 perp tokens (62%), primarily Binance tokens with 8h settlement intervals.

## Bug Details

### What is NOT broken: Sign/Direction Logic

The simulator formula (v4/simulator.py lines 415-419):

```python
notional = abs(pos.quantity * close_val)
d_sign = 1.0 if pos.quantity > 0.0 else -1.0
funding_cost = notional * funding_val * d_sign
state.total_funding += funding_cost
pos.cumulative_funding += funding_cost
```

And the equity formula (line 104-105):
```python
equity = initial_capital + realized_pnl - total_fees - total_funding
```

For a SHORT position (quantity < 0) with POSITIVE funding (longs pay shorts):
- `d_sign = -1.0`
- `funding_cost = notional * positive * -1.0 = NEGATIVE`
- `total_funding += NEGATIVE` => total_funding decreases
- `equity = ... - total_funding` => equity INCREASES (subtracting a more-negative number)

This is **correct**: shorts earn funding income when funding is positive.

Verified in s65 backtest results: `total_funding: -188,451` (negative = income).

### What IS broken: Magnitude (8x Overcharge)

**Location 1: On-disk parquet data**

The parquet files in `data/perp/1h_cache/` have `funding_1h == funding_rate`:

```
BTC_1h.parquet:
  funding_rate = -0.000124  (raw 8h Binance rate)
  funding_1h   = -0.000124  (should be -0.000124 / 8 = -0.0000155)
```

The code in `tools/build_parquet_cache.py` line 281 IS correct:
```python
funding_df['funding_1h'] = funding_df['funding_rate'] / interval_hours
```

But the on-disk parquets were built with an older version that lacked this division, or were contaminated by the live buffer merge path.

**Location 2: Live fetcher merge**

`v4/live_fetcher.py` line 354:
```python
funding_map[ts] = float(r["fundingRate"])  # RAW 8h rate, not divided by 8
```

Line 363 writes this directly to `funding_1h`:
```python
df.loc[mask, "funding_1h"] = new_funding[mask].astype(float)
```

This means live data also has the 8x bug.

### Scope

| Category | Count | % |
|----------|-------|---|
| Tokens with bug (funding_1h == funding_rate) | 120 | 62% |
| Tokens correct (funding_1h properly divided) | 75 | 38% |
| Total perp tokens | 195 | 100% |

The 75 "correct" tokens are primarily newer Hyperliquid tokens with 4h settlement intervals (ratio = 4.0), suggesting they were processed by a version of `build_parquet_cache.py` that already had the division logic.

### Impact on s65

s65 is a funding carry strategy that shorts perps when funding is positive (longs pay shorts). Key data:

- s65 is SHORT on 90.4% of bars (by design -- funding is almost always positive)
- Average funding rate when short: +0.000104 (per 8h period)
- The sign is correct: s65 EARNS funding income
- But the income is 8x too large

**Actual backtest results:**
| Metric | 12mo | 24mo |
|--------|------|------|
| total_funding | -$188,452 (income) | -$258,790 (income) |
| final_equity | $1,213,803 | $59,295 |
| annualized_return | +148.9% | -33.2% |

**With 8x correction:**
| Metric | 12mo (corrected est.) | 24mo (corrected est.) |
|--------|----------------------|----------------------|
| total_funding | -$23,556 (income) | -$32,349 (income) |
| Funding impact change | +$164,896 (less income) | +$226,441 (less income) |

For the 12-month run, correcting from -$188K to -$24K in funding income would reduce s65's final equity by approximately $165K, dramatically lowering the return from the inflated +148.9%.

### Impact on ALL Perp Strategies

Every strategy that trades perps is affected:
- **Long-biased strategies** (most momentum strategies): Currently OVERCHARGED 8x in funding costs. Fixing would IMPROVE their returns.
- **Short-biased strategies** (carry, funding arbitrage): Currently OVERPAID 8x in funding income. Fixing would REDUCE their returns.
- **Bidirectional strategies**: Net effect depends on long/short balance.

## Root Cause

The parquets were built at a time when `build_parquet_cache.py` did not divide `funding_rate` by `interval_hours`. The division logic was later added to the code, but the parquets were never rebuilt with the corrected code. Additionally, the live fetcher's `merge_funding_into_parquet()` function has never had the division logic, so any funding data flowing through the live path is also 8x too large.

## Required Fixes

### Fix 1: Rebuild all perp parquets (DATA)

Run `tools/build_parquet_cache.py` to regenerate all perp parquets. The current code already has the correct `funding_1h = funding_rate / interval_hours` division.

### Fix 2: Live fetcher funding merge (CODE)

In `v4/live_fetcher.py`, `merge_funding_into_parquet()` method, line 354:

**Current (buggy):**
```python
funding_map[ts] = float(r["fundingRate"])
```

**Fix: Detect settlement interval and divide:**
```python
# Detect settlement interval from timestamps (similar to resample_funding_to_1h)
# For Binance (8h settlements): divide by 8
# For Hyperliquid (4h settlements): divide by 4
interval_hours = 8  # default for Binance
funding_map[ts] = float(r["fundingRate"]) / interval_hours
```

Or more robustly, detect the interval from the `funding_rates` list timestamps.

### Fix 3: Re-run all perp backtests

After fixing the data and code, all perp strategy backtests need to be re-run to get correct results.

## Test Script

The investigation test script is at `research/funding_direction_test.py`. Run with:
```bash
/workspace/venv/bin/python research/funding_direction_test.py
```

## Files Examined

| File | Finding |
|------|---------|
| `v4/simulator.py` lines 415-419 | Sign logic is CORRECT |
| `v4/simulator.py` line 104-105 | Equity formula is CORRECT |
| `v4/signals.py` line 379 | Loads `funding_1h` from parquet (data is wrong) |
| `v4/engine.py` lines 1100-1102 | Loads `funding_1h` from parquet (data is wrong) |
| `tools/build_parquet_cache.py` line 281 | Code IS correct (divides by interval) |
| `v4/live_fetcher.py` line 354 | BUG: raw rate written without division |
| `strategies/s65_funding_carry_v4.py` | Strategy logic is correct |
| `data/perp/1h_cache/BTC_1h.parquet` | DATA BUG: funding_1h == funding_rate |

## Estimated Impact on s65 Backtest Return

With correct funding data (assuming all other factors constant):

- **12-month backtest**: Return drops from +148.9% to approximately +60-70% (funding income reduced by ~$165K)
- **24-month backtest**: Return improves slightly from -33.2% to approximately -20% (less funding income means less gain to offset price losses, but the price-driven losses are the dominant factor)

These are rough estimates. The actual impact depends on position timing, sizing, and which tokens' funding data is affected.

## Paper Trading vs Backtest Discrepancy

The bug report notes paper trading s65 earned +4.9% while the backtest shows different results. Investigation reveals:

**Both engines use the same code path.** The paper engine (`v4/paper_engine.py` line 29) imports `v4.simulator` and delegates all exit/entry processing (including funding accrual) to the same simulator functions. Both engines read the same parquet data with the same 8x bug.

**Paper trading state confirms the same pattern:**
- Pool state (375 ticks): `total_funding = -$9,490` (income), `equity = $210,563`
- Solo state (164 ticks): `total_funding = -$3,697` (income), `equity = $202,768`

The sign is correct in both engines (negative = income for carry shorts). The magnitude is 8x too large in both engines. The performance difference between paper and backtest comes from:
1. Different time periods (15 days paper vs 12 months backtest)
2. Different market conditions (recent vs historical)
3. Walk-forward masking in backtest (reduces entry opportunities)
4. Different token universe availability

## Additional Findings: Live Fetcher Also Affected

The `v4/live_fetcher.py` `merge_funding_into_parquet()` function (line 354) writes raw exchange `fundingRate` values directly to the `funding_1h` column without dividing by the settlement interval. This means:

1. All funding data from live fetches has the 8x bug
2. Paper trading (which uses live fetched data) also has the 8x bug
3. The bug is consistent across paper and backtest -- it does not explain any paper/backtest discrepancy

## Strategies Using Funding Data

The following strategy families are directly impacted by the 8x funding bug:

| Strategy Group | Market | Direction Bias | Bug Effect |
|---------------|--------|---------------|------------|
| s29, s61, s62, s65 (carry) | perp | Short-biased | 8x too much funding INCOME |
| s85, s86 (carry LS) | combined | Short on perp | 8x too much funding INCOME |
| s111, s146, s250 (funding arb) | perp | Depends on signal | 8x funding in both directions |
| All momentum strategies on perp | perp | Long-biased | 8x too much funding COST |

Long-biased perp strategies (most momentum strategies) will show IMPROVED returns after the fix because they're currently paying 8x too much in funding costs.
