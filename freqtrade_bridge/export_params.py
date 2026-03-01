from __future__ import annotations

"""
Parameter Bridge: Engine → cpcv_params.json → Freqtrade
=========================================================

Runs the full validation pipeline (CPCV + Walk-Forward) on a strategy,
then exports validated tokens and their parameters to a JSON file
that Freqtrade reads on startup.

Supports multiple exchanges via the exchange registry. Each exchange
gets its own pair_whitelist filtered to tokens actually listed there.

Usage:
    python export_params.py                         # S11 on CPCV tokens (default)
    python export_params.py --strategy s09          # S09 instead
    python export_params.py --all-tokens            # Run on all 49 tokens
    python export_params.py --noise 0.003           # Add ±0.3% noise injection
    python export_params.py --exchange binance      # Target Binance
    python export_params.py --exchange kraken,binance  # Both exchanges
    python export_params.py --out params.json       # Custom output path
"""

import sys
import os
import json
import argparse
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional

try:
    import pandas as pd
except ImportError:
    pd = None

# Lazy imports for heavy dependencies — allows module import in test environments
_engine_imported = False


def _ensure_engine():
    global _engine_imported
    if not _engine_imported:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
        _engine_imported = True


try:
    from freqtrade_bridge.exchange_registry import (
        get_exchange, get_exchange_availability, get_pair_whitelist,
        generate_freqtrade_config, list_exchanges,
    )
except ImportError:
    pass  # Available when exchange_registry is on the path


# =============================================================================
# Noise Injection — perturb prices to test fragility vs live feed deviation
# =============================================================================

def inject_noise(df_1h: pd.DataFrame, noise_pct: float = 0.003,
                 seed: Optional[int] = None) -> pd.DataFrame:
    """
    Add uniform random noise to OHLC prices to simulate live feed deviation.

    Kraken vs Binance price deviation is typically 0.2-0.5% on mid-caps.
    Default ±0.3% tests whether strategy alpha survives realistic data noise.

    Args:
        df_1h: Original 1H OHLCV DataFrame
        noise_pct: Max noise magnitude (0.003 = ±0.3%)
        seed: Random seed for reproducibility
    """
    rng = np.random.default_rng(seed)
    df = df_1h.copy()

    n = len(df)
    for col in ['open', 'high', 'low', 'close']:
        if col in df.columns:
            noise = rng.uniform(-noise_pct, noise_pct, size=n)
            df[col] = df[col] * (1.0 + noise)

    # Ensure OHLC consistency: high >= max(open, close), low <= min(open, close)
    df['high'] = df[['open', 'high', 'close']].max(axis=1)
    df['low'] = df[['open', 'low', 'close']].min(axis=1)

    return df


# =============================================================================
# Corwin-Schultz Spread Estimator
# =============================================================================

def corwin_schultz_spread(df_1h: pd.DataFrame, window: int = 20) -> float:
    """
    Estimate bid-ask spread from OHLCV data using Corwin & Schultz (2012).

    Returns estimated round-trip spread as a fraction (e.g., 0.002 = 0.2%).
    This calibrates realistic slippage per token.
    """
    high = df_1h['high'].values.astype(np.float64)
    low = df_1h['low'].values.astype(np.float64)
    n = len(high)

    if n < window + 2:
        return 0.002  # default 20bps

    # β = sum of squared log(H/L) over 2-bar windows
    log_hl = np.log(high / np.maximum(low, 1e-10))
    log_hl_sq = log_hl ** 2

    # γ uses 2-bar high/low
    spreads = []
    for i in range(window, n):
        beta = np.sum(log_hl_sq[i - window:i])
        # 2-bar high-low
        h2 = np.maximum(high[i - window:i][:-1], high[i - window:i][1:])
        l2 = np.minimum(low[i - window:i][:-1], low[i - window:i][1:])
        gamma = np.sum(np.log(h2 / np.maximum(l2, 1e-10)) ** 2)

        alpha_num = np.sqrt(2 * beta) - np.sqrt(beta)
        denom = 3 - 2 * np.sqrt(2)
        if denom > 0 and alpha_num > 0:
            alpha = alpha_num / denom
            spread = 2 * (np.exp(alpha) - 1) / (1 + np.exp(alpha))
            if 0 < spread < 0.05:  # sanity: < 5%
                spreads.append(spread)

    if spreads:
        return float(np.median(spreads))
    return 0.002


