# MY_HOME_SYSTEM/tests/test_routine_deadline_job.py
"""monitors/routine_deadline_job.py のテスト(Issue #738 / AUDIT-008)。

このスクリプトは「ルーティンの締切処理」をスケジューラの定期タスクとして起動する
薄いHTTPクライアントであり、次の2点が本質的な要件になる。

1. サーバーの `POST /api/routine/deadlines/process` を叩くだけで、**DBを直接書かない**
   (スケジューラは unified_server とは別プロセスのため、`quest_users` を直接書くと
   `services/quest/locks.py` の threading.Lock を共有できずロストアップデートが起きる。
   CLAUDE.md「並行制御は単一プロセス前提」/ Issue #755・#760)
2. 60秒ごとに走るため、サーバー側の一時的な接続断で毎分「タスク失敗」を鳴らさない
"""
import ast
import os
import sys

import pytest
import requests

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
import scheduler_boot
from monitors import routine_deadline_job

JOB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "monitors", "routine_deadline_job.py")


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


@pytest.fixture
def captured_post(monkeypatch):
    """requests.post を差し替えて呼び出し内容を記録する。"""
    calls = []

    def _post(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeResponse(payload={"date": "2024-01-01", "processed_users": 4,
                                      "failed_users": 0, "transitions": 0})

    monkeypatch.setattr(routine_deadline_job.requests, "post", _post)
    return calls


class TestRunOnce:
    def test_posts_to_the_configured_endpoint(self, captured_post):
        assert routine_deadline_job.run_once() == 0
        assert len(captured_post) == 1
        url, kwargs = captured_post[0]
        assert url == f"{config.ROUTINE_DEADLINE_API_BASE_URL}/api/routine/deadlines/process"
        assert kwargs["timeout"] == config.ROUTINE_DEADLINE_API_TIMEOUT_SEC

    def test_base_url_is_read_at_call_time(self, monkeypatch, captured_post):
        """.env での上書き(monkeypatch)が反映されること。"""
        monkeypatch.setattr(config, "ROUTINE_DEADLINE_API_BASE_URL", "http://127.0.0.1:9999")
        routine_deadline_job.run_once()
        assert captured_post[0][0].startswith("http://127.0.0.1:9999")

    def test_connection_error_is_a_warning_not_a_task_failure(self, monkeypatch):
        """サーバー未起動・再起動中の接続断では終了コード0(タスク失敗にしない)。

        60秒ごとに走るため、ここを失敗扱いにするとデプロイのたびに偽の
        Discord 通知が出る。サーバーの死活は server_watchdog / health_watch が見る。
        """
        def _boom(*args, **kwargs):
            raise requests.exceptions.ConnectionError("connection refused")

        monkeypatch.setattr(routine_deadline_job.requests, "post", _boom)
        assert routine_deadline_job.run_once() == 0

    def test_http_error_is_a_task_failure(self, monkeypatch):
        """サーバーが応答したうえでのエラー(= サーバー側の不具合)は失敗として通知する。"""
        monkeypatch.setattr(
            routine_deadline_job.requests, "post",
            lambda *a, **k: _FakeResponse(status_code=500, text="Internal Server Error"),
        )
        assert routine_deadline_job.run_once() == 1

    def test_non_json_response_is_a_task_failure(self, monkeypatch):
        monkeypatch.setattr(
            routine_deadline_job.requests, "post",
            lambda *a, **k: _FakeResponse(status_code=200, payload=None, text="<html>"),
        )
        assert routine_deadline_job.run_once() == 1

    def test_main_catches_unexpected_exceptions(self, monkeypatch):
        """予期しない例外でもスタックトレースを残して終了コードで知らせる
        (スケジューラ本体を巻き込まない)。"""
        def _boom():
            raise RuntimeError("unexpected")

        monkeypatch.setattr(routine_deadline_job, "run_once", _boom)
        assert routine_deadline_job.main() == 1


class TestSchedulerRegistration:
    def test_job_is_registered_with_a_60_second_interval(self):
        """締切は分単位(routine_data.py の checkpoint_time)なので60秒間隔で十分かつ必要。"""
        task = next(
            (t for t in scheduler_boot.TASKS if t["script"] == "monitors/routine_deadline_job.py"),
            None,
        )
        assert task is not None, "scheduler_boot.TASKS にルーティン締切処理が登録されていません"
        assert task["interval"] == 60
        assert task["args"] == []


class TestDoesNotWriteTheDatabaseDirectly:
    """別プロセスから `quest_users` を直接書かないこと(Issue #755 / #760 の前提)。

    `tests/test_single_process_invariant.py` と同じ発想で、コメントではなく
    import の形を機械的に固定する。締切処理は必ずサーバーのAPI経由で行う。
    """

    def _imported_modules(self) -> set[str]:
        with open(JOB_PATH, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=JOB_PATH)
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        return modules

    def test_does_not_import_db_or_service_layers(self):
        forbidden = {"sqlite3", "core.database", "services.routine_service", "init_unified_db"}
        offenders = sorted(self._imported_modules() & forbidden)
        assert not offenders, (
            "monitors/routine_deadline_job.py はスケジューラ(別プロセス)で動くため、"
            "DB・サービス層を直接使ってはなりません(HTTP API 経由にすること): " + ", ".join(offenders)
        )
