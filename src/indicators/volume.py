import pandas as pd

from src.indicators.base import BaseIndicator
from src.indicators.registry import register_indicator


class VolumeIndicator(BaseIndicator):
    name = "volume"

    def compute(self, df: pd.DataFrame) -> dict:
        if len(df) < 2:
            return {}

        today_vol = df.iloc[-1]["volume"]
        yesterday_vol = df.iloc[-2]["volume"]
        vol_ratio = today_vol / yesterday_vol if yesterday_vol > 0 else 1.0

        # 近5日均量
        vol_5 = df["volume"].tail(5)
        avg_vol_5 = vol_5.mean()
        vol_ratio_vs_5ma = (
            today_vol / avg_vol_5 if avg_vol_5 > 0 else 1.0
        )

        # 缩量判断 (今日量 < 昨日量)
        is_shrink = 1 if today_vol < yesterday_vol else 0

        return {
            "vol_today": float(today_vol),
            "vol_yesterday": float(yesterday_vol),
            "vol_ratio": round(vol_ratio, 4),
            "vol_avg_5d": round(avg_vol_5, 2),
            "vol_ratio_vs_5ma": round(vol_ratio_vs_5ma, 4),
            "vol_is_shrink": is_shrink,
        }

    def get_required_history(self) -> int:
        return 6


_volume = VolumeIndicator()
register_indicator(_volume)