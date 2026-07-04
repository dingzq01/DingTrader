from sqlalchemy import text
from sqlalchemy.orm import Session

from src.data.fetcher import fetch_all_sector_stocks, get_unique_stock_list
from src.data.downloader import download_stocks_batch
from src.data.models import (
    StockBlockRelation,
    get_engine,
    get_session,
    init_db,
)
from src.tq_bridge.client import TQClient
from src.utils.logging import get_logger

logger = get_logger(__name__)


def _resolve_block_columns(df):
    """兼容新旧字段名，返回 (code/name/type) 列名。"""
    columns = set(df.columns)
    code_key = "block_code" if "block_code" in columns else "sector_code"
    name_key = "block_name" if "block_name" in columns else "sector_name"
    type_key = "block_type" if "block_type" in columns else "sector_type"
    return code_key, name_key, type_key


def sync_sector_metadata(session: Session, block_df) -> None:
    """同步板块元数据到 stock_block_relation 表。"""
    if block_df.empty:
        return

    code_key, name_key, type_key = _resolve_block_columns(block_df)

    for block_code, group in block_df.groupby(code_key):
        for _, stock_row in group.iterrows():
            row = group.iloc[0]
            existing = session.execute(
                text(
                    "SELECT 1 FROM stock_block_relation WHERE block_code = :bc AND stock_code = :sc"
                ),
                {"bc": block_code, "sc": stock_row["stock_code"]},
            ).first()
            if not existing:
                session.add(StockBlockRelation(
                    block_code=block_code,
                    block_name=row[name_key],
                    stock_code=stock_row["stock_code"],
                    stock_name=stock_row["stock_name"],
                    block_type=row[type_key],
                ))

    session.commit()
    logger.info("block_metadata_synced", blocks=len(block_df[code_key].unique()))


def full_sync(client: TQClient):
    """完整同步流程：获取板块 → 同步元数据 → 下载所有个股K线。

    确保概念板块和行业板块下的所有个股全部纳入拉取范围。
    """
    logger.info("full_sync_started")

    # 0. 确保数据库表存在
    init_db()

    # 1. 获取所有板块及个股
    block_df = fetch_all_sector_stocks(client)
    if block_df.empty:
        logger.error("no_block_data")
        return

    # 2. 同步板块-个股关联
    engine = get_engine()
    session = get_session(engine)
    try:
        sync_sector_metadata(session, block_df)
        session.commit()
    finally:
        session.close()

    # 3. 获取唯一个股列表
    stock_list = get_unique_stock_list(block_df)
    stock_records = stock_list.to_dict("records")
    logger.info("unique_stocks_to_download", count=len(stock_records))

    # 4. 批量下载个股K线
    results = download_stocks_batch(client, stock_records)

    # 5. 完整性校验
    failed = [k for k, v in results.items() if v == 0]
    if failed:
        logger.warning("sync_complete_with_failures", failed_count=len(failed))

    logger.info("full_sync_completed", success_count=len(stock_records) - len(failed))


def check_data_integrity(client: TQClient) -> dict:
    """数据完整性校验：对比 TQ 板块个股数与数据库实际拉取数。

    Returns: {block_name: {expected, actual_in_db, missing_count}} 字典。
    """
    # 0. 确保数据库表存在
    init_db()

    # 1. 获取最新板块个股关系
    block_df = fetch_all_sector_stocks(client)
    if block_df.empty:
        return {}

    code_key, name_key, _ = _resolve_block_columns(block_df)

    session = get_session()
    integrity_report = {}

    try:
        for block_code, group in block_df.groupby(code_key):
            expected_stocks = set(group["stock_code"].unique())
            block_name = group.iloc[0][name_key]

            db_rows = session.execute(
                text(
                    "SELECT stock_code FROM stock_block_relation WHERE block_code = :block_code"
                ),
                {"block_code": block_code},
            ).fetchall()
            db_stocks = {row[0] for row in db_rows}

            missing = expected_stocks - db_stocks

            integrity_report[block_name] = {
                "expected": len(expected_stocks),
                "actual_in_db": len(expected_stocks & db_stocks),
                "missing_count": len(missing),
            }
    finally:
        session.close()

    return integrity_report
