# MY_HOME_SYSTEM/tests/test_mobile_status_page.py
"""ダッシュボードのホームページ(`/dashboard`)と、その土台である
`services/home_status_service.py` / `services/dashboard_page_service.py` のテスト。

#829: 以前はStreamlit版の「詳細表示」(`dashboard.py`)と、Streamlitを介さない
「かんたん表示」(`/dashboard/m`。ステータスカードのみの単一ページ)が別々に存在した。
詳細表示・表示モード切替UIを廃止し、かんたん表示を唯一のダッシュボードとする方針変更に
伴い、`unified_server`自身がホーム/見守り/くらし/システムの4ページをHTMLで返す構成に
なった。カードの判定ロジックは`home_status_service.py`に一本化されている
("2つの画面が同じ内容を別々に計算し始めると、どちらかを直した時にもう片方が
古いままになる"という以前からの方針は、ページが増えても変わらない)。
"""
import os
import sys
from datetime import date, datetime
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from routers import dashboard_router
from services import dashboard_page_service, home_status_service

NOW = datetime.fromisoformat("2026-09-19T12:00:00+09:00")

# サマリーに並ぶカードの枚数。`home_status_service.build_status_cards` の
# 戻り値と対になっているので、カードを増減させるときは一緒に直す。
EXPECTED_CARD_COUNT = 7


@pytest.fixture(autouse=True)
def _clear_status_cache():
    home_status_service.clear_status_cache()
    yield
    home_status_service.clear_status_cache()


# 外部リンクの稼働チェック(あさノートへの実HTTP・実DBアクセス)の本物。個別のテストで使う。
_REAL_CHECK_QUEST = home_status_service.check_quest_app_health
_REAL_CHECK_ASA_NOTE = home_status_service.check_asa_note_health


@pytest.fixture(autouse=True)
def _stub_link_health_checks(monkeypatch):
    """ホームを開くテストが実際に外部(あさノート)へHTTP接続しないよう、既定で「正常」に差し替える。"""
    monkeypatch.setattr(home_status_service, "check_quest_app_health", lambda: True)
    monkeypatch.setattr(home_status_service, "check_asa_note_health", lambda url: True)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(dashboard_router.router)
    return TestClient(app)


def _stub_loaders(**overrides):
    """DBの読み取りを差し替える(テストで実際に読みに行かせない)。"""
    defaults = {
        "load_sensor_data": pd.DataFrame(),
        "load_generic_data": pd.DataFrame(),
        "load_nas_status": None,
        "load_nas_history": pd.DataFrame(),
        "get_memory_usage": {"percent": 42.0},
        "calculate_monthly_cost_cumulative": 1234,
        "calculate_last_month_cost_same_point": 1000,
        "get_disk_usage": {"percent": 55.0},
        "calculate_daily_cost_series": [(date(2026, 9, 19), 123)],
    }
    defaults.update(overrides)
    return [
        patch.object(home_status_service.analysis_service, name, return_value=value)
        for name, value in defaults.items()
    ]


class TestDashboardHomePage:
    def _get(self, **overrides):
        patches = _stub_loaders(**overrides)
        for p in patches:
            p.start()
        try:
            with _client() as client:
                return client.get(f"{config.DASHBOARD_BASE_PATH}/")
        finally:
            for p in patches:
                p.stop()

    def test_page_renders_all_status_cards(self):
        res = self._get()

        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/html")
        assert res.text.count('class="status-card') == EXPECTED_CARD_COUNT
        assert "status-grid" in res.text

    def test_page_refreshes_itself(self):
        """置きっぱなしでも古い値を見せ続けないこと。"""
        res = self._get()
        assert f'var intervalMs = {home_status_service.MOBILE_PAGE_REFRESH_SEC * 1000};' in res.text

    def test_page_links_to_the_three_sub_pages_and_external_links(self):
        res = self._get()
        assert f'href="{config.DASHBOARD_BASE_PATH}/watch"' in res.text
        assert f'href="{config.DASHBOARD_BASE_PATH}/life"' in res.text
        assert f'href="{config.DASHBOARD_BASE_PATH}/sys"' in res.text
        assert 'href="/quest"' in res.text
        assert f'href="{config.ASA_NOTE_URL}"' in res.text
        assert f'href="{config.YORU_NOTE_URL}"' in res.text

    def test_page_can_be_added_to_the_home_screen(self):
        res = self._get()
        assert f'href="{config.DASHBOARD_BASE_PATH}/app.webmanifest"' in res.text
        assert 'crossorigin="use-credentials"' in res.text

    def test_a_failing_loader_does_not_take_down_the_page(self):
        """1枚のカードの取得失敗でページ全体を落とさないこと。"""
        patches = _stub_loaders()
        for p in patches:
            p.start()
        try:
            with patch.object(home_status_service.analysis_service, "get_memory_usage",
                              side_effect=RuntimeError("psutil failed")), _client() as client:
                res = client.get(f"{config.DASHBOARD_BASE_PATH}/")
        finally:
            for p in patches:
                p.stop()

        assert res.status_code == 200
        assert res.text.count('class="status-card') == EXPECTED_CARD_COUNT
        # 取れなかったものは値を偽らずに「取得失敗」と出す
        assert "取得失敗" in res.text


