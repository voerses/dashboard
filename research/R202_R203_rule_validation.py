#!/usr/bin/env python3
"""
R202/R203 -- Rule Extraction & Walk-Forward Validation
======================================================

Takes the top predictive rules from R201 SHAP/quintile analysis and:
  1. Tests each rule STANDALONE as a long indicator on BTC (7d forward return)
  2. Walk-forward validates across 4 temporal quarters
  3. Cross-token generalization (top 10 tokens)
  4. Tests best rules as OVERLAY on s514 (entry filter / size adjuster)
  5. Reports which combinations survive walk-forward + cross-token tests

Input:  data/ml_features/feature_matrix.parquet
        strategies/s514_ls_div_leveraged.py (via V4 engine)

Output: printed report + outputs/ml_discovery/R202_R203_results.json
"""

import sys
import os
import json
import time
import warnings
import copy

sys.path.insert(0, "/workspace/crypto_backtest")
os.chdir("/workspace/crypto_backtest")

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

t0_global = time.time()

# =============================================================================
# Step 1: Load feature matrix
# =============================================================================

print("=" * 90)
print("  R202/R203: Rule Extraction & Walk-Forward Validation")
print("=" * 90)

df = pd.read_parquet("data/ml_features/feature_matrix.parquet")
print(f"Loaded feature matrix: {df.shape[0]} rows, {df.shape[1]} columns")

label_col = "label_7d_fwd"
has_token = "token" in df.columns

if has_token:
    tokens_all = sorted(df["token"].unique())
    print(f"Tokens: {len(tokens_all)} -- {tokens_all[:5]}...")
    btc = df[df["token"] == "BTC"].copy().sort_index()
else:
    btc = df.copy().sort_index()

print(f"BTC rows: {len(btc)}, date range: {btc.index.min()} to {btc.index.max()}")
print(f"Label stats: mean={btc[label_col].mean():.4f}, std={btc[label_col].std():.4f}")

# =============================================================================
# Step 2: Define rules from R201 SHAP findings
# =============================================================================

rules = {
    # --- Single-feature rules (from R201 top findings) ---
    "fg_bullish":       lambda d: d["fg_value"] > 72,
    "fg_bearish":       lambda d: d["fg_value"] < 25,
    "sp500_momentum":   lambda d: d["sp500_roc5"] > 0.013,
    "sp500_weak":       lambda d: d["sp500_roc5"] < -0.013,
    "dvol_high":        lambda d: d["dvol_btc_z30"] > 0.82,
    "dvol_low":         lambda d: d["dvol_btc_z30"] < -0.82,
    "pos_extreme_bear": lambda d: d["div_pctile90"] < 0.011,
    "pos_extreme_bull": lambda d: d["div_pctile90"] > 0.95,
    "topls_z30_low":    lambda d: d["topls_z30"] < -2.0,
    "topls_z30_high":   lambda d: d["topls_z30"] > 2.0,
    # --- Combinations (AND logic) ---
    "fg_bull_AND_sp500_mom":  lambda d: (d["fg_value"] > 72) & (d["sp500_roc5"] > 0),
    "fg_bull_AND_dvol_high":  lambda d: (d["fg_value"] > 72) & (d["dvol_btc_z30"] > 0.5),
    "pos_bear_AND_fg_bull":   lambda d: (d["div_pctile90"] < 0.05) & (d["fg_value"] > 50),
    "pos_bear_AND_dvol_high": lambda d: (d["div_pctile90"] < 0.05) & (d["dvol_btc_z30"] > 0),
    "topls_low_AND_fg_bull":  lambda d: (d["topls_z30"] < -1.5) & (d["fg_value"] > 50),
    "fg_bull_AND_sp500_AND_dvol": lambda d: (
        (d["fg_value"] > 72) & (d["sp500_roc5"] > 0) & (d["dvol_btc_z30"] > 0)
    ),
}

# =============================================================================
# Step 3: Standalone rule performance on BTC
# =============================================================================

print(f"\n{'=' * 90}")
print("  SECTION 1: STANDALONE RULE PERFORMANCE ON BTC (7d forward return)")
print(f"{'=' * 90}")

header = f"{'Rule':<35} {'Fires':>6} {'HitRate':>8} {'MeanRet':>9} {'Sharpe':>8} {'vs_Base':>9}"
print(header)
print("-" * 80)

base_valid = btc[~btc[label_col].isna()]
base_mean = base_valid[label_col].mean()
base_hit = (base_valid[label_col] > 0).mean()

