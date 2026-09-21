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
        "calculate_last_month_cost_same_point": 1000,
        "get_disk_usage": {"percent": 55.0},
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


class TestCardsLinkToTheirDetail:
    """A: 異常に気づいてから詳細を開くまでを1タップにする。

    以前の軽量ページはカード9枚を出して行き止まりで、詳細を見るには
    「📊 詳しく見る」からダッシュボード本体を開き、そこからタブを探し直す
    必要があった。カード自体を該当タブ(`?tab=...`)へのリンクにする。
    """

    def _page(self, **overrides):
        patches = _stub_loaders(**overrides)
        for p in patches:
            p.start()
        try:
            with _client() as client:
                return client.get(f"{config.DASHBOARD_BASE_PATH}/m").text
        finally:
            for p in patches:
                p.stop()

    def test_every_card_links_to_a_tab_that_exists(self):
        import re

        page = self._page()

        hrefs = re.findall(r'<a class="status-card [^"]*" href="([^"]+)"', page)
        assert len(hrefs) == 9, "9枚すべてがリンクになっていること"
        for href in hrefs:
            assert href.startswith(f"{config.DASHBOARD_BASE_PATH}/?tab=")
            tab_key = href.rsplit("=", 1)[1]
            assert tab_key in home_status_service.DASHBOARD_TAB_KEYS, f"存在しないタブ: {tab_key}"

    def test_cards_point_at_the_tab_that_actually_shows_them(self):
        """リンク先が「そのカードの詳細が載っているタブ」であること。"""
        cards, _ = self._collect()
        by_title = {card.title: card.tab for card in cards}

        assert by_title["👵 高砂 (実家)"] == "watch"
        assert by_title["🚃 JR運行情報"] == "out"
        assert by_title["💰 今月の電気代"] == "life"
        assert by_title["🗄️ NAS"] == "sys"

    def _collect(self):
        patches = _stub_loaders()
        for p in patches:
            p.start()
        try:
            return home_status_service.collect_status_cards(NOW)
        finally:
            for p in patches:
                p.stop()

    def test_the_streamlit_grid_keeps_plain_cards(self):
        """Streamlit 側はリンクにしない。

        Streamlit でリンクを踏むとページ全体が再読み込みになり(セッションが
        作り直され数秒かかる)、同じ移動を「詳しく見る」ボタン(再実行だけで
        切り替わる)が既に担っているため。
        """
        cards, _ = self._collect()

        grid = home_status_service.render_status_grid_html(cards)

        assert "<a class=\"status-card" not in grid
        assert grid.count('<div class="status-card') == 9

    def test_the_link_is_relative_to_the_viewing_origin(self):
        """固定URLを埋めるとLAN内のIPと公開ドメインのどちらかで繋がらなくなる。"""
        card = home_status_service.StatusCard("t", "v", "theme-gray", tab="watch")

        assert home_status_service.card_detail_href(card, "/dashboard/") == "/dashboard/?tab=watch"
        assert home_status_service.card_detail_href(
            home_status_service.StatusCard("t", "v", "theme-gray"), "/dashboard/"
        ) is None

    def test_tab_keys_have_a_single_definition(self):
        """`dashboard.py` 側にタブのキーを書き戻すと、リンク先とタブがずれる。"""
        dashboard_py = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard.py"
        )
        with open(dashboard_py, encoding="utf-8") as f:
            source = f.read()

        assert "home_status_service.DASHBOARD_TABS" in source
        assert '("home", ' not in source, "dashboard.py にタブ定義が再び書かれている"


class TestAlertSummary:
    """B: 赤・黄のカードだけを名前で拾って先頭に出す。

    9枚の並びは固定のまま。JR運行情報は7枚目にあり、運休が出ていても
    画面をスクロールしないと気づけなかった。
    """

    def test_red_comes_before_yellow(self):
        cards = [
            home_status_service.StatusCard("平常", "v", "theme-green"),
            home_status_service.StatusCard("注意", "v", "theme-yellow"),
            home_status_service.StatusCard("異常", "v", "theme-red"),
            home_status_service.StatusCard("不明", "v", "theme-gray"),
        ]

        assert [c.title for c in home_status_service.summarize_alerts(cards)] == ["異常", "注意"]

    def test_the_grid_order_is_not_changed(self):
        """並べ替えではなく要約で解決する(位置で覚えている画面を動かさない)。"""
        cards = [
            home_status_service.StatusCard("1番目", "v", "theme-green"),
            home_status_service.StatusCard("2番目", "v", "theme-red"),
        ]

        grid = home_status_service.render_status_grid_html(cards)

        assert grid.index("1番目") < grid.index("2番目")

    def test_the_line_is_shown_even_when_nothing_is_wrong(self):
        """毎分の更新で行が出たり消えたりすると、下の内容が上下に跳ねる。"""
        ok = home_status_service.render_alerts_html(
            [home_status_service.StatusCard("平常", "v", "theme-green")]
        )

        assert "気になることはありません" in ok
        assert "alerts" in ok

    def test_alerts_link_to_the_detail_tab(self):
        cards = [home_status_service.StatusCard("🚃 JR運行情報", "⛔ 運休発生", "theme-red", tab="out")]

        html_out = home_status_service.render_alerts_html(cards, dashboard_path="/dashboard/")

        assert 'href="/dashboard/?tab=out"' in html_out
        assert "JR運行情報" in html_out

    def test_alert_titles_are_escaped(self):
        cards = [home_status_service.StatusCard("<script>x</script>", "v", "theme-red", tab="out")]

        html_out = home_status_service.render_alerts_html(cards, dashboard_path="/dashboard/")

        assert "<script>" not in html_out
        assert "&lt;script&gt;" in html_out

    def test_the_streamlit_page_uses_the_same_rule(self):
        """拾う条件を View 側に書き直すと、2つの画面で「気になること」が食い違う。"""
        with open(os.path.join(_VIEWS_DIR, "summary.py"), encoding="utf-8") as f:
            source = f.read()

        assert "home_status_service.summarize_alerts" in source


