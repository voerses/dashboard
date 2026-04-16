"""Optimize s523c parameters specifically for 2023 (the worst year at -46.7%).

Sweeps: DIRECTION × THRESHOLD × REGIME_FLIP through the real v4 engine.
Each combo runs a 12-month backtest ending 2024-01-01.
"""
import subprocess, json, sys, os, time

REPO = "/workspace/crypto_backtest"
STRATEGY_FILE = os.path.join(REPO, "strategies", "s523h_regime_adaptive.py")
PYTHON = "/workspace/venv/bin/python"

# Parameters to sweep
DIRECTIONS = ["both", "long", "short"]
THRESHOLDS = [0.5, 0.75, 1.0, 1.5, 2.0]
REGIME_FLIPS = [True, False]  # True = SMA-200 flip active, False = static signs

# Read the original strategy file
with open(STRATEGY_FILE) as f:
    original_code = f.read()


def patch_and_run(direction, threshold, regime_flip, end_date="2024-01-01", months=12):
    """Patch strategy constants, run backtest, restore original."""
    code = original_code

    # Patch DIRECTION
    code = code.replace('DIRECTION = "both"', f'DIRECTION = "{direction}"')

    # Patch THRESHOLD
    code = code.replace('THRESHOLD = 1.0', f'THRESHOLD = {threshold}')

    # Patch regime flip: if regime_flip=False, disable the BTC SMA check
    if not regime_flip:
        # Replace the regime flip block with a no-op
        code = code.replace(
            "bear_regime = (btc_aligned < btc_sma_200d).values",
            "bear_regime = np.zeros(n, dtype=bool)  # DISABLED for sweep"
        )

    # Write patched file
    with open(STRATEGY_FILE, 'w') as f:
        f.write(code)

    # Clear cached results
    for ext in ['trades', 'equity_curve', 'metrics']:
        p = os.path.join(REPO, "results", "v4", f"s523h_regime_adaptive_{months}mo_50k_{ext}.json")
        if os.path.exists(p):
            os.remove(p)

    # Run backtest
    cmd = [
        PYTHON, "v4/portfolio_backtest.py",
        "--strategy", "s523h_regime_adaptive",
        "--months", str(months),
        "--capital", "50000",
        "--market", "perp",
        "--conviction-mode", "ranked",
        "--max-portfolio-positions", "30",
        "--concentration", "0.30",
        "--skip-wf",
        "--end-date", end_date,
    ]
    result = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=120)

    # Restore original
    with open(STRATEGY_FILE, 'w') as f:
        f.write(original_code)

    # Parse results
    metrics_path = os.path.join(REPO, "results", "v4", f"s523h_regime_adaptive_{months}mo_50k_metrics.json")
    if os.path.exists(metrics_path):
        with open(metrics_path) as f:
            return json.load(f)

    # Try to parse from stdout
    lines = result.stdout.split('\n')
    metrics = {}
    for line in lines:
        if 'Total Return:' in line:
            metrics['total_return'] = line.split(':')[1].strip()
        elif 'Sharpe:' in line and 'Sharpe' not in metrics:
            metrics['sharpe'] = line.split(':')[1].strip()
        elif 'Max Drawdown:' in line:
            metrics['max_drawdown'] = line.split(':')[1].strip()
        elif 'Calmar:' in line:
            metrics['calmar'] = line.split(':')[1].strip()
        elif 'Total Trades:' in line:
            metrics['trades'] = line.split(':')[1].strip()
        elif 'Win Rate:' in line:
            metrics['win_rate'] = line.split(':')[1].strip()
    return metrics


def extract_number(s):
    """Extract float from strings like '+60.5%' or '2.88'."""
    if isinstance(s, (int, float)):
        return float(s)
    if isinstance(s, str):
        return float(s.replace('%', '').replace('+', '').replace(',', '').strip())
    return 0.0


def main():
    results = []
    total = len(DIRECTIONS) * len(THRESHOLDS) * len(REGIME_FLIPS)
    i = 0

    print(f"Sweeping {total} combinations for 2023 (12mo ending 2024-01-01)...")
    print()

    for direction in DIRECTIONS:
        for threshold in THRESHOLDS:
            for regime_flip in REGIME_FLIPS:
                i += 1
                label = f"dir={direction:<5} thr={threshold:<4} flip={'ON' if regime_flip else 'OFF'}"
                t0 = time.time()
                try:
                    m = patch_and_run(direction, threshold, regime_flip, "2024-01-01", 12)
                except Exception as e:
                    print(f"  [{i:>2}/{total}] {label}  ERROR: {e}")
                    continue
                elapsed = time.time() - t0

                ret = m.get('total_return', m.get('s523h_regime_adaptive', {}).get('total_return_pct', '?'))
                sharpe = m.get('sharpe', '?')
                maxdd = m.get('max_drawdown', m.get('s523h_regime_adaptive', {}).get('max_drawdown_pct', '?'))
                trades = m.get('trades', m.get('s523h_regime_adaptive', {}).get('n_trades', '?'))
                calmar = m.get('calmar', '?')

                # Try to get from nested structure
                if isinstance(m, dict) and 's523h_regime_adaptive' in m:
                    inner = m['s523h_regime_adaptive']
                    ret = inner.get('total_return_pct', ret)
                    sharpe = inner.get('sharpe', sharpe)
                    maxdd = inner.get('max_drawdown_pct', maxdd)
                    trades = inner.get('n_trades', trades)
                    calmar = inner.get('calmar', calmar)

                results.append({
                    'direction': direction,
                    'threshold': threshold,
                    'regime_flip': regime_flip,
                    'return': ret,
                    'sharpe': sharpe,
                    'maxdd': maxdd,
                    'trades': trades,
                    'calmar': calmar,
                })

                print(f"  [{i:>2}/{total}] {label}  Ret={ret}  Sharpe={sharpe}  MaxDD={maxdd}  Trades={trades}  ({elapsed:.0f}s)")

    # Sort by return (highest first)
    print("\n" + "=" * 100)
    print("TOP 10 — sorted by total return (2023)")
    print("=" * 100)

    def sort_key(r):
        try:
            return extract_number(r['return'])
        except:
            return -9999

    results.sort(key=sort_key, reverse=True)

    print(f"{'#':>3} {'direction':<8} {'threshold':>10} {'flip':>5} {'Return':>10} {'Sharpe':>8} {'MaxDD':>9} {'Calmar':>8} {'Trades':>8}")
    for idx, r in enumerate(results[:10]):
        print(f"{idx+1:>3} {r['direction']:<8} {r['threshold']:>10} {'ON' if r['regime_flip'] else 'OFF':>5} "
              f"{r['return']:>10} {r['sharpe']:>8} {r['maxdd']:>9} {r['calmar']:>8} {r['trades']:>8}")

    # Also show the baseline (both, thr=1.0, flip=OFF)
    baseline = [r for r in results if r['direction'] == 'both' and r['threshold'] == 1.0 and not r['regime_flip']]
    if baseline:
        b = baseline[0]
        print(f"\nBaseline (both, thr=1.0, flip=OFF): Ret={b['return']} Sharpe={b['sharpe']} MaxDD={b['maxdd']}")

    # Save
    with open(os.path.join(REPO, "research", "s523_optimize_2023_results.json"), 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nSaved results to research/s523_optimize_2023_results.json")


if __name__ == "__main__":
    main()
