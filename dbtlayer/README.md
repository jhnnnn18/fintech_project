# Market Data Pipeline

A small, honest fintech data pipeline: daily OHLCV ingestion → dbt models →
a backtested strategy → a dashboard, scheduled and tested in CI.

Built as a portfolio project — the goal is a clean, working repo, not an
overstated backtest. See [Status](#status) and [Assumptions & limitations](#assumptions--limitations)
below.

## Status

- [x] **Week 1 — Ingestion**: daily OHLCV → DuckDB, idempotent, logged
- [x] **Week 2 — dbt transformation layer** (staging → intermediate → marts)
- [ ] **Week 3 — Backtest** (moving-average crossover vs. buy-and-hold)
- [ ] **Week 4 — Ship** (GitHub Actions schedule + D3.js dashboard on GitHub Pages)

## Architecture (current)

```
yfinance  --->  src/ingest.py  --->  DuckDB (raw_ohlcv)
                 (idempotent upsert on ticker+date)
                        |
                        v
                 dbt: staging (stg_ohlcv)
                        |
                        v
             dbt: intermediate (int_daily_returns, int_rolling_metrics)
                        |
                        v
              dbt: marts (fct_daily_metrics)
```

## Universe

25 tickers, configured in [`config/tickers.yaml`](config/tickers.yaml):
5 sector ETFs (XLK, XLF, XLE, XLV, XLY) + 20 large, recognizable stocks
(the Dow-adjacent names — AAPL, MSFT, JPM, etc.). Add tickers to that file
at any time; ingestion picks up new entries on the next run without
touching existing data.

Data source is `yfinance` (no API key required) rather than a brokerage
API — a deliberate choice for a public portfolio repo, since it means
there are no credentials to manage or accidentally commit.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
# Ingest the full configured universe, 2021-01-01 through today
python -m src.ingest

# Override date range or ticker file
python -m src.ingest --start 2021-01-01 --end 2026-08-15
```

Data lands in `data/market_data.duckdb` (gitignored — this is a local
cache, not a source of truth; re-running ingestion rebuilds it).

Re-running ingestion is safe: `raw_ohlcv` is keyed on `(ticker, date)`,
so re-fetching an already-loaded range updates those rows in place
(handling vendor restatements of adjusted close) instead of duplicating
them.

## Tests

```bash
pytest
```

Tests cover the upsert logic (insert, idempotent re-run, refresh-on-conflict,
multi-ticker isolation) against a temp DuckDB file — no network calls, so
they run the same in CI as locally.

## dbt models

Layered `staging -> intermediate -> marts`, all reading from and writing to
the same DuckDB file `src/ingest.py` populates — no separate warehouse to
run or configure.

```bash
cd dbt
DBT_PROFILES_DIR=. dbt seed    # loads config/tickers.yaml's ticker -> asset_type/sector lookup
DBT_PROFILES_DIR=. dbt run
DBT_PROFILES_DIR=. dbt test
DBT_PROFILES_DIR=. dbt docs generate && DBT_PROFILES_DIR=. dbt docs serve
```

- **`stg_ohlcv`** (staging): typed/cleaned OHLCV, one row per `(ticker, date)`,
  with a concatenated surrogate key.
- **`int_daily_returns`** (intermediate): daily return and drawdown-from-
  rolling-peak, computed on `adj_close` (not `close`) so stock splits and
  dividends don't masquerade as price moves — this matters for Week 3, where
  a naive `close`-based signal could fire a false moving-average crossover
  on a split date.
- **`int_rolling_metrics`** (intermediate): 20/50/200-day moving averages and
  20-day annualized realized volatility (`stdev * sqrt(252)`), also on
  `adj_close`.
- **`fct_daily_metrics`** (mart): the one table a dashboard or backtest
  would actually query — joins the above plus a `ticker_reference` seed
  (asset type, sector) at `(ticker, date)` grain.

Every model has `not_null` tests on its key columns; the mart and seed carry
`accepted_values` tests (`asset_type` in `['etf', 'stock']`); two custom
singular tests enforce no future-dated rows and no negative volume; a third
enforces no duplicate `(ticker, date)` rows in the mart, since it's a
three-way join. 28 tests total, all passing.

**Known limitation**: moving averages and volatility near the start of each
ticker's history are computed over a partial window (e.g. the 200-day MA
has only a few days of real history behind it on day 5) — intentional and
documented rather than hidden, since backfilling with padded/fake data would
be worse. `ticker_reference.csv` is a static seed mirroring
`config/tickers.yaml`; adding a custom ticker there means updating both
files.

## Assumptions & limitations

- **Survivorship bias**: the universe is today's large caps, not a
  point-in-time index membership list. A stock that was removed from an
  index over the 5-year window wouldn't appear here. Acceptable for a
  portfolio-scale demo; called out explicitly because it would matter for
  anything real.
- **Data source**: `yfinance` is unofficial and rate-limited; it's not a
  production-grade market data feed. Fine for this project's scope.
- Further limitations (backtest assumptions, transaction costs, etc.) will
  be documented in Week 3 once that work lands.

## Stack

Python, DuckDB, dbt-core + dbt-duckdb, GitHub Actions (Week 4), D3.js on
GitHub Pages (Week 4 dashboard). See repo for current dependencies in
`requirements.txt`.
