# MY_HOME_SYSTEM/tests/test_unified_server_spa.py
"""
unified_server.py の SPA配信ルート (/quest/*, /camera/*) のテスト。

これらのルートは unified_server.py のモジュールロード時に
`config.QUEST_DIST_DIR` が実在する場合のみ登録される(モジュールレベルのif分岐)。
通常のテスト環境ではこのディレクトリが存在しないため、他のテストが使う
`unified_server.app` にはこれらのルートが一切登録されていない。
そのため、一時ディレクトリを用意した上で config.QUEST_DIST_DIR を差し替え、
unified_server モジュール自体を再読み込みしてルート登録を発生させる。
テスト終了後は必ず元の状態に戻し、他のテストファイルへ影響しないようにする。
"""
import importlib
import os
import sys

import pytest
from starlette.testclient import TestClient

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
import unified_server


@pytest.fixture
def spa_client(tmp_path, monkeypatch):
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<html><body>Quest App</body></html>")
    (dist_dir / "app.js").write_text("console.log('app');")

    monkeypatch.setenv("QUEST_DIST_DIR", str(dist_dir))
    importlib.reload(config)
    importlib.reload(unified_server)

    client = TestClient(unified_server.app)
    try:
        yield client
    finally:
        # 元の状態に戻し、他のテストファイルの unified_server.app に影響しないようにする
        monkeypatch.undo()
        importlib.reload(config)
        importlib.reload(unified_server)


def test_quest_root_serves_index_html(spa_client):
    res = spa_client.get("/quest")
    assert res.status_code == 200
    assert "Quest App" in res.text


def test_quest_trailing_slash_serves_index_html(spa_client):
    res = spa_client.get("/quest/")
    assert res.status_code == 200
    assert "Quest App" in res.text


def test_camera_root_serves_index_html(spa_client):
    res = spa_client.get("/camera")
    assert res.status_code == 200
    assert "Quest App" in res.text


def test_existing_static_asset_is_served_directly(spa_client):
    res = spa_client.get("/quest/app.js")
    assert res.status_code == 200
    assert "console.log" in res.text


def test_unknown_spa_route_falls_back_to_index_html(spa_client):
    """SPAのクライアントサイドルーティング(例: /quest/some/deep/route)はindex.htmlにフォールバックすること"""
    res = spa_client.get("/quest/some/deep/client-route")
    assert res.status_code == 200
    assert "Quest App" in res.text


def test_routes_are_not_registered_when_quest_dist_dir_missing(tmp_path, monkeypatch):
    """QUEST_DIST_DIRが存在しない場合はSPAルート自体が登録されず404になること"""
    monkeypatch.setenv("QUEST_DIST_DIR", str(tmp_path / "does_not_exist"))
    importlib.reload(config)
    importlib.reload(unified_server)
    try:
        client = TestClient(unified_server.app)
        res = client.get("/quest")
        assert res.status_code == 404
    finally:
        monkeypatch.undo()
        importlib.reload(config)
        importlib.reload(unified_server)
