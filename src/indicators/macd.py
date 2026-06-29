import pandas as pd

from src.indicators.base import BaseIndicator
from src.indicators.registry import register_indicator


class MACDIndicator(BaseIndicator):
    name = "macd"

    def compute(self, df: pd.DataFrame,
                fast: int = 21, slow: int = 55, signal: int = 13) -> dict:
        close = df["close"]
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        dif = ema_fast - ema_slow
        dea = dif.ewm(span=signal, adjust=False).mean()
        histogram = (dif - dea) * 2

        # 金叉检测 (最近一日)
        dif_last2 = dif.iloc[-2:] if len(dif) >= 2 else dif
        dea_last2 = dea.iloc[-2:] if len(dea) >= 2 else dea
        golden_cross = (
            dif_last2.iloc[-2] < dea_last2.iloc[-2]
            and dif_last2.iloc[-1] > dea_last2.iloc[-1]
        ) if len(dif_last2) >= 2 else False

        # DIF/DEA 粘合检测 (连续粘合天数)
        merge_days = 0
        max_merge = 0
        for i in range(len(dif)):
            if abs(dif.iloc[i] - dea.iloc[i]) <= 0.015:
                merge_days += 1
                max_merge = max(max_merge, merge_days)
            else:
                merge_days = 0

        return {
            "macd_dif": round(dif.iloc[-1], 4),
            "macd_dea": round(dea.iloc[-1], 4),
            "macd_histogram": round(histogram.iloc[-1], 4),
            "macd_golden_cross": 1 if golden_cross else 0,
            "macd_max_merge_days": max_merge,
            "macd_dif_above_zero": 1 if dif.iloc[-1] > 0 else 0,
            "macd_dea_above_zero": 1 if dea.iloc[-1] > 0 else 0,
        }

    def get_required_history(self) -> int:
        return 80  # slow=55 + signal=13 need enough bars


# 实例化并注册
_macd = MACDIndicator()
register_indicator(_macd)