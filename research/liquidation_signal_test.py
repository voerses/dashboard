"""
Liquidation Signal Predictive Power Test
==========================================
Tests whether OKX liquidation data and liquidation-proxy signals
predict future BTC returns.

Data reality:
- OKX tick-level liquidation data: ~11 hours (2026-03-23), 78K+ ticks
- Binance funding/OI/LS data: ~21 days hourly
- BTC funding proxy (daily): 2020-01-01 to 2026-03-17 (2268 days)
- BTC 1h price: 2020-01-01 to 2026-03-17

Strategy:
Part A: Micro-structure analysis of actual OKX liquidation ticks
Part B: Liquidation-proxy signal IC analysis using 6+ years of daily data
         (funding extremes, LS skew, cumulative funding = liquidation pressure)
Part C: Hourly-resolution analysis using ~21 days of Binance derivatives data
"""

import pandas as pd
import numpy as np
from scipy import stats
import json
import warnings
warnings.filterwarnings('ignore')

# ==============================================================================
# PART A: OKX Tick-Level Liquidation Micro-Structure
# ==============================================================================
print("=" * 80)
print("PART A: OKX TICK-LEVEL LIQUIDATION MICRO-STRUCTURE ANALYSIS")
print("=" * 80)

liq = pd.read_csv('/workspace/crypto_backtest/data/alternative/liquidations/okx_btc_liquidations.csv')
liq['dt'] = pd.to_datetime(liq['timestamp'], unit='ms')
liq['usd_volume'] = liq['price'] * liq['size']

# Deduplicate: rows with identical timestamp+price+size are duplicates
liq_dedup = liq.drop_duplicates(subset=['timestamp', 'price', 'size', 'posSide'])
print(f"\nRaw ticks: {len(liq)}, After dedup: {len(liq_dedup)}")
print(f"Date range: {liq_dedup['dt'].min()} to {liq_dedup['dt'].max()}")
print(f"Duration: {(liq_dedup['dt'].max() - liq_dedup['dt'].min()).total_seconds() / 3600:.1f} hours")

# Aggregate by separating long/short first, then resampling
liq_dedup = liq_dedup.copy()
liq_dedup.set_index('dt', inplace=True)

# Separate long and short USD volumes
liq_dedup['long_usd'] = liq_dedup['usd_volume'].where(liq_dedup['posSide'] == 'long', 0)
liq_dedup['short_usd'] = liq_dedup['usd_volume'].where(liq_dedup['posSide'] == 'short', 0)

def agg_liquidations(df, freq):
    """Resample liquidation ticks to a given frequency."""
    agg = df.resample(freq).agg(
        total_usd=('usd_volume', 'sum'),
        count=('usd_volume', 'count'),
        long_usd=('long_usd', 'sum'),
        short_usd=('short_usd', 'sum'),
        max_single=('usd_volume', 'max'),
        avg_price=('price', 'mean'),
    )
    agg = agg[agg['count'] > 0]
    agg['net_direction'] = agg['long_usd'] - agg['short_usd']
    return agg

liq_5m = agg_liquidations(liq_dedup, '5min')

print(f"\n5-minute bars: {len(liq_5m)}")
print(f"\nLiquidation Summary (5-min bars):")
print(f"  Mean USD volume/bar:   ${liq_5m['total_usd'].mean():,.0f}")
print(f"  Max USD volume/bar:    ${liq_5m['total_usd'].max():,.0f}")
print(f"  Mean count/bar:        {liq_5m['count'].mean():.1f}")
total_long = liq_dedup['long_usd'].sum()
total_short = liq_dedup['short_usd'].sum()
total_all = total_long + total_short
print(f"  Long liquidations $:   ${total_long:,.0f}")
print(f"  Short liquidations $:  ${total_short:,.0f}")
pct_long = total_long / total_all * 100 if total_all > 0 else 0
print(f"  Long % of total:       {pct_long:.1f}%")

