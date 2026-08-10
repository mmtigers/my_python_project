# MY_HOME_SYSTEM/tests/test_ai_service.py
"""
services/ai_service.py の tool_search_db テーブル許可リストのテスト。
"""
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services import ai_service


def test_extract_referenced_tables_simple_select():
    sql = "SELECT * FROM child_health_records WHERE child_name = 'A'"
    assert ai_service._extract_referenced_tables(sql) == ["child_health_records"]


def test_extract_referenced_tables_with_join():
    sql = "SELECT a.* FROM food_records a JOIN quest_users b ON a.user_id = b.user_id"
    tables = ai_service._extract_referenced_tables(sql)
    assert "food_records" in tables
    assert "quest_users" in tables


@pytest.mark.asyncio
async def test_tool_search_db_rejects_non_select():
    result = await ai_service.tool_search_db({"sql_query": "DELETE FROM quest_users"})
    assert "許可されていません" in result


@pytest.mark.asyncio
async def test_tool_search_db_blocks_disallowed_table():
    # quest_users はドキュメント記載の許可テーブル(child/food/shopping/power_usage)に含まれない
    result = await ai_service.tool_search_db({"sql_query": "SELECT * FROM quest_users"})
    assert "許可されていない" in result


@pytest.mark.asyncio
async def test_tool_search_db_allows_documented_table(monkeypatch):
    monkeypatch.setattr(ai_service.common, "execute_read_query", lambda sql, params=(): "OK")
    table = next(iter(ai_service.ALLOWED_SEARCH_TABLES))
    result = await ai_service.tool_search_db({"sql_query": f"SELECT * FROM {table}"})
    assert result == "OK"