# =============================================================================
# Parameter Export
# =============================================================================

def export_params(strategy_name: str, strategy_fn, tokens: List[str],
                  engine: Engine, noise_pct: float = 0.0,
                  n_noise_trials: int = 5, output_path: str = 'cpcv_params.json',
                  exchanges: Optional[List[str]] = None,
                  verbose: bool = True) -> Dict:
    """
    Run validation + export parameters for Freqtrade.

    If noise_pct > 0, runs N noise trials and only keeps tokens that remain
    profitable across all trials (fragility filter).
    """

    # ── Step 1: Run validation on clean data ──
    if verbose:
        print("=" * 70)
        print(f"PARAMETER EXPORT: {strategy_name}")
        print(f"Tokens: {len(tokens)}  Noise: {'±' + f'{noise_pct*100:.1f}%' if noise_pct > 0 else 'OFF'}")
        print("=" * 70)

    validation = engine.validate(strategy_fn, tokens=tokens, verbose=verbose)
    validated_clean = set(validation['validated_tokens'])

    if verbose:
        print(f"\n  Clean validation: {len(validated_clean)} tokens pass")

    # ── Step 2: Noise injection fragility test ──
    noise_survivors = set(validated_clean)  # start with clean survivors

    if noise_pct > 0 and validated_clean:
        if verbose:
            print(f"\n  Running {n_noise_trials} noise trials (±{noise_pct*100:.1f}%)...")

        for trial in range(n_noise_trials):
            seed = 42 + trial
            # Create noisy engine that injects noise during data load
            noisy_results = {}

            for tk in validated_clean:
                h1_path = os.path.join(engine.data_dir, f'1h_cache/{tk}_1h.parquet')
                if not os.path.exists(h1_path):
                    continue
                df_1h = pd.read_parquet(h1_path)
                df_noisy = inject_noise(df_1h, noise_pct=noise_pct, seed=seed + hash(tk) % 1000)
                res = engine.backtest_token(strategy_fn, tk, df_1h=df_noisy)
                if res:
                    noisy_results[tk] = res

            # Check which tokens remain profitable
            trial_profitable = {tk for tk, r in noisy_results.items()
                                if r['equity'] > engine.capital}
            noise_survivors &= trial_profitable

            if verbose:
                print(f"    Trial {trial+1}: {len(trial_profitable)} profitable, "
                      f"{len(noise_survivors)} survive all trials so far")

        if verbose:
            dropped = validated_clean - noise_survivors
            if dropped:
                print(f"  Noise filter dropped: {', '.join(sorted(dropped))}")
            print(f"  Final noise-robust tokens: {len(noise_survivors)}")

    final_tokens = sorted(noise_survivors)

    # ── Step 3: Compute per-token spread estimates ──
    spreads = {}
    for tk in final_tokens:
        h1_path = os.path.join(engine.data_dir, f'1h_cache/{tk}_1h.parquet')
        if os.path.exists(h1_path):
            df_1h = pd.read_parquet(h1_path)
            spreads[tk] = corwin_schultz_spread(df_1h)

    # ── Step 4: Get per-token backtest stats ──
    token_stats = {}
    bt_results = engine.run(strategy_fn, tokens=final_tokens, verbose=False)
    for tk, r in bt_results.items():
        tier, _ = get_tier(tk)
        pbo = validation['cpcv'].get(tk, {}).get('pbo', 1.0)
        oos_pnl = validation['walk_forward'].get(tk, {}).get('oos_pnl', 0)
        token_stats[tk] = {
            'tier': tier,
            'pbo': round(pbo, 3),
            'oos_pnl': round(oos_pnl, 2),
            'backtest_trades': r['n_trades'],
            'backtest_wr': round(r['win_rate'], 1),
            'backtest_payoff': round(r['payoff_ratio'], 2),
            'est_spread_bps': round(spreads.get(tk, 0.002) * 10000, 1),
        }

    # ── Step 5: Build Freqtrade-compatible params ──
    # Extract strategy parameters from the StrategyResult
    # We need to call the strategy on a sample token to get its params
    sample_ctx = None
    for tk in final_tokens:
        h1_path = os.path.join(engine.data_dir, f'1h_cache/{tk}_1h.parquet')
        if os.path.exists(h1_path):
            df_1h = pd.read_parquet(h1_path)
            sample_ctx = engine._build_context(tk, df_1h)
            if sample_ctx:
                break

    strat_params = {}
    if sample_ctx:
        result = strategy_fn(sample_ctx)
        strat_params = {
            'stop_mult': result.stop_mult,
            'trail_mult': result.trail_mult,
            'target_mult': result.target_mult,
            'no_stop_bars': result.no_stop_bars,
            'min_hold': result.min_hold,
            'max_hold': result.max_hold,
            'edge': result.edge,
        }

    # ── Step 6: Build exchange-specific sections ──
    target_exchanges = exchanges or ['kraken']

    exchange_sections = {}
    for exc_name in target_exchanges:
        try:
            exc = get_exchange(exc_name)
        except KeyError:
            if verbose:
                print(f"  WARNING: Exchange '{exc_name}' not in registry, skipping")
            continue

        exc_tokens = [tk for tk in final_tokens if exc.supports_token(tk)]
        exc_missing = [tk for tk in final_tokens if not exc.supports_token(tk)]
        exc_pairs = exc.get_pair_whitelist(final_tokens)

        exchange_sections[exc_name] = {
            'display_name': exc.display_name,
            'tokens': exc_tokens,
            'pair_whitelist': exc_pairs,
            'missing_tokens': exc_missing,
            'maker_fee': exc.maker_fee,
            'taker_fee': exc.taker_fee,
            'rate_limit_ms': exc.rate_limit_ms,
            'process_throttle_secs': exc.process_throttle_secs,
            'stoploss_on_exchange': exc.stoploss_on_exchange,
            'max_open_trades': min(len(exc_tokens) * 2, exc.max_pairs),
        }

    # ── Step 7: Assemble output ──
    params = {
        'metadata': {
            'strategy': strategy_name,
            'generated_at': datetime.now().isoformat(),
            'data_period': 'Jan 2021 - Feb 2026' if os.path.exists(
                os.path.join(engine.data_dir, '1h_cache_2yr_backup')) else 'Jan 2024 - Feb 2026',
            'capital': engine.capital,
            'fee_rate': engine.fee_rate,
            'slippage_bps': engine.slippage_bps,
            'noise_injection': f'±{noise_pct*100:.1f}%' if noise_pct > 0 else 'none',
            'noise_trials': n_noise_trials if noise_pct > 0 else 0,
            'pbo_threshold': validation['pbo_threshold'],
            'target_exchanges': target_exchanges,
        },

        'strategy_params': strat_params,

        'validated_tokens': final_tokens,

        'token_stats': token_stats,

        # Per-exchange config — strategy shell reads this to filter pairs
        'exchanges': exchange_sections,

        # Legacy flat format for backward compatibility
        'freqtrade': {
            'stake_currency': 'USDT',
            'stake_amount': 'unlimited',
            'max_open_trades': min(len(final_tokens) * 2, 15),
            'process_throttle_secs': 5,
            'dry_run': True,
            'trading_mode': 'spot',
            'pair_whitelist': [f'{tk}/USDT' for tk in final_tokens],
            'timeframe': '1h',
            'stoploss': -strat_params.get('stop_mult', 3.0) * 0.01,
            'trailing_stop': True,
            'trailing_stop_positive': strat_params.get('trail_mult', 3.0) * 0.005,
        },
    }

    # Write
    with open(output_path, 'w') as f:
        json.dump(params, f, indent=2, default=str)

    if verbose:
        print(f"\n{'=' * 70}")
        print(f"EXPORTED: {output_path}")
        print(f"{'=' * 70}")
        print(f"  Strategy: {strategy_name}")
        print(f"  Validated tokens: {len(final_tokens)} — {', '.join(final_tokens)}")
        print(f"  Noise-robust: {'YES' if noise_pct > 0 else 'N/A'}")
        for tk in final_tokens:
            st = token_stats[tk]
            print(f"    {tk:>8}: PBO={st['pbo']:.0%}  OOS=${st['oos_pnl']:+,.0f}  "
                  f"Trades={st['backtest_trades']}  WR={st['backtest_wr']:.0f}%  "
                  f"Spread={st['est_spread_bps']:.1f}bps")

        # Exchange availability summary
        print(f"\n  Exchange coverage:")
        for exc_name, exc_info in exchange_sections.items():
            n_tokens = len(exc_info['tokens'])
            missing = exc_info['missing_tokens']
            print(f"    {exc_info['display_name']:>10}: {n_tokens}/{len(final_tokens)} tokens, "
                  f"{len(exc_info['pair_whitelist'])} pairs")
            if missing:
                print(f"                  missing: {', '.join(missing)}")

    # ── Step 8: Generate per-exchange Freqtrade configs ──
    output_dir = os.path.dirname(output_path) or '.'
    for exc_name in target_exchanges:
        try:
            exc = get_exchange(exc_name)
        except KeyError:
            continue
        exc_tokens = [tk for tk in final_tokens if exc.supports_token(tk)]
        if not exc_tokens:
            continue
        config = generate_freqtrade_config(exc_name, exc_tokens, dry_run=True,
                                           capital=engine.capital)
        config_path = os.path.join(output_dir, f'config_{exc_name}.json')
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
        if verbose:
            print(f"  Config written: {config_path}")

    return params


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description='Export validated strategy params')
    parser.add_argument('--strategy', default='s11', choices=['s11', 's09', 'dm'],
                        help='Strategy to export (default: s11)')
    parser.add_argument('--all-tokens', action='store_true',
                        help='Run on all 49 tokens instead of CPCV subset')
    parser.add_argument('--noise', type=float, default=0.0,
                        help='Noise injection magnitude (0.003 = ±0.3%%)')
    parser.add_argument('--noise-trials', type=int, default=5,
                        help='Number of noise trials')
    parser.add_argument('--exchange', default='kraken,binance',
                        help='Target exchanges, comma-separated (default: kraken,binance)')
    parser.add_argument('--out', default='cpcv_params.json',
                        help='Output JSON path')
    parser.add_argument('--list-exchanges', action='store_true',
                        help='List available exchanges and exit')
    args = parser.parse_args()

    if args.list_exchanges:
        print("Available exchanges:", ', '.join(list_exchanges()))
        return

    # Load strategy
    if args.strategy == 's11':
        from strategies.s11_momentum_burst import strategy as strat_fn
        name = 'S11_momentum_burst'
    elif args.strategy == 's09':
        from strategies.s09_optimized_trend import strategy as strat_fn
        name = 'S09_optimized_trend'
    else:
        strat_fn = strategy_dual_momentum
        name = 'dual_momentum'

    tokens = LIQUID_TOKENS if args.all_tokens else CPCV_ROBUST_TOKENS
    target_exchanges = [e.strip() for e in args.exchange.split(',')]

    engine = Engine()
    export_params(
        strategy_name=name,
        strategy_fn=strat_fn,
        tokens=tokens,
        engine=engine,
        noise_pct=args.noise,
        n_noise_trials=args.noise_trials,
        output_path=args.out,
        exchanges=target_exchanges,
    )


