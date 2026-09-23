# MY_HOME_SYSTEM/tests/test_quest_required_and_daughter_helper_removal.py
"""
2026-09-23の要件変更の回帰テスト:
1. クエストを「毎日の必須クエスト」(required=True)と「ボーナスクエスト」
   (required=False)に分ける新フィールド(quest_master.required)の同期。
2. すずか(daughter)の「ママ・パパのおてつだい」(旧id=305)は、まだお手伝いが
   難しいため削除した。
"""
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.database import get_db_cursor
from services.quest_service import game_system


def test_real_quest_data_syncs_required_column_without_validation_errors(isolated_db):
    """quest_data.py の全クエストが MasterQuest.required を含めて検証を通過し、
    quest_master.required 列へ正しく書き込まれること。"""
    game_system.sync_master_data()

    with get_db_cursor() as cur:
        # すずかの必須クエスト(生活習慣)。
        row = cur.execute(
            "SELECT required FROM quest_master WHERE quest_id = 301"
        ).fetchone()
        assert row is not None
        assert bool(row['required']) is True

        # すずかのボーナスクエスト(なぞり書きプリント)。
        row = cur.execute(
            "SELECT required FROM quest_master WHERE quest_id = 307"
        ).fetchone()
        assert row is not None
        assert bool(row['required']) is False

        # 智矢の毎日クエスト(必須) / ボーナスクエスト(お手伝い)。
        row = cur.execute(
            "SELECT required FROM quest_master WHERE quest_id = 1021"
        ).fetchone()
        assert bool(row['required']) is True
        row = cur.execute(
            "SELECT required FROM quest_master WHERE quest_id = 48"
        ).fetchone()
        assert bool(row['required']) is False


def test_daughter_help_quest_is_removed_from_master(isolated_db):
    """旧id=305「ママ・パパのおてつだい」はマスタから削除され、二度と同期されない。"""
    game_system.sync_master_data()

    with get_db_cursor() as cur:
        row = cur.execute("SELECT 1 FROM quest_master WHERE quest_id = 305").fetchone()
    assert row is None