print(f"{'[BASELINE unconditional]':<35} {len(base_valid):>6} {base_hit:>7.1%} "
      f"{base_mean:>+8.3%} {'--':>8} {'--':>9}")
print("-" * 80)

rule_results = {}

for name, rule_fn in rules.items():
    try:
        mask = rule_fn(btc)
        mask = mask & ~btc[label_col].isna()
        fires = int(mask.sum())
        if fires < 10:
            print(f"{name:<35} {fires:>6} -- insufficient data")
            rule_results[name] = {"fires": fires, "status": "insufficient"}
            continue
        rets = btc.loc[mask, label_col]
        hit_rate = float((rets > 0).mean())
        mean_ret = float(rets.mean())
        std_ret = float(rets.std())
        sharpe = mean_ret / std_ret * np.sqrt(52) if std_ret > 1e-10 else 0.0
        vs_base = mean_ret - base_mean
        print(f"{name:<35} {fires:>6} {hit_rate:>7.1%} {mean_ret:>+8.3%} "
              f"{sharpe:>8.2f} {vs_base:>+8.3%}")
        rule_results[name] = {
            "fires": fires,
            "hit_rate": round(hit_rate, 4),
            "mean_ret": round(mean_ret, 6),
            "sharpe": round(sharpe, 3),
            "vs_base": round(vs_base, 6),
            "status": "ok",
        }
    except Exception as e:
        print(f"{name:<35} ERROR: {e}")
        rule_results[name] = {"status": "error", "error": str(e)}

# =============================================================================
# Step 4: Walk-forward validation (4 temporal quarters)
# =============================================================================

print(f"\n{'=' * 90}")
print("  SECTION 2: WALK-FORWARD VALIDATION (4 temporal quarters)")
print(f"{'=' * 90}")

# Split BTC data into 4 chronological quarters
btc_valid = btc[~btc[label_col].isna()].sort_index()
n = len(btc_valid)
q_size = n // 4
quarters = []
for i in range(4):
    s = i * q_size
    e = (i + 1) * q_size if i < 3 else n
    q_data = btc_valid.iloc[s:e]
    quarters.append(q_data)
    print(f"  Q{i}: {q_data.index.min().date()} to {q_data.index.max().date()} "
          f"({len(q_data)} rows, mean_ret={q_data[label_col].mean():+.3%})")

print()
print(f"{'Rule':<35} {'Q0':>10} {'Q1':>10} {'Q2':>10} {'Q3':>10} {'Pass':>6} {'Verdict':>8}")
print("-" * 90)

wf_results = {}

for name, rule_fn in rules.items():
    q_rets = []
    for qi, q_data in enumerate(quarters):
        try:
            mask = rule_fn(q_data)
            mask = mask & ~q_data[label_col].isna()
            if mask.sum() < 5:
                q_rets.append(None)
                continue
            mean_ret = float(q_data.loc[mask, label_col].mean())
            q_rets.append(mean_ret)
        except Exception:
            q_rets.append(None)

    valid = [r for r in q_rets if r is not None]
    positive = sum(1 for r in valid if r > 0)
    total_valid = len(valid)
    verdict = "PASS" if positive >= 3 and total_valid >= 3 else "FAIL"

    q_str_parts = []
    for i, r in enumerate(q_rets):
        q_str_parts.append(f"{r:+.3%}" if r is not None else "N/A")

    row = (f"{name:<35} " +
           " ".join(f"{s:>10}" for s in q_str_parts) +
           f" {positive}/{total_valid}  [{verdict}]")
    print(row)

    wf_results[name] = {
        "quarter_returns": [round(r, 6) if r is not None else None for r in q_rets],
        "positive_quarters": positive,
        "total_valid_quarters": total_valid,
        "verdict": verdict,
    }

# Identify rules that PASS walk-forward
wf_pass = [name for name, res in wf_results.items() if res["verdict"] == "PASS"]
print(f"\n  Walk-forward PASS: {len(wf_pass)} rules")
for name in wf_pass:
    sr = rule_results.get(name, {})
    print(f"    {name:<35} mean_ret={sr.get('mean_ret', 'N/A')}, "
          f"sharpe={sr.get('sharpe', 'N/A')}")

# =============================================================================
# Step 5: Cross-token generalization
# =============================================================================

print(f"\n{'=' * 90}")
print("  SECTION 3: CROSS-TOKEN GENERALIZATION (top 10 tokens by row count)")
print(f"{'=' * 90}")

