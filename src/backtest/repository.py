"""回测结果持久化层 (plan11 数据模型)。

策略定义 / 回测运行 / 总体结果 / 交易明细 四个表的增删查接口，
供 scripts/run_strong_trend_backtest.py 落盘，也可用于后续 A/B 实验对比查询。

存储约定：
    - run_id: 每次回测随机生成的 32 位字符串 (不依赖时间戳，避免并发冲突)
    - backtest_config / parameter_config: JSON 文本
    - 纯函数逻辑放在 analyzers / engine，本模块只负责存取。
"""

import datetime
import json
import uuid

from sqlalchemy import select

from src.data.models import (
    BacktestResultRecord,
    BacktestRun,
    BacktestTrade,
    StrategyDefinition,
    get_engine,
    get_session,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)


def generate_run_id() -> str:
    """生成回测 run_id (32 位 hex)。"""
    return uuid.uuid4().hex


def to_json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# 策略定义
# ---------------------------------------------------------------------------


def ensure_strategy_definition(
    strategy,
    parameter_config: dict | None = None,
    engine=None,
) -> int:
    """按 (strategy_name, strategy_version) upsert 策略定义。

    存在则更新规则描述与参数配置; 不存在则插入。返回记录 id。
    """
    ses = get_session(engine or get_engine())
    try:
        row = ses.execute(
            select(StrategyDefinition).where(
                StrategyDefinition.strategy_name == strategy.strategy_name,
                StrategyDefinition.strategy_version == strategy.strategy_version,
            )
        ).scalar_one_or_none()

        pconf = parameter_config if parameter_config is not None else getattr(strategy, "params", None)
        pjson = to_json(pconf) if pconf is not None else None

        if row is None:
            row = StrategyDefinition(
                strategy_name=strategy.strategy_name,
                strategy_version=strategy.strategy_version,
                strategy_type=getattr(strategy, "strategy_type", ""),
                description=getattr(strategy, "description", None),
                entry_rule=getattr(strategy, "entry_rule", None),
                exit_rule=getattr(strategy, "exit_rule", None),
                parameter_config=pjson,
                status="active",
            )
            ses.add(row)
        else:
            row.strategy_type = getattr(strategy, "strategy_type", "") or row.strategy_type
            row.description = getattr(strategy, "description", None) or row.description
            row.entry_rule = getattr(strategy, "entry_rule", None) or row.entry_rule
            row.exit_rule = getattr(strategy, "exit_rule", None) or row.exit_rule
            if pjson is not None:
                row.parameter_config = pjson
            row.updated_at = datetime.datetime.utcnow()

        ses.commit()
        ses.refresh(row)
        return row.id
    except Exception:
        ses.rollback()
        raise
    finally:
        ses.close()


# ---------------------------------------------------------------------------
# 回测运行 / 结果 / 交易
# ---------------------------------------------------------------------------


def save_run(
    run_id: str,
    strategy,
    start_date: datetime.date,
    end_date: datetime.date,
    initial_capital: float,
    backtest_config: dict | None = None,
    stock_count: int | None = None,
    engine=None,
) -> None:
    """保存一次回测运行的元信息。"""
    ses = get_session(engine or get_engine())
    try:
        ses.add(
            BacktestRun(
                run_id=run_id,
                strategy_name=strategy.strategy_name,
                strategy_version=strategy.strategy_version,
                start_date=start_date,
                end_date=end_date,
                initial_capital=initial_capital,
                backtest_config=to_json(backtest_config or {}),
                stock_count=stock_count,
            )
        )
        ses.commit()
    except Exception:
        ses.rollback()
        raise
    finally:
        ses.close()


