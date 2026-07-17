#!/usr/bin/env python
"""一键同步所有板块/个股数据到 TimescaleDB.

用法:
    python scripts/sync_data.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.sync_manager import full_sync
from src.tq_bridge.client import TQClient
from src.utils.logging import setup_logging, get_logger

logger = get_logger(__name__)


def main():
    setup_logging()
    with TQClient() as client:
        full_sync(client)


if __name__ == "__main__":
    main()