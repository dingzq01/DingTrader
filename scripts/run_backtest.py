#!/usr/bin/env python
"""运行回测.

用法:
    python scripts/run_backtest.py --stock=000001 --from=2023-01-01 --to=2024-01-01
    python scripts/run_backtest.py --stock=000001 --optimize  # 参数优化模式
"""

import argparse
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backtrader as bt

from src.backtest.base_strategy import BaseBacktestStrategy
from src.backtest.commission import AStockCommission, SlippageModel
from src.backtest.data_feed import TimescaleDBData
from src.backtest.optimizer import run_optimization
from src.backtest.persistence import save_optimization_result
from src.config.settings import get_settings
from src.utils.logging import setup_logging, get_logger

logger = get_logger(__name__)


def parse_date(s: str) -> datetime.date:
    return datetime.datetime.strptime(s, "%Y-%m-%d").date()


def run_single_backtest(strategy_cls, stock_code: str, fromdate, todate,
                        cash: float | None = None):
    """单次回测运行。"""
    settings = get_settings()
    cerebro = bt.Cerebro()

    df = TimescaleDBData(
        stock_code=stock_code,
        fromdate=fromdate,
        todate=todate,
    )
    cerebro.adddata(df)

    cerebro.addstrategy(strategy_cls)
    cerebro.addsizer(bt.sizers.PercentSizer, percents=95)

    cerebro.broker.setcash(cash or settings.backtest.default_cash)
    cerebro.broker.addcommissioninfo(AStockCommission())
    cerebro.broker.set_slippage_perc(settings.backtest.slippage)

    cerebro.addanalyzer(bt.analyzers.SharpeRatio_A, _name="sharpe", riskfreerate=0.02)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")

    logger.info("backtest_start", stock=stock_code,
                from_date=str(fromdate), to_date=str(todate),
                cash=settings.backtest.default_cash)

    results = cerebro.run()
    strat = results[0]

    sharpe = strat.analyzers.sharpe.get_analysis()
    drawdown = strat.analyzers.drawdown.get_analysis()
    trades = strat.analyzers.trades.get_analysis()
    returns = strat.analyzers.returns.get_analysis()

    print(f"\n=== 回测报告: {stock_code} ===")
    print(f"初始资金: {cerebro.broker.startingcash:.2f}")
    print(f"最终价值: {cerebro.broker.getvalue():.2f}")
    print(f"总收益%: {returns.get('rtot', 0) * 100:.2f}%")
    print(f"Sharpe: {sharpe.get('sharperatio', 'N/A')}")
    print(f"最大回撤: {drawdown.get('max', {}).get('drawdown', 'N/A')}%")

    trade_total = trades.get("total", {}).get("total", 0)
    trade_won = trades.get("won", {}).get("total", 0)
    if trade_total > 0:
        print(f"胜率: {trade_won / trade_total * 100:.1f}% ({trade_won}/{trade_total})")

    return strat


def main():
    setup_logging()
    parser = argparse.ArgumentParser(description="回测运行器")
    parser.add_argument("--stock", required=True, help="股票代码 (如 000001)")
    parser.add_argument("--from", dest="from_date", default="2023-01-01",
                        help="起始日期 (YYYY-MM-DD)")
    parser.add_argument("--to", dest="to_date", default="2024-01-01",
                        help="结束日期 (YYYY-MM-DD)")
    parser.add_argument("--strategy", default="BaseBacktestStrategy",
                        help="回测策略类名")
    parser.add_argument("--optimize", action="store_true",
                        help="参数优化模式")
    parser.add_argument("--cash", type=float,
                        help="初始资金")
    args = parser.parse_args()

    from_date = parse_date(args.from_date)
    to_date = parse_date(args.to_date)

    # 目前只有基类，后续可动态加载具体策略
    strategy_cls = BaseBacktestStrategy

    if args.optimize:
        param_grid = {
            "fast": [10, 21, 30],
            "slow": [50, 55, 60],
        }
        results = run_optimization(
            strategy_cls, args.stock, from_date, to_date, param_grid,
            cash=args.cash,
        )
        print("\n=== 优化结果 (Top 5) ===")
        for r in results[:5]:
            print(f"  params={r}  sharpe={r['sharpe']}  max_dd={r['max_drawdown']}%")
    else:
        run_single_backtest(strategy_cls, args.stock, from_date, to_date,
                            cash=args.cash)


if __name__ == "__main__":
    main()