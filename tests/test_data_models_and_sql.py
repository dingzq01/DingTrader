from pathlib import Path

from src.data.models import Base, StockBlockRelation, StockIndicators, BlockIndicators


def test_refactor_models_exist_in_metadata():
    model_names = {m.class_.__tablename__ for m in Base.registry.mappers}

    assert StockBlockRelation.__tablename__ in model_names
    assert StockIndicators.__tablename__ in model_names
    assert BlockIndicators.__tablename__ in model_names


def test_continuous_aggregates_uses_new_table_names_and_indexes():
    sql = Path("src/data/continuous_aggregates.sql").read_text(encoding="utf-8")

    assert "stock_block_relation" in sql
    assert "stock_indicators" in sql
    assert "block_indicators" in sql
    assert "block_daily_stats" in sql
    assert "sector_daily_stats" not in sql


def test_stock_data_name_is_not_nullable():
    assert "name = Column(String(50), nullable=False)" in Path("src/data/models.py").read_text(encoding="utf-8")


def test_no_amount_columns_in_stock_or_block_data_models():
    model_text = Path("src/data/models.py").read_text(encoding="utf-8")
    assert "class StockData" in model_text
    assert "amount" not in model_text[model_text.find("class StockData"):model_text.find("class BlockData")]
    assert "amount" not in model_text[model_text.find("class BlockData"):model_text.find("class StockIndicators")]
