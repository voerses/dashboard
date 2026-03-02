"""
Core backtesting engine with proper fee accounting, position sizing, and performance metrics.
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class Trade:
    entry_date: str
    exit_date: str
    direction: str  # 'long' or 'short'
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    pnl_pct: float
    fees: float

@dataclass
class BacktestResult:
    strategy_name: str
    asset: str
    initial_capital: float
    final_equity: float
    total_return_pct: float
    cagr: float
    sharpe: float
    sortino: float
    calmar: float
    max_drawdown: float
    max_drawdown_duration: int
    total_trades: int
    win_rate: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    avg_trade_duration: float
    total_fees: float
    equity_curve: pd.Series = field(default_factory=pd.Series)
    trades: List[Trade] = field(default_factory=list)
    monthly_returns: pd.Series = field(default_factory=pd.Series)

class BacktestEngine:
    def __init__(self, initial_capital=200000, fee_rate=0.001, slippage_bps=5):
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.slippage = slippage_bps / 10000

    def run(self, df, signals, strategy_name, asset):
        """
        Run backtest on signals DataFrame.
        signals must have columns: 'signal' (1=long, -1=short, 0=flat), 'position_size' (0-1 fraction)
        """
        equity = self.initial_capital
        position = 0  # number of units held
        entry_price = 0
        entry_date = None
        direction = None

        equity_curve = []
        trades = []
        total_fees = 0

        for i in range(len(df)):
            date = df.index[i]
            close = df['close'].iloc[i]
            sig = signals['signal'].iloc[i] if i < len(signals) else 0
            pos_size = signals['position_size'].iloc[i] if i < len(signals) else 0

            # Mark to market
            if position != 0 and i > 0:
                price_change = close - df['close'].iloc[i-1]
                if direction == 'long':
                    equity = equity + position * price_change
                elif direction == 'short':
                    equity = equity - position * price_change
                # Floor equity at 0 (can't lose more than you have)
                equity = max(equity, 0)

            # Check for signal change
            new_direction = 'long' if sig > 0 else ('short' if sig < 0 else None)

            if new_direction != direction:
                # Close existing position
                if position != 0:
                    exit_price = close * (1 - self.slippage if direction == 'long' else 1 + self.slippage)
                    fee = abs(position * exit_price) * self.fee_rate
                    total_fees += fee
                    equity -= fee

                    if direction == 'long':
                        pnl = position * (exit_price - entry_price)
                    else:
                        pnl = position * (entry_price - exit_price)

                    pnl_pct = pnl / (position * entry_price) if entry_price > 0 else 0

                    trades.append(Trade(
                        entry_date=str(entry_date), exit_date=str(date),
                        direction=direction, entry_price=entry_price,
                        exit_price=exit_price, size=position,
                        pnl=pnl, pnl_pct=pnl_pct, fees=fee
                    ))
                    position = 0

                # Open new position
                if new_direction is not None and pos_size > 0:
                    entry_price = close * (1 + self.slippage if new_direction == 'long' else 1 - self.slippage)
                    fee = abs(equity * pos_size) * self.fee_rate
                    total_fees += fee
                    equity -= fee
                    position = (equity * pos_size) / entry_price
                    entry_date = date
                    direction = new_direction
                else:
                    direction = new_direction

            equity_curve.append(equity)

        # Close any remaining position
        if position != 0:
            exit_price = df['close'].iloc[-1]
            if direction == 'long':
                pnl = position * (exit_price - entry_price)
            else:
                pnl = position * (entry_price - exit_price)
            pnl_pct = pnl / (position * entry_price) if entry_price > 0 else 0
            fee = abs(position * exit_price) * self.fee_rate
            total_fees += fee
            equity -= fee
            trades.append(Trade(
                entry_date=str(entry_date), exit_date=str(df.index[-1]),
                direction=direction, entry_price=entry_price,
                exit_price=exit_price, size=position,
                pnl=pnl, pnl_pct=pnl_pct, fees=fee
            ))

        equity_series = pd.Series(equity_curve, index=df.index[:len(equity_curve)])

        return self._compute_metrics(equity_series, trades, total_fees, strategy_name, asset)

    def _compute_metrics(self, equity_curve, trades, total_fees, strategy_name, asset):
        returns = equity_curve.pct_change().dropna()

        # Basic metrics
        total_return = (equity_curve.iloc[-1] / self.initial_capital - 1) * 100
        n_years = len(equity_curve) / 365.25
        cagr = ((equity_curve.iloc[-1] / self.initial_capital) ** (1/n_years) - 1) * 100 if n_years > 0 else 0

        # Sharpe (annualized)
        if returns.std() > 0:
            sharpe = (returns.mean() / returns.std()) * np.sqrt(365)
        else:
            sharpe = 0

        # Sortino
        downside = returns[returns < 0]
        if len(downside) > 0 and downside.std() > 0:
            sortino = (returns.mean() / downside.std()) * np.sqrt(365)
        else:
            sortino = 0

        # Max drawdown
        cummax = equity_curve.cummax()
        drawdown = (equity_curve - cummax) / cummax
        max_dd = drawdown.min() * 100

        # Max drawdown duration
        dd_duration = 0
        max_dd_duration = 0
        for i in range(len(drawdown)):
            if drawdown.iloc[i] < 0:
                dd_duration += 1
                max_dd_duration = max(max_dd_duration, dd_duration)
            else:
                dd_duration = 0

        # Calmar
        calmar = cagr / abs(max_dd) if max_dd != 0 else 0

        # Trade stats
        winning = [t for t in trades if t.pnl > 0]
        losing = [t for t in trades if t.pnl <= 0]
        win_rate = len(winning) / len(trades) * 100 if trades else 0

        gross_profit = sum(t.pnl for t in winning)
        gross_loss = abs(sum(t.pnl for t in losing))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        avg_win = np.mean([t.pnl for t in winning]) if winning else 0
        avg_loss = np.mean([t.pnl for t in losing]) if losing else 0

        # Monthly returns
        monthly = equity_curve.resample('ME').last().pct_change().dropna() * 100

        return BacktestResult(
            strategy_name=strategy_name, asset=asset,
            initial_capital=self.initial_capital,
            final_equity=equity_curve.iloc[-1],
            total_return_pct=total_return, cagr=cagr,
            sharpe=sharpe, sortino=sortino, calmar=calmar,
            max_drawdown=max_dd, max_drawdown_duration=max_dd_duration,
            total_trades=len(trades), win_rate=win_rate,
            profit_factor=profit_factor,
            avg_win=avg_win, avg_loss=avg_loss,
            avg_trade_duration=0,
            total_fees=total_fees,
            equity_curve=equity_curve, trades=trades,
            monthly_returns=monthly
        )

def buy_and_hold(df, initial_capital=200000):
    """Buy and hold benchmark."""
    signals = pd.DataFrame(index=df.index)
    signals['signal'] = 1
    signals['position_size'] = 1.0
    engine = BacktestEngine(initial_capital=initial_capital)
    return engine.run(df, signals, 'Buy & Hold', '')
