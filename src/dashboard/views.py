"""Grafana 看板数据库函数。

提供 dashboard.block_daily() 函数 — 从个股数据动态聚合板块指标，
支持前缀过滤排除科创板/创业板等个股。
"""

from sqlalchemy import text


BLOCK_DAILY_FUNCTION_SQL = """
CREATE SCHEMA IF NOT EXISTS dashboard;

CREATE OR REPLACE FUNCTION dashboard.block_daily(
    p_exclude_prefixes text DEFAULT ''
)
RETURNS TABLE(
    trading_day   date,
    block_type    text,
    name          text,
    change_pct    double precision,
    up_ratio      double precision,
    limit_up      bigint,
    volume_ratio  double precision,
    total_amount  double precision,
    main_net      double precision,
    price         double precision,
    turnover_rate double precision
) LANGUAGE plpgsql STABLE AS $$
BEGIN
    RETURN QUERY
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
        WHERE (p_exclude_prefixes = '' OR p_exclude_prefixes IS NULL
               OR sd.code NOT LIKE ANY(
                   SELECT (unnest(string_to_array(p_exclude_prefixes, ',')) || '%')::text
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
            ROUND(
                SUM((swb.close - swb.prev_close) / swb.prev_close * 100 * swb.amount)
                / NULLIF(SUM(swb.amount), 0)::numeric,
                2
            ) AS change_pct_val,
            ROUND(
                COUNT(*) FILTER (WHERE swb.close > swb.prev_close)::numeric
                / NULLIF(COUNT(*) FILTER (WHERE swb.close < swb.prev_close), 0)::numeric,
                2
            ) AS up_ratio_val,
            COUNT(*) FILTER (
                WHERE (swb.close - swb.prev_close) / swb.prev_close >= 0.099
            ) AS limit_up_val,
            ROUND(
                SUM(swb.volume) / NULLIF(SUM(swb.prev_volume), 0)::numeric,
                2
            ) AS volume_ratio_val,
            ROUND(SUM(swb.amount)::numeric, 2) AS total_amount_val,
            NULL::double precision AS main_net_val,
            ROUND(AVG(swb.close)::numeric, 2) AS price_val,
            NULL::double precision AS turnover_rate_val
        FROM stock_with_block swb
        GROUP BY swb.block_code, swb.trade_date, swb.block_type, swb.block_name
    )
    SELECT
        bm.trade_date,
        bm.block_type,
        bm.block_name,
        bm.change_pct_val,
        bm.up_ratio_val,
        bm.limit_up_val,
        bm.volume_ratio_val,
        bm.total_amount_val,
        bm.main_net_val,
        bm.price_val,
        bm.turnover_rate_val
    FROM block_metrics bm;
END;
$$;
"""


def init_dashboard_views(engine_or_conn) -> None:
    """在数据库中创建 dashboard schema 和 block_daily 函数。

    可在 init_db() 末尾调用，也可独立通过 engine 调用。
    """
    if hasattr(engine_or_conn, "execute"):
        conn = engine_or_conn
        conn.execute(text(BLOCK_DAILY_FUNCTION_SQL))
    else:
        with engine_or_conn.connect() as conn:
            conn.execute(text(BLOCK_DAILY_FUNCTION_SQL))
            conn.commit()