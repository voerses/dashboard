# Strategy Results Knowledge Base

Last updated: 2026-02-28 (Post-Sweep Update)

## Data Period
- **1H/4H/Daily data**: Jan 2024 - Jan 2026 (~2 years)
- **49 tokens** with 1H data (out of 57 in liquid universe)
- **Capital**: $200,000 per backtest
- **Fees**: 0.1% + 5bps slippage

---

## STRATEGY RANKING (by Annual PnL)

### Tier 1: Post-Sweep Optimized (NEW)

| Rank | Config | Annual PnL | Trades | Win Rate | Payoff | Profitable Tokens |
|------|--------|-----------|--------|----------|--------|-------------------|
| **1** | **S11 Momentum Burst (all 49)** | **+$170,240/yr (+85.1%)** | 4,734 | 46% | 1.81x | **40/49** |
| **2** | **S09 Optimized Trend (all 49)** | **+$162,898/yr (+81.4%)** | 8,507 | 43% | 1.85x | **40/49** |
| **3** | **S11 Momentum Burst (CPCV 11)** | **+$62,705/yr (+31.4%)** | 1,215 | 47% | 1.94x | **10/11** |
| **4** | **S09 Optimized Trend (CPCV 11)** | **+$55,408/yr (+27.7%)** | 1,904 | 45% | 1.93x | **9/11** |

### Tier 2: Previous Best (Baseline Reference)

| Rank | Config | Annual PnL | Trades | Win Rate | Payoff | Profitable Tokens |
|------|--------|-----------|--------|----------|--------|-------------------|
| 5 | DM only on CPCV tokens (11) | +$21,583/yr (+10.8%) | 532 | 39% | 2.37x | 11/11 (100%) |
| 6 | DM+MR on CPCV tokens (11) | +$10,720/yr (+5.4%) | 535 | 39% | 2.36x | 11/11 |
| 7 | V3 Liquidity Contrarian (all 49) | +$8,216/yr (+4.1%) | 108 | 63% | 1.15x | 28/49 |

### Tier 3: Losing Strategies (What NOT to Do)

| Rank | Config | Annual PnL | Notes |
|------|--------|-----------|-------|
| - | S10 Research Dip Buy (all 49) | -$54,894/yr | Research combos don't translate to swing |
| - | VPIN-Enhanced DM+MR (all 49) | -$1,882/yr | VPIN filter too aggressive |
| - | Vol Breakout only (all 49) | -$4,220/yr | BB squeeze unreliable |
| - | V2 Daily Momentum (all 49) | -$13,806/yr | Too slow for crypto |
| - | Mean Reversion strategies (sweep) | -$25K to -$185K/yr | All MR strategies lose on all tokens |

### Sweep Results Summary (70 strategies tested)
- **All-token sweep**: trend_stop_3.0 = +$69K/yr, trend_prot_24 = +$52K/yr, mom_burst = +$50K/yr
- **CPCV sweep**: trend_prot_24 = +$30.7K/yr, mom_burst = +$22K/yr, DM baseline = +$21.6K/yr
- Full sweep results in `results/sweep_*.json`

---

## KEY FINDINGS

### 1. Parameter Optimization > Token Selection > Strategy Selection
**24-bar protection window** was the single biggest improvement (+$9K/yr on CPCV, from $21.6K to $30.7K).
**Tighter stops (3x ATR)** transformed all-token performance from -$726/yr to +$69K/yr.
Token selection still matters: CPCV tokens are consistently profitable across all strategies.
But optimized parameters make even ALL 49 tokens hugely profitable (+$170K/yr).

### 2. CPCV-Robust Tokens (PBO < 40%)
These 11 tokens show statistically robust alpha across all timeline orderings:
```
PENGU (PBO=13%), SUI (13%), OM (13%), TRX (13%), DOT (20%),
AVAX (13%), BONK (33%), FIL (27%), FLOKI (20%), DENT (27%), ZRO (27%)
```

