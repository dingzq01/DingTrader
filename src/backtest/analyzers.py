"""回测绩效分析（纯函数，无数据库依赖，可单测）。"""

import math

import pandas as pd


def compute_performance(
    equity_df: pd.DataFrame,
    trades: list[dict] | None = None,
    initial_capital: float = 1_000_000.0,
) -> dict:
    """从权益曲线与交易明细计算组合级绩效。"""
    trades = trades or []
    if equity_df is None or equity_df.empty:
        return {
            "total_return": 0.0,
            "annual_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe_ratio": 0.0,
            "win_rate": 0.0,
            "profit_factor": None,
            "trade_count": len(trades),
            "avg_trade_return": None,
            "avg_holding_days": None,
            "max_single_trade_return": None,
            "max_single_trade_loss": None,
        }

    value = equity_df["value"].astype("float")

    # 总收益
    final_value = float(value.iloc[-1])
    total_return = final_value / initial_capital - 1.0

    # 年化收益 (按自然日)
    dates = pd.to_datetime(equity_df["date"])
    days = (dates.iloc[-1] - dates.iloc[0]).days
    years = max(days / 365.25, 1e-9)
    if total_return > -1.0:
        annual_return = (1.0 + total_return) ** (1.0 / years) - 1.0
    else:
        annual_return = -1.0

    # 最大回撤
    cummax = value.cummax()
    drawdown = value / cummax - 1.0
    max_drawdown = float(drawdown.min())

    # 夏普比率 (日频, 年化 252)
    daily_returns = value.pct_change().dropna()
    if len(daily_returns) > 1 and float(daily_returns.std()) > 1e-12:
        sharpe_ratio = float(daily_returns.mean() / daily_returns.std() * math.sqrt(252))
    else:
        sharpe_ratio = 0.0

    # 交易统计
    returns = [t["return_pct"] for t in trades if t.get("return_pct") is not None]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]

    win_rate = len(wins) / len(returns) if returns else 0.0

    gross_profit = sum(wins)
    gross_loss = -sum(losses)
    if gross_loss > 1e-9:
        profit_factor = gross_profit / gross_loss
    else:
        profit_factor = None  # 无亏损 → 无法定义 (DB 可空)

    avg_trade_return = sum(returns) / len(returns) if returns else None

    holding_days = [t["holding_days"] for t in trades if t.get("holding_days") is not None]
    avg_holding_days = sum(holding_days) / len(holding_days) if holding_days else None

    max_single_trade_return = max(returns) if returns else None
    max_single_trade_loss = min(losses) if losses else None

    return {
        "total_return": total_return,
        "annual_return": annual_return,
        "max_drawdown": max_drawdown,
        "sharpe_ratio": sharpe_ratio,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "trade_count": len(trades),
        "avg_trade_return": avg_trade_return,
        "avg_holding_days": avg_holding_days,
        "max_single_trade_return": max_single_trade_return,
        "max_single_trade_loss": max_single_trade_loss,
    }