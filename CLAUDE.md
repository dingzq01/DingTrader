# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DingTrader is a Chinese A-stock quantitative trading system. It ingests market data from 通达信 (TDX) TQ API into TimescaleDB, runs stock selection strategies, backtests with Backtrader, and visualizes results in Grafana.

## Architecture

```
src/
├── config/settings.py    # Pydantic-based config (YAML + env vars)
├── data/                  # Data ingestion: fetcher, downloader, sync_manager, models
├── tq_bridge/             # 通达信 TQ API encapsulation (client, sector, market_data)
├── indicators/            # Indicator computation (MACD, limit_up, volume) with registry
├── strategies/            # Stock selection with registry pattern, base strategy
├── backtest/              # Backtrader engine: DataFeed, commission, sizer, optimizer
├── notification/feishu.py # Feishu (Lark) webhook messaging
└── utils/                 # structlog setup, retry decorator
scripts/                   # CLI entry points
tmp/                       # Original reference scripts (to be deprecated)
```

## Key Commands

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Copy and edit config
cp config.yaml.template config.yaml

# Sync all sector/stock data to TimescaleDB
python scripts/sync_data.py

# Check data integrity (no download)
python scripts/sync_data.py --check-integrity

# Run a single stock selection strategy
python scripts/run_strategy.py --name=macd_golden_cross

# Run all enabled strategies
python scripts/run_strategy.py

# Verify strategy TQ vs Python consistency
python scripts/verify_strategy.py --name=macd_golden_cross

# Run backtest
python scripts/run_backtest.py --stock=000001 --from=2023-01-01 --to=2024-01-01

# Run backtest with parameter optimization
python scripts/run_backtest.py --stock=000001 --optimize
```

## Config

Config is loaded from `config.yaml` (gitignored) with `config.yaml.template` as reference. Pydantic Settings reads YAML plus env vars prefixed `DT_` (e.g., `DT_DATABASE__HOST` for nested `database.host`).

## Database (TimescaleDB)

- **daily_kline**: Hypertable on `trade_date`, stores OHLCV per stock
- **sectors** / **sector_stocks**: Sector metadata and membership
- **stock_indicators**: EAV pattern — `(stock_code, trade_date, indicator_name, indicator_value)` — supports dynamic new indicators without schema changes
- **sector_daily_agg**: Sector-level daily aggregates (up/down/limit counts)
- **continuous_aggregates.sql**: Pre-computed materialized views for Grafana

## Strategy Development

To add a new strategy:
1. Subclass `BaseStrategy` and implement `check_conditions(df, indicators) -> (bool, dict|None)`
2. Decorate with `@register_strategy("name", description="...")`
3. Add strategy name to `strategies.enabled` in `config.yaml`

To add a new indicator:
1. Subclass `BaseIndicator` (set `name`, implement `compute(df, **params) -> dict`)
2. Call `register_indicator(instance)` at module level

## TQ (通达信) Dependency

TQ is the sole data source. It requires the local TDX installation at the configured path. All TQ calls go through `src/tq_bridge/` for isolation. The `TQClient` context manager handles initialize/close lifecycle.

## Conventions

- Comments and UI labels in Chinese
- structlog for structured logging (JSON to file, colored to console)
- `@retry_on_failure` decorator for external API calls
- Config never hardcoded — always accessed via `get_settings()`