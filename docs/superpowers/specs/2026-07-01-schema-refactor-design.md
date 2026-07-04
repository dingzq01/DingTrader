# 2026-07-01 方案B重构设计：Schema-First 全量重建（无兼容）

## 1) 背景与目标

基于 `tmp/项目背景.md` 与现有 `src` 框架，用户要求：
- 不保留旧版本兼容历史结果
- 可以重拉历史数据
- 保持 `src` 结构为主骨架
- 优先保证重构后代码语法正确并可运行
- 回测先保留最小骨架

因此采用 **直接重构**（Schema-First）而非兼容改造：
- 先重建关系与指标表
- 统一写入新表结构
- 后续策略、聚合、评分按新表驱动

## 2) 本阶段范围（Phase 1）

### 2.1 包含
- 数据同步链路重构（市场筛选、板块关系、K线抓取）
- 指标存储重构：从 `indicators_data` 拆分为 `stock_indicators` / `block_indicators`
- 板块聚合视图重构（`block_daily_stats`、`block_mainline_view`）
- 策略执行链对齐新指标表
- `scripts/init_db.py` 修复对象名并与新 SQL 同步
- 保持回测入口“可运行骨架”

### 2.2 不包含（本阶段不做）
- 与原历史结果一一回归（可由重跑验证）
- 全量交易执行/实盘闭环
- 极复杂主线评分（保留接口与基础字段）
- 强制高可观测性平台改造（日志/告警先用既有方案）

## 3) 新目录结构与模块职责

### 3.1 建议目录（沿用现有 `src` 分层）

- `src/data/`
  - `models.py`：定义新 ORM（`stock_block_relation`, `stock_indicators`, `block_indicators`）
  - `fetcher.py`：抓取 `stock_data`（新增代码过滤）
  - `downloader.py`：批量抓取编排
  - `sync_manager.py`：同步调度（板块元数据 + K线下载）
  - `continuous_aggregates.sql`：板块聚合 SQL

- `src/indicators/`
  - `registry.py`：保留注册机制
  - `indicator_service.py`（新增）：统一计算并入库新表

- `src/strategies/`
  - `base_strategy.py`：只读指标 + 结果入库策略级
  - `macd_golden_cross.py`、`limit_up_consolidation.py`、`obv_bottom_divergence.py`、`dark_bar_accumulation.py`

- `scripts/`
  - `init_db.py`：重建初始化、创建新表/视图、刷新物化视图
  - `sync_data.py`：同步 + 完整性检查入口
  - `run_strategy.py`：继续走 `stock-pool` 到 `TQ板块` 写入
  - `verify_strategy.py`：改为按“同一候选池+同一期数据口径”比对
  - `run_backtest.py`：保留最小可运行框架

### 3.2 保留但不重构的原因

- `src/backtest/*` 保留现有最小框架。
- `src/tq_bridge/*` 保持对接层，不变更 SDK 边界。
- `src/notification/*` 保持告警链路，供后续接入。

## 4) 数据库设计（阶段一）

### 4.1 目标表

#### 4.1.1 个股日线：`stock_data`（保留）
- `code` / `trade_date` 为联合主键（原有）
- `name`, `open`, `high`, `low`, `close`, `volume`, `amount` 必须齐全
- 新增可选：`change_pct`, `turnover`（后续可补，不阻塞首版）

#### 4.1.2 板块关系：`stock_block_relation`（新增替代）
- `block_code`（主代码）
- `block_name`（名称）
- `stock_code`
- `stock_name`
- `block_type`（industry/concept）
- `is_active`（默认 true）
- `updated_at`
- 唯一约束：`(block_code, stock_code)`

#### 4.1.3 个股指标：`stock_indicators`
- `stock_code`
- `trade_date`
- `indicator_name`
- `indicator_value`
- `created_at`
- 主键：`(stock_code, trade_date, indicator_name)`

#### 4.1.4 板块指标：`block_indicators`
- `block_code`
- `trade_date`
- `indicator_name`
- `indicator_value`
- `created_at`
- 主键：`(block_code, trade_date, indicator_name)`

### 4.2 表名与脚本一致化

- SQL 与初始化脚本统一使用：
  - `block_daily_stats`
  - `block_mainline_view`
- 取消旧命名混淆（如 `sector_daily_stats`）

### 4.3 初始化与重建策略

