from datetime import date
import time

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


def is_target_market(stock_code: str) -> bool:
    """是否为主流程需要同步的 A 股代码。"""
    if not stock_code or len(stock_code) != 6 or not stock_code.isdigit():
        return False

    return stock_code.startswith(("0", "3", "6", "688"))


@retry_on_failure(max_retries=3, base_delay=1.0)
def download_stock_kline(
    client: TQClient,
    stock_code: str,
    stock_name: str | None = None,
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

        # 名称若为空，统一用代码回退
        final_name = stock_name if stock_name else stock_code

        for _, row in new_rows.iterrows():
            session.add(StockData(
                code=stock_code,
                name=final_name,
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
    stock_list: list[dict[str, str]] | list[str],
) -> dict[str, int]:
    """批量下载股票K线数据。

    Returns: {stock_code: new_rows_count} 映射。
    """
    settings = get_settings()
    results = {}

    # Normalize input to records
    stocks: list[dict[str, str]]
    if stock_list and isinstance(stock_list[0], dict):
        stocks = stock_list
    else:
        stocks = [{"stock_code": code} for code in stock_list]

    target_stocks = [row for row in stocks if is_target_market(row["stock_code"])]

    for idx, stock in enumerate(target_stocks):
        stock_code = stock["stock_code"]
        stock_name = stock.get("stock_name")
        try:
            if stock_name is None:
                count = download_stock_kline(client, stock_code)
            else:
                count = download_stock_kline(client, stock_code, stock_name)
            results[stock_code] = count
        except Exception:
            logger.exception("download_stock_failed", stock_code=stock_code)
            results[stock_code] = 0

        if idx + 1 < len(target_stocks):
            time.sleep(settings.sync.request_interval_seconds)

    logger.info(
        "batch_download_complete",
        total_stocks=len(target_stocks),
        total_rows=sum(results.values()),
    )
    return results
