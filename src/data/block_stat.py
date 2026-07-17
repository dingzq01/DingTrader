"""板块每日统计计算模块。

从 stock_data + stock_block_relation 聚合计算 block_stat_daily：
- industry/concept: JOIN relation 后 GROUP BY trade_date, block_code
- market: 直接对 stock_data 聚合（block_code='ALL'）

仅统计沪深主板A股 (code LIKE '0%' OR '6%')，ST 已在下载阶段剔除。
"""

from sqlalchemy import text

from src.utils.logging import get_logger

logger = get_logger(__name__)

# 过滤主板A股
_MAIN_FILTER = "(sd.code LIKE '0%' OR sd.code LIKE '6%')"

# ON CONFLICT DO UPDATE 列
_UPDATE_COLS = (
    "block_name = EXCLUDED.block_name, "
    "stock_count = EXCLUDED.stock_count, "
    "active_stock_count = EXCLUDED.active_stock_count, "
    "avg_change_pct = EXCLUDED.avg_change_pct, "
    "median_change_pct = EXCLUDED.median_change_pct, "
    "max_change_pct = EXCLUDED.max_change_pct, "
    "min_change_pct = EXCLUDED.min_change_pct, "
    "std_change_pct = EXCLUDED.std_change_pct, "
    "up_count = EXCLUDED.up_count, "
    "down_count = EXCLUDED.down_count, "
    "flat_count = EXCLUDED.flat_count, "
    "up_ratio = EXCLUDED.up_ratio, "
    "down_ratio = EXCLUDED.down_ratio, "
    "limit_up_count = EXCLUDED.limit_up_count, "
    "limit_down_count = EXCLUDED.limit_down_count, "
    "gt_5_count = EXCLUDED.gt_5_count, "
    "lt_minus_5_count = EXCLUDED.lt_minus_5_count, "
    "volume = EXCLUDED.volume, "
    "amount = EXCLUDED.amount, "
    "avg_turnover = EXCLUDED.avg_turnover, "
    "created_at = EXCLUDED.created_at"
)

# 聚合字段 (跨 industry/concept 和 market 共用)
_AGG_SELECT_LIST = [
    "COUNT(DISTINCT sd.code) AS stock_count",
    "COUNT(sd.code) AS active_stock_count",
    "AVG(sd.change_pct) AS avg_change_pct",
    "PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY sd.change_pct) AS median_change_pct",
    "MAX(sd.change_pct) AS max_change_pct",
    "MIN(sd.change_pct) AS min_change_pct",
    "STDDEV(sd.change_pct) AS std_change_pct",
    "SUM(CASE WHEN sd.change_pct > 0 THEN 1 ELSE 0 END) AS up_count",
    "SUM(CASE WHEN sd.change_pct < 0 THEN 1 ELSE 0 END) AS down_count",
    "SUM(CASE WHEN sd.change_pct = 0 THEN 1 ELSE 0 END) AS flat_count",
    (
        "CASE WHEN COUNT(sd.code) > 0 "
        "THEN SUM(CASE WHEN sd.change_pct > 0 THEN 1 ELSE 0 END)::float / COUNT(sd.code) "
        "ELSE 0 END AS up_ratio"
    ),
    (
        "CASE WHEN COUNT(sd.code) > 0 "
        "THEN SUM(CASE WHEN sd.change_pct < 0 THEN 1 ELSE 0 END)::float / COUNT(sd.code) "
        "ELSE 0 END AS down_ratio"
    ),
    # 已剔除ST，统一用 >=9.8% 涨停阈值
    "SUM(CASE WHEN sd.change_pct >= 9.8 THEN 1 ELSE 0 END) AS limit_up_count",
    "SUM(CASE WHEN sd.change_pct <= -9.8 THEN 1 ELSE 0 END) AS limit_down_count",
    "SUM(CASE WHEN sd.change_pct >= 5 THEN 1 ELSE 0 END) AS gt_5_count",
    "SUM(CASE WHEN sd.change_pct <= -5 THEN 1 ELSE 0 END) AS lt_minus_5_count",
    "SUM(sd.volume) AS volume",
    "SUM(sd.amount) AS amount",
    "AVG(sd.turnover) AS avg_turnover",
    "CURRENT_TIMESTAMP AS created_at",
]

