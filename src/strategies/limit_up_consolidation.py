"""
涨停整理策略 (从 Download.py / Dashboard.py 提取逻辑)。

识别条件:
1. N日内 (LOOKBACK_DAYS=5) 出现过涨停
2. 涨停后阴线量能 < 涨停后阳线最大量能
3. 今日缩量 (今日量 < 昨日量)
4. 今日最低价 > 涨停K线90%支撑位
5. 今日量 < 涨停后平均量能
"""

from typing import Any

import numpy as np
import pandas as pd

from src.strategies.base_strategy import BaseStrategy
from src.strategies.registry import register_strategy


@register_strategy("limit_up_consolidation",
                   description="涨停整理策略：涨停后缩量整理不破支撑位")
class LimitUpConsolidation(BaseStrategy):
    LOOKBACK_DAYS = 5
    LIMIT_RATIO = 0.9

    def check_conditions(self, df: pd.DataFrame,
                         indicators: dict[str, float]) -> tuple[bool, dict[str, Any] | None]:
        if indicators.get("limit_up_has") != 1:
            return False, None

        today_low = df.iloc[-1]["low"]
        today_vol = df.iloc[-1]["volume"]
        yesterday_vol = df.iloc[-2]["volume"]

        # 缩量
        if today_vol >= yesterday_vol:
            return False, None

        # 不破90%支撑
        limit_90 = indicators.get("limit_up_90_price", 0)
        if today_low < limit_90:
            return False, None

        # 涨停后阴线量能有效
        if indicators.get("limit_up_yin_valid") != 1:
            return False, None

        # 今日量 < 涨停后平均量
        avg_vol = indicators.get("limit_up_avg_vol_after", yesterday_vol)
        if today_vol >= avg_vol:
            return False, None

        return True, {
            "limit_up_date": indicators.get("limit_up_date", ""),
            "limit_90_price": limit_90,
            "today_low": today_low,
            "current_price": df.iloc[-1]["close"],
        }