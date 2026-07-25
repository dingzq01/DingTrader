import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from src.config.settings import get_settings


class Base(DeclarativeBase):
    pass


class StockBlockRelation(Base):
    """板块-个股关系主链路。"""

    __tablename__ = "stock_block_relation"
    __table_args__ = (
        UniqueConstraint("block_code", "stock_code", name="uq_stock_block_relation"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    block_code = Column(String(20), nullable=False, index=True)
    block_name = Column(String(100), nullable=False)
    stock_code = Column(String(10), nullable=False, index=True)
    stock_name = Column(String(50))
    block_type = Column(String(20), nullable=False)
    is_active = Column(Integer, default=1)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)


class StockData(Base):
    """个股日K线 (TimescaleDB hypertable)"""

    __tablename__ = "stock_data"

    code = Column(String(10), primary_key=True, index=True)
    name = Column(String(50), nullable=False)
    trade_date = Column(Date, primary_key=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    amount = Column(Float)
    change_pct = Column(Float)
    turnover = Column(Float)


class BlockData(Base):
    """板块日K线 (TimescaleDB hypertable)"""

    __tablename__ = "block_data"

    code = Column(String(20), primary_key=True, index=True)
    name = Column(String(100))
    trade_date = Column(Date, primary_key=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    amount = Column(Float)


class StockIndicators(Base):
    """个股指标表 (EAV 模式 — 每个指标值一行，新增指标无需改表)。"""

    __tablename__ = "stock_indicators"

    stock_code = Column(String(10), primary_key=True)
    trade_date = Column(Date, primary_key=True)
    indicator_name = Column(String(50), primary_key=True)
    indicator_value = Column(Float)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)


class BlockIndicators(Base):
    """板块指标表 (EAV 模式 — 每个指标值一行，新增指标无需改表)。"""

    __tablename__ = "block_indicators"

    block_code = Column(String(20), primary_key=True)
    trade_date = Column(Date, primary_key=True)
    indicator_name = Column(String(50), primary_key=True)
    indicator_value = Column(Float)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)


class BlockStatDaily(Base):
    """板块每日统计事实表 (TimescaleDB hypertable)。

    保存客观统计数据（Fact），不保存评分/策略结果。
    market/industry/concept 三类板块统一口径：仅统计沪深主板A股。
    """

    __tablename__ = "block_stat_daily"

    trade_date = Column(Date, primary_key=True)
    block_code = Column(String(32), primary_key=True)
    block_name = Column(String(100), nullable=False)
    block_type = Column(String(20), nullable=False)

    # 成分股
    stock_count = Column(Integer, nullable=False)
    active_stock_count = Column(Integer, nullable=False)

    # 涨跌统计
    avg_change_pct = Column(Float)
    median_change_pct = Column(Float)
    max_change_pct = Column(Float)
    min_change_pct = Column(Float)
    std_change_pct = Column(Float)
    up_count = Column(Integer)
    down_count = Column(Integer)
    flat_count = Column(Integer)
    up_ratio = Column(Float)
    down_ratio = Column(Float)

    # 极端行情
    limit_up_count = Column(Integer)
    limit_down_count = Column(Integer)
    gt_5_count = Column(Integer)
    lt_minus_5_count = Column(Integer)

    # 成交统计
    volume = Column(Float)
    amount = Column(Float)
    avg_turnover = Column(Float)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class StockIndicatorDaily(Base):
    """个股每日技术指标表 (TimescaleDB hypertable)。

    Indicator Layer — 仅保存根据历史K线计算得到的连续值技术指标。
    不保存状态(True/False)、评分(Score)、策略逻辑、买卖信号。
    数据来源：stock_data。
    """

    __tablename__ = "stock_indicator_daily"

    trade_date = Column(Date, primary_key=True)
    stock_code = Column(String(16), primary_key=True)

    # 成交量均线
    volume_ma5 = Column(Float)
    volume_ma10 = Column(Float)
    volume_ma20 = Column(Float)

    # MA
    ma5 = Column(Float)
    ma10 = Column(Float)
    ma20 = Column(Float)
    ma30 = Column(Float)
    ma60 = Column(Float)
    ma120 = Column(Float)
    ma250 = Column(Float)

    # EMA
    ema5 = Column(Float)
    ema10 = Column(Float)
    ema20 = Column(Float)
    ema30 = Column(Float)
    ema60 = Column(Float)
    ema120 = Column(Float)
    ema250 = Column(Float)

    # MACD(21,55,13)
    macd_dif = Column(Float)
    macd_dea = Column(Float)
    macd_hist = Column(Float)

    # OBV(20)
    obv = Column(Float)
    obv_ma20 = Column(Float)

    # KDJ(21,5,5)
    k_value = Column(Float)
    d_value = Column(Float)
    j_value = Column(Float)

    # 主力做多做空资金线
    capital_fast = Column(Float)
    capital_slow = Column(Float)

    # 个股资金生命线
    capital_life = Column(Float)
    capital_life_ma = Column(Float)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class StockStateDaily(Base):
    """个股每日技术状态表 (TimescaleDB hypertable)。

    State Layer — 将 stock_indicator_daily 的连续值转换为离散布尔状态。
    不保存评分、不保存策略逻辑、不保存指标原始值。
    数据来源：stock_indicator_daily + stock_data（仅 close/volume/change_pct）。
    """

    __tablename__ = "stock_state_daily"

    trade_date = Column(Date, primary_key=True)
    stock_code = Column(String(16), primary_key=True)

    # 趋势状态
    price_above_ma5 = Column(Boolean)
    price_above_ma20 = Column(Boolean)
    price_above_ma60 = Column(Boolean)
    ma5_above_ma20 = Column(Boolean)
    ma20_above_ma60 = Column(Boolean)
    trend_short_bull = Column(Boolean)
    trend_mid_bull = Column(Boolean)

    # MACD 状态
    macd_bullish = Column(Boolean)
    macd_golden_cross = Column(Boolean)
    macd_dead_cross = Column(Boolean)
    macd_hist_positive = Column(Boolean)
    macd_hist_increasing = Column(Boolean)

    # KDJ 状态
    kdj_golden_cross = Column(Boolean)
    kdj_over_buy = Column(Boolean)
    kdj_over_sell = Column(Boolean)

    # 成交量状态
    volume_expand = Column(Boolean)
    volume_shrink = Column(Boolean)
    price_volume_confirm = Column(Boolean)

    # OBV 资金状态
    obv_above_ma20 = Column(Boolean)
    obv_rising = Column(Boolean)
    obv_price_divergence = Column(Boolean)

    # 主力资金状态
    capital_bullish = Column(Boolean)
    capital_cross_up = Column(Boolean)
    capital_life_up = Column(Boolean)

    # 突破状态
    high_break_20 = Column(Boolean)
    high_break_60 = Column(Boolean)
    new_high = Column(Boolean)

    # 风险状态
    extreme_up = Column(Boolean)
    extreme_down = Column(Boolean)
    high_volatility = Column(Boolean)

    state_version = Column(String(20), default="v1.0")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class StockFactorDaily(Base):
    """个股每日因子评分表 (TimescaleDB hypertable)。

    Factor Layer — 将 stock_state_daily 的布尔状态组合成因子评分。
    不保存因子公式、权重配置、状态规则。
    数据来源：stock_state_daily + stock_block_relation（仅排名计算）。
    """

    __tablename__ = "stock_factor_daily"

    trade_date = Column(Date, primary_key=True)
    stock_code = Column(String(16), primary_key=True)

    # 因子评分
    trend_score = Column(Float)
    momentum_score = Column(Float)
    capital_score = Column(Float)
    volume_price_score = Column(Float)
    breakout_score = Column(Float)

    # 风险扣分
    risk_penalty = Column(Float)

    # 综合评分
    total_score = Column(Float)

    # 排名
    market_rank = Column(Integer)
    block_rank = Column(Integer)

    factor_version = Column(String(20), default="v1.0")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


def get_engine(dsn: str | None = None):
    settings = get_settings()
    url = dsn or settings.database.dsn
    return create_engine(url, pool_size=5, max_overflow=10)


def get_session(engine=None):
    eng = engine or get_engine()
    return sessionmaker(bind=eng)()


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    return bool(
        conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = :table_name AND column_name = :column_name"
            ),
            {"table_name": table_name, "column_name": column_name},
        ).scalar()
    )


