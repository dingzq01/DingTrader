"""个股每日技术状态计算模块 (State Layer)。

将 stock_indicator_daily 的连续技术指标转换为离散布尔状态。
数据来源：stock_indicator_daily（指标值）+ stock_data（close/volume/change_pct）。

支持每日增量更新：仅计算新增交易日。
"""

import pandas as pd
from sqlalchemy import text

from src.config.settings import get_settings
from src.data.state_rules import apply_all_rules
from src.utils.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# INSERT SQL
# ---------------------------------------------------------------------------

_STATE_COLS = [
    "price_above_ma5", "price_above_ma20", "price_above_ma60",
    "ma5_above_ma20", "ma20_above_ma60",
    "trend_short_bull", "trend_mid_bull",
    "macd_bullish", "macd_golden_cross", "macd_dead_cross",
    "macd_hist_positive", "macd_hist_increasing",
    "kdj_golden_cross", "kdj_over_buy", "kdj_over_sell",
    "volume_expand", "volume_shrink", "price_volume_confirm",
    "obv_above_ma20", "obv_rising", "obv_price_divergence",
    "capital_bullish", "capital_cross_up", "capital_life_up",
    "high_break_20", "high_break_60", "new_high",
    "extreme_up", "extreme_down", "high_volatility",
]

_INSERT_COLS = ",\n    ".join(_STATE_COLS)
_UPDATE_COLS = ",\n    ".join(f"{c} = EXCLUDED.{c}" for c in _STATE_COLS)
_PARAM_COLS = ",\n    ".join(f":{c}" for c in _STATE_COLS)

_INSERT_SQL = f"""
INSERT INTO stock_state_daily (
    trade_date, stock_code,
    {_INSERT_COLS},
    state_version, created_at
) VALUES (
    :trade_date, :stock_code,
    {_PARAM_COLS},
    'v1.0', CURRENT_TIMESTAMP
)
ON CONFLICT (trade_date, stock_code) DO UPDATE SET
    {_UPDATE_COLS},
    state_version = EXCLUDED.state_version,
    created_at = EXCLUDED.created_at
"""

# 读取数据的 JOIN SQL：从 stock_indicator_daily 获取所有指标，从 stock_data 获取 close/volume/change_pct
_JOIN_SQL = """
SELECT
    si.trade_date,
    si.stock_code,
    sd.close,
    sd.volume,
    sd.change_pct,
    si.volume_ma5, si.volume_ma10, si.volume_ma20,
    si.ma5, si.ma10, si.ma20, si.ma30, si.ma60, si.ma120, si.ma250,
    si.ema5, si.ema10, si.ema20, si.ema30, si.ema60, si.ema120, si.ema250,
    si.macd_dif, si.macd_dea, si.macd_hist,
    si.obv, si.obv_ma20,
    si.k_value, si.d_value, si.j_value,
    si.capital_fast, si.capital_slow,
    si.capital_life, si.capital_life_ma
FROM stock_indicator_daily si
JOIN stock_data sd ON si.trade_date = sd.trade_date AND si.stock_code = sd.code
WHERE si.stock_code = :stock_code
ORDER BY si.trade_date
"""


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------


def compute_stock_state_daily(engine) -> int:
    """增量刷新 stock_state_daily 表。

    从 stock_indicator_daily + stock_data 读取数据，
    应用 state_rules 将连续指标转换为布尔状态，
    仅处理 stock_state_daily 中尚不存在的交易日。

    Returns:
        本次新增/更新的总行数。
    """
    with engine.connect() as conn:
        # 1. 获取 stock_state_daily 已有最新日期
        default_date = get_settings().sync.data_start_date
        last_date = conn.execute(
            text(
                "SELECT COALESCE(MAX(trade_date), CAST(:default_date AS date)) "
                "FROM stock_state_daily"
            ),
            {"default_date": default_date},
        ).scalar()

        # 2. 获取 stock_indicator_daily 最新日期
        latest_indicator = conn.execute(
            text("SELECT MAX(trade_date) FROM stock_indicator_daily")
        ).scalar()

        if latest_indicator is None:
            logger.warning("stock_state_skipped_no_indicator_data")
            return 0

        if last_date >= latest_indicator:
            logger.info(
                "stock_state_up_to_date",
                last_state_date=str(last_date),
                latest_indicator_date=str(latest_indicator),
            )
            return 0

        logger.info(
            "stock_state_start",
            from_date=str(last_date),
            to_date=str(latest_indicator),
        )

        # 3. 获取所有股票代码
        stocks = [
            row[0]
            for row in conn.execute(
                text("SELECT DISTINCT stock_code FROM stock_indicator_daily ORDER BY stock_code")
            ).fetchall()
        ]
        logger.info("stock_state_stocks_loaded", count=len(stocks))

        total_rows = 0
        processed = 0
        skipped = 0

        for stock_code in stocks:
            df = _read_joined_data(conn, stock_code)
            if df.empty:
                skipped += 1
                continue

            states = apply_all_rules(df)
            # 仅保留 stock_state_daily 中不存在的交易日
            new_data = states[states["trade_date"] > last_date]
            if new_data.empty:
                continue

            records = _to_records(new_data)
            conn.execute(text(_INSERT_SQL), records)
            total_rows += len(records)
            processed += 1

            if processed % 500 == 0:
                logger.info(
                    "stock_state_progress",
                    processed=processed,
                    total_stocks=len(stocks),
                    rows_written=total_rows,
                )

        conn.commit()
        logger.info(
            "stock_state_completed",
            total_rows=total_rows,
            stocks_processed=processed,
            stocks_skipped=skipped,
        )
        return total_rows


def _read_joined_data(conn, stock_code: str) -> pd.DataFrame:
    """读取单只股票的指标数据 + 原始 close/volume/change_pct（按 trade_date 升序）。"""
    result = conn.execute(
        text(_JOIN_SQL),
        {"stock_code": stock_code},
    )
    rows = result.fetchall()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows, columns=result.keys())


def _to_records(df: pd.DataFrame) -> list[dict]:
    """将 DataFrame 转换为 list[dict]，NaN 转为 None（SQL NULL）。"""
    return df.where(pd.notna(df), None).to_dict("records")