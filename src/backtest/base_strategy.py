"""回测策略基类：统一日志/绩效统计/图表渲染."""

import datetime

import backtrader as bt
import backtrader.analyzers as btanalyzers
import pandas as pd

from src.utils.logging import get_logger

logger = get_logger(__name__)


class BaseBacktestStrategy(bt.Strategy):
    """回测策略基类。

    自动集成:
        - 每笔交易日志
        - Sharpe / MaxDrawdown / WinRate 分析器
        - 权益曲线/回撤曲线输出

    子类重写:
        - next(): 核心买卖逻辑
    """

    params = (
        ("log_trades", True),
        ("verbose", False),
    )

    def __init__(self):
        self.order = None
        self.trade_count = 0

    def notify_order(self, order):
        if order.status in [order.Submitted, order.Accepted]:
            return

        if order.status in [order.Completed]:
            if order.isbuy():
                msg = f"BUY  {order.data._name} @ {order.executed.price:.2f}  size={order.executed.size}"
            else:
                msg = f"SELL {order.data._name} @ {order.executed.price:.2f}  size={order.executed.size}"
            self.trade_count += 1
            if self.params.log_trades:
                logger.info("trade_executed", msg=msg, trade_num=self.trade_count)

        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            logger.warning("order_failed", status=order.getstatusname())

        self.order = None

    def notify_trade(self, trade):
        if trade.isclosed:
            logger.info(
                "trade_closed",
                pnl=trade.pnl,
                pnl_comm=trade.pnlcomm,
                gross=trade.pnl,
                net=trade.pnlcomm,
            )

    def get_performance_summary(self) -> dict:
        """收集所有分析器结果。"""
        summary = {}
        for name, analyzer in self.analyzers.items():
            analysis = analyzer.get_analysis()
            if isinstance(analysis, dict):
                for k, v in analysis.items():
                    summary[f"{name}_{k}"] = v
            else:
                summary[name] = analysis
        return summary

    def get_equity_curve(self) -> pd.DataFrame:
        """获取权益曲线 DataFrame（通过 observer 收集）。"""
        records = []
        for i in range(len(self)):
            date = self.data.datetime.date(i)
            value = self.broker.getvalue(datas=[self.data])
            cash = self.broker.getcash()
            records.append({
                "date": date,
                "value": value,
                "cash": cash,
            })
        return pd.DataFrame(records)

    def next(self):
        pass  # 子类重写