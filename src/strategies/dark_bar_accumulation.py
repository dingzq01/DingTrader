"""
暗盘吸筹策略 (待实现)。

原 TQ 脚本中 dark_bar_accumulation.py 实际运行的是 MACD 金叉策略。
真正的暗盘吸筹逻辑需重新设计。

典型暗盘吸筹特征:
- 连续多日小阴线/小阳线，量能温和
- 价格波动幅度收窄
- 大单吃货痕迹（量价异动）
- 出现在关键支撑位附近

当前为占位实现，后续可根据实际需求补充具体筛选条件。
"""

from typing import Any

import pandas as pd

from src.strategies.base_strategy import BaseStrategy
from src.strategies.registry import register_strategy


@register_strategy("dark_bar_accumulation",
                   description="暗盘吸筹策略 (待完善)")
class DarkBarAccumulation(BaseStrategy):

    def check_conditions(self, df: pd.DataFrame,
                         indicators: dict[str, float]) -> tuple[bool, dict[str, Any] | None]:
        # TODO: 实现暗盘吸筹识别逻辑
        # 当前返回空，不影响系统运行
        return False, None