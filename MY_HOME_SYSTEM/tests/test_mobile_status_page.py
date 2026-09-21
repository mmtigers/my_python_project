# MY_HOME_SYSTEM/tests/test_mobile_status_page.py
"""軽量ページ `/dashboard/m` と、その土台である `services/home_status_service.py` のテスト。

このページは「スマホで見るのは結局ステータスカードの9枚」という用途に対して、
Streamlit の初期化・WebSocket接続・Reactの読み込みを丸ごと省くためのもの。
サーバーが1回のリクエストでHTMLを返して終わる。

同時に、判定ロジックを1箇所に集めた("カードの内容がダッシュボード本体と
食い違わない")ことを固定する。2つの画面が同じ内容を別々に計算し始めると、
どちらかを直した時にもう片方が古いままになる。
"""
import os
import sys
from datetime import datetime
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from routers import dashboard_router
from services import home_status_service

NOW = datetime.fromisoformat("2026-09-19T12:00:00+09:00")

_VIEWS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "views", "dashboard"
)


@pytest.fixture(autouse=True)
def _clear_status_cache():
    home_status_service.clear_status_cache()
    yield
    home_status_service.clear_status_cache()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(dashboard_router.router)
    return TestClient(app)


def _stub_loaders(**overrides):
    """DB・スクレイピングを差し替える(テストで実際に読みに行かせない)。"""
    defaults = {
        "load_sensor_data": pd.DataFrame(),
        "load_generic_data": pd.DataFrame(),
        "load_bicycle_data": pd.DataFrame(),
        "load_nas_status": None,
        "get_memory_usage": {"percent": 42.0},
        "calculate_monthly_cost_cumulative": 1234,
    }
    defaults.update(overrides)
    patches = [
        patch.object(home_status_service.analysis_service, name, return_value=value)
        for name, value in defaults.items()
    ]
    patches.append(
        patch.object(home_status_service.train_service, "get_jr_traffic_status",
                     return_value={"宝塚線": {}, "神戸線": {}})
    )
    return patches


class TestMobileStatusPage:
    def _get(self, **overrides):
        patches = _stub_loaders(**overrides)
        for p in patches:
            p.start()
        try:
            with _client() as client:
                return client.get(f"{config.DASHBOARD_BASE_PATH}/m")
        finally:
            for p in patches:
                p.stop()

    def test_page_renders_all_status_cards_without_streamlit(self):
        res = self._get()

        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/html")
        assert res.text.count('class="status-card') == 9
        assert "status-grid" in res.text

    def test_page_refreshes_itself(self):
        """置きっぱなしでも古い値を見せ続けないこと。"""
        res = self._get()
        assert f'http-equiv="refresh" content="{home_status_service.MOBILE_PAGE_REFRESH_SEC}"' in res.text

    def test_page_links_back_to_the_full_dashboard_and_quest(self):
        res = self._get()
        assert f'href="{config.DASHBOARD_BASE_PATH}/"' in res.text
        assert 'href="/quest"' in res.text

    def test_page_can_be_added_to_the_home_screen(self):
        res = self._get()
        assert f'href="{config.DASHBOARD_BASE_PATH}/m/app.webmanifest"' in res.text
        assert 'crossorigin="use-credentials"' in res.text

    def test_its_manifest_opens_the_light_page_not_the_full_one(self):
        """追加した画面と違うものが開くと戸惑うため、start_url を分けてある。"""
        with _client() as client:
            manifest = client.get(f"{config.DASHBOARD_BASE_PATH}/m/app.webmanifest").json()

        assert manifest["start_url"] == f"{config.DASHBOARD_BASE_PATH}/m"
        assert manifest["display"] == "standalone"

    def test_a_failing_loader_does_not_take_down_the_page(self):
        """1枚のカードの取得失敗でページ全体を落とさないこと。"""
        patches = _stub_loaders()
        for p in patches:
            p.start()
        try:
            with patch.object(home_status_service.train_service, "get_jr_traffic_status",
                              side_effect=RuntimeError("scrape failed")), _client() as client:
                res = client.get(f"{config.DASHBOARD_BASE_PATH}/m")
        finally:
            for p in patches:
                p.stop()

        assert res.status_code == 200
        assert res.text.count('class="status-card') == 9
        # 取れなかったものは「平常運転」と偽らずに「取得不可」と出す
        assert "情報取得不可" in res.text