class TestHomeScreenInstall:
    def test_manifest_and_icon_are_served(self):
        with _client() as client:
            res = client.get(f"{config.DASHBOARD_BASE_PATH}/app.webmanifest")
            assert res.status_code == 200
            manifest = res.json()
            assert manifest["start_url"] == f"{config.DASHBOARD_BASE_PATH}/"

            icon = client.get(f"{config.DASHBOARD_BASE_PATH}/icon-180.png")
            assert icon.status_code == 200
            assert icon.headers["content-type"] == "image/png"

    def test_an_unsupported_icon_size_is_rejected(self):
        with _client() as client:
            res = client.get(f"{config.DASHBOARD_BASE_PATH}/icon-999.png")
            assert res.status_code == 404


class TestDashboardSnapshot:
    """見守りページのスナップショット画像1枚を返すエンドポイント(パストラバーサル対策込み)。"""

    def test_existing_snapshot_is_served(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "ASSETS_DIR", str(tmp_path / "assets"))
        snap_dir = tmp_path / "assets" / "snapshots"
        snap_dir.mkdir(parents=True)
        (snap_dir / "photo.jpg").write_bytes(b"fake-jpeg")
        with _client() as client:
            res = client.get(f"{config.DASHBOARD_BASE_PATH}/snapshot/photo.jpg")
        assert res.status_code == 200
        assert res.headers["content-type"] == "image/jpeg"

    def test_missing_snapshot_returns_404(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "ASSETS_DIR", str(tmp_path / "assets"))
        with _client() as client:
            res = client.get(f"{config.DASHBOARD_BASE_PATH}/snapshot/missing.jpg")
        assert res.status_code == 404

    def test_path_traversal_is_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "ASSETS_DIR", str(tmp_path / "assets"))
        (tmp_path / "assets").mkdir(parents=True)
        (tmp_path / "assets" / "secret.txt").write_text("x")
        with _client() as client:
            res = client.get(f"{config.DASHBOARD_BASE_PATH}/snapshot/..%2Fsecret.txt")
        assert res.status_code == 404


class TestLegacyUrlRedirects:
    """#829でStreamlit版のURL体系(`?tab=`)・旧「かんたん表示」(`/dashboard/m`)を
    廃止したが、スマートフォンのホーム画面に古いURLを追加している可能性があるため、
    どちらも新しいページへリダイレクトする。"""

    def test_old_mobile_path_redirects_to_the_home_page(self):
        with _client() as client:
            res = client.get(f"{config.DASHBOARD_BASE_PATH}/m", follow_redirects=False)

        assert res.status_code == 301
        assert res.headers["location"] == f"{config.DASHBOARD_BASE_PATH}/"

    @pytest.mark.parametrize("tab,expected_suffix", [
        ("watch", "/watch"), ("life", "/life"), ("sys", "/sys"), ("home", ""),
    ])
    def test_old_tab_query_param_redirects_to_the_matching_page(self, tab, expected_suffix):
        with _client() as client:
            res = client.get(f"{config.DASHBOARD_BASE_PATH}?tab={tab}", follow_redirects=False)

        assert res.status_code == 302
        assert res.headers["location"] == f"{config.DASHBOARD_BASE_PATH}{expected_suffix}"

    def test_an_unknown_tab_value_redirects_home(self):
        with _client() as client:
            res = client.get(f"{config.DASHBOARD_BASE_PATH}?tab=nonexistent", follow_redirects=False)

        assert res.status_code == 302
        assert res.headers["location"] == config.DASHBOARD_BASE_PATH


