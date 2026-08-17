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


def run(db_path: Path, out_dir: Path) -> None:
    conn = duckdb.connect(str(db_path))
    out_dir.mkdir(parents=True, exist_ok=True)

    daily_records = export_daily(conn)
    summary_records = export_summary(conn)
    conn.close()

    (out_dir / "backtest_daily.json").write_text(json.dumps(daily_records))
    (out_dir / "backtest_summary.json").write_text(json.dumps(summary_records))
    logger.info("wrote %d daily rows and %d summary rows to %s",
                len(daily_records), len(summary_records), out_dir)


if __name__ == "__main__":
    run(DEFAULT_DB_PATH, DEFAULT_OUT_DIR)
