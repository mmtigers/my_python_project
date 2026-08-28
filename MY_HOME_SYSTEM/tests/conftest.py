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
