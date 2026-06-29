#!/usr/bin/env python
"""一键同步所有板块/个股数据到 TimescaleDB.

用法:
    python scripts/sync_data.py
    python scripts/sync_data.py --check-integrity  # 仅校验数据完整性
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.sync_manager import full_sync, check_data_integrity
from src.tq_bridge.client import TQClient
from src.utils.logging import setup_logging, get_logger

logger = get_logger(__name__)


def main():
    setup_logging()
    parser = argparse.ArgumentParser(description="数据同步工具")
    parser.add_argument("--check-integrity", action="store_true",
                        help="仅校验数据完整性，不下载数据")
    args = parser.parse_args()

    with TQClient() as client:
        if args.check_integrity:
            logger.info("checking_data_integrity")
            report = check_data_integrity(client)
            for sector_name, info in report.items():
                if info["missing_count"] > 0:
                    logger.warning(
                        "integrity_gap",
                        sector=sector_name,
                        expected=info["expected"],
                        actual=info["actual_in_db"],
                        missing=info["missing_count"],
                    )
            total_missing = sum(v["missing_count"] for v in report.values())
            logger.info("integrity_check_complete",
                        sectors_checked=len(report),
                        total_missing=total_missing)
        else:
            full_sync(client)


if __name__ == "__main__":
    main()