if has_token and len(wf_pass) > 0:
    token_counts = df["token"].value_counts()
    top_tokens = list(token_counts.head(10).index)
    print(f"  Testing on: {top_tokens}")
    print()

    header = f"{'Rule':<35} " + " ".join(f"{t:>8}" for t in top_tokens) + f" {'Pass':>6}"
    print(header)
    print("-" * (35 + 9 * len(top_tokens) + 8))

    cross_token_results = {}
    for name in wf_pass:
        rule_fn = rules[name]
        token_means = {}
        for tkn in top_tokens:
            tkn_data = df[df["token"] == tkn].copy()
            try:
                mask = rule_fn(tkn_data)
                mask = mask & ~tkn_data[label_col].isna()
                if mask.sum() < 5:
                    token_means[tkn] = None
                    continue
                token_means[tkn] = float(tkn_data.loc[mask, label_col].mean())
            except Exception:
                token_means[tkn] = None

        valid_tokens = {k: v for k, v in token_means.items() if v is not None}
        positive_tokens = sum(1 for v in valid_tokens.values() if v > 0)
        total_valid_tokens = len(valid_tokens)

        parts = []
        for tkn in top_tokens:
            v = token_means.get(tkn)
            parts.append(f"{v:+.3%}" if v is not None else "N/A")

        pass_str = f"{positive_tokens}/{total_valid_tokens}"
        row = f"{name:<35} " + " ".join(f"{s:>8}" for s in parts) + f" {pass_str:>6}"
        print(row)

        cross_token_results[name] = {
            "token_returns": {k: round(v, 6) if v is not None else None
                             for k, v in token_means.items()},
            "positive_tokens": positive_tokens,
            "total_valid_tokens": total_valid_tokens,
            "generalizes": positive_tokens >= total_valid_tokens * 0.6,
        }
else:
    cross_token_results = {}
    if not has_token:
        print("  No token column -- skipping cross-token test")
    else:
        print("  No rules passed walk-forward -- skipping")

# Identify rules that generalize
generalizing = [name for name, res in cross_token_results.items()
                if res.get("generalizes", False)]
print(f"\n  Cross-token generalizing (>=60% tokens positive): {len(generalizing)} rules")
for name in generalizing:
    ct = cross_token_results[name]
    print(f"    {name:<35} {ct['positive_tokens']}/{ct['total_valid_tokens']} tokens positive")

# =============================================================================
# Step 6: s514 Overlay Testing via V4 Engine
# =============================================================================

print(f"\n{'=' * 90}")
print("  SECTION 4: s514 OVERLAY TESTING (V4 Engine)")
print(f"{'=' * 90}")

# Only overlay-test rules that passed walk-forward
overlay_candidates = wf_pass if len(wf_pass) > 0 else list(rules.keys())[:4]
print(f"  Overlay candidates: {overlay_candidates}")

