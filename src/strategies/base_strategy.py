from abc import ABC, abstractmethod
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.data.models import get_session
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
                    "SELECT trade_date, open, high, low, close, volume, amount "
                    "FROM stock_data WHERE code = :code "
                    "ORDER BY trade_date DESC LIMIT :limit"
                ),
                {"code": stock_code, "limit": min_bars},
            ).fetchall()

            if len(result) < min_bars:
                return None

            df = pd.DataFrame(
                result,
                columns=["date", "open", "high", "low", "close", "volume", "amount"],
            )
            return df.sort_values("date").reset_index(drop=True)
        finally:
            if close_session and session:
                session.close()

    @abstractmethod
    def check_conditions(self, df: pd.DataFrame) -> tuple[bool, dict[str, Any] | None]:
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

                is_meet, info = self.check_conditions(df)

                if is_meet:
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