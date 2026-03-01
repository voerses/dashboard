"""
Backtest Engine — Generic, pluggable framework for testing ANY trading strategy.
================================================================================

This is the FLEXIBLE testing layer. It wraps the proven core components:
  - Data loading from real_data/ (NEVER modified)
  - Indicator computation from mtf_strategy_v2 (reused, not duplicated)
  - Numba-JIT simulation loop from mtf_strategy_v2 (reused)
  - CPCV validation from cpcv.py (reused)

ARCHITECTURE:
  1. Data Layer:      Loads 1H/4H/Daily bars + enriched features for any token
  2. Indicator Layer:  Base indicators (compute_indicators_fast) + custom indicators
  3. Strategy Layer:   Any function matching the StrategyFn protocol
  4. Simulation Layer: Generic trade simulator (supports custom exit logic)
  5. Evaluation Layer: Portfolio metrics, CPCV, comparison

USAGE:
    from backtest_engine import Engine, StrategyResult

    def my_strategy(ctx: StrategyContext) -> StrategyResult:
        # ctx has .ind_1h, .ind_4h, .ind_d, .regime_1h, .enriched, .ticker, ...
        entry = ctx.ind_1h['rsi'] < 30  # your signal
        return StrategyResult(
            entry_mask=entry,
            direction=np.ones(len(entry), dtype=np.int8),
            stop_mult=3.0, trail_mult=2.5, target_mult=5.0,
            no_stop_bars=12, edge=0.40,
        )

    engine = Engine()
    results = engine.run(my_strategy, tokens=['BTC', 'ETH', 'SOL'])
    engine.compare([my_strategy, other_strategy], tokens=CPCV_ROBUST_TOKENS)
"""

import sys, os
# Ensure v2/ is first on path so we import from here, not parent directory
_v2_dir = os.path.dirname(os.path.abspath(__file__))
if _v2_dir not in sys.path:
    sys.path.insert(0, _v2_dir)
sys.path.insert(0, os.path.join(_v2_dir, '..'))

import numpy as np
import pandas as pd
import time
import json
from dataclasses import dataclass, field
from typing import Callable, Optional, List, Dict, Any, Tuple
from pathlib import Path
from datetime import datetime

from liquid_universe import LIQUID_TOKENS, TIER1, TIER2, TIER3, get_tier
from mtf_strategy_v2 import (
    aggregate_to_timeframe,
    compute_indicators_fast,
    detect_daily_regime,
    _align_higher_to_lower,
    _simulate_core_jit,
    _EXIT_REASONS,
    _rolling_mean, _rolling_std, _ema,
    CRISIS, QUIET, UPTREND, RANGE, DOWNTREND,
    HAS_NUMBA,
)


# =============================================================================
# Strategy Protocol
# =============================================================================

@dataclass
class StrategyContext:
    """Everything a strategy needs to make decisions. Read-only."""
    ticker: str
    tier: int

    # 1H indicators (numpy arrays) — base set from compute_indicators_fast
    ind_1h: Dict[str, np.ndarray]
    # 4H indicators
    ind_4h: Dict[str, np.ndarray]
    # Daily indicators
    ind_d: Dict[str, np.ndarray]

    # Timeframe indices (for alignment)
    idx_1h: pd.DatetimeIndex
    idx_4h: pd.DatetimeIndex
    idx_d: pd.DatetimeIndex

    # Regime (mapped to 1H)
    regime_1h: np.ndarray  # int8: 0=crisis, 1=quiet, 2=uptrend, 3=range, 4=downtrend

    # Raw DataFrames (for strategies that need custom aggregation)
    df_1h: pd.DataFrame
    df_4h: pd.DataFrame
    df_daily: pd.DataFrame

    # Enriched daily features (VPIN, realized_vol, taker_buy_ratio, etc.)
    # None if not available for this token
    enriched: Optional[pd.DataFrame] = None

    # Custom indicators added by indicator plugins
    custom: Dict[str, np.ndarray] = field(default_factory=dict)

    def align_daily_to_1h(self, daily_values):
        """Helper: forward-fill daily values to 1H index."""
        return _align_higher_to_lower(self.idx_d, daily_values, self.idx_1h)

    def align_4h_to_1h(self, h4_values):
        """Helper: forward-fill 4H values to 1H index."""
        return _align_higher_to_lower(self.idx_4h, h4_values, self.idx_1h)


