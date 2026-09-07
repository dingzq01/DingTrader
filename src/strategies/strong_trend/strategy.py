"""Strong Trend V1 策略实现。"""

from src.strategies.research_base import BaseResearchStrategy, StrategyData
from src.strategies.strong_trend.config import STRONG_TREND_V1_CONFIG
from src.strategies.strong_trend.rules import meets_entry_rules, meets_exit_rules


class StrongTrendV1(BaseResearchStrategy):
    """Strong Trend V1: 中期上升趋势 + MACD 动能增强。"""

    strategy_name = STRONG_TREND_V1_CONFIG["strategy_name"]
    strategy_version = STRONG_TREND_V1_CONFIG["strategy_version"]
    strategy_type = STRONG_TREND_V1_CONFIG["strategy_type"]
    description = STRONG_TREND_V1_CONFIG["description"]
    entry_rule = STRONG_TREND_V1_CONFIG["entry_rule"]
    exit_rule = STRONG_TREND_V1_CONFIG["exit_rule"]

    def __init__(self, params: dict | None = None):
        self.params = dict(STRONG_TREND_V1_CONFIG["params"])
        if params:
            self.params.update(params)

    def entry_signal(self, data: StrategyData) -> bool:
        """判断当前数据是否满足买入条件 (T 日收盘信号)。"""
        return meets_entry_rules(data, self.params)

    def exit_signal(self, data: StrategyData) -> bool:
        """判断当前数据是否满足卖出条件 (T 日收盘信号)。"""
        return meets_exit_rules(data)