class TestSubPages:
    """👀見守り・💡くらし・🔧システムの3ページ(項目1)。"""

    def _get(self, path, **overrides):
        patches = _stub_loaders(**overrides)
        for p in patches:
            p.start()
        try:
            with _client() as client:
                return client.get(f"{config.DASHBOARD_BASE_PATH}/{path}")
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.parametrize("path", ["watch", "life", "sys"])
    def test_each_page_has_a_back_to_home_link(self, path):
        res = self._get(path)
        assert res.status_code == 200
        assert f'href="{config.DASHBOARD_BASE_PATH}/"' in res.text
        assert "ホームへ戻る" in res.text

    def test_watch_page_shows_camera_selector_gallery_and_logs(self):
        res = self._get("watch")
        assert "カメラの映像" in res.text
        assert "最近の写真" in res.text
        assert "防犯ログ" in res.text
        assert "高砂実家のセンサーログ" in res.text

    def test_life_page_shows_the_life_group_cards_only(self):
        res = self._get("life")
        assert "💰 今月の電気代" in res.text
        assert "👵 高砂 (実家)" not in res.text

    def test_life_page_shows_daily_cost_detail(self):
        """不具合修正: 電気代カードをタップしても、以前は同じカードの再掲だけで
        詳細と呼べる情報が無かった。日別の電気代推移が表示されること。"""
        res = self._get("life")
        assert "日別の電気代" in res.text
        assert "cost-history" in res.text
        assert "123円" in res.text

    def test_sys_page_shows_overall_summary_and_maintenance_actions(self):
        res = self._get("sys")
        assert "すべて正常" in res.text or "確認が必要です" in res.text
        assert "システム再起動" in res.text
        assert "今すぐバックアップ" in res.text

    def test_sys_page_shows_nas_capacity_history(self):
        """不具合修正: NASカードをタップしても容量の履歴を見る手段が無かった。"""
        res = self._get(
            "sys",
            load_nas_history=pd.DataFrame({
                "timestamp": [pd.Timestamp("2026-09-14"), pd.Timestamp("2026-09-15")],
                "percent": [30.0, 42.0],
                "free_gb": [120, 100],
            }),
        )
        assert 'id="nas-history"' in res.text
        assert "<svg" in res.text
        assert "現在 42%" in res.text

    @pytest.mark.parametrize("query,expected_days", [
        ("", 30), ("?nas_days=7", 7), ("?nas_days=90", 90), ("?nas_days=5", 30), ("?nas_days=abc", 30), ("?nas_days=", 30),
    ])
    def test_sys_page_nas_period_selects_the_history_range(self, query, expected_days):
        """?nas_days=7|30|90で期間を切り替える。範囲外・数値でない値は既定の30日(422にしない)。"""
        with patch.object(home_status_service, "get_nas_history", return_value=pd.DataFrame()) as mock_load:
            res = self._get(f"sys{query}")
        assert res.status_code == 200
        mock_load.assert_called_once_with(expected_days)
        assert "表示期間" in res.text

    def test_watch_page_device_filter_param_is_accepted(self):
        res = self._get("watch?device=no-such-device")
        assert res.status_code == 200
        assert 'name="device"' not in res.text or "すべての機器" in res.text

    def test_sys_page_restart_button_is_disabled_until_confirmed(self):
        res = self._get("sys")
        assert 'id="restartBtn" disabled' in res.text

    def test_sys_page_deploy_button_is_disabled_until_confirmed(self):
        res = self._get("sys")
        assert 'id="deployBtn" disabled' in res.text
        assert 'id="deployConfirm"' in res.text
        assert "/api/system/deploy" in res.text
        assert "/api/system/deploy/status" in res.text


