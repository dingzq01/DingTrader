import sys
from contextlib import contextmanager

from src.config.settings import get_settings
from src.utils.logging import get_logger

logger = get_logger(__name__)


class TQClient:
    """TQ (通达信) API 生命周期管理。"""

    def __init__(self):
        settings = get_settings()
        self.tdx_path = settings.tdx.install_path
        self.pyplugins_path = settings.tdx.pyplugins_path
        self._tq = None

    def initialize(self, script_path: str = __file__):
        if self.pyplugins_path not in sys.path:
            sys.path.insert(0, self.pyplugins_path)
        from tqcenter import tq

        self._tq = tq
        tq.initialize(script_path)
        logger.info("tq_initialized", tdx_path=self.tdx_path)

    def close(self):
        if self._tq:
            self._tq.close()
            logger.info("tq_closed")

    @property
    def tq(self):
        if self._tq is None:
            raise RuntimeError("TQ not initialized. Call initialize() first.")
        return self._tq

    @contextmanager
    def session(self, script_path: str = __file__):
        self.initialize(script_path)
        try:
            yield self
        finally:
            self.close()