class ExportParams:
    """V3 parameter export: sweep summary -> validated token whitelist.

    Reads sweep_summary JSON, filters by V3 dual-gate pass, and exports
    per-strategy token lists with params, fee schedules, and tier info.
    """

    # Exchange fee schedules (base tier, conservative)
    EXCHANGE_FEES = {
        "binance": {"maker": 0.0010, "taker": 0.0010},
        "kraken": {"maker": 0.0025, "taker": 0.0040},
    }

    # Token liquidity tiers (from CostModel)
    T1_TOKENS = {"BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX",
                 "TRX", "DOT", "LINK", "MATIC", "SHIB", "UNI", "LTC"}
    T3_TOKENS = {"BONK", "FLOKI", "PENGU", "DENT", "OM", "WIF", "PEPE",
                 "JASMY", "CHZ", "GALA", "ENJ", "SAND", "AXS", "MANA",
                 "CRV", "ZRO"}

    def load_sweep_summary(self, path: str) -> dict:
        """Load sweep summary from a JSON file path.

        Args:
            path: Path to sweep_summary JSON file

        Returns:
            Dict of strategy -> token -> results

        Raises:
            FileNotFoundError: If file doesn't exist
        """
        if not os.path.exists(path):
            raise FileNotFoundError(f"No sweep summary found at {path}")
        with open(path) as f:
            return json.load(f)

    def filter_validated(self, sweep_summary: dict, strategy: str) -> list:
        """Filter tokens that pass V3 dual gate for a strategy.

        Args:
            sweep_summary: Full sweep summary dict
            strategy: Strategy name (e.g., 's11')

        Returns:
            List of dicts with 'token', 'params', 'sharpe' for passing tokens
        """
        strategy_data = sweep_summary.get(strategy, {})
        result = []
        for token, data in strategy_data.items():
            if data.get("v3_pass"):
                result.append({
                    "token": token,
                    "params": data.get("params", {}),
                    "sharpe": data.get("sharpe"),
                })
        return result

    def _get_token_tier(self, token: str) -> int:
        """Classify token into liquidity tier."""
        base = token.split("/")[0].upper()
        if base in self.T1_TOKENS:
            return 1
        if base in self.T3_TOKENS:
            return 3
        return 2

    def export(self, sweep_summary: dict, strategy: str,
               exchange: str) -> dict:
        """Export validated tokens with params, fee schedule, and tier info.

        Args:
            sweep_summary: Full sweep summary dict
            strategy: Strategy name
            exchange: Exchange name (for fee schedule)

        Returns:
            Dict with 'params', 'tokens', 'pairs', 'fee_schedule'
        """
        validated = self.filter_validated(sweep_summary, strategy)
        fees = self.EXCHANGE_FEES.get(exchange.lower(), {"maker": 0.001, "taker": 0.001})

        tokens = []
        pairs = []
        for entry in validated:
            tier = self._get_token_tier(entry["token"])
            tokens.append({
                "token": entry["token"],
                "params": entry["params"],
                "tier": tier,
            })
            pairs.append(entry["token"])

        return {
            "strategy": strategy,
            "exchange": exchange,
            "params": {strategy: "validated"},
            "tokens": tokens,
            "pairs": pairs,
            "fee_schedule": fees,
        }

    def export_to_file(self, sweep_summary: dict, strategy: str,
                       exchange: str, output_path: str):
        """Export validated tokens to a JSON file.

        Args:
            sweep_summary: Full sweep summary dict
            strategy: Strategy name
            exchange: Exchange name
            output_path: Path to write JSON output
        """
        result = self.export(sweep_summary, strategy, exchange)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)


if __name__ == '__main__':
    main()