# Hourly aggregation of actual tick data
liq_hourly = agg_liquidations(liq_dedup, '1h')

print(f"\nHourly bars: {len(liq_hourly)}")
print("\nHourly liquidation bars:")
print(liq_hourly[['total_usd', 'count', 'net_direction', 'max_single']].to_string())

# Clustering detection in 4h windows
liq_4h = liq_dedup.resample('4h').agg(total_usd=('usd_volume', 'sum'))
vol_mean = liq_4h['total_usd'].mean()
vol_std = liq_4h['total_usd'].std()
liq_4h['cluster'] = liq_4h['total_usd'] > (vol_mean + 2 * vol_std)
print(f"\n4h clustering (>2 std dev):")
print(f"  Mean 4h volume: ${vol_mean:,.0f}")
print(f"  Std 4h volume:  ${vol_std:,.0f}")
print(f"  Threshold:      ${vol_mean + 2*vol_std:,.0f}")
print(f"  Cluster bars:   {liq_4h['cluster'].sum()} / {len(liq_4h)}")

# ==============================================================================
# PART B: LIQUIDATION-PROXY SIGNAL IC ANALYSIS (Daily, 6+ years)
# ==============================================================================
print("\n" + "=" * 80)
print("PART B: LIQUIDATION-PROXY DAILY SIGNAL IC ANALYSIS (2020-2026)")
print("=" * 80)
print("""
Rationale: Liquidation cascades are driven by:
1. Extreme funding rates -> crowded positions -> forced liquidations on reversal
2. High LS skew -> one-sided positioning -> cascade risk
3. Cumulative funding divergence -> cost pressure -> position unwinding
These proxy signals capture the CONDITIONS that precede liquidation events.
""")

# Load BTC funding proxy (daily, 2020-2026)
proxy = pd.read_parquet('/workspace/crypto_backtest/data/alternative/funding_ls_proxy/BTC_funding_proxy.parquet')
proxy = proxy[proxy['symbol'] == 'BTC'].copy()
proxy.index = pd.to_datetime(proxy.index)
proxy = proxy.sort_index()

# Load BTC price (hourly -> daily close)
price = pd.read_parquet('/workspace/crypto_backtest/data/perp/1h_cache/BTC_1h.parquet')
price.index = pd.to_datetime(price.index)
daily_close = price['close'].resample('D').last().dropna()

# Compute forward returns
for horizon in [1, 3, 7, 14]:
    daily_close_shifted = daily_close.shift(-horizon)
    fwd_ret = (daily_close_shifted / daily_close) - 1
    proxy[f'fwd_ret_{horizon}d'] = fwd_ret

# Define liquidation-proxy signals
# Signal 1: liq_volume_zscore proxy = funding rate z-score (20d rolling)
# High funding z-score -> crowded longs -> liquidation risk on downturn
proxy['liq_volume_zscore'] = proxy['funding_rate_daily'].rolling(20).apply(
    lambda x: (x.iloc[-1] - x.mean()) / x.std() if x.std() > 0 else 0
)

# Signal 2: liq_net_direction proxy = LS proxy deviation from 1.0
# >1 = more longs, <1 = more shorts; deviation = directional pressure
proxy['liq_net_direction'] = proxy['ls_proxy_30d'] - 1.0

# Signal 3: liq_cluster proxy = extreme funding events (|z-score| > 2)
# These cluster events correlate with liquidation cascades
proxy['liq_cluster'] = (proxy['fr_zscore_30d'].abs() > 2.0).astype(int)