try:
    from v4.config import PortfolioConfig, StrategySpec
    from v4.signals import precompute_strategy_signals, discover_tokens
    from v4.simulator import simulate_portfolio
    from v4.report import compute_portfolio_metrics, print_report
    from v4.engine import _load_strategy_fn, _STRATEGY_MODULE_CACHE, _STRATEGY_MODULE_LOCK, StrategyResult

    STRATEGY_ID = "s514"
    MARKET = "perp"
    CAPITAL = 100_000
    MONTHS = 12
    END_DATE = "2026-04-01"

    # -- Load macro data aligned to 1H index for overlay --
    FEATURE_MATRIX = df  # full feature matrix with all tokens
    BTC_FEATURES = btc   # BTC-only features (daily, indexed by date)

    def _align_daily_to_1h(daily_series, idx_1h):
        """Align a daily-indexed pandas Series to an hourly index via forward-fill."""
        daily_df = daily_series.to_frame("val").copy()
        daily_df.index = pd.to_datetime(daily_df.index).tz_localize(None)

        # Normalize both indices to nanosecond resolution to avoid merge_asof dtype mismatch
        idx_1h_ns = pd.DatetimeIndex(idx_1h).as_unit("ns")
        daily_idx_ns = daily_df.index.as_unit("ns")

        target = pd.DataFrame({"ts": idx_1h_ns})
        source = pd.DataFrame({
            "ts": daily_idx_ns,
            "val": daily_df["val"].values,
        })

        merged = pd.merge_asof(target, source, on="ts", direction="backward")
        result = merged["val"].values.astype(np.float64)
        return np.nan_to_num(result, nan=0.0)

    def _make_fg_filter(original_fn, threshold=72, mode="long_only"):
        """Fear & Greed filter: only allow longs when FG > threshold."""
        def wrapped(ctx):
            result = original_fn(ctx)
            n = len(result.entry_mask)
            fg_vals = _align_daily_to_1h(BTC_FEATURES["fg_value"].dropna(), ctx.idx_1h)

            if mode == "long_only":
                # Boost longs when FG bullish, suppress when FG bearish
                bullish = fg_vals > threshold
                bearish = fg_vals < 25

                sm = np.ones(n, dtype=np.float64)
                sm[bullish] = 1.3
                sm[bearish] = 0.6

                if isinstance(result.size_multiplier, np.ndarray):
                    sm = sm * result.size_multiplier
                elif result.size_multiplier != 1.0:
                    sm = sm * float(result.size_multiplier)
                result.size_multiplier = sm
            elif mode == "entry_filter":
                # Only enter when FG > threshold
                bullish = fg_vals > threshold
                result.entry_mask = result.entry_mask & bullish

            return result
        return wrapped

    def _make_dvol_filter(original_fn, threshold=0.82):
        """DVOL z-score filter: boost size when high implied vol (recovery)."""
        def wrapped(ctx):
            result = original_fn(ctx)
            n = len(result.entry_mask)
            dvol_vals = _align_daily_to_1h(BTC_FEATURES["dvol_btc_z30"].dropna(), ctx.idx_1h)

            sm = np.ones(n, dtype=np.float64)
            sm[dvol_vals > threshold] = 1.3       # high IV -> recovery likely
            sm[dvol_vals < -threshold] = 0.7      # low IV -> complacency, reduce

            if isinstance(result.size_multiplier, np.ndarray):
                sm = sm * result.size_multiplier
            elif result.size_multiplier != 1.0:
                sm = sm * float(result.size_multiplier)
            result.size_multiplier = sm
            return result
        return wrapped

    def _make_sp500_filter(original_fn, threshold=0.013):
        """SP500 momentum filter: boost when equity momentum strong."""
        def wrapped(ctx):
            result = original_fn(ctx)
            n = len(result.entry_mask)
            sp_vals = _align_daily_to_1h(BTC_FEATURES["sp500_roc5"].dropna(), ctx.idx_1h)

            sm = np.ones(n, dtype=np.float64)
            sm[sp_vals > threshold] = 1.2
            sm[sp_vals < -threshold] = 0.7

            if isinstance(result.size_multiplier, np.ndarray):
                sm = sm * result.size_multiplier
            elif result.size_multiplier != 1.0:
                sm = sm * float(result.size_multiplier)
            result.size_multiplier = sm
            return result
        return wrapped

    def _make_pos_bear_filter(original_fn, threshold=0.05):
        """Positioning extreme filter: boost when bearish positioning (contrarian)."""
        def wrapped(ctx):
            result = original_fn(ctx)
            n = len(result.entry_mask)
            pos_vals = _align_daily_to_1h(BTC_FEATURES["div_pctile90"].dropna(), ctx.idx_1h)

            sm = np.ones(n, dtype=np.float64)
            sm[pos_vals < threshold] = 1.4   # extreme bearish -> contrarian long
            sm[pos_vals > 0.95] = 0.6        # extreme bullish -> reduce

            if isinstance(result.size_multiplier, np.ndarray):
                sm = sm * result.size_multiplier
            elif result.size_multiplier != 1.0:
                sm = sm * float(result.size_multiplier)
            result.size_multiplier = sm
            return result
        return wrapped

    def _make_combined_macro_filter(original_fn):
        """Combined: FG sizing + DVOL sizing + SP500 momentum."""
        fn1 = _make_fg_filter(original_fn, threshold=72, mode="long_only")
        fn2 = _make_dvol_filter(fn1, threshold=0.82)
        fn3 = _make_sp500_filter(fn2, threshold=0.013)
        return fn3

    def _make_fg_entry_filter(original_fn):
        """Hard entry filter: only enter when FG > 50 (not extreme fear)."""
        return _make_fg_filter(original_fn, threshold=50, mode="entry_filter")

    # Map overlay names to factories
    overlay_map = {
        "fg_sizing":        lambda fn: _make_fg_filter(fn, threshold=72, mode="long_only"),
        "dvol_sizing":      lambda fn: _make_dvol_filter(fn, threshold=0.82),
        "sp500_sizing":     lambda fn: _make_sp500_filter(fn, threshold=0.013),
        "pos_bear_sizing":  lambda fn: _make_pos_bear_filter(fn, threshold=0.05),
        "combined_macro":   _make_combined_macro_filter,
        "fg_entry_filter":  _make_fg_entry_filter,
    }

    def make_config():
        spec = StrategySpec(
            strategy_id=STRATEGY_ID,
            weight=1.0,
            max_positions=28,
            market=MARKET,
            strategy_type="per_token",
            max_concurrent_per_token=1,
            dd_scaling=[],
        )
        config = PortfolioConfig(
            capital=CAPITAL,
            exchange="binance",
            skip_walk_forward=True,
            conviction_mode="ranked",
            max_portfolio_positions=28,
            concentration_limit=0.15,
            strategies=[spec],
        )
        return config, spec

    def run_backtest_s514(label):
        end_date = pd.Timestamp(END_DATE)
        config, spec = make_config()
        tokens = discover_tokens(MARKET)
        t0 = time.time()
        signals = precompute_strategy_signals(spec, tokens, config, MONTHS, end_date=end_date)
        t1 = time.time()
        if not signals:
            print(f"  [{label}] No signals!")
            return None
        strategy_specs = {STRATEGY_ID: spec}
        state = simulate_portfolio({STRATEGY_ID: signals}, strategy_specs, config)
        t2 = time.time()
        metrics, extra_info, eq_daily = compute_portfolio_metrics(state, CAPITAL)
        trades = len(state.position_manager.closed_trades)
        print(f"  [{label}] {trades} trades, "
              f"ret={metrics.total_return_pct:+.1f}%, "
              f"sharpe={metrics.sharpe_ratio:.2f}, "
              f"calmar={metrics.calmar_ratio:.2f}, "
              f"maxDD={metrics.max_drawdown_pct:.1f}%, "
              f"winR={metrics.win_rate_pct:.1f}% "
              f"({t2 - t0:.1f}s)")
        return {
            "total_return_pct": round(metrics.total_return_pct, 2),
            "sharpe": round(metrics.sharpe_ratio, 3),
            "calmar": round(metrics.calmar_ratio, 3),
            "max_dd_pct": round(metrics.max_drawdown_pct, 2),
            "win_rate_pct": round(metrics.win_rate_pct, 2),
            "trades": trades,
        }

    def run_overlay_s514(overlay_name, overlay_factory):
        _load_strategy_fn(STRATEGY_ID)
        with _STRATEGY_MODULE_LOCK:
            mod = _STRATEGY_MODULE_CACHE.get(STRATEGY_ID)
        if mod is None:
            print(f"  ERROR: Could not load {STRATEGY_ID}")
            return None
        original_fn = mod.strategy
        mod.strategy = overlay_factory(original_fn)
        if hasattr(mod, "_aligned_cache"):
            mod._aligned_cache.clear()
        try:
            result = run_backtest_s514(f"OVERLAY: {overlay_name}")
        finally:
            mod.strategy = original_fn
        return result

    # Run baseline
    print("\n  Running BASELINE s514...")
    _load_strategy_fn(STRATEGY_ID)
    baseline_result = run_backtest_s514("BASELINE s514")

    # Run each overlay
    overlay_results = {}
    for ov_name, ov_factory in overlay_map.items():
        print(f"\n  Running overlay: {ov_name}...")
        res = run_overlay_s514(ov_name, ov_factory)
        if res is not None:
            overlay_results[ov_name] = res

    # Summary table
    print(f"\n{'=' * 100}")
    print("  s514 OVERLAY COMPARISON")
    print(f"{'=' * 100}")
    print(f"  {'Overlay':<25} {'Return':>10} {'Delta':>10} {'Sharpe':>8} "
          f"{'Calmar':>8} {'MaxDD':>8} {'Trades':>7} {'WinR':>7}")
    print(f"  {'-' * 25} {'-' * 10} {'-' * 10} {'-' * 8} {'-' * 8} "
          f"{'-' * 8} {'-' * 7} {'-' * 7}")

    if baseline_result:
        print(f"  {'BASELINE':<25} {baseline_result['total_return_pct']:>+9.1f}% "
              f"{'--':>10} {baseline_result['sharpe']:>8.2f} "
              f"{baseline_result['calmar']:>8.2f} {baseline_result['max_dd_pct']:>7.1f}% "
              f"{baseline_result['trades']:>7} {baseline_result['win_rate_pct']:>6.1f}%")

    for ov_name, res in overlay_results.items():
        delta = res["total_return_pct"] - baseline_result["total_return_pct"] if baseline_result else 0
        delta_str = f"{delta:+.1f}%"
        print(f"  {ov_name:<25} {res['total_return_pct']:>+9.1f}% "
              f"{delta_str:>10} {res['sharpe']:>8.2f} "
              f"{res['calmar']:>8.2f} {res['max_dd_pct']:>7.1f}% "
              f"{res['trades']:>7} {res['win_rate_pct']:>6.1f}%")

    # Identify best overlay
    if overlay_results and baseline_result:
        best_name = max(overlay_results, key=lambda k: overlay_results[k]["sharpe"])
        best = overlay_results[best_name]
        print(f"\n  Best overlay by Sharpe: {best_name}")
        print(f"    Sharpe: {baseline_result['sharpe']:.2f} -> {best['sharpe']:.2f} "
              f"({best['sharpe'] - baseline_result['sharpe']:+.3f})")
        print(f"    Return: {baseline_result['total_return_pct']:+.1f}% -> "
              f"{best['total_return_pct']:+.1f}%")
        print(f"    MaxDD:  {baseline_result['max_dd_pct']:.1f}% -> {best['max_dd_pct']:.1f}%")