class TestStatusCacheOnTheServerSide:
    """`unified_server` には `st.cache_data` が無いため、同じTTLのメモを自前で持つ。"""

    def test_repeated_requests_do_not_scrape_again(self):
        patches = _stub_loaders()
        for p in patches:
            p.start()
        try:
            with patch.object(home_status_service.train_service, "get_jr_traffic_status",
                              return_value={"宝塚線": {}, "神戸線": {}}) as mock_scrape, \
                 _client() as client:
                client.get(f"{config.DASHBOARD_BASE_PATH}/m")
                client.get(f"{config.DASHBOARD_BASE_PATH}/m")
        finally:
            for p in patches:
                p.stop()

        mock_scrape.assert_called_once()

    def test_failures_are_not_cached(self):
        """失敗をキャッシュすると、復旧しても60秒間は壊れたままになる。"""
        with patch.object(home_status_service.analysis_service, "get_memory_usage",
                          side_effect=RuntimeError("psutil failed")) as failing:
            assert home_status_service._cached("memory", failing) is None
            assert home_status_service._cached("memory", failing) is None

        assert failing.call_count == 2

    def test_ttl_matches_the_streamlit_side(self):
        from views.dashboard import common as view_common

        assert home_status_service.STATUS_CACHE_TTL_SEC == view_common.DASHBOARD_CACHE_TTL_SEC


class TestOneSourceOfTruth:
    def test_the_service_does_not_depend_on_streamlit(self):
        """`unified_server.py` から使うため、Streamlit を持ち込まないこと。

        (docstring 中の言及と区別するため、import 文そのものを見る)
        """
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(home_status_service))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

        assert "streamlit" not in imported

    def test_the_streamlit_view_no_longer_decides_card_contents(self):
        """判定を View 側へ書き戻すと、軽量ページと内容が食い違う。"""
        with open(os.path.join(_VIEWS_DIR, "summary.py"), encoding="utf-8") as f:
            source = f.read()

        for judgement in ("theme-green", "theme-red", "炊飯器", "高砂"):
            assert judgement not in source, f"summary.py に判定({judgement})が戻っている"

    def test_both_screens_use_the_same_card_builder(self):
        from views.dashboard import summary

        with open(os.path.join(_VIEWS_DIR, "summary.py"), encoding="utf-8") as f:
            source = f.read()

        assert "home_status_service.build_status_cards" in source
        assert summary.home_status_service is home_status_service

    def test_card_css_is_shared_with_the_streamlit_page(self):
        from views.dashboard import common as view_common

        assert home_status_service.STATUS_CARD_CSS in view_common.CUSTOM_CSS


class TestMobilePageHtmlSafety:
    def test_card_titles_and_values_are_escaped(self):
        """Issue #378 と同じ理由。DB・スクレイピング由来の文字列が混ざりうる。"""
        cards = [home_status_service.StatusCard("<script>alert(1)</script>", "<img src=x>", "theme-gray")]
        page = home_status_service.render_mobile_status_page_html(
            cards, NOW,
            manifest_path="/dashboard/m/app.webmanifest",
            icon_path="/dashboard/icon-180.png",
            dashboard_path="/dashboard/",
            quest_path="/quest",
        )

        assert "<script>alert(1)</script>" not in page
        assert "&lt;script&gt;" in page

    def test_intentional_html_is_kept_for_the_bicycle_card(self):
        cards = [home_status_service.StatusCard("🚲 駐輪場待機", "第1A: <b>3</b>台", "theme-green",
                                                value_is_html=True)]
        page = home_status_service.render_mobile_status_page_html(
            cards, NOW,
            manifest_path="/m.webmanifest", icon_path="/i.png",
            dashboard_path="/dashboard/", quest_path="/quest",
        )

        assert "<b>3</b>台" in page

    def test_links_are_large_enough_to_tap(self):
        page = home_status_service.render_mobile_status_page_html(
            [], NOW,
            manifest_path="/m.webmanifest", icon_path="/i.png",
            dashboard_path="/dashboard/", quest_path="/quest",
        )

        assert "min-height: 44px" in page
