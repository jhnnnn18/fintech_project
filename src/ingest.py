"""Daily OHLCV ingestion into DuckDB.

Idempotent: re-running for a date range that's already loaded updates
existing rows in place (handling vendor restatements of adj_close) rather
than duplicating them, because raw_ohlcv is keyed on (ticker, date).

Usage:
    python -m src.ingest
    python -m src.ingest --start 2021-01-01 --end 2026-08-15
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import duckdb
import pandas as pd
import yaml
import yfinance as yf

from src.db import get_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TICKERS_FILE = REPO_ROOT / "config" / "tickers.yaml"
DEFAULT_DB_PATH = REPO_ROOT / "data" / "market_data.duckdb"
DEFAULT_START = "2021-01-01"

RAW_COLUMNS = ["ticker", "date", "open", "high", "low", "close", "adj_close", "volume"]


def load_tickers(tickers_file: Path) -> list[str]:
    with open(tickers_file) as f:
        cfg = yaml.safe_load(f) or {}
    tickers = [*cfg.get("etfs", []), *cfg.get("stocks", []), *cfg.get("custom", [])]
    return sorted(set(tickers))


def fetch_ticker(ticker: str, start: str, end: str | None) -> pd.DataFrame:
    df = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=False)
    if df.empty:
        raise ValueError(f"no data returned for {ticker}")
    df = df.reset_index().rename(columns={
        "Date": "date",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Adj Close": "adj_close",
        "Volume": "volume",
    })
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["ticker"] = ticker
    return df[RAW_COLUMNS]


def upsert_ohlcv(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """Insert new (ticker, date) rows and refresh existing ones. Returns net new rows."""
    if df.empty:
        return 0
    conn.register("staged_ohlcv", df)
    before = conn.execute("SELECT count(*) FROM raw_ohlcv").fetchone()[0]
    conn.execute("""
        INSERT INTO raw_ohlcv (ticker, date, open, high, low, close, adj_close, volume)
        SELECT ticker, date, open, high, low, close, adj_close, volume FROM staged_ohlcv
        ON CONFLICT (ticker, date) DO UPDATE SET
            open = EXCLUDED.open,
            high = EXCLUDED.high,
            low = EXCLUDED.low,
            close = EXCLUDED.close,
            adj_close = EXCLUDED.adj_close,
            volume = EXCLUDED.volume,
            ingested_at = now();
    """)
    conn.unregister("staged_ohlcv")
    after = conn.execute("SELECT count(*) FROM raw_ohlcv").fetchone()[0]
    return after - before


def run(tickers_file: Path, db_path: Path, start: str, end: str | None) -> None:
    tickers = load_tickers(tickers_file)
    logger.info("loaded %d tickers from %s", len(tickers), tickers_file)

    conn = get_connection(str(db_path))
    failures: list[str] = []
    new_rows_total = 0

    for ticker in tickers:
        try:
            df = fetch_ticker(ticker, start, end)
            new_rows = upsert_ohlcv(conn, df)
            new_rows_total += new_rows
            logger.info("%s: %d rows fetched, %d new", ticker, len(df), new_rows)
        except Exception as exc:
            logger.warning("%s: failed (%s)", ticker, exc)
            failures.append(ticker)

    conn.close()
    logger.info(
        "done: %d/%d tickers succeeded, %d new rows inserted",
        len(tickers) - len(failures), len(tickers), new_rows_total,
    )
    if failures:
        logger.warning("failed tickers: %s", ", ".join(failures))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest daily OHLCV into DuckDB")
    parser.add_argument("--tickers-file", type=Path, default=DEFAULT_TICKERS_FILE)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=None, help="defaults to today")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(args.tickers_file, args.db, args.start, args.end)
