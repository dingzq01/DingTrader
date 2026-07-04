import datetime

from sqlalchemy import (
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


class StockIndicators(Base):
    """个股指标新主表。"""

    __tablename__ = "stock_indicators"

    stock_code = Column(String(10), primary_key=True, index=True)
    trade_date = Column(Date, primary_key=True)
    macd = Column(Float)
    signal = Column(Float)
    hist = Column(Float)
    obv = Column(Float)
    obv_slope = Column(Float)
    volume_ma = Column(Float)
    limit_up_ratio = Column(Float)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)


class BlockIndicators(Base):
    """板块指标新主表。"""

    __tablename__ = "block_indicators"

    block_code = Column(String(20), primary_key=True, index=True)
    trade_date = Column(Date, primary_key=True)
    up_count = Column(Integer)
    down_count = Column(Integer)
    limit_up_count = Column(Integer)
    limit_down_count = Column(Integer)
    avg_pct_change = Column(Float)
    weighted_pct_change = Column(Float)
    up_down_ratio = Column(Float)
    total_volume_wan = Column(Float)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)


class IndicatorsData(Base):
    """兼容旧 schema：仍保留旧表映射，不作为主流程依赖。"""

    __tablename__ = "indicators_data"

    stock_code = Column(String(10), primary_key=True, index=True)
    trade_date = Column(Date, primary_key=True)
    indicator_name = Column(String(50), primary_key=True)
    indicator_value = Column(Float)


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


def _drop_legacy_amount_columns(conn):
    drops = (
        ("stock_data", "amount"),
        ("block_data", "amount"),
        ("block_indicators", "total_amount_yi"),
    )
    for table_name, column_name in drops:
        if _column_exists(conn, table_name, column_name):
            conn.execute(
                text(f"ALTER TABLE IF EXISTS {table_name} DROP COLUMN {column_name}")
            )


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


def init_db(engine=None):
    """Create all tables and convert TimescaleDB hypertables."""
    eng = engine or get_engine()
    Base.metadata.create_all(eng)
    with eng.connect() as conn:
        _drop_legacy_sector_tables(conn)
        _drop_legacy_amount_columns(conn)

        if _table_exists(conn, "stock_block_relation"):
            _fill_stock_name_from_relation(conn)
            _ensure_not_null_stock_name(conn)

        _ensure_pk(conn, "stock_block_relation", ("block_code", "stock_code"), "stock_block_relation_pkey")
        _ensure_pk(conn, "stock_indicators", ("stock_code", "trade_date"), "stock_indicators_pkey")
        _ensure_pk(conn, "block_indicators", ("block_code", "trade_date"), "block_indicators_pkey")
        _ensure_pk(conn, "indicators_data", ("stock_code", "trade_date", "indicator_name"), "indicators_data_pkey")

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
        conn.commit()
