import pandas as pd

from src.data import downloader
from src.data import sync_manager
from src.data.models import StockBlockRelation


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return _FakeScalars(self._rows)

    def fetchall(self):
        return self._rows


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return [r[0] for r in self._rows]


class _FakeSession:
    def __init__(self, existing=None, block_query_map=None):
        self.existing = set(existing or [])
        self.block_query_map = dict(block_query_map or {})
        self.executed = []
        self.added = []
        self.closed = False
        self.commits = 0

    def get(self, _model, _key):
        return None

    def execute(self, statement, params=None):
        self.executed.append((str(statement), dict(params or {})))
        params = params or {}

        if {"block_code", "stock_code"}.issubset(params):
            return _FakeResult(
                [(1,)] if (params["block_code"], params["stock_code"]) in self.existing else []
            )

        if {"bc", "sc"}.issubset(params):
            return _FakeResult(
                [(1,)] if (params["bc"], params["sc"]) in self.existing else []
            )

        if "block_code" in params:
            return _FakeResult(
                [(code,) for code in self.block_query_map.get(params["block_code"], [])]
            )

        return _FakeResult([])

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


def test_is_target_market_accepts_a_share_prefixes():
    assert downloader.is_target_market("000001")
    assert downloader.is_target_market("002000")
    assert downloader.is_target_market("300001")
    assert downloader.is_target_market("600000")
    assert downloader.is_target_market("688001")


def test_is_target_market_rejects_non_a_share_codes():
    assert not downloader.is_target_market("")
    assert not downloader.is_target_market("abc")
    assert not downloader.is_target_market("700000")
    assert not downloader.is_target_market("200123")
    assert not downloader.is_target_market("900001")


def test_download_stocks_batch_skips_non_target_markets_and_throttles(monkeypatch):
    called = []
    sleeps = []

    def _fake_download_stock_kline(_client, stock_code):
        called.append(stock_code)
        return 1

    def _fake_sleep(seconds):
        sleeps.append(seconds)

    class _Settings:
        class SyncConfig:
            request_interval_seconds = 1.5

        sync = SyncConfig()

    monkeypatch.setattr(downloader, "download_stock_kline", _fake_download_stock_kline)
    monkeypatch.setattr(downloader, "get_settings", lambda: _Settings())
    monkeypatch.setattr(downloader.time, "sleep", _fake_sleep)

    result = downloader.download_stocks_batch(
        None,
        ["600000", "700000", "000001", "200123", "300001", "688001", "12345"],
    )

    assert result == {
        "600000": 1,
        "000001": 1,
        "300001": 1,
        "688001": 1,
    }
    assert called == ["600000", "000001", "300001", "688001"]
    assert sleeps == [1.5, 1.5, 1.5]


def test_sync_sector_metadata_uses_stock_block_relation(monkeypatch):
    session = _FakeSession(existing={("BK_A", "000001")})
    sector_df = pd.DataFrame([
        {
            "sector_code": "BK_A",
            "sector_name": "板块A",
            "sector_type": "industry",
            "stock_code": "000001",
            "stock_name": "AAA",
        },
        {
            "sector_code": "BK_A",
            "sector_name": "板块A",
            "sector_type": "industry",
            "stock_code": "600000",
            "stock_name": "BBB",
        },
    ])

    sync_manager.sync_sector_metadata(session, sector_df)

    relations = [row for row in session.added if isinstance(row, StockBlockRelation)]
    assert len(relations) == 1
    assert relations[0].block_code == "BK_A"
    assert relations[0].stock_code == "600000"
    assert relations[0].block_name == "板块A"
    assert relations[0].block_type == "industry"
    assert any("stock_block_relation" in sql for sql, _ in session.executed)


def test_check_data_integrity_matches_relation_to_downloaded_data(monkeypatch):
    def _fake_fetch_all_sector_stocks(_client):
        return pd.DataFrame([
            {
                "sector_name": "科创",
                "sector_code": "BK_A",
                "sector_type": "industry",
                "stock_code": "000001",
                "stock_name": "AAA",
            },
            {
                "sector_name": "科创",
                "sector_code": "BK_A",
                "sector_type": "industry",
                "stock_code": "600000",
                "stock_name": "BBB",
            },
            {
                "sector_name": "科创",
                "sector_code": "BK_A",
                "sector_type": "industry",
                "stock_code": "700000",
                "stock_name": "CCC",
            },
            {
                "sector_name": "央企",
                "sector_code": "BK_B",
                "sector_type": "concept",
                "stock_code": "688001",
                "stock_name": "DDD",
            },
        ])

    session = _FakeSession(block_query_map={"BK_A": ["000001"], "BK_B": []})

    monkeypatch.setattr(sync_manager, "fetch_all_sector_stocks", _fake_fetch_all_sector_stocks)
    monkeypatch.setattr(sync_manager, "init_db", lambda: None)
    monkeypatch.setattr(sync_manager, "get_session", lambda: session)

    report = sync_manager.check_data_integrity(None)

    assert report == {
        "科创": {
            "expected": 3,
            "actual_in_db": 1,
            "missing_count": 2,
        },
        "央企": {
            "expected": 1,
            "actual_in_db": 0,
            "missing_count": 1,
        },
    }
    assert any("stock_block_relation" in sql for sql, _ in session.executed)
    assert session.closed
