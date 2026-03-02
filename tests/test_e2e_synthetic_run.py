"""End-to-end synthetic paper trading test.

Simulates a full month of trading with realistic synthetic data:
1. Generates 720 hourly candles (30 days) for 9 tokens
2. Injects intentional 3%+ bursts to trigger S11 entries
3. Runs the actual CpcvSwingStrategy to produce signals
4. Simulates trade execution (entries, exits, stoploss)
5. Persists trades to SQLite in Freqtrade's real schema
6. Calls generate_run_report() and validates the output
"""

import json
import math
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

try:
    import pandas as pd
    import talib as ta
    from freqtrade.strategy import IStrategy
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

pytestmark = pytest.mark.skipif(not HAS_DEPS, reason="freqtrade/talib not installed")

# Strategy entry thresholds (must match CpcvSwingStrategy)
ENTRY_RET_THRESHOLD = 0.03
ENTRY_ADX_THRESHOLD = 20
ENTRY_VOL_RATIO_THRESHOLD = 1.0

EXIT_ADX_THRESHOLD = 15
EXIT_RSI_THRESHOLD = 70

STOPLOSS = -0.09
NO_STOP_BARS = 24
MIN_HOLD = 18
MAX_HOLD = 720

TOKENS = ["SUI", "PENGU", "OM", "TRX", "AVAX", "BONK", "FIL", "FLOKI", "ZRO"]
STAKE_CURRENCY = "USDT"
STARTING_CAPITAL = 200_000
STAKE_PER_TRADE = 10_000


# ---------------------------------------------------------------------------
# Synthetic data generation
# ---------------------------------------------------------------------------

