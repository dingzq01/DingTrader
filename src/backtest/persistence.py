"""回测结果持久化."""

import datetime
import json

from sqlalchemy import Column, DateTime, Float, Integer, String, create_engine, Text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from src.config.settings import get_settings
from src.utils.logging import get_logger

logger = get_logger(__name__)


class Base(DeclarativeBase):
    pass


class BacktestResult(Base):
    __tablename__ = "backtest_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_name = Column(String(100), nullable=False)
    stock_code = Column(String(10), nullable=False)
    run_at = Column(DateTime, default=datetime.datetime.utcnow)
    params_json = Column(Text)       # 策略参数 JSON
    sharpe = Column(Float)
    max_drawdown = Column(Float)
    total_return = Column(Float)
    win_rate = Column(Float)
    equity_curve_json = Column(Text)  # 权益曲线 JSON


def save_optimization_result(strategy_name: str, stock_code: str, results: list[dict]):
    """保存参数优化结果到 TimescaleDB."""
    settings = get_settings()
    engine = create_engine(settings.database.dsn)
    Base.metadata.create_all(engine, checkfirst=True)
    session = sessionmaker(bind=engine)()

    try:
        for r in results:
            session.add(BacktestResult(
                strategy_name=strategy_name,
                stock_code=stock_code,
                params_json=json.dumps({k: r[k] for k in r if k not in
                    ["run", "sharpe", "max_drawdown", "total_return", "win_rate"]},
                    ensure_ascii=False),
                sharpe=r.get("sharpe"),
                max_drawdown=r.get("max_drawdown"),
                total_return=r.get("total_return"),
                win_rate=r.get("win_rate"),
            ))
        session.commit()
        logger.info("backtest_results_saved", count=len(results))
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()