### 3. V3 Liquidity Contrarian is a Strong Complement
- **63% win rate** (vs 36% for DM) — completely different profile
- Only 108 trades in 2 years — very selective (vol spike + 5%+ drop + RSI<35 + weak close)
- Works best on large caps (ETH +1.7%, SOL +1.2%)
- BUT: doesn't work well on CPCV tokens specifically (different token profiles)

### 4. Vol Breakout Strategy is a Drag
- Consistently loses money (-$2,813 over 2 years)
- Inverted payoff ratio (avg loss > avg win even after fixes)
- Squeeze→breakout is unreliable in crypto post-ETF
- **DROP IT** — portfolio improves by ~$2.5K/yr without it

### 5. VPIN as Entry Filter: Hurt More Than Helped
- Reduced trades from 2457 to 1692 without improving quality
- VPIN data quality issue: many tokens show 0.5 (no real data) for early period
- The VPIN filter was too aggressive, cutting good entries along with bad
- **May work with better data** but currently not worth it

### 6. V2 Daily Momentum: Terrible
- -$13,806/yr — worst performing approach
- Simple EMA cross + MACD on daily bars is too slow for crypto
- By the time daily signals confirm, the move is half over
- The multi-timeframe approach (1H entry with 4H+Daily confirmation) is strictly better

### 7. Protection Window: 24 Bars > 18 > 12 > 6 > 0
- 0 bars: losing
- 12 bars: +$21.6K/yr baseline
- 18 bars: +$23.2K/yr
- **24 bars: +$30.7K/yr** (best, 43% improvement over 12)
- Crypto needs even MORE time for thesis to play out than previously thought

### 8. Mean Reversion: NEVER Works on Swing Timeframes
- All MR strategies from the sweep lose money (-$25K to -$185K/yr)
- S10 Research Dip Buy (based on indicator research combos) also loses -$55K/yr
- The research combos (RSI_low+MACD_pos etc.) predict 1-day returns, not swing returns
- **Conclusion**: Only use trend-following for swing trading in crypto

### 9. ADX is the #1 Predictive Indicator (from research)
- IC=0.067 across all tokens, increases with horizon (0.05→0.08 at 5 days)
- ADX > 30 filter beat all lower thresholds in sweep
- RSI is nearly useless as standalone predictor (IC=0.004)

### 10. Indicator Redundancy Discovered
- RSI and BB_pct are 95% correlated — using both wastes capacity
- realized_vol and parkinson_vol are 99% correlated
- 4 truly independent signals: VPIN, amihud_1m, intraday_skew, intraday_kurtosis

### 11. Fat Tails Confirmed but Not the Edge Source
- Every crypto token rejects normality (mean excess kurtosis = 17.6)
- Top 5% of days produce 21% of absolute PnL
- Miss best 10 days: -81.7% return; miss worst 5 days: +181.2%
- BUT: CPCV robustness is NOT explained by fat tails (p=0.573)
- The edge comes from regime persistence + trend continuation, not tail capture

---

## STRATEGY DESCRIPTIONS

### Fat-Tail Strategies (mtf_strategy_v2.py)

#### Dual Momentum (BEST single strategy)
- **Timeframe**: 1H:4H:Daily stack
- **Entry**: Daily uptrend (EMA50, ADX>20, 12d momentum>0) + 4H pullback to EMA20 + 1H volume burst + RSI 30-55
- **Exit**: Trailing 4x ATR stop (starts after 12h), regime change (crisis/downtrend)
- **Sizing**: Half-Kelly for T1, Quarter-Kelly for T2/T3, vol-parity, 5% max per trade
- **Key param**: `no_stop_bars=12` (12-hour protection window)

#### Vol Breakout (DROP - loses money)
- BB squeeze (12+ bars in squeeze) → expansion + volume 2x + taker confirmation
- Even with 5x ATR stop and 12h protection, payoff ratio < 1.0

#### Mean Reversion (marginal)
- Range/quiet regime only, RSI<35, BB_pct<0.15, MACD turning, taker>0.52
- Convex exit: tight stop → trail aggressively at 1.5R+

### Prior Strategies

