"""个股每日因子评分计算模块 (Factor Layer)。

将 stock_state_daily 的布尔状态组合成因子评分，计算综合评分与排名。
数据来源：stock_state_daily + stock_block_relation（仅排名计算）。

禁止直接读取 stock_data 或 stock_indicator_daily。
所有评分规则来自 factor_config.py，禁止硬编码分值。
"""

import pandas as pd
from sqlalchemy import text

from src.config.settings import get_settings
from src.factors.stock_factor_config import STOCK_FACTOR_CONFIG, get_all_state_columns
from src.utils.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# INSERT SQL
# ---------------------------------------------------------------------------

_SCORE_COLS = [
    "trend_score", "momentum_score", "capital_score",
    "volume_price_score", "breakout_score",
    "risk_penalty", "total_score",
    "market_rank", "block_rank",
]

_INSERT_COLS = ",\n    ".join(_SCORE_COLS)
_UPDATE_COLS = ",\n    ".join(f"{c} = EXCLUDED.{c}" for c in _SCORE_COLS)
_PARAM_COLS = ",\n    ".join(f":{c}" for c in _SCORE_COLS)

_INSERT_SQL = f"""
INSERT INTO stock_factor_daily (
    trade_date, stock_code,
    {_INSERT_COLS},
    factor_version, created_at
) VALUES (
    :trade_date, :stock_code,
    {_PARAM_COLS},
    'v1.0', CURRENT_TIMESTAMP
)
ON CONFLICT (trade_date, stock_code) DO UPDATE SET
    {_UPDATE_COLS},
    factor_version = EXCLUDED.factor_version,
    created_at = EXCLUDED.created_at
"""

# 从 stock_state_daily 读取所需状态列
_STATE_COLS = ",\n    ".join(get_all_state_columns())

_READ_SQL = f"""
SELECT
    trade_date,
    stock_code,
    {_STATE_COLS}
FROM stock_state_daily
WHERE trade_date > :last_date
ORDER BY trade_date, stock_code
"""

# 全市场排名更新
_UPDATE_MARKET_RANK_SQL = """
UPDATE stock_factor_daily sfd
SET market_rank = ranked.rn
FROM (
    SELECT trade_date, stock_code,
           ROW_NUMBER() OVER (PARTITION BY trade_date ORDER BY total_score DESC) AS rn
    FROM stock_factor_daily
    WHERE trade_date > :last_date
) ranked
WHERE sfd.trade_date = ranked.trade_date
  AND sfd.stock_code = ranked.stock_code
"""

# 板块内排名更新（最佳排名 = 最低 rank 值）
_UPDATE_BLOCK_RANK_SQL = """
UPDATE stock_factor_daily sfd
SET block_rank = best.min_rn
FROM (
    SELECT trade_date, stock_code, MIN(rn) AS min_rn
    FROM (
        SELECT sfd.trade_date, sfd.stock_code,
               ROW_NUMBER() OVER (
                   PARTITION BY sfd.trade_date, sbr.block_code
                   ORDER BY sfd.total_score DESC
               ) AS rn
        FROM stock_factor_daily sfd
        JOIN stock_block_relation sbr ON sfd.stock_code = sbr.stock_code
        WHERE sfd.trade_date > :last_date
    ) ranked
    GROUP BY trade_date, stock_code
) best
WHERE sfd.trade_date = best.trade_date
  AND sfd.stock_code = best.stock_code
"""


# ---------------------------------------------------------------------------
# 评分计算
# ---------------------------------------------------------------------------

def _compute_factor_score(state_row, factor_config: dict) -> float:
    """计算单个因子的评分（所有满足条件的规则分值求和）。"""
    score = 0.0
    for rule_name, points in factor_config["rules"].items():
        val = state_row.get(rule_name)
        if val is True:  # 只对明确满足条件的状态加分/扣分，NULL 不计
            score += points
    return score


