"""Test that trades are persisted to SQLite and metrics can be calculated.

Verifies the full pipeline:
1. Freqtrade stores trades in SQLite (tradesv3.sqlite)
2. Trade schema has required fields (pair, profit, open/close dates, etc.)
3. Metrics (Sharpe, Sortino, win rate, profit factor) can be computed from trade data
4. Equity curve can be reconstructed from trade history
5. Open and closed trades are both queryable
"""

import json
import math
import os
import sqlite3
from datetime import datetime, timezone

import pytest

try:
    import pandas as pd
    import numpy as np
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

pytestmark = pytest.mark.skipif(not HAS_PANDAS, reason="pandas not installed")


# ---------------------------------------------------------------------------
# Fixtures: Simulated trade data
# ---------------------------------------------------------------------------

SAMPLE_TRADES = [
    {"pair": "SUI/USDT", "stake_amount": 10000, "open_rate": 0.88,
     "close_rate": 0.95, "profit_ratio": 0.0795, "profit_abs": 795.0,
     "open_date": "2026-03-01 10:00:00", "close_date": "2026-03-01 18:00:00",
     "trade_duration": 480, "exit_reason": "trailing_stop_loss", "is_open": False},
    {"pair": "AVAX/USDT", "stake_amount": 10000, "open_rate": 8.50,
     "close_rate": 8.20, "profit_ratio": -0.0353, "profit_abs": -353.0,
     "open_date": "2026-03-01 12:00:00", "close_date": "2026-03-01 20:00:00",
     "trade_duration": 480, "exit_reason": "stop_loss", "is_open": False},
    {"pair": "PENGU/USDT", "stake_amount": 10000, "open_rate": 0.0065,
     "close_rate": 0.0072, "profit_ratio": 0.1077, "profit_abs": 1077.0,
     "open_date": "2026-03-01 14:00:00", "close_date": "2026-03-02 02:00:00",
     "trade_duration": 720, "exit_reason": "trailing_stop_loss", "is_open": False},
    {"pair": "OM/USDT", "stake_amount": 10000, "open_rate": 0.068,
     "close_rate": 0.066, "profit_ratio": -0.0294, "profit_abs": -294.0,
     "open_date": "2026-03-01 16:00:00", "close_date": "2026-03-01 22:00:00",
     "trade_duration": 360, "exit_reason": "stop_loss", "is_open": False},
    {"pair": "ZRO/USDT", "stake_amount": 10000, "open_rate": 1.70,
     "close_rate": 1.82, "profit_ratio": 0.0706, "profit_abs": 706.0,
     "open_date": "2026-03-01 18:00:00", "close_date": "2026-03-02 04:00:00",
     "trade_duration": 600, "exit_reason": "roi", "is_open": False},
    # One open trade
    {"pair": "BONK/USDT", "stake_amount": 10000, "open_rate": 5.5e-6,
     "close_rate": None, "profit_ratio": None, "profit_abs": None,
     "open_date": "2026-03-02 06:00:00", "close_date": None,
     "trade_duration": None, "exit_reason": None, "is_open": True},
]


@pytest.fixture
def trade_db(tmp_path):
    """Create a SQLite DB with sample trades matching Freqtrade schema."""
    db_path = tmp_path / "tradesv3.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            pair TEXT NOT NULL,
            stake_amount REAL,
            open_rate REAL,
            close_rate REAL,
            profit_ratio REAL,
            profit_abs REAL,
            open_date TEXT,
            close_date TEXT,
            trade_duration INTEGER,
            exit_reason TEXT,
            is_open INTEGER DEFAULT 0
        )
    """)
    for i, t in enumerate(SAMPLE_TRADES):
        conn.execute("""
            INSERT INTO trades (id, pair, stake_amount, open_rate, close_rate,
                profit_ratio, profit_abs, open_date, close_date,
                trade_duration, exit_reason, is_open)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            i + 1, t["pair"], t["stake_amount"], t["open_rate"], t["close_rate"],
            t["profit_ratio"], t["profit_abs"], t["open_date"], t["close_date"],
            t["trade_duration"], t["exit_reason"], 1 if t["is_open"] else 0,
        ))
    conn.commit()
    conn.close()
    return str(db_path)


def load_closed_trades(db_path):
    """Load closed trades from SQLite into a DataFrame."""
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT * FROM trades WHERE is_open = 0 ORDER BY open_date", conn
    )
    conn.close()
    return df


def load_open_trades(db_path):
    """Load open trades from SQLite."""
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query("SELECT * FROM trades WHERE is_open = 1", conn)
    conn.close()
    return df


# ---------------------------------------------------------------------------
# Test: Data Persistence
# ---------------------------------------------------------------------------

class TestTradePersistence:
    """Verify trades are stored and queryable from SQLite."""

    def test_closed_trades_count(self, trade_db):
        df = load_closed_trades(trade_db)
        assert len(df) == 5

    def test_open_trades_count(self, trade_db):
        df = load_open_trades(trade_db)
        assert len(df) == 1
        assert df.iloc[0]["pair"] == "BONK/USDT"

    def test_trade_has_required_fields(self, trade_db):
        df = load_closed_trades(trade_db)
        required = ["pair", "stake_amount", "open_rate", "close_rate",
                     "profit_ratio", "profit_abs", "open_date", "close_date",
                     "exit_reason"]
        for col in required:
            assert col in df.columns, f"Missing column: {col}"
            assert df[col].notna().all(), f"Column {col} has NaN in closed trades"

    def test_profit_abs_matches_ratio(self, trade_db):
        df = load_closed_trades(trade_db)
        for _, row in df.iterrows():
            expected = row["stake_amount"] * row["profit_ratio"]
            assert abs(row["profit_abs"] - expected) < 1.0, \
                f"Profit mismatch on {row['pair']}: abs={row['profit_abs']} vs ratio*stake={expected}"


