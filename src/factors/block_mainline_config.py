"""板块主线每日配置模块 (Block Mainline Config)。

集中管理 block_mainline_daily 的所有阈值和参数。
Direction Engine — 在 Block Factor 和 Stock Signal 之间。
"""

# --- Mainline Score Weights ---
MAINLINE_TODAY_WEIGHT = 0.50
MAINLINE_AVG5D_WEIGHT = 0.30
MAINLINE_CAPITAL_WEIGHT = 0.20

# --- State Machine Thresholds (普通板块) ---
DISCOVER_SCORE_THRESHOLD = 70        # 进入 DISCOVER 的最低分
DISCOVER_TOP_N = 10                  # 首次进入 TOP N
WATCH_CONSECUTIVE_DAYS = 2           # DISCOVER 连续 N 天 → WATCH
MAINLINE_CONSECUTIVE_DAYS = 3        # WATCH 连续 N 天 → MAINLINE
MAINLINE_SCORE_THRESHOLD = 80        # 进入 MAINLINE 的最低分
MAINLINE_CONFIDENCE_THRESHOLD = 70   # 进入 MAINLINE 的最低置信度
WEAKEN_DECLINE_DAYS = 2              # 连续下降 N 天 → WEAKEN
WEAKEN_SCORE_THRESHOLD = 70          # 分数低于此 → WEAKEN
EXIT_CONSECUTIVE_DAYS = 2            # WEAKEN 连续 N 天 → EXIT
EXIT_SCORE_THRESHOLD = 60            # 分数低于此 → EXIT

# --- Market State Thresholds ---
BULL_SCORE_THRESHOLD = 75            # 进入 BULL 的最低分
BULL_ENTER_CONSECUTIVE_DAYS = 5      # 连续 N 天 ≥ 阈值 → BULL
BULL_EXIT_CONSECUTIVE_DAYS = 3       # 连续 N 天 < 退出阈值 → 退出 BULL
BULL_EXIT_SCORE_THRESHOLD = 65       # BULL 的退出阈值 (滞回)
RANGE_LOW = 50                       # RANGE 区间下限
RANGE_HIGH = 65                      # RANGE 区间上限
RANGE_CONSECUTIVE_DAYS = 5           # 连续 N 天在区间内 → RANGE
BEAR_SCORE_THRESHOLD = 40            # 进入 BEAR 的最高分
BEAR_CONSECUTIVE_DAYS = 5            # 连续 N 天 ≤ 阈值 → BEAR
NORMAL_HIGH = 75                     # NORMAL 区间上限 (自动回归)

# --- Market Exit Thresholds (滞回) ---
RANGE_EXIT_CONSECUTIVE_DAYS = 3      # RANGE → NORMAL 连续 N 天在区间外
BEAR_EXIT_CONSECUTIVE_DAYS = 3       # BEAR → NORMAL 连续 N 天 ≥ 阈值

# --- Confidence Weights ---
CONFIDENCE_RANK_WEIGHT = 0.40
CONFIDENCE_CONTINUITY_WEIGHT = 0.30
CONFIDENCE_SCORE_STABILITY_WEIGHT = 0.20
CONFIDENCE_MARKET_ENV_WEIGHT = 0.10

# --- Market Environment Bonus (绝对值, 加到 0-100 置信度分数中) ---
MARKET_ENV_BULL_BONUS = 10
MARKET_ENV_NORMAL_BONUS = 5
MARKET_ENV_RANGE_BONUS = 0
MARKET_ENV_BEAR_BONUS = -10

# --- LOOKBACK ---
HISTORY_LOOKBACK_DAYS = 30  # 回溯天数，确保有足够历史数据
AVG_WINDOW_DAYS = 5         # 5日均值/标准差窗口

# --- Position Ratio Mapping (Market only) ---
POSITION_RATIO_MAP = {
    "BULL": 100,
    "NORMAL": 70,
    "RANGE": 30,
    "BEAR": 0,
}

# --- Factor Version ---
FACTOR_VERSION = "v1.0"