def _table_exists(conn, table_name: str) -> bool:
    return bool(
        conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = current_schema() "
                "AND table_name = :table_name"
            ),
            {"table_name": table_name},
        ).scalar()
    )


def _ensure_pk(conn, table_name: str, columns: tuple[str, ...], constraint_name: str):
    conn.execute(text(f"ALTER TABLE IF EXISTS {table_name} DROP CONSTRAINT IF EXISTS {constraint_name}"))
    cols = ", ".join(columns)
    conn.execute(
        text(
            f"ALTER TABLE IF EXISTS {table_name} "
            f"ADD CONSTRAINT {constraint_name} PRIMARY KEY ({cols})"
        )
    )


def _drop_legacy_sector_tables(conn):
    for table_name in ("sector_stocks", "sectors"):
        conn.execute(text(f"DROP TABLE IF EXISTS {table_name} CASCADE"))




def _fill_stock_name_from_relation(conn):
    # Use block metadata first, then fallback to code.
    conn.execute(
        text(
            "UPDATE stock_data "
            "SET name = sbr.stock_name "
            "FROM stock_block_relation sbr "
            "WHERE stock_data.code = sbr.stock_code "
            "AND (stock_data.name IS NULL OR TRIM(stock_data.name) = '') "
            "AND sbr.stock_name IS NOT NULL "
            "AND TRIM(sbr.stock_name) <> ''"
        )
    )
    conn.execute(
        text(
            "UPDATE stock_data "
            "SET name = code "
            "WHERE name IS NULL OR TRIM(name) = ''"
        )
    )


