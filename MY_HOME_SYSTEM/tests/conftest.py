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
os.environ["DISCORD_WEBHOOK_ERROR_CAM"] = ""
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

    # client=("127.0.0.1", ...) を明示: access_control_middleware は実際の接続元IPで
    # 信頼判定するため、デフォルトの ("testclient", 50000) のままだと非内部扱いとなり
    # Cloudflare AccessのJWTが要求されて全APIテストが403になる。
    # 外部アクセス経路の検証は tests/test_cf_access_middleware.py が担当する。
    return TestClient(unified_server.app, client=("127.0.0.1", 50000))
