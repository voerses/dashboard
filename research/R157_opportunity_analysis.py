"""
R157: Maximum Opportunity Per Token Analysis (Last 12 Months)
=============================================================
Analyzes buy-and-hold, trend-following (long-only & L/S), volatility,
max moves, and choppiness for all tokens with sufficient data.

Period: 2025-03-17 to 2026-03-17 (hourly bars)
EMA pairs: 8/21, 12/26, 20/50, 50/100, 50/200
Costs: 7bps per side (14bps round-trip)
"""

import os
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

# ── Config ───────────────────────────────────────────────────────────
DATA_DIR = Path("/workspace/crypto_backtest/data/perp/1h_cache")
OUTPUT_PATH = Path("/workspace/crypto_backtest/research/R157_opportunity_analysis.md")

TEST_START = pd.Timestamp("2025-03-17", tz=None)
TEST_END   = pd.Timestamp("2026-03-17", tz=None)
REQUIRE_DATA_BEFORE = pd.Timestamp("2024-03-17", tz=None)  # 1yr+ before test
REQUIRE_DATA_AFTER  = pd.Timestamp("2026-03-01", tz=None)

EMA_PAIRS = [(8, 21), (12, 26), (20, 50), (50, 100), (50, 200)]
COST_BPS = 7  # per side

# ── Helper Functions ─────────────────────────────────────────────────

def load_token(token: str) -> pd.DataFrame | None:
    """Load parquet, return None if data doesn't meet requirements."""
    fpath = DATA_DIR / f"{token}_1h.parquet"
    if not fpath.exists():
        return None
    df = pd.read_parquet(fpath)
    if df.index.name != 'datetime':
        if 'datetime' in df.columns:
            df = df.set_index('datetime')
        else:
            return None
    # Remove timezone if present
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    # Check data coverage
    if df.index[0] > REQUIRE_DATA_BEFORE:
        return None
    if df.index[-1] < REQUIRE_DATA_AFTER:
        return None
    return df


def compute_ema_trend(close: pd.Series, fast: int, slow: int, cost_bps: float = 7):
    """
    Compute trend-following returns for one EMA pair.
    Signal from previous bar's EMA cross.
    Returns: long_only_return, ls_return, num_crosses
    """
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()

    # Signal: +1 when fast > slow, -1 when fast < slow
    raw_signal = (ema_fast > ema_slow).astype(int)  # 1 or 0
    raw_signal_ls = raw_signal * 2 - 1  # +1 or -1

    # Use previous bar's signal (no lookahead)
    signal_long = raw_signal.shift(1).fillna(0)
    signal_ls = raw_signal_ls.shift(1).fillna(0)

    # Hourly returns
    returns = close.pct_change().fillna(0)

    # Detect trades (signal changes)
    trades_long = signal_long.diff().abs().fillna(0)
    trades_ls = signal_ls.diff().abs().fillna(0)

    # Number of crosses
    num_crosses = int(trades_long.sum())

    # Cost per trade: each signal change costs cost_bps per side
    # For long-only: going from 0->1 or 1->0 = 1 trade = cost_bps
    # For L/S: going from -1->+1 = 2 units of change, but it's really 1 round-trip equivalent
    cost_per_unit = cost_bps / 10000.0

    # Long-only PnL
    strat_ret_long = signal_long * returns - trades_long * cost_per_unit
    cum_long = (1 + strat_ret_long).cumprod()
    total_long = cum_long.iloc[-1] / cum_long.iloc[0] - 1

    # L/S PnL
    strat_ret_ls = signal_ls * returns - trades_ls * cost_per_unit
    cum_ls = (1 + strat_ret_ls).cumprod()
    total_ls = cum_ls.iloc[-1] / cum_ls.iloc[0] - 1

    return total_long, total_ls, num_crosses


def compute_max_moves(close: pd.Series):
    """
    Compute max peak-to-trough UP move and max peak-to-trough DOWN move.
    UP move: from a trough to a subsequent peak.
    DOWN move: from a peak to a subsequent trough.
    """
    prices = close.values

    # Max drawdown (peak-to-trough DOWN move)
    running_max = np.maximum.accumulate(prices)
    drawdowns = prices / running_max - 1
    max_down = drawdowns.min()

    # Max rally (trough-to-peak UP move)
    running_min = np.minimum.accumulate(prices)
    rallies = prices / running_min - 1
    max_up = rallies.max()

    return max_up, max_down


