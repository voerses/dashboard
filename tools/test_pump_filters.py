"""A/B test: pump-and-dump entry filters vs baseline."""
import sys, os, json, time
import numpy as np
sys.path.insert(0, "/workspace/crypto_backtest")
os.chdir("/workspace/crypto_backtest")

from v4.config import PortfolioConfig, StrategySpec
from v4.signals import precompute_strategy_signals, discover_tokens
from v4.simulator import simulate_portfolio
from v4.report import compute_portfolio_metrics

class NpEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.integer,)):
            return int(obj)
        return super().default(obj)

CAPITAL = 200_000

PORTFOLIOS = {
    "s60": {
        "strategies": [("s60", 1.0, "perp", 15, "per_token")],
        "max_portfolio_positions": 15,
    },
    "4-edge": {
        "strategies": [
            ("s56", 0.25, "perp", 10, "per_token"),
            ("s57", 0.25, "combined", 10, "per_token"),
            ("s63", 0.25, "perp", 10, "per_token"),
            ("s65", 0.25, "perp", 10, "per_token"),
        ],
        "max_portfolio_positions": 40,
    },
}

CONFIGS = {
    "BASELINE": dict(circuit_breaker_r=0, pump_filter_range_threshold=0, pump_filter_adv_floor=0, pump_filter_funding_zscore=0),
    "CB_ONLY": dict(circuit_breaker_r=4.0, pump_filter_range_threshold=0, pump_filter_adv_floor=0, pump_filter_funding_zscore=0),
    "L1_RANGE": dict(circuit_breaker_r=0, pump_filter_range_threshold=4.0, pump_filter_adv_floor=0, pump_filter_funding_zscore=0),
    "L3_FUNDING": dict(circuit_breaker_r=0, pump_filter_range_threshold=0, pump_filter_adv_floor=0, pump_filter_funding_zscore=3.0),
    "ALL_FILTERS": dict(circuit_breaker_r=4.0, pump_filter_range_threshold=4.0, pump_filter_adv_floor=5_000_000, pump_filter_funding_zscore=3.0, pump_filter_adv_penalty=0.5),
}

portfolio_name = sys.argv[1]
config_name = sys.argv[2]

pdef = PORTFOLIOS[portfolio_name]
cfg_overrides = CONFIGS[config_name]

strat_list = pdef["strategies"]
specs = {}
for sid, weight, market, max_pos, stype in strat_list:
    specs[sid] = StrategySpec(strategy_id=sid, weight=weight, max_positions=max_pos, market=market, strategy_type=stype)

config = PortfolioConfig(
    capital=CAPITAL,
    max_portfolio_positions=pdef["max_portfolio_positions"],
    concentration_limit=1.0,
    adv_cap_pct=0.05,
    seed=42,
)
for k, v in cfg_overrides.items():
    setattr(config, k, v)

t0 = time.time()
all_signals = {}
for sid, spec in specs.items():
    tokens = discover_tokens(spec.market)
    all_signals[sid] = precompute_strategy_signals(spec, tokens, config, 12)

state = simulate_portfolio(all_signals, specs, config)
m, extra, eq_daily = compute_portfolio_metrics(state, CAPITAL)

final_eq = extra["final_equity"]
pnl = final_eq - CAPITAL
pct_ret = (final_eq / CAPITAL - 1) * 100
elapsed = time.time() - t0

rej = state.rejections.to_dict()

result = {
    "portfolio": portfolio_name,
    "config": config_name,
    "pct_return": round(float(pct_ret), 1),
    "dollar_pnl": int(pnl),
    "max_dd_pct": round(float(m.max_drawdown_pct), 2),
    "sharpe": round(float(m.sharpe_ratio), 2),
    "calmar": round(float(m.calmar_ratio), 2),
    "trades": int(m.total_trades),
    "pump_range_blocked": rej.get("pump_range", 0),
    "pump_funding_blocked": rej.get("pump_funding", 0),
    "total_rejections": rej.get("total", 0),
    "elapsed_s": round(elapsed, 1),
}
print(json.dumps(result, cls=NpEncoder))
