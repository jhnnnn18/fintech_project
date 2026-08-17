-- Fails if any row is dated after today (a data quality bug, not a valid trading day).
select *
from {{ ref('stg_ohlcv') }}
where date > current_date
