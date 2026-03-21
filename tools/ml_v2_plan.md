# ML Direction Model V2 -- Converged Implementation Plan

## Adversarial Review of Research Proposals

Two research agents produced proposals. This document challenges each claim,
cuts over-engineering, and specifies the minimum viable improvement.

---

## CHALLENGE LOG

### 1. Will 18 new market features actually improve OOS performance, or just add noise?

**Verdict: Mixed. Some will help, some are redundant or noise.**

The core hypothesis is sound: the model has zero cross-asset awareness, so it
cannot distinguish "BTC is crashing and dragging everything down" from "this
token has a bearish RSI divergence." Adding BTC returns and market-level
context addresses a real information gap.

However, 18 features is too many to add at once. With ~2.3M total rows across
50 tokens but only ~500k-700k non-neutral labeled samples after the 3%
threshold filter, adding 18 features (34 -> 52) increases the feature-to-useful-
signal ratio without proportional sample growth. Tree models are somewhat
robust to irrelevant features, but each noisy feature still steals splits from
useful ones, diluting importance.

**Risk**: Features like `market_funding_skew`, `breadth_momentum`,
`regime_agreement`, and `token_relative_strength_168h` have low expected
signal-to-noise and could just add parameter space for overfitting.

### 2. What is the overfitting risk from adding 18 features to ~500k samples?

**Verdict: Moderate but manageable with discipline.**

500k non-neutral samples with 52 features gives a ratio of ~10,000 samples
per feature, which is adequate for tree models. The real risk is not
dimensionality per se -- it is that many of the proposed features are highly
correlated with each other or with existing features:

- `token_relative_strength_24h` = `ret_24` minus `market_avg_ret_24h`. Since
  `ret_24` is already a feature, this is just adding a linear transformation.
  The tree can learn this split already.
- `token_vs_dispersion` is defined identically to `token_relative_strength_24h`
  in the pseudocode (both are `token_ret_24h - market_avg_ret_24h`). This is
  literally the same feature proposed twice under different names.
- `token_funding_vs_market` = `funding` minus `market_funding_mean`. Again, a
  linear diff of existing vs new feature. Marginally useful but redundant.
- `regime_agreement` is a binary derived from `token_regime` and
  `market_regime`, both of which are already proposed features. The tree will
  learn this interaction naturally.

### 3. Is HistGBM actually better than RF for this problem size?

**Verdict: Yes, but the improvement is modest -- not the 3-7% claimed.**

HistGBM's advantages:
- Native NaN handling (eliminates `nan_to_num` which introduces bias by
  replacing missing data with 0)
- Early stopping (prevents overfitting without manual tuning)
- 10-50x faster than sklearn's `GradientBoostingClassifier`
- Better calibrated probabilities than RF
- L2 regularization on leaf values

HistGBM's risks for this problem:
- Boosting is more prone to fitting noise in the training distribution. In
  financial data where the test distribution shifts, this can hurt.
- The claimed 3-7% precision lift is optimistic. On noisy financial data,
  expect 1-3% at best, and only if the target and features are good.

**Net assessment**: Switch to HistGBM. It is strictly better than the current
`GradientBoostingClassifier` (same paradigm, 10-50x faster, native NaN). The
speed gain alone justifies it -- faster iteration means faster discovery of
what actually works.

### 4. Is the profitability target just curve-fitting to fee structure?

**Verdict: Partially yes. The concept is right but the implementation is
dangerous.**

The net-of-costs target (`compute_trade_pnl_label`) bakes in specific fee
assumptions (10 bps exchange + 5 bps slippage + 50 bps min edge). This means:

- If fee assumptions are wrong (e.g., maker fees are 2 bps, not 5 bps per
  side), the model is trained on the wrong labels.
- The 50 bps min edge is arbitrary and acts as a stronger threshold than the
  current 3% (~300 bps) threshold. The effect is to discard more samples as
  neutral, reducing training data.
- Funding cost inclusion requires `funding_1h` data which may be spotty for
  some tokens, introducing label noise.
