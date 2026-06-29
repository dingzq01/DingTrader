"""A股统一手续费/印花税/滑点模型."""

import backtrader as bt

from src.config.settings import get_settings


class AStockCommission(bt.CommInfoBase):
    """A股佣金模型: 万2.5手续费 (最低5元) + 千1印花税 (仅卖出)."""

    params = (
        ("commission", 0.00025),   # 万2.5
        ("stamp_tax", 0.001),       # 千1 (仅卖出)
        ("min_commission", 5.0),    # 最低5元
        ("stocklike", True),
        ("commtype", bt.CommInfoBase.COMM_PERC),
    )

    def __init__(self, **kwargs):
        settings = get_settings().backtest
        self.p.commission = kwargs.get("commission", settings.commission_rate)
        self.p.stamp_tax = kwargs.get("stamp_tax", settings.stamp_tax_rate)
        super().__init__()

    def _getcommission(self, size, price, pseudoexec):
        """计算单笔交易佣金."""
        value = abs(size) * price
        commission = max(value * self.p.commission, self.p.min_commission)
        # 印花税仅卖出
        if size < 0:
            commission += value * self.p.stamp_tax
        return commission


class SlippageModel(bt.SlippageBase):
    """固定百分比滑点模型."""

    params = (("slippage_pct", 0.01),)

    def __init__(self):
        settings = get_settings().backtest
        self.p.slippage_pct = settings.slippage
        super().__init__()

    def next(self):
        price = self.data.close[0]
        slippage = price * self.p.slippage_pct
        if self.order.isbuy():
            self.order.executed.price = price + slippage
        else:
            self.order.executed.price = price - slippage