class TestStatusCacheOnTheServerSide:
    """`unified_server` には `st.cache_data` が無いため、同じTTLのメモを自前で持つ。"""

    def test_repeated_requests_do_not_read_the_db_again(self):
        patches = _stub_loaders()
        for p in patches:
            p.start()
        try:
            with patch.object(home_status_service.analysis_service, "load_sensor_data",
                              return_value=pd.DataFrame()) as mock_load, \
                 _client() as client:
                client.get(f"{config.DASHBOARD_BASE_PATH}/")
                client.get(f"{config.DASHBOARD_BASE_PATH}/")
        finally:
            for p in patches:
                p.stop()

        mock_load.assert_called_once()

    def test_sub_pages_share_the_same_cache_as_the_home_page(self):
        """見守り/くらし/システムページを開いても、DB読み取り回数が増えないこと。"""
        patches = _stub_loaders()
        for p in patches:
            p.start()
        try:
            with patch.object(home_status_service.analysis_service, "load_sensor_data",
                              return_value=pd.DataFrame()) as mock_load, \
                 _client() as client:
                client.get(f"{config.DASHBOARD_BASE_PATH}/")
                client.get(f"{config.DASHBOARD_BASE_PATH}/watch")
                client.get(f"{config.DASHBOARD_BASE_PATH}/sys")
        finally:
            for p in patches:
                p.stop()

        mock_load.assert_called_once()

    def test_watch_page_with_date_loads_that_day_from_the_db(self):
        """`?date=`指定時は共通キャッシュ(直近3000件)ではなく、指定日をDBから取る。"""
        patches = _stub_loaders()
        for p in patches:
            p.start()
        try:
            with patch.object(home_status_service.analysis_service, "load_sensor_data",
                              return_value=pd.DataFrame()) as mock_load, \
                 _client() as client:
                res = client.get(f"{config.DASHBOARD_BASE_PATH}/watch?date=2026-10-01")
        finally:
            for p in patches:
                p.stop()

        assert res.status_code == 200
        mock_load.assert_called_once_with(day=date(2026, 10, 1))
        assert 'value="2026-10-01"' in res.text

    def test_watch_page_with_invalid_date_falls_back_to_normal_view(self):
        patches = _stub_loaders()
        for p in patches:
            p.start()
        try:
            with patch.object(home_status_service.analysis_service, "load_sensor_data",
                              return_value=pd.DataFrame()) as mock_load, \
                 _client() as client:
                res = client.get(f"{config.DASHBOARD_BASE_PATH}/watch?date=not-a-date")
        finally:
            for p in patches:
                p.stop()

        assert res.status_code == 200
        mock_load.assert_called_once_with(limit=home_status_service.MOBILE_SENSOR_ROW_LIMIT)

    def test_failures_are_not_cached(self):
        """失敗をキャッシュすると、復旧しても60秒間は壊れたままになる。"""
        with patch.object(home_status_service.analysis_service, "get_memory_usage",
                          side_effect=RuntimeError("psutil failed")) as failing:
            assert home_status_service._cached("memory", failing) is None
            assert home_status_service._cached("memory", failing) is None

        assert failing.call_count == 2


class TestOneSourceOfTruth:
    def test_the_service_does_not_depend_on_streamlit(self):
        """`unified_server.py` から使うため、Streamlit を持ち込まないこと。"""
        import ast
        import inspect

        for module in (home_status_service, dashboard_page_service):
            tree = ast.parse(inspect.getsource(module))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])

            assert "streamlit" not in imported, module.__name__


class TestMobilePageHtmlSafety:
    def test_card_titles_and_values_are_escaped(self):
        """Issue #378 と同じ理由。DB・スクレイピング由来の文字列が混ざりうる。"""
        cards = [home_status_service.StatusCard("<script>alert(1)</script>", "<img src=x>", "theme-gray")]
        page = dashboard_page_service.render_home_page(
            cards, NOW,
            dashboard_path="/dashboard/", status_path="/dashboard/status",
            quest_path="/quest", asa_note_url=config.ASA_NOTE_URL,
            refresh_sec=60,
        )

        assert "<script>alert(1)</script>" not in page
        assert "&lt;script&gt;" in page

    def test_intentional_html_is_kept_when_the_card_asks_for_it(self):
        """`value_is_html=True` を指定した呼び出し元だけがHTML断片を埋め込めること。"""
        cards = [home_status_service.StatusCard("🔧 テスト", "<b>3</b>台", "theme-green",
                                                value_is_html=True)]
        page = dashboard_page_service.render_home_page(
            cards, NOW,
            dashboard_path="/dashboard/", status_path="/dashboard/status",
            quest_path="/quest", asa_note_url=config.ASA_NOTE_URL,
            refresh_sec=60,
        )

        assert "<b>3</b>台" in page

    def test_links_are_large_enough_to_tap(self):
        page = dashboard_page_service.render_home_page(
            [], NOW,
            dashboard_path="/dashboard/", status_path="/dashboard/status",
            quest_path="/quest", asa_note_url=config.ASA_NOTE_URL,
            refresh_sec=60,
        )

        assert "min-height: 44px" in page