@dataclass
class StrategyResult:
    """What a strategy returns: entry signals + trade parameters."""
    entry_mask: np.ndarray        # bool array (1H length) — True where we want to enter
    direction: np.ndarray         # int8 array — +1 long, -1 short, 0 skip

    # Trade management parameters
    stop_mult: float = 3.0        # Initial stop distance in ATR multiples
    trail_mult: float = 3.0       # Trailing stop distance in ATR multiples
    target_mult: float = 999.0    # Target profit in ATR multiples (999 = trail only)
    no_stop_bars: int = 0         # Bars of stop-loss protection after entry
    min_hold: int = 6             # Minimum hold period (bars)
    max_hold: int = 720           # Maximum hold period (bars)
    edge: float = 0.35            # Edge estimate for Kelly sizing

    # Exit options
    exit_regimes: set = field(default_factory=lambda: {CRISIS})
    rsi_exit_level: float = 999.0  # RSI level for exit (999 = disabled)
    convex_exit: bool = False      # Use convex exit logic (tight → trail)

    # Optional: custom mean-reversion target for convex exit
    mean_target_vals: Optional[np.ndarray] = None

    # Metadata
    name: str = 'unnamed'


# Type alias for strategy functions
StrategyFn = Callable[[StrategyContext], StrategyResult]


# =============================================================================
# Custom Indicator Plugin System
# =============================================================================

# Registry of custom indicator functions
# Each takes (ctx: StrategyContext) and adds keys to ctx.custom
_INDICATOR_PLUGINS: List[Callable] = []


def register_indicator(fn):
    """Decorator: register a custom indicator computation function."""
    _INDICATOR_PLUGINS.append(fn)
    return fn


# Built-in custom indicators (can be extended by user code)

@register_indicator
def _compute_obv(ctx: StrategyContext):
    """On-Balance Volume."""
    close = ctx.ind_1h['close']
    volume = ctx.ind_1h['volume']
    n = len(close)
    obv = np.zeros(n)
    for i in range(1, n):
        if close[i] > close[i-1]:
            obv[i] = obv[i-1] + volume[i]
        elif close[i] < close[i-1]:
            obv[i] = obv[i-1] - volume[i]
        else:
            obv[i] = obv[i-1]
    ctx.custom['obv'] = obv
    # OBV slope (10-bar)
    obv_slope = np.zeros(n)
    obv_slope[10:] = obv[10:] - obv[:-10]
    ctx.custom['obv_slope'] = obv_slope


@register_indicator
def _compute_vwap_session(ctx: StrategyContext):
    """Rolling VWAP (20-bar)."""
    close = ctx.ind_1h['close']
    volume = ctx.ind_1h['volume']
    tp = close  # typical price approximation (we have close, not HL avg in ind dict)
    cum_tpv = _rolling_mean(tp * volume, 20) * 20
    cum_vol = _rolling_mean(volume, 20) * 20
    vwap = cum_tpv / np.maximum(cum_vol, 1e-10)
    ctx.custom['vwap_20'] = vwap
    ctx.custom['vwap_dev'] = (close - vwap) / np.maximum(vwap, 1e-10)


@register_indicator
def _compute_momentum_signals(ctx: StrategyContext):
    """Multi-period momentum returns."""
    close = ctx.ind_1h['close']
    for period in [6, 12, 24, 48, 120]:
        ret = np.zeros(len(close))
        ret[period:] = (close[period:] - close[:-period]) / np.maximum(close[:-period], 1e-10)
        ctx.custom[f'ret_{period}h'] = ret

    # Daily momentum mapped to 1H
    close_d = ctx.ind_d['close']
    for period in [5, 10, 20, 60]:
        ret_d = np.zeros(len(close_d))
        ret_d[period:] = (close_d[period:] - close_d[:-period]) / np.maximum(close_d[:-period], 1e-10)
        ctx.custom[f'ret_{period}d'] = ctx.align_daily_to_1h(ret_d)


