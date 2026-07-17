"""旧 dashboard.block_daily_stats 清理。

block_stat_daily 已替代 block_daily_stats，本模块仅提供旧表清理 SQL。
"""

DROP_OLD_BLOCK_DAILY_SQL = """
DROP TABLE IF EXISTS dashboard.block_daily_stats CASCADE;
DROP SCHEMA IF EXISTS dashboard CASCADE;
"""