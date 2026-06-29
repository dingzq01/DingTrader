import pandas as pd

from src.indicators.base import BaseIndicator
from src.indicators.registry import register_indicator


class LimitUpIndicator(BaseIndicator):
    name = "limit_up"

    def compute(self, df: pd.DataFrame, lookback_days: int = 5) -> dict:
        """检测N日内是否出现过涨停及涨停后量能特征。

        Returns:
            has_limit_up: 近期是否有涨停 (0/1)
            limit_up_date: 最近涨停日期索引
            limit_up_vol: 涨停日量能
            after_limit_up_volume_ratio: 涨停后量能/涨停日量能均值
        """
        if len(df) < lookback_days + 1:
            return {"limit_up_has": 0}

        has_limit_up = False
        limit_up_vol = 0.0
        limit_up_position = 0
        limit_up_high = 0.0
        limit_up_low = 0.0
        limit_up_date = None

        for i in range(1, lookback_days + 1):
            if len(df) < i + 2:
                continue
            bar = df.iloc[-i - 1]
            bar_pre_close = df.iloc[-i - 2]["close"]
            if bar_pre_close <= 0:
                continue

            limit_up_price = round(bar_pre_close * 1.1, 2)
            if (abs(bar["close"] - limit_up_price) < 0.01
                    and bar["open"] <= limit_up_price):
                has_limit_up = True
                limit_up_position = i
                limit_up_vol = bar["volume"]
                limit_up_high = bar["high"]
                limit_up_low = bar["low"]
                limit_up_date = str(df.iloc[-i - 1]["date"])
                break

        if not has_limit_up:
            return {"limit_up_has": 0}

        # 涨停后平均量能
        after_volumes = []
        for j in range(limit_up_position - 1, 0, -1):
            idx = -j - 1
            if abs(idx) < len(df):
                after_volumes.append(df.iloc[idx]["volume"])

        avg_vol_after = (
            sum(after_volumes) / len(after_volumes)
            if after_volumes
            else limit_up_vol
        )

        # 涨停后阴线量 < 涨停后阳线最大量
        yang_vols = []
        check_start = -limit_up_position
        for k in range(check_start, 0):
            if abs(k) < len(df):
                bar = df.iloc[k]
                if bar["close"] >= bar["open"]:
                    yang_vols.append(bar["volume"])

        max_yang_vol = max(yang_vols) if yang_vols else limit_up_vol
        all_yin_valid = True
        for k in range(check_start, 0):
            if abs(k) < len(df):
                bar = df.iloc[k]
                if bar["close"] < bar["open"] and bar["volume"] > max_yang_vol:
                    all_yin_valid = False
                    break

        # 90% 支撑位
        limit_90_price = (limit_up_high - limit_up_low) * 0.9 + limit_up_low

        return {
            "limit_up_has": 1,
            "limit_up_position": limit_up_position,
            "limit_up_vol": round(limit_up_vol, 2),
            "limit_up_avg_vol_after": round(avg_vol_after, 2),
            "limit_up_yin_valid": 1 if all_yin_valid else 0,
            "limit_up_90_price": round(limit_90_price, 2),
            "limit_up_date": str(limit_up_date) if limit_up_date else "",
        }

    def get_required_history(self) -> int:
        return 7


# 实例化并注册
_limit_up = LimitUpIndicator()
register_indicator(_limit_up)