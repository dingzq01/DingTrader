"""Custom backtrader DataFeed backed by TimescaleDB."""

import datetime

import backtrader as bt
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.data.models import get_session
from src.utils.logging import get_logger

logger = get_logger(__name__)


class TimescaleDBData(bt.feeds.PandasData):
    """Backtrader DataFeed 的 TimescaleDB 适配器。

    用法:
        cerebro.adddata(TimescaleDBData(
            stock_code="000001",
            fromdate=datetime.date(2023, 1, 1),
            todate=datetime.date(2024, 1, 1),
        ))
    """

    params = (
        ("stock_code", ""),
        ("fromdate", None),
        ("todate", None),
        ("session", None),
    )

    def __init__(self, **kwargs):
        self._own_session = False
        super().__init__(**kwargs)

    def _load_data(self):
        stock_code = self.p.stock_code
        fromdate = self.p.fromdate
        todate = self.p.todate
        session = self.p.session

        close_session = False
        if session is None:
            session = get_session()
            close_session = True

        try:
            query = text(
                "SELECT trade_date, open, high, low, close, volume "
                "FROM stock_data WHERE code = :code "
                "AND trade_date BETWEEN :from_date AND :to_date "
                "ORDER BY trade_date ASC"
            )
            result = session.execute(query, {
                "code": stock_code,
                "from_date": fromdate or datetime.date(2000, 1, 1),
                "to_date": todate or datetime.date.today(),
            }).fetchall()

            if not result:
                logger.warning("no_kline_data_for_backtest", stock_code=stock_code)
                return pd.DataFrame()

            df = pd.DataFrame(
                result,
                columns=["datetime", "open", "high", "low", "close", "volume"],
            )
            df["datetime"] = pd.to_datetime(df["datetime"])
            df["openinterest"] = 0
            return df

        finally:
            if close_session and session:
                session.close()