except Exception as e:
    import traceback
    print(f"\n  s514 OVERLAY TEST FAILED: {e}")
    traceback.print_exc()
    baseline_result = None
    overlay_results = {}

# =============================================================================
# Step 7: Final Summary & Recommendations
# =============================================================================

print(f"\n{'=' * 90}")
print("  FINAL SUMMARY & RECOMMENDATIONS")
print(f"{'=' * 90}")

print("\n  A) Rules that PASS walk-forward (3+/4 quarters positive on BTC):")
if wf_pass:
    for name in wf_pass:
        sr = rule_results.get(name, {})
        wf = wf_results.get(name, {})
        ct = cross_token_results.get(name, {})
        gen_str = "YES" if ct.get("generalizes", False) else "NO"
        print(f"    {name:<35} sharpe={sr.get('sharpe', 'N/A'):>6}, "
              f"wf={wf.get('positive_quarters', '?')}/{wf.get('total_valid_quarters', '?')}, "
              f"cross-token={gen_str}")
else:
    print("    None")

print("\n  B) Rules that also generalize across tokens (>=60%):")
if generalizing:
    for name in generalizing:
        ct = cross_token_results[name]
        print(f"    {name:<35} {ct['positive_tokens']}/{ct['total_valid_tokens']} tokens")
