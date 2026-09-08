"""回测数据适配层。

Strategy 不直接读取数据库。本模块从 TimescaleDB 读取 行情/指标/状态/因子 数据，
组装成 StockResearchFeed 需要的 DataFrame 与 StrategyData 快照。

股票池: 沿用当前系统已有规则 — 取 stock_data 中的沪深主板 A 股
(创业板/科创板/北交所/ST 已在下载阶段由 is_target_market / ST 过滤剔除)。

注意: 本模块只负责「取数 + 组装」，不包含任何策略逻辑。
"""

import datetime

import pandas as pd
from sqlalchemy import text

from src.data.models import get_engine, get_session
from src.strategies.research_base import StrategyData
from src.utils.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 取数
# ---------------------------------------------------------------------------


def _build_query(stock_codes: list[str] | None) -> tuple[str, dict]:
    """构建宽表查询。stock_codes 为 None 时不加个股过滤。"""
    if stock_codes:
        placeholders = ", ".join(f":sc{i}" for i in range(len(stock_codes)))
        code_filter = f"AND si.stock_code IN ({placeholders})"
        params = {f"sc{i}": code for i, code in enumerate(stock_codes)}
    else:
        code_filter = ""
        params = {}
    sql = f"""
SELECT
    si.trade_date,
    si.stock_code,
    sd.open,
    sd.high,
    sd.low,
    sd.close,
    sd.volume,
    si.ma5, si.ma10, si.ma20, si.ma60,
    si.macd_dif, si.macd_dea, si.macd_hist,
    ss.macd_hist_increasing,
    ss.macd_hist_increasing_days,
    ss.macd_bottom_divergence,
    sf.market_rank
FROM stock_indicator_daily si
JOIN stock_data sd
  ON sd.trade_date = si.trade_date AND sd.code = si.stock_code
JOIN stock_state_daily ss
  ON ss.trade_date = si.trade_date AND ss.stock_code = si.stock_code
LEFT JOIN stock_factor_daily sf
  ON sf.trade_date = si.trade_date AND sf.stock_code = si.stock_code
WHERE si.trade_date BETWEEN :start_date AND :end_date
{code_filter}
ORDER BY si.stock_code, si.trade_date
"""
    return sql, params


def _load_pool_codes(session, start_date, end_date) -> list[str]:
    rows = session.execute(
        text(
            "SELECT DISTINCT code FROM stock_data "
            "WHERE trade_date BETWEEN :start_date AND :end_date "
            "ORDER BY code"
        ),
        {"start_date": start_date, "end_date": end_date},
    ).fetchall()
    return [r[0] for r in rows]


def load_stock_frames(
    engine=None,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    stock_codes: list[str] | None = None,
) -> tuple[list[dict], list[datetime.date]]:
    """从数据库加载股票池数据。

    Args:
        engine: SQLAlchemy engine (默认从配置创建)
        start_date / end_date: 回测日期范围 (默认取 stock_data 实际范围)
        stock_codes: 指定股票池 (默认取整个沪深主板股票池)

    Returns:
        (frames, calendar)
            frames:   [{"stock_code": str, "df": DataFrame(DatetimeIndex)}]
                      每只股票对齐到统一交易日历，停牌/未上市日期为 NaN 行
            calendar: 统一交易日历 (list[datetime.date])
    """
    ses = get_session(engine or get_engine())
    try:
        if start_date is None or end_date is None:
            lo, hi = ses.execute(
                text("SELECT MIN(trade_date), MAX(trade_date) FROM stock_data")
            ).one()
            start_date = start_date or lo
            end_date = end_date or hi

        if stock_codes is None:
            stock_codes = _load_pool_codes(ses, start_date, end_date)

        sql, params = _build_query(stock_codes)
        params["start_date"] = start_date
        params["end_date"] = end_date

        df = pd.read_sql_query(text(sql), ses.bind, params=params)
        if df.empty:
            logger.warning(
                "backtest_data_empty",
                start=str(start_date),
                end=str(end_date),
                stock_count=len(stock_codes),
            )
            return [], []

        df = compute_factor_rank(df)
        calendar = _union_calendar(df)
        frames = align_frames(df, calendar)

        logger.info(
            "backtest_data_loaded",
            stocks=len(frames),
            sessions=len(calendar),
            start=str(calendar[0]),
            end=str(calendar[-1]),
        )
        return frames, calendar
    finally:
        ses.close()


