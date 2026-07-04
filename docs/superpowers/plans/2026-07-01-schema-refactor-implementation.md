# Schema-Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按方案B完成 `src` 主框架下的无兼容Schema重构：用 `stock_block_relation`、`stock_indicators`、`block_indicators` 替换旧模型链路，修正同步、聚合与策略验证流程。

**Architecture:** 以现有分层不变：`data` 负责抓取与关系同步，`indicators` 负责计算并入库，`strategies` 只消费新指标表，`scripts` 负责调度和入口。先保证最小可运行，再逐步扩展。

**Tech Stack:** Python 3.11, SQLAlchemy 2, pandas, TimescaleDB(PostgreSQL), pytest, ruff。

---

### Task 1: 对齐数据库模型与初始化脚本

**Files:**
- Modify: `src/data/models.py`
- Modify: `src/data/continuous_aggregates.sql`
- Modify: `scripts/init_db.py`
- Test: `tests/test_data_models_and_sql.py`

- [ ] **Step 1: Write the failing test**
```python
from pathlib import Path
from sqlalchemy import inspect

from src.data.models import Base, StockBlockRelation, StockIndicators, BlockIndicators


def test_refactor_models_exist_in_metadata():
    model_names = {m.__tablename__ for m in Base.registry.mappers}
    assert StockBlockRelation.__tablename__ in model_names
    assert StockIndicators.__tablename__ in model_names
    assert BlockIndicators.__tablename__ in model_names
```

```python
def test_continuous_aggregates_uses_new_table_names_and_indexes():
    sql = Path("src/data/continuous_aggregates.sql").read_text(encoding="utf-8")
    assert "stock_block_relation" in sql
    assert "stock_indicators" in sql
    assert "block_indicators" in sql
    assert "block_daily_stats" in sql
    assert "sector_daily_stats" not in sql
```

- [ ] **Step 2: Run test to make sure it fails**
Run: `pytest tests/test_data_models_and_sql.py -v`
Expected: FAIL due to missing refactored ORM classes and SQL naming mismatches.

- [ ] **Step 3: Write the minimal code**
- `src/data/models.py`：
  - 新增 `StockBlockRelation`、`StockIndicators`、`BlockIndicators` ORM。
  - 保留 `StockData`、`BlockData`。
  - 移除 `Sector`、`SectorStock`、`IndicatorsData` 的新主流程依赖。
  - `init_db()` 保留 Timescale hypertable 初始化逻辑。
- `src/data/continuous_aggregates.sql`：
  - 将关联来源改为 `stock_block_relation`，保持 `block_daily_stats` 物化视图及 `block_mainline_view`。
  - 补齐索引要求（`(block_code, trade_date)` 唯一/主索引 + 日期降序索引）。
- `scripts/init_db.py`：
  - SQL 刷新语句改为 `REFRESH MATERIALIZED VIEW block_daily_stats`。

- [ ] **Step 4: Run test to make sure it passes**
Run: `pytest tests/test_data_models_and_sql.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**
```bash
git add src/data/models.py src/data/continuous_aggregates.sql scripts/init_db.py tests/test_data_models_and_sql.py
git commit -m "feat: add refactored schema models and align init SQL"
```

### Task 2: 重建数据同步链路与市场筛选

**Files:**
- Modify: `src/data/fetcher.py`
- Modify: `src/data/downloader.py`
- Modify: `src/data/sync_manager.py`
- Modify: `scripts/sync_data.py`
- Test: `tests/test_sync_market_filter.py`
- Test: `tests/test_sync_integrity.py`

- [ ] **Step 1: Write the failing test**
```python
def test_market_filter_excludes_non_target_codes():
    assert is_target_market("000001") is True   # 上证
    assert is_target_market("300750") is True   # 创业板
    assert is_target_market("688001") is False  # 科创板
    assert is_target_market("430001") is False  # 北交所(4/8开头)
```

```python
def test_integrity_uses_sector_relation_as_expected_set():
    report = check_data_integrity_payload(sample_sector_df, sample_db_stock_data)
    assert report["个股-硬科技"]["expected"] == 3
    assert report["个股-硬科技"]["actual_in_db"] == 2
    assert report["个股-硬科技"]["missing_count"] == 1