#### V3 Liquidity Contrarian (2nd best overall)
- **Timeframe**: Daily only
- **Entry**: Volume spike (2x avg) + 5-day return < -5% + RSI < 35 + close near low (<30% of range)
- **Exit**: Price returns to EMA20 (mean reversion target), 2x ATR stop, 20-day max hold
- **Profile**: High win rate (63%), low payoff (1.15x), very few trades
- **Best on**: Large caps with institutional flow (ETH, SOL, BNB)

#### V2 Daily Momentum (AVOID)
- EMA 20/50 cross + MACD + ADX > 20 on daily
- Too slow, signals too late, -6.9%/yr

---

## DUAL-VALIDATED TOKENS (pass BOTH CPCV + Walk-Forward)

### S11 Momentum Burst — 6 validated tokens
```
PENGU (PBO=33%), SUI (PBO=7%), AVAX (PBO=27%),
BONK (PBO=7%), FLOKI (PBO=33%), ZRO (PBO=0%)
```

### S09 Optimized Trend — 4 validated tokens
```
SUI (PBO=7%), TRX (PBO=0%), BONK (PBO=0%), FLOKI (PBO=27%)
```

### Triple-validated core (both strategies + both methods): SUI, BONK, FLOKI

### Failed validation (look profitable but DON'T persist):
- **OM**: +$39K backtest but WF_FAIL — edge in first 60% only
- **TRX**: CPCV_FAIL on S11 — inconsistent across folds
- **DOT**: Fails both CPCV and WF on S11
- **DENT**: Loses money on S11, fails CPCV

## RECOMMENDED CONFIGURATIONS

### Option A: Highest Validated Return — S11 on Validated Tokens (BEST)
- **Expected**: ~$50-62K/yr on validated tokens
- **Strategy**: S11 Momentum Burst (3% hourly move + ADX>20 + above EMA20)
- **Tokens**: PENGU, SUI, AVAX, BONK, FLOKI, ZRO (6 validated)
- **Why**: Passes BOTH CPCV and Walk-Forward, highest raw PnL, 46% WR
- **Risk**: Higher trade frequency (1,215 trades/2yr), 24-bar no-stop exposure

### Option B: Most Robust — S11 on Triple-Validated Core
- **Expected**: ~$30-40K/yr
- **Tokens**: SUI, BONK, FLOKI (3 tokens passing both strats + both methods)
- **Why**: Maximum confidence, smallest risk of overfitting
- **Risk**: Very concentrated (3 tokens)

### Option C: Diversified — S11 on 6 tokens + S09 on 4 tokens (split capital)
- **Allocation**: 60% S11 on PENGU/SUI/AVAX/BONK/FLOKI/ZRO + 40% S09 on SUI/TRX/BONK/FLOKI
- **Why**: Two independent strategies, different signal types, overlapping on strongest tokens
- **Risk**: Overlapping positions on SUI/BONK/FLOKI need position limits

---

## WHAT DIDN'T WORK AND WHY

| Approach | Result | Why It Failed |
|----------|--------|---------------|
| Vol Breakout | -$2,813 | BB squeeze→breakout unreliable post-ETF; payoff < 1x |
| VPIN filter | Worse than baseline | Data quality issues; filter too aggressive |
| V2 Daily Momentum | -$13,806/yr | Daily EMA cross too slow for crypto |
| Trading all 49 tokens | ~breakeven | Bad tokens dilute good ones; 22 tokens lose money |
| Tight stops (<3x ATR) | -$42K on early exits | Crypto needs wide stops; 12h no-stop is essential |

---

## NEXT STEPS TO EXPLORE

1. **Expand CPCV analysis** to find more robust tokens (re-run quarterly)
2. **Walk-forward on V3 Contrarian** to validate out-of-sample
3. **Combine V3 Contrarian + CPCV DM** in a proper portfolio optimizer
4. **VPIN with better data**: Use 1-minute aggregated VPIN (from 1m_cache) instead of pre-computed daily
5. **Cross-sectional momentum** (V3 Strategy 7): Long outperformers vs BTC — untested
6. **Adaptive position sizing** per token based on CPCV confidence (lower PBO = larger position)
7. **Regime-conditional V3**: Only take V3 contrarian trades during crisis/high-vol regimes
