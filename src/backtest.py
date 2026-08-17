"""Moving-average crossover backtest vs. buy-and-hold.

Strategy: long a ticker whenever its 20-day moving average is above its
50-day moving average (a "golden cross" state), flat otherwise. Long-only,
no shorting, no leverage, equal-weighted across whatever tickers are
eligible on a given date.

Honesty / correctness choices (see README for the fuller writeup):
  - No lookahead: a day's SIGNAL is computed from that day's close, but the
    POSITION held during a day is the *prior* day's signal (`shift(1)`) --
    you can't act on information before it exists.
  - No fake data: a ticker's first WARMUP_DAYS trading days are dropped.
    dbt's moving averages use a partial window before that many
    observations exist (see dbt/models/intermediate/int_rolling_metrics.sql),
    so a "50-day MA" built from 3 real days would be a spurious signal.
  - Transaction costs: TRANSACTION_COST_BPS is charged on each position
    change (entry or exit), a simplified stand-in for commission + slippage.
    The buy-and-hold benchmark pays it once, on its single entry, and is
    evaluated over the identical post-warmup date range as the strategy so
    the comparison is apples-to-apples.
  - Point-in-time: every input column already comes from fct_daily_metrics,
    whose windows only look backward.

Usage:
    python -m src.backtest
"""
from __future__ import annotations

import logging
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "market_data.duckdb"

WARMUP_DAYS = 50
TRANSACTION_COST_BPS = 5
TRADING_DAYS_PER_YEAR = 252


