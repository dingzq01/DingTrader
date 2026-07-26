"""个股每日技术指标计算模块 (Indicator Layer)。

从 stock_data 计算所有连续值技术指标，写入 stock_indicator_daily。
数据来源：仅 stock_data，不依赖其它任何表。

支持每日增量更新：仅计算新增交易日。
"""

import numpy as np
import pandas as pd
from sqlalchemy import text

from src.config.settings import get_settings
from src.utils.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# 通达信 (TDX) 函数实现
# ---------------------------------------------------------------------------


def _tdx_ref(series: pd.Series, n: int) -> pd.Series:
    """REF(X,N): N 个交易日前的数据。"""
    return series.shift(n)


def _tdx_ma(series: pd.Series, n: int) -> pd.Series:
    """MA(X,N): 简单移动平均。最近 N 个值的算术平均。"""
    return series.rolling(window=n, min_periods=n).mean()


def _tdx_ema(series: pd.Series, n: int) -> pd.Series:
    """EMA(X,N): 指数移动平均。

    α = 2/(N+1)
    EMA(today) = close_today × α + EMA(yesterday) × (1-α)
    首日 EMA = 首个值。
    """
    return series.ewm(span=n, adjust=False).mean()


def _tdx_llv(series: pd.Series, n: int) -> pd.Series:
    """LLV(X,N): 最近 N 日最小值。"""
    return series.rolling(window=n, min_periods=n).min()


def _tdx_hhv(series: pd.Series, n: int) -> pd.Series:
    """HHV(X,N): 最近 N 日最大值。"""
    return series.rolling(window=n, min_periods=n).max()


def _tdx_sma(series: pd.Series, n: int, m: int) -> pd.Series:
    """通达信 SMA: Y = (M×X + (N-M)×Y')/N

    Y': 昨日 SMA 值。首日 SMA = 首个 X 值。

    与普通移动平均不同，这是通达信特有的递归加权算法。
    """
    result = pd.Series(np.nan, index=series.index, dtype=float)
    if len(series) == 0:
        return result
    result.iloc[0] = float(series.iloc[0])
    for i in range(1, len(series)):
        if pd.isna(series.iloc[i]):
            result.iloc[i] = result.iloc[i - 1]
        else:
            result.iloc[i] = (m * float(series.iloc[i]) + (n - m) * result.iloc[i - 1]) / n
    return result


def _tdx_forcast(series: pd.Series, n: int) -> pd.Series:
    """通达信 FORCAST: 对最近 N 个值做线性回归，返回回归线上当前点的拟合值。

    y = a + b×x, 其中 x = 0, 1, ..., N-1
    FORCAST = a + b×(N-1)
    """
    result = pd.Series(np.nan, index=series.index, dtype=float)
    if len(series) < n:
        return result

    x = np.arange(n, dtype=float)
    x_mean = x.mean()
    denom = ((x - x_mean) ** 2).sum()

    vals = series.values
    for i in range(n - 1, len(vals)):
        y = vals[i - n + 1 : i + 1]
        if np.any(np.isnan(y)):
            continue
        y_mean = y.mean()
        slope = ((x - x_mean) * (y - y_mean)).sum() / denom
        intercept = y_mean - slope * x_mean
        result.iloc[i] = intercept + slope * (n - 1)

    return result


# ---------------------------------------------------------------------------
# OBV 计算
# ---------------------------------------------------------------------------


def _compute_obv_series(close: pd.Series, volume: pd.Series) -> pd.Series:
    """计算 OBV 序列。

    今日收盘 > 昨日 → OBV + volume
    今日收盘 < 昨日 → OBV - volume
    今日收盘 = 昨日 → OBV 不变
    首日 OBV = 0。
    """
    obv = pd.Series(0.0, index=close.index)
    if len(close) < 2:
        return obv
    direction = pd.Series(0, index=close.index)
    direction[close > close.shift(1)] = 1
    direction[close < close.shift(1)] = -1
    return (direction * volume).cumsum()


# ---------------------------------------------------------------------------
# 全指标计算
# ---------------------------------------------------------------------------


