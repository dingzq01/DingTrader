"""板块每日主线计算模块 (Block Mainline / Direction Engine)。

从 block_factor_daily 读取板块因子评分，应用状态机确定板块主线状态。
Market 作为特殊板块使用独立的大盘状态机 (BULL/NORMAL/RANGE/BEAR)。

数据来源：block_factor_daily。
"""

import datetime
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import text

from src.config.settings import get_settings
from src.factors.block_mainline_config import (
    AVG_WINDOW_DAYS,
    BEAR_CONSECUTIVE_DAYS,
    BEAR_EXIT_CONSECUTIVE_DAYS,
    BEAR_SCORE_THRESHOLD,
    BULL_ENTER_CONSECUTIVE_DAYS,
    BULL_EXIT_CONSECUTIVE_DAYS,
    BULL_EXIT_SCORE_THRESHOLD,
    BULL_SCORE_THRESHOLD,
    CONFIDENCE_CONTINUITY_WEIGHT,
    CONFIDENCE_MARKET_ENV_WEIGHT,
    CONFIDENCE_RANK_WEIGHT,
    CONFIDENCE_SCORE_STABILITY_WEIGHT,
    DISCOVER_SCORE_THRESHOLD,
    DISCOVER_TOP_N,
    EXIT_CONSECUTIVE_DAYS,
    EXIT_SCORE_THRESHOLD,
    FACTOR_VERSION,
    HISTORY_LOOKBACK_DAYS,
    MAINLINE_AVG5D_WEIGHT,
    MAINLINE_CAPITAL_WEIGHT,
    MAINLINE_CONFIDENCE_THRESHOLD,
    MAINLINE_CONSECUTIVE_DAYS,
    MAINLINE_SCORE_THRESHOLD,
    MAINLINE_TODAY_WEIGHT,
    MARKET_ENV_BEAR_BONUS,
    MARKET_ENV_BULL_BONUS,
    MARKET_ENV_NORMAL_BONUS,
    MARKET_ENV_RANGE_BONUS,
    POSITION_RATIO_MAP,
    RANGE_CONSECUTIVE_DAYS,
    RANGE_EXIT_CONSECUTIVE_DAYS,
    RANGE_HIGH,
    RANGE_LOW,
    WATCH_CONSECUTIVE_DAYS,
    WEAKEN_DECLINE_DAYS,
    WEAKEN_SCORE_THRESHOLD,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# 需要从 block_factor_daily 读取的列
# ---------------------------------------------------------------------------
_READ_COLS = [
    "trade_date",
    "block_code",
    "block_name",
    "block_type",
    "total_score",
    "capital_score",
    "market_rank",
    "market_state",
]

# 需要从已有 block_mainline_daily 读取的状态列
_STATE_COLS = [
    "trade_date",
    "block_code",
    "mainline_score",
    "mainline_status",
    "continuous_days",
    "first_mainline_date",
    "peak_score",
    "mainline_round",
]

# ---------------------------------------------------------------------------
# INSERT SQL
# ---------------------------------------------------------------------------
_SCORE_COLS = [
    "mainline_score",
    "mainline_status",
    "continuous_days",
    "confidence",
    "tradeable",
    "priority",
    "position_ratio",
    "avg_score_5d",
    "avg_rank_5d",
    "first_mainline_date",
    "peak_score",
    "mainline_round",
    "reason",
]

_INSERT_COLS = ",\n    ".join(_SCORE_COLS)
_UPDATE_COLS = ",\n    ".join(f"{c} = EXCLUDED.{c}" for c in _SCORE_COLS)
_PARAM_COLS = ",\n    ".join(f":{c}" for c in _SCORE_COLS)

_INSERT_SQL = f"""
INSERT INTO block_mainline_daily (
    trade_date, block_code, block_name, block_type,
    {_INSERT_COLS},
    factor_version, created_at
) VALUES (
    :trade_date, :block_code, :block_name, :block_type,
    {_PARAM_COLS},
    :factor_version, CURRENT_TIMESTAMP
)
ON CONFLICT (trade_date, block_code) DO UPDATE SET
    block_name = EXCLUDED.block_name,
    block_type = EXCLUDED.block_type,
    {_UPDATE_COLS},
    factor_version = EXCLUDED.factor_version,
    created_at = EXCLUDED.created_at
"""

_READ_FACTOR_SQL = f"""
SELECT {", ".join(_READ_COLS)}
FROM block_factor_daily
WHERE trade_date > :lookback_date
ORDER BY trade_date, block_code
"""

_READ_PREV_MAINLINE_SQL = f"""
SELECT {", ".join(_STATE_COLS)}
FROM block_mainline_daily
WHERE trade_date > :lookback_date
ORDER BY trade_date, block_code
"""


# ---------------------------------------------------------------------------
# 公开计算函数
# ---------------------------------------------------------------------------


def calc_mainline_score(
    today_total_score: float,
    avg_score_5d: float,
    capital_score: float,
) -> float:
    """计算主线综合评分。

    today_total_score × 0.50 + avg_score_5d × 0.30 + capital_score × 0.20
    Clamped 0-100。
    """
    raw = (
        today_total_score * MAINLINE_TODAY_WEIGHT
        + avg_score_5d * MAINLINE_AVG5D_WEIGHT
        + (capital_score or 0.0) * MAINLINE_CAPITAL_WEIGHT
    )
    return max(0.0, min(100.0, raw))


def calc_avg_score_5d(
    prev_scores: list[float],
    today_total_score: float,
) -> float:
    """计算 5 日均分。

    使用历史 mainline_score（已有）+ 今日 total_score（今日 mainline_score 尚未计算）。
    如果历史数据不足 5 天，使用已有的数据计算均值。
    """
    scores = prev_scores[-4:] + [today_total_score]
    return float(np.mean(scores))


def calc_avg_rank_5d(ranks: list[int]) -> float:
    """计算 5 日均排名。"""
    if not ranks:
        return 50.0  # 默认中间值
    return float(np.mean(ranks))


def calc_confidence(
    avg_rank_5d: float,
    continuous_days: int,
    mainline_scores_5d: list[float],
    market_status: Optional[str],
) -> float:
    """计算置信度 (0-100)。

    四个组成部分：
    1. 排名稳定性 (40%)：rank ≤ 3 → 100, ≤ 5 → 80, ≤ 10 → 60, ≤ 20 → 40, > 20 → 20
    2. 连续性 (30%)：MAINLINE 天数 ≥ 10 → 100, ≥ 7 → 80, ≥ 5 → 60, ≥ 3 → 40, < 3 → 20
    3. Score 稳定性 (20%)：score = 100 - min(std * 5, 80)
    4. 市场环境 (10%)：BULL +10, NORMAL +5, RANGE 0, BEAR -10
    """
    # 1. 排名稳定性
    if avg_rank_5d <= 3:
        rank_score = 100.0
    elif avg_rank_5d <= 5:
        rank_score = 80.0
    elif avg_rank_5d <= 10:
        rank_score = 60.0
    elif avg_rank_5d <= 20:
        rank_score = 40.0
    else:
        rank_score = 20.0

    # 2. 连续性
    if continuous_days >= 10:
        continuity_score = 100.0
    elif continuous_days >= 7:
        continuity_score = 80.0
    elif continuous_days >= 5:
        continuity_score = 60.0
    elif continuous_days >= 3:
        continuity_score = 40.0
    else:
        continuity_score = 20.0

    # 3. Score 稳定性
    if len(mainline_scores_5d) >= 2:
        std_val = float(np.std(mainline_scores_5d))
        score_stability = 100.0 - min(std_val * 5, 80.0)
    else:
        score_stability = 50.0  # 数据不足时中性

    # 4. 市场环境 bonus
    market_bonus_map = {
        "BULL": MARKET_ENV_BULL_BONUS,
        "NORMAL": MARKET_ENV_NORMAL_BONUS,
        "RANGE": MARKET_ENV_RANGE_BONUS,
        "BEAR": MARKET_ENV_BEAR_BONUS,
    }
    market_bonus = market_bonus_map.get(market_status or "", 0.0)

    confidence = (
        rank_score * CONFIDENCE_RANK_WEIGHT
        + continuity_score * CONFIDENCE_CONTINUITY_WEIGHT
        + score_stability * CONFIDENCE_SCORE_STABILITY_WEIGHT
        + market_bonus
    )
    return max(0.0, min(100.0, confidence))


def calc_block_status(
    prev_status: Optional[str],
    prev_continuous_days: int,
    mainline_score: float,
    confidence: float,
    market_rank: int,
    prev_mainline_scores: list[float],
) -> tuple[str, int]:
    """普通板块状态机：DISCOVER → WATCH → MAINLINE → WEAKEN → EXIT。

    状态必须按顺序迁移，升级可以慢，降级可以快，但不跳级。
    WEAKEN 只能走向 EXIT（无恢复路径），板块退出后重新从 DISCOVER 开始。
    """
    # 首日或无前序状态 / EXIT → 重新判断
    if prev_status is None or prev_status == "EXIT":
        if mainline_score >= DISCOVER_SCORE_THRESHOLD and market_rank <= DISCOVER_TOP_N:
            return ("DISCOVER", 1)
        else:
            return ("EXIT", 0)

    if prev_status == "DISCOVER":
        # 失去 DISCOVER 条件 → EXIT
        if mainline_score < DISCOVER_SCORE_THRESHOLD or market_rank > DISCOVER_TOP_N:
            return ("EXIT", 0)
        # 连续满足条件 (prev_continuous_days 为之前已连续的天数)
        if prev_continuous_days >= WATCH_CONSECUTIVE_DAYS:
            return ("WATCH", 1)
        return ("DISCOVER", prev_continuous_days + 1)

    if prev_status == "WATCH":
        # 需要持续满足 DISCOVER 基本条件
        if mainline_score < DISCOVER_SCORE_THRESHOLD or market_rank > DISCOVER_TOP_N:
            return ("EXIT", 0)
        # 满足 MAINLINE 升级条件
        if (
            prev_continuous_days >= MAINLINE_CONSECUTIVE_DAYS
            and mainline_score >= MAINLINE_SCORE_THRESHOLD
            and confidence >= MAINLINE_CONFIDENCE_THRESHOLD
        ):
            return ("MAINLINE", 1)
        return ("WATCH", prev_continuous_days + 1)

    if prev_status == "MAINLINE":
        # 检查 WEAKEN 条件：连续 2 天下降
        is_declining = False
        if len(prev_mainline_scores) >= 2:
            # prev_mainline_scores 最后一个是昨天，再往前是前天
            yesterday = prev_mainline_scores[-1]
            day_before = prev_mainline_scores[-2]
            if mainline_score < yesterday and yesterday < day_before:
                is_declining = True

        if is_declining or mainline_score < WEAKEN_SCORE_THRESHOLD:
            return ("WEAKEN", 1)
        return ("MAINLINE", prev_continuous_days + 1)

    if prev_status == "WEAKEN":
        # 分数过差 → 直接 EXIT
        if mainline_score < EXIT_SCORE_THRESHOLD:
            return ("EXIT", 0)
        # 连续 WEAKEN 达到天数 → EXIT
        if prev_continuous_days >= EXIT_CONSECUTIVE_DAYS:
            return ("EXIT", 0)
        return ("WEAKEN", prev_continuous_days + 1)

    # fallback
    return ("EXIT", 0)


def _count_streak(scores: list[float], condition_fn) -> int:
    """统计从最近向前的连续满足条件的天数。"""
    streak = 0
    for s in reversed(scores):
        if condition_fn(s):
            streak += 1
        else:
            break
    return streak


def calc_market_status(
    prev_status: Optional[str],
    prev_continuous_days: int,
    market_score: float,
    prev_scores: list[float],
) -> tuple[str, int]:
    """大盘状态机：BULL / NORMAL / RANGE / BEAR。

    采用滞回 (Hysteresis) 避免震荡市来回切换。
    - 进入需连续 N 天满足进入条件（从 prev_scores 中倒推连续天数）
    - 退出需连续 M 天满足退出条件（阈值与进入不同）
    """
    all_scores = prev_scores + [market_score]

    # 首日：始终从 NORMAL 开始，通过连续天数自然进入其他状态
    if prev_status is None:
        return ("NORMAL", 1)

    # --- BULL 退出 ---
    if prev_status == "BULL":
        exit_streak = _count_streak(all_scores, lambda s: s < BULL_EXIT_SCORE_THRESHOLD)
        if exit_streak >= BULL_EXIT_CONSECUTIVE_DAYS:
            return ("NORMAL", 1)
        return ("BULL", prev_continuous_days + 1)

    # --- BEAR 退出 ---
    if prev_status == "BEAR":
        exit_streak = _count_streak(all_scores, lambda s: s >= BEAR_SCORE_THRESHOLD)
        if exit_streak >= BEAR_EXIT_CONSECUTIVE_DAYS:
            return ("NORMAL", 1)
        return ("BEAR", prev_continuous_days + 1)

    # --- RANGE 退出 ---
    if prev_status == "RANGE":
        exit_streak = _count_streak(
            all_scores, lambda s: s < RANGE_LOW or s > RANGE_HIGH
        )
        if exit_streak >= RANGE_EXIT_CONSECUTIVE_DAYS:
            # 根据 break 方向判断下一个状态
            if market_score >= BULL_SCORE_THRESHOLD:
                return ("BULL", 1)
            elif market_score <= BEAR_SCORE_THRESHOLD:
                return ("BEAR", 1)
            else:
                return ("NORMAL", 1)
        return ("RANGE", prev_continuous_days + 1)

    # --- NORMAL：判断进入其他状态 ---
    bull_streak = _count_streak(all_scores, lambda s: s >= BULL_SCORE_THRESHOLD)
    if bull_streak >= BULL_ENTER_CONSECUTIVE_DAYS:
        return ("BULL", 1)

    bear_streak = _count_streak(all_scores, lambda s: s <= BEAR_SCORE_THRESHOLD)
    if bear_streak >= BEAR_CONSECUTIVE_DAYS:
        return ("BEAR", 1)

    range_streak = _count_streak(
        all_scores, lambda s: RANGE_LOW <= s <= RANGE_HIGH
    )
    if range_streak >= RANGE_CONSECUTIVE_DAYS:
        return ("RANGE", 1)

    return ("NORMAL", prev_continuous_days + 1)


def compute_tradeable(status: str, confidence: float, block_type: str) -> bool:
    """判断是否可交易。

    普通板块：status == MAINLINE AND confidence >= 70
    Market: 永远不可交易
    """
    if block_type == "market":
        return False
    return status == "MAINLINE" and confidence >= MAINLINE_CONFIDENCE_THRESHOLD


def compute_priority(df: pd.DataFrame) -> pd.Series:
    """计算优先级排名。仅对 tradeable=True 的行按 mainline_score 降序排名。
    其他行返回 None。
    """
    result = pd.Series([None] * len(df), index=df.index, dtype="Int64")
    tradeable_mask = df["tradeable"] == True
    if tradeable_mask.any():
        result[tradeable_mask] = (
            df.loc[tradeable_mask, "mainline_score"]
            .rank(ascending=False, method="min")
            .astype("Int64")
        )
    return result


def compute_position_ratio(status: str, block_type: str) -> Optional[float]:
    """计算仓位比例。仅 Market 有效，其他板块为 None。"""
    if block_type == "market":
        return float(POSITION_RATIO_MAP.get(status, 0))
    return None


def compute_first_mainline_date(
    prev_first_date: Optional[datetime.date],
    prev_status: Optional[str],
    new_status: str,
    trade_date: datetime.date,
) -> Optional[datetime.date]:
    """计算首次进入 MAINLINE 的日期。

    进入 MAINLINE 时记录，维持至 EXIT 时清空。
    """
    if new_status == "EXIT":
        return None
    if new_status == "MAINLINE" and prev_status != "MAINLINE":
        return trade_date
    return prev_first_date


def compute_peak_score(
    prev_peak: Optional[float],
    prev_status: Optional[str],
    new_status: str,
    mainline_score: float,
) -> Optional[float]:
    """计算主线周期内最高分。

    进入新 MAINLINE round 时重置为当前值，MAINLINE 期间持续追踪峰值。
    退出 MAINLINE（进入 WEAKEN/EXIT）后保留峰值直至 EXIT。
    """
    if new_status == "EXIT":
        return None
    if new_status == "MAINLINE" and prev_status != "MAINLINE":
        # 进入新 round，重置
        return mainline_score
    if new_status == "MAINLINE":
        # 持续追踪峰值
        return max(prev_peak or 0.0, mainline_score)
    # WEAKEN/DISCOVER/WATCH 保持原值
    return prev_peak


def compute_mainline_round(
    prev_round: Optional[int],
    prev_status: Optional[str],
    new_status: str,
) -> Optional[int]:
    """计算主线轮次。每次进入 MAINLINE（从非 MAINLINE 状态）时 +1。"""
    if new_status == "EXIT":
        return None
    if new_status == "MAINLINE" and prev_status != "MAINLINE":
        return (prev_round or 0) + 1
    return prev_round


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _to_records(df: pd.DataFrame) -> list[dict]:
    """将 DataFrame 转换为 list[dict]，NaN/NaT 转为 None（SQL NULL）。"""
    return df.where(pd.notna(df), None).to_dict("records")


def _build_initial_state(prev_mainline_df: pd.DataFrame) -> dict:
    """从已有 block_mainline_daily 数据构建初始逐板块状态。

    Returns:
        dict: block_code -> {
            "scores": [(date, score), ...],  # 最近几天的主线评分（按日期排序）
            "status": str,
            "continuous_days": int,
            "first_mainline_date": date or None,
            "peak_score": float or None,
            "mainline_round": int or None,
        }
    """
    if prev_mainline_df.empty:
        return {}

    df = prev_mainline_df.sort_values(["block_code", "trade_date"])
    state = {}
    for block_code, group in df.groupby("block_code"):
        rows = group.tail(AVG_WINDOW_DAYS + WEAKEN_DECLINE_DAYS)
        last = rows.iloc[-1]
        scores_list = [(r["trade_date"], r["mainline_score"]) for _, r in rows.iterrows()]
        state[block_code] = {
            "scores": scores_list,
            "status": last["mainline_status"],
            "continuous_days": last["continuous_days"] or 0,
            "first_mainline_date": last["first_mainline_date"],
            "peak_score": last["peak_score"],
            "mainline_round": last["mainline_round"],
        }
    return state


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def compute_block_mainline_daily(engine) -> int:
    """增量刷新 block_mainline_daily 表。

    1. 从 block_factor_daily 读取所需数据（含历史窗口）
    2. 读取已有 block_mainline_daily 状态（用于连续性）
    3. 按 block_code + trade_date 顺序计算每个板块的状态机
    4. 插入/更新结果

    Returns:
        本次新增/更新的总行数。
    """
    with engine.connect() as conn:
        # 1. 获取已有最新日期
        default_date = get_settings().sync.data_start_date
        last_date = conn.execute(
            text(
                "SELECT COALESCE(MAX(trade_date), CAST(:default_date AS date)) "
                "FROM block_mainline_daily"
            ),
            {"default_date": default_date},
        ).scalar()

        latest_factor = conn.execute(
            text("SELECT MAX(trade_date) FROM block_factor_daily")
        ).scalar()

        if latest_factor is None:
            logger.warning("block_mainline_skipped_no_factor_data")
            return 0

        if last_date >= latest_factor:
            logger.info(
                "block_mainline_up_to_date",
                last_mainline_date=str(last_date),
                latest_factor_date=str(latest_factor),
            )
            return 0

        logger.info(
            "block_mainline_start",
            from_date=str(last_date),
            to_date=str(latest_factor),
        )

        # 2. 读取 block_factor_daily（含历史窗口）
        lookback_date = str(
            pd.to_datetime(last_date) - pd.Timedelta(days=HISTORY_LOOKBACK_DAYS * 2)
        )

        factor_df = pd.read_sql_query(
            text(_READ_FACTOR_SQL),
            conn,
            params={"lookback_date": lookback_date},
        )

        if factor_df.empty:
            logger.info("block_mainline_no_data")
            return 0

        factor_df = factor_df.sort_values(["trade_date", "block_code"])
        factor_df["trade_date"] = pd.to_datetime(factor_df["trade_date"])

        # 3. 读取已有 block_mainline_daily 状态
        prev_mainline_df = pd.read_sql_query(
            text(_READ_PREV_MAINLINE_SQL),
            conn,
            params={"lookback_date": lookback_date},
        )
        prev_mainline_df["trade_date"] = pd.to_datetime(prev_mainline_df["trade_date"])

        # 4. 构建初始状态
        block_state = _build_initial_state(prev_mainline_df)

        # 5. 确定需要处理的新日期
        last_date_ts = pd.to_datetime(last_date)
        all_dates = sorted(factor_df["trade_date"].unique())
        new_dates = [d for d in all_dates if d > last_date_ts]

        if not new_dates:
            logger.info("block_mainline_no_new_dates")
            return 0

        # 6. 获取大盘状态映射（用于置信度计算中的 market_env）
        # 大盘状态需要在处理过程中逐步确定，先收集因子数据中的 market_state
        market_factor_state_map = {}
        market_rows = factor_df[factor_df["block_type"] == "market"]
        for _, row in market_rows.iterrows():
            market_factor_state_map[row["trade_date"]] = row["market_state"]

        # 7. 逐日计算
        results = []
        for trade_date in new_dates:
            day_df = factor_df[factor_df["trade_date"] == trade_date]

            for _, row in day_df.iterrows():
                bc = row["block_code"]
                bt = row["block_type"]
                bn = row["block_name"]
                total_score = row["total_score"] or 0.0
                capital_score = row["capital_score"] or 0.0
                market_rank = row["market_rank"]

                # 获取或初始化板块状态
                if bc in block_state:
                    st = block_state[bc]
                else:
                    st = {
                        "scores": [],
                        "status": None,
                        "continuous_days": 0,
                        "first_mainline_date": None,
                        "peak_score": None,
                        "mainline_round": None,
                    }

                prev_scores_list = [s[1] for s in st["scores"]]
                prev_status = st["status"]
                prev_continuous_days = st["continuous_days"]

                # a. 计算 avg_score_5d
                avg_score_5d = calc_avg_score_5d(prev_scores_list, total_score)

                # b. 计算 mainline_score
                mainline_score = calc_mainline_score(total_score, avg_score_5d, capital_score)

                # c. 计算 avg_rank_5d
                # 从 factor_df 中获取最近 5 天的 market_rank
                bc_factor_history = factor_df[
                    (factor_df["block_code"] == bc)
                    & (factor_df["trade_date"] <= trade_date)
                ].tail(AVG_WINDOW_DAYS)
                rank_list = bc_factor_history["market_rank"].dropna().tolist()
                avg_rank_5d = calc_avg_rank_5d(rank_list)

                # d. 确定当前大盘状态（用于置信度中的 market_env）
                # 优先使用已计算的主线大盘状态，其次用因子中的 market_state
                market_status_for_confidence = None
                if "market" in block_state:
                    market_status_for_confidence = block_state["market"]["status"]
                else:
                    market_status_for_confidence = market_factor_state_map.get(trade_date)

                # e. 计算置信度
                confidence = calc_confidence(
                    avg_rank_5d,
                    prev_continuous_days if prev_status == "MAINLINE" else 1,
                    prev_scores_list[-5:] + [mainline_score],
                    market_status_for_confidence,
                )

                # f. 应用状态机
                if bt == "market":
                    new_status, new_continuous_days = calc_market_status(
                        prev_status,
                        prev_continuous_days,
                        mainline_score,
                        prev_scores_list,
                    )
                else:
                    new_status, new_continuous_days = calc_block_status(
                        prev_status,
                        prev_continuous_days,
                        mainline_score,
                        confidence,
                        market_rank or 999,
                        prev_scores_list,
                    )

                # g. 计算派生字段
                tradeable = compute_tradeable(new_status, confidence, bt)
                position_ratio = compute_position_ratio(new_status, bt)
                first_mainline_date = compute_first_mainline_date(
                    st["first_mainline_date"],
                    prev_status,
                    new_status,
                    trade_date.date() if hasattr(trade_date, "date") else trade_date,
                )
                peak_score = compute_peak_score(
                    st["peak_score"],
                    prev_status,
                    new_status,
                    mainline_score,
                )
                mainline_round = compute_mainline_round(
                    st["mainline_round"],
                    prev_status,
                    new_status,
                )

                # h. 构建 reason
                reason_parts = []
                if bt != "market":
                    reason_parts.append(
                        f"score={mainline_score:.1f} rank={market_rank} "
                        f"conf={confidence:.1f}"
                    )
                    if prev_status != new_status:
                        reason_parts.append(f"{prev_status}→{new_status}")
                else:
                    reason_parts.append(
                        f"market_score={total_score:.1f} today_score={mainline_score:.1f}"
                    )
                    if prev_status != new_status:
                        reason_parts.append(f"{prev_status}→{new_status}")
                reason = " | ".join(reason_parts) if reason_parts else None

                # i. 记录结果
                # 处理 trade_date 为 date 类型
                td = trade_date.date() if hasattr(trade_date, "date") else trade_date
                results.append({
                    "trade_date": td,
                    "block_code": bc,
                    "block_name": bn,
                    "block_type": bt,
                    "mainline_score": mainline_score,
                    "mainline_status": new_status,
                    "continuous_days": new_continuous_days,
                    "confidence": confidence,
                    "tradeable": tradeable,
                    "priority": None,  # 稍后统一计算
                    "position_ratio": position_ratio,
                    "avg_score_5d": avg_score_5d,
                    "avg_rank_5d": avg_rank_5d,
                    "first_mainline_date": first_mainline_date,
                    "peak_score": peak_score,
                    "mainline_round": mainline_round,
                    "reason": reason,
                    "factor_version": FACTOR_VERSION,
                })

                # j. 更新状态追踪
                st["scores"].append((trade_date, mainline_score))
                # 保持 scores 列表不过长
                if len(st["scores"]) > AVG_WINDOW_DAYS + WEAKEN_DECLINE_DAYS + 5:
                    st["scores"] = st["scores"][-(AVG_WINDOW_DAYS + WEAKEN_DECLINE_DAYS + 5):]
                st["status"] = new_status
                st["continuous_days"] = new_continuous_days
                st["first_mainline_date"] = first_mainline_date
                st["peak_score"] = peak_score
                st["mainline_round"] = mainline_round
                block_state[bc] = st

        if not results:
            logger.info("block_mainline_no_results")
            return 0

        # 8. 计算优先级（仅对同一天的 tradeable 板块排名）
        result_df = pd.DataFrame(results)
        result_df["priority"] = compute_priority(result_df)

        # 9. 插入/更新
        records = _to_records(result_df)
        conn.execute(text(_INSERT_SQL), records)
        conn.commit()

        logger.info(
            "block_mainline_completed",
            rows=len(records),
            dates=result_df["trade_date"].nunique(),
            blocks=result_df["block_code"].nunique(),
            mainline_count=(result_df["mainline_status"] == "MAINLINE").sum(),
            tradeable_count=result_df["tradeable"].sum(),
        )

        return len(records)