_AGG_FIELDS = ",\n    ".join(_AGG_SELECT_LIST)

# --- 插入 SQL ---

_BLOCK_INSERT_SQL = f"""
INSERT INTO block_stat_daily (
    trade_date, block_code, block_name, block_type,
    stock_count, active_stock_count,
    avg_change_pct, median_change_pct, max_change_pct, min_change_pct, std_change_pct,
    up_count, down_count, flat_count, up_ratio, down_ratio,
    limit_up_count, limit_down_count, gt_5_count, lt_minus_5_count,
    volume, amount, avg_turnover, created_at
)
SELECT
    sd.trade_date,
    sbr.block_code,
    sbr.block_name,
    sbr.block_type,
    {_AGG_FIELDS}
FROM stock_data sd
JOIN stock_block_relation sbr ON sd.code = sbr.stock_code
WHERE sd.trade_date > :last_date
  AND {_MAIN_FILTER}
GROUP BY sd.trade_date, sbr.block_code, sbr.block_name, sbr.block_type
ON CONFLICT (trade_date, block_code) DO UPDATE SET
    {_UPDATE_COLS}
"""

_MARKET_INSERT_SQL = f"""
INSERT INTO block_stat_daily (
    trade_date, block_code, block_name, block_type,
    stock_count, active_stock_count,
    avg_change_pct, median_change_pct, max_change_pct, min_change_pct, std_change_pct,
    up_count, down_count, flat_count, up_ratio, down_ratio,
    limit_up_count, limit_down_count, gt_5_count, lt_minus_5_count,
    volume, amount, avg_turnover, created_at
)
SELECT
    sd.trade_date,
    'ALL' AS block_code,
    '全市场' AS block_name,
    'market' AS block_type,
    {_AGG_FIELDS}
FROM stock_data sd
WHERE sd.trade_date > :last_date
  AND {_MAIN_FILTER}
GROUP BY sd.trade_date
ON CONFLICT (trade_date, block_code) DO UPDATE SET
    {_UPDATE_COLS}
"""


def compute_block_stat_daily(engine) -> int:
    """增量刷新 block_stat_daily 表。

    从 stock_data + stock_block_relation 计算行业/概念板块统计，
    并对全市场 (market, block_code='ALL') 单独聚合。

    Returns: 本次新增/更新的总行数（industry+concept + market）。
    """
    with engine.connect() as conn:
        # 1. 获取 block_stat_daily 已有最新日期
        last_date = conn.execute(
            text(
                "SELECT COALESCE(MAX(trade_date), '2026-01-01'::date) "
                "FROM block_stat_daily"
            )
        ).scalar()

        # 2. 获取 stock_data 最新日期
        latest_stock = conn.execute(
            text("SELECT MAX(trade_date) FROM stock_data")
        ).scalar()

        if latest_stock is None:
            logger.warning("compute_block_stat_skipped_no_stock_data")
            return 0

        if last_date >= latest_stock:
            logger.info(
                "block_stat_up_to_date",
                last_stat_date=str(last_date),
                latest_stock_date=str(latest_stock),
            )
            return 0

        logger.info(
            "compute_block_stat_start",
            from_date=str(last_date),
            to_date=str(latest_stock),
        )

        total_rows = 0
        params = {"last_date": last_date}

        # 3. 行业 + 概念板块聚合
        result = conn.execute(text(_BLOCK_INSERT_SQL), params)
        block_rows = result.rowcount
        total_rows += block_rows
        logger.info("block_stat_industry_concept_completed", rows=block_rows)

        # 4. 全市场聚合 (block_code='ALL')
        result = conn.execute(text(_MARKET_INSERT_SQL), params)
        market_rows = result.rowcount
        total_rows += market_rows
        logger.info("block_stat_market_completed", rows=market_rows)

        conn.commit()
        logger.info("compute_block_stat_completed", total_rows=total_rows)
        return total_rows