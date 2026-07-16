"""Grafana 看板数据落表 + 增量刷新。

- init_dashboard_views() 创建 dashboard schema 和 block_daily_stats 表
- refresh_block_daily_stats() 从 stock_data 聚合板块指标，自动补全最新日期
"""

from sqlalchemy import text

from src.utils.logging import get_logger

logger = get_logger(__name__)

BLOCK_DAILY_TABLE_DDL = """
CREATE SCHEMA IF NOT EXISTS dashboard;

-- drop old function if it exists (migrated to table)
DROP FUNCTION IF EXISTS dashboard.block_daily(text);

CREATE TABLE IF NOT EXISTS dashboard.block_daily_stats (
    block_code      text NOT NULL,
    trade_date      date NOT NULL,
    block_type      text,
    block_name      text,
    exclude_filter  text NOT NULL DEFAULT '',
    stock_count     integer,
    up_count        integer,
    down_count      integer,
    flat_count      integer,
    change_pct      double precision,
    up_ratio        double precision,
    limit_up_count  integer,
    volume_ratio    double precision,
    total_amount    double precision,
    avg_price       double precision,
    updated_at      timestamp DEFAULT now(),
    PRIMARY KEY (block_code, trade_date, exclude_filter)
);
"""

INSERT_BLOCK_DAILY_SQL = """
INSERT INTO dashboard.block_daily_stats (
    block_code, trade_date, block_type, block_name, exclude_filter,
    stock_count, up_count, down_count, flat_count,
    change_pct, up_ratio, limit_up_count, volume_ratio,
    total_amount, avg_price
)
WITH stock_pct AS (
    SELECT
        sd.code,
        sd.trade_date,
        sd.close,
        sd.volume,
        sd.amount,
        LAG(sd.close) OVER (
            PARTITION BY sd.code ORDER BY sd.trade_date
        ) AS prev_close,
        LAG(sd.volume) OVER (
            PARTITION BY sd.code ORDER BY sd.trade_date
        ) AS prev_volume
    FROM stock_data sd
    WHERE sd.trade_date > :last_table_date
      AND sd.trade_date <= :latest_stock_date
      AND (:exclude_filter = '' OR :exclude_filter IS NULL
           OR sd.code NOT LIKE ANY(
               SELECT (unnest(string_to_array(:exclude_filter, ',')) || '%')::text
           ))
),
stock_with_block AS (
    SELECT
        sp.*,
        sbr.block_code,
        sbr.block_name,
        sbr.block_type
    FROM stock_pct sp
    JOIN stock_block_relation sbr ON sp.code = sbr.stock_code
    WHERE sp.prev_close IS NOT NULL AND sp.prev_close > 0
),
block_metrics AS (
    SELECT
        swb.block_code,
        swb.trade_date,
        swb.block_type,
        swb.block_name,
        COUNT(DISTINCT swb.code) AS stock_count,
        COUNT(DISTINCT swb.code) FILTER (WHERE swb.close > swb.prev_close) AS up_count,
        COUNT(DISTINCT swb.code) FILTER (WHERE swb.close < swb.prev_close) AS down_count,
        COUNT(DISTINCT swb.code) FILTER (WHERE swb.close = swb.prev_close) AS flat_count,
        ROUND(
            (SUM((swb.close - swb.prev_close) / swb.prev_close * 100 * swb.amount)
            / NULLIF(SUM(swb.amount), 0))::numeric,
            2
        ) AS change_pct,
        ROUND(
            COUNT(*) FILTER (WHERE swb.close > swb.prev_close)::numeric
            / NULLIF(COUNT(*) FILTER (WHERE swb.close < swb.prev_close), 0)::numeric,
            2
        ) AS up_ratio,
        COUNT(*) FILTER (
            WHERE (swb.close - swb.prev_close) / swb.prev_close >= 0.099
        ) AS limit_up_count,
        ROUND(
            (SUM(swb.volume) / NULLIF(SUM(swb.prev_volume), 0))::numeric,
            2
        ) AS volume_ratio,
        ROUND(SUM(swb.amount)::numeric, 2) AS total_amount,
        ROUND(AVG(swb.close)::numeric, 2) AS avg_price
    FROM stock_with_block swb
    GROUP BY swb.block_code, swb.trade_date, swb.block_type, swb.block_name
)
SELECT
    bm.block_code,
    bm.trade_date,
    bm.block_type,
    bm.block_name,
    :exclude_filter,
    bm.stock_count,
    bm.up_count,
    bm.down_count,
    bm.flat_count,
    bm.change_pct,
    bm.up_ratio,
    bm.limit_up_count,
    bm.volume_ratio,
    bm.total_amount,
    bm.avg_price
FROM block_metrics bm
ON CONFLICT (block_code, trade_date, exclude_filter) DO UPDATE SET
    block_type = EXCLUDED.block_type,
    block_name = EXCLUDED.block_name,
    stock_count = EXCLUDED.stock_count,
    up_count = EXCLUDED.up_count,
    down_count = EXCLUDED.down_count,
    flat_count = EXCLUDED.flat_count,
    change_pct = EXCLUDED.change_pct,
    up_ratio = EXCLUDED.up_ratio,
    limit_up_count = EXCLUDED.limit_up_count,
    volume_ratio = EXCLUDED.volume_ratio,
    total_amount = EXCLUDED.total_amount,
    avg_price = EXCLUDED.avg_price,
    updated_at = now()
"""


def init_dashboard_views(engine_or_conn) -> None:
    """创建 dashboard schema 和 block_daily_stats 实体表。

    可在 init_db() 末尾调用，也可独立通过 engine 调用。
    """
    if hasattr(engine_or_conn, "execute"):
        conn = engine_or_conn
        conn.execute(text(BLOCK_DAILY_TABLE_DDL))
    else:
        with engine_or_conn.connect() as conn:
            conn.execute(text(BLOCK_DAILY_TABLE_DDL))
            conn.commit()


def refresh_block_daily_stats(engine, exclude_filter: str = "") -> int:
    """增量刷新板块日聚合表 — 自动补全最新日期到 stock_data 最新日期。

    Args:
        engine: SQLAlchemy engine
        exclude_filter: 排除前缀，如 '688,689'。'' 表示全量。

    Returns:
        本次插入/更新的行数。
    """
    with engine.connect() as conn:
        # 1. 获取表中已有最新日期
        last_table = conn.execute(
            text(
                "SELECT COALESCE(MAX(trade_date), '2026-01-01'::date) "
                "FROM dashboard.block_daily_stats "
                "WHERE exclude_filter = :filt"
            ),
            {"filt": exclude_filter},
        ).scalar()

        # 2. 获取 stock_data 最新日期
        latest_stock = conn.execute(
            text("SELECT MAX(trade_date) FROM stock_data")
        ).scalar()

        if latest_stock is None:
            logger.warning("refresh_dashboard_skipped_no_stock_data")
            return 0

        if last_table >= latest_stock:
            logger.info(
                "refresh_dashboard_up_to_date",
                last_table=str(last_table),
                latest_stock=str(latest_stock),
            )
            return 0

        # 3. 执行增量插入
        logger.info(
            "refresh_dashboard_start",
            from_date=str(last_table),
            to_date=str(latest_stock),
            exclude_filter=exclude_filter or "(all)",
        )

        result = conn.execute(
            text(INSERT_BLOCK_DAILY_SQL),
            {
                "last_table_date": last_table,
                "latest_stock_date": latest_stock,
                "exclude_filter": exclude_filter,
            },
        )
        conn.commit()
        row_count = result.rowcount
        logger.info("refresh_dashboard_completed", rows=row_count)
        return row_count