class TestCardsLinkToTheirDetail:
    """A: 異常に気づいてから詳細を開くまでを1タップにする。"""

    def _collect(self):
        patches = _stub_loaders()
        for p in patches:
            p.start()
        try:
            return home_status_service.collect_status_cards(NOW)
        finally:
            for p in patches:
                p.stop()

    def test_every_card_links_to_a_page_that_exists(self):
        page, _ = self._collect()
        html_out = home_status_service.render_status_grid_html(page, dashboard_path="/dashboard/")

        import re
        hrefs = re.findall(r'<a class="status-card [^"]*" href="([^"]+)"', html_out)
        assert len(hrefs) == EXPECTED_CARD_COUNT, "すべてのカードがリンクになっていること"
        for href in hrefs:
            assert href.startswith("/dashboard/")
            page_key = href.split("#", 1)[0].split("?", 1)[0].rsplit("/", 1)[1]
            assert page_key in ("watch", "life", "sys")

    def test_cards_point_at_the_page_that_actually_shows_them(self):
        """リンク先が「そのカードの詳細が載っているページ」であること。"""
        cards, _ = self._collect()
        by_title = {card.title: card.tab for card in cards}

        assert by_title["👵 高砂 (実家)"] == "watch"
        assert by_title["🎥 カメラ"] == "watch"
        assert by_title["💰 今月の電気代"] == "life"
        assert by_title["🗄️ NAS"] == "sys"

    def test_watch_group_cards_link_to_different_places_on_the_page(self):
        """不具合修正の回帰テスト: 以前は高砂・伊丹・駐車場・カメラの4枚が全部
        `tab="watch"`だけを持ち、`anchor`が無かったため、どれをタップしても
        同じURL(見守りページの先頭=カメラ映像)にしか飛べなかった。"""
        cards, _ = self._collect()
        watch_titles = ("👵 高砂 (実家)", "🏠 伊丹 (自宅)", "🚗 駐車場", "🎥 カメラ")
        hrefs = {
            card.title: home_status_service.card_detail_href(card, "/dashboard/")
            for card in cards if card.title in watch_titles
        }

        assert set(hrefs) == set(watch_titles)
        assert len(set(hrefs.values())) == len(watch_titles), "4枚のリンク先が全部違うこと"
        for href in hrefs.values():
            assert href.startswith("/dashboard/watch")

    def test_nas_card_links_to_the_history_section(self):
        """不具合修正: NASカードをタップしても容量履歴を見る手段が無かったため、
        システムページ内のNAS履歴セクションへ飛ぶ`anchor`を付けた。"""
        cards, _ = self._collect()
        (nas_card,) = [card for card in cards if card.title == "🗄️ NAS"]

        href = home_status_service.card_detail_href(nas_card, "/dashboard/")

        assert href == "/dashboard/sys#nas-history"

    def test_the_link_is_relative_to_the_viewing_origin(self):
        """固定URLを埋めるとLAN内のIPと公開ドメインのどちらかで繋がらなくなる。"""
        card = home_status_service.StatusCard("t", "v", "theme-gray", tab="watch")

        assert home_status_service.card_detail_href(card, "/dashboard/") == "/dashboard/watch"
        assert home_status_service.card_detail_href(
            home_status_service.StatusCard("t", "v", "theme-gray"), "/dashboard/"
        ) is None

    def test_anchor_is_appended_after_the_tab_url(self):
        card = home_status_service.StatusCard("t", "v", "theme-gray", tab="watch", anchor="#takasago-log")

        assert home_status_service.card_detail_href(card, "/dashboard/") == "/dashboard/watch#takasago-log"


class TestStatusGridOrder:
    def test_the_grid_order_is_not_changed(self):
        """位置で覚えている画面を動かさない(カードの並びは判定結果に関わらず固定)。"""
        cards = [
            home_status_service.StatusCard("1番目", "v", "theme-green"),
            home_status_service.StatusCard("2番目", "v", "theme-red"),
        ]

        grid = home_status_service.render_status_grid_html(cards)

        assert grid.index("1番目") < grid.index("2番目")


class TestAlertSummaryRemoved:
    """不具合修正: ホームページの「気になること」要約行は不要という要望により削除した。
    カード自体が赤・黄の色で状態を示しているため、要約行が無くても情報は失われない。"""

    def test_the_home_page_no_longer_shows_the_alert_line(self):
        page = dashboard_page_service.render_home_page(
            [home_status_service.StatusCard("異常", "v", "theme-red")], NOW,
            dashboard_path="/dashboard/", status_path="/dashboard/status",
            quest_path="/quest", asa_note_url=config.ASA_NOTE_URL,
            refresh_sec=60,
        )

        assert "気になること" not in page

    def test_render_alerts_html_no_longer_exists(self):
        """機能を隠すのではなく削除したことの確認(使われないコードを残さない)。"""
        assert not hasattr(home_status_service, "render_alerts_html")
        assert not hasattr(home_status_service, "summarize_alerts")


class TestDarkMode:
    """B: 夜にスマホで見ると白背景が眩しい。"""

    def _page(self):
        return dashboard_page_service.render_home_page(
            [], NOW,
            dashboard_path="/dashboard/", status_path="/dashboard/status",
            quest_path="/quest", asa_note_url=config.ASA_NOTE_URL,
            refresh_sec=60,
        )

    def test_the_page_follows_the_device_setting(self):
        page = self._page()

        assert "prefers-color-scheme: dark" in page
        assert "color-scheme: light dark" in page


