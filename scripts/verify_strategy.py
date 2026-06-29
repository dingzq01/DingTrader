#!/usr/bin/env python
"""策略验证脚本：对比 TQ 策略结果与 Python 数据库计算结果。

用法:
    python scripts/verify_strategy.py --name=macd_golden_cross
    python scripts/verify_strategy.py --name=limit_up_consolidation

验证流程:
    1. 从 TQ 运行策略获取结果集
    2. 从 TimescaleDB 获取相同股票池的K线数据，用 Python 策略重新计算
    3. 对比两组结果，报告一致性百分比
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import get_settings
from src.data.models import get_session
from src.strategies.registry import get_strategy
from src.tq_bridge.client import TQClient
from src.tq_bridge.sector import SectorManager
from src.utils.logging import setup_logging, get_logger

logger = get_logger(__name__)


def run_tq_strategy(client: TQClient, block_name: str) -> set[str]:
    """通过 TQ 运行策略并获取结果股票集合。"""
    sm = SectorManager(client)
    code = sm.find_sector_code(block_name)
    if not code:
        logger.warning("tq_sector_not_found", name=block_name)
        return set()
    stocks = sm.get_stocks_in_sector(code)
    logger.info("tq_strategy_result", block=block_name, count=len(stocks))
    return set(stocks)


def run_python_strategy(strategy_name: str, stock_list: list[str]) -> set[str]:
    """通过 Python 策略（基于 TimescaleDB）计算结果。"""
    strategy = get_strategy(strategy_name)
    if strategy is None:
        logger.error("strategy_not_found", name=strategy_name)
        return set()

    results = strategy.select(stock_list)
    return {r["stock_code"] for r in results}


def verify(strategy_name: str, tq_block_name: str, stock_pool_block: str):
    """主验证流程。"""
    logger.info("verification_start", strategy=strategy_name)

    with TQClient() as client:
        # 1. TQ 运行策略 → 结果板块股票
        tq_results = run_tq_strategy(client, tq_block_name)
        logger.info("tq_result_count", count=len(tq_results))

        # 2. 获取全量候选股票池 (从TQ自定义板块)
        sm = SectorManager(client)
        pool_code = sm.find_sector_code(stock_pool_block)
        stock_pool = (
            sm.get_stocks_in_sector(pool_code) if pool_code
            else []
        )

        # 3. Python 策略计算
        py_results = run_python_strategy(strategy_name, stock_pool)
        logger.info("python_result_count", count=len(py_results))

        # 4. 对比
        both = tq_results & py_results
        tq_only = tq_results - py_results
        py_only = py_results - tq_results

        total = len(tq_results | py_results)
        agreement = len(both) / total * 100 if total > 0 else 0

        logger.info("verification_result",
                    total=total,
                    both=len(both),
                    tq_only=len(tq_only),
                    py_only=len(py_only),
                    agreement_pct=round(agreement, 2))

        print(f"\n=== 验证报告: {strategy_name} ===")
        print(f"TQ 结果: {len(tq_results)} 只")
        print(f"Python 结果: {len(py_results)} 只")
        print(f"一致: {len(both)} 只")
        print(f"仅在TQ: {len(tq_only)} 只 → {tq_only}")
        print(f"仅在Python: {len(py_only)} 只 → {py_only}")
        print(f"一致率: {agreement:.1f}%")
        print(f"阈值: >= 95% 视为通过")


def main():
    setup_logging()
    parser = argparse.ArgumentParser(description="验证策略 TQ vs Python 结果一致性")
    parser.add_argument("--name", required=True, help="策略名称")
    parser.add_argument("--tq-block", default="观察-缩量整理", help="TQ 策略结果板块名称")
    parser.add_argument("--pool", default="个股-硬科技", help="股票池板块名称")
    args = parser.parse_args()

    verify(args.name, args.tq_block, args.pool)


if __name__ == "__main__":
    main()