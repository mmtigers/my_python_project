# MY_HOME_SYSTEM/tests/test_unified_server_app.py
"""
unified_server.py のアプリレベルのテスト。

- ルートヘルスチェック (`/`, `/health`)
- グローバル例外ハンドラが内部エラー詳細をクライアントに漏らさないこと
  (CODE_REVIEW_REPORT.md 6.1 の再発防止)
- ip_restriction_middleware の現状の挙動の記録
  (CODE_REVIEW_REPORT.md 2.2: このミドルウェアは現状、Cloudflare Access への委譲を理由に
   実質すべてのリクエストを通しており、「制限」としては機能していない。
   本テストはその既知の状態を記録するものであり、「安全である」とは主張しない)
- lifespan (起動/終了) が監視プロセスの起動・終了を正しく行うこと
  (実サブプロセスは起動せず subprocess.Popen をモックする)
"""
import logging
import os
import subprocess
import sys

from starlette.testclient import TestClient

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
import unified_server


def _make_uvicorn_access_record(method: str, path: str, status_code: int) -> logging.LogRecord:
    """uvicorn(h11_impl.py/httptools_impl.py)の実際のアクセスログフォーマット
    ('%s - "%s %s HTTP/%s" %d')を再現したLogRecordを生成する。
    ステータスコードは%位置引数の最後にあり、前方スペースのみで末尾に
    スペースは付かない点が本テストの要。"""
    return logging.LogRecord(
        name="uvicorn.access", level=logging.INFO, pathname=__file__, lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:12345", method, path, "1.1", status_code),
        exc_info=None,
    )


class TestSilencePolicyFilterMatchesRealUvicornLogFormat:
    """Issue #177の回帰テスト: SilencePolicyFilterは" 200 "/" 304 "という
    前後スペース付きの部分文字列でステータスコードを判定していたが、実際の
    uvicornアクセスログはステータスコードがメッセージ末尾にあり後方スペースが
    付かない('... HTTP/1.1" 200'のように末尾が"200"で終わる)ため、この判定は
    常にFalseとなり抑制対象キーワード判定に到達しなかった(死にコード)。"""

    def test_polling_endpoint_200_is_suppressed(self):
        record = _make_uvicorn_access_record("GET", "/api/quest/data", 200)
        filt = unified_server.SilencePolicyFilter()
        assert filt.filter(record) is False, (
            "ポーリングエンドポイントへの200応答が抑制されていない: "
            f"{record.getMessage()!r}"
        )

    def test_static_asset_304_is_suppressed(self):
        record = _make_uvicorn_access_record("GET", "/assets/app.js", 304)
        filt = unified_server.SilencePolicyFilter()
        assert filt.filter(record) is False

    def test_non_silenced_path_with_200_still_passes_through(self):
        record = _make_uvicorn_access_record("GET", "/api/quest/complete", 200)
        filt = unified_server.SilencePolicyFilter()
        assert filt.filter(record) is True

    def test_error_status_is_never_suppressed(self):
        record = _make_uvicorn_access_record("GET", "/api/quest/data", 500)
        filt = unified_server.SilencePolicyFilter()
        assert filt.filter(record) is True

    def test_post_request_is_never_suppressed_even_with_200(self):
        record = _make_uvicorn_access_record("POST", "/api/quest/data", 200)
        filt = unified_server.SilencePolicyFilter()
        assert filt.filter(record) is True


def test_cors_middleware_uses_config_cors_origins():
    """
    M-8-2回帰防止: 以前はunified_server.py側にハードコードされた別のCORS許可
    オリジンリストがあり、config.CORS_ORIGINS(ALLOW_ALL_ORIGINS環境変数の
    反映先)を変更しても実際のCORS設定には一切反映されなかった。
    CORSMiddlewareがconfig.CORS_ORIGINSをそのまま使っていることを確認する。
    """
    cors_middlewares = [
        m for m in unified_server.app.user_middleware if m.cls.__name__ == "CORSMiddleware"
    ]
    assert len(cors_middlewares) == 1
    assert cors_middlewares[0].kwargs.get("allow_origins") == config.CORS_ORIGINS