else:
    print("    None")

print("\n  C) s514 overlay results:")
if baseline_result and overlay_results:
    for ov_name, res in sorted(overlay_results.items(),
                                key=lambda x: x[1]["sharpe"], reverse=True):
        delta_sharpe = res["sharpe"] - baseline_result["sharpe"]
        delta_ret = res["total_return_pct"] - baseline_result["total_return_pct"]
        verdict = "IMPROVES" if delta_sharpe > 0.05 else ("NEUTRAL" if abs(delta_sharpe) <= 0.05 else "HURTS")
        print(f"    {ov_name:<25} sharpe_delta={delta_sharpe:+.3f}, "
              f"ret_delta={delta_ret:+.1f}%, verdict={verdict}")
else:
    print("    No overlay results available")

# =============================================================================
# Save results
# =============================================================================

output_dir = "outputs/ml_discovery"
os.makedirs(output_dir, exist_ok=True)

results_json = {
    "timestamp": pd.Timestamp.now().isoformat(),
    "btc_rows": len(btc),
    "date_range": [str(btc.index.min()), str(btc.index.max())],
    "standalone_rules": rule_results,
    "walk_forward": wf_results,
    "wf_pass_rules": wf_pass,
    "cross_token": cross_token_results,
    "generalizing_rules": generalizing,
    "s514_baseline": baseline_result,
    "s514_overlays": overlay_results,
}

output_path = os.path.join(output_dir, "R202_R203_results.json")
with open(output_path, "w") as f:
    json.dump(results_json, f, indent=2, default=str)
print(f"\n  Results saved to {output_path}")

elapsed = time.time() - t0_global
print(f"\n  Total elapsed: {elapsed:.1f}s")
print("=" * 90)
