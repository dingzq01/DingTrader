-- ============================================================
-- TimescaleDB 板块日级别聚合视图（新 schema 合约）
-- ------------------------------------------------------------

-- 1) block_daily_stats 物化视图
CREATE MATERIALIZED VIEW IF NOT EXISTS block_daily_stats AS
WITH stock_daily_pct AS (
    SELECT
        sd.code AS stock_code,
        sd.trade_date,
        sd.close,
        sd.open,
        sd.volume,
        sd.amount,
        LAG(sd.close) OVER (
            PARTITION BY sd.code ORDER BY sd.trade_date
        ) AS prev_close
    FROM stock_data sd
),
stock_with_block AS (
    SELECT
        sdp.stock_code,
        sdp.trade_date,
        sdp.close,
        sdp.open,
        sdp.volume,
        sdp.amount,
        sdp.prev_close,
        sbr.block_code,
        sbr.block_name,
        sbr.block_type
    FROM stock_daily_pct sdp
    JOIN stock_block_relation sbr
        ON sdp.stock_code = sbr.stock_code
    WHERE sdp.prev_close IS NOT NULL
      AND sdp.prev_close > 0
)
SELECT
    block_code,
    trade_date,
    COUNT(DISTINCT stock_code) AS stock_count,
    COUNT(DISTINCT stock_code) FILTER (WHERE close > prev_close) AS up_count,
    COUNT(DISTINCT stock_code) FILTER (WHERE close < prev_close) AS down_count,
    COUNT(DISTINCT stock_code) FILTER (WHERE close = prev_close) AS flat_count,
    COUNT(DISTINCT stock_code) FILTER (
        WHERE (close - prev_close) / prev_close >= 0.099
    ) AS limit_up_count,
    COUNT(DISTINCT stock_code) FILTER (
        WHERE (close - prev_close) / prev_close <= -0.099
    ) AS limit_down_count,
    ROUND(AVG((close - prev_close) / prev_close * 100)::numeric, 2) AS avg_pct_change,
    ROUND(
        SUM((close - prev_close) / prev_close * 100 * amount)
        / NULLIF(SUM(amount), 0)::numeric,
        2
    ) AS weighted_pct_change,
    ROUND(
        COUNT(DISTINCT stock_code) FILTER (WHERE close > prev_close)::numeric
        / NULLIF(COUNT(DISTINCT stock_code) FILTER (WHERE close < prev_close), 0)::numeric,
        2
    ) AS up_down_ratio,
    ROUND(SUM(amount)::numeric / 100000000, 2) AS total_amount_yi,
    ROUND(SUM(volume)::numeric / 10000, 2) AS total_volume_wan
FROM stock_with_block
GROUP BY block_code, trade_date
ORDER BY trade_date DESC, weighted_pct_change DESC;

-- 2) 板块主线趋势视图
CREATE OR REPLACE VIEW block_mainline_view AS
WITH sector_ranked AS (
    SELECT
        block_code,
        trade_date,
        avg_pct_change,
        weighted_pct_change,
        up_down_ratio,
        up_count,
        down_count,
        stock_count,
        limit_up_count,
        total_amount_yi,
        RANK() OVER (
            PARTITION BY trade_date ORDER BY weighted_pct_change DESC
        ) AS daily_rank,
        RANK() OVER (
            PARTITION BY trade_date ORDER BY total_amount_yi DESC
        ) AS amount_rank
    FROM block_daily_stats
),
sector_momentum AS (
    SELECT
        block_code,
        trade_date,
        daily_rank,
        amount_rank,
        weighted_pct_change,
        up_down_ratio,
        up_count,
        down_count,
        stock_count,
        limit_up_count,
        total_amount_yi,
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

-- 3) 兼容后续指标链路（当前用于 SQL 合约约束）
-- stock_indicators
-- block_indicators
CREATE INDEX IF NOT EXISTS idx_block_daily_stats
    ON block_daily_stats (block_code, trade_date);

CREATE INDEX IF NOT EXISTS idx_block_daily_stats_date
    ON block_daily_stats (trade_date DESC);
