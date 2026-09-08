"""Strong Trend V1 买入/卖出规则。

规则只使用当前交易日 (T 日) 收盘后已经能够获得的数据，
不允许使用未来数据 (shift(-1) / look-ahead)。
"""


def meets_entry_rules(data, params: dict) -> bool:
    """Strong Trend V1 买入规则 (全部满足 -> True)。

    1. close > MA20
    2. MA20 > MA60
    3. MACD DIF > DEA
    4. macd_hist_increasing = true
    5. macd_hist_increasing_days > 3

    可选 (use_factor=true 时启用):
    6. factor_rank <= factor_rank_max (top 30%)
    """
    # 任一输入指标缺失 (None/停牌缺行) 时视为条件不满足，不参与比较
    required = (data.close, data.ma20, data.ma60, data.macd_dif, data.macd_dea)
    if any(v is None for v in required):
        return False
    if not (data.close > data.ma20 and data.ma20 > data.ma60):
        return False
    if not data.macd_dif > data.macd_dea:
        return False
    if not data.macd_hist_increasing:
        return False
    if not data.macd_hist_increasing_days > params.get("macd_hist_increasing_days_min", 3):
        return False

    if params.get("use_factor", False):
        rank = data.factor_rank
        if rank is None:
            return False
        if not rank <= params.get("factor_rank_max", 0.30):
            return False

    return True


def meets_exit_rules(data) -> bool:
    """Strong Trend V1 卖出规则: ma5 < ma10 (指标缺失时不卖出)。"""
    if data.ma5 is None or data.ma10 is None:
        return False
    return data.ma5 < data.ma10