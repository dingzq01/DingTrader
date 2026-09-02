"""技术状态规则集中定义。

stock_state_daily 的所有状态判定逻辑集中于此，统一维护，不散落代码各处。
每条规则：输入一个按 trade_date 排序的 DataFrame（包含指标 + 原始数据列），
        输出一个 Boolean Series（True/False/None）或 Integer Series（持续时间类）。

规则不可包含机器学习、不可动态调整阈值。
"""

import pandas as pd

# ---- MACD DIF trough 识别参数（plan10）----
# 局部底部：DIF[t] < DIF[t-1..t-_TROUGH_LEFT] 且 DIF[t] < DIF[t+1..t+_TROUGH_RIGHT]
# 确认延迟：该 trough 最早只能在 t + _TROUGH_RIGHT 日确认。
#          macd_trough / macd_bottom_divergence 一律在“确认日”置 True，禁止未来函数。
_TROUGH_LEFT = 3
_TROUGH_RIGHT = 3

# ---- 规则接口说明 ----
# 每条规则是一个函数: fn(df) -> pd.Series[bool|None]
# df 包含以下列（来自 stock_indicator_daily JOIN stock_data）:
#   trade_date, stock_code,
#   close, volume, change_pct, low,       -- 来自 stock_data
#   volume_ma5, volume_ma10, volume_ma20,
#   ma5, ma10, ma20, ma30, ma60, ma120, ma250,
#   ema5, ema10, ema20, ema30, ema60, ema120, ema250,
#   macd_dif, macd_dea, macd_hist,
#   obv, obv_ma20,
#   k_value, d_value, j_value,
#   capital_fast, capital_slow,
#   capital_life, capital_life_ma,
#   high20, high60, low20, low60           -- 价格区间特征（stock_indicator_daily）


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


def macd_hist_increasing(df):
    """今日 macd_hist > 昨日。相等或缺失时为 False。"""
    return df["macd_hist"] > df["macd_hist"].shift(1)


def _consecutive_true_count(cond: pd.Series) -> pd.Series:
    """计算连续满足条件的交易日数量；条件为 False 或缺失时重置为 0。"""
    cond = cond.fillna(False).astype(int)
    group = (~cond.astype(bool)).cumsum()
    return cond.groupby(group).cumsum().astype("Int64")


def macd_hist_increasing_days(df):
    """macd_hist 连续增加的交易日数量（含今日）。

    例：-0.50 -0.40 -0.25 -0.10 → 0 1 2 3。
    即使 macd_hist < 0，只要持续增加就表示空头动能减弱，而不是已转多。
    """
    return _consecutive_true_count(df["macd_hist"] > df["macd_hist"].shift(1))


def macd_hist_decreasing_days(df):
    """macd_hist 连续减少的交易日数量（含今日）。

    即使 macd_hist > 0，只要持续减少就表示多头动能减弱，而不是已转空。
    """
    return _consecutive_true_count(df["macd_hist"] < df["macd_hist"].shift(1))


def _macd_dif_trough_candidates(df) -> pd.Series:
    """DIF 局部底部候选（trough 位于索引 t；此处为候选，尚未确认）。

    有效 trough：DIF[t] 严格低于左右各 _TROUGH_LEFT/_TROUGH_RIGHT 根，
    即 DIF[t] < min(dif[t-N..t-1]) 且 DIF[t] < min(dif[t+1..t+N])，
    等价于 DIF[t] 是 [t-N..t+N] 窗口内的严格最低点，避免把肩部误判为底。
    """
    dif = df["macd_dif"]
    left_shoulder_min = dif.rolling(_TROUGH_LEFT, min_periods=_TROUGH_LEFT).min().shift(1)
    right_shoulder_min = dif.rolling(_TROUGH_RIGHT, min_periods=_TROUGH_RIGHT).min().shift(-_TROUGH_RIGHT)
    cand = (dif < left_shoulder_min) & (dif < right_shoulder_min)
    return cand.fillna(False).astype("boolean")