class TestPartialRefresh:
    """C: 自動更新でページ全体を読み込み直さない。"""

    def _get(self, path):
        patches = _stub_loaders()
        for p in patches:
            p.start()
        try:
            with _client() as client:
                return client.get(path)
        finally:
            for p in patches:
                p.stop()

    def test_the_cards_can_be_fetched_on_their_own(self):
        res = self._get(f"{config.DASHBOARD_BASE_PATH}/status")

        assert res.status_code == 200
        assert res.text.count('class="status-card') == EXPECTED_CARD_COUNT
        assert res.text.startswith(f'<div id="{home_status_service.STATUS_SECTION_ID}">')
        # 断片なので、ページ全体の要素は含まない
        assert "<html" not in res.text
        assert "<style" not in res.text

    def test_the_fragment_replaces_itself(self):
        """差し替え後も同じidが残らないと、2回目以降の更新先を見失う。"""
        res = self._get(f"{config.DASHBOARD_BASE_PATH}/status")

        assert res.text.count(f'id="{home_status_service.STATUS_SECTION_ID}"') == 1

    def test_the_fragment_carries_the_fetch_time(self):
        """時刻も一緒に差し替わらないと、値だけ新しく見出しが古くなる。"""
        res = self._get(f"{config.DASHBOARD_BASE_PATH}/status")

        assert "時点" in res.text

    def test_the_script_sends_credentials(self):
        """Cloudflare Access の内側にあるため、Cookie を送らないと弾かれる。"""
        page = dashboard_page_service.render_home_page(
            [], NOW,
            dashboard_path="/dashboard/", status_path="/dashboard/status",
            quest_path="/quest", asa_note_url=config.ASA_NOTE_URL,
            refresh_sec=60,
        )

        assert 'credentials: "same-origin"' in page

    def test_a_failed_update_keeps_the_last_values(self):
        """圏外・サーバー再起動の最中に画面が空になると、かえって困る。"""
        page = dashboard_page_service.render_home_page(
            [], NOW,
            dashboard_path="/dashboard/", status_path="/dashboard/status",
            quest_path="/quest", asa_note_url=config.ASA_NOTE_URL,
            refresh_sec=60,
        )

        assert 'classList.add("stale")' in page
        assert "#status.stale" in page

    def test_repeated_fragment_requests_do_not_read_the_db_again(self):
        """断片の取得経路もページ本体と同じTTLキャッシュを通ること。"""
        patches = _stub_loaders()
        for p in patches:
            p.start()
        try:
            with patch.object(
                home_status_service.analysis_service, "load_sensor_data",
                return_value=pd.DataFrame(),
            ) as load, _client() as client:
                client.get(f"{config.DASHBOARD_BASE_PATH}/")
                client.get(f"{config.DASHBOARD_BASE_PATH}/status")
                client.get(f"{config.DASHBOARD_BASE_PATH}/status")
        finally:
            for p in patches:
                p.stop()

        assert load.call_count == 1