# Also test the pre-built proxy columns directly
signal_cols = {
    # Primary signals (liquidation proxies)
    'liq_volume_zscore': 'Funding rate 20d z-score (liq volume proxy)',
    'liq_net_direction': 'LS proxy 30d deviation (liq direction proxy)',
    'liq_cluster': 'Extreme funding cluster flag (|z30|>2)',
    # Pre-built funding/positioning signals
    'fr_zscore_7d': 'Funding rate z-score (7d)',
    'fr_zscore_14d': 'Funding rate z-score (14d)',
    'fr_zscore_30d': 'Funding rate z-score (30d)',
    'fr_zscore_60d': 'Funding rate z-score (60d)',
    'ls_proxy_7d': 'LS ratio proxy (7d)',
    'ls_proxy_30d': 'LS ratio proxy (30d)',
    'cum_funding_7d': 'Cumulative funding (7d)',
    'cum_funding_30d': 'Cumulative funding (30d)',
    'fr_pctrank_60d': 'Funding percentile rank (60d)',
    'extreme_long': 'Extreme long flag',
    'extreme_short': 'Extreme short flag',
    'fr_cross_rank': 'Funding cross-sectional rank',
}

horizons = [1, 3, 7, 14]

# Temporal split: 70% IS, 30% OOS
total_days = len(proxy)
split_idx = int(total_days * 0.70)
split_date = proxy.index[split_idx]
print(f"Total observations: {total_days}")
print(f"IS period: {proxy.index[0].date()} to {split_date.date()} ({split_idx} days)")
print(f"OOS period: {split_date.date()} to {proxy.index[-1].date()} ({total_days - split_idx} days)")

is_data = proxy.iloc[:split_idx]
oos_data = proxy.iloc[split_idx:]

results = []

for sig_name, sig_desc in signal_cols.items():
    for h in horizons:
        fwd_col = f'fwd_ret_{h}d'

        for period_name, period_data in [('IS', is_data), ('OOS', oos_data)]:
            valid = period_data[[sig_name, fwd_col]].dropna()
            if len(valid) < 30:
                continue

            ic, pval = stats.spearmanr(valid[sig_name], valid[fwd_col])
            n = len(valid)
            t_stat = ic * np.sqrt((n - 2) / (1 - ic**2)) if abs(ic) < 1 else np.inf

            results.append({
                'signal': sig_name,
                'description': sig_desc,
                'horizon': f'{h}d',
                'period': period_name,
                'IC': ic,
                't_stat': t_stat,
                'p_value': pval,
                'n_obs': n,
            })

results_df = pd.DataFrame(results)

# Pivot for sign consistency check
print("\n" + "-" * 80)
print("SPEARMAN IC RESULTS (All Signals x All Horizons)")
print("-" * 80)

for sig_name, sig_desc in signal_cols.items():
    sig_results = results_df[results_df['signal'] == sig_name]
    if len(sig_results) == 0:
        continue

    print(f"\n  {sig_name}: {sig_desc}")
    for h in horizons:
        is_row = sig_results[(sig_results['horizon'] == f'{h}d') & (sig_results['period'] == 'IS')]
        oos_row = sig_results[(sig_results['horizon'] == f'{h}d') & (sig_results['period'] == 'OOS')]

        if len(is_row) == 0 or len(oos_row) == 0:
            continue

        is_ic = is_row.iloc[0]['IC']
        is_t = is_row.iloc[0]['t_stat']
        oos_ic = oos_row.iloc[0]['IC']
        oos_t = oos_row.iloc[0]['t_stat']
        sign_consistent = (is_ic * oos_ic) > 0

        pass_flag = ""
        if abs(is_ic) > 0.05 and abs(is_t) > 2.0 and sign_consistent:
            pass_flag = " ** PASS **"

        print(f"    {h:>2}d: IS IC={is_ic:+.4f} (t={is_t:+.2f}, n={is_row.iloc[0]['n_obs']})  "
              f"OOS IC={oos_ic:+.4f} (t={oos_t:+.2f}, n={oos_row.iloc[0]['n_obs']})  "
              f"Sign={'YES' if sign_consistent else 'NO'}{pass_flag}")


