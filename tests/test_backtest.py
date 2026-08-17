"""Tests for the crossover backtest's core correctness guarantees: no
lookahead, warm-up exclusion, transaction cost timing, trade extraction,
and metric math. All synthetic data -- no DuckDB, no network."""
import pandas as pd
import pytest

from src.backtest import (
    WARMUP_DAYS,
    compute_metrics,
    extract_trades,
    run_ticker_benchmark,
    run_ticker_strategy,
)


def make_crossover_df(ticker="TEST"):
    """55 trading days. MA signal flips to golden-cross on day 50, back to
    death-cross on day 53. Constant 1% daily_return throughout, so expected
    strategy returns can be hand-computed."""
    n = 55
    dates = pd.bdate_range("2020-01-01", periods=n)
    signal_days = {50: 1, 53: 0}  # ma_20 > ma_50 from day 50, flips off at day 53
    state = 0
    ma_20, ma_50 = [], []
    for day in range(1, n + 1):
        if day in signal_days:
            state = signal_days[day]
        ma_20.append(2.0 if state else 1.0)
        ma_50.append(1.0)  # ma_20 > ma_50 iff state == 1
    return pd.DataFrame({
        "ticker": ticker,
        "date": dates,
        "adj_close": 100.0,
        "daily_return": 0.01,
        "ma_20": ma_20,
        "ma_50": ma_50,
    })


def test_warmup_period_is_excluded():
    df = make_crossover_df()
    result = run_ticker_strategy(df)
    assert len(result) == len(df) - WARMUP_DAYS
    assert result["trading_day_number"].min() == WARMUP_DAYS + 1


def test_no_lookahead_position_lags_signal_by_one_day():
    df = make_crossover_df()
    result = run_ticker_strategy(df)
    # trading_day 51 is the first kept row; its position must reflect day
    # 50's signal (already golden-cross), not day 51's own.
    first_row = result[result["trading_day_number"] == 51].iloc[0]
    assert first_row["position"] == 1


def test_transaction_cost_charged_only_on_position_change():
    df = make_crossover_df()
    result = run_ticker_strategy(df).set_index("trading_day_number")
    assert result.loc[51, "position_change"] == 1  # entry: 0 -> 1
    assert result.loc[51, "cost"] > 0
    assert result.loc[52, "position_change"] == 0  # holding, no new cost
    assert result.loc[52, "cost"] == 0
    assert result.loc[54, "position_change"] == 1  # exit: 1 -> 0
    assert result.loc[54, "cost"] > 0


def test_extract_trades_finds_one_round_trip():
    df = make_crossover_df()
    result = run_ticker_strategy(df)
    trades = extract_trades(result)
    assert len(trades) == 1
    trade = trades[0]
    assert trade["trade_return"] > 0  # 3 days of +1% minus two cost legs, net positive
    assert trade["exit_date"] is not None


def test_extract_trades_handles_still_open_position():
    df = make_crossover_df()
    df.loc[df.index >= 52, ["ma_20", "ma_50"]] = [2.0, 1.0]  # stay golden-cross to the end
    result = run_ticker_strategy(df)
    trades = extract_trades(result)
    assert trades[-1]["exit_date"] is None


def test_benchmark_charges_entry_cost_once():
    df = make_crossover_df()
    strategy = run_ticker_strategy(df)
    benchmark = run_ticker_benchmark(strategy)
    assert benchmark.loc[0, "benchmark_return"] < benchmark.loc[0, "daily_return"]
    assert benchmark.loc[1, "benchmark_return"] == pytest.approx(benchmark.loc[1, "daily_return"])


def test_compute_metrics_on_known_series():
    returns = pd.Series([0.10, -0.10, 0.10])
    metrics = compute_metrics(returns)
    expected_equity = 1.10 * 0.90 * 1.10
    assert metrics["total_return"] == pytest.approx(expected_equity - 1)
    assert metrics["max_drawdown"] == pytest.approx(-0.1)  # trough (0.99) vs. prior peak (1.10)


def test_compute_metrics_exposure_and_win_rate():
    df = make_crossover_df()
    result = run_ticker_strategy(df)
    trades = pd.DataFrame(extract_trades(result))
    metrics = compute_metrics(result["strategy_return"], trades, result["position"])
    assert metrics["exposure"] == pytest.approx(result["position"].mean())
    assert metrics["win_rate"] == 1.0
    assert metrics["num_trades"] == 1
