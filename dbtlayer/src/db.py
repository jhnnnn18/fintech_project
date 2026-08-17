"""DuckDB connection and schema management for the market data pipeline."""
from pathlib import Path

import duckdb

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_ohlcv (
    ticker VARCHAR NOT NULL,
    date DATE NOT NULL,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    adj_close DOUBLE,
    volume BIGINT,
    ingested_at TIMESTAMP NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY (ticker, date)
);
"""


def get_connection(db_path: str) -> duckdb.DuckDBPyConnection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(db_path)
    conn.execute(SCHEMA)
    return conn
