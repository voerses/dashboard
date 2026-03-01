"""
V3 Evaluation Pipeline — Liquid Universe Only

Tests V3 + V2 strategies on ALL liquid tokens (>$5M ADV).
Compare vs B&H, focus on post-ETF performance.

Uses cached Binance data from real_data/ directory.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
import pandas as pd
import json
from datetime import datetime

from regime_detector import RegimeDetector, Regime
from strategies import (
    Strategy1_HMMRegimeAdaptive,
    Strategy2_MomentumTrendFollower,
    Strategy4_FundingOIDivergence,
    Strategy5_MultiFactorEnsemble,
)
from strategies_v3 import (
    Strategy6_VolatilityHarvesting,
    Strategy7_CrossSectionalMomentum,
    Strategy8_LiquidityContrarian,
    Strategy9_AdaptiveMultiScale,
    Strategy10_MetaAdaptiveEnsemble,
)
from real_data_fetcher import add_synthetic_derivatives
from walk_forward import WalkForwardEngine
from backtest_engine import BacktestEngine, buy_and_hold
from liquid_universe import LIQUID_TOKENS, get_tier


def load_liquid_tokens(cache_dir='real_data'):
    """Load all liquid tokens that have cached data."""
    datasets = {}
    for ticker in LIQUID_TOKENS:
        path = os.path.join(cache_dir, f'{ticker}_daily.csv')
        if os.path.exists(path):
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            if len(df) >= 365:
                df = add_synthetic_derivatives(df, ticker)
                tier, _ = get_tier(ticker)
                datasets[ticker] = df
    return datasets


def compute_post_etf_metrics(equity_curve, etf_date='2024-01-10'):
    """Compute metrics for post-ETF period only."""
    post = equity_curve[equity_curve.index >= etf_date]
    if len(post) < 30:
        return {'return': 0, 'sharpe': 0, 'max_dd': 0, 'days': 0}

    ret = (post.iloc[-1] / post.iloc[0] - 1) * 100
    daily_rets = post.pct_change().dropna()
    sharpe = float((daily_rets.mean() / daily_rets.std()) * np.sqrt(365)) if daily_rets.std() > 0 else 0
    dd = float(((post / post.cummax()) - 1).min() * 100)
    return {'return': ret, 'sharpe': sharpe, 'max_dd': dd, 'days': len(post)}


def run_quick():
    print("=" * 80)
    print("V3 EVALUATION — Liquid Universe Only")
    print("=" * 80)
    print(f"Run: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Capital: $200K | Fees: 0.1% | Slippage: 5bps")
    print(f"Walk-forward: 365d train, 90d recalibrate")
    print(f"Universe: {len(LIQUID_TOKENS)} liquid tokens (>$5M ADV)")
    print()

    # Load data
    print("[1/4] Loading liquid token data...")
    datasets = load_liquid_tokens()
    print(f"  Loaded {len(datasets)}/{len(LIQUID_TOKENS)} tokens with cached data")
    missing = [t for t in LIQUID_TOKENS if t not in datasets]
    if missing:
        print(f"  Missing (no cached data): {missing}")

    # Load BTC for cross-sectional strategy
    btc_df = datasets.get('BTC')

    # Setup strategies
    v2_strategies = [
        Strategy1_HMMRegimeAdaptive(),
        Strategy2_MomentumTrendFollower(),
        Strategy4_FundingOIDivergence(),
    ]

    v3_strategies = [
        Strategy6_VolatilityHarvesting(),
        Strategy8_LiquidityContrarian(),
        Strategy9_AdaptiveMultiScale(),
    ]

    # Cross-sectional needs BTC data
    cs_strat = Strategy7_CrossSectionalMomentum()
    if btc_df is not None:
        cs_strat.set_market_returns(btc_df)
    v3_strategies.append(cs_strat)

    # Meta-adaptive
    meta = Strategy10_MetaAdaptiveEnsemble()
    if btc_df is not None:
        meta.set_market_returns(btc_df)
    v3_strategies.append(meta)

    all_strategies = v2_strategies + v3_strategies

    wf_engine = WalkForwardEngine(
        initial_capital=200000, fee_rate=0.001, slippage_bps=5,
        train_days=365, recalibrate_every=90,
    )

    # Run backtests
    print(f"\n[2/4] Running walk-forward backtests...")
    print(f"  V2 strategies: {[s.name for s in v2_strategies]}")
    print(f"  V3 strategies: {[s.name for s in v3_strategies]}")

    all_results = []

    for ticker, df in datasets.items():
        tier, size_mult = get_tier(ticker)
        print(f"\n  === {ticker} (T{tier}, {len(df)} days) ===")

        # B&H
        bh = buy_and_hold(df)
        bh.asset = ticker
        bh_post = compute_post_etf_metrics(bh.equity_curve)

        for strat in all_strategies:
            try:
                output = wf_engine.run(df, strat, strat.name, ticker)
                if output is None:
                    continue
                result, signals, wf_regimes = output

                # Post-ETF specific metrics
                post_metrics = compute_post_etf_metrics(result.equity_curve)

                all_results.append({
                    'ticker': ticker,
                    'strategy': strat.name,
                    'version': 'V2' if strat in v2_strategies else 'V3',
                    'return_pct': result.total_return_pct,
                    'sharpe': result.sharpe,
                    'max_dd': result.max_drawdown,
                    'win_rate': result.win_rate,
                    'trades': result.total_trades,
                    'profit_factor': result.profit_factor,
                    'post_etf_return': post_metrics['return'],
                    'post_etf_sharpe': post_metrics['sharpe'],
                    'post_etf_dd': post_metrics['max_dd'],
                    'bh_return': bh.total_return_pct,
                    'bh_sharpe': bh.sharpe,
                    'bh_max_dd': bh.max_drawdown,
                    'bh_post_etf_return': bh_post['return'],
                    'bh_post_etf_sharpe': bh_post['sharpe'],
                    'days': len(df),
                })

                if result.total_trades > 0:
                    marker = '▲' if post_metrics['sharpe'] > bh_post['sharpe'] else '▼'
                    print(f"    {strat.name[:25]:25s} Ret={result.total_return_pct:>7.1f}%  "
                          f"S={result.sharpe:.2f}  DD={result.max_drawdown:.1f}%  "
                          f"PostETF: S={post_metrics['sharpe']:.2f} {marker}  "
                          f"Trades={result.total_trades}")

            except Exception as e:
                print(f"    {strat.name[:25]:25s} ERROR: {e}")

    # Analyze results
    print(f"\n\n[3/4] Analyzing results...")
    results_df = pd.DataFrame(all_results)

    if len(results_df) == 0:
        print("  No results generated!")
        return

    # V2 vs V3 comparison
    print(f"\n{'='*80}")
    print("V2 vs V3 STRATEGY COMPARISON")
    print(f"{'='*80}")

    for version in ['V2', 'V3']:
        vdf = results_df[results_df['version'] == version]
        if len(vdf) == 0:
            continue
        print(f"\n  === {version} Strategies ===")
        for strat_name in vdf['strategy'].unique():
            sdf = vdf[vdf['strategy'] == strat_name]
            print(f"\n    {strat_name} ({len(sdf)} tokens):")
            print(f"      Full Period:  Ret={sdf['return_pct'].mean():>7.1f}%  "
                  f"Sharpe={sdf['sharpe'].mean():.2f}  DD={sdf['max_dd'].mean():.1f}%")
            print(f"      Post-ETF:     Ret={sdf['post_etf_return'].mean():>7.1f}%  "
                  f"Sharpe={sdf['post_etf_sharpe'].mean():.2f}  DD={sdf['post_etf_dd'].mean():.1f}%")
            print(f"      Beat B&H:     {(sdf['sharpe'] > sdf['bh_sharpe']).sum()}/{len(sdf)} (full)  "
                  f"{(sdf['post_etf_sharpe'] > sdf['bh_post_etf_sharpe']).sum()}/{len(sdf)} (post-ETF)")
            print(f"      Avg Trades:   {sdf['trades'].mean():.0f}  "
                  f"WR={sdf['win_rate'].mean():.0f}%  PF={sdf['profit_factor'].mean():.2f}")

    # POST-ETF FOCUS
    print(f"\n{'='*80}")
    print("POST-ETF PERFORMANCE RANKING (Jan 2024 onwards)")
    print(f"{'='*80}")

    # Best post-ETF strategy per token
    for ticker in LIQUID_TOKENS:
        tdf = results_df[results_df['ticker'] == ticker]
        if len(tdf) == 0:
            continue
        best = tdf.loc[tdf['post_etf_sharpe'].idxmax()]
        bh_sharpe = tdf['bh_post_etf_sharpe'].iloc[0]
        beat = 'BEATS B&H' if best['post_etf_sharpe'] > bh_sharpe else 'LOSES to B&H'
        print(f"  {ticker:6s}: Best = {best['strategy']:25s} "
              f"PostETF Sharpe={best['post_etf_sharpe']:.2f} vs B&H={bh_sharpe:.2f} [{beat}]")

    # Overall ranking by post-ETF Sharpe
    print(f"\n  Top 10 Strategy-Token Combos (Post-ETF Sharpe):")
    top10 = results_df.nlargest(10, 'post_etf_sharpe')
    for _, row in top10.iterrows():
        v = row['version']
        print(f"    [{v}] {row['ticker']:6s} {row['strategy']:25s} "
              f"PostETF: S={row['post_etf_sharpe']:.2f} Ret={row['post_etf_return']:.1f}% "
              f"DD={row['post_etf_dd']:.1f}%")

    # KEY INSIGHT: V3 vs V2 post-ETF
    print(f"\n{'='*80}")
    print("KEY INSIGHT: V3 vs V2 POST-ETF")
    print(f"{'='*80}")

    v2_post = results_df[results_df['version'] == 'V2']['post_etf_sharpe'].mean()
    v3_post = results_df[results_df['version'] == 'V3']['post_etf_sharpe'].mean()
    bh_post_avg = results_df['bh_post_etf_sharpe'].mean()

    print(f"\n  Average Post-ETF Sharpe:")
    print(f"    V2 strategies: {v2_post:.3f}")
    print(f"    V3 strategies: {v3_post:.3f}")
    print(f"    Buy & Hold:    {bh_post_avg:.3f}")

    if v3_post > v2_post:
        improvement = (v3_post - v2_post)
        print(f"\n  V3 OUTPERFORMS V2 by {improvement:.3f} Sharpe post-ETF")
    else:
        print(f"\n  V2 still better by {(v2_post - v3_post):.3f} Sharpe — V3 needs tuning")

    if max(v2_post, v3_post) > bh_post_avg:
        print(f"  Active strategies BEAT B&H post-ETF!")
    else:
        print(f"  B&H STILL WINS post-ETF — strategies need more work")

    # TOKEN SELECTION RECOMMENDATIONS
    print(f"\n{'='*80}")
    print("TOKEN SELECTION RECOMMENDATIONS")
    print(f"{'='*80}")

    for ticker in LIQUID_TOKENS:
        tdf = results_df[results_df['ticker'] == ticker]
        if len(tdf) == 0:
            continue

        avg_sharpe = tdf['post_etf_sharpe'].mean()
        best_sharpe = tdf['post_etf_sharpe'].max()
        bh_sharpe = tdf['bh_post_etf_sharpe'].iloc[0]
        n_beat_bh = (tdf['post_etf_sharpe'] > bh_sharpe).sum()

        if best_sharpe > 0.5 and n_beat_bh >= 2:
            verdict = "INVEST — Multiple strategies work"
        elif best_sharpe > 0.3 and n_beat_bh >= 1:
            verdict = "MONITOR — Some edge exists"
        elif avg_sharpe > bh_sharpe:
            verdict = "MONITOR — Slight edge vs B&H"
        elif bh_sharpe > 0.5:
            verdict = "B&H ONLY — Just hold it"
        else:
            verdict = "AVOID — Neither strategies nor B&H work"

        print(f"  {ticker:6s}: {verdict}")
        print(f"           Best strategy Sharpe={best_sharpe:.2f}, "
              f"B&H Sharpe={bh_sharpe:.2f}, "
              f"{n_beat_bh}/{len(tdf)} strategies beat B&H")

    # Save
    print(f"\n[4/4] Saving results...")
    os.makedirs('outputs_v2', exist_ok=True)
    results_df.to_csv('outputs_v2/v3_quick_results.csv', index=False)
    print(f"  Saved v3_quick_results.csv ({len(results_df)} rows)")

    return results_df


if __name__ == '__main__':
    run_quick()
