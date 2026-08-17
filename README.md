# Market Data Pipeline

A small, honest fintech data pipeline: daily OHLCV ingestion → dbt models →
a backtested strategy → a dashboard, scheduled and tested in CI.

Built as a portfolio project — the goal is a clean, working repo, not an
overstated backtest. See [Status](#status) and [Assumptions & limitations](#assumptions--limitations)
below.

## Status

- [x] **Week 1 — Ingestion**: daily OHLCV → DuckDB, idempotent, logged
- [x] **Week 2 — dbt transformation layer** (staging → intermediate → marts)
- [x] **Week 3 — Backtest** (moving-average crossover vs. buy-and-hold)
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
                        |
                        v
              src/backtest.py (Python, not dbt --
              backtest state is inherently sequential)
                        |
                        v
     DuckDB (backtest_daily, backtest_trades, backtest_summary)
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

## Backtest

**Strategy**: long a ticker whenever its 20-day moving average is above its
50-day moving average (a "golden cross" state), flat otherwise. Long-only,
no shorting, no leverage. Equal-weighted across whatever tickers are
eligible on a given date. Benchmarked against buy-and-hold on the same
universe over the same date range. Deliberately simple — the rigor is in
the accounting around it, not the signal.

```bash
python -m src.backtest
```

Writes `backtest_daily` (per-date equity curves for strategy vs.
benchmark), `backtest_trades` (every round-trip trade, ticker/entry/exit/
return), and `backtest_summary` (total return, Sharpe, max drawdown, win
rate, trade count, exposure — strategy vs. benchmark) to the same DuckDB
file, via a full `CREATE OR REPLACE`, since backtest output is a
deterministic function of the marts, not an incremental external fetch
like ingestion.

**Honest-accounting choices**, each also called out as a code comment at
the point it's implemented in `src/backtest.py`:

- **No lookahead.** A day's *signal* (MA20 vs. MA50) is computed from that
  day's own close. The *position* held during a day is the **prior** day's
  signal — you can't trade on information before it exists. Concretely,
  `position = signal.shift(1)`.
- **No warm-up contamination.** A ticker's first 50 trading days are
  excluded from the backtest. dbt's moving averages use a partial window
  before that many observations exist (see the Week 2 known limitation
  above) — trading on a "50-day MA" built from 3 real days would be a
  spurious signal, not a real one. The buy-and-hold benchmark is evaluated
  over the identical post-warm-up date range, so the comparison stays
  apples-to-apples rather than giving the benchmark extra history.
- **Transaction costs.** 5 bps is charged on every position change (entry
  or exit) — a simplified stand-in for commission + slippage, not a
  brokerage-specific model. The benchmark pays this once, on its single
  entry.
- **Point-in-time correctness.** Every input comes from `fct_daily_metrics`,
  whose window functions only look backward (see `dbt/models/intermediate`)
  — there's nothing upstream of the backtest that could leak future data in.

`win_rate`, `num_trades`, and `exposure` are strategy-only metrics and show
as blank/NaN for the benchmark in `backtest_summary` — a buy-and-hold
position that never trades doesn't have a meaningful win rate, and showing
one anyway would be misleading rather than informative.

Tests (`tests/test_backtest.py`) exercise the mechanics above directly —
warm-up exclusion, the one-day signal lag, cost timing on entry vs. exit,
trade-boundary extraction (including a position still open at the end of
the window), and the metric formulas — against small hand-verifiable
synthetic series, no DuckDB or network involved.

## Assumptions & limitations

- **Survivorship bias**: the universe is today's large caps, not a
  point-in-time index membership list. A stock that was removed from an
  index over the 5-year window wouldn't appear here. Acceptable for a
  portfolio-scale demo; called out explicitly because it would matter for
  anything real.
- **Data source**: `yfinance` is unofficial and rate-limited; it's not a
  production-grade market data feed. Fine for this project's scope.
- **Backtest is not investment advice, and isn't tuned to look good.** MA
  lengths (20/50), the cost assumption (5 bps/leg), and the warm-up cutoff
  (50 days) are fixed, sensible defaults — not the result of parameter
  search against this universe. A strategy that underperforms buy-and-hold
  after costs is a legitimate, disclosed outcome here, not a bug.
- **Equal-weight portfolio construction** is a simplification; no
  position sizing, volatility targeting, or correlation-aware allocation
  across the universe.

## Stack

Python, DuckDB, dbt-core + dbt-duckdb, pandas/numpy, GitHub Actions
(Week 4), D3.js on GitHub Pages (Week 4 dashboard). See repo for current
dependencies in `requirements.txt`.
