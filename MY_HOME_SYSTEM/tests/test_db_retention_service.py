# MY_HOME_SYSTEM/tests/test_db_retention_service.py
"""services/db_retention_service.py の回帰テスト (Issue #733 / AUDIT-003)。

行削除は**不可逆**なので、このファイルが固定するのは主に「消えないこと」である:

- 既定(`DB_ROW_RETENTION_ENABLED=false`)では1行も消えない
- 直近のバックアップが確認できなければ消えない
- 保持期間の内側の行は消えない
- 登録していないテーブル(年代記の原資である quest_history 等)は消えない

あわせて、削除が索引を使うこと(`EXPLAIN QUERY PLAN`)も検証する。先頭列が
device_id の既存索引しか無い状態で DELETE すると毎晩テーブル全体をスキャンしながら
書き込みロックを保持することになり、#733 が問題の連鎖として挙げた
「長い書き込みロック → Webhook の保存が database is locked → センサーイベントの
恒久的な喪失」を、対策のつもりで自分で踏むことになる。
"""
import os
import sqlite3
import sys
from datetime import datetime, timedelta

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from core.database import get_db_cursor
from services import db_retention_service as retention

NOW = datetime.fromisoformat("2026-09-19T12:00:00+09:00")


def _ts(days_ago: int) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture
def retention_db(isolated_db, monkeypatch):
    """保持期間の境界をまたぐ行を各対象テーブルへ仕込む。

    古い行(500日前) / 新しい行(10日前) を1行ずつ。既定の保持日数は400日なので、
    古い行だけが削除対象になる。
    """
    monkeypatch.setattr(config, "DB_ROW_RETENTION_SENSOR_DAYS", 400, raising=False)
    monkeypatch.setattr(config, "DB_ROW_RETENTION_EVENT_DAYS", 400, raising=False)
    monkeypatch.setattr(config, "DB_ROW_RETENTION_ENABLED", False, raising=False)

    with get_db_cursor(commit=True) as cur:
        # Issue #747 ステップ3: routine_step_events.user_id / quest_history.user_id に
        # quest_users への外部キーが付いたため、参照先のユーザーを先に作る
        # (本番では必ず存在するユーザーであり、テストの前提を実態へ寄せたもの)。
        cur.execute(
            "INSERT INTO quest_users (user_id, name, job_class, level, exp, gold, role) "
            "VALUES ('u1', 'U1', 'Novice', 1, 0, 0, 'role_child')"
        )
        for old_new in (_ts(500), _ts(10)):
            cur.execute(
                "INSERT INTO device_records (timestamp, device_name) VALUES (?, ?)",
                (old_new, "テスト機器"),
            )
            cur.execute(
                "INSERT INTO power_usage (timestamp, device_id, wattage) VALUES (?, ?, ?)",
                (old_new, "dev-1", 100.0),
            )
            cur.execute(
                "INSERT INTO switchbot_meter_logs (timestamp, device_id, temperature) "
                "VALUES (?, ?, ?)",
                (old_new, "dev-1", 25.0),
            )
            cur.execute(
                "INSERT INTO nas_records (timestamp, status_ping) VALUES (?, ?)",
                (old_new, "OK"),
            )
            cur.execute(
                "INSERT INTO routine_step_events "
                "(user_id, flow_key, progress_date, step_key, to_status, source, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("u1", "morning", old_new[:10], "wake", "done", "test", old_new),
            )
            # 年代記の原資。削除対象に**入っていない**ことの対照として入れる。
            cur.execute(
                "INSERT INTO quest_history (user_id, quest_id, completed_at, status) "
                "VALUES (?, ?, ?, ?)",
                ("u1", 1, old_new, "approved"),
            )
    return isolated_db


def _count(table: str) -> int:
    with get_db_cursor() as cur:
        return cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class TestPlanDeletions:
    def test_plan_counts_only_rows_older_than_retention(self, retention_db):
        plans = {p.target.table: p for p in retention.plan_deletions(now=NOW)}

        assert plans["device_records"].deletable_rows == 1
        assert plans["device_records"].total_rows == 2
        assert plans["routine_step_events"].deletable_rows == 1

    def test_plan_does_not_delete_anything(self, retention_db):
        retention.plan_deletions(now=NOW)
        assert _count("device_records") == 2

    def test_plan_reports_oldest_and_newest(self, retention_db):
        plans = {p.target.table: p for p in retention.plan_deletions(now=NOW)}
        plan = plans["power_usage"]
        assert plan.oldest == _ts(500)
        assert plan.newest == _ts(10)

    def test_retention_days_are_read_at_call_time(self, retention_db, monkeypatch):
        """保持日数を config の属性名で保持しているので、実行時の値が効くこと。"""
        monkeypatch.setattr(config, "DB_ROW_RETENTION_SENSOR_DAYS", 5, raising=False)
        plans = {p.target.table: p for p in retention.plan_deletions(now=NOW)}
        # 5日保持なら 10日前の行も対象になる
        assert plans["device_records"].deletable_rows == 2


