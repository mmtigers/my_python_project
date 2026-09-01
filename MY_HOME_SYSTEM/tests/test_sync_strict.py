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


def _seed_user_inventory_row(reward_id: int, user_id: str = "dad", status: str = "owned"):
    with common.get_db_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO user_inventory (user_id, reward_id, status, purchased_at) VALUES (?, ?, ?, ?)",
            (user_id, reward_id, status, common.get_now_iso()),
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


class TestSyncQuestsResetPeriod:
    """
    #100: sync_quests() の INSERT 列に reset_period が無く、quest_master.reset_period の
    DB列デフォルト('weekly_monday'。current_schema.sql/migrations/0002由来でALTER TABLEでは
    変更不能)がそのまま入ってしまい、is_within_reset_period() が扱えない値のため
    周期内多重完了ガードが機能しなくなる不具合(0005で一度修正済み)の回帰テスト。
    """

    def test_new_quest_gets_daily_reset_period_not_db_column_default(self, isolated_db, monkeypatch):
        monkeypatch.setattr(
            sync_strict, "QUESTS",
            [{"id": 1, "title": "New Quest", "type": "daily", "target": "all", "exp": 1, "gold": 1}],
            raising=False,
        )
        monkeypatch.setattr(sync_strict, "REWARDS", [], raising=False)

        sync_strict.run_sync(dry_run=False, assume_yes=True, allow_empty_master=True)

        with common.get_db_cursor() as cur:
            reset_period = cur.execute(
                "SELECT reset_period FROM quest_master WHERE quest_id = 1"
            ).fetchone()["reset_period"]
        assert reset_period == "daily"

    def test_reupserted_existing_quest_is_corrected_to_daily(self, isolated_db, monkeypatch):
        """既に不正値('weekly_monday')になっている既存行も、再UPSERT時に補正されること。"""
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain, reset_period) "
                "VALUES (1, 'Old Quest', 'daily', 1, 1, 'weekly_monday')"
            )

        monkeypatch.setattr(
            sync_strict, "QUESTS",
            [{"id": 1, "title": "New Quest", "type": "daily", "target": "all", "exp": 1, "gold": 1}],
            raising=False,
        )
        monkeypatch.setattr(sync_strict, "REWARDS", [], raising=False)

        sync_strict.run_sync(dry_run=False, assume_yes=True, allow_empty_master=True)

        with common.get_db_cursor() as cur:
            reset_period = cur.execute(
                "SELECT reset_period FROM quest_master WHERE quest_id = 1"
            ).fetchone()["reset_period"]
        assert reset_period == "daily"


class TestSyncQuestsFullColumnSync:
    """Issue #164の回帰テスト: sync_quests() のINSERT/UPDATE対象が10列のみで、
    start_time/end_time/start_date/end_date/occurrence_chance/pre_requisite_quest_id
    が欠落しており、services/quest_service.py の sync_master_data() (全16列)と
    同等の完全同期になっていなかった。時間帯限定クエストが sync_strict 経由だと
    終日扱いになってしまう不具合。"""

    def test_new_quest_time_window_and_period_columns_are_synced(self, isolated_db, monkeypatch):
        monkeypatch.setattr(
            sync_strict, "QUESTS",
            [{
                "id": 1, "title": "朝ミッション", "type": "daily", "target": "all",
                "exp": 1, "gold": 1,
                "start_time": "06:00", "end_time": "09:30",
                "start_date": "2026-01-01", "end_date": "2026-12-31",
                "chance": 0.5,
                "pre_requisite_quest_id": 42,
            }],
            raising=False,
        )
        monkeypatch.setattr(sync_strict, "REWARDS", [], raising=False)

        sync_strict.run_sync(dry_run=False, assume_yes=True, allow_empty_master=True)

        with common.get_db_cursor() as cur:
            row = cur.execute(
                "SELECT start_time, end_time, start_date, end_date, occurrence_chance, "
                "pre_requisite_quest_id FROM quest_master WHERE quest_id = 1"
            ).fetchone()
        assert row["start_time"] == "06:00"
        assert row["end_time"] == "09:30"
        assert row["start_date"] == "2026-01-01"
        assert row["end_date"] == "2026-12-31"
        assert row["occurrence_chance"] == 0.5
        assert row["pre_requisite_quest_id"] == 42

    def test_existing_quest_time_window_is_updated_not_left_stale(self, isolated_db, monkeypatch):
        """既存行のstart_time/end_timeが変更された場合、再UPSERT時に反映されること
        (欠落列だったため、以前は永久にDB側の古い値のまま反映されなかった)。"""
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO quest_master (quest_id, title, quest_type, exp_gain, gold_gain, "
                "start_time, end_time) VALUES (1, 'Old Quest', 'daily', 1, 1, '05:00', '06:00')"
            )

        monkeypatch.setattr(
            sync_strict, "QUESTS",
            [{
                "id": 1, "title": "New Quest", "type": "daily", "target": "all",
                "exp": 1, "gold": 1, "start_time": "06:00", "end_time": "09:30",
            }],
            raising=False,
        )
        monkeypatch.setattr(sync_strict, "REWARDS", [], raising=False)

        sync_strict.run_sync(dry_run=False, assume_yes=True, allow_empty_master=True)

        with common.get_db_cursor() as cur:
            row = cur.execute(
                "SELECT start_time, end_time FROM quest_master WHERE quest_id = 1"
            ).fetchone()
        assert row["start_time"] == "06:00"
        assert row["end_time"] == "09:30"

    def test_quest_without_time_window_keys_syncs_as_null(self, isolated_db, monkeypatch):
        """start_time/end_time等のキーを持たないクエストは、NULL(終日扱い)としてそのまま
        同期されること(occurrence_chanceのみデフォルト1.0)。"""
        monkeypatch.setattr(
            sync_strict, "QUESTS",
            [{"id": 1, "title": "終日クエスト", "type": "daily", "target": "all", "exp": 1, "gold": 1}],
            raising=False,
        )
        monkeypatch.setattr(sync_strict, "REWARDS", [], raising=False)

        sync_strict.run_sync(dry_run=False, assume_yes=True, allow_empty_master=True)

        with common.get_db_cursor() as cur:
            row = cur.execute(
                "SELECT start_time, end_time, start_date, end_date, occurrence_chance, "
                "pre_requisite_quest_id FROM quest_master WHERE quest_id = 1"
            ).fetchone()
        assert row["start_time"] is None
        assert row["end_time"] is None
        assert row["start_date"] is None
        assert row["end_date"] is None
        assert row["occurrence_chance"] == 1.0
        assert row["pre_requisite_quest_id"] is None