# ==============================================================================
# PART C: HOURLY-RESOLUTION ANALYSIS (~21 days Binance derivatives)
# ==============================================================================
print("\n" + "=" * 80)
print("PART C: HOURLY SIGNAL IC ANALYSIS (Binance derivatives, ~21 days)")
print("=" * 80)

# Load Binance hourly data
with open('/workspace/crypto_backtest/data/alternative/binance_open_interest_hourly.json') as f:
    oi_raw = json.load(f)
with open('/workspace/crypto_backtest/data/alternative/binance_taker_buy_sell_hourly.json') as f:
    taker_raw = json.load(f)
with open('/workspace/crypto_backtest/data/alternative/binance_global_ls_ratio_hourly.json') as f:
    ls_raw = json.load(f)

# Filter BTC only
oi_btc = [r for r in oi_raw if r.get('symbol') == 'BTCUSDT']
taker_btc = [r for r in taker_raw if r.get('symbol') == 'BTCUSDT']
ls_btc = [r for r in ls_raw if r.get('symbol') == 'BTCUSDT']

oi_df = pd.DataFrame(oi_btc)
oi_df['datetime'] = pd.to_datetime(oi_df['timestamp'], unit='ms')
oi_df.set_index('datetime', inplace=True)
oi_df['sumOpenInterestValue'] = oi_df['sumOpenInterestValue'].astype(float)

taker_df = pd.DataFrame(taker_btc)
taker_df['datetime'] = pd.to_datetime(taker_df['timestamp'], unit='ms')
taker_df.set_index('datetime', inplace=True)
taker_df['buySellRatio'] = taker_df['buySellRatio'].astype(float)
taker_df['buyVol'] = taker_df['buyVol'].astype(float)
taker_df['sellVol'] = taker_df['sellVol'].astype(float)

ls_df = pd.DataFrame(ls_btc)
ls_df['datetime'] = pd.to_datetime(ls_df['timestamp'], unit='ms')
ls_df.set_index('datetime', inplace=True)
ls_df['longShortRatio'] = ls_df['longShortRatio'].astype(float)

# Merge all hourly features with price
hourly_price = price['close'].copy()

hourly = pd.DataFrame(index=hourly_price.index)
hourly['close'] = hourly_price

# Align on common index
hourly['oi_value'] = oi_df['sumOpenInterestValue'].reindex(hourly.index, method='ffill')
hourly['buy_sell_ratio'] = taker_df['buySellRatio'].reindex(hourly.index, method='ffill')
hourly['buy_vol'] = taker_df['buyVol'].reindex(hourly.index, method='ffill')
hourly['sell_vol'] = taker_df['sellVol'].reindex(hourly.index, method='ffill')
hourly['ls_ratio'] = ls_df['longShortRatio'].reindex(hourly.index, method='ffill')

# Restrict to where we have derivative data
hourly = hourly.dropna(subset=['oi_value', 'buy_sell_ratio', 'ls_ratio'])
print(f"\nHourly data range: {hourly.index.min()} to {hourly.index.max()}")
print(f"Hourly observations: {len(hourly)}")

# Compute hourly signals
# OI change z-score (proxy for liquidation pressure)
hourly['oi_pct_change'] = hourly['oi_value'].pct_change()
hourly['oi_zscore_24h'] = hourly['oi_pct_change'].rolling(24).apply(
    lambda x: (x.iloc[-1] - x.mean()) / x.std() if x.std() > 0 else 0
)

# Net taker pressure (buy - sell volume normalized)
hourly['net_taker'] = (hourly['buy_vol'] - hourly['sell_vol']) / (hourly['buy_vol'] + hourly['sell_vol'])

# LS ratio deviation
hourly['ls_deviation'] = hourly['ls_ratio'] - hourly['ls_ratio'].rolling(48).mean()

# OI x LS divergence (high OI + extreme LS = liquidation risk)
hourly['oi_ls_divergence'] = hourly['oi_zscore_24h'] * hourly['ls_deviation']

