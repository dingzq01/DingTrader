"""板块每日主线计算模块 (Block Mainline / Direction Engine)。

从 block_factor_daily 读取板块因子评分，计算滚动指标、主线强度评分、
横截面排名，应用状态机确定板块主线状态。

数据来源：block_factor_daily。
"""

import numpy as np
import pandas as pd
from sqlalchemy import text

from src.config.settings import get_settings
from src.factors.block_mainline_config import (
    FACTOR_VERSION,
    LOOKBACK_DAYS,
    MAINLINE_SCORE_CONFIG,
    STATUS_PRIORITY,
    STATUS_RULES,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# 需要从 block_factor_daily 读取的列
# ---------------------------------------------------------------------------
_READ_FACTOR_COLS = [
    "trade_date",
    "block_code",
    "block_name",
    "block_type",
    "total_score",
    "market_rank",
]

# 需要从已有 block_mainline_daily 读取的状态列（增量续算用）
_READ_PREV_STATE_COLS = [
    "block_code",
    "mainline_status",
    "continuous_days",
    "first_mainline_date",
    "mainline_round",
]

# ---------------------------------------------------------------------------
# INSERT SQL
# ---------------------------------------------------------------------------
_SCORE_COLS = [
    "total_score",
    "score_ma5",
    "score_ma20",
    "score_ma5_change",
    "score_ma20_change",
    "rank_raw",
    "rank_ma5",
    "rank_ma20",
    "rank_change_5d",
    "rank_deviation_5d",
    "rank_std_5d",
    "mainline_strength_score",
    "mainline_rank",
    "mainline_status",
    "continuous_days",
    "first_mainline_date",
    "mainline_round",
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
SELECT {", ".join(_READ_FACTOR_COLS)}
FROM block_factor_daily
WHERE trade_date > :lookback_date
ORDER BY trade_date, block_code
"""

_READ_PREV_STATE_SQL = """
SELECT DISTINCT ON (block_code)
    block_code,
    mainline_status,
    continuous_days,
    first_mainline_date,
    mainline_round
FROM block_mainline_daily
ORDER BY block_code, trade_date DESC
"""


# ---------------------------------------------------------------------------
# 状态机判定
# ---------------------------------------------------------------------------


def _safe_val(series: pd.Series, key: str, default: float) -> float:
    """从 Series 安全取值，None/NaN 时返回 default（0 值不会被覆盖）。"""
    val = series.get(key)
    if val is None:
        return default
    try:
        if pd.isna(val):
            return default
    except TypeError:
        pass
    return float(val)


def _check_condition(value: float, condition_key: str, threshold: float,
                     row: pd.Series) -> bool:
    """检查单条条件是否满足。

    条件键后缀含义：
        _gt:  value > threshold
        _lt:  value < threshold
        _le:  value <= threshold
        _ge:  value >= threshold
    特殊键（比较两列）：
        score_ma5_ge_ma20: row["score_ma5"] >= row["score_ma20"]
        score_ma5_lt_ma20: row["score_ma5"] < row["score_ma20"]
    """
    if condition_key == "score_ma5_ge_ma20":
        return _safe_val(row, "score_ma5", 0) >= _safe_val(row, "score_ma20", 0)
    if condition_key == "score_ma5_lt_ma20":
        return _safe_val(row, "score_ma5", 0) < _safe_val(row, "score_ma20", 0)

    if condition_key.endswith("_gt"):
        return value > threshold
    if condition_key.endswith("_lt"):
        return value < threshold
    if condition_key.endswith("_le"):
        return value <= threshold
    if condition_key.endswith("_ge"):
        return value >= threshold

    return False


def _determine_status(row: pd.Series) -> str:
    """按优先级顺序检查状态规则，返回第一个匹配的状态名。

    无匹配时返回 None，由调用方决定 fallback。
    """
    for status_name in STATUS_PRIORITY:
        rules = STATUS_RULES[status_name]
        match = True
        for condition_key, threshold in rules.items():
            if condition_key in ("score_ma5_ge_ma20", "score_ma5_lt_ma20"):
                value = 0  # 不使用，直接传 row
            elif condition_key.startswith("mainline_rank_"):
                value = _safe_val(row, "mainline_rank", 999)
            elif condition_key.startswith("rank_change_5d_"):
                value = _safe_val(row, "rank_change_5d", 0)
            else:
                value = 0
            if not _check_condition(value, condition_key, threshold, row):
                match = False
                break
        if match:
            return status_name
    return None


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _to_records(df: pd.DataFrame) -> list[dict]:
    """将 DataFrame 转换为 list[dict]，NaN/NaT 转为 None（SQL NULL）。"""
    return df.where(pd.notna(df), None).to_dict("records")


def _build_prev_state(prev_df: pd.DataFrame) -> dict:
    """从已有 block_mainline_daily 最新状态构建逐板块前序状态。

    Returns:
        dict: block_code -> {
            "status": str,
            "continuous_days": int,
            "first_mainline_date": date or None,
            "mainline_round": int or None,
        }
    """
    if prev_df.empty:
        return {}
    state = {}
    for _, row in prev_df.iterrows():
        state[row["block_code"]] = {
            "status": row["mainline_status"],
            "continuous_days": row["continuous_days"] or 0,
            "first_mainline_date": row["first_mainline_date"],
            "mainline_round": row["mainline_round"],
        }
    return state


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def compute_block_mainline_daily(engine) -> int:
    """增量刷新 block_mainline_daily 表。

    1. 从 block_factor_daily 读取所需数据（含历史窗口用于滚动计算）
    2. 读取已有 block_mainline_daily 最新状态（用于增量续算）
    3. 按 block_code 分组计算滚动指标
    4. 计算 mainline_strength_score 和 mainline_rank
    5. 应用状态机，追踪 continuous_days / first_mainline_date / mainline_round
    6. INSERT ON CONFLICT UPDATE 写入结果

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

        # 2. 读取 block_factor_daily（含历史窗口用于 rolling）
        lookback_date = str(
            pd.to_datetime(last_date) - pd.Timedelta(days=LOOKBACK_DAYS)
        )

        factor_df = pd.read_sql_query(
            text(_READ_FACTOR_SQL),
            conn,
            params={"lookback_date": lookback_date},
        )

        if factor_df.empty:
            logger.info("block_mainline_no_data")
            return 0

        factor_df = factor_df.sort_values(["block_code", "trade_date"])
        factor_df["trade_date"] = pd.to_datetime(factor_df["trade_date"])

        # 3. 读取已有 block_mainline_daily 最新状态
        prev_state_df = pd.read_sql_query(text(_READ_PREV_STATE_SQL), conn)
        prev_state = _build_prev_state(prev_state_df)

        # 4. 按 block_code 分组计算滚动指标
        grouped = factor_df.groupby("block_code")

        factor_df["score_ma5"] = grouped["total_score"].transform(
            lambda s: s.rolling(5, min_periods=1).mean()
        )
        factor_df["score_ma20"] = grouped["total_score"].transform(
            lambda s: s.rolling(20, min_periods=1).mean()
        )
        factor_df["score_ma5_change"] = grouped["score_ma5"].diff()
        factor_df["score_ma20_change"] = grouped["score_ma20"].diff()

        factor_df["rank_raw"] = factor_df["market_rank"]
        factor_df["rank_ma5"] = grouped["rank_raw"].transform(
            lambda s: s.rolling(5, min_periods=1).mean()
        )
        factor_df["rank_ma20"] = grouped["rank_raw"].transform(
            lambda s: s.rolling(20, min_periods=1).mean()
        )
        factor_df["rank_change_5d"] = grouped["rank_raw"].shift(0) - grouped["rank_raw"].shift(5)
        factor_df["rank_deviation_5d"] = factor_df["rank_raw"] - factor_df["rank_ma5"]
        factor_df["rank_std_5d"] = grouped["rank_raw"].transform(
            lambda s: s.rolling(5, min_periods=1).std()
        )

        # 5. 计算 mainline_strength_score
        default_cfg = MAINLINE_SCORE_CONFIG["default"]
        market_cfg = MAINLINE_SCORE_CONFIG["market"]

        is_market = factor_df["block_type"] == "market"

        factor_df["mainline_strength_score"] = np.where(
            is_market,
            (
                factor_df["total_score"].fillna(0) * market_cfg["today_weight"]
                + factor_df["score_ma5"].fillna(0) * market_cfg["ma5_weight"]
                + factor_df["score_ma20"].fillna(0) * market_cfg["ma20_weight"]
            ),
            (
                factor_df["total_score"].fillna(0) * default_cfg["today_weight"]
                + factor_df["score_ma5"].fillna(0) * default_cfg["ma5_weight"]
                + factor_df["score_ma20"].fillna(0) * default_cfg["ma20_weight"]
            ),
        )
        factor_df["mainline_strength_score"] = factor_df["mainline_strength_score"].clip(0, 100)

        # 6. 计算 mainline_rank：每日横截面按 mainline_strength_score DESC 排名
        # market 固定 0，不参与普通板块排名
        factor_df["mainline_rank"] = 0
        non_market_mask = factor_df["block_type"] != "market"
        if non_market_mask.any():
            factor_df.loc[non_market_mask, "mainline_rank"] = (
                factor_df.loc[non_market_mask]
                .groupby("trade_date")["mainline_strength_score"]
                .rank(ascending=False, method="min")
                .astype("Int64")
            )
        # 确保 mainline_rank 为 Int64（避免 psycopg2 integer out of range）
        factor_df["mainline_rank"] = factor_df["mainline_rank"].astype("Int64")

        # 7. 确定需要处理的新日期
        last_date_ts = pd.to_datetime(last_date)
        all_dates = sorted(factor_df["trade_date"].unique())
        new_dates = [d for d in all_dates if d > last_date_ts]

        if not new_dates:
            logger.info("block_mainline_no_new_dates")
            return 0

        # 8. 逐日逐板块应用状态机
        results = []
        for trade_date in new_dates:
            day_df = factor_df[factor_df["trade_date"] == trade_date].copy()

            for _, row in day_df.iterrows():
                bc = row["block_code"]

                # 获取前序状态
                if bc in prev_state:
                    prev_st = prev_state[bc]
                    prev_status = prev_st["status"]
                else:
                    prev_st = None
                    prev_status = None

                # 状态机判定
                new_status = _determine_status(row)

                # fallback：无匹配时保持前序状态，无前序状态默认 DISCOVER
                if new_status is None:
                    new_status = prev_status or "DISCOVER"

                # 追踪字段
                if new_status != prev_status or prev_st is None:
                    continuous_days = 1
                else:
                    continuous_days = prev_st["continuous_days"] + 1

                # first_mainline_date
                if new_status == "EXIT":
                    first_mainline_date = None
                elif new_status == "MAINLINE" and prev_status != "MAINLINE":
                    first_mainline_date = trade_date.date() if hasattr(trade_date, "date") else trade_date
                else:
                    first_mainline_date = prev_st["first_mainline_date"] if prev_st else None

                # mainline_round
                if new_status == "EXIT":
                    mainline_round = None
                elif new_status == "MAINLINE" and prev_status != "MAINLINE":
                    mainline_round = (prev_st["mainline_round"] or 0) + 1 if prev_st else 1
                else:
                    mainline_round = prev_st["mainline_round"] if prev_st else None

                # 记录结果
                td = trade_date.date() if hasattr(trade_date, "date") else trade_date
                results.append({
                    "trade_date": td,
                    "block_code": bc,
                    "block_name": row["block_name"],
                    "block_type": row["block_type"],
                    "total_score": row.get("total_score"),
                    "score_ma5": row.get("score_ma5"),
                    "score_ma20": row.get("score_ma20"),
                    "score_ma5_change": row.get("score_ma5_change"),
                    "score_ma20_change": row.get("score_ma20_change"),
                    "rank_raw": _safe_int(row.get("rank_raw")),
                    "rank_ma5": row.get("rank_ma5"),
                    "rank_ma20": row.get("rank_ma20"),
                    "rank_change_5d": row.get("rank_change_5d"),
                    "rank_deviation_5d": row.get("rank_deviation_5d"),
                    "rank_std_5d": row.get("rank_std_5d"),
                    "mainline_strength_score": row.get("mainline_strength_score"),
                    "mainline_rank": _safe_int(row.get("mainline_rank")),
                    "mainline_status": new_status,
                    "continuous_days": continuous_days,
                    "first_mainline_date": first_mainline_date,
                    "mainline_round": mainline_round,
                    "factor_version": FACTOR_VERSION,
                })

                # 更新状态追踪
                prev_state[bc] = {
                    "status": new_status,
                    "continuous_days": continuous_days,
                    "first_mainline_date": first_mainline_date,
                    "mainline_round": mainline_round,
                }

        if not results:
            logger.info("block_mainline_no_results")
            return 0

        result_df = pd.DataFrame(results)

        # 将 integer 列转为可为 null 的 Int64，避免 psycopg2 integer out of range
        # （混合 int/None 时 pandas 默认使用 float64，psycopg2 无法正确处理 Float → Integer 映射）
        for col in ("rank_raw", "mainline_rank"):
            if col in result_df.columns:
                result_df[col] = result_df[col].astype("Int64")

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
        )

        return len(records)


def _safe_int(value) -> int | None:
    """将值安全转换为 int，NaN/None 返回 None。"""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
        return int(value)
    except (ValueError, TypeError):
        return None