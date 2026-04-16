# Session Summary — 2026-04-04
> Complete research session: strategy development, ML discovery, portfolio construction

## Final Portfolio: s513 + s514 + s520

**OOS Monthly (12 months, V4 engine, bias-free): +119.9%, 11/12 months positive**

| Month | Return | MaxDD | Equity |
|-------|--------|-------|--------|
| May 25 | -2.8% | -9.1% | $97,177 |
| Jun 25 | +0.5% | -2.8% | $97,662 |
| Jul 25 | +8.1% | -3.9% | $105,537 |
| Aug 25 | +11.9% | -3.9% | $118,045 |
| Sep 25 | +7.7% | -2.2% | $127,009 |
| Oct 25 | +11.0% | -5.1% | $140,943 |
| Nov 25 | +6.0% | -4.2% | $149,305 |
| Dec 25 | +5.3% | -3.3% | $157,114 |
| Jan 26 | +7.8% | -2.6% | $169,248 |
| Feb 26 | +23.4% | -2.4% | $208,580 |
| Mar 26 | +2.0% | -2.3% | $212,669 |
| Apr 26 | +3.4% | -2.3% | $219,882 |

### Portfolio Legs

| Strategy | Signal Type | OOS Return | Sharpe | MaxDD | Tokens | Bias |
|----------|-----------|------------|--------|-------|--------|------|
| s513 | Tech momentum (MACD+RSI+Donchian) | +161% | 2.67 | -15% | Ultra-liquid ($1B+ ADV) | CLEAN |
| s514 | Positioning MR (L/S divergence) | +76% | ~1.2 | -24% | 28 curated | CLEAN (fixed) |
| s520 | Per-token optimized (5-min data) | +30% | 1.35 | -6% | 7 tokens (JUP, ARB, etc) | CLEAN |

### Correlations (monthly returns)
- s513 vs s514: 0.257 (low)
- s513 vs s520: 0.017 (essentially zero)
- s514 vs s520: -0.221 (negatively correlated)

### Leverage Projections (from individual strategy monthly returns)
| Portfolio Leverage | Projected Return | Projected MaxDD |
|-------------------|-----------------|-----------------|
| 1x (current) | +120% | -9% |
| 2x | ~280% | ~18% |
| 3x | ~460% | ~27% |

## Critical Bug Found & Fixed

**s514 look-ahead bias in daily L/S data alignment.**
- Bug: daily value for date D used at D 00:00 (before data available)
- Fix: +1 day shift in `_get_divergence_aligned()` (line 149)
- Impact: +678% (biased) → +76% (honest)
- All L/S strategies (s506-s520) fixed

## Bias Audit — All Active Strategies CLEAN

| Strategy | Uses Daily Data | Lag Correct | Entry Timing | Verdict |
|----------|----------------|-------------|-------------|---------|
| s98 | No | N/A | Close[T] from hourly indicators | CLEAN |
| s513 | No | N/A | Close[T], Donchian uses prev bar | CLEAN |
| s514 | Yes (L/S) | +1 day shift | Day boundary, lagged | CLEAN |
| s517 | Yes (SP500+VIX) | +1 day shift | Day boundary, lagged | CLEAN |
| s518 | Yes (L/S+OI) | +1 day shift | Day boundary, lagged | CLEAN |
| s519 | Yes (DVOL) | +1 day shift | Day boundary, lagged | CLEAN |
| s520 | Yes (5-min L/S) | +1 day shift | Day boundary, lagged | CLEAN |
| s521 | Yes (FG+SP500) | +1 day shift | KILLED (poor perf) | N/A |

## ML Alpha Discovery Pipeline (R200-R205)

### R200: Feature Engineering
- 122 features × 26 tokens × 44K rows
- Sources: positioning, OI, taker, price, funding, macro, DVOL, FG, ETF, liquidations
- All lagged 1 day, stored at: data/ml_features/feature_matrix.parquet

### R201: LightGBM + SHAP
- OOS IC: 0.039 (weak but significant)
- Top features: DVOL z-scores (+0.094), SP500 ROC (+0.080), div_pctile90 (-0.057)
- Models stop early (3-22 iterations) — signal is threshold-based, not complex

### R202-R203: Rule Validation
- 8/16 rules pass walk-forward (3+/4 quarters)
- Best: fg_bull+sp500_mom (Sharpe 1.99, 4/4 WF, 10/10 tokens)
- Only pos_bear_sizing overlay helps s514 (+0.08 Sharpe)

### R204: Per-Token Signal Scan
- 3 clusters: MACRO (13), POSITIONING (11), VOLATILITY (2)
- All 26 tokens have WF-validated signals
- IC matrices: data/ml_features/R204_*.parquet

### R205: 5-min Rolling Signal Scan
- 168h (1 week) window dominates — NOT short windows
- Best: JUP oi_roc_168h IC=-0.181 (4/4 WF)
- Per-token sign matters: SOL=momentum, most=contrarian
- data/ml_features/R205_rolling_signal_scan.parquet