def macd_trough(df):
    """当前 DIF 是否形成有效局部底部（无未来函数）。

    候选 trough 落在 t：DIF[t] < 之前 _TROUGH_LEFT 根 且 < 之后 _TROUGH_RIGHT 根。
    信号最早只能在 t + _TROUGH_RIGHT 日确认，因此把 True 放在“确认日”当天，
    确保任何日子读取 macd_trough 时该信号必然已经可用。
    """
    candidates = _macd_dif_trough_candidates(df)
    confirmed = candidates.shift(_TROUGH_RIGHT)
    return confirmed.fillna(False).astype("boolean")


def macd_bottom_divergence(df):
    """经典 MACD 底背离：DIF 第二底抬高 + 股价 Low 第二底创新低。

    以 DIF trough 为锚点（不另找价格 trough）：
        M1 = 前一个有效 DIF trough
        M2 = 最近一个有效 DIF trough
    满足 DIF[M2] > DIF[M1] 且 Low[M2] < Low[M1] 时，在 M2 的确认日置 True。
    不做未来回填，避免 look-ahead bias。
    """
    dif = df["macd_dif"]
    low = df["low"]
    candidates = _macd_dif_trough_candidates(df)
    result = pd.Series(False, index=df.index, dtype="boolean")

    trough_positions = candidates[candidates].index
    prev_pos = None
    for pos in trough_positions:
        conf = pos + _TROUGH_RIGHT
        if prev_pos is not None and conf < len(df):
            d_prev, d_cur = dif.iloc[prev_pos], dif.iloc[pos]
            l_prev, l_cur = low.iloc[prev_pos], low.iloc[pos]
            if all(pd.notna(v) for v in (d_prev, d_cur, l_prev, l_cur)):
                if d_cur > d_prev and l_cur < l_prev:
                    result.iloc[conf] = True
        prev_pos = pos

    return result

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
    """今日收盘价突破 20 日前高（使用昨日之前形成的 high20，避免自包含）。

    high20 = max(high[t-19..t])，突破判定必须用 high20.shift(1)，
    不允许当前 K 线的高价参与前高计算。
    """
    close = df["close"]
    return close > df["high20"].shift(1)


def high_break_60(df):
    """今日收盘价突破 60 日前高（使用昨日之前形成的 high60）。"""
    close = df["close"]
    return close > df["high60"].shift(1)


def new_high(df):
    """收盘价 >= 最近 250 日最高收盘价（不含今日，无未来函数）。

    保留既有业务语义（250 日历史新高，plan10 允许保留已有明确的新高定义）。
    """
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
# 每项: (列名, 计算函数, [依赖的输入列名], 数据类型)
#   列名      : 入库列名（stock_state_daily 的字段）
#   计算函数  : fn(df) -> Series
#   依赖输入列: 用于 NaN 传播 —— 当任一输入列为 NaN 时，对应状态设为 NULL
#   数据类型  : "bool" -> BOOLEAN；"int" -> INTEGER（持续时间类状态，plan10 统一为 INTEGER）


def _nan_input_mask(df, columns):
    """生成 NaN 掩码：任一指定列为 NaN 的行。"""
    mask = pd.Series(False, index=df.index)
    for col in columns:
        if col in df.columns:
            mask = mask | df[col].isna()
    return mask


