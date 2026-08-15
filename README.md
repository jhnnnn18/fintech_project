# Market Data Pipeline

A small, honest fintech data pipeline: daily OHLCV ingestion → dbt models →
a backtested strategy → a dashboard, scheduled and tested in CI.

Built as a portfolio project — the goal is a clean, working repo, not an
overstated backtest. See [Status](#status) and [Assumptions & limitations](#assumptions--limitations)
below.

## Status

- [x] **Week 1 — Ingestion**: daily OHLCV → DuckDB, idempotent, logged
- [ ] **Week 2 — dbt transformation layer** (staging → intermediate → marts)
- [ ] **Week 3 — Backtest** (moving-average crossover vs. buy-and-hold)
- [ ] **Week 4 — Ship** (GitHub Actions schedule + dashboard)

## Architecture (current)

```
yfinance  --->  src/ingest.py  --->  DuckDB (raw_ohlcv)
                 (idempotent upsert on ticker+date)
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

Python, DuckDB, dbt (Week 2+), GitHub Actions (Week 4). See repo for
current dependencies in `requirements.txt`.
