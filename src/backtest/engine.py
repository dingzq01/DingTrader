"""组合回测引擎。

Backtrader 负责订单/资金/持仓，策略规则来自独立的 BaseResearchStrategy。

执行时序 (避免 look-ahead bias):
    T 日收盘生成 Entry/Exit Signal
        ↓
    T+1 开盘执行订单 (Backtrader Market Order 默认在下一根 bar 开盘成交)

资金管理规则 (属于 Backtest Configuration, 不属于 Strategy):
    - 单只股票固定买入金额 position_size
    - 最多同时持仓 max_positions 只
    - 已持仓的股票不重复买入 (不金字塔加仓)
    - 同一天同时出现 entry/exit 时优先处理 exit
"""

import datetime

import backtrader as bt
import pandas as pd

from src.backtest.analyzers import compute_performance
from src.backtest.commission import AStockCommission
from src.backtest.data_feed import StockResearchFeed
from src.config.settings import get_settings
from src.strategies.research_base import BaseResearchStrategy, StrategyData
from src.utils.logging import get_logger

logger = get_logger(__name__)

EXIT_REASON_MA5_BELOW_MA10 = "ma5_below_ma10"


def _is_missing(v) -> bool:
    if v is None:
        return True
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def lines_to_strategy_data(data, trade_date: datetime.date) -> StrategyData:
    """从 Backtrader data line 组装 StrategyData (仅当前 bar, T 日收盘可获数据)。"""
    def _f(line) -> float | None:
        v = line[0]
        return None if _is_missing(v) else float(v)

    def _b(line) -> bool:
        v = line[0]
        return False if _is_missing(v) else bool(v)

    def _i(line) -> int:
        v = line[0]
        return 0 if _is_missing(v) else int(v)

    return StrategyData(
        trade_date=trade_date,
        stock_code=data._name,
        open=_f(data.open),
        close=_f(data.close),
        ma5=_f(data.ma5),
        ma10=_f(data.ma10),
        ma20=_f(data.ma20),
        ma60=_f(data.ma60),
        macd_dif=_f(data.macd_dif),
        macd_dea=_f(data.macd_dea),
        macd_hist=_f(data.macd_hist),
        macd_hist_increasing=_b(data.macd_hist_increasing),
        macd_hist_increasing_days=_i(data.macd_hist_increasing_days),
        factor_rank=_f(data.factor_rank),
    )


class ResearchPortfolioStrategy(bt.Strategy):
    """组合级回测策略: 调用独立策略的 entry_signal / exit_signal。"""

    params = (
        ("research_strategy", None),   # BaseResearchStrategy 实例
        ("position_size", 1_000_000.0),  # 单只固定买入金额
        ("max_positions", 10),           # 最大同时持仓数
        ("entry_size_ratio", 0.95),      # 单笔最大占用可用资金比例 (留手续费余量)
    )

    def __init__(self):
        self._open_trades = {}   # data -> {entry_date, entry_price, exit_reason}
        self._pending = {}       # data -> order (未成交订单)
        self._pending_notional = {}  # data -> 已预留资金估算 (下单时 size*close)
        self._reserved_cash = 0.0
        self.closed_trades = []  # list[dict]
        self.equity_points = []  # list[(date, value, cash)]

    def next(self):
        ref_date = self.datas[0].datetime.date(0)
        self.equity_points.append(
            (ref_date, float(self.broker.getvalue()), float(self.broker.getcash()))
        )

        slots_used = len(self._open_trades) + len(self._pending)
        for d in self.datas[1:]:
            if len(d) == 0:
                continue
            if d.datetime.date(0) != ref_date:
                continue  # 今日该股无交易 (停牌/未上市)
            if d in self._pending:
                continue  # 已有未成交订单，等待成交后再判断

            data = lines_to_strategy_data(d, ref_date)
            if data.close is None:
                continue  # 停牌/未上市: 当日无有效行情, 跳过交易
            if d in self._open_trades:
                if self.params.research_strategy.exit_signal(data):
                    self._open_trades[d]["exit_reason"] = EXIT_REASON_MA5_BELOW_MA10
                    order = self.close(data=d)
                    self._pending[d] = order
                    continue

            # 2) 买入 — 未持仓 + 未达最大持仓数 + entry_signal
            if d not in self._open_trades and slots_used < self.params.max_positions:
                if self.params.research_strategy.entry_signal(data):
                    size = self._calc_buy_size(d)
                    if size > 0:
                        order = self.buy(data=d, size=size)
                        self._pending[d] = order
                        self._pending_notional[d] = size * float(data.close)
                        self._reserved_cash += self._pending_notional[d]
                        slots_used += 1

    def _calc_buy_size(self, data) -> int:
        """按固定买入金额计算股数 (100 股整数倍, 且不超过可用预留资金)。"""
        price = float(data.close[0])
        if price <= 0:
            return 0
        target = int(self.params.position_size / price / 100) * 100
        available = self.broker.getcash() - self._reserved_cash
        if available <= 0:
            return 0
        cash_capped = int(available * self.params.entry_size_ratio / price / 100) * 100
        size = min(target, cash_capped)
        return size if size >= 100 else 0

    def notify_order(self, order):
        if order.status in (order.Submitted, order.Accepted):
            return
        d = order.data
        if order.status == order.Completed:
            if order.isbuy():
                self._open_trades[d] = {
                    "entry_date": bt.num2date(order.executed.dt).date(),
                    "entry_price": float(order.executed.price),
                    "exit_reason": None,
                }
            else:
                entry = self._open_trades.pop(d, None)
                if entry is not None:
                    trade = {
                        "stock_code": d._name,
                        "entry_date": entry["entry_date"],
                        "entry_price": entry["entry_price"],
                        "exit_date": bt.num2date(order.executed.dt).date(),
                        "exit_price": float(order.executed.price),
                        "exit_reason": entry["exit_reason"],
                        "holding_days": None,  # 引擎结束后按交易日历计算
                        "return_pct": None,
                    }
                    if trade["entry_price"]:
                        trade["return_pct"] = (
                            trade["exit_price"] / trade["entry_price"] - 1
                        ) * 100
                    self.closed_trades.append(trade)
        else:
            logger.warning(
                "order_failed",
                status=order.getstatusname(),
                stock_code=d._name,
                order_ref=order.ref,
            )
        # 释放该订单的预留资金
        released = self._pending_notional.pop(d, 0.0)
        self._reserved_cash = max(0.0, self._reserved_cash - released)
        self._pending.pop(d, None)


