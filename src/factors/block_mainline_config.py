"""板块主线每日配置模块 (Block Mainline Config)。

集中管理 block_mainline_daily 的评分权重、状态机规则。
"""

# --- Mainline Strength Score Weights ---
MAINLINE_SCORE_CONFIG = {
    "default": {"today_weight": 0.20, "ma5_weight": 0.40, "ma20_weight": 0.40},
    "market":  {"today_weight": 0.15, "ma5_weight": 0.35, "ma20_weight": 0.50},
}

# --- Status Machine Rules ---
# 按优先级逐行检查，第一个匹配的状态即为当日状态。
# 条件键名含义：
#   mainline_rank_gt:      mainline_rank > 指定值
#   mainline_rank_le:      mainline_rank <= 指定值
#   score_ma5_ge_ma20:    score_ma5 >= score_ma20
#   score_ma5_lt_ma20:    score_ma5 < score_ma20
#   rank_change_5d_gt:     rank_change_5d > 指定值
#   rank_change_5d_lt:     rank_change_5d < 指定值
STATUS_RULES = {
    "EXIT":     {"mainline_rank_gt": 50, "score_ma5_lt_ma20": True},
    "WEAKEN":   {"mainline_rank_gt": 20, "rank_change_5d_gt": 10},
    "MAINLINE": {"mainline_rank_le": 20,  "score_ma5_ge_ma20": True},
    "WATCH":    {"mainline_rank_le": 50},
    "DISCOVER": {"rank_change_5d_lt": -20},
}

# 优先级从高到低：EXIT 优先判定
STATUS_PRIORITY = ["EXIT", "WEAKEN", "MAINLINE", "WATCH", "DISCOVER"]

# --- Lookback ---
LOOKBACK_DAYS = 60

# --- Factor Version ---
FACTOR_VERSION = "v1.0"