class TestSupportingValues:
    """D: 「いまの値」だけでは判断できないカードに、比べる相手を添える。"""

    def test_the_card_shows_the_supporting_line(self):
        card = home_status_service.StatusCard("💰 今月の電気代", "⚡ 1,234 円", "theme-blue",
                                              sub="先月同日 1,000円 (+234)")

        card_html = home_status_service.render_status_card_html(
            card.title, card.value, card.theme, sub=card.sub
        )

        assert 'class="status-sub"' in card_html
        assert "先月同日 1,000円 (+234)" in card_html

    def test_a_card_without_one_stays_as_it_was(self):
        card_html = home_status_service.render_status_card_html("t", "v", "theme-gray")

        assert "status-sub" not in card_html

    def test_the_supporting_line_is_escaped(self):
        """DB・スクレイピング由来の文字列が混ざりうる経路と同じ扱いにする。"""
        card_html = home_status_service.render_status_card_html(
            "t", "v", "theme-gray", sub="<script>alert(1)</script>"
        )

        assert "<script>" not in card_html
        assert "&lt;script&gt;" in card_html

    def test_the_time_is_written_the_way_a_person_reads_it(self):
        now = datetime.fromisoformat("2026-09-19T12:00:00+09:00")
        fmt = home_status_service._format_moment

        assert fmt(pd.Timestamp("2026-09-19T09:40:00+09:00"), now) == "09:40"
        assert fmt(pd.Timestamp("2026-09-18T18:40:00+09:00"), now) == "昨日 18:40"
        assert fmt(pd.Timestamp("2026-09-15T18:40:00+09:00"), now) == "9/15 18:40"
        assert fmt(None, now) is None

    def test_the_colour_and_the_time_cannot_disagree(self):
        """判定と補足表示が同じ行を見ていること(別々に絞ると食い違う)。"""
        df = pd.DataFrame({
            "location": ["高砂", "高砂"],
            "contact_state": ["open", "closed"],
            "timestamp": [
                pd.Timestamp("2026-09-19T11:30:00+09:00"),
                pd.Timestamp("2026-09-19T11:55:00+09:00"),
            ],
        })
        now = datetime.fromisoformat("2026-09-19T12:00:00+09:00")

        value, theme = home_status_service.get_takasago_status(df, now)
        sub = home_status_service.describe_takasago(df, now)

        # 判定は "open" の行(11:30)を見ているので、補足も同じ行でなければならない
        # ("closed" の 11:55 を拾うと、色は緑なのに時刻だけ新しいという食い違いになる)
        assert theme == "theme-green" and "元気" in value
        assert sub == "最終検知 11:30"

    def test_the_parking_camera_says_when_it_last_detected_motion(self):
        df = pd.DataFrame({
            "device_type": [home_status_service.CAMERA_DEVICE_TYPE],
            "movement_state": ["ON"],
            "device_id": [home_status_service.PARKING_CAMERA_ID],
            "timestamp": [pd.Timestamp("2026-09-19T08:15:00+09:00")],
        })
        now = datetime.fromisoformat("2026-09-19T12:00:00+09:00")

        assert home_status_service.describe_parking(df, now) == "最終検知 08:15"

    def test_the_cost_card_compares_with_last_month(self):
        assert home_status_service.describe_cost(12345, 11000) == "先月同日 11,000円 (+1,345)"
        assert home_status_service.describe_cost(9000, 11000) == "先月同日 11,000円 (-2,000)"

    def test_the_cost_card_says_nothing_without_a_comparison(self):
        """初月・取得失敗のときに「先月同日 0円」と出すと、使っていないように見える。"""
        assert home_status_service.describe_cost(12345, 0) is None
        assert home_status_service.describe_cost(12345, None) is None

    def test_missing_material_only_drops_the_supporting_line(self):
        """補足の材料が取れなくても、カードの値と色は変わらないこと。"""
        empty = pd.DataFrame()
        cards = home_status_service.build_status_cards(
            datetime.fromisoformat("2026-09-19T12:00:00+09:00"),
            empty, None,
            {"percent": 42.0}, 1234,
        )

        assert len(cards) == EXPECTED_CARD_COUNT
        assert all(card.sub is None for card in cards)

    def test_the_page_shows_them_too(self):
        patches = _stub_loaders()
        for p in patches:
            p.start()
        try:
            with _client() as client:
                page = client.get(f"{config.DASHBOARD_BASE_PATH}/").text
        finally:
            for p in patches:
                p.stop()

        assert 'class="status-sub"' in page
        assert "先月同日 1,000円" in page
        assert "ディスク 55%" in page


class TestCardColoursSurviveLinking:
    """カードをリンクにしたときに、状態を表すテーマ色が消えないこと。"""

    def test_the_link_style_does_not_override_the_theme_colour(self):
        assert "color: inherit" not in home_status_service.STATUS_CARD_CSS

    def test_the_link_has_no_underline(self):
        """下線が付くと、テーマ色で示している状態が読み取りにくくなる。"""
        assert "text-decoration: none" in home_status_service.STATUS_CARD_CSS


