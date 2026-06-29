from abc import ABC, abstractmethod
from typing import Any

import pandas as pd


class BaseIndicator(ABC):
    """指标计算基类。

    子类需实现:
        name: 指标名称（用于存储到 stock_indicators 表）
        compute(df, params): 核心计算逻辑
    """

    name: str

    @abstractmethod
    def compute(self, df: pd.DataFrame, **params) -> dict[str, Any]:
        """给定K线DataFrame，返回 {indicator_key: value} 映射。"""
        ...

    def get_required_history(self) -> int:
        """返回计算该指标所需的最小历史K线条数。"""
        return 10