- The target conflates two things: direction prediction (which the model
  controls) and cost estimation (which is an external parameter). Better to
  keep them separate.

**What to do instead**: Keep the direction target for now. The "works in
trending, fails in ranging" problem is about FEATURES and REGIME AWARENESS,
not about the target. Changing the target AND the features AND the model
simultaneously makes it impossible to diagnose what helped. Fix one thing at
a time.

### 5. What is the simplest change that would fix the actual problem?

**Verdict: Add BTC returns + market regime as features. That is it for Phase 1.**

The stated problem is "fails in recent bear/range markets." The root cause is
that the model sees a token's RSI at 30 and thinks "oversold, predict UP" --
but the whole market is in a liquidation cascade. Adding `btc_ret_1h`,
`btc_ret_24h`, and a regime indicator gives the model the ability to say
"RSI 30 during a market crash is NOT oversold, it is momentum."

This is a 4-feature addition (btc_ret_1h, btc_ret_24h, btc_vol_24h,
market_regime) that can be implemented in under 100 lines of code. If this
does not help, adding 14 more features will not help either.

### 6. Are any features redundant with existing features?

**Verdict: Yes, several.**

Redundancies identified:
- `token_relative_strength_24h` = `ret_24 - market_avg_ret_24h` (linear
  transform of existing `ret_24`)
- `token_vs_dispersion` = same formula as `token_relative_strength_24h` (copy-
  paste error in proposal)
- `token_funding_vs_market` = `funding - market_funding_mean` (tree can learn
  this from existing `funding` + new `market_funding_mean`)
- `regime_agreement` = derived from `token_regime == market_regime` (tree
  learns this interaction trivially)
- `token_btc_beta_168h` and `token_btc_corr_168h` are highly correlated with
  each other (beta = corr * vol_ratio). Keep only corr.
- `dispersion_zscore` is a normalization of `market_dispersion_24h`. Keep only
  the z-score (more comparable across time).
- `breadth_momentum` is a 24h diff of `market_breadth_ema20`. With both in the
  model, this is redundant -- the tree can compute the diff via two splits.

### 7. Is CPCV overkill for a 60/40 walk-forward setup?

**Verdict: Yes, for Phase 1. The 60/40 walk-forward is fine as a first pass.**

CPCV with 6 groups and 2 test groups gives 15 folds. This is excellent for
publication-quality research but introduces complexity:
- 15x training time (75s vs 5s)
- Aggregating OOS predictions across overlapping folds is non-trivial
- Each fold has ~4 years of training and ~2 years of test. The 2020-2021 folds
  will dominate because the market was more volatile (more non-neutral labels).
- CPCV assumes stationarity within groups. Crypto is decidedly non-stationary.

**What to do instead**: Use expanding walk-forward with 3 folds:
```
Fold 1: Train 2020-2022, Test 2022-2023
Fold 2: Train 2020-2023, Test 2023-2024
Fold 3: Train 2020-2024, Test 2024-2026
```
This tests the thing we actually care about: does the model degrade as we move
toward recent data? If the model works on Fold 3 (the most recent test), it is
likely robust. If it only works on Fold 1, it is a bull-market artifact.

Total training time: 3 folds x ~5s = 15s. Much faster iteration than CPCV.

---

## KEEP (highest conviction changes)

### K1. Add BTC market context features (4 features)
**Rationale**: The single highest-value information gap. BTC drives 60-90% of
alt moves in high-correlation regimes. The model literally cannot see this.

Features to add:
- `btc_ret_1h`: BTC 1-hour log return (immediate market direction)
- `btc_ret_24h`: BTC 24-hour log return (market trend)
- `btc_vol_24h`: BTC 24h realized volatility (risk level)
- `market_regime`: BTC-derived regime (integer 0-4, from detect_daily_regime
  logic applied to BTC daily bars)

### K2. Switch from RandomForest to HistGradientBoostingClassifier
**Rationale**: Strictly superior to both current RF and current sklearn GBM.
10-50x faster than GradientBoostingClassifier, native NaN handling, early
stopping, better probability calibration. Same sklearn API.

