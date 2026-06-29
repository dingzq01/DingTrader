from src.strategies.base_strategy import BaseStrategy
from src.utils.logging import get_logger

logger = get_logger(__name__)

_strategy_registry: dict[str, BaseStrategy] = {}


def register_strategy(name: str, description: str = ""):
    """策略注册装饰器。

    用法:
        @register_strategy("macd_golden_cross", description="MACD 金叉选股")
        class MACDGoldenCross(BaseStrategy):
            ...
    """
    def decorator(cls):
        instance = cls()
        instance.name = name
        instance.description = description
        _strategy_registry[name] = instance
        logger.info("strategy_registered", name=name)
        return cls
    return decorator


def get_strategy(name: str) -> BaseStrategy | None:
    return _strategy_registry.get(name)


def get_enabled_strategies(configured_names: list[str] | None = None) -> list[BaseStrategy]:
    if configured_names is None:
        from src.config.settings import get_settings
        configured_names = get_settings().strategies.enabled
    return [
        s for name, s in _strategy_registry.items()
        if name in configured_names
    ]


def get_all_strategies() -> dict[str, BaseStrategy]:
    return _strategy_registry