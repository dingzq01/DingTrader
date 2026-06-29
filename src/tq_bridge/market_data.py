import math
import time
from typing import Any

import pandas as pd

from src.config.settings import get_settings
from src.tq_bridge.client import TQClient
from src.utils.logging import get_logger

logger = get_logger(__name__)


class MarketDataAPI:
    """TQ 行情数据获取封装（历史K线 + 实时快照）。"""

    def __init__(self, client: TQClient):
        self._client = client
        self._settings = get_settings()

    def get_kline(self, stock_code: str, period: str = "1d", count: int = 250,
                  dividend_type: str = "none") -> pd.DataFrame | None:
        """获取单只股票历史K线数据。

        Returns DataFrame with columns: date, open, high, low, close, volume.
        """
        try:
            data = self._client.tq.get_market_data(
                stock_list=[stock_code],
                period=period,
                count=count,
                dividend_type=dividend_type,
                fill_data=True,
            )
            df = pd.DataFrame({
                "date": data["Open"].iloc[:, 0].index,
                "open": data["Open"].iloc[:, 0].values,
                "high": data["High"].iloc[:, 0].values,
                "low": data["Low"].iloc[:, 0].values,
                "close": data["Close"].iloc[:, 0].values,
                "volume": data["Volume"].iloc[:, 0].values,
            })
            if df.empty:
                return None
            return df.sort_values("date").reset_index(drop=True)
        except Exception as e:
            logger.error("get_kline_failed", stock_code=stock_code, error=str(e))
            return None

    def get_snapshot(self, stock_code: str) -> dict[str, Any] | None:
        """获取单只股票实时行情快照。"""
        try:
            snapshot = self._client.tq.get_market_snapshot(stock_code=stock_code)
            if snapshot and snapshot.get("ErrorId") == "0":
                return {
                    "open": float(snapshot["Open"]),
                    "last_price": float(snapshot["Now"]),
                    "low": float(snapshot["Min"]),
                    "high": float(snapshot["Max"]),
                    "volume": int(snapshot["Volume"]),
                }
        except Exception as e:
            logger.error("get_snapshot_failed", stock_code=stock_code, error=str(e))
        return None

    def batch_get_snapshots(self, stocks: list[str]) -> dict[str, dict]:
        """批量获取实时行情快照，带限流控制。"""
        all_data: dict[str, dict] = {}
        if not stocks:
            return all_data

        batch_size = self._settings.sync.batch_size
        batch_num = math.ceil(len(stocks) / batch_size)

        for i in range(batch_num):
            start = i * batch_size
            end = min((i + 1) * batch_size, len(stocks))
            batch = stocks[start:end]

            for code in batch:
                snapshot = self.get_snapshot(code)
                if snapshot:
                    all_data[code] = snapshot

            if i < batch_num - 1:
                time.sleep(self._settings.sync.request_interval_seconds)

            logger.debug("snapshot_batch_complete", batch=i + 1, total=batch_num)

        return all_data