class TestDarkMode:
    """B: 夜にスマホで見ると白背景が眩しい。軽量ページだけダークに対応する。"""

    def _page(self):
        return home_status_service.render_mobile_status_page_html(
            [], NOW,
            manifest_path="/m.webmanifest", icon_path="/i.png",
            dashboard_path="/dashboard/", quest_path="/quest",
        )

    def test_the_light_page_follows_the_device_setting(self):
        page = self._page()

        assert "prefers-color-scheme: dark" in page
        assert "color-scheme: light dark" in page

    def test_the_streamlit_page_keeps_its_own_theme(self):
        """本体は Streamlit のテーマ(Light固定もできる)に任せる。

        共有CSSへ入れると、本体を Light に固定している端末で
        「周りは白いのにカードだけ黒い」状態になる。
        """
        from views.dashboard import common as view_common

        assert "prefers-color-scheme" not in home_status_service.STATUS_CARD_CSS
        assert "prefers-color-scheme" not in view_common.CUSTOM_CSS

    def test_the_bicycle_diff_colours_can_be_themed(self):
        """値のHTMLに `style='color:...'` を直接埋めるとダーク側で差し替えられない。"""
        df = pd.DataFrame(
            {
                "area_name": ["JR伊丹駅前(第1)自転車駐車場 (A)"],
                "waiting_count": [3],
                "timestamp": [pd.Timestamp("2026-09-19T12:00:00+09:00")],
            }
        )

        value, _ = home_status_service.get_bicycle_status(df)

        assert "style='color:" not in value
        assert "diff-" in value
        assert ".diff-up" in home_status_service.STATUS_CARD_CSS


class TestPartialRefresh:
    """C: 自動更新でページ全体を読み込み直さない。

    以前は `<meta http-equiv="refresh">` で60秒ごとに全体を再読み込みしており、
    画面が白く瞬き、スクロール位置も先頭へ戻っていた。
    """

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
        res = self._get(f"{config.DASHBOARD_BASE_PATH}/m/status")

        assert res.status_code == 200
        assert res.text.count('class="status-card') == 9
        assert res.text.startswith(f'<div id="{home_status_service.STATUS_SECTION_ID}">')
        # 断片なので、ページ全体の要素は含まない
        assert "<html" not in res.text
        assert "<style" not in res.text

    def test_the_fragment_replaces_itself(self):
        """差し替え後も同じidが残らないと、2回目以降の更新先を見失う。"""
        res = self._get(f"{config.DASHBOARD_BASE_PATH}/m/status")

        assert res.text.count(f'id="{home_status_service.STATUS_SECTION_ID}"') == 1

    def test_the_fragment_carries_the_fetch_time_and_the_alert_line(self):
        """時刻と要約も一緒に差し替わらないと、値だけ新しく見出しが古くなる。"""
        res = self._get(f"{config.DASHBOARD_BASE_PATH}/m/status")

        assert "時点" in res.text
        assert "alerts" in res.text

    def test_the_fragment_is_routed_before_the_streamlit_proxy(self):
        """総当たりの中継ルートより後ろに置くと、Streamlit へ流れて404になる。"""
        res = self._get(f"{config.DASHBOARD_BASE_PATH}/m/status")

        assert res.status_code == 200, "中継側が拾っている可能性がある"

    def test_the_page_only_falls_back_to_a_full_reload_without_js(self):
        res = self._get(f"{config.DASHBOARD_BASE_PATH}/m")

        # meta refresh は noscript の中だけ(JS環境で二重に更新されない)
        assert "<noscript><meta http-equiv=\"refresh\"" in res.text
        assert res.text.count('http-equiv="refresh"') == 1
        assert f'"{config.DASHBOARD_BASE_PATH}/m/status"' in res.text

    def test_the_old_full_page_reload_is_still_available(self):
        """`status_path` を渡さない呼び出し方(JSを使わない経路)を壊さない。"""
        page = home_status_service.render_mobile_status_page_html(
            [], NOW,
            manifest_path="/m.webmanifest", icon_path="/i.png",
            dashboard_path="/dashboard/", quest_path="/quest",
        )

        assert "<noscript>" not in page
        assert f'http-equiv="refresh" content="{home_status_service.MOBILE_PAGE_REFRESH_SEC}"' in page
        assert "<script>" not in page

    def test_the_script_sends_credentials(self):
        """Cloudflare Access の内側にあるため、Cookie を送らないと弾かれる。"""
        page = home_status_service.render_mobile_status_page_html(
            [], NOW,
            manifest_path="/m.webmanifest", icon_path="/i.png",
            dashboard_path="/dashboard/", quest_path="/quest",
            status_path="/dashboard/m/status",
        )

        assert 'credentials: "same-origin"' in page

    def test_a_failed_update_keeps_the_last_values(self):
        """圏外・サーバー再起動の最中に画面が空になると、かえって困る。"""
        page = home_status_service.render_mobile_status_page_html(
            [], NOW,
            manifest_path="/m.webmanifest", icon_path="/i.png",
            dashboard_path="/dashboard/", quest_path="/quest",
            status_path="/dashboard/m/status",
        )

        assert 'classList.add("stale")' in page
        assert "#status.stale" in page

    def test_repeated_fragment_requests_do_not_scrape_again(self):
        """断片の取得経路もページ本体と同じTTLキャッシュを通ること。"""
        patches = _stub_loaders()
        for p in patches:
            p.start()
        try:
            with patch.object(
                home_status_service.train_service, "get_jr_traffic_status",
                return_value={"宝塚線": {}, "神戸線": {}},
            ) as scrape, _client() as client:
                client.get(f"{config.DASHBOARD_BASE_PATH}/m")
                client.get(f"{config.DASHBOARD_BASE_PATH}/m/status")
                client.get(f"{config.DASHBOARD_BASE_PATH}/m/status")
        finally:
            for p in patches:
                p.stop()

        assert scrape.call_count == 1


