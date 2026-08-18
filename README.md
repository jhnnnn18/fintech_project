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
- [x] **Week 4 — Ship** (dashboard + GitHub Actions orchestration; see the
  default-branch caveat in [Orchestration](#orchestration))

## Architecture (current)

The whole chain below runs end to end on a GitHub Actions schedule (see
[Orchestration](#orchestration)):

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
                        |
                        v
        src/export_dashboard_data.py --> docs/data/*.json
                        |
                        v
           docs/index.html (D3.js, static, GitHub Pages)
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

## Dashboard

A static D3.js page (`docs/index.html`) — no server, no build step, no
framework. Reads two pre-computed JSON files rather than querying DuckDB
live, since GitHub Pages only serves static files:

```bash
python -m src.export_dashboard_data   # writes docs/data/*.json from the DuckDB tables
```

`docs/data/` is gitignored — it's generated output, not something to hand-edit
or commit, and is meant to be refreshed by the scheduled pipeline (Week 4's
remaining piece) rather than go stale in git history.

**What's on the page**: a KPI row (total return, Sharpe, max drawdown, win
rate, exposure, trade count — strategy vs. buy-and-hold), a plain HTML
table mirroring those same numbers (the accessible, non-chart twin of the
KPI row), an equity curve (both series indexed to $1, one shared axis —
never a dual-axis chart), and a strategy drawdown chart. Every chart has a
crosshair-driven tooltip; hover/focus works the same way via keyboard.
Dark mode follows the OS setting, same data, separately validated colors.

**Color**: the two series colors (strategy blue, benchmark orange) are
validated for colorblind-safety and contrast, not eyeballed — run
`node scripts/validate_palette.js "#2a78d6,#eb6834" --mode light` from a
checkout of the `dataviz` design-system skill this dashboard's palette
comes from. Both modes clear every check (worst-pair CVD ΔE 24.7 light /
26.8 dark against an 8-point target).

**Known limitation**: the equity/drawdown charts don't have a raw-data
table twin (hundreds of daily rows isn't practical as an inline table for
a 90-second-skim dashboard) — the KPI/summary numbers do, and every
individual value is still reachable via the chart tooltip. A disclosed
scope cut, not an oversight.

Tests (`tests/test_export_dashboard_data.py`) cover the export logic —
date formatting, NaN-to-null handling (JSON has no NaN; a zero-variance
Sharpe would otherwise break the page), drawdown math, and that a null
benchmark metric survives the round trip — against a temp DuckDB file, no
network.

## Orchestration

`.github/workflows/pipeline.yml` runs the whole chain end to end: ingest →
dbt seed/run/test → backtest → export → commit the refreshed
`docs/data/*.json` back to the repo. GitHub Actions gives every step full
internet access, unlike the sandboxed environment this was built in, where
Yahoo Finance and dbt's package registry were both blocked by network
policy — the CI run is the first place this pipeline actually touches real
market data end to end.

**Known caveat, disclosed rather than hidden**: the workflow's `schedule`
trigger only fires on the repository's **default branch** — while this
lives on a feature branch, only manual runs (the "Run workflow" button
under the Actions tab, using `workflow_dispatch`) actually execute it. The
cron activates once this branch is merged to `main`.

**Committing generated data back to the repo** (rather than, say, an
external database or object store) is a deliberate choice for a
portfolio-scale project: no infrastructure to provision, the dashboard's
data has a visible commit history, and GitHub Pages can serve it directly
with zero extra moving parts. The tradeoff is real — this doesn't scale to
high-frequency data or a large team pushing to the same branch — and is
fine at this project's size.

The DuckDB file itself is rebuilt from scratch on every run rather than
cached between runs (ingestion is idempotent, so this is correctness-free,
just a bit more bandwidth than an incremental fetch would use) — simpler
than wiring up cross-run cache restore for a dataset this small.

**Enabling GitHub Pages** (manual, one-time, in the repo's Settings on
GitHub.com — not something a workflow file can do on its own): Settings →
Pages → Source: "Deploy from a branch" → Branch: this branch → Folder:
`/docs` → Save.

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

Python, DuckDB, dbt-core + dbt-duckdb, pandas/numpy, D3.js on GitHub
Pages, GitHub Actions. See repo for current dependencies in
`requirements.txt`.
