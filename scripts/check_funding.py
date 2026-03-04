"""Check funding rate data format and settlement timing."""
import pandas as pd
import numpy as np
from collections import Counter

df = pd.read_parquet('data/perp/1h_cache/BTC_1h.parquet')

# Check when funding_rate actually changes (settlement times)
fr = df['funding_rate']
change_times = df.index[fr.diff().ne(0)]
print('=== FUNDING SETTLEMENT TIMES (first 20 changes) ===')
for t in change_times[:20]:
    print(f'  {t}  hour={t.hour}  funding_rate={fr.loc[t]:+.8f}')

print()
# Check which hours have changes
change_hours = [t.hour for t in change_times]
hour_counts = Counter(change_hours)
print('=== CHANGE FREQUENCY BY HOUR ===')
for h in sorted(hour_counts.keys()):
    print(f'  Hour {h:2d}: {hour_counts[h]} changes')

print()
# Verify: are settlements at 0, 8, 16?
print('=== SETTLEMENT SCHEDULE ===')
print(f'Changes at hour 0:  {hour_counts.get(0, 0)}')
print(f'Changes at hour 8:  {hour_counts.get(8, 0)}')
print(f'Changes at hour 16: {hour_counts.get(16, 0)}')
other = sum(v for k, v in hour_counts.items() if k not in (0, 8, 16))
print(f'Changes at other hours: {other}')

print()
# Show: how much funding would a 10k long position pay in a day?
day_data = df.loc['2024-01-15':'2024-01-15']
if len(day_data) > 0:
    notional = 10000
    hourly_total = sum(notional * day_data['funding_1h'])
    # Settlement-based: only at hours 0, 8, 16
    settlement_bars = day_data[day_data.index.hour.isin([0, 8, 16])]
    settlement_total = sum(notional * settlement_bars['funding_rate'])
    print(f'=== ONE DAY FUNDING COMPARISON (2024-01-15, $10K long) ===')
    print(f'Current (hourly accrual):    ${hourly_total:+.4f}')
    print(f'Correct (8h settlements):    ${settlement_total:+.4f}')
    print(f'Difference: ${abs(hourly_total - settlement_total):.4f}')
    print(f'Same? {np.isclose(hourly_total, settlement_total)}')

print()
# Annual funding cost for a typical long
print('=== ANNUAL FUNDING IMPACT ===')
annual_funding_rate = df['funding_rate'].mean() * 3 * 365  # 3 settlements per day
print(f'Mean 8h rate: {df["funding_rate"].mean()*100:.4f}%')
print(f'Annualized: {annual_funding_rate*100:.2f}%')
print(f'On $10K position: ${10000 * annual_funding_rate:+,.0f}/yr')

# Check: does the amortized approach match settlement approach over long periods?
print()
print('=== LONG-PERIOD ACCURACY CHECK ===')
for period in ['2023-01-01:2023-12-31', '2024-01-01:2024-12-31']:
    start, end = period.split(':')
    pdata = df.loc[start:end]
    if len(pdata) < 100:
        continue
    hourly = sum(10000 * pdata['funding_1h'])
    settlements = pdata[pdata.index.hour.isin([0, 8, 16])]
    settle = sum(10000 * settlements['funding_rate'])
    print(f'  {start[:4]}: hourly=${hourly:+,.2f}  settlement=${settle:+,.2f}  '
          f'diff=${abs(hourly-settle):.2f}  match={np.isclose(hourly, settle, rtol=0.01)}')
