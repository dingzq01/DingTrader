from sqlalchemy.orm import Session

from src.data.fetcher import fetch_all_sector_stocks, get_unique_stock_list
from src.data.downloader import download_stocks_batch
from src.data.models import (
    Sector,
    SectorStock,
    get_engine,
    get_session,
)
from src.tq_bridge.client import TQClient
from src.utils.logging import get_logger

logger = get_logger(__name__)


def sync_sector_metadata(session: Session, sector_df) -> None:
    """同步板块元数据到 sectors 和 sector_stocks 表。"""
    if sector_df.empty:
        return

    for sector_code, group in sector_df.groupby("sector_code"):
        row = group.iloc[0]
        sector = session.get(Sector, sector_code)
        if sector:
            sector.sector_name = row["sector_name"]
            sector.stock_count = len(group)
        else:
            session.add(Sector(
                sector_code=sector_code,
                sector_name=row["sector_name"],
                sector_type=row["sector_type"],
                stock_count=len(group),
            ))

        # 同步板块-个股关联
        for _, stock_row in group.iterrows():
            existing = session.execute(
                "SELECT 1 FROM sector_stocks WHERE sector_code = :sc AND stock_code = :st",
                {"sc": sector_code, "st": stock_row["stock_code"]},
            ).first()
            if not existing:
                session.add(SectorStock(
                    sector_code=sector_code,
                    stock_code=stock_row["stock_code"],
                    stock_name=stock_row["stock_name"],
                ))

    session.commit()
    logger.info("sector_metadata_synced", sectors=len(sector_df["sector_code"].unique()))


def full_sync(client: TQClient):
    """完整同步流程：获取板块 → 同步元数据 → 下载所有个股K线。

    确保概念板块和行业板块下的所有个股全部纳入拉取范围。
    """
    logger.info("full_sync_started")

    # 1. 获取所有板块及个股
    sector_df = fetch_all_sector_stocks(client)
    if sector_df.empty:
        logger.error("no_sector_data")
        return

    # 2. 同步板块元数据
    engine = get_engine()
    session = get_session(engine)
    try:
        sync_sector_metadata(session, sector_df)
        session.commit()
    finally:
        session.close()

    # 3. 获取唯一个股列表
    stock_list = get_unique_stock_list(sector_df)
    stock_codes = stock_list["stock_code"].tolist()
    logger.info("unique_stocks_to_download", count=len(stock_codes))

    # 4. 批量下载个股K线
    results = download_stocks_batch(client, stock_codes)

    # 5. 完整性校验
    failed = [k for k, v in results.items() if v == 0]
    if failed:
        logger.warning("sync_complete_with_failures", failed_count=len(failed))

    logger.info("full_sync_completed", success_count=len(stock_codes) - len(failed))


def check_data_integrity(client: TQClient) -> dict:
    """数据完整性校验：对比 TQ 板块个股数与数据库实际拉取数。

    Returns: {sector_name: {expected, actual, missing_count}} 字典。
    """
    sector_df = fetch_all_sector_stocks(client)
    if sector_df.empty:
        return {}

    session = get_session()
    integrity_report = {}

    try:
        for sector_name, group in sector_df.groupby("sector_name"):
            expected_stocks = set(group["stock_code"].unique())
            # 查询数据库中该板块的个股
            db_stocks = set(
                session.execute(
                    "SELECT DISTINCT stock_code FROM daily_kline"
                ).scalars().all()
            )
            missing = expected_stocks - db_stocks

            integrity_report[sector_name] = {
                "expected": len(expected_stocks),
                "actual_in_db": len(expected_stocks & db_stocks),
                "missing_count": len(missing),
            }
    finally:
        session.close()

    return integrity_report