### K3. Add token-BTC correlation feature (1 feature)
**Rationale**: Tells the model whether to trust token-specific features or
defer to BTC. When correlation is 0.95, RSI/MACD are noise. When 0.3, they
are signal. This is the key conditioning variable.

Feature: `token_btc_corr_168h`: 7-day rolling correlation of token 1h returns
vs BTC 1h returns.

### K4. Add market dispersion feature (1 feature)
**Rationale**: Already validated by s100_dispersion_momentum strategy. High
dispersion = alpha opportunity, low dispersion = beta market. Use z-scored
version for cross-regime comparability.

Feature: `dispersion_zscore`: rolling z-score of cross-sectional return std.

### K5. Add market breadth feature (1 feature)
**Rationale**: Classic market health signal. Cheap to compute, high information
content about market-wide trend strength.

Feature: `market_breadth_ema20`: fraction of top 30 tokens above their 20-bar
EMA.

### K6. Expanding walk-forward validation (3 folds)
**Rationale**: Single 60/40 split gives one point estimate. Three expanding
folds show whether performance is stable, improving, or degrading over time.
Critical for detecting bull-market overfitting.

**Total new features: 7** (34 existing + 7 new = 41 total)

---

## CUT (over-engineered or risky)

### X1. Profitability target (compute_trade_pnl_label) -- CUT for Phase 1
**Rationale**: Changes the target AND bakes in arbitrary fee/slippage
assumptions. Conflates direction prediction with cost estimation. Cannot
isolate feature improvement if the target also changes. Defer to Phase 2
after validating that market features provide lift.

### X2. CPCV validation -- CUT
**Rationale**: 15 folds is overkill. Expanding walk-forward with 3 folds is
simpler, faster, and tests the specific question we care about (recent-market
degradation). If we need CPCV later, the code already exists in v4/cpcv.py.

### X3. Probability calibration (CalibratedClassifierCV) -- CUT for Phase 1
**Rationale**: Premature optimization. Get the features and model right first.
Calibration on top of a bad model is polishing a turd. HistGBM's native
probabilities are better calibrated than RF anyway.

### X4. Stacking / ensemble -- CUT
**Rationale**: Two-layer ensemble on a noisy target is complexity for
complexity's sake. A single well-tuned HistGBM is more interpretable and
easier to debug.

### X5. token_relative_strength_24h / token_relative_strength_168h -- CUT
**Rationale**: `ret_24 - market_avg_ret_24h` is a linear transform of an
existing feature. Tree models learn this trivially. Does not warrant a
separate feature.

### X6. token_vs_dispersion -- CUT
**Rationale**: Same formula as token_relative_strength_24h (copy-paste in
proposal). Literally a duplicate.

### X7. token_funding_vs_market -- CUT
**Rationale**: `funding - market_funding_mean`. The tree can learn this from
existing `funding` plus new `market_funding_mean` if we add it. Not worth
the feature slot.

### X8. regime_agreement -- CUT
**Rationale**: Binary derived from two features already in the model. Tree
splits learn this automatically.

### X9. token_btc_beta_168h -- CUT
**Rationale**: Highly correlated with `token_btc_corr_168h` (beta = corr *
vol_ratio, and vol_ratio is already a feature). Keep corr, cut beta.

### X10. market_funding_skew -- CUT
**Rationale**: Low expected signal. The simplified (mean-median)/std formula
is noisy. Market funding mean is sufficient.

### X11. breadth_momentum -- CUT
**Rationale**: 24h diff of breadth. The tree can compute this implicitly from
breadth at t vs breadth at t-24 (approximated by breadth level changes). Not
worth the feature slot.

### X12. token_regime (per-token regime from hourly data) -- CUT
**Rationale**: The existing features (adx, ema_dist_10/20/50, vol_ratio_5_20)
already encode regime information per-token. detect_daily_regime is just a
threshold-based function on these same indicators. Adding it as a separate
feature is redundant with its inputs which are already in the model. Keep
market_regime (BTC-derived) which is genuinely new information.

