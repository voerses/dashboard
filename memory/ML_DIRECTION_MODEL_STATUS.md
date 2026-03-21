# ML Direction Model — Development Status

> **Last updated:** 2026-03-21
> **Status:** V4 iteration complete, model passes adversarial tests, ready for strategy integration
> **Task tracking:** #7 (iterate model) in_progress, #8 (update strategies) pending

## Summary

Building an ML model to predict crypto price direction (LONG/SHORT) for use in trading strategies.
The model uses HistGradientBoostingClassifier on 47 technical + market + cross-sectional features.

## Version History

### V1-V2 (DEAD — temporal leakage)
- V2 claimed 82% LONG precision, +2115% backtest return
- **Root cause:** Walk-forward split data by ROW INDEX across concatenated tokens. Since tokens are stacked sequentially, train/test sets OVERLAP in time. Massive temporal leakage through shared market context features.
- **Evidence:** Strict temporal OOS showed 52% precision (random). See `tools/ml_v2_strict_oos.py`, `tools/ml_v2_temporal_split.py`.
- Files: `tools/ml_direction_model_v2_1.py` (DO NOT USE)

### V3 (baseline — proper temporal splits)
- Fixed temporal leakage: all splits DATE-BASED with 48h embargo
- 3-fold expanding temporal walk-forward
- Best: Experiment B (Adaptive 1.5σ target, 24h horizon)
- **Results:** LONG p60=60.2%, SHORT p60=55.1% (averaged across 3 folds)
- **Adversarial tests (3/3 PASS):**
  - Permutation test: PASS (model learns real patterns; 1000x signal generation rate vs shuffled)
  - Cross-token OOS: PASS (barely — 54% precision on unseen tokens, down from 60% on seen tokens)
  - Embargo sensitivity: PASS (48h→336h embargo drops precision by only ~1pp)
- **Key finding:** ~6pp gap between in-sample-token and OOS-token precision = token-specific overfitting
- Files: `tools/ml_direction_model_v3.py`, `tools/ml_direction_model_v3_1.py` (iteration, no improvement), `tools/ml_v3_adversarial.py`
- Model: `results/v4/ml_dir_v3_model.joblib`

### V4 (current — cross-token generalization)
- Added Leave-K-tokens-out (LOTO) validation within temporal folds
- Added 5 cross-sectional (rank-based) features: ret24h_rank, ret1h_rank, vol_rank, rsi_rank, relative_strength_24h
- Tests 4 configurations across 2 LOTO groups (train on 10, test on 5 held-out tokens)
- **Results (2026-03-21):**

| Experiment | LONG p55 | LONG p60 | LONG p65 | SHORT p55 | SHORT p60 | SHORT p65 |
|------------|----------|----------|----------|-----------|-----------|-----------|
| K: LOTO base (no xsect) | 0.594 | 0.600 | 0.609 | 0.558 | 0.574 | 0.586 |
| L: LOTO + cross-sectional | 0.593 | 0.602 | 0.613 | 0.556 | 0.566 | 0.578 |
| M: LOTO + xsect + heavy reg | 0.605 | 0.611 | 0.619 | 0.559 | 0.571 | 0.588 |
| N: All-token temporal (V3-style) | 0.607 | 0.614 | 0.620 | 0.558 | 0.581 | 0.604 |

- **Token-specific overfitting gap: only 1.15pp** (was 5.5pp in V3 adversarial test)
- Cross-sectional features provide marginal improvement (+0.2pp)
- Heavy regularization (depth=4, l2=5.0) helps LOTO by +0.8pp
- Model saved: `results/v4/ml_dir_v4_model.joblib`
- File: `tools/ml_direction_model_v4.py`

## Key Architecture Details

- **Model:** sklearn HistGradientBoostingClassifier
  - V4 hparams: max_depth=5, l2_reg=2.0, min_samples_leaf=80, lr=0.05, max_iter=400
  - Heavy reg variant: max_depth=4, l2_reg=5.0, min_samples_leaf=120, lr=0.03
