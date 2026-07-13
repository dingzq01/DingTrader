from datetime import date
import time

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.config.settings import get_settings
from src.data.models import BlockData, StockData, get_engine, get_session
from src.tq_bridge.client import TQClient
from src.tq_bridge.market_data import MarketDataAPI
from src.utils.logging import get_logger
from src.utils.retry import retry_on_failure

logger = get_logger(__name__)


def is_target_market(stock_code: str) -> bool:
    """是否为主流程需要同步的 A 股代码。"""
    if not stock_code:
        return False

    code = stock_code.strip()

    # 去掉市场前缀 (e.g., SZ000001 → 000001, SH600000 → 600000)
    for prefix in ("SZ", "SH", "BJ"):
        if code.startswith(prefix) and len(code) == 8 and code[2:].isdigit():
            code = code[2:]
            break

    # 去掉市场后缀 (e.g., 000001.SZ → 000001)
    if "." in code:
        code = code.rsplit(".", 1)[0]

    if len(code) != 6 or not code.isdigit():
        return False

    return code.startswith(("0", "3", "6", "688"))


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
    settings = get_settings()
    lookback_days = settings.sync.lookback_years * 250

    # 提前创建 session，用于查询最新日期
    close_session = False
    if session is None:
        session = get_session()
        close_session = True

    try:
        # 根据数据库最新日期动态计算需要请求的K线条数
        latest_date = get_latest_stock_date(session, stock_code)
        if latest_date is not None:
            days_since = (date.today() - latest_date).days
            if days_since <= 0:
                logger.debug("stock_already_up_to_date",
                             stock_code=stock_code, latest_date=latest_date)
                return 0
            count = min(int(days_since * 1.5) + 5, lookback_days)
        else:
            count = lookback_days

        api = MarketDataAPI(client)
        df = api.get_kline(stock_code, count=count)
        if df is None or df.empty:
            logger.warning("no_kline_data", stock_code=stock_code)
            return 0

        # 去重过滤：排除数据库中已存在的日期
        existing_dates = get_existing_dates(session, stock_code)
        new_rows = df[~df["date"].dt.date.isin(existing_dates)]

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
                amount=row["amount"] if "amount" in row.index else 0,
            ))

        session.commit()
        logger.debug("kline_downloaded", stock_code=stock_code,
                     new_rows=len(new_rows), count_requested=count)
        return len(new_rows)
    except Exception:
        session.rollback()
        raise
    finally:
        if close_session:
            session.close()


def get_latest_stock_date(session: Session, stock_code: str) -> date | None:
    """查询某只股票在数据库中的最新交易日期。"""
    result = session.execute(
        text("SELECT MAX(trade_date) FROM stock_data WHERE code = :code"),
        {"code": stock_code},
    ).scalar()
    return result


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


def get_latest_block_date(session: Session, block_code: str) -> date | None:
    """查询某个板块在数据库中的最新交易日期。"""
    result = session.execute(
        text("SELECT MAX(trade_date) FROM block_data WHERE code = :code"),
        {"code": block_code},
    ).scalar()
    return result


def get_existing_block_dates(session: Session, block_code: str) -> set[date]:
    """查询某个板块在数据库中已有的日期集合。"""
    result = session.execute(
        text(
            "SELECT trade_date FROM block_data WHERE code = :code"
        ),
        {"code": block_code},
    ).fetchall()
    return {row[0] for row in result}


@retry_on_failure(max_retries=3, base_delay=1.0)
def download_block_kline(
    client: TQClient,
    block_code: str,
    block_name: str | None = None,
    session: Session | None = None,
) -> int:
    """下载单个板块的历史K线并写入 TimescaleDB。

    Returns: 成功写入的K线条数。
    """
    settings = get_settings()
    lookback_days = settings.sync.lookback_years * 250

    # 提前创建 session，用于查询最新日期
    close_session = False
    if session is None:
        session = get_session()
        close_session = True

    try:
        # 根据数据库最新日期动态计算需要请求的K线条数
        latest_date = get_latest_block_date(session, block_code)
        if latest_date is not None:
            days_since = (date.today() - latest_date).days
            if days_since <= 0:
                logger.debug("block_already_up_to_date",
                             block_code=block_code, latest_date=latest_date)
                return 0
            count = min(int(days_since * 1.5) + 5, lookback_days)
        else:
            count = lookback_days

        api = MarketDataAPI(client)
        df = api.get_kline(block_code, count=count)
        if df is None or df.empty:
            logger.warning("no_block_kline_data", block_code=block_code)
            return 0

        # 去重过滤：排除数据库中已存在的日期
        existing_dates = get_existing_block_dates(session, block_code)
        new_rows = df[~df["date"].dt.date.isin(existing_dates)]

        if new_rows.empty:
            return 0

        final_name = block_name if block_name else block_code

        for _, row in new_rows.iterrows():
            session.add(BlockData(
                code=block_code,
                name=final_name,
                trade_date=row["date"],
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
                amount=row["amount"] if "amount" in row.index else 0,
            ))

        session.commit()
        logger.debug("block_kline_downloaded", block_code=block_code,
                     new_rows=len(new_rows), count_requested=count)
        return len(new_rows)
    except Exception:
        session.rollback()
        raise
    finally:
        if close_session:
            session.close()


def download_blocks_batch(
    client: TQClient,
    block_list: list[dict[str, str]] | list[str],
) -> dict[str, int]:
    """批量下载板块K线数据。

    Returns: {block_code: new_rows_count} 映射。
    """
    settings = get_settings()
    results = {}

    blocks: list[dict[str, str]]
    if block_list and isinstance(block_list[0], dict):
        blocks = block_list
    else:
        blocks = [{"block_code": code} for code in block_list]

    for idx, block in enumerate(blocks):
        block_code = block["block_code"]
        block_name = block.get("block_name")
        try:
            if block_name is None:
                count = download_block_kline(client, block_code)
            else:
                count = download_block_kline(client, block_code, block_name)
            results[block_code] = count
        except Exception:
            logger.exception("download_block_failed", block_code=block_code)
            results[block_code] = 0

        if idx + 1 < len(blocks):
            time.sleep(settings.sync.request_interval_seconds)

    logger.info(
        "batch_block_download_complete",
        total_blocks=len(blocks),
        total_rows=sum(results.values()),
    )
    return results