## Other Strategies Tested

| Strategy | Signal | Result | Verdict |
|----------|--------|--------|---------|
| s513 triple trigger | MACD+RSI+Donchian, 3x | +161% OOS | **PORTFOLIO LEG** |
| s514 L/S divergence | Positioning MR, 3x | +76% OOS | **PORTFOLIO LEG** |
| s520 per-token | 5-min optimized, 3x | +30% OOS | **PORTFOLIO LEG** |
| s518 positioning+OI | L/S + OI confirm | +20% OOS, Sharpe 1.48 | Good risk, low return |
| s517 macro | SP500+VIX timing | +13% OOS | Diversifier only |
| s519 vol | DVOL-based BTC+SOL | -12% L12M | Regime-dependent, KILLED |
| s521 FG+SP500 | Sentiment trend | -58% L12M | KILLED |
| s98 MACD squeeze | Original s513 base | +119% L12M | Subsumed by s513 |

## Data Infrastructure Built

### New Downloads
- 5-min L/S metrics: 227 symbols, 42.5M rows, 2.3GB (data/alternative/binance_metrics/5min/)
- Daily L/S: expanded 29 → 229 symbols
- Per-metric parquets: data/alternative/binance_metrics/by_metric/
- Macro backfilled through 2026-04-02 (SP500, VIX, Gold, Oil, DXY, US10Y, FG, ETF)

### Fetcher Scripts
- scripts/fetch_metrics_bulk.py — parallel daily L/S fetcher (229 tokens)
- scripts/fetch_metrics_5min.py — parallel 5-min L/S fetcher (227 tokens)
- scripts/backfill_macro.py — macro + sentiment backfill

## Strategy Files Created
- s513_triple_trigger_swing.py — 3x, MACD+RSI+Donchian, $1B+ ADV filter
- s514_ls_div_leveraged.py — 3x, L/S divergence, 28-token allowlist, BIAS FIXED
- s515_ls_div_short.py / s516_ls_div_long.py — short/long split variants
- s514c_ls_div_core11.py / s514w_ls_div_wide38.py — token set variants
- s517_macro_cluster.py — SP500+VIX contrarian on 13 macro-responsive tokens
- s518_positioning_cluster.py — L/S+OI dual filter on 11 positioning tokens
- s519_vol_cluster.py — DVOL rank on BTC+SOL
- s520_per_token_optimized.py — per-token signal from 5-min data, 7 tokens
- s521_fg_sp500_trend.py — FG+SP500 trend (KILLED)

## Research Scripts
- R200_ml_feature_engineering.py — 122-feature matrix builder
- R201_ml_model_train.py — LightGBM + SHAP discovery
- R202_R203_rule_validation.py — Walk-forward rule testing
- R204_per_token_signal_scan.py — IC matrix across all tokens
- R205_rolling_signal_scan.py — 5-min data multi-window scan
- R206_portfolio_combination.py — Portfolio combination testing
- R207_mass_strategy_screen.py — Mass screen of all 236 strategies (running)
- BIAS_AUDIT_ALL_STRATEGIES.py — Comprehensive bias verification
- S514_SESSION_PLAN.md — Full session plan and ML mission
- s514_bias_audit.py — Original bias discovery script
- Multiple s514 optimization scripts (window, threshold, conviction, regime, overlay, etc.)

## Key Findings (numbered, for RESEARCH_STATUS.md)
138. s514 daily L/S data had look-ahead bias — daily mean for D used at D 00:00. Fixed with +1 day shift.
139. Honest s514 = +76% OOS at 3x. Short side lost most edge after fix (biased +$317K → unbiased -$3K).
140. 5-min L/S data: 168h rolling window dominates. Short windows fail walk-forward.
141. ML discovery (LightGBM): OOS IC=0.039. Top features are DVOL and macro, not positioning.
142. Signal is self-contained: regime, S/R, VRP, trend overlays ALL hurt s514.
143. Token selection: OI is best predictor (2.83x winner/loser ratio). Static 28 beats dynamic rotation.
144. s513 (triple trigger swing) is best single strategy: +161% OOS, Sharpe 2.67, CLEAN bias audit.
145. s513 + s514 + s520 portfolio: +120% OOS, 11/12 months positive, max DD -9%. All strategies bias-free.
146. Per-token signal profiles cluster into 3 groups: MACRO (13), POSITIONING (11), VOL (2).

## Next Session Priorities
1. Check mass screen results (R207) for hidden gems in old strategies
2. Test portfolio at higher effective leverage (increase capital allocation per leg)
3. Expand R205 to all 227 tokens (currently 10)
4. Implement ML token selection (LambdaMART) for dynamic optimization
5. Consider combining s98 (7x) with the portfolio for additional return
6. Paper trading deployment planning for the 3-strategy portfolio