def _compute_all_scores(states_df: pd.DataFrame) -> pd.DataFrame:
    """对所有股票的所有日期计算因子评分。

    Args:
        states_df: [trade_date, stock_code, state_cols...] from stock_state_daily

    Returns:
        [trade_date, stock_code, score_cols...] with scores computed
    """
    result = pd.DataFrame({
        "trade_date": states_df["trade_date"],
        "stock_code": states_df["stock_code"],
    })

    for factor_key, cfg in STOCK_FACTOR_CONFIG.items():
        col_name = cfg["column"]
        result[col_name] = states_df.apply(
            lambda row: _compute_factor_score(row, cfg), axis=1
        )

    # total_score = 各加权因子求和
    # 注意：risk_penalty 仍单独计算并入库，但不计入 total_score（扣分逻辑保留，仅不纳入总分）
    total = pd.Series(0.0, index=result.index)
    for factor_key, cfg in STOCK_FACTOR_CONFIG.items():
        col_name = cfg["column"]
        if cfg.get("weight"):
            total += result[col_name] * cfg["weight"]
    result["total_score"] = total

    # 排名先填充 NULL，稍后通过 SQL 窗口函数更新
    result["market_rank"] = None
    result["block_rank"] = None

    return result


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

def compute_stock_factor_daily(engine) -> int:
    """增量刷新 stock_factor_daily 表。

    1. 从 stock_state_daily 读取新增交易日的所有股票状态
    2. 根据 factor_config 计算各因子评分 + total_score
    3. 插入结果（排名先置 NULL）
    4. 通过 SQL 窗口函数更新 market_rank 和 block_rank
    5. block_rank 取同一股票在所有板块中的最佳（最低）排名

    Returns:
        本次新增/更新的总行数。
    """
    with engine.connect() as conn:
        # 1. 获取已有最新日期
        default_date = get_settings().sync.data_start_date
        last_date = conn.execute(
            text(
                "SELECT COALESCE(MAX(trade_date), CAST(:default_date AS date)) "
                "FROM stock_factor_daily"
            ),
            {"default_date": default_date},
        ).scalar()

        latest_state = conn.execute(
            text("SELECT MAX(trade_date) FROM stock_state_daily")
        ).scalar()

        if latest_state is None:
            logger.warning("stock_factor_skipped_no_state_data")
            return 0

        if last_date >= latest_state:
            logger.info(
                "stock_factor_up_to_date",
                last_factor_date=str(last_date),
                latest_state_date=str(latest_state),
            )
            return 0

        logger.info(
            "stock_factor_start",
            from_date=str(last_date),
            to_date=str(latest_state),
        )

        # 2. 读取所有新增日期的状态数据
        states_df = pd.read_sql_query(
            text(_READ_SQL),
            conn,
            params={"last_date": last_date},
        )

        if states_df.empty:
            logger.info("stock_factor_no_new_rows")
            return 0

        logger.info(
            "stock_factor_states_loaded",
            rows=len(states_df),
            dates=states_df["trade_date"].nunique(),
            stocks=states_df["stock_code"].nunique(),
        )

        # 3. 计算所有因子评分
        scores_df = _compute_all_scores(states_df)
        records = _to_records(scores_df)

        # 4. 插入/更新因子评分
        conn.execute(text(_INSERT_SQL), records)
        logger.info("stock_factor_scores_inserted", rows=len(records))

        # 5. 更新全市场排名
        mr_result = conn.execute(
            text(_UPDATE_MARKET_RANK_SQL),
            {"last_date": last_date},
        )
        logger.info("stock_factor_market_rank_updated", rows=mr_result.rowcount)

        # 6. 更新板块内部排名
        br_result = conn.execute(
            text(_UPDATE_BLOCK_RANK_SQL),
            {"last_date": last_date},
        )
        logger.info("stock_factor_block_rank_updated", rows=br_result.rowcount)

        conn.commit()
        logger.info("stock_factor_completed", total_rows=len(records))
        return len(records)


def _to_records(df: pd.DataFrame) -> list[dict]:
    """将 DataFrame 转换为 list[dict]，NaN/NaT 转为 None（SQL NULL）。"""
    return df.where(pd.notna(df), None).to_dict("records")