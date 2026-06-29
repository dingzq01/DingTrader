"""资金与仓位管理."""

import backtrader as bt


class FixedPctSizer(bt.Sizer):
    """固定比例仓位: 每笔交易使用总资金的固定百分比."""

    params = (("pct", 0.95),)

    def _getsizing(self, comminfo, cash, data, isbuy):
        if not isbuy:
            return self.broker.getposition(data).size
        available = cash * self.params.pct
        price = data.close[0]
        if price <= 0:
            return 0
        return int(available / price / 100) * 100  # A股100股整数倍


class EqualWeightSizer(bt.Sizer):
    """等权重仓位: 总资金均分给所有活跃策略."""

    params = (("max_positions", 5), ("pct_per_position", 0.2))

    def _getsizing(self, comminfo, cash, data, isbuy):
        if not isbuy:
            return self.broker.getposition(data).size

        position_count = len([d for d in self.strategy.datas if self.strategy.getposition(d).size > 0])
        if position_count >= self.params.max_positions:
            return 0

        available = cash * self.params.pct_per_position
        price = data.close[0]
        return int(available / price / 100) * 100 if price > 0 else 0