class TestDryRunIsTheDefault:
    def test_disabled_config_deletes_nothing(self, retention_db):
        outcome = retention.run_db_retention(now=NOW)

        assert outcome.dry_run is True
        assert outcome.planned_rows == 5  # 対象5テーブル × 古い行1件
        assert outcome.deleted_rows == 0
        assert _count("device_records") == 2
        assert _count("power_usage") == 2

    def test_dry_run_summary_says_it_is_a_dry_run(self, retention_db):
        outcome = retention.run_db_retention(now=NOW)
        lines = retention.format_summary_lines(outcome)
        assert any("ドライラン" in line for line in lines), lines


class TestBackupIsRequiredBeforeDeleting:
    def test_enabled_but_no_backup_skips_deletion(self, retention_db, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "DB_ROW_RETENTION_ENABLED", True, raising=False)
        monkeypatch.setattr(config, "DB_BACKUPS_DIR", str(tmp_path / "empty"), raising=False)

        outcome = retention.run_db_retention(now=NOW)

        assert outcome.skipped_reason is not None
        assert outcome.deleted_rows == 0
        assert _count("device_records") == 2

    def test_stale_backup_is_rejected(self, monkeypatch, tmp_path):
        backups = tmp_path / "db_backups"
        backups.mkdir()
        old_backup = backups / "home_system_20240101_040000.db"
        old_backup.write_bytes(b"x")
        os.utime(old_backup, (0, 0))  # 1970年
        monkeypatch.setattr(config, "DB_BACKUPS_DIR", str(backups), raising=False)

        ok, detail = retention.recent_backup(within_hours=26)
        assert ok is False
        # 「バックアップが無い」ではなく「古い」と区別して報告すること
        assert "home_system_20240101_040000.db" in detail
        assert "時間前" in detail

    def test_fresh_backup_is_accepted(self, monkeypatch, tmp_path):
        backups = tmp_path / "db_backups"
        backups.mkdir()
        (backups / "home_system_20260919_040000.db").write_bytes(b"x")
        monkeypatch.setattr(config, "DB_BACKUPS_DIR", str(backups), raising=False)

        ok, detail = retention.recent_backup(within_hours=26)
        assert ok is True
        assert detail == "home_system_20260919_040000.db"

    def test_non_backup_files_are_ignored(self, monkeypatch, tmp_path):
        """db_backups/ には設定ファイルのコピー(.env 等)も置かれるため、
        DB のバックアップだけを成功の証拠として見ること。"""
        backups = tmp_path / "db_backups"
        backups.mkdir()
        (backups / "config_20260919_040000.py").write_bytes(b"x")
        monkeypatch.setattr(config, "DB_BACKUPS_DIR", str(backups), raising=False)

        ok, _ = retention.recent_backup(within_hours=26)
        assert ok is False


class TestApplyDeletions:
    def _enable(self, monkeypatch, tmp_path):
        backups = tmp_path / "db_backups"
        backups.mkdir(exist_ok=True)
        (backups / "home_system_20260919_040000.db").write_bytes(b"x")
        monkeypatch.setattr(config, "DB_ROW_RETENTION_ENABLED", True, raising=False)
        monkeypatch.setattr(config, "DB_BACKUPS_DIR", str(backups), raising=False)

    def test_only_rows_older_than_retention_are_deleted(self, retention_db, monkeypatch, tmp_path):
        self._enable(monkeypatch, tmp_path)

        outcome = retention.run_db_retention(now=NOW)

        assert outcome.dry_run is False
        assert outcome.deleted_rows == 5
        for table in ("device_records", "power_usage", "switchbot_meter_logs",
                      "nas_records", "routine_step_events"):
            assert _count(table) == 1, table

    def test_remaining_row_is_the_recent_one(self, retention_db, monkeypatch, tmp_path):
        self._enable(monkeypatch, tmp_path)
        retention.run_db_retention(now=NOW)

        with get_db_cursor() as cur:
            remaining = cur.execute("SELECT timestamp FROM device_records").fetchall()
        assert [r[0] for r in remaining] == [_ts(10)]

    def test_unregistered_tables_are_never_touched(self, retention_db, monkeypatch, tmp_path):
        """quest_history は家族の年代記の原資なので、対象に入っていないこと。"""
        self._enable(monkeypatch, tmp_path)
        retention.run_db_retention(now=NOW)

        assert _count("quest_history") == 2
        assert "quest_history" not in {t.table for t in retention.RETENTION_TARGETS}
        assert "reward_history" not in {t.table for t in retention.RETENTION_TARGETS}
        assert "daily_logs" not in {t.table for t in retention.RETENTION_TARGETS}

    def test_max_rows_per_run_caps_the_deletion_and_reports_it(self, retention_db, monkeypatch, tmp_path):
        self._enable(monkeypatch, tmp_path)
        plans = retention.plan_deletions(now=NOW)

        results = retention.apply_deletions(plans, batch_size=1, max_rows_per_run=2)

        assert sum(r.deleted_rows for r in results) == 2
        assert any(r.capped for r in results)

    def test_batching_uses_multiple_transactions(self, retention_db, monkeypatch, tmp_path):
        """1トランザクションで全部消さないこと(書き込みロックの保持時間を抑える)。"""
        self._enable(monkeypatch, tmp_path)
        with get_db_cursor(commit=True) as cur:
            for i in range(10):
                cur.execute(
                    "INSERT INTO device_records (timestamp, device_name) VALUES (?, ?)",
                    (_ts(500 + i), "bulk"),
                )

        calls = {"n": 0}
        original = retention.get_db_cursor

        def counting_cursor(*args, **kwargs):
            calls["n"] += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(retention, "get_db_cursor", counting_cursor)
        plans = [p for p in retention.plan_deletions(now=NOW) if p.target.table == "device_records"]
        retention.apply_deletions(plans, batch_size=2, max_rows_per_run=100)

        assert _count("device_records") == 1
        # 11行を2行ずつ → 6トランザクション以上
        assert calls["n"] >= 6, calls


