# MY_HOME_SYSTEM/tests/conftest.py
"""
テスト共通フィクスチャ。

既存の11テストファイルは各自 setup_method/setUp で
config.SQLITE_DB_PATH を書き換えて init_unified_db.init_db() を呼ぶ、
というコピペパターンでDB分離を行っている。挙動を変えるリスクを避けるため
既存ファイルはそのままにし、新規テストファイルのみここで定義する
`isolated_db` フィクスチャを使う。
"""
import os
import sys

# core.logger.setup_logging() は import された時点で config.DISCORD_WEBHOOK_ERROR を
# DiscordErrorHandler に焼き込む(以降 config を monkeypatch しても効果がない)。
# 各サービスモジュールの `logger = setup_logging(...)` はテストファイルの import
# (collection)時点で実行されるため、個々のテストの setUp/monkeypatch では
# 手遅れになる。`import config` より前に環境変数そのものを潰しておくことで、
# どのテストファイルが最初に import されても実際のDiscord Webhookが
# 発火しないようにする(load_dotenv は既存の環境変数を上書きしないため有効)。
#
# 2026-08-28: DISCORD_WEBHOOK_ERROR系のみをマスクしていたため、報酬の申請/使用
# 通知が使う DISCORD_WEBHOOK_NOTIFY 経路がノーマークになっており、ローカルの
# .env に本番の認証情報が入った状態で test_quest_router_endpoints.py 等の
# inventory系テスト(実際にHTTP経由でuse_item/consume_itemを叩く)を実行すると
# 本物のDiscord/LINEに通知が飛ぶ事故が発生した。notification_service経由で
# 送信されうる認証情報は全てここでマスクする。
os.environ["DISCORD_WEBHOOK_ERROR"] = ""
os.environ["DISCORD_WEBHOOK_REPORT"] = ""
os.environ["DISCORD_WEBHOOK_NOTIFY"] = ""
os.environ["DISCORD_WEBHOOK_URL"] = ""
os.environ["LINE_CHANNEL_ACCESS_TOKEN"] = ""
os.environ["LINE_USER_ID"] = ""

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
import init_unified_db


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """
    tmp_path配下に空のSQLiteファイルを作り、config.SQLITE_DB_PATH をテスト用に
    差し替えたうえでスキーマを初期化する。テスト終了時に自動で元のパスへ復元される
    (monkeypatch)ため、他のテストファイル・実DBへ影響しない。
    """
    db_path = tmp_path / "test_home_system.db"
    monkeypatch.setattr(config, "SQLITE_DB_PATH", str(db_path))
    init_unified_db.init_db()
    return str(db_path)


@pytest.fixture
def api_client(isolated_db):
    """
    unified_server.app に対する httpx ベースの TestClient。

    `with TestClient(app) as c:` は使わない — unified_server.lifespan() は
    subprocess.Popen でカメラ監視/スケジューラの実プロセスを起動するため、
    通常のエンドポイントテストでそれを毎回起動すると重く、CI環境にも依存する。
    ルーター登録・静的ファイルmountはモジュールロード時に完了しているため、
    lifespanを起動しなくても大半のエンドポイントは動作する。
    lifespan自体を検証したいテストは、このフィクスチャを使わず
    subprocess.Popen 等を個別にmonkeypatchした上で `with TestClient(app):` を使うこと。
    """
    from starlette.testclient import TestClient
    import unified_server

    return TestClient(unified_server.app)


class FakeChildProcess:
    """`lifespan` が起動する監視子プロセス(camera_monitor / scheduler)の代役。

    `subprocess.Popen` を直接モックすると「どの子プロセスとして起動されたか」が
    分からないため、`_spawn_child_process` を差し替えて名前付きの偽プロセスを返す。
    シャットダウン時に `terminate`/`wait`/`kill` のどれが呼ばれたかを記録する。
    """

    def __init__(self, name: str, pid: int = 99999) -> None:
        self.name = name
        self.pid = pid
        self.terminated = False
        self.killed = False
        self.waited = False
        self._returncode = None

    def poll(self):
        return self._returncode

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout=None):
        self.waited = True
        return 0

    def kill(self) -> None:
        self.killed = True