def generate_month_candles(token: str, n=720, seed=None) -> pd.DataFrame:
    """Generate 720 hourly candles with realistic patterns.

    Includes:
    - Random walk with drift
    - 3-5 intentional burst candles (>3% return) scattered through the month
    - Volume patterns (spikes on bursts)
    - A weak-trend overbought period for exit testing
    """
    if seed is None:
        seed = hash(token) % (2**31)
    rng = np.random.RandomState(seed)

    # Base prices vary by token
    base_prices = {
        "SUI": 0.88, "PENGU": 0.0065, "OM": 0.068, "TRX": 0.12,
        "AVAX": 8.50, "BONK": 5.5e-6, "FIL": 3.20, "FLOKI": 0.00015,
        "ZRO": 1.70,
    }
    base = base_prices.get(token, 1.0)

    dates = pd.date_range("2026-02-01", periods=n, freq="1h")
    returns = rng.normal(0.0002, 0.008, n)  # slight upward drift

    # Inject 3-5 burst candles at random positions (after warmup)
    n_bursts = rng.randint(3, 6)
    burst_positions = rng.choice(range(60, n - 60), size=n_bursts, replace=False)
    for pos in burst_positions:
        returns[pos] = rng.uniform(0.035, 0.08)  # 3.5%-8% burst

    # Inject 1-2 crash candles for stoploss testing
    n_crashes = rng.randint(1, 3)
    crash_positions = rng.choice(range(100, n - 30), size=n_crashes, replace=False)
    for pos in crash_positions:
        returns[pos] = rng.uniform(-0.12, -0.06)

    close = base * np.exp(np.cumsum(returns))
    high = close * (1 + rng.uniform(0.001, 0.015, n))
    low = close * (1 - rng.uniform(0.001, 0.015, n))
    open_ = close * (1 + rng.normal(0, 0.003, n))

    # Volume: baseline with spikes on bursts
    volume = rng.uniform(5e5, 5e6, n)
    for pos in burst_positions:
        volume[pos] *= rng.uniform(2.5, 5.0)

    return pd.DataFrame({
        "date": dates,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


# ---------------------------------------------------------------------------
# Lightweight trade simulator
# ---------------------------------------------------------------------------

def simulate_trades(strategy, token: str, candles: pd.DataFrame) -> list:
    """Run the strategy on candles and simulate trade execution.

    Returns list of trade dicts in Freqtrade's schema.
    """
    pair = f"{token}/{STAKE_CURRENCY}"
    metadata = {"pair": pair}

    # Run strategy pipeline
    df = strategy.populate_indicators(candles.copy(), metadata)
    df = strategy.populate_entry_trend(df, metadata)
    df = strategy.populate_exit_trend(df, metadata)

    trades = []
    open_trade = None

    for i in range(len(df)):
        row = df.iloc[i]
        current_time = row["date"]
        current_price = row["close"]

        if open_trade is not None:
            # Check exit conditions
            hours_held = (current_time - open_trade["open_date"]).total_seconds() / 3600
            current_profit = (current_price - open_trade["open_rate"]) / open_trade["open_rate"]

            exit_reason = None

            # Max hold exit
            if hours_held >= MAX_HOLD:
                exit_reason = "max_hold_exit"

            # Min hold protection
            elif hours_held < MIN_HOLD:
                pass  # can't exit yet

            # Stoploss check (after no_stop_bars protection)
            elif hours_held >= NO_STOP_BARS and current_profit <= STOPLOSS:
                exit_reason = "stop_loss"

            # Trailing stop: if profit > 3%, trail at 1.5% below peak
            elif hours_held >= MIN_HOLD and current_profit > 0.03:
                # Track peak profit
                if current_profit > open_trade.get("peak_profit", 0):
                    open_trade["peak_profit"] = current_profit
                if (open_trade.get("peak_profit", 0) - current_profit) > 0.015:
                    exit_reason = "trailing_stop_loss"

            # Strategy exit signal
            elif hours_held >= MIN_HOLD and row.get("exit_long", 0) == 1:
                exit_reason = "exit_signal"

            if exit_reason:
                profit_ratio = (current_price - open_trade["open_rate"]) / open_trade["open_rate"]
                profit_abs = open_trade["stake_amount"] * profit_ratio
                trades.append({
                    "pair": pair,
                    "stake_amount": open_trade["stake_amount"],
                    "open_rate": open_trade["open_rate"],
                    "close_rate": current_price,
                    "close_profit": profit_ratio,
                    "close_profit_abs": profit_abs,
                    "open_date": open_trade["open_date"],
                    "close_date": current_time,
                    "exit_reason": exit_reason,
                    "is_open": False,
                    "strategy": "CpcvSwingStrategy",
                    "token": token,
                })
                open_trade = None

        # Check entry (only if no open trade for this token)
        if open_trade is None and row.get("enter_long", 0) == 1:
            open_trade = {
                "pair": pair,
                "stake_amount": STAKE_PER_TRADE,
                "open_rate": current_price,
                "open_date": current_time,
                "peak_profit": 0,
            }

    # If trade still open at end, record it
    if open_trade is not None:
        last_price = df.iloc[-1]["close"]
        trades.append({
            "pair": pair,
            "stake_amount": open_trade["stake_amount"],
            "open_rate": open_trade["open_rate"],
            "close_rate": None,
            "close_profit": None,
            "close_profit_abs": None,
            "open_date": open_trade["open_date"],
            "close_date": None,
            "exit_reason": None,
            "is_open": True,
            "strategy": "CpcvSwingStrategy",
            "token": token,
        })

    return trades


# ---------------------------------------------------------------------------
# DB writer (Freqtrade-compatible schema)
# ---------------------------------------------------------------------------

def create_freqtrade_db(db_path: str, trades: list):
    """Write trades to SQLite using Freqtrade's real schema."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY,
            exchange VARCHAR(25) DEFAULT 'binance',
            pair VARCHAR(25) NOT NULL,
            base_currency VARCHAR(25),
            stake_currency VARCHAR(25) DEFAULT 'USDT',
            is_open BOOLEAN DEFAULT 0,
            fee_open FLOAT DEFAULT 0.001,
            fee_open_cost FLOAT,
            fee_open_currency VARCHAR(25),
            fee_close FLOAT DEFAULT 0.001,
            fee_close_cost FLOAT,
            fee_close_currency VARCHAR(25),
            open_rate FLOAT,
            open_rate_requested FLOAT,
            open_trade_value FLOAT,
            close_rate FLOAT,
            close_rate_requested FLOAT,
            realized_profit FLOAT DEFAULT 0,
            close_profit FLOAT,
            close_profit_abs FLOAT,
            stake_amount FLOAT,
            max_stake_amount FLOAT,
            amount FLOAT,
            amount_requested FLOAT,
            open_date DATETIME,
            close_date DATETIME,
            stop_loss FLOAT,
            stop_loss_pct FLOAT,
            initial_stop_loss FLOAT,
            initial_stop_loss_pct FLOAT,
            is_stop_loss_trailing BOOLEAN DEFAULT 1,
            max_rate FLOAT,
            min_rate FLOAT,
            exit_reason VARCHAR(255),
            exit_order_status VARCHAR(100),
            strategy VARCHAR(100),
            enter_tag VARCHAR(255),
            timeframe INTEGER DEFAULT 60,
            trading_mode VARCHAR(7) DEFAULT 'spot',
            amount_precision FLOAT,
            price_precision FLOAT,
            precision_mode INTEGER,
            precision_mode_price INTEGER,
            contract_size FLOAT DEFAULT 1.0,
            leverage FLOAT DEFAULT 1.0,
            is_short BOOLEAN DEFAULT 0,
            liquidation_price FLOAT,
            interest_rate FLOAT DEFAULT 0,
            funding_fees FLOAT DEFAULT 0,
            funding_fee_running FLOAT,
            record_version INTEGER DEFAULT 2
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS KeyValueStore (
            id INTEGER PRIMARY KEY,
            key VARCHAR(255),
            value_type VARCHAR(20),
            int_value INTEGER,
            string_value TEXT,
            float_value FLOAT,
            datetime_value DATETIME
        )
    """)

    # Write bot start time
    conn.execute(
        "INSERT INTO KeyValueStore (key, value_type, string_value) "
        "VALUES (?, ?, ?)",
        ("bot_start_time", "datetime", "2026-02-01 00:00:00")
    )

    for i, t in enumerate(trades):
        amount = t["stake_amount"] / t["open_rate"] if t["open_rate"] else 0
        conn.execute("""
            INSERT INTO trades (
                id, pair, base_currency, stake_currency, is_open,
                open_rate, close_rate, close_profit, close_profit_abs,
                stake_amount, amount, open_date, close_date,
                exit_reason, strategy, stop_loss, stop_loss_pct,
                initial_stop_loss, initial_stop_loss_pct,
                max_rate, min_rate, open_trade_value
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            i + 1,
            t["pair"],
            t.get("token", t["pair"].split("/")[0]),
            STAKE_CURRENCY,
            1 if t["is_open"] else 0,
            t["open_rate"],
            t["close_rate"],
            t["close_profit"],
            t["close_profit_abs"],
            t["stake_amount"],
            amount,
            str(t["open_date"]),
            str(t["close_date"]) if t["close_date"] else None,
            t["exit_reason"],
            t.get("strategy", "CpcvSwingStrategy"),
            t["open_rate"] * (1 + STOPLOSS) if t["open_rate"] else None,
            STOPLOSS,
            t["open_rate"] * (1 + STOPLOSS) if t["open_rate"] else None,
            STOPLOSS,
            t.get("close_rate") or t["open_rate"],
            t["open_rate"] * 0.95 if t["open_rate"] else None,
            t["stake_amount"],
        ))

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def strategy():
    """Load the CpcvSwingStrategy with test config."""
    import sys
    strat_path = os.path.join(os.path.dirname(__file__),
                              "..", "user_data", "strategies")
    sys.path.insert(0, strat_path)

    tmp_dir = os.path.join(os.path.dirname(__file__), "..", "user_data")
    config = {
        "exchange": {"name": "binance"},
        "user_data_dir": os.path.abspath(tmp_dir),
        "stake_currency": "USDT",
        "trading_mode": "spot",
    }
    from CpcvSwingStrategy import CpcvSwingStrategy
    return CpcvSwingStrategy(config)


@pytest.fixture
def synthetic_run(tmp_path, strategy):
    """Run the full synthetic simulation and return (db_path, all_trades, run_dir)."""
    run_dir = str(tmp_path / "s11_binance" / "runs" / "run_20260201_000000")
    os.makedirs(run_dir)

    all_trades = []
    for token in TOKENS:
        candles = generate_month_candles(token)
        token_trades = simulate_trades(strategy, token, candles)
        all_trades.extend(token_trades)

    db_path = os.path.join(run_dir, "tradesv3.sqlite")
    create_freqtrade_db(db_path, all_trades)

    return db_path, all_trades, run_dir


# ---------------------------------------------------------------------------
# Tests: Synthetic data quality
# ---------------------------------------------------------------------------

class TestSyntheticDataQuality:
    """Verify the synthetic data generator produces usable candles."""

    def test_candle_count(self):
        df = generate_month_candles("SUI")
        assert len(df) == 720

    def test_has_bursts(self):
        """At least 3 candles with >3% return exist."""
        df = generate_month_candles("SUI")
        returns = df["close"].pct_change()
        bursts = (returns > 0.03).sum()
        assert bursts >= 3

    def test_ohlcv_consistency(self):
        """High >= Close >= Low for each candle."""
        df = generate_month_candles("AVAX")
        assert (df["high"] >= df["close"]).all()
        assert (df["low"] <= df["close"]).all()

    def test_volume_positive(self):
        df = generate_month_candles("TRX")
        assert (df["volume"] > 0).all()

    def test_different_tokens_different_data(self):
        """Each token gets unique price series."""
        df_sui = generate_month_candles("SUI")
        df_avax = generate_month_candles("AVAX")
        assert not np.allclose(df_sui["close"].values, df_avax["close"].values)


# ---------------------------------------------------------------------------
# Tests: Trade simulation produces trades
# ---------------------------------------------------------------------------

class TestTradeSimulation:
    """Verify the simulator produces trades from synthetic data."""

    def test_at_least_one_trade_across_all_tokens(self, strategy):
        """With 9 tokens and intentional bursts, we should get trades."""
        total = 0
        for token in TOKENS:
            candles = generate_month_candles(token)
            trades = simulate_trades(strategy, token, candles)
            total += len(trades)
        assert total > 0, "No trades generated across all 9 tokens"

    def test_trades_have_required_fields(self, strategy):
        candles = generate_month_candles("SUI")
        trades = simulate_trades(strategy, "SUI", candles)
        if not trades:
            pytest.skip("No trades for SUI in this seed")

        for t in trades:
            assert "pair" in t
            assert "open_rate" in t
            assert "open_date" in t
            assert "is_open" in t
            if not t["is_open"]:
                assert t["close_rate"] is not None
                assert t["close_profit"] is not None
                assert t["exit_reason"] is not None

    def test_closed_trades_have_profit(self, strategy):
        candles = generate_month_candles("SUI")
        trades = simulate_trades(strategy, "SUI", candles)
        closed = [t for t in trades if not t["is_open"]]
        if not closed:
            pytest.skip("No closed trades for SUI")

        for t in closed:
            expected = t["stake_amount"] * t["close_profit"]
            assert abs(t["close_profit_abs"] - expected) < 0.01

    def test_exit_reasons_are_valid(self, strategy):
        valid_reasons = {"stop_loss", "trailing_stop_loss", "exit_signal",
                         "max_hold_exit"}
        all_trades = []
        for token in TOKENS:
            candles = generate_month_candles(token)
            all_trades.extend(simulate_trades(strategy, token, candles))

        closed = [t for t in all_trades if not t["is_open"]]
        for t in closed:
            assert t["exit_reason"] in valid_reasons, \
                f"Unexpected exit reason: {t['exit_reason']}"


# ---------------------------------------------------------------------------
# Tests: Database persistence
# ---------------------------------------------------------------------------

class TestDatabasePersistence:
    """Verify trades are correctly persisted to SQLite."""

    def test_trade_count_matches(self, synthetic_run):
        db_path, all_trades, _ = synthetic_run
        conn = sqlite3.connect(db_path)
        db_count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        conn.close()
        assert db_count == len(all_trades)

    def test_closed_trades_queryable(self, synthetic_run):
        db_path, all_trades, _ = synthetic_run
        expected_closed = len([t for t in all_trades if not t["is_open"]])
        conn = sqlite3.connect(db_path)
        db_closed = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE is_open = 0"
        ).fetchone()[0]
        conn.close()
        assert db_closed == expected_closed

    def test_open_trades_queryable(self, synthetic_run):
        db_path, all_trades, _ = synthetic_run
        expected_open = len([t for t in all_trades if t["is_open"]])
        conn = sqlite3.connect(db_path)
        db_open = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE is_open = 1"
        ).fetchone()[0]
        conn.close()
        assert db_open == expected_open

    def test_schema_matches_freqtrade(self, synthetic_run):
        """DB has the columns generate_run_report() needs."""
        db_path, _, _ = synthetic_run
        conn = sqlite3.connect(db_path)
        cols = [row[1] for row in conn.execute(
            "PRAGMA table_info(trades)"
        ).fetchall()]
        conn.close()

        required = ["close_profit", "close_profit_abs", "open_rate",
                     "close_rate", "stake_amount", "pair", "is_open",
                     "open_date", "close_date", "exit_reason", "strategy"]
        for col in required:
            assert col in cols, f"Missing column: {col}"

    def test_kvs_has_bot_start_time(self, synthetic_run):
        db_path, _, _ = synthetic_run
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT * FROM KeyValueStore WHERE key = 'bot_start_time'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# Tests: Report generation (the core E2E test)
# ---------------------------------------------------------------------------

class TestRunReport:
    """Verify generate_run_report() produces a complete, valid report."""

    def test_report_generated(self, synthetic_run):
        from run_paper_trade import generate_run_report
        db_path, _, run_dir = synthetic_run
        report = generate_run_report("s11_binance", run_dir)
        assert "error" not in report

    def test_report_saved_to_file(self, synthetic_run):
        from run_paper_trade import generate_run_report
        _, _, run_dir = synthetic_run
        report = generate_run_report("s11_binance", run_dir)
        report_path = os.path.join(run_dir, "report.json")
        assert os.path.exists(report_path)

        with open(report_path) as f:
            saved = json.load(f)
        assert saved["instance_id"] == "s11_binance"

    def test_report_has_metrics(self, synthetic_run):
        from run_paper_trade import generate_run_report
        _, _, run_dir = synthetic_run
        report = generate_run_report("s11_binance", run_dir)

        m = report["metrics"]
        required_keys = [
            "closed_trades", "open_trades", "total_profit",
            "return_on_capital_pct", "win_rate", "profit_factor",
            "avg_win", "avg_loss", "expectancy",
            "best_trade", "worst_trade",
            "avg_trade_duration_hours", "shortest_trade_hours",
            "longest_trade_hours",
            "sharpe_ratio", "sortino_ratio",
            "max_drawdown", "max_drawdown_pct",
            "max_consecutive_wins", "max_consecutive_losses",
            "equity_high", "equity_low", "equity_final",
        ]
        for key in required_keys:
            assert key in m, f"Missing metric: {key}"

    def test_report_closed_trade_count_positive(self, synthetic_run):
        from run_paper_trade import generate_run_report
        _, all_trades, run_dir = synthetic_run
        report = generate_run_report("s11_binance", run_dir)

        expected_closed = len([t for t in all_trades if not t["is_open"]])
        assert report["metrics"]["closed_trades"] == expected_closed

    def test_report_has_trade_details(self, synthetic_run):
        from run_paper_trade import generate_run_report
        _, all_trades, run_dir = synthetic_run
        report = generate_run_report("s11_binance", run_dir)

        assert len(report["trades"]) == len(all_trades)
        for t in report["trades"]:
            assert "pair" in t
            assert "open_rate" in t
            assert "open_date" in t

    def test_report_has_per_pair_breakdown(self, synthetic_run):
        from run_paper_trade import generate_run_report
        _, all_trades, run_dir = synthetic_run
        report = generate_run_report("s11_binance", run_dir)

        closed = [t for t in all_trades if not t["is_open"]]
        if closed:
            assert len(report["per_pair"]) > 0
            for pair, stats in report["per_pair"].items():
                assert "trades" in stats
                assert "profit" in stats
                assert "win_rate" in stats

    def test_report_win_rate_sensible(self, synthetic_run):
        from run_paper_trade import generate_run_report
        _, _, run_dir = synthetic_run
        report = generate_run_report("s11_binance", run_dir)

        wr = report["metrics"]["win_rate"]
        assert 0 <= wr <= 1.0

    def test_report_profit_factor_positive(self, synthetic_run):
        from run_paper_trade import generate_run_report
        _, all_trades, run_dir = synthetic_run
        report = generate_run_report("s11_binance", run_dir)

        closed = [t for t in all_trades if not t["is_open"]]
        if closed:
            assert report["metrics"]["profit_factor"] >= 0

    def test_report_has_metadata(self, synthetic_run):
        from run_paper_trade import generate_run_report
        _, _, run_dir = synthetic_run
        report = generate_run_report("s11_binance", run_dir)

        assert report["instance_id"] == "s11_binance"
        assert report["run_id"] == "run_20260201_000000"
        assert "generated_at" in report
        assert report["bot_start_time"] is not None

    def test_report_return_on_capital(self, synthetic_run):
        from run_paper_trade import generate_run_report
        _, _, run_dir = synthetic_run
        report = generate_run_report("s11_binance", run_dir)
        m = report["metrics"]
        # ROC% should match total_profit / 200k * 100
        expected_roc = m["total_profit"] / 200_000 * 100
        assert abs(m["return_on_capital_pct"] - expected_roc) < 0.01

    def test_report_avg_win_loss(self, synthetic_run):
        from run_paper_trade import generate_run_report
        _, all_trades, run_dir = synthetic_run
        report = generate_run_report("s11_binance", run_dir)
        m = report["metrics"]
        closed = [t for t in all_trades if not t["is_open"]]
        if any(t["close_profit_abs"] > 0 for t in closed):
            assert m["avg_win"] > 0
        if any(t["close_profit_abs"] < 0 for t in closed):
            assert m["avg_loss"] < 0

    def test_report_best_worst_trade(self, synthetic_run):
        from run_paper_trade import generate_run_report
        _, _, run_dir = synthetic_run
        report = generate_run_report("s11_binance", run_dir)
        m = report["metrics"]
        assert m["best_trade"] >= m["worst_trade"]

    def test_report_sortino_ratio(self, synthetic_run):
        from run_paper_trade import generate_run_report
        _, _, run_dir = synthetic_run
        report = generate_run_report("s11_binance", run_dir)
        # Sortino should be a number (not NaN)
        assert not math.isnan(report["metrics"]["sortino_ratio"])

    def test_report_consecutive_streaks(self, synthetic_run):
        from run_paper_trade import generate_run_report
        _, _, run_dir = synthetic_run
        report = generate_run_report("s11_binance", run_dir)
        m = report["metrics"]
        assert m["max_consecutive_wins"] >= 0
        assert m["max_consecutive_losses"] >= 0
        # Total streaks can't exceed closed trades
        assert m["max_consecutive_wins"] <= m["closed_trades"]
        assert m["max_consecutive_losses"] <= m["closed_trades"]

    def test_report_equity_watermarks(self, synthetic_run):
        from run_paper_trade import generate_run_report
        _, _, run_dir = synthetic_run
        report = generate_run_report("s11_binance", run_dir)
        m = report["metrics"]
        assert m["equity_high"] >= m["equity_low"]
        assert m["equity_high"] >= m["equity_final"] or m["equity_final"] == m["equity_high"]
        assert m["equity_low"] <= m["equity_final"] or m["equity_final"] == m["equity_low"]
        # Final should be starting_capital + total_profit
        assert abs(m["equity_final"] - (200_000 + m["total_profit"])) < 0.01

    def test_report_trade_durations(self, synthetic_run):
        from run_paper_trade import generate_run_report
        _, _, run_dir = synthetic_run
        report = generate_run_report("s11_binance", run_dir)
        m = report["metrics"]
        if m["closed_trades"] > 0:
            assert m["avg_trade_duration_hours"] > 0
            assert m["shortest_trade_hours"] > 0
            assert m["longest_trade_hours"] >= m["shortest_trade_hours"]
            assert m["avg_trade_duration_hours"] >= m["shortest_trade_hours"]
            assert m["avg_trade_duration_hours"] <= m["longest_trade_hours"]

    def test_report_max_drawdown_pct(self, synthetic_run):
        from run_paper_trade import generate_run_report
        _, _, run_dir = synthetic_run
        report = generate_run_report("s11_binance", run_dir)
        m = report["metrics"]
        assert m["max_drawdown"] <= 0
        assert m["max_drawdown_pct"] <= 0

    def test_report_json_serializable(self, synthetic_run):
        from run_paper_trade import generate_run_report
        _, _, run_dir = synthetic_run
        report = generate_run_report("s11_binance", run_dir)

        # Must be fully JSON-serializable (no datetime objects, no NaN)
        serialized = json.dumps(report)
        deserialized = json.loads(serialized)
        assert deserialized["instance_id"] == "s11_binance"


# ---------------------------------------------------------------------------
# Tests: Run isolation
# ---------------------------------------------------------------------------

class TestRunIsolation:
    """Verify that separate runs don't share trade data."""

    def test_two_runs_independent(self, tmp_path, strategy):
        """Two runs for the same instance have completely separate DBs."""
        from run_paper_trade import create_run_dir, generate_run_report

        instance_dir = str(tmp_path / "s11_binance")
        os.makedirs(instance_dir)

        # Run 1: only SUI
        run1_dir, run1_id = create_run_dir(instance_dir, "run_01")
        candles = generate_month_candles("SUI")
        trades_1 = simulate_trades(strategy, "SUI", candles)
        create_freqtrade_db(os.path.join(run1_dir, "tradesv3.sqlite"), trades_1)

        # Run 2: only AVAX
        run2_dir, run2_id = create_run_dir(instance_dir, "run_02")
        candles = generate_month_candles("AVAX")
        trades_2 = simulate_trades(strategy, "AVAX", candles)
        create_freqtrade_db(os.path.join(run2_dir, "tradesv3.sqlite"), trades_2)

        # Reports should have different trade sets
        r1 = generate_run_report("s11_binance", run1_dir)
        r2 = generate_run_report("s11_binance", run2_dir)

        # Verify isolation: run 1 has only SUI pairs, run 2 has only AVAX
        r1_pairs = {t["pair"] for t in r1["trades"]}
        r2_pairs = {t["pair"] for t in r2["trades"]}
        if r1["trades"]:
            assert all("SUI" in p for p in r1_pairs)
        if r2["trades"]:
            assert all("AVAX" in p for p in r2_pairs)

    def test_list_runs(self, tmp_path, strategy):
        """list_runs() returns all runs with metadata."""
        from run_paper_trade import create_run_dir, list_runs

        instance_dir = str(tmp_path / "s11_binance")
        os.makedirs(instance_dir)

        create_run_dir(instance_dir, "run_alpha")
        create_run_dir(instance_dir, "run_beta")

        runs = list_runs(instance_dir)
        assert len(runs) == 2
        ids = [r["run_id"] for r in runs]
        assert "run_alpha" in ids
        assert "run_beta" in ids

    def test_current_symlink_points_to_latest(self, tmp_path):
        from run_paper_trade import create_run_dir

        instance_dir = str(tmp_path / "s11_binance")
        os.makedirs(instance_dir)

        create_run_dir(instance_dir, "run_old")
        create_run_dir(instance_dir, "run_new")

        current = os.path.join(instance_dir, "current")
        assert os.path.islink(current)
        target = os.readlink(current)
        assert "run_new" in target
