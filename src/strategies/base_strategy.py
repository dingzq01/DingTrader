from abc import ABC, abstractmethod
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.data.models import DailyKline, StockIndicator, get_session
from src.indicators.registry import compute_for_stock
from src.utils.logging import get_logger

logger = get_logger(__name__)


class BaseStrategy(ABC):
    """选股策略基类。

    子类只需实现 check_conditions() — 核心买卖逻辑。
    """

    name: str = ""
    description: str = ""

    def fetch_kline(self, stock_code: str, session: Session | None = None,
                    min_bars: int = 80) -> pd.DataFrame | None:
        """从 TimescaleDB 获取个股历史K线。"""
        close_session = False
        if session is None:
            session = get_session()
            close_session = True

        try:
            result = session.execute(
                text(
                    "SELECT trade_date, open, high, low, close, volume "
                    "FROM daily_kline WHERE stock_code = :code "
                    "ORDER BY trade_date DESC LIMIT :limit"
                ),
                {"code": stock_code, "limit": min_bars},
            ).fetchall()

            if len(result) < min_bars:
                return None

            df = pd.DataFrame(
                result,
                columns=["date", "open", "high", "low", "close", "volume"],
            )
            return df.sort_values("date").reset_index(drop=True)
        finally:
            if close_session and session:
                session.close()

    def compute_indicators(self, df: pd.DataFrame) -> dict[str, float]:
        """计算所有已注册指标。"""
        return compute_for_stock(df)

    def save_indicators(self, stock_code: str, trade_date: date,
                        indicators: dict[str, float], session: Session):
        """将指标值存入 stock_indicators 表 (EAV模式)。"""
        for name, value in indicators.items():
            if value is None:
                continue
            # Upsert: 有则更新，无则插入
            existing = session.get(StockIndicator, {
                "stock_code": stock_code,
                "trade_date": trade_date,
                "indicator_name": name,
            })
            if existing:
                existing.indicator_value = float(value)
            else:
                session.add(StockIndicator(
                    stock_code=stock_code,
                    trade_date=trade_date,
                    indicator_name=name,
                    indicator_value=float(value),
                ))

    @abstractmethod
    def check_conditions(self, df: pd.DataFrame,
                         indicators: dict[str, float]) -> tuple[bool, dict[str, Any] | None]:
        """核心筛选逻辑 (子类实现)。

        Returns: (是否符合条件, 可选详情dict)
        """
        ...

    def select(self, stock_list: list[str]) -> list[dict[str, Any]]:
        """模板方法: 遍历股票列表，返回符合条件的股票详情列表。"""
        results = []
        session = get_session()

        try:
            for code in stock_list:
                df = self.fetch_kline(code, session=session)
                if df is None:
                    continue

                indicators = self.compute_indicators(df)
                is_meet, info = self.check_conditions(df, indicators)

                if is_meet:
                    trade_date = df.iloc[-1]["date"] if "date_orig" not in df.columns else date.today()
                    self.save_indicators(code, trade_date, indicators, session)
                    entry = {"stock_code": code}
                    if info:
                        entry.update(info)
                    results.append(entry)

            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        logger.info("strategy_select_complete", strategy=self.name,
                    input_count=len(stock_list), selected=len(results))
        return results