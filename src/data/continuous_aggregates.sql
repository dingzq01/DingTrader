-- ============================================================
-- TimescaleDB 板块日级别聚合视图
-- 最小粒度: 日 (天)
-- 用途: Grafana 板块看板，用于确认主线
-- ============================================================

-- 1. 将 stock_data 转为 hypertable (部署时执行一次)
SELECT create_hypertable('stock_data', 'trade_date',
    chunk_time_interval => INTERVAL '1 month',
    if_not_exists => TRUE
);

-- 2. 为 stock_data 添加前收盘价列（用于计算涨跌幅，通过窗口函数效率更高但物化视图需要持久化）
--    此处用 LATERAL 或 自连接方式在物化视图中计算

-- ============================================================
-- 3. 板块日级别聚合物化视图（核心：主线确认用）
--    每个交易日每个板块一份汇总，天级刷新
-- ============================================================
CREATE MATERIALIZED VIEW IF NOT EXISTS block_daily_stats AS
WITH stock_daily_pct AS (
    SELECT
        dk.code,
        dk.trade_date,
        dk.close,
        dk.open,
        dk.volume,
        dk.amount,
        dk.high,
        dk.low,
        LAG(dk.close) OVER (
            PARTITION BY dk.code ORDER BY dk.trade_date
        ) AS prev_close
    FROM stock_data dk
),
stock_with_sector AS (
    SELECT
        ssp.code AS stock_code,
        ssp.trade_date,
        ssp.close,
        ssp.open,
        ssp.volume,
        ssp.amount,
        ssp.prev_close,
        ss.sector_code,
        s.name AS sector_name,
        s.sector_type
    FROM stock_daily_pct ssp
    JOIN sector_stocks ss ON ssp.code = ss.stock_code
    JOIN sectors s ON ss.sector_code = s.code
    WHERE ssp.prev_close IS NOT NULL AND ssp.prev_close > 0
)
SELECT
    sector_code,
    trade_date,

    -- 板块内个股数量
    COUNT(DISTINCT stock_code) AS stock_count,

    -- 涨跌平家数
    COUNT(DISTINCT stock_code) FILTER (WHERE close > prev_close)  AS up_count,
    COUNT(DISTINCT stock_code) FILTER (WHERE close < prev_close)  AS down_count,
    COUNT(DISTINCT stock_code) FILTER (WHERE close = prev_close)  AS flat_count,

    -- 涨停/跌停家数 (A股 ±10%)
    COUNT(DISTINCT stock_code) FILTER (
        WHERE (close - prev_close) / prev_close >= 0.099
    ) AS limit_up_count,
    COUNT(DISTINCT stock_code) FILTER (
        WHERE (close - prev_close) / prev_close <= -0.099
    ) AS limit_down_count,

    -- 板块均涨幅 (%)
    ROUND(AVG((close - prev_close) / prev_close * 100)::numeric, 2) AS avg_pct_change,

    -- 板块加权涨幅 (按成交额加权，更能反映主力方向)
    ROUND(
        SUM((close - prev_close) / prev_close * 100 * amount)
        / NULLIF(SUM(amount), 0)::numeric,
        2
    ) AS weighted_pct_change,

    -- 板块涨跌比 (up/down ratio，>1 偏多)
    ROUND(
        COUNT(DISTINCT stock_code) FILTER (WHERE close > prev_close)::numeric
        / NULLIF(COUNT(DISTINCT stock_code) FILTER (WHERE close < prev_close), 0)::numeric,
        2
    ) AS up_down_ratio,

    -- 板块总成交额 (亿)
    ROUND(SUM(amount)::numeric / 100000000, 2) AS total_amount_yi,

    -- 板块总成交量 (万手)
    ROUND(SUM(volume)::numeric / 10000, 2) AS total_volume_wan

FROM stock_with_sector
GROUP BY sector_code, trade_date
ORDER BY trade_date DESC, weighted_pct_change DESC;

-- ============================================================
-- 4. 板块主线趋势视图（确认主线用）
--    显示板块连续上涨天数、近N日累计涨幅、量能趋势
-- ============================================================
CREATE OR REPLACE VIEW block_mainline_view AS
WITH sector_ranked AS (
    SELECT
        sector_code,
        trade_date,
        avg_pct_change,
        weighted_pct_change,
        up_down_ratio,
        up_count,
        down_count,
        stock_count,
        limit_up_count,
        total_amount_yi,
        -- 板块内涨幅排名（在所有板块中）
        RANK() OVER (
            PARTITION BY trade_date ORDER BY weighted_pct_change DESC
        ) AS daily_rank,
        -- 板块成交额排名
        RANK() OVER (
            PARTITION BY trade_date ORDER BY total_amount_yi DESC
        ) AS amount_rank
    FROM block_daily_stats
),
sector_momentum AS (
    SELECT
        sector_code,
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
        -- 近5日累计涨幅
        SUM(weighted_pct_change) OVER (
            PARTITION BY sector_code
            ORDER BY trade_date
            ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
        ) AS cum_return_5d,
        -- 近5日平均涨跌比
        ROUND(AVG(up_down_ratio) OVER (
            PARTITION BY sector_code
            ORDER BY trade_date
            ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
        ), 2) AS avg_updown_ratio_5d
    FROM sector_ranked
)
SELECT * FROM sector_momentum
ORDER BY trade_date DESC, daily_rank ASC;

-- ============================================================
-- 5. 创建唯一索引（加速 Grafana 查询）
-- ============================================================
CREATE UNIQUE INDEX IF NOT EXISTS idx_block_daily_stats
    ON block_daily_stats (sector_code, trade_date);

CREATE INDEX IF NOT EXISTS idx_block_daily_stats_date
    ON block_daily_stats (trade_date DESC);

CREATE INDEX IF NOT EXISTS idx_block_daily_stats_rank
    ON block_daily_stats (trade_date DESC, weighted_pct_change DESC);

-- ============================================================
-- 6. 刷新策略（每日一次，盘后刷新当日数据即可）
--    手动刷新方式: REFRESH MATERIALIZED VIEW block_daily_stats;
--    或用 TimescaleDB 的 refresh policy：
-- ============================================================
-- 注意: block_daily_stats 是普通物化视图（非 continuous aggregate），
-- 建议用 crontab 或 Python 定时任务在每天收盘后（如 15:30）执行:
--   REFRESH MATERIALIZED VIEW CONCURRENTLY block_daily_stats;
--
-- 如果希望用 TimescaleDB 自动刷新，可改为 continuous aggregate:
--   CREATE MATERIALIZED VIEW block_daily_stats_cont
--   WITH (timescaledb.continuous) AS ...
--   但需注意 continuous aggregate 不支持窗口函数和复杂聚合，
--   因此当前设计为普通物化视图 + 定时刷新的方式更灵活。