@pytest.fixture
def lifespan_client_factory(isolated_db, monkeypatch):
    """`lifespan` を実行する TestClient を作るファクトリ。

    `api_client` の docstring が指示する「subprocess.Popen 等を個別に monkeypatch
    した上で `with TestClient(app):` を使う」パターンを、実際に使える形で提供する
    (Issue #756 / AUDIT-027)。以前はこの指示が docstring にあるだけで、実際に
    そのパターンで書かれたテストが少なく、lifespan(マイグレーション適用・NAS
    プリウォーム・監視子プロセス起動・死活監視・シャットダウン)という最も危険な
    経路がほぼ未検証だった。

    シャットダウン側の検証(子プロセスの terminate、camera_service.stop_all_processes()
    等)は `with` を抜けた**後**に assert する必要があるため、フィクスチャ側で
    `with` を張ってしまう `api_client_with_lifespan` ではなくこちらを使う。
    マイグレーション失敗時の挙動も、`with` に入る前に差し替える必要があるため
    `migration_error` 引数で注入する。

    使い方::

        with lifespan_client_factory() as (client, spawned):
            assert client.get("/health").status_code == 200
        assert all(p.terminated for p in spawned)
    """
    import contextlib as _contextlib

    import unified_server
    from starlette.testclient import TestClient

    # 子プロセスのグローバル(camera_process / scheduler_process)と再起動履歴は
    # モジュールレベルの可変状態なので、テストごとに初期化して復元する。
    monkeypatch.setattr(unified_server, "camera_process", None)
    monkeypatch.setattr(unified_server, "scheduler_process", None)
    monkeypatch.setattr(unified_server, "_child_restart_history", {})
    monkeypatch.setattr(unified_server, "_child_restart_disabled", set())
    # 30秒間隔の監視ループを即座に回す。`_supervise_child_processes` の既定引数は
    # def 時に束縛されるため、定数の差し替えだけでは 30 秒のままになる。実体を
    # 包んで差し替え、差し替え後の定数を明示的に渡す。
    monkeypatch.setattr(unified_server, "CHILD_MONITOR_INTERVAL_SEC", 0.01)
    _real_supervise = unified_server._supervise_child_processes

    async def _fast_supervise(interval_sec: float = 0.01) -> None:
        await _real_supervise(unified_server.CHILD_MONITOR_INTERVAL_SEC)

    monkeypatch.setattr(unified_server, "_supervise_child_processes", _fast_supervise)

    @_contextlib.contextmanager
    def _factory(migration_error: "Exception | None" = None):
        spawned = []

        def _fake_spawn(name: str):
            proc = FakeChildProcess(name)
            spawned.append(proc)
            return proc

        monkeypatch.setattr(unified_server, "_spawn_child_process", _fake_spawn)

        if migration_error is None:
            monkeypatch.setattr(unified_server, "apply_pending_migrations", lambda conn: None)
        else:
            def _boom(conn):
                raise migration_error

            monkeypatch.setattr(unified_server, "apply_pending_migrations", _boom)

        with TestClient(unified_server.app) as client:
            yield client, spawned

    return _factory


@pytest.fixture
def api_client_with_lifespan(lifespan_client_factory):
    """`lifespan` を実行済みの TestClient と、起動された偽子プロセスの一覧。

    `lifespan_client_factory()` を既定の引数(マイグレーション成功)で開いたもの。
    起動側だけを検証するテスト向けの薄いラッパーで、シャットダウン側を検証したい
    場合や、マイグレーション失敗を注入したい場合は `lifespan_client_factory` を
    直接使うこと。
    """
    with lifespan_client_factory() as ctx:
        yield ctx
