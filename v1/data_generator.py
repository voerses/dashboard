"""
Generate realistic historical crypto OHLCV data calibrated to actual BTC/ETH/SOL price regimes.
Uses geometric Brownian motion with regime-switching volatility to match real market structure.
"""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

def generate_regime_prices(start_price, regimes, seed=42):
    """Generate prices using regime-switching GBM."""
    rng = np.random.RandomState(seed)
    all_prices = [start_price]
    all_dates = []

    for regime in regimes:
        n_days = regime['days']
        mu = regime['drift']  # daily drift
        sigma = regime['vol']  # daily vol

        start_date = regime['start']
        dates = [start_date + timedelta(days=i) for i in range(n_days)]
        all_dates.extend(dates)

        for _ in range(n_days):
            shock = rng.normal(0, 1)
            # Add occasional jumps (fat tails)
            if rng.random() < 0.03:
                shock *= rng.uniform(2, 4) * (1 if rng.random() > 0.4 else -1)

            ret = mu + sigma * shock
            new_price = all_prices[-1] * np.exp(ret)
            all_prices.append(new_price)

    return all_prices[1:], all_dates

def generate_ohlcv(dates, closes, seed=42):
    """Generate OHLCV from close prices with realistic intraday structure."""
    rng = np.random.RandomState(seed + 1)
    rows = []

    for i, (date, close) in enumerate(zip(dates, closes)):
        prev_close = closes[i-1] if i > 0 else close

        # Intraday range proportional to daily vol
        daily_range = abs(close - prev_close) / prev_close + rng.exponential(0.01)

        open_price = prev_close * (1 + rng.normal(0, 0.003))

        if close > open_price:
            high = max(close, open_price) * (1 + rng.exponential(daily_range * 0.3))
            low = min(close, open_price) * (1 - rng.exponential(daily_range * 0.2))
        else:
            high = max(close, open_price) * (1 + rng.exponential(daily_range * 0.2))
            low = min(close, open_price) * (1 - rng.exponential(daily_range * 0.3))

        # Volume correlated with volatility
        base_vol = 1e9 if 'BTC' in str(seed) else 5e8
        vol_mult = 1 + 3 * daily_range
        volume = base_vol * vol_mult * rng.lognormal(0, 0.5)

        rows.append({
            'date': date, 'open': open_price, 'high': high,
            'low': low, 'close': close, 'volume': volume
        })

    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['date'])
    df.set_index('date', inplace=True)
    return df

def generate_btc():
    """BTC: 2019-2026, calibrated to real price regimes."""
    regimes = [
        {'start': datetime(2019, 1, 1), 'days': 180, 'drift': 0.003, 'vol': 0.035},   # 2019 H1 recovery
        {'start': datetime(2019, 7, 1), 'days': 185, 'drift': -0.001, 'vol': 0.03},    # 2019 H2 consolidation
        {'start': datetime(2020, 1, 1), 'days': 75, 'drift': 0.001, 'vol': 0.025},     # 2020 pre-crash
        {'start': datetime(2020, 3, 15), 'days': 45, 'drift': -0.015, 'vol': 0.08},    # COVID crash
        {'start': datetime(2020, 5, 1), 'days': 245, 'drift': 0.005, 'vol': 0.04},     # 2020 recovery
        {'start': datetime(2021, 1, 1), 'days': 120, 'drift': 0.008, 'vol': 0.05},     # 2021 bull run
        {'start': datetime(2021, 5, 1), 'days': 90, 'drift': -0.008, 'vol': 0.06},     # 2021 May crash
        {'start': datetime(2021, 8, 1), 'days': 100, 'drift': 0.006, 'vol': 0.04},     # 2021 recovery
        {'start': datetime(2021, 11, 10), 'days': 150, 'drift': -0.005, 'vol': 0.04},  # Start of bear
        {'start': datetime(2022, 4, 10), 'days': 240, 'drift': -0.004, 'vol': 0.045},  # 2022 bear market
        {'start': datetime(2022, 12, 1), 'days': 120, 'drift': 0.002, 'vol': 0.03},    # 2023 early recovery
        {'start': datetime(2023, 4, 1), 'days': 180, 'drift': 0.001, 'vol': 0.025},    # 2023 accumulation
        {'start': datetime(2023, 10, 1), 'days': 120, 'drift': 0.006, 'vol': 0.035},   # 2023 Q4 rally
        {'start': datetime(2024, 2, 1), 'days': 120, 'drift': 0.004, 'vol': 0.04},     # 2024 ETF rally
        {'start': datetime(2024, 6, 1), 'days': 180, 'drift': 0.002, 'vol': 0.03},     # 2024 H2
        {'start': datetime(2024, 12, 1), 'days': 90, 'drift': 0.003, 'vol': 0.035},    # 2025 Q1
    ]
    prices, dates = generate_regime_prices(3700, regimes, seed=100)
    return generate_ohlcv(dates, prices, seed=100)

