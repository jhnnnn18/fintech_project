-- Fails if any (ticker, date) pair appears more than once in the mart —
-- the join fan-out this would catch is a real risk given three upstream
-- models are joined together here.
select ticker, date, count(*) as row_count
from {{ ref('fct_daily_metrics') }}
group by ticker, date
having count(*) > 1