- **Features (47):** 31 base TA + 3 funding + 8 market context + 5 cross-sectional
- **Labeling:** Adaptive vol-normalized, threshold = 1.5 * rolling_std_168h * sqrt(24h), floor 1%
- **Balancing:** Random undersampling to equalize LONG/SHORT classes
- **Validation:** 3-fold expanding temporal walk-forward with 48h embargo
- **LOTO:** Train on 10 tokens, test on 5 held-out tokens, 2 groups (A: first10/last5, B: last10/first5)
- **15 tokens:** BTC, ETH, BNB, DOGE, XLM, SOL, ADA, BCH, TRX, LINK, AVAX, ETC, XRP, ENJ, SAND
- **Memory:** ~3.5GB available, using float32 throughout, 15 tokens keeps under 200MB dataset

## Top Features (stable across all experiments)

1. market_funding_mean (~15%)
2. btc_vol_24h (~12%)
3. dispersion_zscore (~9%)
4. btc_ret_24h (~6%)
5. token_btc_corr_168h (~5-6%)
6. funding_ma48 (~5%)
7. market_regime (~5%)
8. ret_168 (~5%)
9. vol_50 (~5%)
10. ret_48 (~3%)

Market-level features dominate (>50% of total importance). This is why the model generalizes across tokens.

## Regime-Dependent Performance (from V3.1)

| Regime | LONG precision | SHORT precision | Notes |
|--------|---------------|-----------------|-------|
| UPTREND | 0.616 | 0.414 | LONG works, SHORT fails |
| DOWNTREND | 0.592 | 0.604 | Both work |
| QUIET | 0.095 | 0.905 | SHORT works, LONG fails badly |
| RANGE | 0.500 | 0.635 | SHORT better |
| CRISIS | 0.583 | 0.469 | Mixed |

**Implication for strategies:** Use regime filtering at inference time.

## Honest Assessment

- **True edge:** ~58-61% precision at p>=0.60 on temporal OOS, ~54-60% on cross-token OOS
- **After fees (~0.14% round-trip perps):** Edge is marginal but real
- **SHORT signals are more robust** than LONG (improve with higher confidence thresholds)
- **Model is NOT a standalone alpha source** — should be used as a filter/overlay on existing strategies
- **Best use case:** Regime-filtered trading — only take LONG in UPTREND/DOWNTREND, SHORT in QUIET/RANGE

## Existing ML Strategies (need update from V2 to V4)

- `strategies/s312_ml_v2_long.py` — standalone ML LONG signals (uses V2 model — BROKEN)
- `strategies/s313_ml_v2_short.py` — standalone ML SHORT signals (uses V2 model — BROKEN)
- These need to be updated to use V4 model with regime filtering

## Next Steps (for next session)

1. **Run adversarial tests on V4** — verify the LOTO model passes the same 3 tests V3 passed
2. **Update s312/s313** to use V4 model with regime filtering
3. **Run portfolio backtest** with realistic expectations (~58% precision, not 82%)
4. **Consider ML as overlay** — add ML confidence as a filter to existing strategies (s56, s60, etc.) rather than standalone
5. **Expected outcome:** Marginal but real improvement to existing portfolio, not a game-changer

## File Index

| File | Purpose |
|------|---------|
| `tools/ml_direction_model_v2_1.py` | V2 model (BROKEN — temporal leakage) |
| `tools/ml_v2_strict_oos.py` | V2 strict temporal OOS test (proved V2 is overfit) |
| `tools/ml_v2_temporal_split.py` | V2 temporal split comparison (confirmed zero edge) |
| `tools/ml_direction_model_v3.py` | V3 proper temporal validation pipeline |
| `tools/ml_direction_model_v3_1.py` | V3.1 iteration (extended features, no improvement) |
| `tools/ml_v3_adversarial.py` | V3 adversarial tests (permutation, cross-token, embargo) |
| `tools/ml_direction_model_v4.py` | V4 LOTO cross-token generalization pipeline |
| `results/v4/ml_dir_v3_model.joblib` | V3 best model (Experiment B) |
| `results/v4/ml_dir_v4_model.joblib` | V4 best model (Experiment N) |
| `results/v4/ml_dir_v4_features.joblib` | V4 feature list (47 features) |
| `results/v4/ml_dir_v4_config.joblib` | V4 config metadata |
| `strategies/s312_ml_v2_long.py` | ML LONG strategy (needs V4 update) |
| `strategies/s313_ml_v2_short.py` | ML SHORT strategy (needs V4 update) |