def load_metrics(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return conn.execute("""
        SELECT ticker, date, adj_close, daily_return, ma_20, ma_50
        FROM fct_daily_metrics
        ORDER BY ticker, date
    """).fetchdf()


def run_ticker_strategy(ticker_df: pd.DataFrame) -> pd.DataFrame:
    """One ticker's daily crossover-strategy returns, after the warm-up cut."""
    df = ticker_df.reset_index(drop=True).copy()
    df["trading_day_number"] = df.index + 1

    df["signal"] = (df["ma_20"] > df["ma_50"]).astype(int)
    df["position"] = df["signal"].shift(1).fillna(0).astype(int)
    df["position_change"] = df["position"].diff().fillna(0).abs()
    df["cost"] = df["position_change"] * (TRANSACTION_COST_BPS / 10_000)
    df["strategy_return"] = df["position"] * df["daily_return"].fillna(0) - df["cost"]

    return df[df["trading_day_number"] > WARMUP_DAYS].reset_index(drop=True)


def run_ticker_benchmark(strategy_df: pd.DataFrame) -> pd.DataFrame:
    """Buy-and-hold over the same post-warmup rows the strategy trades on."""
    df = strategy_df[["ticker", "date", "daily_return"]].reset_index(drop=True).copy()
    df["benchmark_return"] = df["daily_return"].fillna(0)
    df.loc[0, "benchmark_return"] -= TRANSACTION_COST_BPS / 10_000  # one-time entry cost
    return df


def extract_trades(strategy_df: pd.DataFrame) -> list[dict]:
    """Round-trip trades: entry day through the day the exit cost is charged,
    so a trade's compounded return reflects both its entry and exit costs."""
    trades = []
    ticker = strategy_df["ticker"].iloc[0] if not strategy_df.empty else None
    entry_idx = None

    for i, row in strategy_df.iterrows():
        if entry_idx is None and row["position"] == 1:
            entry_idx = i
        elif entry_idx is not None and row["position"] == 0:
            segment = strategy_df.loc[entry_idx:i]
            trade_return = (1 + segment["strategy_return"]).prod() - 1
            trades.append({
                "ticker": ticker,
                "entry_date": strategy_df.loc[entry_idx, "date"],
                "exit_date": row["date"],
                "trade_return": trade_return,
            })
            entry_idx = None

    if entry_idx is not None:
        segment = strategy_df.loc[entry_idx:]
        trade_return = (1 + segment["strategy_return"]).prod() - 1
        trades.append({
            "ticker": ticker,
            "entry_date": strategy_df.loc[entry_idx, "date"],
            "exit_date": None,
            "trade_return": trade_return,
        })

    return trades


def compute_metrics(daily_returns: pd.Series, trades: pd.DataFrame | None = None,
                     positions: pd.Series | None = None) -> dict:
    returns = daily_returns.fillna(0)
    equity = (1 + returns).cumprod()
    mean_r, std_r = returns.mean(), returns.std()

    metrics = {
        "total_return": equity.iloc[-1] - 1,
        "sharpe": (mean_r / std_r) * np.sqrt(TRADING_DAYS_PER_YEAR) if std_r > 0 else float("nan"),
        "max_drawdown": (equity / equity.cummax() - 1).min(),
    }
    if trades is not None and len(trades) > 0:
        metrics["win_rate"] = (trades["trade_return"] > 0).mean()
        metrics["num_trades"] = len(trades)
    if positions is not None:
        metrics["exposure"] = positions.mean()
    return metrics


def run(db_path: Path) -> None:
    conn = duckdb.connect(str(db_path))
    metrics_df = load_metrics(conn)
    tickers = sorted(metrics_df["ticker"].unique())
    logger.info("running backtest for %d tickers", len(tickers))

    strategy_frames, benchmark_frames, all_trades = [], [], []
    for ticker in tickers:
        ticker_df = metrics_df[metrics_df["ticker"] == ticker]
        strategy_df = run_ticker_strategy(ticker_df)
        if strategy_df.empty:
            logger.warning("%s: fewer than %d trading days, skipped", ticker, WARMUP_DAYS)
            continue
        strategy_frames.append(strategy_df)
        benchmark_frames.append(run_ticker_benchmark(strategy_df))
        all_trades.extend(extract_trades(strategy_df))

    strategy_all = pd.concat(strategy_frames, ignore_index=True)
    benchmark_all = pd.concat(benchmark_frames, ignore_index=True)
    trades_df = pd.DataFrame(all_trades)

    portfolio_strategy = strategy_all.groupby("date")["strategy_return"].mean()
    portfolio_benchmark = benchmark_all.groupby("date")["benchmark_return"].mean()

    strategy_metrics = compute_metrics(portfolio_strategy, trades_df, strategy_all["position"])
    benchmark_metrics = compute_metrics(portfolio_benchmark)

    logger.info("strategy:  %s", strategy_metrics)
    logger.info("benchmark: %s", benchmark_metrics)

    daily = pd.DataFrame({
        "date": portfolio_strategy.index,
        "strategy_return": portfolio_strategy.values,
        "benchmark_return": portfolio_benchmark.reindex(portfolio_strategy.index).values,
    })
    daily["strategy_equity"] = (1 + daily["strategy_return"]).cumprod()
    daily["benchmark_equity"] = (1 + daily["benchmark_return"]).cumprod()

    summary = pd.DataFrame([
        {"metric": k, "strategy": strategy_metrics.get(k), "benchmark": benchmark_metrics.get(k)}
        for k in ["total_return", "sharpe", "max_drawdown", "win_rate", "num_trades", "exposure"]
    ])

    conn.register("daily_df", daily)
    conn.execute("CREATE OR REPLACE TABLE backtest_daily AS SELECT * FROM daily_df")
    conn.register("trades_df", trades_df)
    conn.execute("CREATE OR REPLACE TABLE backtest_trades AS SELECT * FROM trades_df")
    conn.register("summary_df", summary)
    conn.execute("CREATE OR REPLACE TABLE backtest_summary AS SELECT * FROM summary_df")
    conn.close()

    logger.info("wrote backtest_daily, backtest_trades, backtest_summary to %s", db_path)


if __name__ == "__main__":
    run(DEFAULT_DB_PATH)