class TestExternalLinkHealth:
    """ホームの「ファミクエ」「あさノート」ボタンの稼働状況による色分け(緑=稼働中・赤=停止中)。"""

    def _home(self, path=""):
        patches = _stub_loaders()
        for p in patches:
            p.start()
        try:
            with _client() as client:
                return client.get(f"{config.DASHBOARD_BASE_PATH}{path}")
        finally:
            for p in patches:
                p.stop()

    def test_healthy_links_are_green_with_label(self):
        res = self._home("/")
        assert res.text.count("external-card external-up") == 3
        assert "external-down" not in res.text.split("</style>", 1)[1]
        assert "● 稼働中" in res.text

    def test_down_app_is_red_and_other_stays_green(self, monkeypatch):
        monkeypatch.setattr(home_status_service, "check_asa_note_health", lambda url: False)
        res = self._home("/")
        body = res.text.split("</style>", 1)[1]
        asa = body.split("あさノート", 1)[0].rsplit("<a ", 1)[1]
        quest = body.split("ファミクエ", 1)[0].rsplit("<a ", 1)[1]
        assert "external-down" in asa
        assert "external-up" in quest
        assert "● 停止中" in res.text

    def test_down_link_is_still_tappable(self, monkeypatch):
        monkeypatch.setattr(home_status_service, "check_quest_app_health", lambda: False)
        res = self._home("/")
        assert 'class="external-card external-down" href="/quest"' in res.text

    def test_external_links_open_in_a_new_tab(self):
        """よく使うリンク(ファミクエ・あさノート・よるノート)は新しいタブで開く。"""
        res = self._home("/")
        section = res.text.split(f'id="{home_status_service.STATUS_SECTION_ID}"', 1)[1].split("メニュー", 1)[0]
        anchors = [a for a in section.split("<a ")[1:] if "external-card" in a]
        assert len(anchors) == 3
        assert all('target="_blank"' in a and 'rel="noopener"' in a for a in anchors)

    def test_note_urls_point_to_the_deployed_apps(self):
        assert config.ASA_NOTE_URL == "https://asa-note.vercel.app/"
        assert config.YORU_NOTE_URL == "https://yorunote-mm.vercel.app/"

    def test_down_yoru_note_is_red_and_only_that_link(self, monkeypatch):
        monkeypatch.setattr(home_status_service, "check_asa_note_health", lambda url: "yorunote" not in url)
        body = self._home("/").text.split("</style>", 1)[1]
        yoru = body.split("よるノート", 1)[0].rsplit("<a ", 1)[1]
        asa = body.split("あさノート", 1)[0].rsplit("<a ", 1)[1]
        assert "external-down" in yoru and "external-up" in asa

    def test_status_fragment_keeps_links_so_color_refreshes(self, monkeypatch):
        """自動更新はこのフラグメントで範囲ごと差し替わる。リンクが入っていないと更新で消える。"""
        monkeypatch.setattr(home_status_service, "check_asa_note_health", lambda url: False)
        res = self._home("/status")
        assert "よく使うリンク" in res.text
        assert "external-down" in res.text
        assert 'href="/quest"' in res.text

    def test_links_are_inside_the_refreshed_section(self):
        res = self._home("/")
        section = res.text.split(f'id="{home_status_service.STATUS_SECTION_ID}"', 1)[1].split("メニュー", 1)[0]
        assert "ファミクエ" in section and "あさノート" in section

    def test_render_without_health_keeps_plain_style(self):
        html = dashboard_page_service._render_external_links("/quest", "https://x.example/", None)
        assert "external-up" not in html and "external-down" not in html
        assert "稼働中" not in html and "停止中" not in html

    def test_health_is_cached_between_requests(self, monkeypatch):
        calls = []
        monkeypatch.setattr(home_status_service, "check_asa_note_health", lambda url: calls.append(url) or True)
        home_status_service.collect_link_health("https://x.example/")
        home_status_service.collect_link_health("https://x.example/")
        assert calls == ["https://x.example/"]


class TestCheckQuestAppHealth:
    def _dist(self, tmp_path, monkeypatch, with_index=True):
        if with_index:
            (tmp_path / "index.html").write_text("<html></html>")
        monkeypatch.setattr(config, "QUEST_DIST_DIR", str(tmp_path))

    def test_ok_when_index_exists_and_db_readable(self, tmp_path, monkeypatch, isolated_db):
        self._dist(tmp_path, monkeypatch)
        assert _REAL_CHECK_QUEST() is True

    def test_down_when_index_html_missing(self, tmp_path, monkeypatch, isolated_db):
        self._dist(tmp_path, monkeypatch, with_index=False)
        assert _REAL_CHECK_QUEST() is False

    def test_down_when_db_unreachable(self, tmp_path, monkeypatch, isolated_db):
        self._dist(tmp_path, monkeypatch)
        monkeypatch.setattr(config, "SQLITE_DB_PATH", str(tmp_path / "missing" / "no.db"))
        assert _REAL_CHECK_QUEST() is False


class TestCheckAsaNoteHealth:
    class _Resp:
        def __init__(self, status):
            self.status_code = status

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    @pytest.mark.parametrize("status,expected", [(200, True), (302, True), (404, False), (503, False)])
    def test_status_codes(self, monkeypatch, status, expected):
        monkeypatch.setattr(home_status_service.requests, "get", lambda url, **kw: self._Resp(status))
        assert _REAL_CHECK_ASA_NOTE("https://x.example/") is expected

    def test_connection_error_is_down(self, monkeypatch):
        def boom(url, **kw):
            raise home_status_service.requests.exceptions.ConnectionError("no route")

        monkeypatch.setattr(home_status_service.requests, "get", boom)
        assert _REAL_CHECK_ASA_NOTE("https://x.example/") is False

    def test_uses_short_timeout(self, monkeypatch):
        seen = {}

        def fake_get(url, **kw):
            seen.update(kw)
            return self._Resp(200)

        monkeypatch.setattr(home_status_service.requests, "get", fake_get)
        _REAL_CHECK_ASA_NOTE("https://x.example/")
        assert seen["timeout"] == home_status_service.ASA_NOTE_HEALTH_TIMEOUT_SEC