```

- [ ] **Step 2: Run test to make sure it fails**
Run: `pytest tests/test_sync_market_filter.py tests/test_sync_integrity.py -v`
Expected: FAIL because market filter/关系口径未实现。

- [ ] **Step 3: Write the minimal code**
- `src/data/fetcher.py`：新增市场过滤辅助函数，按目标市场规则过滤；新增 `download_stock_kline` 对股票代码与 `amount`、`name` 写入一致性。
- `src/data/downloader.py`：保留批量下载接口，但按批次控制下载间隔（读取配置 `request_interval_seconds`）。
- `src/data/sync_manager.py`：
  - `sync_sector_metadata` 改写为使用 `StockBlockRelation`（字段 `block_code`,`block_name`,`stock_code`,`stock_name`,`block_type`,`is_active`,`updated_at`）。
  - `check_data_integrity` 改为按板块名分组对照 `stock_block_relation + stock_data`。
- `scripts/sync_data.py`：完整性模式打印每板块 `expected/actual_in_db/missing_count`。

- [ ] **Step 4: Run test to make sure it passes**
Run: `pytest tests/test_sync_market_filter.py tests/test_sync_integrity.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**
```bash
git add src/data/fetcher.py src/data/downloader.py src/data/sync_manager.py scripts/sync_data.py tests/test_sync_market_filter.py tests/test_sync_integrity.py
git commit -m "feat: rebuild sync flow with target market filtering and block relation"
```

### Task 3: 新增指标入库服务并对齐策略读取

**Files:**
- Add: `src/indicators/indicator_service.py`
- Modify: `src/indicators/registry.py`
- Modify: `src/strategies/base_strategy.py`
- Test: `tests/test_indicator_service.py`

- [ ] **Step 1: Write the failing test**
```python
def test_indicator_service_compute_then_store_upserts_stock_indicators(session):
    service = IndicatorService(session)
    df = pd.DataFrame({"trade_date":[...], "close":[...], "volume":[...]})
    service.compute_and_store("000001", pd.to_datetime("2026-01-01").date(), df)
    assert session.query(StockIndicators).count() > 0
```

- [ ] **Step 2: Run test to make sure it fails**
Run: `pytest tests/test_indicator_service.py -v`
Expected: FAIL because service missing and table target incorrect.

- [ ] **Step 3: Write the minimal code**
- 新增 `src/indicators/indicator_service.py`：
  - 提供 `compute_and_store_stock_indicators(session, stock_code, trade_date, df)`。
  - 结果写入 `stock_indicators`，存在则更新，不存在则插入。
- `src/indicators/registry.py`：保留原 `compute_for_stock` 兼容 API。
- `src/strategies/base_strategy.py`：
  - `select()` 先取 K线，尝试读取已存在 `stock_indicators`（按 trade_date）
  - 如缺失才回退到 `compute_for_stock`（开发期容错）并由 service 入库。

- [ ] **Step 4: Run test to make sure it passes**
Run: `pytest tests/test_indicator_service.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**
```bash
git add src/indicators/indicator_service.py src/indicators/registry.py src/strategies/base_strategy.py tests/test_indicator_service.py
git commit -m "feat: add indicator_service and route strategy ingestion to stock_indicators"
```

### Task 4: 聚合视图与入口刷新链路联调

**Files:**
- Modify: `src/data/continuous_aggregates.sql`
- Modify: `scripts/init_db.py`
- Test: `tests/test_aggregate_sql_contract.py`

- [ ] **Step 1: Write the failing test**
```python
def test_block_daily_stats_uses_relation_and_has_key_fields():
    sql = Path("src/data/continuous_aggregates.sql").read_text(encoding="utf-8")
    assert "JOIN stock_block_relation" in sql
    assert "stock_count" in sql
    assert "up_down_ratio" in sql
    assert "total_amount_yi" in sql
```

- [ ] **Step 2: Run test to make sure it fails**
Run: `pytest tests/test_aggregate_sql_contract.py -v`
Expected: FAIL due to existing old SQL contract.

- [ ] **Step 3: Write the minimal code**
- `src/data/continuous_aggregates.sql`：按方案B字段完成首版必选字段。
- `scripts/init_db.py`：保持“创建表 -> 执行SQL -> `REFRESH MATERIALIZED VIEW block_daily_stats`”。

- [ ] **Step 4: Run test to make sure it passes**
Run: `pytest tests/test_aggregate_sql_contract.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**
```bash
git add src/data/continuous_aggregates.sql scripts/init_db.py tests/test_aggregate_sql_contract.py
git commit -m "feat: align aggregate SQL contract with block relation and indicator tables"
```

