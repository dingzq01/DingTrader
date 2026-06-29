from src.indicators.base import BaseIndicator
from src.utils.logging import get_logger

logger = get_logger(__name__)

_indicator_registry: dict[str, BaseIndicator] = {}


def register_indicator(indicator: BaseIndicator):
    """在模块加载时注册指标。"""
    _indicator_registry[indicator.name] = indicator
    logger.debug("indicator_registered", name=indicator.name)


def get_indicator(name: str) -> BaseIndicator | None:
    return _indicator_registry.get(name)


def get_all_indicators() -> dict[str, BaseIndicator]:
    return _indicator_registry


def compute_for_stock(df, indicator_names: list[str] | None = None) -> dict[str, float]:
    """对单只股票的K线DataFrame计算指定指标（或全部已注册指标）。

    Returns: {indicator_name: value} 字典。
    """
    names = indicator_names or list(_indicator_registry.keys())
    results = {}
    for name in names:
        indicator = _indicator_registry.get(name)
        if indicator is None:
            continue
        values = indicator.compute(df)
        # 展平嵌套指标（如 MACD 的 dif/dea/histogram）
        for key, val in values.items():
            results[key] = val
    return results