class TestSupportingValues:
    """D: 「いまの値」だけでは判断できないカードに、比べる相手を添える。

    前日比を持っていたのは駐輪場カードだけで、「⚡ 12,345 円」が高いのか安いのか、
    「🍚 炊いてない」が今日だけなのかが分からなかった。
    """

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

    def test_the_rice_cooker_says_when_it_last_ran(self):
        df = pd.DataFrame({
            "device_name": ["炊飯器", "炊飯器"],
            "power_watts": [700.0, 3.0],
            "timestamp": [
                pd.Timestamp("2026-09-18T18:40:00+09:00"),
                pd.Timestamp("2026-09-19T08:00:00+09:00"),
            ],
        })
        now = datetime.fromisoformat("2026-09-19T12:00:00+09:00")

        value, _ = home_status_service.get_rice_status(df, now)

        assert value == "🍚 炊いてない", "今日の判定は変わらない"
        assert home_status_service.describe_rice(df, now) == "前回 昨日 18:40"

    def test_the_car_says_when_it_left(self):
        df = pd.DataFrame({
            "action": ["LEAVE"],
            "timestamp": [pd.Timestamp("2026-09-19T08:15:00+09:00")],
        })
        now = datetime.fromisoformat("2026-09-19T12:00:00+09:00")

        assert home_status_service.describe_car(df, now) == "08:15 に出発"

    def test_the_train_card_names_the_affected_line(self):
        """「⚠️ 遅延あり」だけでは、どちらの路線かが分からない。"""
        jr = {"宝塚線": {"is_delay": True}, "神戸線": {}}

        assert home_status_service.describe_traffic(jr) == "宝塚線"
        assert home_status_service.describe_traffic({"宝塚線": {}, "神戸線": {}}) is None

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
            empty, empty, empty, None,
            {"宝塚線": {}, "神戸線": {}}, {"percent": 42.0}, 1234,
        )

        assert len(cards) == 9
        assert all(card.sub is None for card in cards)

    def test_the_light_page_shows_them_too(self):
        patches = _stub_loaders()
        for p in patches:
            p.start()
        try:
            with _client() as client:
                page = client.get(f"{config.DASHBOARD_BASE_PATH}/m").text
        finally:
            for p in patches:
                p.stop()

        assert 'class="status-sub"' in page
        assert "先月同日 1,000円" in page
        assert "ディスク 55%" in page


class TestCardColoursSurviveLinking:
    """カードをリンクにしたときに、状態を表すテーマ色が消えないこと。

    `a.status-card { color: inherit }` を入れると、要素+クラス(0,1,1)が
    `.theme-green`(0,1,0)に勝ってしまい、値の文字色が本文色に戻る
    (背景色だけで状態を示すことになり、配色の意図が半分失われる)。
    """

    def test_the_link_style_does_not_override_the_theme_colour(self):
        assert "color: inherit" not in home_status_service.STATUS_CARD_CSS

    def test_the_link_has_no_underline(self):
        """下線が付くと、テーマ色で示している状態が読み取りにくくなる。"""
        assert "text-decoration: none" in home_status_service.STATUS_CARD_CSS
