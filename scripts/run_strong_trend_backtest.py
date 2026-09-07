#!/usr/bin/env python
"""Strong Trend V1 组合回测 CLI (docs/plan11)。

用法:
    python scripts/run_strong_trend_backtest.py --from=2024-01-01 --to=2024-12-31
    python scripts/run_strong_trend_backtest.py --use-factor          # V1-B (启用因子过滤)
    python scripts/run_strong_trend_backtest.py --max-positions=5 --position-size=100000

流程:
    1) data_adapter 从 TimescaleDB 加载沪深主板股票池宽表数据
    2) StrongTrendV1 规则判断 → engine 组合回测 (Backtrader, T+1 开盘执行)
    3) 结果持久化: strategy_definition / backtest_run / backtest_result / backtest_trade
"""

import argparse
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest import repository
from src.backtest.data_adapter import load_stock_frames
from src.backtest.engine import run_backtest
from src.config.settings import get_settings
from src.strategies.strong_trend import STRONG_TREND_V1_CONFIG, StrongTrendV1
from src.utils.logging import get_logger, setup_logging

logger = get_logger(__name__)


def parse_date(s: str) -> datetime.date:
    return datetime.datetime.strptime(s, "%Y-%m-%d").date()


def build_backtest_config(args, settings) -> dict:
    """回测环境配置 JSON (写入 backtest_run.backtest_config)。"""
    return {
        "use_factor": args.use_factor,
        "initial_capital": args.initial_capital,
        "position_size": args.position_size,
        "max_positions": args.max_positions,
        "commission_rate": settings.backtest.commission_rate,
        "stamp_tax_rate": settings.backtest.stamp_tax_rate,
        "slippage": settings.backtest.slippage,
        "slippage_pct": args.slippage,
    }


def main():
    setup_logging()
    parser = argparse.ArgumentParser(description="Strong Trend V1 组合回测")
    parser.add_argument("--from", dest="from_date", default=None, help="起始日期 (YYYY-MM-DD)")
    parser.add_argument("--to", dest="to_date", default=None, help="结束日期 (YYYY-MM-DD)")
    parser.add_argument("--use-factor", action="store_true",
                        help="启用因子过滤 (V1-B): factor_rank <= 0.30; 默认关闭 = V1-A")
    parser.add_argument("--factor-rank-max", type=float, default=None,
                        help="因子排位阈值 (默认 0.30, 仅 --use-factor 生效)")
    parser.add_argument("--initial-capital", type=float, default=None,
                        help="初始资金 (默认读配置或 10,000,000)")
    parser.add_argument("--position-size", type=float, default=None,
                        help="单只固定买入金额")
    parser.add_argument("--max-positions", type=int, default=None,
                        help="最大同时持仓数")
    parser.add_argument("--slippage", type=float, default=None,
                        help="滑点比例, 覆盖配置")
    args = parser.parse_args()

    settings = get_settings()

    from_date = parse_date(args.from_date) if args.from_date else None
    to_date = parse_date(args.to_date) if args.to_date else None
    initial_capital = args.initial_capital or settings.backtest.default_cash or 10_000_000.0
    position_size = args.position_size or 1_000_000.0
    max_positions = args.max_positions or 10

    # V1-A / V1-B 通过参数区分 (plan11: 不改写策略代码)
    strategy_params = {"use_factor": args.use_factor}
    if args.factor_rank_max is not None:
        strategy_params["factor_rank_max"] = args.factor_rank_max
    strategy = StrongTrendV1(params=strategy_params)
    config_name = "V1-B" if args.use_factor else "V1-A"

    # 1) 加载数据
    frames, calendar = load_stock_frames(
        start_date=from_date, end_date=to_date,
    )
    if not frames:
        logger.warning(
            "backtest_data_empty",
            start=str(from_date), end=str(to_date),
        )
        print("未加载到任何股票数据（请检查数据库连接与日期范围）。")
        return

    run_id = repository.generate_run_id()
    start_date = from_date or calendar[0]
    end_date = to_date or calendar[-1]
    stock_count = len(frames)

    logger.info(
        "backtest_start",
        run_id=run_id,
        strategy=strategy.get_full_name(),
        config=config_name,
        start=str(start_date),
        end=str(end_date),
        stock_count=stock_count,
        initial_capital=initial_capital,
        position_size=position_size,
        max_positions=max_positions,
    )

    # 2) 组合回测
    result = run_backtest(
        research_strategy=strategy,
        frames=frames,
        initial_capital=initial_capital,
        position_size=position_size,
        max_positions=max_positions,
        start_date=start_date,
        end_date=end_date,
        slippage_pct=args.slippage,
    )

    perf = result["performance"]
    print(f"\n=== {strategy.get_full_name()} ({config_name}) 回测报告 ===")
    print(f"run_id:          {run_id}")
    print(f"区间:            {start_date} ~ {end_date}")
    print(f"股票池:          {stock_count} 只")
    print(f"总收益:          {perf.get('total_return', 0):.2%}")
    print(f"年化收益:        {perf.get('annual_return', 0):.2%}")
    print(f"最大回撤:        {perf.get('max_drawdown', 0):.2%}")
    print(f"夏普比率:        {perf.get('sharpe_ratio', 0):.2f}")
    print(f"胜率:            {perf.get('win_rate', 0):.1%}")
    print(f"交易笔数:        {perf.get('trade_count', 0)}")
    if perf.get("profit_factor") is not None:
        print(f"盈亏比:          {perf['profit_factor']:.2f}")

    # 3) 持久化 (数据库不可用时不影响回测结果输出)
    backtest_config = build_backtest_config(args, settings)
    try:
        repository.ensure_strategy_definition(strategy, engine=None)
        repository.save_run(
            run_id=run_id,
            strategy=strategy,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            backtest_config=backtest_config,
            stock_count=stock_count,
        )
        repository.save_result(run_id, perf)
        repository.save_trades(run_id, result["trades"])
        logger.info("backtest_persisted", run_id=run_id, trades=len(result["trades"]))
    except Exception as exc:  # noqa: BLE001
        logger.warning("backtest_persist_failed", run_id=run_id, error=str(exc))

    logger.info(
        "backtest_completed",
        run_id=run_id,
        strategy=strategy.get_full_name(),
        total_return=perf.get("total_return"),
        annual_return=perf.get("annual_return"),
        max_drawdown=perf.get("max_drawdown"),
        sharpe=perf.get("sharpe_ratio"),
        win_rate=perf.get("win_rate"),
        trade_count=perf.get("trade_count"),
    )
    print("=== 回测完成 ===")


if __name__ == "__main__":
    main()