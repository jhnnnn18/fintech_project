-- Grain: one row per (ticker, date). The dashboard/backtest-ready fact table
-- combining price, returns, drawdown, moving averages, and realized vol.
with returns as (
    select * from {{ ref('int_daily_returns') }}
),

rolling as (
    select * from {{ ref('int_rolling_metrics') }}
),

reference as (
    select * from {{ ref('ticker_reference') }}
)

select
    r.ticker,
    r.date,
    ref.asset_type,
    ref.sector,
    r.close,
    r.adj_close,
    r.volume,
    r.daily_return,
    r.drawdown,
    m.ma_20,
    m.ma_50,
    m.ma_200,
    m.realized_vol_20d_annualized
from returns r
left join rolling m
    on r.ticker = m.ticker and r.date = m.date
left join reference ref
    on r.ticker = ref.ticker