def compute_monthly_vol(close: pd.Series):
    """Annualized volatility from daily returns."""
    # Resample to daily
    daily = close.resample('1D').last().dropna()
    daily_ret = daily.pct_change().dropna()
    ann_vol = daily_ret.std() * np.sqrt(365)
    return ann_vol


# ── Main Analysis ────────────────────────────────────────────────────

def main():
    files = sorted(DATA_DIR.glob("*_1h.parquet"))
    tokens = [f.stem.replace("_1h", "") for f in files]

    print(f"Found {len(tokens)} token files")

    results = []
    skipped = []

    for i, token in enumerate(tokens):
        if (i + 1) % 20 == 0:
            print(f"  Processing {i+1}/{len(tokens)}...")

        df = load_token(token)
        if df is None:
            skipped.append(token)
            continue

        # Slice to test period
        mask = (df.index >= TEST_START) & (df.index <= TEST_END)
        test_df = df.loc[mask].copy()

        if len(test_df) < 100:  # need meaningful data
            skipped.append(token)
            continue

        close = test_df['close']

        # 1. Buy and hold
        bnh_return = close.iloc[-1] / close.iloc[0] - 1

        # 2 & 3. EMA trend returns
        best_long = -np.inf
        best_ls = -np.inf
        best_pair_long = None
        best_pair_ls = None
        best_crosses = 0

        pair_results = {}
        for fast, slow in EMA_PAIRS:
            long_ret, ls_ret, crosses = compute_ema_trend(close, fast, slow, COST_BPS)
            pair_results[(fast, slow)] = (long_ret, ls_ret, crosses)

            if long_ret > best_long:
                best_long = long_ret
                best_pair_long = (fast, slow)

            if ls_ret > best_ls:
                best_ls = ls_ret
                best_pair_ls = (fast, slow)
                best_crosses = crosses

        # Use the best L/S pair for "optimal" designation and crosses
        optimal_pair = best_pair_ls
        trend_changes = best_crosses

        # 5. Monthly volatility
        monthly_vol = compute_monthly_vol(close)

        # 6 & 7. Max moves
        max_up, max_down = compute_max_moves(close)

        results.append({
            'token': token,
            'bnh_return': bnh_return,
            'best_long_return': best_long,
            'best_ls_return': best_ls,
            'best_pair_long': f"{best_pair_long[0]}/{best_pair_long[1]}",
            'best_pair_ls': f"{best_pair_ls[0]}/{best_pair_ls[1]}",
            'optimal_ema': f"{optimal_pair[0]}/{optimal_pair[1]}",
            'monthly_vol': monthly_vol,
            'max_up': max_up,
            'max_down': max_down,
            'trend_changes': trend_changes,
        })

    print(f"\nProcessed: {len(results)} tokens, Skipped: {len(skipped)}")

    # ── Build DataFrame ──────────────────────────────────────────────
    df_res = pd.DataFrame(results)
    df_res = df_res.sort_values('best_ls_return', ascending=False).reset_index(drop=True)

    # ── Top/Bottom Lists ─────────────────────────────────────────────
    top10_long = df_res.nlargest(10, 'best_long_return')
    top10_ls = df_res.nlargest(10, 'best_ls_return')
    bottom10_bnh = df_res.nsmallest(10, 'bnh_return')

    # Equal-weight portfolio returns
    ew_top10_long = top10_long['best_long_return'].mean()
    ew_top10_ls = top10_ls['best_ls_return'].mean()
    ew_bottom10_bnh = bottom10_bnh['bnh_return'].mean()

    # Universe stats
    ew_all_bnh = df_res['bnh_return'].mean()
    ew_all_long = df_res['best_long_return'].mean()
    ew_all_ls = df_res['best_ls_return'].mean()
    median_vol = df_res['monthly_vol'].median()

    # ── Generate Markdown Report ─────────────────────────────────────
    lines = []
    lines.append("# R157: Maximum Opportunity Per Token Analysis")
    lines.append("")
    lines.append("## Methodology")
    lines.append("")
    lines.append("- **Period**: 2025-03-17 to 2026-03-17 (12 months, hourly bars)")
    lines.append("- **Universe**: All perpetual tokens with data from before 2024-03-17 and through 2026-03-01")
    lines.append(f"- **Tokens analyzed**: {len(results)} (skipped {len(skipped)} with insufficient data)")
    lines.append("- **EMA pairs tested**: 8/21, 12/26, 20/50, 50/100, 50/200 (hourly)")
    lines.append("- **Trend long-only**: Long when fast EMA > slow EMA, flat otherwise")
    lines.append("- **Trend L/S**: Long when fast > slow, short when fast < slow")
    lines.append("- **Signal**: Previous bar's EMA cross (no lookahead)")
    lines.append("- **Costs**: 7 bps per side on each signal change")
    lines.append("")

    lines.append("## Universe Summary")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Tokens analyzed | {len(results)} |")
    lines.append(f"| EW Buy & Hold return | {ew_all_bnh:.1%} |")
    lines.append(f"| EW Best Long-Only return | {ew_all_long:.1%} |")
    lines.append(f"| EW Best L/S return | {ew_all_ls:.1%} |")
    lines.append(f"| Median annualized vol | {median_vol:.1%} |")
    lines.append(f"| Tokens with positive B&H | {(df_res['bnh_return'] > 0).sum()} / {len(results)} |")
    lines.append(f"| Tokens with positive best-long | {(df_res['best_long_return'] > 0).sum()} / {len(results)} |")
    lines.append(f"| Tokens with positive best-L/S | {(df_res['best_ls_return'] > 0).sum()} / {len(results)} |")
    lines.append("")

    # ── Top 10 Long-Only ─────────────────────────────────────────────
    lines.append("## Top 10 by Long-Only Trend Return")
    lines.append("")
    lines.append(f"**Equal-weight portfolio return: {ew_top10_long:.1%}**")
    lines.append("")
    lines.append("| # | Token | B&H | Long-Only | Optimal EMA | Vol | Max Up | Max Down | Crosses |")
    lines.append("|---|-------|-----|-----------|-------------|-----|--------|----------|---------|")
    for idx, row in top10_long.iterrows():
        lines.append(
            f"| {top10_long.index.get_loc(idx)+1} | {row['token']} | {row['bnh_return']:.1%} | "
            f"{row['best_long_return']:.1%} | {row['best_pair_long']} | {row['monthly_vol']:.1%} | "
            f"{row['max_up']:.1%} | {row['max_down']:.1%} | {row['trend_changes']} |"
        )
    lines.append("")

    # ── Top 10 L/S ───────────────────────────────────────────────────
    lines.append("## Top 10 by L/S Trend Return")
    lines.append("")
    lines.append(f"**Equal-weight portfolio return: {ew_top10_ls:.1%}**")
    lines.append("")
    lines.append("| # | Token | B&H | L/S | Optimal EMA | Vol | Max Up | Max Down | Crosses |")
    lines.append("|---|-------|-----|-----|-------------|-----|--------|----------|---------|")
    for idx, row in top10_ls.iterrows():
        lines.append(
            f"| {top10_ls.index.get_loc(idx)+1} | {row['token']} | {row['bnh_return']:.1%} | "
            f"{row['best_ls_return']:.1%} | {row['best_pair_ls']} | {row['monthly_vol']:.1%} | "
            f"{row['max_up']:.1%} | {row['max_down']:.1%} | {row['trend_changes']} |"
        )
    lines.append("")

    # ── Bottom 10 B&H (short opportunities) ──────────────────────────
    lines.append("## Bottom 10 by Buy & Hold (Short Opportunities)")
    lines.append("")
    lines.append(f"**Equal-weight B&H return (i.e., short return if perfectly timed): {-ew_bottom10_bnh:.1%}**")
    lines.append("")
    lines.append("| # | Token | B&H | Long-Only | L/S | Optimal EMA | Vol | Max Down | Crosses |")
    lines.append("|---|-------|-----|-----------|-----|-------------|-----|----------|---------|")
    for idx, row in bottom10_bnh.iterrows():
        lines.append(
            f"| {bottom10_bnh.index.get_loc(idx)+1} | {row['token']} | {row['bnh_return']:.1%} | "
            f"{row['best_long_return']:.1%} | {row['best_ls_return']:.1%} | {row['optimal_ema']} | "
            f"{row['monthly_vol']:.1%} | {row['max_down']:.1%} | {row['trend_changes']} |"
        )
    lines.append("")

    # ── Full Table (sorted by best L/S) ──────────────────────────────
    lines.append("## Full Results (Sorted by Best L/S Return)")
    lines.append("")
    lines.append("| # | Token | B&H | Long-Only | L/S | Opt EMA (L/S) | Vol | Max Up | Max Down | Crosses |")
    lines.append("|---|-------|-----|-----------|-----|---------------|-----|--------|----------|---------|")
    for i, row in df_res.iterrows():
        lines.append(
            f"| {i+1} | {row['token']} | {row['bnh_return']:.1%} | "
            f"{row['best_long_return']:.1%} | {row['best_ls_return']:.1%} | {row['optimal_ema']} | "
            f"{row['monthly_vol']:.1%} | {row['max_up']:.1%} | {row['max_down']:.1%} | {row['trend_changes']} |"
        )
    lines.append("")

    # ── EMA Pair Analysis ────────────────────────────────────────────
    lines.append("## EMA Pair Distribution")
    lines.append("")
    lines.append("Which EMA pair was optimal most often?")
    lines.append("")

    # Count best pairs
    long_pair_counts = df_res['best_pair_long'].value_counts()
    ls_pair_counts = df_res['best_pair_ls'].value_counts()

    lines.append("### Long-Only Optimal Pair Frequency")
    lines.append("")
    lines.append("| EMA Pair | Count | % |")
    lines.append("|----------|-------|---|")
    for pair, count in long_pair_counts.items():
        lines.append(f"| {pair} | {count} | {count/len(results)*100:.0f}% |")
    lines.append("")

    lines.append("### L/S Optimal Pair Frequency")
    lines.append("")
    lines.append("| EMA Pair | Count | % |")
    lines.append("|----------|-------|---|")
    for pair, count in ls_pair_counts.items():
        lines.append(f"| {pair} | {count} | {count/len(results)*100:.0f}% |")
    lines.append("")

    # ── Key Observations ─────────────────────────────────────────────
    lines.append("## Key Observations")
    lines.append("")

    # Tokens where L/S >> B&H (trend captured well)
    df_res['ls_vs_bnh'] = df_res['best_ls_return'] - df_res['bnh_return']
    trend_captures = df_res.nlargest(5, 'ls_vs_bnh')

    lines.append("### Tokens Where Trend L/S Most Outperformed B&H")
    lines.append("")
    lines.append("| Token | B&H | L/S | Excess |")
    lines.append("|-------|-----|-----|--------|")
    for _, row in trend_captures.iterrows():
        lines.append(f"| {row['token']} | {row['bnh_return']:.1%} | {row['best_ls_return']:.1%} | {row['ls_vs_bnh']:.1%} |")
    lines.append("")

    # Choppiest tokens (most crosses)
    choppiest = df_res.nlargest(5, 'trend_changes')
    lines.append("### Choppiest Tokens (Most EMA Crosses)")
    lines.append("")
    lines.append("| Token | Crosses | L/S Return | Vol |")
    lines.append("|-------|---------|------------|-----|")
    for _, row in choppiest.iterrows():
        lines.append(f"| {row['token']} | {row['trend_changes']} | {row['best_ls_return']:.1%} | {row['monthly_vol']:.1%} |")
    lines.append("")

    # Smoothest tokens (fewest crosses)
    smoothest = df_res.nsmallest(5, 'trend_changes')
    lines.append("### Smoothest Tokens (Fewest EMA Crosses)")
    lines.append("")
    lines.append("| Token | Crosses | L/S Return | Vol |")
    lines.append("|-------|---------|------------|-----|")
    for _, row in smoothest.iterrows():
        lines.append(f"| {row['token']} | {row['trend_changes']} | {row['best_ls_return']:.1%} | {row['monthly_vol']:.1%} |")
    lines.append("")

    # Biggest absolute moves
    lines.append("### Largest Absolute Moves")
    lines.append("")
    biggest_up = df_res.nlargest(5, 'max_up')
    lines.append("**Top 5 Max Up-Moves (trough-to-peak):**")
    lines.append("")
    for _, row in biggest_up.iterrows():
        lines.append(f"- {row['token']}: +{row['max_up']:.0%} (B&H: {row['bnh_return']:.1%})")
    lines.append("")

    biggest_down = df_res.nsmallest(5, 'max_down')
    lines.append("**Top 5 Max Down-Moves (peak-to-trough):**")
    lines.append("")
    for _, row in biggest_down.iterrows():
        lines.append(f"- {row['token']}: {row['max_down']:.0%} (B&H: {row['bnh_return']:.1%})")
    lines.append("")

    # Volatility quintile analysis
    lines.append("### Returns by Volatility Quintile")
    lines.append("")
    df_res['vol_quintile'] = pd.qcut(df_res['monthly_vol'], 5, labels=['Q1 (Low)', 'Q2', 'Q3', 'Q4', 'Q5 (High)'])
    vol_summary = df_res.groupby('vol_quintile', observed=True).agg({
        'bnh_return': 'mean',
        'best_long_return': 'mean',
        'best_ls_return': 'mean',
        'monthly_vol': 'mean',
        'token': 'count'
    }).rename(columns={'token': 'count'})

    lines.append("| Vol Quintile | Count | Avg Vol | Avg B&H | Avg Long | Avg L/S |")
    lines.append("|-------------|-------|---------|---------|----------|---------|")
    for q, row in vol_summary.iterrows():
        lines.append(
            f"| {q} | {int(row['count'])} | {row['monthly_vol']:.1%} | "
            f"{row['bnh_return']:.1%} | {row['best_long_return']:.1%} | {row['best_ls_return']:.1%} |"
        )
    lines.append("")

    # ── Conclusion ───────────────────────────────────────────────────
    lines.append("## Conclusion")
    lines.append("")
    lines.append(f"Across {len(results)} tokens over the last 12 months:")
    lines.append(f"- Simple EMA trend-following (best long-only) averaged {ew_all_long:.1%} vs B&H of {ew_all_bnh:.1%}")
    lines.append(f"- L/S trend-following averaged {ew_all_ls:.1%}")
    lines.append(f"- Top 10 long-only tokens averaged {ew_top10_long:.1%}")
    lines.append(f"- Top 10 L/S tokens averaged {ew_top10_ls:.1%}")
    lines.append(f"- Bottom 10 B&H tokens lost {-ew_bottom10_bnh:.1%} on average (short opportunity)")
    lines.append(f"- Median annualized volatility was {median_vol:.1%}")
    lines.append("")
    lines.append("*Note: These are in-sample best-EMA results (5 pairs tested per token). "
                 "Out-of-sample results will be worse due to parameter overfitting. "
                 "However, this establishes the opportunity ceiling for simple trend strategies.*")

    # Write output
    report = "\n".join(lines)
    OUTPUT_PATH.write_text(report)
    print(f"\nReport written to {OUTPUT_PATH}")

    # Also print summary to stdout
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    print(f"Tokens: {len(results)}")
    print(f"EW B&H: {ew_all_bnh:.1%}")
    print(f"EW Best Long: {ew_all_long:.1%}")
    print(f"EW Best L/S: {ew_all_ls:.1%}")
    print(f"\nTop 10 Long-Only (EW: {ew_top10_long:.1%}):")
    for _, row in top10_long.iterrows():
        print(f"  {row['token']:10s}  Long: {row['best_long_return']:+.1%}  B&H: {row['bnh_return']:+.1%}  EMA: {row['best_pair_long']}")
    print(f"\nTop 10 L/S (EW: {ew_top10_ls:.1%}):")
    for _, row in top10_ls.iterrows():
        print(f"  {row['token']:10s}  L/S: {row['best_ls_return']:+.1%}  B&H: {row['bnh_return']:+.1%}  EMA: {row['best_pair_ls']}")
    print(f"\nBottom 10 B&H (EW: {ew_bottom10_bnh:.1%}):")
    for _, row in bottom10_bnh.iterrows():
        print(f"  {row['token']:10s}  B&H: {row['bnh_return']:+.1%}  L/S: {row['best_ls_return']:+.1%}")


if __name__ == "__main__":
    main()
