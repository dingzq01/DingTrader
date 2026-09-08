"""MACD Bottom Divergence V1 买入/卖出规则。

买入条件只有 macd_bottom_divergence 事件信号，不叠加任何其他技术条件；
卖出条件只有 ma5 < ma10，不加止损/止盈/持仓天数等复杂退出逻辑（V1 保持简单）。

规则只使用当前交易日 (T 日) 收盘后已经能够获得的数据
（macd_bottom_divergence 来自 stock_state_daily，state 层已保证
信号只会在确认日出现，不会提前使用未来数据）。
"""


def meets_entry_rules(data) -> bool:
    """MACD 底背离 V1 买入规则。

    当日 stock_state_daily.macd_bottom_divergence 为 True（新确认的底背离事件）。
    """
    return bool(data.macd_bottom_divergence)


def meets_exit_rules(data) -> bool:
    """MACD 底背离 V1 卖出规则: ma5 < ma10。"""
    return data.ma5 < data.ma10
