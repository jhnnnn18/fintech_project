"""Exports backtest results to JSON for the static D3.js dashboard
(docs/index.html), so GitHub Pages can serve pre-computed data without a
live database behind it.

Usage:
    python -m src.export_dashboard_data
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path

import duckdb

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "market_data.duckdb"
DEFAULT_OUT_DIR = REPO_ROOT / "docs" / "data"

DAILY_COLUMNS = [
    "date", "strategy_return", "benchmark_return",
    "strategy_equity", "benchmark_equity", "strategy_drawdown",
]


def _json_safe(value):
    """NaN isn't valid JSON (json.dumps emits a bare `NaN` token by default,
    which JS's JSON.parse rejects) -- e.g. Sharpe is NaN for a zero-variance
    series. Null is the honest, parseable stand-in."""
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _round_safe(value, ndigits=4):
    """4 decimal places is far more precision than a price chart needs and
    keeps the per-ticker JSON small -- full float64 repr roughly doubles it."""
    value = _json_safe(value)
    return round(value, ndigits) if isinstance(value, float) else value


def export_daily(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    rows = conn.execute("""
        SELECT
            date,
            strategy_return,
            benchmark_return,
            strategy_equity,
            benchmark_equity,
            strategy_equity / max(strategy_equity) OVER (
                ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) - 1 AS strategy_drawdown
        FROM backtest_daily
        ORDER BY date
    """).fetchall()
    return [
        dict(zip(DAILY_COLUMNS, [
            row[0].strftime("%Y-%m-%d") if hasattr(row[0], "strftime") else row[0],
            *(_json_safe(v) for v in row[1:]),
        ]))
        for row in rows
    ]


def export_summary(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    rows = conn.execute("SELECT metric, strategy, benchmark FROM backtest_summary").fetchall()
    return [
        {"metric": m, "strategy": _json_safe(s), "benchmark": _json_safe(b)}
        for m, s, b in rows
    ]


def export_ticker_prices(conn: duckdb.DuckDBPyConnection) -> dict[str, list[dict]]:
    """One series per ticker: adj_close, ma_20, ma_50, for the standalone
    per-ticker price/MA chart -- independent of the backtest's warm-up cut,
    since this is a plain price view, not a trading signal."""
    rows = conn.execute("""
        SELECT ticker, date, adj_close, ma_20, ma_50
        FROM fct_daily_metrics
        ORDER BY ticker, date
    """).fetchall()
    by_ticker: dict[str, list[dict]] = {}
    for ticker, date, adj_close, ma_20, ma_50 in rows:
        by_ticker.setdefault(ticker, []).append({
            "date": date.strftime("%Y-%m-%d") if hasattr(date, "strftime") else date,
            "adj_close": _round_safe(adj_close),
            "ma_20": _round_safe(ma_20),
            "ma_50": _round_safe(ma_50),
        })
    return by_ticker


def run(db_path: Path, out_dir: Path, tickers_out_dir: Path | None = None) -> None:
    tickers_out_dir = tickers_out_dir or out_dir / "tickers"
    conn = duckdb.connect(str(db_path))
    out_dir.mkdir(parents=True, exist_ok=True)
    tickers_out_dir.mkdir(parents=True, exist_ok=True)

    daily_records = export_daily(conn)
    summary_records = export_summary(conn)
    ticker_prices = export_ticker_prices(conn)
    conn.close()

    (out_dir / "backtest_daily.json").write_text(json.dumps(daily_records))
    (out_dir / "backtest_summary.json").write_text(json.dumps(summary_records))
    for ticker, records in ticker_prices.items():
        (tickers_out_dir / f"{ticker}.json").write_text(json.dumps(records))
    (out_dir / "tickers_index.json").write_text(json.dumps(sorted(ticker_prices.keys())))

    logger.info("wrote %d daily rows and %d summary rows to %s",
                len(daily_records), len(summary_records), out_dir)
    logger.info("wrote per-ticker price data for %d tickers to %s",
                len(ticker_prices), tickers_out_dir)


if __name__ == "__main__":
    run(DEFAULT_DB_PATH, DEFAULT_OUT_DIR)
