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


class StockResearchFeed(bt.feeds.PandasData):
    """组合回测用股票 DataFeed。

    在 OHLCV 之外增加策略所需的 指标/状态/因子 线:
        ma5, ma10, ma20, ma60,
        macd_dif, macd_dea, macd_hist,
        macd_hist_increasing (来自 stock_state_daily),
        macd_hist_increasing_days (来自 stock_state_daily),
        factor_rank (0~1 百分位; 无因子数据时为 NaN)

    输入 DataFrame 需包含:
        index = DatetimeIndex (交易日历)
        columns = open, high, low, close, volume,
                  ma5, ma10, ma20, ma60,
                  macd_dif, macd_dea, macd_hist,
                  macd_hist_increasing, macd_hist_increasing_days, factor_rank

    停牌/未上市日期用 NaN 行占位，回测引擎据此跳过今日交易。
    """

    lines = (
        "ma5",
        "ma10",
        "ma20",
        "ma60",
        "macd_dif",
        "macd_dea",
        "macd_hist",
        "macd_hist_increasing",
        "macd_hist_increasing_days",
        "factor_rank",
    )

    # 列名与数据库宽表一致 (data_adapter 宽表列名)
    params = (
        ("datetime", None),  # 使用 DataFrame index (DatetimeIndex)
        ("ma5", "ma5"),
        ("ma10", "ma10"),
        ("ma20", "ma20"),
        ("ma60", "ma60"),
        ("macd_dif", "macd_dif"),
        ("macd_dea", "macd_dea"),
        ("macd_hist", "macd_hist"),
        ("macd_hist_increasing", "macd_hist_increasing"),
        ("macd_hist_increasing_days", "macd_hist_increasing_days"),
        ("factor_rank", "factor_rank"),
    )