def _compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """对单只股票的 K 线 DataFrame 计算全部技术指标。

    Args:
        df: 包含 [trade_date, code, open, high, low, close, volume] 的 DataFrame，
            按 trade_date 升序排列。

    Returns:
        [trade_date, stock_code, 各 indicator 列...] 的 DataFrame。
    """
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    result = pd.DataFrame({
        "trade_date": df["trade_date"],
        "stock_code": df["code"].iloc[0],
    })

    # ---- 成交量均线 ----
    result["volume_ma5"] = _tdx_ma(volume, 5)
    result["volume_ma10"] = _tdx_ma(volume, 10)
    result["volume_ma20"] = _tdx_ma(volume, 20)

    # ---- MA ----
    for n in [5, 10, 20, 30, 60, 120, 250]:
        result[f"ma{n}"] = _tdx_ma(close, n)

    # ---- EMA ----
    for n in [5, 10, 20, 30, 60, 120, 250]:
        result[f"ema{n}"] = _tdx_ema(close, n)

    # ---- MACD(21,55,13) ----
    ema21 = _tdx_ema(close, 21)
    ema55 = _tdx_ema(close, 55)
    dif = ema21 - ema55
    dea = _tdx_ema(dif, 13)
    result["macd_dif"] = dif
    result["macd_dea"] = dea
    result["macd_hist"] = 2 * (dif - dea)

    # ---- OBV(20) ----
    obv_series = _compute_obv_series(close, volume)
    result["obv"] = obv_series
    result["obv_ma20"] = _tdx_ma(obv_series, 20)

    # ---- KDJ(21,5,5) ----
    llv21 = _tdx_llv(low, 21)
    hhv21 = _tdx_hhv(high, 21)
    denom = hhv21 - llv21
    rsv = pd.Series(50.0, index=close.index)  # 默认 50（无波动时）
    valid = denom > 0
    rsv[valid] = (close[valid] - llv21[valid]) / denom[valid] * 100

    k = _tdx_sma(rsv, 5, 1)
    d = _tdx_sma(k, 5, 1)
    result["k_value"] = k
    result["d_value"] = d
    result["j_value"] = 3 * k - 2 * d

    # ---- 主力做多做空资金线 ----
    var0 = (2 * close + high + low) / 4
    llv26 = _tdx_llv(low, 26)
    hhv34 = _tdx_hhv(high, 34)
    denom_b = hhv34 - llv26
    b_raw = pd.Series(50.0, index=close.index)
    valid_b = denom_b > 0
    b_raw[valid_b] = (var0[valid_b] - llv26[valid_b]) / denom_b[valid_b] * 100
    b = _tdx_ema(b_raw, 16)
    result["capital_fast"] = _tdx_ema(b, 5)
    result["capital_slow"] = _tdx_ema(result["capital_fast"], 26)

    # ---- 个股资金线 / 资金生命线 ----
    # 个股资金线 = MA(CLOSE,1) / MA(REF(CLOSE,18),18) × 100
    # 资金生命线 = MA(FORCAST(个股资金线,20),6)

    # REF(CLOSE,18)
    ref18 = _tdx_ref(close, 18)

    # MA(REF(CLOSE,18),18)
    ma_ref = _tdx_ma(ref18, 18)

    # 个股资金线（对应数据库字段：capital_life）
    capital_line = pd.Series(np.nan, index=close.index)
    valid = ma_ref > 0
    capital_line[valid] = close[valid] / ma_ref[valid] * 100
    result["capital_life"] = capital_line

    # FORCAST(个股资金线,20)
    capital_line_forecast = _tdx_forcast(capital_line, 20)

    # 资金生命线（对应数据库字段：capital_life_ma）
    result["capital_life_ma"] = _tdx_ma(capital_line_forecast, 6)

    return result


# ---------------------------------------------------------------------------
# 增量刷新 SQL
# ---------------------------------------------------------------------------

_COLS = [
    "volume_ma5", "volume_ma10", "volume_ma20",
    "ma5", "ma10", "ma20", "ma30", "ma60", "ma120", "ma250",
    "ema5", "ema10", "ema20", "ema30", "ema60", "ema120", "ema250",
    "macd_dif", "macd_dea", "macd_hist",
    "obv", "obv_ma20",
    "k_value", "d_value", "j_value",
    "capital_fast", "capital_slow",
    "capital_life", "capital_life_ma",
]

