"""技术状态规则集中定义。

stock_state_daily 的所有状态判定逻辑集中于此，统一维护，不散落代码各处。
每条规则：输入一个按 trade_date 排序的 DataFrame（包含指标 + 原始数据列），
        输出一个 Boolean Series（True/False/None）。

规则不可包含机器学习、不可动态调整阈值。
"""

import pandas as pd

# ---- 规则接口说明 ----
# 每条规则是一个函数: fn(df) -> pd.Series[bool|None]
# df 包含以下列（来自 stock_indicator_daily JOIN stock_data）:
#   trade_date, stock_code,
#   close, volume, change_pct,           -- 来自 stock_data
#   volume_ma5, volume_ma10, volume_ma20,
#   ma5, ma10, ma20, ma30, ma60, ma120, ma250,
#   ema5, ema10, ema20, ema30, ema60, ema120, ema250,
#   macd_dif, macd_dea, macd_hist,
#   obv, obv_ma20,
#   k_value, d_value, j_value,
#   capital_fast, capital_slow,
#   capital_life, capital_life_ma


def price_above_ma5(df):
    return df["close"] > df["ma5"]


def price_above_ma20(df):
    return df["close"] > df["ma20"]


def price_above_ma60(df):
    return df["close"] > df["ma60"]


def ma5_above_ma20(df):
    return df["ma5"] > df["ma20"]


def ma20_above_ma60(df):
    return df["ma20"] > df["ma60"]


def trend_short_bull(df):
    return (df["close"] > df["ma20"]) & (df["ma5"] > df["ma20"])


def trend_mid_bull(df):
    return (df["close"] > df["ma60"]) & (df["ma20"] > df["ma60"])

# ---- MACD 状态 ----


def macd_bullish(df):
    return df["macd_dif"] > df["macd_dea"]


def macd_golden_cross(df):
    """今日 dif>dea 且 昨日 dif<=dea。"""
    dif = df["macd_dif"]
    dea = df["macd_dea"]
    today = dif > dea
    yesterday = dif.shift(1) <= dea.shift(1)
    return today & yesterday


def macd_dead_cross(df):
    """今日 dif<dea 且 昨日 dif>=dea。"""
    dif = df["macd_dif"]
    dea = df["macd_dea"]
    today = dif < dea
    yesterday = dif.shift(1) >= dea.shift(1)
    return today & yesterday


def macd_hist_positive(df):
    return df["macd_hist"] > 0


def macd_hist_increasing(df):
    return df["macd_hist"] > df["macd_hist"].shift(1)

# ---- KDJ 状态 ----


def kdj_golden_cross(df):
    """今日 K>D 且 昨日 K<=D。"""
    k = df["k_value"]
    d = df["d_value"]
    today = k > d
    yesterday = k.shift(1) <= d.shift(1)
    return today & yesterday


def kdj_over_buy(df):
    return df["k_value"] > 80


def kdj_over_sell(df):
    return df["k_value"] < 20

# ---- 成交量状态 ----


def volume_expand(df):
    return df["volume"] > df["volume_ma20"] * 1.5


def volume_shrink(df):
    return df["volume"] < df["volume_ma20"] * 0.7


def price_volume_confirm(df):
    """今日收盘 > 昨日收盘 且 成交量 > 20日均量。"""
    return (df["close"] > df["close"].shift(1)) & (df["volume"] > df["volume_ma20"])

# ---- OBV 资金状态 ----


def obv_above_ma20(df):
    return df["obv"] > df["obv_ma20"]


def obv_rising(df):
    return df["obv"] > df["obv"].shift(1)


def obv_price_divergence(df):
    """价格创 60 日新高，但 OBV 未同步创新高。"""
    close = df["close"]
    obv = df["obv"]
    # 最近 60 日（不含今日）的最高收盘价
    rolling_max_close = close.shift(1).rolling(window=60, min_periods=1).max()
    rolling_max_obv = obv.shift(1).rolling(window=60, min_periods=1).max()
    return (close >= rolling_max_close) & (obv < rolling_max_obv)

# ---- 主力资金状态 ----


def capital_bullish(df):
    return df["capital_fast"] > df["capital_slow"]


def capital_cross_up(df):
    """今日 fast>slow 且 昨日 fast<=slow。"""
    fast = df["capital_fast"]
    slow = df["capital_slow"]
    today = fast > slow
    yesterday = fast.shift(1) <= slow.shift(1)
    return today & yesterday


def capital_life_up(df):
    return df["capital_life"] > df["capital_life_ma"]

# ---- 突破状态 ----


def high_break_20(df):
    """收盘价突破 20 日最高收盘价（不含今日）。"""
    close = df["close"]
    prev_20_max = close.shift(1).rolling(window=20, min_periods=1).max()
    return close > prev_20_max


def high_break_60(df):
    """收盘价突破 60 日最高收盘价（不含今日）。"""
    close = df["close"]
    prev_60_max = close.shift(1).rolling(window=60, min_periods=1).max()
    return close > prev_60_max


