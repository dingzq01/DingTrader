"""参数优化工具."""

import itertools

import backtrader as bt

from src.backtest.base_strategy import BaseBacktestStrategy
from src.backtest.commission import AStockCommission, SlippageModel
from src.backtest.data_feed import TimescaleDBData
from src.backtest.persistence import save_optimization_result
from src.config.settings import get_settings
from src.utils.logging import get_logger

logger = get_logger(__name__)


def run_optimization(
    strategy_cls: type[BaseBacktestStrategy],
    stock_code: str,
    fromdate,
    todate,
    param_grid: dict[str, list],
    cash: float | None = None,
):
    """对指定策略执行参数网格搜索优化。

    Args:
        strategy_cls: Backtrader 策略类
        stock_code: 个股代码
        fromdate / todate: 回测日期范围
        param_grid: 参数网格，e.g. {"fast": [10, 21, 30], "slow": [50, 55, 60]}
        cash: 初始资金（默认从配置读取）

    Returns:
        list[dict]: 按 Sharpe 降序排列的优化结果
    """
    settings = get_settings()
    cerebro = bt.Cerebro(optreturn=False)

    cerebro.adddata(TimescaleDBData(
        stock_code=stock_code,
        fromdate=fromdate,
        todate=todate,
    ))

    cerebro.addstrategy(strategy_cls)
    cerebro.addsizer(bt.sizers.PercentSizer, percents=95)

    cerebro.broker.setcash(cash or settings.backtest.default_cash)
    cerebro.broker.addcommissioninfo(AStockCommission())
    cerebro.broker.set_slippage_perc(settings.backtest.slippage)

    # 分析器
    cerebro.addanalyzer(bt.analyzers.SharpeRatio_A, _name="sharpe", riskfreerate=0.02)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")

    # 参数网格
    keys = list(param_grid.keys())
    strats = cerebro.optstrategy(strategy_cls, **param_grid)

    results = cerebro.run(maxcpus=1)
    logger.info("optimization_complete", total_runs=len(results))

    parsed = []
    for i, result in enumerate(results):
        opt_result = {"run": i}
        # 参数值
        for key in keys:
            opt_result[key] = getattr(result[0].params, key, None)

        # 绩效指标
        sharpe = result[0].analyzers.sharpe.get_analysis()
        dd = result[0].analyzers.drawdown.get_analysis()
        trades = result[0].analyzers.trades.get_analysis()
        ret = result[0].analyzers.returns.get_analysis()

        opt_result["sharpe"] = sharpe.get("sharperatio", None)
        opt_result["max_drawdown"] = dd.get("max", {}).get("drawdown", None)
        opt_result["total_return"] = ret.get("rtot", None)
        opt_result["win_rate"] = (
            trades.get("won", {}).get("total", 0)
            / max(trades.get("total", {}).get("total", 1), 1)
        )

        parsed.append(opt_result)

    parsed.sort(key=lambda x: x.get("sharpe") or 0, reverse=True)
    save_optimization_result(strategy_cls.__name__, stock_code, parsed)

    return parsed