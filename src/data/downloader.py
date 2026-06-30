from datetime import date, timedelta

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.config.settings import get_settings
from src.data.models import StockData, get_engine, get_session
from src.tq_bridge.client import TQClient
from src.tq_bridge.market_data import MarketDataAPI
from src.utils.logging import get_logger
from src.utils.retry import retry_on_failure

logger = get_logger(__name__)


@retry_on_failure(max_retries=3, base_delay=1.0)
def download_stock_kline(
    client: TQClient,
    stock_code: str,
    session: Session | None = None,
) -> int:
    """下载单只股票的历史K线并写入 TimescaleDB。

    Returns: 成功写入的K线条数。
    """
    api = MarketDataAPI(client)
    settings = get_settings()
    lookback_days = settings.sync.lookback_years * 250

    df = api.get_kline(stock_code, count=lookback_days)
    if df is None or df.empty:
        logger.warning("no_kline_data", stock_code=stock_code)
        return 0

    close_session = False
    if session is None:
        session = get_session()
        close_session = True

    try:
        # 只插入数据库中不存在的日期
        existing_dates = get_existing_dates(session, stock_code)
        new_rows = df[~df["date"].isin(existing_dates)]

        if new_rows.empty:
            return 0

        for _, row in new_rows.iterrows():
            session.add(StockData(
                code=stock_code,
                trade_date=row["date"],
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
            ))

        session.commit()
        logger.debug("kline_downloaded", stock_code=stock_code,
                     new_rows=len(new_rows))
        return len(new_rows)
    except Exception:
        session.rollback()
        raise
    finally:
        if close_session:
            session.close()


def get_existing_dates(session: Session, stock_code: str) -> set[date]:
    """查询某只股票在数据库中已有的日期集合。"""
    result = session.execute(
        text(
            "SELECT trade_date FROM stock_data WHERE code = :code"
        ),
        {"code": stock_code},
    ).fetchall()
    return {row[0] for row in result}


def download_stocks_batch(
    client: TQClient,
    stock_list: list[str],
) -> dict[str, int]:
    """批量下载股票K线数据。

    Returns: {stock_code: new_rows_count} 映射。
    """
    settings = get_settings()
    results = {}

    for stock_code in stock_list:
        count = download_stock_kline(client, stock_code)
        results[stock_code] = count

    logger.info("batch_download_complete",
                total_stocks=len(stock_list),
                total_rows=sum(results.values()))
    return results