-- Daily return and drawdown, computed on adj_close so splits/dividends don't
-- masquerade as price moves. Both are windowed per ticker, ordered by date,
-- so there's no lookahead: each row only sees its own history.
with ohlcv as (
    select * from {{ ref('stg_ohlcv') }}
),

calc as (
    select
        ticker,
        date,
        close,
        adj_close,
        volume,
        (adj_close / lag(adj_close) over (partition by ticker order by date)) - 1
            as daily_return,
        max(adj_close) over (
            partition by ticker order by date
            rows between unbounded preceding and current row
        ) as rolling_peak_adj_close
    from ohlcv
)

select
    ticker,
    date,
    close,
    adj_close,
    volume,
    daily_return,
    (adj_close / rolling_peak_adj_close) - 1 as drawdown
from calc
