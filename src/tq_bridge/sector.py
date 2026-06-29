from src.tq_bridge.client import TQClient
from src.utils.logging import get_logger

logger = get_logger(__name__)


class SectorManager:
    """通达信板块操作封装：创建/清空/写入/获取自定义板块及股票列表。"""

    def __init__(self, client: TQClient):
        self._client = client

    def get_user_sectors(self) -> list[dict]:
        """获取所有自定义板块列表。"""
        return self._client.tq.get_user_sector()

    def get_stocks_in_sector(self, sector_code: str, block_type: int = 1) -> list[str]:
        """获取指定板块下的所有股票代码。"""
        return self._client.tq.get_stock_list_in_sector(sector_code, block_type=block_type)

    def create_sector(self, block_code: str, block_name: str):
        """创建自定义板块（已存在则忽略）。"""
        self._client.tq.create_sector(block_code=block_code, block_name=block_name)
        logger.info("sector_created", block_code=block_code, block_name=block_name)

    def clear_sector(self, block_code: str):
        """清空自定义板块。"""
        self._client.tq.clear_sector(block_code=block_code)

    def send_stocks_to_block(self, block_code: str, stocks: list[str]):
        """将股票列表写入自定义板块（覆盖式写入）。"""
        if stocks:
            self._client.tq.send_user_block(block_code=block_code, stocks=stocks)
            logger.info("stocks_sent_to_block", count=len(stocks), block_code=block_code)

    def put_stocks(self, block_code: str, block_name: str, stocks: list[str]):
        """一站式操作：创建板块 → 清空 → 写入股票。"""
        self.create_sector(block_code, block_name)
        self.clear_sector(block_code)
        self.send_stocks_to_block(block_code, stocks)

    def find_sector_code(self, sector_name: str) -> str | None:
        """根据名称查找自定义板块代码。"""
        for sector in self.get_user_sectors():
            if sector["Name"] == sector_name:
                return sector["Code"]
        return None

    def get_builtin_sectors(self) -> list[dict]:
        """获取通达信内置的概念板块和行业板块列表。"""
        return self._client.tq.get_sector_list()