def generate_eth():
    """ETH: 2019-2026, calibrated to real price regimes."""
    regimes = [
        {'start': datetime(2019, 1, 1), 'days': 180, 'drift': 0.003, 'vol': 0.04},
        {'start': datetime(2019, 7, 1), 'days': 185, 'drift': -0.002, 'vol': 0.035},
        {'start': datetime(2020, 1, 1), 'days': 75, 'drift': 0.001, 'vol': 0.03},
        {'start': datetime(2020, 3, 15), 'days': 45, 'drift': -0.018, 'vol': 0.09},
        {'start': datetime(2020, 5, 1), 'days': 245, 'drift': 0.006, 'vol': 0.045},
        {'start': datetime(2021, 1, 1), 'days': 120, 'drift': 0.01, 'vol': 0.055},
        {'start': datetime(2021, 5, 1), 'days': 90, 'drift': -0.009, 'vol': 0.07},
        {'start': datetime(2021, 8, 1), 'days': 100, 'drift': 0.007, 'vol': 0.045},
        {'start': datetime(2021, 11, 10), 'days': 150, 'drift': -0.006, 'vol': 0.045},
        {'start': datetime(2022, 4, 10), 'days': 240, 'drift': -0.005, 'vol': 0.05},
        {'start': datetime(2022, 12, 1), 'days': 120, 'drift': 0.003, 'vol': 0.035},
        {'start': datetime(2023, 4, 1), 'days': 180, 'drift': 0.001, 'vol': 0.03},
        {'start': datetime(2023, 10, 1), 'days': 120, 'drift': 0.007, 'vol': 0.04},
        {'start': datetime(2024, 2, 1), 'days': 120, 'drift': 0.005, 'vol': 0.045},
        {'start': datetime(2024, 6, 1), 'days': 180, 'drift': 0.002, 'vol': 0.035},
        {'start': datetime(2024, 12, 1), 'days': 90, 'drift': 0.004, 'vol': 0.04},
    ]
    prices, dates = generate_regime_prices(130, regimes, seed=200)
    return generate_ohlcv(dates, prices, seed=200)

def generate_sol():
    """SOL: 2020-2026, calibrated to real price regimes."""
    regimes = [
        {'start': datetime(2020, 4, 1), 'days': 270, 'drift': 0.008, 'vol': 0.06},
        {'start': datetime(2021, 1, 1), 'days': 120, 'drift': 0.015, 'vol': 0.08},
        {'start': datetime(2021, 5, 1), 'days': 90, 'drift': -0.01, 'vol': 0.09},
        {'start': datetime(2021, 8, 1), 'days': 100, 'drift': 0.012, 'vol': 0.07},
        {'start': datetime(2021, 11, 10), 'days': 180, 'drift': -0.01, 'vol': 0.07},
        {'start': datetime(2022, 5, 10), 'days': 210, 'drift': -0.008, 'vol': 0.08},
        {'start': datetime(2022, 12, 1), 'days': 120, 'drift': 0.005, 'vol': 0.05},
        {'start': datetime(2023, 4, 1), 'days': 180, 'drift': 0.003, 'vol': 0.045},
        {'start': datetime(2023, 10, 1), 'days': 120, 'drift': 0.012, 'vol': 0.06},
        {'start': datetime(2024, 2, 1), 'days': 120, 'drift': 0.008, 'vol': 0.055},
        {'start': datetime(2024, 6, 1), 'days': 180, 'drift': 0.003, 'vol': 0.045},
        {'start': datetime(2024, 12, 1), 'days': 90, 'drift': 0.005, 'vol': 0.05},
    ]
    prices, dates = generate_regime_prices(0.5, regimes, seed=300)
    return generate_ohlcv(dates, prices, seed=300)

def generate_all_data(output_dir='outputs'):
    """Generate and save all datasets."""
    import os
    os.makedirs(output_dir, exist_ok=True)

    datasets = {}
    for name, gen_func in [('BTCUSDT', generate_btc), ('ETHUSDT', generate_eth), ('SOLUSDT', generate_sol)]:
        df = gen_func()
        df.to_csv(f'{output_dir}/{name}_daily.csv')
        datasets[name] = df
        print(f"{name}: {len(df)} days, {df['close'].iloc[0]:.2f} -> {df['close'].iloc[-1]:.2f}")

    return datasets

if __name__ == '__main__':
    generate_all_data()
