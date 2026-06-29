"""
MACD 21/55/13 金叉策略 (从 dark_bar_accumulation.py / limit_up_drop_strategy.py 提取).

两组条件 (满足任一组即可):
A组: DIF/DEA 粘合 >= 5日 + 7日内金叉
B组: DIF >= 0 且 DEA >= 0 + 7日内金叉
"""

from typing import Any

import pandas as pd

from src.strategies.base_strategy import BaseStrategy
from src.strategies.registry import register_strategy


@register_strategy("macd_golden_cross",
                   description="MACD 21/55/13 金叉策略")
class MACDGoldenCross(BaseStrategy):
    MERGE_THRESHOLD = 0.015
    MERGE_DAYS = 5
    GOLDEN_CROSS_DAYS = 7

    def _check_golden_cross_recent(self, df: pd.DataFrame) -> bool:
        """最近 GOLDEN_CROSS_DAYS 个交易日内是否出现金叉。"""
        # 这里简单使用从 indicators 预计算的 golden_cross 标记
        # 但由于要检查7日内金叉，需要更详细的检查逻辑
        close = df["close"]
        ema_fast = close.ewm(span=21, adjust=False).mean()
        ema_slow = close.ewm(span=55, adjust=False).mean()
        dif = ema_fast - ema_slow
        dea = dif.ewm(span=13, adjust=False).mean()

        n = self.GOLDEN_CROSS_DAYS
        if len(dif) < n + 1 or len(dea) < n + 1:
            return False

        for i in range(-1, -(n + 1), -1):
            try:
                if dif.iloc[i - 1] < dea.iloc[i - 1] and dif.iloc[i] > dea.iloc[i]:
                    return True
            except IndexError:
                continue
        return False

    def check_conditions(self, df: pd.DataFrame,
                         indicators: dict[str, float]) -> tuple[bool, dict[str, Any] | None]:
        dif_now = indicators.get("macd_dif", 0)
        dea_now = indicators.get("macd_dea", 0)
        max_merge = indicators.get("macd_max_merge_days", 0)

        golden_cross = self._check_golden_cross_recent(df)

        if not golden_cross:
            return False, None

        # A组: 粘合 + 金叉
        group_a = max_merge >= self.MERGE_DAYS

        # B组: 0轴上方 + 金叉
        dif_above = indicators.get("macd_dif_above_zero", 0) == 1
        dea_above = indicators.get("macd_dea_above_zero", 0) == 1
        group_b = dif_above and dea_above

        if group_a or group_b:
            return True, {
                "dif": dif_now,
                "dea": dea_now,
                "group": "A" if group_a else "B",
                "max_merge_days": max_merge,
            }

        return False, None