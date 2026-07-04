#!/usr/bin/env python
"""Initialize database: create tables, hypertable, and materialized views.

用法:
    python scripts/init_db.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from src.data.models import init_db, get_engine
from src.utils.logging import setup_logging, get_logger

logger = get_logger(__name__)


def _run_sql_file(engine, filepath: str) -> None:
    """Execute a .sql file against the database."""
    with open(filepath, "r", encoding="utf-8") as f:
        sql = f.read()

    with engine.connect() as conn:
        for statement in sql.split(";"):
            stmt = statement.strip()
            if stmt and not stmt.startswith("--"):
                try:
                    conn.execute(text(stmt))
                except Exception as e:
                    logger.warning("sql_statement_failed", error=str(e)[:200])
        conn.commit()


def main():
    setup_logging()

    logger.info("init_db_started")

    # 1. Create tables + hypertable
    engine = get_engine()
    init_db(engine)
    logger.info("tables_and_hypertable_created")

    # 2. Create materialized views from continuous_aggregates.sql
    sql_path = (
        Path(__file__).resolve().parent.parent
        / "src" / "data" / "continuous_aggregates.sql"
    )
    _run_sql_file(engine, str(sql_path))
    logger.info("materialized_views_created")

    # 3. Refresh materialized views (initial population)
    with engine.connect() as conn:
        conn.execute(text("REFRESH MATERIALIZED VIEW sector_daily_stats"))
        conn.commit()
    logger.info("materialized_views_refreshed")

    logger.info("init_db_completed")


if __name__ == "__main__":
    main()