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