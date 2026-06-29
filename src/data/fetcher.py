from datetime import datetime

import pandas as pd

from src.tq_bridge.client import TQClient
from src.tq_bridge.sector import SectorManager
from src.utils.logging import get_logger

logger = get_logger(__name__)


def fetch_all_sector_stocks(client: TQClient) -> pd.DataFrame:
    """获取所有概念板块和行业板块及其包含的个股列表。

    Returns DataFrame with columns: sector_code, sector_name, sector_type, stock_code, stock_name.
    """
    sm = SectorManager(client)
    all_records = []

    builtin_sectors = sm.get_builtin_sectors()
    logger.info("builtin_sectors_count", total=len(builtin_sectors))

    for sector in builtin_sectors:
        if isinstance(sector, dict):
            sector_code = sector.get("Code", "")
            sector_name = sector.get("Name", "")
            sector_type = sector.get("Type", "")
        else:
            sector_code = str(sector)
            sector_name = ""
            sector_type = ""

        try:
            stocks = sm.get_stocks_in_sector(sector_code, block_type=0)
        except Exception as e:
            logger.warning("fetch_sector_stocks_failed",
                           sector_name=sector_name, error=str(e))
            continue

        for stock in stocks:
            # TQ returns stock info as dict or list of codes depending on API
            if isinstance(stock, dict):
                stock_code = stock.get("Code", "")
                stock_name = stock.get("Name", "")
            else:
                stock_code = str(stock)
                stock_name = ""

            all_records.append({
                "sector_code": sector_code,
                "sector_name": sector_name,
                "sector_type": sector_type,
                "stock_code": stock_code,
                "stock_name": stock_name,
            })

        logger.debug("sector_stocks_fetched",
                     sector=sector_name, stock_count=len(stocks))

    df = pd.DataFrame(all_records)
    logger.info("all_sectors_fetched",
                total_records=len(df),
                unique_stocks=df["stock_code"].nunique() if not df.empty else 0)
    return df


def get_unique_stock_list(sector_df: pd.DataFrame) -> pd.DataFrame:
    """从板块-个股关联DataFrame中提取唯一个股列表。"""
    if sector_df.empty:
        return pd.DataFrame(columns=["stock_code", "stock_name"])
    return sector_df[["stock_code", "stock_name"]].drop_duplicates(
        subset="stock_code"
    ).reset_index(drop=True)