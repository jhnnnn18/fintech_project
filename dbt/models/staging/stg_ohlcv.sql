with source as (
    select * from {{ source('raw', 'raw_ohlcv') }}
)

select
    ticker || '-' || cast(date as varchar) as ohlcv_id,
    ticker,
    date,
    open,
    high,
    low,
    close,
    adj_close,
    volume
from source
