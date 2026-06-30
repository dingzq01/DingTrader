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


class Sector(Base):
    """概念板块 / 行业板块"""

    __tablename__ = "sectors"

    code = Column(String(20), primary_key=True)
    name = Column(String(100), nullable=False)
    sector_type = Column(String(20), nullable=False)  # concept / industry
    stock_count = Column(Integer, default=0)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)


class SectorStock(Base):
    """板块-个股 关联"""

    __tablename__ = "sector_stocks"
    __table_args__ = (
        UniqueConstraint("sector_code", "stock_code", name="uq_sector_stock"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    sector_code = Column(String(20), nullable=False, index=True)
    stock_code = Column(String(10), nullable=False, index=True)
    stock_name = Column(String(50))
    added_at = Column(DateTime, default=datetime.datetime.utcnow)


class StockData(Base):
    """个股日K线 (TimescaleDB hypertable)"""

    __tablename__ = "stock_data"

    code = Column(String(10), primary_key=True, index=True)
    name = Column(String(50))
    trade_date = Column(Date, primary_key=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    amount = Column(Float)


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


class IndicatorsData(Base):
    """个股指标值 (EAV模式, 支持动态新增指标)"""

    __tablename__ = "indicators_data"
    __table_args__ = (
        UniqueConstraint(
            "stock_code", "trade_date", "indicator_name",
            name="uq_stock_indicator_date_name",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(10), nullable=False, index=True)
    trade_date = Column(Date, nullable=False)
    indicator_name = Column(String(50), nullable=False)
    indicator_value = Column(Float)


def get_engine(dsn: str | None = None):
    settings = get_settings()
    url = dsn or settings.database.dsn
    return create_engine(url, pool_size=5, max_overflow=10)


def get_session(engine=None):
    eng = engine or get_engine()
    return sessionmaker(bind=eng)()


def init_db(engine=None):
    """Create all tables and convert stock_data, block_data to hypertables. Idempotent."""
    eng = engine or get_engine()
    Base.metadata.create_all(eng)
    with eng.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
        conn.execute(text(
            "SELECT create_hypertable('stock_data', 'trade_date', "
            "chunk_time_interval => INTERVAL '1 month', "
            "if_not_exists => TRUE)"
        ))
        conn.execute(text(
            "SELECT create_hypertable('block_data', 'trade_date', "
            "chunk_time_interval => INTERVAL '1 month', "
            "if_not_exists => TRUE)"
        ))
        conn.execute(text(
            "SELECT create_hypertable('indicators_data', 'trade_date', "
            "chunk_time_interval => INTERVAL '1 month', "
            "if_not_exists => TRUE)"
        ))
        conn.commit()