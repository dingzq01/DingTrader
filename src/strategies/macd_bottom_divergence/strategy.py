"""MACD Bottom Divergence V1 策略实现。"""

from src.strategies.macd_bottom_divergence.config import MACD_BOTTOM_DIVERGENCE_V1_CONFIG
from src.strategies.macd_bottom_divergence.rules import meets_entry_rules, meets_exit_rules
from src.strategies.research_base import BaseResearchStrategy, StrategyData


class MACDBottomDivergenceV1(BaseResearchStrategy):
    """MACD Bottom Divergence V1: DIF 底背离事件买入 + MA5<MA10 卖出。"""

    strategy_name = MACD_BOTTOM_DIVERGENCE_V1_CONFIG["strategy_name"]
    strategy_version = MACD_BOTTOM_DIVERGENCE_V1_CONFIG["strategy_version"]
    strategy_type = MACD_BOTTOM_DIVERGENCE_V1_CONFIG["strategy_type"]
    description = MACD_BOTTOM_DIVERGENCE_V1_CONFIG["description"]
    entry_rule = MACD_BOTTOM_DIVERGENCE_V1_CONFIG["entry_rule"]
    exit_rule = MACD_BOTTOM_DIVERGENCE_V1_CONFIG["exit_rule"]

    def __init__(self, params: dict | None = None):
        self.params = dict(MACD_BOTTOM_DIVERGENCE_V1_CONFIG["params"])
        if params:
            self.params.update(params)

    def entry_signal(self, data: StrategyData) -> bool:
        """判断当前数据是否满足买入条件 (T 日收盘信号)。"""
        return meets_entry_rules(data)

    def exit_signal(self, data: StrategyData) -> bool:
        """判断当前数据是否满足卖出条件 (T 日收盘信号)。"""
        return meets_exit_rules(data)