def test_root_health_check(api_client):
    res = api_client.get("/")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["system"] == "MY_HOME_SYSTEM v2"


def test_health_endpoint(api_client):
    res = api_client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "healthy"}


class TestGlobalExceptionHandler:
    def test_unhandled_exception_does_not_leak_details_to_client(self, isolated_db, monkeypatch):
        """
        意図的に内部で機密情報を含む例外を発生させ、レスポンスボディに
        その内容が一切含まれず、常に定型メッセージのみが返ることを確認する。

        TestClientのデフォルト(raise_server_exceptions=True)ではサーバー側例外が
        テストプロセスに再送出されてしまい、global_exception_handlerが実際に
        クライアントへ返すレスポンスを検証できないため、ここでは無効化する。
        """
        secret_detail = "/etc/passwd could not be read: permission denied for internal_admin_token=xyz"

        def _boom():
            raise RuntimeError(secret_detail)

        monkeypatch.setattr(
            unified_server.quest_router.game_system, "sync_master_data", lambda: _boom()
        )

        client = TestClient(unified_server.app, raise_server_exceptions=False)
        res = client.post("/api/quest/sync_master")

        assert res.status_code == 500
        assert res.json() == {"detail": "Internal Server Error"}
        assert secret_detail not in res.text


class TestIpRestrictionMiddlewareCurrentBehavior:
    """
    現状の ip_restriction_middleware は、プライベートIP判定に失敗した場合でも
    最終的に call_next(request) を呼んで通過させる実装になっており、
    実質的な遮断機能を持たない(2.2参照)。将来この挙動を意図的に厳格化した際に
    「Webhookパスだけは常に通す」という前提が壊れていないかを検知するためのテスト。
    """

    def test_webhook_paths_bypass_without_ip_parsing(self, api_client, monkeypatch):
        import ipaddress

        calls = []
        original = ipaddress.ip_address

        def _spy(value):
            calls.append(value)
            return original(value)

        monkeypatch.setattr(ipaddress, "ip_address", _spy)
        # LINE Bot未設定環境では501になるが、ミドルウェアが素通りしていることの確認が目的
        api_client.post("/callback/line", content=b"{}")
        assert calls == []

    def test_normal_path_is_currently_always_allowed(self, api_client):
        """スプーフィング可能なヘッダーを一切付けなくても現状は必ず通過する(既知の未解決リスク)"""
        res = api_client.get("/health")
        assert res.status_code == 200

    def test_spoofed_forwarded_for_header_is_also_allowed(self, api_client):
        """
        X-Forwarded-For を詐称しても(現状は)拒否されない。
        これは修正すべき既知のセキュリティリスクとして最終報告書に記録する。
        """
        res = api_client.get("/health", headers={"X-Forwarded-For": "203.0.113.5"})
        assert res.status_code == 200


class _FakeProcess:
    def __init__(self):
        self.terminated = False
        self.killed = False
        self.pid = 12345

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        if not self.terminated:
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)

    def kill(self):
        self.killed = True


class TestLifespan:
    def test_startup_spawns_monitors_and_applies_migrations_shutdown_terminates_them(
        self, isolated_db, monkeypatch
    ):
        spawned = []

        def _fake_popen(args, **kwargs):
            proc = _FakeProcess()
            spawned.append((args, proc))
            return proc

        migration_calls = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen)
        monkeypatch.setattr(
            unified_server, "apply_pending_migrations", lambda conn: migration_calls.append(conn)
        )

        with TestClient(unified_server.app) as client:
            res = client.get("/health")
            assert res.status_code == 200
            assert len(spawned) == 2  # camera_monitor.py, scheduler_boot.py
            assert len(migration_calls) == 1

        # シャットダウン後は起動したプロセスすべてに terminate が呼ばれていること
        for _args, proc in spawned:
            assert proc.terminated is True
