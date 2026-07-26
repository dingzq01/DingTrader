from sqlalchemy import text
from sqlalchemy.orm import Session

from src.data.fetcher import fetch_all_sector_stocks, get_unique_block_list, get_unique_stock_list
from src.data.downloader import download_blocks_batch, download_stocks_batch
from src.data.models import (
    StockBlockRelation,
    get_engine,
    get_session,
    init_db,
)
from src.data.block_stat import compute_block_stat_daily
from src.data.stock_indicator import compute_stock_indicator_daily
from src.data.stock_state import compute_stock_state_daily
from src.factors.stock_factor import compute_stock_factor_daily
from src.factors.block_factor import compute_block_factor_daily
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
    """完整同步流程：获取板块 → 同步元数据 → 下载个股K线 → 下载板块K线。

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

    # 5. 批量下载板块K线
    block_list = get_unique_block_list(block_df)
    block_records = block_list.to_dict("records")
    logger.info("unique_blocks_to_download", count=len(block_records))
    block_results = download_blocks_batch(client, block_records)

    # 6. 完整性校验
    stock_failed = [k for k, v in results.items() if v == 0]
    block_failed = [k for k, v in block_results.items() if v == 0]
    if stock_failed:
        logger.warning("stock_sync_failures", failed_count=len(stock_failed))
    if block_failed:
        logger.warning("block_sync_failures", failed_count=len(block_failed))

    # 7. 刷新板块统计表（自动补全到最新日期）
    compute_block_stat_daily(engine)

    # 8. 刷新个股技术指标表（自动补全到最新日期）
    compute_stock_indicator_daily(engine)

    # 9. 刷新个股技术状态表（自动补全到最新日期）
    compute_stock_state_daily(engine)

    # 10. 刷新个股因子评分表（自动补全到最新日期）
    compute_stock_factor_daily(engine)

    # 11. 刷新板块因子评分表（自动补全到最新日期）
    compute_block_factor_daily(engine)

    logger.info(
        "full_sync_completed",
        stock_success=len(stock_records) - len(stock_failed),
        block_success=len(block_records) - len(block_failed),
    )


