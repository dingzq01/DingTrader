-- ============================================================
-- TimescaleDB 板块日级别视图
-- ------------------------------------------------------------
-- block_daily_stats 已改为实体表（由 src/dashboard/views.py 管理），
-- 本文件只保留基于该表的视图。

-- 板块主线趋势视图
CREATE OR REPLACE VIEW block_mainline_view AS
WITH sector_ranked AS (
    SELECT
        block_code,
        trade_date,
        block_type,
        block_name,
        stock_count,
        up_count,
        down_count,
        change_pct AS weighted_pct_change,
        up_ratio AS up_down_ratio,
        limit_up_count,
        total_amount,
        RANK() OVER (
            PARTITION BY trade_date ORDER BY change_pct DESC
        ) AS daily_rank,
        RANK() OVER (
            PARTITION BY trade_date ORDER BY total_amount DESC
        ) AS volume_rank
    FROM dashboard.block_daily_stats
    WHERE exclude_filter = ''
),
sector_momentum AS (
    SELECT
        block_code,
        trade_date,
        daily_rank,
        volume_rank,
        weighted_pct_change,
        up_down_ratio,
        up_count,
        down_count,
        stock_count,
        limit_up_count,
        total_amount,
        SUM(weighted_pct_change) OVER (
            PARTITION BY block_code
            ORDER BY trade_date
            ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
        ) AS cum_return_5d,
        ROUND(AVG(up_down_ratio) OVER (
            PARTITION BY block_code
            ORDER BY trade_date
            ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
        ), 2) AS avg_updown_ratio_5d
    FROM sector_ranked
)
SELECT *
FROM sector_momentum
ORDER BY trade_date DESC, daily_rank ASC;