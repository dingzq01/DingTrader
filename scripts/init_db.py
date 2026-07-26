#!/usr/bin/env python
"""Initialize database: create tables, hypertable, and views.

用法:
    python scripts/init_db.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.models import init_db, get_engine
from src.data.block_stat import compute_block_stat_daily
from src.data.stock_indicator import compute_stock_indicator_daily
from src.data.stock_state import compute_stock_state_daily
from src.factors.stock_factor import compute_stock_factor_daily
from src.utils.logging import setup_logging, get_logger

logger = get_logger(__name__)


def main():
    setup_logging()

    logger.info("init_db_started")

    # 1. Create tables + hypertable
    engine = get_engine()
    init_db(engine)
    logger.info("tables_and_hypertable_created")

    # 2. 板块统计表初始填充
    compute_block_stat_daily(engine)
    logger.info("block_stat_refreshed")

    # 4. 个股技术指标表初始填充
    compute_stock_indicator_daily(engine)
    logger.info("stock_indicator_refreshed")

    # 5. 个股技术状态表初始填充
    compute_stock_state_daily(engine)
    logger.info("stock_state_refreshed")

    # 6. 个股因子评分表初始填充
    compute_stock_factor_daily(engine)
    logger.info("stock_factor_refreshed")

    # 7. 板块因子评分表初始填充
    from src.factors.block_factor import compute_block_factor_daily
    compute_block_factor_daily(engine)
    logger.info("block_factor_refreshed")

    logger.info("init_db_completed")


if __name__ == "__main__":
    main()