### X13. Adaptive threshold target -- DEFER to Phase 2
**Rationale**: Interesting idea (vol-normalized threshold) but changes the
target, which makes it impossible to attribute improvements to features vs
target change. Test features first.

### X14. Regression head for PnL magnitude -- DEFER to Phase 3
**Rationale**: Requires fundamentally different model architecture and
evaluation framework. Not appropriate for Phase 1.

### X15. Dynamic / per-regime probability thresholds -- DEFER to Phase 2
**Rationale**: Requires calibrated probabilities and enough data per regime
to estimate thresholds reliably. Get the model right first.

---

## PHASE 1 vs PHASE 2

### Phase 1 (implement now -- smallest change, biggest impact)

**Goal**: Add 7 market-context features + switch to HistGBM. Validate with
3-fold expanding walk-forward. Compare precision at p>=0.60 against baseline RF.

**Success criteria**: Phase 1 is successful if ANY of the following:
1. OOS precision at p>=0.60 improves by >= 2pp on Fold 3 (2024-2026 test)
2. OOS precision at p>=0.60 on Fold 3 exceeds 0.55 (minimum viable)
3. Market features appear in the top 10 feature importances

**Kill criteria**: Phase 1 is a failure if:
1. OOS precision degrades on Fold 3 relative to the current RF baseline
2. No market feature appears in the top 15 importances
3. Feature importances are dominated by noise features

### Phase 2 (if Phase 1 shows lift)

- Add market_funding_mean as a feature (aggregate positioning signal)
- Switch to adaptive threshold target (vol-normalized)
- Add probability calibration
- Add per-regime precision reporting
- Experiment with sample weighting by recency

### Phase 3 (if Phase 2 shows lift)

- Profitability target (net-of-costs)
- CPCV validation for publication-quality analysis
- Stacking ensemble (if single-model ceiling is reached)
- Dynamic Kelly sizing from model confidence

---

## IMPLEMENTATION SPEC (Phase 1 only)

### File to modify
`/workspace/crypto_backtest/tools/ml_direction_model.py`

### New function: `build_market_context(data_dir, top_n=30)`

**Purpose**: Precompute shared market-level features once before the token
loop.

