"""
EXTREME NEGATIVE FUNDING RATE EDGE STUDY
=========================================
Hypothesis: Deeply negative funding = overcrowded shorts = contrarian long signal.
"""

import pandas as pd
import numpy as np
from scipy import stats
import warnings
warnings.filterwarnings('ignore')

DATA_DIR = '/workspace/crypto_backtest/data/perp/1h_cache/'

# Top 20 tokens by file size (most liquid)
TOKENS = [
    'BTC', 'ETH', 'BNB', 'DOGE', 'XLM', 'SOL', 'ADA', 'BCH', 'TRX', 'LINK',
    'AVAX', 'ETC', 'XRP', 'ENJ', 'SAND', 'NEO', 'KAVA', 'CHZ', 'XMR', 'ATOM'
]

# Train/Test split
SPLIT_DATE = pd.Timestamp('2025-07-01')

# Forward return horizons (hours)
FWD_HORIZONS = [4, 8, 24, 72]

# Z-score windows
ZSCORE_WINDOWS = [24, 72, 168]

print("=" * 80)
print("STEP 1: LOADING DATA")
print("=" * 80)

all_data = {}
for token in TOKENS:
    try:
        df = pd.read_parquet(f'{DATA_DIR}{token}_1h.parquet')
        df = df.sort_index()
        # Use funding_rate as primary
        if 'funding_rate' in df.columns:
            df['funding'] = df['funding_rate']
        elif 'funding_1h' in df.columns:
            df['funding'] = df['funding_1h']
        else:
            print(f"  {token}: NO funding column, skipping")
            continue
        
        # Require at least 1000 rows with funding data
        valid = df['funding'].notna().sum()
        if valid < 1000:
            print(f"  {token}: only {valid} funding rows, skipping")
            continue
        
        all_data[token] = df
        print(f"  {token}: {len(df)} rows, {df.index.min().date()} to {df.index.max().date()}, "
              f"funding non-null: {valid}")
    except Exception as e:
        print(f"  {token}: ERROR {e}")

print(f"\nLoaded {len(all_data)} tokens")

# Load BTC separately for market context
btc = all_data.get('BTC')
if btc is not None:
    btc_ret_24h = btc['close'].pct_change(24).rename('btc_ret_24h')

print("\n" + "=" * 80)
print("STEP 2: CONSTRUCT SIGNALS & FORWARD RETURNS")
print("=" * 80)

panel_rows = []