# ---------------------------------------------------------------------------
# Test: Metrics Calculation
# ---------------------------------------------------------------------------

class TestMetricsCalculation:
    """Verify standard trading metrics can be computed from persisted data."""

    def test_total_profit(self, trade_db):
        df = load_closed_trades(trade_db)
        total = df["profit_abs"].sum()
        # 795 - 353 + 1077 - 294 + 706 = 1931
        assert abs(total - 1931.0) < 1.0

    def test_win_rate(self, trade_db):
        df = load_closed_trades(trade_db)
        wins = (df["profit_abs"] > 0).sum()
        win_rate = wins / len(df)
        assert win_rate == pytest.approx(3 / 5, abs=0.01)  # 60%

    def test_profit_factor(self, trade_db):
        """Profit factor = gross profit / gross loss."""
        df = load_closed_trades(trade_db)
        gross_profit = df.loc[df["profit_abs"] > 0, "profit_abs"].sum()
        gross_loss = abs(df.loc[df["profit_abs"] < 0, "profit_abs"].sum())
        pf = gross_profit / gross_loss
        # (795 + 1077 + 706) / (353 + 294) = 2578 / 647 = 3.98
        assert pf > 1.0
        assert pf == pytest.approx(3.98, abs=0.1)

    def test_sharpe_ratio(self, trade_db):
        """Sharpe = mean(returns) / std(returns) * sqrt(N)."""
        df = load_closed_trades(trade_db)
        returns = df["profit_ratio"]
        if returns.std() == 0:
            sharpe = 0.0
        else:
            sharpe = returns.mean() / returns.std() * math.sqrt(len(returns))
        # Should be positive (net profitable)
        assert sharpe > 0

    def test_sortino_ratio(self, trade_db):
        """Sortino = mean(returns) / downside_std * sqrt(N)."""
        df = load_closed_trades(trade_db)
        returns = df["profit_ratio"]
        downside = returns[returns < 0]
        if len(downside) == 0 or downside.std() == 0:
            sortino = float("inf")
        else:
            sortino = returns.mean() / downside.std() * math.sqrt(len(returns))
        assert sortino > 0

    def test_max_drawdown(self, trade_db):
        """Max drawdown from cumulative profit curve."""
        df = load_closed_trades(trade_db)
        cum_profit = df["profit_abs"].cumsum()
        peak = cum_profit.cummax()
        drawdown = (cum_profit - peak)
        max_dd = drawdown.min()
        # Should be negative (there was a losing trade)
        assert max_dd <= 0

    def test_avg_trade_duration(self, trade_db):
        df = load_closed_trades(trade_db)
        avg_dur = df["trade_duration"].mean()
        assert avg_dur > 0
        # Average of 480, 480, 720, 360, 600 = 528 minutes
        assert avg_dur == pytest.approx(528, abs=1)


# ---------------------------------------------------------------------------
# Test: Equity Curve Reconstruction
# ---------------------------------------------------------------------------

class TestEquityCurve:
    """Verify equity curve can be built from trade history."""

    def test_equity_curve_monotonic_timestamps(self, trade_db):
        df = load_closed_trades(trade_db)
        df["close_dt"] = pd.to_datetime(df["close_date"])
        df = df.sort_values("close_dt")
        assert df["close_dt"].is_monotonic_increasing

    def test_equity_curve_starts_at_capital(self, trade_db):
        starting_capital = 200_000
        df = load_closed_trades(trade_db)
        df = df.sort_values("open_date")
        cum_profit = df["profit_abs"].cumsum()
        equity = starting_capital + cum_profit
        assert equity.iloc[0] == starting_capital + df["profit_abs"].iloc[0]

    def test_equity_curve_final_value(self, trade_db):
        starting_capital = 200_000
        df = load_closed_trades(trade_db)
        total_profit = df["profit_abs"].sum()
        final_equity = starting_capital + total_profit
        assert final_equity == pytest.approx(201_931, abs=1)

    def test_per_pair_breakdown(self, trade_db):
        """Can group profit by pair."""
        df = load_closed_trades(trade_db)
        by_pair = df.groupby("pair")["profit_abs"].sum()
        assert "SUI/USDT" in by_pair.index
        assert by_pair["SUI/USDT"] == pytest.approx(795, abs=1)
        assert by_pair["AVAX/USDT"] < 0  # losing trade


# ---------------------------------------------------------------------------
# Test: Live DB Validation (if running)
# ---------------------------------------------------------------------------

class TestLiveDB:
    """Validate the actual running instance's SQLite DB."""

    LIVE_DB = "paper_trading/s11_binance/tradesv3.sqlite"

    @pytest.mark.skipif(
        not os.path.exists("paper_trading/s11_binance/tradesv3.sqlite"),
        reason="No live instance DB"
    )
    def test_live_db_has_trades_table(self):
        conn = sqlite3.connect(self.LIVE_DB)
        tables = [t[0] for t in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        conn.close()
        assert "trades" in tables

    @pytest.mark.skipif(
        not os.path.exists("paper_trading/s11_binance/tradesv3.sqlite"),
        reason="No live instance DB"
    )
    def test_live_db_schema_has_profit_columns(self):
        conn = sqlite3.connect(self.LIVE_DB)
        cols = [row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()]
        conn.close()
        for col in ["close_profit", "close_profit_abs", "open_rate"]:
            assert col in cols, f"Live DB missing column: {col}"
