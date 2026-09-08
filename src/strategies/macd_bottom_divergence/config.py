"""MACD Bottom Divergence V1 策略配置。

策略规则复用 stock_state_daily 的 macd_bottom_divergence 状态
（state 层已实现无未来函数的 DIF trough 识别，见 src/data/state_rules.py），
本策略不重复实现底背离算法，只做规则判断。

买卖规则与参数集中管理，不散落在代码中。
"""

MACD_BOTTOM_DIVERGENCE_V1_CONFIG: dict = {
    "strategy_name": "macd_bottom_divergence",
    "strategy_version": "v1",
    "strategy_type": "divergence",
    "description": "MACD底背离：DIF第二底抬高 + 股价Low第二底创新低，确认后买入；MA5<MA10 卖出。",
    "entry_rule": (
        "stock_state_daily.macd_bottom_divergence = true "
        "(MACD DIF 形成两个有效局部低点 M1/M2，DIF[M2] > DIF[M1] 且 "
        "Low[M2] < Low[M1]，信号在 M2 确认日触发，为事件信号)"
    ),
    "exit_rule": "ma5 < ma10",
    "params": {
        # 底背离算法参数由 state 层统一维护（src/data/state_rules.py）。
        # 以下仅记录 V1 生效的实际值：DIF trough 需严格低于左右各 pivot 根K线，
        # 信号最早在 trough + pivot_right 日确认（禁止 look-ahead bias）。
        "pivot_left": 3,
        "pivot_right": 3,
    },
}