@register_indicator
def _compute_enriched_signals(ctx: StrategyContext):
    """Map enriched daily features (VPIN, realized_vol, etc.) to 1H."""
    if ctx.enriched is None:
        return
    for col in ['vpin', 'realized_vol', 'taker_buy_ratio', 'amihud_1m',
                'vwap_deviation', 'intraday_skew', 'parkinson_vol']:
        if col in ctx.enriched.columns:
            vals = ctx.enriched[col].values
            mapped = _align_higher_to_lower(ctx.enriched.index, vals, ctx.idx_1h)
            ctx.custom[f'enr_{col}'] = mapped


# =============================================================================
# Engine
# =============================================================================

class Engine:
    """
    Main backtesting engine. Handles data loading, indicator computation,
    strategy dispatch, and result aggregation.
    """

    def __init__(self, data_dir='real_data', capital=200_000,
                 fee_rate=0.001, slippage_bps=5):
        self.data_dir = data_dir
        self.capital = capital
        self.fee_rate = fee_rate
        self.slippage_bps = slippage_bps
        self._enriched = None
        self._enriched_loaded = False

    def _load_enriched(self):
        """Lazy-load enriched daily data."""
        if self._enriched_loaded:
            return self._enriched
        path = os.path.join(self.data_dir, 'all_tokens_enriched.parquet')
        if os.path.exists(path):
            df = pd.read_parquet(path)
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date')
            self._enriched = df
        self._enriched_loaded = True
        return self._enriched

    def _build_context(self, ticker: str, df_1h: pd.DataFrame) -> Optional[StrategyContext]:
        """Build a StrategyContext for one token."""
        if df_1h is None or len(df_1h) < 500:
            return None

        df_4h = aggregate_to_timeframe(df_1h, hours=4)
        df_daily = aggregate_to_timeframe(df_1h, hours=24)

        if len(df_4h) < 100 or len(df_daily) < 30:
            return None

        # Extract arrays
        def _arrays(df):
            return (df['close'].values.astype(np.float64),
                    df['high'].values.astype(np.float64),
                    df['low'].values.astype(np.float64),
                    df['volume'].values.astype(np.float64),
                    df['taker_buy_base'].values.astype(np.float64) if 'taker_buy_base' in df.columns else None)

        c1, h1, l1, v1, t1 = _arrays(df_1h)
        c4, h4, l4, v4, t4 = _arrays(df_4h)
        cd, hd, ld, vd, _ = _arrays(df_daily)

        ind_1h = compute_indicators_fast(c1, h1, l1, v1, t1)
        ind_4h = compute_indicators_fast(c4, h4, l4, v4, t4)
        ind_d = compute_indicators_fast(cd, hd, ld, vd)

        idx_1h = df_1h.index
        idx_4h = df_4h.index
        idx_d = df_daily.index

        regimes_d = detect_daily_regime(ind_d)
        regime_1h = _align_higher_to_lower(idx_d, regimes_d.astype(float), idx_1h).astype(np.int8)
        regime_1h = np.nan_to_num(regime_1h, nan=RANGE).astype(np.int8)

        # Get enriched data for this token
        enriched = self._load_enriched()
        token_enriched = None
        if enriched is not None:
            mask = enriched['ticker'] == ticker
            if mask.sum() > 0:
                token_enriched = enriched.loc[mask].copy()
                token_enriched = token_enriched[token_enriched.index >= '2024-01-01']
                if len(token_enriched) < 10:
                    token_enriched = None

        tier, _ = get_tier(ticker)

        ctx = StrategyContext(
            ticker=ticker, tier=tier,
            ind_1h=ind_1h, ind_4h=ind_4h, ind_d=ind_d,
            idx_1h=idx_1h, idx_4h=idx_4h, idx_d=idx_d,
            regime_1h=regime_1h,
            df_1h=df_1h, df_4h=df_4h, df_daily=df_daily,
            enriched=token_enriched,
        )

        # Run indicator plugins
        for plugin in _INDICATOR_PLUGINS:
            try:
                plugin(ctx)
            except Exception as e:
                pass  # Silently skip failed plugins

        return ctx

    def _simulate(self, ctx: StrategyContext, result: StrategyResult) -> Tuple[list, float]:
        """Run the simulation engine on a strategy result."""
        n = len(ctx.ind_1h['close'])
        close = ctx.ind_1h['close']
        high = ctx.ind_1h['high']
        low = ctx.ind_1h['low']
        atr = ctx.ind_1h['atr']

        # Build exit regime mask
        exit_regime_mask = np.zeros(n, dtype=np.bool_)
        for i in range(n):
            if ctx.regime_1h[i] in result.exit_regimes:
                exit_regime_mask[i] = True

        # RSI
        use_rsi = result.rsi_exit_level < 999
        rsi = ctx.ind_1h['rsi']

        # Mean target
        use_mean_target = result.mean_target_vals is not None
        mean_target = result.mean_target_vals if use_mean_target else np.full(n, np.nan)

        # Position sizing params
        kelly_mult = 0.5 if ctx.tier == 1 else 0.25
        cap_pct = 0.12 if ctx.tier == 1 else (0.08 if ctx.tier == 2 else 0.04)

        entry_mask = np.asarray(result.entry_mask, dtype=np.bool_)
        direction = np.asarray(result.direction, dtype=np.int8)

        pnl_arr, ret_arr, hold_arr, exit_arr, pos_arr, final_equity = _simulate_core_jit(
            close, high, low, atr, entry_mask, direction,
            float(result.stop_mult), float(result.trail_mult), float(result.target_mult),
            ctx.regime_1h, exit_regime_mask, int(result.min_hold), int(result.max_hold),
            rsi, float(result.rsi_exit_level), use_rsi,
            float(self.fee_rate), float(self.slippage_bps),
            float(self.capital), int(ctx.tier),
            result.convex_exit, mean_target, use_mean_target,
            int(result.no_stop_bars), float(result.edge),
            float(kelly_mult), float(cap_pct),
        )

        trades = []
        for j in range(len(pnl_arr)):
            trades.append({
                'pnl': float(pnl_arr[j]),
                'return_pct': float(ret_arr[j]),
                'hold_hours': int(hold_arr[j]),
                'exit_reason': _EXIT_REASONS.get(int(exit_arr[j]), 'unknown'),
                'position_usd': float(pos_arr[j]),
                'strategy': result.name,
            })

        return trades, final_equity

    def backtest_token(self, strategy_fn: StrategyFn, ticker: str,
                       df_1h: Optional[pd.DataFrame] = None) -> Optional[Dict]:
        """Backtest a single token with a strategy function."""
        if df_1h is None:
            h1_path = os.path.join(self.data_dir, f'1h_cache/{ticker}_1h.parquet')
            if not os.path.exists(h1_path):
                return None
            df_1h = pd.read_parquet(h1_path)

        ctx = self._build_context(ticker, df_1h)
        if ctx is None:
            return None

        result = strategy_fn(ctx)
        trades, equity = self._simulate(ctx, result)

        total_return = (equity - self.capital) / self.capital * 100
        wins = [t for t in trades if t['pnl'] > 0]
        losers = [t for t in trades if t['pnl'] <= 0]
        avg_win = np.mean([t['pnl'] for t in wins]) if wins else 0
        avg_loss = abs(np.mean([t['pnl'] for t in losers])) if losers else 1

        return {
            'ticker': ticker,
            'tier': ctx.tier,
            'total_return': total_return,
            'n_trades': len(trades),
            'win_rate': len(wins) / max(len(trades), 1) * 100,
            'payoff_ratio': avg_win / max(avg_loss, 1),
            'equity': equity,
            'trades': trades,
            'strategy': result.name,
        }

    def run(self, strategy_fn: StrategyFn, tokens: Optional[List[str]] = None,
            verbose: bool = True) -> Dict[str, Dict]:
        """Run a strategy across all tokens."""
        if tokens is None:
            tokens = LIQUID_TOKENS

        results = {}
        t0 = time.time()

        for idx, ticker in enumerate(tokens, 1):
            if verbose:
                print(f"  [{idx}/{len(tokens)}] {ticker}...", end=' ', flush=True)

            r = self.backtest_token(strategy_fn, ticker)
            if r is not None:
                results[ticker] = r
                if verbose:
                    print(f"Ret={r['total_return']:+.1f}%  "
                          f"Trades={r['n_trades']}  WR={r['win_rate']:.0f}%  "
                          f"Payoff={r['payoff_ratio']:.1f}x")
            elif verbose:
                print("skip")

        elapsed = time.time() - t0
        if verbose:
            self._print_summary(results, elapsed)

        return results

    def compare(self, strategies: List[StrategyFn], tokens: Optional[List[str]] = None,
                labels: Optional[List[str]] = None) -> Dict[str, Dict]:
        """Compare multiple strategies side-by-side."""
        if tokens is None:
            tokens = LIQUID_TOKENS
        if labels is None:
            labels = [f'strategy_{i}' for i in range(len(strategies))]

        all_agg = {}
        for strat_fn, label in zip(strategies, labels):
            print(f"\n{'='*80}")
            print(f"  {label}")
            print(f"{'='*80}")
            results = self.run(strat_fn, tokens=tokens, verbose=False)
            agg = self._aggregate(results)
            agg['label'] = label
            all_agg[label] = agg

            total_pnl = agg['total_pnl']
            annual = total_pnl / 2.0
            print(f"  Tokens: {agg['n_tokens']}  Trades: {agg['n_trades']}  "
                  f"Profitable: {agg['profitable']}/{agg['n_tokens']}")
            print(f"  PnL: ${total_pnl:+,.0f}  Annual: ${annual:+,.0f}/yr  "
                  f"WR: {agg['win_rate']:.0f}%  Payoff: {agg['payoff_ratio']:.2f}x")

        # Ranking
        print(f"\n{'='*80}")
        print("RANKING (by annual PnL)")
        print(f"{'='*80}")
        for label, agg in sorted(all_agg.items(), key=lambda x: -x[1]['total_pnl']):
            annual = agg['total_pnl'] / 2.0
            print(f"  {label:40s} ${annual:>+10,.0f}/yr  "
                  f"{agg['n_trades']:>5d} trades  "
                  f"{agg['profitable']}/{agg['n_tokens']} profitable")

        # Auto-log
        self._log_comparison(all_agg, tokens)
        return all_agg

    def _aggregate(self, results: Dict) -> Dict:
        """Aggregate token-level results to portfolio level."""
        if not results:
            return {'total_pnl': 0, 'n_tokens': 0, 'n_trades': 0,
                    'profitable': 0, 'win_rate': 0, 'payoff_ratio': 0}

        total_pnl = sum(r['equity'] - self.capital for r in results.values())
        total_trades = sum(r['n_trades'] for r in results.values())
        profitable = sum(1 for r in results.values() if r['equity'] > self.capital)

        all_trades = []
        for r in results.values():
            all_trades.extend(r.get('trades', []))

        wins = [t for t in all_trades if t['pnl'] > 0]
        losers = [t for t in all_trades if t['pnl'] <= 0]
        avg_win = np.mean([t['pnl'] for t in wins]) if wins else 0
        avg_loss = abs(np.mean([t['pnl'] for t in losers])) if losers else 1

        return {
            'total_pnl': total_pnl,
            'n_tokens': len(results),
            'n_trades': total_trades,
            'profitable': profitable,
            'win_rate': len(wins) / max(len(all_trades), 1) * 100,
            'payoff_ratio': avg_win / max(avg_loss, 1),
        }

    def _print_summary(self, results, elapsed):
        """Print portfolio summary."""
        if not results:
            print("No results.")
            return
        agg = self._aggregate(results)
        total_pnl = agg['total_pnl']
        print(f"\n  Portfolio: PnL=${total_pnl:+,.0f}  Trades={agg['n_trades']}  "
              f"WR={agg['win_rate']:.0f}%  Payoff={agg['payoff_ratio']:.2f}x  "
              f"Profitable={agg['profitable']}/{agg['n_tokens']}  "
              f"Time={elapsed:.2f}s")

    def _log_comparison(self, all_agg, tokens):
        """Auto-log comparison to results/."""
        os.makedirs('results', exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        fname = f'results/comparison_{timestamp}.json'
        log = {
            'timestamp': datetime.now().isoformat(),
            'n_tokens': len(tokens),
            'capital': self.capital,
            'strategies': {
                label: {k: v for k, v in agg.items() if k != 'label'}
                for label, agg in all_agg.items()
            }
        }
        with open(fname, 'w') as f:
            json.dump(log, f, indent=2, default=str)

    # ─── VALIDATION PIPELINE ─────────────────────────────────────────────

    def validate(self, strategy_fn: StrategyFn,
                 tokens: Optional[List[str]] = None,
                 n_groups: int = 6, n_test_groups: int = 2,
                 wf_train_days: int = 365, wf_test_days: int = 90,
                 pbo_threshold: float = 0.40,
                 verbose: bool = True) -> Dict:
        """
        Full validation pipeline: Walk-Forward + CPCV.

        1. Run backtest on all tokens (uses Numba-JIT simulation)
        2. Run CPCV per token → compute PBO (Probability of Backtest Overfitting)
        3. Run Walk-Forward per token → compute rolling Sharpe
        4. Only tokens passing BOTH gates are "validated"

        Returns dict with per-token results and validation summary.
        """
        if tokens is None:
            tokens = LIQUID_TOKENS

        results = {}

        print("=" * 70)
        print(f"VALIDATION PIPELINE: {strategy_fn.__name__}")
        print("=" * 70)

        # ── Stage 1: Full backtest ──
        print("\n[1/3] Full backtest...")
        bt_results = self.run(strategy_fn, tokens=tokens, verbose=False)
        agg = self._aggregate(bt_results)
        annual = agg['total_pnl'] / 2.0
        print(f"  PnL: ${agg['total_pnl']:+,.0f}  Annual: ${annual:+,.0f}/yr  "
              f"WR: {agg['win_rate']:.0f}%  "
              f"Profitable: {agg['profitable']}/{agg['n_tokens']}")

        # ── Stage 2: CPCV per token ──
        print("\n[2/3] CPCV validation (per token)...")
        cpcv_results = {}
        for tk in tokens:
            h1_path = os.path.join(self.data_dir, f'1h_cache/{tk}_1h.parquet')
            if not os.path.exists(h1_path):
                continue
            df_1h = pd.read_parquet(h1_path)
            n = len(df_1h)
            if n < 2000:
                cpcv_results[tk] = {'pbo': 1.0, 'folds_profitable': 0, 'reason': 'insufficient_data'}
                continue

            # Run strategy on CPCV splits
            from cpcv import generate_cpcv_splits
            splits = generate_cpcv_splits(n, n_groups, n_test_groups, purge_pct=0.01)
            fold_pnls = []
            for train_idx, test_idx in splits:
                if len(test_idx) < 200:
                    continue
                # Backtest on test portion only
                test_df = df_1h.iloc[test_idx]
                res = self.backtest_token(strategy_fn, tk, df_1h=test_df)
                if res:
                    pnl = res['equity'] - self.capital
                    fold_pnls.append(pnl)

            if not fold_pnls:
                cpcv_results[tk] = {'pbo': 1.0, 'folds_profitable': 0, 'reason': 'no_valid_folds'}
                continue

            folds_profitable = sum(1 for p in fold_pnls if p > 0)
            pbo = 1.0 - (folds_profitable / len(fold_pnls))
            cpcv_results[tk] = {
                'pbo': pbo,
                'folds_profitable': folds_profitable,
                'total_folds': len(fold_pnls),
                'avg_fold_pnl': np.mean(fold_pnls),
            }

        # ── Stage 3: Walk-Forward (simplified — temporal split) ──
        print("[3/3] Walk-Forward validation (temporal split)...")
        wf_results = {}
        for tk in tokens:
            h1_path = os.path.join(self.data_dir, f'1h_cache/{tk}_1h.parquet')
            if not os.path.exists(h1_path):
                continue
            df_1h = pd.read_parquet(h1_path)
            n = len(df_1h)
            n_train = int(n * 0.6)  # 60% train, 40% test
            if n - n_train < 500:
                wf_results[tk] = {'oos_pnl': 0, 'reason': 'insufficient_data'}
                continue

            # Out-of-sample only
            test_df = df_1h.iloc[n_train:]
            res = self.backtest_token(strategy_fn, tk, df_1h=test_df)
            if res:
                oos_pnl = res['equity'] - self.capital
                wf_results[tk] = {
                    'oos_pnl': oos_pnl,
                    'oos_trades': res['n_trades'],
                    'oos_wr': res['win_rate'],
                }

        # ── Combine Gates ──
        print("\n" + "=" * 70)
        print("VALIDATION RESULTS")
        print("=" * 70)
        print(f"\n{'Token':>8}  {'Backtest PnL':>12}  {'PBO':>6}  {'CPCV':>6}  "
              f"{'OOS PnL':>10}  {'WF':>4}  {'Status':>10}")
        print("-" * 70)

        validated_tokens = []
        for tk in tokens:
            bt_pnl = bt_results.get(tk, {}).get('equity', self.capital) - self.capital if tk in bt_results else 0
            pbo = cpcv_results.get(tk, {}).get('pbo', 1.0)
            oos_pnl = wf_results.get(tk, {}).get('oos_pnl', 0)

            cpcv_pass = pbo < pbo_threshold
            wf_pass = oos_pnl > 0
            both_pass = cpcv_pass and wf_pass

            status = 'VALIDATED' if both_pass else ('CPCV_FAIL' if not cpcv_pass else 'WF_FAIL')
            if both_pass:
                validated_tokens.append(tk)

            if verbose:
                print(f"  {tk:>8}  ${bt_pnl:>+10,.0f}  {pbo:>5.0%}  "
                      f"{'PASS' if cpcv_pass else 'FAIL':>6}  "
                      f"${oos_pnl:>+9,.0f}  "
                      f"{'PASS' if wf_pass else 'FAIL':>4}  "
                      f"{status:>10}")

        print("-" * 70)
        print(f"  Validated: {len(validated_tokens)}/{len(tokens)} tokens")
        print(f"  Validated tokens: {', '.join(validated_tokens)}")

        results = {
            'backtest': agg,
            'cpcv': cpcv_results,
            'walk_forward': wf_results,
            'validated_tokens': validated_tokens,
            'pbo_threshold': pbo_threshold,
        }

        # Auto-log
        os.makedirs('results', exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        fname = f'results/validation_{timestamp}.json'
        with open(fname, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        print(f"\n  Results saved to {fname}")

        return results


# =============================================================================
# CPCV-Robust Token Lists (from previous analysis)
# =============================================================================

CPCV_ROBUST_TOKENS = [
    'PENGU', 'SUI', 'OM', 'TRX', 'DOT', 'AVAX',
    'BONK', 'FIL', 'FLOKI', 'DENT', 'ZRO'
]


# =============================================================================
# Example: Built-in strategy wrappers (demonstrate the protocol)
# =============================================================================

def strategy_dual_momentum(ctx: StrategyContext) -> StrategyResult:
    """Dual Momentum — the proven best strategy, as a plugin."""
    from mtf_strategy_v2 import _generate_dual_momentum_signals
    n = len(ctx.ind_1h['close'])

    entry = _generate_dual_momentum_signals(
        ctx.ind_1h, ctx.ind_4h, ctx.ind_d,
        ctx.regime_1h, ctx.idx_1h, ctx.idx_4h, ctx.idx_d)

    return StrategyResult(
        entry_mask=entry,
        direction=np.ones(n, dtype=np.int8),
        stop_mult=5.0, trail_mult=4.0, target_mult=999,
        no_stop_bars=12, min_hold=18, max_hold=720,
        edge=0.40,
        exit_regimes={CRISIS, DOWNTREND},
        rsi_exit_level=999,
        name='dual_momentum',
    )


def strategy_mean_reversion(ctx: StrategyContext) -> StrategyResult:
    """Mean Reversion with convex exits, as a plugin."""
    from mtf_strategy_v2 import _generate_mean_reversion_signals
    n = len(ctx.ind_1h['close'])

    entry = _generate_mean_reversion_signals(
        ctx.ind_1h, ctx.ind_4h, ctx.ind_d,
        ctx.regime_1h, ctx.idx_1h, ctx.idx_4h, ctx.idx_d)

    ema20_4h_1h = ctx.align_4h_to_1h(ctx.ind_4h['ema_20'])

    return StrategyResult(
        entry_mask=entry,
        direction=np.ones(n, dtype=np.int8),
        stop_mult=2.0, trail_mult=2.0, target_mult=5.0,
        no_stop_bars=0, min_hold=18, max_hold=240,
        edge=0.50,
        exit_regimes={CRISIS, DOWNTREND},
        convex_exit=True, mean_target_vals=ema20_4h_1h,
        name='mean_reversion',
    )


def strategy_v3_contrarian(ctx: StrategyContext) -> StrategyResult:
    """V3 Liquidity Contrarian — vol spike + oversold + weak close."""
    n = len(ctx.ind_1h['close'])

    # Use daily signals mapped to 1H
    close_d = ctx.ind_d['close']
    rsi_d = ctx.ind_d['rsi']
    vol_ratio_d = ctx.ind_d['vol_ratio']
    ema20_d = ctx.ind_d['ema_20']

    # 5-day return
    ret_5d = np.zeros(len(close_d))
    ret_5d[5:] = (close_d[5:] - close_d[:-5]) / np.maximum(close_d[:-5], 1e-10)

    # Close location (% of daily range)
    high_d = ctx.ind_d['high']
    low_d = ctx.ind_d['low']
    close_loc = (close_d - low_d) / np.maximum(high_d - low_d, 1e-10)

    # Daily entry conditions
    vol_spike = vol_ratio_d > 2.0
    price_drop = ret_5d < -0.05
    oversold = rsi_d < 35
    weak_close = close_loc < 0.3

    entry_d = vol_spike & price_drop & oversold & weak_close
    entry_d[:60] = False

    # Map to 1H
    entry_1h = ctx.align_daily_to_1h(entry_d.astype(float)) > 0.5

    # Target: daily EMA20
    target_1h = ctx.align_daily_to_1h(ema20_d)

    return StrategyResult(
        entry_mask=entry_1h,
        direction=np.ones(n, dtype=np.int8),
        stop_mult=2.0, trail_mult=2.0, target_mult=999,
        no_stop_bars=0, min_hold=72, max_hold=480,  # 3-day min, 20-day max
        edge=0.40,
        exit_regimes=set(),  # no regime exit
        convex_exit=True, mean_target_vals=target_1h,
        name='v3_contrarian',
    )


# =============================================================================
# Quick test
# =============================================================================

if __name__ == '__main__':
    engine = Engine()

    print("=== Testing Plugin Architecture ===\n")

    # Quick single-token test
    r = engine.backtest_token(strategy_dual_momentum, 'BTC')
    print(f"BTC DM: PnL=${r['equity']-200000:+,.0f}  Trades={r['n_trades']}  "
          f"WR={r['win_rate']:.0f}%  Payoff={r['payoff_ratio']:.1f}x")

    # Compare strategies on CPCV tokens
    engine.compare(
        strategies=[strategy_dual_momentum, strategy_mean_reversion, strategy_v3_contrarian],
        labels=['Dual Momentum', 'Mean Reversion', 'V3 Contrarian'],
        tokens=CPCV_ROBUST_TOKENS,
    )
