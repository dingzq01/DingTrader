"""Strong Trend V1 策略配置。

规则和参数集中管理，不散落在代码中。
A/B 实验通过 use_factor 参数切换，不复制策略代码。
"""

STRONG_TREND_V1_CONFIG: dict = {
    "strategy_name": "strong_trend",
    "strategy_version": "v1",
    "strategy_type": "trend_following",
    "description": "选择处于中期上升趋势，并且 MACD 动能正在增强的股票。",
    "entry_rule": (
        "close > ma20 AND ma20 > ma60 AND macd_dif > macd_dea "
        "AND macd_hist_increasing AND macd_hist_increasing_days > 3"
        "[AND factor_rank <= factor_rank_max (仅 use_factor=true 时启用)]"
    ),
    "exit_rule": "ma5 < ma10",
    "params": {
        # 是否启用 Factor 过滤 (V1-A: false, V1-B: true)
        "use_factor": False,
        # factor_rank 百分位上限 (top 30%)
        "factor_rank_max": 0.30,
        # macd_hist 连续增加天数严格大于该值 (>3 即至少 4 天)
        "macd_hist_increasing_days_min": 3,
    },
}