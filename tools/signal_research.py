#!/usr/bin/env python3
"""
Signal Research Toolkit — Spectral Analysis & Signal Discovery

Systematic approach to finding profitable signals:
1. Spectral analysis (FFT) of returns → dominant frequencies
2. Autocorrelation analysis → predictability at various lags
3. Cross-correlation of indicators → which leads price?
4. Optimal holding period analysis → autocorrelation-based
5. Signal-to-noise ratio of each indicator
6. Cost breakeven analysis → minimum edge needed
"""
import sys, os
import numpy as np
import pandas as pd
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "v4"))
from engine import Engine

DATA_DIR = PROJECT_ROOT / "data" / "perp"


def load_token_data(token="BTC", months=12):
    """Load hourly OHLCV data for a token."""
    fpath = DATA_DIR / "1h_cache" / f"{token}_1h.parquet"
    df = pd.read_parquet(fpath)
    n_bars = months * 30 * 24
    if len(df) > n_bars:
        df = df.iloc[-n_bars:]
    return df


def build_context(token="BTC", months=12):
    """Build a strategy context for a token."""
    eng = Engine(data_dir=str(DATA_DIR))
    df = load_token_data(token, months)
    return eng._build_context(token, df)


def spectral_analysis(token="BTC", months=12):
    """FFT analysis of hourly returns to find dominant frequencies."""
    df = load_token_data(token, months)
    close = df['close'].values
    returns = np.diff(np.log(close))
    returns = returns - returns.mean()

    # FFT
    n = len(returns)
    fft_vals = np.fft.rfft(returns)
    power = np.abs(fft_vals) ** 2
    freqs = np.fft.rfftfreq(n, d=1.0)  # frequency in cycles/hour

    # Convert to period in hours
    periods = np.zeros_like(freqs)
    periods[1:] = 1.0 / freqs[1:]
    periods[0] = np.inf

    # Find dominant frequencies (excluding DC component)
    power_norm = power[1:] / power[1:].sum()
    periods_no_dc = periods[1:]

    # Top 20 frequencies by power
    top_idx = np.argsort(power_norm)[-20:][::-1]

    print(f"\n{'='*70}")
    print(f"SPECTRAL ANALYSIS: {token} ({months}mo)")
    print(f"{'='*70}")
    print(f"{'Period (h)':>12} {'Period (d)':>12} {'Power %':>10} {'Frequency':>12}")
    print("-" * 50)
    for i in top_idx:
        p_h = periods_no_dc[i]
        p_d = p_h / 24
        pwr = power_norm[i] * 100
        f = 1.0 / p_h if p_h > 0 else 0
        if p_h < 1000:  # Skip very long periods
            print(f"{p_h:>12.1f} {p_d:>12.1f} {pwr:>10.3f}% {f:>12.6f}")

    # Band-pass analysis: what fraction of variance is at different timescales?
    print(f"\nVariance decomposition by timescale:")
    bands = [(1, 6, "1-6h (noise)"), (6, 24, "6-24h (intraday)"),
             (24, 72, "1-3d (short)"), (72, 168, "3-7d (week)"),
             (168, 720, "1-4w (swing)"), (720, 4380, "1-6mo (trend)")]
    total_power = power[1:].sum()
    for lo, hi, label in bands:
        mask = (periods_no_dc >= lo) & (periods_no_dc < hi)
        band_power = power_norm[mask].sum() * 100
        print(f"  {label:25s}: {band_power:6.2f}%")

    return periods_no_dc, power_norm


def autocorrelation_analysis(token="BTC", months=12, max_lag=168):
    """Autocorrelation of returns at various lags."""
    df = load_token_data(token, months)
    close = df['close'].values
    returns = np.diff(np.log(close))

    print(f"\n{'='*70}")
    print(f"AUTOCORRELATION: {token} ({months}mo)")
    print(f"{'='*70}")

    # Compute autocorrelation at each lag
    n = len(returns)
    mean_r = returns.mean()
    var_r = returns.var()
    acf = np.zeros(max_lag + 1)
    for lag in range(max_lag + 1):
        if lag == 0:
            acf[0] = 1.0
            continue
        acf[lag] = np.corrcoef(returns[:-lag], returns[lag:])[0, 1]

    # Significance level (95%)
    sig_level = 1.96 / np.sqrt(n)

    print(f"95% significance threshold: ±{sig_level:.4f}")
    print(f"\n{'Lag(h)':>8} {'AC':>8} {'Significant':>12} {'Interpretation':>20}")
    print("-" * 55)
    significant_lags = []
    for lag in [1, 2, 3, 4, 6, 8, 12, 24, 48, 72, 96, 120, 168]:
        if lag <= max_lag:
            sig = "***" if abs(acf[lag]) > sig_level else ""
            if abs(acf[lag]) > sig_level:
                significant_lags.append(lag)
            interp = "momentum" if acf[lag] > 0 else "reversal"
            print(f"{lag:>8d} {acf[lag]:>8.4f} {sig:>12} {interp:>20}")

    # Multi-bar returns autocorrelation (holding period analysis)
    print(f"\nHOLDING PERIOD ANALYSIS (multi-bar return AC):")
    print(f"{'Hold(h)':>8} {'AC(1)':>8} {'AC(2)':>8} {'Momentum?':>12}")
    print("-" * 40)
    for hold in [1, 2, 4, 6, 12, 24, 48, 72, 168]:
        if hold * 3 < n:
            # Compute hold-period returns
            hold_ret = close[hold:] / close[:-hold] - 1
            ac1 = np.corrcoef(hold_ret[:-hold], hold_ret[hold:])[0, 1]
            ac2 = np.corrcoef(hold_ret[:-2*hold], hold_ret[2*hold:])[0, 1] if 3*hold < n else 0
            label = "YES" if ac1 > sig_level else ("REV" if ac1 < -sig_level else "no")
            print(f"{hold:>8d} {ac1:>8.4f} {ac2:>8.4f} {label:>12}")

    return acf, significant_lags


