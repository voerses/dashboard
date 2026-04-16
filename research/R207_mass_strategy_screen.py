"""R207: Mass strategy screen — run every strategy through L12M V4 backtest."""

import subprocess, os, re, sys, json, time

os.chdir('/workspace/crypto_backtest')

# Get all strategy files and extract the strategy ID (prefix before first _)
strat_dir = 'strategies'
strategies = []
seen_ids = set()
for f in sorted(os.listdir(strat_dir)):
    if f.startswith('s') and f.endswith('.py') and not f.startswith('__'):
        # Extract strategy ID: e.g. s98 from s98_sr_breakout_swing.py
        # The CLI loads by prefix match: fname.startswith(strategy_id + "_")
        parts = f.replace('.py', '').split('_', 1)
        sid = parts[0]  # e.g. "s98", "s100", "s514c"
        if sid not in seen_ids:
            seen_ids.add(sid)
            strategies.append(sid)

# Known dead/archived (skip to save time)
skip = set()
# ML strategies all dead
for i in range(240, 320):
    for suffix in ['', 'a', 'b']:
        skip.add(f's{i}{suffix}')
# Known s514 variants (already tested extensively)
skip.update(['s514c', 's514w', 's515', 's516', 's506', 's507',
             's508', 's509', 's510', 's511', 's512'])

total = len(strategies)
skipping = len([s for s in strategies if s in skip])
testing = len([s for s in strategies if s not in skip])

print(f"Total unique strategy IDs: {total}")
print(f"Skipping: {skipping}")
print(f"Testing: {testing}")
print()

results = []
errors = []
fmt = '{:<30} {:>10} {:>8} {:>8} {:>8}'
print(fmt.format('Strategy', 'Return', 'Sharpe', 'MaxDD', 'Trades'))
print('-' * 70)

t0 = time.time()

for idx, sid in enumerate(strategies):
    if sid in skip:
        continue

    try:
        cmd = [
            sys.executable, 'v4/portfolio_backtest.py',
            '--strategy', sid,
            '--market', 'perp',
            '--months', '12',
            '--capital', '100000',
            '--skip-wf',
            '--max-positions', '20',
            '--max-portfolio-positions', '20',
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        output = result.stdout + result.stderr

        # Parse key metrics from output
        ret_match = re.search(r'Total Return:\s+([+-]?\d+\.?\d*)%', output)
        sharpe_match = re.search(r'Sharpe:\s+([+-]?\d+\.?\d*)', output)
        dd_match = re.search(r'Max Drawdown:\s+([+-]?\d+\.?\d*)%', output)
        trades_match = re.search(r'Total Trades:\s+(\d+)', output)

        if ret_match:
            ret = float(ret_match.group(1))
            sharpe = float(sharpe_match.group(1)) if sharpe_match else 0
            dd = float(dd_match.group(1)) if dd_match else 0
            trades = int(trades_match.group(1)) if trades_match else 0

            results.append({
                'sid': sid, 'ret': ret, 'sharpe': sharpe,
                'dd': dd, 'trades': trades
            })

            # Print strategies with positive return AND trades > 10
            if ret > 0 and trades > 10:
                print(fmt.format(sid, f'{ret:+.1f}%', f'{sharpe:.2f}',
                                 f'{dd:.1f}%', str(trades)))
        else:
            errors.append(sid)

    except subprocess.TimeoutExpired:
        errors.append(f'{sid}(timeout)')
    except Exception as e:
        errors.append(f'{sid}({e})')

elapsed = time.time() - t0
print(f"\nScreen completed in {elapsed/60:.1f} minutes")
print(f"Errors/skipped: {len(errors)} — {', '.join(errors[:20])}")

# Sort by Sharpe and show top 20
print(f"\n{'='*70}")
print(f"  TOP 20 BY SHARPE (positive return, >10 trades)")
print(f"{'='*70}")

positive = [r for r in results if r['ret'] > 0 and r['trades'] > 10]
positive.sort(key=lambda x: x['sharpe'], reverse=True)

for r in positive[:20]:
    print(fmt.format(r['sid'], f"{r['ret']:+.1f}%", f"{r['sharpe']:.2f}",
                     f"{r['dd']:.1f}%", str(r['trades'])))

# Also show top 20 by return
print(f"\n{'='*70}")
print(f"  TOP 20 BY RETURN (>10 trades)")
print(f"{'='*70}")

by_ret = sorted(positive, key=lambda x: x['ret'], reverse=True)
for r in by_ret[:20]:
    print(fmt.format(r['sid'], f"{r['ret']:+.1f}%", f"{r['sharpe']:.2f}",
                     f"{r['dd']:.1f}%", str(r['trades'])))

# Show all results sorted by return (including negative)
print(f"\n{'='*70}")
print(f"  ALL RESULTS BY RETURN (>10 trades)")
print(f"{'='*70}")

all_traded = [r for r in results if r['trades'] > 10]
all_traded.sort(key=lambda x: x['ret'], reverse=True)
for r in all_traded:
    print(fmt.format(r['sid'], f"{r['ret']:+.1f}%", f"{r['sharpe']:.2f}",
                     f"{r['dd']:.1f}%", str(r['trades'])))

# Save results
os.makedirs('outputs/ml_discovery', exist_ok=True)
with open('outputs/ml_discovery/R207_mass_screen.json', 'w') as f:
    json.dump(results, f, indent=2)

print(f"\nTotal screened: {len(results)}, with trades>10: {len(all_traded)}, positive: {len(positive)}")
print(f"Results saved to outputs/ml_discovery/R207_mass_screen.json")
