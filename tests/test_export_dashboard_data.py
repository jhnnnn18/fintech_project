"""Tests for the dashboard JSON export. No network; a temp DuckDB file
stands in for a built backtest_daily/backtest_summary."""
import math

import duckdb
import pytest

from src.export_dashboard_data import _json_safe, export_daily, export_summary


def test_json_safe_converts_nan_to_none():
    assert _json_safe(float("nan")) is None


def test_json_safe_passes_through_normal_values():
    assert _json_safe(0.05) == 0.05
    assert _json_safe(None) is None
    assert _json_safe("etf") == "etf"


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
