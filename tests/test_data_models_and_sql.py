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


def test_amount_column_exists_in_stock_and_block_data_models():
    model_text = Path("src/data/models.py").read_text(encoding="utf-8")
    assert "class StockData" in model_text
    assert "amount = Column(Float)" in model_text[model_text.find("class StockData"):model_text.find("class BlockData")]
    assert "amount = Column(Float)" in model_text[model_text.find("class BlockData"):model_text.find("class StockIndicators")]


def test_stock_and_block_indicators_use_eav_schema():
    model_text = Path("src/data/models.py").read_text(encoding="utf-8")
    # StockIndicators: EAV — indicator_name + indicator_value, no fixed columns
    stock_section = model_text[model_text.find("class StockIndicators"):model_text.find("class BlockIndicators")]
    assert "indicator_name = Column(String(50), primary_key=True)" in stock_section
    assert "indicator_value = Column(Float)" in stock_section
    assert "macd = Column" not in stock_section
    # BlockIndicators: same EAV pattern
    block_section = model_text[model_text.find("class BlockIndicators"):model_text.find("def get_engine")]
    assert "indicator_name = Column(String(50), primary_key=True)" in block_section
    assert "indicator_value = Column(Float)" in block_section
    assert "up_count = Column" not in block_section
