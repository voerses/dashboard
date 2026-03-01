"""
Strategy Registry — all strategies registered here for the test suite.
"""

STRATEGY_REGISTRY = {
    's01_dual_momentum': {
        'name': 'Dual Momentum (MTF Fat-Tail)',
        'status': 'PROFITABLE',
        'module': 'strategies.s01_dual_momentum',
        'annual_pnl': '+$10,720/yr on CPCV tokens',
    },
    's02_mean_reversion': {
        'name': 'Mean Reversion Convex Exit',
        'status': 'MARGINAL',
        'module': 'strategies.s02_mean_reversion',
        'annual_pnl': '~breakeven, 21 trades only',
    },
    's03_vol_breakout': {
        'name': 'Volatility Breakout (Squeeze)',
        'status': 'LOSING',
        'module': 'strategies.s03_vol_breakout',
        'annual_pnl': '-$1,400/yr, payoff < 1x',
    },
    's04_v3_contrarian': {
        'name': 'V3 Liquidity Contrarian',
        'status': 'PROFITABLE',
        'module': 'strategies.s04_v3_contrarian',
        'annual_pnl': '+$8,216/yr on all tokens',
    },
    's05_vpin_enhanced': {
        'name': 'VPIN-Enhanced DM+MR',
        'status': 'LOSING',
        'module': 'strategies.s05_vpin_enhanced',
        'annual_pnl': '-$1,882/yr (filter too aggressive)',
    },
    's06_v2_daily_momentum': {
        'name': 'V2 Daily Momentum (EMA cross)',
        'status': 'LOSING',
        'module': 'strategies.s06_v2_daily_momentum',
        'annual_pnl': '-$13,806/yr (too slow)',
    },
}