ALL_RULES = [
    # 趋势状态
    ("price_above_ma5", price_above_ma5, ["close", "ma5"], "bool"),
    ("price_above_ma20", price_above_ma20, ["close", "ma20"], "bool"),
    ("price_above_ma60", price_above_ma60, ["close", "ma60"], "bool"),
    ("ma5_above_ma20", ma5_above_ma20, ["ma5", "ma20"], "bool"),
    ("ma20_above_ma60", ma20_above_ma60, ["ma20", "ma60"], "bool"),
    ("trend_short_bull", trend_short_bull, ["close", "ma5", "ma20"], "bool"),
    ("trend_mid_bull", trend_mid_bull, ["close", "ma20", "ma60"], "bool"),
    # MACD 状态
    ("macd_bullish", macd_bullish, ["macd_dif", "macd_dea"], "bool"),
    ("macd_golden_cross", macd_golden_cross, ["macd_dif", "macd_dea"], "bool"),
    ("macd_dead_cross", macd_dead_cross, ["macd_dif", "macd_dea"], "bool"),
    ("macd_hist_increasing", macd_hist_increasing, ["macd_hist"], "bool"),
    ("macd_hist_increasing_days", macd_hist_increasing_days, ["macd_hist"], "int"),
    ("macd_hist_decreasing_days", macd_hist_decreasing_days, ["macd_hist"], "int"),
    ("macd_trough", macd_trough, ["macd_dif"], "bool"),
    ("macd_bottom_divergence", macd_bottom_divergence, ["macd_dif", "low"], "bool"),
    # KDJ 状态
    ("kdj_golden_cross", kdj_golden_cross, ["k_value", "d_value"], "bool"),
    ("kdj_over_buy", kdj_over_buy, ["k_value"], "bool"),
    ("kdj_over_sell", kdj_over_sell, ["k_value"], "bool"),
    # 成交量状态
    ("volume_expand", volume_expand, ["volume", "volume_ma20"], "bool"),
    ("volume_shrink", volume_shrink, ["volume", "volume_ma20"], "bool"),
    ("price_volume_confirm", price_volume_confirm, ["close", "volume", "volume_ma20"], "bool"),
    # OBV 资金状态
    ("obv_above_ma20", obv_above_ma20, ["obv", "obv_ma20"], "bool"),
    ("obv_rising", obv_rising, ["obv"], "bool"),
    ("obv_price_divergence", obv_price_divergence, ["close", "obv"], "bool"),
    # 主力资金状态
    ("capital_bullish", capital_bullish, ["capital_fast", "capital_slow"], "bool"),
    ("capital_cross_up", capital_cross_up, ["capital_fast", "capital_slow"], "bool"),
    ("capital_life_up", capital_life_up, ["capital_life", "capital_life_ma"], "bool"),
    # 突破状态
    ("high_break_20", high_break_20, ["close", "high20"], "bool"),
    ("high_break_60", high_break_60, ["close", "high60"], "bool"),
    ("new_high", new_high, ["close"], "bool"),
    # 风险状态
    ("extreme_up", extreme_up, ["change_pct"], "bool"),
    ("extreme_down", extreme_down, ["change_pct"], "bool"),
    ("high_volatility", high_volatility, ["change_pct"], "bool"),
]


def apply_all_rules(df: pd.DataFrame) -> pd.DataFrame:
    """对一只股票的完整数据应用全部状态规则。

    NaN 传播：当规则的任一输入列为 NaN 时，对应状态设为 NULL（pd.NA）。
    这确保"指标不存在时返回 NULL"，而非强制填 False。

    Args:
        df: 按 trade_date 升序排列，包含所有必需的指标和原始数据列。

    Returns:
        [trade_date, stock_code, 全部 state 列(BOOLEAN/INTEGER)] 的 DataFrame。
    """
    result = pd.DataFrame({
        "trade_date": df["trade_date"],
        "stock_code": df["stock_code"].iloc[0],
    })

    for col_name, fn, input_cols, dtype in ALL_RULES:
        # 布尔状态 -> 可空布尔；持续时间 -> 可空整数
        raw = fn(df).astype("Int64" if dtype == "int" else "boolean")
        # NaN 传播：当依赖指标缺失时，状态应为 NULL
        mask = _nan_input_mask(df, input_cols)
        raw[mask] = pd.NA
        result[col_name] = raw

    return result