def new_high(df):
    """收盘价 >= 最近 250 日最高收盘价（不含今日）。"""
    close = df["close"]
    prev_250_max = close.shift(1).rolling(window=250, min_periods=1).max()
    return close >= prev_250_max

# ---- 风险状态 ----


def extreme_up(df):
    return df["change_pct"] >= 9.0


def extreme_down(df):
    return df["change_pct"] <= -9.0


def high_volatility(df):
    """最近 20 日最高涨幅 - 最低涨幅 > 20%。"""
    chg_20_high = df["change_pct"].rolling(window=20, min_periods=20).max()
    chg_20_low = df["change_pct"].rolling(window=20, min_periods=20).min()
    return (chg_20_high - chg_20_low) > 20.0


# ---- 规则注册表 ----
# 每项: (列名, 计算函数, [依赖的输入列名])
# 依赖列用于 NaN 传播：当任一输入列为 NaN 时，对应状态设为 NULL。


def _nan_input_mask(df, columns):
    """生成 NaN 掩码：任一指定列为 NaN 的行。"""
    mask = pd.Series(False, index=df.index)
    for col in columns:
        if col in df.columns:
            mask = mask | df[col].isna()
    return mask


ALL_RULES = [
    # 趋势状态
    ("price_above_ma5", price_above_ma5, ["close", "ma5"]),
    ("price_above_ma20", price_above_ma20, ["close", "ma20"]),
    ("price_above_ma60", price_above_ma60, ["close", "ma60"]),
    ("ma5_above_ma20", ma5_above_ma20, ["ma5", "ma20"]),
    ("ma20_above_ma60", ma20_above_ma60, ["ma20", "ma60"]),
    ("trend_short_bull", trend_short_bull, ["close", "ma5", "ma20"]),
    ("trend_mid_bull", trend_mid_bull, ["close", "ma20", "ma60"]),
    # MACD 状态
    ("macd_bullish", macd_bullish, ["macd_dif", "macd_dea"]),
    ("macd_golden_cross", macd_golden_cross, ["macd_dif", "macd_dea"]),
    ("macd_dead_cross", macd_dead_cross, ["macd_dif", "macd_dea"]),
    ("macd_hist_positive", macd_hist_positive, ["macd_hist"]),
    ("macd_hist_increasing", macd_hist_increasing, ["macd_hist"]),
    # KDJ 状态
    ("kdj_golden_cross", kdj_golden_cross, ["k_value", "d_value"]),
    ("kdj_over_buy", kdj_over_buy, ["k_value"]),
    ("kdj_over_sell", kdj_over_sell, ["k_value"]),
    # 成交量状态
    ("volume_expand", volume_expand, ["volume", "volume_ma20"]),
    ("volume_shrink", volume_shrink, ["volume", "volume_ma20"]),
    ("price_volume_confirm", price_volume_confirm, ["close", "volume", "volume_ma20"]),
    # OBV 资金状态
    ("obv_above_ma20", obv_above_ma20, ["obv", "obv_ma20"]),
    ("obv_rising", obv_rising, ["obv"]),
    ("obv_price_divergence", obv_price_divergence, ["close", "obv"]),
    # 主力资金状态
    ("capital_bullish", capital_bullish, ["capital_fast", "capital_slow"]),
    ("capital_cross_up", capital_cross_up, ["capital_fast", "capital_slow"]),
    ("capital_life_up", capital_life_up, ["capital_life", "capital_life_ma"]),
    # 突破状态
    ("high_break_20", high_break_20, ["close"]),
    ("high_break_60", high_break_60, ["close"]),
    ("new_high", new_high, ["close"]),
    # 风险状态
    ("extreme_up", extreme_up, ["change_pct"]),
    ("extreme_down", extreme_down, ["change_pct"]),
    ("high_volatility", high_volatility, ["change_pct"]),
]


def apply_all_rules(df: pd.DataFrame) -> pd.DataFrame:
    """对一只股票的完整数据应用全部状态规则。

    NaN 传播：当规则的任一输入列为 NaN 时，对应状态设为 NULL（pd.NA）。
    这确保"指标不存在时返回 NULL"，而非强制填 False。

    Args:
        df: 按 trade_date 升序排列，包含所有必需的指标和原始数据列。

    Returns:
        [trade_date, stock_code, 30 个 boolean state 列] 的 DataFrame。
    """
    result = pd.DataFrame({
        "trade_date": df["trade_date"],
        "stock_code": df["stock_code"].iloc[0],
    })

    for col_name, fn, input_cols in ALL_RULES:
        raw = fn(df).astype("boolean")  # 可空布尔类型
        # NaN 传播：当依赖指标缺失时，状态应为 NULL
        mask = _nan_input_mask(df, input_cols)
        raw[mask] = pd.NA
        result[col_name] = raw

    return result