"""因子评分配置 — 集中管理所有因子权重与状态对应分值。

禁止将评分数字直接写在 Python 业务代码中。
所有因子规则必须从这里读取。

结构:
    STOCK_FACTOR_CONFIG = {
        "<factor_key>": {
            "column": "<数据库列名>",
            "weight": <float>,       # 仅评分因子有此字段；risk_factor 无 weight
            "rules": {
                "<state_column>": <int>,  # 状态满足时加分/扣分
            }
        },
        ...
    }
"""

STOCK_FACTOR_CONFIG: dict[str, dict] = {

    "trend_factor": {
        "column": "trend_score",
        "weight": 0.30,
        "rules": {
            "price_above_ma20": 20,
            "price_above_ma60": 20,
            "ma5_above_ma20": 20,
            "ma20_above_ma60": 20,
            "trend_mid_bull": 20,
        },
    },

    "momentum_factor": {
        "column": "momentum_score",
        "weight": 0.20,
        "rules": {
            "macd_bullish": 40,
            "macd_hist_increasing": 30,
            "kdj_golden_cross": 30,
        },
    },

    "capital_factor": {
        "column": "capital_score",
        "weight": 0.25,
        "rules": {
            "capital_bullish": 30,
            "capital_cross_up": 20,
            "capital_life_up": 20,
            "obv_rising": 15,
            "obv_above_ma20": 15,
        },
    },

    "volume_price_factor": {
        "column": "volume_price_score",
        "weight": 0.15,
        "rules": {
            "volume_expand": 50,
            "price_volume_confirm": 50,
        },
    },

    "breakout_factor": {
        "column": "breakout_score",
        "weight": 0.10,
        "rules": {
            "high_break_20": 30,
            "high_break_60": 40,
            "new_high": 30,
        },
    },

    "risk_factor": {
        "column": "risk_penalty",
        # 风险因子没有 weight — 单独计算并入库，不加入 total_score（扣分逻辑保留，仅不计入总分）
        "rules": {
            "extreme_up": -20,
            "extreme_down": -30,
            "high_volatility": -20,
        },
    },
}


def get_all_state_columns() -> list[str]:
    """返回所有因子依赖的 state 列名（用于从 stock_state_daily 读取时做列过滤）。"""
    cols: set[str] = set()
    for cfg in STOCK_FACTOR_CONFIG.values():
        cols.update(cfg["rules"].keys())
    return sorted(cols)