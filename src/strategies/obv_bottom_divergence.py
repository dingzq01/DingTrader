"""
OBV底背离策略。

筛选条件:
1. MACD 21/55/13 最近3个交易日内有金叉，且当天 DIF > DEA
2. 金叉点距离前面的死叉 >= 5个交易日
3. 当天 OBV > 死叉时的 OBV

底背离含义: 价格创新低但OBV未创新低（或OBV已回升），配合MACD金叉确认反转。
"""

from typing import Any

import pandas as pd

from src.strategies.base_strategy import BaseStrategy
from src.strategies.registry import register_strategy


@register_strategy("obv_bottom_divergence",
                   description="OBV底背离策略：MACD 3日内金叉 + 金叉距死叉>=5日 + OBV>死叉时OBV")
class OBVBottomDivergence(BaseStrategy):
    FAST = 21
    SLOW = 55
    SIGNAL = 13
    GOLDEN_CROSS_WINDOW = 3      # 最近3个交易日内金叉
    MIN_CROSS_DISTANCE = 5       # 金叉距死叉最少5个交易日
    LOOKBACK = 120               # 回溯K线数量

    def _calc_macd_series(self, df: pd.DataFrame):
        """计算 MACD 序列。"""
        close = df["close"]
        ema_fast = close.ewm(span=self.FAST, adjust=False).mean()
        ema_slow = close.ewm(span=self.SLOW, adjust=False).mean()
        dif = ema_fast - ema_slow
        dea = dif.ewm(span=self.SIGNAL, adjust=False).mean()
        return dif, dea

    def _calc_obv_series(self, df: pd.DataFrame) -> pd.Series:
        """计算 OBV 序列。"""
        close = df["close"]
        volume = df["volume"]
        direction = pd.Series(0, index=close.index)
        direction[close > close.shift(1)] = 1
        direction[close < close.shift(1)] = -1
        return (direction * volume).cumsum()

    def _find_golden_cross_in_window(self, dif: pd.Series, dea: pd.Series) -> int | None:
        """在最近 GOLDEN_CROSS_WINDOW 个交易日内查找最近的金叉点位置。

        从最新的K线开始向前扫描，找到第一个（最近的）金叉。
        金叉定义: 前一天 DIF < DEA 且当天 DIF > DEA。

        Returns: 金叉点在序列中的索引位置（0-based），未找到返回 None。
        """
        n = self.GOLDEN_CROSS_WINDOW
        if len(dif) < n + 1:
            return None

        # 从倒数第2根K线开始，扫描到 -n-1
        for offset in range(-1, -(n + 1), -1):
            prev_dif = dif.iloc[offset - 1]
            prev_dea = dea.iloc[offset - 1]
            curr_dif = dif.iloc[offset]
            curr_dea = dea.iloc[offset]
            if prev_dif < prev_dea and curr_dif > curr_dea:
                # 返回当前offset对应的实际索引
                return len(dif) + offset  # offset是负值，转为正索引
        return None

    def _find_previous_death_cross(self, dif: pd.Series, dea: pd.Series,
                                    golden_cross_idx: int) -> int | None:
        """从金叉点向前查找最近的一个死叉。

        死叉定义: 前一天 DIF > DEA 且当天 DIF < DEA。

        Returns: 死叉点的索引位置（0-based），未找到返回 None。
        """
        # 从金叉前一根K线开始向前扫描
        for i in range(golden_cross_idx - 1, 0, -1):
            prev_dif = dif.iloc[i - 1]
            prev_dea = dea.iloc[i - 1]
            curr_dif = dif.iloc[i]
            curr_dea = dea.iloc[i]
            if prev_dif > prev_dea and curr_dif < curr_dea:
                return i
        return None

    def check_conditions(self, df: pd.DataFrame,
                         indicators: dict[str, float]) -> tuple[bool, dict[str, Any] | None]:
        if len(df) < self.LOOKBACK:
            return False, None

        # 1. 计算 MACD 序列
        dif, dea = self._calc_macd_series(df)

        # 2. 当天 DIF > DEA
        dif_now = dif.iloc[-1]
        dea_now = dea.iloc[-1]
        if dif_now <= dea_now:
            return False, None

        # 3. 在最近3个交易日内找金叉
        golden_idx = self._find_golden_cross_in_window(dif, dea)
        if golden_idx is None:
            return False, None

        # 4. 从金叉点向前找前一个死叉
        death_idx = self._find_previous_death_cross(dif, dea, golden_idx)
        if death_idx is None:
            return False, None

        # 5. 检查金叉距死叉距离 >= 5个交易日
        distance = golden_idx - death_idx
        if distance < self.MIN_CROSS_DISTANCE:
            return False, None

        # 6. 计算 OBV 序列
        obv_series = self._calc_obv_series(df)
        obv_today = obv_series.iloc[-1]
        obv_at_death = obv_series.iloc[death_idx]

        # 7. 当天 OBV > 死叉时 OBV
        if obv_today <= obv_at_death:
            return False, None

        return True, {
            "dif": round(dif_now, 4),
            "dea": round(dea_now, 4),
            "golden_cross_idx": golden_idx,
            "death_cross_idx": death_idx,
            "cross_distance": distance,
            "obv_today": round(obv_today, 2),
            "obv_at_death": round(obv_at_death, 2),
            "obv_divergence_ratio": round(obv_today / obv_at_death, 4) if obv_at_death > 0 else 0,
        }