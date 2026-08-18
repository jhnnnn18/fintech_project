-- Moving averages and realized volatility, windowed per ticker ordered by
-- date. Built on adj_close (not close) so a stock split doesn't masquerade
-- as a moving-average crossover signal downstream in the Week 3 backtest.
--
-- Rolling windows near the start of a ticker's history are partial (e.g. the
-- 200-day MA has fewer than 200 observations behind it until day 200) —
-- documented in the README rather than hidden.
with returns as (
    select * from {{ ref('int_daily_returns') }}
)

select
    ticker,
    date,
    avg(adj_close) over (
        partition by ticker order by date
        rows between 19 preceding and current row
    ) as ma_20,
    avg(adj_close) over (
        partition by ticker order by date
        rows between 49 preceding and current row
    ) as ma_50,
    avg(adj_close) over (
        partition by ticker order by date
        rows between 199 preceding and current row
    ) as ma_200,
    -- 20-day rolling stdev of daily returns, annualized on a 252-trading-day year
    stddev_samp(daily_return) over (
        partition by ticker order by date
        rows between 19 preceding and current row
    ) * sqrt(252) as realized_vol_20d_annualized
from returns
