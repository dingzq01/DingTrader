"""OBV (On-Balance Volume) 能量潮指标."""

import pandas as pd

from src.indicators.base import BaseIndicator
from src.indicators.registry import register_indicator


class OBVIndicator(BaseIndicator):
    name = "obv"

    def compute(self, df: pd.DataFrame) -> dict:
        """计算 OBV 累积量。

        OBV 规则:
            - 今日收盘 > 昨日收盘: OBV = 前OBV + 今日量
            - 今日收盘 < 昨日收盘: OBV = 前OBV - 今日量
            - 今日收盘 == 昨日收盘: OBV = 前OBV
        """
        if len(df) < 2:
            return {}

        obv_series = self._calc_obv(df)

        return {
            "obv": float(obv_series.iloc[-1]),
            "obv_prev": float(obv_series.iloc[-2]) if len(obv_series) >= 2 else 0,
            "obv_change": float(obv_series.iloc[-1] - obv_series.iloc[-2]) if len(obv_series) >= 2 else 0,
        }

    def _calc_obv(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        volume = df["volume"]
        direction = pd.Series(0, index=close.index)
        direction[close > close.shift(1)] = 1
        direction[close < close.shift(1)] = -1
        obv = (direction * volume).cumsum()
        return obv

    def get_required_history(self) -> int:
        return 2


_obv = OBVIndicator()
register_indicator(_obv)