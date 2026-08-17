"""Tests for the DuckDB upsert logic. No network calls — yfinance is not exercised here."""
import pandas as pd
import pytest

from src.db import get_connection
from src.ingest import upsert_ohlcv


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(str(tmp_path / "test.duckdb"))
    yield connection
    connection.close()


def sample_df(ticker="AAPL", n=3):
    return pd.DataFrame({
        "ticker": [ticker] * n,
        "date": pd.date_range("2024-01-01", periods=n).date,
        "open": [100.0 + i for i in range(n)],
        "high": [101.0 + i for i in range(n)],
        "low": [99.0 + i for i in range(n)],
        "close": [100.5 + i for i in range(n)],
        "adj_close": [100.5 + i for i in range(n)],
        "volume": [1_000_000 + i for i in range(n)],
    })


def test_insert_adds_rows(conn):
    new_rows = upsert_ohlcv(conn, sample_df())
    assert new_rows == 3
    assert conn.execute("SELECT count(*) FROM raw_ohlcv").fetchone()[0] == 3


def test_rerun_is_idempotent(conn):
    df = sample_df()
    upsert_ohlcv(conn, df)
    new_rows = upsert_ohlcv(conn, df)
    assert new_rows == 0
    assert conn.execute("SELECT count(*) FROM raw_ohlcv").fetchone()[0] == 3


def test_upsert_refreshes_existing_rows(conn):
    df = sample_df()
    upsert_ohlcv(conn, df)
    revised = df.copy()
    revised["close"] += 5
    upsert_ohlcv(conn, revised)

    assert conn.execute("SELECT count(*) FROM raw_ohlcv").fetchone()[0] == 3
    closes = conn.execute("SELECT close FROM raw_ohlcv ORDER BY date").fetchall()
    assert closes[0][0] == 105.5


def test_multiple_tickers_no_collision(conn):
    upsert_ohlcv(conn, sample_df("AAPL"))
    upsert_ohlcv(conn, sample_df("MSFT"))
    assert conn.execute("SELECT count(*) FROM raw_ohlcv").fetchone()[0] == 6


def test_empty_dataframe_is_a_noop(conn):
    empty = sample_df(n=0)
    assert upsert_ohlcv(conn, empty) == 0
    assert conn.execute("SELECT count(*) FROM raw_ohlcv").fetchone()[0] == 0
