#!/workspace/venv/bin/python
"""
R151: Honest Carry Portfolio — Funding Rate Carry on Binance Perps
===================================================================

Hypothesis: Short high-funding tokens to collect carry. Unlike per-token carry
strategies (s62/s65), this is a PORTFOLIO strategy — it ranks ALL tokens by
funding rate and shorts the top N.

Strategy:
  - Load ALL perp 1h data from Binance
  - Filter: ADV > $50M, min 6 months history
  - Weekly rebalance (168h):
    - Rank eligible tokens by 72h rolling mean funding rate
    - SHORT top N highest-funding (they pay us)
    - LONG bottom N lowest-funding (hedge / we pay less)
    - Equal weight within each side
  - 10% equity per position (N longs + N shorts = ~100% gross, ~0% net)
  - Fees: 4bps entry + 4bps exit per side + actual funding from data
  - Leverage: 1x per side

Variants:
  A. Base: top 5 / bottom 5, 168h rebalance
  B. Wider: top 10 / bottom 10, 168h rebalance
  C. Fast: top 5 / bottom 5, 72h rebalance
  D. Quality: ADV > $200M, top 5 / bottom 5, 168h rebalance

Author: Quant Research Agent
Date: 2026-03-28
"""

import warnings
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

warnings.filterwarnings('ignore')

# ── Paths ──────────────────────────────────────────────────────────────────
PROJECT_DIR = Path('/workspace/crypto_backtest')
DATA_DIR = PROJECT_DIR / 'data' / 'perp' / '1h_cache'
OUTPUT_MD = PROJECT_DIR / 'research' / 'R151_honest_carry_results.md'

# ── Constants ──────────────────────────────────────────────────────────────
ANNUAL_HOURS = 8766  # 365.25 * 24
FEE_BPS = 4  # per side, entry and exit
MIN_HISTORY_HOURS = 6 * 30 * 24  # ~6 months in hours


# ══════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════════════════════════

def load_all_perps():
    """Load all perp parquet files, return dict of token -> DataFrame."""
    files = sorted(DATA_DIR.glob('*.parquet'))
    print(f"[DATA] Found {len(files)} parquet files in {DATA_DIR}")

    data = {}
    skipped = 0
    for f in files:
        token = f.stem.replace('_1h', '')
        try:
            df = pd.read_parquet(f)
            df.index = pd.to_datetime(df.index)
            df = df.sort_index()
            df = df[~df.index.duplicated(keep='first')]

            # Must have required columns
            required = ['open', 'high', 'low', 'close', 'volume', 'funding_1h']
            if not all(c in df.columns for c in required):
                skipped += 1
                continue

            # Must have some data
            if len(df) < 100:
                skipped += 1
                continue

            data[token] = df
        except Exception as e:
            skipped += 1
            continue

    print(f"[DATA] Loaded {len(data)} tokens, skipped {skipped}")
    return data


def compute_adv(df, window=720):
    """Compute rolling average daily volume in USD (30d = 720 hours)."""
    # Dollar volume per hour
    dollar_vol_hourly = df['volume'] * df['close']
    # Rolling sum over 24h to get daily, then rolling mean over 30 periods
    daily_dollar_vol = dollar_vol_hourly.rolling(24, min_periods=12).sum()
    adv = daily_dollar_vol.rolling(30, min_periods=15).mean()
    return adv


def filter_tokens(data, adv_threshold=50_000_000, min_hours=MIN_HISTORY_HOURS):
    """Filter tokens by ADV threshold and minimum history.

    Returns a dict of token -> (df, adv_series) for tokens that pass.
    """
    eligible = {}
    adv_cache = {}

    for token, df in data.items():
        if len(df) < min_hours:
            continue

        adv = compute_adv(df)
        adv_cache[token] = adv

        # Check if token has enough periods above threshold
        above = adv > adv_threshold
        if above.sum() > min_hours * 0.3:  # At least 30% of time above threshold
            eligible[token] = (df, adv)

    return eligible


# ══════════════════════════════════════════════════════════════════════════
# PORTFOLIO BACKTEST ENGINE
# ══════════════════════════════════════════════════════════════════════════