- 本阶段不做兼容迁移。
- 初始化步骤：
  1) `init_db()` 创建/重建表与 hypertable
  2) 执行 `continuous_aggregates.sql`
  3) `REFRESH MATERIALIZED VIEW block_daily_stats`

## 5) 指标计算框架设计

### 5.1 原则

- 指标只在 `indicators/*` 中计算
- 策略层不再内置技术指标核心计算（仅消费）
- 所有策略入选必须有统一 `compute_indicators()` 数据源

### 5.2 `indicator_service` 结构

- 输入：`stock_code`, `trade_date`、可选窗口
- 输出：`dict[str, float]`
- 写入：`stock_indicators`
- 当日重复运行：按 `ON CONFLICT`（或先删后插）覆盖

### 5.3 与现有策略对齐

- `compute_for_stock` 保留兼容调用方式，内部切换到新表写入；逐步支持 block level 扩展
- `BaseStrategy.select()`：
  - 拉K线
  - 拉指标
  - 校验条件
  - 写入策略结果（若需要）

## 6) 板块聚合框架（核心看板数据）

### 6.1 聚合 SQL 改造方向

- `stock_data` 与 `stock_block_relation` 进行关联计算
- `block_daily_stats` 继续作为物化视图（先保证日更新可控）

### 6.2 首版字段（必备）

- `stock_count`, `up_count`, `down_count`, `flat_count`
- `limit_up_count`, `limit_down_count`
- `avg_pct_change`, `weighted_pct_change`
- `up_down_ratio`
- `total_amount_yi`, `total_volume_wan`
- 索引：`(block_code, trade_date)` 唯一索引 + 按日期降序索引

### 6.3 扩展预留（后续）

- `first_board_count`, `second_board_count`, `break_limit_count`, `limit_hold_ratio`, `heat_score`, `activity_score`, `leader_count` 等字段留出扩展位

## 7) 策略框架改造

### 7.1 入口兼容但内部切源

- `scripts/run_strategy.py` 保留参数风格（name/stock-pool/block）
- 候选股来源先保留 TQ 自定义板块，或切换 DB 选股池（后续）

### 7.2 单策略行为

- 策略类保留注册风格（`@register_strategy`）
- 所有策略从 `stock_indicators` 读取指标
- 条件判断保持现有风格，避免一次性重写

### 7.3 暂未实现项

- `dark_bar_accumulation` 保持占位不阻断主流程
- 回测策略路由先不扩展，保留基础骨架

## 8) 验证与数据质量

### 8.1 `sync_data.py` 完整性

- 返回每板块“期望股票数/已抓取数/缺失数”
- 直接使用 `stock_block_relation + stock_data` 进行分组比对，不再全量 stock_code 粗比

### 8.2 结果验证

- `verify_strategy.py`：
  - TQ 侧继续用于结果对照接口
  - Python 侧使用 `BaseStrategy` 输出集合
  - 先输出集合差异、交集、差异占比（不追求历史一致性，只做流程自检）

## 9) 风险与注意点

- 全量重拉会增加首次同步耗时
- 板块关系质量（TQ 返回数据稳定性）影响聚合质量
- 材料化视图刷新时间需与收盘后的调度对齐
- `block_indicators` 首版先不塞太多字段，避免复杂 SQL 过早过载

## 10) 交付顺序（每步确认）

1. 数据库模型与初始化（`models.py`, `continuous_aggregates.sql`, `scripts/init_db.py`）
2. 数据同步与过滤（`fetcher.py`, `downloader.py`, `sync_manager.py`, `downloader/fetcher`）
3. 指标入库服务（`indicators/indicator_service.py`）+ `BaseStrategy` 对齐
4. 聚合 SQL 与视图刷新链验证（`continuous_aggregates.sql`, `init_db.py`）
5. 策略入口自检（`run_strategy.py`, `verify_strategy.py`）
6. 回测脚本最小运行确认（`run_backtest.py`）

---

## 11) 设计确认点

请确认以下是否可以开始：
1) 采用 `stock_block_relation` + `stock_indicators` + `block_indicators` 的新表结构
2) 聚合看板先保留 `block_daily_stats` 物化视图（不启用 continuous aggregate）
3) 先保留 `dark_bar_accumulation` 为占位，不阻塞
4) 回测仅保持最小可运行，不做优化器扩展