# MY_HOME_SYSTEM/tests/test_sync_strict.py
"""
sync_strict.py のテスト。

M-9-6: このスクリプトは quest_data.py(マスタ)に無い行を quest_master/reward_master
から**無確認でDELETE**しており、QUESTS/REWARDSが空リストになった場合は全件削除される。
quest_data.py のID変更ミス一発で本番マスタが消えるリスクがあるため、実行前に
(1) 空マスタでの全件削除を拒否する安全ガード、(2) 対話的な確認プロンプト、
(3) 何も変更しないdry-runモード、を追加した。
"""
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import common
import sync_strict


def _seed_quest_master_row(quest_id: int = 9999, title: str = "Stale Quest"):
    with common.get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain) VALUES (?, ?, ?, ?, ?)",
            (quest_id, title, "daily", 10, 5),
        )


def _seed_reward_master_row(reward_id: int = 8888, title: str = "Stale Reward"):
    with common.get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO reward_master (reward_id, title, cost_gold) VALUES (?, ?, ?)",
            (reward_id, title, 100),
        )


class TestConfirmOrAbortSafetyGuard:
    """対話的な確認プロンプト・空マスタガードの単体テスト(DBアクセスなし)。"""

    def test_raises_when_quest_master_is_empty_without_flag(self):
        with pytest.raises(sync_strict.SyncAborted):
            sync_strict.confirm_or_abort(
                master_quest_ids=[], master_reward_ids=[1, 2],
                allow_empty_master=False, assume_yes=True,
            )

    def test_raises_when_reward_master_is_empty_without_flag(self):
        with pytest.raises(sync_strict.SyncAborted):
            sync_strict.confirm_or_abort(
                master_quest_ids=[1, 2], master_reward_ids=[],
                allow_empty_master=False, assume_yes=True,
            )

    def test_allows_empty_master_when_flag_is_set(self):
        # 例外を送出しないこと。
        sync_strict.confirm_or_abort(
            master_quest_ids=[], master_reward_ids=[],
            allow_empty_master=True, assume_yes=True,
        )

    def test_skips_prompt_when_assume_yes(self):
        calls = []

        def input_should_not_be_called(prompt):
            calls.append(prompt)
            return "y"

        sync_strict.confirm_or_abort(
            master_quest_ids=[1], master_reward_ids=[1],
            allow_empty_master=False, assume_yes=True,
            input_func=input_should_not_be_called,
        )
        assert calls == []

    def test_aborts_when_user_declines_prompt(self):
        with pytest.raises(sync_strict.SyncAborted):
            sync_strict.confirm_or_abort(
                master_quest_ids=[1], master_reward_ids=[1],
                allow_empty_master=False, assume_yes=False,
                input_func=lambda prompt: "n",
            )

    def test_proceeds_when_user_confirms_prompt(self):
        # 例外を送出しないこと。
        sync_strict.confirm_or_abort(
            master_quest_ids=[1], master_reward_ids=[1],
            allow_empty_master=False, assume_yes=False,
            input_func=lambda prompt: "y",
        )


class TestRunSyncDryRun:
    """dry-runモードではDBが一切変更されないことの回帰テスト。"""

    def test_dry_run_does_not_delete_or_upsert_anything(self, isolated_db, monkeypatch):
        _seed_quest_master_row()
        _seed_reward_master_row()
        monkeypatch.setattr(sync_strict, "QUESTS", [], raising=False)
        monkeypatch.setattr(sync_strict, "REWARDS", [], raising=False)

        # dry-runは確認プロンプトなしで実行できる(何も変更しないため)。
        sync_strict.run_sync(dry_run=True, assume_yes=False)

        with common.get_db_cursor() as cur:
            quest_count = cur.execute("SELECT COUNT(*) as c FROM quest_master").fetchone()["c"]
            reward_count = cur.execute("SELECT COUNT(*) as c FROM reward_master").fetchone()["c"]
        assert quest_count == 1, "dry-run should not delete the stale quest row"
        assert reward_count == 1, "dry-run should not delete the stale reward row"


class TestRunSyncDestructiveDeleteIsGated:
    """マスタに無い行のDELETEが、確認/安全ガードを経由しないと実行されないことの回帰テスト。"""

    def test_empty_master_without_flag_leaves_database_untouched(self, isolated_db, monkeypatch):
        _seed_quest_master_row()
        _seed_reward_master_row()
        monkeypatch.setattr(sync_strict, "QUESTS", [], raising=False)
        monkeypatch.setattr(sync_strict, "REWARDS", [], raising=False)

        with pytest.raises(sync_strict.SyncAborted):
            sync_strict.run_sync(dry_run=False, assume_yes=True, allow_empty_master=False)

        with common.get_db_cursor() as cur:
            quest_count = cur.execute("SELECT COUNT(*) as c FROM quest_master").fetchone()["c"]
            reward_count = cur.execute("SELECT COUNT(*) as c FROM reward_master").fetchone()["c"]
        assert quest_count == 1, "aborted sync must not delete anything"
        assert reward_count == 1, "aborted sync must not delete anything"

    def test_declined_confirmation_leaves_database_untouched(self, isolated_db, monkeypatch):
        _seed_quest_master_row()
        monkeypatch.setattr(
            sync_strict, "QUESTS",
            [{"id": 1, "title": "Kept Quest", "type": "daily", "target": "all", "exp": 1, "gold": 1}],
            raising=False,
        )
        monkeypatch.setattr(sync_strict, "REWARDS", [], raising=False)

        with pytest.raises(sync_strict.SyncAborted):
            sync_strict.run_sync(
                dry_run=False, assume_yes=False, allow_empty_master=True,
                input_func=lambda prompt: "n",
            )

        with common.get_db_cursor() as cur:
            quest_count = cur.execute("SELECT COUNT(*) as c FROM quest_master WHERE quest_id = 9999").fetchone()["c"]
        assert quest_count == 1, "declining the confirmation prompt must not delete the stale row"

    def test_confirmed_sync_deletes_stale_rows_and_upserts_master(self, isolated_db, monkeypatch):
        _seed_quest_master_row(quest_id=9999, title="Stale Quest")
        monkeypatch.setattr(
            sync_strict, "QUESTS",
            [{"id": 1, "title": "Kept Quest", "type": "daily", "target": "all", "exp": 1, "gold": 1}],
            raising=False,
        )
        monkeypatch.setattr(sync_strict, "REWARDS", [], raising=False)

        sync_strict.run_sync(dry_run=False, assume_yes=True, allow_empty_master=True)

        with common.get_db_cursor() as cur:
            stale = cur.execute("SELECT COUNT(*) as c FROM quest_master WHERE quest_id = 9999").fetchone()["c"]
            kept = cur.execute("SELECT title FROM quest_master WHERE quest_id = 1").fetchone()
        assert stale == 0, "confirmed sync should still delete rows not present in the master data"
        assert kept["title"] == "Kept Quest"