**Returns**: pandas DataFrame indexed by DatetimeIndex (BTC's index) with
columns:
- `btc_ret_1h`: float64
- `btc_ret_24h`: float64
- `btc_vol_24h`: float64
- `market_regime`: int8 (0=crisis, 1=quiet, 2=uptrend, 3=range, 4=downtrend)
- `market_breadth_ema20`: float64 (range 0-1)
- `dispersion_zscore`: float64
- `_btc_ret_1h_series`: float64 (private, for per-token corr computation)

**Implementation notes**:
- Load BTC parquet, compute btc_ret_1h and btc_ret_24h via log returns
- btc_vol_24h = rolling_std(btc_ret_1h, 24)
- For market_regime: resample BTC to daily, compute ADX + EMAs using existing
  helper functions, apply detect_daily_regime logic (inline, not importing
  from engine.py to avoid numba dependency). Forward-fill to hourly.
- For market_breadth: load top_n token close prices, align to BTC index,
  compute EMA20 for each, count fraction above EMA20 at each bar.
- For dispersion_zscore: compute 24h log returns for top_n tokens, take
  cross-sectional nanstd per bar, then z-score against 720-bar rolling
  mean/std.
- Total computation: ~5-10 seconds (195 parquet reads for breadth/dispersion
  are the bottleneck, but only reading 30 tokens).

### Modified function: `build_token_features_direction(token, horizon, threshold_pct, market_ctx=None)`

**Changes**:
- Accept optional `market_ctx` DataFrame parameter
- After computing existing 34 features, if `market_ctx` is not None:
  - Align market_ctx to token's DatetimeIndex via `reindex(method='ffill')`
  - Copy shared features (btc_ret_1h, btc_ret_24h, btc_vol_24h,
    market_regime, market_breadth_ema20, dispersion_zscore) into features dict
  - Compute `token_btc_corr_168h`:
    ```python
    token_ret_1h = np.zeros(n)
    token_ret_1h[1:] = np.log(close[1:] / close[:-1])
    btc_ret_aligned = ctx_aligned['_btc_ret_1h_series'].values
    corr = pd.Series(token_ret_1h).rolling(168, min_periods=48).corr(
        pd.Series(btc_ret_aligned)).values
    features['token_btc_corr_168h'] = np.nan_to_num(corr, nan=0.0)
    ```
- nan_to_num all new features before adding to dict
- Feature names are sorted alphabetically as before, yielding 41 total

### Modified function: `train_and_evaluate(X, y, feat_names, horizon_name)`

**Changes**:
- Replace RandomForestClassifier with HistGradientBoostingClassifier
- Remove StandardScaler (HistGBM does not need scaling)
- Update hyperparameters

**Model config (exact hyperparameters)**:
```python
from sklearn.ensemble import HistGradientBoostingClassifier

model = HistGradientBoostingClassifier(
    max_iter=400,
    max_depth=6,
    learning_rate=0.05,
    min_samples_leaf=50,
    max_leaf_nodes=31,
    l2_regularization=1.0,
    max_bins=128,
    early_stopping=True,
    validation_fraction=0.15,
    n_iter_no_change=20,
    random_state=42,
    verbose=0,
)
```

**Rationale for each hyperparameter**:
- `max_iter=400`: enough trees for convergence, early stopping prevents
  overfit. Not 500 (slower, marginal gain).
- `max_depth=6`: one deeper than current GBM (5), allows slightly more
  complex interactions for the new market features.
- `learning_rate=0.05`: slightly higher than the proposal's 0.03 because
  with 400 max iterations and early stopping, 0.03 would require 600+ trees
  to converge. 0.05 balances convergence speed and generalization.
- `min_samples_leaf=50`: aggressive regularization. Financial data is noisy;
  leaves with <50 samples are likely fitting noise.
- `max_leaf_nodes=31`: caps tree complexity. With depth 6, max possible is
  64 leaves; 31 forces pruning.
- `l2_regularization=1.0`: penalizes extreme leaf values, which are common
  in imbalanced financial data.
- `max_bins=128`: coarser histograms than default 255. Faster and acts as
  regularization (smoother split boundaries).
- `early_stopping=True, validation_fraction=0.15, n_iter_no_change=20`:
  hold out 15% of training data for early stopping. Stop after 20 iterations
  without improvement. This is the primary overfitting defense.

### Training target (exact formula)
**No change.** Keep the existing 3% binary target:
```python
labels[fwd_ret > 0.03] = 1
labels[fwd_ret < -0.03] = -1
labels[-horizon:] = 0
```
Changing the target in Phase 1 makes it impossible to attribute improvements.

### Validation approach (exact specification)

Replace single 60/40 split with 3-fold expanding walk-forward:

```python
# After combining all tokens into X_nz, y_nz (non-neutral only)
# sorted by time (tokens interleaved chronologically)

n = len(y_nz)
folds = [
    # Fold 1: Train ~first 33%, Test next 17%
    (slice(0, int(n * 0.33)), slice(int(n * 0.33), int(n * 0.50))),
    # Fold 2: Train first 50%, Test next 17%
    (slice(0, int(n * 0.50)), slice(int(n * 0.50), int(n * 67))),
    # Fold 3: Train first 67%, Test last 33%
    (slice(0, int(n * 0.67)), slice(int(n * 0.67), n)),
]
```

**IMPORTANT**: The current pipeline pools tokens then sorts by the original
index position (tokens are loaded in order, not interleaved by time). This
means the "first 60%" is not strictly the first 60% of calendar time -- it
is the first 60% of stacked-token rows, which approximately corresponds to
earlier data since tokens are loaded by data size (longest first, most overlap
with early dates).

For Phase 1, keep this approximate chronological ordering. It is good enough.
True temporal sorting across tokens would require storing timestamps alongside
features, which is a Phase 2 improvement.

### Evaluation (exact specification)

For each fold:
1. Balance training set via existing `undersample_balance()`
2. Train HistGBM (no scaler needed)
3. Predict on test set
4. Evaluate using existing `evaluate_direction()` at thresholds [0.55, 0.60, 0.65, 0.70]
5. Report feature importances

Additionally report:
- Per-fold precision at p>=0.60 (critical for detecting temporal degradation)
- Feature importance rank of each new market feature
- Comparison table: RF baseline vs HistGBM+features on same fold splits

### Modified function: `run_direction_research()`

**Changes**:
1. Call `build_market_context()` once at the start
2. Pass `market_ctx` to `build_token_features_direction()` in the token loop
3. Use expanding walk-forward instead of single 60/40 split
4. Run a baseline RF model on Fold 3 first (for comparison), then run
   HistGBM+features on all 3 folds
5. Save model only if Fold 3 precision > 0.55 (same threshold as current)
6. Print comparison table at the end

### Feature list (final, 41 features, no duplicates)

Existing 31 base features (unchanged):
```
ret_1, ret_4, ret_12, ret_24, ret_48, ret_168
ema_dist_10, ema_dist_20, ema_dist_50, ema_align
macd_norm, macd_hist_norm
rsi, rsi_mom
bb_pct, bb_width
atr_pct
vol_5, vol_20, vol_50, vol_ratio_5_20
vol_ratio
adx, plus_di, minus_di
donch_pos
body_pct, wick_ratio
consec
dist_high, dist_low
```

Existing 3 funding features (unchanged):
```
funding, funding_ma8, funding_ma48
```

New 7 market context features:
```
btc_ret_1h          -- BTC 1h log return (shared)
btc_ret_24h         -- BTC 24h log return (shared)
btc_vol_24h         -- BTC 24h realized vol (shared)
market_regime       -- BTC-derived regime integer 0-4 (shared)
market_breadth_ema20 -- fraction of top 30 tokens above EMA20 (shared)
dispersion_zscore   -- cross-sectional return dispersion z-score (shared)
token_btc_corr_168h -- 7-day rolling correlation with BTC (per-token)
```

### Files NOT to modify
- `v4/engine.py` -- do not import from here; inline the regime logic
- `v4/cpcv.py` -- not used in Phase 1
- `tools/ml_direction_model_gb.py` -- leave as-is, update after Phase 1
  validates
- `v4/sizing.py` -- no changes to position sizing in Phase 1

### Estimated implementation time
- `build_market_context()`: ~60 lines, ~30 minutes
- Feature integration in `build_token_features_direction()`: ~30 lines, ~15 minutes
- Model swap to HistGBM: ~10 lines, ~10 minutes
- Validation loop change: ~40 lines, ~20 minutes
- Comparison reporting: ~20 lines, ~10 minutes
- Testing and debugging: ~30 minutes
- **Total: ~2 hours**

### Risk mitigation
1. Run baseline RF on the same 3-fold splits FIRST to establish a comparison point
2. If HistGBM+features shows no improvement on any fold, STOP and investigate
   feature importances before adding more complexity
3. If market features have negligible importance (<1%), the hypothesis is wrong
   and we should not proceed to Phase 2
4. Save both the baseline RF results and HistGBM results for comparison

---

## Summary

The proposals were largely correct in diagnosis (model needs market context)
but over-engineered in prescription (18 features, CPCV, profitability target,
calibration, stacking). The minimum viable improvement is:

1. Add 7 carefully selected market features (not 18)
2. Switch to HistGBM (not ensemble)
3. Use 3-fold expanding walk-forward (not CPCV)
4. Keep the existing 3% target (not profitability target)
5. Measure lift on recent data (Fold 3) specifically

If this works, Phase 2 adds more features and changes the target. If it does
not work, the fundamental hypothesis (market context helps token prediction)
is invalidated and we need a different approach -- not more features.
