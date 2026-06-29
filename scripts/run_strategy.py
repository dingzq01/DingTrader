#!/usr/bin/env python
"""运行选股策略并写结果到 TQ 自定义板块.

用法:
    python scripts/run_strategy.py --name=macd_golden_cross
    python scripts/run_strategy.py --name=limit_up_consolidation --block-code=MY-BLOCK --block-name="我的板块"
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.data.models import get_session
from src.notification.feishu import send_feishu_message, format_strategy_result_msg
from src.strategies.registry import get_strategy, get_enabled_strategies
from src.tq_bridge.client import TQClient
from src.tq_bridge.sector import SectorManager
from src.utils.logging import setup_logging, get_logger

logger = get_logger(__name__)


def main():
    setup_logging()
    parser = argparse.ArgumentParser(description="运行选股策略")
    parser.add_argument("--name", help="策略名称（不指定则运行所有已启用策略）")
    parser.add_argument("--block-code", default="GC-ZTZL",
                        help="TQ 自定义板块代码 (默认: GC-ZTZL)")
    parser.add_argument("--block-name", default="观察-涨停整理",
                        help="TQ 自定义板块名称")
    parser.add_argument("--stock-pool", default="个股-硬科技",
                        help="候选股票池板块名称")
    parser.add_argument("--no-feishu", action="store_true",
                        help="不发送飞书通知")
    args = parser.parse_args()

    # 确定要运行的策略
    if args.name:

        strategies = [s for s in [get_strategy(args.name)] if s is not None]
        if not strategies:
            logger.error("strategy_not_found", name=args.name)
            sys.exit(1)
    else:
        strategies = get_enabled_strategies()
        logger.info("running_all_enabled_strategies",
                    count=len(strategies))

    # 获取股票池
    with TQClient() as client:
        sm = SectorManager(client)
        pool_code = sm.find_sector_code(args.stock_pool)
        if not pool_code:
            logger.error("stock_pool_not_found", name=args.stock_pool)
            sys.exit(1)

        stock_list = sm.get_stocks_in_sector(pool_code)
        logger.info("stock_pool_loaded", count=len(stock_list))

        # 运行各策略并汇总结果
        all_selected = []
        for strategy in strategies:
            selected = strategy.select(stock_list)
            codes = [r["stock_code"] for r in selected]
            all_selected.extend(codes)
            logger.info("strategy_result", name=strategy.name, count=len(selected))

            # 格式化并发送飞书消息
            if not args.no_feishu:
                msg = format_strategy_result_msg(strategy.name, selected)
                send_feishu_message(msg)

        # 写入 TQ 自定义板块
        unique_selected = list(dict.fromkeys(all_selected))
        sm.put_stocks(args.block_code, args.block_name, unique_selected)
        logger.info("results_written_to_tq", total_unique=len(unique_selected))


if __name__ == "__main__":
    main()