# Compute forward hourly returns
for h_hours in [4, 12, 24, 48]:
    hourly[f'fwd_ret_{h_hours}h'] = hourly['close'].shift(-h_hours) / hourly['close'] - 1

hourly_signals = {
    'oi_zscore_24h': 'OI change z-score (24h rolling)',
    'net_taker': 'Net taker pressure (buy-sell normalized)',
    'ls_deviation': 'LS ratio deviation from 48h mean',
    'oi_ls_divergence': 'OI x LS divergence (interaction)',
    'buy_sell_ratio': 'Raw buy/sell ratio',
}

hourly_horizons_h = [4, 12, 24, 48]

# Split: 70/30
h_split = int(len(hourly) * 0.70)
h_is = hourly.iloc[:h_split]
h_oos = hourly.iloc[h_split:]

print(f"IS: {len(h_is)} bars ({h_is.index.min().date()} to {h_is.index.max().date()})")
print(f"OOS: {len(h_oos)} bars ({h_oos.index.min().date()} to {h_oos.index.max().date()})")

hourly_results = []

for sig_name, sig_desc in hourly_signals.items():
    for h_hours in hourly_horizons_h:
        fwd_col = f'fwd_ret_{h_hours}h'

        for period_name, period_data in [('IS', h_is), ('OOS', h_oos)]:
            valid = period_data[[sig_name, fwd_col]].dropna()
            if len(valid) < 20:
                continue

            ic, pval = stats.spearmanr(valid[sig_name], valid[fwd_col])
            n = len(valid)
            t_stat = ic * np.sqrt((n - 2) / (1 - ic**2)) if abs(ic) < 1 else np.inf

            hourly_results.append({
                'signal': sig_name,
                'description': sig_desc,
                'horizon': f'{h_hours}h',
                'period': period_name,
                'IC': ic,
                't_stat': t_stat,
                'p_value': pval,
                'n_obs': n,
            })

hourly_results_df = pd.DataFrame(hourly_results)

print("\n" + "-" * 80)
print("HOURLY SPEARMAN IC RESULTS")
print("-" * 80)

for sig_name, sig_desc in hourly_signals.items():
    sig_results = hourly_results_df[hourly_results_df['signal'] == sig_name]
    if len(sig_results) == 0:
        continue

    print(f"\n  {sig_name}: {sig_desc}")
    for h_hours in hourly_horizons_h:
        is_row = sig_results[(sig_results['horizon'] == f'{h_hours}h') & (sig_results['period'] == 'IS')]
        oos_row = sig_results[(sig_results['horizon'] == f'{h_hours}h') & (sig_results['period'] == 'OOS')]

        if len(is_row) == 0 or len(oos_row) == 0:
            continue

        is_ic = is_row.iloc[0]['IC']
        is_t = is_row.iloc[0]['t_stat']
        oos_ic = oos_row.iloc[0]['IC']
        oos_t = oos_row.iloc[0]['t_stat']
        sign_consistent = (is_ic * oos_ic) > 0

        pass_flag = ""
        if abs(is_ic) > 0.05 and abs(is_t) > 2.0 and sign_consistent:
            pass_flag = " ** PASS **"

        print(f"    {h_hours:>3}h: IS IC={is_ic:+.4f} (t={is_t:+.2f}, n={is_row.iloc[0]['n_obs']})  "
              f"OOS IC={oos_ic:+.4f} (t={oos_t:+.2f}, n={oos_row.iloc[0]['n_obs']})  "
              f"Sign={'YES' if sign_consistent else 'NO'}{pass_flag}")

# ==============================================================================
# SUMMARY
# ==============================================================================
print("\n" + "=" * 80)
print("SUMMARY: PASS/FAIL ASSESSMENT")
print("=" * 80)
print("Criteria: |IC| > 0.05, |t-stat| > 2.0, sign consistent IS->OOS")
print()