class TestSyncRewardsSkipsDeleteWhenReferencedByUserInventory:
    """Issue #165の回帰テスト: sync_rewards() が無条件で
    DELETE FROM reward_master WHERE reward_id NOT IN (...) を実行しており、
    PRAGMA foreign_keys=ON下でuser_inventoryにFK参照が残る報酬(所持実績あり)を
    quest_data.REWARDSから削除した状態で実行すると、DELETE自体がIntegrityErrorで
    失敗しrun_sync全体がexit 1していた。services/quest_service.pyのsync_master_data()
    側ではM-1-2として既に対策済み(参照が残っている報酬は削除をスキップ)だった。"""

    def test_referenced_reward_delete_does_not_raise_and_sync_completes(self, isolated_db, monkeypatch):
        _seed_reward_master_row(reward_id=8888, title="Stale Reward")
        _seed_user_inventory_row(reward_id=8888)

        monkeypatch.setattr(sync_strict, "QUESTS", [], raising=False)
        monkeypatch.setattr(sync_strict, "REWARDS", [], raising=False)

        # 修正前はここでsqlite3.IntegrityErrorが送出され、run_sync全体が失敗していた。
        sync_strict.run_sync(dry_run=False, assume_yes=True, allow_empty_master=True)

    def test_referenced_reward_row_is_kept_but_unreferenced_stale_reward_is_deleted(
        self, isolated_db, monkeypatch
    ):
        _seed_reward_master_row(reward_id=8888, title="Referenced Stale Reward")
        _seed_reward_master_row(reward_id=7777, title="Unreferenced Stale Reward")
        _seed_user_inventory_row(reward_id=8888)

        monkeypatch.setattr(sync_strict, "QUESTS", [], raising=False)
        monkeypatch.setattr(sync_strict, "REWARDS", [], raising=False)

        sync_strict.run_sync(dry_run=False, assume_yes=True, allow_empty_master=True)

        with common.get_db_cursor() as cur:
            referenced = cur.execute(
                "SELECT COUNT(*) as c FROM reward_master WHERE reward_id = 8888"
            ).fetchone()["c"]
            unreferenced = cur.execute(
                "SELECT COUNT(*) as c FROM reward_master WHERE reward_id = 7777"
            ).fetchone()["c"]
        assert referenced == 1, "user_inventoryに参照が残る報酬はマスタ削除をスキップすべき"
        assert unreferenced == 0, "参照の無い報酬は通常通り削除されるべき"


class TestSyncRewardsWritesDescriptionColumn:
    """Issue #165の回帰テスト: sync_rewards() がレガシー列 desc のみを書き込み、
    アプリが実際に読む description 列(InventoryService.get_user_inventoryの
    `rm.description as desc`。services/quest_service.py:848)を更新していなかった。
    sync_strict経由で登録・更新された報酬は所持済みアイテム一覧で説明が
    空表示になってしまう不具合。"""

    def test_new_reward_description_column_is_populated(self, isolated_db, monkeypatch):
        monkeypatch.setattr(
            sync_strict, "REWARDS",
            [{"id": 1, "title": "New Reward", "desc": "とても良い報酬です"}],
            raising=False,
        )
        monkeypatch.setattr(sync_strict, "QUESTS", [], raising=False)

        sync_strict.run_sync(dry_run=False, assume_yes=True, allow_empty_master=True)

        with common.get_db_cursor() as cur:
            row = cur.execute(
                "SELECT desc, description FROM reward_master WHERE reward_id = 1"
            ).fetchone()
        assert row["desc"] == "とても良い報酬です"
        assert row["description"] == "とても良い報酬です"

    def test_existing_reward_description_column_is_updated_not_left_stale(self, isolated_db, monkeypatch):
        with common.get_db_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO reward_master (reward_id, title, cost_gold, desc, description) "
                "VALUES (1, 'Old Reward', 100, '古い説明', '古い説明')"
            )

        monkeypatch.setattr(
            sync_strict, "REWARDS",
            [{"id": 1, "title": "New Reward", "desc": "新しい説明"}],
            raising=False,
        )
        monkeypatch.setattr(sync_strict, "QUESTS", [], raising=False)

        sync_strict.run_sync(dry_run=False, assume_yes=True, allow_empty_master=True)

        with common.get_db_cursor() as cur:
            row = cur.execute(
                "SELECT desc, description FROM reward_master WHERE reward_id = 1"
            ).fetchone()
        assert row["desc"] == "新しい説明"
        assert row["description"] == "新しい説明"
