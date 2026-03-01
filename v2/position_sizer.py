"""
Professional Position Sizing Engine

Implements:
1. Fractional Kelly Criterion (quarter-Kelly default)
2. ATR-based volatility scaling
3. Regime-conditional sizing
4. Drawdown-based position reduction
5. Anti-martingale scaling
6. Risk parity across assets
"""

import numpy as np
from regime_detector import Regime


class PositionSizer:
    """
    Regime-aware position sizing engine.

    Combines Kelly, volatility scaling, drawdown control, and anti-martingale.
    """

    def __init__(self, initial_capital=200000, base_risk_pct=0.02,
                 kelly_fraction=0.25, max_position_pct=0.60,
                 drawdown_halt_pct=0.25):
        self.initial_capital = initial_capital
        self.base_risk_pct = base_risk_pct
        self.kelly_fraction = kelly_fraction
        self.max_position_pct = max_position_pct
        self.drawdown_halt_pct = drawdown_halt_pct

        # Tracking state
        self.peak_equity = initial_capital
        self.consecutive_wins = 0
        self.consecutive_losses = 0
        self.recent_trades = []  # (pnl_pct, win/loss)

    def calculate_size(self, equity, regime, atr_pct, signal_strength=1.0):
        """
        Calculate position size as fraction of equity.

        Args:
            equity: Current equity
            regime: Regime enum value
            atr_pct: ATR as percentage of price (e.g., 0.03 = 3%)
            signal_strength: 0-1, how strong the signal is

        Returns:
            position_size: fraction of equity to deploy (0 to max_position_pct)
        """
        if equity <= 0:
            return 0.0

        # Update peak for drawdown tracking
        self.peak_equity = max(self.peak_equity, equity)
        current_dd = (self.peak_equity - equity) / self.peak_equity

        # 1. Drawdown control (override everything)
        dd_multiplier = self._drawdown_multiplier(current_dd)
        if dd_multiplier <= 0:
            return 0.0

        # 2. Base size from Kelly
        kelly_size = self._kelly_size()

        # 3. Volatility scaling
        vol_multiplier = self._volatility_multiplier(atr_pct)

        # 4. Regime adjustment
        regime_multiplier = self._regime_multiplier(regime)

        # 5. Anti-martingale adjustment
        streak_multiplier = self._streak_multiplier()

        # 6. Signal strength
        signal_mult = max(0.3, min(1.0, signal_strength))

        # Combine
        raw_size = (kelly_size * vol_multiplier * regime_multiplier *
                   dd_multiplier * streak_multiplier * signal_mult)

        # Clamp
        return np.clip(raw_size, 0, self.max_position_pct)

    def _kelly_size(self):
        """Quarter-Kelly based on recent trade history."""
        if len(self.recent_trades) < 10:
            return 0.35  # Default 35% position until enough history

        wins = [t for t in self.recent_trades if t > 0]
        losses = [t for t in self.recent_trades if t <= 0]

        if not wins or not losses:
            return self.base_risk_pct * 10

        win_rate = len(wins) / len(self.recent_trades)
        avg_win = np.mean(wins)
        avg_loss = abs(np.mean(losses))

        if avg_loss < 1e-10:
            return self.max_position_pct

        b = avg_win / avg_loss  # win/loss ratio
        kelly = (win_rate * b - (1 - win_rate)) / b

        # Fractional Kelly
        kelly *= self.kelly_fraction
        return np.clip(kelly, self.base_risk_pct, self.max_position_pct)

    def _volatility_multiplier(self, atr_pct):
        """
        Scale inversely to volatility.
        Target: ~3% daily ATR gets 1.0x.
        Higher vol → smaller positions.
        """
        target_atr = 0.03  # 3% daily ATR baseline
        if atr_pct <= 0:
            return 1.0
        multiplier = target_atr / (atr_pct + 1e-10)
        return np.clip(multiplier, 0.3, 2.0)

    def _regime_multiplier(self, regime):
        """Adjust position size by regime."""
        multipliers = {
            Regime.TRENDING_UP: 1.2,         # Overweight in trends
            Regime.TRENDING_DOWN: 0.5,        # Half size
            Regime.MEAN_REVERTING: 0.8,       # 80%
            Regime.HIGH_VOL_CHAOS: 0.3,       # Reduced
            Regime.LOW_VOL_ACCUMULATION: 0.7, # Moderate
        }
        return multipliers.get(regime, 0.5)

    def _drawdown_multiplier(self, current_dd):
        """Tiered drawdown reduction."""
        if current_dd >= self.drawdown_halt_pct:
            return 0.0  # Stop trading
        elif current_dd >= 0.15:
            return 0.25
        elif current_dd >= 0.10:
            return 0.50
        elif current_dd >= 0.05:
            return 0.75
        return 1.0

    def _streak_multiplier(self):
        """Anti-martingale: increase after wins, decrease after losses."""
        if self.consecutive_wins >= 3:
            return min(1.5, 1.0 + 0.15 * self.consecutive_wins)  # Cap at 1.5x
        elif self.consecutive_losses >= 2:
            return max(0.5, 1.0 - 0.15 * self.consecutive_losses)  # Floor at 0.5x
        return 1.0

    def record_trade(self, pnl_pct):
        """Record trade result for Kelly and anti-martingale tracking."""
        self.recent_trades.append(pnl_pct)
        if len(self.recent_trades) > 100:
            self.recent_trades = self.recent_trades[-100:]

        if pnl_pct > 0:
            self.consecutive_wins += 1
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
            self.consecutive_wins = 0

    def reset(self):
        """Reset for new backtest run."""
        self.peak_equity = self.initial_capital
        self.consecutive_wins = 0
        self.consecutive_losses = 0
        self.recent_trades = []