def _ensure_not_null_stock_name(conn):
    conn.execute(text("ALTER TABLE IF EXISTS stock_data ALTER COLUMN name SET NOT NULL"))


def _migrate_legacy_indicator_tables(conn):
    """检测旧固定列 indicator 表并自动迁移为 EAV 模式。"""
    # stock_indicators: 旧表有 macd 列 → DROP 重建
    if _table_exists(conn, "stock_indicators") and _column_exists(conn, "stock_indicators", "macd"):
        conn.execute(text("DROP TABLE IF EXISTS stock_indicators CASCADE"))
    # block_indicators: 旧表有 up_count 列 → DROP 重建
    if _table_exists(conn, "block_indicators") and _column_exists(conn, "block_indicators", "up_count"):
        conn.execute(text("DROP TABLE IF EXISTS block_indicators CASCADE"))


def init_db(engine=None):
    """Create all tables and convert TimescaleDB hypertables."""
    eng = engine or get_engine()
    Base.metadata.create_all(eng)
    with eng.connect() as conn:
        _drop_legacy_sector_tables(conn)
        _migrate_legacy_indicator_tables(conn)

        # 清理旧 dashboard schema（已由 block_stat_daily 替代）
        conn.execute(text("DROP TABLE IF EXISTS dashboard.block_daily_stats CASCADE"))
        conn.execute(text("DROP SCHEMA IF EXISTS dashboard CASCADE"))

        # 旧表被 DROP 后，重新创建新 EAV 表
        Base.metadata.create_all(eng)

        if _table_exists(conn, "stock_data"):
            for col_name in ("change_pct", "turnover"):
                if not _column_exists(conn, "stock_data", col_name):
                    conn.execute(text(
                        f"ALTER TABLE stock_data ADD COLUMN {col_name} FLOAT"
                    ))

        if _table_exists(conn, "stock_block_relation"):
            _fill_stock_name_from_relation(conn)
            _ensure_not_null_stock_name(conn)

        _ensure_pk(conn, "stock_block_relation", ("block_code", "stock_code"), "stock_block_relation_pkey")
        _ensure_pk(conn, "stock_indicators", ("stock_code", "trade_date", "indicator_name"), "stock_indicators_pkey")
        _ensure_pk(conn, "block_indicators", ("block_code", "trade_date", "indicator_name"), "block_indicators_pkey")
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
        conn.execute(
            text(
                "SELECT create_hypertable('stock_data', 'trade_date', "
                "chunk_time_interval => INTERVAL '1 month', "
                "if_not_exists => TRUE)"
            )
        )
        conn.execute(
            text(
                "SELECT create_hypertable('block_data', 'trade_date', "
                "chunk_time_interval => INTERVAL '1 month', "
                "if_not_exists => TRUE)"
            )
        )
        conn.execute(
            text(
                "SELECT create_hypertable('stock_indicators', 'trade_date', "
                "chunk_time_interval => INTERVAL '1 month', "
                "if_not_exists => TRUE)"
            )
        )
        conn.execute(
            text(
                "SELECT create_hypertable('block_indicators', 'trade_date', "
                "chunk_time_interval => INTERVAL '1 month', "
                "if_not_exists => TRUE)"
            )
        )
        conn.execute(
            text(
                "SELECT create_hypertable('block_stat_daily', 'trade_date', "
                "chunk_time_interval => INTERVAL '1 month', "
                "if_not_exists => TRUE)"
            )
        )
        # 建立 block_stat_daily 索引
        for idx_col, idx_name in [
            ("trade_date", "idx_block_stat_date"),
            ("block_type", "idx_block_stat_type"),
            ("block_code", "idx_block_stat_code"),
        ]:
            conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS {idx_name} "
                f"ON block_stat_daily ({idx_col})"
            ))

        # stock_indicator_daily hypertable
        conn.execute(
            text(
                "SELECT create_hypertable('stock_indicator_daily', 'trade_date', "
                "chunk_time_interval => INTERVAL '1 month', "
                "if_not_exists => TRUE)"
            )
        )
        # 建立 stock_indicator_daily 索引
        for idx_col, idx_name in [
            ("trade_date", "idx_indicator_date"),
            ("stock_code", "idx_indicator_code"),
        ]:
            conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS {idx_name} "
                f"ON stock_indicator_daily ({idx_col})"
            ))

        # stock_state_daily hypertable
        conn.execute(
            text(
                "SELECT create_hypertable('stock_state_daily', 'trade_date', "
                "chunk_time_interval => INTERVAL '1 month', "
                "if_not_exists => TRUE)"
            )
        )
        # 建立 stock_state_daily 索引
        for idx_col, idx_name in [
            ("trade_date", "idx_state_date"),
            ("stock_code", "idx_state_code"),
        ]:
            conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS {idx_name} "
                f"ON stock_state_daily ({idx_col})"
            ))

        # stock_factor_daily hypertable
        conn.execute(
            text(
                "SELECT create_hypertable('stock_factor_daily', 'trade_date', "
                "chunk_time_interval => INTERVAL '1 month', "
                "if_not_exists => TRUE)"
            )
        )
        # 建立 stock_factor_daily 索引
        for idx_col, idx_name in [
            ("trade_date", "idx_factor_date"),
            ("stock_code", "idx_factor_code"),
            ("total_score", "idx_factor_total_score"),
        ]:
            conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS {idx_name} "
                f"ON stock_factor_daily ({idx_col})"
            ))
        conn.commit()
