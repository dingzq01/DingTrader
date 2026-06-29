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
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from src.config.settings import get_settings


class Base(DeclarativeBase):
    pass


class Sector(Base):
    """概念板块 / 行业板块"""

    __tablename__ = "sectors"

    sector_code = Column(String(20), primary_key=True)
    sector_name = Column(String(100), nullable=False)
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


class DailyKline(Base):
    """个股日K线 (TimescaleDB hypertable)"""

    __tablename__ = "daily_kline"
    __table_args__ = (
        UniqueConstraint("stock_code", "trade_date", name="uq_stock_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(10), nullable=False, index=True)
    trade_date = Column(Date, nullable=False)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    amount = Column(Float)


class StockIndicator(Base):
    """个股指标值 (EAV模式, 支持动态新增指标)"""

    __tablename__ = "stock_indicators"
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


class SectorDailyAgg(Base):
    """板块日聚合数据 (由 Continuous Aggregate 维护，此为普通表用于存储手动刷新结果)"""

    __tablename__ = "sector_daily_agg"
    __table_args__ = (
        UniqueConstraint("sector_code", "trade_date", name="uq_sector_agg_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    sector_code = Column(String(20), nullable=False, index=True)
    trade_date = Column(Date, nullable=False)
    total_stocks = Column(Integer, default=0)
    up_count = Column(Integer, default=0)
    down_count = Column(Integer, default=0)
    flat_count = Column(Integer, default=0)
    limit_up_count = Column(Integer, default=0)
    limit_down_count = Column(Integer, default=0)
    avg_pct_change = Column(Float)


def get_engine(dsn: str | None = None):
    settings = get_settings()
    url = dsn or settings.database.dsn
    return create_engine(url, pool_size=5, max_overflow=10)


def get_session(engine=None):
    eng = engine or get_engine()
    return sessionmaker(bind=eng)()