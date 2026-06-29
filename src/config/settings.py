from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseConfig(BaseModel):
    host: str = "localhost"
    port: int = 5432
    dbname: str = "dingtrader"
    user: str = "postgres"
    password: str = ""

    @property
    def dsn(self) -> str:
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.dbname}"


class TdxConfig(BaseModel):
    install_path: str = r"C:\1new_tdx64"

    @property
    def pyplugins_path(self) -> str:
        import os
        return os.path.join(self.install_path, "PYPlugins", "user")


class FeishuConfig(BaseModel):
    webhook_url: str = ""
    keyword: str = "Dbacktrader"


class BacktestConfig(BaseModel):
    default_cash: float = 100_000
    commission_rate: float = 0.00025
    stamp_tax_rate: float = 0.001
    slippage: float = 0.01


class SyncConfig(BaseModel):
    lookback_years: int = 3
    batch_size: int = 10
    request_interval_seconds: float = 1.0
    max_retries: int = 3


class StrategiesConfig(BaseModel):
    enabled: list[str] = ["limit_up_consolidation", "macd_golden_cross"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DT_", env_nested_delimiter="__")

    database: DatabaseConfig = DatabaseConfig()
    tdx: TdxConfig = TdxConfig()
    feishu: FeishuConfig = FeishuConfig()
    backtest: BacktestConfig = BacktestConfig()
    sync: SyncConfig = SyncConfig()
    strategies: StrategiesConfig = StrategiesConfig()

    @classmethod
    def from_yaml(cls, path: Optional[Path] = None) -> "Settings":
        if path is None:
            path = Path(__file__).resolve().parent.parent.parent / "config.yaml"
        settings = cls()
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data:
                settings = cls(**data)
        return settings


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_yaml()
    return _settings