# R204: Per-Token Signal Scan — Next Session Brief

## Mission
Scan ALL 122 features × ALL 26+ tokens. For each token, identify which signals
have predictive power (IC > 0.03, walk-forward stable). Group tokens by which
signals work on them. Build token-specific or group-specific strategies.

## Why
- s514 focused on ONE signal (L/S divergence) on 28 curated tokens
- But different tokens may respond to different signals (some to positioning,
  some to macro, some to liquidations, some to funding)
- A per-token signal scan would reveal the full opportunity set

## Approach
1. For each token × each feature: compute trailing IC at 1d, 3d, 7d, 14d horizons
2. Walk-forward validate: does IC persist across 4 temporal quarters?
3. Build a TOKEN × SIGNAL matrix showing where edge exists
4. Cluster tokens by signal profile (which signals work on which tokens)
5. For each cluster: design optimal strategy using the signals that work for that group

## Data Available
- Feature matrix: data/ml_features/feature_matrix.parquet (122 features, 26 tokens)
- Need to EXPAND to all 229 tokens with L/S data (feature matrix currently only has 26)
- 5-min L/S data: data/alternative/binance_metrics/5min/ (downloading, ~2GB)
- All other data sources: see .claude/.strategy-mission for full inventory

## Key Constraint
- ALL analysis must use properly lagged data (no look-ahead)
- IC computation: feature at T-1 vs return at T to T+horizon
- Walk-forward: 4 temporal quarters, rule must pass 3+/4

## What We Know So Far (from R201)
- DVOL (implied vol) has highest IC across tokens (+0.094)
- Macro (SP500, VIX) has broad cross-token power
- Positioning features are token-specific (work on some, not others)
- Unbiased OOS IC for positioning: mean -0.076 (16 tokens), but ranges from -0.262 (ARB) to +0.120 (BNB)
- 8/16 ML rules pass walk-forward validation

## Session State
- s514 bias FIXED (1-day lag applied)
- s514 honest results: +76.2% OOS monthly at 3x, 5/12 months positive
- 5-min data ~95% downloaded
- All ML discovery scripts at research/R200-R203
- Strategy mission at .claude/.strategy-mission