def indicator_lead_lag(token="BTC", months=12):
    """Cross-correlation: which indicators lead future returns?"""
    ctx = build_context(token, months)
    close = ctx.ind_1h['close']
    n = len(close)
    fwd_returns = {}

    # Compute forward returns at various horizons
    for h in [1, 4, 12, 24, 48, 72]:
        fwd = np.full(n, np.nan)
        fwd[:n-h] = close[h:] / close[:n-h] - 1
        fwd_returns[h] = fwd

    print(f"\n{'='*70}")
    print(f"INDICATOR PREDICTIVE POWER: {token} ({months}mo)")
    print(f"{'='*70}")

    indicators = {
        'adx': ctx.ind_1h['adx'],
        'plus_di': ctx.ind_1h['plus_di'],
        'minus_di': ctx.ind_1h['minus_di'],
        'rsi': ctx.ind_1h['rsi'],
        'macd': ctx.ind_1h['macd'],
        'macd_hist': ctx.ind_1h['macd_hist'],
        'bb_pct': ctx.ind_1h['bb_pct'],
        'bb_width': ctx.ind_1h['bb_width'],
        'vol_ratio': ctx.ind_1h['vol_ratio'],
        'ret_1': ctx.ind_1h['ret_1'],
        'taker': ctx.ind_1h['taker'],
        'di_spread': ctx.ind_1h['plus_di'] - ctx.ind_1h['minus_di'],
        'ema_slope': (ctx.ind_1h['ema_10'] - ctx.ind_1h['ema_20']) / ctx.ind_1h['ema_20'],
    }

    print(f"\n{'Indicator':>15} | {'1h':>7} {'4h':>7} {'12h':>7} {'24h':>7} {'48h':>7} {'72h':>7} | {'Best':>7}")
    print("-" * 85)

    best_predictors = []
    for name, values in indicators.items():
        corrs = []
        for h, fwd in fwd_returns.items():
            valid = ~(np.isnan(values) | np.isnan(fwd))
            if valid.sum() > 100:
                c = np.corrcoef(values[valid], fwd[valid])[0, 1]
            else:
                c = 0
            corrs.append(c)

        best_h = list(fwd_returns.keys())[np.argmax(np.abs(corrs))]
        best_c = corrs[np.argmax(np.abs(corrs))]
        best_predictors.append((name, best_h, best_c))

        corr_strs = [f"{c:>7.4f}" for c in corrs]
        print(f"{name:>15} | {' '.join(corr_strs)} | {best_c:>7.4f} ({best_h}h)")

    # Sort by absolute correlation
    best_predictors.sort(key=lambda x: abs(x[2]), reverse=True)
    print(f"\nTOP PREDICTORS (by absolute correlation):")
    for name, h, c in best_predictors[:5]:
        direction = "bullish" if c > 0 else "bearish"
        print(f"  {name}: corr={c:.4f} at {h}h horizon ({direction})")

    return best_predictors


