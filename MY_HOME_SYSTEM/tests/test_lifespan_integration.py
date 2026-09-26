# MY_HOME_SYSTEM/tests/test_lifespan_integration.py
"""
unified_server.lifespan の統合テスト (Issue #756 / AUDIT-027)。

lifespan(マイグレーション適用・NAS プリウォーム・監視子プロセス起動・死活監視・
シャットダウン処理)は失敗したときの影響が最も大きい経路だが、`api_client`
フィクスチャが意図的に lifespan を実行しない方針のため、大半のテストで
一度も通らない状態だった。`tests/conftest.py` の `lifespan_client_factory` /
`api_client_with_lifespan` を使い、起動・シャットダウン両方を検証する。

実プロセスは一切起動しない(`_spawn_child_process` を差し替えている)。
"""
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import unified_server


class TestLifespanStartup:
    """起動側: マイグレーションの成否が監視子プロセスの起動と readiness を決める。"""

    def test_starts_both_monitor_children_when_migration_succeeds(self, api_client_with_lifespan):
        client, spawned = api_client_with_lifespan
        assert sorted(p.name for p in spawned) == ["camera_monitor", "scheduler"]
        assert unified_server.app.state.migration_ok is True
        assert client.get("/health").status_code == 200

    def test_migration_failure_sets_migration_ok_false_and_skips_children(self, lifespan_client_factory):
        """#383/#756: マイグレーション失敗時は「スキーマ未適用のまま全APIが500」を
        避けるため監視子プロセスを起動しない。状態は app.state.migration_ok に残る。"""
        with lifespan_client_factory(migration_error=RuntimeError("database is locked")) as (_client, spawned):
            assert spawned == []
            assert unified_server.app.state.migration_ok is False

    def test_health_returns_503_when_migration_failed(self, lifespan_client_factory):
        """Issue #735 (AUDIT-005) と組み合わせた検証。プロセスは生きているが機能して
        いない状態を、lifespan を実際に通した状態で外形から見分けられること。"""
        with lifespan_client_factory(migration_error=RuntimeError("no such table")) as (client, _spawned):
            res = client.get("/health")
        assert res.status_code == 503
        assert res.json() == {"status": "unhealthy", "reason": "migration_failed"}

    def test_nas_prewarm_failure_does_not_abort_startup(self, lifespan_client_factory, monkeypatch):
        """config.prewarm_nas_paths() の失敗は logger.error で握られ、起動は続行される
        (AUDIT-027 の「NAS 障害時のフォールバック判定」経路)。"""
        def _boom():
            raise OSError("NAS is not mounted")

        monkeypatch.setattr(unified_server.config, "prewarm_nas_paths", _boom)
        with lifespan_client_factory() as (client, spawned):
            assert client.get("/health").status_code == 200
            assert len(spawned) == 2


class TestLifespanShutdown:
    """シャットダウン側: 子プロセスと ffmpeg / タスク / httpx クライアントの後始末。"""

    def test_child_processes_are_terminated_on_shutdown(self, lifespan_client_factory):
        with lifespan_client_factory() as (_client, spawned):
            assert len(spawned) == 2
        # #360: 孤児プロセス(HLS の二重書き込み)を防ぐため、両方に terminate が届くこと
        assert all(p.terminated for p in spawned)
        assert all(p.waited for p in spawned)
        assert not any(p.killed for p in spawned), "正常終了した子プロセスを kill してはいけない"

    def test_child_process_that_ignores_terminate_is_killed(self, lifespan_client_factory):
        """terminate 後の wait が TimeoutExpired になった場合は kill にエスカレートする。"""
        import subprocess

        with lifespan_client_factory() as (_client, spawned):
            for proc in spawned:
                def _timeout(timeout=None, _p=proc):
                    _p.waited = True
                    raise subprocess.TimeoutExpired(cmd="child", timeout=timeout or 5)

                proc.wait = _timeout
        assert all(p.terminated and p.killed for p in spawned)

    def test_cleanup_helpers_are_called_on_shutdown(self, lifespan_client_factory, monkeypatch):
        """#360 の ffmpeg 停止・モーションタスクの cancel が、いずれもシャットダウンで
        呼ばれること。#829: ダッシュボード中継(httpxクライアント)はStreamlit版廃止に
        伴い撤去したため、ここでは検証しない。"""
        calls = []

        monkeypatch.setattr(
            unified_server.camera_service, "stop_all_processes",
            lambda: calls.append("stop_all_processes") or 0,
        )
        monkeypatch.setattr(
            unified_server.sensor_service, "cancel_all_tasks",
            lambda: calls.append("cancel_all_tasks"),
        )

        with lifespan_client_factory() as (_client, _spawned):
            assert calls == []

        assert calls == ["stop_all_processes", "cancel_all_tasks"]

    def test_cleanup_helper_failure_does_not_break_shutdown(self, lifespan_client_factory, monkeypatch):
        """ffmpeg 停止の失敗はログに落として握り、残りの後始末を止めないこと
        (失敗してもモーションタスクの cancel が漏れると孤児プロセスが残る)。"""
        calls = []

        def _stop_boom():
            calls.append("stop_all_processes")
            raise RuntimeError("ffmpeg is unkillable")

        monkeypatch.setattr(unified_server.camera_service, "stop_all_processes", _stop_boom)
        monkeypatch.setattr(
            unified_server.sensor_service, "cancel_all_tasks",
            lambda: calls.append("cancel_all_tasks"),
        )

        with lifespan_client_factory() as (_client, spawned):
            pass

        assert calls == ["stop_all_processes", "cancel_all_tasks"]
        assert all(p.terminated for p in spawned)

    def test_cleanup_still_runs_when_migration_failed(self, lifespan_client_factory, monkeypatch):
        """子プロセスを起動していない(= migration 失敗)場合でも、shutdown 側の
        後始末自体は走る(未起動の子には terminate が呼ばれない)。"""
        calls = []
        monkeypatch.setattr(
            unified_server.camera_service, "stop_all_processes",
            lambda: calls.append("stop_all_processes") or 0,
        )

        with lifespan_client_factory(migration_error=RuntimeError("boom")) as (_client, spawned):
            assert spawned == []

        assert calls == ["stop_all_processes"]


class TestLifespanSupervisor:
    """起動後の死活監視ループ(Issue #646)が lifespan 内で実際に回ること。"""

    def test_dead_child_is_restarted_by_the_supervisor_loop(self, lifespan_client_factory):
        import time as _time

        with lifespan_client_factory() as (_client, spawned):
            # camera_monitor が異常終了した状態を作る
            dead = next(p for p in spawned if p.name == "camera_monitor")
            dead._returncode = 1

            deadline = _time.monotonic() + 5.0
            while _time.monotonic() < deadline:
                if len(spawned) > 2:
                    break
                # TestClient のポータルは別スレッドで動くため、こちらは待つだけでよい
                _time.sleep(0.02)

            restarted = [p for p in spawned if p.name == "camera_monitor"]
            assert len(restarted) >= 2, "監視ループが終了した子プロセスを再起動していない"
            assert unified_server.camera_process is restarted[-1]