def run_carry_backtest(data, adv_threshold=50_000_000, n_positions=5,
                       rebalance_hours=168, funding_lookback=72,
                       label="Base"):
    """
    Run the funding carry portfolio backtest.

    Returns dict with equity curve, metrics, and position details.
    """
    print(f"\n{'='*70}")
    print(f"[BACKTEST] Variant: {label}")
    print(f"  ADV threshold: ${adv_threshold/1e6:.0f}M, positions: {n_positions} per side")
    print(f"  Rebalance: {rebalance_hours}h, funding lookback: {funding_lookback}h")
    print(f"{'='*70}")

    # ── Step 1: Filter eligible tokens ──
    eligible = filter_tokens(data, adv_threshold=adv_threshold)
    print(f"[FILTER] {len(eligible)} tokens pass ADV>${adv_threshold/1e6:.0f}M + 6mo history")

    if len(eligible) < 2 * n_positions:
        print(f"[ERROR] Not enough eligible tokens ({len(eligible)}) for {2*n_positions} positions")
        return None

    # ── Step 2: Build aligned panel of funding rates and returns ──
    # Find common date range
    all_starts = []
    all_ends = []
    for token, (df, adv) in eligible.items():
        all_starts.append(df.index.min())
        all_ends.append(df.index.max())

    # Use the latest start and earliest end for the common period
    # But we need enough tokens, so use a sliding approach
    global_start = sorted(all_starts)[len(all_starts) // 3]  # After 1/3 of tokens have started
    global_end = min(all_ends)

    print(f"[DATES] Global range: {global_start.date()} to {global_end.date()}")

    # Build hourly index
    hourly_idx = pd.date_range(global_start, global_end, freq='h')

    # Build funding rate panel and return panel
    funding_panel = pd.DataFrame(index=hourly_idx)
    return_panel = pd.DataFrame(index=hourly_idx)
    close_panel = pd.DataFrame(index=hourly_idx)
    adv_panel = pd.DataFrame(index=hourly_idx)
    funding_raw_panel = pd.DataFrame(index=hourly_idx)

    for token, (df, adv) in eligible.items():
        # Reindex to common hourly index
        df_aligned = df.reindex(hourly_idx)

        # 72h rolling mean funding rate for ranking
        funding_panel[token] = df_aligned['funding_1h'].rolling(
            funding_lookback, min_periods=funding_lookback // 2
        ).mean()

        # Raw hourly funding for P&L calculation
        funding_raw_panel[token] = df_aligned['funding_1h']

        # Hourly returns
        return_panel[token] = df_aligned['close'].pct_change()

        # Close prices
        close_panel[token] = df_aligned['close']

        # ADV for eligibility check at each rebalance
        adv_panel[token] = adv.reindex(hourly_idx)

    print(f"[PANEL] {len(funding_panel.columns)} tokens in panel, "
          f"{len(hourly_idx)} hours")

    # ── Step 3: Simulate portfolio ──
    # Initialize tracking
    equity = 1.0
    equity_curve = pd.Series(index=hourly_idx, dtype=float)
    equity_curve.iloc[0] = equity

    # Track positions: {token: weight} where weight is signed (negative = short)
    positions = {}  # token -> (direction, weight, entry_price)

    # Cumulative tracking
    total_funding_income = 0.0
    total_price_pnl = 0.0
    total_fees = 0.0
    trade_count = 0

    # Per-token tracking
    token_pnl = {t: 0.0 for t in eligible.keys()}
    token_funding = {t: 0.0 for t in eligible.keys()}

    # Determine rebalance points
    warmup = max(funding_lookback, 720)  # Need funding lookback + ADV warmup
    start_idx = warmup

    last_rebalance = start_idx
    hours_since_start = 0

    print(f"[SIM] Starting simulation from hour {start_idx} "
          f"({hourly_idx[start_idx].date()})")

    for i in range(start_idx, len(hourly_idx)):
        current_time = hourly_idx[i]

        # ── Mark to market: apply hourly returns and funding ──
        hour_pnl = 0.0
        hour_funding = 0.0

        for token, (direction, weight, entry_price) in list(positions.items()):
            # Price return for this hour
            ret = return_panel.loc[current_time, token]
            if pd.isna(ret):
                continue

            # Position P&L from price movement
            # direction: +1 for long, -1 for short
            # weight: fraction of equity allocated (always positive)
            price_pnl = direction * weight * ret * equity
            hour_pnl += price_pnl
            total_price_pnl += price_pnl
            token_pnl[token] += price_pnl

            # Funding P&L
            funding_rate = funding_raw_panel.loc[current_time, token]
            if pd.isna(funding_rate):
                funding_rate = 0.0

            # Funding mechanics:
            # If funding > 0: longs pay shorts
            # If funding < 0: shorts pay longs
            # For shorts (direction=-1): we RECEIVE funding when funding > 0
            # For longs (direction=+1): we PAY funding when funding > 0
            funding_pnl = -direction * weight * funding_rate * equity
            hour_funding += funding_pnl
            total_funding_income += funding_pnl
            token_funding[token] += funding_pnl

        equity += hour_pnl + hour_funding

        # Prevent equity from going to zero/negative
        if equity <= 0.01:
            print(f"[BLOWN] Equity went to zero at {current_time}")
            equity = 0.01

        equity_curve.iloc[i] = equity

        # ── Rebalance check ──
        if (i - last_rebalance) >= rebalance_hours:
            last_rebalance = i

            # Get current funding rankings
            current_funding = funding_panel.iloc[i].dropna()
            current_adv = adv_panel.iloc[i].dropna()

            # Filter by ADV at this point
            adv_eligible = current_adv[current_adv > adv_threshold].index
            rankable = current_funding.index.intersection(adv_eligible)
            current_funding = current_funding.loc[rankable].sort_values()

            if len(current_funding) < 2 * n_positions:
                continue

            # Bottom N = lowest funding -> LONG these
            longs = current_funding.head(n_positions).index.tolist()

            # Top N = highest funding -> SHORT these
            shorts = current_funding.tail(n_positions).index.tolist()

            # Calculate new positions
            weight_per_position = 0.10  # 10% per position
            new_positions = {}

            for token in longs:
                new_positions[token] = (+1, weight_per_position,
                                        close_panel.loc[current_time, token])
            for token in shorts:
                new_positions[token] = (-1, weight_per_position,
                                        close_panel.loc[current_time, token])

            # Calculate turnover for fee purposes
            old_tokens = set(positions.keys())
            new_tokens = set(new_positions.keys())

            # Tokens being closed
            closed = old_tokens - new_tokens
            # Tokens being opened
            opened = new_tokens - old_tokens
            # Tokens changing direction
            flipped = set()
            for t in old_tokens & new_tokens:
                if positions[t][0] != new_positions[t][0]:
                    flipped.add(t)

            # Fee calculation
            n_trades = len(closed) + len(opened) + 2 * len(flipped)
            trade_count += n_trades

            # Each trade costs FEE_BPS per side (entry + exit)
            fee_cost = 0
            for t in closed:
                fee_cost += positions[t][1] * FEE_BPS / 10000  # exit
            for t in opened:
                fee_cost += new_positions[t][1] * FEE_BPS / 10000  # entry
            for t in flipped:
                fee_cost += positions[t][1] * FEE_BPS / 10000  # exit old
                fee_cost += new_positions[t][1] * FEE_BPS / 10000  # enter new
            # Also charge entry fee for positions that stay but this is only at rebalance
            # Simplification: charge 4bps for all new entries, 4bps for all exits
            # Actually: the requirement says 4bps entry + 4bps exit per side
            # So total round-trip = 8bps per position
            # On rebalance: charge exit fee for all old positions + entry fee for all new positions
            total_exit_fee = sum(positions[t][1] for t in positions) * FEE_BPS / 10000
            total_entry_fee = sum(new_positions[t][1] for t in new_positions) * FEE_BPS / 10000
            fee_cost = total_exit_fee + total_entry_fee

            equity -= fee_cost * equity
            total_fees += fee_cost * equity

            positions = new_positions

            if i % (rebalance_hours * 10) == 0 or i == start_idx:
                n_eligible = len(current_funding)
                top_funding = current_funding.iloc[-1] if len(current_funding) > 0 else 0
                bot_funding = current_funding.iloc[0] if len(current_funding) > 0 else 0
                print(f"  [{current_time.date()}] Equity={equity:.4f}, "
                      f"Eligible={n_eligible}, "
                      f"TopFR={top_funding*8760*100:.1f}%/yr, "
                      f"BotFR={bot_funding*8760*100:.1f}%/yr, "
                      f"Longs={longs[:3]}..., Shorts={shorts[:3]}...")

        hours_since_start += 1

    # ── Step 4: Compute metrics ──
    # Trim equity curve to simulation period
    eq = equity_curve.iloc[start_idx:].dropna()

    if len(eq) < 100:
        print("[ERROR] Not enough data points in equity curve")
        return None

    # Hourly returns from equity curve
    hourly_returns = eq.pct_change().dropna()

    # Annualize
    total_hours = len(hourly_returns)
    total_years = total_hours / ANNUAL_HOURS

    total_return = eq.iloc[-1] / eq.iloc[0] - 1
    ann_return = (1 + total_return) ** (1 / total_years) - 1

    ann_vol = hourly_returns.std() * np.sqrt(ANNUAL_HOURS)
    sharpe = ann_return / ann_vol if ann_vol > 0 else 0

    # Max drawdown
    hwm = eq.cummax()
    dd = eq / hwm - 1
    max_dd = dd.min()

    calmar = ann_return / abs(max_dd) if max_dd != 0 else 0

    # Last 12 months
    last_12m_start = eq.index[-1] - pd.Timedelta(hours=ANNUAL_HOURS)
    eq_12m = eq.loc[last_12m_start:]
    if len(eq_12m) > 100:
        ret_12m = eq_12m.iloc[-1] / eq_12m.iloc[0] - 1
        hr_12m = eq_12m.pct_change().dropna()
        vol_12m = hr_12m.std() * np.sqrt(ANNUAL_HOURS)
        sharpe_12m = ret_12m / vol_12m if vol_12m > 0 else 0
    else:
        ret_12m = 0
        sharpe_12m = 0

    # Top contributors / detractors
    token_total = {t: token_pnl[t] + token_funding[t] for t in eligible.keys()}
    sorted_tokens = sorted(token_total.items(), key=lambda x: x[1], reverse=True)
    top_5 = sorted_tokens[:5]
    bottom_5 = sorted_tokens[-5:]

    results = {
        'label': label,
        'n_positions': n_positions,
        'rebalance_hours': rebalance_hours,
        'adv_threshold': adv_threshold,
        'n_eligible': len(eligible),
        'period_start': eq.index[0],
        'period_end': eq.index[-1],
        'total_years': total_years,
        'total_return': total_return,
        'ann_return': ann_return,
        'ann_vol': ann_vol,
        'sharpe': sharpe,
        'calmar': calmar,
        'max_dd': max_dd,
        'ret_12m': ret_12m,
        'sharpe_12m': sharpe_12m,
        'trade_count': trade_count,
        'total_funding_income': total_funding_income,
        'total_price_pnl': total_price_pnl,
        'total_fees': total_fees,
        'top_contributors': top_5,
        'top_detractors': bottom_5,
        'equity_curve': eq,
        'token_pnl': token_pnl,
        'token_funding': token_funding,
    }

    print(f"\n[RESULTS] {label}")
    print(f"  Period: {eq.index[0].date()} to {eq.index[-1].date()} ({total_years:.1f} years)")
    print(f"  Total Return: {total_return:.2%}")
    print(f"  Annual Return: {ann_return:.2%}")
    print(f"  Annual Vol:    {ann_vol:.2%}")
    print(f"  Sharpe:        {sharpe:.3f}")
    print(f"  Calmar:        {calmar:.3f}")
    print(f"  Max DD:        {max_dd:.2%}")
    print(f"  Last 12M Ret:  {ret_12m:.2%}")
    print(f"  Last 12M Sharpe: {sharpe_12m:.3f}")
    print(f"  Trades:        {trade_count}")
    print(f"  Funding Income: {total_funding_income:.4f} ({total_funding_income/max(eq.iloc[0],0.01)*100:.2f}% of initial)")
    print(f"  Price PnL:      {total_price_pnl:.4f}")
    print(f"  Total Fees:     {total_fees:.4f}")
    print(f"\n  Top 5 Contributors:")
    for t, pnl in top_5:
        fr = token_funding.get(t, 0)
        pr = token_pnl.get(t, 0)
        print(f"    {t:>10s}: total={pnl:+.4f}, funding={fr:+.4f}, price={pr:+.4f}")
    print(f"  Bottom 5 Detractors:")
    for t, pnl in bottom_5:
        fr = token_funding.get(t, 0)
        pr = token_pnl.get(t, 0)
        print(f"    {t:>10s}: total={pnl:+.4f}, funding={fr:+.4f}, price={pr:+.4f}")

    return results


# ══════════════════════════════════════════════════════════════════════════
# REPORT GENERATION
# ══════════════════════════════════════════════════════════════════════════

def generate_report(all_results):
    """Generate markdown report from all variant results."""
    lines = []
    lines.append("# R151: Honest Carry Portfolio — Funding Rate Carry Results")
    lines.append(f"\n**Date:** {datetime.now().strftime('%Y-%m-%d')}")
    lines.append("")
    lines.append("## Hypothesis")
    lines.append("")
    lines.append("Short high-funding tokens to collect carry. This is a PORTFOLIO strategy")
    lines.append("that ranks ALL tokens by funding rate and shorts the top N while longing")
    lines.append("the bottom N as a hedge. The carry should provide steady income regardless")
    lines.append("of market direction.")
    lines.append("")

    # ── Summary Table ──
    lines.append("## Summary Across Variants")
    lines.append("")
    lines.append("| Variant | Ann Ret | Ann Ret (12M) | Sharpe | Sharpe (12M) | Calmar | MaxDD | Trades | Funding Inc | Price PnL |")
    lines.append("|---------|---------|---------------|--------|-------------|--------|-------|--------|------------|----------|")

    for r in all_results:
        if r is None:
            continue
        lines.append(
            f"| {r['label']} | {r['ann_return']:.2%} | {r['ret_12m']:.2%} | "
            f"{r['sharpe']:.3f} | {r['sharpe_12m']:.3f} | {r['calmar']:.3f} | "
            f"{r['max_dd']:.2%} | {r['trade_count']} | "
            f"{r['total_funding_income']:.4f} | {r['total_price_pnl']:.4f} |"
        )
    lines.append("")

    # ── Detailed Results Per Variant ──
    for r in all_results:
        if r is None:
            continue

        lines.append(f"## Variant {r['label']}")
        lines.append("")
        lines.append(f"- **Positions per side:** {r['n_positions']}")
        lines.append(f"- **Rebalance:** every {r['rebalance_hours']}h")
        lines.append(f"- **ADV threshold:** ${r['adv_threshold']/1e6:.0f}M")
        lines.append(f"- **Eligible tokens:** {r['n_eligible']}")
        lines.append(f"- **Period:** {r['period_start'].date()} to {r['period_end'].date()} ({r['total_years']:.1f} years)")
        lines.append("")

        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Total Return | {r['total_return']:.2%} |")
        lines.append(f"| Annual Return (full) | {r['ann_return']:.2%} |")
        lines.append(f"| Annual Return (last 12M) | {r['ret_12m']:.2%} |")
        lines.append(f"| Annual Volatility | {r['ann_vol']:.2%} |")
        lines.append(f"| Sharpe (full) | {r['sharpe']:.3f} |")
        lines.append(f"| Sharpe (last 12M) | {r['sharpe_12m']:.3f} |")
        lines.append(f"| Calmar | {r['calmar']:.3f} |")
        lines.append(f"| Max Drawdown | {r['max_dd']:.2%} |")
        lines.append(f"| Trade Count | {r['trade_count']} |")
        lines.append(f"| Funding Income | {r['total_funding_income']:.4f} |")
        lines.append(f"| Price PnL | {r['total_price_pnl']:.4f} |")
        lines.append(f"| Total Fees | {r['total_fees']:.4f} |")
        lines.append("")

        lines.append("### Top 5 Contributors")
        lines.append("")
        lines.append("| Token | Total PnL | Funding | Price |")
        lines.append("|-------|-----------|---------|-------|")
        for t, pnl in r['top_contributors']:
            fr = r['token_funding'].get(t, 0)
            pr = r['token_pnl'].get(t, 0)
            lines.append(f"| {t} | {pnl:+.4f} | {fr:+.4f} | {pr:+.4f} |")
        lines.append("")

        lines.append("### Bottom 5 Detractors")
        lines.append("")
        lines.append("| Token | Total PnL | Funding | Price |")
        lines.append("|-------|-----------|---------|-------|")
        for t, pnl in r['top_detractors']:
            fr = r['token_funding'].get(t, 0)
            pr = r['token_pnl'].get(t, 0)
            lines.append(f"| {t} | {pnl:+.4f} | {fr:+.4f} | {pr:+.4f} |")
        lines.append("")

    # ── Conclusions ──
    lines.append("## Conclusions")
    lines.append("")

    valid = [r for r in all_results if r is not None]
    if not valid:
        lines.append("No valid results to analyze.")
        return "\n".join(lines)

    best = max(valid, key=lambda r: r['sharpe'])
    lines.append(f"1. **Best variant:** {best['label']} with Sharpe {best['sharpe']:.3f}, "
                 f"Ann Return {best['ann_return']:.2%}")

    # Check if funding is the main driver
    for r in valid:
        funding_frac = r['total_funding_income'] / (r['total_funding_income'] + r['total_price_pnl'] + 1e-10)
        direction = "funding-driven" if abs(r['total_funding_income']) > abs(r['total_price_pnl']) else "price-driven"
        lines.append(f"2. **{r['label']}** is {direction}: "
                     f"funding={r['total_funding_income']:.4f}, price={r['total_price_pnl']:.4f}")

    # Verdict
    lines.append("")
    if best['sharpe'] > 0.5 and best['max_dd'] > -0.30:
        lines.append(f"**VERDICT: PROMISING.** Best variant achieves Sharpe > 0.5 with "
                     f"manageable drawdown ({best['max_dd']:.1%}). Worth further development.")
    elif best['sharpe'] > 0.0:
        lines.append(f"**VERDICT: MARGINAL.** Best Sharpe is {best['sharpe']:.3f} — positive but "
                     f"not compelling after fees. Needs refinement (signal quality, timing).")
    else:
        lines.append(f"**VERDICT: REJECT.** Negative Sharpe across variants. Funding carry alone "
                     f"is not sufficient — price risk dominates carry income.")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("R151: Honest Carry Portfolio — Funding Rate Carry")
    print("=" * 70)

    # ── Load all data ──
    data = load_all_perps()

    # ── Run variants ──
    results = []

    # A. Base: top 5 / bottom 5, weekly rebalance
    r_a = run_carry_backtest(
        data, adv_threshold=50_000_000, n_positions=5,
        rebalance_hours=168, funding_lookback=72,
        label="A. Base (5+5, 168h)"
    )
    results.append(r_a)

    # B. Wider: top 10 / bottom 10, weekly rebalance
    r_b = run_carry_backtest(
        data, adv_threshold=50_000_000, n_positions=10,
        rebalance_hours=168, funding_lookback=72,
        label="B. Wider (10+10, 168h)"
    )
    results.append(r_b)

    # C. Fast: top 5 / bottom 5, 72h rebalance
    r_c = run_carry_backtest(
        data, adv_threshold=50_000_000, n_positions=5,
        rebalance_hours=72, funding_lookback=72,
        label="C. Fast (5+5, 72h)"
    )
    results.append(r_c)

    # D. Quality: ADV > $200M, top 5 / bottom 5, weekly rebalance
    r_d = run_carry_backtest(
        data, adv_threshold=200_000_000, n_positions=5,
        rebalance_hours=168, funding_lookback=72,
        label="D. Quality (5+5, 168h, ADV>200M)"
    )
    results.append(r_d)

    # ── Generate report ──
    print("\n" + "=" * 70)
    print("GENERATING REPORT")
    print("=" * 70)

    report = generate_report(results)
    OUTPUT_MD.write_text(report)
    print(f"\nReport saved to {OUTPUT_MD}")

    print("\n" + "=" * 70)
    print("FULL REPORT")
    print("=" * 70)
    print(report)


if __name__ == '__main__':
    main()
