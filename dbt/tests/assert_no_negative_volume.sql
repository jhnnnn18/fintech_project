-- Fails if any row has negative volume, which is not physically possible.
select *
from {{ ref('stg_ohlcv') }}
where volume < 0