_INSERT_COLS = ",\n    ".join(_COLS)
_UPDATE_COLS = ",\n    ".join(f"{c} = EXCLUDED.{c}" for c in _COLS)
_PARAM_COLS = ",\n    ".join(f":{c}" for c in _COLS)

_INSERT_SQL = f"""
INSERT INTO stock_indicator_daily (
    trade_date, stock_code,
    {_INSERT_COLS},
    created_at
) VALUES (
    :trade_date, :stock_code,
    {_PARAM_COLS},
    CURRENT_TIMESTAMP
)
ON CONFLICT (trade_date, stock_code) DO UPDATE SET
    {_UPDATE_COLS},
    created_at = EXCLUDED.created_at
"""


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------


def compute_stock_indicator_daily(engine) -> int:
    """增量刷新 stock_indicator_daily 表。

    从 stock_data 计算所有技术指标，仅处理 stock_indicator_daily
    中尚不存在的交易日。

    Returns:
        本次新增/更新的总行数。
    """
    with engine.connect() as conn:
        # 1. 获取 stock_indicator_daily 已有最新日期
        default_date = get_settings().sync.data_start_date
        last_date = conn.execute(
            text(
                "SELECT COALESCE(MAX(trade_date), CAST(:default_date AS date)) "
                "FROM stock_indicator_daily"
            ),
            {"default_date": default_date},
        ).scalar()

        # 2. 获取 stock_data 最新日期
        latest_stock = conn.execute(
            text("SELECT MAX(trade_date) FROM stock_data")
        ).scalar()

        if latest_stock is None:
            logger.warning("compute_indicator_skipped_no_stock_data")
            return 0

        if last_date >= latest_stock:
            logger.info(
                "indicator_up_to_date",
                last_indicator_date=str(last_date),
                latest_stock_date=str(latest_stock),
            )
            return 0

        logger.info(
            "compute_indicator_start",
            from_date=str(last_date),
            to_date=str(latest_stock),
        )

        # 3. 获取所有股票代码
        stocks = [
            row[0]
            for row in conn.execute(
                text("SELECT DISTINCT code FROM stock_data ORDER BY code")
            ).fetchall()
        ]
        logger.info("indicator_stock_count", count=len(stocks))

        total_rows = 0
        processed = 0
        skipped = 0

        for stock_code in stocks:
            df = _read_stock_data(conn, stock_code)
            if len(df) < 5:
                skipped += 1
                continue

            indicators = _compute_all_indicators(df)
            # 仅保留 stock_indicator_daily 中不存在的交易日
            new_data = indicators[indicators["trade_date"] > last_date]
            if new_data.empty:
                continue

            records = _to_records(new_data)
            conn.execute(text(_INSERT_SQL), records)
            total_rows += len(records)
            processed += 1

            if processed % 500 == 0:
                logger.info(
                    "indicator_progress",
                    processed=processed,
                    total_stocks=len(stocks),
                    rows_written=total_rows,
                )

        conn.commit()
        logger.info(
            "compute_indicator_completed",
            total_rows=total_rows,
            stocks_processed=processed,
            stocks_skipped=skipped,
        )
        return total_rows


def _read_stock_data(conn, stock_code: str) -> pd.DataFrame:
    """读取单只股票的全部历史 K 线数据（按 trade_date 升序）。"""
    result = conn.execute(
        text(
            "SELECT trade_date, code, open, high, low, close, volume "
            "FROM stock_data WHERE code = :code ORDER BY trade_date"
        ),
        {"code": stock_code},
    )
    rows = result.fetchall()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(
        rows,
        columns=["trade_date", "code", "open", "high", "low", "close", "volume"],
    )


def _to_records(df: pd.DataFrame) -> list[dict]:
    """将 DataFrame 转换为 list[dict]，NaN 转为 None（SQL NULL）。"""
    return df.where(pd.notna(df), None).to_dict("records")