class TestDeletionUsesAnIndex:
    """削除の WHERE 句が索引を使うこと(migrations/0014)。

    ベースラインの索引は (device_id, timestamp) 等で先頭列が device_id のため、
    保持期間削除の「デバイスを問わず古い行」という条件には使えない。
    """

    @pytest.mark.parametrize("target", retention.RETENTION_TARGETS, ids=lambda t: t.table)
    def test_delete_scan_uses_index(self, isolated_db, target):
        with get_db_cursor() as cur:
            plan = cur.execute(
                f"EXPLAIN QUERY PLAN SELECT rowid FROM {target.table} "
                f"WHERE {target.timestamp_column} < ? ORDER BY {target.timestamp_column} LIMIT 10",
                ("2020-01-01 00:00:00",),
            ).fetchall()
        detail = " ".join(str(row[-1]) for row in plan)
        assert "USING INDEX" in detail or "USING COVERING INDEX" in detail, (
            f"{target.table}: 索引が使われていない ({detail})。"
            f"migrations/ に {target.timestamp_column} の索引を追加してください。"
        )


class TestBuildReport:
    def test_report_includes_unregistered_tables(self, retention_db):
        """対象に入れていないテーブルの行数も出すこと。
        「どれを対象に追加すべきか」を実測で決めるための材料になる(#733 の保留理由)。"""
        report = retention.build_report(now=NOW)

        by_name = {t["table"]: t for t in report["tables"]}
        assert by_name["device_records"]["managed"] is True
        assert by_name["quest_history"]["managed"] is False
        assert by_name["quest_history"]["rows"] == 2

    def test_report_does_not_delete_anything(self, retention_db):
        retention.build_report(now=NOW)
        assert _count("device_records") == 2

    def test_reclaimable_bytes_is_non_negative(self, retention_db):
        assert retention.reclaimable_bytes() >= 0


class TestCutoffFormatMatchesStoredValues:
    def test_cutoff_is_comparable_as_text(self):
        """SQLite の比較は辞書順なので、保存側と同じ "%Y-%m-%d %H:%M:%S" である必要がある。"""
        cutoff = retention._cutoff_string(400, now=NOW)
        assert cutoff == (NOW - timedelta(days=400)).strftime("%Y-%m-%d %H:%M:%S")
        assert _ts(500) < cutoff < _ts(10)


class TestMissingTableIsTolerated:
    def test_plan_skips_tables_that_do_not_exist(self, isolated_db, monkeypatch):
        """マイグレーション未適用の古いDBでも落ちないこと。"""
        bogus = retention.RetentionTarget(
            "not_a_real_table", "timestamp", "DB_ROW_RETENTION_SENSOR_DAYS", "架空"
        )
        monkeypatch.setattr(retention, "RETENTION_TARGETS", (bogus,), raising=False)
        assert retention.plan_deletions(now=NOW) == []


class TestNasMonitorIntegration:
    def test_file_cleanup_still_notifies_when_db_retention_fails(self, monkeypatch):
        """DB 側が失敗してもファイル削除の集計通知は出ること。"""
        from monitors import nas_monitor

        monitor = nas_monitor.NasMonitor.__new__(nas_monitor.NasMonitor)
        monkeypatch.setattr(
            nas_monitor.db_retention_service, "run_db_retention",
            lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")),
        )
        assert monitor._run_db_row_retention() == []

    def test_summary_lines_are_appended_to_the_file_cleanup_notification(self, monkeypatch):
        from monitors import nas_monitor

        monitor = nas_monitor.NasMonitor.__new__(nas_monitor.NasMonitor)
        outcome = retention.RetentionOutcome(dry_run=True, plans=[])
        monkeypatch.setattr(
            nas_monitor.db_retention_service, "run_db_retention", lambda *a, **k: outcome
        )
        monkeypatch.setattr(
            nas_monitor.db_retention_service, "format_summary_lines", lambda o: ["- テスト行"]
        )
        assert monitor._run_db_row_retention() == ["- テスト行"]
