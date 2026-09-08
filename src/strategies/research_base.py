"""策略研究与回测的基类接口 (Strategy Layer)。

与 `src/strategies/base_strategy.py` (选股策略, 直接读写数据库) 不同，
本接口服务于「策略研究与回测框架」: Strategy 只负责规则判断，
不依赖 Backtrader / 数据库 / Scanner / SQL。

Strategy 只接收已经准备好的 行情/指标/状态/因子 数据 (StrategyData)，
并判断 entry_signal / exit_signal。

未来可以被共同复用:
    - Backtrader        : historical data -> StrongTrendV1.entry_signal() -> buy()
    - Daily Scanner     : 某日股票数据 -> StrongTrendV1.entry_signal() -> candidate list
    - Position Monitor  : 当前持仓 -> StrongTrendV1.exit_signal() -> SELL ALERT
"""

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
import datetime


@dataclass
class StrategyData:
    """策略输入数据快照 — 某日某只股票准备好的行情/指标/状态/因子数据。

    由数据适配层组装 (Backtrader DataFeed 或未来的 Scanner 均可)，Strategy 只读。

    factor_rank: 0~1 的横截面排名百分位 (越小代表排名越靠前)，
                 未启用 Factor 或该日无因子数据时为 None。

    macd_bottom_divergence: 当日是否新确认 MACD 底背离事件
                  (来自 stock_state_daily，事件信号而非持续状态)。
    """

    trade_date: datetime.date
    stock_code: str
    open: float
    close: float
    ma5: float
    ma10: float
    ma20: float
    ma60: float
    macd_dif: float
    macd_dea: float
    macd_hist: float
    macd_hist_increasing: bool
    macd_hist_increasing_days: int
    macd_bottom_divergence: bool = False
    factor_rank: float | None = None

    def as_dict(self) -> dict:
        """返回 dict 形式，便于记录 entry_context / exit_context 或日志。"""
        return asdict(self)

    def __getitem__(self, key: str):
        return getattr(self, key)


class BaseResearchStrategy(ABC):
    """策略研究与回测基类。

    子类需要提供:
        strategy_name     e.g. "strong_trend"
        strategy_version  e.g. "v1"
        strategy_type     e.g. "trend_following"
        description

    并实现:
        entry_signal(data) -> bool
        exit_signal(data)  -> bool

    约束:
        1. Strategy 不直接操作 Backtrader Order
        2. Strategy 不直接调用数据库
        3. Strategy 不直接修改持仓
        4. Strategy 只负责判断策略条件
    """

    strategy_name: str = ""
    strategy_version: str = ""
    strategy_type: str = ""
    description: str = ""

    # 默认参数 (可通过实例化 params 覆盖)
    params: dict = {}

    def get_full_name(self) -> str:
        """完整策略标识，如 strong_trend_v1。"""
        return f"{self.strategy_name}_{self.strategy_version}"

    def get_params(self) -> dict:
        """返回当前生效的参数配置副本。"""
        return dict(self.params)

    @abstractmethod
    def entry_signal(self, data: StrategyData) -> bool:
        """判断当前数据是否满足买入条件。"""

    @abstractmethod
    def exit_signal(self, data: StrategyData) -> bool:
        """判断当前数据是否满足卖出条件。"""