# ---------------------------------------------------------------------------
# 纯数据处理 (可在无数据库的单元测试中使用)
# ---------------------------------------------------------------------------


def compute_factor_rank(df: pd.DataFrame) -> pd.DataFrame:
    """按交易日计算 factor_rank 百分位 = market_rank / 当日有因子评分股票数。

    返回 df 副本，新增 factor_count / factor_rank 两列。
    当日无因子评分的行，factor_rank 为 NaN。
    """
    out = df.copy()
    has_factor = out["market_rank"].notna()
    out["factor_count"] = (
        out[has_factor].groupby("trade_date")["stock_code"].transform("size").reindex(out.index)
    )
    out["factor_rank"] = out["market_rank"] / out["factor_count"]
    return out


def _union_calendar(df: pd.DataFrame) -> list[datetime.date]:
    """从宽表提取统一交易日历 (升序去重)。

    兼容 date / datetime / Timestamp / "YYYY-MM-DD" 字符串，统一转 datetime.date。
    """
    dates = df["trade_date"].drop_duplicates().sort_values()
    return [pd.Timestamp(d).date() for d in dates]


def align_frames(df: pd.DataFrame, calendar: list[datetime.date]) -> list[dict]:
    """将宽表按股票拆分，仅保留各股票实际交易日。

    说明: 停牌/未上市日期不写入 frame (bt 的 PandasData 会照常投递 NaN 行，
    导致挂单以 NaN 价格成交)。回测引擎用"统一日历参考线 + 逐股日期错配"
    判断当日该股是否可交易 (date 不匹配即跳过)，因此缺行是安全的。

    calendar 参数保留，用于构造统一交易日历的语义统一。
    """
    frames = []
    for code, group in df.groupby("stock_code", sort=True):
        g = group.set_index("trade_date")
        g.index = pd.DatetimeIndex(g.index)  # 兼容 date/datetime/Timestamp/字符串
        g = g.sort_index()
        g.index.name = "trade_date"
        g = g.reset_index().rename(columns={"trade_date": "datetime"})
        g["datetime"] = pd.to_datetime(g["datetime"])
        g = g.set_index("datetime")
        frames.append({"stock_code": code, "df": g})
    return frames


# ---------------------------------------------------------------------------
# StrategyData 组装
# ---------------------------------------------------------------------------


def row_to_strategy_data(row) -> StrategyData:
    """将一行宽表数据转换为 StrategyData (Scanner / 单元测试可复用)。"""
    def _f(key: str) -> float | None:
        v = row[key]
        return None if v is None or _is_missing(v) else float(v)

    def _b(key: str) -> bool:
        v = row[key]
        return False if v is None or _is_missing(v) else bool(v)

    def _i(key: str) -> int:
        v = row[key]
        return 0 if v is None or _is_missing(v) else int(v)

    return StrategyData(
        trade_date=pd.Timestamp(row["trade_date"]).date(),
        stock_code=row["stock_code"],
        open=_f("open"),
        close=_f("close"),
        ma5=_f("ma5"),
        ma10=_f("ma10"),
        ma20=_f("ma20"),
        ma60=_f("ma60"),
        macd_dif=_f("macd_dif"),
        macd_dea=_f("macd_dea"),
        macd_hist=_f("macd_hist"),
        macd_hist_increasing=_b("macd_hist_increasing"),
        macd_hist_increasing_days=_i("macd_hist_increasing_days"),
        macd_bottom_divergence=_b("macd_bottom_divergence"),
        factor_rank=_f("factor_rank"),
    )


def _is_missing(v) -> bool:
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False