class ExitManager:
    """
    Professional exit management system.

    Combines:
    1. ATR trailing stop (regime-adaptive multiplier)
    2. Chandelier exit
    3. Partial profit taking (3 tranches)
    4. Time-based exit
    5. Regime change forced exit
    """

    def __init__(self, base_atr_mult=3.5, max_hold_days=30):
        self.base_atr_mult = base_atr_mult
        self.max_hold_days = max_hold_days

    def get_stop_distance(self, atr, regime):
        """Get ATR-based stop distance adjusted for regime."""
        regime_mults = {
            Regime.TRENDING_UP: 1.0,           # Normal
            Regime.TRENDING_DOWN: 0.8,          # Tighter (we shouldn't be long anyway)
            Regime.MEAN_REVERTING: 0.6,          # Tight stops in ranges
            Regime.HIGH_VOL_CHAOS: 1.5,          # Wide stops or don't trade
            Regime.LOW_VOL_ACCUMULATION: 0.7,    # Moderate
        }
        mult = regime_mults.get(regime, 1.0)
        return atr * self.base_atr_mult * mult

    def should_exit(self, entry_price, current_price, highest_price,
                    direction, atr, regime, bars_held, entry_regime):
        """
        Check all exit conditions.

        Returns: (should_exit: bool, reason: str, exit_fraction: float)
        """
        stop_dist = self.get_stop_distance(atr, regime)

        # 1. Trailing stop
        if direction == 'long':
            trailing_stop = highest_price - stop_dist
            if current_price <= trailing_stop:
                return True, 'trailing_stop', 1.0
        elif direction == 'short':
            trailing_stop = highest_price + stop_dist  # highest_price is actually lowest for shorts
            if current_price >= trailing_stop:
                return True, 'trailing_stop', 1.0

        # 2. Time-based exit
        if bars_held >= self.max_hold_days:
            return True, 'time_limit', 1.0

        # 3. Regime change forced exit
        if regime == Regime.HIGH_VOL_CHAOS and entry_regime != Regime.HIGH_VOL_CHAOS:
            return True, 'regime_change', 1.0

        # 4. Partial profit taking
        if direction == 'long':
            pnl_pct = (current_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - current_price) / entry_price

        r_multiple = pnl_pct / (stop_dist / entry_price) if stop_dist > 0 else 0

        if r_multiple >= 3.0:
            return True, 'profit_target_3R', 0.34  # Exit last third
        elif r_multiple >= 2.0:
            return False, 'partial_2R', 0.33  # Signal partial exit
        elif r_multiple >= 1.0:
            return False, 'partial_1R', 0.33

        return False, 'hold', 0.0
