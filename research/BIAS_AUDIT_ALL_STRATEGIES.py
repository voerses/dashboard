"""
BIAS AUDIT — All Active Strategies (2026-04-04)
================================================

Auditor: Claude Code (automated code review)
Scope: Forward bias, intra-bar trading, rolling window bias
Strategies: s98, s513, s514, s517, s518, s519, s521

This file documents the audit findings. It is NOT executable analysis code —
it is a structured report in .py format for consistency with the research/ dir.


========================================================================
  CLASSIFICATION TABLE
========================================================================

| Strategy                    | Verdict    | Daily Data? | Lag OK? | Notes                              |
|-----------------------------|------------|-------------|---------|--------------------------------------|
| s98_sr_breakout_swing       | CLEAN      | No          | N/A     | ctx.ind_1h only                      |
| s513_triple_trigger_swing   | CLEAN      | No          | N/A     | ctx.ind_1h only                      |
| s514_ls_div_leveraged       | CLEAN      | Yes (L/S)   | Yes     | +1 day shift verified (bias fix)     |
| s517_macro_cluster          | CLEAN      | Yes (macro)  | Yes     | +1 day shift on SP500 + VIX          |
| s518_positioning_cluster    | CLEAN      | Yes (L/S+OI)| Yes     | +1 day shift on both L/S and OI      |
| s519_vol_cluster            | CLEAN      | Yes (DVOL)   | Yes     | +1 day shift on DVOL z-score + rank  |
| s521_fg_sp500_trend         | CLEAN      | Yes (FG+SP)  | Yes     | +1 day lag applied at index creation |


========================================================================
  DETAILED AUDIT PER STRATEGY
========================================================================


----------------------------------------------------------------------
STRATEGY: s98_sr_breakout_swing
----------------------------------------------------------------------
FILE: strategies/s98_sr_breakout_swing.py
DATA SOURCES: ctx.ind_1h only
  - close, volume, macd, adx, plus_di, minus_di, ema_10/20/50, bb_width
  - regime_1h (V4 built-in regime classifier)
DAILY DATA: None
EXTERNAL DATA: None

A. Data Timing Bias: NOT APPLICABLE
   No daily or external data used. All indicators are computed from hourly
   OHLCV bars by the V4 engine (ctx.ind_1h).

B. Intra-Bar Bias: CLEAN
   Signal uses MACD zero-cross (macd[T] > 0 && macd[T-1] <= 0), regime
   state, ADX, DI, EMA alignment, BB squeeze — all from bar T's close.
   V4 engine enters at close[T]. For hourly technical indicators computed
   from close, entry at bar close is standard practice (signal confirmed
   at bar close = entry at bar close). Not intra-bar.

   Regime change detection uses np.roll(regime, 1) — compares bar T to
   bar T-1. This is correct: regime change is detected at T, entry at T.

   MACD cross uses np.roll(macd, 1) — compares macd[T] to macd[T-1].
   Standard cross detection, no bias.

C. Rolling Window Bias: CLEAN
   - rolling_mean(dollar_vol, 24) for ADV: includes current bar. For
     liquidity filtering this is fine — we need to know current liquidity.
   - rolling_mean(bb_width, BB_LOOKBACK=240): BB width average includes
     current bar. Standard for squeeze detection.
   - All rolling windows are on hourly price data, not alternative data.

VERDICT: CLEAN
   Pure hourly technical strategy. No external data, no daily alignment
   issues. Standard bar-close entry for hourly indicators.


----------------------------------------------------------------------
STRATEGY: s513_triple_trigger_swing
----------------------------------------------------------------------
FILE: strategies/s513_triple_trigger_swing.py
DATA SOURCES: ctx.ind_1h only
  - close, high, low, volume, macd, rsi, adx, plus_di, minus_di,
    ema_10/20/50, bb_width
  - regime_1h
DAILY DATA: None
EXTERNAL DATA: None

A. Data Timing Bias: NOT APPLICABLE
   No daily or external data. Same as s98 — pure hourly indicators.

B. Intra-Bar Bias: CLEAN
   Three triggers, all using hourly bar data:
   1. MACD zero-cross: macd[T] vs macd[T-1] via np.roll. Standard.
   2. RSI pullback: rsi[T] vs rsi[T-1] via np.roll. Standard cross
      detection on hourly RSI.
   3. Donchian breakout: close[T] > donchian_high_prev[T], where
      donchian_high_prev uses np.roll(donchian_high, 1). This means:
      - donchian_high = rolling(480).max() of high — includes bar T
      - donchian_high_prev = donchian_high shifted by 1 bar
      - Entry: close[T] > max(high[T-480:T-1])
      This is CORRECT: breakout is defined as current close exceeding
      the PRIOR bar's Donchian channel, not the current bar's.

   All entries at bar T close — standard for hourly indicators.

C. Rolling Window Bias: CLEAN
   - Donchian rolling(480) includes current bar for donchian_high/low,
     but the strategy uses donchian_high_prev (shifted by 1) for the
     comparison. So the breakout level excludes bar T. Correct.
   - ADV, BB squeeze: same as s98, standard.

VERDICT: CLEAN
   Pure hourly technical strategy with three triggers. Donchian breakout
   correctly uses prior bar's channel for comparison. No external data.


----------------------------------------------------------------------
STRATEGY: s514_ls_div_leveraged
----------------------------------------------------------------------
FILE: strategies/s514_ls_div_leveraged.py
DATA SOURCES:
  - ctx.ind_1h['close'] (for bar count only)
  - External: all_symbols_daily_ls.parquet (Binance L/S ratios)
    Columns: count_toptrader_ls_ratio, count_ls_ratio
DAILY DATA: Yes — L/S divergence (daily granularity)

A. Data Timing Bias: CLEAN (FIXED)
   _get_divergence_aligned() at line 132-155:
   - Loads daily L/S divergence per symbol
   - BIAS FIX (line 148-149):
       shifted.index = shifted.index + pd.Timedelta(days=1)
   - Then reindexes to idx_1h.normalize() with method="ffill"

   This means: daily L/S value for date D is shifted to D+1, then
   forward-filled to all hourly bars of D+1 and beyond. Day D's data
   is NOT used on day D bars. CORRECT.

   Docstring explicitly documents the fix: "Daily L/S metric for date D
   covers 00:00-23:55 UTC and is NOT available until D+1 00:00."

B. Intra-Bar Bias: CLEAN
   - Entry gated by day_change (first bar of each new day = 00:00 UTC)
   - Signal is rolling_zscore of lagged daily data
   - At 00:00 of day D+1, the signal uses L/S data through day D (lagged)
   - Entry at 00:00 close — this is the first available bar after the
     daily data becomes known. No intra-bar issue.

   Edge detection (lines 221-228): short_level & (~short_prev | day_change)
   This prevents re-entering the same signal every day. The use of
   np.roll(short_level, 1) for edge detection is on the hourly z-score
   series, not on raw daily data. Correct.

C. Rolling Window Bias: CLEAN
   - rolling_zscore(divergence, 720) on hourly-aligned daily data
   - The daily data is already lagged by 1 day before alignment
   - The rolling window includes bar T, but since the underlying daily
     value was lagged, bar T's "current" value is actually from D-1.
   - No look-ahead: the z-score at time T uses only data through D-1.

VERDICT: CLEAN (previously biased, fix verified correct)
   The +1 day shift at line 148-149 correctly prevents look-ahead bias.
   Entry on day_change ensures no intra-bar issues. The fix was applied
   on 2026-04-04 per the docstring.


----------------------------------------------------------------------
STRATEGY: s517_macro_cluster
----------------------------------------------------------------------
FILE: strategies/s517_macro_cluster.py
DATA SOURCES:
  - ctx.ind_1h['close'] (for bar count)
  - External: sp500.parquet (SP500 close prices)
  - External: vix.parquet (VIX close prices)
DAILY DATA: Yes — SP500 5d ROC + VIX 14d z-score

A. Data Timing Bias: CLEAN
   _get_macro_aligned() at lines 132-168:
   - SP500 ROC5: computed on daily data, then:
       shifted.index = shifted.index + pd.Timedelta(days=1)  (line 154)
       aligned = shifted.reindex(dates_norm, method="ffill")  (line 155)
   - VIX z14: same pattern:
       shifted.index = shifted.index + pd.Timedelta(days=1)  (line 161)
       aligned = shifted.reindex(dates_norm, method="ffill")  (line 162)

   Both daily series are shifted +1 day before alignment to hourly.
   Day D's SP500/VIX data first appears on D+1 bars. CORRECT.

   Note: SP500 ROC is pct_change(5) — a 5-day return ending on day D.
   The market close for SP500 is ~21:00 UTC (4 PM ET). Crypto runs 24h.
   Shifting to D+1 00:00 UTC means the signal is available ~3h after
   the SP500 market close. This is conservative (good).

   VIX z-score uses rolling_zscore on daily data, then shifts +1 day.
   The z-score window (14 days) uses data through day D, which is only
   available at D+1. CORRECT.

B. Intra-Bar Bias: CLEAN
   - Entry gated by day_change (first bar of new day)
   - Regime classification uses lagged macro data
   - Edge detection: regime[T] != regime[T-24] — compares current regime
     to 24 bars ago (prior day). This is a coarser edge detector but not
     biased — both values are from lagged data.

C. Rolling Window Bias: CLEAN
   - SP500 pct_change(5): computed on daily data before shifting. The
     5-day window includes day D, but since the whole series is shifted
     by +1 day, day D's value doesn't appear until D+1. CORRECT.
   - VIX rolling z-score (14 days): same reasoning. CORRECT.

VERDICT: CLEAN
   Both SP500 and VIX daily data are properly lagged by +1 day via
   Timedelta shift before alignment to hourly bars. Entry on day_change
   ensures consistent timing.


----------------------------------------------------------------------
STRATEGY: s518_positioning_cluster
----------------------------------------------------------------------
FILE: strategies/s518_positioning_cluster.py
DATA SOURCES:
  - ctx.ind_1h['close'] (for bar count)
  - External: all_symbols_daily_ls.parquet
    Columns: count_toptrader_ls_ratio, count_ls_ratio, sum_open_interest_value
DAILY DATA: Yes — L/S divergence + OI value (both daily)

A. Data Timing Bias: CLEAN
   _get_aligned() at lines 133-157 is a GENERIC alignment function:
   - Takes any daily cache dict, shifts +1 day:
       shifted.index = shifted.index + pd.Timedelta(days=1)  (line 149)
       aligned = shifted.reindex(idx_1h.normalize(), method="ffill")  (line 152)
   - Used for BOTH divergence AND OI:
       divergence = _get_aligned(_ls_cache, symbol, ctx.idx_1h, "div")  (line 199)
       oi_value = _get_aligned(_oi_cache, symbol, ctx.idx_1h, "oi")     (line 202)

   Both daily data streams are shifted +1 day. Day D's L/S and OI data
   first appear on D+1 bars. CORRECT.

B. Intra-Bar Bias: CLEAN
   - Entry gated by day_change & oi_declining (lines 232-233)
   - Same edge detection pattern as s514 (short_level vs short_prev)
   - All signals derived from lagged daily data, entry at day boundary

C. Rolling Window Bias: CLEAN
   - rolling_zscore(divergence, 720) and rolling_zscore(oi_value, 720)
   - Both inputs are already lagged by +1 day before alignment
   - Rolling windows include "current" bar, but that bar's daily value
     is from D-1, so no look-ahead

VERDICT: CLEAN
   Follows the same pattern as s514 with a generic _get_aligned() that
   applies +1 day shift to any daily data source. Both L/S and OI data
   are properly lagged.


----------------------------------------------------------------------
STRATEGY: s519_vol_cluster
----------------------------------------------------------------------
FILE: strategies/s519_vol_cluster.py
DATA SOURCES:
  - ctx.ind_1h['close'] (for bar count)
  - External: btc_dvol_daily.json (Deribit BTC DVOL)
    Format: [timestamp_ms, open, high, low, close]
DAILY DATA: Yes — BTC DVOL (daily implied volatility)

A. Data Timing Bias: CLEAN
   Two alignment functions, both with +1 day shift:

   _get_dvol_zscore_aligned() lines 120-151:
   - Computes rolling z-score on daily DVOL data FIRST
   - Then shifts: dvol_z_shifted.index = dvol_z_shifted.index + pd.Timedelta(days=1) (line 142)
   - Forward-fills to hourly

   _get_dvol_rank_aligned() lines 154-185:
   - Computes rolling percentile rank on daily DVOL data FIRST
   - Then shifts: dvol_rank_shifted.index = dvol_rank_shifted.index + pd.Timedelta(days=1) (line 175)
   - Forward-fills to hourly

   Both z-score AND rank are computed on daily data, then shifted +1 day
   before alignment. Day D's DVOL is not available until D+1. CORRECT.

   Note: DVOL is a Deribit product. The daily candle closes at 08:00 UTC.
   Shifting to D+1 00:00 UTC means the signal appears ~16h after the
   candle closes. This is very conservative — could potentially use D's
   candle starting at D 08:00, but the current approach is safe.

B. Intra-Bar Bias: CLEAN
   - Entry gated by day_change (first bar of new day)
   - Signal: dvol_rank > 0.80 (long) or dvol_rank < 0.20 (short)
   - Both rank and z-score are from lagged daily data

C. Rolling Window Bias: CLEAN
   - Z-score: rolling(30, min_periods=10) on daily DVOL before shifting
   - Rank: rolling(30, min_periods=10) percentile rank before shifting
   - Both computed on daily data, include "today" in the window, but
     since the whole result is shifted +1 day, "today" in the context
     of hourly bars is actually yesterday's DVOL. CORRECT.

   Note on rank computation (lines 170-172):
     lambda x: (x[-1] >= x[:-1]).mean()
   This compares the current value to all prior values in the window.
   x[-1] IS included in the comparison base (it's the "current" day
   in the daily series). This is standard for percentile rank.

VERDICT: CLEAN
   DVOL daily data is properly lagged by +1 day for both the z-score
   and percentile rank signals. Entry on day_change.


----------------------------------------------------------------------
STRATEGY: s521_fg_sp500_trend
----------------------------------------------------------------------
FILE: strategies/s521_fg_sp500_trend.py
DATA SOURCES:
  - ctx.ind_1h['close'] (for bar count)
  - External: fear_greed_index.parquet (Crypto Fear & Greed Index)
  - External: sp500.parquet (SP500 close prices)
DAILY DATA: Yes — Fear & Greed value + SP500 5d ROC

A. Data Timing Bias: CLEAN
   _load_macro_data() at lines 97-144:

   Fear & Greed (lines 108-117):
   - Raw dates loaded from parquet
   - Lag applied at index creation:
       index=fg_df["date"].values + np.timedelta64(1, 'D')  (line 115)
   - This shifts the entire series: day D's FG value has index D+1
   - Different implementation than s517/s518/s519 (which copy + shift),
     but same effect. CORRECT.

   SP500 (lines 122-143):
   - 5-day ROC computed from daily closes
   - Lag applied at index creation:
       index=sp_df["date"].values + np.timedelta64(1, 'D')  (line 139)
   - Same approach as FG. CORRECT.

   _align_daily_to_1h() at lines 147-162:
   - Simple ffill to hourly bars — NO additional shift needed because
     the +1 day lag was already baked into the series index at load time.
   - Uses idx_1h.normalize() for alignment. CORRECT.

   Note: The docstring says "already lagged at source" (line 149). This
   is accurate — the lag is applied during _load_macro_data(), not during
   alignment. Different pattern from s514/s517/s518/s519 which apply the
   lag in the alignment function, but equally correct.

B. Intra-Bar Bias: CLEAN
   - Entry gated by day_change
   - FG and SP500 ROC are both lagged at source
   - At 00:00 of D+1, the signal uses FG(D) and SP500_ROC(D). These
     were both known by end of day D. CORRECT.

C. Rolling Window Bias: NOT APPLICABLE
   - No rolling windows in the strategy itself
   - SP500 ROC is a simple 5-day return (pct_change), not a rolling stat
   - FG is a raw value, no rolling transformation

VERDICT: CLEAN
   Both Fear & Greed and SP500 data are lagged +1 day at the index
   level during data loading. The alignment function simply forward-fills
   without adding additional lag. Entry on day_change.


========================================================================
  CROSS-STRATEGY OBSERVATIONS
========================================================================

1. LAG IMPLEMENTATION PATTERNS:
   Two patterns are used across the codebase:
   a) Shift-at-alignment: s514, s517, s518, s519
      - Load daily data with original dates
      - In alignment function: copy series, shift index +1 day, ffill
   b) Shift-at-load: s521
      - During load: create series with index = dates + 1 day
      - Alignment function just forward-fills (no additional shift)

   Both are correct. Pattern (a) is more explicit and self-documenting
   at the point of use. Pattern (b) is more compact but the lag is less
   visible in the alignment code. Recommend standardizing on pattern (a)
   for consistency, but this is cosmetic not a correctness issue.

2. ROLLING ZSCORE ON HOURLY-ALIGNED DAILY DATA:
   s514 and s518 compute rolling_zscore(divergence, 720) where divergence
   is daily data forward-filled to hourly. This means 720 hourly bars =
   30 days, but the underlying data only changes once per day (24 bars
   per unique value). The z-score computation is correct because:
   - The rolling window captures ~30 distinct daily values
   - Forward-filling creates 24 copies per day, but mean/std are
     mathematically equivalent whether computed on 720 hourly copies or
     30 daily values (each day contributes equally to the statistics)
   No bias, but slightly inefficient computation.

3. ENTRY TIMING CONSISTENCY:
   All strategies using daily data gate entries on day_change (first bar
   of each new day, 00:00 UTC). This is consistent and correct — the
   lagged daily signal for D first becomes available at D+1 00:00, which
   is exactly when day_change fires.

4. EDGE DETECTION:
   s514/s518 use a pattern: signal & (~prev_signal | day_change). This
   means the signal fires on:
   - First bar where the z-score crosses the threshold, OR
   - First bar of each new day while still above threshold
   The day_change component means re-entry is possible each day if the
   signal persists. This is intentional (documented in s514 docstring:
   "Entry only on first bar of each new day").

5. NO STRATEGIES USE HIGH/LOW FOR ENTRY PRICE:
   None of the audited strategies set entry_limit_price or use high/low
   for entry price computation. All use V4's default close[bar] entry.
   This eliminates a common intra-bar bias vector.


========================================================================
  SUMMARY
========================================================================

ALL 7 STRATEGIES: CLEAN

- s98, s513: Pure hourly technical strategies using only ctx.ind_1h.
  No daily data, no external data, no alignment issues possible.

- s514: L/S divergence with +1 day shift (bias fix applied 2026-04-04).
  Fix verified correct at _get_divergence_aligned() line 148-149.

- s517: SP500 + VIX macro data with +1 day shift on both series.
  Verified in _get_macro_aligned() lines 153-155 and 160-162.

- s518: L/S divergence + OI with generic _get_aligned() applying +1 day
  shift to both data streams. Verified at lines 148-153.

- s519: BTC DVOL with +1 day shift on both z-score and rank signals.
  Verified at _get_dvol_zscore_aligned() line 142 and
  _get_dvol_rank_aligned() line 175.

- s521: Fear & Greed + SP500 with +1 day lag baked into series index
  at load time. Verified at _load_macro_data() lines 115 and 139.

No forward bias or intra-bar trading issues found in any strategy.
"""