for token, df in all_data.items():
    d = df[['close', 'funding']].copy()
    
    # Forward returns
    for h in FWD_HORIZONS:
        d[f'fwd_ret_{h}h'] = d['close'].pct_change(h).shift(-h)
    
    # Trailing return (for momentum filter)
    d['ret_24h'] = d['close'].pct_change(24)
    
    # Rolling z-scores of funding
    for w in ZSCORE_WINDOWS:
        roll_mean = d['funding'].rolling(w, min_periods=max(w // 2, 10)).mean()
        roll_std = d['funding'].rolling(w, min_periods=max(w // 2, 10)).std()
        d[f'funding_z_{w}h'] = (d['funding'] - roll_mean) / roll_std.replace(0, np.nan)
    
    # Rolling percentile (30-day = 720 hours)
    d['funding_pct_30d'] = d['funding'].rolling(720, min_periods=360).apply(
        lambda x: stats.percentileofscore(x, x.iloc[-1]) / 100.0, raw=False
    )
    
    # Add BTC context
    if btc is not None and token != 'BTC':
        d = d.join(btc_ret_24h, how='left')
    elif token == 'BTC':
        d['btc_ret_24h'] = d['ret_24h']
    
    d['token'] = token
    panel_rows.append(d)
    
print(f"  Constructed signals for {len(panel_rows)} tokens")

panel = pd.concat(panel_rows)
panel = panel.dropna(subset=['funding'])

# Tag train/test
panel['period'] = np.where(panel.index < SPLIT_DATE, 'TRAIN', 'TEST')

train = panel[panel['period'] == 'TRAIN']
test = panel[panel['period'] == 'TEST']

print(f"  TRAIN: {len(train):,} rows (before {SPLIT_DATE.date()})")
print(f"  TEST:  {len(test):,} rows (after {SPLIT_DATE.date()})")

print("\n" + "=" * 80)
print("STEP 3: INFORMATION COEFFICIENT (RANK IC)")
print("=" * 80)

signal_cols = [f'funding_z_{w}h' for w in ZSCORE_WINDOWS] + ['funding_pct_30d', 'funding']
fwd_cols = [f'fwd_ret_{h}h' for h in FWD_HORIZONS]

print("\n--- RANK IC: Spearman correlation (signal vs forward return) ---")
print(f"{'Signal':<22} {'FwdRet':<12} {'TRAIN IC':>10} {'TRAIN p':>10} {'TEST IC':>10} {'TEST p':>10} {'Decay':>8}")
print("-" * 84)

ic_results = []
for sig in signal_cols:
    for fwd in fwd_cols:
        for period_name, subset in [('TRAIN', train), ('TEST', test)]:
            valid = subset[[sig, fwd]].dropna()
            if len(valid) < 100:
                continue
            rho, pval = stats.spearmanr(valid[sig], valid[fwd])
            ic_results.append({
                'signal': sig, 'fwd': fwd, 'period': period_name,
                'ic': rho, 'pval': pval, 'n': len(valid)
            })

ic_df = pd.DataFrame(ic_results)

for sig in signal_cols:
    for fwd in fwd_cols:
        train_row = ic_df[(ic_df['signal'] == sig) & (ic_df['fwd'] == fwd) & (ic_df['period'] == 'TRAIN')]
        test_row = ic_df[(ic_df['signal'] == sig) & (ic_df['fwd'] == fwd) & (ic_df['period'] == 'TEST')]
        if len(train_row) == 0 or len(test_row) == 0:
            continue
        tr_ic = train_row.iloc[0]['ic']
        tr_p = train_row.iloc[0]['pval']
        te_ic = test_row.iloc[0]['ic']
        te_p = test_row.iloc[0]['pval']
        decay = te_ic / tr_ic if abs(tr_ic) > 1e-6 else np.nan
        print(f"{sig:<22} {fwd:<12} {tr_ic:>10.5f} {tr_p:>10.2e} {te_ic:>10.5f} {te_p:>10.2e} {decay:>8.2f}")

print("\n" + "=" * 80)
print("STEP 4: EXTREME NEGATIVE FUNDING AS ENTRY SIGNAL (OOS ONLY)")
print("=" * 80)

# Use 72h z-score as primary signal (best balance of responsiveness and stability)
primary_signal = 'funding_z_72h'
threshold = -2.0

print(f"\nSignal: {primary_signal} < {threshold}")
print(f"Period: TEST only (after {SPLIT_DATE.date()})\n")

# Filter to test period
oos = test.copy()

# Identify signal events
oos['signal_active'] = oos[primary_signal] < threshold

signal_events = oos[oos['signal_active']].copy()
n_signal = len(signal_events)

print(f"Signal events (OOS): {n_signal:,}")
print(f"Unique tokens in signal: {signal_events['token'].nunique()}")
print(f"Signal rate: {n_signal / len(oos) * 100:.2f}%\n")

print(f"{'Horizon':<12} {'Signal Avg':>12} {'Unconditional':>14} {'Edge':>10} {'t-stat':>10} {'p-value':>10} {'N':>8} {'Hit Rate':>10}")
print("-" * 88)

edge_results = []
for h in FWD_HORIZONS:
    fwd_col = f'fwd_ret_{h}h'
    
    sig_rets = signal_events[fwd_col].dropna()
    unc_rets = oos[fwd_col].dropna()
    
    if len(sig_rets) < 10:
        continue
    
    sig_mean = sig_rets.mean()
    unc_mean = unc_rets.mean()
    edge = sig_mean - unc_mean
    
    # t-test: signal returns vs 0
    t_stat, p_val = stats.ttest_1samp(sig_rets, unc_mean)
    
    hit_rate = (sig_rets > 0).mean()
    
    print(f"{h}h{'':<9} {sig_mean*100:>11.4f}% {unc_mean*100:>13.4f}% {edge*100:>9.4f}% {t_stat:>10.3f} {p_val:>10.4f} {len(sig_rets):>8} {hit_rate*100:>9.1f}%")
    
    edge_results.append({
        'horizon': h, 'signal_mean': sig_mean, 'unconditional_mean': unc_mean,
        'edge': edge, 't_stat': t_stat, 'p_val': p_val, 'n': len(sig_rets),
        'hit_rate': hit_rate
    })

# Monthly breakdown
print(f"\n--- MONTHLY BREAKDOWN (OOS) ---")
print(f"Entry: {primary_signal} < {threshold}")

signal_events_ts = signal_events.copy()
signal_events_ts['month'] = signal_events_ts.index.to_period('M')

print(f"\n{'Month':<12} {'N events':>10} {'Avg 4h ret':>12} {'Avg 8h ret':>12} {'Avg 24h ret':>13} {'Hit24h':>8}")
print("-" * 70)

months_sorted = sorted(signal_events_ts['month'].unique())
monthly_results = []
for m in months_sorted:
    mask = signal_events_ts['month'] == m
    sub = signal_events_ts[mask]
    n = len(sub)
    r4 = sub['fwd_ret_4h'].dropna().mean() * 100
    r8 = sub['fwd_ret_8h'].dropna().mean() * 100
    r24 = sub['fwd_ret_24h'].dropna().mean() * 100
    hit24 = (sub['fwd_ret_24h'].dropna() > 0).mean() * 100
    print(f"{str(m):<12} {n:>10} {r4:>11.4f}% {r8:>11.4f}% {r24:>12.4f}% {hit24:>7.1f}%")
    monthly_results.append({'month': str(m), 'n': n, 'r4': r4, 'r8': r8, 'r24': r24, 'hit24': hit24})

# Also test other z-score windows and thresholds
print(f"\n--- SENSITIVITY: Different z-score windows and thresholds (OOS, 24h forward) ---")
print(f"{'Signal':<22} {'Threshold':>10} {'N':>8} {'Avg 24h':>10} {'Edge':>10} {'Hit':>8}")
print("-" * 72)

unc_24h = oos['fwd_ret_24h'].dropna().mean()

for sig in [f'funding_z_{w}h' for w in ZSCORE_WINDOWS]:
    for thresh in [-1.5, -2.0, -2.5, -3.0]:
        mask = oos[sig] < thresh
        sub = oos.loc[mask, 'fwd_ret_24h'].dropna()
        if len(sub) < 20:
            continue
        avg = sub.mean()
        edge = avg - unc_24h
        hit = (sub > 0).mean()
        print(f"{sig:<22} {thresh:>10.1f} {len(sub):>8} {avg*100:>9.4f}% {edge*100:>9.4f}% {hit*100:>7.1f}%")

# Also test funding percentile
print(f"\n--- SENSITIVITY: Funding percentile thresholds (OOS, 24h forward) ---")
print(f"{'Threshold':<22} {'N':>8} {'Avg 24h':>10} {'Edge':>10} {'Hit':>8}")
print("-" * 60)

for pct_thresh in [0.05, 0.10, 0.15, 0.20]:
    mask = oos['funding_pct_30d'] < pct_thresh
    sub = oos.loc[mask, 'fwd_ret_24h'].dropna()
    if len(sub) < 20:
        continue
    avg = sub.mean()
    edge = avg - unc_24h
    hit = (sub > 0).mean()
    print(f"pct < {pct_thresh:<16.2f} {len(sub):>8} {avg*100:>9.4f}% {edge*100:>9.4f}% {hit*100:>7.1f}%")


print("\n" + "=" * 80)
print("STEP 5: COMBINE WITH MOMENTUM (OOS ONLY)")
print("=" * 80)

print(f"\nEntry: {primary_signal} < {threshold} AND ret_24h > 0 (momentum confirm)")

# Funding only
fund_mask = oos[primary_signal] < threshold
# Funding + momentum
combo_mask = fund_mask & (oos['ret_24h'] > 0)
# Funding + negative momentum (for comparison)
contra_mask = fund_mask & (oos['ret_24h'] <= 0)

print(f"\n{'Condition':<35} {'N':>8} {'Avg 4h':>10} {'Avg 8h':>10} {'Avg 24h':>11} {'Hit24h':>8}")
print("-" * 86)

for label, mask in [
    ('Funding only', fund_mask),
    ('Funding + Momentum (ret24>0)', combo_mask),
    ('Funding + Falling (ret24<=0)', contra_mask),
    ('Unconditional (all OOS)', pd.Series(True, index=oos.index)),
]:
    sub = oos[mask]
    n = len(sub)
    if n < 10:
        continue
    r4 = sub['fwd_ret_4h'].dropna().mean() * 100
    r8 = sub['fwd_ret_8h'].dropna().mean() * 100
    r24 = sub['fwd_ret_24h'].dropna().mean() * 100
    hit24 = (sub['fwd_ret_24h'].dropna() > 0).mean() * 100
    print(f"{label:<35} {n:>8} {r4:>9.4f}% {r8:>9.4f}% {r24:>10.4f}% {hit24:>7.1f}%")

# Also test funding + BTC context
print(f"\n--- With BTC regime filter ---")
btc_up = oos['btc_ret_24h'] > 0

for label, mask in [
    ('Funding + BTC up', fund_mask & btc_up),
    ('Funding + BTC down', fund_mask & ~btc_up),
]:
    sub = oos[mask]
    n = len(sub)
    if n < 10:
        continue
    r4 = sub['fwd_ret_4h'].dropna().mean() * 100
    r8 = sub['fwd_ret_8h'].dropna().mean() * 100
    r24 = sub['fwd_ret_24h'].dropna().mean() * 100
    hit24 = (sub['fwd_ret_24h'].dropna() > 0).mean() * 100
    print(f"{label:<35} {n:>8} {r4:>9.4f}% {r8:>9.4f}% {r24:>10.4f}% {hit24:>7.1f}%")

print("\n" + "=" * 80)
print("STEP 6: TOKEN-LEVEL BREAKDOWN (OOS, 24h forward, primary signal)")
print("=" * 80)

print(f"\n{'Token':<8} {'N signal':>10} {'Avg 24h':>10} {'Edge':>10} {'Hit':>8} {'Sharpe':>8}")
print("-" * 58)

token_results = []
for token in sorted(all_data.keys()):
    mask = (oos['token'] == token) & (oos[primary_signal] < threshold)
    sub = oos.loc[mask, 'fwd_ret_24h'].dropna()
    if len(sub) < 5:
        continue
    avg = sub.mean()
    edge = avg - unc_24h
    hit = (sub > 0).mean()
    sharpe = sub.mean() / sub.std() * np.sqrt(365) if sub.std() > 0 else 0  # annualized
    print(f"{token:<8} {len(sub):>10} {avg*100:>9.4f}% {edge*100:>9.4f}% {hit*100:>7.1f}% {sharpe:>8.2f}")
    token_results.append({'token': token, 'n': len(sub), 'avg': avg, 'edge': edge, 'hit': hit})


print("\n" + "=" * 80)
print("FINAL VERDICT")
print("=" * 80)

# Gather key metrics for verdict
e = pd.DataFrame(edge_results)
best_horizon = e.loc[e['edge'].abs().idxmax()] if len(e) > 0 else None

# Monthly stability check
monthly_df = pd.DataFrame(monthly_results)
if len(monthly_df) > 0:
    recent_months = monthly_df[monthly_df['month'] >= '2026-01']
    pct_positive_months = (monthly_df['r24'] > 0).mean() * 100
    recent_avg = recent_months['r24'].mean() if len(recent_months) > 0 else np.nan
else:
    pct_positive_months = 0
    recent_avg = np.nan

print(f"\n{'METRIC':<45} {'VALUE':>15}")
print("-" * 62)

if best_horizon is not None:
    print(f"{'Best horizon':<45} {int(best_horizon['horizon'])}h")
    print(f"{'Signal avg return (best horizon)':<45} {best_horizon['signal_mean']*100:>14.4f}%")
    print(f"{'Unconditional avg return (best horizon)':<45} {best_horizon['unconditional_mean']*100:>14.4f}%")
    print(f"{'Edge (signal - unconditional)':<45} {best_horizon['edge']*100:>14.4f}%")
    print(f"{'t-statistic':<45} {best_horizon['t_stat']:>15.3f}")
    print(f"{'p-value':<45} {best_horizon['p_val']:>15.4f}")
    print(f"{'Hit rate':<45} {best_horizon['hit_rate']*100:>14.1f}%")
    print(f"{'N signal events (OOS)':<45} {int(best_horizon['n']):>15}")

print(f"{'% months with positive 24h signal return':<45} {pct_positive_months:>14.1f}%")
print(f"{'Recent months avg 24h return (2026)':<45} {recent_avg:>14.4f}%")

# IC decay
for sig in ['funding_z_72h']:
    for fwd in ['fwd_ret_24h']:
        tr = ic_df[(ic_df['signal'] == sig) & (ic_df['fwd'] == fwd) & (ic_df['period'] == 'TRAIN')]
        te = ic_df[(ic_df['signal'] == sig) & (ic_df['fwd'] == fwd) & (ic_df['period'] == 'TEST')]
        if len(tr) > 0 and len(te) > 0:
            print(f"{'IC (72h zscore -> 24h ret) TRAIN':<45} {tr.iloc[0]['ic']:>15.5f}")
            print(f"{'IC (72h zscore -> 24h ret) TEST':<45} {te.iloc[0]['ic']:>15.5f}")

# Kill criteria check
print("\n--- KILL CRITERIA ---")
kill_pass = True

if best_horizon is not None:
    avg_ret = best_horizon['signal_mean'] * 100
    n_events = best_horizon['n']
    
    # Check: avg forward return > 0.5% after costs
    # Estimate costs: ~0.1% round-trip (taker fees + slippage)
    cost_bps = 0.10
    net_ret = avg_ret - cost_bps
    print(f"Avg forward return (best horizon): {avg_ret:.4f}%")
    print(f"After estimated costs ({cost_bps:.2f}%):   {net_ret:.4f}%")
    print(f"Required: > +0.50% after costs")
    
    if net_ret <= 0.50:
        print(">>> FAILS kill metric (return too low)")
        kill_pass = False
    else:
        print(">>> PASSES kill metric (return)")
    
    if n_events < 50:
        print(f">>> FAILS kill metric (only {n_events} events, need >50)")
        kill_pass = False
    else:
        print(f">>> PASSES kill metric (N={n_events} > 50)")

# Final verdict
print("\n" + "=" * 40)
if not kill_pass:
    # Check if marginal
    if best_horizon is not None and best_horizon['edge'] * 100 > 0.1 and best_horizon['p_val'] < 0.10:
        print("VERDICT: MARGINAL EDGE")
        print("Signal shows some predictive power but does not")
        print("clear the kill threshold of >0.5% after costs.")
    else:
        print("VERDICT: NO EDGE")
        print("Signal does not demonstrate reliable predictive")
        print("power in the out-of-sample period.")
else:
    print("VERDICT: EDGE DETECTED")
    print("Signal clears kill threshold. Proceed to")
    print("portfolio-level backtest.")
print("=" * 40)