def cost_breakeven_analysis(capital=200000, n_positions=15):
    """Calculate minimum edge needed to overcome transaction costs."""
    print(f"\n{'='*70}")
    print(f"COST BREAKEVEN ANALYSIS")
    print(f"{'='*70}")

    base_spread_bps = 3.0  # 0.03%
    impact_coeff = 0.03
    position_size = capital / n_positions

    print(f"Capital: ${capital:,.0f}")
    print(f"Positions: {n_positions}")
    print(f"Avg position: ${position_size:,.0f}")
    print(f"Spread: {base_spread_bps:.1f} bps each way")
    print(f"Impact coeff: {impact_coeff}")

    # Cost per round trip
    spread_cost = position_size * (base_spread_bps / 10000) * 2
    # Impact cost depends on ADV - approximate
    for adv_usd in [1_000_000, 10_000_000, 50_000_000, 200_000_000]:
        participation = position_size / (adv_usd / 24)
        impact = position_size * impact_coeff * participation * 2
        total_cost = spread_cost + impact
        cost_pct = total_cost / position_size * 100

        print(f"\n  ADV ${adv_usd/1e6:.0f}M:")
        print(f"    Spread cost: ${spread_cost:.2f} ({spread_cost/position_size*100:.3f}%)")
        print(f"    Impact cost: ${impact:.2f} ({impact/position_size*100:.3f}%)")
        print(f"    Total round-trip: ${total_cost:.2f} ({cost_pct:.3f}%)")

        # At different trade frequencies, annualized cost
        for trades_per_year in [50, 100, 200, 500, 1000]:
            annual_cost = total_cost * trades_per_year
            annual_pct = cost_pct * trades_per_year
            print(f"    {trades_per_year:>4d} trades/yr → ${annual_cost:,.0f} ({annual_pct:.1f}% of position)")

    # Edge needed to break even
    print(f"\nMINIMUM EDGE PER TRADE (to break even):")
    print(f"  At ADV $50M, spread+impact ≈ {spread_cost/position_size*100+0.02:.3f}% per trade")
    print(f"  Need avg trade PnL > this cost")
    print(f"  With 40% win rate & payoff 1.5: avg PnL = 0.4*1.5W - 0.6*W = 0.0W → break even only if W > cost")


def signal_noise_ratio(token="BTC", months=12):
    """Measure signal-to-noise ratio of various signals."""
    ctx = build_context(token, months)
    close = ctx.ind_1h['close']
    n = len(close)

    print(f"\n{'='*70}")
    print(f"SIGNAL-TO-NOISE ANALYSIS: {token} ({months}mo)")
    print(f"{'='*70}")

    # Define signals as direction predictions
    signals = {
        'ema10>20': np.where(ctx.ind_1h['ema_10'] > ctx.ind_1h['ema_20'], 1, -1),
        'ema20>50': np.where(ctx.ind_1h['ema_20'] > ctx.ind_1h['ema_50'], 1, -1),
        'macd>0': np.where(ctx.ind_1h['macd'] > 0, 1, -1),
        'macd>sig': np.where(ctx.ind_1h['macd'] > ctx.ind_1h['macd_signal'], 1, -1),
        'rsi<50': np.where(ctx.ind_1h['rsi'] < 50, -1, 1),
        'adx_dir': np.where(ctx.ind_1h['plus_di'] > ctx.ind_1h['minus_di'], 1, -1),
        'bb_pct<0.5': np.where(ctx.ind_1h['bb_pct'] < 0.5, -1, 1),
    }

    # For each signal, compute directional accuracy at various horizons
    print(f"\n{'Signal':>15} | {'1h acc':>8} {'4h acc':>8} {'12h acc':>8} {'24h acc':>8} {'48h acc':>8} | {'Best':>8}")
    print("-" * 80)

    for name, sig in signals.items():
        accs = []
        for h in [1, 4, 12, 24, 48]:
            if h < n:
                fwd_ret = close[h:] / close[:n-h] - 1
                sig_trim = sig[:n-h]
                valid = ~np.isnan(sig_trim) & ~np.isnan(fwd_ret)
                # Accuracy: fraction where sign(signal) == sign(fwd_return)
                correct = (np.sign(sig_trim[valid]) == np.sign(fwd_ret[valid])).mean()
                accs.append(correct)
            else:
                accs.append(0.5)

        best_acc = max(accs)
        acc_strs = [f"{a*100:>7.1f}%" for a in accs]
        print(f"{name:>15} | {' '.join(acc_strs)} | {best_acc*100:>7.1f}%")


def run_all(tokens=None, months=12):
    """Run complete analysis suite."""
    if tokens is None:
        tokens = ["BTC", "ETH", "SOL"]

    for token in tokens:
        try:
            spectral_analysis(token, months)
            autocorrelation_analysis(token, months)
            indicator_lead_lag(token, months)
            signal_noise_ratio(token, months)
        except Exception as e:
            print(f"\nERROR analyzing {token}: {e}")

    cost_breakeven_analysis()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--tokens", type=str, default="BTC,ETH,SOL")
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--analysis", type=str, default="all",
                        choices=["all", "spectral", "autocorr", "leadlag", "snr", "cost"])
    args = parser.parse_args()

    tokens = [t.strip() for t in args.tokens.split(",")]

    if args.analysis == "all":
        run_all(tokens, args.months)
    elif args.analysis == "spectral":
        for t in tokens: spectral_analysis(t, args.months)
    elif args.analysis == "autocorr":
        for t in tokens: autocorrelation_analysis(t, args.months)
    elif args.analysis == "leadlag":
        for t in tokens: indicator_lead_lag(t, args.months)
    elif args.analysis == "snr":
        for t in tokens: signal_noise_ratio(t, args.months)
    elif args.analysis == "cost":
        cost_breakeven_analysis()