def save_result(run_id: str, performance: dict, engine=None) -> None:
    """保存总体绩效结果 (一次 run 一条)。"""
    ses = get_session(engine or get_engine())
    try:
        ses.add(
            BacktestResultRecord(
                run_id=run_id,
                total_return=performance.get("total_return"),
                annual_return=performance.get("annual_return"),
                max_drawdown=performance.get("max_drawdown"),
                sharpe_ratio=performance.get("sharpe_ratio"),
                win_rate=performance.get("win_rate"),
                profit_factor=performance.get("profit_factor"),
                trade_count=performance.get("trade_count"),
                avg_trade_return=performance.get("avg_trade_return"),
                avg_holding_days=performance.get("avg_holding_days"),
                max_single_trade_return=performance.get("max_single_trade_return"),
                max_single_trade_loss=performance.get("max_single_trade_loss"),
            )
        )
        ses.commit()
    except Exception:
        ses.rollback()
        raise
    finally:
        ses.close()


def save_trades(run_id: str, trades: list[dict], engine=None) -> None:
    """保存每笔交易明细。"""
    if not trades:
        return
    ses = get_session(engine or get_engine())
    try:
        for t in trades:
            ses.add(
                BacktestTrade(
                    run_id=run_id,
                    stock_code=t["stock_code"],
                    entry_date=t.get("entry_date"),
                    entry_price=t.get("entry_price"),
                    exit_date=t.get("exit_date"),
                    exit_price=t.get("exit_price"),
                    holding_days=t.get("holding_days"),
                    return_pct=t.get("return_pct"),
                    exit_reason=t.get("exit_reason"),
                )
            )
        ses.commit()
    except Exception:
        ses.rollback()
        raise
    finally:
        ses.close()


def _trade_row_to_dict(row: BacktestTrade) -> dict:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "stock_code": row.stock_code,
        "entry_date": row.entry_date,
        "entry_price": row.entry_price,
        "exit_date": row.exit_date,
        "exit_price": row.exit_price,
        "holding_days": row.holding_days,
        "return_pct": row.return_pct,
        "exit_reason": row.exit_reason,
    }


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------


def query_stock_trades(
    engine=None,
    run_id: str | None = None,
    stock_code: str | None = None,
) -> list[dict]:
    """查询交易明细，可按 run_id 或 stock_code 过滤，按入场日期排序。"""
    ses = get_session(engine or get_engine())
    try:
        stmt = select(BacktestTrade).order_by(BacktestTrade.entry_date)
        if run_id:
            stmt = stmt.where(BacktestTrade.run_id == run_id)
        if stock_code:
            stmt = stmt.where(BacktestTrade.stock_code == stock_code)
        rows = ses.execute(stmt).scalars().all()
        return [_trade_row_to_dict(r) for r in rows]
    finally:
        ses.close()


def query_run_summary(engine=None, run_id: str | None = None) -> list[dict]:
    """联查 backtest_run + backtest_result，返回每次回测汇总。"""
    ses = get_session(engine or get_engine())
    try:
        runs = ses.execute(
            select(BacktestRun).order_by(BacktestRun.created_at.desc())
        ).scalars().all()
        if run_id:
            runs = [r for r in runs if r.run_id == run_id]
        out = []
        for r in runs:
            res = ses.execute(
                select(BacktestResultRecord).where(
                    BacktestResultRecord.run_id == r.run_id
                )
            ).scalar_one_or_none()
            out.append(
                {
                    "run_id": r.run_id,
                    "strategy_name": r.strategy_name,
                    "strategy_version": r.strategy_version,
                    "start_date": r.start_date,
                    "end_date": r.end_date,
                    "initial_capital": r.initial_capital,
                    "backtest_config": r.backtest_config,
                    "stock_count": r.stock_count,
                    "created_at": r.created_at,
                    **(
                        {
                            "total_return": res.total_return,
                            "annual_return": res.annual_return,
                            "max_drawdown": res.max_drawdown,
                            "sharpe_ratio": res.sharpe_ratio,
                            "win_rate": res.win_rate,
                            "profit_factor": res.profit_factor,
                            "trade_count": res.trade_count,
                            "avg_trade_return": res.avg_trade_return,
                            "avg_holding_days": res.avg_holding_days,
                            "max_single_trade_return": res.max_single_trade_return,
                            "max_single_trade_loss": res.max_single_trade_loss,
                        }
                        if res is not None
                        else {}
                    ),
                }
            )
        return out
    finally:
        ses.close()