### Task 5: 策略执行与对比验证脚本对齐

**Files:**
- Modify: `scripts/run_strategy.py`
- Modify: `scripts/verify_strategy.py`
- Test: `tests/test_run_strategy_entry.py`
- Test: `tests/test_verify_strategy_compare.py`

- [ ] **Step 1: Write the failing test**
```python
def test_run_strategy_returns_candidates_with_selected_codes():
    result = collect_strategy_outputs(["macd_golden_cross"], ["000001", "000002"], fake_settings)
    assert isinstance(result, list)
```

```python
def test_verify_strategy_uses_same_stock_pool_scope():
    tq = {"a", "b", "c"}
    py = {"b", "c", "d"}
    report = compare_result_sets(tq, py)
    assert report["both"] == {"b", "c"}
```

- [ ] **Step 2: Run test to make sure it fails**
Run: `pytest tests/test_run_strategy_entry.py tests/test_verify_strategy_compare.py -v`
Expected: FAIL because verify entry currently不满足同池对比语义。

- [ ] **Step 3: Write the minimal code**
- `scripts/run_strategy.py`：保留当前 CLI 风格（name/stock-pool/block）不变，逻辑改为新 `BaseStrategy.select` 输出。
- `scripts/verify_strategy.py`：
  - 同一候选池：`stock_pool_block` 与 Python/TQ 侧一致口径。
  - 输出 intersection / A-only / B-only / agreement。

- [ ] **Step 4: Run test to make sure it passes**
Run: `pytest tests/test_run_strategy_entry.py tests/test_verify_strategy_compare.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**
```bash
git add scripts/run_strategy.py scripts/verify_strategy.py tests/test_run_strategy_entry.py tests/test_verify_strategy_compare.py
git commit -m "feat: align strategy run + verify flows with unified candidate scope"
```

### Task 6: 回测最小骨架确认可运行

**Files:**
- Modify: `scripts/run_backtest.py`
- Test: `tests/test_run_backtest_smoke.py`

- [ ] **Step 1: Write the failing test**
```python
def test_run_backtest_parses_args_and_dispatches_base_flow():
    assert parse_args(["--stock", "000001"]).stock == "000001"
```

- [ ] **Step 2: Run test to make sure it fails**
Run: `pytest tests/test_run_backtest_smoke.py -v`
Expected: FAIL where parser helper not yet可抽离。

- [ ] **Step 3: Write the minimal code**
- 为最小可运行性抽离参数解析函数。
- 确保调用默认 `BaseBacktestStrategy` 的路径稳定，不引入新回测接口。

- [ ] **Step 4: Run test to make sure it passes**
Run: `pytest tests/test_run_backtest_smoke.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**
```bash
git add scripts/run_backtest.py tests/test_run_backtest_smoke.py
git commit -m "chore: keep run_backtest minimal entry runnable"
```

### Task 7: 端到端冒烟与收敛

**Files:**
- Test: `tests/test_schema_refactor_smoke.py`

- [ ] **Step 1: Write the failing test**
```python
def test_smoke_pipeline_contract():
    assert Path("scripts/init_db.py").exists()
    assert Path("scripts/sync_data.py").exists()
    assert Path("scripts/run_strategy.py").exists()
    assert Path("scripts/verify_strategy.py").exists()
```

- [ ] **Step 2: Run test to make sure it fails**
Run: `pytest tests/test_schema_refactor_smoke.py -v`
Expected: PASS by default; adjust as needed for environment-dependent checks.

- [ ] **Step 3: Write minimal implementation**
- 补充 `smoke` 测试只做文件/函数存在性与命令入口参数基本可解析校验，避免数据库环境耦合。

- [ ] **Step 4: Run test to make sure it passes**
Run: `pytest tests/test_schema_refactor_smoke.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**
```bash
git add tests/test_schema_refactor_smoke.py
git commit -m "test: add schema-refactor smoke checks for script entrypoints"
```

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-01-schema-refactor-implementation.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session with executing-plans, batch execution with checkpoints.

