"""Tests for the dashboard JSON export. No network; a temp DuckDB file
stands in for a built backtest_daily/backtest_summary."""
import math

import duckdb
import pytest

from src.export_dashboard_data import (
    _json_safe,
    _round_safe,
    export_daily,
    export_summary,
    export_ticker_prices,
)


def test_json_safe_converts_nan_to_none():
    assert _json_safe(float("nan")) is None


def test_json_safe_passes_through_normal_values():
    assert _json_safe(0.05) == 0.05
    assert _json_safe(None) is None
    assert _json_safe("etf") == "etf"


def test_round_safe_rounds_floats_and_converts_nan():
    assert _round_safe(1.234567) == 1.2346
    assert _round_safe(float("nan")) is None
    assert _round_safe(None) is None
    assert _round_safe("AAPL") == "AAPL"


@pytest.fixture
def conn(tmp_path):
    connection = duckdb.connect(str(tmp_path / "test.duckdb"))
    connection.execute("""
        CREATE TABLE backtest_daily (
            date DATE, strategy_return DOUBLE, benchmark_return DOUBLE,
            strategy_equity DOUBLE, benchmark_equity DOUBLE
        )
    """)
    connection.execute("""
        INSERT INTO backtest_daily VALUES
            ('2024-01-01', 0.01, 0.02, 1.01, 1.02),
            ('2024-01-02', -0.02, 0.01, 0.9898, 1.0302),
            ('2024-01-03', 0.03, -0.01, 1.019194, 1.019898)
    """)
    connection.execute("""
        CREATE TABLE backtest_summary (metric VARCHAR, strategy DOUBLE, benchmark DOUBLE)
    """)
    connection.execute("""
        INSERT INTO backtest_summary VALUES
            ('total_return', 0.10, 0.05),
            ('win_rate', 0.5, NULL)
    """)
    connection.execute("""
        CREATE TABLE fct_daily_metrics (
            ticker VARCHAR, date DATE, adj_close DOUBLE, ma_20 DOUBLE, ma_50 DOUBLE
        )
    """)
    connection.execute("""
        INSERT INTO fct_daily_metrics VALUES
            ('AAPL', '2024-01-01', 100.123456, 99.5, 98.0),
            ('AAPL', '2024-01-02', 101.0, 99.6, 98.1),
            ('MSFT', '2024-01-01', 200.0, 199.0, 198.0)
    """)
    yield connection
    connection.close()


def test_export_daily_shape_and_date_format(conn):
    records = export_daily(conn)
    assert len(records) == 3
    assert records[0]["date"] == "2024-01-01"
    assert set(records[0].keys()) == {
        "date", "strategy_return", "benchmark_return",
        "strategy_equity", "benchmark_equity", "strategy_drawdown",
    }


def test_export_daily_drawdown_is_zero_at_new_peak_and_negative_after_dip(conn):
    records = export_daily(conn)
    # day 1 (1.01) is a new peak -> zero drawdown
    assert records[0]["strategy_drawdown"] == pytest.approx(0.0)
    # day 2 (0.9898) dipped below day 1's peak (1.01) -> negative drawdown
    assert records[1]["strategy_drawdown"] < 0
    # day 3 (1.019194) set a new peak above day 1 -> back to zero
    assert records[2]["strategy_drawdown"] == pytest.approx(0.0)


def test_export_summary_preserves_null_benchmark(conn):
    records = export_summary(conn)
    win_rate = next(r for r in records if r["metric"] == "win_rate")
    assert win_rate["benchmark"] is None
    assert win_rate["strategy"] == 0.5


def test_export_ticker_prices_groups_by_ticker(conn):
    by_ticker = export_ticker_prices(conn)
    assert set(by_ticker.keys()) == {"AAPL", "MSFT"}
    assert len(by_ticker["AAPL"]) == 2
    assert len(by_ticker["MSFT"]) == 1


def test_export_ticker_prices_shape_date_format_and_rounding(conn):
    by_ticker = export_ticker_prices(conn)
    first = by_ticker["AAPL"][0]
    assert set(first.keys()) == {"date", "adj_close", "ma_20", "ma_50"}
    assert first["date"] == "2024-01-01"
    assert first["adj_close"] == 100.1235  # rounded from 100.123456


def test_export_ticker_prices_preserves_date_order_within_ticker(conn):
    by_ticker = export_ticker_prices(conn)
    dates = [row["date"] for row in by_ticker["AAPL"]]
    assert dates == sorted(dates)