def _union_calendar(frames: list[dict], start_date, end_date) -> list[datetime.date]:
    """统一交易日历 (按 [start_date, end_date] 裁剪)。"""
    dates = set()
    for f in frames:
        dates.update(pd.Timestamp(d).date() for d in f["df"].index)
    calendar = sorted(dates)
    if start_date is not None:
        calendar = [d for d in calendar if d >= start_date]
    if end_date is not None:
        calendar = [d for d in calendar if d <= end_date]
    return calendar


def _trim_frame(df: pd.DataFrame, start_date, end_date) -> pd.DataFrame:
    if start_date is not None:
        df = df[df.index >= pd.Timestamp(start_date)]
    if end_date is not None:
        df = df[df.index <= pd.Timestamp(end_date)]
    return df


def _finalize_holding_days(trades: list[dict], calendar: list[datetime.date]) -> list[dict]:
    """按交易日历计算 holding_days (exit 与 entry 之间的交易日数)。"""
    cal = {d: i for i, d in enumerate(calendar)}
    for t in trades:
        ei = cal.get(t["entry_date"])
        xi = cal.get(t["exit_date"])
        t["holding_days"] = (xi - ei) if (ei is not None and xi is not None) else None
    return trades


def run_backtest(
    research_strategy: BaseResearchStrategy,
    frames: list[dict],
    initial_capital: float = 10_000_000.0,
    position_size: float = 1_000_000.0,
    max_positions: int = 10,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    slippage_pct: float | None = None,
) -> dict:
    """运行组合回测。

    Args:
        research_strategy: BaseResearchStrategy 实例 (仅规则判断)
        frames: data_adapter.load_stock_frames() 的输出
        initial_capital: 初始资金
        position_size:   单只固定买入金额
        max_positions:   最大同时持仓数
        start_date/end_date: 日期范围 (默认取 frames 实际范围)
        slippage_pct: 滑点 (默认读配置)

    Returns:
        {
            "equity_curve": DataFrame(date/value/cash),
            "trades": list[dict],           # 已按日历补齐 holding_days
            "performance": dict,
            "strategy": bt.Strategy 实例 (调试用),
            "calendar": list[datetime.date],
        }
    """
    if not frames:
        logger.warning("backtest_no_frames")
        return {
            "equity_curve": pd.DataFrame(),
            "trades": [],
            "performance": {},
            "strategy": None,
            "calendar": [],
        }

    settings = get_settings()
    slippage = settings.backtest.slippage if slippage_pct is None else slippage_pct

    calendar = _union_calendar(frames, start_date, end_date)

    cerebro = bt.Cerebro()

    # 统一交易日历参考数据 (始终推进, 供各股票判断当日是否可交易)
    index_df = pd.DataFrame(
        {
            "open": [1.0] * len(calendar),
            "high": [1.0] * len(calendar),
            "low": [1.0] * len(calendar),
            "close": [1.0] * len(calendar),
            "volume": [0.0] * len(calendar),
            "openinterest": [0.0] * len(calendar),
        },
        index=pd.DatetimeIndex(calendar),
    )
    cerebro.adddata(bt.feeds.PandasData(dataname=index_df), name="__index__")

    for f in frames:
        df = _trim_frame(f["df"], start_date, end_date)
        if df.empty:
            continue
        cerebro.adddata(StockResearchFeed(dataname=df), name=f["stock_code"])

    cerebro.addstrategy(
        ResearchPortfolioStrategy,
        research_strategy=research_strategy,
        position_size=position_size,
        max_positions=max_positions,
    )

    cerebro.broker.setcash(initial_capital)
    cerebro.broker.addcommissioninfo(AStockCommission())
    cerebro.broker.set_slippage_perc(slippage)

    logger.info(
        "backtest_run_start",
        strategy=research_strategy.get_full_name(),
        stocks=len(cerebro.datas) - 1,
        sessions=len(calendar),
        initial_capital=initial_capital,
        position_size=position_size,
        max_positions=max_positions,
        start=str(calendar[0]) if calendar else None,
        end=str(calendar[-1]) if calendar else None,
    )

    results = cerebro.run()
    strat = results[0]

    _finalize_holding_days(strat.closed_trades, calendar)

    equity_df = pd.DataFrame(strat.equity_points, columns=["date", "value", "cash"])
    perf = compute_performance(equity_df, strat.closed_trades, initial_capital)

    logger.info(
        "backtest_run_complete",
        strategy=research_strategy.get_full_name(),
        total_return=perf.get("total_return"),
        annual_return=perf.get("annual_return"),
        max_drawdown=perf.get("max_drawdown"),
        sharpe=perf.get("sharpe_ratio"),
        win_rate=perf.get("win_rate"),
        trade_count=perf.get("trade_count"),
    )

    return {
        "equity_curve": equity_df,
        "trades": strat.closed_trades,
        "performance": perf,
        "strategy": strat,
        "calendar": calendar,
    }