all_results = pd.concat([results_df, hourly_results_df], ignore_index=True)

pass_count = 0
fail_count = 0
passing_signals = []

# Check daily signals
for sig_name in list(signal_cols.keys()):
    for h in horizons:
        sig_results = results_df[results_df['signal'] == sig_name]
        is_row = sig_results[(sig_results['horizon'] == f'{h}d') & (sig_results['period'] == 'IS')]
        oos_row = sig_results[(sig_results['horizon'] == f'{h}d') & (sig_results['period'] == 'OOS')]

        if len(is_row) == 0 or len(oos_row) == 0:
            fail_count += 1
            continue

        is_ic = is_row.iloc[0]['IC']
        is_t = is_row.iloc[0]['t_stat']
        oos_ic = oos_row.iloc[0]['IC']
        sign_consistent = (is_ic * oos_ic) > 0

        if abs(is_ic) > 0.05 and abs(is_t) > 2.0 and sign_consistent:
            pass_count += 1
            passing_signals.append({
                'signal': sig_name,
                'desc': signal_cols[sig_name],
                'horizon': f'{h}d',
                'is_ic': is_ic,
                'is_t': is_t,
                'oos_ic': oos_ic,
            })
        else:
            fail_count += 1

# Check hourly signals
for sig_name in list(hourly_signals.keys()):
    for h_hours in hourly_horizons_h:
        sig_results = hourly_results_df[hourly_results_df['signal'] == sig_name]
        is_row = sig_results[(sig_results['horizon'] == f'{h_hours}h') & (sig_results['period'] == 'IS')]
        oos_row = sig_results[(sig_results['horizon'] == f'{h_hours}h') & (sig_results['period'] == 'OOS')]

        if len(is_row) == 0 or len(oos_row) == 0:
            fail_count += 1
            continue

        is_ic = is_row.iloc[0]['IC']
        is_t = is_row.iloc[0]['t_stat']
        oos_ic = oos_row.iloc[0]['IC']
        sign_consistent = (is_ic * oos_ic) > 0

        if abs(is_ic) > 0.05 and abs(is_t) > 2.0 and sign_consistent:
            pass_count += 1
            passing_signals.append({
                'signal': sig_name,
                'desc': hourly_signals[sig_name],
                'horizon': f'{h_hours}h',
                'is_ic': is_ic,
                'is_t': is_t,
                'oos_ic': oos_ic,
            })
        else:
            fail_count += 1

print(f"Total signal-horizon combinations tested: {pass_count + fail_count}")
print(f"PASSING: {pass_count}")
print(f"FAILING: {fail_count}")

if passing_signals:
    print(f"\nPassing signal-horizon pairs:")
    for p in passing_signals:
        print(f"  {p['signal']:25s} @ {p['horizon']:>4s}: IS IC={p['is_ic']:+.4f} (t={p['is_t']:+.2f}), OOS IC={p['oos_ic']:+.4f}")
else:
    print("\nNo signal-horizon pairs meet all three criteria.")

# Overall verdict
print("\n" + "-" * 80)
if pass_count > 0:
    print(f"VERDICT: CONDITIONAL PASS - {pass_count} signal-horizon pairs meet criteria")
    print("However, these are PROXY signals for liquidation pressure, not direct liquidation data.")
    print("The actual OKX liquidation tick data has insufficient history for IC analysis.")
else:
    print("VERDICT: FAIL - No signal-horizon pairs meet all three criteria (IC>0.05, t>2, sign consistent)")
    print("Liquidation proxy signals do not show reliable predictive power for BTC returns.")
print("-" * 80)

# Save detailed results to CSV for reference
all_results.to_csv('/workspace/crypto_backtest/research/raw/liquidation_ic_results.csv', index=False)
print(f"\nDetailed results saved to research/raw/liquidation_ic_results.csv")
