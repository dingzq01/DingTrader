"""板块每日因子评分计算模块 (Block Factor Layer)。

从 block_stat_daily 计算板块因子评分，包括热度、强度、赚钱效应、
持续性、资金流入、风险扣分六大因子及综合评分与排名。
数据来源：block_stat_daily。

禁止直接读取 stock_data 或 stock_indicator_daily。
所有评分规则来自 block_factor_config.py，禁止硬编码分值。
"""

import pandas as pd
from sqlalchemy import text

from src.factors.block_factor_config import (
    BLOCK_FACTOR_CONFIG,
    LOOKBACK_MA5,
    LOOKBACK_MA20,
    LOOKBACK_STD20,
    RISK_LOOKBACK,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# 需要从 block_stat_daily 读取的列
# ---------------------------------------------------------------------------
_READ_COLS = [
    "trade_date", "block_code", "block_name", "block_type",
    "stock_count", "avg_change_pct", "median_change_pct",
    "up_ratio", "limit_up_count", "gt_5_count",
    "amount",
]

# ---------------------------------------------------------------------------
# INSERT SQL
# ---------------------------------------------------------------------------
_SCORE_COLS = [
    "heat_score", "strength_score", "profit_score",
    "persistence_score", "capital_score",
    "risk_penalty", "total_score", "market_rank",
]

_INSERT_COLS = ",\n    ".join(_SCORE_COLS)
_UPDATE_COLS = ",\n    ".join(f"{c} = EXCLUDED.{c}" for c in _SCORE_COLS)
_PARAM_COLS = ",\n    ".join(f":{c}" for c in _SCORE_COLS)

_INSERT_SQL = f"""
INSERT INTO block_factor_daily (
    trade_date, block_code, block_name, block_type,
    {_INSERT_COLS},
    factor_version, created_at
) VALUES (
    :trade_date, :block_code, :block_name, :block_type,
    {_PARAM_COLS},
    'v1.0', CURRENT_TIMESTAMP
)
ON CONFLICT (trade_date, block_code) DO UPDATE SET
    block_name = EXCLUDED.block_name,
    block_type = EXCLUDED.block_type,
    {_UPDATE_COLS},
    factor_version = EXCLUDED.factor_version,
    created_at = EXCLUDED.created_at
"""

_READ_SQL = f"""
SELECT {', '.join(_READ_COLS)}
FROM block_stat_daily
WHERE trade_date > :lookback_date
ORDER BY trade_date, block_code
"""

# 全市场排名更新
_UPDATE_MARKET_RANK_SQL = """
UPDATE block_factor_daily bfd
SET market_rank = ranked.rn
FROM (
    SELECT trade_date, block_code,
           ROW_NUMBER() OVER (PARTITION BY trade_date ORDER BY total_score DESC) AS rn
    FROM block_factor_daily
    WHERE trade_date > :last_date
) ranked
WHERE bfd.trade_date = ranked.trade_date
  AND bfd.block_code = ranked.block_code
"""


# ---------------------------------------------------------------------------
# 标准化工具
# ---------------------------------------------------------------------------

def _minmax_standardize(series: pd.Series) -> pd.Series:
    """Min-max 标准化到 0-100，所有值相同时返回 50。"""
    mn, mx = series.min(), series.max()
    if mx - mn < 1e-9:
        return pd.Series(50.0, index=series.index)
    return ((series - mn) / (mx - mn)) * 100.0


def _tiered_score(series: pd.Series, tiers: list[tuple[float, float, float]]) -> pd.Series:
    """根据分段阈值打分。

    tiers: [(low, high, score), ...]
    值在 [low, high) 区间内获得对应 score。
    注意：最后一个区间的上界是 inclusive 的边界（如 1.01 用于 up_ratio=1.0）。
    """
    result = pd.Series(0.0, index=series.index)
    for low, high, score in tiers:
        mask = (series >= low) & (series < high)
        result[mask] = score
    # 处理刚好落在最后区间上界的值（如 up_ratio == 1.0）
    last_high = tiers[-1][1]
    last_score = tiers[-1][2]
    result[series >= last_high] = last_score
    return result


# ---------------------------------------------------------------------------
# 因子计算
# ---------------------------------------------------------------------------

def _compute_raw_sub_factors(df: pd.DataFrame, market_amount_map: dict) -> pd.DataFrame:
    """计算所有原始子因子值（未标准化）。

    返回 DataFrame 含所有子因子列 + 原始 source 列 + trade_date/block_code/block_name/block_type。
    """
    result = df.copy()

    # --- 基础比值 ---
    result["amount_ratio"] = df.apply(
        lambda r: r["amount"] / market_amount_map.get(r["trade_date"], r["amount"])
        if market_amount_map.get(r["trade_date"], 0) > 0 else 0.0,
        axis=1,
    )
    result["limit_up_ratio"] = df["limit_up_count"] / df["stock_count"].replace(0, 1)
    result["gt_5_ratio"] = df["gt_5_count"] / df["stock_count"].replace(0, 1)

    return result


def _compute_rolling_stats(full_df: pd.DataFrame) -> pd.DataFrame:
    """计算滚动统计量（按 block_code 分组）。

    需要 full_df 包含足够历史数据（已排序）。
    返回含滚动统计的 DataFrame。
    """
    df = full_df.sort_values(["block_code", "trade_date"]).copy()

    # --- 按 block_code 分组计算滚动值 ---
    grouped = df.groupby("block_code")

    # amount_ratio 的 5日MA
    df["amount_ratio_ma5"] = grouped["amount_ratio"].transform(
        lambda s: s.rolling(LOOKBACK_MA5, min_periods=1).mean()
    )

    # amount 的 5日MA
    df["amount_ma5"] = grouped["amount"].transform(
        lambda s: s.rolling(LOOKBACK_MA5, min_periods=1).mean()
    )

    # amount 的 20日MA
    df["amount_ma20"] = grouped["amount"].transform(
        lambda s: s.rolling(LOOKBACK_MA20, min_periods=1).mean()
    )

    # amount 的 20日STD
    df["amount_std20"] = grouped["amount"].transform(
        lambda s: s.rolling(LOOKBACK_STD20, min_periods=1).std()
    )

    # avg_change_pct 的 5日MA
    df["avg_change_ma5"] = grouped["avg_change_pct"].transform(
        lambda s: s.rolling(LOOKBACK_MA5, min_periods=1).mean()
    )

    # up_ratio 的 5日MA
    df["up_ratio_ma5"] = grouped["up_ratio"].transform(
        lambda s: s.rolling(LOOKBACK_MA5, min_periods=1).mean()
    )

    # amount_growth = today_amount / MA5(amount) - 1
    df["amount_growth"] = df["amount"] / df["amount_ma5"].replace(0, 1) - 1

    # amount_growth 的 5日MA
    df["amount_growth_ma5"] = grouped["amount_growth"].transform(
        lambda s: s.rolling(LOOKBACK_MA5, min_periods=1).mean()
    )

    # amount_ratio_change = amount_ratio - MA5(amount_ratio)
    df["amount_ratio_change"] = df["amount_ratio"] - df["amount_ratio_ma5"]

    # amount_zscore = (amount - MA20(amount)) / STD20(amount)
    df["amount_zscore"] = (df["amount"] - df["amount_ma20"]) / df["amount_std20"].replace(0, 1)

    # 别名：heat_factor config 使用 amount_growth_5d
    df["amount_growth_5d"] = df["amount_growth"]

    return df


def _standardize_and_combine(df: pd.DataFrame) -> pd.DataFrame:
    """对每个因子做子因子标准化并加权组合。

    传入 df 必须包含所有子因子原始值列和 trade_date。
    排除 market 行进行标准化，然后为 market 行赋予中性分。
    """
    cfg = BLOCK_FACTOR_CONFIG
    non_market = df[df["block_type"] != "market"].copy()
    result = df[["trade_date", "block_code", "block_name", "block_type"]].copy()

    # --- heat_factor ---
    heat_cfg = cfg["heat_factor"]
    heat_parts = {}
    for sub_name, sub_cfg in heat_cfg["sub_factors"].items():
        # 对每个交易日单独做标准化
        raw_col = sub_name
        norm_col = f"{sub_name}_score"
        non_market[norm_col] = non_market.groupby("trade_date")[raw_col].transform(
            _minmax_standardize
        )
        heat_parts[sub_name] = non_market[norm_col] * sub_cfg["weight"]
    non_market["heat_score"] = sum(heat_parts.values())

    # --- strength_factor (tiered scoring) ---
    strength_cfg = cfg["strength_factor"]
    strength_parts = {}
    for sub_name, sub_cfg in strength_cfg["sub_factors"].items():
        col_map = {
            "avg_change_pct": "avg_change_pct",
            "median_change_pct": "median_change_pct",
            "up_ratio": "up_ratio",
        }
        raw_col = col_map[sub_name]
        tier_score = f"{sub_name}_tier"
        non_market[tier_score] = non_market.groupby("trade_date")[raw_col].transform(
            lambda s: _tiered_score(s, sub_cfg["tiers"])
        )
        strength_parts[sub_name] = non_market[tier_score] * sub_cfg["weight"]
    non_market["strength_score"] = sum(strength_parts.values())

    # --- profit_factor ---
    profit_cfg = cfg["profit_factor"]
    profit_parts = {}
    for sub_name, sub_cfg in profit_cfg["sub_factors"].items():
        raw_col = sub_name  # limit_up_ratio, gt_5_ratio, up_ratio
        norm_col = f"{sub_name}_score"
        non_market[norm_col] = non_market.groupby("trade_date")[raw_col].transform(
            _minmax_standardize
        )
        profit_parts[sub_name] = non_market[norm_col] * sub_cfg["weight"]
    non_market["profit_score"] = sum(profit_parts.values())

    # --- persistence_factor ---
    persist_cfg = cfg["persistence_factor"]
    persist_parts = {}
    col_map_persist = {
        "ma5_avg_change": "avg_change_ma5",
        "ma5_up_ratio": "up_ratio_ma5",
        "ma5_amount_growth": "amount_growth_ma5",
    }
    for sub_name, sub_cfg in persist_cfg["sub_factors"].items():
        raw_col = col_map_persist[sub_name]
        norm_col = f"{sub_name}_score"
        non_market[norm_col] = non_market.groupby("trade_date")[raw_col].transform(
            _minmax_standardize
        )
        persist_parts[sub_name] = non_market[norm_col] * sub_cfg["weight"]
    non_market["persistence_score"] = sum(persist_parts.values())

    # --- capital_factor ---
    capital_cfg = cfg["capital_factor"]
    capital_parts = {}
    for sub_name, sub_cfg in capital_cfg["sub_factors"].items():
        raw_col = sub_name  # amount_ratio_change, amount_growth
        norm_col = f"{sub_name}_score"
        non_market[norm_col] = non_market.groupby("trade_date")[raw_col].transform(
            _minmax_standardize
        )
        capital_parts[sub_name] = non_market[norm_col] * sub_cfg["weight"]
    non_market["capital_score"] = sum(capital_parts.values())

    # --- risk_penalty ---
    risk_cfg = cfg["risk_factor"]["rules"]
    non_market["risk_penalty"] = 0.0

    # 连续上涨风险 (按 block_code 分组计算)
    non_market = non_market.sort_values(["block_code", "trade_date"])
    non_market["prev_avg_change"] = non_market.groupby("block_code")["avg_change_pct"].shift(1)
    non_market["prev2_avg_change"] = non_market.groupby("block_code")["avg_change_pct"].shift(2)
    consec_up_mask = (
        (non_market["avg_change_pct"] > risk_cfg["consecutive_up"]["avg_change_threshold"])
        & (non_market["prev_avg_change"] > risk_cfg["consecutive_up"]["avg_change_threshold"])
        & (non_market["prev2_avg_change"] > risk_cfg["consecutive_up"]["avg_change_threshold"])
    )
    non_market.loc[consec_up_mask, "risk_penalty"] += risk_cfg["consecutive_up"]["penalty"]

    # 高位高潮风险
    high_limit_mask = (
        non_market["limit_up_ratio"]
        > risk_cfg["high_limit_up_ratio"]["limit_up_ratio_threshold"]
    )
    non_market.loc[high_limit_mask, "risk_penalty"] += risk_cfg["high_limit_up_ratio"]["penalty"]

    # 成交异常风险
    extreme_cfg = risk_cfg["extreme_amount"]
    extreme_mask = (
        (non_market["amount_zscore"] > extreme_cfg["zscore_threshold"])
        & (non_market["avg_change_pct"] > extreme_cfg["avg_change_threshold"])
    )
    non_market.loc[extreme_mask, "risk_penalty"] += extreme_cfg["penalty"]

    # --- total_score ---
    total_weights = {
        "heat_score": cfg["heat_factor"]["weight"],
        "strength_score": cfg["strength_factor"]["weight"],
        "profit_score": cfg["profit_factor"]["weight"],
        "persistence_score": cfg["persistence_factor"]["weight"],
        "capital_score": cfg["capital_factor"]["weight"],
    }
    non_market["total_score"] = (
        non_market["heat_score"] * total_weights["heat_score"]
        + non_market["strength_score"] * total_weights["strength_score"]
        + non_market["profit_score"] * total_weights["profit_score"]
        + non_market["persistence_score"] * total_weights["persistence_score"]
        + non_market["capital_score"] * total_weights["capital_score"]
        + non_market["risk_penalty"]
    )

    # --- 对 market 行赋中性分 ---
    score_cols = [
        "heat_score", "strength_score", "profit_score",
        "persistence_score", "capital_score",
        "risk_penalty", "total_score",
    ]
    for col in score_cols:
        result[col] = pd.NA

    # 将 non_market 的结果合并回 result
    for col in score_cols:
        result.loc[non_market.index, col] = non_market[col]

    # market 行给中性值
    market_mask = result["block_type"] == "market"
    result.loc[market_mask, "heat_score"] = 50.0
    result.loc[market_mask, "strength_score"] = 50.0
    result.loc[market_mask, "profit_score"] = 50.0
    result.loc[market_mask, "persistence_score"] = 50.0
    result.loc[market_mask, "capital_score"] = 50.0
    result.loc[market_mask, "risk_penalty"] = 0.0
    # market 的 total_score 也设为中性，不参与排名
    result.loc[market_mask, "total_score"] = 50.0

    return result


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

def compute_block_factor_daily(engine) -> int:
    """增量刷新 block_factor_daily 表。

    1. 从 block_stat_daily 读取所需数据（含历史窗口用于滚动计算）
    2. 计算所有因子评分 + total_score
    3. 插入结果（排名先置 NULL）
    4. 通过 SQL 窗口函数更新 market_rank

    Returns:
        本次新增/更新的总行数。
    """
    with engine.connect() as conn:
        # 1. 获取已有最新日期
        last_date = conn.execute(
            text(
                "SELECT COALESCE(MAX(trade_date), '2026-01-01'::date) "
                "FROM block_factor_daily"
            )
        ).scalar()

        latest_stat = conn.execute(
            text("SELECT MAX(trade_date) FROM block_stat_daily")
        ).scalar()

        if latest_stat is None:
            logger.warning("compute_block_factor_skipped_no_stat_data")
            return 0

        if last_date >= latest_stat:
            logger.info(
                "block_factor_up_to_date",
                last_factor_date=str(last_date),
                latest_stat_date=str(latest_stat),
            )
            return 0

        logger.info(
            "compute_block_factor_start",
            from_date=str(last_date),
            to_date=str(latest_stat),
        )

        # 2. 读取 block_stat_daily（含历史窗口）
        # 回溯足够天数用于 MA20 + 连续3日检测
        lookback_date = str(
            pd.to_datetime(last_date) - pd.Timedelta(days=max(LOOKBACK_MA20, RISK_LOOKBACK) * 2)
        )

        raw_df = pd.read_sql_query(
            text(_READ_SQL),
            conn,
            params={"lookback_date": lookback_date},
        )

        if raw_df.empty:
            logger.info("compute_block_factor_no_data")
            return 0

        raw_df = raw_df.sort_values("trade_date")
        # 统一 trade_date 为 Timestamp 类型，避免和 datetime.date 比较时报错
        raw_df["trade_date"] = pd.to_datetime(raw_df["trade_date"])

        # 3. 提取 market_amount 映射
        market_df = raw_df[raw_df["block_type"] == "market"]
        market_amount_map = dict(zip(market_df["trade_date"], market_df["amount"]))

        # 4. 计算原始子因子
        df = _compute_raw_sub_factors(raw_df, market_amount_map)

        # 5. 计算滚动统计量
        df = _compute_rolling_stats(df)
        # heat_factor config 使用 amount_growth_5d 作为列名（等同于 amount_growth）
        df["amount_growth_5d"] = df["amount_growth"]

        # 6. 标准化 + 加权组合 → 因子评分
        scores_df = _standardize_and_combine(df)

        # 7. 只保留新增日期的行
        last_date_ts = pd.to_datetime(last_date)
        new_mask = scores_df["trade_date"] > last_date_ts
        new_df = scores_df[new_mask].copy()

        if new_df.empty:
            logger.info("compute_block_factor_no_new_dates")
            return 0

        logger.info(
            "block_factor_scores_computed",
            rows=len(new_df),
            dates=new_df["trade_date"].nunique(),
            blocks=new_df["block_code"].nunique(),
        )

        # 8. 插入/更新
        records = _to_records(new_df)
        conn.execute(text(_INSERT_SQL), records)
        logger.info("block_factor_scores_inserted", rows=len(records))

        # 9. 更新全市场排名
        mr_result = conn.execute(
            text(_UPDATE_MARKET_RANK_SQL),
            {"last_date": last_date},
        )
        logger.info("block_factor_market_rank_updated", rows=mr_result.rowcount)

        conn.commit()
        logger.info("compute_block_factor_completed", total_rows=len(records))
        return len(records)


def _to_records(df: pd.DataFrame) -> list[dict]:
    """将 DataFrame 转换为 list[dict]，NaN/NaT 转为 None（SQL NULL）。"""
    return df.where(pd.notna(df), None).to_dict("records")